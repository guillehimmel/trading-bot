"""Convierte el %-de-riesgo que decide el CEO en una cantidad real de unidades
a operar, con límites duros aplicados en código — nunca confiamos en que el LLM
respetó los topes de riesgo (ver backtest: un trade real llegó a pedir 5% de
riesgo con MAX_RISK_PER_TRADE_PCT=1%, y nada lo frenó porque el único límite
vivía en texto de un prompt). También revalida el R:R con el precio de fill
real: el CEO lo calcula sobre el entry que propone, pero el backtest ejecuta al
cierre real de la vela — si esos dos precios no coinciden, el R:R que se
termina jugando puede ser otro (hasta invertido) y nadie lo chequeaba."""

from dataclasses import dataclass

from config.settings import MAX_RISK_PER_TRADE_PCT, MIN_NOTIONAL_USD, MIN_RISK_REWARD


@dataclass
class PositionSize:
    quantity: float
    notional: float
    risk_amount: float
    sized_pct: float
    rejected: bool
    motivo: str | None = None


def size_position(
    equity: float,
    ceo_tamano_posicion_pct: float,
    riesgo_disponible_pct: float,
    entry: float,
    stop_loss: float,
    take_profit: float,
    direccion: str,
) -> PositionSize:
    """riesgo_disponible_pct viene del departamento de Riesgo (ya debería estar
    acotado por MAX_RISK_PER_TRADE_PCT, pero lo volvemos a acotar acá: un solo
    punto de confianza no alcanza para algo que puede fundir la cuenta)."""
    stop_distance = abs(entry - stop_loss)
    reward_distance = abs(take_profit - entry)
    if stop_distance <= 0 or entry <= 0:
        return PositionSize(0.0, 0.0, 0.0, 0.0, True, "stop_loss inválido (igual o invertido respecto al entry)")

    geometria_valida = (
        stop_loss < entry < take_profit if direccion == "long" else take_profit < entry < stop_loss
    )
    if not geometria_valida:
        return PositionSize(
            0.0, 0.0, 0.0, 0.0, True,
            "geometría inválida al precio de fill real (el precio ya cruzó el stop o el take profit antes de ejecutar)",
        )

    real_rr = reward_distance / stop_distance
    if real_rr < MIN_RISK_REWARD:
        return PositionSize(
            0.0, 0.0, 0.0, 0.0, True,
            f"R:R real al fill es {real_rr:.2f}, por debajo del mínimo exigido ({MIN_RISK_REWARD}) — "
            f"el CEO lo calculó sobre otro precio de entrada",
        )

    sized_pct = max(0.0, min(ceo_tamano_posicion_pct, MAX_RISK_PER_TRADE_PCT, riesgo_disponible_pct))
    if sized_pct <= 0:
        return PositionSize(0.0, 0.0, 0.0, 0.0, True, "riesgo disponible es 0 (bloqueado por riesgo o tamaño propuesto inválido)")

    risk_amount = equity * sized_pct / 100
    quantity = risk_amount / stop_distance
    notional = quantity * entry

    # Spot sin margen: nunca se puede comprar más de lo que hay en la cuenta,
    # aunque el riesgo calculado "pediría" una posición más grande porque el
    # stop está muy ajustado.
    if notional > equity:
        quantity = equity / entry
        notional = equity
        risk_amount = quantity * stop_distance
        sized_pct = risk_amount / equity * 100

    if notional < MIN_NOTIONAL_USD:
        return PositionSize(
            quantity, notional, risk_amount, sized_pct, True,
            f"notional ${notional:.2f} por debajo del mínimo operable (${MIN_NOTIONAL_USD:.2f})",
        )

    return PositionSize(quantity, notional, risk_amount, sized_pct, False, None)
