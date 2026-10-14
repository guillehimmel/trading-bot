"""Backtest piloto de la agencia de trading.

Corre el pipeline real (Mercado + Riesgo + CEO) sobre velas históricas de Binance,
en el timeframe configurado en config/settings.py (TIMEFRAME), avanzando vela por
vela sin mirar al futuro. Mercado y Riesgo corren en el modelo barato
(ANTHROPIC_MODEL_WORKER) y solo el CEO en el caro — ver README para el costo.
Limitaciones conocidas (ver README para el detalle):

- Sin Noticias ni contexto on-chain: no hay fuente histórica real para ninguno
  de los dos, así que el CEO recibe "noticias.disponible: false" en vez de
  datos inventados.
- Sin order book histórico: Binance no lo expone vía REST, se pasa null.
- Universo fijo (no se puede reconstruir el top-10 por market cap del pasado).
- Si SL y TP caen dentro de la misma vela, se asume que el SL se toca primero
  (supuesto conservador — no hay datos intra-vela para saber el orden real).

Gestión de riesgo (ver src/risk/sizing.py):
- El tamaño de cada posición se recalcula en código a partir del riesgo real
  (nunca se confía en el % que propone el CEO): se acota a MAX_RISK_PER_TRADE_PCT
  y al riesgo disponible que informó el departamento de Riesgo, y se convierte a
  cantidad real de unidades usando la distancia al stop. Si esa cantidad implica
  más notional que el equity disponible (apalancamiento que no existe en spot) o
  menos que MIN_NOTIONAL_USD (no se puede ejecutar en Binance), el trade se
  rechaza y queda en portfolio.rejected_trades.
- Time-stop (TIME_STOP_HOURS, default 72h): si una posición no toca SL ni TP en
  ese tiempo, se cierra al precio de mercado — evita que una posición se quede
  bloqueando el cupo de riesgo por días sin que la tesis se termine de jugar.

Uso:
    python -m backtest.run_backtest --days 21 --pairs BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT
"""

import argparse
import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import (
    ACCOUNT_EQUITY_USD,
    ANTHROPIC_MODEL_CEO,
    ANTHROPIC_MODEL_WORKER,
    CONTEXT_KLINES_LIMIT,
    CONTEXT_TIMEFRAME,
    FEE_PCT,
    KLINES_LIMIT,
    LIMIT_ORDER_EXPIRY_HOURS,
    MAX_DRAWDOWN_PCT,
    MAX_PORTFOLIO_RISK_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MIN_RISK_REWARD,
    TIME_STOP_HOURS,
    TIMEFRAME,
)
from src.agents import runner
from src.analysis import indicators
from src.data import binance_client
from src.risk import sizing
from src.state import lessons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
NO_NEWS_INPUT = {
    "disponible": False,
    "motivo": "backtest histórico: no hay fuente de noticias ni de contexto on-chain con cobertura del pasado",
}


def fetch_history(pair: str, start_ms: int, end_ms: int) -> dict:
    lookback_principal_ms = KLINES_LIMIT * binance_client.INTERVAL_MS[TIMEFRAME]
    lookback_contexto_ms = CONTEXT_KLINES_LIMIT * binance_client.INTERVAL_MS[CONTEXT_TIMEFRAME]
    klines_principal = binance_client.get_klines_range(
        pair, TIMEFRAME, start_ms - lookback_principal_ms, end_ms
    )
    klines_contexto = binance_client.get_klines_range(
        pair, CONTEXT_TIMEFRAME, start_ms - lookback_contexto_ms, end_ms
    )
    return {"principal": klines_principal, "contexto": klines_contexto}


def window(klines: list[dict], upto_open_time: int, limit: int) -> list[dict]:
    eligible = [k for k in klines if k["open_time"] <= upto_open_time]
    return eligible[-limit:]


TIME_STOP_MS = int(TIME_STOP_HOURS * 3_600_000)
LIMIT_ORDER_EXPIRY_MS = int(LIMIT_ORDER_EXPIRY_HOURS * 3_600_000)


