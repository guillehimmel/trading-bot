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
