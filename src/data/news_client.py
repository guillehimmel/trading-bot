"""Noticias cripto gratis vía RSS (sin API key, sin costo).

CryptoPanic requiere plan pago para uso continuo, así que usamos los feeds
públicos de medios cripto establecidos. Sin sentiment pre-armado: el depto
de Noticias infiere el sentiment a partir del título/resumen.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
}


def _fetch_feed(source: str, url: str) -> list[dict]:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date_raw = item.findtext("pubDate", "")
        description = (item.findtext("description", "") or "").strip()
        try:
            published_at = parsedate_to_datetime(pub_date_raw)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "title": title,
                "description": description,
                "url": link,
                "source": source,
                "published_at": published_at,
            }
        )
    return items


def get_news(assets: list[dict], hours: int = 6) -> dict:
    """assets: [{"ticker": "BTC", "name": "Bitcoin"}, ...]"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    all_items = []
    for source, url in FEEDS.items():
        try:
            all_items.extend(_fetch_feed(source, url))
        except (requests.RequestException, ET.ParseError):
            continue

    recent = [item for item in all_items if item["published_at"] >= cutoff]

    def serialize(item: dict) -> dict:
        return {
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "published_at": item["published_at"].isoformat(),
        }

    por_symbol = {asset["ticker"]: [] for asset in assets}
    for item in recent:
        haystack = f"{item['title']} {item['description']}".lower()
        for asset in assets:
            if asset["ticker"].lower() in haystack or asset["name"].lower() in haystack:
                por_symbol[asset["ticker"]].append(serialize(item))

    return {
        "macro": [serialize(item) for item in recent],
        "por_symbol": por_symbol,
    }
