"""SuperTrend, Parabolic SAR y salida por señal en el backtester."""

import numpy as np
import pandas as pd
import pytest

from src.lab.backtester import Backtester
from src.lab.indicators import add_all_indicators, parabolic_sar, supertrend
from src.lab.strategies import ALL_STRATEGIES
from src.lab.strategies.base_strategy import BaseStrategy, Signal, SignalType
from src.lab.strategies.parabolic_sar import ParabolicSARStrategy
from src.lab.strategies.supertrend import SuperTrendStrategy


def _ohlc(close, noise=0.004, seed=0):
    rng = np.random.default_rng(seed)
    c = np.asarray(close, dtype=float)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, noise, len(c))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, noise, len(c))))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c,
                         "volume": np.full(len(c), 100.0)},
                        index=pd.date_range("2021-01-01", periods=len(c), freq="4h", tz="UTC"))


def _up_then_down(n_up=150, n_down=150):
    up = 100 * np.exp(np.cumsum(np.full(n_up, 0.006)))
    down = up[-1] * np.exp(np.cumsum(np.full(n_down, -0.006)))
    return np.r_[up, down]


def test_supertrend_follows_the_trend_and_line_is_on_the_right_side():
    df = _ohlc(_up_then_down())
    line, d = supertrend(df["high"], df["low"], df["close"], 10, 3.0)
    assert d.iloc[100] == 1 and d.iloc[-1] == -1
    up_idx = (d == 1) & line.notna()
    down_idx = (d == -1) & line.notna()
    assert (line[up_idx] < df["close"][up_idx]).all()       # soporte bajo el precio
    assert (line[down_idx] > df["close"][down_idx]).all()   # resistencia sobre el precio


def test_supertrend_is_causal():
    """Cambiar el futuro no puede cambiar el pasado (sin look-ahead)."""
    df = _ohlc(_up_then_down())
    base_line, base_dir = supertrend(df["high"], df["low"], df["close"])
    alt = df.copy()
    alt.iloc[200:, alt.columns.get_loc("close")] *= 0.5
    alt.iloc[200:, alt.columns.get_loc("high")] *= 0.5
    alt.iloc[200:, alt.columns.get_loc("low")] *= 0.5
    new_line, new_dir = supertrend(alt["high"], alt["low"], alt["close"])
    pd.testing.assert_series_equal(base_dir.iloc[:200], new_dir.iloc[:200])
    np.testing.assert_allclose(base_line.iloc[:200].to_numpy(), new_line.iloc[:200].to_numpy(),
                               equal_nan=True)


def test_parabolic_sar_flips_with_the_trend_and_is_causal():
    df = _ohlc(_up_then_down())
    sar, up = parabolic_sar(df["high"], df["low"])
    assert bool(up.iloc[100]) and not bool(up.iloc[-1])
    assert (sar[up].iloc[5:] < df["close"][up].iloc[5:]).all()
    alt = df.copy()
    alt.iloc[200:, :4] *= 0.5
    sar2, up2 = parabolic_sar(alt["high"], alt["low"])
    pd.testing.assert_series_equal(up.iloc[:200], up2.iloc[:200])


def test_parabolic_sar_short_input_does_not_crash():
    df = _ohlc([100.0, 101.0])
    sar, up = parabolic_sar(df["high"], df["low"])
    assert len(sar) == 2


@pytest.mark.parametrize("cls", [SuperTrendStrategy, ParabolicSARStrategy])
def test_new_strategies_trade_a_trending_market_and_never_short(cls):
    rng = np.random.default_rng(5)
    # tramos alternados de suba y baja con ruido: genera giros de tendencia
    rets = np.concatenate([rng.normal(0.004 * s, 0.012, 120) for s in (1, -1, 1, -1, 1, -1, 1)])
    close = 100 * np.exp(np.cumsum(rets))
    assert 1 < close.max() / close.min() < 1e3       # datos sintéticos razonables
    df = add_all_indicators(_ohlc(close, seed=5))
    res = Backtester(cls(), df).run()
    assert res.total_trades > 0
    assert all(t.side == "LONG" for t in res.trades)
    assert any(t.exit_reason == "SIGNAL_EXIT" for t in res.trades)


# ── salida por señal en el backtester ────────────────────────────────────────

class _BuyThenExit(BaseStrategy):
    """Compra en la vela `buy_at`; pide salir en la vela `exit_at` (posiciones enteras:
    el Backtester hace reset_index, así que las estrategias ven índices 0..n-1)."""
    def __init__(self, buy_at, exit_at):
        super().__init__("stub", {"candle_interval": "1d"})
        self.buy_at, self.exit_at = buy_at, exit_at

    min_candles = property(lambda self: 5)
    candle_interval = property(lambda self: "1d")
    max_hold_candles = property(lambda self: 1000)

    def generate_signal(self, df):
        if df.index[-1] == self.buy_at:
            return Signal(SignalType.BUY, 0.9, stop_loss=1.0, take_profit=1e9)
        return Signal(SignalType.HOLD, 0.0)

    def exit_signal(self, df):
        return df.index[-1] == self.exit_at


def _flat_df(n=30):
    idx = pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC")
    o = 100.0 + np.arange(n)           # cada vela abre 1 más que la anterior
    return pd.DataFrame({"open": o, "high": o + 0.5, "low": o - 0.5, "close": o + 0.2,
                         "volume": 100.0}, index=idx)


def test_signal_exit_and_entry_fill_at_next_open():
    df = _flat_df()
    buy_at, exit_at = 10, 15
    res = Backtester(_BuyThenExit(buy_at, exit_at), df, initial_capital=10000).run()
    t = res.trades[0]
    fee_rt = 0.001 + 0.0003
    assert t.exit_reason == "SIGNAL_EXIT"
    assert t.entry_price == pytest.approx(df["open"].iloc[11] * (1 + fee_rt))   # apertura i+1
    assert t.exit_price == pytest.approx(df["open"].iloc[16])                   # apertura i+1
    assert t.hold_candles == 5


def test_entry_bar_can_hit_its_own_stop():
    """Si la vela de entrada toca el stop, el backtest debe registrarlo."""
    df = _flat_df()
    df.iloc[11, df.columns.get_loc("low")] = 50.0            # la vela de entrada se desploma
    class S(_BuyThenExit):
        def generate_signal(self, d):
            if d.index[-1] == self.buy_at:
                return Signal(SignalType.BUY, 0.9, stop_loss=90.0, take_profit=1e9)
            return Signal(SignalType.HOLD, 0.0)
    res = Backtester(S(10, 25), df, initial_capital=10000).run()
    assert res.trades[0].exit_reason == "STOP_LOSS" and res.trades[0].hold_candles == 0


def test_default_exit_signal_is_false_for_old_strategies():
    assert all(cls().exit_signal(pd.DataFrame()) is False
               for cls in ALL_STRATEGIES if cls not in (SuperTrendStrategy, ParabolicSARStrategy))
