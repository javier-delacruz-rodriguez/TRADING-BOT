#!/usr/bin/env bash
# Supervisor sencillo para tmux: reinicia el bot si se cae, con espera creciente.
# Uso: ./deploy/run.sh bybit   (o binance)
set -u
cd "$(dirname "$0")/.."
EXCHANGE="${1:-bybit}"
ESPERA=5
while true; do
  inicio=$(date +%s)
  ./venv/bin/python -u -m trading_bot.main --exchange "$EXCHANGE"
  codigo=$?
  [ "$codigo" -eq 0 ] && { echo "Bot detenido normalmente."; exit 0; }
  [ "$codigo" -eq 2 ] && { echo "Configuración inválida: revisa el .env."; exit 2; }
  # Si corrió más de 10 min se considera estable y la espera vuelve a 5 s
  [ $(( $(date +%s) - inicio )) -gt 600 ] && ESPERA=5
  echo "Bot terminó con código $codigo. Reinicio en ${ESPERA}s..."
  sleep "$ESPERA"
  ESPERA=$(( ESPERA * 2 > 300 ? 300 : ESPERA * 2 ))
done
