// Nur damit sich die Seite als App installieren lässt. Speichert nichts zwischen:
// die Liste kommt immer frisch aus dem Netz.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
