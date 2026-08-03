"""Comprobantes de transferencia declarados por el vendedor.

Reemplaza el flujo del grupo de WhatsApp «COMPROBANTES TRANSF.»: hoy el vendedor sube
una foto y un texto suelto, un bot los pasa por OCR y alguien revisa el Excel que sale.
Acá el vendedor escribe los datos en un formulario, así que **no hay OCR que fallar**:
el dato entra estructurado o no entra.

Esa diferencia se nota en los estados. El bot tenía `REVISAR OCR` para la foto ilegible;
acá no existe, porque no hay nada que interpretar. El resto de los estados se conserva
igual, con los mismos nombres, porque son los que el área de cobranza ya lee todos los
días y cambiarlos costaría más de lo que aportaría.

Módulo puro: sin base de datos, sin archivos, sin red.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

#: Cota inferior de un monto creíble. Bajo esto es casi seguro un número de factura o
#: un dígito verificador que se coló en el campo equivocado.
MONTO_MINIMO_CLP = 500
#: Cota superior. Una transferencia mayor existe, pero merece que alguien la mire.
MONTO_MAXIMO_CLP = 999_999_999

#: Palabras con que el vendedor avisa que la transferencia no cubre el total. Vienen
#: del vocabulario real del grupo de WhatsApp.
PALABRAS_ABONO = frozenset(
    {"abono", "abona", "saldo", "parcial", "parte", "cuota", "adelanto", "anticipo"}
)


class EstadoComprobante(StrEnum):
    """Los mismos estados del Excel de cobranza, en código."""

    LISTO = "listo"
    FALTA_MONTO = "falta_monto"
    FALTA_FACTURA = "falta_factura"
    FALTA_CLIENTE = "falta_cliente"
    FALTA_DATO = "falta_dato"
    DUPLICADO_POSIBLE = "duplicado_posible"
    ABONO_PARCIAL = "abono_parcial"


#: Etiqueta que ve la persona de cobranza. Es literalmente la del Excel actual.
ETIQUETAS: dict[EstadoComprobante, str] = {
    EstadoComprobante.LISTO: "LISTO PARA INGRESAR",
    EstadoComprobante.FALTA_MONTO: "FALTA MONTO",
    EstadoComprobante.FALTA_FACTURA: "FALTA FACTURA",
    EstadoComprobante.FALTA_CLIENTE: "FALTA CLIENTE",
    EstadoComprobante.FALTA_DATO: "FALTA DATO",
    EstadoComprobante.DUPLICADO_POSIBLE: "DUPLICADO POSIBLE",
    EstadoComprobante.ABONO_PARCIAL: "ABONO PARCIAL",
}

#: Color con que se pinta la fila, en el Excel y en la web. Mismo criterio que el bot:
#: verde pasa, amarillo falta algo, rojo pare, azul es parcial.
COLORES: dict[EstadoComprobante, str] = {
    EstadoComprobante.LISTO: "00C853",
    EstadoComprobante.FALTA_MONTO: "FFEB3B",
    EstadoComprobante.FALTA_FACTURA: "FFEB3B",
    EstadoComprobante.FALTA_CLIENTE: "FFEB3B",
    EstadoComprobante.FALTA_DATO: "FFEB3B",
    EstadoComprobante.DUPLICADO_POSIBLE: "EF5350",
    EstadoComprobante.ABONO_PARCIAL: "42A5F5",
}


@dataclass(frozen=True, slots=True)
class DatosComprobante:
    """Lo que el vendedor declaró. Todo opcional salvo el texto libre: el punto es
    poder registrar lo incompleto, no rechazarlo."""

    cliente: str = ""
    facturas: tuple[str, ...] = ()
    monto_clp: int | None = None
    numero_operacion: str | None = None
    fecha_transferencia: date | None = None
    detalle: str = ""


@dataclass(frozen=True, slots=True)
class Clasificacion:
    estado: EstadoComprobante
    #: Qué le falta, en palabras. Va tal cual a la columna «Observación» del Excel.
    observacion: str = ""
    faltantes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def etiqueta(self) -> str:
        return ETIQUETAS[self.estado]

    @property
    def color(self) -> str:
        return COLORES[self.estado]


def clasificar(datos: DatosComprobante) -> Clasificacion:
    """Asigna el estado con que la persona de cobranza va a triarlo.

    No inventa nada: si un dato no está, lo dice. Un comprobante incompleto se guarda
    igual —perder el registro sería peor— pero sale marcado.
    """
    faltantes: list[str] = []
    if datos.monto_clp is None:
        faltantes.append("monto")
    if not datos.cliente.strip():
        faltantes.append("cliente")
    if not datos.facturas:
        faltantes.append("factura")

    observacion = "Faltan: " + ", ".join(faltantes) if faltantes else ""
    faltan = tuple(faltantes)

    # El abono manda sobre lo que falte: es una decisión comercial (la transferencia no
    # cubre el total), no un error de carga. Igual se arrastra qué falta.
    if es_abono(datos.detalle):
        return Clasificacion(EstadoComprobante.ABONO_PARCIAL, observacion, faltan)

    if not faltantes:
        return Clasificacion(EstadoComprobante.LISTO)

    if faltan == ("monto",):
        return Clasificacion(EstadoComprobante.FALTA_MONTO, "", faltan)
    if faltan == ("cliente",):
        return Clasificacion(EstadoComprobante.FALTA_CLIENTE, "", faltan)
    if faltan == ("factura",):
        return Clasificacion(EstadoComprobante.FALTA_FACTURA, "", faltan)

    return Clasificacion(EstadoComprobante.FALTA_DATO, observacion, faltan)


@dataclass(frozen=True, slots=True)
class ClaveDuplicado:
    """Lo que identifica una transferencia sin ambigüedad."""

    numero_operacion: str
    monto_clp: int
    fecha_transferencia: date | None


def clave_duplicado(datos: DatosComprobante) -> ClaveDuplicado | None:
    """Devuelve la clave, o `None` si no hay con qué comparar.

    Sin nº de operación no se compara: dos ferreterías pueden transferir el mismo monto
    el mismo día y no son el mismo pago. Marcarlas como duplicado obligaría a cobranza a
    desmarcarlas a mano todos los días, y una alarma que siempre suena se ignora.
    """
    if not datos.numero_operacion or datos.monto_clp is None:
        return None
    return ClaveDuplicado(
        numero_operacion=datos.numero_operacion.strip(),
        monto_clp=datos.monto_clp,
        fecha_transferencia=datos.fecha_transferencia,
    )


# --- parseo de lo que el vendedor escribe ------------------------------------

#: `$510.459`, `510.459`, `510 459`, `68282`. El punto es separador de miles en Chile,
#: nunca decimal: el peso no tiene centavos.
_RE_MONTO = re.compile(r"\d{1,3}(?:[.\s]\d{3}){1,3}|\d{3,9}")
#: Marcado como plata sin lugar a duda: lleva `$` o separador de miles.
_RE_MONTO_MARCADO = re.compile(r"\$\s*(\d{1,3}(?:[.\s]\d{3})+|\d{3,9})|(\d{1,3}(?:[.\s]\d{3})+)")
#: Marcado por la palabra que lo antecede, como lo escribe el vendedor.
_RE_MONTO_PALABRA = re.compile(
    r"\b(?:por|monto|abono|abona|transfer\w*|pag\w*|deposit\w*)\s*\$?\s*(\d{3,9})", re.IGNORECASE
)

#: Número suelto de 4 a 8 dígitos. La coma no cuenta como frontera prohibida: en
#: «33135, 33134» separa dos facturas. El punto sí, porque separa miles.
_RE_FACTURA = re.compile(r"(?<!\d)(?<!\d\.)\d{4,8}(?!\.?\d)")
#: Monto con separador de miles: `510.459`, `1 250 000`.
_RE_AGRUPADO = re.compile(r"\d{1,3}(?:[.\s]\d{3})+")
#: RUT, con o sin puntos. Sus dígitos no son un número de factura.
_RE_RUT = re.compile(r"\d{1,3}(?:\.?\d{3}){1,2}\s*-\s*[\dkK]")
#: «op 12345678», «operación: 998877». El nº de transferencia tampoco es una factura.
_RE_OPERACION = re.compile(
    r"\b(?:op|ope|operaci[oó]n|nro|n[°º])\.?\s*[:#]?\s*\d{4,12}", re.IGNORECASE
)
#: Número precedido de una palabra de plata: es el monto, no un documento.
_RE_MONTO_ESCRITO = re.compile(
    r"(?:\$|\bpor\b|\babono\b|\bmonto\b|\btransfer\w*|\bpag\w*)\s*\$?\s*\d{4,9}", re.IGNORECASE
)
#: Dónde dice el vendedor que empieza la lista de facturas.
_RE_ETIQUETA_FACTURA = re.compile(
    r"\b(?:facturas?|fact|fc|fra|boletas?)\b\.?\s*(?:n[°º]?\.?)?\s*", re.IGNORECASE
)
#: Dónde termina esa lista: a partir de acá los números ya son otra cosa.
_RE_CORTE = re.compile(
    r"\b(?:op|ope|operaci[oó]n|nro|por|monto|banco|rut|cliente|transfer\w*|abono)\b|\$",
    re.IGNORECASE,
)


def parsear_facturas(texto: str) -> tuple[str, ...]:
    """`"33135 y 33134"` → `("33135", "33134")`. Conserva el orden y no repite.

    En el mismo mensaje viajan monto, nº de operación y RUT, todos números de largo
    parecido. Cuando el vendedor escribe «factura» se leen sólo los números que van
    detrás; si no lo escribe, se leen los sueltos que no parezcan monto. Una factura
    inventada manda a cobranza a buscar un documento que no existe: eso cuesta más que
    no tener el dato.
    """
    limpio = _RE_OPERACION.sub(" ", _RE_RUT.sub(" ", _RE_AGRUPADO.sub(" ", texto or "")))

    tramos = [
        _RE_CORTE.split(limpio[coincidencia.end() :], maxsplit=1)[0]
        for coincidencia in _RE_ETIQUETA_FACTURA.finditer(limpio)
    ]
    if not tramos:
        tramos = [_RE_MONTO_ESCRITO.sub(" ", limpio)]

    vistos: dict[str, None] = {}
    for tramo in tramos:
        for numero in _RE_FACTURA.findall(tramo):
            vistos.setdefault(numero.lstrip("0") or numero, None)
    return tuple(vistos)


def parsear_monto(texto: str) -> int | None:
    """`"$ 510.459"` → `510459`. Devuelve `None` si no hay un monto creíble.

    Prefiere no dar un monto a dar uno malo: un monto equivocado se ingresa al sistema
    de cobranza sin que nadie lo note, mientras que un `FALTA MONTO` salta a la vista.
    Por eso mira primero lo que viene marcado como plata —con `$`, con separador de
    miles o detrás de «por», «abono»— y sólo al final un número suelto.
    """
    if not texto:
        return None
    for bruto in _candidatos_monto(texto):
        valor = int(re.sub(r"[.\s]", "", bruto))
        if MONTO_MINIMO_CLP <= valor <= MONTO_MAXIMO_CLP:
            return valor
    return None


def _candidatos_monto(texto: str) -> Iterator[str]:
    """Números que podrían ser el monto, del más marcado al menos marcado."""
    for coincidencia in _RE_MONTO_MARCADO.finditer(texto):
        yield next(grupo for grupo in coincidencia.groups() if grupo)
    for coincidencia in _RE_MONTO_PALABRA.finditer(texto):
        yield coincidencia.group(1)

    # Último recurso: un número suelto. Nunca uno que ya se leyó como factura —
    # «pago factura 33780» no dice cuánto se transfirió, y ese es el caso que hay que
    # dejar en FALTA MONTO en vez de inventarle un monto de 33.780.
    facturas = set(parsear_facturas(texto))
    for bruto in _RE_MONTO.findall(texto.replace("$", " ")):
        limpio = re.sub(r"[.\s]", "", bruto)
        if limpio.lstrip("0") not in facturas:
            yield limpio


def es_abono(texto: str) -> bool:
    """True si el vendedor avisó que la transferencia no cubre el total."""
    palabras = set(re.findall(r"[a-záéíóúñ]+", (texto or "").lower()))
    return bool(palabras & PALABRAS_ABONO)
