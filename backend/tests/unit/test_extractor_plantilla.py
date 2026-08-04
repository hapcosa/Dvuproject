"""Extracción de la maqueta del catálogo impreso: banda, logos de marca, páginas de arte.

Estas tres piezas no son texto y por eso el extractor de filas las ignora, pero son las
que hacen que una página **parezca** el catálogo de DVU. Se prueban sobre un PDF armado
acá con la geometría medida del original (banda roja arriba, folio adentro, logo a un
lado), porque el PDF real pesa 380 MB y no se versiona.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from dvu.extractor.plantilla import (
    LogoMarca,
    _clasificar,
    _es_rojo,
    _primera_racha,
    asociar_marcas_a_filas,
    extraer_banners,
    extraer_paginas_diseno,
)

ANCHO, ALTO = 595.0, 842.0
#: Los mismos valores del impreso: la banda no arranca en el borde, arranca a 33 pt.
BANDA_Y0, BANDA_Y1 = 33.0, 98.0
ROJO = (0.93, 0.11, 0.14)
AZUL = (0.18, 0.35, 0.65)


def _pagina_de_tabla(doc: fitz.Document, folio: int) -> None:
    """Una página de producto: banda roja con folio, fila de encabezados azul y grilla."""
    pagina = doc.new_page(width=ANCHO, height=ALTO)
    pagina.draw_rect(fitz.Rect(8, BANDA_Y0, 585, BANDA_Y1), color=ROJO, fill=ROJO)
    pagina.insert_text((20, 60), str(folio), fontsize=14, color=(1, 1, 1))
    # La fila de encabezados va pegada debajo, con su línea roja: es lo que hacía que
    # buscar el rojo por máximo se comiera media página.
    pagina.draw_rect(fitz.Rect(8, 105, 585, 130), color=AZUL, fill=AZUL)
    pagina.draw_line(fitz.Point(8, 132), fitz.Point(585, 132), color=ROJO, width=2)


def _pagina_de_arte(doc: fitz.Document, texto: str) -> None:
    pagina = doc.new_page(width=ANCHO, height=ALTO)
    pagina.insert_text((80, 300), texto, fontsize=30)


@pytest.fixture
def catalogo(tmp_path: Path) -> Path:
    """Portada, promoción, dos páginas de tabla y contraportada."""
    doc = fitz.open()
    _pagina_de_arte(doc, "PORTADA")
    _pagina_de_arte(doc, "NUEVO PRODUCTO")
    _pagina_de_tabla(doc, 3)
    _pagina_de_tabla(doc, 4)
    _pagina_de_arte(doc, "CONTRAPORTADA")
    ruta = tmp_path / "catalogo.pdf"
    doc.save(ruta)
    doc.close()
    return ruta


class TestBanda:
    def test_saca_la_banda_de_una_pagina_de_tabla_y_no_de_la_portada(
        self, catalogo: Path, tmp_path: Path
    ) -> None:
        """La portada es toda arte y tiene rojo por todos lados: si se la deja entrar, el
        encabezado del catálogo termina siendo un pedazo de publicidad."""
        banners = extraer_banners(catalogo, tmp_path, {3, 4})

        assert set(banners) == {"par", "impar"}
        for key in banners.values():
            pixeles = fitz.Pixmap(tmp_path / Path(key).name)
            # Una tira ancha y baja. Si hubiera agarrado la portada saldría casi cuadrada.
            assert 6 < pixeles.width / pixeles.height < 12

    def test_la_banda_no_se_lleva_el_encabezado_azul(self, catalogo: Path, tmp_path: Path) -> None:
        """La línea roja de la grilla, justo debajo de la fila azul, es la trampa: por
        máximo de rojo el recorte se estiraba hasta ahí y arrastraba el encabezado."""
        banners = extraer_banners(catalogo, tmp_path, {3, 4})
        ruta = tmp_path / Path(banners["impar"]).name

        pixeles = fitz.Pixmap(ruta)
        proporcion = pixeles.height / pixeles.width
        alto_pt = (585 - 8) * proporcion

        assert alto_pt < 100, "el recorte llegó hasta la fila de encabezados"
        assert alto_pt > 40

    def test_el_folio_no_queda_pegado_dentro_de_la_banda(
        self, catalogo: Path, tmp_path: Path
    ) -> None:
        """La banda se reusa en todas las páginas del catálogo generado: con el número de
        la página 3 impreso adentro, las 180 páginas dirían 3."""
        banners = extraer_banners(catalogo, tmp_path, {3, 4})
        ruta = tmp_path / Path(banners["impar"]).name
        pixeles = fitz.Pixmap(ruta)

        # Donde estaba el folio (arriba a la izquierda) tiene que haber quedado rojo.
        muestra = pixeles.pixel(int(pixeles.width * 0.03), int(pixeles.height * 0.5))
        assert _es_rojo(muestra)

    def test_sin_paginas_de_tabla_no_hay_banda(self, catalogo: Path, tmp_path: Path) -> None:
        assert extraer_banners(catalogo, tmp_path, set()) == {}


class TestPaginasDeDiseno:
    def test_copia_las_paginas_que_no_son_tabla(self, catalogo: Path, tmp_path: Path) -> None:
        paginas = extraer_paginas_diseno(catalogo, tmp_path, {3, 4})

        assert [(p.pagina, p.tipo) for p in paginas] == [
            (1, "portada"),
            (2, "promocion"),
            (5, "contraportada"),
        ]

    def test_la_pagina_se_copia_verbatim_no_se_redibuja(
        self, catalogo: Path, tmp_path: Path
    ) -> None:
        """Es arte hecho a mano en CorelDRAW. Cualquier reconstrucción es una imitación
        peor que el original, así que se guarda la página tal cual, en PDF."""
        portada = extraer_paginas_diseno(catalogo, tmp_path, {3, 4})[0]

        with fitz.open(portada.ruta_pdf) as doc:
            assert doc.page_count == 1
            assert "PORTADA" in doc[0].get_text()
        assert portada.ruta_png.exists()  # la vista previa para la web

    @pytest.mark.parametrize(
        ("numero", "total", "esperado"),
        [
            (1, 50, "portada"),
            (2, 50, "promocion"),
            (37, 50, "promocion"),
            (50, 50, "contraportada"),
        ],
    )
    def test_clasificacion_por_posicion_en_el_pliego(
        self, numero: int, total: int, esperado: str
    ) -> None:
        assert _clasificar(numero, total) == esperado


class TestAsociacionDeMarcas:
    def _logo(self, pagina: int, top: float, key: str) -> LogoMarca:
        return LogoMarca(pagina=pagina, key=key, ruta=Path(key), top=top, bottom=top + 18.0)

    def test_cada_logo_va_a_la_fila_que_tiene_al_lado(self) -> None:
        logos = [self._logo(3, 200.0, "a"), self._logo(3, 240.0, "b")]
        filas = {3: [(0, 209.0), (1, 249.0)]}

        assert asociar_marcas_a_filas(logos, filas) == {(3, 0): "a", (3, 1): "b"}

    def test_una_fila_sin_logo_cerca_se_queda_sin_marca(self) -> None:
        """En el catálogo hay 700 productos sin logo. Inventarles uno de la fila vecina
        es peor que dejar la celda vacía: le atribuye a un proveedor lo que no vende."""
        logos = [self._logo(3, 200.0, "a")]
        filas = {3: [(0, 209.0), (1, 400.0)]}

        assert asociar_marcas_a_filas(logos, filas) == {(3, 0): "a"}

    def test_un_logo_no_cruza_de_pagina(self) -> None:
        logos = [self._logo(3, 200.0, "a")]

        assert asociar_marcas_a_filas(logos, {4: [(0, 209.0)]}) == {}


class TestPrimeraRacha:
    """La banda es la **primera** franja roja de la página, no la más grande ni la unión
    de todas: debajo hay líneas de grilla del mismo rojo."""

    def test_devuelve_la_primera_franja_contigua(self) -> None:
        rojas = {3, 4, 5, 6, 20, 21}

        assert _primera_racha(lambda y: y in rojas, 30) == (3, 6)

    def test_sin_ninguna_fila_roja_no_hay_racha(self) -> None:
        assert _primera_racha(lambda y: False, 30) is None
