"""Fase 0 — extracción del catálogo PDF a filas normalizadas.

Estrategia:

1. Cada **precio** ancla una fila de producto. Es el campo más confiablemente
   presente: si no hay precio, no hay producto vendible.
2. El resto de las columnas se asocia por **proximidad vertical** a ese ancla.
3. `codigo` es **exclusivo**: dos productos nunca comparten código. Si no queda
   uno libre, la fila va a revisión.
4. El resto es **híbrido**: primero se reparten las celdas de forma exclusiva (greedy
   por distancia) y solo las filas que quedan sin celda copian la más cercana. Esto
   respeta los dos patrones reales del catálogo a la vez: una medida distinta por
   variante (`1/2"` / `3/4"`), y una descripción única que abarca toda la familia.

Todo lo dudoso se marca en `problemas` en vez de adivinarse. El reporte de calidad
es parte de la entrega, no un extra.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from dvu.domain.catalogo import (
    FilaNormalizada,
    diagnosticar,
    generar_sku,
    normalizar_codigo,
    parse_medida,
    parse_precio_clp,
    parse_venta_minima,
)
from dvu.extractor.layout import (
    DISTANCIA_MAXIMA_ASOCIACION,
    Celda,
    Columna,
    Palabra,
    agrupar_en_celdas,
    inicio_de_datos,
)

#: Un precio del catálogo: 3 dígitos, o miles separados por punto.
_RE_PRECIO_CELDA = re.compile(r"^\$?\s*\d{1,3}(?:\.\d{3})*$")

_COLUMNAS_EXCLUSIVAS: tuple[Columna, ...] = ("codigo",)
_COLUMNAS_HIBRIDAS: tuple[Columna, ...] = ("descripcion", "venta_min", "marca", "medida")


@dataclass(slots=True)
class ResultadoExtraccion:
    archivo: str
    sha256: str
    paginas: int
    filas: list[FilaNormalizada]

    @property
    def cargables(self) -> list[FilaNormalizada]:
        return [f for f in self.filas if f.cargable]


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def extraer_pdf(
    ruta: Path,
    *,
    desde_pagina: int = 1,
    hasta_pagina: int | None = None,
) -> ResultadoExtraccion:
    """Extrae un PDF completo del catálogo."""
    filas: list[FilaNormalizada] = []
    with pdfplumber.open(ruta) as pdf:
        total = len(pdf.pages)
        fin = min(hasta_pagina or total, total)
        for numero in range(desde_pagina, fin + 1):
            filas.extend(extraer_pagina(pdf.pages[numero - 1], numero))

    return ResultadoExtraccion(
        archivo=ruta.name,
        sha256=sha256_archivo(ruta),
        paginas=total,
        filas=filas,
    )


def extraer_pagina(pagina: object, numero: int) -> list[FilaNormalizada]:
    """Extrae las filas de producto de una página."""
    palabras = _palabras_de(pagina)
    y_inicio = inicio_de_datos(palabras)
    palabras = [p for p in palabras if p.top >= y_inicio]
    if not palabras:
        return []

    celdas = agrupar_en_celdas(palabras)
    anclas = [c for c in celdas["precio"] if _RE_PRECIO_CELDA.match(c.texto.strip())]
    if not anclas:
        return []
    anclas.sort(key=lambda c: c.top)

    asignaciones: dict[Columna, dict[int, Celda]] = {}
    heredadas: dict[Columna, set[int]] = {}
    for col in _COLUMNAS_EXCLUSIVAS:
        asignaciones[col] = _asignar_exclusivo(anclas, celdas[col])
        heredadas[col] = set()
    for col in _COLUMNAS_HIBRIDAS:
        asignaciones[col], heredadas[col] = _asignar_hibrido(anclas, celdas[col])

    _corregir_marca_numerica(asignaciones)

    return [
        _construir_fila(
            ancla=ancla,
            indice=i,
            pagina=numero,
            asignaciones=asignaciones,
            heredadas=heredadas,
        )
        for i, ancla in enumerate(anclas)
    ]


# --- asociación de celdas ----------------------------------------------------


def _asignar_exclusivo(anclas: list[Celda], candidatas: list[Celda]) -> dict[int, Celda]:
    """Cada celda se usa a lo más una vez. Greedy por distancia ascendente.

    Aplica a `codigo` (dos productos nunca comparten código) y a `medida` (en el
    catálogo, las variantes de una familia se distinguen justamente por la medida).
    """
    pares = sorted(
        (
            (candidata.distancia_a(ancla.centro_y), i, j)
            for i, ancla in enumerate(anclas)
            for j, candidata in enumerate(candidatas)
        ),
        key=lambda t: (t[0], t[1]),
    )

    asignado: dict[int, Celda] = {}
    usadas: set[int] = set()
    for distancia, i, j in pares:
        if distancia > DISTANCIA_MAXIMA_ASOCIACION or i in asignado or j in usadas:
            continue
        asignado[i] = candidatas[j]
        usadas.add(j)
    return asignado


def _asignar_hibrido(
    anclas: list[Celda], candidatas: list[Celda]
) -> tuple[dict[int, Celda], set[int]]:
    """Reparto exclusivo primero; las filas que quedan sin celda copian la más cercana.

    Sin la fase exclusiva, dos variantes contiguas terminan compartiendo la venta
    mínima o la medida de la fila vecina. Sin la fase de copia, una descripción
    única no llegaría al resto de la familia.

    Devuelve también qué filas recibieron un valor **heredado** en vez de propio: es
    una copia plausible, no un dato leído del PDF, y el reporte debe distinguirlo.
    """
    asignado = _asignar_exclusivo(anclas, candidatas)
    heredadas: set[int] = set()

    for i, ancla in enumerate(anclas):
        if i in asignado:
            continue
        mejor: Celda | None = None
        mejor_dist = DISTANCIA_MAXIMA_ASOCIACION
        for candidata in candidatas:
            d = candidata.distancia_a(ancla.centro_y)
            if d < mejor_dist:
                mejor, mejor_dist = candidata, d
        if mejor is not None:
            asignado[i] = mejor
            heredadas.add(i)

    return asignado, heredadas


def _corregir_marca_numerica(asignaciones: dict[Columna, dict[int, Celda]]) -> None:
    """La columna «Marca» viene casi siempre vacía en el catálogo de DVU.

    Cuando trae solo un número o una fracción (`3`, `1/2`), no es una marca: es el
    inicio de la medida que quedó a la izquierda del límite de columna, porque
    CorelDRAW posiciona el texto libremente. Ej.: `3 /16 X 50 MTS` se parte en
    `Marca="3"` y `Medida="/16 X 50 MTS"`. Se reconstruye la medida.
    """
    marcas = asignaciones.get("marca", {})
    medidas = asignaciones.get("medida", {})
    for i, celda_marca in list(marcas.items()):
        texto = celda_marca.texto.strip()
        if not re.fullmatch(r"\d+(?:/\d+)?", texto):
            continue
        celda_medida = medidas.get(i)
        if celda_medida is not None and celda_medida.texto.lstrip().startswith("/"):
            celda_medida.texto = f"{texto} {celda_medida.texto.strip()}"
        del marcas[i]


def _construir_fila(
    *,
    ancla: Celda,
    indice: int,
    pagina: int,
    asignaciones: dict[Columna, dict[int, Celda]],
    heredadas: dict[Columna, set[int]],
) -> FilaNormalizada:
    def texto(col: Columna) -> str | None:
        celda = asignaciones[col].get(indice)
        return celda.texto.strip() if celda and celda.texto.strip() else None

    codigo = normalizar_codigo(texto("codigo"))
    descripcion = texto("descripcion") or ""
    fila = FilaNormalizada(
        codigo=codigo,
        sku=generar_sku(codigo) if codigo else None,
        descripcion=re.sub(r"\s+", " ", descripcion),
        venta_minima=parse_venta_minima(texto("venta_min")),
        marca=texto("marca"),
        medida=parse_medida(texto("medida")),
        precio_clp=parse_precio_clp(ancla.texto),
        pagina=pagina,
        orden=indice,
        y_centro=ancla.centro_y,
    )
    fila.problemas = diagnosticar(fila)

    # Un valor heredado es una copia plausible de la fila vecina, no un dato leído.
    # Se marca para que el reporte lo distinga de un dato propio.
    for col in ("medida", "venta_min"):
        if indice in heredadas.get(col, set()):
            fila.problemas.append(f"{col}_heredada")

    return fila


def _palabras_de(pagina: object) -> list[Palabra]:
    crudas = pagina.extract_words(  # type: ignore[attr-defined]
        x_tolerance=1.5,
        y_tolerance=2,
        keep_blank_chars=False,
    )
    return [
        Palabra(
            texto=w["text"],
            x0=float(w["x0"]),
            x1=float(w["x1"]),
            top=float(w["top"]),
            bottom=float(w["bottom"]),
        )
        for w in crudas
    ]


def iter_pdfs(directorio: Path) -> Iterator[Path]:
    """PDF del catálogo, en orden (PARTE 1 antes que PARTE 2)."""
    yield from sorted(directorio.glob("*.pdf"))
