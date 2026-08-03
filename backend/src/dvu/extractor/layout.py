"""Geometría de la tabla del catálogo DVU.

Los PDF salen de CorelDRAW: no hay tabla real, solo texto posicionado. Las columnas
se identifican por rango de X, medido sobre los PDF de julio 2026 y verificado en
ambas partes del catálogo.

El encabezado se repite en cada página; se usa para *calibrar* los rangos y para
saber dónde empieza la zona de datos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Columna = Literal["codigo", "imagen", "descripcion", "venta_min", "marca", "medida", "precio"]

#: Rangos X por columna, en puntos PDF sobre página A4 (595.28 pt de ancho).
#: Medidos empíricamente; se recalibran por página cuando el encabezado es legible.
RANGOS_X: dict[Columna, tuple[float, float]] = {
    "codigo": (10.0, 110.0),
    "imagen": (110.0, 218.0),
    "descripcion": (218.0, 345.0),
    "venta_min": (345.0, 415.0),
    "marca": (415.0, 486.0),
    "medida": (486.0, 545.0),
    "precio": (545.0, 595.0),
}

#: Palabras del encabezado. "MedidPaR" y "ECPIOrecio" no son errores: en el PDF
#: original los rótulos "Medida"/"Precio" y "PRECIO" se superponen y pdfplumber
#: los entrega intercalados.
PALABRAS_ENCABEZADO = frozenset(
    {"código", "codigo", "imagen", "descripción", "descripcion", "detalle", "marca", "venta", "min"}
)

#: Máxima separación vertical (pt) para fusionar dos líneas en una misma celda.
#:
#: Depende de la columna, y la diferencia importa: `descripcion` y `venta_min` sí
#: ocupan varias líneas ("ESPATULA C/ MANGO" + "DOBLE COLOR", "BOLSA" + "X200UN."),
#: mientras que `codigo`, `medida` y `precio` son siempre de una línea — fusionarlas
#: destruye filas enteras, porque dos precios consecutivos quedan a solo ~2 pt.
TOLERANCIA_MERGE_Y: dict[Columna, float] = {
    "codigo": 0.0,
    "imagen": 0.0,
    "descripcion": 4.5,
    "venta_min": 4.5,
    "marca": 4.5,
    "medida": 0.0,
    "precio": 0.0,
}

#: Distancia vertical máxima (pt) para asociar una celda a una fila de precio.
#: Más allá de esto se asume que la celda pertenece a otro producto.
DISTANCIA_MAXIMA_ASOCIACION = 40.0


@dataclass(slots=True)
class Palabra:
    texto: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def centro_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def centro_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class Celda:
    """Texto contiguo dentro de una columna, posiblemente de varias líneas."""

    columna: Columna
    texto: str
    top: float
    bottom: float
    palabras: list[Palabra] = field(default_factory=list)

    @property
    def centro_y(self) -> float:
        return (self.top + self.bottom) / 2

    def distancia_a(self, y: float) -> float:
        """0 si `y` cae dentro de la celda; si no, la separación al borde más cercano."""
        if self.top <= y <= self.bottom:
            return 0.0
        return min(abs(y - self.top), abs(y - self.bottom))


def columna_de(x_centro: float) -> Columna | None:
    for col, (x0, x1) in RANGOS_X.items():
        if x0 <= x_centro < x1:
            return col
    return None


def inicio_de_datos(palabras: list[Palabra]) -> float:
    """Y bajo la cual empiezan los productos (debajo del encabezado).

    Si no se detecta encabezado (portadas, páginas de sección), devuelve 0: la
    página se procesa igual y probablemente no produzca filas, lo que es correcto.
    """
    encabezado = [p for p in palabras if p.texto.strip().lower() in PALABRAS_ENCABEZADO]
    if not encabezado:
        return 0.0
    # "Venta Min" va en una segunda línea del encabezado; se toma el borde inferior.
    return max(p.bottom for p in encabezado) + 2.0


def agrupar_en_celdas(palabras: list[Palabra]) -> dict[Columna, list[Celda]]:
    """Agrupa palabras por columna y las fusiona en celdas verticalmente contiguas."""
    por_columna: dict[Columna, list[Palabra]] = {c: [] for c in RANGOS_X}
    for p in palabras:
        if (col := columna_de(p.centro_x)) is not None:
            por_columna[col].append(p)

    celdas: dict[Columna, list[Celda]] = {}
    for col, ps in por_columna.items():
        celdas[col] = _fusionar(col, ps)
    return celdas


def _fusionar(columna: Columna, palabras: list[Palabra]) -> list[Celda]:
    if not palabras:
        return []

    # Primero líneas (misma banda vertical), luego líneas contiguas en una celda.
    lineas: list[list[Palabra]] = []
    for p in sorted(palabras, key=lambda w: (w.top, w.x0)):
        if lineas and _solapan(lineas[-1], p):
            lineas[-1].append(p)
        else:
            lineas.append([p])

    tolerancia = TOLERANCIA_MERGE_Y[columna]
    celdas: list[Celda] = []
    for linea in lineas:
        linea.sort(key=lambda w: w.x0)
        texto = " ".join(w.texto for w in linea).strip()
        top = min(w.top for w in linea)
        bottom = max(w.bottom for w in linea)

        anterior = celdas[-1] if celdas else None
        if anterior is not None and tolerancia > 0 and top - anterior.bottom <= tolerancia:
            anterior.texto = f"{anterior.texto} {texto}".strip()
            anterior.bottom = max(anterior.bottom, bottom)
            anterior.palabras.extend(linea)
        else:
            celdas.append(
                Celda(columna=columna, texto=texto, top=top, bottom=bottom, palabras=list(linea))
            )
    return celdas


def _solapan(linea: list[Palabra], p: Palabra) -> bool:
    """¿La palabra cae en la misma banda horizontal que la línea?"""
    top = min(w.top for w in linea)
    bottom = max(w.bottom for w in linea)
    altura = max(bottom - top, 1.0)
    solape = min(bottom, p.bottom) - max(top, p.top)
    return solape > altura * 0.4
