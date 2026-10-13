"""Cliente opcional para las APIs públicas de Binance Web3 (on-chain).

Estas APIs identifican activos por contractAddress/chainId (tokens en BSC,
Solana, Base) y no por par spot (BTCUSDT, etc.), así que no mapean 1:1 contra
nuestro universo de top-10 por market cap. Se usan solo como contexto extra
de "mood" on-chain (hype social, flujo de smart money) para el departamento
de noticias — nunca como señal principal de un activo puntual del universo.

Fuente: https://github.com/binance/binance-skills-hub
  (skills/binance-web3/crypto-market-rank)
"""

import requests

BASE_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct"
HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "binance-web3/3.0 (Skill)",
    "source": "agent",
}

BSC_CHAIN_ID = "56"  # único chain soportado por los 3 endpoints usados acá


def _get(path: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{BASE_URL}{path}", json=body, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_social_hype(chain_id: str = BSC_CHAIN_ID) -> dict:
    return _get(
        "/buw/wallet/market/token/pulse/social/hype/rank/leaderboard/ai",
        {"chainId": chain_id, "targetLanguage": "es", "timeRange": 1},
    )


def get_smart_money_inflow(chain_id: str = BSC_CHAIN_ID, tag_type: int = 2) -> dict:
    return _post(
        "/tracker/wallet/token/inflow/rank/query/ai",
        {"chainId": chain_id, "tagType": tag_type},
    )


def get_trending_tokens(chain_id: str = BSC_CHAIN_ID, size: int = 5) -> dict:
    return _post(
        "/buw/wallet/market/token/pulse/unified/rank/list/ai",
        {
            "chainId": chain_id,
            "rankType": 10,
            "countMin": 10,
            "launchTimeMin": 15,
            "liquidityMin": 5000,
            "uniqueTraderMin": 10,
            "volumeMin": 10000,
            "tagFilter": [1, 2, 3],
            "size": size,
        },
    )


def _best_effort(fn) -> list | None:
    try:
        return fn()
    except requests.RequestException:
        return None


TOP_N = 5  # cuántos tokens se resumen por lista — cada respuesta cruda trae
# decenas de entradas con 60-80 campos cada una (gráficos, holders, tags...),
# así que acá se extraen solo los campos relevantes para "mood" de mercado.


def _top_social_hype() -> list[dict]:
    data = get_social_hype().get("data", {})
    entries = data.get("leaderBoardList", [])[:TOP_N]
    return [
        {
            "symbol": e["metaInfo"]["symbol"],
            "sentiment": e["socialHypeInfo"]["sentiment"],
            "social_hype": e["socialHypeInfo"]["socialHype"],
        }
        for e in entries
    ]


def _top_smart_money_inflow() -> list[dict]:
    entries = get_smart_money_inflow().get("data", [])[:TOP_N]
    return [
        {
            "token": e["tokenName"],
            "inflow_usd": e["inflow"],
            "price_change_pct": e["priceChangeRate"],
        }
        for e in entries
    ]


def _top_trending() -> list[dict]:
    entries = get_trending_tokens().get("data", {}).get("tokens", [])[:TOP_N]
    return [
        {
            "symbol": e["symbol"],
            "price_change_24h_pct": e.get("percentChange24h"),
            "volume_24h": e.get("volume24h"),
        }
        for e in entries
    ]


def get_onchain_context() -> dict:
    """Contexto on-chain best-effort y RESUMIDO (top 5, pocos campos) para el
    depto de noticias — las respuestas crudas de Binance Web3 son enormes
    (cientos de miles de tokens por llamada) y esto es solo "mood" general,
    no una señal por activo. Cada sub-llamada es independiente — si una
    falla, las demás igual se devuelven."""
    return {
        "social_hype_top5": _best_effort(_top_social_hype),
        "smart_money_inflow_top5": _best_effort(_top_smart_money_inflow),
        "trending_top5": _best_effort(_top_trending),
    }
