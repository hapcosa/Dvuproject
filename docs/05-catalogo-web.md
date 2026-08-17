# Prototipo web: catálogo y comprobantes

Qué es, qué reemplaza y qué decisiones tomó. Es un prototipo **para mostrar**: cubre el
catálogo y la carga de comprobantes. No cablea pagos en línea ni despacho.

## Qué reemplaza

Hoy DVU opera con dos artefactos que este prototipo sustituye:

| Hoy | Con el prototipo |
|---|---|
| Catálogo PDF de 150 páginas que el vendedor lleva impreso | `/` — el mismo diseño, con buscador y precios que se corrigen en el momento |
| Grupo de WhatsApp «COMPROBANTES TRANSF.» + un bot con OCR que arma un Excel | `/vendedor` para cargar, `/cobranza` para revisar y bajar el Excel |
| Alguien corrige precios en el PDF y lo reparte de nuevo | `/admin` — se edita la celda y ya está |

El bot actual (`bot dvu cabron`) lee el export del chat, pasa las fotos por OCR y saca
una planilla con estados y colores. Acá el vendedor escribe en un formulario, así que
**no hay OCR que fallar**: el dato entra estructurado o no entra. Por eso el estado
`REVISAR OCR` no existe; los demás se conservan con el mismo nombre, la misma etiqueta y
el mismo color, porque cobranza ya los lee todos los días.

| Estado | Etiqueta en el Excel | Color |
|---|---|---|
| `listo` | LISTO PARA INGRESAR | verde |
| `falta_monto` / `falta_factura` / `falta_cliente` / `falta_dato` | FALTA … | amarillo |
| `duplicado_posible` | DUPLICADO POSIBLE | rojo |
| `abono_parcial` | ABONO PARCIAL | azul |

## Decisiones

**Las páginas son clientes de la API, no plantillas con datos.** El HTML se sirve sin
autenticar y no trae ni un dato adentro; el JavaScript pide el token a `/auth/login` y
llama a los mismos endpoints que usará la app Flutter. Así hay un solo modelo de
permisos que mantener, todo lo que la web puede hacer está documentado en `/docs`, y no
hay sesión de servidor ni superficie de CSRF.

**Sin framework, sin build, sin CDN.** Es un prototipo que tiene que abrirse en el
computador de la oficina sin instalar nada. Tres archivos: una hoja de estilos, un
módulo JS de ~130 líneas y las plantillas.

**El token va en `sessionStorage`, no en `localStorage`.** Se borra al cerrar la
pestaña. En un equipo compartido de bodega esa diferencia importa.

**El comprobante no es un pago.** Son dos tablas distintas a propósito: el `Pago` es el
hecho contable que se aplica a pedidos y se cruza con la cartola; el `Comprobante` es lo
que declaró el vendedor y todavía nadie verificó. Un comprobante puede estar mal y aun
así tiene que existir en la base, y uno solo puede terminar siendo un pago, ninguno o
parte de uno. Registrar un comprobante **no** crea un pago.

**Nada se rechaza.** Un comprobante sin monto o sin factura se guarda igual, marcado con
lo que falta. El aviso del vendedor es el dato escaso: perderlo es el único error
irreparable de este flujo. La única excepción es un monto imposible (cero o negativo),
que no es un dato incompleto sino uno erróneo y ensuciaría el total que cobranza suma.

**Un RUT mal escrito no pierde el comprobante.** Se ignora y el cliente queda sin
identificar, que es exactamente lo que pasó. Rechazar el aviso completo por un dígito
verificador sería peor.

**Sin nº de operación no se marcan duplicados.** Dos ferreterías pueden transferir el
mismo monto el mismo día. Una alarma que suena todos los días deja de mirarse.

**El monto se lee con prioridades.** Primero lo marcado como plata (`$`, separador de
miles, detrás de «por» o «abono»); recién al final un número suelto, y nunca uno que ya
se leyó como factura. «pago factura 33780» queda en FALTA MONTO en vez de inventar un
monto de 33.780: un monto equivocado entra al sistema sin que nadie lo note, un FALTA
MONTO salta a la vista.

**Nada se borra.** Desactivar un producto lo saca del catálogo y conserva la ficha,
porque puede estar referenciada en pedidos históricos. Marcar un comprobante como
ingresado lo saca de la bandeja y conserva la fila.

## Diseño

El catálogo copia la hoja impresa: banda roja con degradado, `CATALOGO / FERRETERIA`
espaciado, folio arriba a la izquierda, logo blanco a la derecha, y la grilla de siete
columnas con encabezado azul y bordes rojos. El vendedor y el cliente conocen esa hoja
de memoria; reproducirla hace que la web se lea sin explicación.

