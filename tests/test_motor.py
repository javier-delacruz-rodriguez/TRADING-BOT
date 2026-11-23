from decimal import Decimal

import pandas as pd
import pytest

from tests.conftest import velas_con_cruce
from trading_bot.estado import AlmacenEstado, EstadoBot, Posicion
from trading_bot.motor import Bot, ConexionMuerta


class Reloj:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def crear_bot(fake, cfg, tmp_path, estado=None):
    alm = AlmacenEstado(str(tmp_path / "estado.json"), str(tmp_path / "ops.csv"))
    if estado:
        alm.guardar(estado)
    r = Reloj()
    bot = Bot(cfg, fake, alm, reloj=r, dormir=r.dormir)
    return bot, alm, r


def preparar_hasta_senal(fake, cfg, tmp_path):
    """Carga el historial sin la última vela y la entrega por el stream (como en real)."""
    completas = velas_con_cruce()
    from trading_bot import estrategia
    senales = estrategia.serie_senales(estrategia.calcular_indicadores(completas, cfg), cfg)
    i = int(senales[senales].index[0])
    fake.velas = completas.iloc[:i].reset_index(drop=True)
    bot, alm, r = crear_bot(fake, cfg, tmp_path)
    bot.preparar()
    bot.on_libro(Decimal("99"), Decimal("99.01"))
    bot.on_vela(completas.iloc[i].to_dict())
    return bot, alm, r


def test_abre_posicion_con_stop_en_exchange(fake, cfg, tmp_path):
    bot, alm, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    pos = bot.estado.posicion
    assert pos is not None and pos.stop_loss < pos.precio_entrada < pos.take_profit
    assert pos.stop_order_id in fake.stops
    assert alm.cargar().posicion is not None  # persistida


def test_cantidad_neta_de_comision(fake, cfg, tmp_path):
    fake.comision_base = Decimal("0.001")
    bot, _, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    comprado = fake.colocadas[0]["cantidad"]
    assert bot.estado.posicion.cantidad == comprado * Decimal("0.999")


def test_take_profit_cancela_stop_y_vende(fake, cfg, tmp_path):
    bot, _, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    pos = bot.estado.posicion
    stop_id = pos.stop_order_id
    bot.on_libro(pos.take_profit + 1, pos.take_profit + Decimal("1.01"))
    bot.paso(espera=0)
    assert bot.estado.posicion is None
    assert fake.stops[stop_id]["activo"] is False
    assert fake.saldos["BTC"] == 0
    assert bot.estado.pnl_total > 0
    assert (tmp_path / "ops.csv").exists()


def test_stop_loss_vende_a_mercado(fake, cfg, tmp_path):
    bot, _, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    sl = bot.estado.posicion.stop_loss
    bot.on_libro(sl - 1, sl - Decimal("0.99"))
    bot.paso(espera=0)
    assert bot.estado.posicion is None
    assert fake.colocadas[-1]["tipo"] == "market" and fake.colocadas[-1]["lado"] == "Sell"


def test_stop_ejecutado_en_exchange_cierra_sin_vender(fake, cfg, tmp_path):
    bot, _, r = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    pos = bot.estado.posicion
    fake.stops[pos.stop_order_id]["activo"] = False  # el exchange lo disparó
    fake.saldos["BTC"] = Decimal("0")
    n = len(fake.colocadas)
    r.t += 60
    bot.on_libro(pos.precio_entrada, pos.precio_entrada + 1)
    bot.paso(espera=0)
    assert bot.estado.posicion is None and len(fake.colocadas) == n


def test_no_vende_btc_del_usuario(fake, cfg, tmp_path):
    fake.saldos["BTC"] = Decimal("2")  # BTC que el usuario ya tenía
    bot, _, _ = crear_bot(fake, cfg, tmp_path)
    bot.on_libro(Decimal("100"), Decimal("100.01"))
    bot.preparar()
    assert fake.colocadas == [] and fake.saldos["BTC"] == 2


