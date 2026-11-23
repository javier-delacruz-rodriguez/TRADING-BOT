import numpy as np
import pandas as pd

from tests.conftest import velas_con_cruce
from trading_bot import estrategia


def test_indicadores_no_duplican_columnas(cfg):
    df = velas_con_cruce()
    for _ in range(3):  # recalcular repetidamente (bug original de bot_produccion.py)
        df = estrategia.calcular_indicadores(df, cfg)
    assert df.columns.is_unique
    assert np.isscalar(df.iloc[-1]["ADX"])


def test_adx_sin_division_por_cero(cfg):
    plano = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=150, freq="15min"),
                          "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0})
    df = estrategia.calcular_indicadores(plano, cfg)
    assert not np.isinf(df["ADX"]).any()
    assert not estrategia.senal_compra(df, cfg)


def test_senal_en_cruce_alcista_con_tendencia(cfg):
    df = estrategia.calcular_indicadores(velas_con_cruce(), cfg)
    senales = estrategia.serie_senales(df, cfg)
    assert senales.any()
    i = senales[senales].index[0]
    fila, prev = df.iloc[i], df.iloc[i - 1]
    assert prev["SMA_F"] <= prev["SMA_S"] and fila["SMA_F"] > fila["SMA_S"]
    assert fila["ADX"] > cfg.adx_min and fila["MACD_Hist"] > 0


def test_sin_senal_con_pocas_velas(cfg):
    df = estrategia.calcular_indicadores(velas_con_cruce().tail(50), cfg)
    assert not estrategia.senal_compra(df, cfg)
