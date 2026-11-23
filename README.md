# Trading Bot spot: Bybit EU / Binance 🤖

Bot de trading algorítmico para **spot** que opera en **Bybit EU (API V5, cuenta unificada)** o **Binance**.
Entra en tendencias alcistas confirmadas y gestiona el riesgo con stops dinámicos basados en ATR.

> ⚠️ **Por defecto arranca en simulación (`DRY_RUN=true`)**: usa datos reales del mercado pero no envía órdenes.
> Para operar con dinero real pon `DRY_RUN=false` en el `.env`. Prueba primero con `TESTNET=true` y con el backtest.

## 📈 Estrategia

Se evalúa al **cierre** de cada vela (15 min en Bybit y 1 h en Binance por defecto):

1. **Filtro de tendencia:** ADX(14) > 25.
2. **Entrada:** cruce alcista de SMA 9 sobre SMA 21, con el histograma del MACD (12, 26, 9) positivo.
3. **Tamaño de posición:** se arriesga el 1 % del saldo hasta el stop, con un máximo del 95 % del saldo por orden.
4. **Salidas:** stop loss a 2 × ATR y take profit a 4 × ATR.

## 🛡️ Gestión del riesgo y de la ejecución

- **Stop-loss en el exchange:** al abrir una posición se coloca un stop en Bybit o Binance, así que la posición sigue protegida aunque se caiga el bot o el servidor. Antes de cerrar por TP, el bot cancela ese stop para no vender dos veces.
- **Stop loss a mercado:** si el bot detecta el SL, vende a mercado al instante en lugar de dejar una orden límite por encima del precio.
- **Órdenes maker de verdad:** se usa `PostOnly` en Bybit y `LIMIT_MAKER` en Binance. Una orden que se ejecutaría como taker es rechazada por el exchange y el bot reintenta con el precio actualizado.
- **Llenados parciales:** se acumula lo ejecutado y solo se pide el resto. Nunca hay dos órdenes del bot abiertas a la vez. Una orden que se llena justo mientras se cancela cuenta como ejecutada.
- **Comisiones:** la cantidad de la posición es lo que realmente llega a la cuenta, descontada la comisión que se cobra en BTC. El P&L se registra en neto.
- **Límite de pérdida diaria:** si se pierde más del 3 % en el día (UTC), no se abren nuevas posiciones hasta el día siguiente.
- **Precisión:** cantidades y precios se calculan con `Decimal`, siempre ajustados al paso del exchange.

## 🧱 Tolerancia a fallos

- **Estado persistente** (`estado_<exchange>.json`, con escritura atómica). Al reiniciar, el bot recupera la posición abierta con su SL y TP y la compara con los saldos reales y con el stop del exchange.
- **No toca tu BTC:** solo vende lo que compró el propio bot. El BTC que ya tuvieras en la cuenta no se vende, salvo que actives `VENDER_BASE_NO_REGISTRADO=true`.
- **WebSocket sin bloqueos:** los callbacks solo actualizan precios y encolan velas. Las órdenes se ejecutan en el hilo principal mientras los precios siguen actualizándose.
- **Watchdog:** si no llegan datos en 5 min, el bot sale con código 1 y el supervisor (`deploy/run.sh` o systemd) lo reinicia.
- **Huecos y duplicados de velas:** tras una reconexión se ignoran las velas repetidas y, si falta alguna, se recarga el historial.
- **Registros:** log rotativo (`bot_<exchange>.log`), historial de operaciones en CSV (`operaciones_<exchange>.csv`) y alertas opcionales por Telegram.

## 🗂️ Estructura

```
trading_bot/
  config.py        configuración desde .env (con validación)
  estrategia.py    indicadores y señal (compartidos con el backtest)
  riesgo.py        tamaño de posición
  ejecutor.py      órdenes maker, reintentos, parciales y respaldo a mercado
  motor.py         bucle principal, SL/TP, reconciliación y límite diario
  estado.py        persistencia JSON y registro CSV
  notificador.py   alertas de Telegram
  exchanges/       adaptadores de Bybit, Binance y simulado (DRY_RUN)
bot_bybit.py       atajo: python bot_bybit.py
bot_produccion.py  atajo para Binance
backtest.py        backtest con comisiones y deslizamiento
tests/             tests con un exchange simulado (sin red)
deploy/            run.sh (tmux) y servicio systemd
```

## 🚀 Instalación

```bash
git clone https://github.com/javier-delacruz-rodriguez/TRADING-BOT.git
cd TRADING-BOT
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y edita el .env
```

Claves API: activa solo el permiso de **spot trading**, **nunca el de retiros**, y restringe la clave a la IP de tu servidor.

## ▶️ Ejecución

```bash
python bot_bybit.py                         # Bybit
python bot_produccion.py                    # Binance
python -m trading_bot.main --exchange bybit # equivalente
```

En el servidor, con reinicio automático:

```bash
tmux new -s bot_bybit
./deploy/run.sh bybit
```

O, mejor, como servicio systemd (ver instrucciones en `deploy/trading-bot@.service`).

## 🔬 Backtest

```bash
python backtest.py --descargar --dias 180 --timeframe 15 --guardar velas.csv
python backtest.py --csv velas.csv --operaciones ops.csv
```

Entra al open de la vela siguiente a la señal. Si una misma vela toca el SL y el TP, se asume el SL. Incluye comisión (0,1 % por lado) y deslizamiento.

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

Los tests también se ejecutan en GitHub Actions en cada push.

## ⚖️ Aviso

Software educativo, sin garantía. El trading con criptomonedas conlleva un riesgo alto de pérdida. Usa solo capital que puedas permitirte perder.
