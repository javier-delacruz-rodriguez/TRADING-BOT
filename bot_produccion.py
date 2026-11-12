import os
import math
import logging
import asyncio
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from binance import AsyncClient, BinanceSocketManager
from binance.exceptions import BinanceAPIException

load_dotenv()

# ============================================================================
# CONFIGURACIÓN DE PRODUCCIÓN
# ============================================================================
class Config:
    API_KEY = os.getenv("BINANCE_API_KEY", "")
    API_SECRET = os.getenv("BINANCE_API_SECRET", "")
    SYMBOL = "BTCUSDT"
    TIMEFRAME = "1h"
    
    # RIESGO Y CAPITAL (Fuego Real)
    # Por seguridad, el bot consultará tu saldo real de USDT en Binance.
    RISK_PER_TRADE_PCT = 1.0          # Arriesga máximo 1% de tu saldo por operación
    
    # Microestructura
    MAX_ORDER_RETRIES = 5
    ORDER_TIMEOUT_SEC = 10
    
    # Indicadores
    SMA_FAST = 9
    SMA_SLOW = 21
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0  
    ATR_MULTIPLIER_TP = 4.0  
    
    # MODO PRODUCCIÓN: CUIDADO
    DRY_RUN = False # <--- False = DINERO REAL
    LOG_LEVEL = logging.INFO

logging.basicConfig(level=Config.LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("trading_real.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

class IndicadoresAvanzados:
    @staticmethod
    def calcular_atr(df: pd.DataFrame, periodo: int) -> pd.Series:
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1/periodo, adjust=False).mean()

    @staticmethod
    def calcular_adx(df: pd.DataFrame, periodo: int) -> pd.DataFrame:
        high, low = df['high'], df['low']
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        
        pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        
        tr_smooth = IndicadoresAvanzados.calcular_atr(df, periodo)
        pos_dm_smooth = pos_dm.ewm(alpha=1/periodo, adjust=False).mean()
        neg_dm_smooth = neg_dm.ewm(alpha=1/periodo, adjust=False).mean()
        
        pos_di = 100 * (pos_dm_smooth / tr_smooth)
        neg_di = 100 * (neg_dm_smooth / tr_smooth)
        
        dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di)
        adx = dx.ewm(alpha=1/periodo, adjust=False).mean()
        return pd.DataFrame({'ADX': adx, 'MACD_Hist': IndicadoresAvanzados.calcular_macd_hist(df)})

    @staticmethod
    def calcular_macd_hist(df: pd.DataFrame, fast=12, slow=26, signal=9):
        ema_f = df['close'].ewm(span=fast, adjust=False).mean()
        ema_s = df['close'].ewm(span=slow, adjust=False).mean()
        macd = ema_f - ema_s
        return macd - macd.ewm(span=signal, adjust=False).mean()

