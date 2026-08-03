"""CLI de operación. `python -m dvu.cli --help`"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from dvu.carga.catalogo import cargar_catalogo
from dvu.carga.excel import exportar_excel
from dvu.carga.seed import SeedEnProduccion, sembrar
from dvu.config import get_settings
from dvu.db.session import get_sessionmaker
from dvu.extractor.catalogo_pdf import ResultadoExtraccion, extraer_pdf, iter_pdfs
from dvu.extractor.imagenes import asociar_a_filas, extraer_imagenes
from dvu.extractor.reporte import escribir_salidas

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
) -> None:
    """Fase 0 — carga el JSONL extraído a producto / producto_alias."""
    cfg = get_settings()
    origen = directorio or cfg.extractor_output_dir

    with get_sessionmaker()() as session:
        try:
            resumen = cargar_catalogo(session, origen, desactivar_ausentes=desactivar_ausentes)
        except FileNotFoundError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        session.commit()

    typer.echo(resumen.resumen())


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


@app.command()
def config() -> None:
    """Muestra la configuración efectiva (sin secretos)."""
    cfg = get_settings()
    ocultos = {"secret_key", "s3_secret_key"}
    for campo in sorted(type(cfg).model_fields):
        valor = "***" if campo in ocultos else getattr(cfg, campo)
        typer.echo(f"  {campo:<32} {valor}")


if __name__ == "__main__":
    app()
