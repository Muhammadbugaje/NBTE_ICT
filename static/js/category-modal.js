/* ==========================================================================
   Category quick-create modal
   Opens from a [data-cat-open="kind"] button, posts to the API, and
   injects the new <option> into the target <select>.
   ========================================================================== */
(function () {
  "use strict";

  var ENDPOINT = "/portal/categories/{kind}/quick-create/";

  function getCookie(name) {
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.indexOf(name + "=") === 0) {
        return decodeURIComponent(c.substring(name.length + 1));
      }
    }
    return null;
  }

  var modal = null, form = null, input = null, errEl = null;

  function ensure() {
    if (modal) return true;
    modal = document.getElementById("cat-modal");
    if (!modal) return false;
    form = document.getElementById("cat-modal-form");
    input = document.getElementById("cat-modal-name");
    errEl = document.getElementById("cat-modal-error");

    form.addEventListener("submit", onSubmit);

    modal.querySelectorAll("[data-cat-close]").forEach(function (el) {
      el.addEventListener("click", close);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) close();
    });
    return true;
  }

  function open(kind, targetId) {
    if (!ensure()) return;
    modal.setAttribute("data-kind", kind);
    modal.setAttribute("data-target", targetId);
    var label = document.getElementById("cat-modal-kind-label");
    if (label) label.textContent = kind.replace("-", " ");
    input.value = "";
    errEl.textContent = "";
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    setTimeout(function () { input.focus(); }, 60);
  }

  function close() {
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  function onSubmit(e) {
    e.preventDefault();
    var kind = modal.getAttribute("data-kind");
    var targetId = modal.getAttribute("data-target");
    var name = input.value.trim();
    errEl.textContent = "";

    if (!name) { errEl.textContent = "Name is required."; return; }
    var submitBtn = document.getElementById("cat-modal-submit");
    submitBtn.disabled = true;

    var body = "name=" + encodeURIComponent(name);
    fetch(ENDPOINT.replace("{kind}", kind), {
      method: "POST",
      headers: {
        "X-CSRFToken": getCookie("csrftoken") || "",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: body,
    })
      .then(function (r) { return r.json().then(function (d) { return [r.status, d]; }); })
      .then(function (pair) {
        var status = pair[0], data = pair[1];
        if (!data.ok) {
          errEl.textContent = data.error || "Failed.";
          submitBtn.disabled = false;
          return;
        }
        var sel = document.getElementById(targetId);
        if (sel) {
          var opt = document.createElement("option");
          opt.value = data.pk;
          opt.textContent = data.name;
          opt.selected = true;
          sel.appendChild(opt);
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
        submitBtn.disabled = false;
        close();
      })
      .catch(function () {
        errEl.textContent = "Network error.";
        submitBtn.disabled = false;
      });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-cat-open]");
    if (!btn) return;
    e.preventDefault();
    var kind = btn.getAttribute("data-cat-open");
    var target = btn.getAttribute("data-cat-target");
    if (!target) return;
    open(kind, target);
  });
})();