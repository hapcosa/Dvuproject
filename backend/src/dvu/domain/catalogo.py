"""Normalización de datos del catálogo. Lógica pura: sin I/O, sin dependencias externas.

Todo lo que aquí se decide salió de leer el catálogo real de DVU (150 páginas,
~2.200 filas). Los casos de ejemplo en los docstrings son literales del PDF.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

# --- precios -----------------------------------------------------------------

# Chile usa "." como separador de miles y no hay decimales en CLP.
# El signo peso admite repetición: en algunas páginas del catálogo el rótulo "$" del
# encabezado se superpone al de la fila y pdfplumber entrega "$$".
_RE_PRECIO = re.compile(r"^\$*\s*(\d{1,3}(?:\.\d{3})+|\d+)\s*$")


def parse_precio_clp(raw: str | None) -> int | None:
    """Convierte el precio del catálogo a entero CLP.

    >>> parse_precio_clp("1.790")
    1790
    >>> parse_precio_clp("62.500")
    62500
    >>> parse_precio_clp("455")
    455
    >>> parse_precio_clp("") is None
    True
    """
    if not raw:
        return None
    m = _RE_PRECIO.match(raw.strip())
    if not m:
        return None
    valor = int(m.group(1).replace(".", ""))
    # Un precio de 0 en el catálogo es dato faltante, no un producto gratis.
    return valor or None


def neto_a_bruto(neto_clp: int, iva: float = 0.19) -> int:
    """CLP no tiene decimales: el IVA se redondea al peso."""
    return int(Decimal(neto_clp) * (1 + Decimal(str(iva))).quantize(Decimal("0.0001")))


# --- venta mínima ------------------------------------------------------------

# Regla de negocio central: DVU vende por caja/bolsa, no por unidad suelta.
# Formatos observados: "X 12 UNID", "X20 UN", "BOLSA X200UN.", "X 4 UN", "X UN".

_ENVASES = {
    "BOLSA": "BOLSA",
    "CAJA": "CAJA",
    "PACK": "PACK",
    "TIRA": "TIRA",
    "SET": "SET",
    "JUEGO": "JUEGO",
    "JGO": "JUEGO",
    "DISPLAY": "DISPLAY",
    "ROLLO": "ROLLO",
    "SACO": "SACO",
}

_UNIDADES = {
    "UN": "UNID",
    "UNI": "UNID",
    "UND": "UNID",
    "UNID": "UNID",
    "UNIDAD": "UNID",
    "UNIDADES": "UNID",
    "MT": "MT",
    "MTS": "MT",
    "M": "MT",
    "KG": "KG",
    "LT": "LT",
    "LTS": "LT",
    "PAR": "PAR",
    "PARES": "PAR",
}

_RE_CANTIDAD = re.compile(r"X\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class VentaMinima:
    """Cómo se vende un producto. `multiplo` es lo que valida el carrito."""

    multiplo: int
    unidad: str
    envase: str | None
    confianza: float
    raw: str


def parse_venta_minima(raw: str | None) -> VentaMinima:
    """Interpreta la columna «Detalle Venta Min».

    >>> parse_venta_minima("X 12 UNID").multiplo
    12
    >>> parse_venta_minima("BOLSA X200UN.").envase
    'BOLSA'
    >>> parse_venta_minima("X UN").multiplo
    1
    >>> parse_venta_minima(None).confianza
    0.0

    Un valor ausente devuelve multiplo=1 con confianza 0: el sistema queda
    operativo pero la fila aparece en el reporte para revisión manual. Nunca se
    inventa un múltiplo.
    """
    original = (raw or "").strip()
    if not original:
        return VentaMinima(multiplo=1, unidad="UNID", envase=None, confianza=0.0, raw="")

    texto = _normalizar_texto(original)

    envase = next((v for k, v in _ENVASES.items() if k in texto), None)

    m = _RE_CANTIDAD.search(texto)
    if m:
        multiplo = int(m.group(1))
        confianza = 1.0
    else:
        # "X UN" sin número: venta por unidad.
        multiplo = 1
        confianza = 0.8 if "X" in texto else 0.3

    unidad = "UNID"
    for token in re.findall(r"[A-Z]+", texto):
        if token in _UNIDADES:
            unidad = _UNIDADES[token]
            break

    if multiplo <= 0:
        multiplo, confianza = 1, 0.0

    return VentaMinima(
        multiplo=multiplo, unidad=unidad, envase=envase, confianza=confianza, raw=original
    )


# --- medidas -----------------------------------------------------------------

_RE_FRACCION_PULG = re.compile(r'^(\d+)?\s*(\d+)/(\d+)\s*"?$')
_RE_NUM_UNIDAD = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(MM|CM|MT|MTS|M|LT|LTS|KG|GR|G|PULG)\.?$")


@dataclass(frozen=True, slots=True)
class Medida:
    valor: Decimal | None
    unidad: str | None
    texto: str


def parse_medida(raw: str | None) -> Medida:
    """Best-effort sobre la columna «Medida». Muchos valores no son dimensionales.

    >>> parse_medida('1/2"').unidad
    'PULG'
    >>> parse_medida("3 MM").valor
    Decimal('3')
    >>> parse_medida("75W/80").valor is None   # viscosidad, no es una medida
    True
    """
    texto = (raw or "").strip()
    if not texto:
        return Medida(valor=None, unidad=None, texto="")

    t = _normalizar_texto(texto)

    if m := _RE_FRACCION_PULG.match(t):
        entero = Decimal(m.group(1) or 0)
        valor = entero + Decimal(m.group(2)) / Decimal(m.group(3))
        return Medida(valor=valor, unidad="PULG", texto=texto)

    if t.endswith('"') and t[:-1].replace(".", "").isdigit():
        return Medida(valor=Decimal(t[:-1]), unidad="PULG", texto=texto)

    if m := _RE_NUM_UNIDAD.match(t):
        unidad = {"MTS": "MT", "M": "MT", "LTS": "LT", "G": "GR"}.get(m.group(2), m.group(2))
        return Medida(valor=Decimal(m.group(1).replace(",", ".")), unidad=unidad, texto=texto)

    # Viscosidades (75W/80), rodados (350X8), roscas: se conservan como texto.
    return Medida(valor=None, unidad=None, texto=texto)


# --- códigos y SKU -----------------------------------------------------------

# En el catálogo conviven al menos 5 familias de código de proveedor.
_RE_CODIGO_VALIDO = re.compile(r"^[A-Z0-9][A-Z0-9/\-. ]{2,29}$")


def normalizar_codigo(raw: str | None) -> str | None:
    """Limpia un código de proveedor conservando su forma reconocible.

    >>> normalizar_codigo("  pr/49573 ")
    'PR/49573'
    >>> normalizar_codigo("FERCADGAL  174")
    'FERCADGAL 174'
    >>> normalizar_codigo("—") is None
    True
    """
    if not raw:
        return None
    codigo = re.sub(r"\s+", " ", _normalizar_texto(raw)).strip()
    if not codigo or not _RE_CODIGO_VALIDO.match(codigo):
        return None
    return codigo


def generar_sku(codigo_proveedor: str) -> str:
    """SKU interno DVU, determinista a partir del código de proveedor.

    Determinista a propósito: re-extraer el catálogo no debe generar SKUs nuevos
    para los mismos productos.

    >>> generar_sku("PR/49573")
    'DVU-PR49573'
    >>> generar_sku("080633000-T")
    'DVU-080633000T'
    """
    limpio = re.sub(r"[^A-Z0-9]", "", _normalizar_texto(codigo_proveedor))
    return f"DVU-{limpio}"


# --- validación de fila ------------------------------------------------------


@dataclass(slots=True)
class FilaNormalizada:
    """Una fila del catálogo lista para cargar, con su diagnóstico de calidad."""

    codigo: str | None
    sku: str | None
    descripcion: str
    venta_minima: VentaMinima
    marca: str | None
    medida: Medida
    precio_clp: int | None
    pagina: int
    orden: int
    #: Y del ancla de precio en la página (pt). Sirve para asociar la foto de producto.
    y_centro: float = 0.0
    problemas: list[str] = field(default_factory=list)

    @property
    def confianza(self) -> float:
        """0.0–1.0. Bajo el umbral configurado, la fila va a revisión manual."""
        puntos = 0.0
        if self.codigo:
            puntos += 0.35
        if self.descripcion.strip():
            puntos += 0.30
        if self.precio_clp:
            puntos += 0.25
        puntos += 0.10 * self.venta_minima.confianza
        return round(puntos, 2)

    @property
    def cargable(self) -> bool:
        """Mínimo indispensable para existir como producto vendible."""
        return bool(self.codigo and self.descripcion.strip() and self.precio_clp)


def diagnosticar(fila: FilaNormalizada) -> list[str]:
    """Lista los problemas de una fila. Datos faltantes reales, no se inventan."""
    problemas: list[str] = []
    if not fila.codigo:
        problemas.append("sin_codigo")
    if not fila.descripcion.strip():
        problemas.append("sin_descripcion")
    if not fila.precio_clp:
        problemas.append("sin_precio")
    if fila.venta_minima.confianza == 0.0:
        problemas.append("sin_venta_minima")
    if not fila.marca:
        problemas.append("sin_marca")
    if fila.precio_clp and fila.precio_clp > 5_000_000:
        problemas.append("precio_sospechoso_alto")
    return problemas


# --- utilidades --------------------------------------------------------------


def _normalizar_texto(s: str) -> str:
    """Mayúsculas sin tildes. El catálogo mezcla «UNIÓN» y «UNION»."""
    sin_tildes = unicodedata.normalize("NFKD", s)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return sin_tildes.upper().strip()
