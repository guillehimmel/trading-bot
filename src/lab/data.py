"""Descarga de velas OHLCV públicas de Binance (no requiere API key)."""

import time

import pandas as pd
import requests

BASE_URL = "https://api.binance.com"
_INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def get_klines_since(symbol: str, interval: str, days: int,
                     base_url: str = BASE_URL) -> pd.DataFrame:
    """Devuelve `days` días de velas, paginando de a 1000. Índice = hora de apertura (UTC)."""
    step = _INTERVAL_MS[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    rows: list = []
    while start < end:
        r = requests.get(
            f"{base_url}/api/v3/klines",
            params={"symbol": symbol, "interval": interval,
                    "startTime": start, "limit": 1000},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        start = batch[-1][0] + step
        if len(batch) < 1000:
            break
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
    if df.empty:
        return df
    df = df.drop_duplicates("open_time")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]]
