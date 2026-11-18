/* v2.3 Phase 1 (D9) — boost guard: a boosted link must never leave the page stuck.
 *
 * Boosted navigation (hx-boost="true", hx-target="#qa-main") swaps the next
 * page into the current one. When that request fails, times out, comes back
 * as a whole HTML document (sign-in page, error page, a view that ignored the
 * partial), or is redirected to the other host, the old behaviour was a toast
 * or an inline error inside a half-swapped page. The page looked frozen,
 * while the same URL opened in a new tab worked.
 *
 * This guard turns every such case into an ordinary full page load of the
 * link the person clicked. It runs in the capture phase on document, so it
 * acts before the general handlers in app.js and qa.js (which still handle
 * every non-boosted request exactly as before).
 *
 * Only boosted GETs are touched. Boosted form posts keep their own handling.
 */
(function () {
  "use strict";
  if (window.NBTE && window.NBTE.boostGuard) return; // loaded twice (shell + page): run once

  var SLOW_MS = 10000; // a boosted page that takes longer than this loads normally instead
  var FULL_DOC = /^\s*(<!--[\s\S]*?-->\s*)*<!doctype html|<html[\s>]/i;

  function isBoostedGet(d) {
    if (!d) return false;
    var cfg = d.requestConfig || {};
    var boosted = d.boosted || cfg.boosted;
    return !!boosted && String(cfg.verb || "get").toLowerCase() === "get";
  }

  function pathOf(d) {
    var p = (d.pathInfo && (d.pathInfo.requestPath || d.pathInfo.finalRequestPath)) ||
            (d.requestConfig && d.requestConfig.path) || "";
    if (!p && d.elt && d.elt.getAttribute) p = d.elt.getAttribute("href") || "";
    return p;
  }

  var leaving = false;
  function fullLoad(evt, why, url) {
    var d = evt.detail || {};
    var target = url || pathOf(d);
    if (!target || leaving) return false;
    leaving = true;
    d.shouldSwap = false;
    if (evt.stopPropagation) evt.stopPropagation();
    try { console.info("[boost-guard] full page load (" + why + "): " + target); } catch (e) { /* no console */ }
    window.location.assign(target);
    return true;
  }

  function crossOrigin(xhr) {
    if (!xhr || !xhr.responseURL) return false;
    try { return new URL(xhr.responseURL).origin !== window.location.origin; } catch (e) { return false; }
  }

  // Slow-request watchdog: start a timer for each boosted GET.
  document.addEventListener("htmx:beforeRequest", function (evt) {
    var d = evt.detail || {};
    if (!isBoostedGet(d) || !d.xhr) return;
    var xhr = d.xhr;
    xhr.__nbteBoostTimer = window.setTimeout(function () {
      if (xhr.readyState === 4) return;
      try { xhr.abort(); } catch (e) { /* already finished */ }
      fullLoad(evt, "slow", pathOf(d));
    }, SLOW_MS);
  }, true);

  document.addEventListener("htmx:afterRequest", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    if (xhr && xhr.__nbteBoostTimer) { window.clearTimeout(xhr.__nbteBoostTimer); xhr.__nbteBoostTimer = null; }
  }, true);

  document.addEventListener("htmx:beforeSwap", function (evt) {
    var d = evt.detail || {};
    if (!isBoostedGet(d)) return;
    var xhr = d.xhr;
    if (!xhr) return;
    if (xhr.status >= 400) { fullLoad(evt, "status " + xhr.status); return; }
    if (crossOrigin(xhr)) { fullLoad(evt, "other host", xhr.responseURL); return; }
    var head = String(xhr.responseText || "").slice(0, 2000);
    if (FULL_DOC.test(head)) {
      // A whole document cannot live inside #qa-main: load it as a page.
      fullLoad(evt, "full document", xhr.responseURL || pathOf(d));
    }
  }, true);

  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, function (evt) {
      if (isBoostedGet(evt.detail)) fullLoad(evt, name.replace("htmx:", ""));
    }, true);
  });

  // Back/forward from the bfcache: a stale "leaving" flag must not block the next click.
  window.addEventListener("pageshow", function () { leaving = false; });

  window.NBTE = window.NBTE || {};
  window.NBTE.boostGuard = { isBoostedGet: isBoostedGet, FULL_DOC: FULL_DOC, SLOW_MS: SLOW_MS };
})();
