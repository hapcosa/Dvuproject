"""Prototipo web: catálogo, edición y carga de comprobantes.

Son páginas HTML servidas por la misma app, pero **clientes de la API JSON**: no hay
sesión de servidor ni plantillas con datos incrustados. La página pide el token a
`/auth/login`, lo guarda en el navegador y desde ahí llama a los mismos endpoints que
usará la app Flutter. Así hay un solo modelo de permisos que mantener, y todo lo que la
web puede hacer está documentado en `/docs`.

Es un prototipo para mostrar: sin framework, sin build, sin dependencias de CDN.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dvu.config import get_settings

RAIZ = Path(__file__).parent
plantillas = Jinja2Templates(directory=str(RAIZ / "templates"))

router = APIRouter(tags=["web"], include_in_schema=False)

#: Los estáticos que las plantillas enlazan con `?v=`.
ESTATICOS = (RAIZ / "static" / "dvu.css", RAIZ / "static" / "dvu.js")


def version_estaticos() -> str:
    """Huella de la hoja de estilos y del JS, para colgarla de su URL.

    `StaticFiles` manda `etag` y `last-modified` pero **no** `cache-control`. Sin esa
    cabecera el navegador cachea por heurística: no revalida y sigue mostrando la versión
    vieja, así que un arreglo desplegado no se ve y no hay nada en pantalla que lo diga.
    Pasó: se desplegó el panel del carrito y en el navegador seguía el de antes.

    Con la huella en la URL, un archivo distinto es una URL distinta y el navegador la
    pide sí o sí; y mientras no cambie, la sigue cacheando como corresponde. Se mira el
    disco en cada página, que son dos `stat`: la alternativa es acordarse de vaciar la
    caché a mano, y eso ya falló.
    """
    marcas = []
    for archivo in ESTATICOS:
        try:
            info = archivo.stat()
            marcas.append(f"{info.st_mtime_ns}-{info.st_size}")
        except OSError:  # pragma: no cover - sólo si falta el archivo
            marcas.append("0")
    return sha256("|".join(marcas).encode()).hexdigest()[:12]


PAGINAS = {
    "/": ("catalogo.html", "Catálogo"),
    "/ingresar": ("ingresar.html", "Ingresar"),
    "/admin": ("admin.html", "Administrar catálogo"),
    "/pedido": ("pedido.html", "Armar pedido"),
    "/vendedor": ("vendedor.html", "Registrar comprobante"),
    "/cobranza": ("cobranza.html", "Bandeja de cobranza"),
}


def _render(request: Request, plantilla: str, titulo: str) -> HTMLResponse:
    ajustes = get_settings()
    return plantillas.TemplateResponse(
        request=request,
        name=plantilla,
        context={
            "titulo": titulo,
            "api": ajustes.api_prefix,
            # Las cuentas de ejemplo se nombran sólo fuera de producción. Son las del
            # `make seed` y con contraseña conocida: escritas en la pantalla de ingreso
            # de producción serían una invitación, aunque ahí no existan.
            "pista_dev": not ajustes.es_produccion,
            "v": version_estaticos(),
        },
    )


@router.get("/", response_class=HTMLResponse)
def catalogo(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/"])


@router.get("/ingresar", response_class=HTMLResponse)
def ingresar(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/ingresar"])


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/admin"])


@router.get("/pedido", response_class=HTMLResponse)
def pedido(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/pedido"])


@router.get("/vendedor", response_class=HTMLResponse)
def vendedor(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/vendedor"])


@router.get("/cobranza", response_class=HTMLResponse)
def cobranza(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/cobranza"])
