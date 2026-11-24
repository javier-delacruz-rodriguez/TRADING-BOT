"""Redondeo exacto con Decimal para cantidades y precios.

Con float, ``math.floor(0.29 / 0.01) * 0.01`` da 0.28: se pierde un paso
entero por errores de representación. Aquí todo se hace en Decimal.
"""
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Union

Numero = Union[Decimal, float, int, str]


def D(valor: Numero) -> Decimal:
    """Convierte a Decimal pasando por str para no arrastrar errores de float."""
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def _a_paso(valor: Numero, paso: Numero, modo: str) -> Decimal:
    v, p = D(valor), D(paso)
    if p <= 0:
        return v
    pasos = (v / p).to_integral_value(rounding=modo)
    return (pasos * p).quantize(p)


def redondear_abajo(valor: Numero, paso: Numero) -> Decimal:
    return _a_paso(valor, paso, ROUND_DOWN)


def redondear_arriba(valor: Numero, paso: Numero) -> Decimal:
    return _a_paso(valor, paso, ROUND_UP)


def a_texto(valor: Decimal) -> str:
    """Formato plano sin notación científica (lo que esperan las APIs)."""
    return format(D(valor), "f")
