"""Cálculo del tamaño de posición."""
from decimal import Decimal
from typing import Optional, Tuple

from .config import Config
from .exchanges.base import Filtros
from .precision import D, redondear_arriba


def calcular_cantidad(
    saldo_quote: Decimal, atr: Decimal, precio: Decimal, cfg: Config, filtros: Filtros
) -> Tuple[Optional[Decimal], str]:
    """Cantidad a comprar arriesgando ``risk_per_trade_pct`` del saldo hasta el stop.

    Devuelve (cantidad, "") o (None, motivo).
    """
    saldo_quote, atr, precio = D(saldo_quote), D(atr), D(precio)
    if precio <= 0:
        return None, "precio de referencia no disponible"
    if atr <= 0:
        return None, "ATR no válido"

    limite_quote = saldo_quote * D(cfg.max_capital_use_pct) / 100
    minimo = filtros.nocional_minimo
    if limite_quote < minimo:
        return None, f"saldo insuficiente ({saldo_quote:.2f}; máx. usable {limite_quote:.2f} < mínimo {minimo:.2f})"

    riesgo_quote = saldo_quote * D(cfg.risk_per_trade_pct) / 100
    distancia_sl = atr * D(cfg.atr_multiplier_sl)
    cantidad = riesgo_quote / distancia_sl

    if cantidad * precio < minimo:
        # Se fuerza el mínimo del exchange (el riesgo real será algo mayor del objetivo)
        cantidad = redondear_arriba(minimo / precio, filtros.paso_cantidad)
    if cantidad * precio > limite_quote:
        cantidad = limite_quote / precio

    cantidad = filtros.cantidad(cantidad)
    if not filtros.es_valida(cantidad, precio):
        return None, f"cantidad ajustada {cantidad} por debajo de los mínimos del exchange"
    return cantidad, ""
