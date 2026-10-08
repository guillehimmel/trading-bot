"""
Technical indicator calculations – pure numpy / pandas (no external TA library).
Expects OHLCV DataFrame with columns: open, high, low, close, volume.
"""

import numpy as np
import pandas as pd


# ─── Core helpers ─────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=1).mean()

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

def _bollinger(close: pd.Series, window: int = 20, std: float = 2.0):
    mid   = _sma(close, window)
    sigma = close.rolling(window=window, min_periods=1).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    width = (upper - lower) / (mid + 1e-10)
    pband = ((close - lower) / (upper - lower + 1e-10)).clip(0, 1)
    return upper, mid, lower, width, pband

def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    macd_line   = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram

def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int = 14, d_period: int = 3):
    lowest_low   = low.rolling(k_period, min_periods=1).min()
    highest_high = high.rolling(k_period, min_periods=1).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-10)
    d = k.rolling(d_period, min_periods=1).mean()
    return k, d

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move   = high - prev_high
    down_move = prev_low - low
    dm_pos = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    dm_neg = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    dm_pos = pd.Series(dm_pos, index=close.index, dtype=float)
    dm_neg = pd.Series(dm_neg, index=close.index, dtype=float)

    smooth = lambda s: s.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    atr_s  = smooth(tr)
    di_pos = 100 * smooth(dm_pos) / (atr_s + 1e-10)
    di_neg = 100 * smooth(dm_neg) / (atr_s + 1e-10)
    dx     = 100 * (di_pos - di_neg).abs() / (di_pos + di_neg + 1e-10)
    adx    = smooth(dx)
    return adx, di_pos, di_neg

def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


