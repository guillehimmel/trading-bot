"""
Parabolic SAR (seguimiento de tendencia, solo long)
───────────────────────────────────────────────────
Idea del curso AxelMunguiaQuintero/Trading-Cuantitativo-en-Python (MIT), módulo 09.

  Entrada : el SAR pasa de arriba a abajo del precio (giro alcista) y el cierre
            está sobre la EMA 50 (filtro para no comprar giros en tendencia bajista)
  Salida  : el SAR vuelve a quedar sobre el precio (exit_signal), o stop en el SAR
"""

import pandas as pd

from src.lab import params as config
from src.lab.indicators import parabolic_sar
from .base_strategy import BaseStrategy, Signal, SignalType


class ParabolicSARStrategy(BaseStrategy):

    def __init__(self, params: dict = None):
        defaults = config.STRATEGY_PARAMS["Parabolic_SAR"].copy()
        if params:
            defaults.update(params)
        super().__init__("Parabolic_SAR", defaults)

    @property
    def min_candles(self) -> int:
        return 120   # EMA 50 + calentamiento del SAR

    @property
    def max_hold_candles(self) -> int:
        return 200

    @property
    def candle_interval(self) -> str:
        return self.params.get("candle_interval", "4h")

    def _sar(self, df: pd.DataFrame):
        return parabolic_sar(df["high"], df["low"],
                             float(self.params["step"]), float(self.params["max_step"]))

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.min_candles:
            return Signal(SignalType.HOLD, 0.0)
        sar, up = self._sar(df)
        if not (bool(up.iloc[-1]) and not bool(up.iloc[-2])):
            return Signal(SignalType.HOLD, 0.0)

        close = float(df["close"].iloc[-1])
        if "ema_50" in df and close < float(df["ema_50"].iloc[-1]):
            return Signal(SignalType.HOLD, 0.0)
        stop = float(sar.iloc[-1])
        if not (0 < stop < close):
            return Signal(SignalType.HOLD, 0.0)
        atr = float(df["atr_14"].iloc[-1]) if "atr_14" in df else close * 0.02
        return Signal(
            SignalType.BUY, 0.58,
            stop_loss=stop,
            take_profit=close + float(self.params["atr_tp_mult"]) * atr,
            metadata={"sar": stop},
        )

    def exit_signal(self, df: pd.DataFrame) -> bool:
        if len(df) < self.min_candles:
            return False
        _, up = self._sar(df)
        return not bool(up.iloc[-1])
