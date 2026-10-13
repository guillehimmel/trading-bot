import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Binance Spot Testnet (testnet.binance.vision) — plata falsa, mismo API que
# producción. Separado a propósito del cliente de datos de mercado
# (src/data/binance_client.py), que sigue pegándole al Binance real para que
# el backtest use precios reales.
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_TESTNET_BASE_URL = "https://testnet.binance.vision"

# "notify" (default): solo manda Telegram, no ejecuta nada — comportamiento
# de siempre. "testnet": ejecuta de verdad contra Binance Spot Testnet (plata
# falsa). Nunca agregamos un modo "live" por accidente acá — eso requiere una
# decisión explícita del humano, no un valor más en una lista.
EXECUTION_MODE = os.environ.get("EXECUTION_MODE", "notify")

# Modelo caro (razonamiento final) solo para el CEO. Modelo barato para los
# departamentos que procesan datos (Mercado, Riesgo, Noticias) — ver costo en
# README. Esto es lo que mantiene el bot dentro de un presupuesto chico/mes.
ANTHROPIC_MODEL_CEO = os.environ.get("ANTHROPIC_MODEL_CEO", "claude-sonnet-5")
ANTHROPIC_MODEL_WORKER = os.environ.get("ANTHROPIC_MODEL_WORKER", "claude-haiku-4-5")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

UNIVERSE_SIZE = int(os.environ.get("UNIVERSE_SIZE", "5"))
STABLECOIN_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS"}

TIMEFRAME = "4h"
CONTEXT_TIMEFRAME = "1d"
KLINES_LIMIT = 200
CONTEXT_KLINES_LIMIT = 100
ORDER_BOOK_DEPTH = 10

MIN_RISK_REWARD = float(os.environ.get("MIN_RISK_REWARD", "2.0"))
MAX_RISK_PER_TRADE_PCT = float(os.environ.get("MAX_RISK_PER_TRADE_PCT", "1.0"))
MAX_PORTFOLIO_RISK_PCT = float(os.environ.get("MAX_PORTFOLIO_RISK_PCT", "5.0"))
ACCOUNT_EQUITY_USD = float(os.environ.get("ACCOUNT_EQUITY_USD", "10000"))

# Si una posición lleva más de este tiempo sin tocar SL ni TP, se cierra al
# precio de mercado (la tesis original ya no aplica) en vez de seguir
# bloqueando ese cupo de riesgo indefinidamente. Ver backtest/run_backtest.py.
TIME_STOP_HOURS = float(os.environ.get("TIME_STOP_HOURS", "72"))

# El CEO propone un entry como orden límite (se notifica, no se ejecuta sola).
# Si el precio no la toca dentro de este tiempo, se descarta — no tiene sentido
# "perseguir" una entrada que la tesis original ya dejó atrás.
LIMIT_ORDER_EXPIRY_HOURS = float(os.environ.get("LIMIT_ORDER_EXPIRY_HOURS", "12"))

# Mínimo operable en Binance spot (en la práctica ~$5-10 según el par). Si el
# tamaño calculado para una operación queda por debajo, se descarta — no tiene
# sentido proponer una orden que no se puede ejecutar.
MIN_NOTIONAL_USD = float(os.environ.get("MIN_NOTIONAL_USD", "10.0"))

# Comisión taker de Binance spot por lado, sin descuento por pagar con BNB
# (0.1% es el valor de lista — ver https://www.binance.com/en/fee/schedule).
# Se cobra dos veces por trade (al abrir y al cerrar).
FEE_PCT = float(os.environ.get("FEE_PCT", "0.1"))

# Tope de concentración: aunque el riesgo calculado pida más (stop muy
# ajustado), ninguna posición individual puede comprometer más de este % del
# equity. Evita que una sola operación "use toda la cuenta".
MAX_POSITION_NOTIONAL_PCT = float(os.environ.get("MAX_POSITION_NOTIONAL_PCT", "20.0"))

# Kill-switch: si el equity cae este % por debajo del máximo histórico de la
# cuenta, se dejan de abrir posiciones nuevas (las que ya están abiertas
# siguen resolviéndose normal) hasta reactivarlo a mano.
MAX_DRAWDOWN_PCT = float(os.environ.get("MAX_DRAWDOWN_PCT", "15.0"))

PORTFOLIO_STATE_PATH = BASE_DIR / "state" / "portfolio.json"
LESSONS_PATH = BASE_DIR / "state" / "lessons.md"
