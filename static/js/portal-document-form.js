/* NBTE ICT — portal-document-form.js
   Drives the conditional sections of the portal document form
   (templates/portal/documents/form.html).

   The form marks optional blocks with
       data-scope-for="<group>"  data-scope-when="<value>"
   and portal.css keeps every .portal-scope-slot hidden until it has
   .is-visible. Nothing used to add that class, so the file upload,
   external-link and department/roles/people blocks never appeared and
   the form could not be completed. This script wires two groups:

     document-source -> the "Source type" select  (file | external)
     document        -> the "Visibility" select   (department | role | individual)

   It also previews the chosen cover image. Plain DOM, no dependencies.
*/
(function () {
  "use strict";

  function init() {
    var form = document.querySelector('form[data-scope-form="document"]');
    if (!form) return;

    var controllers = {
      "document-source": form.querySelector('[name="source_type"]'),
      "document":        form.querySelector('[name="visibility"]')
    };

    function sync(group) {
      var ctrl = controllers[group];
      if (!ctrl) return;
      var value = ctrl.value;
      var slots = form.querySelectorAll('[data-scope-for="' + group + '"]');
      Array.prototype.forEach.call(slots, function (slot) {
        slot.classList.toggle("is-visible", slot.getAttribute("data-scope-when") === value);
      });
    }

    Object.keys(controllers).forEach(function (group) {
      var ctrl = controllers[group];
      if (!ctrl) return;
      ctrl.addEventListener("change", function () { sync(group); });
      sync(group);
    });

    // ---- Cover image preview ---------------------------------------
    var coverInput = form.querySelector('input[type="file"][name="cover_image"]');
    var preview = form.querySelector("[data-portal-image-preview]");
    if (coverInput && preview) {
      coverInput.addEventListener("change", function () {
        var file = coverInput.files && coverInput.files[0];
        if (!file || !/^image\//.test(file.type)) return;
        var reader = new FileReader();
        reader.onload = function (e) {
          preview.innerHTML = "";
          var img = document.createElement("img");
          img.alt = "";
          img.src = e.target.result;
          preview.appendChild(img);
          preview.setAttribute("data-has-image", "1");
        };
        reader.readAsDataURL(file);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
