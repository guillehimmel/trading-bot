"""Valida todas las estrategias registradas contra el sobreajuste.

    python -m src.lab.validate                     # datos de Binance, parámetros por defecto
    python -m src.lab.validate --optimize 50       # optimiza en IS con Optuna y juzga en OOS
    python -m src.lab.validate --csv velas.csv --interval 1d   # datos propios

Una estrategia solo figura como APROBADA si pasa IS/OOS, monkey test y vecinos.
"""

import argparse
from typing import Dict

import pandas as pd

from src.lab import params
from src.lab.indicators import add_all_indicators
from src.lab.strategies import ALL_STRATEGIES
from src.lab.validation import DEFAULTS, evaluate_strategy


def load_csv(path: str) -> pd.DataFrame:
    """CSV con columnas open,high,low,close,volume (y opcionalmente timestamp)."""
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    if "timestamp" in df.columns:
        df.index = pd.to_datetime(df["timestamp"], utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1000, help="días de historia (default 1000)")
    ap.add_argument("--symbol", default=params.SYMBOL)
    ap.add_argument("--optimize", type=int, default=0, metavar="N",
                    help="trials de Optuna sobre IS por estrategia (0 = parámetros por defecto)")
    ap.add_argument("--monkeys", type=int, default=DEFAULTS["n_monkeys"])
    ap.add_argument("--csv", help="CSV propio en vez de bajar de Binance")
    ap.add_argument("--interval", help="intervalo del CSV (1h/4h/1d); solo corre las estrategias de ese intervalo")
    ap.add_argument("--only", help="nombre de una estrategia")
    args = ap.parse_args()

    cache: Dict[str, pd.DataFrame] = {}
    approved = []
    print(f"{'estrategia':22s} {'int':3s} {'trIS':>5s} {'PFis':>5s} {'trOOS':>5s} {'PFoos':>5s} "
          f"{'DDoos':>6s} {'p-azar':>6s} {'vecinos':>7s}  veredicto")
    for cls in ALL_STRATEGIES:
        probe = cls()
        if args.only and probe.name != args.only:
            continue
        iv = probe.candle_interval
        if args.csv:
            if args.interval and iv != args.interval:
                continue
            if iv not in cache:
                cache[iv] = add_all_indicators(load_csv(args.csv))
        elif iv not in cache:
            from src.lab.data import get_klines_since
            cache[iv] = add_all_indicators(get_klines_since(args.symbol, iv, args.days))
        try:
            ev = evaluate_strategy(cls, cache[iv], optimize_trials=args.optimize,
                                   cfg={"n_monkeys": args.monkeys})
        except ValueError as e:
            print(f"{probe.name:22s} {iv:3s}  omitida: {e}")
            continue
        p = f"{ev.monkey.p_value:.2f}" if ev.monkey else "  - "
        nb = f"{ev.neighbors.fraction_profitable:.0%}" if ev.neighbors else " - "
        verdict = "APROBADA" if ev.approved else "descartada: " + "; ".join(ev.reasons)
        print(f"{ev.name:22s} {iv:3s} {ev.res_is.total_trades:5d} {ev.res_is.profit_factor:5.2f} "
              f"{ev.res_oos.total_trades:5d} {ev.res_oos.profit_factor:5.2f} "
              f"{ev.res_oos.max_drawdown:6.1%} {p:>6s} {nb:>7s}  {verdict}")
        if ev.approved:
            approved.append(ev)
            if ev.params:
                print(f"{'':22s} parámetros optimizados: {ev.params}")

    print(f"\n{len(approved)} estrategia(s) aprobada(s).")
    if not approved:
        print("Que ninguna apruebe es un resultado normal y útil: significa que no hay ventaja "
              "demostrada con estos datos. No bajes los umbrales para que pase alguna.")


if __name__ == "__main__":
    main()
