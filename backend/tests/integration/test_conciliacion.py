"""Conciliación bancaria contra la base real.

El motor de matching se prueba puro en `tests/unit/test_conciliacion.py`. Aquí importa
lo otro: que resincronizar no duplique, que lo aceptado quede registrado con su
respaldo, y —sobre todo— que **nada se pierda**: un pago que no cuadra sigue existiendo,
en la bandeja.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.conciliacion import aplicar_coincidencia, sincronizar_y_conciliar
from dvu.db.models import Cliente, MovimientoBanco, Pago, Usuario
from dvu.domain.conciliacion import Movimiento
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"
RUT = "76123456-0"
OTRO_RUT = "77888999-6"


class BancoDePrueba:
    """Agregador de mentira: devuelve los movimientos que le pasa el test."""

    nombre = "prueba"

    def __init__(self, movimientos: list[Movimiento]) -> None:
        self._movimientos = movimientos

    def movimientos(self, desde: date, hasta: date) -> list[Movimiento]:
        return [m for m in self._movimientos if desde <= m.fecha <= hasta]


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    sesion.add_all([vendedor, admin])
    sesion.flush()

    ferreteria = Cliente(rut=RUT, razon_social="FERRETERIA TEST SPA", vendedor_id=vendedor.id)
    otra = Cliente(rut=OTRO_RUT, razon_social="FERRETERIA DOS LTDA", vendedor_id=vendedor.id)
    sesion.add_all([ferreteria, otra])
    sesion.flush()

    return {
        "vendedor": vendedor,
        "admin": admin,
        "cliente": ferreteria,
        "otro_cliente": otra,
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
    }


def _pago(
    sesion: Session,
    cliente: Cliente,
    *,
    monto: int = 51122,
    fecha: date = date(2026, 8, 1),
    referencia: str | None = "99887766",
    estado: str = "declarado",
) -> Pago:
    pago = Pago(
        cliente_id=cliente.id,
        monto_clp=Decimal(monto),
        fecha_pago=fecha,
        metodo="transferencia",
        referencia=referencia,
        estado=estado,
    )
    sesion.add(pago)
    sesion.flush()
    return pago


def _movimiento(
    id_externo: str = "mov-1",
    *,
    monto: int = 51122,
    fecha: date = date(2026, 8, 1),
    referencia: str | None = "99887766",
    rut: str | None = RUT,
    descripcion: str = "TEF ABONO",
) -> Movimiento:
    return Movimiento(
        id_externo=id_externo,
        fecha=fecha,
        monto_clp=monto,
        descripcion=descripcion,
        referencia=referencia,
        rut_contraparte=rut,
    )


def test_un_pago_respaldado_por_la_cartola_queda_verificado(
    sesion: Session, datos: dict[str, Any]
) -> None:
    pago = _pago(sesion, datos["cliente"])
    banco = BancoDePrueba([_movimiento()])

    resumen = sincronizar_y_conciliar(
        sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco
    )

    assert resumen.conciliados == 1
    sesion.refresh(pago)
    assert pago.estado == "verificado"
    assert pago.movimiento_banco_id is not None
    # Queda registrado con qué confianza lo aceptó la máquina: sin eso no se puede
    # auditar después si el umbral estaba bien puesto.
    assert pago.conciliacion_confianza is not None
    assert pago.conciliacion_confianza >= Decimal("0.85")


def test_resincronizar_el_mismo_rango_no_duplica_movimientos(
    sesion: Session, datos: dict[str, Any]
) -> None:
    _pago(sesion, datos["cliente"])
    banco = BancoDePrueba([_movimiento()])
    rango = {"desde": date(2026, 7, 25), "hasta": date(2026, 8, 5)}

    primera = sincronizar_y_conciliar(sesion, banco=banco, **rango)
    segunda = sincronizar_y_conciliar(sesion, banco=banco, **rango)

    assert primera.movimientos_nuevos == 1
    assert segunda.movimientos_nuevos == 0
    assert segunda.movimientos_ya_conocidos == 1
    total = sesion.scalars(select(MovimientoBanco)).all()
    assert len(total) == 1


def test_un_pago_sin_respaldo_no_se_descarta_va_a_la_bandeja(
    sesion: Session, datos: dict[str, Any]
) -> None:
    pago = _pago(sesion, datos["cliente"])
    banco = BancoDePrueba([])

    resumen = sincronizar_y_conciliar(
        sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco
    )

    assert resumen.pagos_sin_respaldo == 1
    sesion.refresh(pago)
    assert pago.estado == "pendiente_revision"
    assert sesion.get(Pago, pago.id) is not None


def test_dos_pagos_iguales_el_mismo_dia_no_se_resuelven_solos(
    sesion: Session, datos: dict[str, Any]
) -> None:
    """Dos ferreterías transfieren lo mismo el mismo día y ninguna anotó operación.

    Elegir sería inventar. Los dos quedan para que decida una persona.
    """
    uno = _pago(sesion, datos["cliente"], referencia=None)
    dos = _pago(sesion, datos["otro_cliente"], referencia=None)
    banco = BancoDePrueba(
        [
            _movimiento("mov-1", referencia=None, rut=None),
            _movimiento("mov-2", referencia=None, rut=None),
        ]
    )

    resumen = sincronizar_y_conciliar(
        sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco
    )

    assert resumen.conciliados == 0
    for pago in (uno, dos):
        sesion.refresh(pago)
        assert pago.estado == "pendiente_revision"
        assert pago.movimiento_banco_id is None


def test_un_pago_ya_verificado_a_mano_no_se_vuelve_a_tocar(
    sesion: Session, datos: dict[str, Any]
) -> None:
    pago = _pago(sesion, datos["cliente"], estado="verificado")
    banco = BancoDePrueba([_movimiento()])

    resumen = sincronizar_y_conciliar(
        sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco
    )

    assert resumen.conciliados == 0
    sesion.refresh(pago)
    assert pago.estado == "verificado"
    assert pago.movimiento_banco_id is None


def test_un_cargo_nunca_respalda_un_pago(sesion: Session, datos: dict[str, Any]) -> None:
    """Un egreso de la cuenta no es plata que entró. Se guarda, pero no concilia."""
    pago = _pago(sesion, datos["cliente"], monto=51122)
    banco = BancoDePrueba([_movimiento("mov-cargo", monto=-51122)])

    resumen = sincronizar_y_conciliar(
        sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco
    )

    assert resumen.conciliados == 0
    assert resumen.movimientos_nuevos == 1
    sesion.refresh(pago)
    assert pago.estado == "pendiente_revision"


def test_aplicar_a_mano_deja_el_pago_verificado_sin_confianza(
    sesion: Session, datos: dict[str, Any]
) -> None:
    """La decisión humana no lleva puntaje: no la tomó un umbral."""
    pago = _pago(sesion, datos["cliente"], referencia=None)
    movimiento = MovimientoBanco(
        id_externo="mov-manual",
        proveedor="prueba",
        fecha=date(2026, 8, 1),
        monto_clp=Decimal(51122),
        descripcion="TEF SIN GLOSA",
        estado="sin_conciliar",
    )
    sesion.add(movimiento)
    sesion.flush()

    resultado = aplicar_coincidencia(
        sesion, pago_id=pago.id, movimiento_id=movimiento.id, usuario_id=datos["admin"].id
    )

    assert resultado.estado == "verificado"
    assert resultado.conciliacion_confianza is None
    assert resultado.verificado_por == datos["admin"].id
    sesion.refresh(movimiento)
    assert movimiento.estado == "conciliado"


def test_un_movimiento_no_puede_respaldar_dos_pagos(sesion: Session, datos: dict[str, Any]) -> None:
    uno = _pago(sesion, datos["cliente"], referencia=None)
    dos = _pago(sesion, datos["otro_cliente"], referencia=None)
    movimiento = MovimientoBanco(
        id_externo="mov-unico",
        proveedor="prueba",
        fecha=date(2026, 8, 1),
        monto_clp=Decimal(51122),
        estado="sin_conciliar",
    )
    sesion.add(movimiento)
    sesion.flush()

    aplicar_coincidencia(
        sesion, pago_id=uno.id, movimiento_id=movimiento.id, usuario_id=datos["admin"].id
    )

    with pytest.raises(ValueError, match="ya respalda"):
        aplicar_coincidencia(
            sesion, pago_id=dos.id, movimiento_id=movimiento.id, usuario_id=datos["admin"].id
        )


# --- API ---------------------------------------------------------------------


def test_la_cartola_es_solo_del_dueno(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    """Un vendedor declara pagos, pero no ve los movimientos de la cuenta de la empresa."""
    r = cliente_api.get(f"{PREFIJO}/conciliacion/bandeja", headers=datos["auth_vendedor"])
    assert r.status_code == 403

    r = cliente_api.get(f"{PREFIJO}/conciliacion/bandeja", headers=datos["auth_admin"])
    assert r.status_code == 200


def test_la_bandeja_muestra_los_dos_lados_sin_cruzar(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    _pago(sesion, datos["cliente"], estado="pendiente_revision")
    sesion.add(
        MovimientoBanco(
            id_externo="mov-huerfano",
            proveedor="prueba",
            fecha=date(2026, 8, 1),
            monto_clp=Decimal(12345),
            descripcion="ABONO REVERSA COMISION",
            estado="sin_conciliar",
        )
    )
    sesion.flush()

    cuerpo = cliente_api.get(f"{PREFIJO}/conciliacion/bandeja", headers=datos["auth_admin"]).json()

    assert [p["monto_clp"] for p in cuerpo["pagos"]] == [51122]
    assert [m["monto_clp"] for m in cuerpo["movimientos"]] == [12345]


def test_ignorar_un_abono_no_lo_borra(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    movimiento = MovimientoBanco(
        id_externo="mov-ruido",
        proveedor="prueba",
        fecha=date(2026, 8, 1),
        monto_clp=Decimal(12345),
        estado="sin_conciliar",
    )
    sesion.add(movimiento)
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/conciliacion/movimientos/{movimiento.id}/ignorar",
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["estado"] == "ignorado"
    assert sesion.get(MovimientoBanco, movimiento.id) is not None


def test_no_se_ignora_un_movimiento_que_ya_respalda_un_pago(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    pago = _pago(sesion, datos["cliente"], referencia=None)
    movimiento = MovimientoBanco(
        id_externo="mov-usado",
        proveedor="prueba",
        fecha=date(2026, 8, 1),
        monto_clp=Decimal(51122),
        estado="sin_conciliar",
    )
    sesion.add(movimiento)
    sesion.flush()
    aplicar_coincidencia(
        sesion, pago_id=pago.id, movimiento_id=movimiento.id, usuario_id=datos["admin"].id
    )

    r = cliente_api.post(
        f"{PREFIJO}/conciliacion/movimientos/{movimiento.id}/ignorar",
        headers=datos["auth_admin"],
    )

    assert r.status_code == 409


def test_aplicar_desde_la_api(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    pago = _pago(sesion, datos["cliente"], referencia=None)
    movimiento = MovimientoBanco(
        id_externo="mov-api",
        proveedor="prueba",
        fecha=date(2026, 8, 1),
        monto_clp=Decimal(51122),
        estado="sin_conciliar",
    )
    sesion.add(movimiento)
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/conciliacion/aplicar",
        json={"pago_id": pago.id, "movimiento_id": movimiento.id},
        headers=datos["auth_admin"],
    )

    assert r.status_code == 200
    assert r.json()["estado"] == "verificado"


def test_aplicar_un_pago_inexistente_es_404(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/conciliacion/aplicar",
        json={"pago_id": 999999, "movimiento_id": 999999},
        headers=datos["auth_admin"],
    )
    assert r.status_code == 404


def test_uuid_no_se_usa_como_id_externo(sesion: Session, datos: dict[str, Any]) -> None:
    """El `id_externo` viene del banco: es lo que hace idempotente la sincronización."""
    _pago(sesion, datos["cliente"])
    banco = BancoDePrueba([_movimiento("banco-abc-123")])

    sincronizar_y_conciliar(sesion, desde=date(2026, 7, 25), hasta=date(2026, 8, 5), banco=banco)

    movimiento = sesion.scalar(
        select(MovimientoBanco).where(MovimientoBanco.id_externo == "banco-abc-123")
    )
    assert movimiento is not None
    assert movimiento.uuid != uuid_lib.UUID(int=0)
