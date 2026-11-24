"""Adaptador Bybit V5 (Unified Trading Account, spot) con pybit."""
import logging
import time
from decimal import Decimal
from typing import Optional

import pandas as pd
from pybit.unified_trading import HTTP, WebSocket

from ..estrategia import COLUMNAS_OHLCV
from ..precision import D
from .base import CallbackLibro, CallbackVela, ErrorExchange, EstadoOrden, Exchange, Filtros, InfoOrden, fila_vela

logger = logging.getLogger(__name__)

INTERVALOS = {1: "1", 3: "3", 5: "5", 15: "15", 30: "30", 60: "60", 120: "120",
              240: "240", 360: "360", 720: "720", 1440: "D"}

ESTADOS = {
    "New": EstadoOrden.ABIERTA,
    "PartiallyFilled": EstadoOrden.ABIERTA,
    "Untriggered": EstadoOrden.ABIERTA,
    "Triggered": EstadoOrden.ABIERTA,
    "Filled": EstadoOrden.LLENADA,
    "Cancelled": EstadoOrden.CERRADA,
    "PartiallyFilledCanceled": EstadoOrden.CERRADA,
    "Rejected": EstadoOrden.CERRADA,
    "Deactivated": EstadoOrden.CERRADA,
}


def _dec(valor) -> Decimal:
    return D(valor) if valor not in (None, "") else Decimal("0")


