"""Ejecución de órdenes: maker con reintentos, llenados parciales y respaldo a mercado.

Reglas que garantiza:
* Nunca hay más de una orden propia abierta a la vez: antes de reintentar se cancela
  la anterior y se vuelve a consultar su estado final.
* Lo ejecutado en llenados parciales se acumula y sólo se pide el resto.
* Una orden que se llena justo mientras se cancela se cuenta como ejecutada.
"""
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional, Tuple

from .config import Config
from .exchanges.base import EstadoOrden, Exchange, Filtros, InfoOrden, PostOnlyRechazada
from .precision import a_texto

logger = logging.getLogger(__name__)


@dataclass
class ResultadoEjecucion:
    cantidad: Decimal = Decimal("0")
    precio_medio: Decimal = Decimal("0")
    error: Optional[str] = None

    @property
    def llenado(self) -> bool:
        return self.cantidad > 0

    def sumar(self, otro: "ResultadoEjecucion") -> "ResultadoEjecucion":
        total = self.cantidad + otro.cantidad
        if total == 0:
            return ResultadoEjecucion(error=otro.error or self.error)
        precio = (self.cantidad * self.precio_medio + otro.cantidad * otro.precio_medio) / total
        return ResultadoEjecucion(total, precio, otro.error or self.error)


class Ejecutor:
    def __init__(self, exchange: Exchange, filtros: Filtros, precios: Callable[[], Tuple[Decimal, Decimal]],
                 cfg: Config, dormir: Callable[[float], None] = time.sleep,
                 reloj: Callable[[], float] = time.monotonic):
        self.ex = exchange
        self.filtros = filtros
        self.precios = precios
        self.cfg = cfg
        self.dormir = dormir
        self.reloj = reloj

    # ------------------------------------------------------------------
    def limit_maker(self, lado: str, cantidad_objetivo) -> ResultadoEjecucion:
        restante = self.filtros.cantidad(cantidad_objetivo)
        resultado = ResultadoEjecucion()

        for intento in range(1, self.cfg.max_order_retries + 1):
            bid, ask = self.precios()
            referencia = bid if lado == "Buy" else ask
            if referencia <= 0:
                logger.warning("Sin precio de mercado; no se coloca la orden.")
                break
            precio = self.filtros.precio(referencia, lado)
            cantidad = self.filtros.cantidad(restante)
            if not self.filtros.es_valida(cantidad, precio):
                break

            logger.info("⚙️ MAKER [%d/%d]: %s %s a %s", intento, self.cfg.max_order_retries, lado,
                        a_texto(cantidad), a_texto(precio))
            try:
                order_id = self.ex.colocar_limit(lado, a_texto(cantidad), a_texto(precio), self.cfg.post_only)
            except PostOnlyRechazada:
                logger.info("   Post-only rechazada (el precio cruzó). Reintentando con precio nuevo...")
                self.dormir(0.5)
                continue
            except Exception as e:
                logger.error("   ❌ Error al colocar la orden: %s", e)
                resultado.error = str(e)
                break

            info = self._esperar_llenado(order_id, self.cfg.order_timeout_sec)
            if info.estado != EstadoOrden.LLENADA:
                info = self._cancelar_y_confirmar(order_id)

            if info.ejecutado > 0:
                precio_llenado = info.precio_medio if info.precio_medio > 0 else precio
                resultado = resultado.sumar(ResultadoEjecucion(info.ejecutado, precio_llenado))
                restante -= info.ejecutado
                logger.info("   ✅ Ejecutado %s a %s (acumulado %s)", a_texto(info.ejecutado),
                            a_texto(precio_llenado), a_texto(resultado.cantidad))

            if info.estado not in (EstadoOrden.LLENADA, EstadoOrden.CERRADA):
                # No se pudo confirmar que la orden esté cerrada: no se abre otra para no duplicar.
                resultado.error = f"estado de la orden {order_id} sin confirmar ({info.estado.value})"
                logger.error("   ⚠️ No se pudo confirmar el cierre de %s; se detienen los reintentos.", order_id)
                break
            if info.estado == EstadoOrden.LLENADA or restante <= 0:
                break
            logger.info("   ⏳ Precio escapó. Quedan %s por ejecutar.", a_texto(restante))

        return resultado

    def market(self, lado: str, cantidad) -> ResultadoEjecucion:
        cantidad = self.filtros.cantidad(cantidad)
        bid, ask = self.precios()
        referencia = bid if lado == "Sell" else ask
        logger.info("⚡ MARKET: %s %s (ref. %s)", lado, a_texto(cantidad), a_texto(referencia))
        try:
            order_id = self.ex.colocar_market(lado, a_texto(cantidad))
        except Exception as e:
            logger.error("   ❌ Error en orden a mercado: %s", e)
            return ResultadoEjecucion(error=str(e))

        info = self._esperar_llenado(order_id, 5)
        if info.estado == EstadoOrden.DESCONOCIDA:
            # La orden se aceptó; si no se puede consultar se asume ejecutada al precio de referencia.
            logger.warning("   Orden a mercado %s sin confirmar; se asume ejecutada.", order_id)
            return ResultadoEjecucion(cantidad, referencia)
        precio = info.precio_medio if info.precio_medio > 0 else referencia
        logger.info("   ✅ Mercado ejecutado %s a %s", a_texto(info.ejecutado), a_texto(precio))
        return ResultadoEjecucion(info.ejecutado, precio)

    def vender(self, cantidad, a_mercado: bool) -> ResultadoEjecucion:
        """Venta con la política configurada: maker con respaldo a mercado para lo que quede."""
        cantidad = self.filtros.cantidad(cantidad)
        if a_mercado:
            return self.market("Sell", cantidad)
        resultado = self.limit_maker("Sell", cantidad)
        restante = self.filtros.cantidad(cantidad - resultado.cantidad)
        bid, _ = self.precios()
        if (restante > 0 and self.cfg.allow_market_sell_fallback
                and self.filtros.es_valida(restante, bid if bid > 0 else Decimal("1"))):
            logger.info("   Respaldo a mercado para %s restantes.", a_texto(restante))
            resultado = resultado.sumar(self.market("Sell", restante))
        return resultado

    # ------------------------------------------------------------------
    def _esperar_llenado(self, order_id: str, timeout: float) -> InfoOrden:
        fin = self.reloj() + timeout
        while True:
            info = self.ex.consultar_orden(order_id)
            if info.estado in (EstadoOrden.LLENADA, EstadoOrden.CERRADA):
                return info
            if self.reloj() >= fin:
                return info
            self.dormir(1.0)

    def _cancelar_y_confirmar(self, order_id: str, intentos: int = 5) -> InfoOrden:
        info = InfoOrden(order_id, EstadoOrden.DESCONOCIDA)
        for _ in range(intentos):
            self.ex.cancelar_orden(order_id)
            info = self.ex.consultar_orden(order_id)
            if info.estado in (EstadoOrden.LLENADA, EstadoOrden.CERRADA):
                return info
            self.dormir(1.0)
        return info
