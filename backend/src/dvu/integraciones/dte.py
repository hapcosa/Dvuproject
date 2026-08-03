"""Emisión de documentos tributarios electrónicos al SII.

DVU no se conecta al SII directamente: firmar el XML con el certificado digital,
timbrar contra el CAF y hacer el envío es trabajo de un proveedor certificado
(LibreDTE, SimpleAPI, Facturación.cl). Este módulo lo esconde detrás de un `Protocol`.

El folio lo asigna el proveedor, porque sale del CAF que el SII le entregó al
contribuyente. El `fake` lo simula con un contador que el servicio alimenta desde la
base, para que la unicidad `(tipo, folio)` se sostenga también en desarrollo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from dvu.config import get_settings
from dvu.domain.dte import DocumentoDte, TipoDte


class ErrorDte(Exception):
    """El documento no se pudo emitir.

    Nunca se traga: un pedido que se cree facturado sin folio en el SII es una venta
    que no existe para el fisco.
    """


@dataclass(frozen=True, slots=True)
class Emision:
    """Lo que devuelve el SII (vía proveedor) cuando acepta el envío."""

    folio: int
    #: Identificador del envío. Con él se consulta después si quedó aceptado.
    track_id: str | None
    estado: str
    glosa: str | None = None
    xml: bytes | None = None


class ProveedorDte(Protocol):
    nombre: str

    def emitir(self, documento: DocumentoDte) -> Emision: ...

    def consultar(self, track_id: str) -> Emision | None: ...


class DteFake:
    """Emisor de mentira para desarrollo y tests.

    Devuelve `emitido`, nunca `aceptado`: el SII real demora en responder y el sistema
    tiene que saber convivir con ese limbo. Fingir aceptación inmediata escondería el
    caso que más importa.
    """

    nombre = "fake"

    def __init__(self, siguiente_folio: Callable[[TipoDte], int] | None = None) -> None:
        self._siguiente = siguiente_folio or self._contador_en_memoria
        self._contadores: dict[TipoDte, int] = {}
        self._emitidos: dict[str, Emision] = {}

    def emitir(self, documento: DocumentoDte) -> Emision:
        folio = self._siguiente(documento.tipo)
        emision = Emision(
            folio=folio,
            track_id=f"fake-{documento.tipo.value}-{folio}",
            estado="emitido",
            glosa="Emitido por el proveedor fake; no existe en el SII",
            xml=_xml_minimo(documento, folio),
        )
        if emision.track_id is not None:
            self._emitidos[emision.track_id] = emision
        return emision

    def consultar(self, track_id: str) -> Emision | None:
        return self._emitidos.get(track_id)

    def _contador_en_memoria(self, tipo: TipoDte) -> int:
        self._contadores[tipo] = self._contadores.get(tipo, 0) + 1
        return self._contadores[tipo]


class DteApi:
    """Proveedor real, hablado por HTTP.

    Se apunta al ambiente de **certificación** por defecto: emitir en producción es
    irreversible —un folio quemado sólo se corrige con nota de crédito—, así que
    producción se elige a mano en `DVU_DTE_AMBIENTE`.
    """

    nombre = "api"
    BASES: ClassVar[dict[str, str]] = {
        "certificacion": "https://api.certificacion.dte.cl/v1",
        "produccion": "https://api.dte.cl/v1",
    }

    def __init__(self) -> None:
        cfg = get_settings()
        if not cfg.dte_api_key:
            raise ErrorDte("Falta DVU_DTE_API_KEY")
        self._api_key = cfg.dte_api_key
        self._base = self.BASES[cfg.dte_ambiente]

    def emitir(self, documento: DocumentoDte) -> Emision:
        datos = self._post("/documentos", _a_payload(documento))
        return Emision(
            folio=int(datos["folio"]),
            track_id=_texto(datos.get("track_id")),
            estado=str(datos.get("estado") or "emitido"),
            glosa=_texto(datos.get("glosa")),
        )

    def consultar(self, track_id: str) -> Emision | None:
        import httpx

        try:
            respuesta = httpx.get(
                f"{self._base}/documentos/{track_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30,
            )
            if respuesta.status_code == 404:
                return None
            respuesta.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErrorDte(f"No se pudo consultar {track_id}: {exc}") from exc

        datos = respuesta.json()
        return Emision(
            folio=int(datos["folio"]),
            track_id=track_id,
            estado=str(datos.get("estado") or "emitido"),
            glosa=_texto(datos.get("glosa")),
        )

    def _post(self, ruta: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        try:
            respuesta = httpx.post(
                f"{self._base}{ruta}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=60,
            )
            respuesta.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErrorDte(f"El SII rechazó el envío o no respondió: {exc}") from exc

        datos: dict[str, Any] = respuesta.json()
        if "folio" not in datos:
            raise ErrorDte(f"Respuesta sin folio: {datos}")
        return datos


def get_dte(siguiente_folio: Callable[[TipoDte], int] | None = None) -> ProveedorDte:
    if get_settings().dte_proveedor == "fake":
        return DteFake(siguiente_folio)
    return DteApi()


# --- serialización -----------------------------------------------------------


def _a_payload(documento: DocumentoDte) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tipo": documento.tipo.value,
        "emisor": {
            "rut": documento.emisor.rut,
            "razon_social": documento.emisor.razon_social,
            "giro": documento.emisor.giro,
            "direccion": documento.emisor.direccion,
            "comuna": documento.emisor.comuna,
        },
        "receptor": {
            "rut": documento.receptor.rut,
            "razon_social": documento.receptor.razon_social,
            "giro": documento.receptor.giro,
            "direccion": documento.receptor.direccion,
            "comuna": documento.receptor.comuna,
        },
        "detalle": [
            {
                "codigo": linea.sku,
                "nombre": linea.descripcion,
                "cantidad": linea.cantidad,
                "precio": linea.precio_unitario_clp,
                "monto": linea.total_clp,
            }
            for linea in documento.lineas
        ],
        "neto": documento.neto_clp,
        "iva": documento.iva_clp,
        "total": documento.total_clp,
    }
    if documento.referencia_folio is not None and documento.referencia_tipo is not None:
        payload["referencias"] = [
            {
                "tipo": documento.referencia_tipo.value,
                "folio": documento.referencia_folio,
                "razon": documento.motivo,
            }
        ]
    return payload


def _xml_minimo(documento: DocumentoDte, folio: int) -> bytes:
    """XML de juguete. Sirve para que el flujo de guardado en MinIO sea el mismo que en
    producción; no pretende ser un DTE válido."""
    lineas = "".join(
        f"<Detalle><Codigo>{linea.sku}</Codigo><Monto>{linea.total_clp}</Monto></Detalle>"
        for linea in documento.lineas
    )
    return (
        f"<DTE><Encabezado><TipoDTE>{documento.tipo.value}</TipoDTE><Folio>{folio}</Folio>"
        f"<RUTEmisor>{documento.emisor.rut}</RUTEmisor>"
        f"<RUTRecep>{documento.receptor.rut}</RUTRecep>"
        f"<MntTotal>{documento.total_clp}</MntTotal></Encabezado>{lineas}</DTE>"
    ).encode()


def _texto(valor: object) -> str | None:
    texto = str(valor).strip() if valor is not None else ""
    return texto or None
