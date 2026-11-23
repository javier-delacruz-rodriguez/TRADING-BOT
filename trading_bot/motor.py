"""Motor del bot: estrategia + gestión de la posición, independiente del exchange.

Hilos:
* Los callbacks del WebSocket sólo actualizan precios y encolan velas cerradas (rápidos).
* El hilo principal procesa velas, vigila SL/TP y ejecuta órdenes. Mientras espera a
  que una orden se llene, el WebSocket sigue actualizando bid/ask, así que cada
  reintento usa el precio real del momento.
"""
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

import pandas as pd

from . import estrategia
from .config import Config
from .ejecutor import Ejecutor
from .estado import AlmacenEstado, EstadoBot, Posicion
from .exchanges.base import Exchange, Filtros
from .notificador import Notificador
from .precision import D, a_texto
from .riesgo import calcular_cantidad

logger = logging.getLogger(__name__)

CERO = Decimal("0")


class ConexionMuerta(Exception):
    """El WebSocket dejó de enviar datos: el proceso debe reiniciarse."""


class Bot:
    def __init__(self, cfg: Config, exchange: Exchange, almacen: AlmacenEstado,
                 notificador: Optional[Notificador] = None, reloj: Callable[[], float] = time.time,
                 dormir: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.ex = exchange
        self.almacen = almacen
        self.notif = notificador or Notificador()
        self.reloj = reloj
        self.dormir = dormir

        self._lock = threading.Lock()
        self._bid = CERO
        self._ask = CERO
        self.ultimo_mensaje = reloj()
        self.cola_velas: "queue.Queue[dict]" = queue.Queue()

        self.df = pd.DataFrame()
        self.filtros: Optional[Filtros] = None
        self.ejecutor: Optional[Ejecutor] = None
        self.estado = EstadoBot()
        self._proxima_comprobacion_stop = 0.0
        self._reintento_cierre_desde = 0.0
        self._detener = threading.Event()

    # ------------------------------------------------------------ precios
    def precios(self):
        with self._lock:
            return self._bid, self._ask

    def on_libro(self, bid: Decimal, ask: Decimal) -> None:
        with self._lock:
            self._bid, self._ask = D(bid), D(ask)
        self.ultimo_mensaje = self.reloj()

    def on_vela(self, vela: dict) -> None:
        self.ultimo_mensaje = self.reloj()
        self.cola_velas.put(vela)

    def _precio_referencia(self, lado: str) -> Decimal:
        bid, ask = self.precios()
        precio = ask if lado == "Buy" else bid
        if precio <= 0 and len(self.df):
            precio = D(self.df.iloc[-1]["close"])
        return precio

    # ------------------------------------------------------------ arranque
    def preparar(self) -> None:
        self.filtros = self.ex.cargar_filtros()
        self.filtros.nocional_minimo = max(self.filtros.nocional_minimo, D(self.cfg.min_order_quote))
        logger.info("📏 Filtros %s: paso cantidad=%s, paso precio=%s, mín. cantidad=%s, mín. nocional=%s",
                    self.cfg.symbol, self.filtros.paso_cantidad, self.filtros.paso_precio,
                    self.filtros.cantidad_minima, self.filtros.nocional_minimo)
        self.ejecutor = Ejecutor(self.ex, self.filtros, self.precios, self.cfg, dormir=self.dormir)
        self.recargar_historial()
        self.estado = self.almacen.cargar()
        self.reconciliar()

    def recargar_historial(self) -> None:
        velas = self.ex.obtener_velas(self.cfg.velas_historial)
        self.df = estrategia.calcular_indicadores(velas, self.cfg)
        logger.info("✅ Historial cargado: %d velas cerradas.", len(self.df))
        if len(self.df) < self.cfg.velas_minimas:
            logger.warning("Sólo hay %d velas (mínimo %d para operar).", len(self.df), self.cfg.velas_minimas)

    def reconciliar(self) -> None:
        """Contrasta el estado guardado con los saldos reales tras un (re)inicio."""
        base = self.ex.activo_base
        pos = self.estado.posicion
        if pos:
            if pos.stop_order_id:
                activo = self.ex.stop_activo(pos.stop_order_id)
                if activo is False:
                    logger.warning("El stop-loss del exchange se ejecutó mientras el bot estaba parado.")
                    self._registrar_cierre(pos.cantidad, pos.stop_loss, "Stop Loss (exchange, offline)")
                    return
                if activo:
                    logger.info("♻️ Posición recuperada (stop en exchange activo): %s %s a %s | SL %s | TP %s",
                                a_texto(pos.cantidad), base, a_texto(pos.precio_entrada),
                                a_texto(pos.stop_loss), a_texto(pos.take_profit))
                    return
            disponible = self.ex.saldo_libre(base)
            if disponible >= pos.cantidad * D("0.98"):
                logger.info("♻️ Posición recuperada: %s %s a %s | SL %s | TP %s", a_texto(pos.cantidad), base,
                            a_texto(pos.precio_entrada), a_texto(pos.stop_loss), a_texto(pos.take_profit))
                if self.cfg.usar_stop_en_exchange and not pos.stop_order_id:
                    self._colocar_stop(pos)
                    self._guardar()
            elif self.filtros.es_valida(self.filtros.cantidad(disponible), pos.precio_entrada):
                logger.warning("Saldo de %s menor que la posición guardada; se ajusta a %s.", base, disponible)
                pos.cantidad = disponible
                self._guardar()
            else:
                logger.warning("La posición guardada ya no está en la cuenta (vendida fuera del bot). Se descarta.")
                self.estado.posicion = None
                self._guardar()
            return

        disponible = self.ex.saldo_libre(base)
        precio = self._precio_referencia("Sell")
        cantidad = self.filtros.cantidad(disponible)
        if precio > 0 and self.filtros.es_valida(cantidad, precio):
            if self.cfg.vender_base_no_registrado:
                logger.warning("Vendiendo %s %s no registrados por el bot (VENDER_BASE_NO_REGISTRADO=true).",
                               a_texto(cantidad), base)
                self.ejecutor.vender(cantidad, a_mercado=True)
            else:
                logger.info("Hay %s %s en la cuenta que el bot no compró: no se tocarán.", a_texto(cantidad), base)

    # ------------------------------------------------------------ bucle
    def ejecutar(self) -> None:
        self.preparar()
        self.ex.iniciar_streams(self.on_libro, self.on_vela)
        self.ultimo_mensaje = self.reloj()
        modo = "SIMULACIÓN" if self.cfg.dry_run else ("TESTNET" if self.cfg.testnet else "DINERO REAL")
        logger.info("🚨 BOT %s INICIADO EN %s | velas %dm | modo %s 🚨", self.ex.nombre, self.cfg.symbol,
                    self.cfg.timeframe_min, modo)
        self.notif.enviar(f"🚀 Bot iniciado ({modo}) en {self.cfg.symbol}")
        try:
            while not self._detener.is_set():
                self.paso()
        finally:
            self.ex.detener_streams()

    def detener(self) -> None:
        self._detener.set()

    def paso(self, espera: float = 1.0) -> None:
        try:
            vela = self.cola_velas.get(timeout=espera)
        except queue.Empty:
            vela = None
        if vela is not None:
            self.procesar_vela(vela)
        self.vigilar_riesgo()
        if self.reloj() - self.ultimo_mensaje > self.cfg.watchdog_sec:
            raise ConexionMuerta(f"Sin datos del exchange en {self.cfg.watchdog_sec}s")

    def procesar_vela(self, vela: dict) -> None:
        ts = pd.Timestamp(vela["timestamp"])
        if len(self.df):
            ultimo = self.df.iloc[-1]["timestamp"]
            if ts <= ultimo:
                return  # duplicada (p.ej. tras reconexión)
            if ts - ultimo > pd.Timedelta(minutes=self.cfg.timeframe_min):
                logger.warning("Hueco en las velas (%s → %s). Recargando historial.", ultimo, ts)
                self.recargar_historial()
                if len(self.df) and self.df.iloc[-1]["timestamp"] >= ts:
                    self.evaluar_estrategia()
                    return
        nueva = pd.DataFrame([vela], columns=estrategia.COLUMNAS_OHLCV)
        velas = pd.concat([self.df[estrategia.COLUMNAS_OHLCV] if len(self.df) else None, nueva], ignore_index=True)
        self.df = estrategia.calcular_indicadores(velas.tail(self.cfg.velas_historial), self.cfg)
        self.evaluar_estrategia()

    # ------------------------------------------------------------ estrategia
    def _actualizar_dia(self) -> None:
        hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.estado.dia != hoy:
            self.estado.dia = hoy
            self.estado.pnl_dia = CERO
            self.estado.capital_ref_dia = self.ex.saldo_libre(self.ex.activo_cotizado)
            self._guardar()

    def limite_diario_alcanzado(self) -> bool:
        if self.cfg.max_perdida_diaria_pct <= 0 or self.estado.capital_ref_dia <= 0:
            return False
        limite = self.estado.capital_ref_dia * D(self.cfg.max_perdida_diaria_pct) / 100
        return self.estado.pnl_dia <= -limite

    def evaluar_estrategia(self) -> None:
        if self.estado.posicion or len(self.df) < self.cfg.velas_minimas:
            return
        if not estrategia.senal_compra(self.df, self.cfg):
            return
        logger.info("📈 SEÑAL DE COMPRA CONFIRMADA.")
        if not self.cfg.allow_buy_orders:
            logger.info("⏸️ ALLOW_BUY_ORDERS=false: no se compra.")
            return
        if self.reloj() < self.estado.bloqueado_hasta:
            logger.info("⏸️ En pausa tras un rechazo hasta %s.",
                        datetime.fromtimestamp(self.estado.bloqueado_hasta).strftime("%H:%M:%S"))
            return
        self._actualizar_dia()
        if self.limite_diario_alcanzado():
            logger.warning("⛔ Límite de pérdida diaria alcanzado (%s). Sin compras hasta mañana (UTC).",
                           a_texto(self.estado.pnl_dia))
            return
        self.abrir_posicion(D(self.df.iloc[-1]["ATR"]))

    def _pausar(self, motivo: str) -> None:
        self.estado.bloqueado_hasta = self.reloj() + self.cfg.cooldown_after_reject_sec
        self._guardar()
        logger.warning("⛔ %s. Pausa de %d min.", motivo, self.cfg.cooldown_after_reject_sec // 60)

    def abrir_posicion(self, atr: Decimal) -> None:
        base, quote = self.ex.activo_base, self.ex.activo_cotizado
        saldo = self.ex.saldo_libre(quote)
        precio = self._precio_referencia("Buy")
        cantidad, motivo = calcular_cantidad(saldo, atr, precio, self.cfg, self.filtros)
        if cantidad is None:
            self._pausar(motivo)
            return

        base_antes = self.ex.saldo_libre(base)
        res = self.ejecutor.limit_maker("Buy", cantidad)
        if not res.llenado:
            if res.error:
                self._pausar(f"Compra rechazada: {res.error}")
                self.notif.enviar(f"⚠️ Compra rechazada: {res.error}")
            else:
                logger.info("La compra no se llenó; se espera a la próxima señal.")
            return

        # Cantidad real recibida (en spot la comisión de compra suele cobrarse en la moneda base)
        recibido = self.ex.saldo_libre(base) - base_antes
        cantidad_pos = recibido if CERO < recibido <= res.cantidad else res.cantidad
        distancia_sl = atr * D(self.cfg.atr_multiplier_sl)
        pos = Posicion(
            cantidad=cantidad_pos,
            precio_entrada=res.precio_medio,
            stop_loss=res.precio_medio - distancia_sl,
            take_profit=res.precio_medio + atr * D(self.cfg.atr_multiplier_tp),
            abierta_en=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.estado.posicion = pos
        self._guardar()  # se guarda antes del stop: si algo falla, la posición no se pierde
        if self.cfg.usar_stop_en_exchange:
            self._colocar_stop(pos)
            self._guardar()
        msg = (f"🟩 LONG abierto: {a_texto(pos.cantidad)} {base} a {a_texto(pos.precio_entrada)} | "
               f"SL {pos.stop_loss:.2f} | TP {pos.take_profit:.2f}")
        logger.info(msg)
        self.notif.enviar(msg)

    def _colocar_stop(self, pos: Posicion) -> None:
        cantidad = self.filtros.cantidad(pos.cantidad)
        disparo = self.filtros.precio(pos.stop_loss, "Buy")
        try:
            pos.stop_order_id = self.ex.colocar_stop(a_texto(cantidad), a_texto(disparo))
            logger.info("🛡️ Stop-loss colocado en el exchange a %s (id %s).", a_texto(disparo), pos.stop_order_id)
        except Exception as e:
            pos.stop_order_id = None
            logger.error("No se pudo colocar el stop en el exchange (%s). El bot vigilará el SL.", e)
            self.notif.enviar(f"⚠️ Stop-loss en exchange no colocado: {e}")

    # ------------------------------------------------------------ riesgo
    def vigilar_riesgo(self) -> None:
        pos = self.estado.posicion
        if not pos or self.reloj() < self._reintento_cierre_desde:
            return
        bid, _ = self.precios()
        if bid <= 0:
            return

        if pos.stop_order_id and self.reloj() >= self._proxima_comprobacion_stop:
            self._proxima_comprobacion_stop = self.reloj() + 30
            if self.ex.stop_activo(pos.stop_order_id) is False:
                self._cierre_por_stop_exchange(pos)
                return

        if bid <= pos.stop_loss:
            logger.warning("💥 STOP LOSS ALCANZADO (bid %s ≤ %s).", a_texto(bid), a_texto(pos.stop_loss))
            self.cerrar_posicion("Stop Loss", a_mercado=self.cfg.sl_a_mercado)
        elif bid >= pos.take_profit:
            logger.info("🎉 TAKE PROFIT ALCANZADO (bid %s ≥ %s).", a_texto(bid), a_texto(pos.take_profit))
            self.cerrar_posicion("Take Profit", a_mercado=False)

    def _cierre_por_stop_exchange(self, pos: Posicion) -> None:
        info = self.ex.consultar_orden(pos.stop_order_id)
        precio = info.precio_medio if info.precio_medio > 0 else pos.stop_loss
        cantidad = info.ejecutado if info.ejecutado > 0 else pos.cantidad
        self._registrar_cierre(cantidad, precio, "Stop Loss (exchange)")

    def cerrar_posicion(self, razon: str, a_mercado: bool) -> None:
        pos = self.estado.posicion
        if pos.stop_order_id:
            # Primero se retira el stop del exchange (en Binance bloquea el saldo y además
            # evita vender dos veces). Si no se puede cancelar, comprobar si ya se ejecutó.
            if not self.ex.cancelar_stop(pos.stop_order_id):
                activo = self.ex.stop_activo(pos.stop_order_id)
                if activo is False:
                    self._cierre_por_stop_exchange(pos)
                    return
                if activo is None:
                    # Si ya no queda saldo vendible, el stop se ejecutó (algunos exchanges lo
                    # convierten en otra orden y deja de ser consultable por su id).
                    disponible = self.filtros.cantidad(self.ex.saldo_libre(self.ex.activo_base))
                    if not self.filtros.es_valida(disponible, self._precio_referencia("Sell")):
                        self._cierre_por_stop_exchange(pos)
                        return
                    logger.warning("No se pudo cancelar ni consultar el stop; se reintentará en 10 s.")
                    self._reintento_cierre_desde = self.reloj() + 10
                    return
            pos.stop_order_id = None
            self._guardar()

        # Nunca se vende más de lo que compró el bot ni más de lo que hay disponible.
        disponible = self.ex.saldo_libre(self.ex.activo_base)
        cantidad = self.filtros.cantidad(min(pos.cantidad, disponible))
        precio = self._precio_referencia("Sell")
        if not self.filtros.es_valida(cantidad, precio):
            logger.warning("Cantidad a vender (%s) por debajo del mínimo; se da la posición por cerrada.",
                           a_texto(cantidad))
            self.estado.posicion = None
            self._guardar()
            return

        res = self.ejecutor.vender(cantidad, a_mercado=a_mercado)
        if not res.llenado:
            logger.error("❌ No se pudo cerrar la posición (%s). Reintento en 10 s.", res.error or "sin llenado")
            self._reintento_cierre_desde = self.reloj() + 10
            if self.cfg.usar_stop_en_exchange:
                self._colocar_stop(pos)
                self._guardar()
            return

        restante = pos.cantidad - res.cantidad
        if restante > 0 and self.filtros.es_valida(self.filtros.cantidad(restante), precio):
            self._contabilizar(res.cantidad, res.precio_medio, pos, razon + " (parcial)")
            pos.cantidad = restante
            self._guardar()
            logger.warning("Cierre parcial: quedan %s por vender; se reintentará.", a_texto(restante))
            return
        self._registrar_cierre(res.cantidad, res.precio_medio, razon)

    # ------------------------------------------------------------ contabilidad
    def _contabilizar(self, cantidad: Decimal, precio_salida: Decimal, pos: Posicion, razon: str) -> Decimal:
        self._actualizar_dia()
        comision = D(self.cfg.comision_pct) / 100 * cantidad * (pos.precio_entrada + precio_salida)
        pnl = (precio_salida - pos.precio_entrada) * cantidad - comision
        self.estado.pnl_dia += pnl
        self.estado.pnl_total += pnl
        self.almacen.registrar_operacion(
            simbolo=self.cfg.symbol, razon=razon, cantidad=a_texto(cantidad),
            entrada=a_texto(pos.precio_entrada), salida=f"{precio_salida:.8f}",
            pnl_neto=f"{pnl:.4f}", abierta_en=pos.abierta_en,
        )
        return pnl

    def _registrar_cierre(self, cantidad: Decimal, precio_salida: Decimal, razon: str) -> None:
        pos = self.estado.posicion
        pnl = self._contabilizar(cantidad, precio_salida, pos, razon)
        self.estado.posicion = None
        self.estado.operaciones_totales += 1
        self._guardar()
        msg = (f"⬛ POSICIÓN CERRADA ({razon}) a {precio_salida:.2f} | P&L neto {pnl:+.2f} | "
               f"P&L hoy {self.estado.pnl_dia:+.2f} | total {self.estado.pnl_total:+.2f}")
        logger.info(msg)
        self.notif.enviar(msg)

    def _guardar(self) -> None:
        self.almacen.guardar(self.estado)
