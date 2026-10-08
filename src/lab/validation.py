"""Validación anti-sobreajuste de estrategias.

Ideas tomadas de lauragp28/generador-trading-claude-code (split IS/OOS, monkey
test, chequeo de vecinos) y de jmoraleses/Backtrader-optuna (optimización con
Optuna), reescritas sobre el backtester de este proyecto. El OOS nunca se usa
para optimizar: solo para juzgar.

Una estrategia se considera APROBADA solo si cumple a la vez:
  1. Trades suficientes en IS y en OOS.
  2. Profit factor mínimo en IS y en OOS, sin degradarse demasiado del IS al OOS.
  3. Drawdown en OOS no mucho peor que en IS.
  4. Le gana al azar (monkey test, p-value bajo) en el OOS.
  5. Es robusta a variaciones chicas de sus parámetros (vecinos).
  6. Monte Carlo sobre sus trades: riesgo de pérdida y drawdown acotados (idea de Jesse).
  7. Walk-forward: rinde en la mayoría de las ventanas sucesivas, no solo en una.

Además el umbral del monkey test se corrige por la cantidad de estrategias probadas
(Šidák): cuantas más se prueban, más probable es que alguna pase por azar.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.lab import params as config
from src.lab.backtester import BacktestResult, Backtester
from src.lab.metrics import RiskMetrics, compute_risk_metrics
from src.lab.strategies.base_strategy import BaseStrategy

# Umbrales (los de Laura, adaptados: PF/trades/drawdown relativos al capital)
DEFAULTS = {
    "oos_fraction": 0.30,
    "min_trades_is": 30,
    "min_trades_oos": 10,
    "min_pf_is": 1.20,
    "min_pf_oos": 1.10,
    "max_dd_degradation": 1.5,       # MaxDD_oos <= 1.5 x MaxDD_is
    "min_pf_degradation_ratio": 0.6,  # PF_oos >= 0.6 x PF_is
    "n_monkeys": 1000,
    "p_value_max": 0.15,
    "neighbor_pct": 0.20,
    "min_neighbor_profitable": 0.6,   # fracción de vecinos con PF >= 1
    "mc_simulations": 1000,
    "mc_max_prob_loss": 0.20,         # P(terminar en pérdida) al remuestrear los trades
    "mc_max_dd_p95": 0.30,            # drawdown del percentil 95 (sobre el capital de la estrategia)
    "wf_windows": 4,
    "wf_min_windows_with_trades": 3,
    "wf_min_profitable_fraction": 0.6,
}

_INTERVAL_PARAMS_SKIP = {"candle_interval"}


# ── Split IS / OOS ────────────────────────────────────────────────────────────

def split_is_oos(df: pd.DataFrame, min_candles: int,
                 oos_fraction: float = DEFAULTS["oos_fraction"]
                 ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Divide en orden temporal. Al OOS se le antepone un colchón de `min_candles`
    velas del IS solo para calentar indicadores: no se opera sobre ellas."""
    split = int(len(df) * (1 - oos_fraction))
    if split <= min_candles or len(df) - split <= 0:
        raise ValueError("Datos insuficientes para dividir en IS y OOS")
    return df.iloc[:split], df.iloc[split - min_candles:]


# ── Métricas auxiliares ───────────────────────────────────────────────────────

def profit_factor(returns: np.ndarray) -> float:
    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def net_trade_returns(result: BacktestResult) -> np.ndarray:
    """Retorno neto por trade (el costo de entrada ya está en entry_price)."""
    fee_rt = config.TRADING_FEE + config.SLIPPAGE
    return np.array([t.pnl_pct - fee_rt for t in result.trades], dtype=float)


# ── Criterios IS / OOS ────────────────────────────────────────────────────────

