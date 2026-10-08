"""Tests del cliente de Binance con una sesión HTTP falsa (sin red)."""

import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode

import pytest

from src.api.binance_client import MAINNET_URL, TESTNET_URL, BinanceClient
from src.api.symbol_mapper import (
    Exchange,
    normalize_symbol,
    to_binance_interval,
    to_exchange_symbol,
)

EXCHANGE_INFO = {
    "symbols": [{
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
            {"filterType": "NOTIONAL", "minNotional": "5.00"},
        ]
    }]
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Registra requests y responde por (método, path)."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def request(self, method, url, params=None, timeout=None):
        path = url.split("binance.", 1)[1].split("/", 1)[1]
        path = "/" + path
        self.calls.append((method, url, dict(params or {})))
        resp = self.routes[(method, path)]
        return resp(params) if callable(resp) else resp


def make_client(routes, testnet=True):
    base = {("GET", "/api/v3/exchangeInfo"): FakeResponse(EXCHANGE_INFO)}
    base.update(routes)
    session = FakeSession(base)
    return BinanceClient("key", "secret", testnet=testnet, session=session), session


def test_symbol_mapping():
    assert to_exchange_symbol("BTC-USD", Exchange.BINANCE) == "BTCUSDT"
    assert to_exchange_symbol("ETH-USDT", Exchange.BINANCE) == "ETHUSDT"
    assert normalize_symbol("BTCUSDT", Exchange.BINANCE) == "BTC-USDT"
    assert normalize_symbol("ETHBTC", Exchange.BINANCE) == "ETH-BTC"
    assert to_binance_interval("FOUR_HOUR") == "4h"
    with pytest.raises(ValueError):
        to_binance_interval("NOPE")


def test_testnet_vs_mainnet_url():
    _, s1 = make_client({("GET", "/api/v3/ticker/price"): FakeResponse({"price": "1"})}, testnet=True)
    c1, s1 = make_client({("GET", "/api/v3/ticker/price"): FakeResponse({"price": "1"})}, testnet=True)
    c2, s2 = make_client({("GET", "/api/v3/ticker/price"): FakeResponse({"price": "1"})}, testnet=False)
    c1.get_current_price("BTC-USDT")
    c2.get_current_price("BTC-USDT")
    assert s1.calls[0][1].startswith(TESTNET_URL)
    assert s2.calls[0][1].startswith(MAINNET_URL)


def test_signed_request_has_valid_signature():
    c, s = make_client({("GET", "/api/v3/account"): FakeResponse({"balances": [
        {"asset": "USDT", "free": "100.5", "locked": "0.5"}]})})
    bal = c.get_balance("USD")
    assert bal.currency == "USDT" and bal.total == Decimal("101.0")

    params = s.calls[0][2]
    sig = params.pop("signature")
    expected = hmac.new(b"secret", urlencode(params).encode(), hashlib.sha256).hexdigest()
    assert sig == expected


def test_market_buy_parses_fills_and_fee():
    resp = {
        "orderId": 42, "status": "FILLED", "executedQty": "0.002",
        "cummulativeQuoteQty": "120.00",
        "fills": [{"price": "60000", "qty": "0.002", "commission": "0.12", "commissionAsset": "USDT"}],
    }
    c, s = make_client({("POST", "/api/v3/order"): FakeResponse(resp)})
    r = c.market_buy("BTC-USDT", Decimal("120"))
    assert r.success and r.order_id == "42"
    assert r.filled_price == Decimal("60000") and r.size == Decimal("0.002")
    assert r.fee == Decimal("0.12")
    sent = s.calls[-1][2]
    assert sent["quoteOrderQty"] == "120" and sent["type"] == "MARKET" and sent["side"] == "BUY"


def test_market_buy_below_min_notional_is_not_sent():
    c, s = make_client({})
    r = c.market_buy("BTC-USDT", Decimal("1"))
    assert not r.success and "minimum notional" in r.error
    assert not any(call[0] == "POST" for call in s.calls)


def test_market_sell_rounds_down_to_step_size():
    resp = {"orderId": 7, "status": "FILLED", "executedQty": "0.00123",
            "cummulativeQuoteQty": "73.8", "fills": [
                {"price": "60000", "qty": "0.00123", "commission": "0.0000012", "commissionAsset": "BTC"}]}
    c, s = make_client({("POST", "/api/v3/order"): FakeResponse(resp)})
    r = c.market_sell("BTC-USDT", Decimal("0.001239999"))
    assert s.calls[-1][2]["quantity"] == "0.00123"
    assert r.fee == Decimal("0.0000012") * Decimal("60000")  # comisión en BTC -> USDT


def test_limit_ioc_rounds_price_and_qty():
    resp = {"orderId": 9, "status": "EXPIRED", "executedQty": "0", "cummulativeQuoteQty": "0", "fills": []}
    c, s = make_client({("POST", "/api/v3/order"): FakeResponse(resp)})
    c.limit_buy_ioc("BTC-USDT", Decimal("0.0012345"), Decimal("59999.999"))
    sent = s.calls[-1][2]
    assert sent["timeInForce"] == "IOC" and sent["type"] == "LIMIT"
    assert sent["price"] == "59999.99" and sent["quantity"] == "0.00123"


def test_api_error_becomes_failed_order():
    err = FakeResponse({"code": -2010, "msg": "Account has insufficient balance"}, status=400)
    c, _ = make_client({("POST", "/api/v3/order"): err})
    r = c.market_buy("BTC-USDT", Decimal("100"))
    assert not r.success and "insufficient balance" in r.error


def test_cancel_and_get_order_use_remembered_symbol():
    filled = {"orderId": 5, "status": "NEW", "executedQty": "0", "cummulativeQuoteQty": "0", "fills": []}
    c, s = make_client({
        ("POST", "/api/v3/order"): FakeResponse(filled),
        ("DELETE", "/api/v3/order"): FakeResponse({"status": "CANCELED"}),
        ("GET", "/api/v3/order"): FakeResponse({"status": "NEW"}),
    })
    c.market_buy("BTC-USDT", Decimal("100"))
    assert c.get_order("5") == {"status": "NEW"}
    assert c.cancel_order("5") is True
    assert s.calls[-1][2]["symbol"] == "BTCUSDT"
    assert c.cancel_order("999") is False  # orden desconocida


def test_fee_rate_falls_back_to_default():
    err = FakeResponse({"code": -1, "msg": "boom"}, status=500)
    c, _ = make_client({("GET", "/api/v3/account/commission"): err})
    assert c.get_trading_fee_rate("BTC-USDT") == Decimal("0.001")


def test_candles_shape():
    row = [1700000000000, "1", "2", "0.5", "1.5", "10", 0, 0, 0, 0, 0, 0]
    c, _ = make_client({("GET", "/api/v3/klines"): FakeResponse([row])})
    df = c.get_candles("BTC-USDT", "ONE_HOUR", 1)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df.iloc[0]["close"] == Decimal("1.5")
