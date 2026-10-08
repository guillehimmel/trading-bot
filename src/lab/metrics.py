"""Métricas de riesgo/retorno sobre la curva de capital del backtest.

Las fórmulas están inspiradas en jesse-ai/jesse (MIT, services/metrics.py) y
reescritas para este proyecto. Todo se calcula con la curva a valor de mercado
(una observación por vela) y se anualiza con 365 días: cripto opera 24/7.
"""

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.lab import params as config

HOURS_PER_YEAR = 24 * 365
INTERVAL_HOURS = {"1h": 1, "4h": 4, "1d": 24}


def periods_per_year(interval: str) -> float:
    return HOURS_PER_YEAR / INTERVAL_HOURS[interval]


def candle_returns(equity: Sequence[float]) -> np.ndarray:
    eq = np.asarray(equity, dtype=float)
    if eq.size < 2:
        return np.array([])
    prev = eq[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(prev > 0, eq[1:] / prev - 1.0, 0.0)
    return r


def sharpe_ratio(returns: np.ndarray, periods: float) -> float:
    if returns.size < 2:
        return 0.0
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd * np.sqrt(periods)) if sd > 0 else 0.0


def sortino_ratio(returns: np.ndarray, periods: float) -> float:
    if returns.size < 2:
        return 0.0
    downside = np.sqrt((returns[returns < 0] ** 2).sum() / returns.size)
    if downside == 0:
        return float("inf") if returns.mean() > 0 else 0.0
    return float(returns.mean() / downside * np.sqrt(periods))


def drawdown_series(equity: Sequence[float]) -> np.ndarray:
    eq = np.asarray(equity, dtype=float)
    if eq.size == 0:
        return eq
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peak > 0, eq / peak - 1.0, 0.0)   # <= 0


def max_drawdown(equity: Sequence[float]) -> float:
    """Positivo: 0.25 = caída máxima del 25%."""
    dd = drawdown_series(equity)
    return float(-dd.min()) if dd.size else 0.0


def ulcer_index(equity: Sequence[float]) -> float:
    dd = drawdown_series(equity)
    return float(np.sqrt((dd ** 2).mean())) if dd.size else 0.0


def cvar(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Pérdida media en el peor (1-confidence) de las velas (negativo = pérdida)."""
    if returns.size < 20:
        return 0.0
    k = max(1, int((1 - confidence) * returns.size))
    return float(np.sort(returns)[:k].mean())


def omega_ratio(returns: np.ndarray, threshold: float = 0.0) -> float:
    gains = (returns[returns > threshold] - threshold).sum()
    losses = (threshold - returns[returns < threshold]).sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def longest_underwater(equity: Sequence[float]) -> int:
    """Velas seguidas por debajo del máximo previo (el 'tiempo bajo el agua')."""
    dd = drawdown_series(equity)
    longest = cur = 0
    for v in dd:
        cur = cur + 1 if v < 0 else 0
        longest = max(longest, cur)
    return longest


def max_consecutive_losses(trade_pnls: Sequence[float]) -> int:
    longest = cur = 0
    for p in trade_pnls:
        cur = cur + 1 if p <= 0 else 0
        longest = max(longest, cur)
    return longest


@dataclass
class RiskMetrics:
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    ulcer_index: float
    cvar_95: float
    omega: float
    longest_underwater_candles: int
    expectancy_pct: float            # retorno neto medio por trade
    max_consecutive_losses: int
    exposure: float                  # fracción de velas con posición abierta (aprox.)


def compute_risk_metrics(result, interval: str) -> RiskMetrics:
    """`result` es un BacktestResult; `interval` el de las velas ('1h', '4h', '1d')."""
    eq = np.asarray(result.equity_curve, dtype=float)
    per = periods_per_year(interval)
    r = candle_returns(eq)

    years = max(len(eq) / per, 1e-9)
    total = eq[-1] / eq[0] if eq.size and eq[0] > 0 else 1.0
    cagr = total ** (1 / years) - 1 if total > 0 else -1.0
    mdd = max_drawdown(eq)

    fee_rt = config.TRADING_FEE + config.SLIPPAGE
    net = [t.pnl_pct - fee_rt for t in result.trades]
    held = sum(t.hold_candles for t in result.trades)

    return RiskMetrics(
        sharpe=sharpe_ratio(r, per),
        sortino=sortino_ratio(r, per),
        calmar=float(cagr / mdd) if mdd > 0 else 0.0,
        max_drawdown=mdd,
        ulcer_index=ulcer_index(eq),
        cvar_95=cvar(r),
        omega=omega_ratio(r),
        longest_underwater_candles=longest_underwater(eq),
        expectancy_pct=float(np.mean(net)) if net else 0.0,
        max_consecutive_losses=max_consecutive_losses([t.pnl for t in result.trades]),
        exposure=float(held / len(eq)) if eq.size else 0.0,
    )
