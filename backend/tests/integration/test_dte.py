"""Documentos tributarios electrónicos.

Lo que se cuida aquí es lo que cuesta caro deshacer: no facturar dos veces el mismo
pedido, no despachar sin guía, y que anular sea una nota de crédito y no un DELETE.
"""

from __future__ import annotations

import uuid as uuid_lib
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Dte, Pedido, PedidoLinea, Producto, Usuario
from dvu.domain.dte import NoFacturable, TipoDte
from dvu.facturacion import emitir_factura, emitir_guia, emitir_nota_credito, tiene_guia
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"
RUT = "76123456-0"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    bodega = Usuario(email="b@test.cl", nombre="B", rol="bodega", password_hash=hashear("x"))
    sesion.add_all([vendedor, admin, bodega])
    sesion.flush()

    cliente = Cliente(
        rut=RUT,
        razon_social="FERRETERIA TEST SPA",
        giro="VENTA AL POR MENOR DE ARTICULOS DE FERRETERIA",
        direccion="AV SIEMPRE VIVA 742",
        comuna="MAIPU",
        vendedor_id=vendedor.id,
    )
    producto = Producto(
        sku="DVU-PR49573",
        descripcion="LIQUIDO DE FRENO FEDERAL",
        multiplo_venta=12,
        precio_lista_clp=Decimal("1790"),
    )
    sesion.add_all([cliente, producto])
    sesion.flush()

    pedido = Pedido(
        client_uuid=uuid_lib.uuid4(),
        numero="P-2026-000001",
        cliente_id=cliente.id,
        vendedor_id=vendedor.id,
        origen="app_vendedor",
        estado="confirmado",
        neto_clp=Decimal("42960"),
        iva_clp=Decimal("8162"),
        total_clp=Decimal("51122"),
        lineas=[
            PedidoLinea(
                producto_id=producto.id,
                sku=producto.sku,
                descripcion=producto.descripcion,
                multiplo_venta=12,
                cantidad=24,
                precio_unitario_clp=Decimal("1790"),
                total_linea_clp=Decimal("42960"),
            )
        ],
    )
    sesion.add(pedido)
    sesion.flush()

    return {
        "cliente": cliente,
        "pedido": pedido,
        "admin": admin,
        "auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"},
        "auth_vendedor": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
        "auth_bodega": {"Authorization": f"Bearer {emitir_token(bodega.uuid, 'bodega')}"},
    }


def test_la_factura_es_tipo_33_con_el_total_del_pedido(
    sesion: Session, datos: dict[str, Any]
) -> None:
    """Nunca boleta: los clientes son empresas y descuentan IVA."""
    dte = emitir_factura(sesion, pedido_id=datos["pedido"].id, usuario_id=datos["admin"].id)

    assert dte.tipo == TipoDte.FACTURA_AFECTA
    assert dte.folio is not None
    assert dte.rut_receptor == RUT
    assert (int(dte.neto_clp), int(dte.iva_clp), int(dte.total_clp)) == (42960, 8162, 51122)
    # El proveedor fake no finge que el SII ya aceptó: ese limbo es real.
    assert dte.estado == "emitido"


def test_no_se_factura_dos_veces_el_mismo_pedido(sesion: Session, datos: dict[str, Any]) -> None:
    emitir_factura(sesion, pedido_id=datos["pedido"].id)

    with pytest.raises(NoFacturable, match="nota de crédito"):
        emitir_factura(sesion, pedido_id=datos["pedido"].id)


def test_no_se_factura_un_pedido_recien_enviado(sesion: Session, datos: dict[str, Any]) -> None:
    """Antes de `confirmado` el pedido todavía puede cambiar; facturarlo obligaría a
    una nota de crédito de inmediato."""
    datos["pedido"].estado = "enviado"
    sesion.flush()

    with pytest.raises(NoFacturable, match="enviado"):
        emitir_factura(sesion, pedido_id=datos["pedido"].id)


def test_no_se_factura_un_pedido_anulado(sesion: Session, datos: dict[str, Any]) -> None:
    datos["pedido"].estado = "anulado"
    sesion.flush()

    with pytest.raises(NoFacturable, match="anulado"):
        emitir_factura(sesion, pedido_id=datos["pedido"].id)


def test_la_nota_de_credito_anula_la_factura_sin_borrarla(
    sesion: Session, datos: dict[str, Any]
) -> None:
    factura = emitir_factura(sesion, pedido_id=datos["pedido"].id)
    folio_original = factura.folio

    nota = emitir_nota_credito(
        sesion, pedido_id=datos["pedido"].id, motivo="Precio mal aplicado en la línea 1"
    )

    assert nota.tipo == TipoDte.NOTA_CREDITO
    assert nota.referencia_dte_id == factura.id
    assert int(nota.total_clp) == 51122
    sesion.refresh(factura)
    assert factura.estado == "anulado"
    # La factura sigue en el registro: el SII exige conservar el rastro de las dos.
    viva = sesion.scalar(select(Dte).where(Dte.folio == folio_original, Dte.tipo == 33))
    assert viva is not None


