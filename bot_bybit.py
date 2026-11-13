import os
import time
import math
import logging
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP, WebSocket

load_dotenv()

class Config:
    API_KEY = os.getenv("BYBIT_API_KEY", "")
    API_SECRET = os.getenv("BYBIT_API_SECRET", "")
    SYMBOL = "BTCUSDT"
    TIMEFRAME = "15"  # CAMBIO 1: Velas de 15 min para entradas más frecuentes
    CAPITAL_SIMULADO = 10000.0
    RISK_PER_TRADE_PCT = 1.0
    MAX_ORDER_RETRIES = 5
    ORDER_TIMEOUT_SEC = 10
    SMA_FAST = 9
    SMA_SLOW = 21
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0  
    ATR_MULTIPLIER_TP = 4.0  
    DRY_RUN = False
    LOG_LEVEL = logging.INFO

logging.basicConfig(level=Config.LOG_LEVEL, format='%(asctime)s - %(message)s', force=True)
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

class BotBybit:
    def __init__(self, config: Config):
        print("🔹 Inicializando BotBybit...", flush=True)
        self.config = config
        self.df = pd.DataFrame()
        self.session = HTTP(testnet=False, tld="eu", api_key=self.config.API_KEY, api_secret=self.config.API_SECRET)
        self.ws = WebSocket(testnet=False, tld="eu", channel_type="spot")
        self.capital_actual = config.CAPITAL_SIMULADO
        self.step_size = 0.00001
        self.tick_size = 0.01
        self.best_bid = 0.0 
        self.best_ask = 0.0 
        self.ultimo_mensaje = time.time()
        self.estado = {
            "posicion_abierta": False,
            "precio_entrada": 0.0,
            "cantidad_activos": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "orden_en_curso": False
        }

    def iniciar(self):
        print("🔹 Cargando filtros del símbolo...", flush=True)
        self.cargar_filtros_simbolo()
        print("🔹 Cargando historial de velas...", flush=True)
        self.cargar_historial()
        print("🔹 Conectando WebSockets...", flush=True)
        self.ws.orderbook_stream(depth=1, symbol=self.config.SYMBOL, callback=self.on_orderbook_message)
        self.ws.kline_stream(interval=self.config.TIMEFRAME, symbol=self.config.SYMBOL, callback=self.on_kline_message)
        
        print(f"🚨 SISTEMA BYBIT INICIADO EN {self.config.SYMBOL} (VELAS {self.config.TIMEFRAME}m) 🚨", flush=True)
        print("📡 Escuchando mercado en tiempo real...", flush=True)
        
        try:
            while True:
                time.sleep(10)
                if time.time() - self.ultimo_mensaje > 300:
                    print("🛑 Conexión muerta (Watchdog). Forzando reinicio...", flush=True)
                    os._exit(1)
        except KeyboardInterrupt:
            print("🛑 Deteniendo el bot...", flush=True)
            self.ws.exit()

    def cargar_filtros_simbolo(self):
        try:
            res = self.session.get_instruments_info(category="spot", symbol=self.config.SYMBOL)
            lista = res.get('result', {}).get('list', [])
            if lista:
                info = lista[0]
                self.step_size = float(info['lotSizeFilter']['basePrecision'])
                self.tick_size = float(info['priceFilter']['tickSize'])
                print(f"📏 Filtros API cargados: Step={self.step_size}, Tick={self.tick_size}", flush=True)
            else:
                print("⚠️ Sin filtros desde API. Usando predeterminados BTCUSDT (0.00001 / 0.01).", flush=True)
                self.step_size = 0.00001
                self.tick_size = 0.01
        except Exception as e:
            print(f"⚠️ Error al obtener filtros de API ({e}). Usando predeterminados.", flush=True)
            self.step_size = 0.00001
            self.tick_size = 0.01

    def ajustar_cantidad(self, cantidad: float) -> str:
        cant_truncada = math.floor(cantidad / self.step_size) * self.step_size
        precision = int(round(-math.log(self.step_size, 10), 0)) if self.step_size < 1 else 0
        return "{:0.0{}f}".format(cant_truncada, precision)

    def ajustar_precio(self, precio: float) -> str:
        precio_truncado = math.floor(precio / self.tick_size) * self.tick_size
        precision = int(round(-math.log(self.tick_size, 10), 0)) if self.tick_size < 1 else 0
        return "{:0.0{}f}".format(precio_truncado, precision)

    def cargar_historial(self):
        try:
            res = self.session.get_kline(category="spot", symbol=self.config.SYMBOL, interval=self.config.TIMEFRAME, limit=100)
            velas = res.get('result', {}).get('list', [])
            if not velas:
                pub_session = HTTP(testnet=False)
                res = pub_session.get_kline(category="spot", symbol=self.config.SYMBOL, interval=self.config.TIMEFRAME, limit=100)
                velas = res.get('result', {}).get('list', [])
            velas.reverse()
            datos = [{'timestamp': pd.to_datetime(float(v[0]), unit='ms'), 'open': float(v[1]), 'high': float(v[2]), 'low': float(v[3]), 'close': float(v[4]), 'volume': float(v[5])} for v in velas]
            self.df = pd.DataFrame(datos)
            self.recalcular_matrices()
            print("✅ Historial de 100 velas cargado correctamente.", flush=True)
        except Exception as e:
            print(f"❌ Error al cargar historial de velas: {e}", flush=True)

    def recalcular_matrices(self):
        self.df['SMA_F'] = self.df['close'].rolling(self.config.SMA_FAST).mean()
        self.df['SMA_S'] = self.df['close'].rolling(self.config.SMA_SLOW).mean()
        self.df['ATR'] = IndicadoresAvanzados.calcular_atr(self.df, self.config.ATR_PERIOD)
        adx_df = IndicadoresAvanzados.calcular_adx(self.df, self.config.ADX_PERIOD)
        self.df['ADX'] = adx_df['ADX']
        self.df['MACD_Hist'] = adx_df['MACD_Hist']

    def obtener_balance_usdt(self) -> float:
        if self.config.DRY_RUN: return self.capital_actual
        res = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        return float(res['result']['list'][0]['coin'][0]['walletBalance'])

    def on_orderbook_message(self, message):
        self.ultimo_mensaje = time.time()
        data = message.get('data', {})
        if 'b' in data and data['b']: self.best_bid = float(data['b'][0][0])
        if 'a' in data and data['a']: self.best_ask = float(data['a'][0][0])
        self.vigilar_riesgo()

    def on_kline_message(self, message):
        self.ultimo_mensaje = time.time()
        data = message.get('data', [])
        if data and isinstance(data, list) and data[0].get('confirm', False):
            d = data[0]
            timestamp = pd.to_datetime(float(d['start']), unit='ms')
            nueva_fila = pd.DataFrame([{'timestamp': timestamp, 'open': float(d['open']), 'high': float(d['high']), 'low': float(d['low']), 'close': float(d['close']), 'volume': float(d['volume'])}])
            self.df = pd.concat([self.df.iloc[1:], nueva_fila], ignore_index=True)
            self.recalcular_matrices()
            self.evaluar_estrategia()

    def ejecutor_inteligente_maker(self, lado: str, cantidad_objetivo: float):
        self.estado['orden_en_curso'] = True
        cantidad_str = self.ajustar_cantidad(cantidad_objetivo)
        intentos = 0
        llenado = False
        precio_ejecutado = 0.0

        if float(cantidad_str) == 0:
            self.estado['orden_en_curso'] = False
            return False, 0.0

        while intentos < self.config.MAX_ORDER_RETRIES and not llenado:
            intentos += 1
            precio_limite_raw = self.best_bid if lado == 'Buy' else self.best_ask
            precio_limite_str = self.ajustar_precio(precio_limite_raw)
            print(f"⚙️ MARKET MAKER: {lado} {cantidad_str} a ${precio_limite_str}", flush=True)

            if not self.config.DRY_RUN:
                try:
                    res = self.session.place_order(
                        category="spot", symbol=self.config.SYMBOL, side=lado, 
                        orderType="Limit", qty=cantidad_str, price=precio_limite_str, timeInForce="GTC"
                    )
                    order_id = res['result']['orderId']
                    time.sleep(self.config.ORDER_TIMEOUT_SEC)
                    status_res = self.session.get_order_history(category="spot", symbol=self.config.SYMBOL, orderId=order_id)
                    status = status_res['result']['list'][0]['orderStatus']
                    
                    if status == 'Filled':
                        llenado = True
                        precio_ejecutado = float(status_res['result']['list'][0]['avgPrice'])
                        print(f"   ✅ ¡Orden LLENADA a ${precio_ejecutado:.2f}!", flush=True)
                    elif status in ['New', 'PartiallyFilled']:
                        print("   ⏳ Precio escapó. Cancelando...", flush=True)
                        self.session.cancel_order(category="spot", symbol=self.config.SYMBOL, orderId=order_id)
                except Exception as e:
                    print(f"   ❌ Error Bybit API: {e}", flush=True)
                    break
            else:
                time.sleep(2)
                llenado = True
                precio_ejecutado = float(precio_limite_str)
                print(f"   ✅ [SIMULADOR] Orden {lado} simulada a ${precio_ejecutado:.2f}", flush=True)

        self.estado['orden_en_curso'] = False
        return llenado, precio_ejecutado

    def evaluar_estrategia(self):
        if len(self.df) < 50 or self.estado['orden_en_curso']: return
        vela, vela_prev = self.df.iloc[-1], self.df.iloc[-2]
        if vela['ADX'] > 25 and not self.estado['posicion_abierta']:
            cruce_alcista = vela_prev['SMA_F'] <= vela_prev['SMA_S'] and vela['SMA_F'] > vela['SMA_S']
            if cruce_alcista and vela['MACD_Hist'] > 0:
                print("📈 SEÑAL DE COMPRA CONFIRMADA.", flush=True)
                self.lanzar_compra_estrategica(vela['ATR'])

    def lanzar_compra_estrategica(self, atr: float):
        capital = self.obtener_balance_usdt()
        riesgo_usd = capital * (self.config.RISK_PER_TRADE_PCT / 100)
        distancia_sl = atr * self.config.ATR_MULTIPLIER_SL
        
        # Cálculo de tamaño de posición inicial
        cantidad = riesgo_usd / distancia_sl
        
        # CAMBIO 2: Límite de Capital en SPOT (Hard Cap)
        precio_estimado = self.best_ask if self.best_ask > 0 else self.df.iloc[-1]['close']
        coste_orden = cantidad * precio_estimado
        limite_max_usd = capital * 0.95
        
        if coste_orden > limite_max_usd:
            print(f"⚠️ Ajuste de capital: Orden original (${coste_orden:.2f}) supera el 95% del saldo disponible. Limitando cantidad a max capital (${limite_max_usd:.2f})", flush=True)
            cantidad = limite_max_usd / precio_estimado

        llenado, precio_entrada = self.ejecutor_inteligente_maker('Buy', cantidad)
        if llenado:
            self.estado['posicion_abierta'] = True
            self.estado['precio_entrada'] = precio_entrada
            self.estado['cantidad_activos'] = float(self.ajustar_cantidad(cantidad))
            self.estado['stop_loss'] = precio_entrada - distancia_sl
            self.estado['take_profit'] = precio_entrada + (atr * self.config.ATR_MULTIPLIER_TP)
            if self.config.DRY_RUN: self.capital_actual -= (self.estado['cantidad_activos'] * precio_entrada)
            print(f"🟩 POSICIÓN LONG ABIERTA | SL: ${self.estado['stop_loss']:.2f} | TP: ${self.estado['take_profit']:.2f}", flush=True)

    def vigilar_riesgo(self):
        if not self.estado['posicion_abierta'] or self.estado['orden_en_curso']: return
        if self.best_bid <= self.estado['stop_loss']:
            print("💥 STOP LOSS ALCANZADO.", flush=True)
            self.cerrar_posicion('Sell', "Stop Loss")
        elif self.best_bid >= self.estado['take_profit']:
            print("🎉 TAKE PROFIT ALCANZADO.", flush=True)
            self.cerrar_posicion('Sell', "Take Profit")

    def cerrar_posicion(self, lado: str, razon: str):
        llenado, precio_salida = self.ejecutor_inteligente_maker(lado, self.estado['cantidad_activos'])
        if llenado:
            pnl = (precio_salida - self.estado['precio_entrada']) * self.estado['cantidad_activos']
            if self.config.DRY_RUN: self.capital_actual += (self.estado['cantidad_activos'] * precio_salida)
            print(f"⬛ POSICIÓN CERRADA ({razon}) | P&L: ${pnl:+.2f}", flush=True)
            self.estado['posicion_abierta'] = False

if __name__ == "__main__":
    bot = BotBybit(Config())
    bot.iniciar()
