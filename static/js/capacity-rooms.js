(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-capacity-add-room]");
    if (!button) return;

    var fieldset = button.closest(".qa-cap__rooms");
    var list = fieldset && fieldset.querySelector("[data-capacity-room-list]");
    var template = fieldset && fieldset.querySelector("[data-capacity-room-template]");
    if (!list || !template) return;

    var maxRows = 60;
    if (list.children.length >= maxRows) {
      button.disabled = true;
      return;
    }

    var rowIndex = list.children.length;
    var row = template.content.cloneNode(true);
    row.querySelectorAll("[id], [for], [name]").forEach(function (element) {
      ["id", "for", "name"].forEach(function (attribute) {
        var value = element.getAttribute(attribute);
        if (value) element.setAttribute(attribute, value.replaceAll("__row__", String(rowIndex)));
      });
    });
    list.appendChild(row);

    if (list.children.length >= maxRows) button.disabled = true;
    var nameInput = list.lastElementChild.querySelector("input");
    if (nameInput) nameInput.focus();
  });
})();
