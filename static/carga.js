"use strict";
// Carga automática de comprobantes. Usa $, api, fmt, esc, fmtFecha, abrir, aplicarDatos, estado,
// cargar y cargarCategorias de app.js.

const auto = { cuits: [], sugerido: false };
const resLista = $("#auto-res");

async function cargarAjustes() {
  const a = await api("api/ajustes");
  auto.cuits = a.cuits_propios;
  $("#auto-cuit").textContent = a.cuits_propios.length ? a.cuits_propios.join(", ") : "no configurado";
  const lectores = [];
  if (!a.ocr_local && !a.ia) lectores.push("⚠ sin lector de fotos: solo se leen PDFs con texto");
  $("#auto-lectores").textContent = lectores.join(" · ");
}

async function guardarCuits(valor) {
  const a = await api("api/ajustes", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cuits_propios: valor }),
  });
  auto.cuits = a.cuits_propios;
  await cargarAjustes();
}

$("#btn-cuit").onclick = async () => {
  const valor = prompt(
    `CUIT de ${empresaActual().nombre} (si tiene varios, separalos con coma).\n` +
      "Sirve para saber si un comprobante es un ingreso o un egreso.",
    auto.cuits.join(", ")
  );
  if (valor === null) return;
  try {
    await guardarCuits(valor);
  } catch (e) {
    alert(e.message);
  }
};

// ------------------------------------------------------------ Resultados

function itemResultado(clase, titulo, detalle, acciones = []) {
  const li = document.createElement("li");
  li.className = clase;
  const txt = document.createElement("div");
  txt.className = "txt";
  txt.textContent = titulo;
  if (detalle) {
    const s = document.createElement("small");
    s.textContent = detalle;
    txt.appendChild(s);
  }
  li.appendChild(txt);
  for (const [texto, fn, primario] of acciones) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn chico" + (primario ? " primary" : "");
    b.textContent = texto;
    b.onclick = fn;
    li.appendChild(b);
  }
  return li;
}

function describir(m) {
  const signo = m.tipo === "ingreso" ? "Ingreso" : "Egreso";
  return `${signo} ${fmt(m.monto, m.moneda)} · ${m.tercero || "sin contraparte"} · ${fmtFecha(m.fecha_factura)}`;
}

const revisar = async (id) => abrir(await api(`api/movimientos/${id}`));

async function procesar(archivo, forzar = false, liPrevio = null) {
  const li = itemResultado("procesando", `⏳ ${archivo.name}`, "Leyendo el comprobante…");
  liPrevio ? liPrevio.replaceWith(li) : resLista.prepend(li);
  const fd = new FormData();
  fd.append("archivo", archivo);
  if (forzar) fd.append("forzar", "true");
  let r;
  try {
    r = await api("api/carga-automatica", { method: "POST", body: fd });
  } catch (e) {
    li.replaceWith(itemResultado("incompleto", `✖ ${archivo.name}`, e.message));
    return;
  }

  let nuevo;
  if (r.estado === "creado") {
    const m = r.movimiento;
    const otra = m.empresa_id !== empresas.id;
    nuevo = itemResultado("creado", `${otra ? `→ ${r.datos.empresa_nombre}: ` : ""}✔ ${describir(m)}`,
      [otra && `Se cargó en ${r.datos.empresa_nombre} porque el CUIT del comprobante es de esa empresa`,
        m.desglose && `Neto: bruto ${fmt(m.desglose.bruto, m.moneda)} − ${m.desglose.items.length} retenciones` +
          (r.datos.desglose_verificado ? " ✔" : ""),
        m.numero_factura && `N.º ${m.numero_factura}`, m.categoria, r.datos.tipo_motivo].filter(Boolean).join(" · "),
      [["Revisar", () => revisar(m.id)]]);
  } else if (r.estado === "duplicado") {
    const donde = r.movimiento.empresa_id !== empresas.id ? ` en ${r.datos.empresa_nombre}` : "";
    nuevo = itemResultado("duplicado", `⚠ ${archivo.name}: ya estaba cargado${donde}`, describir(r.movimiento), [
      ["Ver", () => revisar(r.movimiento.id)],
      ["Cargar igual", () => procesar(archivo, true, nuevo)],
    ]);
  } else {
    nuevo = itemResultado("incompleto", `✎ ${archivo.name}`, r.mensaje, [
      ["Completar", () => {
        abrir();
        estado.archivo = archivo;
        aplicarDatos(r.datos, archivo);
      }, true],
    ]);
  }
  li.replaceWith(nuevo);

  const sug = r.sugerencia_cuit_propio;
  if (sug && !auto.cuits.length && !auto.sugerido) {
    auto.sugerido = true;
    const liSug = itemResultado("procesando",
      `¿${sug.cuit}${sug.nombre ? ` (${sug.nombre})` : ""} es el CUIT de ${empresaActual().nombre}?`,
      "Guardarlo permite distinguir solo los ingresos de los egresos.", [
        ["Sí, es mío", async () => { await guardarCuits([sug.cuit]); liSug.remove(); }, true],
        ["No", () => liSug.remove()],
      ]);
    nuevo.after(liSug);
  }
  while (resLista.children.length > 30) resLista.lastElementChild.remove();
}

async function procesarVarios(archivos) {
  archivos = [...archivos].filter((f) => f.type === "application/pdf" || f.type.startsWith("image/"));
  if (!archivos.length) return;
  location.hash = "#movimientos";
  for (const f of archivos) await procesar(f); // de a uno, para no saturar el lector
  document.dispatchEvent(new Event("movimientos-cambiaron"));
  await Promise.all([cargar(), cargarCategorias()]);
}

for (const sel of ["#auto-archivos", "#auto-foto"]) {
  $(sel).addEventListener("change", (ev) => {
    const archivos = [...ev.target.files];
    ev.target.value = "";
    procesarVarios(archivos);
  });
}

// ------------------------------------------------------------ Arrastrar y soltar en cualquier parte

let arrastres = 0;
const capaSoltar = $("#soltar");
const conArchivos = (ev) => [...(ev.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (ev) => {
  if (!conArchivos(ev) || $("#dlg").open) return;
  arrastres++;
  capaSoltar.hidden = false;
});
window.addEventListener("dragleave", () => {
  if (--arrastres <= 0) { arrastres = 0; capaSoltar.hidden = true; }
});
window.addEventListener("dragover", (ev) => conArchivos(ev) && ev.preventDefault());
window.addEventListener("drop", (ev) => {
  if (!conArchivos(ev)) return;
  ev.preventDefault();
  arrastres = 0;
  capaSoltar.hidden = true;
  if (!$("#dlg").open) procesarVarios(ev.dataTransfer.files);
});

document.addEventListener("empresa-cambiada", () => {
  resLista.replaceChildren();
  auto.sugerido = false;
  cargarAjustes().catch(() => {});
});

listo.then(() => cargarAjustes()).catch(() => {});
