from types import SimpleNamespace

from trading_bot.config import Config
from trading_bot.exchanges import bybit as modulo_bybit

INFO = {"retCode": 0, "result": {"list": [{
    "baseCoin": "BTC", "quoteCoin": "USDT",
    "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.000048", "minOrderAmt": "5"},
    "priceFilter": {"tickSize": "0.01"}}]}}
VACIO = {"retCode": 0, "result": {"list": []}}


def falso_http(respuestas):
    def fabrica(**kwargs):
        clave = "eu" if "tld" in kwargs else "global"
        r = respuestas[clave]
        return SimpleNamespace(get_instruments_info=lambda **_: r, get_kline=lambda **_: VACIO)
    return fabrica


def test_bybit_eu_vacio_usa_endpoint_global(monkeypatch):
    monkeypatch.setattr(modulo_bybit, "HTTP", falso_http({"eu": VACIO, "global": INFO}))
    ex = modulo_bybit.BybitExchange(Config(exchange="bybit", bybit_tld="eu"))
    f = ex.cargar_filtros()
    assert ex.activo_base == "BTC" and str(f.paso_precio) == "0.01"


def test_bybit_simbolo_inexistente_mensaje_claro(monkeypatch):
    monkeypatch.setattr(modulo_bybit, "HTTP", falso_http({"eu": VACIO, "global": VACIO}))
    ex = modulo_bybit.BybitExchange(Config(exchange="bybit", bybit_tld="eu"))
    try:
        ex.cargar_filtros()
        raise AssertionError("debía fallar")
    except modulo_bybit.ErrorExchange as e:
        assert "SYMBOL" in str(e)


def test_bybit_real_no_usa_respaldo_para_el_simbolo(monkeypatch):
    monkeypatch.setattr(modulo_bybit, "HTTP", falso_http({"eu": VACIO, "global": INFO}))
    cfg = Config(exchange="bybit", bybit_tld="eu", dry_run=False, api_key="k", api_secret="s")
    ex = modulo_bybit.BybitExchange(cfg)
    try:
        ex.cargar_filtros()
        raise AssertionError("debía fallar en modo real")
    except modulo_bybit.ErrorExchange as e:
        assert "BTCUSDC" in str(e)
