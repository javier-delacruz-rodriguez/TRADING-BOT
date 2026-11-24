"""Indicadores y señal de entrada (funciones puras, compartidas por bots y backtest)."""
import numpy as np
import pandas as pd

from .config import Config

COLUMNAS_OHLCV = ["timestamp", "open", "high", "low", "close", "volume"]


def calcular_atr(df: pd.DataFrame, periodo: int) -> pd.Series:
    cierre_prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - cierre_prev).abs(), (df["low"] - cierre_prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / periodo, adjust=False).mean()


def calcular_adx_di(df: pd.DataFrame, periodo: int):
    """Devuelve (ADX, +DI, -DI). El ADX mide la FUERZA de la tendencia, no su dirección."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr = calcular_atr(df, periodo).replace(0, np.nan)
    pos_di = 100 * pos_dm.ewm(alpha=1 / periodo, adjust=False).mean() / atr
    neg_di = 100 * neg_dm.ewm(alpha=1 / periodo, adjust=False).mean() / atr
    suma = (pos_di + neg_di).replace(0, np.nan)
    dx = 100 * (pos_di - neg_di).abs() / suma
    return dx.ewm(alpha=1 / periodo, adjust=False).mean(), pos_di, neg_di


def calcular_adx(df: pd.DataFrame, periodo: int) -> pd.Series:
    return calcular_adx_di(df, periodo)[0]


def calcular_macd_hist(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = df["close"].ewm(span=fast, adjust=False).mean() - df["close"].ewm(span=slow, adjust=False).mean()
    return macd - macd.ewm(span=signal, adjust=False).mean()


def calcular_indicadores(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Devuelve una copia con las columnas de indicadores (sobrescritas, nunca duplicadas)."""
    out = df[COLUMNAS_OHLCV].copy().reset_index(drop=True)
    out["SMA_F"] = out["close"].rolling(cfg.sma_fast).mean()
    out["SMA_S"] = out["close"].rolling(cfg.sma_slow).mean()
    out["ATR"] = calcular_atr(out, cfg.atr_period)
    out["ADX"], out["DI_POS"], out["DI_NEG"] = calcular_adx_di(out, cfg.adx_period)
    out["EMA_T"] = out["close"].ewm(span=cfg.ema_tendencia, adjust=False).mean()
    out["MACD_Hist"] = calcular_macd_hist(out)
    return out


def serie_senales(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Señal de compra por vela (True en la vela cerrada donde se confirma)."""
    cruce = (df["SMA_F"].shift(1) <= df["SMA_S"].shift(1)) & (df["SMA_F"] > df["SMA_S"])
    senal = cruce & (df["ADX"] > cfg.adx_min) & (df["MACD_Hist"] > 0) & (df["ATR"] > 0)
    if cfg.filtro_di:
        # Sin esto, un ADX alto en plena caída también daba señal de compra.
        senal &= df["DI_POS"] > df["DI_NEG"]
    if cfg.filtro_ema_tendencia:
        senal &= df["close"] > df["EMA_T"]
    # Las primeras velas no tienen indicadores fiables (calentamiento de las EMA)
    senal.iloc[: cfg.velas_minimas - 1] = False
    return senal.fillna(False).astype(bool)


def senal_compra(df: pd.DataFrame, cfg: Config) -> bool:
    if len(df) < cfg.velas_minimas:
        return False
    return bool(serie_senales(df, cfg).iloc[-1])
