"""Adaptador Binance spot con python-binance (cliente síncrono + WebSocket en hilo)."""
import logging
import time
from decimal import Decimal
from typing import Optional

import pandas as pd
from binance import Client, ThreadedWebsocketManager
from binance.exceptions import BinanceAPIException

from ..estrategia import COLUMNAS_OHLCV
from ..precision import D
from .base import (
    CallbackLibro,
    CallbackVela,
    ErrorExchange,
    EstadoOrden,
    Exchange,
    Filtros,
    InfoOrden,
    PostOnlyRechazada,
    fila_vela,
)

logger = logging.getLogger(__name__)

INTERVALOS = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 120: "2h",
              240: "4h", 360: "6h", 720: "12h", 1440: "1d"}

ESTADOS = {
    "NEW": EstadoOrden.ABIERTA,
    "PARTIALLY_FILLED": EstadoOrden.ABIERTA,
    "PENDING_NEW": EstadoOrden.ABIERTA,
    "FILLED": EstadoOrden.LLENADA,
    "CANCELED": EstadoOrden.CERRADA,
    "PENDING_CANCEL": EstadoOrden.CERRADA,
    "REJECTED": EstadoOrden.CERRADA,
    "EXPIRED": EstadoOrden.CERRADA,
    "EXPIRED_IN_MATCH": EstadoOrden.CERRADA,
}


