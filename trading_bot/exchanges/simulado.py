"""Modo DRY_RUN: datos de mercado reales, órdenes simuladas en memoria."""
import itertools
import logging
from decimal import Decimal
from typing import Dict, Optional

import pandas as pd

from ..precision import D
from .base import CallbackLibro, CallbackVela, EstadoOrden, Exchange, Filtros, InfoOrden

logger = logging.getLogger(__name__)


class ExchangeSimulado(Exchange):
    def __init__(self, real: Exchange, capital_quote: float, comision_pct: float = 0.1,
                 saldo_base_inicial: Decimal = Decimal("0")):
        self.real = real
        self.nombre = f"{real.nombre} (simulado)"
        self.comision = D(comision_pct) / 100
        self.saldos: Dict[str, Decimal] = {}
        self._capital = D(capital_quote)
        self._base_inicial = D(saldo_base_inicial)
        self.ordenes: Dict[str, InfoOrden] = {}
        self.stops: Dict[str, tuple] = {}
        self._ids = itertools.count(1)
        self.bid = Decimal("0")
        self.ask = Decimal("0")

    # ---- datos reales ----
    def cargar_filtros(self) -> Filtros:
        filtros = self.real.cargar_filtros()
        self.activo_base, self.activo_cotizado = self.real.activo_base, self.real.activo_cotizado
        self.saldos.setdefault(self.activo_cotizado, self._capital)
        self.saldos.setdefault(self.activo_base, self._base_inicial)
        return filtros

    def obtener_velas(self, limite: int) -> pd.DataFrame:
        return self.real.obtener_velas(limite)

    def iniciar_streams(self, on_libro: CallbackLibro, on_vela: CallbackVela) -> None:
        def libro(bid, ask):
            self.bid, self.ask = D(bid), D(ask)
            on_libro(bid, ask)
        self.real.iniciar_streams(libro, on_vela)

    def detener_streams(self) -> None:
        self.real.detener_streams()

    # ---- órdenes simuladas ----
    def saldo_libre(self, moneda: str) -> Decimal:
        return self.saldos.get(moneda, Decimal("0"))

    def _llenar(self, lado: str, cantidad: Decimal, precio: Decimal) -> str:
        base, quote = self.activo_base, self.activo_cotizado
        if lado == "Buy":
            coste = cantidad * precio
            if coste > self.saldos[quote]:
                raise ValueError("saldo simulado insuficiente")
            self.saldos[quote] -= coste
            self.saldos[base] += cantidad * (1 - self.comision)  # comisión cobrada en la base
        else:
            if cantidad > self.saldos[base]:
                raise ValueError("saldo simulado insuficiente")
            self.saldos[base] -= cantidad
            self.saldos[quote] += cantidad * precio * (1 - self.comision)
        oid = f"sim-{next(self._ids)}"
        self.ordenes[oid] = InfoOrden(oid, EstadoOrden.LLENADA, cantidad, precio)
        logger.info("   [SIMULADOR] %s %s a %s", lado, cantidad, precio)
        return oid

    def colocar_limit(self, lado: str, cantidad: str, precio: str, post_only: bool = True) -> str:
        return self._llenar(lado, D(cantidad), D(precio))

    def colocar_market(self, lado: str, cantidad: str) -> str:
        precio = self.bid if lado == "Sell" else self.ask
        return self._llenar(lado, D(cantidad), precio)

    def consultar_orden(self, order_id: str) -> InfoOrden:
        return self.ordenes.get(order_id, InfoOrden(order_id, EstadoOrden.DESCONOCIDA))

    def cancelar_orden(self, order_id: str) -> bool:
        return True

    def colocar_stop(self, cantidad: str, precio_disparo: str) -> str:
        oid = f"sim-stop-{next(self._ids)}"
        self.stops[oid] = (D(cantidad), D(precio_disparo))
        return oid

    def stop_activo(self, order_id: str) -> Optional[bool]:
        if order_id not in self.stops:
            return False
        cantidad, disparo = self.stops[order_id]
        if self.bid > 0 and self.bid <= disparo:
            del self.stops[order_id]
            self._llenar("Sell", min(cantidad, self.saldos[self.activo_base]), self.bid)
            return False
        return True

    def cancelar_stop(self, order_id: str) -> bool:
        self.stops.pop(order_id, None)
        return True