class PortfolioSim:
    def __init__(self, equity: float):
        self.equity = equity
        self.peak_equity = equity
        self.open_positions: list[dict] = []
        self.pending_orders: list[dict] = []
        self.closed_trades: list[dict] = []
        self.rejected_trades: list[dict] = []
        self.expired_orders: list[dict] = []
        self.kill_switch_rejections = 0

    def capital_disponible(self) -> float:
        """Efectivo libre: lo que no está ya comprometido en posiciones
        abiertas. Sin esto, dos posiciones simultáneas podían cada una
        calcularse contra el equity total, como si la plata se duplicara."""
        comprometido = sum(p["notional"] for p in self.open_positions)
        return max(0.0, self.equity - comprometido)

    def kill_switch_active(self) -> bool:
        if self.peak_equity <= 0:
            return False
        return self.equity <= self.peak_equity * (1 - MAX_DRAWDOWN_PCT / 100)

    def risk_input(self) -> dict:
        exposicion = sum(p["sized_pct"] for p in self.open_positions)
        drawdown = 0.0
        if self.peak_equity > 0:
            drawdown = max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100)
        return {
            "equity": self.equity,
            "posiciones_abiertas": self.open_positions,
            "drawdown_7d_pct": drawdown,
            "drawdown_30d_pct": drawdown,
            "exposicion_actual_pct": exposicion,
            "max_portfolio_risk_pct": MAX_PORTFOLIO_RISK_PCT,
            "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
        }

    def resolve_positions(self, candles_by_pair: dict, open_time: int) -> None:
        still_open = []
        for pos in self.open_positions:
            candle = candles_by_pair.get(pos["symbol"], {}).get(open_time)
            if candle is None:
                still_open.append(pos)
                continue

            if pos["direccion"] == "long":
                hit_sl = candle["low"] <= pos["stop_loss"]
                hit_tp = candle["high"] >= pos["take_profit"]
            else:
                hit_sl = candle["high"] >= pos["stop_loss"]
                hit_tp = candle["low"] <= pos["take_profit"]

            if hit_sl:
                exit_price, exit_reason = pos["stop_loss"], "stop_loss"
            elif hit_tp:
                exit_price, exit_reason = pos["take_profit"], "take_profit"
            elif (open_time - pos["opened_at"]) >= TIME_STOP_MS:
                exit_price, exit_reason = candle["close"], "time_stop"
            else:
                still_open.append(pos)
                continue

            if pos["direccion"] == "long":
                pnl_gross = pos["quantity"] * (exit_price - pos["entry"])
            else:
                pnl_gross = pos["quantity"] * (pos["entry"] - exit_price)

            entry_notional = pos["quantity"] * pos["entry"]
            exit_notional = pos["quantity"] * exit_price
            fees = (entry_notional + exit_notional) * FEE_PCT / 100
            pnl = pnl_gross - fees
            outcome = "win" if pnl >= 0 else "loss"

            self.equity += pnl
            self.peak_equity = max(self.peak_equity, self.equity)
            duration_hours = (open_time - pos["opened_at"]) / 3_600_000
            self.closed_trades.append(
                {
                    **pos,
                    "outcome": outcome,
                    "pnl_gross": pnl_gross,
                    "fees": fees,
                    "exit_reason": exit_reason,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "closed_at": open_time,
                    "duration_hours": duration_hours,
                }
            )
        self.open_positions = still_open

    def add_pending_order(self, ceo_output: dict, riesgo_disponible_pct: float, proposed_at: int) -> None:
        """El CEO propone un entry como orden límite (así se usaría en producción:
        se notifica, un humano la coloca) — el backtest ya no la llena de una al
        cierre actual, espera a que el precio la toque de verdad en una vela
        siguiente, o la descarta si no pasa en LIMIT_ORDER_EXPIRY_HOURS."""
        self.pending_orders.append(
            {
                "symbol": ceo_output["symbol"],
                "direccion": ceo_output["direccion"],
                "entry": ceo_output["entry"],
                "stop_loss": ceo_output["stop_loss"],
                "take_profit": ceo_output["take_profit"][0],
                "risk_reward": ceo_output["risk_reward"],
                "tamano_posicion_pct": ceo_output["tamano_posicion_pct"],
                "riesgo_disponible_pct": riesgo_disponible_pct,
                "razonamiento": ceo_output.get("razonamiento"),
                "conflictos_resueltos": ceo_output.get("conflictos_resueltos"),
                "proposed_at": proposed_at,
                "expires_at": proposed_at + LIMIT_ORDER_EXPIRY_MS,
            }
        )

    def resolve_pending_orders(self, candles_by_pair: dict, open_time: int) -> None:
        still_pending = []
        for order in self.pending_orders:
            candle = candles_by_pair.get(order["symbol"], {}).get(open_time)
            if candle is None:
                still_pending.append(order)
                continue
            if open_time >= order["expires_at"]:
                self.expired_orders.append(order)
                logger.info("Orden límite de %s expiró sin tocar el entry (%.8g)", order["symbol"], order["entry"])
                continue
            if not (candle["low"] <= order["entry"] <= candle["high"]):
                still_pending.append(order)
                continue
            self._fill_order(order, open_time)
        self.pending_orders = still_pending

    def _fill_order(self, order: dict, open_time: int) -> None:
        if self.kill_switch_active():
            self.kill_switch_rejections += 1
            logger.warning(
                "Orden de %s tocó el entry pero el kill-switch está activo (drawdown ≥ %.0f%%) — no se abren posiciones nuevas",
                order["symbol"], MAX_DRAWDOWN_PCT,
            )
            return

        result = sizing.size_position(
            equity=self.equity,
            capital_disponible=self.capital_disponible(),
            ceo_tamano_posicion_pct=order["tamano_posicion_pct"],
            riesgo_disponible_pct=order["riesgo_disponible_pct"],
            entry=order["entry"],
            stop_loss=order["stop_loss"],
            take_profit=order["take_profit"],
            direccion=order["direccion"],
        )
        if result.rejected:
            self.rejected_trades.append({"symbol": order["symbol"], "opened_at": open_time, "motivo": result.motivo})
            logger.warning("Orden de %s tocó el entry pero se rechazó al llenar (%s)", order["symbol"], result.motivo)
            return

        self.open_positions.append(
            {
                "symbol": order["symbol"],
                "direccion": order["direccion"],
                "entry": order["entry"],
                "stop_loss": order["stop_loss"],
                "take_profit": order["take_profit"],
                "risk_reward": order["risk_reward"],
                "quantity": result.quantity,
                "notional": result.notional,
                "sized_pct": result.sized_pct,
                "tamano_posicion_pct_propuesto": order["tamano_posicion_pct"],
                "opened_at": open_time,
                "razonamiento": order.get("razonamiento"),
                "conflictos_resueltos": order.get("conflictos_resueltos"),
            }
        )


