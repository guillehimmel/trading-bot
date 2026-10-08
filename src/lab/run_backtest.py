"""Corre el backtest de todas las estrategias registradas.

    python -m src.lab.run_backtest [--days 500] [--symbol BTCUSDT]
"""

import argparse

from src.lab import params
from src.lab.backtester import Backtester
from src.lab.indicators import add_all_indicators
from src.lab.strategies import ALL_STRATEGIES
from src.lab.data import get_klines_since


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=params.BACKTEST_DAYS)
    ap.add_argument("--symbol", default=params.SYMBOL)
    args = ap.parse_args()

    cache = {}
    for cls in ALL_STRATEGIES:
        strat = cls()
        iv = strat.candle_interval
        if iv not in cache:
            cache[iv] = add_all_indicators(get_klines_since(args.symbol, iv, args.days))
        res = Backtester(strat, cache[iv]).run()
        print(res.summary())


if __name__ == "__main__":
    main()
