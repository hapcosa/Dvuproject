"""Reporte de calidad de la extracción.

La Fase 0 tiene un criterio de salida medible (≥95% de filas cargables). Este módulo
es lo que permite verificarlo y, sobre todo, listar explícitamente el resto para
revisión manual en vez de dejarlo desaparecer en silencio.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from dvu.domain.catalogo import FilaNormalizada
from dvu.extractor.catalogo_pdf import ResultadoExtraccion


@dataclass(slots=True)
class Reporte:
    total_filas: int
    cargables: int
    para_revision: int
    codigos_unicos: int
    codigos_duplicados: list[str]
    problemas: dict[str, int]
    precio_min_clp: int | None
    precio_max_clp: int | None
    por_archivo: dict[str, int]

    @property
    def porcentaje_cargable(self) -> float:
        return round(100 * self.cargables / self.total_filas, 2) if self.total_filas else 0.0

    def resumen(self) -> str:
        lineas = [
            "",
            "═══ Extracción de catálogo — reporte de calidad ═══",
            f"  Filas detectadas   : {self.total_filas}",
            f"  Cargables          : {self.cargables}  ({self.porcentaje_cargable}%)",
            f"  Para revisión      : {self.para_revision}",
            f"  Códigos únicos     : {self.codigos_unicos}",
            f"  Códigos duplicados : {len(self.codigos_duplicados)}",
            f"  Rango de precios   : {self.precio_min_clp} — {self.precio_max_clp} CLP",
            "",
            "  Filas por archivo:",
        ]
        lineas += [f"    {a}: {n}" for a, n in self.por_archivo.items()]
        lineas += ["", "  Problemas detectados:"]
        lineas += [f"    {p:<26} {n}" for p, n in sorted(self.problemas.items(), key=_por_conteo)]

        criterio = "CUMPLE" if self.porcentaje_cargable >= 95 else "NO CUMPLE"
        lineas += ["", f"  Criterio de salida Fase 0 (≥95% cargable): {criterio}", ""]
        return "\n".join(lineas)


def _por_conteo(item: tuple[str, int]) -> tuple[int, str]:
    return (-item[1], item[0])


def construir_reporte(resultados: list[ResultadoExtraccion]) -> Reporte:
    filas = [f for r in resultados for f in r.filas]
    codigos = [f.codigo for f in filas if f.codigo]
    conteo = Counter(codigos)
    precios = [f.precio_clp for f in filas if f.precio_clp]

    problemas: Counter[str] = Counter()
    for f in filas:
        problemas.update(f.problemas)

    return Reporte(
        total_filas=len(filas),
        cargables=sum(1 for f in filas if f.cargable),
        para_revision=sum(1 for f in filas if not f.cargable),
        codigos_unicos=len(conteo),
        codigos_duplicados=sorted(c for c, n in conteo.items() if n > 1),
        problemas=dict(problemas),
        precio_min_clp=min(precios) if precios else None,
        precio_max_clp=max(precios) if precios else None,
        por_archivo={r.archivo: len(r.filas) for r in resultados},
    )


def escribir_salidas(resultados: list[ResultadoExtraccion], destino: Path) -> Reporte:
    """Deja en `destino`:

    - `catalogo.jsonl`      — todas las filas cargables, una por línea
    - `revision.jsonl`      — las que necesitan intervención humana
    - `reporte.json`        — métricas de calidad
    - `fuentes.json`        — sha256 y páginas de cada PDF, para trazar la carga
    """
    destino.mkdir(parents=True, exist_ok=True)
    reporte = construir_reporte(resultados)

    cargables = destino / "catalogo.jsonl"
    revision = destino / "revision.jsonl"

    with cargables.open("w", encoding="utf-8") as fc, revision.open("w", encoding="utf-8") as fr:
        for resultado in resultados:
            for fila in resultado.filas:
                registro = _serializar(fila, resultado.archivo)
                destino_fh = fc if fila.cargable else fr
                destino_fh.write(json.dumps(registro, ensure_ascii=False) + "\n")

    (destino / "reporte.json").write_text(
        json.dumps(asdict(reporte), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destino / "fuentes.json").write_text(
        json.dumps(
            [
                {
                    "archivo": r.archivo,
                    "sha256": r.sha256,
                    "paginas": r.paginas,
                    "filas_detectadas": len(r.filas),
                    "filas_cargables": len(r.cargables),
                }
                for r in resultados
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return reporte


def _serializar(fila: FilaNormalizada, archivo: str) -> dict[str, object]:
    return {
        "archivo": archivo,
        "pagina": fila.pagina,
        "orden": fila.orden,
        "codigo": fila.codigo,
        "sku": fila.sku,
        "descripcion": fila.descripcion,
        "marca": fila.marca,
        "precio_clp": fila.precio_clp,
        "venta_minima": {
            "multiplo": fila.venta_minima.multiplo,
            "unidad": fila.venta_minima.unidad,
            "envase": fila.venta_minima.envase,
            "raw": fila.venta_minima.raw,
        },
        "medida": {
            "texto": fila.medida.texto,
            "valor": str(fila.medida.valor) if fila.medida.valor is not None else None,
            "unidad": fila.medida.unidad,
        },
        "confianza": fila.confianza,
        "problemas": fila.problemas,
    }