Los datos que el catálogo original no trae —marca vacía, venta mínima vacía— se marcan
con `—`, nunca se inventan. Son datos faltantes reales.

## Las fotos

Van por dos vías, y hacen falta las dos:

1. **Masiva, del PDF.** `make extract` saca las fotos de la columna «Imagen» por
   posición, descarta iconos y logos por tamaño (mínimo 120×90 px y 4 KB) y deduplica
   por sha256. `make cargar-catalogo` las sube al almacén y les pone el `imagen_key` al
   producto. Cada archivo se sube **una sola vez** aunque lo compartan veinte filas: el
   catálogo trae una foto por familia de productos, no por SKU.
2. **Individual, desde `/admin`.** `POST /productos/{sku}/imagen` reemplaza la foto de un
   producto. Sin esto, arreglar un recorte malo obligaría a volver a correr la extracción
   completa, que son horas, y el producto que no está impreso jamás tendría foto.

Tres decisiones ahí:

- **Cargar los precios no puede depender de las fotos.** Extraer imágenes es mucho más
  lento que extraer texto, así que subirlas es un flag (`--con-imagenes`) y no un
  requisito. Y una foto que falta en disco no aborta la carga: esa fila queda sin imagen.
  El catálogo con una foto menos sirve; sin precios, no.
- **Una edición nueva sin fotos no borra las viejas.** Mejor la foto del catálogo
  anterior que un hueco en la grilla.
- **La foto manual acepta menos formatos que el comprobante.** El comprobante toma PDF y
  HEIC porque es evidencia que hay que conservar tal como la mandó el vendedor —el banco
  entrega PDF, el iPhone manda HEIC—. La foto de producto se pinta en un `<img>`: un PDF
  no sirve y el HEIC no lo muestra ningún navegador. La key va por SKU, así que resubir
  pisa la anterior en vez de acumular objetos huérfanos.

Las fotos se sirven por URL firmada de vida corta, igual que los comprobantes: no porque
tengan nada reservado —son las mismas que están impresas— sino porque el bucket es uno
solo y no se abre al público por comodidad. **Con el almacén en disco** (sin MinIO) esa
URL es un `file://` que el navegador no carga: para ver las fotos en la web hace falta el
almacén S3, que `make up` levanta.

## Las categorías

**El PDF no las trae.** Se verificó con pdfplumber sobre páginas reales: arriba de la
zona de datos sólo hay el folio y los títulos de columna. No hay encabezado de sección
que extraer, así que no había forma de sacar el árbol del original.

El árbol se define a mano en `dvu/domain/categorias.py` —diez categorías con sus palabras
clave— y `make clasificar` lo aplica sobre la descripción. Sobre el catálogo real:
**1.448 de 1.975 productos clasificados, 73 %**.

Decisiones que sostienen eso:

- **Lo que ninguna regla reconoce queda sin categoría.** El 27 % restante no recibe una
  categoría aproximada. Una categoría inventada es peor que ninguna: el vendedor navega
  el árbol, no encuentra lo que sabe que existe y deja de confiar en el árbol entero.
  Esos productos se siguen encontrando por texto, que es como se busca hoy.
- **La asignación a mano manda.** La clasificación automática sólo toca productos sin
  categoría. Si `make clasificar` pisara las correcciones del administrador, cada
  corrección duraría hasta la próxima corrida y nadie volvería a corregir nada.
  `--reclasificar` fuerza la pasada completa y es explícito porque destruye trabajo.
- **Sembrar no renombra.** Si el administrador le cambió el nombre a una categoría desde
  `/admin`, ese es el nombre que usa la fuerza de venta.
- **Las categorías vacías no se ofrecen** (`?con_vacias=true` las trae igual, para
  administrar). Una categoría en el menú sin productos adentro es una promesa que el
  catálogo no cumple.
- **No hay `DELETE`.** Igual que el resto del sistema: nada se borra.
- **Una categoría inexistente en el filtro devuelve vacío, no 404.** El slug llega desde
  un enlace o un marcador viejo; romper la página entera por eso no ayuda a nadie.

En `/admin` el filtro «— Sin categoría —» es exactamente la bandeja de revisión del
clasificador: la lista de lo que falta asignar a mano.

## La maqueta: portada, ofertas y contraportada

Lo que en el catálogo es arte y no tabla. Salen del PDF original (`extraer` las recorta y
`cargar-catalogo` las registra) y desde `/admin` se agregan, se cambian de lugar, se
reemplazan y se quitan.

