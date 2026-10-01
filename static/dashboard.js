"use strict";
// Dashboard: resumen mensual y segmentación. Usa $, $$, api, fmt y esc de app.js.

const SVG = "http://www.w3.org/2000/svg";
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const NOMBRE_SEG = { categoria: "categoría", tercero: "proveedor / cliente", factura: "factura / manual" };
const dash = { datos: null, matrizTipo: "egreso", cargado: false };

const mesCorto = (ym) => `${MESES[+ym.slice(5) - 1]} ${ym.slice(2, 4)}`;
const mesLargo = (ym) => `${MESES[+ym.slice(5) - 1]} ${ym.slice(0, 4)}`;
const fmtCompacto = (n) =>
  new Intl.NumberFormat("es-AR", { notation: "compact", maximumFractionDigits: 1 }).format(n);

function el(tag, attrs = {}, padre) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (padre) padre.appendChild(e);
  return e;
}

// ------------------------------------------------------------ Navegación entre vistas

function mostrarVista() {
  const vista = location.hash === "#dashboard" ? "dashboard" : "movimientos";
  $("#vista-movimientos").hidden = vista !== "movimientos";
  $("#vista-dashboard").hidden = vista !== "dashboard";
  $$(".tabs a").forEach((a) => a.classList.toggle("activo", a.dataset.vista === vista));
  if (vista === "dashboard") cargarDashboard();
}
window.addEventListener("hashchange", mostrarVista);
document.addEventListener("movimientos-cambiaron", () => {
  if (!$("#vista-dashboard").hidden) cargarDashboard();
  else dash.cargado = false;
});

// ------------------------------------------------------------ Filtros

function rangoPeriodo() {
  const hoy = new Date();
  const y = hoy.getFullYear();
  const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  const hace = (n) => new Date(y, hoy.getMonth() - n, 1);
  switch ($("#d-periodo").value) {
    case "anio": return [`${y}-01`, ym(hoy)];
    case "anio-1": return [`${y - 1}-01`, `${y - 1}-12`];
    case "6m": return [ym(hace(5)), ym(hoy)];
    case "custom": return [$("#d-desde").value || ym(hace(11)), $("#d-hasta").value || ym(hoy)];
    default: return [ym(hace(11)), ym(hoy)];
  }
}

function ultimoDia(ym) {
  const [y, m] = ym.split("-").map(Number);
  return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}

function qsDashboard() {
  const [d, h] = rangoPeriodo();
  const p = new URLSearchParams({ desde: `${d}-01`, hasta: ultimoDia(h), segmento: $("#d-segmento").value });
  if ($("#d-moneda").value) p.set("moneda", $("#d-moneda").value);
  return p.toString();
}

async function cargarDashboard() {
  const cuerpo = $("#dash-cuerpo");
  cuerpo.classList.add("cargando"); // mantiene el render anterior mientras recarga
  const qs = qsDashboard();
  $("#d-csv").href = "api/dashboard.csv?" + qs;
  try {
    dash.datos = await api("api/dashboard?" + qs);
    dash.cargado = true;
    pintarDashboard();
  } catch (e) {
    $("#d-kpis").innerHTML = `<p class="vacio">No se pudo cargar el dashboard: ${esc(e.message)}</p>`;
  } finally {
    cuerpo.classList.remove("cargando");
  }
}

function pintarDashboard() {
  const d = dash.datos;
  const sel = $("#d-moneda");
  const actual = sel.value || d.moneda;
  sel.innerHTML = d.monedas.map((m) => `<option ${m === actual ? "selected" : ""}>${esc(m)}</option>`).join("");
  if (!d.monedas.includes(actual)) sel.add(new Option(actual, actual, true, true));

  const seg = NOMBRE_SEG[d.segmento];
  $("#t-seg-egreso").textContent = `Egresos por ${seg}`;
  $("#t-seg-ingreso").textContent = `Ingresos por ${seg}`;
  $("#t-matriz").textContent = `Detalle por ${seg} y mes`;
  $("#d-sub-meses").textContent = `${mesLargo(d.meses[0].mes)} a ${mesLargo(d.meses.at(-1).mes)} · ${d.moneda}`;

  pintarKpis(d);
  graficoMeses($("#ch-meses"), d);
  graficoSaldo($("#ch-saldo"), d);
  barrasSegmento($("#ch-seg-egreso"), d.segmentos.egreso, "egreso", d.moneda);
  barrasSegmento($("#ch-seg-ingreso"), d.segmentos.ingreso, "ingreso", d.moneda);
  tablaMeses($("#tb-meses"), d);
  tablaMatriz($("#tb-matriz"), d);
}

