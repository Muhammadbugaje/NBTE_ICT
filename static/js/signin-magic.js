/* signin-magic.js — small, optional enhancements for the sign-in pages
   (templates/accounts/login.html, templates/qa_portal/login.html) and the
   portal registration page. Everything here is progressive: with this file
   blocked the pages still render and post exactly the same.

   · time-of-day greeting                     [data-sm-greeting]
   · Caps Lock warning on password fields     [data-sm-caps]
   · accessible name for the show/hide button (app.js does the toggling)
   · a sheen on the glass card that follows the pointer
   · .is-signing on the card while the form posts (cleared on pageshow)
   · registration: show/hide buttons + Caps Lock hint on the password fields
*/
(function () {
  "use strict";

  var root = document.querySelector(".signin-magic");
  var isRegister = document.body && document.body.classList.contains("rg-magic");
  if (!root && !isRegister) return;

  var EYE = '<svg class="icon-eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>' +
            '<svg class="icon-eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function motionOk() {
    var m = document.documentElement.getAttribute("data-motion");
    if (m === "off" || m === "reduce" || m === "reduced") return false;
    return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* ---- Caps Lock (shared) ---------------------------------------------- */
  function watchCaps(input, hint) {
    if (!input || !hint) return;
    function check(e) {
      if (!e.getModifierState) return;
      hint.hidden = !e.getModifierState("CapsLock");
    }
    input.addEventListener("keydown", check);
    input.addEventListener("keyup", check);
    input.addEventListener("blur", function () { hint.hidden = true; });
  }

  /* ---- Show / hide: keep the button's accessible name in step ---------- */
  function syncRevealLabel(btn) {
    var id = btn.getAttribute("data-toggle-password");
    var input = id ? document.getElementById(id) : null;
    if (!input) return;
    var shown = input.type === "text";
    btn.setAttribute("aria-label", shown ? "Hide password" : "Show password");
    btn.setAttribute("aria-pressed", shown ? "true" : "false");
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-toggle-password]");
    if (btn) setTimeout(function () { syncRevealLabel(btn); }, 0);   // after app.js toggles the type
  });

  /* ---- Sign-in pages ---------------------------------------------------- */
  if (root) {
    var greet = root.querySelector("[data-sm-greeting]");
    if (greet) {
      var h = new Date().getHours();
      greet.textContent = h >= 5 && h < 12 ? "Good morning" : (h >= 12 && h < 17 ? "Good afternoon" : "Good evening");
    }

    Array.prototype.forEach.call(root.querySelectorAll("[data-sm-caps]"), function (hint) {
      var field = hint.closest(".sm-field");
      watchCaps(field && field.querySelector('input[name="password"]'), hint);
    });

    var card = root.querySelector(".sm-card");
    if (card && motionOk() && window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
      var frame = 0, px = 0, py = 0;
      card.addEventListener("pointermove", function (e) {
        px = e.clientX; py = e.clientY;
        if (frame) return;
        frame = requestAnimationFrame(function () {
          frame = 0;
          var r = card.getBoundingClientRect();
          card.style.setProperty("--sm-mx", (px - r.left) + "px");
          card.style.setProperty("--sm-my", (py - r.top) + "px");
          card.classList.add("is-lit");
        });
      });
      card.addEventListener("pointerleave", function () { card.classList.remove("is-lit"); });
    }

    var form = root.querySelector("form[data-loading-text]");
    if (form && card) {
      var timer = 0;
      var settle = function () { clearTimeout(timer); card.classList.remove("is-signing"); };
      form.addEventListener("submit", function (e) {
        setTimeout(function () {
          if (e.defaultPrevented) return;
          card.classList.add("is-signing");
          clearTimeout(timer);
          timer = setTimeout(settle, 8000);   // same give-back window as ui.js
        }, 0);
      });
      window.addEventListener("pageshow", settle);   // back/forward cache
    }
  }

  /* ---- Registration: show/hide + Caps Lock on the password fields ------- */
  if (isRegister) {
    var rg = document.querySelector("[data-rg-form]");
    if (rg) {
      Array.prototype.forEach.call(rg.querySelectorAll('input[type="password"]'), function (input) {
        if (!input.id || input.closest(".sm-pw")) return;
        var wrap = document.createElement("span");
        wrap.className = "sm-pw";
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "reveal";
        btn.setAttribute("data-toggle-password", input.id);
        btn.setAttribute("aria-label", "Show password");
        btn.setAttribute("aria-pressed", "false");
        btn.innerHTML = EYE;
        wrap.appendChild(btn);
        var hint = document.createElement("p");
        hint.className = "sm-caps";
        hint.setAttribute("role", "status");
        hint.hidden = true;
        hint.textContent = "Caps Lock is on";
        wrap.parentNode.insertBefore(hint, wrap.nextSibling);
        watchCaps(input, hint);
      });
    }
  }
})();