class BinanceExchange(Exchange):
    nombre = "Binance"

    def __init__(self, cfg):
        if cfg.timeframe_min not in INTERVALOS:
            raise ValueError(f"TIMEFRAME_MIN={cfg.timeframe_min} no soportado por Binance: {sorted(INTERVALOS)}")
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.intervalo = INTERVALOS[cfg.timeframe_min]
        self.client = Client(cfg.api_key or None, cfg.api_secret or None, testnet=cfg.testnet,
                             requests_params={"timeout": 15})
        self.twm: Optional[ThreadedWebsocketManager] = None
        self.filtros: Optional[Filtros] = None

    # ---- mercado ----
    def cargar_filtros(self) -> Filtros:
        info = self.client.get_symbol_info(self.symbol)
        if not info:
            raise ErrorExchange(f"Binance no devolvió información del símbolo {self.symbol}")
        self.activo_base, self.activo_cotizado = info["baseAsset"], info["quoteAsset"]
        f = {x["filterType"]: x for x in info["filters"]}
        nocional = f.get("NOTIONAL", {}).get("minNotional") or f.get("MIN_NOTIONAL", {}).get("minNotional") or "0"
        self.filtros = Filtros(
            paso_cantidad=D(f["LOT_SIZE"]["stepSize"]).normalize(),
            paso_precio=D(f["PRICE_FILTER"]["tickSize"]).normalize(),
            cantidad_minima=D(f["LOT_SIZE"]["minQty"]),
            nocional_minimo=D(nocional),
        )
        return self.filtros

    def obtener_velas(self, limite: int) -> pd.DataFrame:
        velas = self.client.get_klines(symbol=self.symbol, interval=self.intervalo, limit=min(limite + 1, 1000))
        ahora_ms = time.time() * 1000
        # La última vela de Binance sigue abierta (close time en el futuro): se descarta.
        filas = [fila_vela(*v[:6]) for v in velas if float(v[6]) < ahora_ms]
        return pd.DataFrame(filas, columns=COLUMNAS_OHLCV).tail(limite).reset_index(drop=True)

    # ---- cuenta ----
    def saldo_libre(self, moneda: str) -> Decimal:
        saldo = self.client.get_asset_balance(asset=moneda)
        return D(saldo["free"]) if saldo else Decimal("0")

    # ---- órdenes ----
    def colocar_limit(self, lado: str, cantidad: str, precio: str, post_only: bool = True) -> str:
        params = dict(symbol=self.symbol, side=lado.upper(), quantity=cantidad, price=precio)
        if post_only:
            params["type"] = "LIMIT_MAKER"
        else:
            params.update(type="LIMIT", timeInForce="GTC")
        try:
            orden = self.client.create_order(**params)
        except BinanceAPIException as e:
            if e.code == -2010 and "immediately match" in str(e.message).lower():
                raise PostOnlyRechazada(e.message) from e
            raise
        return str(orden["orderId"])

    def colocar_market(self, lado: str, cantidad: str) -> str:
        orden = self.client.create_order(symbol=self.symbol, side=lado.upper(), type="MARKET", quantity=cantidad)
        return str(orden["orderId"])

    def consultar_orden(self, order_id: str) -> InfoOrden:
        try:
            o = self.client.get_order(symbol=self.symbol, orderId=int(order_id))
        except Exception as e:
            logger.warning("No se pudo consultar la orden %s: %s", order_id, e)
            return InfoOrden(order_id, EstadoOrden.DESCONOCIDA)
        ejecutado = D(o.get("executedQty", "0"))
        quote = D(o.get("cummulativeQuoteQty", "0"))
        precio = quote / ejecutado if ejecutado > 0 else Decimal("0")
        return InfoOrden(order_id, ESTADOS.get(o.get("status"), EstadoOrden.DESCONOCIDA), ejecutado, precio)

    def cancelar_orden(self, order_id: str) -> bool:
        try:
            self.client.cancel_order(symbol=self.symbol, orderId=int(order_id))
            return True
        except Exception as e:
            logger.info("Cancelación de %s no aplicada: %s", order_id, e)
            return False

    def colocar_stop(self, cantidad: str, precio_disparo: str) -> str:
        # STOP_LOSS_LIMIT con un precio límite algo por debajo del disparo para asegurar el llenado.
        disparo = D(precio_disparo)
        limite = disparo * (1 - D(self.cfg.stop_limit_deslizamiento_pct) / 100)
        orden = self.client.create_order(
            symbol=self.symbol, side="SELL", type="STOP_LOSS_LIMIT", timeInForce="GTC",
            quantity=cantidad, stopPrice=precio_disparo, price=self.filtros.texto_precio(limite, "Buy"),
        )
        return str(orden["orderId"])

    def stop_activo(self, order_id: str) -> Optional[bool]:
        info = self.consultar_orden(order_id)
        if info.estado == EstadoOrden.DESCONOCIDA:
            return None
        # Un stop disparado y parcialmente llenado ya no protege: se considera ejecutado.
        return info.estado == EstadoOrden.ABIERTA and info.ejecutado == 0

    def cancelar_stop(self, order_id: str) -> bool:
        return self.cancelar_orden(order_id)

    # ---- streams ----
    def iniciar_streams(self, on_libro: CallbackLibro, on_vela: CallbackVela) -> None:
        self.twm = ThreadedWebsocketManager(api_key=self.cfg.api_key or None,
                                            api_secret=self.cfg.api_secret or None, testnet=self.cfg.testnet)
        self.twm.daemon = True
        self.twm.start()

        def cb_libro(msg):
            if msg.get("e") == "error":
                logger.warning("Error en stream de libro: %s", msg)
                return
            try:
                on_libro(D(msg["b"]), D(msg["a"]))
            except Exception:
                logger.exception("Mensaje de libro no válido")

        def cb_vela(msg):
            if msg.get("e") == "error":
                logger.warning("Error en stream de velas: %s", msg)
                return
            try:
                k = msg["k"]
                if k["x"]:
                    on_vela(fila_vela(k["t"], k["o"], k["h"], k["l"], k["c"], k["v"]))
            except Exception:
                logger.exception("Mensaje de vela no válido")

        self.twm.start_symbol_book_ticker_socket(callback=cb_libro, symbol=self.symbol)
        self.twm.start_kline_socket(callback=cb_vela, symbol=self.symbol, interval=self.intervalo)

    def detener_streams(self) -> None:
        if self.twm:
            try:
                self.twm.stop()
            except Exception:
                pass