class BybitExchange(Exchange):
    nombre = "Bybit"

    def __init__(self, cfg):
        if cfg.timeframe_min not in INTERVALOS:
            raise ValueError(f"TIMEFRAME_MIN={cfg.timeframe_min} no soportado por Bybit: {sorted(INTERVALOS)}")
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.intervalo = INTERVALOS[cfg.timeframe_min]
        kwargs = {"testnet": cfg.testnet, "recv_window": 10000}
        if cfg.api_key:
            kwargs.update(api_key=cfg.api_key, api_secret=cfg.api_secret)
        if not cfg.testnet and cfg.bybit_tld:
            kwargs["tld"] = cfg.bybit_tld
        self.http = HTTP(**kwargs)
        # Algunos endpoints públicos de Bybit EU devuelven listas vacías; los datos de
        # mercado (no las órdenes ni el saldo) se piden entonces al endpoint global.
        self.http_publico = HTTP(testnet=cfg.testnet) if "tld" in kwargs else None
        self._aviso_publico = False
        self.ws: Optional[WebSocket] = None

    def _lista(self, respuesta) -> list:
        return (respuesta or {}).get("result", {}).get("list", []) or []

    def _publico(self, metodo: str, **params) -> list:
        """Datos de mercado: endpoint configurado y, si viene vacío, el global."""
        lista = self._lista(getattr(self.http, metodo)(**params))
        if not lista and self.http_publico is not None:
            if not self._aviso_publico:
                logger.warning("Bybit %s devolvió datos vacíos para %s en %s; usando el endpoint público global.",
                               self.cfg.bybit_tld.upper(), self.symbol, metodo)
                self._aviso_publico = True
            lista = self._lista(getattr(self.http_publico, metodo)(**params))
        return lista

    # ---- mercado ----
    def cargar_filtros(self) -> Filtros:
        info = self._lista(self.http.get_instruments_info(category="spot", symbol=self.symbol))
        if not info and not self.cfg.dry_run:
            # En real, el símbolo tiene que existir donde se envían las órdenes.
            raise ErrorExchange(f"El símbolo {self.symbol} no se puede operar en este endpoint de Bybit "
                                f"({self.cfg.bybit_tld or 'global'}). En Bybit EU usa pares en USDC o EUR, "
                                f"p.ej. SYMBOL=BTCUSDC.")
        if not info:
            info = self._publico("get_instruments_info", category="spot", symbol=self.symbol)
        if not info:
            raise ErrorExchange(f"El símbolo {self.symbol} no existe en Bybit spot. Revisa SYMBOL en el .env "
                                f"(en Bybit EU suelen operarse pares en USDC o EUR, p.ej. BTCUSDC).")
        info = info[0]
        self.activo_base, self.activo_cotizado = info["baseCoin"], info["quoteCoin"]
        lot, price = info.get("lotSizeFilter", {}), info.get("priceFilter", {})
        return Filtros(
            paso_cantidad=_dec(lot.get("basePrecision")),
            paso_precio=_dec(price.get("tickSize")),
            cantidad_minima=_dec(lot.get("minOrderQty")),
            nocional_minimo=_dec(lot.get("minOrderAmt")),
        )

    def obtener_velas(self, limite: int) -> pd.DataFrame:
        velas = self._publico("get_kline", category="spot", symbol=self.symbol, interval=self.intervalo,
                              limit=min(limite + 1, 1000))
        ahora_ms = time.time() * 1000
        duracion_ms = self.cfg.timeframe_min * 60_000
        # Bybit devuelve de más reciente a más antigua e incluye la vela aún abierta: se descarta.
        filas = [fila_vela(*v[:6]) for v in reversed(velas) if float(v[0]) + duracion_ms <= ahora_ms]
        return pd.DataFrame(filas, columns=COLUMNAS_OHLCV).tail(limite).reset_index(drop=True)

    # ---- cuenta ----
    def saldo_libre(self, moneda: str) -> Decimal:
        cuentas = self._lista(self.http.get_wallet_balance(accountType="UNIFIED", coin=moneda))
        for cuenta in cuentas:
            for c in cuenta.get("coin", []):
                if c.get("coin") == moneda:
                    libre = _dec(c.get("walletBalance")) - _dec(c.get("locked"))
                    return max(libre, Decimal("0"))
        return Decimal("0")

    # ---- órdenes ----
    def colocar_limit(self, lado: str, cantidad: str, precio: str, post_only: bool = True) -> str:
        res = self.http.place_order(category="spot", symbol=self.symbol, side=lado, orderType="Limit",
                                    qty=cantidad, price=precio, timeInForce="PostOnly" if post_only else "GTC")
        return str(res["result"]["orderId"])

    def colocar_market(self, lado: str, cantidad: str) -> str:
        res = self.http.place_order(category="spot", symbol=self.symbol, side=lado, orderType="Market",
                                    qty=cantidad, marketUnit="baseCoin")
        return str(res["result"]["orderId"])

    def _buscar(self, order_id: str, **extra) -> Optional[dict]:
        lista = self._lista(self.http.get_open_orders(category="spot", symbol=self.symbol, orderId=order_id, **extra))
        if not lista:
            lista = self._lista(self.http.get_order_history(category="spot", symbol=self.symbol,
                                                            orderId=order_id, **extra))
        return lista[0] if lista else None

    def consultar_orden(self, order_id: str) -> InfoOrden:
        try:
            orden = self._buscar(order_id)
        except Exception as e:
            logger.warning("No se pudo consultar la orden %s: %s", order_id, e)
            return InfoOrden(order_id, EstadoOrden.DESCONOCIDA)
        if not orden:
            return InfoOrden(order_id, EstadoOrden.DESCONOCIDA)
        return InfoOrden(
            order_id,
            ESTADOS.get(orden.get("orderStatus"), EstadoOrden.DESCONOCIDA),
            _dec(orden.get("cumExecQty")),
            _dec(orden.get("avgPrice")),
        )

    def cancelar_orden(self, order_id: str) -> bool:
        try:
            self.http.cancel_order(category="spot", symbol=self.symbol, orderId=order_id)
            return True
        except Exception as e:
            logger.info("Cancelación de %s no aplicada: %s", order_id, e)
            return False

    def colocar_stop(self, cantidad: str, precio_disparo: str) -> str:
        res = self.http.place_order(category="spot", symbol=self.symbol, side="Sell", orderType="Market",
                                    qty=cantidad, triggerPrice=precio_disparo, orderFilter="StopOrder",
                                    marketUnit="baseCoin")
        return str(res["result"]["orderId"])

    def stop_activo(self, order_id: str) -> Optional[bool]:
        try:
            orden = self._buscar(order_id, orderFilter="StopOrder")
        except Exception as e:
            logger.warning("No se pudo consultar el stop %s: %s", order_id, e)
            return None
        if not orden:
            return None
        return orden.get("orderStatus") in ("Untriggered", "New")

    def cancelar_stop(self, order_id: str) -> bool:
        try:
            self.http.cancel_order(category="spot", symbol=self.symbol, orderId=order_id, orderFilter="StopOrder")
            return True
        except Exception as e:
            logger.info("Cancelación del stop %s no aplicada: %s", order_id, e)
            return False

    # ---- streams ----
    def iniciar_streams(self, on_libro: CallbackLibro, on_vela: CallbackVela) -> None:
        kwargs = {"testnet": self.cfg.testnet, "channel_type": "spot"}
        if not self.cfg.testnet and self.cfg.bybit_tld:
            kwargs["tld"] = self.cfg.bybit_tld
        self.ws = WebSocket(**kwargs)

        def cb_libro(msg):
            try:
                data = msg.get("data", {})
                if data.get("b") and data.get("a"):
                    on_libro(D(data["b"][0][0]), D(data["a"][0][0]))
            except Exception:
                logger.exception("Mensaje de orderbook no válido")

        def cb_vela(msg):
            try:
                for d in msg.get("data", []):
                    if d.get("confirm"):
                        on_vela(fila_vela(d["start"], d["open"], d["high"], d["low"], d["close"], d["volume"]))
            except Exception:
                logger.exception("Mensaje de vela no válido")

        self.ws.orderbook_stream(depth=1, symbol=self.symbol, callback=cb_libro)
        self.ws.kline_stream(interval=self.intervalo, symbol=self.symbol, callback=cb_vela)

    def detener_streams(self) -> None:
        if self.ws:
            try:
                self.ws.exit()
            except Exception:
                pass
