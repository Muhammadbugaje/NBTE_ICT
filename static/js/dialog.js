(function () {
  "use strict";

  var nextId = 0;
  var dialogQueue = Promise.resolve();

  function createButton(label, className) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    return button;
  }

  function showDialog(options) {
    return new Promise(function (resolve) {
      var id = ++nextId;
      var tone = options.tone === "danger" ? "danger" : "default";
      var root = document.createElement("div");
      root.className = "nbte-confirm";
      root.setAttribute("role", "dialog");
      root.setAttribute("aria-modal", "true");
      root.setAttribute("aria-labelledby", "nbte-confirm-title-" + id);
      root.setAttribute("aria-describedby", "nbte-confirm-body-" + id);

      var backdrop = document.createElement("button");
      backdrop.type = "button";
      backdrop.className = "nbte-confirm__backdrop";
      backdrop.setAttribute("aria-label", "Cancel confirmation");

      var panel = document.createElement("div");
      panel.className = "nbte-confirm__panel";
      if (tone === "danger") panel.classList.add("nbte-confirm__panel--danger");
      panel.setAttribute("role", "document");
      panel.tabIndex = -1;

      var brand = document.createElement("div");
      brand.className = "nbte-confirm__brand";
      var crest = document.querySelector("link[rel='icon'][type='image/svg+xml']");
      if (crest) {
        var img = document.createElement("img");
        img.src = crest.href; img.alt = "";
        brand.appendChild(img);
      }
      brand.appendChild(document.createTextNode("NBTE · Please confirm"));

      var icon = document.createElement("div");
      icon.className = "nbte-confirm__icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = options.icon || (tone === "danger" ? "!" : "✓");

      var title = document.createElement("h2");
      title.className = "nbte-confirm__title";
      title.id = "nbte-confirm-title-" + id;
      title.textContent = options.title || "Are you sure?";

      var body = document.createElement("p");
      body.className = "nbte-confirm__body";
      body.id = "nbte-confirm-body-" + id;
      body.textContent = options.body || "This action may not be reversible.";

      var actions = document.createElement("div");
      actions.className = "nbte-confirm__actions";
      var cancelButton = createButton(
        options.cancelLabel || "Cancel",
        "btn btn-ghost"
      );
      var confirmButton = createButton(
        options.confirmLabel || "Confirm",
        tone === "danger" ? "btn btn-danger" : "btn btn-primary"
      );

      actions.append(cancelButton, confirmButton);
      panel.append(brand, icon, title, body);
      if (options.note) {
        var note = document.createElement("p");
        note.className = "nbte-confirm__note";
        note.textContent = options.note;
        panel.append(note);
      }
      panel.append(actions);
      root.append(backdrop, panel);

      var previousFocus = document.activeElement;
      var previousOverflow = document.body.style.overflow;
      var closed = false;

      function close(result) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKeyDown, true);
        document.removeEventListener("focusin", onFocusIn, true);
        root.remove();
        document.body.style.overflow = previousOverflow;
        if (previousFocus && typeof previousFocus.focus === "function" &&
            previousFocus.isConnected) {
          previousFocus.focus();
        }
        resolve(result);
      }

      function onKeyDown(event) {
        if (event.key === "Escape") {
          event.preventDefault();
          close(false);
          return;
        }
        if (event.key !== "Tab") return;

        var first = cancelButton;
        var last = confirmButton;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }

      function onFocusIn(event) {
        if (!root.contains(event.target)) {
          (tone === "danger" ? cancelButton : confirmButton).focus();
        }
      }

      backdrop.addEventListener("click", function () { close(false); });
      cancelButton.addEventListener("click", function () { close(false); });
      confirmButton.addEventListener("click", function () { close(true); });
      document.addEventListener("keydown", onKeyDown, true);
      document.addEventListener("focusin", onFocusIn, true);
      document.body.style.overflow = "hidden";
      document.body.appendChild(root);
      (tone === "danger" ? cancelButton : confirmButton).focus();
    });
  }

  function open(options) {
    if (!options || typeof options !== "object") {
      return Promise.reject(new TypeError("Confirmation options are required."));
    }
    var result = dialogQueue.then(function () {
      return showDialog(options);
    });
    dialogQueue = result.then(function () {}, function () {});
    return result;
  }

  window.NBTEConfirm = { open: open };

  function initFormConfirmations() {
    var bypass = new WeakSet();
    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) {
        return;
      }
      if (bypass.has(form)) {
        bypass.delete(form);
        return;
      }

      event.preventDefault();
      if (form.dataset.confirmPending === "true") return;
      form.dataset.confirmPending = "true";
      var submitter = event.submitter;

      open({
        title: form.dataset.confirmTitle,
        body: form.dataset.confirmBody || form.dataset.confirm,
        confirmLabel: form.dataset.confirmLabel ||
          form.dataset.confirmAction || "Confirm",
        cancelLabel: form.dataset.confirmCancel,
        tone: form.dataset.confirmTone ||
          (form.dataset.confirmVariant === "neutral" ? "default" : "danger")
      }).then(function (confirmed) {
        if (!confirmed || !form.isConnected) return;
        if (typeof form.requestSubmit === "function") {
          bypass.add(form);
          window.setTimeout(function () { bypass.delete(form); }, 0);
          if (submitter && submitter.form === form) form.requestSubmit(submitter);
          else form.requestSubmit();
        } else if (!form.reportValidity || form.reportValidity()) {
          form.submit();
        }
      }).catch(function (error) {
        console.error("[NBTE] Could not open confirmation dialog", error);
      }).finally(function () {
        delete form.dataset.confirmPending;
      });
    }, true);
  }

  initFormConfirmations();

  // v2.3: every hx-confirm uses the NBTE dialog instead of the browser's.
  // Optional attributes on the element: data-confirm-title, data-confirm-label,
  // data-confirm-tone (danger|default), data-confirm-note, data-confirm-icon.
  document.addEventListener("htmx:confirm", function (event) {
    var question = event.detail && event.detail.question;
    if (!question) return;
    event.preventDefault();
    var el = event.detail.elt || event.target;
    var ds = (el && el.dataset) || {};
    var guess = /delete|remove|revoke|reject|decline|cancel|disable|stop/i.test(question) ? "danger" : "default";
    open({
      title: ds.confirmTitle || "Are you sure?",
      body: question,
      confirmLabel: ds.confirmLabel || (guess === "danger" ? "Yes, continue" : "Confirm"),
      cancelLabel: ds.confirmCancel || "Cancel",
      tone: ds.confirmTone || guess,
      note: ds.confirmNote,
      icon: ds.confirmIcon
    }).then(function (ok) {
      if (ok) event.detail.issueRequest(true);
    });
  });
})();
