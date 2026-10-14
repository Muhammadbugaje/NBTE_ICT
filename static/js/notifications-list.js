(function () {
  "use strict";

  function getCsrfToken(form) {
    var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function notifyError(message) {
    if (window.NBTE && window.NBTE.Toast) {
      window.NBTE.Toast.error("Could not update notification", message);
      return;
    }
    window.alert(message);
  }

  function updateCounters(unread) {
    document.querySelectorAll("[data-notif-unread-count]").forEach(function (node) {
      node.textContent = String(unread);
    });
    var markAll = document.querySelector("[data-notification-mark-all]");
    if (markAll) markAll.hidden = unread === 0;
    document.body.dispatchEvent(new CustomEvent("notificationCountChanged", {
      detail: { unread: unread }
    }));
  }

  function showEmptyState(container, filter) {
    var list = container.querySelector(".notif-list-page");
    if (list) list.hidden = true;
    var pagination = container.querySelector(".pagination");
    if (pagination) pagination.hidden = true;

    var empty = container.querySelector("[data-notification-empty]");
    if (!empty) {
      empty = document.createElement("div");
      empty.className = "notif-list-empty";
      empty.setAttribute("data-notification-empty", "");
      var title = document.createElement("strong");
      var detail = document.createElement("span");
      empty.appendChild(title);
      empty.appendChild(detail);
      container.appendChild(empty);
    }

    var titleNode = empty.querySelector("strong");
    var detailNode = empty.querySelector("span");
    titleNode.textContent = filter === "unread" ? "You’re all caught up" : "No notifications to show";
    detailNode.textContent = filter === "unread"
      ? "There are no unread notifications in this view."
      : "Your notifications will appear here.";
  }

  function setReadState(row, form, isRead) {
    row.dataset.isRead = isRead ? "true" : "false";
    row.classList.toggle("unread", !isRead);

    var button = form.querySelector("[data-notification-toggle-button]");
    if (button) button.textContent = isRead ? "Mark unread" : "Mark read";

    form.action = isRead ? form.dataset.unreadUrl : form.dataset.readUrl;
  }

  function removeRowFromFilteredView(row, container, filter) {
    var shouldRemove = (filter === "unread" && row.dataset.isRead === "true")
      || (filter === "read" && row.dataset.isRead === "false");
    if (!shouldRemove) return;

    var total = document.querySelector("[data-notif-total-count]");
    if (total) {
      total.textContent = String(Math.max(0, Number(total.textContent) - 1));
    }
    row.remove();

    if (!container.querySelector("[data-notification-row]")) {
      showEmptyState(container, filter);
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-notification-mark-all]");
    if (!form) return;

    event.preventDefault();
    var button = form.querySelector("button");
    if (!button || button.disabled) return;
    button.disabled = true;

    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": getCsrfToken(form),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json"
      }
    })
      .then(function (response) {
        return response.json().catch(function () {
          throw new Error("The server returned an invalid response.");
        }).then(function (data) {
          if (!response.ok || !data.ok) {
            throw new Error(data.error || "Please try again.");
          }
          return data;
        });
      })
      .then(function (data) {
        updateCounters(data.unread);
        form.hidden = true;
        var container = document.querySelector("#notif-list-container");
        if (!container) return;

        if (container.dataset.notificationFilter === "unread") {
          container.querySelectorAll("[data-notification-row]").forEach(function (row) {
            row.remove();
          });
          var total = document.querySelector("[data-notif-total-count]");
          if (total) total.textContent = "0";
          showEmptyState(container, "unread");
          return;
        }

        container.querySelectorAll("[data-notification-row]").forEach(function (row) {
          var toggleForm = row.querySelector("form[data-notification-toggle]");
          if (toggleForm) setReadState(row, toggleForm, true);
        });
      })
      .catch(function (error) {
        notifyError(error.message || "Please try again.");
      })
      .finally(function () {
        if (button.isConnected) button.disabled = false;
      });
  });

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-notification-toggle]");
    if (!form) return;

    event.preventDefault();
    var button = form.querySelector("[data-notification-toggle-button]");
    var row = form.closest("[data-notification-row]");
    var container = form.closest("#notif-list-container");
    if (!button || !row || !container || button.disabled) return;

    var willBeRead = row.dataset.isRead !== "true";
    button.disabled = true;

    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": getCsrfToken(form),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json"
      }
    })
      .then(function (response) {
        return response.json().catch(function () {
          throw new Error("The server returned an invalid response.");
        }).then(function (data) {
          if (!response.ok || !data.ok) {
            throw new Error(data.error || "Please try again.");
          }
          return data;
        });
      })
      .then(function (data) {
        setReadState(row, form, typeof data.is_read === "boolean" ? data.is_read : willBeRead);
        updateCounters(data.unread);
        removeRowFromFilteredView(row, container, container.dataset.notificationFilter);
      })
      .catch(function (error) {
        notifyError(error.message || "Please try again.");
      })
      .finally(function () {
        if (button.isConnected) button.disabled = false;
      });
  });
})();