// ------------------------------------------------------------ KPIs

function pintarKpis(d) {
  const t = d.totales, m = d.moneda;
  // En los KPI se muestran montos sin centavos para que entren en pantallas chicas
  const fmt = (n, moneda) =>
    new Intl.NumberFormat("es-AR", { style: "currency", currency: moneda, maximumFractionDigits: 0 }).format(n);
  const mesesConDatos = d.meses.filter((x) => x.cantidad).length;
  const kpi = (titulo, valor, det, key = "") =>
    `<div class="kpi"><h3>${key}${titulo}</h3><div class="valor">${valor}</div><div class="det">${det}</div></div>`;
  $("#d-kpis").innerHTML =
    kpi("Ingresos", fmt(t.ingresos, m), `${d.segmentos.ingreso.reduce((a, s) => a + s.cantidad, 0)} movimientos`, '<i class="key ingreso"></i>') +
    kpi("Egresos", fmt(t.egresos, m), `Promedio mensual ${fmt(t.promedio_egresos, m)}`, '<i class="key egreso"></i>') +
    kpi("Saldo del período", `<span class="${t.saldo < 0 ? "egr" : ""}">${fmt(t.saldo, m)}</span>`, `${mesesConDatos} de ${d.meses.length} meses con movimientos`) +
    kpi("Margen", t.margen == null ? "—" : `${t.margen.toLocaleString("es-AR")} %`, "Saldo sobre ingresos");
}

// ------------------------------------------------------------ Escalas y ejes

function escalaY(min, max, alto, top) {
  if (max === min) max = min + 1;
  const paso0 = (max - min) / 4;
  const mag = 10 ** Math.floor(Math.log10(paso0));
  const paso = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((p) => p >= paso0);
  const lo = Math.floor(min / paso) * paso, hi = Math.ceil(max / paso) * paso;
  const ticks = [];
  for (let v = lo; v <= hi + paso / 2; v += paso) ticks.push(+v.toFixed(6));
  const y = (v) => top + alto - ((v - lo) / (hi - lo)) * alto;
  return { y, ticks };
}

function ejes(svg, esc, ancho, izq, der) {
  const g = el("g", { class: "grid" }, svg);
  for (const t of esc.ticks) {
    el("line", { x1: izq, x2: ancho - der, y1: esc.y(t), y2: esc.y(t) }, g);
    const tx = el("text", { x: izq - 6, y: esc.y(t) + 4, "text-anchor": "end" }, svg);
    tx.textContent = fmtCompacto(t);
  }
}

function etiquetasX(svg, meses, xCentro, yBase, ancho) {
  const cada = Math.ceil(meses.length / Math.max(1, Math.floor(ancho / 52)));
  meses.forEach((m, i) => {
    if (i % cada) return;
    const t = el("text", { x: xCentro(i), y: yBase + 16, "text-anchor": "middle" }, svg);
    t.textContent = mesCorto(m.mes);
  });
}

// Columna con extremo de datos redondeado (4px) y base recta.
function columna(x, yTop, w, yBase) {
  const h = yBase - yTop;
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${yBase}V${yTop + r}Q${x},${yTop} ${x + r},${yTop}H${x + w - r}Q${x + w},${yTop} ${x + w},${yTop + r}V${yBase}Z`;
}

// ------------------------------------------------------------ Tooltip

const tip = $("#tooltip");
function mostrarTip(ev, titulo, filas) {
  tip.replaceChildren();
  const t = document.createElement("div");
  t.className = "tt-tit";
  t.textContent = titulo;
  tip.appendChild(t);
  for (const [color, nombre, valor] of filas) {
    const f = document.createElement("div");
    f.className = "tt-fila";
    if (color) {
      const k = document.createElement("i");
      k.className = "tt-key";
      k.style.background = color;
      f.appendChild(k);
    }
    const n = document.createElement("span");
    n.textContent = nombre;
    const v = document.createElement("b");
    v.textContent = valor;
    f.append(n, v);
    tip.appendChild(f);
  }
  tip.hidden = false;
  const r = ev.clientX !== undefined && ev.type.startsWith("pointer")
    ? { x: ev.clientX, y: ev.clientY }
    : (() => { const b = ev.target.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top }; })();
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = r.x + 14, y = r.y - h - 10;
  if (x + w > innerWidth - 8) x = r.x - w - 14;
  if (y < 8) y = r.y + 16;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${y}px`;
}
const ocultarTip = () => (tip.hidden = true);
const colorVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

