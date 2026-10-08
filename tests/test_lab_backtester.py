"""El backtester portado debe ser solo-long (Binance spot no permite shorts)."""

import numpy as np
import pandas as pd

from src.lab import params
from src.lab.backtester import Backtester
from src.lab.indicators import add_all_indicators
from src.lab.strategies import ALL_STRATEGIES


def _synthetic(n=900, seed=1):
    rng = np.random.default_rng(seed)
    c = 20000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    df = pd.DataFrame(
        {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
         "volume": rng.uniform(100, 200, n)},
        index=pd.date_range("2022-01-01", periods=n, freq="1D", tz="UTC"),
    )
    return add_all_indicators(df)


def test_all_registered_strategies_run_and_never_short():
    assert params.ALLOW_SHORT is False
    df = _synthetic()
    assert len(ALL_STRATEGIES) >= 8
    for cls in ALL_STRATEGIES:
        result = Backtester(cls(), df).run()
        assert all(t.side == "LONG" for t in result.trades), cls.__name__
