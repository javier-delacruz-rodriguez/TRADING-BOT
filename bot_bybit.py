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
    TIMEFRAME = "15"
    CAPITAL_SIMULADO = 10000.0
    RISK_PER_TRADE_PCT = 1.0
    MAX_ORDER_RETRIES = 3
    ORDER_TIMEOUT_SEC = 10
    SMA_FAST = 9
    SMA_SLOW = 21
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0
    ATR_MULTIPLIER_TP = 4.0

    # Controles de capital y operativa Spot
    ALLOW_BUY_ORDERS = True           # Activado para operar automáticamente
    MAX_CAPITAL_USE_PCT = 95.0        # Máximo % del saldo USDT disponible por orden
    MIN_ORDER_USDT = 5.5              # Mínimo seguro en USDT para evitar error 170140 y comisiones altas
    COOLDOWN_AFTER_REJECT_SEC = 900   # 15 min de pausa si una orden es rechazada

    # Gestión de BTC pendiente y ventas no completadas
    RECOVER_STUCK_BTC = True          # Lee saldo real de BTC y vende BTC sobrante/pendiente para liberar USDT
    KEEP_BTC_RESERVE = 0.0            # Cantidad mínima de BTC a reservar sin vender (0.0 para vender todo lo vendible)
    ALLOW_MARKET_SELL_FALLBACK = True # Si la venta Limit no se llena, ejecuta venta Market para no dejar BTC atascado

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
        self.btc_simulado = 0.0
        self.step_size = 0.00001
        self.tick_size = 0.01
        self.min_order_amt = config.MIN_ORDER_USDT
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.ultimo_mensaje = time.time()
        self.bloqueado_hasta = 0.0
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

        print(f"🚨 SISTEMA BYBIT INICIADO EN {self.config.SYMBOL} (VELAS {self.config.TIMEFRAME}m | COMPRAS={self.config.ALLOW_BUY_ORDERS}) 🚨", flush=True)
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
                if 'minOrderAmt' in info.get('lotSizeFilter', {}):
                    api_min = float(info['lotSizeFilter']['minOrderAmt'])
                    self.min_order_amt = max(self.config.MIN_ORDER_USDT, api_min)
                print(f"📏 Filtros API cargados: Step={self.step_size}, Tick={self.tick_size}, MinAmt={self.min_order_amt}", flush=True)
            else:
                print("⚠️ Sin filtros desde API. Usando predeterminados.", flush=True)
        except Exception as e:
            print(f"⚠️ Error al obtener filtros de API ({e}). Usando predeterminados.", flush=True)

    def ajustar_cantidad(self, cantidad: float) -> str:
        precision = int(round(-math.log(self.step_size, 10), 0)) if self.step_size < 1 else 0
        cant_truncada = math.floor(cantidad / self.step_size) * self.step_size
        return f"{cant_truncada:.{precision}f}"

    def ajustar_precio(self, precio: float) -> str:
        precision = int(round(-math.log(self.tick_size, 10), 0)) if self.tick_size < 1 else 0
        precio_truncado = round(math.floor(precio / self.tick_size) * self.tick_size, precision)
        return f"{precio_truncado:.{precision}f}"

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

    def obtener_balance_moneda(self, moneda: str) -> float:
        if self.config.DRY_RUN:
            return self.capital_actual if moneda == "USDT" else self.btc_simulado
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED", coin=moneda)
            coins = res.get('result', {}).get('list', [{}])[0].get('coin', [])
            if coins:
                coin_data = coins[0]
                for campo in ('walletBalance', 'equity', 'usdValue'):
                    if campo in coin_data and coin_data[campo] not in (None, ''):
                        if campo == 'usdValue':
                            precio_ref = self.best_bid if self.best_bid > 0 else (float(self.df.iloc[-1]['close']) if len(self.df) > 0 else 0.0)
                            return float(coin_data[campo]) / precio_ref if precio_ref > 0 else 0.0
                        return float(coin_data[campo])
        except Exception as e:
            print(f"⚠️ Error al consultar balance {moneda}: {e}", flush=True)
        return 0.0

    def obtener_balance_usdt(self) -> float:
        return self.obtener_balance_moneda("USDT")

    def obtener_balance_btc(self) -> float:
        return self.obtener_balance_moneda("BTC")

    def ejecutar_venta_market_respaldo(self, cantidad_str: str):
        precio_ref = self.best_bid if self.best_bid > 0 else (float(self.df.iloc[-1]['close']) if len(self.df) > 0 else 0.0)
        print(f"⚡ RESCATE VENTA MARKET: Sell {cantidad_str} BTC (~${float(cantidad_str) * precio_ref:.2f})", flush=True)
        if not self.config.DRY_RUN:
            try:
                res = self.session.place_order(
                    category="spot", symbol=self.config.SYMBOL, side="Sell",
                    orderType="Market", qty=cantidad_str
                )
                order_id = res.get('result', {}).get('orderId')
                time.sleep(2)
                if order_id:
                    status_res = self.session.get_order_history(category="spot", symbol=self.config.SYMBOL, orderId=order_id)
                    lista_historial = status_res.get('result', {}).get('list', [])
                    if lista_historial and float(lista_historial[0].get('avgPrice') or 0) > 0:
                        precio_ejecutado = float(lista_historial[0]['avgPrice'])
                        print(f"   ✅ ¡Venta Market completada a ${precio_ejecutado:.2f}!", flush=True)
                        return True, precio_ejecutado
                print("   ✅ ¡Venta Market enviada y procesada!", flush=True)
                return True, precio_ref
            except Exception as e:
                print(f"   ❌ Error en venta Market Bybit API: {e}", flush=True)
                return False, 0.0
        else:
            time.sleep(1)
            print(f"   ✅ [SIMULADOR] Venta Market simulada a ${precio_ref:.2f}", flush=True)
            return True, precio_ref

    def on_orderbook_message(self, message):
        self.ultimo_mensaje = time.time()
        data = message.get('data', {})
        if 'b' in data and data['b']:
            self.best_bid = float(data['b'][0][0])
        if 'a' in data and data['a']:
            self.best_ask = float(data['a'][0][0])
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
            if precio_limite_raw <= 0:
                break
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
                    lista_historial = status_res.get('result', {}).get('list', [])

                    if lista_historial:
                        status = lista_historial[0]['orderStatus']
                        if status == 'Filled':
                            llenado = True
                            precio_ejecutado = float(lista_historial[0]['avgPrice'])
                            print(f"   ✅ ¡Orden LLENADA a ${precio_ejecutado:.2f}!", flush=True)
                        elif status in ['New', 'PartiallyFilled']:
                            print("   ⏳ Precio escapó. Cancelando...", flush=True)
                            self.session.cancel_order(category="spot", symbol=self.config.SYMBOL, orderId=order_id)
                    else:
                        print("   ⚠️ Respuesta vacía al consultar orden. Reintentando...", flush=True)
                except Exception as e:
                    print(f"   ❌ Error Bybit API: {e}", flush=True)
                    if lado == 'Buy':
                        self.bloqueado_hasta = time.time() + self.config.COOLDOWN_AFTER_REJECT_SEC
                    break
            else:
                time.sleep(2)
                llenado = True
                precio_ejecutado = float(precio_limite_str)
                print(f"   ✅ [SIMULADOR] Orden {lado} simulada a ${precio_ejecutado:.2f}", flush=True)

        # Si es venta y no se completó por límite, usar respaldo Market para no dejar BTC atascado
        if lado == 'Sell' and not llenado and self.config.ALLOW_MARKET_SELL_FALLBACK:
            llenado, precio_ejecutado = self.ejecutar_venta_market_respaldo(cantidad_str)

        self.estado['orden_en_curso'] = False
        return llenado, precio_ejecutado

    def intentar_vender_btc_pendiente(self) -> bool:
        if not self.config.RECOVER_STUCK_BTC or self.estado['orden_en_curso'] or self.estado['posicion_abierta']:
            return False

        btc_total = self.obtener_balance_btc()
        btc_vendible = max(0.0, btc_total - self.config.KEEP_BTC_RESERVE)
        qty_str = self.ajustar_cantidad(btc_vendible)
        qty_float = float(qty_str)

        if qty_float <= 0:
            return False

        precio_ref = self.best_bid if self.best_bid > 0 else (float(self.df.iloc[-1]['close']) if len(self.df) > 0 else 0.0)
        valor_usd = qty_float * precio_ref
        if precio_ref <= 0 or valor_usd < self.min_order_amt:
            return False

        print(f"🔄 Detectado saldo BTC pendiente ({qty_float} BTC ≈ ${valor_usd:.2f}). Vendiendo para liberar USDT...", flush=True)
        llenado, precio_salida = self.ejecutor_inteligente_maker('Sell', qty_float)
        if llenado:
            if self.config.DRY_RUN:
                self.btc_simulado = max(0.0, self.btc_simulado - qty_float)
                self.capital_actual += (qty_float * precio_salida)
            print(f"💵 BTC pendiente vendido a ${precio_salida:.2f}. Saldo USDT actualizado.", flush=True)
            return True
        return False

    def evaluar_estrategia(self):
        if len(self.df) < 50 or self.estado['orden_en_curso']:
            return

        # Revisa si quedó BTC sin vender tras una operación incompleta o reinicio
        self.intentar_vender_btc_pendiente()

        if time.time() < self.bloqueado_hasta:
            return
        vela, vela_prev = self.df.iloc[-1], self.df.iloc[-2]
        if vela['ADX'] > 25 and not self.estado['posicion_abierta']:
            cruce_alcista = vela_prev['SMA_F'] <= vela_prev['SMA_S'] and vela['SMA_F'] > vela['SMA_S']
            if cruce_alcista and vela['MACD_Hist'] > 0:
                if not self.config.ALLOW_BUY_ORDERS:
                    print("⏸️ Señal detectada, pero ALLOW_BUY_ORDERS=False (no se gasta USDT en comprar BTC).", flush=True)
                    return
                print("📈 SEÑAL DE COMPRA CONFIRMADA.", flush=True)
                self.lanzar_compra_estrategica(vela['ATR'])

    def lanzar_compra_estrategica(self, atr: float):
        capital = self.obtener_balance_usdt()
        limite_max_usd = capital * (self.config.MAX_CAPITAL_USE_PCT / 100.0)

        if limite_max_usd < self.min_order_amt:
            print(
                f"⛔ Saldo USDT insuficiente para operar (${capital:.2f} USDT; máx usable ${limite_max_usd:.2f} < mínimo ${self.min_order_amt:.2f}). "
                f"Pausa de {self.config.COOLDOWN_AFTER_REJECT_SEC // 60} min.",
                flush=True
            )
            self.bloqueado_hasta = time.time() + self.config.COOLDOWN_AFTER_REJECT_SEC
            return

        if atr <= 0:
            return

        riesgo_usd = capital * (self.config.RISK_PER_TRADE_PCT / 100.0)
        distancia_sl = atr * self.config.ATR_MULTIPLIER_SL
        cantidad = riesgo_usd / distancia_sl

        precio_estimado = self.best_ask if self.best_ask > 0 else float(self.df.iloc[-1]['close'])
        coste_orden = cantidad * precio_estimado

        if coste_orden < self.min_order_amt:
            cantidad = self.min_order_amt / precio_estimado
            coste_orden = self.min_order_amt

        if coste_orden > limite_max_usd:
            cantidad = limite_max_usd / precio_estimado

        cantidad_str = self.ajustar_cantidad(cantidad)
        valor_final_usd = float(cantidad_str) * precio_estimado
        if float(cantidad_str) <= 0 or valor_final_usd < self.min_order_amt:
            print(f"⛔ Cantidad ajustada (${valor_final_usd:.2f}) por debajo del mínimo (${self.min_order_amt:.2f}). Evitando error 170140.", flush=True)
            self.bloqueado_hasta = time.time() + self.config.COOLDOWN_AFTER_REJECT_SEC
            return

        llenado, precio_entrada = self.ejecutor_inteligente_maker('Buy', float(cantidad_str))
        if llenado:
            cant_real = float(cantidad_str)
            self.estado['posicion_abierta'] = True
            self.estado['precio_entrada'] = precio_entrada
            self.estado['cantidad_activos'] = cant_real
            self.estado['stop_loss'] = precio_entrada - distancia_sl
            self.estado['take_profit'] = precio_entrada + (atr * self.config.ATR_MULTIPLIER_TP)
            if self.config.DRY_RUN:
                self.btc_simulado += cant_real
                self.capital_actual -= (cant_real * precio_entrada)
            print(f"🟩 POSICIÓN LONG ABIERTA | SL: ${self.estado['stop_loss']:.2f} | TP: ${self.estado['take_profit']:.2f}", flush=True)

    def vigilar_riesgo(self):
        if not self.estado['posicion_abierta'] or self.estado['orden_en_curso']:
            return
        if self.best_bid > 0 and self.best_bid <= self.estado['stop_loss']:
            print("💥 STOP LOSS ALCANZADO.", flush=True)
            self.cerrar_posicion('Sell', "Stop Loss")
        elif self.best_bid > 0 and self.best_bid >= self.estado['take_profit']:
            print("🎉 TAKE PROFIT ALCANZADO.", flush=True)
            self.cerrar_posicion('Sell', "Take Profit")

    def cerrar_posicion(self, lado: str, razon: str):
        saldo_btc_real = self.obtener_balance_btc()
        cantidad_a_vender = min(self.estado['cantidad_activos'], saldo_btc_real) if saldo_btc_real > 0 else self.estado['cantidad_activos']
        if cantidad_a_vender <= 0 and saldo_btc_real > 0:
            cantidad_a_vender = saldo_btc_real

        llenado, precio_salida = self.ejecutor_inteligente_maker(lado, cantidad_a_vender)
        if llenado:
            pnl = (precio_salida - self.estado['precio_entrada']) * cantidad_a_vender
            if self.config.DRY_RUN:
                self.btc_simulado = max(0.0, self.btc_simulado - cantidad_a_vender)
                self.capital_actual += (cantidad_a_vender * precio_salida)
            print(f"⬛ POSICIÓN CERRADA ({razon}) | P&L: ${pnl:+.2f}", flush=True)
            self.estado['posicion_abierta'] = False
            self.estado['cantidad_activos'] = 0.0

if __name__ == "__main__":
    bot = BotBybit(Config())
    bot.iniciar()