// ------------------------------------------------------------ Gráfico: ingresos vs egresos por mes

function graficoMeses(cont, d) {
  cont.replaceChildren();
  const ancho = Math.max(300, cont.clientWidth), alto = 240;
  const izq = 48, der = 8, top = 10, abajo = 26;
  const max = Math.max(0, ...d.meses.flatMap((m) => [m.ingresos, m.egresos]));
  if (!max) return vacioChart(cont, "Sin movimientos en este período.");
  const esc = escalaY(0, max, alto - top - abajo, top);
  const svg = el("svg", { viewBox: `0 0 ${ancho} ${alto}`, role: "img", "aria-label": "Ingresos y egresos por mes" }, cont);
  ejes(svg, esc, ancho, izq, der);

  const banda = (ancho - izq - der) / d.meses.length;
  const bw = Math.max(3, Math.min(24, (banda - 10) / 2));
  const xC = (i) => izq + banda * (i + 0.5);
  const yBase = esc.y(0);
  d.meses.forEach((m, i) => {
    el("path", { class: "b-ingreso", d: columna(xC(i) - bw - 1, esc.y(m.ingresos), bw, yBase) }, svg);
    el("path", { class: "b-egreso", d: columna(xC(i) + 1, esc.y(m.egresos), bw, yBase) }, svg);
    const hit = el("rect", { class: "hit", x: izq + banda * i, y: top, width: banda, height: yBase - top, tabindex: 0,
      "aria-label": `${mesLargo(m.mes)}: ingresos ${fmt(m.ingresos, d.moneda)}, egresos ${fmt(m.egresos, d.moneda)}` }, svg);
    const tt = (ev) => mostrarTip(ev, mesLargo(m.mes), [
      [colorVar("--s-ingreso"), "Ingresos", fmt(m.ingresos, d.moneda)],
      [colorVar("--s-egreso"), "Egresos", fmt(m.egresos, d.moneda)],
      [null, "Saldo", fmt(m.saldo, d.moneda)],
    ]);
    hit.addEventListener("pointermove", tt);
    hit.addEventListener("focus", tt);
    hit.addEventListener("pointerleave", ocultarTip);
    hit.addEventListener("blur", ocultarTip);
  });
  el("line", { class: "cero", x1: izq, x2: ancho - der, y1: yBase, y2: yBase }, svg);
  etiquetasX(svg, d.meses, xC, yBase, ancho - izq - der);
}

// ------------------------------------------------------------ Gráfico: saldo acumulado