class BotProduccionAsync:
    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.bsm = None
        self.df = pd.DataFrame()
        
        # Filtros de precisión de Binance
        self.step_size = 0.0
        self.tick_size = 0.0
        
        self.best_bid = 0.0 
        self.best_ask = 0.0 
        
        self.estado = {
            "posicion_abierta": False,
            "precio_entrada": 0.0,
            "cantidad_activos": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "orden_en_curso": False
        }

    async def iniciar_sistemas(self):
        if not self.config.API_KEY or not self.config.API_SECRET:
            raise ValueError("¡Faltan las credenciales de la API en el archivo .env!")
            
        self.client = await AsyncClient.create(self.config.API_KEY, self.config.API_SECRET)
        self.bsm = BinanceSocketManager(self.client)
        
        await self.cargar_filtros_simbolo()
        
        velas = await self.client.get_klines(symbol=self.config.SYMBOL, interval=self.config.TIMEFRAME, limit=100)
        datos = [{'timestamp': pd.to_datetime(v[0], unit='ms'), 'open': float(v[1]), 'high': float(v[2]), 'low': float(v[3]), 'close': float(v[4]), 'volume': float(v[5])} for v in velas]
        self.df = pd.DataFrame(datos)
        self.recalcular_matrices()
        
        logger.info(f"🚨 SISTEMA REAL INICIADO EN {self.config.SYMBOL} 🚨")
        logger.info(f"Step Size (Cantidad): {self.step_size} | Tick Size (Precio): {self.tick_size}")

    async def cargar_filtros_simbolo(self):
        """Descarga las reglas matemáticas del exchange para evitar errores LOT_SIZE"""
        info = await self.client.get_symbol_info(self.config.SYMBOL)
        for filtro in info['filters']:
            if filtro['filterType'] == 'LOT_SIZE':
                self.step_size = float(filtro['stepSize'])
            elif filtro['filterType'] == 'PRICE_FILTER':
                self.tick_size = float(filtro['tickSize'])

    def ajustar_cantidad(self, cantidad: float) -> str:
        """Trunca la cantidad al número de decimales exacto que permite Binance"""
        precision = int(round(-math.log(self.step_size, 10), 0))
        cant_truncada = math.floor(cantidad / self.step_size) * self.step_size
        return "{:0.0{}f}".format(cant_truncada, precision)

    def ajustar_precio(self, precio: float) -> str:
        """Trunca el precio al número de decimales exacto que permite Binance"""
        precision = int(round(-math.log(self.tick_size, 10), 0))
        precio_truncado = math.floor(precio / self.tick_size) * self.tick_size
        return "{:0.0{}f}".format(precio_truncado, precision)

    def recalcular_matrices(self):
        self.df['SMA_F'] = self.df['close'].rolling(self.config.SMA_FAST).mean()
        self.df['SMA_S'] = self.df['close'].rolling(self.config.SMA_SLOW).mean()
        self.df['ATR'] = IndicadoresAvanzados.calcular_atr(self.df, self.config.ATR_PERIOD)
        adx_df = IndicadoresAvanzados.calcular_adx(self.df, self.config.ADX_PERIOD)
        self.df = pd.concat([self.df, adx_df], axis=1)

    async def obtener_balance_usdt(self) -> float:
        balance = await self.client.get_asset_balance(asset='USDT')
        return float(balance['free'])

    async def ejecutor_inteligente_maker(self, lado: str, cantidad_objetivo: float):
        self.estado['orden_en_curso'] = True
        cantidad_str = self.ajustar_cantidad(cantidad_objetivo)
        
        if float(cantidad_str) == 0:
            logger.error("Cantidad ajustada es 0. Operación abortada.")
            self.estado['orden_en_curso'] = False
            return False, 0.0

        intentos = 0
        llenado = False
        precio_ejecutado = 0.0

        logger.info(f"⚙️ MARKET MAKER: {lado} {cantidad_str} {self.config.SYMBOL}")

        while intentos < self.config.MAX_ORDER_RETRIES and not llenado:
            intentos += 1
            precio_limite_raw = self.best_bid if lado == 'BUY' else self.best_ask
            precio_limite_str = self.ajustar_precio(precio_limite_raw)
            
            try:
                # Orden LIMIT tipo Post-Only (GTC)
                order = await self.client.create_order(
                    symbol=self.config.SYMBOL,
                    side=lado,
                    type='LIMIT',
                    timeInForce='GTC',
                    quantity=cantidad_str,
                    price=precio_limite_str
                )
                order_id = order['orderId']
                logger.info(f"   [Intento {intentos}] Orden colocada a ${precio_limite_str}. Esperando {self.config.ORDER_TIMEOUT_SEC}s...")
                
                await asyncio.sleep(self.config.ORDER_TIMEOUT_SEC)
                
                status = await self.client.get_order(symbol=self.config.SYMBOL, orderId=order_id)
                if status['status'] == 'FILLED':
                    llenado = True
                    # Calcular el precio medio real pagado (por si hubo llenado parcial a varios precios)
                    cummulative_quote = float(status['cummulativeQuoteQty'])
                    executed_qty = float(status['executedQty'])
                    precio_ejecutado = cummulative_quote / executed_qty
                    logger.info(f"   ✅ ¡Orden LLENADA a ${precio_ejecutado:.2f}!")
                elif status['status'] in ['NEW', 'PARTIALLY_FILLED']:
                    logger.warning("   ⏳ El precio se escapó. Cancelando y reubicando...")
                    await self.client.cancel_order(symbol=self.config.SYMBOL, orderId=order_id)
                    
            except BinanceAPIException as e:
                logger.error(f"   ❌ Error API: {e.message}")
                break

        self.estado['orden_en_curso'] = False
        return llenado, precio_ejecutado

    async def lanzar_compra_estrategica(self, atr: float):
        capital = await self.obtener_balance_usdt()
        riesgo_usd = capital * (self.config.RISK_PER_TRADE_PCT / 100)
        distancia_sl = atr * self.config.ATR_MULTIPLIER_SL
        
        cantidad = riesgo_usd / distancia_sl
        
        logger.info(f"Preparando compra. Capital: ${capital:.2f} | Riesgo: ${riesgo_usd:.2f}")
        llenado, precio_entrada = await self.ejecutor_inteligente_maker('BUY', cantidad)
        
        if llenado:
            self.estado['posicion_abierta'] = True
            self.estado['precio_entrada'] = precio_entrada
            self.estado['cantidad_activos'] = float(self.ajustar_cantidad(cantidad))
            self.estado['stop_loss'] = precio_entrada - distancia_sl
            self.estado['take_profit'] = precio_entrada + (atr * self.config.ATR_MULTIPLIER_TP)
            logger.info(f"🟩 POSICIÓN LONG ABIERTA | SL: ${self.estado['stop_loss']:.2f} | TP: ${self.estado['take_profit']:.2f}")

    async def evaluar_estrategia(self):
        if len(self.df) < 50 or self.estado['orden_en_curso']: return
        
        vela, vela_prev = self.df.iloc[-1], self.df.iloc[-2]
        if vela['ADX'] > 25 and not self.estado['posicion_abierta']:
            cruce_alcista = vela_prev['SMA_F'] <= vela_prev['SMA_S'] and vela['SMA_F'] > vela['SMA_S']
            if cruce_alcista and vela['MACD_Hist'] > 0:
                logger.info("📈 SEÑAL DE COMPRA CONFIRMADA.")
                asyncio.create_task(self.lanzar_compra_estrategica(vela['ATR']))

    async def vigilar_riesgo(self):
        if not self.estado['posicion_abierta'] or self.estado['orden_en_curso']: return

        if self.best_bid <= self.estado['stop_loss']:
            logger.warning("💥 STOP LOSS ALCANZADO. CERRANDO POSICIÓN.")
            await self.cerrar_posicion('SELL', "Stop Loss")
        elif self.best_bid >= self.estado['take_profit']:
            logger.info("🎉 TAKE PROFIT ALCANZADO. ASEGURANDO GANANCIAS.")
            await self.cerrar_posicion('SELL', "Take Profit")

    async def cerrar_posicion(self, lado: str, razon: str):
        llenado, precio_salida = await self.ejecutor_inteligente_maker(lado, self.estado['cantidad_activos'])
        if llenado:
            pnl = (precio_salida - self.estado['precio_entrada']) * self.estado['cantidad_activos']
            logger.info(f"⬛ POSICIÓN CERRADA ({razon}) | P&L: ${pnl:+.2f}")
            self.estado['posicion_abierta'] = False
            self.estado['cantidad_activos'] = 0.0

    async def escuchar_mercados(self):
        stream_velas = f"{self.config.SYMBOL.lower()}@kline_{self.config.TIMEFRAME}"
        stream_libro = f"{self.config.SYMBOL.lower()}@bookTicker"
        
        socket = self.bsm.multiplex_socket([stream_velas, stream_libro])
        logger.info("📡 Conectado al mercado en tiempo real...")
        
        async with socket as stream:
            while True:
                try:
                    respuesta = await stream.recv()
                    data = respuesta['data']
                    
                    if respuesta['stream'] == stream_libro:
                        self.best_bid = float(data['b'])
                        self.best_ask = float(data['a'])
                        await self.vigilar_riesgo()
                        
                    elif respuesta['stream'] == stream_velas:
                        vela = data['k']
                        if vela['x']:
                            timestamp = pd.to_datetime(vela['t'], unit='ms')
                            nueva_fila = pd.DataFrame([{'timestamp': timestamp, 'open': float(vela['o']), 'high': float(vela['h']), 'low': float(vela['l']), 'close': float(vela['c']), 'volume': float(vela['v'])}])
                            self.df = pd.concat([self.df.iloc[1:], nueva_fila], ignore_index=True)
                            self.recalcular_matrices()
                            await self.evaluar_estrategia()
                except Exception as e:
                    logger.error(f"Error de red: {e}. Reconectando...")
                    await asyncio.sleep(2)

    async def ejecutar(self):
        try:
            await self.iniciar_sistemas()
            await self.escuchar_mercados()
        finally:
            if self.client:
                await self.client.close_connection()

if __name__ == "__main__":
    bot = BotProduccionAsync(Config())
    try:
        asyncio.run(bot.ejecutar())
    except KeyboardInterrupt:
        logger.info("🛑 Bot detenido manualmente.")