def validate_oos(res_is: BacktestResult, res_oos: BacktestResult,
                 cfg: Optional[dict] = None) -> Tuple[bool, str]:
    """Devuelve (aprobada, motivo)."""
    c = {**DEFAULTS, **(cfg or {})}
    if res_is.total_trades < c["min_trades_is"]:
        return False, f"IS con pocos trades ({res_is.total_trades} < {c['min_trades_is']})"
    if res_is.profit_factor < c["min_pf_is"]:
        return False, f"PF IS={res_is.profit_factor:.2f} < {c['min_pf_is']}"
    if res_oos.total_trades < c["min_trades_oos"]:
        return False, f"OOS con pocos trades ({res_oos.total_trades} < {c['min_trades_oos']})"
    if res_oos.profit_factor < c["min_pf_oos"]:
        return False, f"PF OOS={res_oos.profit_factor:.2f} < {c['min_pf_oos']}"
    ratio = res_oos.profit_factor / res_is.profit_factor
    if ratio < c["min_pf_degradation_ratio"]:
        return False, f"PF se degrada IS->OOS (ratio {ratio:.2f} < {c['min_pf_degradation_ratio']})"
    if res_is.max_drawdown > 0 and (
            res_oos.max_drawdown > c["max_dd_degradation"] * res_is.max_drawdown):
        return False, (f"Drawdown OOS {res_oos.max_drawdown:.1%} > "
                       f"{c['max_dd_degradation']}x el de IS ({res_is.max_drawdown:.1%})")
    return True, (f"OOS OK: PF={res_oos.profit_factor:.2f}, {res_oos.total_trades} trades, "
                  f"PF ratio={ratio:.2f}")


# ── Monkey test ───────────────────────────────────────────────────────────────

@dataclass
class MonkeyResult:
    p_value: float
    pf_real: float
    pf_monkeys_mean: float
    pf_monkeys_p95: float
    n_trades: int
    hold_candles: int
    passed: bool


def monkey_test(df: pd.DataFrame, result: BacktestResult,
                n_monkeys: int = DEFAULTS["n_monkeys"],
                p_value_max: float = DEFAULTS["p_value_max"],
                seed: int = 0) -> Optional[MonkeyResult]:
    """¿Le gana la estrategia a entrar al azar con la misma estructura?

    Cada 'mono' hace el mismo número de trades long, con la misma duración
    mediana, pero en momentos aleatorios y sin stop ni take-profit. p-value =
    fracción de monos con profit factor mayor que el real (menor = mejor).
    Limitación: la estrategia real usa SL/TP y el mono no, así que el test
    es una vara baja: pasarlo es necesario, no suficiente.
    """
    if not result.trades:
        return None
    rets = net_trade_returns(result)
    n_trades = len(rets)
    hold = max(1, int(np.median([t.hold_candles for t in result.trades])))
    close = df["close"].to_numpy(dtype=float)
    fee_rt = config.TRADING_FEE + config.SLIPPAGE
    last_start = len(close) - hold - 1
    if last_start < 1:
        return None

    pf_real = profit_factor(rets)
    rng = np.random.default_rng(seed)
    pfs = np.empty(n_monkeys)
    for k in range(n_monkeys):
        starts = rng.integers(0, last_start, size=n_trades)
        entry = close[starts] * (1 + fee_rt)
        ret = close[starts + hold] / entry - 1 - fee_rt
        pfs[k] = profit_factor(ret)

    greater = np.sum(pfs > pf_real)
    equal = np.sum(pfs == pf_real)
    p_value = float((greater + 0.5 * equal) / n_monkeys)
    finite = pfs[np.isfinite(pfs)]
    return MonkeyResult(
        p_value=p_value,
        pf_real=pf_real,
        pf_monkeys_mean=float(finite.mean()) if finite.size else 0.0,
        pf_monkeys_p95=float(np.percentile(finite, 95)) if finite.size else 0.0,
        n_trades=n_trades,
        hold_candles=hold,
        passed=p_value < p_value_max,
    )


# ── Robustez a parámetros (vecinos) ───────────────────────────────────────────

