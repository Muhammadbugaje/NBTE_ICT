(function () {
  "use strict";

  function initActionQueue() {
    var form = document.querySelector(".action-queue-bulk-form");
    var bar = document.getElementById("action-queue-bulk-bar");
    if (!form || !bar) return;

    var boxes = Array.prototype.slice.call(
      form.querySelectorAll('input[name="request_ids"]')
    );
    var count = document.getElementById("action-queue-selected-count");
    var buttons = Array.prototype.slice.call(
      bar.querySelectorAll('button[name="bulk_action"]')
    );

    function update() {
      var selected = boxes.filter(function (box) { return box.checked; }).length;
      count.textContent = String(selected);
      bar.hidden = selected === 0;
      buttons.forEach(function (button) {
        button.disabled = selected === 0;
      });
    }

    boxes.forEach(function (box) {
      box.addEventListener("change", update);
    });
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initActionQueue, { once: true });
  } else {
    initActionQueue();
  }
})();
