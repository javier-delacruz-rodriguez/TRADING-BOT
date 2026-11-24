"""Interfaz común de exchange: el motor del bot no sabe si habla con Bybit o Binance."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

import pandas as pd

from ..precision import D, a_texto, redondear_abajo, redondear_arriba

CallbackLibro = Callable[[Decimal, Decimal], None]
CallbackVela = Callable[[dict], None]


class ErrorExchange(Exception):
    """Error devuelto por el exchange o por la red."""


class PostOnlyRechazada(ErrorExchange):
    """La orden post-only se habría ejecutado como taker y el exchange la rechazó."""


class EstadoOrden(Enum):
    ABIERTA = "abierta"          # en el libro (puede tener llenado parcial)
    LLENADA = "llenada"          # completamente ejecutada
    CERRADA = "cerrada"          # cancelada / rechazada / expirada (puede tener llenado parcial)
    DESCONOCIDA = "desconocida"  # no se pudo consultar


@dataclass
class InfoOrden:
    order_id: str
    estado: EstadoOrden
    ejecutado: Decimal = Decimal("0")
    precio_medio: Decimal = Decimal("0")


@dataclass
class Filtros:
    paso_cantidad: Decimal
    paso_precio: Decimal
    cantidad_minima: Decimal = Decimal("0")
    nocional_minimo: Decimal = Decimal("0")

    def cantidad(self, valor) -> Decimal:
        return redondear_abajo(valor, self.paso_cantidad)

    def precio(self, valor, lado: str) -> Decimal:
        # Compra: nunca por encima del precio dado. Venta: nunca por debajo.
        if lado == "Sell":
            return redondear_arriba(valor, self.paso_precio)
        return redondear_abajo(valor, self.paso_precio)

    def es_valida(self, cantidad, precio) -> bool:
        cantidad, precio = D(cantidad), D(precio)
        return cantidad > 0 and cantidad >= self.cantidad_minima and cantidad * precio >= self.nocional_minimo

    def texto_cantidad(self, valor) -> str:
        return a_texto(self.cantidad(valor))

    def texto_precio(self, valor, lado: str) -> str:
        return a_texto(self.precio(valor, lado))


class Exchange(ABC):
    """Lados de orden: 'Buy' / 'Sell'. Cantidades y precios se pasan ya redondeados como str."""

    nombre: str = "base"
    activo_base: str = ""
    activo_cotizado: str = ""

    @abstractmethod
    def cargar_filtros(self) -> Filtros: ...

    @abstractmethod
    def obtener_velas(self, limite: int) -> pd.DataFrame:
        """Velas CERRADAS, de la más antigua a la más reciente, columnas OHLCV + timestamp."""

    @abstractmethod
    def saldo_libre(self, moneda: str) -> Decimal: ...

    @abstractmethod
    def colocar_limit(self, lado: str, cantidad: str, precio: str, post_only: bool = True) -> str: ...

    @abstractmethod
    def colocar_market(self, lado: str, cantidad: str) -> str: ...

    @abstractmethod
    def consultar_orden(self, order_id: str) -> InfoOrden: ...

    @abstractmethod
    def cancelar_orden(self, order_id: str) -> bool: ...

    @abstractmethod
    def colocar_stop(self, cantidad: str, precio_disparo: str) -> str:
        """Stop-loss de venta que vive en el exchange (protege aunque el bot se caiga)."""

    @abstractmethod
    def stop_activo(self, order_id: str) -> Optional[bool]:
        """True si sigue pendiente, False si ya se disparó/cerró, None si no se sabe."""

    @abstractmethod
    def cancelar_stop(self, order_id: str) -> bool: ...

    @abstractmethod
    def iniciar_streams(self, on_libro: CallbackLibro, on_vela: CallbackVela) -> None:
        """Los callbacks deben ser rápidos: no se ejecutan órdenes dentro de ellos."""

    @abstractmethod
    def detener_streams(self) -> None: ...


def fila_vela(timestamp_ms, apertura, maximo, minimo, cierre, volumen) -> dict:
    return {
        "timestamp": pd.to_datetime(int(float(timestamp_ms)), unit="ms"),
        "open": float(apertura), "high": float(maximo), "low": float(minimo), "close": float(cierre),
        "volume": float(volumen),
    }