def tunable_params(strategy: BaseStrategy) -> Dict[str, float]:
    """Parámetros numéricos ajustables (excluye booleanos y el intervalo de velas)."""
    out = {}
    for k, v in strategy.params.items():
        if k in _INTERVAL_PARAMS_SKIP or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = v
    return out


def _perturb(value, factor: float):
    new = value * factor
    if isinstance(value, int):
        new = max(1, int(round(new)))
        if new == value:                       # evitar vecino idéntico por redondeo
            new = value + (1 if factor > 1 else -1)
            new = max(1, new)
    return new


@dataclass
class NeighborResult:
    n_neighbors: int
    fraction_profitable: float     # vecinos con PF >= 1
    pf_min: float
    pf_median: float
    passed: bool


def neighbor_check(strategy_cls: Callable[..., BaseStrategy], df: pd.DataFrame,
                   base_params: Optional[dict] = None,
                   pct: float = DEFAULTS["neighbor_pct"],
                   min_profitable: float = DEFAULTS["min_neighbor_profitable"],
                   run: Optional[Callable[[BaseStrategy, pd.DataFrame], BacktestResult]] = None
                   ) -> Optional[NeighborResult]:
    """Mueve cada parámetro ±pct (uno por vez) y mira cuántos vecinos siguen ganando.

    Una estrategia buena tiene una 'meseta' de parámetros; una sobreajustada
    funciona solo en un valor exacto.
    """
    run = run or (lambda st, d: Backtester(st, d).run())
    base = strategy_cls(base_params)
    pfs: List[float] = []
    for key, value in tunable_params(base).items():
        for factor in (1 - pct, 1 + pct):
            try:
                st = strategy_cls({**(base_params or {}), key: _perturb(value, factor)})
                res = run(st, df)
            except Exception:
                continue
            if res.total_trades > 0:
                pfs.append(res.profit_factor)
            else:
                pfs.append(0.0)
    if not pfs:
        return None
    arr = np.array(pfs)
    frac = float((arr >= 1.0).mean())
    return NeighborResult(
        n_neighbors=len(arr),
        fraction_profitable=frac,
        pf_min=float(arr.min()),
        pf_median=float(np.median(arr)),
        passed=frac >= min_profitable,
    )


# ── Optimización con Optuna (solo sobre IS) ───────────────────────────────────

def optimize_params(strategy_cls: Callable[..., BaseStrategy], df_is: pd.DataFrame,
                    n_trials: int = 50, min_trades: int = DEFAULTS["min_trades_is"],
                    seed: int = 0) -> Tuple[dict, float]:
    """Busca parámetros con Optuna usando SOLO datos IS.

    Objetivo: retorno / drawdown, penalizando pocos trades (evita 'optimizar'
    hacia una estrategia que opera dos veces). Devuelve (params, score).
    Requiere `pip install optuna`.
    """
    try:
        import optuna
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Falta optuna: pip install optuna") from e
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    base = strategy_cls()
    space = tunable_params(base)
    if not space:
        return {}, 0.0

    def objective(trial: "optuna.Trial") -> float:
        params = {}
        for key, value in space.items():
            lo, hi = value * 0.5, value * 2.0
            if isinstance(value, int):
                params[key] = trial.suggest_int(key, max(1, int(lo)), max(2, int(hi)))
            else:
                params[key] = trial.suggest_float(key, lo, hi)
        res = Backtester(strategy_cls(params), df_is).run()
        if res.total_trades < min_trades:
            return -1.0 + res.total_trades / max(min_trades, 1) * 0.5   # gradiente hacia más trades
        return float(res.total_pnl_pct / max(res.max_drawdown, 0.05))

    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return dict(study.best_params), float(study.best_value)


# ── Monte Carlo sobre los trades ─────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    prob_loss: float          # P(retorno final < 0)
    ret_p5: float             # retorno final, percentil 5 (escenario malo)
    ret_median: float
    dd_p95: float             # max drawdown, percentil 95
    n_trades: int
    passed: bool


