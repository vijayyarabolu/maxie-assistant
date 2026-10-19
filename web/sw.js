// Service worker: shows push notifications and keeps the app shell loadable when the Mac is asleep.
const SHELL = "maxie-shell-v10";  // bump on every shell change
const FILES = ["/", "/index.html", "/styles.css", "/app.js", "/maxie-fur.js", "/maxie.js", "/mascot.js", "/character.js", "/vendor/three.min.js", "/icon-192.png",
               "/badge-72.png", "/manifest.webmanifest", "/settings.html", "/settings.js", "/signin.html"];

self.addEventListener("install", (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname === "/health") return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request).then((r) => r || caches.match("/"))));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: "Maxie", body: e.data ? e.data.text() : "" }; }
  e.waitUntil((async () => {
    await self.registration.showNotification(d.title || "Maxie", { body: d.body, tag: d.tag || undefined, icon: "/icon-192.png", badge: "/badge-72.png" });
    for (const c of await self.clients.matchAll()) c.postMessage({ mood: d.mood });  // proud / happy / sad
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs[0] ? cs[0].focus() : self.clients.openWindow("/"))));
});
