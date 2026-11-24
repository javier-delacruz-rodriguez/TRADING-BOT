import os
import sys
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot.config import Config  # noqa: E402
from trading_bot.exchanges.base import EstadoOrden, Exchange, Filtros, InfoOrden  # noqa: E402


@pytest.fixture
def cfg():
    return Config(dry_run=True, order_timeout_sec=3, max_order_retries=3, cooldown_after_reject_sec=900,
                  estado_archivo="", operaciones_archivo="")


@pytest.fixture
def filtros():
    return Filtros(Decimal("0.000001"), Decimal("0.01"), Decimal("0.000048"), Decimal("5.5"))


def velas_con_cruce(n=150, seed=1):
    """Serie bajista suave y luego subida fuerte: produce un cruce alcista con ADX alto al final."""
    rng = np.random.default_rng(seed)
    base = np.concatenate([np.linspace(100, 90, n - 12), np.linspace(90.5, 99, 12)])
    close = base + rng.normal(0, 0.05, n)
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"timestamp": ts, "open": close - 0.1, "high": close + 0.3, "low": close - 0.3,
                         "close": close, "volume": 1.0})


class FakeExchange(Exchange):
    """Exchange en memoria con comportamiento de llenado programable."""

    nombre = "Fake"
    activo_base = "BTC"
    activo_cotizado = "USDT"

    def __init__(self, filtros, saldos=None, velas=None):
        self.filtros = filtros
        self.saldos = {"BTC": Decimal("0"), "USDT": Decimal("1000"), **(saldos or {})}
        self.velas = velas if velas is not None else velas_con_cruce()
        self.ordenes = {}
        self.abiertas = set()
        self.max_abiertas = 0
        self.colocadas = []
        self.n = 0
        # Guion: lista de funciones (order) -> (estado_al_consultar, ejecutado) por orden colocada
        self.guion = []
        self.stops = {}
        self.stop_ejecutado = False
        self.fallo_colocar = None
        self.comision_base = Decimal("0")

    def cargar_filtros(self):
        return self.filtros

    def obtener_velas(self, limite):
        return self.velas.tail(limite).reset_index(drop=True)

    def saldo_libre(self, moneda):
        return self.saldos[moneda]

    def _nuevo(self, lado, cantidad, precio, tipo):
        if self.fallo_colocar:
            raise self.fallo_colocar
        self.n += 1
        oid = str(self.n)
        comportamiento = self.guion.pop(0) if self.guion else ("llenar", None)
        self.ordenes[oid] = {"lado": lado, "cantidad": Decimal(cantidad), "precio": Decimal(precio),
                             "tipo": tipo, "comp": comportamiento, "ejecutado": Decimal("0"),
                             "estado": EstadoOrden.ABIERTA}
        self.abiertas.add(oid)
        self.max_abiertas = max(self.max_abiertas, len(self.abiertas))
        self.colocadas.append(self.ordenes[oid])
        return oid

    def _llenar(self, oid, cantidad):
        o = self.ordenes[oid]
        if cantidad <= 0:
            return
        o["ejecutado"] += cantidad
        if o["lado"] == "Buy":
            self.saldos["USDT"] -= cantidad * o["precio"]
            self.saldos["BTC"] += cantidad * (1 - self.comision_base)
        else:
            self.saldos["BTC"] -= cantidad
            self.saldos["USDT"] += cantidad * o["precio"]

    def colocar_limit(self, lado, cantidad, precio, post_only=True):
        return self._nuevo(lado, cantidad, precio, "limit")

    def colocar_market(self, lado, cantidad):
        oid = self._nuevo(lado, cantidad, "100" if lado == "Sell" else "100", "market")
        self.ordenes[oid]["comp"] = ("llenar", None)
        return oid

    def consultar_orden(self, oid):
        if oid not in self.ordenes:
            return InfoOrden(oid, EstadoOrden.DESCONOCIDA)
        o = self.ordenes[oid]
        accion, valor = o["comp"]
        if o["estado"] == EstadoOrden.ABIERTA:
            if accion == "llenar":
                self._llenar(oid, o["cantidad"] - o["ejecutado"])
                o["estado"] = EstadoOrden.LLENADA
                self.abiertas.discard(oid)
            elif accion == "parcial" and o["ejecutado"] == 0:
                self._llenar(oid, Decimal(valor))
        return InfoOrden(oid, o["estado"], o["ejecutado"], o["precio"])

    def cancelar_orden(self, oid):
        o = self.ordenes[oid]
        accion, _ = o["comp"]
        if accion == "llenar_al_cancelar":
            self._llenar(oid, o["cantidad"] - o["ejecutado"])
            o["estado"] = EstadoOrden.LLENADA
            self.abiertas.discard(oid)
            return False
        if o["estado"] == EstadoOrden.ABIERTA:
            o["estado"] = EstadoOrden.CERRADA
            self.abiertas.discard(oid)
            return True
        return False

    def colocar_stop(self, cantidad, precio_disparo):
        oid = f"stop-{len(self.stops) + 1}"
        self.stops[oid] = {"cantidad": Decimal(cantidad), "disparo": Decimal(precio_disparo), "activo": True}
        return oid

    def stop_activo(self, oid):
        return self.stops[oid]["activo"] if oid in self.stops else None

    def cancelar_stop(self, oid):
        if oid in self.stops and self.stops[oid]["activo"]:
            self.stops[oid]["activo"] = False
            return True
        return False

    def iniciar_streams(self, on_libro, on_vela):
        pass

    def detener_streams(self):
        pass


@pytest.fixture
def fake(filtros):
    return FakeExchange(filtros)
