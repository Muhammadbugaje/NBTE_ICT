/* Small portal-only behaviors that complement the shared QA interaction kit. */
(function () {
  "use strict";

  var sectionByPath = [
    { prefix: "/application", section: "application" },
    { prefix: "/documents", section: "documents" },
    { prefix: "/payments", section: "payments" },
    { prefix: "/institution", section: "institution" },
    { prefix: "/complaints", section: "complaints" },
    { prefix: "/help", section: "help" },
    { prefix: "/scan", section: "verification" },
    { prefix: "/memos", section: "memos" },
    { prefix: "/letters", section: "letters" },
    { prefix: "/programmes", section: "programmes" },
    { prefix: "/qa-requests", section: "requests" },
    { prefix: "/queries", section: "queries" },
    { prefix: "/inspections", section: "visits" },
    { prefix: "/ssq", section: "reviews" },
    { prefix: "/timeline", section: "activity" },
    { prefix: "/alerts", section: "notifications" },
    { prefix: "/account", section: "account" },
    { prefix: "/me/preferences", section: "account" },
    { prefix: "/profile", section: "engagements" },
    { prefix: "/engagements", section: "engagements" },
    { prefix: "/service-history", section: "engagements" },
    { prefix: "/how-to", section: "help" },
    { prefix: "/guides", section: "help" },
    { prefix: "/setup", section: "institution" },
    { prefix: "/register", section: "institution" },
    { prefix: "/download", section: "documents" },
    { prefix: "/verify", section: "verification" },
    { prefix: "/v/", section: "verification" },
    { prefix: "/pb", section: "professional" }
  ];

  function syncPortalSection() {
    var path = window.location.pathname;
    var section = "dashboard";
    if (path.indexOf("/ssq/") !== -1) {
      section = "reviews";
    } else {
      for (var i = 0; i < sectionByPath.length; i += 1) {
        if (path.indexOf(sectionByPath[i].prefix) === 0) {
          section = sectionByPath[i].section;
          break;
        }
      }
    }

    [document.body, document.getElementById("qa-main")].forEach(function (element) {
      if (element) element.setAttribute("data-portal-section", section);
    });
  }

  function addHeroWatermarks(scope) {
    var template = document.getElementById("qa-hero-watermark-template");
    if (!template || !template.content.firstElementChild) return;

    var selector = ".qa-page-head, .qa-welcome, .qa-ob-hero, .cx-portal-hero, #qa-main[data-portal-section='verification'] .sv-head";
    var heroes = [];
    if (scope && scope.nodeType === 1 && scope.matches(selector)) heroes.push(scope);
    if (scope && scope.querySelectorAll) {
      Array.prototype.push.apply(heroes, scope.querySelectorAll(selector));
    }

    heroes.forEach(function (hero) {
      if (hero.hasAttribute("data-qa-hero-watermark-ready")) return;
      var watermark = document.createElement("span");
      watermark.className = "qa-hero-watermark";
      watermark.setAttribute("aria-hidden", "true");
      watermark.appendChild(template.content.firstElementChild.cloneNode(true));
      hero.appendChild(watermark);
      hero.setAttribute("data-qa-hero-watermark-ready", "true");
    });
  }

  function focusAuthError() {
    var form = document.querySelector(".qa-auth form");
    if (!form || !form.querySelector(".auth-error, [aria-invalid='true'], .input.error")) return;
    var invalid = form.querySelector("[aria-invalid='true'], .input.error");
    var password = form.querySelector('input[type="password"]');
    var target = invalid || password;
    if (target) target.focus({ preventScroll: true });
  }

  function markSubmitting(event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest(".qa-auth")) return;

    var submitter = event.submitter;
    if (submitter && submitter.formNoValidate) return;
    if (!form.checkValidity()) {
      event.preventDefault();
      form.reportValidity();
      var firstInvalid = form.querySelector(":invalid");
      if (firstInvalid) firstInvalid.focus();
      return;
    }

    if (form.dataset.qaPortalSubmitting === "true") {
      event.preventDefault();
      return;
    }

    form.dataset.qaPortalSubmitting = "true";
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach(function (button) {
      if (button.disabled) return;
      button.dataset.qaPortalBusy = "true";
      // NBTE.setBusy (ui.js): spinner + data-busy-text ("Signing in…") on the clicked button.
      if (window.NBTE && window.NBTE.setBusy && (!submitter || submitter === button)) {
        window.NBTE.setBusy(button, true, button.getAttribute("data-busy-text") || form.getAttribute("data-busy-text"));
        return;
      }
      button.classList.add("is-busy");
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
    });
  }

  function restoreAfterHistoryNavigation(event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-qa-portal-submitting='true']").forEach(function (form) {
      delete form.dataset.qaPortalSubmitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll("[data-qa-portal-busy='true']").forEach(function (button) {
        if (window.NBTE && window.NBTE.setBusy) window.NBTE.setBusy(button, false);
        button.disabled = false;
        button.classList.remove("is-busy");
        button.removeAttribute("aria-busy");
        delete button.dataset.qaPortalBusy;
      });
    });
  }

  /* v2.3 shell: the sidebar is a drawer on phones and tablets ("Menu"), and the
     top bar names the page you are on after every boosted navigation. */
  function setMenu(open) {
    document.body.classList.toggle("pp-nav-open", open);
    document.querySelectorAll("[data-pp-menu]").forEach(function (b) {
      b.setAttribute("aria-expanded", open ? "true" : "false");
    });
    if (open) {
      var first = document.querySelector("#pp-side .pp-nav__link.is-active, #pp-side .pp-nav__link");
      if (first) window.setTimeout(function () { first.focus({ preventScroll: true }); }, 60);
    }
  }

  function syncWhere() {
    window.setTimeout(function () {
      var label = document.querySelector("[data-pp-where]");
      var active = document.querySelector("#pp-side .pp-nav__link.is-active .pp-nav__label");
      if (label) label.textContent = active ? active.textContent.trim() : "";
    }, 40);
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-pp-menu]")) { setMenu(!document.body.classList.contains("pp-nav-open")); return; }
    if (event.target.closest("[data-pp-menu-close]")) { setMenu(false); return; }
    if (event.target.closest("#pp-side a")) setMenu(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && document.body.classList.contains("pp-nav-open")) setMenu(false);
  });
  document.addEventListener("htmx:pushedIntoHistory", syncWhere);
  window.addEventListener("popstate", syncWhere);

  document.addEventListener("submit", markSubmitting);
  window.addEventListener("pageshow", restoreAfterHistoryNavigation);
  window.addEventListener("popstate", syncPortalSection);
  document.addEventListener("htmx:afterSwap", function (event) {
    syncPortalSection();
    addHeroWatermarks(event.detail && event.detail.target);
  });
  document.addEventListener("htmx:pushedIntoHistory", syncPortalSection);
  document.addEventListener("htmx:replacedInHistory", syncPortalSection);
  syncPortalSection();
  addHeroWatermarks(document);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", focusAuthError, { once: true });
  } else {
    focusAuthError();
  }
})();
