import numpy as np
import pandas as pd

from backtest import backtest
from tests.conftest import velas_con_cruce


def test_backtest_resultado_coherente(cfg):
    rng = np.random.default_rng(3)
    precios = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, 3000)))
    velas = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=3000, freq="15min"),
                          "open": precios, "high": precios * 1.003, "low": precios * 0.997,
                          "close": precios, "volume": 1.0})
    res = backtest(velas, cfg, capital=1000)
    assert len(res.curva) == len(velas)
    assert res.max_drawdown_pct <= 0
    assert abs(res.capital_final - (1000 + sum(o.pnl for o in res.operaciones))) < 1e-6
    assert "Operaciones" in res.resumen()


def test_backtest_detecta_operacion(cfg):
    res = backtest(velas_con_cruce(), cfg)
    assert len(res.operaciones) >= 1


def test_comparar_variantes(cfg):
    from backtest import VARIANTES, comparar
    tabla = comparar(velas_con_cruce(400), cfg, 1000)
    for nombre in VARIANTES:
        assert nombre in tabla


def test_filtro_di_no_compra_en_tendencia_bajista(cfg):
    from dataclasses import replace

    from trading_bot import estrategia
    df = estrategia.calcular_indicadores(velas_con_cruce(), replace(cfg, filtro_di=True))
    senales = estrategia.serie_senales(df, replace(cfg, filtro_di=True))
    assert (df.loc[senales, "DI_POS"] > df.loc[senales, "DI_NEG"]).all()
