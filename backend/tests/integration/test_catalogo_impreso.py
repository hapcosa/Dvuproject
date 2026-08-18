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

from dvu.carga.catalogo_impreso import CatalogoVacio, exportar_catalogo_pdf, formatear_clp
from dvu.carga.categorias import clasificar_catalogo
from dvu.db.models import (
    CatalogoActivo,
    CatalogoPagina,
    Marca,
    Producto,
    ProductoAlias,
    Usuario,
)
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
        marca=Marca(nombre="VINILIT", slug="vinilit"),
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

    # Sin banda guardada en el almacén se dibuja la de respaldo, con los rótulos del
    # impreso. Con banda real el texto va dentro de la imagen y no se puede buscar acá.
    assert "CATALOGO" in primera
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


def _banda(logo_a_la_izquierda: bool) -> bytes:
    """Una banda como la extraída del impreso: roja, con el logo de un solo lado.

    El lado se detecta midiendo dónde la banda deja de ser roja, así que acá se pinta un
    bloque blanco en la mitad que corresponde.
    """
    from PIL import Image as PilImage

    imagen = PilImage.new("RGB", (600, 70), "red")
    x = 0 if logo_a_la_izquierda else 400
    imagen.paste(PilImage.new("RGB", (200, 70), "white"), (x, 0))
    buffer = BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_de_una_pagina(texto: str) -> bytes:
    doc = fitz.open()
    doc.new_page(width=595, height=842).insert_text((80, 200), texto, fontsize=28)
    datos: bytes = doc.tobytes()
    doc.close()
    return datos


@pytest.fixture
def plantilla(sesion: Session, almacen: AlmacenDeMentira) -> None:
    """Los activos que deja el extractor: las dos bandas y una portada."""
    for clave, izquierda in (("banner_par", True), ("banner_impar", False)):
        key = f"catalogo/plantilla/{clave}.png"
        almacen.guardar(key, BytesIO(_banda(izquierda)), "image/png")
        sesion.add(CatalogoActivo(clave=clave, key_objeto=key))

    almacen.guardar(
        "catalogo/paginas/portada.pdf",
        BytesIO(_pdf_de_una_pagina("PORTADA DVU")),
        "application/pdf",
    )
    almacen.guardar("catalogo/paginas/portada.png", BytesIO(_png("blue")), "image/png")
    sesion.add(
        CatalogoPagina(
            archivo="CAT.pdf",
            pagina=1,
            tipo="portada",
            key_pdf="catalogo/paginas/portada.pdf",
            key_png="catalogo/paginas/portada.png",
        )
    )
    sesion.flush()


