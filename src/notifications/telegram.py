import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = "https://api.telegram.org"


def send_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el .env"
        )
    resp = requests.post(
        f"{BASE_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


def format_signal(ceo_output: dict) -> str:
    if ceo_output["decision"] == "no_trade":
        return (
            "<b>Sin operación esta hora</b>\n"
            f"Confianza: {ceo_output.get('confianza', 0)}\n"
            f"Motivo: {ceo_output.get('motivo_no_trade', 'no especificado')}"
        )

    take_profits = ", ".join(str(tp) for tp in ceo_output.get("take_profit", []))
    lines = [
        f"<b>Señal: {ceo_output['symbol']} {ceo_output['direccion'].upper()}</b>",
        f"Entry: {ceo_output['entry']}",
        f"Stop loss: {ceo_output['stop_loss']}",
        f"Take profit: {take_profits}",
        f"R:R: {ceo_output.get('risk_reward')}",
        f"Tamaño sugerido: {ceo_output.get('tamano_posicion_pct')}% del equity",
        f"Confianza: {ceo_output.get('confianza')}",
        "",
        f"{ceo_output.get('razonamiento', '')}",
    ]
    if ceo_output.get("conflictos_resueltos"):
        lines += ["", f"Conflictos resueltos: {ceo_output['conflictos_resueltos']}"]
    return "\n".join(lines)


def format_position_opened(pos: dict) -> str:
    notional = pos["quantity"] * pos["entry"]
    sl_pct = (pos["stop_loss"] - pos["entry"]) / pos["entry"] * 100
    tp_pct = (pos["take_profit"] - pos["entry"]) / pos["entry"] * 100
    return "\n".join(
        [
            f"<b>Operación abierta: {pos['symbol']} {pos['direccion'].upper()}</b>",
            f"Entry: {pos['entry']:.8g} ({pos['quantity']:.8g} unidades, ~${notional:,.2f})",
            f"Stop loss: {pos['stop_loss']:.8g} ({sl_pct:+.2f}%)",
            f"Take profit: {pos['take_profit']:.8g} ({tp_pct:+.2f}%)",
            f"R:R: {pos.get('risk_reward')}",
        ]
    )


_EXIT_REASON_LABELS = {
    "take_profit": "take profit",
    "stop_loss": "stop loss",
    "time_stop": "time-stop (no resolvió a tiempo)",
}


def format_position_closed(trade: dict) -> str:
    resultado = "GANANCIA" if trade["pnl_gross"] >= 0 else "PÉRDIDA"
    pnl_pct = (trade["exit_price"] - trade["entry"]) / trade["entry"] * 100
    if trade["direccion"] == "short":
        pnl_pct = -pnl_pct
    motivo = _EXIT_REASON_LABELS.get(trade["exit_reason"], trade["exit_reason"])
    return "\n".join(
        [
            f"<b>Cerrada con {resultado}: {trade['symbol']}</b>",
            f"Motivo: {motivo}",
            f"Entry: {trade['entry']:.8g} → Salida: {trade['exit_price']:.8g}",
            f"PnL: {trade['pnl_gross']:+.2f} USDT ({pnl_pct:+.2f}%)",
        ]
    )
