// Service worker mínimo: permite instalar la web como app en el celular.
// Siempre va a la red (los datos viven en el servidor); si no hay conexión
// muestra la última versión guardada de la interfaz.
const CACHE = "csa-v3";
const ESTATICOS = ["./", "index.html", "styles.css", "app.js", "dashboard.js", "carga.js", "manifest.json", "icon.svg"];

self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ESTATICOS))));
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))))
);
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const copia = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copia));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
