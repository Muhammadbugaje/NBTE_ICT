(function () {
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-add-table-row]");
    if (!button) return;
    var field = button.closest(".qa-field");
    var table = field && field.querySelector("[data-report-table]");
    var body = table && table.querySelector("tbody");
    var source = body && body.lastElementChild;
    if (!source) return;

    var maxRows = Number(table.dataset.maxRows || 200);
    if (body.rows.length >= maxRows) {
      button.disabled = true;
      return;
    }

    var index = body.rows.length;
    var row = source.cloneNode(true);
    row.querySelectorAll("input, select, textarea").forEach(function (control) {
      control.name = control.name.replace(/^(f__.+)__\d+__(.+)$/, "$1__" + index + "__$2");
      if (control.tagName === "SELECT") control.selectedIndex = 0;
      else if (control.type === "checkbox" || control.type === "radio") control.checked = false;
      else control.value = "";
      if (control.hasAttribute("aria-label")) {
        control.setAttribute("aria-label", control.getAttribute("aria-label").replace(/row \d+/, "row " + (index + 1)));
      }
    });
    body.appendChild(row);
    if (body.rows.length >= maxRows) button.disabled = true;
    var firstControl = row.querySelector("input, select, textarea");
    if (firstControl) firstControl.focus();
  });
})();
