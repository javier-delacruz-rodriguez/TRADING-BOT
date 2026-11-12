# Bybit EU Automated HFT Trading Bot 🚀

Un bot de trading algorítmico autónomo de alta frecuencia (HFT) diseñado para operar en **Bybit EU (V5 Unified Trading API)**. Integra ejecución cuantitativa, gestión dinámica de riesgos y resistencia a fallos para infraestructura cloud en AWS.

## 🛠️ Arquitectura y Tecnologías
- **Lenguaje:** Python 3.14
- **API Exchange:** `pybit` (Bybit Unified Trading API V5 - EU Endpoints)
- **Análisis de Datos:** `pandas`, `numpy`
- **Infraestructura Cloud:** AWS EC2 (Ubuntu 22.04 LTS, Tokio)
- **Persistencia y Resiliencia:** Tmux + Watchdog en Python + Auto-restart loop en Bash

## 📈 Estrategia de Trading
1. **Filtro de Tendencia (ADX):** Evalúa la fuerza del mercado ($ADX > 25$) antes de abrir posiciones.
2. **Confirmación de Entrada (SMA + MACD):** Cruce alcista de medias móviles (SMA 9 / SMA 21) respaldado por el histograma del MACD.
3. **Gestión Dinámica del Riesgo (ATR):**
   - Cálculo dinámico de tamaño de posición basado en el 1% de riesgo por operación.
   - **Stop Loss:** $2.0 \times ATR$
   - **Take Profit:** $4.0 \times ATR$
4. **Ejecutor Market Maker:** Envío de órdenes límite al *Orderbook* para minimizar tarifas de comisión (*maker fees*).

## 🛡️ Tolerancia a Fallos (Cloud Resilience)
- **Heartbeat Watchdog:** Monitor de inactividad que detecta congelamientos de red (`Errno 104`) y reinicia el proceso en menos de 5 minutos.
- **Manejo Regional Directo:** Conexión nativa a endpoints de Europa (`tld="eu"`).

## 🚀 Despliegue en Servidor Linux
```bash
# Clone e instalación de dependencias
git clone [https://github.com/TU_USUARIO/bybit-hft-bot.git](https://github.com/TU_USUARIO/bybit-hft-bot.git)
cd bybit-hft-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configurar variables de entorno (.env)
BYBIT_API_KEY=tu_api_key
BYBIT_API_SECRET=tu_api_secret

# Ejecución persistente
tmux new -s bot_bybit
while true; do python -u bot_bybit.py; sleep 5; done
