"""Corre UN ciclo de la agencia contra Binance Spot Testnet: lee el estado de
órdenes/posiciones persistido, reconcilia lo que cambió desde el ciclo
anterior (fills, expiraciones, stop/take-profit tocados, time-stops), corre
Mercado/Riesgo/CEO igual que el backtest/producción, y si hay señal de compra
coloca una orden límite real en testnet.

Limitación real, no un bug: Binance spot no permite shorts (sin margen no se
puede vender algo que no se tiene). Las señales "short" del CEO se loguean
pero no se ejecutan acá.

Pensado para correrse ciclo a ciclo (python -m src.execution.testnet_orchestrator),
igual que se valida el resto del pipeline — no es un loop infinito todavía.
"""

import logging
from datetime import datetime, timezone

from config.settings import (
    ANTHROPIC_MODEL_CEO,
    ANTHROPIC_MODEL_WORKER,
    CONTEXT_KLINES_LIMIT,
    CONTEXT_TIMEFRAME,
    KLINES_LIMIT,
    LIMIT_ORDER_EXPIRY_HOURS,
    MAX_DRAWDOWN_PCT,
    MAX_PORTFOLIO_RISK_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MIN_RISK_REWARD,
    TIME_STOP_HOURS,
    TIMEFRAME,
)
from src.agents import runner
from src.analysis import indicators
from src.execution import binance_testnet as bt
from src.notifications import telegram
from src.risk import sizing
from src.state import execution_state, lessons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
NO_NEWS_INPUT = {"disponible": False, "motivo": "testnet: sin fuente de noticias conectada todavía"}

TIME_STOP_MS = int(TIME_STOP_HOURS * 3_600_000)
LIMIT_ORDER_EXPIRY_MS = int(LIMIT_ORDER_EXPIRY_HOURS * 3_600_000)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _notify(text: str) -> None:
    """Un fallo de Telegram (token mal puesto, sin red, etc.) nunca puede
    tirar abajo el tracking de una posición real — se loguea y se sigue."""
    try:
        telegram.send_message(text)
    except Exception:
        logger.exception("No se pudo enviar la notificación de Telegram")


def compute_equity_and_capital(pairs: list[str]) -> tuple[float, float]:
    balances = bt.get_account_balances()
    usdt = balances.get("USDT", {"free": 0.0, "locked": 0.0})
    capital_disponible = usdt["free"]
    equity = usdt["free"] + usdt["locked"]
    for pair in pairs:
        base = pair.replace("USDT", "")
        if base in balances:
            price = bt.get_price(pair)
            equity += (balances[base]["free"] + balances[base]["locked"]) * price
    return equity, capital_disponible


def reconcile_pending_orders(state: dict) -> None:
    still_pending = []
    for order in state["pending_orders"]:
        status = bt.get_order(order["symbol"], order["order_id"])

        if status["status"] == "FILLED":
            _open_position_from_fill(state, order, status)
            continue

        if status["status"] in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info("Orden pendiente de %s terminó en %s (sin intervención nuestra)", order["symbol"], status["status"])
            continue

        if now_ms() >= order["expires_at"]:
            bt.cancel_order(order["symbol"], order["order_id"])
            logger.info("Orden límite de %s expiró sin llenarse — cancelada", order["symbol"])
            continue

        still_pending.append(order)
    state["pending_orders"] = still_pending


def _open_position_from_fill(state: dict, order: dict, fill_status: dict) -> None:
    symbol = order["symbol"]
    qty = float(fill_status["executedQty"])
    fill_quote = float(fill_status.get("cummulativeQuoteQty", 0.0))
    avg_entry = fill_quote / qty if qty and fill_quote else order["entry"]

    filters = bt.get_symbol_filters(symbol)
    stop_price = bt.round_to_step(order["stop_loss"], filters["tick_size"])
    stop_limit_price = bt.round_to_step(order["stop_loss"] * 0.999, filters["tick_size"])
    tp_price = bt.round_to_step(order["take_profit"], filters["tick_size"])

    position = {
        "symbol": symbol,
        "direccion": "long",
        "entry": avg_entry,
        "quantity": qty,
        "stop_loss": order["stop_loss"],
        "take_profit": order["take_profit"],
        "risk_reward": order["risk_reward"],
        "order_list_id": None,
        "stop_order_id": None,
        "limit_order_id": None,
        "opened_at": now_ms(),
    }

    try:
        oco = bt.place_oco_sell(symbol, qty, tp_price, stop_price, stop_limit_price)
    except Exception:
        # La compra YA se ejecutó en el exchange — perder el tracking acá
        # dejaría el activo en la cuenta sin que el bot sepa que existe.
        # Se guarda igual (sin OCO) para que el próximo ciclo reintente
        # colocarlo, y para que quede visible que necesita atención manual.
        state["open_positions"].append(position)
        logger.exception("La entrada de %s se llenó pero el OCO de salida falló — posición guardada SIN stop/take-profit, revisar a mano", symbol)
        _notify(f"<b>Atención: {symbol}</b> se compró pero el stop loss/take profit NO se pudo colocar — revisar a mano.")
        return

    legs = {leg["type"]: leg["orderId"] for leg in oco["orderReports"]}
    position["order_list_id"] = oco["orderListId"]
    position["stop_order_id"] = legs.get("STOP_LOSS_LIMIT")
    position["limit_order_id"] = legs.get("LIMIT_MAKER")
    state["open_positions"].append(position)
    logger.info("Entrada de %s llenada @ %.8g — OCO de salida colocado (SL %.8g / TP %.8g)", symbol, avg_entry, stop_price, tp_price)
    _notify(telegram.format_position_opened(position))


