# Qué necesitamos de DVU

> Documento para los dueños de Comercial DVU SpA. Versión 1 · 3 de agosto de 2026
>
> Escrito sin jerga técnica a propósito. La contraparte técnica está en
> [`03-puesta-en-marcha.md`](03-puesta-en-marcha.md).

## En una frase

El sistema está construido y funcionando, pero **hoy funciona con datos de prueba**. Para
que empiece a operar de verdad necesitamos cinco respuestas, cuatro accesos y una
decisión sobre cómo trabajan los vendedores. Nada de esto lo puede resolver el equipo
técnico solo.

---

## Parte 1 — Cinco preguntas que bloquean decisiones

Estas están abiertas desde el inicio del proyecto. Mientras no haya respuesta, el sistema
asume algo (lo indicamos), y si el supuesto es equivocado hay que rehacer trabajo.

### 1. ¿Dónde vive hoy la lista de precios y el stock?

¿En un Excel? ¿En el archivo de CorelDRAW del catálogo? ¿En un ERP o un sistema de punto
de venta que no conocemos?

**Por qué importa:** define si el sistema tiene que conectarse a algo existente o si pasa
a ser *él* la fuente de verdad de precios y stock.

**Supuesto actual:** no hay otro sistema; DVU pasa a llevar los precios aquí.

**Además, hoy el sistema no maneja stock.** El catálogo no lo trae. Si un cliente pide 50
cajas y hay 3, el sistema acepta el pedido igual. Eso es aceptable mientras haya alguien
revisando antes de despachar, pero no lo es para el ecommerce: una web que vende lo que no
existe genera más reclamos que ventas. Necesitamos saber si existe un inventario en alguna
parte.

### 2. ¿Los precios del catálogo son netos o con IVA incluido?

**Por qué importa:** el sistema calcula el IVA al facturar. Si los precios ya lo traían
incluido, **todas las facturas saldrían con un 19 % de más**.

**Supuesto actual:** son **netos** (el IVA se suma después).

Es la pregunta más barata de responder y la más cara de equivocar. Confírmenla mirando una
factura reciente contra el precio del catálogo.

### 3. ¿Hay precios distintos por cliente?

¿Todas las ferreterías pagan lo mismo, o hay descuentos por volumen, por antigüedad o
negociados uno a uno? ¿Esos acuerdos están escritos en alguna parte o los lleva el
vendedor en la cabeza?

**Por qué importa:** si hay listas distintas, el modelo de precios cambia (y es un cambio
de fondo, mejor hacerlo antes de cargar todo).

**Supuesto actual:** una sola lista para todos.

### 4. ¿Cuál es el volumen real?

- ¿Cuántos vendedores en terreno?
- ¿Cuántas ferreterías compran al mes?
- ¿Cuántos pedidos por día en promedio, y en el mes más cargado del año?

**Por qué importa:** define el tamaño del servidor y cuánto va a costar operar esto al
mes. Con los números reales podemos dar un costo mensual concreto en vez de un rango.

### 5. ¿DVU ya emite factura electrónica?

Y si sí: **¿con qué proveedor y quién tiene la clave?**

**Por qué importa:** es la diferencia entre conectarnos a algo que ya existe (rápido) y
tramitarlo desde cero ante el SII (semanas, ver Parte 2).

---

## Parte 2 — Cuatro accesos que hay que conseguir

Ninguno lo puede pedir el equipo técnico: todos requieren la firma o la clave del titular
de la empresa.

### A. Facturación electrónica ante el SII — **el más lento, empiecen por acá**

DVU vende a otras ferreterías. Son empresas y descuentan IVA, así que **necesitan factura
electrónica tipo 33 sí o sí** — la boleta no les sirve. Y la mercadería no puede salir en
un camión sin guía de despacho electrónica.

Lo que hace falta, en orden:

1. **Firma electrónica avanzada** (certificado digital) a nombre del representante legal.
   Se compra a un proveedor autorizado; tiene un costo anual bajo y se tramita en días.
2. **Estar autorizado por el SII como emisor electrónico**, y tener folios vigentes (el
   SII entrega rangos de números de factura; se piden en sii.cl).
3. **Elegir un proveedor de facturación** que haga el trámite técnico con el SII —
   SimpleAPI, LibreDTE u OpenFactura son los que evaluamos. Cobran mensual o por
   documento.
