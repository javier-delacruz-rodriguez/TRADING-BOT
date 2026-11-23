"""Persistencia del estado del bot (posición abierta, P&L diario) en JSON."""
import csv
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .precision import D

logger = logging.getLogger(__name__)


@dataclass
class Posicion:
    cantidad: Decimal
    precio_entrada: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    abierta_en: str
    stop_order_id: Optional[str] = None

    def a_dict(self) -> dict:
        d = asdict(self)
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}

    @classmethod
    def desde_dict(cls, d: dict) -> "Posicion":
        return cls(
            cantidad=D(d["cantidad"]),
            precio_entrada=D(d["precio_entrada"]),
            stop_loss=D(d["stop_loss"]),
            take_profit=D(d["take_profit"]),
            abierta_en=d.get("abierta_en", ""),
            stop_order_id=d.get("stop_order_id"),
        )


@dataclass
class EstadoBot:
    posicion: Optional[Posicion] = None
    dia: str = ""
    pnl_dia: Decimal = Decimal("0")
    capital_ref_dia: Decimal = Decimal("0")
    bloqueado_hasta: float = 0.0
    operaciones_totales: int = 0
    pnl_total: Decimal = Decimal("0")

    def a_dict(self) -> dict:
        return {
            "posicion": self.posicion.a_dict() if self.posicion else None,
            "dia": self.dia,
            "pnl_dia": str(self.pnl_dia),
            "capital_ref_dia": str(self.capital_ref_dia),
            "bloqueado_hasta": self.bloqueado_hasta,
            "operaciones_totales": self.operaciones_totales,
            "pnl_total": str(self.pnl_total),
        }

    @classmethod
    def desde_dict(cls, d: dict) -> "EstadoBot":
        pos = d.get("posicion")
        return cls(
            posicion=Posicion.desde_dict(pos) if pos else None,
            dia=d.get("dia", ""),
            pnl_dia=D(d.get("pnl_dia", "0")),
            capital_ref_dia=D(d.get("capital_ref_dia", "0")),
            bloqueado_hasta=float(d.get("bloqueado_hasta", 0.0)),
            operaciones_totales=int(d.get("operaciones_totales", 0)),
            pnl_total=D(d.get("pnl_total", "0")),
        )


class AlmacenEstado:
    def __init__(self, ruta_estado: str, ruta_operaciones: str = ""):
        self.ruta_estado = ruta_estado
        self.ruta_operaciones = ruta_operaciones

    def cargar(self) -> EstadoBot:
        if not os.path.exists(self.ruta_estado):
            return EstadoBot()
        try:
            with open(self.ruta_estado, encoding="utf-8") as f:
                return EstadoBot.desde_dict(json.load(f))
        except (OSError, ValueError, KeyError) as e:
            # Nunca se sobrescribe un estado ilegible: se conserva para revisarlo a mano
            copia = self.ruta_estado + ".corrupto"
            os.replace(self.ruta_estado, copia)
            logger.error("Estado ilegible (%s). Copiado a %s; se arranca sin posición.", e, copia)
            return EstadoBot()

    def guardar(self, estado: EstadoBot) -> None:
        """Escritura atómica: un corte a mitad no deja el archivo a medias."""
        directorio = os.path.dirname(os.path.abspath(self.ruta_estado))
        fd, tmp = tempfile.mkstemp(dir=directorio, prefix=".estado_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(estado.a_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.ruta_estado)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def registrar_operacion(self, **datos) -> None:
        if not self.ruta_operaciones:
            return
        nuevo = not os.path.exists(self.ruta_operaciones)
        datos = {"fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), **datos}
        with open(self.ruta_operaciones, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(datos.keys()))
            if nuevo:
                w.writeheader()
            w.writerow({k: str(v) for k, v in datos.items()})
