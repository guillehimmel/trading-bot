"""Métricas de riesgo/retorno (src/lab/metrics.py)."""

import numpy as np
import pytest

from src.lab import metrics as m


def test_max_drawdown_known_value():
    assert m.max_drawdown([100, 120, 90, 110, 130]) == pytest.approx(0.25)   # 120 -> 90
    assert m.max_drawdown([100, 110, 120]) == 0.0
    assert m.max_drawdown([]) == 0.0


def test_drawdown_uses_running_peak():
    dd = m.drawdown_series([100, 200, 100, 200, 150])
    assert dd.min() == pytest.approx(-0.5) and dd[-1] == pytest.approx(-0.25)


def test_sharpe_scales_with_sqrt_of_periods():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 2000)
    assert m.sharpe_ratio(r, 365 * 6) == pytest.approx(m.sharpe_ratio(r, 365) * np.sqrt(6))
    assert m.sharpe_ratio(np.zeros(10), 365) == 0.0
    assert m.sharpe_ratio(np.array([0.1]), 365) == 0.0


def test_sortino_ignores_upside_volatility():
    base = np.array([0.01, -0.01] * 200)
    lumpy_up = np.array([0.05, -0.01] * 200)      # misma caída, más suba
    assert m.sortino_ratio(lumpy_up, 365) > m.sortino_ratio(base, 365)
    assert m.sortino_ratio(np.array([0.01, 0.02, 0.03]), 365) == float("inf")


def test_cvar_is_mean_of_worst_tail_and_needs_enough_data():
    r = np.concatenate([np.full(95, 0.01), np.full(5, -0.10)])
    assert m.cvar(r, 0.95) == pytest.approx(-0.10)
    assert m.cvar(np.array([0.1, -0.1]), 0.95) == 0.0       # muestra chica


def test_omega_ulcer_underwater_and_streaks():
    assert m.omega_ratio(np.array([0.02, -0.01])) == pytest.approx(2.0)
    assert m.ulcer_index([100, 100, 100]) == 0.0 and m.ulcer_index([100, 80, 100]) > 0
    assert m.longest_underwater([100, 90, 95, 99, 100, 101]) == 3
    assert m.max_consecutive_losses([5, -1, -2, -3, 4, -1]) == 3


def test_candle_returns_handles_zero_and_short_curves():
    assert m.candle_returns([100]).size == 0
    assert list(m.candle_returns([100, 110, 0, 5])) == [pytest.approx(0.1), -1.0, 0.0]


def test_compute_risk_metrics_end_to_end_on_a_real_backtest():
    from tests.test_lab_validation import _df
    from src.lab.backtester import Backtester
    from src.lab.strategies.ema5_momentum import EMA5MomentumStrategy
    res = Backtester(EMA5MomentumStrategy(), _df(1500, seed=4)).run()
    rm = m.compute_risk_metrics(res, "1d")
    assert rm.max_drawdown == pytest.approx(res.max_drawdown)     # misma curva, mismo valor
    assert 0 <= rm.exposure <= 1 and rm.cvar_95 <= 0
    assert rm.max_consecutive_losses >= 0 and np.isfinite(rm.sharpe)
