/* TVET Support action pages (templates/institutions/_action_panel.html).
   Edit Programme: shows what the chosen programme says today, from the JSON
   the page already carries (#vx-prog-facts) - no request. Loaded (defer) by
   templates/qa_core/_shell.html. No inline handlers, no eval (CSP). */
(function () {
  "use strict";
  function facts() {
    var node = document.getElementById("vx-prog-facts");
    if (!node) return null;
    try { return JSON.parse(node.textContent || "{}"); } catch (e) { return null; }
  }
  function render() {
    var card = document.querySelector("[data-vx-progcard]");
    if (!card) return;
    var form = card.closest("form");
    var pick = form && form.querySelector("[name=programme]");
    var data = facts();
    var row = data && pick && pick.value ? data[pick.value] : null;
    card.hidden = !row;
    if (!row) return;
    ["name", "level", "category", "body", "master", "status"].forEach(function (key) {
      var slot = card.querySelector('[data-vx-prog="' + key + '"]');
      if (!slot) return;
      var value = row[key] || "";
      slot.textContent = value || (key === "master" ? "Not linked to the NBTE list" : "Not set");
      slot.classList.toggle("is-empty", !value);
    });
    var after = card.querySelector("[data-vx-prog-after]");
    var source = form.querySelector("[name=source]");
    if (!after || !source) return;
    var opt = source.options && source.selectedIndex >= 0 ? source.options[source.selectedIndex] : null;
    var label = "";
    if (opt && opt.value === "custom") {
      var name = form.querySelector("[name=name]");
      label = name && name.value.trim() ? name.value.trim() : "the details you type below";
    } else if (opt && opt.value) {
      label = opt.textContent.trim();
    }
    after.hidden = !label;
    var slot = after.querySelector('[data-vx-prog="after"]');
    if (slot) slot.textContent = label;
  }
  function onEdit(event) {
    var t = event.target;
    if (t && t.name && (t.name === "programme" || t.name === "source" || t.name === "name") && t.closest(".qa-actform")) render();
  }
  document.addEventListener("change", onEdit);
  document.addEventListener("input", onEdit);
  document.addEventListener("htmx:afterSettle", render);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render);
  else render();
})();
