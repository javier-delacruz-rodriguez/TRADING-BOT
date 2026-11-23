from decimal import Decimal

from trading_bot.exchanges.base import Filtros
from trading_bot.precision import a_texto, redondear_abajo, redondear_arriba


def test_redondeo_float_no_pierde_un_paso():
    # Con float: math.floor(0.29 / 0.01) * 0.01 == 0.28
    assert redondear_abajo(0.29, "0.01") == Decimal("0.29")
    assert redondear_abajo("0.123456789", "0.000001") == Decimal("0.123456")


def test_redondeo_arriba_y_texto_plano():
    assert redondear_arriba("100.001", "0.01") == Decimal("100.01")
    assert a_texto(redondear_abajo("0.00001", "0.000001")) == "0.000010"
    assert "E" not in a_texto(Decimal("1E-7"))


def test_filtros_precio_segun_lado():
    f = Filtros(Decimal("0.0001"), Decimal("0.5"))
    assert f.precio("100.3", "Buy") == Decimal("100.0")
    assert f.precio("100.3", "Sell") == Decimal("100.5")


def test_filtros_validez_minimos():
    f = Filtros(Decimal("0.0001"), Decimal("0.01"), Decimal("0.001"), Decimal("5"))
    assert not f.es_valida("0.0009", "10000")   # bajo la cantidad mínima
    assert not f.es_valida("0.001", "1000")     # 1 USDT < nocional mínimo
    assert f.es_valida("0.001", "10000")
