/**
 * The field app's service worker — §E21, §E25 Phase 22, F17.
 *
 * **This worker does not own the queue.** ADR-0056 rejected Background Sync
 * because Safari has never shipped it, and everything durable lives in
 * IndexedDB where the page can reach it. What is left for a worker is the
 * narrow thing it is actually good at: making the app *open* when there is no
 * network, so a field hand in a basement can still photograph a job.
 *
 * Two strategies, and the split matters:
 *
 * · **Navigations are network-first, cache-fallback.** A field app that served
 *   a stale shell after a deploy would be a field app running last week's
 *   capture logic against this week's contract. So the network wins whenever it
 *   answers, and the cache exists for the case where it does not answer at all.
 *
 * · **Static build assets are cache-first.** Next fingerprints everything under
 *   `/_next/static/`, so a hit is provably the right bytes and a miss fetches
 *   and stores. This is what makes a cold offline open work rather than render
 *   an unstyled page.
 *
 * **Nothing else is touched.** Not `/api/*` — a cached mutation is a
 * catastrophe and a cached read is a lie about a live system. Not anything
 * that is not a GET. Not cross-origin requests. When in doubt this worker does
 * nothing at all, which is always safe.
 *
 * The cache name carries a version. Bumping it drops the old cache on the next
 * activation, which is the only cache invalidation strategy that has ever
 * worked.
 */

const CACHE = "nemesis-field-v1";

/** The smallest set that makes `/field` open with no network. */
const SHELL = ["/field", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // `addAll` rejects the whole install if any one request fails, which on
      // a first load behind a captive portal would leave the app with no
      // worker at all. Each is added independently and a failure is survivable:
      // the worker still installs and the shell fills in on first visit.
      Promise.all(
        SHELL.map((url) =>
          cache.add(url).catch(() => {
            /* one asset short of a shell is still a working worker */
          }),
        ),
      ),
    ),
  );
  // Take over immediately rather than waiting for every tab to close. A field
  // phone has one tab and waiting means the worker never activates.
  void self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Never a mutation, never a cross-origin request, never a non-GET.
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // The BFF. A cached read of a live system is a lie about it, and a cached
  // mutation does not bear thinking about (ADR-0040).
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }

  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/fonts/")) {
    event.respondWith(cacheFirst(request));
  }
});

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    // Only a real 200. An opaque or errored response cached here is a shell
    // that will not render, served forever after.
    if (response.ok && response.type === "basic") {
      const cache = await caches.open(CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached !== undefined) return cached;
    // The last resort: the shell itself, which is what an installed icon opens
    // and what a field hand in a dead zone actually needs on the screen.
    const shell = await caches.match("/field");
    if (shell !== undefined) return shell;
    throw new Error("offline, and no cached shell");
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached !== undefined) return cached;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}
