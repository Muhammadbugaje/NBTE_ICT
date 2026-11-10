/* NBTE — chat-groups.js (v2.1 Phase E: L3, L4, L6, L7)
   Group info sheet (name/photo/description, settings, members, invite links,
   leave), in-chat pins (message menu + pinned bar) and "Remind me" sheet.
   Every action posts the same forms that work without JS; the server answers
   JSON with fresh HTML (messaging/group_views.py). */
(function () {
  'use strict';

  function csrf() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function send(url, body, method) {
    return fetch(url, {
      method: method || 'POST', body: body, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json', 'X-CSRFToken': csrf() },
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Unexpected server response.' }; });
    });
  }

  function el(html) {
    var t = document.createElement('template');
    t.innerHTML = (html || '').trim();
    return t.content.firstElementChild;
  }

  function toast(root, text, isError) {
    if (root && root.chatToast) { root.chatToast(text, isError); return; }
    if (isError) window.alert(text);
  }

  function openSheet(dlg) {
    if (!dlg) return;
    if (dlg.open) return;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
  }

  function closeSheet(dlg) {
    if (!dlg || !dlg.open) return;
    if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open');
  }

  /* Confirmation from inside a modal sheet: the site confirm dialog lives
     outside the top layer, so the sheet closes while it asks and comes back. */
  function confirmFrom(dlg, form) {
    var msg = form.getAttribute('data-gconfirm');
    if (!msg) return Promise.resolve(true);
    if (!window.NBTEConfirm) return Promise.resolve(window.confirm(msg));
    var wasOpen = dlg && dlg.open;
    if (wasOpen) closeSheet(dlg);
    return window.NBTEConfirm.open({
      title: form.getAttribute('data-gconfirm-title') || 'Are you sure?',
      body: msg,
      confirmLabel: form.getAttribute('data-gconfirm-action') || 'Confirm',
      tone: 'danger',
    }).then(function (ok) { if (wasOpen) openSheet(dlg); return ok; });
  }

  // ---------------------------------------------------------- info sheet
  function initInfo(root) {
    var dlg = root.querySelector('[data-group-info-dialog]');
    var body = dlg ? dlg.querySelector('[data-group-info-body]') : root.querySelector('[data-ginfo]');
    var url = root.dataset.infoUrl;

    function fill(html) {
      if (!body || !html) return;
      if (dlg) body.innerHTML = html;
      else { var fresh = el(html); var cur = root.querySelector('[data-ginfo]'); if (fresh && cur) cur.replaceWith(fresh); }
    }

    function load() {
      if (!url) return;
      if (body && dlg) body.innerHTML = '<p class="muted">Loading…</p>';
      fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok) fill(d.html); else fill('<p class="muted">' + (d.error || 'Could not load.') + '</p>'); })
        .catch(function () { fill('<p class="muted">Could not load the group info. Check your connection.</p>'); });
    }

    root.addEventListener('click', function (e) {
      var opener = e.target.closest('[data-group-info-open]');
      if (opener && dlg) {
        e.preventDefault();
        var menu = opener.closest('details[open]');
        if (menu) menu.open = false;
        openSheet(dlg);
        load();
        return;
      }
      var copy = e.target.closest('[data-invite-copy]');
      if (copy) {
        var input = copy.closest('[data-invite]').querySelector('[data-invite-url]');
        var done = function () { toast(root, 'Invite link copied.'); };
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
        else { input.select(); try { document.execCommand('copy'); done(); } catch (err) { /* manual copy */ } }
        return;
      }
      var jump = e.target.closest('[data-jump-pinned]');
      if (jump && dlg) {
        e.preventDefault();
        closeSheet(dlg);
        jumpTo(root, jump.getAttribute('data-jump-pinned'));
      }
    });

    // Forms inside the sheet: fetch, then swap the sheet's HTML.
    (dlg || root).addEventListener('submit', function (e) {
      var form = e.target;
      if (!form.matches('[data-ginfo-form], [data-ginfo-action]')) return;
      e.preventDefault();
      e.stopPropagation();
      var data = new FormData(form);
      if (e.submitter && e.submitter.name) data.append(e.submitter.name, e.submitter.value);
      confirmFrom(dlg, form).then(function (ok) {
        if (!ok) return;
        var btns = form.querySelectorAll('button');
        btns.forEach(function (b) { b.disabled = true; });
        send(form.action, data).then(function (d) {
          btns.forEach(function (b) { b.disabled = false; });
          if (!d.ok) { toast(root, d.error || 'That did not work.', true); return; }
          if (d.left || d.redirect) { window.location.href = d.redirect || root.dataset.inboxUrl || '/messages/'; return; }
          if (d.html) fill(d.html);
          if (d.head && root.chatApplyHead) root.chatApplyHead(d.head);
          if (d.pinned_html && root.chatApplyHead) root.chatApplyHead({ pinned_html: d.pinned_html, can_send: !root.hasAttribute('data-read-only'), can_pin: root.hasAttribute('data-can-pin') });
          if (d.html && d.message_id && root.chatReplaceMessage) root.chatReplaceMessage(d.message_id, d.html);
          if (d.notice) toast(root, d.notice);
          if (root.chatPollNow) root.chatPollNow();
          if (d.invite_url) {
            var input = (dlg || root).querySelector('[data-invite-url]');
            if (input) { input.focus(); input.select(); }
          }
        }).catch(function () {
          btns.forEach(function (b) { b.disabled = false; });
          toast(root, 'Network problem. Try again.', true);
        });
      });
    }, true);

    // Add members: search staff, tick, add.
    var searchTimer = null;
    (dlg || root).addEventListener('input', function (e) {
      var input = e.target.closest('[data-member-search]');
      if (!input) return;
      var form = input.closest('[data-member-add]');
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { searchPeople(form, input.value); }, 250);
    });
    (dlg || root).addEventListener('change', function (e) {
      var form = e.target.closest('[data-member-add]');
      if (!form) return;
      var n = form.querySelectorAll('input[name=users]:checked').length;
      var btn = form.querySelector('[data-member-add-submit]');
      if (btn) { btn.disabled = !n; btn.textContent = n > 1 ? 'Add ' + n + ' people' : 'Add to group'; }
    });

    function searchPeople(form, q) {
      var box = form.querySelector('[data-member-results]');
      var url = form.getAttribute('data-candidates-url') + '?q=' + encodeURIComponent(q || '');
      var ticked = {};
      form.querySelectorAll('input[name=users]:checked').forEach(function (c) { ticked[c.value] = c.closest('label').outerHTML; });
      fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var html = Object.keys(ticked).map(function (k) { return ticked[k]; }).join('');
          (d.people || []).forEach(function (p) {
            if (ticked[String(p.id)]) return;
            var dept = p.department ? '<small class="muted">' + escapeHtml(p.department) + '</small>' : '';
            html += '<label class="msg-sheet-option"><input type="checkbox" name="users" value="' + p.id + '">' +
              '<span class="msg-avatar msg-avatar-sm msg-avatar-c3" aria-hidden="true">' + escapeHtml(p.initials) + '</span>' +
              '<span class="msg-sheet-option-text">' + escapeHtml(p.name) + dept + '</span></label>';
          });
          box.innerHTML = html || '<p class="muted msg-ginfo-empty">' + (d.error ? escapeHtml(d.error) : 'No one else to add.') + '</p>';
        });
    }

    if (!dlg) return;
    dlg.addEventListener('click', function (e) {
      if (e.target === dlg || e.target.closest('[data-sheet-close]')) closeSheet(dlg);
    });
  }

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function jumpTo(root, id) {
    var target = document.getElementById('m' + id);
    if (target) {
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target.classList.add('is-flash');
      setTimeout(function () { target.classList.remove('is-flash'); }, 1600);
    } else {
      window.location.hash = 'm' + id;
    }
  }

  // ---------------------------------------------------------- pinned bar
  function initPins(root) {
    root.addEventListener('click', function (e) {
      var bar = e.target.closest('[data-pinned-cycle]');
      if (!bar) return;
      var items = Array.prototype.slice.call(bar.querySelectorAll('[data-pinned-item]'));
      var ticks = Array.prototype.slice.call(bar.querySelectorAll('.msg-pinned-ticks > span'));
      var cur = items.findIndex(function (i) { return !i.hidden; });
      jumpTo(root, items[cur].getAttribute('data-message-id'));
      if (items.length > 1) {
        var next = (cur + 1) % items.length;
        items.forEach(function (i, n) { i.hidden = n !== next; });
        ticks.forEach(function (t, n) { t.classList.toggle('is-current', n === next); });
      }
    });

    // Pin / unpin from the message menu.
    root.addEventListener('submit', function (e) {
      var form = e.target.closest('[data-pin-msg]');
      if (!form) return;
      e.preventDefault();
      var menu = form.closest('details');
      if (menu) menu.open = false;
      send(form.action, new FormData(form)).then(function (d) {
        if (!d.ok) { toast(root, d.error || 'Could not pin this message.', true); return; }
        if (root.chatReplaceMessage && d.html) root.chatReplaceMessage(d.message_id, d.html);
        if (root.chatApplyHead) root.chatApplyHead({ pinned_html: d.pinned_html, can_send: !root.hasAttribute('data-read-only'), can_pin: root.hasAttribute('data-can-pin') });
        toast(root, d.notice);
        if (root.chatPollNow) root.chatPollNow();
      });
    });
  }

  // ---------------------------------------------------------- reminders
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function localValue(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function initReminders(root) {
    var dlg = root.querySelector('[data-remind-dialog]');
    if (!dlg) return;
    var form = dlg.querySelector('[data-remind-form]');
    var at = dlg.querySelector('[data-remind-at]');
    var quote = dlg.querySelector('[data-remind-quote]');
    var current = dlg.querySelector('[data-remind-current]');
    var cancelBtn = dlg.querySelector('[data-remind-cancel]');
    var actionUrl = '';

    root.addEventListener('click', function (e) {
      var opener = e.target.closest('[data-remind-open]');
      if (!opener) return;
      e.preventDefault();
      var msg = opener.closest('[data-message]');
      if (!msg) return;
      var menu = opener.closest('details');
      if (menu) menu.open = false;
      var id = msg.getAttribute('data-message-id');
      actionUrl = '/messages/' + root.dataset.threadId + '/message/' + id + '/remind/';
      var text = msg.querySelector('[data-message-text]');
      quote.textContent = text ? text.textContent.trim().slice(0, 160) : 'This message';
      var existing = opener.getAttribute('data-remind-at');
      var soon = new Date(Date.now() + 3600 * 1000);
      at.min = localValue(new Date(Date.now() + 60 * 1000));
      at.value = localValue(existing ? new Date(existing) : soon);
      current.hidden = !existing;
      cancelBtn.hidden = !existing;
      if (existing) current.textContent = 'Reminder set for ' + new Date(existing).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) + '.';
      openSheet(dlg);
    });

    function submit(data) {
      send(actionUrl, data).then(function (d) {
        if (!d.ok) { toast(root, d.error || 'Could not set the reminder.', true); return; }
        closeSheet(dlg);
        if (root.chatReplaceMessage && d.html) root.chatReplaceMessage(d.message_id, d.html);
        toast(root, d.notice);
      }).catch(function () { toast(root, 'Network problem. Try again.', true); });
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var data = new FormData();
      data.append('csrfmiddlewaretoken', csrf());
      var note = form.querySelector('[name=note]');
      if (note && note.value) data.append('note', note.value);
      if (e.submitter && e.submitter.name === 'preset') data.append('preset', e.submitter.value);
      else if (at.value) {
        var d = new Date(at.value);       // local time -> ISO with offset
        data.append('remind_at', d.toISOString());
      }
      submit(data);
    });
    cancelBtn.addEventListener('click', function () {
      var data = new FormData();
      data.append('cancel', '1');
      submit(data);
    });
    dlg.addEventListener('click', function (e) {
      if (e.target === dlg || e.target.closest('[data-sheet-close]')) closeSheet(dlg);
    });
  }

  function boot() {
    var root = document.querySelector('[data-chat]');
    if (root) {
      initInfo(root);
      initPins(root);
      initReminders(root);
      if (window.location.hash && /^#m\d+$/.test(window.location.hash)) {
        setTimeout(function () { jumpTo(root, window.location.hash.slice(2)); }, 300);
      }
      return;
    }
    var page = document.querySelector('.msg-ginfo-page');
    if (page) initInfo(page);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