def build_market_input(history: dict, pairs: list[str], t: int) -> dict:
    return {
        "timeframe": TIMEFRAME,
        "timeframe_contexto": CONTEXT_TIMEFRAME,
        "activos": [
            {
                "symbol": pair,
                "indicadores": indicators.compute_features(window(history[pair]["principal"], t, KLINES_LIMIT)),
                "indicadores_contexto": indicators.compute_features(
                    window(history[pair]["contexto"], t, CONTEXT_KLINES_LIMIT)
                ),
                "order_book": None,
            }
            for pair in pairs
        ],
    }


def precompute_mercado(history: dict, pairs: list[str], sim_open_times: list[int], max_workers: int = 8) -> dict:
    """El agente Mercado solo depende de las velas (no del estado del portfolio),
    así que a diferencia de Riesgo/CEO se puede correr para todos los ciclos en
    paralelo por adelantado, en vez de esperarlo secuencialmente dentro del loop."""
    logger.info("Precalculando Mercado para %d ciclos (en paralelo, %d workers)", len(sim_open_times), max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            t: pool.submit(runner.run_agent, "mercado", build_market_input(history, pairs, t), ANTHROPIC_MODEL_WORKER)
            for t in sim_open_times
        }
        return {t: future.result() for t, future in futures.items()}