function graficoSaldo(cont, d) {
  cont.replaceChildren();
  const ancho = Math.max(300, cont.clientWidth), alto = 200;
  const izq = 48, der = 16, top = 22, abajo = 26;
  const vals = d.meses.map((m) => m.acumulado);
  if (!d.totales.cantidad) return vacioChart(cont, "Sin movimientos en este período.");
  const esc = escalaY(Math.min(0, ...vals), Math.max(0, ...vals), alto - top - abajo, top);
  const svg = el("svg", { viewBox: `0 0 ${ancho} ${alto}`, role: "img", "aria-label": "Saldo acumulado por mes" }, cont);
  ejes(svg, esc, ancho, izq, der);
  const n = d.meses.length;
  const x = (i) => (n === 1 ? (izq + ancho - der) / 2 : izq + ((ancho - izq - der) * i) / (n - 1));
  const y0 = esc.y(0);
  el("line", { class: "cero", x1: izq, x2: ancho - der, y1: y0, y2: y0 }, svg);
  const pts = vals.map((v, i) => `${x(i)},${esc.y(v)}`);
  el("path", { class: "area", d: `M${x(0)},${y0}L${pts.join("L")}L${x(n - 1)},${y0}Z` }, svg);
  el("path", { class: "linea", d: `M${pts.join("L")}` }, svg);
  etiquetasX(svg, d.meses, x, esc.y(esc.ticks[0]), ancho - izq - der);

  // Valor final rotulado
  const ult = vals.at(-1);
  el("circle", { class: "punto", cx: x(n - 1), cy: esc.y(ult), r: 4 }, svg);
  const lab = el("text", { class: "etq", x: x(n - 1), y: esc.y(ult) - 10, "text-anchor": "end" }, svg);
  lab.textContent = fmt(ult, d.moneda);

  // Crosshair que se engancha al mes más cercano
  const cross = el("line", { class: "cross", y1: top, y2: esc.y(esc.ticks[0]), visibility: "hidden" }, svg);
  const marca = el("circle", { class: "punto", r: 4, visibility: "hidden" }, svg);
  const capa = el("rect", { class: "hit", x: izq, y: 0, width: ancho - izq - der, height: alto - abajo, tabindex: 0,
    "aria-label": `Saldo acumulado al final del período: ${fmt(ult, d.moneda)}` }, svg);
  let foco = n - 1;
  const mostrar = (i, ev) => {
    const m = d.meses[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    marca.setAttribute("cx", x(i)); marca.setAttribute("cy", esc.y(m.acumulado));
    cross.setAttribute("visibility", "visible"); marca.setAttribute("visibility", "visible");
    mostrarTip(ev, mesLargo(m.mes), [
      [colorVar("--s-saldo"), "Saldo acumulado", fmt(m.acumulado, d.moneda)],
      [null, "Saldo del mes", fmt(m.saldo, d.moneda)],
    ]);
  };
  capa.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * ancho;
    foco = n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - izq) / (ancho - izq - der)) * (n - 1))));
    mostrar(foco, ev);
  });
  capa.addEventListener("focus", (ev) => mostrar(foco, ev));
  capa.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    foco = Math.max(0, Math.min(n - 1, foco + (ev.key === "ArrowRight" ? 1 : -1)));
    mostrar(foco, ev);
  });
  const salir = () => { ocultarTip(); cross.setAttribute("visibility", "hidden"); marca.setAttribute("visibility", "hidden"); };
  capa.addEventListener("pointerleave", salir);
  capa.addEventListener("blur", salir);
}

function vacioChart(cont, texto) {
  const p = document.createElement("p");
  p.className = "vacio-chart";
  p.textContent = texto;
  cont.appendChild(p);
}

// ------------------------------------------------------------ Barras horizontales por segmento

function barrasSegmento(cont, segs, tipo, moneda) {
  cont.replaceChildren();
  if (!segs.length) return vacioChart(cont, `Sin ${tipo === "egreso" ? "egresos" : "ingresos"} en este período.`);
  const max = Math.max(...segs.map((s) => s.total));
  for (const s of segs) {
    const fila = document.createElement("div");
    fila.className = `bh ${tipo}`;
    fila.tabIndex = 0;
    const nom = document.createElement("span");
    nom.className = "nom";
    nom.textContent = s.nombre;
    nom.title = s.nombre;
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = fmt(s.total, moneda);
    const pct = document.createElement("small");
    pct.textContent = `${s.pct.toLocaleString("es-AR")} %`;
    val.appendChild(pct);
    const pista = document.createElement("div");
    pista.className = "pista";
    const barra = document.createElement("div");
    barra.className = "barra";
    barra.style.width = `${(100 * s.total) / max}%`;
    pista.appendChild(barra);
    fila.append(nom, val, pista);
    const tt = (ev) => mostrarTip(ev, s.nombre, [
      [colorVar(tipo === "egreso" ? "--s-egreso" : "--s-ingreso"), "Total", fmt(s.total, moneda)],
      [null, "Movimientos", String(s.cantidad)],
      [null, `% de ${tipo === "egreso" ? "egresos" : "ingresos"}`, `${s.pct.toLocaleString("es-AR")} %`],
    ]);
    fila.addEventListener("pointermove", tt);
    fila.addEventListener("focus", tt);
    fila.addEventListener("pointerleave", ocultarTip);
    fila.addEventListener("blur", ocultarTip);
    cont.appendChild(fila);
  }
}

// ------------------------------------------------------------ Tablas

