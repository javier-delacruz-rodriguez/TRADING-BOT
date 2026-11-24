"""Configuración del bot, leída de variables de entorno (.env)."""
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

EXCHANGES = ("bybit", "binance")
TIMEFRAME_POR_DEFECTO = {"bybit": 15, "binance": 60}


def _str(nombre: str, defecto: str = "") -> str:
    valor = os.getenv(nombre)
    return defecto if valor is None or valor.strip() == "" else valor.strip()


def _bool(nombre: str, defecto: bool) -> bool:
    valor = os.getenv(nombre)
    if valor is None or valor.strip() == "":
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def _float(nombre: str, defecto: float) -> float:
    return float(_str(nombre, str(defecto)))


def _int(nombre: str, defecto: int) -> int:
    return int(_str(nombre, str(defecto)))


@dataclass
class Config:
    exchange: str = "bybit"
    symbol: str = "BTCUSDT"
    timeframe_min: int = 15

    # Modo de ejecución
    dry_run: bool = True
    testnet: bool = False
    capital_simulado: float = 10000.0
    bybit_tld: str = "eu"

    # Credenciales
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)

    # Riesgo y capital
    risk_per_trade_pct: float = 1.0
    max_capital_use_pct: float = 95.0
    min_order_quote: float = 5.5
    max_perdida_diaria_pct: float = 3.0
    comision_pct: float = 0.1
    cooldown_after_reject_sec: int = 900

    # Ejecución de órdenes
    max_order_retries: int = 3
    order_timeout_sec: float = 10.0
    post_only: bool = True
    sl_a_mercado: bool = True
    allow_market_sell_fallback: bool = True
    usar_stop_en_exchange: bool = True
    stop_limit_deslizamiento_pct: float = 0.5
    allow_buy_orders: bool = True
    vender_base_no_registrado: bool = False

    # Estrategia
    sma_fast: int = 9
    sma_slow: int = 21
    atr_period: int = 14
    adx_period: int = 14
    adx_min: float = 25.0
    filtro_di: bool = False
    filtro_ema_tendencia: bool = False
    ema_tendencia: int = 200
    atr_multiplier_sl: float = 2.0
    atr_multiplier_tp: float = 4.0
    velas_historial: int = 900
    velas_minimas: int = 100

    # Infraestructura
    watchdog_sec: int = 300
    estado_archivo: str = ""
    operaciones_archivo: str = ""
    log_archivo: str = ""
    log_level: str = "INFO"
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

    @classmethod
    def desde_entorno(cls, exchange: Optional[str] = None) -> "Config":
        load_dotenv()
        ex = (exchange or _str("EXCHANGE", "bybit")).lower()
        if ex not in EXCHANGES:
            raise ValueError(f"EXCHANGE debe ser uno de {EXCHANGES}, no '{ex}'")
        prefijo = ex.upper()
        dry_run = _bool("DRY_RUN", True)
        sufijo = "_sim" if dry_run else ""

        cfg = cls(
            exchange=ex,
            symbol=_str("SYMBOL", "BTCUSDT").upper(),
            timeframe_min=_int("TIMEFRAME_MIN", TIMEFRAME_POR_DEFECTO[ex]),
            dry_run=dry_run,
            testnet=_bool("TESTNET", False),
            capital_simulado=_float("CAPITAL_SIMULADO", 10000.0),
            bybit_tld=_str("BYBIT_TLD", "eu"),
            api_key=_str(f"{prefijo}_API_KEY"),
            api_secret=_str(f"{prefijo}_API_SECRET"),
            risk_per_trade_pct=_float("RISK_PER_TRADE_PCT", 1.0),
            max_capital_use_pct=_float("MAX_CAPITAL_USE_PCT", 95.0),
            min_order_quote=_float("MIN_ORDER_QUOTE", 5.5),
            max_perdida_diaria_pct=_float("MAX_PERDIDA_DIARIA_PCT", 3.0),
            comision_pct=_float("COMISION_PCT", 0.1),
            cooldown_after_reject_sec=_int("COOLDOWN_AFTER_REJECT_SEC", 900),
            max_order_retries=_int("MAX_ORDER_RETRIES", 3),
            order_timeout_sec=_float("ORDER_TIMEOUT_SEC", 10.0),
            post_only=_bool("POST_ONLY", True),
            sl_a_mercado=_bool("SL_A_MERCADO", True),
            allow_market_sell_fallback=_bool("ALLOW_MARKET_SELL_FALLBACK", True),
            usar_stop_en_exchange=_bool("USAR_STOP_EN_EXCHANGE", True),
            stop_limit_deslizamiento_pct=_float("STOP_LIMIT_DESLIZAMIENTO_PCT", 0.5),
            allow_buy_orders=_bool("ALLOW_BUY_ORDERS", True),
            vender_base_no_registrado=_bool("VENDER_BASE_NO_REGISTRADO", False),
            sma_fast=_int("SMA_FAST", 9),
            sma_slow=_int("SMA_SLOW", 21),
            atr_period=_int("ATR_PERIOD", 14),
            adx_period=_int("ADX_PERIOD", 14),
            adx_min=_float("ADX_MIN", 25.0),
            filtro_di=_bool("FILTRO_DI", False),
            filtro_ema_tendencia=_bool("FILTRO_EMA_TENDENCIA", False),
            ema_tendencia=_int("EMA_TENDENCIA", 200),
            atr_multiplier_sl=_float("ATR_MULTIPLIER_SL", 2.0),
            atr_multiplier_tp=_float("ATR_MULTIPLIER_TP", 4.0),
            velas_historial=_int("VELAS_HISTORIAL", 900),
            velas_minimas=_int("VELAS_MINIMAS", 100),
            watchdog_sec=_int("WATCHDOG_SEC", 300),
            estado_archivo=_str("ESTADO_ARCHIVO", f"estado_{ex}{sufijo}.json"),
            operaciones_archivo=_str("OPERACIONES_ARCHIVO", f"operaciones_{ex}{sufijo}.csv"),
            log_archivo=_str("LOG_ARCHIVO", f"bot_{ex}{sufijo}.log"),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
            telegram_token=_str("TELEGRAM_TOKEN"),
            telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
        )
        cfg.validar()
        return cfg

    def validar(self) -> None:
        errores = []
        if not self.dry_run and (not self.api_key or not self.api_secret):
            ex = self.exchange.upper()
            errores.append(f"Faltan {ex}_API_KEY / {ex}_API_SECRET en el .env (DRY_RUN=false)")
        if not 0 < self.risk_per_trade_pct <= 5:
            errores.append("RISK_PER_TRADE_PCT debe estar entre 0 y 5")
        if not 0 < self.max_capital_use_pct <= 100:
            errores.append("MAX_CAPITAL_USE_PCT debe estar entre 0 y 100")
        if self.sma_fast >= self.sma_slow:
            errores.append("SMA_FAST debe ser menor que SMA_SLOW")
        if self.atr_multiplier_sl <= 0 or self.atr_multiplier_tp <= 0:
            errores.append("Los multiplicadores de ATR deben ser positivos")
        if self.max_order_retries < 1:
            errores.append("MAX_ORDER_RETRIES debe ser >= 1")
        if self.velas_historial < self.velas_minimas:
            errores.append("VELAS_HISTORIAL debe ser >= VELAS_MINIMAS")
        if self.velas_minimas < self.sma_slow + 2:
            errores.append("VELAS_MINIMAS es demasiado pequeño para SMA_SLOW")
        if errores:
            raise ValueError("Configuración inválida:\n - " + "\n - ".join(errores))
