"""
Binance Spot client implementing the ExchangeClient protocol.

- Firma HMAC-SHA256 en los endpoints privados.
- Testnet (testnet.binance.vision) por defecto; mainnet solo con testnet=False.
- Los símbolos internos son BTC-USDT / BTC-USD; USD se traduce a USDT.
- Las cantidades se redondean hacia abajo a los filtros LOT_SIZE / PRICE_FILTER
  del par, y se valida MIN_NOTIONAL antes de enviar la orden.
"""

import hashlib
import hmac
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Optional
from urllib.parse import urlencode

import pandas as pd
import requests
import structlog

from src.api.exchange_protocol import Balance, MarketData, OrderResult
from src.api.symbol_mapper import Exchange, to_binance_interval, to_exchange_symbol

logger = structlog.get_logger(__name__)

MAINNET_URL = "https://api.binance.com"
TESTNET_URL = "https://testnet.binance.vision"

REQUEST_TIMEOUT = 15
RECV_WINDOW_MS = 5000
DEFAULT_TAKER_FEE = Decimal("0.001")
FEE_CACHE_TTL = 3600


class BinanceAPIError(Exception):
    """Respuesta de error de la API de Binance."""

    def __init__(self, status: int, code: Optional[int], msg: str):
        super().__init__(f"Binance API error {status} (code={code}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    """Redondea hacia abajo a un múltiplo de `step` (step 0 = sin restricción)."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class BinanceClient:
    """Cliente de Binance Spot."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self.testnet = testnet
        self._base_url = TESTNET_URL if testnet else MAINNET_URL
        self._session = session or requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": api_key})

        self._filters_cache: dict[str, dict[str, Any]] = {}
        self._fee_cache: dict[str, tuple[Decimal, float]] = {}
        self._order_symbols: dict[str, str] = {}
        self._lock = threading.Lock()

        logger.info("binance_client_initialized", testnet=testnet)

    # ------------------------------------------------------------------ HTTP

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        signed: bool = False,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if signed:
            params["recvWindow"] = RECV_WINDOW_MS
            params["timestamp"] = int(time.time() * 1000)
            query = urlencode(params)
            params["signature"] = hmac.new(
                self._api_secret, query.encode(), hashlib.sha256
            ).hexdigest()

        resp = self._session.request(
            method, f"{self._base_url}{path}", params=params, timeout=REQUEST_TIMEOUT
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if resp.status_code >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            msg = payload.get("msg") if isinstance(payload, dict) else resp.text
            raise BinanceAPIError(resp.status_code, code, str(msg))
        return payload

    # --------------------------------------------------------------- filtros

    def _symbol_filters(self, symbol: str) -> dict[str, Decimal]:
        """tickSize, stepSize, minQty y minNotional del par (cacheado)."""
        with self._lock:
            cached = self._filters_cache.get(symbol)
        if cached:
            return cached

        info = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        filters = {f["filterType"]: f for f in info["symbols"][0]["filters"]}
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        parsed = {
            "tick_size": Decimal(filters.get("PRICE_FILTER", {}).get("tickSize", "0")),
            "step_size": Decimal(filters.get("LOT_SIZE", {}).get("stepSize", "0")),
            "min_qty": Decimal(filters.get("LOT_SIZE", {}).get("minQty", "0")),
            "min_notional": Decimal(notional.get("minNotional", "0")),
        }
        with self._lock:
            self._filters_cache[symbol] = parsed
        return parsed

    @staticmethod
    def _symbol(product_id: str) -> str:
        return to_exchange_symbol(product_id, Exchange.BINANCE)

    # ----------------------------------------------------------- datos de mercado

    def get_balance(self, currency: str = "BTC") -> Balance:
        currency = currency.upper()
        if currency == "USD":
            currency = "USDT"
        account = self._request("GET", "/api/v3/account", signed=True)
        for b in account.get("balances", []):
            if b["asset"] == currency:
                return Balance(
                    currency=currency,
                    available=Decimal(b["free"]),
                    hold=Decimal(b["locked"]),
                )
        return Balance(currency=currency, available=Decimal("0"), hold=Decimal("0"))

    def get_current_price(self, product_id: str = "BTC-USDT") -> Decimal:
        data = self._request("GET", "/api/v3/ticker/price", {"symbol": self._symbol(product_id)})
        return Decimal(data["price"])

    def get_market_data(self, product_id: str = "BTC-USDT") -> MarketData:
        symbol = self._symbol(product_id)
        book = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        stats = self._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol})
        bid, ask = Decimal(book["bidPrice"]), Decimal(book["askPrice"])
        return MarketData(
            symbol=product_id,
            price=Decimal(stats["lastPrice"]),
            bid=bid,
            ask=ask,
            volume_24h=Decimal(stats["volume"]),
            timestamp=datetime.now(timezone.utc),
        )

    def get_candles(
        self,
        product_id: str = "BTC-USDT",
        granularity: str = "ONE_HOUR",
        limit: int = 100,
    ) -> pd.DataFrame:
        rows = self._request(
            "GET",
            "/api/v3/klines",
            {
                "symbol": self._symbol(product_id),
                "interval": to_binance_interval(granularity),
                "limit": min(limit, 1000),
            },
        )
        data = [
            {
                "timestamp": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
                "open": Decimal(r[1]),
                "high": Decimal(r[2]),
                "low": Decimal(r[3]),
                "close": Decimal(r[4]),
                "volume": Decimal(r[5]),
            }
            for r in rows
        ]
        return pd.DataFrame(
            data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    # ---------------------------------------------------------------- órdenes

    def _fail(self, side: str, size: Decimal, error: str) -> OrderResult:
        logger.error("binance_order_failed", side=side, error=error)
        return OrderResult(
            order_id="", side=side, size=size, filled_price=None,
            status="failed", fee=Decimal("0"), success=False, error=error,
        )

    def _to_result(self, side: str, symbol: str, resp: dict) -> OrderResult:
        """Convierte la respuesta FULL de /api/v3/order en un OrderResult."""
        order_id = str(resp["orderId"])
        with self._lock:
            self._order_symbols[order_id] = symbol

        executed = Decimal(resp.get("executedQty", "0"))
        quote_spent = Decimal(resp.get("cummulativeQuoteQty", "0"))
        filled_price = (quote_spent / executed) if executed > 0 else None

        fee = Decimal("0")
        for fill in resp.get("fills", []):
            commission = Decimal(fill["commission"])
            asset = fill["commissionAsset"]
            if symbol.endswith(asset):  # comisión en moneda de cotización
                fee += commission
            elif symbol.startswith(asset):  # comisión en moneda base
                fee += commission * Decimal(fill["price"])
            # comisión en BNB u otro activo: no convertible acá, se omite

        status = resp.get("status", "").lower()
        return OrderResult(
            order_id=order_id,
            side=side,
            size=executed,
            filled_price=filled_price,
            status=status,
            fee=fee,
            success=status in ("filled", "partially_filled", "new"),
        )

    def market_buy(self, product_id: str, quote_size: Decimal) -> OrderResult:
        try:
            symbol = self._symbol(product_id)
            filters = self._symbol_filters(symbol)
            if quote_size < filters["min_notional"]:
                return self._fail(
                    "buy", Decimal("0"),
                    f"Order value {quote_size} below minimum notional {filters['min_notional']}",
                )
            resp = self._request(
                "POST", "/api/v3/order",
                {
                    "symbol": symbol, "side": "BUY", "type": "MARKET",
                    "quoteOrderQty": format(quote_size, "f"),
                    "newOrderRespType": "FULL",
                },
                signed=True,
            )
            logger.info("binance_market_buy", symbol=symbol, quote_size=str(quote_size))
            return self._to_result("buy", symbol, resp)
        except (BinanceAPIError, requests.RequestException, KeyError, ValueError) as e:
            return self._fail("buy", Decimal("0"), str(e))

    def market_sell(self, product_id: str, base_size: Decimal) -> OrderResult:
        try:
            symbol = self._symbol(product_id)
            filters = self._symbol_filters(symbol)
            qty = _round_down(base_size, filters["step_size"])
            if qty < filters["min_qty"] or qty <= 0:
                return self._fail("sell", qty, f"Quantity {qty} below minimum {filters['min_qty']}")
            resp = self._request(
                "POST", "/api/v3/order",
                {
                    "symbol": symbol, "side": "SELL", "type": "MARKET",
                    "quantity": format(qty, "f"), "newOrderRespType": "FULL",
                },
                signed=True,
            )
            logger.info("binance_market_sell", symbol=symbol, base_size=str(qty))
            return self._to_result("sell", symbol, resp)
        except (BinanceAPIError, requests.RequestException, KeyError, ValueError) as e:
            return self._fail("sell", base_size, str(e))

    def _limit_ioc(self, side: str, product_id: str, base_size: Decimal,
                   limit_price: Decimal) -> OrderResult:
        try:
            symbol = self._symbol(product_id)
            filters = self._symbol_filters(symbol)
            qty = _round_down(base_size, filters["step_size"])
            # Redondear el precio hacia abajo: en compra nunca paga de más; en venta
            # baja el mínimo aceptado, así que sigue siendo ejecutable.
            price = _round_down(limit_price, filters["tick_size"])
            if qty < filters["min_qty"] or qty <= 0:
                return self._fail(side, qty, f"Quantity {qty} below minimum {filters['min_qty']}")
            if price * qty < filters["min_notional"]:
                return self._fail(
                    side, qty,
                    f"Order value {price * qty} below minimum notional {filters['min_notional']}",
                )
            resp = self._request(
                "POST", "/api/v3/order",
                {
                    "symbol": symbol, "side": side.upper(), "type": "LIMIT",
                    "timeInForce": "IOC",
                    "quantity": format(qty, "f"), "price": format(price, "f"),
                    "newOrderRespType": "FULL",
                },
                signed=True,
            )
            return self._to_result(side, symbol, resp)
        except (BinanceAPIError, requests.RequestException, KeyError, ValueError) as e:
            return self._fail(side, base_size, str(e))

    def limit_buy_ioc(self, product_id: str, base_size: Decimal,
                      limit_price: Decimal) -> OrderResult:
        return self._limit_ioc("buy", product_id, base_size, limit_price)

    def limit_sell_ioc(self, product_id: str, base_size: Decimal,
                       limit_price: Decimal) -> OrderResult:
        return self._limit_ioc("sell", product_id, base_size, limit_price)

    def _symbol_for_order(self, order_id: str) -> str:
        with self._lock:
            symbol = self._order_symbols.get(str(order_id))
        if not symbol:
            raise ValueError(
                f"Unknown order {order_id}: Binance needs the symbol and this client "
                "only knows orders it placed in this process"
            )
        return symbol

    def get_order(self, order_id: str) -> dict:
        return self._request(
            "GET", "/api/v3/order",
            {"symbol": self._symbol_for_order(order_id), "orderId": order_id},
            signed=True,
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request(
                "DELETE", "/api/v3/order",
                {"symbol": self._symbol_for_order(order_id), "orderId": order_id},
                signed=True,
            )
            return True
        except (BinanceAPIError, requests.RequestException, ValueError) as e:
            logger.warning("binance_cancel_failed", order_id=order_id, error=str(e))
            return False

    # ------------------------------------------------------------------ fees

    def get_trading_fee_rate(self, product_id: str = "BTC-USDT") -> Decimal:
        """Tasa taker del par; 0.1% si la consulta falla (conservador)."""
        symbol = self._symbol(product_id)
        with self._lock:
            cached = self._fee_cache.get(symbol)
        if cached and time.time() - cached[1] < FEE_CACHE_TTL:
            return cached[0]
        try:
            data = self._request(
                "GET", "/api/v3/account/commission", {"symbol": symbol}, signed=True
            )
            rate = Decimal(data["standardCommission"]["taker"]) + Decimal(
                data.get("taxCommission", {}).get("taker", "0")
            )
        except (BinanceAPIError, requests.RequestException, KeyError, ValueError) as e:
            logger.warning("binance_fee_lookup_failed", symbol=symbol, error=str(e))
            return DEFAULT_TAKER_FEE
        with self._lock:
            self._fee_cache[symbol] = (rate, time.time())
        return rate
