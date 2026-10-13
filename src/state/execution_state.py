import json
from pathlib import Path

from config.settings import ACCOUNT_EQUITY_USD, BASE_DIR

STATE_PATH = BASE_DIR / "state" / "testnet_execution.json"

DEFAULT_STATE = {
    "peak_equity_usdt": ACCOUNT_EQUITY_USD,
    "kill_switch_active": False,
    "pending_orders": [],
    "open_positions": [],
    "closed_trades": [],
}


def load() -> dict:
    if not STATE_PATH.exists():
        save(DEFAULT_STATE)
        return json.loads(json.dumps(DEFAULT_STATE))
    return json.loads(STATE_PATH.read_text())


def save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))