def test_sin_factura_no_hay_nada_que_anular(sesion: Session, datos: dict[str, Any]) -> None:
    with pytest.raises(NoFacturable, match="No hay factura"):
        emitir_nota_credito(sesion, pedido_id=datos["pedido"].id, motivo="cualquiera")


def test_tras_la_nota_de_credito_se_puede_volver_a_facturar(
    sesion: Session, datos: dict[str, Any]
) -> None:
    """Es el flujo de corregir: anular y reemitir. Sin esto, un error de precio dejaría
    el pedido sin factura válida para siempre."""
    emitir_factura(sesion, pedido_id=datos["pedido"].id)
    emitir_nota_credito(sesion, pedido_id=datos["pedido"].id, motivo="Precio mal aplicado")

    nueva = emitir_factura(sesion, pedido_id=datos["pedido"].id)

    assert nueva.estado == "emitido"
    assert nueva.folio is not None


def test_los_folios_no_se_repiten_por_tipo(sesion: Session, datos: dict[str, Any]) -> None:
    """`(tipo, folio)` es único: el contador del proveedor fake sale de la base."""
    factura = emitir_factura(sesion, pedido_id=datos["pedido"].id)
    guia = emitir_guia(sesion, pedido_id=datos["pedido"].id)

    emitir_nota_credito(sesion, pedido_id=datos["pedido"].id, motivo="Corrección")
    otra_factura = emitir_factura(sesion, pedido_id=datos["pedido"].id)

    assert otra_factura.folio != factura.folio
    # Distinto tipo, numeración independiente: el CAF es por tipo de documento.
    assert guia.tipo == TipoDte.GUIA_DESPACHO


def test_no_se_emite_dos_veces_la_guia(sesion: Session, datos: dict[str, Any]) -> None:
    emitir_guia(sesion, pedido_id=datos["pedido"].id)

    with pytest.raises(NoFacturable, match="ya tiene guía"):
        emitir_guia(sesion, pedido_id=datos["pedido"].id)


# --- API ---------------------------------------------------------------------


def test_solo_admin_emite(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    """Emitir es irreversible: un folio entregado al SII no se borra."""
    cuerpo = {"numero_pedido": "P-2026-000001"}

    assert (
        cliente_api.post(
            f"{PREFIJO}/dte/facturas", json=cuerpo, headers=datos["auth_vendedor"]
        ).status_code
        == 403
    )
    assert (
        cliente_api.post(
            f"{PREFIJO}/dte/facturas", json=cuerpo, headers=datos["auth_admin"]
        ).status_code
        == 201
    )


def test_facturar_dos_veces_por_api_es_409(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    cuerpo = {"numero_pedido": "P-2026-000001"}
    cliente_api.post(f"{PREFIJO}/dte/facturas", json=cuerpo, headers=datos["auth_admin"])

    r = cliente_api.post(f"{PREFIJO}/dte/facturas", json=cuerpo, headers=datos["auth_admin"])

    assert r.status_code == 409


def test_la_nota_de_credito_exige_motivo(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    """Sin razón de la anulación el documento queda impugnable ante el SII."""
    r = cliente_api.post(
        f"{PREFIJO}/dte/notas-credito",
        json={"numero_pedido": "P-2026-000001"},
        headers=datos["auth_admin"],
    )
    assert r.status_code == 422


def test_facturar_un_pedido_inexistente_es_404(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/dte/facturas",
        json={"numero_pedido": "P-2026-999999"},
        headers=datos["auth_admin"],
    )
    assert r.status_code == 404


def test_el_vendedor_solo_ve_los_dte_de_sus_pedidos(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    emitir_factura(sesion, pedido_id=datos["pedido"].id)
    datos["pedido"].vendedor_id = None
    sesion.flush()

    del_vendedor = cliente_api.get(f"{PREFIJO}/dte", headers=datos["auth_vendedor"]).json()
    del_admin = cliente_api.get(f"{PREFIJO}/dte", headers=datos["auth_admin"]).json()

    assert del_vendedor == []
    assert len(del_admin) == 1


# --- la regla del despacho ---------------------------------------------------


def test_no_se_despacha_sin_guia(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    """La mercadería no sale sin guía electrónica: es lo que le piden al camión."""
    datos["pedido"].estado = "preparacion"
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/P-2026-000001/estado",
        json={"estado": "despachado"},
        headers=datos["auth_bodega"],
    )

    assert r.status_code == 409
    assert "guía" in r.json()["detail"]
    sesion.refresh(datos["pedido"])
    assert datos["pedido"].estado == "preparacion"


def test_con_guia_emitida_el_pedido_puede_despacharse(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    datos["pedido"].estado = "preparacion"
    sesion.flush()
    emitir_guia(sesion, pedido_id=datos["pedido"].id)
    assert tiene_guia(sesion, datos["pedido"].id)

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/P-2026-000001/estado",
        json={"estado": "despachado"},
        headers=datos["auth_bodega"],
    )

    assert r.status_code == 200
    assert r.json()["estado"] == "despachado"
