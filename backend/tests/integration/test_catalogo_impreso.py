"""Exportación del catálogo a PDF con el diseño del impreso.

Lo que se verifica acá no es que «salga un PDF» sino que salga **el catálogo**: que los
precios y códigos estén escritos en la página, que la foto que ya está en el almacén se
embeba una sola vez aunque la compartan varios productos, y que un producto sin foto no
tumbe la exportación entera.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any, BinaryIO

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dvu.carga.catalogo_impreso import exportar_catalogo_pdf, formatear_clp
from dvu.carga.categorias import clasificar_catalogo
from dvu.db.models import Producto, ProductoAlias, Usuario
from dvu.seguridad import emitir_token, hashear

pytestmark = pytest.mark.integration

PREFIJO = "/api/v1"


def _png(color: str = "red") -> bytes:
    """Un PNG chico de verdad. Lo que se prueba es el circuito de la foto, no la foto."""
    from PIL import Image as PilImage

    buffer = BytesIO()
    PilImage.new("RGB", (40, 30), color).save(buffer, format="PNG")
    return buffer.getvalue()


class AlmacenDeMentira:
    """Guarda en memoria. Implementa el `Protocol` completo, `leer` incluido."""

    def __init__(self) -> None:
        self.contenido: dict[str, bytes] = {}
        self.lecturas: list[str] = []

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        self.contenido[key] = datos.read()
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        return f"https://ejemplo/{key}"

    def leer(self, key: str) -> bytes | None:
        self.lecturas.append(key)
        return self.contenido.get(key)


@pytest.fixture
def almacen() -> AlmacenDeMentira:
    alm = AlmacenDeMentira()
    alm.guardar("catalogo/abc123.png", BytesIO(_png()), "image/png")
    return alm


@pytest.fixture
def datos(sesion: Session) -> dict[str, Any]:
    admin = Usuario(email="a@test.cl", nombre="A", rol="admin", password_hash=hashear("x"))
    sesion.add(admin)

    codo = Producto(
        sku="DVU-CODO",
        descripcion="CODO 90 PVC 110MM",
        multiplo_venta=12,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("1790"),
        imagen_key="catalogo/abc123.png",
        marca="VINILIT",
        medida="110MM",
    )
    codo.alias = [ProductoAlias(codigo="PR/49573", origen="pdf")]
    # Comparte la misma foto: en el catálogo real una imagen sirve a toda una familia.
    tee = Producto(
        sku="DVU-TEE",
        descripcion="TEE PVC 110MM",
        multiplo_venta=12,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("2450"),
        imagen_key="catalogo/abc123.png",
    )
    sin_foto = Producto(
        sku="DVU-RARO",
        descripcion="POLICARBONATO ALVEOLAR BRONCE",
        multiplo_venta=1,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("93000"),
    )
    inactivo = Producto(
        sku="DVU-VIEJO",
        descripcion="PRODUCTO DESCONTINUADO",
        multiplo_venta=1,
        unidad_venta="UNID",
        precio_lista_clp=Decimal("100"),
        activo=False,
    )
    sesion.add_all([codo, tee, sin_foto, inactivo])
    sesion.flush()

    return {"auth_admin": {"Authorization": f"Bearer {emitir_token(admin.uuid, 'admin')}"}}


def _texto(pdf: bytes) -> str:
    """Texto del PDF con los espacios normalizados: una descripción larga se parte en
    dos líneas dentro de la celda, y eso es maquetación, no contenido."""
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        crudo = "\n".join(pagina.get_text() for pagina in doc)
    return " ".join(crudo.split())


def test_el_pdf_trae_los_datos_que_el_vendedor_necesita_para_cotizar(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    texto = _texto(exportar_catalogo_pdf(sesion, almacen))

    assert "CODO 90 PVC 110MM" in texto
    assert "PR/49573" in texto  # el código del proveedor, que es por el que pregunta
    assert "$ 1.790" in texto
    assert "12 X UNID" in texto  # sin la venta mínima el precio no sirve para cotizar


def test_el_producto_desactivado_no_se_imprime(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Un catálogo impreso con productos descontinuados genera pedidos que no se pueden
    despachar."""
    assert "DESCONTINUADO" not in _texto(exportar_catalogo_pdf(sesion, almacen))


