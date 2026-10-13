# trading-bot

Agencia de trading de criptomonedas con agentes especializados (mercado,
gestión de riesgo, análisis de noticias) que reportan a un agente "CEO" que
consolida todo en una señal de trade con entry/SL/TP, notificada por Telegram.
Opera sobre velas de 4h, risk/reward mínimo 1:2. Solo notifica — no ejecuta
órdenes.

## Costo

El punto más caro de un sistema así es mandarle a un LLM datos de mercado
crudos (velas, order book) en cada ciclo. Dos decisiones lo mantienen barato:

1. **Indicadores, no velas crudas** (`src/analysis/indicators.py`): las velas
   se bajan de Binance y se resumen a ~10 números (ATR, RSI, medias, cambio %,
   volumen relativo) en Python *antes* de llamar a Claude. Esto reduce el
   payload por activo de ~20.000 tokens a ~150.
2. **Split de modelos**: Mercado, Riesgo y Noticias (que solo resumen datos)
   corren en Haiku 4.5 (`ANTHROPIC_MODEL_WORKER`, $1/$5 por MTok). Solo el CEO
   —la decisión final— corre en Sonnet 5 (`ANTHROPIC_MODEL_CEO`, $3/$15 por
   MTok).

Con el universo default (5 activos) y cadencia de 4h (6 ciclos/día ≈ 180/mes),
el costo estimado es de **~$5/mes**.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# completá ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env
python main.py
```

## Estructura

- `src/agents/prompts/` — prompt de cada departamento (mercado, riesgo, noticias, CEO).
- `src/agents/runner.py` — llama a Claude con el prompt de un departamento (modelo explícito por llamada) y parsea su JSON.
- `src/analysis/indicators.py` — resume velas crudas a indicadores escalares (ver "Costo" arriba).
- `src/data/` — clientes de Binance (velas/order book), CoinGecko (ranking por market cap), noticias por RSS (CoinDesk/CoinTelegraph/Decrypt, gratis) y Binance Web3 (contexto on-chain opcional).
- `src/state/portfolio.py` — estado de cartera (equity, posiciones abiertas), por ahora mantenido a mano en `state/portfolio.json` ya que la agencia no ejecuta operaciones.
- `src/orchestrator/pipeline.py` — corre Mercado/Riesgo/Noticias en paralelo, consolida con el CEO y notifica.
- `main.py` — loop que dispara un ciclo completo en cada cierre de vela de 4h.
- `backtest/run_backtest.py` — backtest piloto sobre histórico real de Binance (ver limitaciones en el propio archivo).
