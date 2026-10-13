import json

from config.settings import ACCOUNT_EQUITY_USD, MAX_PORTFOLIO_RISK_PCT, MAX_RISK_PER_TRADE_PCT, PORTFOLIO_STATE_PATH

DEFAULT_STATE = {
    "equity": ACCOUNT_EQUITY_USD,
    "posiciones_abiertas": [],
    "drawdown_7d_pct": 0.0,
    "drawdown_30d_pct": 0.0,
}


def load() -> dict:
    """Reads the local portfolio snapshot (manually maintained for now, since
    the agency only notifies and doesn't execute orders)."""
    if not PORTFOLIO_STATE_PATH.exists():
        save(DEFAULT_STATE)
        return dict(DEFAULT_STATE)
    return json.loads(PORTFOLIO_STATE_PATH.read_text())


def save(state: dict) -> None:
    PORTFOLIO_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def risk_manager_input(state: dict) -> dict:
    exposicion_actual_pct = sum(
        pos.get("riesgo_pct", 0.0) for pos in state["posiciones_abiertas"]
    )
    return {
        "equity": state["equity"],
        "posiciones_abiertas": state["posiciones_abiertas"],
        "drawdown_7d_pct": state["drawdown_7d_pct"],
        "drawdown_30d_pct": state["drawdown_30d_pct"],
        "exposicion_actual_pct": exposicion_actual_pct,
        "max_portfolio_risk_pct": MAX_PORTFOLIO_RISK_PCT,
        "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
    }
