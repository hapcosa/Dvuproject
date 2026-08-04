"""Prototipo web: catálogo, edición y carga de comprobantes.

Son páginas HTML servidas por la misma app, pero **clientes de la API JSON**: no hay
sesión de servidor ni plantillas con datos incrustados. La página pide el token a
`/auth/login`, lo guarda en el navegador y desde ahí llama a los mismos endpoints que
usará la app Flutter. Así hay un solo modelo de permisos que mantener, y todo lo que la
web puede hacer está documentado en `/docs`.

Es un prototipo para mostrar: sin framework, sin build, sin dependencias de CDN.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dvu.config import get_settings

RAIZ = Path(__file__).parent
plantillas = Jinja2Templates(directory=str(RAIZ / "templates"))

router = APIRouter(tags=["web"], include_in_schema=False)

PAGINAS = {
    "/": ("catalogo.html", "Catálogo"),
    "/admin": ("admin.html", "Administrar catálogo"),
    "/pedido": ("pedido.html", "Armar pedido"),
    "/vendedor": ("vendedor.html", "Registrar comprobante"),
    "/cobranza": ("cobranza.html", "Bandeja de cobranza"),
}


def _render(request: Request, plantilla: str, titulo: str) -> HTMLResponse:
    return plantillas.TemplateResponse(
        request=request,
        name=plantilla,
        context={"titulo": titulo, "api": get_settings().api_prefix},
    )


@router.get("/", response_class=HTMLResponse)
def catalogo(request: Request) -> HTMLResponse:
    return _render(request, *PAGINAS["/"])


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
