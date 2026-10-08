"""Parámetros de las estrategias y del backtester (portado de benclawbot/Claude-trading-bot).

Solo constantes puras: sin lectura de entorno ni credenciales.
"""

SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = 10000.0
MAX_STRATEGIES = 7

# Riesgo
DEFAULT_STOP_LOSS_PCT = 0.025
DEFAULT_TAKE_PROFIT_PCT = 0.055
MAX_POSITION_PCT = 0.35

# Costos (Binance spot)
TRADING_FEE = 0.001
SLIPPAGE = 0.0003

# Backtest / criterio de activación de estrategias
BACKTEST_DAYS = 500
MIN_CAGR_THRESHOLD = 0.30
MIN_WIN_RATE = 0.38
MIN_PROFIT_FACTOR = 1.20

# Solo spot: sin posiciones cortas
ALLOW_SHORT = False

STRATEGY_PARAMS = {
    "EMA5_Momentum": {
        # Source  : Quantified Strategies – best EMA period for Bitcoin (~145% CAGR)
        # Entry LONG  : close crosses above 5-day EMA
        # Entry SHORT : close crosses below 5-day EMA
        "ema_period":      5,
        "atr_sl_mult":     1.5,    # SL = 1.5 × ATR below entry
        "atr_tp_mult":     3.5,    # TP = 3.5 × ATR above entry
        "candle_interval": "1d",
    },
    "DualMA_Crossover": {
        # Source  : Quantified Strategies – 100/250 SMA crossover (~115% CAGR)
        # Entry LONG  : SMA-100 crosses above SMA-250 (golden cross)
        # Entry SHORT : SMA-100 crosses below SMA-250 (death  cross)
        # Requires BACKTEST_DAYS >= 300 for SMA-250 warm-up
        "fast_period":     100,
        "slow_period":     250,
        "atr_sl_mult":     2.0,
        "atr_tp_mult":     5.0,
        "candle_interval": "1d",
    },
    "Regime_RiskOnOff": {
        # Source  : Menthor Q – binary risk-on/risk-off model (~100-200% cumulative/yr)
        # Proxy   : EMA-200 + MACD histogram + RSI all must agree (on-chain metrics
        #           not available via Binance REST API)
        # Entry LONG  : all three conditions bullish (regime switches to RISK-ON)
        # Entry SHORT : all three conditions bearish (regime switches to RISK-OFF)
        "ema_trend":       200,
        "rsi_bull_min":    50,
        "rsi_bear_max":    50,
        "atr_sl_mult":     2.0,
        "atr_tp_mult":     4.5,
        "candle_interval": "4h",
    },
    "PriceMomentum_25": {
        # Source  : Quantified Strategies – 25-day close-to-close momentum (~115% CAGR)
        # Entry LONG  : today's close > close 25 days ago
        # Entry SHORT : today's close < close 25 days ago
        "lookback":        25,
        "atr_sl_mult":     1.5,
        "atr_tp_mult":     4.0,
        "candle_interval": "1d",
    },
    "Residual_MeanRev": {
        # Source  : Medium – BTC-neutral residual mean reversion (Sharpe ~2.3 post-2021)
        # Proxy   : rolling OLS regression on log-price; trade deviations from trend
        #           (original uses altcoin-vs-BTC beta stripping; here we strip BTC's
        #            own trend since we only trade BTCUSDT)
        # Entry LONG  : z-score of residual < -1.5 (below trend, oversold)
        # Entry SHORT : z-score of residual > +1.5 (above trend, overbought)
        "reg_window":      60,
        "zscore_window":   30,
        "entry_threshold": 1.5,
        "atr_sl_mult":     1.8,
        "atr_tp_mult":     3.5,
        "candle_interval": "4h",
    },
    "Donchian_Breakout": {
        # Source  : Quantified Strategies – Donchian breakout on BTC/USD (back to 2015)
        # INVERSE ADX: enter when ADX < threshold (market is calm / consolidating)
        # 15-day lookback offers best risk/reward per research
        # Entry LONG  : close > previous 15-day Donchian upper  AND  ADX < 25
        # Entry SHORT : close < previous 15-day Donchian lower  AND  ADX < 25
        "dc_period":       15,
        "adx_calm_max":    25,
        "atr_sl_mult":     1.5,
        "atr_tp_mult":     3.5,
        "candle_interval": "1d",
    },
    "Blended_MomentumMR": {
        # Source  : Medium – 50/50 momentum + mean-reversion portfolio (best risk-adj)
        # Momentum: 25-period close-to-close (pre-2021 dominant)
        # MR      : RSI + Bollinger Bands (post-2021 dominant)
        # Blend for regime-robust performance across all market cycles
        "momentum_period":  25,
        "rsi_oversold":     38,
        "rsi_overbought":   62,
        "bb_period":        20,
        "bb_std":           2.0,
        "atr_sl_mult":      1.8,
        "atr_tp_mult":      3.8,
        "candle_interval":  "4h",
    },
    "BTC_MomentumBreakout": {
        # Source  : Vault – BTC momentum breakout (backtested 2018-2026, +42% CAGR)
        # Entry   : close > 200d EMA  AND  close > 20d close-high  AND  volume > 1.2× vol MA
        # Exit    : close < 10d low  OR  close < 50d SMA  OR  -8% hard stop
        # TP/SL   : ATR-based take-profit (3× ATR), dual SL (ATR-mult + -8% hard cap)
        # Alpha   : catches sustained BTC breakouts in bull regimes; avoids chop
        "sma_trend":          200,
        "breakout_lookback":   20,
        "volume_ma_period":    20,
        "volume_mult":        1.2,
        "exit_low_period":    10,
        "sma_exit_period":    50,
        "hard_stop_pct":     0.08,
        "atr_tp_mult":       3.0,
        "atr_sl_mult":       1.5,
        "candle_interval":  "1d",
    },
}

# Estrategias "clásicas" del bot original. Sus parámetros NO venían definidos
# (el config original solo traía las 8 nuevas), así que estos valores son
# convencionales, elegidos acá, y no están optimizados.
STRATEGY_PARAMS.update({
    "RSI_Bollinger": {   # mean reversion: RSI extremo + banda de Bollinger
        "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
        "bb_period": 20, "candle_interval": "4h",
    },
    "MACD_Momentum": {   # cruce de MACD a favor de la tendencia (EMA 200)
        "trend_ema": 200, "candle_interval": "1h",
    },
    "EMA_Crossover": {   # cruce EMA 9/21 con filtro EMA 50
        "trend_ema": 50, "candle_interval": "1h",
    },
    "Breakout": {        # ruptura del máximo/mínimo con volumen
        "lookback": 24, "atr_period": 14, "volume_multiplier": 1.5,
        "candle_interval": "4h",
    },
})
