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

## El pedido desde el catálogo

`/pedido` reemplaza el mensaje de WhatsApp con el pedido: busca en el catálogo (por
texto o por categoría), arma el carrito, elige el cliente de su cartera y envía.

- Las cantidades se escriben **en envases**, no en unidades. El vendedor pone «2 cajas» y
  la página envía `2 × multiplo_venta`: la cantidad es múltiplo válido por construcción y
  no hay forma de tipear un número que el backend vaya a rechazar.
- El carrito vive en `sessionStorage` con un `client_uuid` que **no se regenera entre
  intentos** —sólo tras un envío exitoso o al vaciarlo—. Si se corta la señal al enviar,
  reintentar cae en la idempotencia del backend en vez de duplicar el pedido. Es el mismo
  contrato que después va a usar la app Flutter offline-first.
- La página **no calcula IVA**: muestra la suma neta y después los totales que devolvió
  el servidor. La regla del impuesto vive en un solo lugar y esta página no la repite.

## Cómo probarlo

```bash
make up
make migrate
make seed        # usuarios de ejemplo; nunca en producción
make clasificar  # arma el árbol de categorías sobre el catálogo ya cargado
```

Después, en <http://localhost:8000>:

1. `/` — busca «codo» o pega un código de proveedor (`PR/49573`, `KM521`), o filtrá por
   categoría en el desplegable.
2. `/pedido` — entra como `vendedor@dvu.cl` / `dvu-dev-1234`, filtra por «Gasfitería»,
   agrega dos envases de un codo, elige el cliente y envía. Probá recargar la página
   antes de enviar: el carrito sigue ahí.
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
