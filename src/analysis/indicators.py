"""Indicadores calculados en Python a partir de velas crudas de Binance.

Por qué existe este módulo: mandarle a Claude 200-300 velas crudas en JSON por
cada activo es la parte más cara del pipeline (decenas de miles de tokens por
ciclo). Resumir cada serie a ~10 números (ATR, RSI, medias, cambio %, volumen
relativo) le da al LLM la misma señal con una fracción del costo.
"""


def _true_range(curr: dict, prev: dict) -> float:
    return max(
        curr["high"] - curr["low"],
        abs(curr["high"] - prev["close"]),
        abs(curr["low"] - prev["close"]),
    )


def _atr(klines: list[dict], period: int) -> float | None:
    if len(klines) < period + 1:
        return None
    window = klines[-(period + 1):]
    trs = [_true_range(window[i], window[i - 1]) for i in range(1, len(window))]
    return sum(trs) / len(trs)


def _rsi(klines: list[dict], period: int) -> float | None:
    if len(klines) < period + 1:
        return None
    window = klines[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(window)):
        delta = window[i]["close"] - window[i - 1]["close"]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _sma(klines: list[dict], period: int) -> float | None:
    if len(klines) < period:
        return None
    window = klines[-period:]
    return sum(k["close"] for k in window) / period


def _ema_series(values: list[float], period: int) -> list[float]:
    """EMA de toda la serie (no solo el último valor) — hace falta la serie
    completa para poder calcular el MACD, que es un EMA de otro EMA."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema_vals = [sum(values[:period]) / period]
    for v in values[period:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _macd_histogram_pct(klines: list[dict], close: float) -> float | None:
    """Histograma del MACD (12/26/9), normalizado por precio para poder
    comparar entre activos de distinto valor nominal (BTC vs XRP)."""
    closes = [k["close"] for k in klines]
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    if not ema26 or len(ema12) < len(ema26):
        return None
    ema12_aligned = ema12[-len(ema26):]
    macd_series = [a - b for a, b in zip(ema12_aligned, ema26)]
    signal_series = _ema_series(macd_series, 9)
    if not signal_series:
        return None
    histogram = macd_series[-1] - signal_series[-1]
    return histogram / close * 100 if close else None


def _obv_neto_pct(klines: list[dict], lookback: int) -> float | None:
    """On-Balance Volume: suma el volumen de las velas que cierran en alza y
    resta el de las que cierran en baja. Expresado como % del volumen total
    del período — positivo = presión compradora neta, negativo = vendedora.
    Sirve para detectar divergencias (precio sube pero el volumen no acompaña)."""
    if len(klines) < lookback + 1:
        return None
    window = klines[-(lookback + 1):]
    net_volume = 0.0
    total_volume = 0.0
    for i in range(1, len(window)):
        vol = window[i]["volume"]
        total_volume += vol
        if window[i]["close"] > window[i - 1]["close"]:
            net_volume += vol
        elif window[i]["close"] < window[i - 1]["close"]:
            net_volume -= vol
    return net_volume / total_volume * 100 if total_volume else None


def compute_features(klines: list[dict]) -> dict:
    """Resumen de una serie de velas (un timeframe) a indicadores escalares."""
    if not klines:
        return {}

    last = klines[-1]
    close = last["close"]

    change_lookback = min(6, len(klines) - 1)
    change_pct = None
    if change_lookback > 0:
        ref_close = klines[-1 - change_lookback]["close"]
        change_pct = (close - ref_close) / ref_close * 100 if ref_close else None

    atr14 = _atr(klines, 14)
    rsi14 = _rsi(klines, 14)
    sma20 = _sma(klines, 20)
    sma50 = _sma(klines, 50)
    macd_histograma_pct = _macd_histogram_pct(klines, close)
    obv_neto_pct = _obv_neto_pct(klines, change_lookback)

    vol_window = klines[-20:] if len(klines) >= 20 else klines
    avg_volume = sum(k["volume"] for k in vol_window) / len(vol_window)
    volume_relative = last["volume"] / avg_volume if avg_volume else None

    swing_window = klines[-20:] if len(klines) >= 20 else klines
    recent_high = max(k["high"] for k in swing_window)
    recent_low = min(k["low"] for k in swing_window)

    return {
        "close": round(close, 8),
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "atr_pct": round(atr14 / close * 100, 2) if atr14 else None,
        "rsi14": round(rsi14, 1) if rsi14 is not None else None,
        "precio_vs_sma20_pct": round((close - sma20) / sma20 * 100, 2) if sma20 else None,
        "precio_vs_sma50_pct": round((close - sma50) / sma50 * 100, 2) if sma50 else None,
        "macd_histograma_pct": round(macd_histograma_pct, 3) if macd_histograma_pct is not None else None,
        "obv_neto_pct": round(obv_neto_pct, 1) if obv_neto_pct is not None else None,
        "volumen_relativo": round(volume_relative, 2) if volume_relative is not None else None,
        "max_reciente": round(recent_high, 8),
        "min_reciente": round(recent_low, 8),
        "velas_analizadas": len(klines),
    }


def compute_orderbook_features(order_book: dict | None) -> dict | None:
    """Spread % e imbalance de profundidad — reemplaza mandar el book crudo."""
    if not order_book or not order_book.get("bids") or not order_book.get("asks"):
        return None

    best_bid = order_book["bids"][0][0]
    best_ask = order_book["asks"][0][0]
    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100 if mid else None

    bid_volume = sum(qty for _, qty in order_book["bids"])
    ask_volume = sum(qty for _, qty in order_book["asks"])
    total = bid_volume + ask_volume
    imbalance = (bid_volume - ask_volume) / total if total else None

    return {
        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
        "imbalance_bid_ask": round(imbalance, 2) if imbalance is not None else None,
    }
