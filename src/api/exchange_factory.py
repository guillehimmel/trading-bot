"""
Exchange factory for creating the appropriate exchange client.

Based on the EXCHANGE setting, creates a Coinbase, Kraken or Binance client.
"""

import structlog

from config.settings import Exchange, Settings, get_settings
from src.api.binance_client import BinanceClient
from src.api.coinbase_client import CoinbaseClient
from src.api.exchange_protocol import ExchangeClient
from src.api.kraken_client import KrakenClient

logger = structlog.get_logger(__name__)


def create_exchange_client(settings: Settings | None = None) -> ExchangeClient:
    """
    Create an exchange client based on settings.

    Args:
        settings: Optional settings instance. If not provided, uses global settings.

    Returns:
        Exchange client implementing ExchangeClient protocol

    Raises:
        ValueError: If required credentials are missing
    """
    if settings is None:
        settings = get_settings()

    if settings.exchange == Exchange.COINBASE:
        return _create_coinbase_client(settings)
    elif settings.exchange == Exchange.KRAKEN:
        return _create_kraken_client(settings)
    elif settings.exchange == Exchange.BINANCE:
        return _create_binance_client(settings)
    else:
        raise ValueError(f"Unsupported exchange: {settings.exchange}")


def _create_coinbase_client(settings: Settings) -> CoinbaseClient:
    """Create a Coinbase client from settings."""
    # Prefer key file if provided
    if settings.coinbase_key_file:
        logger.info("creating_coinbase_client", source="key_file")
        return CoinbaseClient(key_file=settings.coinbase_key_file)

    # Fall back to key/secret
    if settings.coinbase_api_key and settings.coinbase_api_secret:
        logger.info("creating_coinbase_client", source="api_key")
        return CoinbaseClient(
            api_key=settings.coinbase_api_key.get_secret_value(),
            api_secret=settings.coinbase_api_secret.get_secret_value(),
        )

    raise ValueError(
        "Coinbase credentials not configured. "
        "Set COINBASE_KEY_FILE or COINBASE_API_KEY + COINBASE_API_SECRET"
    )


def _create_kraken_client(settings: Settings) -> KrakenClient:
    """Create a Kraken client from settings."""
    if not settings.kraken_api_key or not settings.kraken_api_secret:
        raise ValueError(
            "Kraken credentials not configured. "
            "Set KRAKEN_API_KEY and KRAKEN_API_SECRET in your .env file"
        )

    logger.info("creating_kraken_client")
    return KrakenClient(
        api_key=settings.kraken_api_key.get_secret_value(),
        api_secret=settings.kraken_api_secret.get_secret_value(),
    )


def _create_binance_client(settings: Settings) -> BinanceClient:
    """Create a Binance client from settings."""
    if not settings.binance_api_key or not settings.binance_api_secret:
        raise ValueError(
            "Binance credentials not configured. "
            "Set BINANCE_API_KEY and BINANCE_API_SECRET in your .env file"
        )

    logger.info("creating_binance_client", testnet=settings.binance_testnet)
    return BinanceClient(
        api_key=settings.binance_api_key.get_secret_value(),
        api_secret=settings.binance_api_secret.get_secret_value(),
        testnet=settings.binance_testnet,
    )


def get_exchange_name(settings: Settings | None = None) -> str:
    """Get the name of the configured exchange."""
    if settings is None:
        settings = get_settings()
    return settings.exchange.value.title()