4. Que ese proveedor entregue una **clave de acceso (API key)** de su ambiente de pruebas
   y luego del de producción.

> Si DVU **ya factura electrónicamente**, casi todo esto está hecho y solo hace falta el
> punto 4. Por eso la pregunta 5 de la Parte 1 es urgente.
>
> Los plazos y requisitos exactos hay que confirmarlos con el proveedor que se elija y con
> el contador de DVU; el marco general es este, pero el trámite lo tramita quien tiene la
> firma.

**Mientras tanto:** el sistema emite facturas de mentira, con folios inventados que no
existen para el SII. Sirven para probar el flujo completo, no para vender.

### B. Acceso a la cartola del banco — **el que más tiempo le ahorra al dueño**

Hoy alguien revisa a mano la cartola para verificar cada transferencia. Eso es lo que este
sistema reemplaza, y es probablemente la mayor ganancia de tiempo del proyecto completo.

Para eso necesita **leer** los movimientos de la cuenta donde entran las transferencias de
los clientes. Se hace con un servicio intermediario (evaluamos **Fintoc**), que se conecta
al banco con autorización del titular.

**Tres cosas que conviene decir claramente, porque la pregunta va a salir:**

- El acceso es **solo de lectura**. El sistema no puede transferir, ni pagar, ni mover un
  peso. Solo mirar los abonos.
- Hay que **autorizarlo explícitamente** con las credenciales del banco, y se puede
  revocar cuando se quiera.
- Es un tercero. Si eso incomoda —es una incomodidad legítima, no una paranoia—, hay un
  **plan B que funciona igual de bien**: descargar la cartola del banco en un archivo
  (CSV u OFX) y subirla al sistema. El mismo motor de conciliación la procesa. Son dos
  minutos al día en vez de horas, y el banco no se conecta a nada.

> Es una decisión del dueño, no técnica. Las dos opciones están soportadas. Si hay dudas,
> **empiecen por el plan B**: se prueba el beneficio sin conectar nada.

Si se opta por el agregador: crear cuenta, conectar la cuenta bancaria, y entregar al
equipo técnico la clave de acceso y el identificador de la cuenta.

### C. WhatsApp Business API (opcional, se puede dejar para después)

Para avisarle al cliente automáticamente que su pedido fue confirmado, despachado o
entregado. Requiere una cuenta de WhatsApp Business verificada por Meta y un número
telefónico dedicado a esto. **Ojo:** ese número queda tomado por el sistema; no se puede
usar el WhatsApp personal del vendedor.

Es lo menos urgente de la lista. El sistema funciona perfectamente sin esto.

### D. Pago en línea (opcional)

Que el cliente pague desde el sistema en vez de transferir por su cuenta. Tiene un
beneficio que no es obvio: **un pago hecho desde el sistema ya viene identificado**, así
que se concilia solo, sin adivinar de quién era la transferencia.

Requiere contratar Webpay (Transbank), Khipu o similar, que cobran comisión por
transacción. Es el único punto del plan de la Fase 2 que aún no está construido.

---

## Parte 3 — La decisión que no es técnica: cómo trabajan los vendedores

Esta es la parte que decide si el proyecto sirve o no, y **no depende del software**.

El sistema reemplaza el pedido por WhatsApp por una app en el teléfono del vendedor. Eso
funciona sólo si los vendedores efectivamente la usan. Si el pedido sigue llegando por
WhatsApp, el sistema queda a medias: la mitad de las ventas adentro, la mitad afuera, y el
Excel vuelve a ser necesario. Sería peor que no haber empezado.

Lo que necesitamos de los dueños:

1. **Elegir un vendedor para partir.** El más dispuesto, no el que más vende. Que sea
   voluntario.
2. **Que ese vendedor tome el 100 % de sus pedidos por la app durante dos semanas
   seguidas**, sin WhatsApp. Esa es la prueba de que funciona.
3. **Que los dueños respalden eso explícitamente.** Si el vendedor sabe que el pedido por
   WhatsApp se acepta igual, va a usar WhatsApp — es lo que ya conoce.

A cambio, dos compromisos del lado del sistema:

