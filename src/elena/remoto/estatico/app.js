// ELENA · control remoto de música. Sin dependencias ni compilación.
// El estado llega por WebSocket (/api/ws); las órdenes van por POST. El avance de la canción y la
// cuenta regresiva se miden desde que llega cada mensaje (performance.now), sin usar el reloj del
// teléfono: solo importa la diferencia, y la PC manda su hora en `ahora`.
"use strict";

const $ = (id) => document.getElementById(id);
const NIVELES = { pausar: "Pausar", vm: "Pausar y apagar la VM", suspender: "Apagar la VM y suspender la PC",
                  apagar: "Apagar la VM y la PC" };
const FASES = { contando: "", desvaneciendo: "Bajando el volumen…", pausando: "Pausando…",
                restaurando: "Pausando…", apagando_vm: "Apagando la VM…" };
const VM = { apagada: "apagada", encendiendo: "encendiendo…", corriendo: "encendida", apagando: "apagando…" };
const MANTENER_MS = 2000;
const SALTO_MS = 10000;     // los botones −10 s y +10 s

let estado = null;          // el último mensaje de estado
let recibido = 0;           // performance.now() al recibirlo
let socket = null;
let espera = 1000;          // reintento del WebSocket (crece hasta 10 s)
let reintento = null;
let arrastrando = 0;        // hasta cuándo (performance.now) se ignora el volumen que llega
let caratulaActual = null;
let destino = null;         // ms mientras se arrastra la barra (solo en el teléfono hasta soltar)

// ---------- red ----------

async function api(metodo, ruta, cuerpo) {
  let r;
  try {
    r = await fetch(ruta, {
      method: metodo,
      headers: cuerpo === undefined ? {} : { "Content-Type": "application/json" },
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      credentials: "same-origin",
    });
  } catch {
    aviso("Sin conexión con la PC.", true);
    return null;
  }
  const datos = await r.json().catch(() => ({}));
  if (r.status === 401) { mostrarEmparejar(); return null; }
  if (!r.ok) { aviso(datos.error || `Error ${r.status}`, true); return null; }
  return datos;
}

async function arrancar() {
  const r = await fetch("/api/estado", { credentials: "same-origin" }).catch(() => null);
  if (r && r.status === 401) { mostrarEmparejar(); return; }
  if (r && r.ok) pintar(await r.json());
  $("app").hidden = false;
  conectar();
}

function conectar() {
  clearTimeout(reintento);
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  socket = new WebSocket(`ws://${location.host}/api/ws`);
  socket.onopen = () => { espera = 1000; conexion(true); };
  socket.onmessage = (m) => {
    const datos = JSON.parse(m.data);
    if (datos.t === "estado") pintar(datos);
    else if (datos.t === "aviso") aviso(datos.texto);
  };
  socket.onclose = async () => {
    conexion(false);
    // ¿Se cerró porque ya no hay permiso (elena remoto token nuevo)? Entonces no reintentar.
    const r = await fetch("/api/estado", { credentials: "same-origin" }).catch(() => null);
    if (r && r.status === 401) { mostrarEmparejar(); return; }
    reintento = setTimeout(conectar, espera);
    espera = Math.min(espera * 2, 10000);
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") { espera = 1000; conectar(); }
});

function conexion(viva) {
  $("conexion").classList.toggle("vivo", viva);
  $("conexion").setAttribute("aria-label", viva ? "Conectado" : "Sin conexión");
}

function mostrarEmparejar() {
  $("app").hidden = true;
  $("emparejar").hidden = false;
  if (socket) { socket.onclose = null; socket.close(); }
}

// ---------- pintar ----------