def monte_carlo_trades(result: BacktestResult,
                       n_sims: int = DEFAULTS["mc_simulations"],
                       max_prob_loss: float = DEFAULTS["mc_max_prob_loss"],
                       max_dd_p95: float = DEFAULTS["mc_max_dd_p95"],
                       seed: int = 0) -> Optional[MonteCarloResult]:
    """Remuestrea los trades (con reposición) para ver cuánta de la curva fue suerte de orden.

    El backtest muestra UN orden posible de los trades; con otro orden el drawdown
    puede ser mucho peor. Acá se simulan `n_sims` órdenes y se mira la cola mala.
    """
    if len(result.trades) < 10 or not result.equity_curve:
        return None
    start = float(result.equity_curve[0])
    pnl = np.array([t.pnl - t.fees for t in result.trades], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(pnl, size=(n_sims, pnl.size), replace=True)
    equity = start + np.cumsum(draws, axis=1)
    equity = np.concatenate([np.full((n_sims, 1), start), equity], axis=1)
    peak = np.maximum.accumulate(equity, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, 1 - equity / peak, 1.0).max(axis=1)
    final = equity[:, -1] / start - 1
    prob_loss = float((final < 0).mean())
    dd_p95 = float(np.percentile(dd, 95))
    return MonteCarloResult(
        prob_loss=prob_loss,
        ret_p5=float(np.percentile(final, 5)),
        ret_median=float(np.median(final)),
        dd_p95=dd_p95,
        n_trades=int(pnl.size),
        passed=prob_loss <= max_prob_loss and dd_p95 <= max_dd_p95,
    )


# ── Walk-forward ──────────────────────────────────────────────────────────────

@dataclass
class WalkForwardResult:
    n_windows: int
    windows_with_trades: int
    profitable: int
    median_pf: float
    pfs: List[float]
    passed: bool


def walk_forward(strategy_cls: Callable[..., BaseStrategy], df: pd.DataFrame,
                 n_windows: int = DEFAULTS["wf_windows"], optimize_trials: int = 0,
                 min_windows_with_trades: int = DEFAULTS["wf_min_windows_with_trades"],
                 min_profitable_fraction: float = DEFAULTS["wf_min_profitable_fraction"],
                 seed: int = 0) -> Optional[WalkForwardResult]:
    """Walk-forward anclado: el IS crece y se prueba en el tramo siguiente.

    Divide los datos en n_windows+1 tramos; para cada k se usa el tramo k como OOS
    (con IS = tramos anteriores, solo para optimizar si optimize_trials>0).
    Pasa si rinde (PF>=1 y ganancia neta) en la mayoría de las ventanas con trades.
    """
    probe = strategy_cls()
    chunk = len(df) // (n_windows + 1)
    if chunk <= probe.min_candles:
        return None

    pfs: List[float] = []
    profitable = 0
    for k in range(1, n_windows + 1):
        df_is = df.iloc[: k * chunk]
        df_oos = df.iloc[k * chunk - probe.min_candles: (k + 1) * chunk]
        params: dict = {}
        if optimize_trials > 0:
            params, _ = optimize_params(strategy_cls, df_is, optimize_trials, seed=seed + k)
        res = Backtester(strategy_cls(params), df_oos).run()
        if res.total_trades == 0:
            continue
        pfs.append(res.profit_factor)
        if res.profit_factor >= 1.0 and res.total_pnl > 0:
            profitable += 1

    with_trades = len(pfs)
    ok = (with_trades >= min_windows_with_trades
          and profitable / with_trades >= min_profitable_fraction)
    return WalkForwardResult(
        n_windows=n_windows, windows_with_trades=with_trades, profitable=profitable,
        median_pf=float(np.median(pfs)) if pfs else 0.0, pfs=pfs, passed=ok,
    )


def sidak_threshold(alpha: float, n_tests: int) -> float:
    """Umbral por prueba para mantener el error global en `alpha` al probar n_tests estrategias."""
    return 1 - (1 - alpha) ** (1 / max(1, n_tests))


# ── Evaluación completa de una estrategia ─────────────────────────────────────

@dataclass
class Evaluation:
    name: str
    interval: str
    params: dict
    res_is: BacktestResult
    res_oos: BacktestResult
    oos_ok: bool
    oos_reason: str
    monkey: Optional[MonkeyResult]
    neighbors: Optional[NeighborResult]
    monte_carlo: Optional[MonteCarloResult]
    walk_forward: Optional[WalkForwardResult]
    risk_oos: RiskMetrics
    p_value_threshold: float
    approved: bool
    reasons: List[str] = field(default_factory=list)


def evaluate_strategy(strategy_cls: Callable[..., BaseStrategy], df: pd.DataFrame,
                      optimize_trials: int = 0, cfg: Optional[dict] = None,
                      seed: int = 0, n_tests: int = 1) -> Evaluation:
    """IS/OOS, monkey test (corregido por n_tests), vecinos, Monte Carlo y walk-forward.

    Con optimize_trials>0 los parámetros se optimizan SOLO en IS (y por ventana en el
    walk-forward). `n_tests` es cuántas estrategias se están probando en total.
    """
    c = {**DEFAULTS, **(cfg or {})}
    probe = strategy_cls()
    df_is, df_oos = split_is_oos(df, probe.min_candles, c["oos_fraction"])

    params: dict = {}
    if optimize_trials > 0:
        params, _ = optimize_params(strategy_cls, df_is, optimize_trials, seed=seed)

    res_is = Backtester(strategy_cls(params), df_is).run()
    res_oos = Backtester(strategy_cls(params), df_oos).run()
    oos_ok, reason = validate_oos(res_is, res_oos, c)

    p_max = sidak_threshold(c["p_value_max"], n_tests)
    monkey = monkey_test(df_oos, res_oos, c["n_monkeys"], p_max, seed)
    neighbors = neighbor_check(strategy_cls, df_is, params, c["neighbor_pct"],
                               c["min_neighbor_profitable"])
    mc = monte_carlo_trades(res_oos, c["mc_simulations"], c["mc_max_prob_loss"],
                            c["mc_max_dd_p95"], seed)
    wf = walk_forward(strategy_cls, df, c["wf_windows"], optimize_trials,
                      c["wf_min_windows_with_trades"], c["wf_min_profitable_fraction"], seed)
    risk = compute_risk_metrics(res_oos, probe.candle_interval)

    reasons = []
    if not oos_ok:
        reasons.append(reason)
    if monkey is None:
        reasons.append("monkey test no ejecutable (sin trades en OOS)")
    elif not monkey.passed:
        reasons.append(f"no supera al azar (p={monkey.p_value:.3f} >= {p_max:.3f})")
    if neighbors is None:
        reasons.append("sin vecinos evaluables")
    elif not neighbors.passed:
        reasons.append(f"frágil a parámetros ({neighbors.fraction_profitable:.0%} de vecinos rentables)")
    if mc is None:
        reasons.append("Monte Carlo no ejecutable (< 10 trades en OOS)")
    elif not mc.passed:
        reasons.append(f"Monte Carlo: P(pérdida)={mc.prob_loss:.0%}, DD p95={mc.dd_p95:.0%}")
    if wf is None:
        reasons.append("walk-forward no ejecutable (pocos datos)")
    elif not wf.passed:
        reasons.append(f"walk-forward: rentable en {wf.profitable}/{wf.windows_with_trades} ventanas con trades")

    return Evaluation(
        name=probe.name, interval=probe.candle_interval, params=params,
        res_is=res_is, res_oos=res_oos, oos_ok=oos_ok, oos_reason=reason,
        monkey=monkey, neighbors=neighbors, monte_carlo=mc, walk_forward=wf,
        risk_oos=risk, p_value_threshold=p_max, approved=not reasons, reasons=reasons,
    )