- **La app tiene que ser más rápida que WhatsApp para tomar un pedido.** Si agrega
  fricción, el vendedor tiene razón en volver a WhatsApp. Esto lo vamos a medir, no
  suponer.
- **La app funciona sin señal.** Se usa en bodegas y obras. El pedido se toma igual y se
  sincroniza cuando hay internet. Ya está resuelto.

### Sobre el Excel

**No lo vamos a quitar.** El sistema lo **genera solo**, con el mismo formato que ya se
usa, cuando se quiera. Es a propósito: nadie tiene que dejar de golpe la herramienta con
la que trabaja hace años. El Excel deja de escribirse a mano; no deja de existir.

---

## Parte 4 — Qué cambia en el día a día

| Hoy | Con el sistema |
|---|---|
| El vendedor manda el pedido por WhatsApp | Lo toma en la app; llega estructurado y sin interpretar |
| Alguien revisa la cartola a mano para ver quién pagó | El sistema cruza los pagos solo; **una persona revisa sólo lo que no cuadró** |
| Alguien transcribe ventas y pagos al Excel | El Excel se genera solo |
| La factura se hace aparte | Sale del pedido, con un clic |
| "¿Dónde está mi pedido?" se responde llamando | El estado está en el sistema (y se puede avisar por WhatsApp) |
| El catálogo se imprime y queda desactualizado | Precios actualizados en el momento |

### Una cosa que va a ser distinta y conviene saberla desde ya

**La conciliación de pagos nunca va a ser 100 % automática, y eso está bien.**

El sistema cruza los pagos que puede identificar con certeza. Los que no —porque el
cliente no puso referencia, porque transfirió un monto que no calza con ningún pedido, o
porque dos ferreterías transfirieron exactamente lo mismo el mismo día— quedan en una
**bandeja de revisión** para que una persona decida.

Esto es deliberado. Preferimos mandar un pago a la bandeja antes que darlo por pagado sin
estar seguros: dar por pagado un pedido que no se pagó significa despachar mercadería
gratis. La meta es que **al menos 85 %** se concilie solo, y que alguien revise el resto
en unos minutos al día — comparado con las horas de hoy.

**Y ningún pago se descarta nunca.** Lo que no calza queda esperando, no se pierde.

---

## Parte 5 — Lo que pedimos, en orden

Si hay que elegir por dónde partir, este es el orden por urgencia:

| # | Qué | Quién | Urgencia |
|---|---|---|---|
| 1 | Responder: ¿los precios son netos o con IVA? | Dueño / contador | **Hoy.** Es un minuto y equivocarse cuesta un 19 % en cada factura |
| 2 | Responder: ¿DVU ya factura electrónicamente y con quién? | Dueño / contador | **Hoy.** Define si el trámite del SII toma días o semanas |
| 3 | Decidir: ¿agregador bancario o cartola descargada a mano? | Dueño | Esta semana |
| 4 | Elegir el vendedor piloto y comprometer las dos semanas | Dueños | Esta semana |
| 5 | Responder: precios por cliente, volumen, dónde vive el stock | Dueño | Este mes |
| 6 | Conseguir firma electrónica y proveedor de facturación | Dueño | Este mes (es el trámite más lento) |
| 7 | WhatsApp Business y pago en línea | Dueño | Cuando lo anterior esté andando |

---

## Lo que ya está hecho y no requiere nada de DVU

Para que quede claro qué se está esperando y qué no:

- El catálogo de 150 páginas ya se convirtió en base de datos: **97 % de las filas
  cargadas automáticamente**, y las 57 restantes están listadas una por una para revisión
  manual — no se inventó ningún dato.
- El sistema de pedidos respeta la **venta por múltiplos** (cajas de 12, bolsas de 200).
  Un pedido de 5 unidades cuando la venta mínima es 12 se rechaza al momento, con la
  cantidad correcta sugerida.
- Los **RUT se validan** con dígito verificador al ingresarlos.
- Las **facturas, guías y notas de crédito** están construidas y probadas contra un
  emisor de prueba. Falta conectar el proveedor real.
- El motor de **conciliación bancaria** está construido y probado. Falta la cartola real
  para calibrarlo.
- Un pedido **no puede despacharse sin guía de despacho emitida**. El sistema lo impide.
- Todo está respaldado por **214 pruebas automáticas** que corren en cada cambio.
