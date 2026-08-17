/* Cliente mínimo de la API DVU.
 *
 * Sin framework y sin build: el prototipo tiene que abrirse en el navegador de
 * cualquiera sin instalar nada. Todo lo que hace pasa por los mismos endpoints
 * documentados en /docs — la web no tiene atajos propios contra la base.
 *
 * El token va en sessionStorage y no en localStorage: se borra al cerrar la pestaña.
 * En un equipo compartido de bodega esa diferencia importa.
 *
 * Se guarda también el refresh token y el 401 se reintenta una vez renovando. Sin eso el
 * access token dura una hora y la pestaña que quedó abierta desde la mañana falla en la
 * primera acción de la tarde con «la sesión venció», que es justo cuando el vendedor
 * está frente al cliente.
 */

const DVU = (() => {
  const base = document.querySelector('meta[name="dvu-api"]').content;
  const CLAVE = "dvu_token";
  const CLAVE_REFRESH = "dvu_refresh";

  //: Los dos endpoints que **entregan** sesión. Un 401 acá significa «esas credenciales
  //: no sirven», no «la tuya venció»: son estados opuestos y confundirlos le decía «La
  //: sesión venció, ingresa de nuevo» a quien nunca había entrado y sólo tecleó mal la
  //: contraseña —el consejo, además, era exactamente lo que estaba intentando hacer—.
  const ENTREGAN_SESION = ["/auth/login", "/auth/refresh"];

  //: Qué rol abre qué página. Vive acá y no en cada plantilla porque se usa en dos
  //: lados —el aviso de «esta página no es para tu cuenta» y los atajos que ofrece— y
  //: dos copias se desincronizan. `admin` entra a todas, igual que en `exige_rol`.
  const PAGINAS = [
    { ruta: "/", nombre: "Catálogo", roles: null },
    { ruta: "/pedido", nombre: "Armar pedido", roles: ["vendedor", "cliente"] },
    { ruta: "/vendedor", nombre: "Comprobantes", roles: ["vendedor"] },
    { ruta: "/cobranza", nombre: "Cobranza", roles: ["admin"] },
    { ruta: "/admin", nombre: "Administrar catálogo", roles: ["admin"] },
  ];

  const alcanza = (rol, roles) => !roles || rol === "admin" || roles.includes(rol);

  const token = () => sessionStorage.getItem(CLAVE);

  //: Una sola renovación en vuelo. Si tres requests vencen a la vez —pasa al volver de
  //: dejar el computador— tres refresh en paralelo se pisan y dos quedan inválidos.
  let renovacion = null;

  function renovar() {
    const refresh = sessionStorage.getItem(CLAVE_REFRESH);
    if (!refresh) return Promise.resolve(false);
    renovacion ||= fetch(base + "/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((datos) => {
        if (!datos) return false;
        guardarSesion(datos);
        return true;
      })
      .catch(() => false)
      .finally(() => { renovacion = null; });
    return renovacion;
  }

  function guardarSesion(datos) {
    sessionStorage.setItem(CLAVE, datos.access_token);
    if (datos.refresh_token) sessionStorage.setItem(CLAVE_REFRESH, datos.refresh_token);
  }

  //: Quién entró, para poder decir «tu cuenta es vendedor» cuando el servidor responde
  //: 403. El servidor sabe qué rol hace falta y el navegador sabe cuál tiene: el mensaje
  //: entendible necesita las dos mitades.
  let quienEntro = null;

  function cerrarSesion() {
    sessionStorage.removeItem(CLAVE);
    sessionStorage.removeItem(CLAVE_REFRESH);
    quienEntro = null;
  }

  /** Un error de una línea, en palabras. Llegan de dos formas: la validación de FastAPI
   *  (`loc`/`msg`) y las reglas del dominio, que hablan de un SKU (`sku`/`error`). */
  const explicar = (e) =>
    e?.error ? `${e.sku}: ${e.error}` : `${e?.loc?.slice(1).join(".")}: ${e?.msg}`;

  async function pedir(ruta, opciones = {}, reintentando = false) {
    const cabeceras = { ...(opciones.headers || {}) };
    // El cuerpo se arma aparte y no se pisa `opciones`: si hay que reintentar tras
    // renovar, un `JSON.stringify` sobre lo ya serializado mandaría una cadena escapada.
    let cuerpo = opciones.body;
    if (cuerpo && !(cuerpo instanceof FormData)) {
      cabeceras["Content-Type"] = "application/json";
      cuerpo = JSON.stringify(cuerpo);
    }
    if (token()) cabeceras.Authorization = `Bearer ${token()}`;

    const respuesta = await fetch(base + ruta, { ...opciones, body: cuerpo, headers: cabeceras });

    // Un 401 sólo es «se venció» si mandamos una credencial y el servidor la rechazó.
    // Si no mandamos ninguna, o si el que contesta es el endpoint que las entrega, el
    // 401 es su respuesta y su `detail` dice lo que de verdad pasó: se deja caer al
    // manejo de abajo, que lo muestra tal cual.
    const entregaSesion = ENTREGAN_SESION.some((r) => ruta.startsWith(r));
    if (respuesta.status === 401 && cabeceras.Authorization && !entregaSesion) {
      if (!reintentando && (await renovar())) return pedir(ruta, opciones, true);
      cerrarSesion();
      throw Object.assign(new Error("La sesión venció. Ingresa de nuevo."), {
        estado: 401,
        vencida: true,
      });
    }
    if (!respuesta.ok) {
      // El detalle del backend es el mensaje útil (qué falta, qué chocó). Se muestra
      // tal cual en vez de un "error 422" que no le dice nada a nadie.
      let detalle = `Error ${respuesta.status}`;
      let crudo = null;
      try {
        const cuerpo = await respuesta.json();
        crudo = cuerpo.detail;
        if (typeof crudo === "string") detalle = crudo;
        else if (Array.isArray(crudo)) detalle = crudo.map(explicar).join(" · ");
      } catch { /* respuesta sin JSON: queda el código */ }

      // Un 403 no es un error de datos: es «tu cuenta no puede hacer esto». Dicho así
      // —con el rol que tienes, no sólo el que falta— se entiende sin adivinar.
      if (respuesta.status === 403) {
        const tuyo = quienEntro ? ` Tu cuenta es «${quienEntro.rol}».` : "";
        detalle = `No puedes hacer esto: ${detalle.replace(/^Se requiere/, "se requiere")}.${tuyo}`;
      }

      // El detalle crudo viaja en el error para que quien llama pueda hacer algo más
      // que mostrarlo: marcar las líneas malas del pedido, por ejemplo.
      const error = new Error(detalle);
      error.estado = respuesta.status;
      error.detalle = crudo;
      error.sinPermiso = respuesta.status === 403;
      throw error;
    }
    if (respuesta.status === 204) return null;
    const tipo = respuesta.headers.get("content-type") || "";
    return tipo.includes("json") ? respuesta.json() : respuesta.blob();
  }

  async function ingresar(email, password) {
    const datos = await pedir("/auth/login", { method: "POST", body: { email, password } });
    guardarSesion(datos);
    return yo();
  }

  async function yo() {
    quienEntro = await pedir("/auth/yo");
    return quienEntro;
  }

  const salir = () => {
    cerrarSesion();
    location.href = "/";
  };

  /** Descarga chica: el archivo entra en memoria sin problema (un .xlsx son cientos de KB). */
  async function descargar(ruta, nombre) {
    const blob = await pedir(ruta);
    const url = URL.createObjectURL(blob);
    const enlace = Object.assign(document.createElement("a"), { href: url, download: nombre });
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    URL.revokeObjectURL(url);
  }

  /** Descarga grande: la baja el navegador, no nosotros.
   *
   *  El catálogo con fotos pesa decenas de MB. Por `fetch` hay que esperar a tenerlo
   *  entero en memoria antes de poder escribir un solo byte a disco, sin barra de
   *  progreso y sin forma de cancelar: en un celular eso se queda pegado o se cae. Acá se
   *  pide un token corto (`POST /auth/descarga`) y se navega a la URL, que es la vía por
   *  la que el navegador sabe bajar archivos grandes desde siempre.
   *
   *  El token va en la query porque al navegar no hay dónde poner el `Authorization`.
   *  Dura dos minutos y sólo sirve para leer; ver `dvu.api.deps.usuario_descargando`. */
  async function descargarGrande(ruta) {
    const permiso = await pedir("/auth/descarga", { method: "POST" });
    const url = `${base}${ruta}${ruta.includes("?") ? "&" : "?"}token=` +
      encodeURIComponent(permiso.token);
    // `download` en vez de `location.href`: si el servidor respondiera un error en vez
    // del archivo, navegar dejaría al usuario mirando un JSON en lugar del catálogo.
    const enlace = Object.assign(document.createElement("a"), { href: url, download: "" });
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
  }

  /* --- utilidades de presentación --------------------------------------- */

  // CLP sin decimales, siempre. El peso no tiene centavos.
  const pesos = (n) =>
    n === null || n === undefined ? "" : "$ " + Number(n).toLocaleString("es-CL");

  const escapar = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );

  /** Dato que el catálogo original no trae: se marca, no se inventa. */
  const oVacio = (valor) =>
    valor ? escapar(valor) : '<span class="vacio">—</span>';

  function avisar(elemento, mensaje, tipo = "info") {
    elemento.className = `aviso ${tipo}`;
    elemento.textContent = mensaje;
    elemento.hidden = false;
  }

  /** Deja pasar `ms` de calma antes de ejecutar; cada llamada nueva reinicia la espera.
   *
   *  Es lo que hace que buscar mientras se escribe no sea una petición por tecla, y que
   *  apretar «+» cinco veces seguidas guarde una vez y no cinco. Devuelve una función
   *  con `.ahora(...)` para cuando no se puede esperar —al enviar, por ejemplo—. */
  function aplazar(fn, ms) {
    let reloj = null;
    const aplazada = (...args) => {
      clearTimeout(reloj);
      reloj = setTimeout(() => fn(...args), ms);
    };
    aplazada.ahora = (...args) => {
      clearTimeout(reloj);
      return fn(...args);
    };
    aplazada.cancelar = () => clearTimeout(reloj);
    return aplazada;
  }

  /** Abre WhatsApp con el texto listo para elegir a quién mandárselo.
   *
   *  El pedido hoy viaja por WhatsApp y va a seguir viajando un tiempo: el vendedor le
   *  manda el resumen al ferretero para que confirme. Sin esto, copiaría a mano. */
  const porWhatsapp = (texto) =>
    window.open(`https://wa.me/?text=${encodeURIComponent(texto)}`, "_blank", "noopener");

  /* --- sesión ------------------------------------------------------------- */

  /** La URL de la página de ingreso, con dónde volver.
   *
   *  `volver` se usa después como destino de una navegación, así que sólo se acepta una
   *  ruta de este sitio: una URL absoluta ahí es un redirect abierto, y el correo de
   *  «entra al sistema» es exactamente el lugar donde eso se aprovecha. */
  function urlDeIngreso(destino = location.pathname + location.search) {
    return `/ingresar?volver=${encodeURIComponent(destino)}`;
  }

  const rutaLocal = (v) => (v && v.startsWith("/") && !v.startsWith("//") ? v : "/");

  /** Deja la sesión visible en la barra de arriba, en toda página.
   *
   *  Se pinta siempre, también sin entrar: saber que se puede entrar —y por dónde— es
   *  parte de la página. Antes el hueco quedaba vacío y la única puerta era el formulario
   *  enterrado dentro de la página privada a la que ya no podías llegar. */
  function pintarSesion(usuario) {
    const sesion = document.getElementById("sesion");
    if (!sesion) return;

    if (!usuario) {
      const aca = location.pathname === "/ingresar";
      sesion.innerHTML = aca
        ? '<span class="anonimo">Sin sesión</span>'
        : `<a class="entrar" href="${escapar(urlDeIngreso())}">Ingresar</a>`;
      return;
    }
    sesion.innerHTML =
      `<span class="quien">${escapar(usuario.nombre)}</span>` +
      `<span class="pastilla rol">${escapar(usuario.rol)}</span>` +
      '<a href="#" id="salir">Salir</a>';
    document.getElementById("salir").onclick = (e) => { e.preventDefault(); salir(); };
  }

  /** Explica por qué la página está en blanco y ofrece a dónde sí se puede ir.
   *
   *  Mandar a la pantalla de ingreso sería mentir: la sesión está perfecta, lo que no
   *  alcanza es el rol, y volver a entrar con la misma cuenta da lo mismo. */
  function negarAcceso(usuario, roles) {
    const bloqueo = document.getElementById("sin-acceso");
    const app = document.getElementById("app");
    if (app) app.hidden = true;
    if (!bloqueo) return;

    const suyas = PAGINAS.filter((p) => alcanza(usuario.rol, p.roles) && p.ruta !== location.pathname);
    bloqueo.innerHTML =
      "<h2>Esta página no es para tu cuenta</h2>" +
      `<div class="aviso error">Esta página es para ${escapar(roles.join(" o "))}. ` +
      `Entraste como ${escapar(usuario.nombre)}, que es ${escapar(usuario.rol)}.</div>` +
      (suyas.length
        ? '<p class="pista">Con tu cuenta puedes usar: ' +
          suyas.map((p) => `<a href="${p.ruta}">${escapar(p.nombre)}</a>`).join(" · ") +
          ".</p>"
        : "") +
      `<p class="pista">Si te equivocaste de cuenta, <a href="${escapar(urlDeIngreso())}">` +
      "entra con otra</a>.</p>";
    bloqueo.hidden = false;
  }

  /** Deja ver la página sólo a quien corresponde, y explica el resto de los casos.
   *
   *  Tres desenlaces distintos que antes eran uno solo: sin sesión se va a ingresar, con
   *  sesión y sin rol se explica, y si el servidor no contesta se dice eso —y no «la
   *  sesión venció», que mandaba a reingresar contra un servidor caído—. */
  let protegida = false;

  async function proteger({ rol, alIngresar }) {
    const app = document.getElementById("app");
    const bloqueo = document.getElementById("sin-acceso");
    protegida = true;

    if (!token()) {
      location.href = urlDeIngreso();
      return;
    }

    let usuario;
    try {
      usuario = await yo();
    } catch (e) {
      if (e.estado === 401) {
        location.href = urlDeIngreso();
        return;
      }
      pintarSesion(null);
      if (bloqueo) {
        bloqueo.innerHTML =
          "<h2>No se pudo comprobar tu sesión</h2>" +
          `<div class="aviso error">${escapar(e.message)}</div>` +
          '<p class="pista">El servidor no está respondiendo. Vuelve a cargar la página ' +
          "en un momento.</p>";
        bloqueo.hidden = false;
      }
      return;
    }

    pintarSesion(usuario);
    if (rol && !alcanza(usuario.rol, rol)) return negarAcceso(usuario, rol);

    if (bloqueo) bloqueo.hidden = true;
    if (app) app.hidden = false;
    alIngresar?.(usuario);
  }

  /** La barra de arriba se pinta sola en toda página, también en las públicas.
   *
   *  En las privadas ya lo hizo `proteger`, que corre antes —el `<script>` del final del
   *  body se ejecuta antes del `DOMContentLoaded`—: preguntar de nuevo quién soy sería
   *  un `/auth/yo` por página sin motivo. */
  async function arrancar() {
    if (protegida) return;
    if (!token()) return pintarSesion(null);
    try {
      pintarSesion(await yo());
    } catch {
      // Token viejo en una pestaña vieja: no es un error que mostrar, es no haber entrado.
      cerrarSesion();
      pintarSesion(null);
    }
  }
  document.addEventListener("DOMContentLoaded", arrancar);

  return {
    pedir, ingresar, yo, salir, descargar, descargarGrande,
    pesos, escapar, oVacio, avisar, aplazar, porWhatsapp, proteger,
    pintarSesion, urlDeIngreso, rutaLocal, alcanza, PAGINAS,
    haySesion: () => Boolean(token()),
  };
})();