El orden es en dos niveles, y la diferencia entre ellos es la decisión de fondo:

1. **Entre secciones no se puede mover nada.** Portada al principio, ofertas después del
   cuerpo, contraportada al final. Eso no es una preferencia sino lo que hace que un
   catálogo se lea como un catálogo, y dejarlo arrastrable sólo permitiría equivocarse.
2. **Dentro de la sección manda el administrador.** Es la columna `orden`, la que se
   arrastra en pantalla.

Hasta la migración `b7d2c4e18f30` el orden salía de `(archivo, pagina)` —de qué PDF vino
el recorte y en qué página estaba—, que es la *procedencia* y no el *lugar*. Con dos PDF
cargados eso intercalaba las dos portadas con las ofertas del primero y no había forma de
arreglarlo desde la web: mover `pagina` habría mentido sobre de dónde salió el recorte.

El criterio vive en `dvu/db/maqueta.py` y no repetido en cada consumidor. Son tres —la
pantalla de administración, el catálogo web y el exportador— y si se desincronizan el
administrador ve un orden y el PDF sale con otro.

Decisiones:

- **Se arrastra, y también hay flechas ◀ ▶.** El arrastre nativo del navegador no existe
  al tocar: sin las flechas la pantalla no se puede usar en una tablet.
- **El orden se manda entero en cada movimiento**, no «esta página subió tres lugares».
  Es lo único que no depende de qué había antes en la base, y por lo tanto lo único que
  no se desincroniza si dos pestañas mueven cosas a la vez. Los ids que ya no existen se
  ignoran en vez de fallar.
- **Cambiar de sección manda la página al final de la nueva.** Su posición anterior era
  del orden de la otra sección y ahí no significa nada.
- **Reemplazar el archivo conserva el lugar y el id.** Es el caso del diseñador que manda
  la portada corregida: si hubiera que borrar y volver a subir, habría que reacomodarla.
  Los objetos viejos **no se borran** del almacén — el PDF que se exportó ayer todavía los
  referencia.
- **Quitar no borra.** Una oferta que sale en agosto suele volver; «Reponer» la devuelve.
- **El modo edición es de la pantalla, no del servidor.** No hay estado que quede colgado
  si alguien cierra la pestaña a la mitad.

## Volver a PDF

El catálogo web se exporta a PDF con el diseño del impreso, desde los dos botones del
buscador o con `make catalogo-pdf`.

**Las fotos no hay que ir a buscarlas al PDF original.** El extractor de Fase 0 ya las
sacó —JPEG embebidos a ~300 ppi, deduplicados por sha256— y `cargar-catalogo
--con-imagenes` las dejó en el almacén con su `imagen_key`. El exportador las baja de
ahí, una sola vez por archivo aunque la compartan veinte productos de la misma familia.

La geometría de las siete columnas sale de `extractor/layout.py`: los mismos rangos de X
con los que se *lee* el original. Leer y volver a emitir usan un único juego de números,
así que recalibrar una edición nueva se hace en un solo lugar.

Lo que **no** es: una copia página por página. El original salió de CorelDRAW con
portadas y saltos armados a mano; acá el contenido es lo que hay en la base —que ya trae
las correcciones que el PDF impreso no tiene— y la paginación la decide el contenido. Lo
que se conserva es la identidad visual: la banda roja con el folio, el encabezado azul
repetido en cada página y los bordes rojos de las celdas.

Dos modos:

| Modo | Peso | Páginas | Tiempo | Para qué |
|---|---|---|---|---|
| Con fotos | ~24 MB | 142 | ~14 s | Para imprimir o dejar en el mostrador |
| Lista de precios | ~250 KB | 51 | ~4 s | Para mandar por WhatsApp |

Decisiones:

- **El filtro de la pantalla viaja al PDF.** Lo que el vendedor ve es lo que se lleva:
  «Gasfitería» son 2 páginas, no 142.
- **Filtrado salen las tapas pero no las hojas de oferta.** Las ofertas son del catálogo
  completo: pegadas detrás de una lista de gasfitería son decenas de MB de páginas que no
  responden lo que se preguntó. La portada y la contraportada sí van, porque son la
  identidad del documento.
- **Una búsqueda sin resultados responde 404, no un PDF.** Con las páginas de arte
  pegadas ese archivo pesa decenas de MB y no tiene ni una fila: el vendedor lo baja, lo
  abre y recién ahí se entera de que su búsqueda no encontró nada.