def test_la_banda_del_impreso_reemplaza_a_la_dibujada_a_mano(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Con la banda original guardada, el encabezado es la del catálogo, no una imitación.

    Es lo que le da identidad a la página: el degradado rojo y el logo DVU son de
    imprenta y no se pueden redibujar con un rectángulo.
    """
    pdf = exportar_catalogo_pdf(sesion, almacen)

    assert "CATALOGO\nFERRETERIA" not in _texto(pdf)  # la banda de respaldo ya no aparece
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        cuerpo = doc[1]  # la 0 es la portada pegada
        arriba = [b for b in cuerpo.get_image_info() if b["bbox"][1] < 20]
    assert arriba, "la banda tiene que estar dibujada al tope de la página"


def test_el_folio_va_del_lado_contrario_al_logo(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Como en el impreso: el número de página no se monta sobre el logo."""
    with fitz.open(stream=exportar_catalogo_pdf(sesion, almacen), filetype="pdf") as doc:
        pagina = doc[1]
        # "2" porque la portada pegada corre la numeración: el folio es la página física.
        folios = [p for p in pagina.get_text("words") if p[4] == "2" and p[3] < 40]

    assert folios, "falta el folio en la banda"
    # Página par -> banner_par, que tiene el logo a la izquierda: el folio va a la derecha.
    assert folios[0][0] > 400


def test_la_portada_original_se_pega_tal_cual(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """No se redibuja: es la página de imprenta, copiada como PDF y no como imagen."""
    with fitz.open(stream=exportar_catalogo_pdf(sesion, almacen), filetype="pdf") as doc:
        assert "PORTADA DVU" in doc[0].get_text()
        assert doc.page_count >= 2


def test_la_lista_de_precios_no_lleva_portada(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Sin fotos es una lista de precios para mandar por WhatsApp: la portada del
    catálogo ilustrado ahí confunde sobre lo que es, y pesa."""
    with fitz.open(
        stream=exportar_catalogo_pdf(sesion, almacen, con_imagenes=False), filetype="pdf"
    ) as doc:
        assert "PORTADA DVU" not in doc[0].get_text()


def test_la_marca_sale_como_logo_y_no_como_texto(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """En el impreso la marca es el PNG del proveedor. Si está el logo, se usa el logo."""
    almacen.guardar("catalogo/marcas/vinilit.png", BytesIO(_png("green")), "image/png")
    producto = sesion.query(Producto).filter_by(sku="DVU-CODO").one()
    producto.marca_logo_key = "catalogo/marcas/vinilit.png"
    sesion.flush()

    assert "VINILIT" not in _texto(exportar_catalogo_pdf(sesion, almacen))


def test_sin_logo_la_marca_cae_al_nombre_escrito(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Un producto cargado a mano por el administrador no tiene logo del PDF. Antes de
    dejar la celda vacía se escribe el nombre de la marca: el dato existe."""
    assert "VINILIT" in _texto(exportar_catalogo_pdf(sesion, almacen))


def test_sin_marca_nombrada_queda_lo_que_imprimio_el_pdf(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any]
) -> None:
    """Último recurso: la columna «Marca» del PDF, que casi siempre trae basura.

    Se muestra igual porque el hueco no ayuda a nadie, pero va después de la marca
    nombrada: mientras alguien no le ponga nombre al logo, esto es todo lo que hay.
    """
    producto = sesion.query(Producto).filter_by(sku="DVU-TEE").one()
    producto.marca_impresa = "FEDERAL"
    sesion.flush()

    assert "FEDERAL" in _texto(exportar_catalogo_pdf(sesion, almacen))


# --- el orden de la maqueta -------------------------------------------------


def _maqueta_completa(sesion: Session, almacen: AlmacenDeMentira) -> None:
    """Una portada, dos ofertas y una contraportada, cargadas a propósito desordenadas.

    Los `orden` van al revés de como deben salir: si el exportador usara el orden de
    inserción o el de la base, el PDF saldría con las ofertas al revés y el test no lo
    notaría.
    """
    piezas = [
        ("contraportada", "CIERRE DVU", 1),
        ("promocion", "OFERTA B", 2),
        ("promocion", "OFERTA A", 1),
    ]
    for numero, (tipo, texto, orden) in enumerate(piezas, start=10):
        base = f"catalogo/paginas/{tipo}-{numero}"
        almacen.guardar(f"{base}.pdf", BytesIO(_pdf_de_una_pagina(texto)), "application/pdf")
        almacen.guardar(f"{base}.png", BytesIO(_png("blue")), "image/png")
        sesion.add(
            CatalogoPagina(
                archivo="CAT.pdf",
                pagina=numero,
                tipo=tipo,
                orden=orden,
                key_pdf=f"{base}.pdf",
                key_png=f"{base}.png",
            )
        )
    sesion.flush()


def test_las_secciones_salen_portada_cuerpo_ofertas_contraportada(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """El orden de las secciones no es configurable: es lo que hace que se lea como catálogo."""
    _maqueta_completa(sesion, almacen)

    with fitz.open(stream=exportar_catalogo_pdf(sesion, almacen), filetype="pdf") as doc:
        paginas = [doc[i].get_text() for i in range(doc.page_count)]

    assert "PORTADA DVU" in paginas[0]
    assert "CIERRE DVU" in paginas[-1]
    ofertas = [i for i, t in enumerate(paginas) if "OFERTA" in t]
    assert ofertas, "las ofertas tienen que estar pegadas"
    assert min(ofertas) > 0, "las ofertas van después del cuerpo, no antes"
    assert max(ofertas) < len(paginas) - 1, "la contraportada cierra"


def test_dentro_de_la_seccion_manda_el_orden_del_administrador(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Es lo que se arrastra en la pantalla de administración."""
    _maqueta_completa(sesion, almacen)

    with fitz.open(stream=exportar_catalogo_pdf(sesion, almacen), filetype="pdf") as doc:
        textos = [doc[i].get_text() for i in range(doc.page_count)]

    posicion = {
        rotulo: next(i for i, t in enumerate(textos) if rotulo in t)
        for rotulo in ("OFERTA A", "OFERTA B")
    }
    assert posicion["OFERTA A"] < posicion["OFERTA B"]


def test_el_catalogo_filtrado_lleva_tapas_pero_no_las_ofertas(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Las hojas de oferta son del catálogo completo: detrás de una categoría son medio
    catálogo de peso en páginas que no responden lo que se preguntó."""
    _maqueta_completa(sesion, almacen)
    clasificar_catalogo(sesion)

    with fitz.open(
        stream=exportar_catalogo_pdf(sesion, almacen, categoria="gasfiteria"), filetype="pdf"
    ) as doc:
        textos = "".join(doc[i].get_text() for i in range(doc.page_count))

    assert "PORTADA DVU" in textos
    assert "CIERRE DVU" in textos
    assert "OFERTA" not in textos


def test_un_filtro_sin_resultados_avisa_en_vez_de_emitir_un_pdf_vacio(
    sesion: Session, almacen: AlmacenDeMentira, datos: dict[str, Any], plantilla: None
) -> None:
    """Con las páginas de arte pegadas ese PDF pesa decenas de MB y no tiene ni una fila:
    el vendedor lo baja, lo abre y recién ahí descubre que su búsqueda no encontró nada."""
    with pytest.raises(CatalogoVacio):
        exportar_catalogo_pdf(sesion, almacen, q="no-existe-este-producto")


# --- bajarlo por URL, que es como el navegador sabe bajar archivos grandes ---


def test_el_pdf_se_baja_con_el_token_de_descarga_en_la_url(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """Al navegar a una URL no hay dónde poner el `Authorization`, y el catálogo con
    fotos pesa demasiado para juntarlo en memoria antes de escribirlo."""
    permiso = cliente_api.post(f"{PREFIJO}/auth/descarga", headers=datos["auth_admin"]).json()

    respuesta = cliente_api.get(
        f"{PREFIJO}/reportes/catalogo.pdf", params={"token": permiso["token"]}
    )

    assert respuesta.status_code == 200
    assert respuesta.content.startswith(b"%PDF")


def test_el_token_de_sesion_no_sirve_en_la_url(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    """La query queda en el historial del navegador y en el log del proxy: lo que se
    filtre por ahí tiene que servir para bajar un archivo y para nada más."""
    de_sesion = datos["auth_admin"]["Authorization"].removeprefix("Bearer ")

    respuesta = cliente_api.get(f"{PREFIJO}/reportes/catalogo.pdf", params={"token": de_sesion})

    assert respuesta.status_code == 401


def test_el_token_de_descarga_no_abre_el_resto_de_la_api(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    permiso = cliente_api.post(f"{PREFIJO}/auth/descarga", headers=datos["auth_admin"]).json()

    respuesta = cliente_api.get(
        f"{PREFIJO}/auth/yo", headers={"Authorization": f"Bearer {permiso['token']}"}
    )

    assert respuesta.status_code == 401


def test_una_busqueda_sin_resultados_responde_404_y_no_un_pdf(
    sesion: Session, cliente_api: TestClient, datos: dict[str, Any]
) -> None:
    respuesta = cliente_api.get(
        f"{PREFIJO}/reportes/catalogo.pdf",
        params={"q": "no-existe-este-producto"},
        headers=datos["auth_admin"],
    )

    assert respuesta.status_code == 404
