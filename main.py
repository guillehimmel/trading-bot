import logging
import time
from datetime import datetime, timedelta, timezone

from config.settings import EXECUTION_MODE, TIMEFRAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INTERVAL_HOURS = int(TIMEFRAME.rstrip("h"))

if EXECUTION_MODE == "testnet":
    from src.execution.testnet_orchestrator import run_cycle
elif EXECUTION_MODE == "notify":
    from src.orchestrator.pipeline import run_cycle
else:
    raise ValueError(f"EXECUTION_MODE='{EXECUTION_MODE}' inválido — usar 'notify' o 'testnet'")


def seconds_until_next_candle(interval_hours: int, buffer_seconds: int = 15) -> float:
    now = datetime.now(timezone.utc)
    block_start = now.replace(minute=0, second=0, microsecond=0)
    block_start -= timedelta(hours=block_start.hour % interval_hours)
    next_block = block_start + timedelta(hours=interval_hours)
    return (next_block - now).total_seconds() + buffer_seconds


def main() -> None:
    logger.info("Agencia de trading iniciada (modo=%s). Corre cada cierre de vela de %s.", EXECUTION_MODE, TIMEFRAME)
    while True:
        wait = seconds_until_next_candle(INTERVAL_HOURS)
        logger.info("Esperando %.0fs hasta la próxima vela de %s", wait, TIMEFRAME)
        time.sleep(wait)
        try:
            run_cycle()
        except Exception:
            logger.exception("Error corriendo el ciclo")


if __name__ == "__main__":
    main()
