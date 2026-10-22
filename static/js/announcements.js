/* Phase 9: announcements form — show the department picker or the role
   checkboxes only when that audience is chosen. */
(function () {
  "use strict";
  function sync(box) {
    var checked = box.querySelector('input[name="audience"]:checked');
    var value = checked ? checked.value : "all_staff";
    var dept = box.querySelector("[data-ann-when-dept]");
    var roles = box.querySelector("[data-ann-when-roles]");
    if (dept) dept.hidden = value !== "department";
    if (roles) roles.hidden = value !== "roles";
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-ann-audience]").forEach(function (box) {
      sync(box);
      box.addEventListener("change", function (e) {
        if (e.target && e.target.name === "audience") sync(box);
      });
    });
  });
})();
