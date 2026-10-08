"""Tests de la validación anti-sobreajuste (src/lab/validation.py)."""

import numpy as np
import pandas as pd
import pytest

from src.lab.backtester import BacktestResult, BacktestTrade, Backtester
from src.lab.indicators import add_all_indicators
from src.lab.strategies import ALL_STRATEGIES
from src.lab.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.lab import validation as v


def _result(pf=1.5, trades=40, dd=0.05, pnl_pcts=None, hold=5):
    ts = [
        BacktestTrade(side="LONG", entry_price=1, exit_price=1, quantity=1, pnl=p, pnl_pct=p,
                      fees=0, entry_time=0, exit_time=0, duration_hours=0,
                      exit_reason="TAKE_PROFIT", hold_candles=hold)
        for p in (pnl_pcts or [])
    ]
    return BacktestResult(
        strategy_name="x", cagr=0, win_rate=0.5, profit_factor=pf, max_drawdown=dd,
        sharpe_ratio=0, sortino_ratio=0, total_trades=trades, winning_trades=0,
        losing_trades=0, total_pnl=0, total_pnl_pct=0, avg_trade_pnl=0, avg_win=0,
        avg_loss=0, avg_hold_hours=0, trades=ts)


def _df(n=1500, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    c = 20000 * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99,
                       "close": c, "volume": rng.uniform(100, 200, n)},
                      index=pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC"))
    return add_all_indicators(df)


# ── split ─────────────────────────────────────────────────────────────────────

def test_split_is_oos_is_chronological_with_warmup():
    df = _df(1000)
    df_is, df_oos = v.split_is_oos(df, min_candles=50, oos_fraction=0.3)
    assert len(df_is) == 700
    assert df_oos.index[50] == df.index[700]      # el OOS operable arranca en el corte
    assert df_is.index[-1] < df_oos.index[50]     # sin solapamiento operable
    with pytest.raises(ValueError):
        v.split_is_oos(df.iloc[:60], min_candles=50)


# ── criterios IS/OOS ──────────────────────────────────────────────────────────

def test_validate_oos_accepts_a_consistent_strategy():
    ok, why = v.validate_oos(_result(pf=1.6, trades=50), _result(pf=1.4, trades=20, dd=0.06))
    assert ok, why


@pytest.mark.parametrize("res_is,res_oos,fragment", [
    (_result(trades=10), _result(), "pocos trades"),
    (_result(pf=1.1), _result(), "PF IS"),
    (_result(), _result(trades=3), "OOS con pocos trades"),
    (_result(), _result(pf=1.0), "PF OOS"),
    (_result(pf=3.0), _result(pf=1.15), "degrada"),
    (_result(dd=0.04), _result(pf=2.0, dd=0.10), "Drawdown"),
])
def test_validate_oos_rejections(res_is, res_oos, fragment):
    ok, why = v.validate_oos(res_is, res_oos)
    assert not ok and fragment in why


# ── monkey test ───────────────────────────────────────────────────────────────

def test_monkey_test_rejects_a_strategy_that_only_matches_chance():
    df = _df(1500, drift=0.0)
    mixed = [0.05, -0.05] * 20          # PF ~ 1 antes de costos: igual que el azar
    m = v.monkey_test(df, _result(pnl_pcts=mixed), n_monkeys=300)
    assert m is not None and not m.passed and m.p_value > 0.15


def test_monkey_test_accepts_an_implausibly_good_strategy():
    df = _df(1500, drift=0.0)
    m = v.monkey_test(df, _result(pnl_pcts=[0.06] * 40 + [-0.01] * 5), n_monkeys=300)
    assert m.passed and m.p_value < 0.15


def test_monkey_test_without_trades_returns_none():
    assert v.monkey_test(_df(300), _result(trades=0, pnl_pcts=[])) is None


def test_monkey_test_is_reproducible_with_seed():
    df, res = _df(1000), _result(pnl_pcts=[0.03, -0.02] * 15)
    assert v.monkey_test(df, res, 200, seed=7).p_value == v.monkey_test(df, res, 200, seed=7).p_value


# ── vecinos ───────────────────────────────────────────────────────────────────

def test_neighbor_check_flags_a_fragile_parameter():
    """PF alto solo con los parámetros exactos: cualquier cambio, por chico que sea, pierde."""
    exact = DonchianBreakoutStrategy().params

    def run(st, d):
        return _result(pf=2.0 if st.params == exact else 0.7, trades=30)
    fragile = v.neighbor_check(DonchianBreakoutStrategy, _df(300), run=run)
    assert not fragile.passed

    def run_flat(st, d):
        return _result(pf=1.4, trades=30)
    robust = v.neighbor_check(DonchianBreakoutStrategy, _df(300), run=run_flat)
    assert robust.passed and robust.fraction_profitable == 1.0


def test_perturb_never_returns_the_same_int():
    assert v._perturb(1, 1.2) != 1
    assert v._perturb(10, 0.8) == 8
    assert v._perturb(1.5, 1.2) == pytest.approx(1.8)


def test_tunable_params_excludes_interval_and_bools():
    st = DonchianBreakoutStrategy({"flag": True})
    keys = v.tunable_params(st)
    assert "candle_interval" not in keys and "flag" not in keys and "dc_period" in keys


# ── optuna ────────────────────────────────────────────────────────────────────

def test_optimize_uses_only_in_sample_and_returns_valid_params():
    pytest.importorskip("optuna")
    df = _df(1200)
    df_is, _ = v.split_is_oos(df, DonchianBreakoutStrategy().min_candles)
    params, score = v.optimize_params(DonchianBreakoutStrategy, df_is, n_trials=6, seed=1)
    assert set(params) <= set(v.tunable_params(DonchianBreakoutStrategy()))
    assert np.isfinite(score)
    # deterministas con la misma semilla
    assert v.optimize_params(DonchianBreakoutStrategy, df_is, n_trials=6, seed=1)[0] == params


