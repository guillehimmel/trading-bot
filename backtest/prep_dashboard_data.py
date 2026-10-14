"""Prepara los datos para sincronizar el dashboard (ver Artifact con db) a
partir de un backtest ya corrido: arma los trades (con velas reales de
Binance) y la curva de equity en el formato que espera la colección "trades"
y el doc "equity/curve" de la base de datos del artifact.

No escribe a la base de datos — eso lo hace Claude llamando a la tool
Artifact (write_db) con la salida de este script, porque esa tool solo está
disponible dentro de una sesión de Claude Code.

Uso:
    python -m backtest.prep_dashboard_data [prefijo_del_run]

Si no se pasa prefijo, usa el cycles CSV más reciente en backtest/results/.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import ACCOUNT_EQUITY_USD
from src.data import binance_client

RESULTS_DIR = Path(__file__).parent / "results"
OUT_PATH = Path(__file__).parent / "results" / "dashboard_sync.json"


def parse_iso(s: str) -> int:
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def latest_run_prefix() -> str:
    cycles_csvs = sorted(p for p in RESULTS_DIR.glob("*.csv") if not p.stem.endswith("_trades"))
    if not cycles_csvs:
        raise FileNotFoundError(f"No hay ningún backtest corrido en {RESULTS_DIR}")
    return cycles_csvs[-1].stem


def main() -> None:
    prefix = sys.argv[1] if len(sys.argv) > 1 else latest_run_prefix()
    cycles_csv = RESULTS_DIR / f"{prefix}.csv"
    trades_csv = RESULTS_DIR / f"{prefix}_trades.csv"

    with cycles_csv.open() as f:
        cycle_rows = list(csv.DictReader(f))
    with trades_csv.open() as f:
        closed_trades = list(csv.DictReader(f))

    closed_keys = {(t["symbol"], t["opened_at"]) for t in closed_trades}
    trade_cycle_rows = [r for r in cycle_rows if r["decision"] == "trade"]
    open_rows = [r for r in trade_cycle_rows if (r["symbol"], r["timestamp"]) not in closed_keys]

    now_ms = parse_iso(cycle_rows[-1]["timestamp"])
    pad_ms = 6 * 3_600_000
    trades_out = []

    for t in closed_trades:
        opened_ms = parse_iso(t["opened_at"])
        closed_ms = parse_iso(t["closed_at"])
        candles = binance_client.get_klines_range(t["symbol"], "4h", opened_ms - pad_ms, closed_ms + pad_ms)
        trades_out.append({
            "symbol": t["symbol"],
            "direccion": t["direccion"],
            "entry": float(t["entry"]),
            "stop_loss": float(t["stop_loss"]),
            "take_profit": float(t["take_profit"]),
            "risk_reward": float(t["risk_reward"]),
            "tamano_posicion_pct": float(t.get("sized_pct") or t.get("tamano_posicion_pct_propuesto") or 0),
            "status": "closed",
            "outcome": t["outcome"],
            "pnl": float(t["pnl"]),
            "fees": float(t["fees"]),
            "duration_hours": float(t["duration_hours"]),
            "opened_at": opened_ms,
            "closed_at": closed_ms,
            "razonamiento": t.get("razonamiento") or None,
            "conflictos_resueltos": t.get("conflictos_resueltos") or None,
            "candles": [{"t": c["open_time"], "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]} for c in candles],
        })

    for r in open_rows:
        opened_ms = parse_iso(r["timestamp"])
        candles = binance_client.get_klines_range(r["symbol"], "4h", opened_ms - pad_ms, now_ms + pad_ms)
        tp_raw = r["take_profit"]
        try:
            tp = float(tp_raw.strip("[]").split(",")[0].strip().strip("'\""))
        except Exception:
            tp = None
        trades_out.append({
            "symbol": r["symbol"],
            "direccion": r["direccion"],
            "entry": float(r.get("entry_propuesto") or r.get("entry_fill") or 0),
            "stop_loss": float(r["stop_loss"]),
            "take_profit": tp,
            "risk_reward": float(r["risk_reward"]) if r["risk_reward"] else None,
            "tamano_posicion_pct": None,
            "status": "open",
            "outcome": None,
            "pnl": None,
            "duration_hours": (now_ms - opened_ms) / 3_600_000,
            "opened_at": opened_ms,
            "closed_at": None,
            "razonamiento": None,
            "conflictos_resueltos": None,
            "candles": [{"t": c["open_time"], "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]} for c in candles],
        })

    equity_curve = [{"t": parse_iso(r["timestamp"]), "equity": round(float(r["equity_after"]), 2)} for r in cycle_rows]

    out = {
        "trades": trades_out,
        "equity_curve": equity_curve,
        "equity_inicial": ACCOUNT_EQUITY_USD,
    }
    OUT_PATH.write_text(json.dumps(out))
    print(f"Escrito {OUT_PATH} ({len(trades_out)} trades, {len(equity_curve)} puntos de equity)")
    print("Para sincronizar el dashboard, pedile a Claude que lea este archivo y lo suba con Artifact write_db.")


if __name__ == "__main__":
    main()