def run_backtest(days: int, pairs: list[str], output_path: Path) -> None:
    runner.reset_usage()
    end_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    start_ms, end_ms = int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)

    logger.info("Descargando histórico de %s desde %s hasta %s", pairs, start_dt, end_dt)
    history = {pair: fetch_history(pair, start_ms, end_ms) for pair in pairs}

    candles_by_pair = {pair: {k["open_time"]: k for k in history[pair]["principal"]} for pair in pairs}
    sim_open_times = sorted(t for t in candles_by_pair[pairs[0]] if t >= start_ms)
    logger.info("%d ciclos de %s a simular", len(sim_open_times), TIMEFRAME)

    mercado_by_time = precompute_mercado(history, pairs, sim_open_times)
    lecciones_aprendidas = lessons.load()
    if lecciones_aprendidas:
        logger.info("Lecciones aprobadas cargadas desde state/lessons.md")

    portfolio = PortfolioSim(ACCOUNT_EQUITY_USD)
    rows = []

    try:
        for i, t in enumerate(sim_open_times):
            portfolio.resolve_positions(candles_by_pair, t)
            portfolio.resolve_pending_orders(candles_by_pair, t)
            iso_t = datetime.fromtimestamp(t / 1000, tz=timezone.utc).isoformat()

            try:
                risk_input = portfolio.risk_input()
                riesgo_output = runner.run_agent("risk_manager", risk_input, ANTHROPIC_MODEL_WORKER)

                ceo_input = {
                    "universo": pairs,
                    "timeframe": TIMEFRAME,
                    "min_risk_reward": MIN_RISK_REWARD,
                    "noticias": NO_NEWS_INPUT,
                    "mercado": mercado_by_time[t],
                    "riesgo": riesgo_output,
                    "lecciones_aprendidas": lecciones_aprendidas,
                }
                ceo_output = runner.run_agent("ceo", ceo_input, ANTHROPIC_MODEL_CEO)
            except (ValueError, KeyError) as exc:
                logger.warning("[%d/%d] %s: agente falló (%s) — se registra como no_trade", i + 1, len(sim_open_times), iso_t, exc)
                ceo_output = {"decision": "error", "motivo_no_trade": str(exc)}

            if ceo_output["decision"] == "trade" and portfolio.kill_switch_active():
                portfolio.kill_switch_rejections += 1
                logger.warning(
                    "[%d/%d] %s: kill-switch activo (drawdown ≥ %.0f%%) — se descarta la propuesta de %s",
                    i + 1, len(sim_open_times), iso_t, MAX_DRAWDOWN_PCT, ceo_output.get("symbol"),
                )
            elif ceo_output["decision"] == "trade" and ceo_output.get("entry") is not None:
                portfolio.add_pending_order(ceo_output, riesgo_output.get("riesgo_disponible_pct", 0.0), t)
            elif ceo_output["decision"] == "trade":
                logger.warning("[%d/%d] %s: CEO pidió trade sin 'entry', se descarta", i + 1, len(sim_open_times), iso_t)

            rows.append(
                {
                    "timestamp": iso_t,
                    "decision": ceo_output["decision"],
                    "symbol": ceo_output.get("symbol"),
                    "direccion": ceo_output.get("direccion"),
                    "entry_propuesto": ceo_output.get("entry"),
                    "stop_loss": ceo_output.get("stop_loss"),
                    "take_profit": ceo_output.get("take_profit"),
                    "risk_reward": ceo_output.get("risk_reward"),
                    "tamano_posicion_pct_propuesto": ceo_output.get("tamano_posicion_pct"),
                    "confianza": ceo_output.get("confianza"),
                    "equity_after": portfolio.equity,
                    "pendientes": len(portfolio.pending_orders),
                }
            )
            logger.info(
                "[%d/%d] %s decision=%s equity=%.2f abiertas=%d pendientes=%d",
                i + 1,
                len(sim_open_times),
                iso_t,
                ceo_output["decision"],
                portfolio.equity,
                len(portfolio.open_positions),
                len(portfolio.pending_orders),
            )
    finally:
        if rows:
            write_log(rows, output_path)
            write_trades_log(portfolio.closed_trades, output_path)
            print_summary(portfolio, days)
            propose_lessons(portfolio, days, output_path)


