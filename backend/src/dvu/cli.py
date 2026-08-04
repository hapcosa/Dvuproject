"""CLI de operación. `python -m dvu.cli --help`"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from dvu.almacenamiento import get_almacen
from dvu.carga.cartola import cartola_de_prueba
from dvu.carga.catalogo import cargar_catalogo
from dvu.carga.catalogo_impreso import exportar_catalogo_pdf
from dvu.carga.categorias import clasificar_catalogo
from dvu.carga.excel import exportar_excel
from dvu.carga.seed import SeedEnProduccion, sembrar
from dvu.conciliacion import sincronizar_y_conciliar
from dvu.config import get_settings
from dvu.db.session import get_sessionmaker
from dvu.extractor.catalogo_pdf import ResultadoExtraccion, extraer_pdf, iter_pdfs
from dvu.extractor.imagenes import asociar_a_filas, extraer_imagenes
from dvu.extractor.reporte import escribir_salidas
from dvu.integraciones.banco import ErrorBanco

app = typer.Typer(help="Herramientas del sistema DVU", no_args_is_help=True)


@app.command()
def extraer(
    con_imagenes: Annotated[
        bool, typer.Option("--con-imagenes", help="Extrae también las fotos de producto")
    ] = False,
    hasta_pagina: Annotated[
        int | None, typer.Option(help="Procesa solo las N primeras páginas de cada PDF")
    ] = None,
    catalogo_dir: Annotated[Path | None, typer.Option(help="Directorio con los PDF")] = None,
    salida: Annotated[Path | None, typer.Option(help="Directorio de salida")] = None,
) -> None:
    """Fase 0 — extrae los PDF del catálogo a JSONL + reporte de calidad."""
    cfg = get_settings()
    origen = catalogo_dir or cfg.catalogo_dir
    destino = salida or cfg.extractor_output_dir

    pdfs = list(iter_pdfs(origen))
    if not pdfs:
        typer.secho(f"No hay PDF en {origen}", fg=typer.colors.RED)
        raise typer.Exit(1)

    resultados: list[ResultadoExtraccion] = []
    for pdf in pdfs:
        typer.echo(f"Extrayendo {pdf.name} …")
        resultados.append(extraer_pdf(pdf, hasta_pagina=hasta_pagina))

    reporte = escribir_salidas(resultados, destino)

    if con_imagenes:
        _extraer_imagenes(pdfs, resultados, destino, hasta_pagina)

    typer.echo(reporte.resumen())
    typer.echo(f"  Salidas en {destino}")

    if reporte.porcentaje_cargable < 95:
        typer.secho(
            "  El criterio de salida de la Fase 0 no se cumple: revisa revision.jsonl",
            fg=typer.colors.YELLOW,
        )


def _extraer_imagenes(
    pdfs: list[Path],
    resultados: list[ResultadoExtraccion],
    destino: Path,
    hasta_pagina: int | None,
) -> None:
    dir_img = destino / "imagenes"
    asociaciones: dict[str, dict[str, str]] = {}

    for pdf, resultado in zip(pdfs, resultados, strict=True):
        typer.echo(f"Imágenes de {pdf.name} …")
        imagenes = extraer_imagenes(pdf, dir_img, hasta_pagina=hasta_pagina)

        filas_por_pagina: dict[int, list[tuple[int, float]]] = {}
        for fila in resultado.filas:
            filas_por_pagina.setdefault(fila.pagina, []).append((fila.orden, fila.y_centro))

        mapa = asociar_a_filas(imagenes, filas_por_pagina)
        asociaciones[pdf.name] = {f"{p}:{o}": key for (p, o), key in mapa.items()}
        typer.echo(f"  {len(imagenes)} imágenes ({len({i.key for i in imagenes})} únicas)")

    (destino / "imagenes.json").write_text(
        json.dumps(asociaciones, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@app.command("cargar-catalogo")
def cargar_catalogo_cmd(
    directorio: Annotated[
        Path | None, typer.Option(help="Directorio con catalogo.jsonl y fuentes.json")
    ] = None,
    desactivar_ausentes: Annotated[
        bool,
        typer.Option(
            "--desactivar-ausentes",
            help="Marca inactivo todo producto que no venga en este JSONL "
            "(sólo con el catálogo completo)",
        ),
    ] = False,
    con_imagenes: Annotated[
        bool,
        typer.Option(
            "--con-imagenes",
            help="Sube al almacén las fotos de `extraer --con-imagenes` y las asocia",
        ),
    ] = False,
) -> None:
    """Fase 0 — carga el JSONL extraído a producto / producto_alias."""
    cfg = get_settings()
    origen = directorio or cfg.extractor_output_dir

    with get_sessionmaker()() as session:
        try:
            resumen = cargar_catalogo(
                session,
                origen,
                desactivar_ausentes=desactivar_ausentes,
                almacen=get_almacen() if con_imagenes else None,
            )
        except FileNotFoundError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        session.commit()

    typer.echo(resumen.resumen())


@app.command()
def clasificar(
    reclasificar: Annotated[
        bool,
        typer.Option(
            "--reclasificar",
            help="Vuelve a clasificar TODO, incluso lo que corrigió una persona "
            "(sólo cuando cambian las reglas)",
        ),
    ] = False,
) -> None:
    """Crea el árbol de categorías y clasifica el catálogo por descripción.

    El PDF no trae categorías, así que salen de reglas explícitas
    (`dvu.domain.categorias`). Lo que ninguna regla reconoce queda **sin categoría** a
    propósito: se sigue encontrando por búsqueda de texto, que es como se busca hoy.
    """
    with get_sessionmaker()() as session:
        resumen = clasificar_catalogo(session, reclasificar=reclasificar)
        session.commit()

    typer.echo(resumen.resumen())
    if resumen.ejemplos_sin_categoria:
        typer.echo("\nSin categoría (muestra, para decidir qué regla falta):")
        for descripcion in resumen.ejemplos_sin_categoria:
            typer.echo(f"  - {descripcion}")


@app.command()
def seed() -> None:
    """Carga usuarios y clientes de ejemplo para desarrollo."""
    with get_sessionmaker()() as session:
        try:
            resumen = sembrar(session)
        except SeedEnProduccion as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        session.commit()
    typer.echo(resumen.resumen())


@app.command()
def exportar(
    salida: Annotated[Path | None, typer.Option(help="Ruta del .xlsx a escribir")] = None,
    desde: Annotated[str | None, typer.Option(help="Fecha inicial AAAA-MM-DD")] = None,
    hasta: Annotated[str | None, typer.Option(help="Fecha final AAAA-MM-DD")] = None,
) -> None:
    """Genera el Excel de ventas, detalle y pagos."""
    destino = salida or Path(f"dvu-ventas-{datetime.now(UTC):%Y%m%d}.xlsx")
    with get_sessionmaker()() as session:
        contenido = exportar_excel(
            session,
            desde=date.fromisoformat(desde) if desde else None,
            hasta=date.fromisoformat(hasta) if hasta else None,
        )
    destino.write_bytes(contenido)
    typer.echo(f"Excel escrito en {destino} ({len(contenido) // 1024} KB)")


@app.command("catalogo-pdf")
def catalogo_pdf_cmd(
    salida: Annotated[Path | None, typer.Option(help="Ruta del .pdf a escribir")] = None,
    categoria: Annotated[
        str | None, typer.Option(help="Slug de la categoría; vacío = catálogo completo")
    ] = None,
    sin_imagenes: Annotated[
        bool,
        typer.Option("--sin-imagenes", help="Lista de precios: sin fotos, pesa cien veces menos"),
    ] = False,
) -> None:
    """Exporta el catálogo a PDF con el diseño del impreso.

    Las fotos salen del almacén, donde las dejó `cargar-catalogo --con-imagenes`. Sin
    MinIO configurado se leen del respaldo en disco, y las que falten van con el guion
    de dato faltante en vez de inventarse.
    """
    sufijo = f"-{categoria}" if categoria else ""
    destino = salida or Path(f"catalogo-dvu{sufijo}-{datetime.now(UTC):%Y%m%d}.pdf")
    with get_sessionmaker()() as session:
        contenido = exportar_catalogo_pdf(
            session, get_almacen(), categoria=categoria, con_imagenes=not sin_imagenes
        )
    destino.write_bytes(contenido)
    typer.echo(f"PDF escrito en {destino} ({len(contenido) // 1024} KB)")


@app.command()
def conciliar(
    desde: Annotated[str | None, typer.Option(help="Fecha inicial AAAA-MM-DD")] = None,
    hasta: Annotated[str | None, typer.Option(help="Fecha final AAAA-MM-DD")] = None,
) -> None:
    """Trae la cartola del banco y concilia los pagos declarados.

    Sin rango usa los últimos `DVU_CONCILIACION_DIAS_ATRAS` días. Lo que no cuadra queda
    en la bandeja (`GET /conciliacion/bandeja`), no se descarta.
    """
    with get_sessionmaker()() as session:
        try:
            resumen = sincronizar_y_conciliar(
                session,
                desde=date.fromisoformat(desde) if desde else None,
                hasta=date.fromisoformat(hasta) if hasta else None,
            )
        except ErrorBanco as exc:
            typer.secho(f"No se pudo leer la cartola: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        session.commit()

    typer.echo(resumen.resumen())
    for sugerencia in resumen.sugerencias:
        typer.echo(
            f"  pago {sugerencia.pago_id} ~ {sugerencia.movimiento_id_externo} "
            f"({sugerencia.confianza:.2f}: {'; '.join(sugerencia.motivos)})"
        )


@app.command("cartola-demo")
def cartola_demo(
    salida: Annotated[Path | None, typer.Option(help="Ruta del JSONL a escribir")] = None,
) -> None:
    """Escribe una cartola de prueba que calza con los pagos del `seed`.

    Sirve para ensayar la conciliación sin credenciales del agregador ni tocar la cuenta
    real del dueño.
    """
    destino = salida or get_settings().cartola_fake_path
    with get_sessionmaker()() as session:
        lineas = cartola_de_prueba(session)

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    typer.echo(f"Cartola de prueba con {len(lineas)} movimientos en {destino}")


@app.command()
def config() -> None:
    """Muestra la configuración efectiva (sin secretos)."""
    cfg = get_settings()
    ocultos = {"secret_key", "s3_secret_key", "banco_api_key", "banco_link_token", "dte_api_key"}
    for campo in sorted(type(cfg).model_fields):
        valor = "***" if campo in ocultos else getattr(cfg, campo)
        typer.echo(f"  {campo:<32} {valor}")


if __name__ == "__main__":
    app()