- **Lo baja el navegador, no `fetch`.** Un archivo de decenas de MB traído por `fetch`
  hay que tenerlo entero en memoria antes de poder escribir un solo byte a disco, sin
  barra de progreso y sin forma de cancelar; en un celular eso se queda pegado o se cae.
  La página pide un token corto (`POST /auth/descarga`, dos minutos, sólo lectura) y
  navega a la URL con él en la query, que es la vía por la que el navegador sabe bajar
  archivos grandes desde siempre. Va en la query porque al navegar no hay dónde poner el
  `Authorization`, y por eso es de un tipo aparte: el token de sesión **no** sirve ahí.
- **Sin fotos se saca la columna «Imagen»**, no se deja en blanco. Una columna vacía a lo
  largo de 51 páginas no informa nada y le roba ancho a la descripción.
- **Una foto faltante o corrupta no tumba el catálogo.** Se verifica al bajarla, antes de
  armar la página: reportlab abre la imagen en medio del maquetado, y un JPEG truncado se
  llevaría puestas las 142 páginas por una sola foto mala de novecientas.
- **Pide sesión, aunque el catálogo web sea público.** Generarlo entero cuesta ~14 s de
  CPU y cientos de lecturas al almacén; abierto al mundo es una palanca de denegación de
  servicio gratis. Cualquier rol sirve.
- **Los productos desactivados no se imprimen.** Un catálogo con descontinuados adentro
  genera pedidos que después no se pueden despachar.
- **El pie dice que los precios son netos.** El cliente descuenta IVA y el precio de
  lista no lo incluye; dejarlo implícito en un papel que circula es cómo nacen los
  reclamos por diferencia de monto.

## El pedido desde el catálogo

`/pedido` reemplaza el mensaje de WhatsApp con el pedido: se abre una lista para una
ferretería, se busca en el catálogo, se agrega, y al final se envía.

El supuesto que hay que tener a la vista es cómo se arma un pedido de verdad: **el
ferretero dicta**. Canta treinta productos seguidos mientras atiende el mesón, y cada
roce por producto se multiplica por treinta. La otra mitad del supuesto es que la mañana
del vendedor son cinco ferreterías, no una: el pedido se interrumpe, se retoma, y a veces
se termina en otro equipo.

### Las listas viven en el servidor

Una lista a medias es un pedido en estado `borrador` (`/pedidos/borradores`), no algo
guardado en el navegador. Antes el carrito estaba en `sessionStorage` y cerrar la pestaña
—o que se apague el celular— era perder el trabajo con el cliente al lado.

- **Un borrador no es un pedido.** No tiene folio, no aparece en `GET /pedidos`, no entra
  al Excel de ventas y no se puede facturar. `numero` es nulo hasta que se envía
  (migración `c8f1a6b34d92`): darle folio al crearlo quemaría un número de la secuencia
  por cada lista que nunca se manda, y eso se ve después como huecos en la correlatividad
  que alguien tiene que explicar.
- **Guardar es permisivo; enviar es estricto.** El borrador acepta una cantidad que no es
  múltiplo del envase y la deja marcada. Rechazar el guardado obligaría a arreglarla con
  el cliente esperando, o a perderla. Al enviar sí se valida todo.
- **Se manda la lista entera en cada guardado**, no «esta línea cambió»: es lo único que
  no depende de qué había antes, y por lo tanto lo único que no se desincroniza si la
  misma lista está abierta en el celular y en el computador.
- **Al enviar se vuelven a leer los precios del catálogo.** El precio que vale es el del
  momento en que se hace el pedido, no el de cuando se abrió la lista, que pueden ser días
  distintos. Recién ahí se congela.
- **Descartar no borra.** La lista queda `anulado` con su motivo, como todo acá.
- Las listas son **del vendedor** que las arma. Un usuario con rol `cliente` no tiene a
  quién atribuírselas —`vendedor_id` queda nulo y no hay vínculo usuario↔cliente— así que
  esa página arma una sola lista en el navegador y la manda con `POST /pedidos`.

### Bajar el costo de cada producto

- **Se busca mientras se escribe** y con <kbd>Enter</kbd> se agrega cuando queda un solo
  resultado, que es lo que pasa al tipear un código de proveedor. Un botón «Buscar» son
  dos toques más por producto.
- **La fila del buscador dice cuánto de eso ya lleva la lista.** Sumar en silencio es cómo
  se agrega dos veces el mismo codo sin notarlo.
- **Miniatura del producto**, botones `−` / `+` grandes —esto se usa de pie— y una barra
  fija abajo con el total, porque mientras se busca la lista queda fuera de pantalla y
  «¿cuánto llevamos?» es una pregunta del cliente, no del sistema.