def reconcile_open_positions(state: dict) -> None:
    still_open = []
    for pos in state["open_positions"]:
        if pos["order_list_id"] is None:
            _retry_oco(pos)
            still_open.append(pos)
            continue

        order_list = bt.get_order_list(pos["order_list_id"])

        if order_list["listOrderStatus"] == "ALL_DONE":
            limit_status = bt.get_order(pos["symbol"], pos["limit_order_id"])
            stop_status = bt.get_order(pos["symbol"], pos["stop_order_id"])
            if limit_status["status"] == "FILLED":
                exit_price, exit_reason = float(limit_status["price"]), "take_profit"
            elif stop_status["status"] == "FILLED":
                exit_price, exit_reason = float(stop_status["price"]), "stop_loss"
            else:
                logger.warning("OCO de %s terminó ALL_DONE pero ninguna pata figura FILLED — reviso a mano", pos["symbol"])
                still_open.append(pos)
                continue
            _close_position(state, pos, exit_price, exit_reason)
            continue

        if now_ms() - pos["opened_at"] >= TIME_STOP_MS:
            bt.cancel_order_list(pos["symbol"], pos["order_list_id"])
            market_sell = bt.place_order(pos["symbol"], "SELL", "MARKET", pos["quantity"])
            qty = float(market_sell["executedQty"])
            quote = float(market_sell.get("cummulativeQuoteQty", 0.0))
            exit_price = quote / qty if qty and quote else bt.get_price(pos["symbol"])
            _close_position(state, pos, exit_price, "time_stop")
            continue

        still_open.append(pos)
    state["open_positions"] = still_open


def _retry_oco(pos: dict) -> None:
    """La posición ya tiene el activo comprado pero el OCO de salida falló al
    colocarse (ver _open_position_from_fill) — reintenta cada ciclo hasta que
    entre. Mientras tanto queda sin SL/TP activo, por eso el warning."""
    try:
        filters = bt.get_symbol_filters(pos["symbol"])
        stop_price = bt.round_to_step(pos["stop_loss"], filters["tick_size"])
        stop_limit_price = bt.round_to_step(pos["stop_loss"] * 0.999, filters["tick_size"])
        tp_price = bt.round_to_step(pos["take_profit"], filters["tick_size"])
        oco = bt.place_oco_sell(pos["symbol"], pos["quantity"], tp_price, stop_price, stop_limit_price)
        legs = {leg["type"]: leg["orderId"] for leg in oco["orderReports"]}
        pos["order_list_id"] = oco["orderListId"]
        pos["stop_order_id"] = legs.get("STOP_LOSS_LIMIT")
        pos["limit_order_id"] = legs.get("LIMIT_MAKER")
        logger.info("OCO de %s reintentado con éxito", pos["symbol"])
    except Exception:
        logger.warning("Posición de %s sigue SIN OCO de salida (reintento falló de nuevo) — atención manual", pos["symbol"])


def _close_position(state: dict, pos: dict, exit_price: float, exit_reason: str) -> None:
    pnl_gross = pos["quantity"] * (exit_price - pos["entry"])
    trade = {**pos, "exit_price": exit_price, "exit_reason": exit_reason, "pnl_gross": pnl_gross, "closed_at": now_ms()}
    state["closed_trades"].append(trade)
    logger.info("Posición de %s cerrada por %s @ %.8g (pnl bruto %.2f)", pos["symbol"], exit_reason, exit_price, pnl_gross)
    _notify(telegram.format_position_closed(trade))


def build_market_input(pairs: list[str]) -> dict:
    activos = []
    for pair in pairs:
        klines = bt.get_klines(pair, TIMEFRAME, KLINES_LIMIT)
        klines_contexto = bt.get_klines(pair, CONTEXT_TIMEFRAME, CONTEXT_KLINES_LIMIT)
        activos.append(
            {
                "symbol": pair,
                "indicadores": indicators.compute_features(klines),
                "indicadores_contexto": indicators.compute_features(klines_contexto),
                "order_book": None,
            }
        )
    return {"timeframe": TIMEFRAME, "timeframe_contexto": CONTEXT_TIMEFRAME, "activos": activos}