def write_log(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Log de ciclos guardado en %s", output_path)


def write_trades_log(trades: list[dict], output_path: Path) -> None:
    if not trades:
        return
    trades_path = output_path.with_name(output_path.stem + "_trades.csv")
    fieldnames = [
        "symbol",
        "direccion",
        "entry",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "tamano_posicion_pct_propuesto",
        "sized_pct",
        "quantity",
        "notional",
        "outcome",
        "exit_reason",
        "exit_price",
        "pnl_gross",
        "fees",
        "pnl",
        "opened_at",
        "closed_at",
        "duration_hours",
        "razonamiento",
        "conflictos_resueltos",
    ]
    rows = [
        {
            **{k: t.get(k) for k in fieldnames},
            "opened_at": datetime.fromtimestamp(t["opened_at"] / 1000, tz=timezone.utc).isoformat(),
            "closed_at": datetime.fromtimestamp(t["closed_at"] / 1000, tz=timezone.utc).isoformat(),
        }
        for t in trades
    ]
    with trades_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Log de trades guardado en %s", trades_path)


def print_summary(portfolio: PortfolioSim, days: int) -> None:
    trades = portfolio.closed_trades
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    total_return_pct = (portfolio.equity / ACCOUNT_EQUITY_USD - 1) * 100
    drawdown_pct = (
        (portfolio.peak_equity - min(portfolio.equity, portfolio.peak_equity)) / portfolio.peak_equity * 100
        if portfolio.peak_equity > 0
        else 0.0
    )

    print("\n=== Resumen del backtest ===")
    print(f"Ventana: {days} días")
    print(f"Equity inicial: {ACCOUNT_EQUITY_USD:.2f} -> final: {portfolio.equity:.2f} ({total_return_pct:+.2f}%)")
    print(f"Trades cerrados: {len(trades)} (wins={len(wins)}, losses={len(losses)})")
    net_pnl = None
    if trades:
        print(f"Win rate: {len(wins) / len(trades) * 100:.1f}%")
        total_fees = sum(t["fees"] for t in trades)
        total_pnl_gross = sum(t["pnl_gross"] for t in trades)
        net_pnl = total_pnl_gross - total_fees
        print(f"PnL bruto: {total_pnl_gross:+.2f} | Comisiones pagadas: -{total_fees:.2f} ({FEE_PCT}%/lado) | PnL neto: {net_pnl:+.2f}")
    # Lo que importa es la plata neta, no el win rate aislado: un win rate alto
    # con pérdidas grandes (o un solo trade tapando el resto) puede ser una mala
    # estrategia igual. Estas métricas lo hacen visible.
    avg_win_usd = biggest_win_usd = None
    if wins:
        avg_win_h = sum(t["duration_hours"] for t in wins) / len(wins)
        avg_win_usd = sum(t["pnl"] for t in wins) / len(wins)
        biggest_win_usd = max(t["pnl"] for t in wins)
        print(f"Duración media hasta TP: {avg_win_h:.1f}h")
        print(f"Ganancia neta media por win: {avg_win_usd:+.2f} | Mayor win individual: {biggest_win_usd:+.2f}")
    avg_loss_usd = None
    if losses:
        avg_loss_h = sum(t["duration_hours"] for t in losses) / len(losses)
        avg_loss_usd = sum(t["pnl"] for t in losses) / len(losses)
        biggest_loss_usd = min(t["pnl"] for t in losses)
        print(f"Duración media hasta SL: {avg_loss_h:.1f}h")
        print(f"Pérdida neta media por loss: {avg_loss_usd:+.2f} | Mayor loss individual: {biggest_loss_usd:+.2f}")
    if avg_win_usd is not None and avg_loss_usd:
        payoff_ratio = avg_win_usd / abs(avg_loss_usd)
        print(f"Payoff ratio (ganancia media / pérdida media): {payoff_ratio:.2f}")
    if net_pnl is not None and net_pnl > 0 and biggest_win_usd is not None:
        concentracion_pct = biggest_win_usd / net_pnl * 100
        alerta = " (⚠ un solo trade está tapando el resultado del resto de la estrategia)" if concentracion_pct > 50 else ""
        print(f"Concentración: el mayor win individual explica el {concentracion_pct:.1f}% del PnL neto total{alerta}")
    time_stopped = [t for t in trades if t.get("exit_reason") == "time_stop"]
    if time_stopped:
        print(f"Cerrados por time-stop (no tocaron SL ni TP): {len(time_stopped)}")
    print(f"Posiciones sin resolver al final de la ventana: {len(portfolio.open_positions)}")
    print(f"Drawdown máximo observado: {drawdown_pct:.2f}%")
    if portfolio.rejected_trades:
        print(f"Trades rechazados al llenar (riesgo 0, notional por debajo del mínimo o R:R/geometría inválida al fill): {len(portfolio.rejected_trades)}")
    if portfolio.expired_orders:
        print(f"Órdenes límite expiradas sin tocar el entry: {len(portfolio.expired_orders)}")
    if portfolio.pending_orders:
        print(f"Órdenes límite todavía pendientes al cierre de la ventana: {len(portfolio.pending_orders)}")
    if portfolio.kill_switch_rejections:
        print(f"Propuestas descartadas por kill-switch (drawdown ≥ {MAX_DRAWDOWN_PCT:.0f}%): {portfolio.kill_switch_rejections}")

    usage = runner.get_usage_summary()
    print("\n=== Gasto real en la API (Anthropic) ===")
    for model, m in usage["by_model"].items():
        print(f"  {model}: {m['calls']} llamadas, {m['input_tokens']} in / {m['output_tokens']} out tokens")
    print(f"Total: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out tokens")
    print(f"Costo estimado (precio de lista): ${usage['estimated_cost_usd']:.4f}")


def propose_lessons(portfolio: PortfolioSim, days: int, output_path: Path) -> None:
    """Corre el agente 'analista' sobre los trades cerrados y escribe una
    PROPUESTA de lecciones a revisar a mano — nunca se aplican solas. Si el
    humano está de acuerdo, las copia a state/lessons.md, que es lo único que
    el CEO llega a leer (ver src/state/lessons.py)."""
    trades = portfolio.closed_trades
    if not trades:
        logger.info("Sin trades cerrados — no hay nada para que el analista revise.")
        return

    analista_input = {
        "ventana_dias": days,
        "equity_inicial": ACCOUNT_EQUITY_USD,
        "equity_final": portfolio.equity,
        "trades": [
            {
                "symbol": t["symbol"],
                "direccion": t["direccion"],
                "entry": t["entry"],
                "stop_loss": t["stop_loss"],
                "take_profit": t["take_profit"],
                "risk_reward": t["risk_reward"],
                "sized_pct": t["sized_pct"],
                "outcome": t["outcome"],
                "exit_reason": t.get("exit_reason"),
                "pnl": t["pnl"],
                "duration_hours": t["duration_hours"],
                "razonamiento": t.get("razonamiento"),
                "conflictos_resueltos": t.get("conflictos_resueltos"),
                "opened_at": datetime.fromtimestamp(t["opened_at"] / 1000, tz=timezone.utc).isoformat(),
            }
            for t in trades
        ],
    }

    try:
        resultado = runner.run_agent("analista", analista_input, ANTHROPIC_MODEL_CEO)
    except ValueError as exc:
        logger.warning("El analista no devolvió JSON válido, se omite la propuesta de lecciones: %s", exc)
        return

    lecciones_path = output_path.with_name(output_path.stem + "_lecciones_propuestas.md")
    lines = [
        "<!-- PROPUESTA generada automáticamente — revisar antes de copiar a state/lessons.md -->",
        f"# Lecciones propuestas — backtest de {days} días ({output_path.stem})",
        "",
        resultado.get("resumen", ""),
        "",
    ]
    for leccion in resultado.get("lecciones", []):
        lines.append(f"## {leccion.get('titulo', 'sin título')} (confianza: {leccion.get('confianza', '?')})")
        lines.append(leccion.get("leccion", ""))
        lines.append(f"- Evidencia: {leccion.get('evidencia', '')}")
        lines.append("")
    lecciones_path.write_text("\n".join(lines))
    logger.info("Propuesta de lecciones guardada en %s — revisarla antes de aprobar nada", lecciones_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest piloto de la agencia de trading")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--pairs", type=str, default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    pairs = [p.strip().upper() for p in args.pairs.split(",")]
    output_path = Path(args.output) if args.output else Path(__file__).parent / "results" / (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".csv"
    )
    run_backtest(args.days, pairs, output_path)