def test_venta_limitada_a_lo_comprado(fake, cfg, tmp_path):
    fake.saldos["BTC"] = Decimal("5")  # el usuario tiene más BTC
    estado = EstadoBot(posicion=Posicion(Decimal("0.1"), Decimal("100"), Decimal("90"), Decimal("110"), ""))
    bot, _, _ = crear_bot(fake, cfg, tmp_path, estado)
    bot.preparar()
    bot.on_libro(Decimal("111"), Decimal("111.01"))
    bot.paso(espera=0)
    assert fake.colocadas[0]["cantidad"] == Decimal("0.1")
    assert fake.saldos["BTC"] == Decimal("4.9")


def test_reinicio_recupera_posicion(fake, cfg, tmp_path):
    fake.saldos["BTC"] = Decimal("0.1")
    estado = EstadoBot(posicion=Posicion(Decimal("0.1"), Decimal("100"), Decimal("90"), Decimal("110"), ""))
    bot, _, _ = crear_bot(fake, cfg, tmp_path, estado)
    bot.preparar()
    assert bot.estado.posicion is not None
    assert bot.estado.posicion.stop_order_id in fake.stops  # se vuelve a proteger
    assert fake.colocadas == []


def test_posicion_vendida_fuera_se_descarta(fake, cfg, tmp_path):
    estado = EstadoBot(posicion=Posicion(Decimal("0.1"), Decimal("100"), Decimal("90"), Decimal("110"), ""))
    bot, _, _ = crear_bot(fake, cfg, tmp_path, estado)
    bot.preparar()
    assert bot.estado.posicion is None


def test_limite_perdida_diaria_bloquea_compras(fake, cfg, tmp_path):
    bot, _, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot._actualizar_dia()
    bot.estado.pnl_dia = -bot.estado.capital_ref_dia * Decimal("0.05")
    bot.paso(espera=0)
    assert bot.estado.posicion is None and fake.colocadas == []


def test_rechazo_activa_pausa(fake, cfg, tmp_path):
    bot, _, r = preparar_hasta_senal(fake, cfg, tmp_path)
    fake.fallo_colocar = RuntimeError("rechazada")
    bot.paso(espera=0)
    assert bot.estado.posicion is None
    assert bot.estado.bloqueado_hasta == r.t + cfg.cooldown_after_reject_sec


def test_vela_duplicada_se_ignora(fake, cfg, tmp_path):
    bot, _, _ = crear_bot(fake, cfg, tmp_path)
    bot.preparar()
    n = len(bot.df)
    bot.procesar_vela(bot.df.iloc[-1][["timestamp", "open", "high", "low", "close", "volume"]].to_dict())
    assert len(bot.df) == n


def test_hueco_recarga_historial(fake, cfg, tmp_path):
    bot, _, _ = crear_bot(fake, cfg, tmp_path)
    fake.velas = velas_con_cruce().iloc[:120]
    bot.preparar()
    fake.velas = velas_con_cruce()
    salto = velas_con_cruce().iloc[130].to_dict()
    bot.procesar_vela(salto)
    assert bot.df.iloc[-1]["timestamp"] >= pd.Timestamp(salto["timestamp"])


def test_watchdog(fake, cfg, tmp_path):
    bot, _, r = crear_bot(fake, cfg, tmp_path)
    bot.preparar()
    r.t += cfg.watchdog_sec + 1
    with pytest.raises(ConexionMuerta):
        bot.paso(espera=0)


def test_stop_no_consultable_y_sin_saldo_se_da_por_ejecutado(fake, cfg, tmp_path):
    bot, _, _ = preparar_hasta_senal(fake, cfg, tmp_path)
    bot.paso(espera=0)
    pos = bot.estado.posicion
    del fake.stops[pos.stop_order_id]  # el exchange lo convirtió en otra orden
    fake.saldos["BTC"] = Decimal("0")
    bot.on_libro(pos.stop_loss - 1, pos.stop_loss - Decimal("0.99"))
    bot.paso(espera=0)
    assert bot.estado.posicion is None
