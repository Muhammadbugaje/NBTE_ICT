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

    // Select mode: checkboxes stay hidden until "Select" is pressed; then a
    // "Select all" box appears and the bar slides in once something is
    // ticked. Done, Cancel or Esc hide them again and clear the ticks.
    var html = document.documentElement;
    var tools = form.querySelector("[data-aq-tools]");
    var modeBtn = form.querySelector("[data-aq-mode]");
    var allBox = form.querySelector("[data-aq-all]");
    var on = false;
    if (tools) tools.hidden = boxes.length === 0;

    function update() {
      var selected = boxes.filter(function (box) { return box.checked; }).length;
      count.textContent = String(selected);
      bar.hidden = selected === 0;
      buttons.forEach(function (button) {
        button.disabled = selected === 0;
      });
      if (allBox) {
        allBox.checked = selected > 0 && selected === boxes.length;
        allBox.indeterminate = selected > 0 && selected < boxes.length;
      }
    }
    function setMode(v) {
      on = !!v;
      html.classList.toggle("aq-selecting", on);
      if (modeBtn) {
        modeBtn.classList.toggle("is-on", on);
        modeBtn.setAttribute("aria-pressed", on ? "true" : "false");
        var l = modeBtn.querySelector("[data-aq-mode-label]");
        if (l) l.textContent = on ? "Done" : "Select";
      }
      if (!on) boxes.forEach(function (box) { box.checked = false; });
      update();
    }

    boxes.forEach(function (box) {
      box.addEventListener("change", update);
    });
    if (modeBtn) modeBtn.addEventListener("click", function () { setMode(!on); });
    if (allBox) allBox.addEventListener("change", function () {
      boxes.forEach(function (box) { box.checked = allBox.checked; });
      update();
    });
    var cancel = bar.querySelector("[data-aq-cancel]");
    if (cancel) cancel.addEventListener("click", function () { setMode(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !on) return;
      if (Array.prototype.some.call(document.querySelectorAll(".nbte-confirm, [role=dialog][aria-modal=true]:not([hidden])"), function (d) { return d.getClientRects().length > 0 && !d.closest("[aria-hidden=true], [hidden]"); })) return;
      setMode(false);
    });
    // Back/forward can restore ticks: start in select mode then.
    setMode(boxes.some(function (box) { return box.checked; }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initActionQueue, { once: true });
  } else {
    initActionQueue();
  }
})();
