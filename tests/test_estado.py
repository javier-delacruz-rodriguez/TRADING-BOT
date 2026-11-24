from decimal import Decimal

from trading_bot.estado import AlmacenEstado, EstadoBot, Posicion


def test_guardar_y_cargar(tmp_path):
    ruta = tmp_path / "estado.json"
    alm = AlmacenEstado(str(ruta), str(tmp_path / "ops.csv"))
    est = EstadoBot(posicion=Posicion(Decimal("0.001234"), Decimal("50000.5"), Decimal("49000"),
                                      Decimal("52000"), "2026-01-01T00:00:00", "stop-1"),
                    pnl_dia=Decimal("-3.5"))
    alm.guardar(est)
    leido = alm.cargar()
    assert leido.posicion.cantidad == Decimal("0.001234")
    assert leido.posicion.stop_order_id == "stop-1"
    assert leido.pnl_dia == Decimal("-3.5")
    assert not list(tmp_path.glob(".estado_*"))  # sin temporales


def test_estado_corrupto_se_conserva(tmp_path):
    ruta = tmp_path / "estado.json"
    ruta.write_text("{no es json")
    est = AlmacenEstado(str(ruta)).cargar()
    assert est.posicion is None
    assert (tmp_path / "estado.json.corrupto").exists()


def test_registro_operaciones(tmp_path):
    alm = AlmacenEstado(str(tmp_path / "e.json"), str(tmp_path / "ops.csv"))
    alm.registrar_operacion(razon="TP", pnl_neto="1.0")
    alm.registrar_operacion(razon="SL", pnl_neto="-0.5")
    lineas = (tmp_path / "ops.csv").read_text().strip().splitlines()
    assert len(lineas) == 3 and lineas[0].startswith("fecha")
