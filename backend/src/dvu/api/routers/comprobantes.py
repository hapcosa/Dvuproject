"""Comprobantes de transferencia declarados por el vendedor.

Es el reemplazo directo del grupo «COMPROBANTES TRANSF.» de WhatsApp: el vendedor manda
lo mismo que mandaba, pero a un formulario, y queda en la base en vez de en un chat.

Nada se rechaza. Un comprobante al que le falta el monto o la factura se guarda igual,
marcado con lo que le falta: el aviso del vendedor es el dato escaso.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from dvu.almacenamiento import (
    Almacen,
    ArchivoDemasiadoGrande,
    TipoNoPermitido,
    get_almacen,
    validar_comprobante,
)
from dvu.api.deps import SessionDep, UsuarioDep, exige_rol
from dvu.carga.excel_comprobantes import exportar_comprobantes
from dvu.comprobantes import Entrada, marcar_ingresado, registrar
from dvu.db.models import Comprobante, Usuario
from dvu.domain.comprobante import ETIQUETAS, EstadoComprobante, parsear_facturas, parsear_monto

router = APIRouter(prefix="/comprobantes", tags=["comprobantes"])

AlmacenDep = Annotated[Almacen, Depends(get_almacen)]

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ComprobanteEntrada(BaseModel):
    """Todo opcional salvo el detalle: se registra lo que haya."""

    cliente_texto: str = ""
    cliente_rut: str | None = None
    #: Si vienen vacías se intentan leer del detalle: el vendedor suele escribir
    #: "factura 33780" en el texto y no repetirlo en un campo aparte.
    facturas: list[str] = Field(default_factory=list)
    monto_clp: int | None = Field(default=None, gt=0)
    banco: str | None = None
    rut_contraparte: str | None = None
    numero_operacion: str | None = None
    fecha_transferencia: date | None = None
    detalle: str = ""
    #: Idempotencia offline-first. La app lo genera local y reintenta con el mismo.
    client_uuid: uuid_lib.UUID | None = None


class ComprobanteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_lib.UUID
    creado_en: datetime
    vendedor: str
    cliente: str
    cliente_rut: str | None
    facturas: list[str]
    monto_clp: int | None
    banco: str | None
    rut_contraparte: str | None
    numero_operacion: str | None
    fecha_transferencia: date | None
    detalle: str
    tiene_imagen: bool
    estado: str
    estado_etiqueta: str
    observacion: str
    ingresado: bool


class PaginaComprobantes(BaseModel):
    total: int
    items: list[ComprobanteOut]


@router.post("", response_model=ComprobanteOut, status_code=status.HTTP_201_CREATED)
def crear(
    entrada: ComprobanteEntrada,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("vendedor"))],
) -> ComprobanteOut:
    """Registra el aviso del vendedor. Nunca falla por datos incompletos."""
    comprobante = registrar(session, _a_entrada(entrada, usuario.id))
    return _a_salida(comprobante)


@router.post("/{uuid}/imagen", response_model=ComprobanteOut)
def subir_imagen(
    uuid: uuid_lib.UUID,
    session: SessionDep,
    usuario: UsuarioDep,
    almacen: AlmacenDep,
    archivo: Annotated[UploadFile, File(description="Foto o PDF del comprobante")],
) -> ComprobanteOut:
    """Adjunta la foto de la transferencia.

    Va aparte del alta porque en terreno la foto y el texto no siempre salen juntos: el
    vendedor avisa primero y adjunta cuando le carga la imagen.
    """
    comprobante = _cargar(session, uuid)
    if usuario.rol != "admin" and comprobante.vendedor_id != usuario.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No es tu comprobante")

    try:
        extension = validar_comprobante(archivo.content_type, archivo.size)
    except (TipoNoPermitido, ArchivoDemasiadoGrande) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    key = f"comprobantes/vendedor/{comprobante.uuid}.{extension}"
    comprobante.imagen_key = almacen.guardar(
        key, archivo.file, archivo.content_type or "application/octet-stream"
    )
    session.flush()
    return _a_salida(comprobante)


@router.get("/{uuid}/imagen")
def ver_imagen(
    uuid: uuid_lib.UUID, session: SessionDep, usuario: UsuarioDep, almacen: AlmacenDep
) -> RedirectResponse:
    """Redirige a una URL firmada de vida corta.

    La imagen trae datos bancarios del cliente: no se sirve desde una ruta pública ni se
    devuelve el contenido por esta vía.
    """
    comprobante = _cargar(session, uuid)
    if comprobante.imagen_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="El comprobante no tiene imagen")
    return RedirectResponse(almacen.url_firmada(comprobante.imagen_key), status_code=307)


@router.get("", response_model=PaginaComprobantes)
def listar(
    session: SessionDep,
    usuario: UsuarioDep,
    estado: Annotated[EstadoComprobante | None, Query()] = None,
    pendientes: Annotated[bool, Query(description="Sólo los que cobranza no ingresó")] = False,
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginaComprobantes:
    """La bandeja de cobranza. El vendedor sólo ve los suyos."""
    consulta = _filtrar(
        select(Comprobante), usuario, estado=estado, pendientes=pendientes, desde=desde, hasta=hasta
    )
    total = session.scalar(select(func.count()).select_from(consulta.subquery())) or 0
    filas = session.scalars(
        consulta.options(selectinload(Comprobante.vendedor), selectinload(Comprobante.cliente))
        .order_by(Comprobante.creado_en.desc())
        .limit(limite)
        .offset(offset)
    ).all()
    return PaginaComprobantes(total=total, items=[_a_salida(c) for c in filas])


@router.post("/{uuid}/ingresado", response_model=ComprobanteOut)
def marcar(
    uuid: uuid_lib.UUID,
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("admin"))],
) -> ComprobanteOut:
    """Sale de la bandeja porque cobranza ya lo ingresó. La fila no se borra."""
    comprobante = _cargar(session, uuid)
    return _a_salida(marcar_ingresado(session, comprobante, usuario.id))


@router.get("/reporte.xlsx")
def reporte_xlsx(
    session: SessionDep,
    usuario: Annotated[Usuario, Depends(exige_rol("admin"))],
    desde: Annotated[date | None, Query()] = None,
    hasta: Annotated[date | None, Query()] = None,
    incluir_ingresados: Annotated[bool, Query()] = True,
) -> Response:
    """El Excel de cobranza: mismas columnas y colores que el que hoy sale del bot."""
    contenido = exportar_comprobantes(
        session, desde=desde, hasta=hasta, incluir_ingresados=incluir_ingresados
    )
    nombre = f"dvu-comprobantes-{date.today():%Y%m%d}.xlsx"
    return Response(
        content=contenido,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --- apoyo -------------------------------------------------------------------


def _a_entrada(entrada: ComprobanteEntrada, vendedor_id: int) -> Entrada:
    """Rellena desde el texto libre lo que el vendedor no puso en un campo.

    En terreno se escribe «abono factura 33780 por 510.459» de corrido. Leerlo del
    detalle evita que el comprobante quede marcado por un dato que sí estaba.
    """
    facturas = tuple(entrada.facturas) or parsear_facturas(entrada.detalle)
    monto = entrada.monto_clp if entrada.monto_clp is not None else parsear_monto(entrada.detalle)
    return Entrada(
        vendedor_id=vendedor_id,
        cliente_texto=entrada.cliente_texto,
        cliente_rut=entrada.cliente_rut,
        facturas=facturas,
        monto_clp=monto,
        banco=entrada.banco,
        rut_contraparte=entrada.rut_contraparte,
        numero_operacion=entrada.numero_operacion,
        fecha_transferencia=entrada.fecha_transferencia,
        detalle=entrada.detalle,
        client_uuid=entrada.client_uuid,
    )


def _filtrar(
    consulta: Select[tuple[Comprobante]],
    usuario: Usuario,
    *,
    estado: EstadoComprobante | None,
    pendientes: bool,
    desde: date | None,
    hasta: date | None,
) -> Select[tuple[Comprobante]]:
    if usuario.rol == "vendedor":
        consulta = consulta.where(Comprobante.vendedor_id == usuario.id)
    if estado is not None:
        consulta = consulta.where(Comprobante.estado == estado.value)
    if pendientes:
        consulta = consulta.where(Comprobante.ingresado.is_(False))
    if desde is not None:
        consulta = consulta.where(Comprobante.creado_en >= desde)
    if hasta is not None:
        # `hasta` es inclusivo para quien lo escribe: pedir "hasta el 3" tiene que traer
        # lo del día 3, y `creado_en` es un timestamp, no una fecha.
        consulta = consulta.where(Comprobante.creado_en < hasta + timedelta(days=1))
    return consulta


def _cargar(session: Session, uuid: uuid_lib.UUID) -> Comprobante:
    comprobante = session.scalar(
        select(Comprobante)
        .options(selectinload(Comprobante.vendedor), selectinload(Comprobante.cliente))
        .where(Comprobante.uuid == uuid)
    )
    if comprobante is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No existe el comprobante {uuid}")
    return comprobante


def _a_salida(comprobante: Comprobante) -> ComprobanteOut:
    cliente = comprobante.cliente
    return ComprobanteOut(
        uuid=comprobante.uuid,
        creado_en=comprobante.creado_en,
        vendedor=comprobante.vendedor.nombre,
        cliente=cliente.razon_social if cliente is not None else comprobante.cliente_texto,
        cliente_rut=cliente.rut if cliente is not None else None,
        facturas=list(comprobante.facturas),
        monto_clp=int(comprobante.monto_clp) if comprobante.monto_clp is not None else None,
        banco=comprobante.banco,
        rut_contraparte=comprobante.rut_contraparte,
        numero_operacion=comprobante.numero_operacion,
        fecha_transferencia=comprobante.fecha_transferencia,
        detalle=comprobante.detalle,
        tiene_imagen=comprobante.imagen_key is not None,
        estado=comprobante.estado,
        estado_etiqueta=ETIQUETAS.get(EstadoComprobante(comprobante.estado), comprobante.estado),
        observacion=comprobante.observacion,
        ingresado=comprobante.ingresado,
    )