# ── evaluación completa ───────────────────────────────────────────────────────

def test_evaluate_strategy_end_to_end_is_consistent():
    ev = v.evaluate_strategy(DonchianBreakoutStrategy, _df(1500), cfg={"n_monkeys": 100})
    assert ev.approved == (not ev.reasons)
    assert ev.res_is.total_trades >= 0 and ev.neighbors is not None


def test_no_registered_strategy_is_approved_on_pure_noise():
    """Con ruido puro ninguna estrategia debería pasar todos los filtros a la vez."""
    df = _df(1500, drift=0.0, seed=11)
    for cls in ALL_STRATEGIES:
        if cls().candle_interval != "1d":
            continue
        ev = v.evaluate_strategy(cls, df, cfg={"n_monkeys": 100})
        assert not ev.approved, cls.__name__


# ── regresión: los defaults compartidos no se contaminan ─────────────────────

@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.__name__)
def test_constructing_with_overrides_never_mutates_shared_defaults(cls):
    from src.lab import params as cfg
    base = cls()
    snapshot = dict(base.params)
    numeric = {k: val * 3 for k, val in v.tunable_params(base).items()}
    cls(numeric)                                   # instancia con valores distintos
    assert cls().params == snapshot                # los defaults siguen intactos
    assert cfg.STRATEGY_PARAMS[base.name] == snapshot


# ── Monte Carlo sobre trades ─────────────────────────────────────────────────

def _mc_result(pnls, start=1000.0):
    r = _result(trades=len(pnls), pnl_pcts=[0.01] * len(pnls))
    for t, p in zip(r.trades, pnls):
        t.pnl, t.fees = p, 0.0
    r.equity_curve = [start]
    return r


def test_monte_carlo_passes_a_clearly_profitable_edge():
    mc = v.monte_carlo_trades(_mc_result([20.0] * 30 + [-5.0] * 10), n_sims=500, seed=1)
    assert mc.passed and mc.prob_loss == 0.0 and mc.ret_p5 > 0


def test_monte_carlo_fails_a_losing_strategy_and_a_deep_drawdown():
    losing = v.monte_carlo_trades(_mc_result([10.0] * 10 + [-30.0] * 10), n_sims=500, seed=1)
    assert not losing.passed and losing.prob_loss > 0.5
    # ganadora en promedio pero con pérdidas enormes: el orden adverso da un drawdown grande
    risky = v.monte_carlo_trades(_mc_result([100.0] * 12 + [-250.0] * 4), n_sims=500, seed=1)
    assert risky.dd_p95 > 0.3 and not risky.passed


def test_monte_carlo_needs_enough_trades_and_is_reproducible():
    assert v.monte_carlo_trades(_mc_result([1.0] * 5)) is None
    a = v.monte_carlo_trades(_mc_result([5.0, -4.0] * 10), n_sims=300, seed=9)
    b = v.monte_carlo_trades(_mc_result([5.0, -4.0] * 10), n_sims=300, seed=9)
    assert (a.prob_loss, a.dd_p95) == (b.prob_loss, b.dd_p95)


# ── Walk-forward ─────────────────────────────────────────────────────────────

def test_walk_forward_returns_per_window_stats():
    wf = v.walk_forward(DonchianBreakoutStrategy, _df(2500, drift=0.002, seed=3), n_windows=4)
    assert wf is not None and wf.n_windows == 4
    assert 0 <= wf.profitable <= wf.windows_with_trades <= 4
    assert len(wf.pfs) == wf.windows_with_trades
    assert wf.passed == (wf.windows_with_trades >= 3 and wf.profitable / wf.windows_with_trades >= 0.6)


def test_walk_forward_none_when_chunks_are_shorter_than_warmup():
    from src.lab.strategies.supertrend import SuperTrendStrategy       # min_candles = 120
    assert SuperTrendStrategy().min_candles > 300 // 5
    assert v.walk_forward(SuperTrendStrategy, _df(300), n_windows=4) is None


def test_walk_forward_oos_windows_do_not_overlap_in_trading():
    """Cada ventana OOS arranca justo donde terminó la anterior (sin operar dos veces el mismo tramo)."""
    df = _df(2500)
    chunk = len(df) // 5
    probe = DonchianBreakoutStrategy()
    starts = [k * chunk for k in range(1, 5)]
    firsts = [df.iloc[k * chunk - probe.min_candles: (k + 1) * chunk].index[probe.min_candles]
              for k in range(1, 5)]
    assert firsts == [df.index[s] for s in starts]


# ── Corrección por múltiples pruebas ─────────────────────────────────────────

def test_sidak_threshold_gets_stricter_with_more_tests():
    assert v.sidak_threshold(0.15, 1) == pytest.approx(0.15)
    assert v.sidak_threshold(0.15, 14) < v.sidak_threshold(0.15, 4) < 0.15
    assert v.sidak_threshold(0.15, 14) == pytest.approx(1 - 0.85 ** (1 / 14))
    assert v.sidak_threshold(0.15, 0) == pytest.approx(0.15)       # n inválido no rompe


def test_evaluate_reports_all_layers_and_applies_correction():
    ev = v.evaluate_strategy(DonchianBreakoutStrategy, _df(2500, drift=0.001, seed=2),
                             cfg={"n_monkeys": 100, "mc_simulations": 100}, n_tests=14)
    assert ev.p_value_threshold == pytest.approx(v.sidak_threshold(0.15, 14))
    assert ev.walk_forward is not None and ev.risk_oos is not None
    assert ev.approved == (not ev.reasons)
