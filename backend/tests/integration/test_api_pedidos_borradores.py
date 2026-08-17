"""Las listas que el vendedor arma en terreno, y la cotización que le muestra el total.

Lo que cuidan estos tests es el límite entre «lista» y «pedido»: mientras es lista se
puede guardar cualquier cosa y no existe para el resto del sistema; al enviarla recién
ahí hay folio, precios congelados y estrictez de múltiplos.
"""

from __future__ import annotations

import uuid as uuid_lib
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from dvu.db.models import Cliente, Pedido, Producto, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"
RUT = "76123456-0"
SKU = "DVU-PR49573"


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    vendedor = Usuario(email="v@test.cl", nombre="V", rol="vendedor", password_hash=hashear("x"))
    bodega = Usuario(email="b@test.cl", nombre="B", rol="bodega", password_hash=hashear("x"))
    sesion.add_all([vendedor, bodega])
    sesion.flush()

    cliente = Cliente(rut=RUT, razon_social="FERRETERIA TEST SPA", vendedor_id=vendedor.id)
    # El líquido de freno del catálogo real: se vende de a 12.
    producto = Producto(
        sku=SKU,
        descripcion="LIQUIDO DE FRENO FEDERAL",
        unidad_venta="UNID",
        multiplo_venta=12,
        precio_lista_clp=Decimal("1790"),
    )
    sesion.add_all([cliente, producto])
    sesion.flush()

    return {
        "vendedor": vendedor,
        "producto": producto,
        "auth": {"Authorization": f"Bearer {emitir_token(vendedor.uuid, 'vendedor')}"},
        "auth_bodega": {"Authorization": f"Bearer {emitir_token(bodega.uuid, 'bodega')}"},
    }


def _nueva(cantidad: int = 24) -> dict[str, Any]:
    return {
        "client_uuid": str(uuid_lib.uuid4()),
        "cliente_rut": RUT,
        "lineas": [{"sku": SKU, "cantidad": cantidad}],
    }


def _crear(cliente_api: TestClient, datos: dict[str, Any], cantidad: int = 24) -> dict[str, Any]:
    cuerpo = _nueva(cantidad)
    r = cliente_api.post(f"{PREFIJO}/pedidos/borradores", json=cuerpo, headers=datos["auth"])
    assert r.status_code == 201, r.text
    return dict(r.json())


# --- cotizar -----------------------------------------------------------------


def test_cotizar_responde_el_total_con_iva_sin_crear_nada(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/pedidos/cotizar",
        json={"lineas": [{"sku": SKU, "cantidad": 24}]},
        headers=datos["auth"],
    )

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["neto_clp"] == 24 * 1790
    assert cuerpo["iva_clp"] == round(24 * 1790 * 0.19)
    assert cuerpo["total_clp"] == cuerpo["neto_clp"] + cuerpo["iva_clp"]
    # En envases, que es como el vendedor lo pide y como lo lee el ferretero.
    assert cuerpo["lineas"][0]["envases"] == 2
    assert cuerpo["con_problema"] == 0
    # Cotizar no deja rastro: es una pantalla, no un compromiso.
    assert sesion.scalars(select(Pedido)).all() == []


def test_cotizar_marca_la_cantidad_que_no_calza_y_no_la_suma(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Repetir un pedido viejo cuando el envase cambió de tamaño: se ve, no explota."""
    r = cliente_api.post(
        f"{PREFIJO}/pedidos/cotizar",
        json={"lineas": [{"sku": SKU, "cantidad": 7}, {"sku": "DVU-NO-EXISTE", "cantidad": 1}]},
        headers=datos["auth"],
    )

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["con_problema"] == 2
    assert cuerpo["neto_clp"] == 0
    mala, inexistente = cuerpo["lineas"]
    assert "12" in mala["problema"]
    assert mala["cantidad_sugerida"] == 12
    assert mala["total_linea_clp"] == 0
    assert inexistente["problema"] == "Ya no está en el catálogo"


def test_cotizar_exige_sesion(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    r = cliente_api.post(
        f"{PREFIJO}/pedidos/cotizar", json={"lineas": [{"sku": SKU, "cantidad": 12}]}
    )
    assert r.status_code == 401


# --- ciclo de vida de la lista -----------------------------------------------


def test_la_lista_nace_sin_folio_y_no_es_un_pedido(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos)

    assert borrador["estado"] == "borrador"
    assert borrador["numero"] is None
    assert borrador["estado_etiqueta"] == "Lista sin enviar"
    assert borrador["cliente_razon_social"] == "FERRETERIA TEST SPA"
    # Y no ensucia «mis últimos pedidos», que es lo que ya se envió.
    assert cliente_api.get(f"{PREFIJO}/pedidos", headers=datos["auth"]).json()["total"] == 0
    # Pedida explícitamente sí aparece.
    con_filtro = cliente_api.get(f"{PREFIJO}/pedidos?estado=borrador", headers=datos["auth"])
    assert con_filtro.json()["total"] == 1


def test_crear_dos_veces_el_mismo_client_uuid_no_duplica(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    cuerpo = _nueva()
    primera = cliente_api.post(f"{PREFIJO}/pedidos/borradores", json=cuerpo, headers=datos["auth"])
    segunda = cliente_api.post(f"{PREFIJO}/pedidos/borradores", json=cuerpo, headers=datos["auth"])

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert primera.json()["uuid"] == segunda.json()["uuid"]
    assert len(cliente_api.get(f"{PREFIJO}/pedidos/borradores", headers=datos["auth"]).json()) == 1


def test_guardar_reescribe_la_lista_entera(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    borrador = _crear(cliente_api, datos)

    r = cliente_api.put(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}",
        json={
            "cliente_rut": RUT,
            "lineas": [{"sku": SKU, "cantidad": 36}],
            "observaciones": "Entregar después de las 15:00",
        },
        headers=datos["auth"],
    )

    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo["lineas"]) == 1
    assert cuerpo["lineas"][0]["cantidad"] == 36
    assert cuerpo["neto_clp"] == 36 * 1790
    assert cuerpo["observaciones"] == "Entregar después de las 15:00"


def test_la_lista_acepta_una_cantidad_que_no_calza(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Guardar es permisivo: arreglar el múltiplo con el cliente esperando puede esperar,
    perder la lista no."""
    borrador = _crear(cliente_api, datos)

    r = cliente_api.put(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}",
        json={"cliente_rut": RUT, "lineas": [{"sku": SKU, "cantidad": 7}]},
        headers=datos["auth"],
    )

    assert r.status_code == 200
    # La línea queda guardada, pero no suma: no es vendible así.
    assert r.json()["lineas"][0]["cantidad"] == 7
    assert r.json()["neto_clp"] == 0


