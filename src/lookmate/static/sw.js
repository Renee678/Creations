// Lookmate service worker: makes the app installable and opens the app shell offline.
// It only ever caches the static shell. API calls, uploads, try-on pictures and product photos
// always go to the network, so nothing personal is stored here and results are never stale.
const CACHE = "lookmate-shell-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/logo.svg", "/static/manifest.webmanifest",
  "/static/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const isShell = e.request.method === "GET" && url.origin === location.origin && SHELL.includes(url.pathname);
  if (!isShell) return;  // everything else: straight to the network, never cached
  // Network first, so a new deploy shows up immediately; the cached copy is only the offline fallback.
  e.respondWith(fetch(e.request)
    .then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return res; })
    .catch(() => caches.match(e.request)));
});