# ─── SuperTrend / Parabolic SAR ───────────────────────────────────────────────
# Reescritos a partir de los scripts del curso AxelMunguiaQuintero/Trading-Cuantitativo-
# en-Python (MIT), con ATR de Wilder, sin recorrer pandas por índice y devolviendo
# arrays alineados con el DataFrame original.

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               length: int = 10, factor: float = 3.0):
    """Devuelve (linea, direccion): direccion +1 alcista (línea bajo el precio) / -1 bajista.
    Causal: el valor en la vela i solo usa velas <= i."""
    h, l, c = (x.to_numpy(dtype=float) for x in (high, low, close))
    n = len(c)
    atr = _atr(high, low, close, length).to_numpy(dtype=float)
    hl2 = (h + l) / 2.0
    basic_u, basic_l = hl2 + factor * atr, hl2 - factor * atr

    fu, fl = basic_u.copy(), basic_l.copy()
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)
    for i in range(1, n):
        if np.isnan(atr[i]) or np.isnan(fu[i - 1]):
            continue
        fu[i] = basic_u[i] if (basic_u[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = basic_l[i] if (basic_l[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if c[i] < fl[i] else 1
        else:
            direction[i] = 1 if c[i] > fu[i] else -1
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return (pd.Series(line, index=close.index, name="supertrend"),
            pd.Series(direction, index=close.index, name="supertrend_dir"))


def parabolic_sar(high: pd.Series, low: pd.Series, step: float = 0.02,
                  max_step: float = 0.20):
    """Devuelve (sar, tendencia_alcista[bool]). SAR de Wilder; causal."""
    h, l = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
    n = len(h)
    sar = np.full(n, np.nan)
    up_trend = np.ones(n, dtype=bool)
    if n < 3:
        return (pd.Series(sar, index=high.index), pd.Series(up_trend, index=high.index))
    up, af, ep = True, step, h[0]
    sar[0] = l[0]
    for i in range(1, n):
        s = sar[i - 1] + af * (ep - sar[i - 1])
        if up:
            s = min(s, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < s:                       # se da vuelta a bajista
                up, s, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            s = max(s, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > s:                       # se da vuelta a alcista
                up, s, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        sar[i], up_trend[i] = s, up
    return (pd.Series(sar, index=high.index, name="psar"),
            pd.Series(up_trend, index=high.index, name="psar_up"))


# ─── Main function ────────────────────────────────────────────────────────────

def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicators to an OHLCV DataFrame.
    Expects columns: open, high, low, close, volume.
    Returns a copy with new indicator columns.
    """
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # ── Momentum ──────────────────────────────────────────────────────────────
    df["rsi_14"] = _rsi(c, 14)
    df["rsi_7"]  = _rsi(c, 7)
    df["stoch_k"], df["stoch_d"] = _stochastic(h, l, c, 14, 3)

    # ── Trend ─────────────────────────────────────────────────────────────────
    df["macd"], df["macd_signal"], df["macd_hist"] = _macd(c)
    df["ema_9"]   = _ema(c, 9)
    df["ema_21"]  = _ema(c, 21)
    df["ema_50"]  = _ema(c, 50)
    df["ema_100"] = _ema(c, 100)
    df["ema_200"] = _ema(c, 200)
    df["sma_20"]  = _sma(c, 20)
    df["sma_50"]  = _sma(c, 50)
    df["adx"], df["adx_pos"], df["adx_neg"] = _adx(h, l, c, 14)

    # ── Volatility ────────────────────────────────────────────────────────────
    df["bb_upper"], df["bb_middle"], df["bb_lower"], df["bb_width"], df["bb_pct"] = \
        _bollinger(c, 20, 2.0)
    df["atr_14"] = _atr(h, l, c, 14)

    # ── Volume ────────────────────────────────────────────────────────────────
    df["volume_sma_20"] = _sma(v, 20)
    df["volume_ratio"]  = v / (df["volume_sma_20"] + 1e-10)
    df["obv"]           = _obv(c, v)

    # ── Custom features ───────────────────────────────────────────────────────
    df["candle_body"]  = (c - df["open"]).abs()
    df["candle_range"] = h - l
    df["body_ratio"]   = df["candle_body"] / (df["candle_range"] + 1e-8)

    df["rolling_high_20"] = h.rolling(20, min_periods=1).max()
    df["rolling_low_10"]  = l.rolling(10, min_periods=1).min()
    df["rolling_high_24"] = h.rolling(24, min_periods=1).max()
    df["rolling_low_24"]  = l.rolling(24, min_periods=1).min()
    df["rolling_high_48"] = h.rolling(48, min_periods=1).max()
    df["rolling_low_48"]  = l.rolling(48, min_periods=1).min()
    # BTC momentum breakout: 20-day high of CLOSES (not high-of-highs)
    df["rolling_close_high_20"] = c.rolling(20, min_periods=1).max()

    df["return_1h"]     = c.pct_change(1)
    df["return_4h"]     = c.pct_change(4)
    df["return_24h"]    = c.pct_change(24)
    df["volatility_20"] = df["return_1h"].rolling(20).std() * np.sqrt(24)

    return df


# ─── Market regime classifier ─────────────────────────────────────────────────

def compute_market_regime(df: pd.DataFrame, idx: int = -1) -> str:
    row     = df.iloc[idx]
    adx     = float(row.get("adx",     20))
    ema_50  = float(row.get("ema_50",  row["close"]))
    ema_200 = float(row.get("ema_200", row["close"]))
    atr_14  = float(row.get("atr_14",  0))
    close   = float(row["close"])
    atr_pct = atr_14 / (close + 1e-10)

    if adx > 35:
        return "TRENDING_UP" if ema_50 > ema_200 else "TRENDING_DOWN"
    elif atr_pct > 0.035:
        return "VOLATILE"
    return "RANGING"


# ─── ML feature vector ────────────────────────────────────────────────────────

def get_feature_vector(df: pd.DataFrame, idx: int = -1) -> list:
    row   = df.iloc[idx]
    close = float(row["close"])

    def safe(val, default=0.0):
        try:
            v = float(val)
            return v if np.isfinite(v) else default
        except Exception:
            return default

    return [
        safe(row.get("rsi_14",       50))  / 100.0,
        safe(row.get("bb_pct",        0.5)),
        safe(row.get("macd_hist",     0))  / (close * 0.01 + 1e-8),
        min(safe(row.get("volume_ratio", 1)) / 5.0, 1.0),
        safe(row.get("adx",           20)) / 100.0,
        safe(row.get("atr_14",        0))  / (close + 1e-8),
        1.0 if safe(row.get("ema_9",  close)) > safe(row.get("ema_21", close)) else 0.0,
        1.0 if safe(row.get("ema_50", close)) > safe(row.get("ema_200",close)) else 0.0,
        safe(row.get("return_24h",    0)),
        safe(row.get("volatility_20", 0.02)),
        safe(row.get("stoch_k",       50)) / 100.0,
        safe(row.get("body_ratio",    0.5)),
    ]