def test_guardar_un_sku_que_no_existe_da_422(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos)

    r = cliente_api.put(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}",
        json={"cliente_rut": RUT, "lineas": [{"sku": "DVU-NO-EXISTE", "cantidad": 1}]},
        headers=datos["auth"],
    )
    assert r.status_code == 422
    assert "DVU-NO-EXISTE" in r.json()["detail"]


# --- enviar ------------------------------------------------------------------


def test_enviar_asigna_folio_y_relee_el_precio_de_hoy(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    """El precio que vale es el del momento del pedido, no el de cuando se abrió la lista."""
    borrador = _crear(cliente_api, datos)

    datos["producto"].precio_lista_clp = Decimal("1990")
    sesion.flush()

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}/enviar", headers=datos["auth"]
    )

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "enviado"
    assert cuerpo["estado_etiqueta"] == "Enviado a DVU"
    assert str(cuerpo["numero"]).startswith("P-")
    assert cuerpo["lineas"][0]["precio_unitario_clp"] == 1990
    assert cuerpo["neto_clp"] == 24 * 1990
    assert cuerpo["sincronizado_en"] is not None
    # Es el mismo pedido de siempre, con la lista adentro de su bitácora.
    assert cuerpo["client_uuid"] == borrador["client_uuid"]
    detalle = cliente_api.get(f"{PREFIJO}/pedidos/{cuerpo['numero']}", headers=datos["auth"])
    assert [e["estado_nuevo"] for e in detalle.json()["eventos"]] == ["borrador", "enviado"]


def test_enviar_una_lista_con_cantidad_invalida_falla_con_el_detalle(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos, cantidad=7)

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}/enviar", headers=datos["auth"]
    )

    assert r.status_code == 422
    assert r.json()["detail"][0]["cantidad_sugerida"] == 12
    # Y la lista sigue viva para arreglarla.
    assert len(cliente_api.get(f"{PREFIJO}/pedidos/borradores", headers=datos["auth"]).json()) == 1


def test_enviar_una_lista_vacia_da_422(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    cuerpo = _nueva()
    cuerpo["lineas"] = []
    client_uuid = cuerpo["client_uuid"]
    cliente_api.post(f"{PREFIJO}/pedidos/borradores", json=cuerpo, headers=datos["auth"])

    r = cliente_api.post(
        f"{PREFIJO}/pedidos/borradores/{client_uuid}/enviar", headers=datos["auth"]
    )
    assert r.status_code == 422


def test_una_lista_ya_enviada_no_se_vuelve_a_tocar(
    cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos)
    ruta = f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}"
    numero = cliente_api.post(f"{ruta}/enviar", headers=datos["auth"]).json()["numero"]

    reenvio = cliente_api.post(f"{ruta}/enviar", headers=datos["auth"])
    guardado = cliente_api.put(ruta, json={"cliente_rut": RUT, "lineas": []}, headers=datos["auth"])

    assert reenvio.status_code == 409
    assert numero in reenvio.json()["detail"]
    assert guardado.status_code == 409


# --- descartar y permisos ----------------------------------------------------


def test_descartar_anula_pero_no_borra(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos)

    r = cliente_api.delete(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}", headers=datos["auth"]
    )

    assert r.status_code == 200
    assert r.json()["estado"] == "anulado"
    assert cliente_api.get(f"{PREFIJO}/pedidos/borradores", headers=datos["auth"]).json() == []
    # La fila sigue en la base con su motivo: nada se borra.
    guardado = sesion.scalar(select(Pedido).where(Pedido.client_uuid == borrador["client_uuid"]))
    assert guardado is not None
    assert guardado.eventos[-1].motivo == "Lista descartada por el vendedor"


def test_un_vendedor_no_ve_ni_toca_las_listas_de_otro(
    cliente_api: TestClient, sesion: Session, datos: dict[str, Any]
) -> None:
    borrador = _crear(cliente_api, datos)

    otro = Usuario(email="o@test.cl", nombre="O", rol="vendedor", password_hash=hashear("x"))
    sesion.add(otro)
    sesion.flush()
    auth_otro = {"Authorization": f"Bearer {emitir_token(otro.uuid, 'vendedor')}"}

    assert cliente_api.get(f"{PREFIJO}/pedidos/borradores", headers=auth_otro).json() == []
    # 404 y no 403: quién más tiene listas abiertas no es asunto de nadie.
    ajena = cliente_api.get(
        f"{PREFIJO}/pedidos/borradores/{borrador['client_uuid']}", headers=auth_otro
    )
    assert ajena.status_code == 404


def test_las_listas_son_del_vendedor(cliente_api: TestClient, datos: dict[str, Any]) -> None:
    assert (
        cliente_api.get(f"{PREFIJO}/pedidos/borradores", headers=datos["auth_bodega"]).status_code
        == 403
    )