- **«Repetir»** sobre un pedido anterior arma una lista nueva con las mismas líneas. La
  ferretería pide casi siempre lo mismo; volver a buscar treinta productos es media
  visita. Si el envase cambió de tamaño desde entonces, la línea queda marcada y el
  vendedor decide: corregirla sola sería cambiarle el pedido al cliente sin decirle.
- **El cliente se elige escribiendo**, no de un desplegable de doscientas ferreterías.

### El dinero lo calcula el servidor

- Las cantidades se escriben **en envases**, no en unidades. El vendedor pone «2 cajas» y
  la página envía `2 × multiplo_venta`: la cantidad es múltiplo válido por construcción y
  no hay forma de tipear un número que el backend vaya a rechazar.
- La página **no calcula IVA**. `POST /pedidos/cotizar` devuelve neto, IVA, total y el
  precio de hoy de cada línea sin crear nada, y se llama en cada cambio. Así el vendedor
  puede responder «¿en cuánto me queda?» antes de cerrar, la regla del impuesto sigue
  viviendo en un solo lugar, y los múltiplos malos se ven mientras se arma la lista y no
  en el 422 del envío. Cotizar **no falla** por una línea mala: la marca y sigue.
- El `client_uuid` **no se regenera entre intentos**. Si se corta la señal al enviar,
  reintentar cae en la idempotencia del backend en vez de duplicar el pedido. Es el mismo
  contrato que después va a usar la app Flutter offline-first.
- **Enviar pide confirmación** con el resumen, y después ofrece **mandarlo por WhatsApp**:
  el pedido va a seguir viajando por ahí mientras el cliente no entre a la web, y copiarlo
  a mano es el paso donde el vendedor abandona la herramienta.

### Lo que ya se envió

El folio en «Mis últimos pedidos» abre el detalle: las líneas como quedaron, los totales
y la **bitácora de estados**. Es la respuesta a «¿en qué va lo mío?», que hoy la da
alguien mirando el Excel. Los estados se muestran en palabras —«Enviado a DVU»,
«Preparándose en bodega»— y no con el nombre de la máquina de estados; las etiquetas
viven en `dvu.domain.pedido.ETIQUETAS`, no repartidas por las plantillas.

Y cuando el backend rechaza líneas —el 422 de múltiplos, que llega con **todas** las
malas de una vez— se marcan donde están, con el botón para dejar la cantidad vendible al
lado. Un párrafo de texto obliga a buscarlas a ojo en una lista de treinta.

## Cómo probarlo

```bash
make up
make migrate
make seed        # usuarios de ejemplo; nunca en producción
make clasificar  # arma el árbol de categorías sobre el catálogo ya cargado
```

Después, en <http://localhost:8000>:

1. `/` — busca «codo» o pega un código de proveedor (`PR/49573`, `KM521`), o filtrá por
   categoría en el desplegable. Con sesión iniciada, «↓ PDF» baja lo que estás viendo
   con el diseño del impreso, y «↓ Lista de precios» lo mismo sin fotos.
2. `/pedido` — entra como `vendedor@dvu.cl` / `dvu-dev-1234`, empieza una lista para una
   ferretería, escribe «codo» y agrega dos envases. Probá **cerrar la pestaña** antes de
   enviar y volver a entrar: la lista sigue en «Mis listas», porque vive en el servidor.
   Envíalo y después apretá «Repetir» sobre el pedido: arma la misma lista de nuevo.
3. `/vendedor` — escribe un mensaje como se lo mandarías al grupo: *«Ferretería El
   Martillo, abono factura 33780 por 510.459, BCI op 12345678»*. El monto y la factura
   salen solos del texto.
4. Manda uno **sin monto** a propósito: se guarda marcado FALTA MONTO.
5. `/cobranza` — entra como `admin@dvu.cl`, mira la bandeja y baja el Excel.
6. `/admin` — corrige un precio haciendo clic en la celda, cambia la foto de un producto
   con el botón «cambiar», y filtra por «— Sin categoría —» para asignar a mano lo que el
   clasificador no reconoció.

## Qué falta

- Que el árbol de categorías cubra más del 73 %: hoy 527 productos quedan sin categoría y
  sólo se encuentran por búsqueda de texto. Se cierra agregando reglas o asignando a mano
  desde `/admin`; ninguna de las dos cosas requiere código nuevo.
- Pagos en línea y despacho, que este prototipo no cablea a propósito.
- La app Flutter offline-first, que es el destino final de este flujo.