function tiempo(ms) {
  if (ms == null || ms < 0) return "";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

function pintar(datos) {
  estado = datos;
  recibido = performance.now();
  pintarMusica(datos.musica, datos.reproductores || []);
  pintarVolumen(datos.volumen);
  pintarTemporizador(datos.temporizador);
  pintarVm(datos.vm, datos.vm_servicio);
  avanzar();
}

function pintarMusica(m, lista) {
  const select = $("reproductor");
  select.hidden = lista.length < 2;
  const opciones = lista.map((r) => `${r.id}\u0000${r.nombre}`).join("\u0001");
  if (select.dataset.opciones !== opciones) {
    select.dataset.opciones = opciones;
    select.replaceChildren(...lista.map((r) => new Option(r.nombre, r.id)));
  }
  if (m) select.value = m.reproductor;

  $("titulo").textContent = m ? (m.titulo || m.nombre) : "Nada sonando";
  $("artista").textContent = m ? [m.artista, m.album].filter(Boolean).join(" · ") : "";
  const sonando = m && m.estado === "Playing";
  $("icono-play").hidden = sonando;
  $("icono-pausa").hidden = !sonando;
  $("alternar").setAttribute("aria-label", sonando ? "Pausar" : "Reproducir");
  $("alternar").disabled = !m || !(m.puede.play || m.puede.pausa);
  $("anterior").disabled = !m || !m.puede.anterior;
  $("siguiente").disabled = !m || !m.puede.siguiente;
  $("dur").textContent = m ? tiempo(m.duracion_ms) : "";

  const barra = $("barra");
  const movible = puedeArrastrar(m);
  barra.classList.toggle("movible", movible);
  barra.setAttribute("aria-disabled", String(!movible));
  barra.tabIndex = movible ? 0 : -1;
  barra.setAttribute("aria-valuemax", String(Math.round((m && m.duracion_ms ? m.duracion_ms : 0) / 1000)));
  if (!movible && destino !== null) soltarBarra();
  $("saltos").hidden = !(m && m.puede.posicion);

  const src = m && m.caratula;
  if (src !== caratulaActual) {
    caratulaActual = src;
    const img = $("caratula");
    if (src) { img.src = src; img.hidden = false; } else { img.removeAttribute("src"); img.hidden = true; }
    $("sin-caratula").hidden = Boolean(src);
  }
}

$("caratula").addEventListener("error", () => { $("caratula").hidden = true; $("sin-caratula").hidden = false; });

function pintarVolumen(v) {
  const vol = $("vol");
  vol.disabled = !v;
  $("mudo").disabled = !v;
  $("mudo").classList.toggle("activo", Boolean(v && v.mudo));
  $("icono-sonido").hidden = Boolean(v && v.mudo);
  $("icono-mudo").hidden = !(v && v.mudo);
  $("mudo").setAttribute("aria-label", v && v.mudo ? "Activar sonido" : "Silenciar");
  if (!v) { $("vol-valor").textContent = "—"; return; }
  if (performance.now() < arrastrando) return;   // no pelear con el dedo
  vol.value = Math.round(v.pct);
  pintarRelleno();
  $("vol-valor").textContent = `${Math.round(v.pct)} %`;
}

function pintarRelleno() {
  const vol = $("vol");
  vol.style.setProperty("--lleno", `${(vol.value / vol.max) * 100}%`);
}

function pintarTemporizador(t) {
  $("tz-inactivo").hidden = Boolean(t);
  $("tz-activo").hidden = !t;
  if (!t) return;
  const fase = FASES[t.fase] || "";
  const vence = new Date(t.vence).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
  const cuando = t.modo === "fin_cancion" ? "al terminar esta canción" : `a las ${vence}`;
  $("tz-detalle").textContent = fase || `${NIVELES[t.nivel]} ${cuando}`;
  $("tz-mas").disabled = t.modo !== "minutos" || t.fase !== "contando";
  $("tz-cancelar").textContent = t.fase === "contando" ? "Cancelar" : "Detener";
}

function pintarVm(vm, servicio) {
  $("vm-estado").textContent = VM[vm] || vm;
  $("vm-encender").hidden = vm !== "apagada";
  $("vm-encender").disabled = !servicio;
  $("vm-apagar").hidden = vm !== "corriendo";
}

// El avance y la cuenta regresiva, dos veces por segundo
function avanzar() {
  if (!estado) return;
  const pasado = performance.now() - recibido;
  const m = estado.musica;
  if (m) {
    const pos = destino ?? posicionAhora(m);
    const fraccion = m.duracion_ms ? `${(pos / m.duracion_ms) * 100}%` : "0";
    $("pos").textContent = tiempo(pos);
    $("relleno").style.width = fraccion;
    $("perilla").style.left = fraccion;
    $("barra").setAttribute("aria-valuenow", String(Math.round(pos / 1000)));
    $("barra").setAttribute("aria-valuetext", m.duracion_ms ? `${tiempo(pos)} de ${tiempo(m.duracion_ms)}` : tiempo(pos));
  } else {
    $("pos").textContent = "";
    $("relleno").style.width = "0";
    $("perilla").style.left = "0";
  }
  const t = estado.temporizador;
  if (t) {
    let falta = t.vence - estado.ahora - pasado;
    if (t.modo === "fin_cancion" && m && m.duracion_ms) {
      falta = Math.max(m.duracion_ms - (m.posicion_ms + (m.estado === "Playing" ? pasado : 0)), 0);
    }
    $("tz-cuenta").textContent = t.fase === "contando" ? tiempo(Math.max(falta, 0)) : "—";
  }
}
setInterval(avanzar, 500);

// ---------- órdenes ----------

const musica = (orden) => api("POST", "/api/musica", { orden, reproductor: estado?.musica?.reproductor });
$("alternar").addEventListener("click", () => musica("alternar"));
$("anterior").addEventListener("click", () => musica("anterior"));
$("siguiente").addEventListener("click", () => musica("siguiente"));
$("reproductor").addEventListener("change", (e) => api("POST", "/api/musica", { elegir: e.target.value }));

// Posición: la barra se arrastra o se toca (la orden sale al soltar) y los botones saltan 10 s.
// El teléfono no espera la respuesta: se pone en el destino y el siguiente estado lo corrige.
function posicionAhora(m) {
  const pos = m.posicion_ms + (m.estado === "Playing" ? performance.now() - recibido : 0);
  return m.duracion_ms ? Math.min(pos, m.duracion_ms) : pos;
}
function puedeArrastrar(m) {
  return Boolean(m && m.puede.posicion && m.duracion_ms);
}
function fijarPosicion(m, ms) {
  // como si posicion_ms hubiera llegado en `recibido` (no se toca `recibido`: la usa el temporizador)
  m.posicion_ms = ms - (m.estado === "Playing" ? performance.now() - recibido : 0);
  avanzar();
}
function irA(ms) {
  const m = estado?.musica;
  if (!puedeArrastrar(m)) return;
  ms = Math.round(Math.min(Math.max(ms, 0), m.duracion_ms));
  fijarPosicion(m, ms);
  api("POST", "/api/musica", { posicion_ms: ms, reproductor: m.reproductor });
}
function saltar(ms) {
  const m = estado?.musica;
  if (!m || !m.puede.posicion) return;
  let pos = Math.max(posicionAhora(m) + ms, 0);
  if (m.duracion_ms) pos = Math.min(pos, m.duracion_ms);
  fijarPosicion(m, pos);
  api("POST", "/api/musica", { saltar_ms: ms, reproductor: m.reproductor });
}

const barra = $("barra");
function msDelDedo(e) {
  const r = barra.getBoundingClientRect();
  const f = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
  return f * estado.musica.duracion_ms;
}
function soltarBarra() {
  destino = null;
  barra.classList.remove("activa");
}
barra.addEventListener("pointerdown", (e) => {
  if (!puedeArrastrar(estado?.musica)) return;
  barra.setPointerCapture(e.pointerId);
  barra.classList.add("activa");
  destino = msDelDedo(e);
  avanzar();
});
barra.addEventListener("pointermove", (e) => {
  if (destino === null || !puedeArrastrar(estado?.musica)) return;
  destino = msDelDedo(e);
  avanzar();
});
barra.addEventListener("pointerup", (e) => {
  if (destino === null) return;
  const ms = puedeArrastrar(estado?.musica) ? msDelDedo(e) : null;
  soltarBarra();
  if (ms === null) avanzar(); else irA(ms);
});
barra.addEventListener("pointercancel", () => { soltarBarra(); avanzar(); });
barra.addEventListener("keydown", (e) => {
  const paso = { ArrowLeft: -SALTO_MS, ArrowDown: -SALTO_MS, ArrowRight: SALTO_MS, ArrowUp: SALTO_MS }[e.key];
  if (paso && puedeArrastrar(estado?.musica)) { e.preventDefault(); saltar(paso); }
});
$("atras").addEventListener("click", () => saltar(-SALTO_MS));
$("adelante").addEventListener("click", () => saltar(SALTO_MS));

// Ver este video: el servidor valida el enlace (solo YouTube) y lo abre en la PC
$("video-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("video-url").value.trim();
  if (!url) { aviso("Pega el enlace de un video de YouTube.", true); return; }
  const r = await api("POST", "/api/abrir", { url });
  if (r) { $("video-url").value = ""; $("video-url").blur(); aviso("Abriendo en la PC…"); }
});

