"""
SuperTrend (seguimiento de tendencia, solo long)
────────────────────────────────────────────────
Idea y parámetros base del curso AxelMunguiaQuintero/Trading-Cuantitativo-en-Python
(MIT), módulos 09 y 10. A diferencia del curso, acá hay comisiones, ejecución en la
apertura de la vela siguiente y no se abren shorts (Binance spot).

  Entrada : la dirección pasa de bajista a alcista en la última vela cerrada
  Salida  : la dirección vuelve a bajista (exit_signal), o stop en la línea de SuperTrend
"""

import pandas as pd

from src.lab import params as config
from src.lab.indicators import supertrend
from .base_strategy import BaseStrategy, Signal, SignalType


class SuperTrendStrategy(BaseStrategy):

    def __init__(self, params: dict = None):
        defaults = config.STRATEGY_PARAMS["SuperTrend"].copy()
        if params:
            defaults.update(params)
        super().__init__("SuperTrend", defaults)

    @property
    def min_candles(self) -> int:
        return int(self.params["length"]) * 8 + 40   # el SuperTrend es recursivo: necesita calentar

    @property
    def max_hold_candles(self) -> int:
        return 200

    @property
    def candle_interval(self) -> str:
        return self.params.get("candle_interval", "4h")

    def _direction(self, df: pd.DataFrame):
        return supertrend(df["high"], df["low"], df["close"],
                          int(self.params["length"]), float(self.params["factor"]))

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.min_candles:
            return Signal(SignalType.HOLD, 0.0)
        line, direction = self._direction(df)
        if not (direction.iloc[-1] == 1 and direction.iloc[-2] == -1):
            return Signal(SignalType.HOLD, 0.0)

        close = float(df["close"].iloc[-1])
        stop = float(line.iloc[-1])
        if not (0 < stop < close):
            return Signal(SignalType.HOLD, 0.0)
        atr = float(df["atr_14"].iloc[-1]) if "atr_14" in df else close * 0.02
        return Signal(
            SignalType.BUY, 0.60,
            stop_loss=stop,
            take_profit=close + float(self.params["atr_tp_mult"]) * atr,
            metadata={"supertrend": stop},
        )

    def exit_signal(self, df: pd.DataFrame) -> bool:
        if len(df) < self.min_candles:
            return False
        _, direction = self._direction(df)
        return bool(direction.iloc[-1] == -1)
