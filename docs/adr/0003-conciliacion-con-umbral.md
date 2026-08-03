# ADR-0003 — Conciliación por puntaje con umbral, y bandeja humana permanente

- **Estado:** aceptado
- **Fecha:** 2026-08-03

## Contexto

Hoy el dueño de DVU verifica cada transferencia a mano: mira la cartola del banco y la
cruza con las fotos que llegan por WhatsApp. La Fase 2 automatiza ese cruce, con un
criterio de salida de **≥85 % de los pagos conciliados sin intervención humana**.

El problema es que la evidencia disponible es pobre y desigual. Un abono en la cartola
trae monto, fecha y una glosa libre; a veces el RUT de quien transfirió, a veces el nº de
operación, a menudo ninguno de los dos. El vendedor declara monto, fecha y —cuando se
acuerda— la referencia. Nada de eso es un identificador confiable por sí solo.

## Decisión

**Puntaje sobre evidencia acumulada, con un umbral de aplicación automática (0,85).**

- El **monto exacto es un requisito duro**: sin él no hay candidato, cualquiera sea el
  resto de la evidencia. Nada de "cerca es suficiente" en dinero.
- La fecha admite ±3 días, porque el banco acredita al día siguiente y a veces el
  vendedor anota la fecha en que pidió la transferencia, no en que se cursó.
- Suman: nº de operación en la glosa (+0,35), RUT de la contraparte (+0,25), misma fecha
  (+0,10) o desfase dentro de tolerancia (+0,05), sobre una base de 0,50.
- Sobre 0,85 se aplica solo. Debajo, va a la bandeja como sugerencia con sus motivos.
- **Un empate nunca se resuelve automáticamente.** Si dos candidatos puntúan igual, los
  dos bajan a sugerencia.

El motor vive en `dvu.domain.conciliacion` y es puro: sin base de datos, sin red, sin
reloj. Se puede reprocesar una cartola completa en un test y comparar resultados.

## Razones

- El costo de los dos errores es asimétrico. Dejar un pago en la bandeja cuesta un
  minuto de alguien. Dar por pagado un pedido que no se pagó despacha mercadería contra
  nada, y se descubre semanas después cuadrando el mes.
- Un umbral explícito es discutible y ajustable; una cascada de `if` anidados no.
- El puntaje con que se aceptó cada pago queda en `pago.conciliacion_confianza`.
  Sin eso no hay forma de saber después si el umbral estaba bien puesto.

## Consecuencias y mitigaciones

- **Los pesos son supuestos, no medición.** Están calibrados sobre dos creencias: que la
  glosa chilena suele traer el RUT, y que los vendedores rara vez anotan el nº de
  operación. Si en la cartola real de DVU eso no se cumple, el 85 % no se alcanza.
  Mitigación: `conciliacion_confianza` permite recalibrar contra datos reales sin
  adivinar; los pesos son constantes en un módulo puro, cambiarlos es una línea y un test.
- **La bandeja de excepciones no desaparece nunca.** No es una carencia de la
  implementación: es una regla de dominio. Un pago que no cuadra no se descarta, queda
  `pendiente_revision`.
- **Una confirmación manual no lleva puntaje** (`conciliacion_confianza` queda nula). Es
  deliberado: no la decidió un umbral, la decidió una persona, y mezclarlas contaminaría
  la medición del criterio de salida.
- **El agregador puede fallar.** Una cartola vacía por error haría parecer que ningún
  pago tiene respaldo y mandaría a revisión el trabajo de un día. Por eso `ErrorBanco`
  nunca se traduce a "no hay movimientos": la sincronización falla ruidosamente (502 en
  la API, error en el CLI, log en el worker) y se reintenta.

## Cuándo revisar esta decisión

Después de dos semanas de cartola real. Si la tasa automática queda muy bajo el 85 %,
primero recalibrar pesos con los datos observados; recién si eso no alcanza, considerar
pedirle al cliente que la transferencia incluya el nº de pedido en la glosa —que es lo
único que convertiría el matching en determinista.
