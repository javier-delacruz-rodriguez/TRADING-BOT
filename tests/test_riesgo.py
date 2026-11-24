from decimal import Decimal

from trading_bot.riesgo import calcular_cantidad


def test_cantidad_por_riesgo(cfg, filtros):
    # 1% de 10000 = 100 de riesgo; SL a 2*ATR = 1000 → 0.1 BTC (= 5000 USDT, cabe en el 95%)
    q, motivo = calcular_cantidad(Decimal("10000"), Decimal("500"), Decimal("50000"), cfg, filtros)
    assert motivo == "" and q == Decimal("0.100000")


def test_cantidad_limitada_por_capital(cfg, filtros):
    q, _ = calcular_cantidad(Decimal("1000"), Decimal("10"), Decimal("50000"), cfg, filtros)
    assert q * Decimal("50000") <= Decimal("950")


def test_minimo_forzado(cfg, filtros):
    q, _ = calcular_cantidad(Decimal("1000"), Decimal("100000"), Decimal("50000"), cfg, filtros)
    assert q * Decimal("50000") >= filtros.nocional_minimo


def test_saldo_insuficiente(cfg, filtros):
    q, motivo = calcular_cantidad(Decimal("3"), Decimal("500"), Decimal("50000"), cfg, filtros)
    assert q is None and "insuficiente" in motivo
