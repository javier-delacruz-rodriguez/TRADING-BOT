from decimal import Decimal

from tests.test_motor import Reloj
from trading_bot.estado import AlmacenEstado
from trading_bot.exchanges.simulado import ExchangeSimulado
from trading_bot.motor import Bot


def test_dry_run_ciclo_completo(fake, cfg, tmp_path):
    from tests.conftest import velas_con_cruce
    from trading_bot import estrategia
    completas = velas_con_cruce()
    senales = estrategia.serie_senales(estrategia.calcular_indicadores(completas, cfg), cfg)
    i = int(senales[senales].index[0])
    fake.velas = completas.iloc[:i].reset_index(drop=True)

    sim = ExchangeSimulado(fake, capital_quote=1000, comision_pct=0.1)
    r = Reloj()
    bot = Bot(cfg, sim, AlmacenEstado(str(tmp_path / "e.json"), str(tmp_path / "o.csv")), reloj=r, dormir=r.dormir)
    bot.preparar()
    sim.bid, sim.ask = Decimal("99"), Decimal("99.01")
    bot.on_libro(sim.bid, sim.ask)
    bot.on_vela(completas.iloc[i].to_dict())
    bot.paso(espera=0)

    pos = bot.estado.posicion
    assert pos is not None and sim.saldos["BTC"] == pos.cantidad
    assert fake.colocadas == []  # nada llega al exchange real

    sim.bid, sim.ask = pos.take_profit + 1, pos.take_profit + 2
    bot.on_libro(sim.bid, sim.ask)
    bot.paso(espera=0)
    assert bot.estado.posicion is None
    assert sim.saldos["BTC"] < fake.filtros.paso_cantidad  # sólo queda polvo por la comisión
    assert sim.saldos["USDT"] > 1000
