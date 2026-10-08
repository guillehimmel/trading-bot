from .base_strategy import BaseStrategy, Signal, SignalType
from .ema5_momentum import EMA5MomentumStrategy
from .dual_ma_crossover import DualMACrossoverStrategy
from .regime_riskoff import RegimeRiskOffStrategy
from .price_momentum_25 import PriceMomentum25Strategy
from .residual_mean_reversion import ResidualMeanReversionStrategy
from .donchian_breakout import DonchianBreakoutStrategy
from .blended_momentum_mr import BlendedMomentumMRStrategy
from .btc_momentum_breakout import BTCMomentumBreakoutStrategy
from .rsi_bollinger import RSIBollingerStrategy
from .macd_momentum import MACDMomentumStrategy
from .ema_crossover import EMACrossoverStrategy
from .breakout import BreakoutStrategy
from .supertrend import SuperTrendStrategy
from .parabolic_sar import ParabolicSARStrategy

ALL_STRATEGIES = [
    EMA5MomentumStrategy,           # 1 – Short-window EMA momentum     (~145% CAGR)
    DualMACrossoverStrategy,        # 2 – 100/250 SMA dual crossover     (~115% CAGR)
    RegimeRiskOffStrategy,          # 3 – Risk-On/Off regime model       (variable, high)
    PriceMomentum25Strategy,        # 4 – 25-day close-to-close momentum (~115% CAGR)
    ResidualMeanReversionStrategy,  # 5 – Residual mean reversion        (Sharpe ~2.3)
    DonchianBreakoutStrategy,        # 6 – Donchian breakout + inverse ADX (competitive)
    BlendedMomentumMRStrategy,      # 7 – 50/50 momentum + MR blend      (best risk-adj)
    BTCMomentumBreakoutStrategy,    # 8 – BTC momentum breakout          (+42% CAGR vault)
    RSIBollingerStrategy,           # 9 – RSI + Bollinger mean reversion (clásica)
    MACDMomentumStrategy,           # 10 – MACD a favor de EMA 200       (clásica)
    EMACrossoverStrategy,           # 11 – Cruce EMA 9/21                (clásica)
    BreakoutStrategy,               # 12 – Ruptura con volumen           (clásica)
    SuperTrendStrategy,             # 13 – SuperTrend con salida por giro (curso Axel)
    ParabolicSARStrategy,           # 14 – Parabolic SAR con filtro EMA50 (curso Axel)
]
# MLAdaptiveStrategy no se registra: solo aprende de trades cerrados en vivo y en un
# backtest quedaría reducida a una regla fija; además lee/guarda un modelo en disco.
