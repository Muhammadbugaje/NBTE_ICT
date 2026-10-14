/* ==========================================================================
   NBTE ICT — Landing page JS (v4)
   - No inline JS in the template. Modal fallback uses data-login-url.
   - Mobile nav, modal triggers, expanders, sign-in form.
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

    sheet.querySelectorAll("a, button").forEach(function (el) {
      el.addEventListener("click", function () {
        // Don't close the sheet if this is the sign-in trigger — the modal
        // handler needs to run first.
        if (el.hasAttribute("data-open-modal")) return;
        sheet.classList.remove("open");
        burger.setAttribute("aria-expanded", "false");
      });
    });
  }

  /* ---------- Modal triggers (no inline JS) ---------- */
  function initModalTriggers() {
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest("[data-open-modal]");
      if (!trigger) return;
      var id = trigger.getAttribute("data-open-modal");
      if (!id) return;

      // If it's a real link with a real href, let it navigate.
      if (trigger.tagName === "A"
          && trigger.hasAttribute("href")
          && trigger.getAttribute("href") !== "#") {
        return;
      }

      e.preventDefault();

      var opened = false;
      if (window.NBTE && window.NBTE.Modal) {
        opened = window.NBTE.Modal.open(id);
      } else if (window.NBTEModal) {
        opened = window.NBTEModal.open(id);
      }

      // Fallback: navigate to the login page if no modal controller exists.
      if (!opened) {
        var loginUrl = trigger.getAttribute("data-login-url");
        if (loginUrl) {
          window.location.href = loginUrl;
        } else {
          console.warn("[landing] modal controller not ready for", id);
        }
      }
    });

    // Close buttons + backdrop
    document.querySelectorAll("[data-close-modal]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        var id = el.getAttribute("data-close-modal")
                 || (el.closest(".modal-root") && el.closest(".modal-root").id);
        if (!id) return;
        e.preventDefault();
        if (window.NBTE && window.NBTE.Modal) window.NBTE.Modal.close(id);
        else if (window.NBTEModal) window.NBTEModal.close(id);
      });
    });

    // Click on backdrop closes the modal.
    document.querySelectorAll(".modal-root").forEach(function (root) {
      root.addEventListener("click", function (e) {
        if (e.target === root || e.target.classList.contains("modal-backdrop")) {
          if (window.NBTE && window.NBTE.Modal) window.NBTE.Modal.close(root.id);
          else if (window.NBTEModal) window.NBTEModal.close(root.id);
        }
      });
    });
  }

  /* ---------- "View all" expanders ---------- */
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

  /* ---------- Sign-in form (progressive enhancement) ----------
     The modal form is a plain POST to /accounts/login/. Django redirects
     to the dashboard on success, or re-renders the login page with errors.
     We leave native submission intact — it's more robust than fetch here. */
  function initSigninForm() {
    // No-op for now. Kept as a hook for future in-place error rendering.
  }
})();