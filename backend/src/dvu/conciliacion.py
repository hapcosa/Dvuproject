"""Orquestación de la conciliación bancaria.

Junta las tres piezas: el agregador (`dvu.integraciones.banco`) trae la cartola, el
motor puro (`dvu.domain.conciliacion`) decide, y este módulo persiste.

Lo que **sí** hace solo: marcar `verificado` un pago cuya coincidencia supera el
umbral, dejando registrado el movimiento y la confianza con que se aceptó.

Lo que **no** hace nunca:

- Descartar un pago. Lo que no cuadra pasa a `pendiente_revision`, que es la bandeja.
- Elegir entre dos candidatos empatados. Eso es una decisión humana.
- Tocar un pago que una persona ya revisó (`verificado` o `rechazado`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.config import get_settings
from dvu.db.models import Cliente, MovimientoBanco, Pago
from dvu.domain.conciliacion import Coincidencia, Movimiento, PagoDeclarado, conciliar
from dvu.integraciones.banco import Banco, get_banco

#: Estados de pago sobre los que la máquina puede opinar. Un pago ya verificado o
#: rechazado por una persona no se vuelve a tocar.
ESTADOS_CONCILIABLES = ("declarado", "pendiente_revision")


@dataclass
class ResumenConciliacion:
    movimientos_nuevos: int = 0
    movimientos_ya_conocidos: int = 0
    conciliados: int = 0
    para_revisar: int = 0
    pagos_sin_respaldo: int = 0
    sugerencias: list[Coincidencia] = field(default_factory=list)

    def resumen(self) -> str:
        return (
            f"Cartola: {self.movimientos_nuevos} movimientos nuevos "
            f"({self.movimientos_ya_conocidos} ya conocidos)\n"
            f"Conciliados sin intervención: {self.conciliados}\n"
            f"A la bandeja de revisión: {self.para_revisar}\n"
            f"Pagos sin respaldo en la cartola: {self.pagos_sin_respaldo}"
        )


def sincronizar_y_conciliar(
    session: Session,
    *,
    desde: date | None = None,
    hasta: date | None = None,
    banco: Banco | None = None,
) -> ResumenConciliacion:
    """Trae la cartola del rango y concilia contra los pagos declarados."""
    cfg = get_settings()
    hasta = hasta or datetime.now(UTC).date()
    desde = desde or hasta - timedelta(days=cfg.conciliacion_dias_atras)

    proveedor = banco or get_banco()
    resumen = ResumenConciliacion()

    _guardar_movimientos(session, proveedor.movimientos(desde, hasta), proveedor.nombre, resumen)
    session.flush()

    movimientos = _movimientos_sin_conciliar(session)
    pagos = _pagos_conciliables(session)
    resultado = conciliar(
        [_a_dominio_movimiento(m) for m in movimientos.values()],
        [_a_dominio_pago(p, rut) for p, rut in pagos.values()],
    )

    for coincidencia in resultado.automaticas:
        _aplicar(session, coincidencia, movimientos, pagos)
        resumen.conciliados += 1

    # Todo lo demás queda visible en la bandeja. Un pago sin match no desaparece: pasa
    # a `pendiente_revision` para que alguien lo mire contra la cartola.
    for coincidencia in resultado.sugerencias:
        _marcar_para_revision(pagos, coincidencia.pago_id)
        resumen.para_revisar += 1
    resumen.sugerencias = list(resultado.sugerencias)

    for pago_id in resultado.pagos_sin_match:
        _marcar_para_revision(pagos, pago_id)
        resumen.pagos_sin_respaldo += 1

    session.flush()
    return resumen


def aplicar_coincidencia(
    session: Session, *, pago_id: int, movimiento_id: int, usuario_id: int | None
) -> Pago:
    """Confirma a mano una sugerencia de la bandeja.

    Es la decisión humana que el motor no toma solo. Queda sin `conciliacion_confianza`
    a propósito: no la decidió un puntaje, la decidió una persona.
    """
    pago = session.get(Pago, pago_id)
    movimiento = session.get(MovimientoBanco, movimiento_id)
    if pago is None:
        raise LookupError(f"No existe el pago {pago_id}")
    if movimiento is None:
        raise LookupError(f"No existe el movimiento {movimiento_id}")
    if movimiento.estado == "conciliado":
        raise ValueError("El movimiento ya respalda otro pago")

    pago.movimiento_banco_id = movimiento.id
    pago.estado = "verificado"
    pago.verificado_por = usuario_id
    movimiento.estado = "conciliado"
    session.flush()
    return pago


# --- persistencia ------------------------------------------------------------


def _guardar_movimientos(
    session: Session,
    movimientos: list[Movimiento],
    proveedor: str,
    resumen: ResumenConciliacion,
) -> None:
    """Idempotente por `id_externo`: resincronizar el mismo rango no duplica nada."""
    if not movimientos:
        return

    conocidos = set(
        session.scalars(
            select(MovimientoBanco.id_externo).where(
                MovimientoBanco.id_externo.in_([m.id_externo for m in movimientos])
            )
        )
    )

    for movimiento in movimientos:
        if movimiento.id_externo in conocidos:
            resumen.movimientos_ya_conocidos += 1
            continue
        session.add(
            MovimientoBanco(
                id_externo=movimiento.id_externo,
                proveedor=proveedor,
                fecha=movimiento.fecha,
                monto_clp=Decimal(movimiento.monto_clp),
                descripcion=movimiento.descripcion,
                referencia=movimiento.referencia,
                rut_contraparte=movimiento.rut_contraparte,
                estado="sin_conciliar",
            )
        )
        resumen.movimientos_nuevos += 1


def _movimientos_sin_conciliar(session: Session) -> dict[str, MovimientoBanco]:
    filas = session.scalars(
        select(MovimientoBanco).where(MovimientoBanco.estado == "sin_conciliar")
    ).all()
    # Un cargo no respalda un pago recibido: sólo se concilian los abonos.
    return {m.id_externo: m for m in filas if m.monto_clp > 0}


def _pagos_conciliables(session: Session) -> dict[int, tuple[Pago, str]]:
    filas = session.execute(
        select(Pago, Cliente.rut)
        .join(Cliente, Pago.cliente_id == Cliente.id)
        .where(
            Pago.estado.in_(ESTADOS_CONCILIABLES),
            Pago.movimiento_banco_id.is_(None),
        )
    ).all()
    return {pago.id: (pago, rut) for pago, rut in filas}


def _aplicar(
    session: Session,
    coincidencia: Coincidencia,
    movimientos: dict[str, MovimientoBanco],
    pagos: dict[int, tuple[Pago, str]],
) -> None:
    movimiento = movimientos[coincidencia.movimiento_id_externo]
    pago, _ = pagos[coincidencia.pago_id]

    pago.movimiento_banco_id = movimiento.id
    pago.estado = "verificado"
    pago.conciliacion_confianza = Decimal(str(coincidencia.confianza))
    movimiento.estado = "conciliado"


def _marcar_para_revision(pagos: dict[int, tuple[Pago, str]], pago_id: int) -> None:
    pago, _ = pagos[pago_id]
    if pago.estado == "declarado":
        pago.estado = "pendiente_revision"


# --- traducción al dominio ---------------------------------------------------


def _a_dominio_movimiento(fila: MovimientoBanco) -> Movimiento:
    return Movimiento(
        id_externo=fila.id_externo,
        fecha=fila.fecha,
        monto_clp=int(fila.monto_clp),
        descripcion=fila.descripcion or "",
        referencia=fila.referencia,
        rut_contraparte=fila.rut_contraparte,
    )


def _a_dominio_pago(fila: Pago, cliente_rut: str) -> PagoDeclarado:
    return PagoDeclarado(
        id=fila.id,
        cliente_rut=cliente_rut,
        monto_clp=int(fila.monto_clp),
        fecha_pago=fila.fecha_pago,
        referencia=fila.referencia,
    )
