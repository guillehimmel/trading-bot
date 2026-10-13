"""Cliente autenticado contra Binance Spot Testnet (testnet.binance.vision) —
plata falsa, mismo API que producción. Separado de src/data/binance_client.py
a propósito: ese sigue apuntando al Binance real para que los precios del
backtest sean reales; este solo se usa para validar ejecución de órdenes
antes de tocar una cuenta de verdad.
"""

import hashlib
import hmac
import math
import time
from urllib.parse import urlencode

import requests

from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET_BASE_URL


def round_to_step(value: float, step: float) -> float:
    """Redondea hacia abajo al múltiplo de `step` más cercano (tickSize/stepSize
    de Binance) — un valor con más decimales de los que el symbol permite
    hace que la orden se rechace."""
    if step <= 0:
        return value
    precision = max(0, -int(round(math.log10(step))))
    return round(int(value / step) * step, precision)


def _sign(params: dict) -> dict:
    query = urlencode(params)
    signature = hmac.new(BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return {**params, "signature": signature}


def _signed_request(method: str, path: str, params: dict | None = None) -> dict:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise RuntimeError("Faltan BINANCE_API_KEY / BINANCE_API_SECRET en .env")

    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    signed = _sign(params)

    resp = requests.request(
        method,
        f"{BINANCE_TESTNET_BASE_URL}{path}",
        params=signed,
        headers={"X-MBX-APIKEY": BINANCE_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_account_balances() -> dict[str, dict[str, float]]:
    """Balances con saldo != 0. {"USDT": {"free": 10000.0, "locked": 0.0}, ...}"""
    data = _signed_request("GET", "/api/v3/account")
    return {
        b["asset"]: {"free": float(b["free"]), "locked": float(b["locked"])}
        for b in data["balances"]
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    }


def ping() -> bool:
    """Chequeo sin firmar — solo confirma que el endpoint de testnet responde."""
    resp = requests.get(f"{BINANCE_TESTNET_BASE_URL}/api/v3/ping", timeout=10)
    return resp.status_code == 200


def get_symbol_filters(symbol: str) -> dict:
    """Público, sin firmar — tickSize/stepSize/minNotional reales del symbol en testnet."""
    resp = requests.get(f"{BINANCE_TESTNET_BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    filters = {f["filterType"]: f for f in resp.json()["symbols"][0]["filters"]}
    return {
        "tick_size": float(filters["PRICE_FILTER"]["tickSize"]),
        "step_size": float(filters["LOT_SIZE"]["stepSize"]),
        "min_notional": float(filters["NOTIONAL"]["minNotional"]),
    }


def place_order(symbol: str, side: str, order_type: str, quantity: float, price: float | None = None) -> dict:
    """side: 'BUY'|'SELL'. order_type: 'LIMIT'|'MARKET'. price requerido para LIMIT."""
    params = {"symbol": symbol, "side": side, "type": order_type, "quantity": quantity}
    if order_type == "LIMIT":
        params["price"] = price
        params["timeInForce"] = "GTC"
    return _signed_request("POST", "/api/v3/order", params)


def get_order(symbol: str, order_id: int) -> dict:
    return _signed_request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id})


def cancel_order(symbol: str, order_id: int) -> dict:
    return _signed_request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})


def get_klines(symbol: str, interval: str, limit: int) -> list[dict]:
    """Igual que src/data/binance_client.get_klines pero contra testnet — las
    decisiones del CEO tienen que basarse en los mismos precios donde después
    se ejecuta, no en el Binance real (pueden divergir)."""
    resp = requests.get(
        f"{BINANCE_TESTNET_BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    return [
        {"open_time": r[0], "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
        for r in resp.json()
    ]


def get_price(symbol: str) -> float:
    resp = requests.get(f"{BINANCE_TESTNET_BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    return float(resp.json()["price"])


def place_oco_sell(symbol: str, quantity: float, take_profit_price: float, stop_price: float, stop_limit_price: float) -> dict:
    """Cierra una posición long con un solo request: vende al take_profit (orden
    límite) O al stop_price (dispara una orden límite a stop_limit_price) —
    la que se ejecute primero cancela automáticamente la otra."""
    params = {
        "symbol": symbol,
        "side": "SELL",
        "quantity": quantity,
        "price": take_profit_price,
        "stopPrice": stop_price,
        "stopLimitPrice": stop_limit_price,
        "stopLimitTimeInForce": "GTC",
    }
    return _signed_request("POST", "/api/v3/order/oco", params)


def get_order_list(order_list_id: int) -> dict:
    return _signed_request("GET", "/api/v3/orderList", {"orderListId": order_list_id})


def cancel_order_list(symbol: str, order_list_id: int) -> dict:
    return _signed_request("DELETE", "/api/v3/orderList", {"symbol": symbol, "orderListId": order_list_id})
