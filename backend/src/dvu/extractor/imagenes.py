"""Extracción de las imágenes de producto del catálogo.

Los PDF traen las fotos como JPEG embebidos a ~300 ppi en la columna «Imagen».
Se asocian a las filas por solapamiento vertical con el ancla de precio.

Se descartan los adornos: el catálogo repite iconos y logos pequeñísimos (83×46 px,
751 B) decenas de veces por página, y no son fotos de producto.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from dvu.extractor.layout import RANGOS_X

#: Bajo estos umbrales la imagen es un icono o un separador, no una foto.
ANCHO_MINIMO_PX = 120
ALTO_MINIMO_PX = 90
BYTES_MINIMO = 4096


@dataclass(slots=True)
class ImagenProducto:
    pagina: int
    #: Clave de objeto: `catalogo/<sha256[:16]>.<ext>`. El hash deduplica: un mismo
    #: producto repetido en varias páginas comparte archivo.
    key: str
    ruta: Path
    top: float
    bottom: float
    ancho_px: int
    alto_px: int

    @property
    def centro_y(self) -> float:
        return (self.top + self.bottom) / 2


def extraer_imagenes(
    pdf: Path, destino: Path, *, hasta_pagina: int | None = None
) -> list[ImagenProducto]:
    """Vuelca las imágenes de producto a `destino` y devuelve su ubicación en la página."""
    destino.mkdir(parents=True, exist_ok=True)
    encontradas: list[ImagenProducto] = []
    x0_col, x1_col = RANGOS_X["imagen"]

    with fitz.open(pdf) as doc:
        fin = min(hasta_pagina or doc.page_count, doc.page_count)
        for numero in range(1, fin + 1):
            pagina = doc[numero - 1]
            for info in pagina.get_images(full=True):
                xref = info[0]
                rects = pagina.get_image_rects(xref)
                if not rects:
                    continue

                # Solo lo que cae en la columna «Imagen».
                rect = next((r for r in rects if x0_col <= (r.x0 + r.x1) / 2 < x1_col), None)
                if rect is None:
                    continue

                base = doc.extract_image(xref)
                datos: bytes = base["image"]
                if (
                    base["width"] < ANCHO_MINIMO_PX
                    or base["height"] < ALTO_MINIMO_PX
                    or len(datos) < BYTES_MINIMO
                ):
                    continue

                sha = hashlib.sha256(datos).hexdigest()[:16]
                ext = base.get("ext", "jpg")
                key = f"catalogo/{sha}.{ext}"
                ruta = destino / f"{sha}.{ext}"
                if not ruta.exists():
                    ruta.write_bytes(datos)

                encontradas.append(
                    ImagenProducto(
                        pagina=numero,
                        key=key,
                        ruta=ruta,
                        top=float(rect.y0),
                        bottom=float(rect.y1),
                        ancho_px=int(base["width"]),
                        alto_px=int(base["height"]),
                    )
                )
    return encontradas


def asociar_a_filas(
    imagenes: list[ImagenProducto],
    filas_por_pagina: dict[int, list[tuple[int, float]]],
    *,
    tolerancia: float = 30.0,
) -> dict[tuple[int, int], str]:
    """Asocia imágenes a filas por cercanía vertical dentro de la misma página.

    `filas_por_pagina`: página -> [(orden de la fila, centro Y del ancla de precio)].
    Devuelve: (página, orden) -> key de la imagen.

    Una imagen puede servir a varias filas (una foto por familia de productos), pero
    una fila recibe a lo más una imagen.
    """
    asignacion: dict[tuple[int, int], str] = {}
    for pagina, filas in filas_por_pagina.items():
        candidatas = [img for img in imagenes if img.pagina == pagina]
        if not candidatas:
            continue
        for orden, centro_y in filas:
            mejor = min(candidatas, key=lambda img: abs(img.centro_y - centro_y))
            if abs(mejor.centro_y - centro_y) <= tolerancia:
                asignacion[(pagina, orden)] = mejor.key
    return asignacion
