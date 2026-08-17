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

    if (respuesta.status === 401) {
      if (!reintentando && (await renovar())) return pedir(ruta, opciones, true);
      sessionStorage.removeItem(CLAVE);
      sessionStorage.removeItem(CLAVE_REFRESH);
      throw new Error("La sesión venció. Ingresa de nuevo.");
    }
    if (!respuesta.ok) {
      // El detalle del backend es el mensaje útil (qué falta, qué chocó). Se muestra
      // tal cual en vez de un "error 422" que no le dice nada a nadie.
      let detalle = `Error ${respuesta.status}`;
      try {
        const cuerpo = await respuesta.json();
        if (typeof cuerpo.detail === "string") detalle = cuerpo.detail;
        else if (Array.isArray(cuerpo.detail)) {
          detalle = cuerpo.detail.map((e) => `${e.loc?.slice(1).join(".")}: ${e.msg}`).join(" · ");
        }
      } catch { /* respuesta sin JSON: queda el código */ }
      throw new Error(detalle);
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

  const yo = () => pedir("/auth/yo");
  const salir = () => {
    sessionStorage.removeItem(CLAVE);
    sessionStorage.removeItem(CLAVE_REFRESH);
    location.reload();
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

  /** Muestra el bloque que corresponde al rol y deja la sesión visible arriba. */
  async function proteger({ rol, alIngresar }) {
    const login = document.getElementById("login");
    const app = document.getElementById("app");
    const sesion = document.getElementById("sesion");

    const mostrar = (usuario) => {
      login.hidden = true;
      app.hidden = false;
      sesion.innerHTML =
        `${escapar(usuario.nombre)} (${escapar(usuario.rol)}) · ` +
        '<a href="#" id="salir">salir</a>';
      document.getElementById("salir").onclick = (e) => { e.preventDefault(); salir(); };
      alIngresar?.(usuario);
    };

    if (token()) {
      try {
        const usuario = await yo();
        if (!rol || usuario.rol === "admin" || rol.includes(usuario.rol)) return mostrar(usuario);
        avisar(document.getElementById("login-error"),
          `Tu rol (${usuario.rol}) no tiene acceso a esta página.`, "error");
      } catch { sessionStorage.removeItem(CLAVE); }
    }

    login.hidden = false;
    app.hidden = true;
    document.getElementById("form-login").onsubmit = async (evento) => {
      evento.preventDefault();
      const error = document.getElementById("login-error");
      error.hidden = true;
      try {
        const usuario = await ingresar(evento.target.email.value, evento.target.password.value);
        if (rol && usuario.rol !== "admin" && !rol.includes(usuario.rol)) {
          return avisar(error, `Tu rol (${usuario.rol}) no tiene acceso a esta página.`, "error");
        }
        mostrar(usuario);
      } catch (e) {
        avisar(error, e.message, "error");
      }
    };
  }

  return {
    pedir, ingresar, yo, salir, descargar, descargarGrande,
    pesos, escapar, oVacio, avisar, proteger,
  };
})();
