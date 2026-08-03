"""Motor de conciliación bancaria. Lógica pura, sin I/O.

Hoy el dueño abre la cartola del banco y compara a ojo contra los comprobantes que le
llegan por WhatsApp. Esto hace lo mismo, con reglas explícitas.

**La conciliación nunca es 100 % automática.** Este módulo no decide nada solo: separa
lo que puede aplicarse sin que nadie mire (`automaticas`) de lo que necesita ojo humano
(`sugerencias`), y deja fuera lo que no cuadra. Nada se descarta ni se borra.

Tres criterios, en orden de peso:

1. **Número de operación.** Si el vendedor anotó la referencia de la transferencia y el
   banco la entrega, es el match más fuerte que existe.
2. **RUT de la contraparte.** El banco a veces lo trae en la glosa; identifica a la
   ferretería que pagó.
3. **Monto y fecha.** El monto exacto es requisito duro; la fecha admite desfase, porque
   el vendedor declara el día que le mostraron el comprobante y el banco registra el día
   que acreditó.

Un movimiento que empata con dos pagos igual de bien **no se aplica solo**: dos
ferreterías transfiriendo la misma cifra el mismo día es un caso real, y elegir una al
azar es peor que preguntar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from dvu.domain.rut import limpiar

#: Sobre este puntaje la coincidencia se aplica sin intervención.
UMBRAL_AUTOMATICO = 0.85

#: Días de desfase tolerados entre lo que declara el vendedor y lo que registra el banco.
TOLERANCIA_DIAS = 3

#: Mínimo de dígitos para dar por buena una referencia. Menos que esto son falsos
#: positivos: cualquier glosa contiene un "123".
DIGITOS_REFERENCIA = 6

_SOLO_DIGITOS = re.compile(r"\D+")


@dataclass(frozen=True, slots=True)
class Movimiento:
    """Una línea de la cartola, ya normalizada por el agregador."""

    id_externo: str
    fecha: date
    monto_clp: int
    descripcion: str = ""
    referencia: str | None = None
    rut_contraparte: str | None = None


@dataclass(frozen=True, slots=True)
class PagoDeclarado:
    """Lo que el vendedor subió: el comprobante que hoy va por WhatsApp."""

    id: int
    cliente_rut: str
    monto_clp: int
    fecha_pago: date
    referencia: str | None = None


@dataclass(frozen=True, slots=True)
class Coincidencia:
    pago_id: int
    movimiento_id_externo: str
    confianza: float
    #: Por qué se propone. Va a la bandeja: quien revisa tiene que poder discrepar.
    motivos: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Resultado:
    automaticas: tuple[Coincidencia, ...] = field(default_factory=tuple)
    sugerencias: tuple[Coincidencia, ...] = field(default_factory=tuple)
    pagos_sin_match: tuple[int, ...] = field(default_factory=tuple)
    movimientos_sin_match: tuple[str, ...] = field(default_factory=tuple)

    def resumen(self) -> str:
        return (
            f"{len(self.automaticas)} conciliados automáticamente, "
            f"{len(self.sugerencias)} para revisar, "
            f"{len(self.pagos_sin_match)} pagos y "
            f"{len(self.movimientos_sin_match)} movimientos sin match"
        )


def conciliar(
    movimientos: list[Movimiento],
    pagos: list[PagoDeclarado],
    *,
    tolerancia_dias: int = TOLERANCIA_DIAS,
) -> Resultado:
    """Empareja cartola contra pagos declarados.

    Cada pago y cada movimiento se usan una sola vez: un abono en la cuenta no puede
    respaldar dos comprobantes distintos.
    """
    candidatas = [
        candidata
        for movimiento in movimientos
        for pago in pagos
        if (candidata := _puntuar(pago, movimiento, tolerancia_dias)) is not None
    ]

    ambiguas = _ambiguas(candidatas)

    automaticas: list[Coincidencia] = []
    sugerencias: list[Coincidencia] = []
    pagos_usados: set[int] = set()
    movimientos_usados: set[str] = set()

    # Mayor confianza primero; a igual confianza, el pago más antiguo. El orden es
    # determinista para que dos corridas sobre la misma cartola den lo mismo.
    for candidata in sorted(candidatas, key=lambda c: (-c.confianza, c.pago_id)):
        if candidata.pago_id in pagos_usados:
            continue
        if candidata.movimiento_id_externo in movimientos_usados:
            continue

        pagos_usados.add(candidata.pago_id)
        movimientos_usados.add(candidata.movimiento_id_externo)

        es_ambigua = (candidata.pago_id, candidata.movimiento_id_externo) in ambiguas
        if candidata.confianza >= UMBRAL_AUTOMATICO and not es_ambigua:
            automaticas.append(candidata)
        else:
            sugerencias.append(_con_motivo_de_ambiguedad(candidata) if es_ambigua else candidata)

    return Resultado(
        automaticas=tuple(automaticas),
        sugerencias=tuple(sugerencias),
        pagos_sin_match=tuple(p.id for p in pagos if p.id not in pagos_usados),
        movimientos_sin_match=tuple(
            m.id_externo for m in movimientos if m.id_externo not in movimientos_usados
        ),
    )


# --- puntuación --------------------------------------------------------------


def _puntuar(
    pago: PagoDeclarado, movimiento: Movimiento, tolerancia_dias: int
) -> Coincidencia | None:
    """Devuelve la coincidencia, o `None` si ni siquiera vale considerarla."""
    # El monto exacto es requisito duro: un abono por otra cifra es otro pago.
    if pago.monto_clp != movimiento.monto_clp:
        return None

    dias = abs((movimiento.fecha - pago.fecha_pago).days)
    if dias > tolerancia_dias:
        return None

    confianza = 0.5
    motivos = ["monto exacto"]

    if _referencias_coinciden(pago.referencia, movimiento):
        confianza += 0.35
        motivos.append(f"nº de operación {pago.referencia}")

    if _rut_coincide(pago.cliente_rut, movimiento):
        confianza += 0.25
        motivos.append(f"RUT {pago.cliente_rut} en la cartola")

    if dias == 0:
        confianza += 0.10
        motivos.append("misma fecha")
    else:
        confianza += 0.05
        motivos.append(f"{dias} día(s) de desfase")

    return Coincidencia(
        pago_id=pago.id,
        movimiento_id_externo=movimiento.id_externo,
        confianza=round(min(confianza, 1.0), 4),
        motivos=tuple(motivos),
    )


def _referencias_coinciden(referencia: str | None, movimiento: Movimiento) -> bool:
    """El nº de operación puede venir en el campo propio o enterrado en la glosa."""
    digitos = _digitos(referencia)
    if len(digitos) < DIGITOS_REFERENCIA:
        return False
    return digitos in _digitos(movimiento.referencia) or digitos in _digitos(movimiento.descripcion)


def _rut_coincide(cliente_rut: str, movimiento: Movimiento) -> bool:
    rut = limpiar(cliente_rut)
    if movimiento.rut_contraparte and limpiar(movimiento.rut_contraparte) == rut:
        return True
    # Sin el dígito verificador: muchas glosas traen sólo el número.
    return rut[:-1] in _digitos(movimiento.descripcion)


def _digitos(texto: str | None) -> str:
    return _SOLO_DIGITOS.sub("", texto or "")


# --- ambigüedad --------------------------------------------------------------


def _ambiguas(candidatas: list[Coincidencia]) -> set[tuple[int, str]]:
    """Pares que empatan en primer lugar con otro candidato del mismo pago o movimiento.

    Empatar es señal de que los datos no alcanzan para decidir. No se resuelve solo.
    """
    mejor_por_pago: dict[int, float] = {}
    mejor_por_movimiento: dict[str, float] = {}
    for c in candidatas:
        mejor_por_pago[c.pago_id] = max(mejor_por_pago.get(c.pago_id, 0.0), c.confianza)
        mejor_por_movimiento[c.movimiento_id_externo] = max(
            mejor_por_movimiento.get(c.movimiento_id_externo, 0.0), c.confianza
        )

    empates_pago: dict[int, int] = {}
    empates_movimiento: dict[str, int] = {}
    for c in candidatas:
        if c.confianza == mejor_por_pago[c.pago_id]:
            empates_pago[c.pago_id] = empates_pago.get(c.pago_id, 0) + 1
        if c.confianza == mejor_por_movimiento[c.movimiento_id_externo]:
            empates_movimiento[c.movimiento_id_externo] = (
                empates_movimiento.get(c.movimiento_id_externo, 0) + 1
            )

    return {
        (c.pago_id, c.movimiento_id_externo)
        for c in candidatas
        if empates_pago.get(c.pago_id, 0) > 1
        or empates_movimiento.get(c.movimiento_id_externo, 0) > 1
    }


def _con_motivo_de_ambiguedad(candidata: Coincidencia) -> Coincidencia:
    return Coincidencia(
        pago_id=candidata.pago_id,
        movimiento_id_externo=candidata.movimiento_id_externo,
        confianza=candidata.confianza,
        motivos=(*candidata.motivos, "empata con otro candidato: decide una persona"),
    )