function celda(fila, texto, clase = "", tag = "td") {
  const c = document.createElement(tag);
  c.textContent = texto;
  if (clase) c.className = clase;
  fila.appendChild(c);
  return c;
}

function tablaMeses(tabla, d) {
  tabla.replaceChildren();
  const m = d.moneda;
  const head = tabla.createTHead().insertRow();
  ["Mes", "Ingresos", "Egresos", "Saldo", "Acumulado", "Mov."].forEach((h) => celda(head, h, "", "th"));
  const body = tabla.createTBody();
  for (const f of [...d.meses].reverse()) {
    const r = body.insertRow();
    celda(r, mesLargo(f.mes));
    celda(r, fmt(f.ingresos, m), f.ingresos ? "" : "cero-v");
    celda(r, fmt(f.egresos, m), f.egresos ? "" : "cero-v");
    celda(r, fmt(f.saldo, m), f.saldo < 0 ? "neg" : f.saldo ? "" : "cero-v");
    celda(r, fmt(f.acumulado, m), f.acumulado < 0 ? "neg" : "");
    celda(r, String(f.cantidad), f.cantidad ? "" : "cero-v");
  }
  const t = d.totales;
  const pie = tabla.createTFoot().insertRow();
  celda(pie, "Total");
  celda(pie, fmt(t.ingresos, m));
  celda(pie, fmt(t.egresos, m));
  celda(pie, fmt(t.saldo, m), t.saldo < 0 ? "neg" : "");
  celda(pie, "");
  celda(pie, String(t.cantidad));
}

function tablaMatriz(tabla, d) {
  tabla.replaceChildren();
  const tipo = dash.matrizTipo;
  const segs = d.segmentos[tipo];
  tabla.style.setProperty("--heat-rgb", `var(--${tipo}-rgb)`);
  if (!segs.length) {
    celda(tabla.insertRow(), `Sin ${tipo === "egreso" ? "egresos" : "ingresos"} en este período.`, "cero-v");
    return;
  }
  const head = tabla.createTHead().insertRow();
  celda(head, "Segmento", "", "th");
  d.meses.forEach((m) => celda(head, mesCorto(m.mes), "", "th"));
  celda(head, "Total", "", "th");
  const max = Math.max(...segs.flatMap((s) => s.meses));
  const body = tabla.createTBody();
  for (const s of segs) {
    const r = body.insertRow();
    celda(r, s.nombre).title = s.nombre;
    s.meses.forEach((v) => {
      const a = max ? v / max : 0;
      const c = celda(r, v ? fmtCompacto(v) : "–", v ? "celda" : "celda cero-v");
      c.style.setProperty("--a", (a * 0.85).toFixed(3));
      if (a > 0.55) c.classList.add("oscuro");
      c.title = fmt(v, d.moneda);
    });
    celda(r, fmt(s.total, d.moneda));
  }
  const pie = tabla.createTFoot().insertRow();
  celda(pie, "Total");
  d.meses.forEach((m) => celda(pie, fmtCompacto(tipo === "egreso" ? m.egresos : m.ingresos)));
  celda(pie, fmt(tipo === "egreso" ? d.totales.egresos : d.totales.ingresos, d.moneda));
}

// ------------------------------------------------------------ Eventos

$("#d-periodo").addEventListener("change", () => {
  $("#d-custom").hidden = $("#d-periodo").value !== "custom";
  if ($("#d-periodo").value === "custom" && !$("#d-desde").value) {
    const [d, h] = rangoPeriodo();
    $("#d-desde").value = d;
    $("#d-hasta").value = h;
  }
  cargarDashboard();
});
["#d-desde", "#d-hasta", "#d-moneda", "#d-segmento"].forEach((s) => $(s).addEventListener("change", cargarDashboard));
$$("#d-matriz-tipo button").forEach((b) =>
  b.addEventListener("click", () => {
    dash.matrizTipo = b.dataset.tipo;
    $$("#d-matriz-tipo button").forEach((x) => x.classList.toggle("activo", x === b));
    if (dash.datos) tablaMatriz($("#tb-matriz"), dash.datos);
  })
);
let tResize;
window.addEventListener("resize", () => {
  clearTimeout(tResize);
  tResize = setTimeout(() => dash.datos && !$("#vista-dashboard").hidden && pintarDashboard(), 150);
});

mostrarVista();