def test_la_foto_compartida_se_baja_una_sola_vez(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Dos productos comparten `imagen_key`. Sin caché se bajaría dos veces del almacén y
    el PDF cargaría la imagen duplicada; con 1.975 productos eso son megabytes de más."""
    exportar_catalogo_pdf(sesion, almacen)

    assert almacen.lecturas.count("catalogo/abc123.png") == 1


def test_un_producto_sin_foto_no_rompe_la_exportacion(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    texto = _texto(exportar_catalogo_pdf(sesion, almacen))

    assert "POLICARBONATO ALVEOLAR BRONCE" in texto
    assert "$ 93.000" in texto


def test_una_foto_que_falta_en_el_almacen_tampoco_rompe(
    sesion: Session, datos: dict[str, Any]
) -> None:
    """La fila apunta a una key que no está: el catálogo sale igual, con el hueco."""
    vacio = AlmacenDeMentira()

    texto = _texto(exportar_catalogo_pdf(sesion, vacio))

    assert "CODO 90 PVC 110MM" in texto


def test_una_foto_corrupta_no_tumba_el_catalogo(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Reportlab abre la imagen en medio del armado de la página: un JPEG truncado en el
    almacén se llevaría puestas las 140 páginas por una sola foto mala."""
    almacen.contenido["catalogo/abc123.png"] = b"\x89PNG\r\n\x1a\n esto no es una imagen"

    texto = _texto(exportar_catalogo_pdf(sesion, almacen))

    assert "CODO 90 PVC 110MM" in texto


def test_filtrar_por_categoria_deja_afuera_al_resto(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    clasificar_catalogo(sesion)

    texto = _texto(exportar_catalogo_pdf(sesion, almacen, categoria="gasfiteria"))

    assert "CODO 90 PVC 110MM" in texto
    assert "POLICARBONATO" not in texto


def test_la_lista_de_precios_saca_la_columna_de_imagen(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Sin fotos la columna no se deja en blanco: se saca y el ancho va a la descripción."""
    assert "Imagen" in _texto(exportar_catalogo_pdf(sesion, almacen))

    almacen.lecturas.clear()
    sin = _texto(exportar_catalogo_pdf(sesion, almacen, con_imagenes=False))

    assert "Imagen" not in sin
    assert "CODO 90 PVC 110MM" in sin
    assert not almacen.lecturas  # ni siquiera toca el almacén


def test_cada_pagina_repite_el_encabezado(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Una hoja suelta del catálogo tiene que poder leerse sola: así se usa en terreno."""
    with fitz.open(stream=exportar_catalogo_pdf(sesion, almacen), filetype="pdf") as doc:
        primera = doc[0].get_text()

    assert "CATÁLOGO DVU" in primera
    assert "Precio" in primera
    assert "no incluyen IVA" in primera  # el precio de lista es neto y hay que decirlo


def test_el_pdf_se_descarga_desde_la_api(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    respuesta = cliente_api.get(f"{PREFIJO}/reportes/catalogo.pdf", headers=datos["auth_admin"])

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "application/pdf"
    assert "catalogo-dvu" in respuesta.headers["content-disposition"]
    assert respuesta.content.startswith(b"%PDF")


def test_sin_sesion_no_se_baja_el_catalogo_entero(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """El catálogo web es público, pero generar el PDF cuesta segundos de CPU: abierto al
    mundo es una palanca de denegación de servicio."""
    assert cliente_api.get(f"{PREFIJO}/reportes/catalogo.pdf").status_code == 401


@pytest.mark.parametrize(
    ("monto", "esperado"),
    [
        (0, "$ 0"),
        (990, "$ 990"),
        (12990, "$ 12.990"),
        (290000, "$ 290.000"),
        (1234567, "$ 1.234.567"),
    ],
)
def test_el_precio_va_en_pesos_sin_decimales(monto: int, esperado: str) -> None:
    """CLP entero, con punto de miles. Un decimal en un precio delata un float en alguna
    capa, y eso está prohibido en todo el sistema."""
    assert formatear_clp(monto) == esperado
