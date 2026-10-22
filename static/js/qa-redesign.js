/* QA redesign helpers (Phase 8 UI). Small and dependency-free.
   - Notification template editor: clicking a value chip inserts the
     placeholder where the cursor was (subject or message), then fires
     `tpl:insert` so the live preview refreshes.
*/
(function () {
  "use strict";

  var lastField = new WeakMap();

  document.addEventListener("focusin", function (e) {
    var el = e.target;
    if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) return;
    var form = el.closest("[data-tpl-form]");
    if (form && (el.name === "subject" || el.name === "body")) lastField.set(form, el);
  });

  document.addEventListener("click", function (e) {
    var chip = e.target.closest && e.target.closest("[data-tpl-insert]");
    if (!chip || chip.disabled) return;
    var form = chip.closest("[data-tpl-form]");
    if (!form) return;
    var field = lastField.get(form) || form.querySelector("textarea[name=body]");
    if (!field || field.readOnly) return;
    var text = chip.getAttribute("data-tpl-insert");
    var start = field.selectionStart == null ? field.value.length : field.selectionStart;
    var end = field.selectionEnd == null ? start : field.selectionEnd;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    var caret = start + text.length;
    field.focus();
    try { field.setSelectionRange(caret, caret); } catch (err) { /* some inputs refuse */ }
    chip.classList.add("is-inserted");
    window.setTimeout(function () { chip.classList.remove("is-inserted"); }, 600);
    form.dispatchEvent(new CustomEvent("tpl:insert", { bubbles: true }));
  });
})();
