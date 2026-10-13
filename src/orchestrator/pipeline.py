import logging
from concurrent.futures import ThreadPoolExecutor

from config.settings import (
    ANTHROPIC_MODEL_CEO,
    ANTHROPIC_MODEL_WORKER,
    CONTEXT_KLINES_LIMIT,
    CONTEXT_TIMEFRAME,
    KLINES_LIMIT,
    MIN_RISK_REWARD,
    ORDER_BOOK_DEPTH,
    TIMEFRAME,
    UNIVERSE_SIZE,
)
from src.agents import runner
from src.analysis import indicators
from src.data import binance_client, binance_web3_client, coingecko_client, news_client
from src.notifications import telegram
from src.state import lessons, portfolio

logger = logging.getLogger(__name__)

NEWS_MACRO_LIMIT = 10
NEWS_PER_ASSET_LIMIT = 5


def build_universe() -> list[dict]:
    candidates = coingecko_client.get_top_assets(UNIVERSE_SIZE)
    universe = []
    for asset in candidates:
        pair = f"{asset['ticker']}USDT"
        if binance_client.pair_exists(pair):
            universe.append({"ticker": asset["ticker"], "name": asset["name"], "pair": pair})
        if len(universe) == UNIVERSE_SIZE:
            break
    return universe


def build_market_input(universe: list[dict]) -> dict:
    """Trae velas crudas solo para calcular indicadores — nunca se las manda
    crudas a Claude (eso es lo que hacía caro el ciclo)."""
    activos = []
    for asset in universe:
        pair = asset["pair"]
        klines = binance_client.get_klines(pair, TIMEFRAME, KLINES_LIMIT)
        klines_contexto = binance_client.get_klines(pair, CONTEXT_TIMEFRAME, CONTEXT_KLINES_LIMIT)
        order_book = binance_client.get_order_book(pair, limit=ORDER_BOOK_DEPTH)
        activos.append(
            {
                "symbol": pair,
                "indicadores": indicators.compute_features(klines),
                "indicadores_contexto": indicators.compute_features(klines_contexto),
                "order_book": indicators.compute_orderbook_features(order_book),
            }
        )
    return {"timeframe": TIMEFRAME, "timeframe_contexto": CONTEXT_TIMEFRAME, "activos": activos}


def build_news_input(universe: list[dict]) -> dict:
    assets = [{"ticker": a["ticker"], "name": a["name"]} for a in universe]
    news = news_client.get_news(assets)
    return {
        "macro": news["macro"][:NEWS_MACRO_LIMIT],
        "por_activo": [
            {
                "symbol": a["pair"],
                "noticias": news["por_symbol"].get(a["ticker"], [])[:NEWS_PER_ASSET_LIMIT],
            }
            for a in universe
        ],
        "contexto_onchain": binance_web3_client.get_onchain_context(),
    }


def run_cycle() -> dict:
    universe = build_universe()
    if not universe:
        raise RuntimeError("No se pudo armar el universo de activos")

    market_input = build_market_input(universe)
    news_input = build_news_input(universe)
    risk_input = portfolio.risk_manager_input(portfolio.load())

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            "mercado": pool.submit(runner.run_agent, "mercado", market_input, ANTHROPIC_MODEL_WORKER),
            "riesgo": pool.submit(runner.run_agent, "risk_manager", risk_input, ANTHROPIC_MODEL_WORKER),
            "noticias": pool.submit(runner.run_agent, "news_analysis", news_input, ANTHROPIC_MODEL_WORKER),
        }
        departments = {name: future.result() for name, future in futures.items()}

    ceo_input = {
        "universo": [a["pair"] for a in universe],
        "timeframe": TIMEFRAME,
        "min_risk_reward": MIN_RISK_REWARD,
        "lecciones_aprendidas": lessons.load(),
        **departments,
    }
    ceo_output = runner.run_agent("ceo", ceo_input, ANTHROPIC_MODEL_CEO)

    telegram.send_message(telegram.format_signal(ceo_output))
    logger.info("Ciclo completo. Decisión: %s", ceo_output.get("decision"))
    return ceo_output