def run_cycle(pairs: list[str] = PAIRS) -> dict:
    state = execution_state.load()

    reconcile_pending_orders(state)
    reconcile_open_positions(state)

    equity, capital_disponible = compute_equity_and_capital(pairs)
    state["peak_equity_usdt"] = max(state["peak_equity_usdt"], equity)
    kill_switch = equity <= state["peak_equity_usdt"] * (1 - MAX_DRAWDOWN_PCT / 100)
    state["kill_switch_active"] = kill_switch

    resultado = {"equity": equity, "capital_disponible": capital_disponible, "kill_switch": kill_switch, "decision": None}

    if kill_switch:
        logger.warning("Kill-switch activo (equity %.2f vs pico %.2f, drawdown >= %.0f%%) — no se evalúan trades nuevos", equity, state["peak_equity_usdt"], MAX_DRAWDOWN_PCT)
        execution_state.save(state)
        return resultado

    market_input = build_market_input(pairs)
    exposicion_actual_pct = sum(
        (p["quantity"] * p["entry"]) / equity * 100 for p in state["open_positions"]
    ) if equity else 0.0
    drawdown_pct = max(0.0, (state["peak_equity_usdt"] - equity) / state["peak_equity_usdt"] * 100) if state["peak_equity_usdt"] else 0.0
    risk_input = {
        "equity": equity,
        "posiciones_abiertas": state["open_positions"],
        "drawdown_7d_pct": drawdown_pct,
        "drawdown_30d_pct": drawdown_pct,
        "exposicion_actual_pct": exposicion_actual_pct,
        "max_portfolio_risk_pct": MAX_PORTFOLIO_RISK_PCT,
        "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
    }
    riesgo_output = runner.run_agent("risk_manager", risk_input, ANTHROPIC_MODEL_WORKER)
    mercado_output = runner.run_agent("mercado", market_input, ANTHROPIC_MODEL_WORKER)

    ceo_input = {
        "universo": pairs,
        "timeframe": TIMEFRAME,
        "min_risk_reward": MIN_RISK_REWARD,
        "noticias": NO_NEWS_INPUT,
        "mercado": mercado_output,
        "riesgo": riesgo_output,
        "lecciones_aprendidas": lessons.load(),
    }
    ceo_output = runner.run_agent("ceo", ceo_input, ANTHROPIC_MODEL_CEO)
    resultado["decision"] = ceo_output.get("decision")

    if ceo_output["decision"] == "trade" and ceo_output.get("direccion") == "short":
        logger.info("CEO propuso SHORT en %s — no ejecutable en spot, se ignora", ceo_output.get("symbol"))
    elif ceo_output["decision"] == "trade" and ceo_output.get("entry") is not None:
        _place_entry(state, ceo_output, equity, capital_disponible, riesgo_output.get("riesgo_disponible_pct", 0.0))
    elif ceo_output["decision"] == "trade":
        logger.warning("CEO pidió trade sin 'entry' — se descarta")

    execution_state.save(state)
    return resultado


def _place_entry(state: dict, ceo_output: dict, equity: float, capital_disponible: float, riesgo_disponible_pct: float) -> None:
    symbol = ceo_output["symbol"]
    take_profit = ceo_output["take_profit"][0] if isinstance(ceo_output["take_profit"], list) else ceo_output["take_profit"]

    result = sizing.size_position(
        equity=equity,
        capital_disponible=capital_disponible,
        ceo_tamano_posicion_pct=ceo_output["tamano_posicion_pct"],
        riesgo_disponible_pct=riesgo_disponible_pct,
        entry=ceo_output["entry"],
        stop_loss=ceo_output["stop_loss"],
        take_profit=take_profit,
        direccion="long",
    )
    if result.rejected:
        logger.info("Propuesta de %s descartada por sizing: %s", symbol, result.motivo)
        return

    filters = bt.get_symbol_filters(symbol)
    qty = bt.round_to_step(result.quantity, filters["step_size"])
    price = bt.round_to_step(ceo_output["entry"], filters["tick_size"])
    if qty * price < filters["min_notional"]:
        logger.info("Propuesta de %s descartada: notional tras redondeo ($%.2f) por debajo del mínimo", symbol, qty * price)
        return

    order = bt.place_order(symbol, "BUY", "LIMIT", qty, price)
    state["pending_orders"].append(
        {
            "symbol": symbol,
            "order_id": order["orderId"],
            "direccion": "long",
            "entry": price,
            "stop_loss": ceo_output["stop_loss"],
            "take_profit": take_profit,
            "risk_reward": ceo_output["risk_reward"],
            "tamano_posicion_pct": ceo_output["tamano_posicion_pct"],
            "proposed_at": now_ms(),
            "expires_at": now_ms() + LIMIT_ORDER_EXPIRY_MS,
        }
    )
    logger.info("Orden límite BUY colocada: %s %.8g @ %.8g (orderId=%s)", symbol, qty, price, order["orderId"])


if __name__ == "__main__":
    resultado = run_cycle()
    print("\n=== Ciclo de testnet ===")
    print(f"Equity: ${resultado['equity']:.2f} | Capital disponible: ${resultado['capital_disponible']:.2f}")
    print(f"Kill-switch: {resultado['kill_switch']}")
    print(f"Decisión del CEO: {resultado['decision']}")
    state = execution_state.load()
    print(f"Órdenes pendientes: {len(state['pending_orders'])} | Posiciones abiertas: {len(state['open_positions'])} | Trades cerrados: {len(state['closed_trades'])}")
