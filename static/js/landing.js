/* ==========================================================================
   NBTE ICT — Landing page JS (v3)
   Fixes in v3:
   - Sign-in works even if NBTE namespace isn't ready yet
   - Inline onclick fallback on buttons themselves (in the template)
   - Expander toggle more forgiving of selector variants
   - Error logging so console shows what failed
   ========================================================================== */

(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    try { initMobileNav(); } catch (e) { console.error("[landing] nav", e); }
    try { initModalTriggers(); } catch (e) { console.error("[landing] modal", e); }
    try { initExpanders(); } catch (e) { console.error("[landing] expander", e); }
    try { initSigninForm(); } catch (e) { console.error("[landing] signin form", e); }
  });

  /* ---------- Mobile nav ---------- */
  function initMobileNav() {
    var burger = document.querySelector("[data-nav-burger]");
    var sheet = document.querySelector("[data-nav-sheet]");
    if (!burger || !sheet) return;

    burger.addEventListener("click", function () {
      var open = sheet.classList.toggle("open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });

    sheet.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        sheet.classList.remove("open");
        burger.setAttribute("aria-expanded", "false");
      });
    });
  }

  /* ---------- Modal triggers (fallback if inline onclick missing) ---------- */
  function initModalTriggers() {
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest("[data-open-modal]");
      if (!trigger) return;
      var id = trigger.getAttribute("data-open-modal");
      if (!id) return;
      if (trigger.tagName === "A" && trigger.hasAttribute("href") && trigger.getAttribute("href") !== "#") {
        return; // let the link navigate
      }
      e.preventDefault();
      if (window.NBTE && window.NBTE.Modal) {
        window.NBTE.Modal.open(id);
      } else if (window.NBTEModal) {
        window.NBTEModal.open(id);
      } else {
        console.warn("[landing] modal controller not ready for", id);
      }
    });

    // Close buttons + backdrop
    document.querySelectorAll("[data-close-modal]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        var id = el.getAttribute("data-close-modal") || (el.closest(".modal-root") && el.closest(".modal-root").id);
        if (!id) return;
        e.preventDefault();
        if (window.NBTE && window.NBTE.Modal) window.NBTE.Modal.close(id);
        else if (window.NBTEModal) window.NBTEModal.close(id);
      });
    });

    document.querySelectorAll(".modal-root").forEach(function (root) {
      root.addEventListener("click", function (e) {
        if (e.target === root || e.target.classList.contains("modal-backdrop")) {
          if (window.NBTE && window.NBTE.Modal) window.NBTE.Modal.close(root.id);
          else if (window.NBTEModal) window.NBTEModal.close(root.id);
        }
      });
    });
  }

  /* ---------- Expand "View more" ---------- */
  function initExpanders() {
    document.querySelectorAll("[data-expander-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var sel = btn.getAttribute("data-expander-toggle");
        if (!sel) return;
        var target = null;
        try { target = document.querySelector(sel); } catch (e) {}
        if (!target) return;
        var open = target.getAttribute("aria-hidden") === "true";
        target.setAttribute("aria-hidden", open ? "false" : "true");
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        var label = btn.querySelector("[data-expander-label]");
        if (label) label.textContent = open ? "Show less" : "View all";
        var chev = btn.querySelector("[data-expander-chevron]");
        if (chev) chev.style.transform = open ? "rotate(180deg)" : "rotate(0deg)";
      });
    });
  }

  /* ---------- Sign-in form (progressive enhancement) ---------- */
  function initSigninForm() {
    var form = document.querySelector("[data-signin-form]");
    if (!form) return;
    // If inside a modal, rely on native submit → /accounts/login/
    // (no fetch — the redirect to home after login is handled by Django).
    // Only intercept in standalone pages if you want in-place errors.
    // For now, leave native submission intact — it's more robust.
    return;
  }
})();