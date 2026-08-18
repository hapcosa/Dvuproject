"""Almacén de objetos: comprobantes de pago e imágenes de producto.

Detrás de un `Protocol`, como el resto de las integraciones: sin credenciales S3 el
sistema usa el respaldo en disco y sigue funcionando completo. Eso permite levantar
el stack y correr los tests sin MinIO.

Los comprobantes son fotos de transferencias que el vendedor saca con el teléfono:
contienen datos bancarios del cliente. Nunca se sirven públicamente — se entregan por
URL firmada de vida corta.
"""

from __future__ import annotations

import shutil
import uuid as uuid_lib
from pathlib import Path
from typing import BinaryIO, Protocol

from dvu.config import get_settings

#: Lo que el vendedor puede subir como comprobante: foto o PDF del banco.
TIPOS_PERMITIDOS = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}
)
EXTENSIONES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "application/pdf": "pdf",
}
TAMANO_MAXIMO_BYTES = 10 * 1024 * 1024

#: Foto de producto del catálogo. Más estrecho que el comprobante a propósito: esto se
#: pinta en un `<img>` del catálogo, así que un PDF no sirve y HEIC no lo muestra ningún
#: navegador —lo manda el iPhone tal cual, y quedaría un hueco en la grilla—.
TIPOS_IMAGEN_PRODUCTO = frozenset({"image/jpeg", "image/png", "image/webp"})


class TipoNoPermitido(Exception):
    pass


class ArchivoDemasiadoGrande(Exception):
    pass


class Almacen(Protocol):
    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str: ...

    def url_firmada(self, key: str, *, segundos: int = 300) -> str: ...

    def leer(self, key: str) -> bytes | None: ...


class AlmacenS3:
    """MinIO en desarrollo, S3 (o compatible) en producción."""

    def __init__(self) -> None:
        import boto3

        cfg = get_settings()
        self._bucket = cfg.s3_bucket
        self._cliente = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.s3_access_key,
            aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region,
            use_ssl=cfg.s3_secure,
        )

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        self._cliente.upload_fileobj(
            datos, self._bucket, key, ExtraArgs={"ContentType": content_type}
        )
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        url: str = self._cliente.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=segundos,
        )
        return url

    def leer(self, key: str) -> bytes | None:
        """Baja el objeto. `None` si no está: una foto faltante no rompe el catálogo."""
        from botocore.exceptions import ClientError

        try:
            respuesta = self._cliente.get_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return None
        contenido: bytes = respuesta["Body"].read()
        return contenido


class AlmacenLocal:
    """Respaldo en disco para desarrollo y tests. No firma nada: devuelve la ruta."""

    def __init__(self, raiz: Path | None = None) -> None:
        self._raiz = raiz or get_settings().extractor_output_dir.parent / "objetos"

    def guardar(self, key: str, datos: BinaryIO, content_type: str) -> str:
        destino = self._raiz / key
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("wb") as fh:
            shutil.copyfileobj(datos, fh)
        return key

    def url_firmada(self, key: str, *, segundos: int = 300) -> str:
        return f"file://{self._raiz / key}"

    def leer(self, key: str) -> bytes | None:
        ruta = self._raiz / key
        return ruta.read_bytes() if ruta.is_file() else None


def get_almacen() -> Almacen:
    """Sin credenciales S3, disco. Es la misma política que el resto de integraciones."""
    cfg = get_settings()
    if cfg.s3_access_key and cfg.s3_secret_key:
        return AlmacenS3()
    return AlmacenLocal()


def validar_comprobante(content_type: str | None, tamano: int | None) -> str:
    """Devuelve la extensión a usar. Lanza si el archivo no sirve como comprobante."""
    if content_type not in TIPOS_PERMITIDOS:
        raise TipoNoPermitido(
            f"Tipo '{content_type}' no permitido; se aceptan: "
            + ", ".join(sorted(TIPOS_PERMITIDOS))
        )
    if tamano is not None and tamano > TAMANO_MAXIMO_BYTES:
        raise ArchivoDemasiadoGrande(
            f"El comprobante pesa {tamano} bytes; el máximo es {TAMANO_MAXIMO_BYTES}"
        )
    return EXTENSIONES[content_type]


def validar_imagen_producto(content_type: str | None, tamano: int | None) -> str:
    """Devuelve la extensión a usar. Lanza si el archivo no sirve como foto de catálogo."""
    if content_type not in TIPOS_IMAGEN_PRODUCTO:
        raise TipoNoPermitido(
            f"Tipo '{content_type}' no permitido para una foto de producto; se aceptan: "
            + ", ".join(sorted(TIPOS_IMAGEN_PRODUCTO))
        )
    if tamano is not None and tamano > TAMANO_MAXIMO_BYTES:
        raise ArchivoDemasiadoGrande(
            f"La imagen pesa {tamano} bytes; el máximo es {TAMANO_MAXIMO_BYTES}"
        )
    return EXTENSIONES[content_type]


def key_comprobante(pago_uuid: uuid_lib.UUID, extension: str) -> str:
    """Los comprobantes van bajo su propio prefijo: no se sirven junto al catálogo."""
    return f"comprobantes/{pago_uuid}.{extension}"


def key_logo_marca(slug: str, extension: str) -> str:
    """Logo subido a mano para una marca.

    Prefijo propio y no `catalogo/marcas/`, que es donde el extractor deja sus recortes
    con nombre de hash: así se ve de un vistazo cuál logo salió del PDF y cuál lo puso
    una persona, y resubir uno no puede pisar el recorte de otra marca.
    """
    return f"marcas/{slug}.{extension}"


def key_imagen_producto(sku: str, extension: str) -> str:
    """Foto subida a mano, bajo el mismo prefijo que las que salen del PDF.

    Va por SKU y no por hash: resubir la foto de un producto pisa la anterior en vez de
    ir dejando objetos huérfanos en el bucket (salvo que cambie el formato, caso en que
    el archivo viejo queda ahí sin que nadie lo referencie). Las del extractor sí van
    por hash, porque allá un mismo archivo sirve a varias filas.
    """
    return f"catalogo/{sku}.{extension}"