// Volumen: como mucho un envío cada 120 ms mientras se arrastra, y siempre el último valor
let envioVol = null, ultimoVol = null, enVuelo = false;
async function mandarVolumen() {
  if (enVuelo || ultimoVol === null) return;
  enVuelo = true;
  const pct = ultimoVol;
  ultimoVol = null;
  await api("POST", "/api/volumen", { pct });
  enVuelo = false;
  if (ultimoVol !== null) envioVol = setTimeout(mandarVolumen, 120);
}
$("vol").addEventListener("input", (e) => {
  arrastrando = performance.now() + 800;
  pintarRelleno();
  $("vol-valor").textContent = `${e.target.value} %`;
  ultimoVol = Number(e.target.value);
  clearTimeout(envioVol);
  envioVol = setTimeout(mandarVolumen, enVuelo ? 120 : 0);
});
$("mudo").addEventListener("click", () => api("POST", "/api/volumen", { silencio: null }));

// Temporizador
function nivel() {
  return document.querySelector("input[name=nivel]:checked").value;
}
document.querySelectorAll("input[name=nivel]").forEach((r) => r.addEventListener("change", () => {
  try { localStorage.setItem("elena-nivel", nivel()); } catch { /* sin almacenamiento */ }
}));
try {
  const guardado = localStorage.getItem("elena-nivel");
  const radio = guardado && document.querySelector(`input[name=nivel][value="${CSS.escape(guardado)}"]`);
  if (radio) radio.checked = true;
} catch { /* sin almacenamiento */ }

