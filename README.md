# trading-bot

Bot de trading personal (uso privado). Paper trading / testnet por defecto.

Se arma combinando lo mejor de tres proyectos:

| Origen | Licencia | Qué se toma |
|---|---|---|
| [Byte-Ventures/claude-trader](https://github.com/Byte-Ventures/claude-trader) | AGPL-3.0 | Base: exchanges, señales, seguridad, paper trading, dashboard |
| [benclawbot/Claude-trading-bot](https://github.com/benclawbot/Claude-trading-bot) | sin licencia | Estrategias, backtester, cliente Binance |
| [hugoguerrap/crypto-claude-desk](https://github.com/hugoguerrap/crypto-claude-desk) | MIT | Fuentes de datos (futuros, DeFiLlama, microestructura) |
| [lauragp28/generador-trading-claude-code](https://github.com/lauragp28/generador-trading-claude-code) | sin licencia | Metodología de validación (IS/OOS, monkey test, vecinos) |
| [jmoraleses/Backtrader-optuna](https://github.com/jmoraleses/Backtrader-optuna) | sin licencia | Solo la idea de optimizar con Optuna (sin código) |

Repo privado: no publicar, por la AGPL y por el código sin licencia.

## Estado

- **Base** (`src/`, `config/`): exchanges, puntaje de señales, seguridad, paper trading, dashboard, Telegram.
- **Binance Spot** (`src/api/binance_client.py`): testnet por defecto (`BINANCE_TESTNET=true`). Par: `TRADING_PAIR=BTC-USDT`.
- **Laboratorio de estrategias** (`src/lab/`): 12 estrategias, backtester **solo-long** con ejecución en la **apertura de la vela siguiente** y cargador de velas públicas de Binance.
- **Validación anti-sobreajuste** (`src/lab/validation.py`): split IS/OOS, monkey test, chequeo de vecinos y optimización con Optuna solo sobre IS.

```bash
pip install -r requirements.txt
python -m pytest --no-cov                 # tests
python -m src.lab.run_backtest --days 500 # backtest con datos reales de Binance
python -m src.lab.validate --days 2000    # ¿alguna estrategia sobrevive IS/OOS + azar + vecinos?
python -m src.lab.validate --optimize 50  # igual, optimizando con Optuna solo en IS
cp .env.example .env                      # EXCHANGE=binance, TRADING_MODE=paper
python -m src.main                        # arranca en paper trading
```

## Pendiente

- Conectar las estrategias de `src/lab/` como fuente de señales del daemon (hoy corren solo en backtest).
- Traer las fuentes de datos de crypto-claude-desk (funding, open interest, DeFiLlama).
- Validar con backtest real y paper trading de varias semanas antes de pensar en `live`.
- Los resultados de backtest del README original (CAGR 50%+) no están verificados: tomarlos con escepticismo.
