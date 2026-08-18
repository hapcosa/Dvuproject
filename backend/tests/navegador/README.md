# Tests del prototipo web

```bash
make up          # tiene que estar levantado: corren contra la API de verdad
make seed        # y con datos de ejemplo
make test-web
```

Sin stack o sin seed **se saltan solos**, no fallan: nadie debería quedarse en rojo por
no tener el stack arriba. Es el mismo criterio de `conftest.py` con Postgres.

## Por qué existen

`pytest` cubre el HTML que sale del servidor, pero el prototipo web es casi todo
JavaScript en el navegador, y ahí no llegaba nada. Los bugs que aparecieron fueron todos
del mismo tipo: **el carrito es una lista con dos vistas** —el panel lateral y la tarjeta
grande de `/pedido`— y el estado llegaba a una y no a la otra. El objeto siempre estuvo
bien; lo que fallaba era lo que se veía. Por eso estos tests miran el DOM.

Se carga **el mismo `dvu.js` que sirve el servidor**, no una copia, sobre un DOM real
(jsdom), y con **la hoja de estilos real aplicada**. Un error de sintaxis lo dicen acá y
no el navegador de la oficina.

El CSS importa tanto como el JS: `.carrito { display: flex }` le ganaba al atributo
`hidden` y dejaba el panel visible para siempre, con el JS escondiéndolo correctamente.
Cualquier test que mirara el atributo pasaba mientras en pantalla el panel seguía clavado.
Por eso `seVe()` mira el estilo calculado, y por eso **jsdom va pineado a `^30`**: la 26 no
implementa `!important` en el cascade y daría el visto bueno a esa misma pantalla rota.

## Las dos reglas

Corren contra la base del stack, así que:

1. **Nunca se envía un pedido.** Un pedido enviado no se deshace y quedaría para siempre
   en la base con la que se hacen las demostraciones. Enviar ya está cubierto por
   `pytest`, que corre contra `<base>_test`.
2. **Toda lista que se crea, se anula**, en un `finally`. `DELETE` deja la fila en
   `anulado` en vez de borrarla —el dominio guarda la historia—, así que cada corrida
   deja una fila muerta que no se ve en ninguna pantalla.

## Por qué no están en `make check`

CI no levanta la API sirviendo, y estos la necesitan. Se corren a mano al tocar el
prototipo web. `node_modules` se instala dentro del contenedor y está en `.gitignore`: el
repo se abre con Docker y nada más, sin node en la máquina.
