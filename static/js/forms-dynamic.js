/* v2.1 X49: conditional fields for forms built with core/forms_dynamic.py.
 * Any element with data-show-if='{"field": value | [values] | true/false}' is
 * shown only while every named field in the same form has that value. The
 * server applies the same rules in clean(). Re-runs after HTMX swaps. */
(function () {
  "use strict";
  function valueOf(form, name) {
    var els = form.querySelectorAll('[name="' + name + '"]');
    if (!els.length) return "";
    var el = els[0];
    if (el.type === "checkbox") return el.checked;
    if (el.type === "radio") {
      for (var i = 0; i < els.length; i++) if (els[i].checked) return els[i].value;
      return "";
    }
    return el.value;
  }
  function truthy(v) { return v === true || ["1", "true", "yes", "on"].indexOf(String(v).toLowerCase()) >= 0; }
  function matches(expected, actual) {
    if (typeof expected === "boolean") return truthy(actual) === expected;
    if (Array.isArray(expected)) return expected.map(String).indexOf(String(actual)) >= 0;
    return String(actual) === String(expected);
  }
  function apply(form) {
    form.querySelectorAll("[data-show-if]").forEach(function (el) {
      var rules = {};
      try { rules = JSON.parse(el.getAttribute("data-show-if")); } catch (e) { return; }
      var ok = Object.keys(rules).every(function (k) { return matches(rules[k], valueOf(form, k)); });
      var wrap = el.closest("[data-field-wrap]") || el;
      wrap.hidden = !ok;
      if (el.matches("input, select, textarea")) {
        el.disabled = !ok;
        if (el.getAttribute("data-dynamic-required") === "true") {
          el.required = ok;
          el.setAttribute("aria-required", ok ? "true" : "false");
        }
      }
    });
  }
  function init(root) {
    (root || document).querySelectorAll("form").forEach(function (form) {
      if (!form.querySelector("[data-show-if]")) return;
      apply(form);
      if (form._dyn) return;
      form._dyn = true;
      form.addEventListener("change", function () { apply(form); });
      form.addEventListener("input", function () { apply(form); });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { init(); });
  else init();
  document.addEventListener("htmx:afterSettle", function (e) { init(e.target); });
})();
