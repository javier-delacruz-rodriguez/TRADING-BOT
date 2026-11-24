"""Punto de entrada: ``python -m trading_bot.main --exchange bybit``."""
import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from .config import Config
from .estado import AlmacenEstado
from .exchanges import crear_exchange
from .exchanges.simulado import ExchangeSimulado
from .motor import Bot, ConexionMuerta
from .notificador import Notificador

logger = logging.getLogger("trading_bot")


def configurar_logging(cfg: Config) -> None:
    formato = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    manejadores = [logging.StreamHandler(sys.stdout)]
    if cfg.log_archivo:
        manejadores.append(RotatingFileHandler(cfg.log_archivo, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    raiz = logging.getLogger()
    raiz.handlers.clear()
    for m in manejadores:
        m.setFormatter(formato)
        raiz.addHandler(m)
    raiz.setLevel(cfg.log_level)
    # Las librerías de red son muy verbosas en INFO
    for ruidoso in ("urllib3", "websocket", "pybit", "binance"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def construir_bot(cfg: Config) -> Bot:
    almacen = AlmacenEstado(cfg.estado_archivo, cfg.operaciones_archivo)
    exchange = crear_exchange(cfg)
    if cfg.dry_run:
        estado = almacen.cargar()
        base_inicial = estado.posicion.cantidad if estado.posicion else 0
        exchange = ExchangeSimulado(exchange, cfg.capital_simulado, cfg.comision_pct, base_inicial)
    notif = Notificador(cfg.telegram_token, cfg.telegram_chat_id, prefijo=f"[{cfg.exchange} {cfg.symbol}] ")
    return Bot(cfg, exchange, almacen, notif)


def main(exchange: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Bot de trading spot (Bybit / Binance)")
    parser.add_argument("--exchange", choices=["bybit", "binance"], default=exchange)
    args = parser.parse_args()

    try:
        cfg = Config.desde_entorno(args.exchange)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    configurar_logging(cfg)
    if cfg.dry_run:
        logger.info("🧪 DRY_RUN activo: órdenes simuladas con %.2f de capital. Pon DRY_RUN=false para operar real.",
                    cfg.capital_simulado)

    bot = None
    codigo = 0
    try:
        bot = construir_bot(cfg)
        bot.ejecutar()
    except KeyboardInterrupt:
        logger.info("🛑 Bot detenido manualmente.")
    except ConexionMuerta as e:
        logger.error("🛑 %s. Saliendo para que el supervisor reinicie el proceso.", e)
        codigo = 1
    except Exception as e:
        logger.exception("💀 Error fatal: %s", e)
        if bot:
            bot.notif.enviar(f"💀 Bot detenido por error: {e}")
        codigo = 1
    return codigo


def ejecutar_y_salir(exchange: Optional[str] = None) -> None:
    codigo = main(exchange)
    logging.shutdown()
    # os._exit evita que un hilo de WebSocket colgado impida terminar el proceso
    os._exit(codigo)


if __name__ == "__main__":
    ejecutar_y_salir()
