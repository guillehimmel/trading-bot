import requests

BASE_URL = "https://api.binance.com"

INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _parse_klines(raw: list) -> list[dict]:
    return [
        {
            "open_time": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": row[6],
        }
        for row in raw
    ]


def get_klines(
    symbol: str,
    interval: str,
    limit: int,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[dict]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = start_time
    if end_time is not None:
        params["endTime"] = end_time
    resp = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=10)
    resp.raise_for_status()
    return _parse_klines(resp.json())


def get_klines_range(symbol: str, interval: str, start_time: int, end_time: int) -> list[dict]:
    """Trae TODAS las velas entre start_time y end_time (ms epoch), paginando
    de a 1000 (el máximo de Binance por request). Para uso en backtesting."""
    step_ms = INTERVAL_MS[interval]
    candles = []
    cursor = start_time
    while cursor <= end_time:
        batch = get_klines(symbol, interval, 1000, start_time=cursor, end_time=end_time)
        if not batch:
            break
        candles.extend(batch)
        cursor = batch[-1]["open_time"] + step_ms
        if len(batch) < 1000:
            break
    return candles


def get_order_book(symbol: str, limit: int = 50) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    return {
        "bids": [[float(p), float(q)] for p, q in raw["bids"]],
        "asks": [[float(p), float(q)] for p, q in raw["asks"]],
    }


def pair_exists(symbol: str) -> bool:
    try:
        get_klines(symbol, "1h", 1)
        return True
    except requests.HTTPError:
        return False
