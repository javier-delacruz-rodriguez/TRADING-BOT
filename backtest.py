"""Backtest de la estrategia con comisiones y deslizamiento.

Ejemplos:
    python backtest.py --csv velas.csv
    python backtest.py --descargar --dias 180 --timeframe 15 --guardar velas.csv

El CSV debe tener columnas: timestamp, open, high, low, close, volume.
Supuestos (conservadores):
* La señal se evalúa al cierre de una vela y se entra al OPEN de la siguiente.
* Si en una misma vela se tocan SL y TP, se asume que primero se tocó el SL.
* El SL se ejecuta al precio del SL (o al open si la vela abre por debajo: gap).
"""
import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import List

import pandas as pd

from trading_bot import estrategia
from trading_bot.config import Config
from trading_bot.estrategia import COLUMNAS_OHLCV


@dataclass
class Operacion:
    entrada_ts: pd.Timestamp
    salida_ts: pd.Timestamp
    entrada: float
    salida: float
    cantidad: float
    pnl: float
    razon: str


@dataclass
class Resultado:
    capital_inicial: float
    capital_final: float
    operaciones: List[Operacion] = field(default_factory=list)
    curva: List[float] = field(default_factory=list)

    @property
    def retorno_pct(self) -> float:
        return (self.capital_final / self.capital_inicial - 1) * 100

    @property
    def win_rate(self) -> float:
        return 100 * sum(o.pnl > 0 for o in self.operaciones) / len(self.operaciones) if self.operaciones else 0.0

    @property
    def profit_factor(self) -> float:
        ganancias = sum(o.pnl for o in self.operaciones if o.pnl > 0)
        perdidas = -sum(o.pnl for o in self.operaciones if o.pnl < 0)
        return ganancias / perdidas if perdidas > 0 else float("inf") if ganancias > 0 else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        pico, peor = float("-inf"), 0.0
        for v in self.curva:
            pico = max(pico, v)
            peor = min(peor, (v / pico - 1) * 100)
        return peor

    def resumen(self) -> str:
        return "\n".join([
            f"Operaciones:     {len(self.operaciones)}",
            f"Win rate:        {self.win_rate:.1f}%",
            f"Profit factor:   {self.profit_factor:.2f}",
            f"Retorno:         {self.retorno_pct:+.2f}%  ({self.capital_inicial:.2f} → {self.capital_final:.2f})",
            f"Max drawdown:    {self.max_drawdown_pct:.2f}%",
        ])


def backtest(velas: pd.DataFrame, cfg: Config, capital: float = 10000.0, deslizamiento_pct: float = 0.02) -> Resultado:
    df = estrategia.calcular_indicadores(velas, cfg)
    senales = estrategia.serie_senales(df, cfg)
    comision = cfg.comision_pct / 100
    desl = deslizamiento_pct / 100

    res = Resultado(capital, capital)
    efectivo = capital
    pos = None  # dict con cantidad, entrada, sl, tp, ts
    for i in range(len(df)):
        fila = df.iloc[i]
        if pos is not None:
            salida, razon = None, ""
            if fila["open"] <= pos["sl"]:
                salida, razon = fila["open"] * (1 - desl), "Stop Loss (gap)"
            elif fila["low"] <= pos["sl"]:
                salida, razon = pos["sl"] * (1 - desl), "Stop Loss"
            elif fila["high"] >= pos["tp"]:
                salida, razon = pos["tp"], "Take Profit"
            if salida is not None:
                bruto = salida * pos["cantidad"]
                efectivo += bruto * (1 - comision)
                pnl = bruto * (1 - comision) - pos["coste"]
                res.operaciones.append(Operacion(pos["ts"], fila["timestamp"], pos["entrada"], salida,
                                                 pos["cantidad"], pnl, razon))
                pos = None

        valor = efectivo + (pos["cantidad"] * fila["close"] if pos else 0.0)
        res.curva.append(valor)

        if pos is None and senales.iloc[i] and i + 1 < len(df):
            entrada = df.iloc[i + 1]["open"] * (1 + desl)
            atr = fila["ATR"]
            riesgo = efectivo * cfg.risk_per_trade_pct / 100
            cantidad = riesgo / (atr * cfg.atr_multiplier_sl)
            cantidad = min(cantidad, efectivo * cfg.max_capital_use_pct / 100 / entrada)
            coste = cantidad * entrada
            if coste < cfg.min_order_quote:
                continue
            efectivo -= coste
            pos = {"cantidad": cantidad * (1 - comision), "entrada": entrada, "coste": coste,
                   "sl": entrada - atr * cfg.atr_multiplier_sl, "tp": entrada + atr * cfg.atr_multiplier_tp,
                   "ts": df.iloc[i + 1]["timestamp"]}

    if pos is not None:  # se cierra al último precio
        ultimo = df.iloc[-1]
        bruto = ultimo["close"] * pos["cantidad"]
        efectivo += bruto * (1 - comision)
        res.operaciones.append(Operacion(pos["ts"], ultimo["timestamp"], pos["entrada"], ultimo["close"],
                                         pos["cantidad"], bruto * (1 - comision) - pos["coste"], "Fin de datos"))
    res.capital_final = efectivo
    return res


def descargar_bybit(symbol: str, timeframe_min: int, dias: int) -> pd.DataFrame:
    from pybit.unified_trading import HTTP

    from trading_bot.exchanges.bybit import INTERVALOS
    http = HTTP(testnet=False)
    fin = int(time.time() * 1000)
    inicio = fin - dias * 86_400_000
    filas = {}
    while fin > inicio:
        lista = http.get_kline(category="spot", symbol=symbol, interval=INTERVALOS[timeframe_min],
                               end=fin, limit=1000)["result"]["list"]
        if not lista:
            break
        for v in lista:
            filas[int(v[0])] = v[:6]
        fin = int(lista[-1][0]) - 1
    df = pd.DataFrame(sorted(filas.values(), key=lambda v: int(v[0])), columns=COLUMNAS_OHLCV)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms")
    df[COLUMNAS_OHLCV[1:]] = df[COLUMNAS_OHLCV[1:]].astype(float)
    return df[df["timestamp"] >= pd.to_datetime(inicio, unit="ms")].reset_index(drop=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", help="archivo CSV con velas")
    p.add_argument("--descargar", action="store_true", help="descargar velas públicas de Bybit")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", type=int, default=15, help="minutos por vela")
    p.add_argument("--dias", type=int, default=90)
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--comision", type=float, default=0.1, help="comisión por lado en %%")
    p.add_argument("--guardar", help="guardar las velas descargadas en este CSV")
    p.add_argument("--operaciones", help="guardar el detalle de operaciones en este CSV")
    a = p.parse_args()

    if a.csv:
        velas = pd.read_csv(a.csv, parse_dates=["timestamp"])
    elif a.descargar:
        velas = descargar_bybit(a.symbol, a.timeframe, a.dias)
        if a.guardar:
            velas.to_csv(a.guardar, index=False)
    else:
        p.error("indica --csv o --descargar")
        return 2

    cfg = Config(symbol=a.symbol, timeframe_min=a.timeframe, comision_pct=a.comision)
    res = backtest(velas, cfg, capital=a.capital)
    print(f"Velas: {len(velas)} ({velas['timestamp'].iloc[0]} → {velas['timestamp'].iloc[-1]})")
    print(res.resumen())
    if a.operaciones:
        pd.DataFrame([o.__dict__ for o in res.operaciones]).to_csv(a.operaciones, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
