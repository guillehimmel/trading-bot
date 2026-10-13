import requests

from config.settings import STABLECOIN_SYMBOLS

BASE_URL = "https://api.coingecko.com/api/v3"


def get_top_assets(n: int, buffer: int = 3) -> list[dict]:
    """Top-n crypto {ticker, name} by market cap, stablecoins excluded.

    Fetches a few extra candidates (buffer) since some get filtered out
    and the caller may also drop ones without a matching Binance pair.
    """
    resp = requests.get(
        f"{BASE_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": n + buffer,
            "page": 1,
        },
        timeout=10,
    )
    resp.raise_for_status()
    coins = resp.json()

    assets = []
    for coin in coins:
        ticker = coin["symbol"].upper()
        if ticker in STABLECOIN_SYMBOLS:
            continue
        assets.append({"ticker": ticker, "name": coin["name"]})
        if len(assets) == n:
            break
    return assets