document.querySelectorAll("[data-min]").forEach((b) => b.addEventListener("click", () =>
  api("POST", "/api/temporizador", { minutos: Number(b.dataset.min), nivel: nivel() })));
document.querySelector("[data-fin]").addEventListener("click", () =>
  api("POST", "/api/temporizador", { fin_cancion: true, nivel: nivel() }));
$("tz-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const minutos = Number($("tz-min").value);
  if (!minutos) { aviso("Escribe los minutos.", true); return; }
  api("POST", "/api/temporizador", { minutos, nivel: nivel() }).then((r) => { if (r) $("tz-min").value = ""; });
});
$("tz-mas").addEventListener("click", () => api("POST", "/api/temporizador/mas", { minutos: 10 }));
$("tz-cancelar").addEventListener("click", () => api("DELETE", "/api/temporizador"));

// Equipo
$("vm-encender").addEventListener("click", () => api("POST", "/api/vm", { accion: "encender" }));

const MANTENIDOS = {
  vm: () => api("POST", "/api/vm", { accion: "apagar" }),
  suspender: () => api("POST", "/api/sistema", { accion: "suspender", confirmar: true }),
  apagar: () => api("POST", "/api/sistema", { accion: "apagar", confirmar: true }),
};
document.querySelectorAll("[data-mantener]").forEach((b) => {
  let temporizador = null;
  const soltar = () => { clearTimeout(temporizador); b.classList.remove("cargando"); };
  b.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    b.classList.add("cargando");
    temporizador = setTimeout(() => {
      soltar();
      if (navigator.vibrate) navigator.vibrate(40);
      MANTENIDOS[b.dataset.mantener]();
    }, MANTENER_MS);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => b.addEventListener(ev, soltar));
  b.addEventListener("contextmenu", (e) => e.preventDefault());
  b.addEventListener("click", (e) => { if (e.detail === 0) aviso("Mantén presionado 2 s."); });
});

// ---------- avisos ----------

let avisoFin = null;
function aviso(texto, error = false) {
  const a = $("aviso");
  a.textContent = texto;
  a.classList.toggle("error", error);
  a.hidden = false;
  clearTimeout(avisoFin);
  avisoFin = setTimeout(() => { a.hidden = true; }, error ? 5000 : 3500);
}

arrancar();
