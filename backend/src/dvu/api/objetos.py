"""Sirve objetos del bucket por la propia API, en vez de redirigir a una URL firmada.

Una URL firmada apunta al endpoint de MinIO (`http://minio:9000`), que sólo resuelve
dentro de la red de Docker: desde un navegador en la LAN o en la VPN la imagen queda
rota. Poner un endpoint público en su lugar obligaría a fijar *una* IP, y el catálogo se
abre desde varias redes a la vez.

Devolver el contenido por la API resuelve las dos cosas de una vez: la foto viaja por la
misma conexión con la que ya se está viendo el catálogo, sea cual sea, y deja de existir
la firma de cinco minutos que vencía con la página abierta en el mesón.
"""

from __future__ import annotations

import mimetypes

from fastapi import HTTPException, Response, status

from dvu.almacenamiento import Almacen

#: Las fotos del catálogo son inmutables: la key lleva el hash del contenido, y una foto
#: subida a mano pisa la anterior bajo el mismo SKU. Un día de caché ahorra el viaje al
#: bucket en cada scroll del vendedor.
_CACHE = "public, max-age=86400"


def responder_objeto(almacen: Almacen, key: str) -> Response:
    """Baja el objeto del bucket y lo devuelve. 404 si no está."""
    contenido = almacen.leer(key)
    if contenido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="El archivo no está en el bucket")
    tipo, _ = mimetypes.guess_type(key)
    return Response(
        content=contenido,
        media_type=tipo or "application/octet-stream",
        headers={"Cache-Control": _CACHE},
    )
