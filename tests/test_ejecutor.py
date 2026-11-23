from decimal import Decimal

from trading_bot.ejecutor import Ejecutor
from trading_bot.exchanges.base import PostOnlyRechazada


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def crear(fake, filtros, cfg, bid="100", ask="100.01"):
    r = Reloj()
    ej = Ejecutor(fake, filtros, lambda: (Decimal(bid), Decimal(ask)), cfg, dormir=r.dormir, reloj=r)
    return ej


def test_llenado_directo(fake, filtros, cfg):
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert res.cantidad == Decimal("0.5") and res.precio_medio == Decimal("100")
    assert len(fake.colocadas) == 1


def test_parcial_no_compra_de_mas(fake, filtros, cfg):
    fake.guion = [("parcial", "0.2"), ("llenar", None)]
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert res.cantidad == Decimal("0.5")
    # segunda orden sólo por lo que faltaba
    assert fake.colocadas[1]["cantidad"] == Decimal("0.3")
    assert fake.max_abiertas == 1


def test_llenado_durante_la_cancelacion_cuenta(fake, filtros, cfg):
    fake.guion = [("llenar_al_cancelar", None)]
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert res.cantidad == Decimal("0.5")
    assert len(fake.colocadas) == 1  # no se compró una segunda vez


def test_nunca_dos_ordenes_abiertas(fake, filtros, cfg):
    fake.guion = [("nada", None)] * 3
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert not res.llenado
    assert len(fake.colocadas) == 3 and fake.max_abiertas == 1 and not fake.abiertas


def test_post_only_rechazada_reintenta(fake, filtros, cfg):
    llamadas = {"n": 0}
    original = fake.colocar_limit

    def colocar(*a, **k):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise PostOnlyRechazada("would match")
        return original(*a, **k)

    fake.colocar_limit = colocar
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert res.cantidad == Decimal("0.5")


def test_error_de_api_devuelve_error(fake, filtros, cfg):
    fake.fallo_colocar = RuntimeError("170140 order value too small")
    res = crear(fake, filtros, cfg).limit_maker("Buy", Decimal("0.5"))
    assert not res.llenado and "170140" in res.error


def test_venta_con_respaldo_a_mercado(fake, filtros, cfg):
    fake.saldos["BTC"] = Decimal("1")
    fake.guion = [("nada", None)] * 3
    res = crear(fake, filtros, cfg).vender(Decimal("0.5"), a_mercado=False)
    assert res.cantidad == Decimal("0.5")
    assert fake.colocadas[-1]["tipo"] == "market"
