(function () {
  "use strict";

  var maxUploadBytes = 5 * 1024 * 1024;
  var holdTimer = null;
  var holdTarget = null;
  var holdStartX = 0;
  var holdStartY = 0;

  function initMessageFilters() {
    document.querySelectorAll("[data-message-list]").forEach(function (list) {
      var search = list.querySelector("[data-message-search]");
      var state = list.querySelector("[data-message-state]");
      var kindGroup = list.querySelector("[data-kind-filter]");
      var count = list.querySelector("[data-filter-count]");
      var empty = list.querySelector("[data-filter-empty]");
      var kind = list.getAttribute("data-inbox-kind") || "all";
      if (!list.querySelector("[data-message-thread]")) return;

      function applyFilters() {
        // Phase 9: rows are re-read every time (live updates add/move rows).
        var rows = Array.prototype.slice.call(list.querySelectorAll("[data-message-thread]"));
        var query = search ? search.value.trim().toLocaleLowerCase() : "";
        var selectedState = state ? state.value : "all";
        var visibleCount = 0;
        var sectionCounts = new Map();

        rows.forEach(function (row) {
          var isUnread = row.getAttribute("data-message-unread") === "true";
          var rowKind = row.getAttribute("data-thread-kind") || "";
          var matchesState = selectedState === "all"
            || (selectedState === "unread" && isUnread)
            || (selectedState === "read" && !isUnread);
          var matchesKind = kind === "all"
            || (kind === "unread" && isUnread)
            || (kind === "groups" && rowKind === "group")
            || (kind === "direct" && rowKind === "direct");
          var matchesSearch = !query
            || row.textContent.toLocaleLowerCase().indexOf(query) !== -1;
          var isVisible = matchesState && matchesKind && matchesSearch;
          row.hidden = !isVisible;
          if (isVisible) visibleCount += 1;

          var section = row.closest("[data-message-section]");
          if (section) {
            sectionCounts.set(
              section,
              (sectionCounts.get(section) || 0) + (isVisible ? 1 : 0)
            );
          }
        });

        sectionCounts.forEach(function (visibleInSection, section) {
          section.hidden = visibleInSection === 0;
          var sectionCount = section.querySelector("[data-section-count]");
          if (sectionCount) sectionCount.textContent = String(visibleInSection);
        });
        if (count) {
          count.textContent = visibleCount + " conversation" + (visibleCount === 1 ? "" : "s");
        }
        if (empty) empty.hidden = visibleCount !== 0;
      }

      if (search) search.addEventListener("input", applyFilters);
      if (state) state.addEventListener("change", applyFilters);
      if (kindGroup) {
        kindGroup.addEventListener("click", function (event) {
          var chip = event.target.closest("[data-kind]");
          if (!chip) return;
          kind = chip.getAttribute("data-kind");
          list.setAttribute("data-inbox-kind", kind);
          kindGroup.querySelectorAll("[data-kind]").forEach(function (c) {
            var on = c === chip;
            c.classList.toggle("is-active", on);
            c.setAttribute("aria-pressed", on ? "true" : "false");
          });
          try {
            var url = new URL(window.location.href);
            if (kind === "all") url.searchParams.delete("filter");
            else url.searchParams.set("filter", kind);
            window.history.replaceState(null, "", url.toString());
          } catch (e) { /* old browsers: filter still applies */ }
          applyFilters();
        });
      }
      list.addEventListener("messages:refilter", applyFilters);
      applyFilters();
    });
  }

  function textOfMessage(message) {
    var content = message.querySelector("[data-message-text]");
    return content ? content.textContent.trim() : "";
  }

  function clearHoldTimer() {
    if (holdTimer) window.clearTimeout(holdTimer);
    holdTimer = null;
    holdTarget = null;
  }

  function openMenu(message) {
    var menu = message.querySelector("[data-message-menu]");
    if (!menu) return;
    document.querySelectorAll("[data-message-menu][open]").forEach(function (other) {
      if (other !== menu) other.open = false;
    });
    menu.open = true;
    var summary = menu.querySelector("summary");
    if (summary) summary.focus();
  }

  function initThread() {
    var page = document.querySelector("[data-messaging-thread]");
    if (!page) return;

    var threadHero = page.querySelector(".msg-thread-hero");
    var replyForm = page.querySelector("[data-reply-form]");
    var replyField = page.querySelector("[data-reply-to-field]");
    var replyReference = page.querySelector("[data-reply-reference]");
    var replyReferenceText = page.querySelector("[data-reply-reference-text]");
    var replyBody = page.querySelector("[data-reply-body]");

    if (threadHero && window.matchMedia("(max-width: 600px)").matches) {
      var previousScrollY = window.scrollY;
      var scrollFramePending = false;
      var scrollTrackingReady = false;
      window.setTimeout(function () {
        previousScrollY = window.scrollY;
        scrollTrackingReady = true;
      }, 500);
      window.addEventListener("scroll", function () {
        if (scrollFramePending) return;
        scrollFramePending = true;
        window.requestAnimationFrame(function () {
          var currentScrollY = window.scrollY;
          var scrollDelta = currentScrollY - previousScrollY;
          if (scrollTrackingReady && (currentScrollY < 100 || scrollDelta < -4)) {
            threadHero.classList.remove("is-hidden");
          } else if (scrollTrackingReady && scrollDelta > 4) {
            threadHero.classList.add("is-hidden");
          }
          previousScrollY = currentScrollY;
          scrollFramePending = false;
        });
      }, { passive: true });
    }

    page.addEventListener("click", function (event) {
      var replyButton = event.target.closest("[data-reply-to]");
      if (replyButton) {
        var message = replyButton.closest("[data-message]");
        if (!message || !replyField || !replyReference || !replyReferenceText) return;
        replyField.value = replyButton.getAttribute("data-reply-to");
        replyReferenceText.textContent = "Replying to: " + textOfMessage(message);
        replyReference.hidden = false;
        var details = replyButton.closest("[data-message-menu]");
        if (details) details.open = false;
        if (replyBody) replyBody.focus();
        return;
      }

      if (event.target.closest("[data-clear-reply]")) {
        if (replyField) replyField.value = "";
        if (replyReference) replyReference.hidden = true;
        if (replyBody) replyBody.focus();
        return;
      }

      var editButton = event.target.closest("[data-edit-message]");
      if (editButton) {
        var bubble = editButton.closest("[data-message]");
        var editForm = bubble && bubble.querySelector("[data-edit-form]");
        var menu = editButton.closest("[data-message-menu]");
        if (menu) menu.open = false;
        if (editForm) {
          editForm.hidden = false;
          var editInput = editForm.querySelector("textarea");
          if (editInput) {
            editInput.focus();
            editInput.setSelectionRange(editInput.value.length, editInput.value.length);
          }
        }
        return;
      }

      if (event.target.closest("[data-cancel-edit]")) {
        var form = event.target.closest("[data-edit-form]");
        if (form) form.hidden = true;
      }
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest("[data-message-menu]")) {
        document.querySelectorAll("[data-message-menu][open]").forEach(function (menu) {
          menu.open = false;
        });
      }
    });

    page.addEventListener("contextmenu", function (event) {
      var message = event.target.closest("[data-message]");
      if (!message) return;
      event.preventDefault();
      openMenu(message);
    });

    page.addEventListener("pointerdown", function (event) {
      if (event.pointerType !== "touch") return;
      var message = event.target.closest("[data-message]");
      if (!message || event.target.closest("a, button, input, textarea, summary")) return;
      clearHoldTimer();
      holdTarget = message;
      holdStartX = event.clientX;
      holdStartY = event.clientY;
      holdTimer = window.setTimeout(function () {
        if (holdTarget === message) {
          openMenu(message);
          clearHoldTimer();
        }
      }, 550);
    });

    page.addEventListener("pointermove", function (event) {
      if (!holdTimer) return;
      if (
        Math.abs(event.clientX - holdStartX) > 10
        || Math.abs(event.clientY - holdStartY) > 10
      ) clearHoldTimer();
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
      page.addEventListener(name, clearHoldTimer);
    });

    if (replyBody && replyForm) {
      replyBody.addEventListener("keydown", function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          replyForm.requestSubmit();
        }
      });
      replyBody.addEventListener("input", function () {
        replyBody.style.height = "auto";
        var maxHeight = window.matchMedia("(max-width: 600px)").matches ? 132 : 210;
        replyBody.style.height = Math.min(replyBody.scrollHeight, maxHeight) + "px";
      });
    }

  }

  function initAttachmentInputs() {
    document.querySelectorAll("[data-message-files]").forEach(function (filesInput) {
      filesInput.addEventListener("change", function () {
        var total = Array.prototype.reduce.call(filesInput.files, function (sum, file) {
          return sum + file.size;
        }, 0);
        var tooLarge = total > maxUploadBytes;
        filesInput.setCustomValidity(tooLarge ? "Attachments must total no more than 5 MB." : "");
        var summary = filesInput.form && filesInput.form.querySelector("[data-attachment-summary]");
        if (summary) {
          summary.textContent = tooLarge
            ? "Selected files exceed the 5 MB total limit."
            : filesInput.files.length
              ? Array.prototype.map.call(filesInput.files, function (file) {
                return file.name;
              }).join(", ")
              : "Up to 5 MB total · files expire after 30 days";
          summary.classList.toggle("is-invalid", tooLarge);
        }
        var picker = filesInput.closest(".msg-attachment-picker");
        if (picker) picker.classList.toggle("has-files", filesInput.files.length > 0);
      });
    });
  }

  function initSubmitFeedback() {
    document.addEventListener("submit", function (event) {
      var form = event.target;
      var filesInput = form.querySelector("[data-message-files]");
      if (filesInput && filesInput.files) {
        var total = Array.prototype.reduce.call(filesInput.files, function (sum, file) {
          return sum + file.size;
        }, 0);
        if (total > maxUploadBytes) {
          event.preventDefault();
          filesInput.setCustomValidity("Attachments must total no more than 5 MB.");
          filesInput.reportValidity();
          return;
        }
      }

      if (!form.matches("[data-reply-form], [data-compose-form]")) return;
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      form.classList.add("is-sending");
      var sendButton = form.querySelector("[data-send-button], button[type='submit']");
      if (sendButton) {
        sendButton.disabled = true;
        sendButton.setAttribute("aria-busy", "true");
        var label = sendButton.querySelector("[data-send-label]");
        if (label) label.textContent = "Sending…";
        else sendButton.classList.add("is-sending");
      }
    });
  }

  function scrollToLatestMessage() {
    var thread = document.querySelector("[data-thread]");
    if (!thread || !thread.lastElementChild) return;
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  function boot() {
    initMessageFilters();
    initThread();
    initAttachmentInputs();
    initSubmitFeedback();
    window.setTimeout(scrollToLatestMessage, 80);
    window.setTimeout(scrollToLatestMessage, 320);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
