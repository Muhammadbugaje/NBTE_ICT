"""NBTE service worker and offline page.

``/sw.js`` is served from the site root (scope ``/``) on both hosts — the
internal system and the QA portal. Registering it replaces any service worker
another app left on the same address (e.g. a local EE Hub on localhost:8000,
whose offline page then showed for NBTE), and on activation it deletes every
cache that is not NBTE's.

The worker only steps in when a page cannot load: navigations go to the
network, and only if that fails is the cached ``/offline/`` page shown.
Nothing else is cached, so signed-in pages are never served stale.
"""
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache

CACHE_VERSION = "nbte-offline-v1"

SW_TEMPLATE = """/* NBTE service worker: offline page only (core/pwa.py). */
const CACHE = "%(cache)s";
const OFFLINE_URL = "/offline/";
const PRECACHE = [OFFLINE_URL, "%(logo)s", "%(icon)s"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // Remove every cache that is not this version of NBTE's (old app caches included).
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  const url = new URL(req.url);
  if (url.origin === self.location.origin && PRECACHE.includes(url.pathname)) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});
"""


@never_cache
def service_worker(request):
    body = SW_TEMPLATE % {"cache": CACHE_VERSION, "logo": static("img/NBTE_logo.png"),
                          "icon": static("img/brand/nbte-icon-180.png")}
    resp = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "offline.html", {"logo": static("img/NBTE_logo.png")})
