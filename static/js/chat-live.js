/* NBTE ICT — chat-live.js (Phase 9)
   Live chat without reloads, using SHORT polling only (the project avoids
   long-held connections — see core/polling.py):
     * thread page: GET /messages/<id>/updates/ every 3 s while visible,
       every 15 s while hidden; backs off on errors and stops after retries.
     * inbox: GET /messages/inbox/updates/ every 10 s (30 s hidden).
   Also wires the message menu (react, star, copy, forward, info), polls,
   chat search, the composer "+" menu and the typing indicator. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ utils
  function csrfToken() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function postForm(url, data) {
    if (!(data instanceof FormData)) {
      var fd = new FormData();
      Object.keys(data || {}).forEach(function (k) {
        var v = data[k];
        if (Array.isArray(v)) v.forEach(function (x) { fd.append(k, x); });
        else fd.append(k, v);
      });
      data = fd;
    }
    return fetch(url, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrfToken() },
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, error: r.status === 403 ? 'Not allowed.' : 'Unexpected server response.' };
      }).then(function (j) {
        if (!r.ok && j.ok !== false) j = { ok: false, error: j.error || 'Something went wrong.' };
        return j;
      });
    });
  }

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function htmlToElement(html) {
    var t = document.createElement('template');
    t.innerHTML = (html || '').trim();
    return t.content.firstElementChild;
  }

  var toastTimer = null;
  function toast(root, text, isError) {
    var el = root && root.querySelector('[data-chat-toast]');
    if (!el) { if (isError) window.alert(text); return; }
    el.textContent = text;
    el.classList.toggle('is-error', !!isError);
    el.hidden = false;
    requestAnimationFrame(function () { el.classList.add('is-visible'); });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.classList.remove('is-visible');
      setTimeout(function () { el.hidden = true; }, 250);
    }, isError ? 4200 : 2200);
  }

  // Same format as the server's chat_time filter: "14:05" today,
  // "Mon 14:05" this week, "12 Oct 14:05" this year, else "12 Oct 2025".
  var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function chatTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    var now = new Date();
    var hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    var dayMs = 86400000;
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    var startThen = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    var days = Math.round((startToday - startThen) / dayMs);
    if (days <= 0) return hm;
    if (days < 7) return DAYS[d.getDay()] + ' ' + hm;
    if (d.getFullYear() === now.getFullYear()) return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + hm;
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
  }

  // ============================================================ THREAD PAGE
  function initThread(root) {
    var thread = root.querySelector('[data-thread]');
    if (!thread) return;
    var threadId = root.dataset.threadId;
    var updatesUrl = root.dataset.updatesUrl;
    var typingUrl = root.dataset.typingUrl;
    var lastId = parseInt(root.dataset.lastId || '0', 10) || 0;
    var since = root.dataset.serverTime || '';
    var typingLine = root.querySelector('[data-typing-line]');
    var participantsLine = root.querySelector('[data-participants-line]');
    var typingBubble = root.querySelector('[data-typing-bubble]');
    var newPill = root.querySelector('[data-new-pill]');
    var liveStatus = root.querySelector('[data-live-status]');
    var body = root.querySelector('[data-reply-body]');
    var replyField = root.querySelector('[data-reply-to-field]');

    var VISIBLE_MS = 3000, HIDDEN_MS = 15000, MAX_FAILURES = 5;
    var timer = null, inflight = false, failures = 0, stopped = false;
    var deferred = {};

    function messageEl(id) {
      return thread.querySelector('[data-message-id="' + id + '"]');
    }

    function nearBottom() {
      return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 140;
    }

    function scrollToBottom(smooth) {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    }

    function flash(el) {
      if (!el) return;
      el.classList.remove('is-flash');
      void el.offsetWidth;
      el.classList.add('is-flash');
      setTimeout(function () { el.classList.remove('is-flash'); }, 1800);
    }

    function scrollToMessage(el, smooth) {
      if (!el) return;
      var rect = el.getBoundingClientRect();
      var top = window.scrollY + rect.top - window.innerHeight / 2 + rect.height / 2;
      window.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' });
    }

    // A message element is "busy" while the viewer interacts with it; live
    // replacements wait until it is free.
    function isBusy(el) {
      if (!el) return false;
      if (el.querySelector('[data-message-menu][open]')) return true;
      var edit = el.querySelector('[data-edit-form]');
      if (edit && !edit.hidden) return true;
      var tform = el.querySelector('[data-transcript-form]');
      if (tform && !tform.hidden) return true;
      return false;
    }

    function replaceMessage(id, html) {
      var el = messageEl(id);
      if (!el) return null;
      if (isBusy(el)) { deferred[id] = html; return null; }
      var fresh = htmlToElement(html);
      if (!fresh) return null;
      if (el.classList.contains('is-search-hit')) fresh.classList.add('is-search-hit');
      if (el.classList.contains('is-search-current')) fresh.classList.add('is-search-current');
      el.replaceWith(fresh);
      delete deferred[id];
      document.dispatchEvent(new CustomEvent('chat:message-replaced', { detail: { id: id, el: fresh } }));
      return fresh;
    }

    function appendMessage(html, id) {
      if (id && messageEl(id)) return null;
      var el = htmlToElement(html);
      if (!el) return null;
      var empty = thread.querySelector('.msg-empty-thread');
      if (empty) empty.remove();
      // Keep the pending (sending…) bubble last.
      var pending = thread.querySelector('[data-pending]');
      if (pending) thread.insertBefore(el, pending);
      else thread.appendChild(el);
      el.classList.add('msg-arrive');
      setTimeout(function () { el.classList.remove('msg-arrive'); }, 600);
      if (id) lastId = Math.max(lastId, parseInt(id, 10) || 0);
      return el;
    }
    root.chatAppendMessage = appendMessage;
    root.chatReplaceMessage = replaceMessage;
    root.chatToast = function (t, e) { toast(root, t, e); };

    function applyReceipts(receipts) {
      Object.keys(receipts || {}).forEach(function (id) {
        var el = messageEl(id);
        if (!el) return;
        var ticks = el.querySelector('[data-ticks]');
        if (!ticks) return;
        var r = receipts[id];
        ticks.classList.remove('is-sent', 'is-delivered', 'is-read');
        ticks.classList.add('is-' + r.state);
        ticks.title = r.label;
        ticks.setAttribute('aria-label', r.label + '. Open message info');
      });
    }

    function applyTyping(list) {
      var names = (list || []).map(function (u) { return u.first_name || u.name; });
      var text = '';
      if (names.length === 1) text = names[0] + ' is typing…';
      else if (names.length === 2) text = names[0] + ' and ' + names[1] + ' are typing…';
      else if (names.length > 2) text = names.length + ' people are typing…';
      if (typingLine) {
        typingLine.textContent = text;
        typingLine.hidden = !text;
      }
      if (participantsLine) participantsLine.classList.toggle('is-dimmed', !!text);
      if (typingBubble) {
        var wasHidden = typingBubble.hidden;
        typingBubble.hidden = !text;
        if (text && wasHidden && nearBottom()) setTimeout(function () { scrollToBottom(true); }, 30);
      }
    }

    function applyUpdates(data) {
      if (!data || !data.ok) return;
      var stick = nearBottom();
      var arrivedFromOthers = false;
      (data.new || []).forEach(function (item) {
        var el = appendMessage(item.html, item.id);
        if (el && item.message && !item.message.is_mine) arrivedFromOthers = true;
      });
      (data.changed || []).forEach(function (item) { replaceMessage(item.id, item.html); });
      Object.keys(deferred).forEach(function (id) {
        if (!isBusy(messageEl(id))) replaceMessage(id, deferred[id]);
      });
      applyReceipts(data.receipts);
      applyTyping(data.typing);
      lastId = Math.max(lastId, data.last_id || 0);
      since = data.server_time || since;
      if (arrivedFromOthers) {
        if (stick) scrollToBottom(true);
        else if (newPill) newPill.hidden = false;
      }
    }

    function nextDelay() {
      var base = document.hidden ? HIDDEN_MS : VISIBLE_MS;
      if (failures) base = Math.min(30000, base * Math.pow(2, failures));
      return base;
    }

    function schedule(ms) {
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(poll, ms);
    }

    function setLiveStatus(show) {
      if (liveStatus) liveStatus.hidden = !show;
    }

    function poll() {
      if (inflight || stopped) return;
      inflight = true;
      var url = updatesUrl + '?after=' + lastId +
        '&since=' + encodeURIComponent(since || '') +
        '&visible=' + (document.hidden ? '0' : '1');
      fetch(url, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (r) {
          if (r.status === 404 || r.status === 403) { stopped = true; throw new Error('gone'); }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (data) {
          failures = 0;
          setLiveStatus(false);
          applyUpdates(data);
        })
        .catch(function () {
          failures += 1;
          if (failures >= MAX_FAILURES) stopped = true;
          if (stopped) setLiveStatus(true);
        })
        .finally(function () {
          inflight = false;
          if (!stopped) schedule(nextDelay());
        });
    }

    document.addEventListener('visibilitychange', function () {
      if (stopped) return;
      schedule(document.hidden ? HIDDEN_MS : 200);
    });
    var retry = root.querySelector('[data-live-retry]');
    if (retry) retry.addEventListener('click', function () {
      stopped = false; failures = 0; setLiveStatus(false); schedule(0);
    });
    document.addEventListener('chat:sent', function () { schedule(800); });
    root.chatPollNow = function () { schedule(50); };
    schedule(VISIBLE_MS);

    if (newPill) {
      newPill.addEventListener('click', function () { newPill.hidden = true; scrollToBottom(true); });
      window.addEventListener('scroll', function () {
        if (!newPill.hidden && nearBottom()) newPill.hidden = true;
      }, { passive: true });
    }

    // ------------------------------------------------------------ typing
    var lastTypingSent = 0;
    if (body && typingUrl) {
      body.addEventListener('input', function () {
        if (!body.value.trim()) return;
        var now = Date.now();
        if (now - lastTypingSent < 4000) return;
        lastTypingSent = now;
        postForm(typingUrl, { state: 'typing' }).catch(function () {});
      });
      document.addEventListener('chat:sent', function () { lastTypingSent = 0; });
    }

    // ------------------------------------------------------------ menus
    function closeMenus(except) {
      root.querySelectorAll('[data-message-menu][open], [data-chat-menu][open]').forEach(function (d) {
        if (d !== except) d.open = false;
      });
      var plus = root.querySelector('[data-plus-menu]');
      if (plus && !plus.hidden && except !== plus) {
        plus.hidden = true;
        var t = root.querySelector('[data-plus-toggle]');
        if (t) t.setAttribute('aria-expanded', 'false');
      }
    }

    // Open the message menu on the side with more room (above by default),
    // capped to the space available so it never runs under the composer.
    root.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d.matches || !d.matches('[data-message-menu]') || !d.open) return;
      var panel = d.querySelector('.msg-message-actions-menu');
      d.classList.remove('is-below');
      if (!panel) return;
      panel.style.maxHeight = '';
      if (window.matchMedia('(max-width: 600px)').matches) return; // bottom sheet
      requestAnimationFrame(function () {
        var trigger = d.querySelector('summary').getBoundingClientRect();
        var hero = root.querySelector('.msg-thread-hero');
        var composer = root.querySelector('.msg-reply');
        var top = hero ? hero.getBoundingClientRect().bottom : 60;
        var bottom = composer ? composer.getBoundingClientRect().top : window.innerHeight;
        var above = trigger.top - top - 10;
        var below = bottom - trigger.bottom - 10;
        var need = panel.scrollHeight;
        if (need > above && below > above) d.classList.add('is-below');
        var room = Math.max(above, below);
        if (need > room) panel.style.maxHeight = Math.max(180, room) + 'px';
      });
    }, true);

    function messageUrl(el, action) {
      return '/messages/' + threadId + '/message/' + el.getAttribute('data-message-id') + '/' + action + '/';
    }

    function openDialog(dlg) {
      if (!dlg) return;
      closeMenus();
      if (typeof dlg.showModal === 'function') dlg.showModal();
      else dlg.setAttribute('open', '');
    }
    function closeDialog(dlg) {
      if (!dlg) return;
      if (typeof dlg.close === 'function') dlg.close();
      else dlg.removeAttribute('open');
    }

    // ------------------------------------------------------------ forward
    var fwdDialog = root.querySelector('[data-forward-dialog]');
    var fwdForm = fwdDialog && fwdDialog.querySelector('[data-forward-form]');
    function updateForwardCount() {
      if (!fwdForm) return;
      var n = fwdForm.querySelectorAll('input[name=threads]:checked').length;
      var c = fwdForm.querySelector('[data-forward-count]');
      if (c) c.textContent = n ? n + ' selected' : 'Choose chats';
      var b = fwdForm.querySelector('[data-forward-submit]');
      if (b) b.disabled = !n;
    }
    if (fwdForm) {
      fwdForm.addEventListener('change', updateForwardCount);
      var filter = fwdForm.querySelector('[data-forward-filter]');
      if (filter) filter.addEventListener('input', function () {
        var q = filter.value.trim().toLowerCase();
        fwdForm.querySelectorAll('[data-forward-option]').forEach(function (o) {
          o.hidden = q && o.textContent.toLowerCase().indexOf(q) === -1;
        });
      });
      fwdForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var btn = fwdForm.querySelector('[data-forward-submit]');
        if (btn) btn.disabled = true;
        postForm(fwdForm.action, new FormData(fwdForm)).then(function (j) {
          if (!j.ok) { toast(root, j.error || 'Could not forward.', true); return; }
          closeDialog(fwdDialog);
          toast(root, j.notice || 'Forwarded.');
          schedule(300);
        }).finally(updateForwardCount);
      });
    }

    // ------------------------------------------------------------ polls
    var pollDialog = root.querySelector('[data-poll-dialog]');
    var pollForm = pollDialog && pollDialog.querySelector('[data-poll-create-form]');
    if (pollForm) {
      pollForm.addEventListener('submit', function (e) {
        e.preventDefault();
        postForm(pollForm.action, new FormData(pollForm)).then(function (j) {
          if (!j.ok) { toast(root, j.error || 'Could not create the poll.', true); return; }
          appendMessage(j.html, j.message_id);
          closeDialog(pollDialog);
          pollForm.reset();
          var inputs = pollForm.querySelectorAll('[data-poll-inputs] input');
          for (var i = 2; i < inputs.length; i++) inputs[i].remove();
          scrollToBottom(true);
        });
      });
    }

    function votePoll(optionBtn) {
      var art = optionBtn.closest('[data-message]');
      var poll = optionBtn.closest('[data-poll]');
      var form = optionBtn.closest('[data-poll-form]');
      if (!art || !poll || !form) return;
      var multiple = poll.getAttribute('data-poll-multiple') === '1';
      var id = optionBtn.getAttribute('data-option-id');
      var chosen = [];
      if (multiple) {
        poll.querySelectorAll('[data-poll-option]').forEach(function (b) {
          var mine = b.classList.contains('is-mine');
          if (b === optionBtn) mine = !mine;
          if (mine) chosen.push(b.getAttribute('data-option-id'));
        });
      } else {
        chosen = [id];
      }
      optionBtn.classList.add('is-busy');
      var fd = new FormData();
      chosen.forEach(function (c) { fd.append('option', c); });
      postForm(form.action, fd).then(function (j) {
        if (!j.ok) { toast(root, j.error || 'Could not vote.', true); optionBtn.classList.remove('is-busy'); return; }
        replaceMessage(art.getAttribute('data-message-id'), j.html);
      });
    }

    // ------------------------------------------------------------ search
    var searchForm = root.querySelector('[data-thread-search]');
    var searchIds = (root.dataset.searchIds || '').split(',').filter(Boolean);
    var searchPos = searchIds.length - 1;
    function showHit(pos, smooth) {
      if (!searchIds.length) return;
      searchPos = (pos + searchIds.length) % searchIds.length;
      thread.querySelectorAll('.is-search-current').forEach(function (el) { el.classList.remove('is-search-current'); });
      var el = messageEl(searchIds[searchPos]);
      if (el) {
        el.classList.add('is-search-current');
        scrollToMessage(el, smooth);
      }
      var p = root.querySelector('[data-search-pos]');
      if (p) p.textContent = String(searchPos + 1);
    }
    searchIds.forEach(function (id) {
      var el = messageEl(id);
      if (el) el.classList.add('is-search-hit');
    });

    // Deep links (#m123 from Starred / search) and search jumps run after the
    // initial scroll-to-bottom done by the other thread scripts.
    function initialJump() {
      if (searchIds.length) { showHit(searchIds.length - 1, false); return; }
      var m = (window.location.hash || '').match(/^#m(\d+)$/);
      if (m) {
        var el = messageEl(m[1]);
        if (el) { scrollToMessage(el, false); flash(el); }
      }
    }
    if (searchIds.length || /^#m\d+$/.test(window.location.hash || '')) {
      setTimeout(initialJump, 450);
      window.addEventListener('load', function () { setTimeout(initialJump, 120); });
    }

    // ------------------------------------------------------------ clicks
    root.addEventListener('click', function (e) {
      var t = e.target;
      var el;

      if ((el = t.closest('[data-react]'))) {
        e.preventDefault();
        var art = el.closest('[data-message]');
        closeMenus();
        postForm(messageUrl(art, 'react'), { emoji: el.getAttribute('data-react') }).then(function (j) {
          if (!j.ok) { toast(root, j.error || 'Could not react.', true); return; }
          replaceMessage(art.getAttribute('data-message-id'), j.html);
        });
        return;
      }

      if ((el = t.closest('[data-reaction-chip]'))) {
        var users = (el.getAttribute('data-users') || '').split('|').filter(Boolean);
        var emoji = el.getAttribute('data-emoji');
        var dlg = root.querySelector('[data-reactors-dialog]');
        var bodyEl = dlg && dlg.querySelector('[data-reactors-body]');
        if (!dlg || !bodyEl) return;
        var mine = el.classList.contains('is-mine');
        var host = el.closest('[data-message]');
        bodyEl.innerHTML =
          '<p class="msg-reactors-emoji" aria-hidden="true">' + escapeHtml(emoji) + '</p>' +
          '<ul class="msg-reactors">' + users.map(function (u) {
            return '<li>' + escapeHtml(u) + '</li>';
          }).join('') + '</ul>' +
          (mine ? '<button type="button" class="btn btn-ghost btn-sm" data-remove-reaction>Remove my reaction</button>' : '');
        dlg._target = host;
        dlg._emoji = emoji;
        openDialog(dlg);
        return;
      }

      if ((el = t.closest('[data-remove-reaction]'))) {
        var rd = el.closest('dialog');
        if (rd && rd._target) {
          var target = rd._target;
          postForm(messageUrl(target, 'react'), { emoji: rd._emoji }).then(function (j) {
            if (j.ok) replaceMessage(target.getAttribute('data-message-id'), j.html);
            closeDialog(rd);
          });
        }
        return;
      }

      if ((el = t.closest('[data-copy]'))) {
        var host2 = el.closest('[data-message]');
        var txt = host2 && host2.querySelector('[data-message-text]');
        closeMenus();
        var text = txt ? txt.innerText.trim() : '';
        var done = function () { toast(root, 'Copied'); };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(done, function () { toast(root, 'Could not copy.', true); });
        } else {
          var ta = document.createElement('textarea');
          ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
          document.body.appendChild(ta); ta.select();
          try { document.execCommand('copy'); done(); } catch (err) { toast(root, 'Could not copy.', true); }
          ta.remove();
        }
        return;
      }

      if ((el = t.closest('[data-forward]'))) {
        var host3 = el.closest('[data-message]');
        if (fwdForm && host3) {
          fwdForm.action = messageUrl(host3, 'forward');
          fwdForm.reset();
          fwdForm.querySelectorAll('[data-forward-option]').forEach(function (o) { o.hidden = false; });
          updateForwardCount();
          openDialog(fwdDialog);
        }
        return;
      }

      if ((el = t.closest('[data-info-open]'))) {
        var host4 = el.closest('[data-message]');
        var idlg = root.querySelector('[data-info-dialog]');
        if (!host4 || !idlg) return;
        var ib = idlg.querySelector('[data-info-body]');
        ib.innerHTML = '<p class="muted">Loading…</p>';
        openDialog(idlg);
        fetch(messageUrl(host4, 'info'), {
          credentials: 'same-origin',
          headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        }).then(function (r) { return r.json(); }).then(function (j) {
          ib.innerHTML = j.html || '<p class="muted">Not available.</p>';
        }).catch(function () { ib.innerHTML = '<p class="muted">Could not load message info.</p>'; });
        return;
      }

      if ((el = t.closest('[data-poll-option]'))) {
        e.preventDefault();
        votePoll(el);
        return;
      }

      if ((el = t.closest('[data-poll-open]'))) {
        closeMenus();
        openDialog(pollDialog);
        var q = pollDialog && pollDialog.querySelector('[name=question]');
        if (q) setTimeout(function () { q.focus(); }, 30);
        return;
      }

      if ((el = t.closest('[data-poll-add]'))) {
        var wrap = root.querySelector('[data-poll-inputs]');
        var n = wrap ? wrap.querySelectorAll('input').length : 0;
        if (wrap && n < 6) {
          var inp = document.createElement('input');
          inp.className = 'msg-input';
          inp.name = 'options';
          inp.maxLength = 120;
          inp.placeholder = 'Option ' + (n + 1);
          inp.setAttribute('aria-label', 'Option ' + (n + 1));
          wrap.appendChild(inp);
          inp.focus();
        }
        if (n + 1 >= 6) el.disabled = true;
        return;
      }

      if ((el = t.closest('[data-jump-to]'))) {
        var target2 = messageEl(el.getAttribute('data-jump-to'));
        if (target2) { scrollToMessage(target2, true); flash(target2); }
        else toast(root, 'That message is not available.');
        return;
      }

      if ((el = t.closest('[data-plus-toggle]'))) {
        var menu = root.querySelector('[data-plus-menu]');
        if (!menu) return;
        var open = menu.hidden;
        closeMenus(menu);
        menu.hidden = !open;
        el.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) { var first = menu.querySelector('button'); if (first) first.focus(); }
        return;
      }

      if ((el = t.closest('[data-pick-files]'))) {
        closeMenus();
        var fi = root.querySelector('[data-message-files]');
        if (fi) fi.click();
        return;
      }

      if ((el = t.closest('[data-voice-start]'))) {
        closeMenus();
        document.dispatchEvent(new CustomEvent('voice:start'));
        return;
      }

      if ((el = t.closest('[data-search-toggle]'))) {
        if (!searchForm) return;
        searchForm.hidden = !searchForm.hidden;
        el.setAttribute('aria-expanded', searchForm.hidden ? 'false' : 'true');
        if (!searchForm.hidden) {
          var si = searchForm.querySelector('[data-search-input]');
          if (si) si.focus();
        }
        return;
      }
      if (t.closest('[data-search-prev]')) { showHit(searchPos - 1, true); return; }
      if (t.closest('[data-search-next]')) { showHit(searchPos + 1, true); return; }

      if ((el = t.closest('[data-sheet-close]'))) {
        closeDialog(el.closest('dialog'));
        return;
      }

      if ((el = t.closest('[data-transcript-edit]'))) {
        var box = el.closest('[data-transcript]');
        var f = box && box.querySelector('[data-transcript-form]');
        if (f) {
          f.hidden = false;
          var area = f.querySelector('textarea');
          if (area) { area.focus(); area.setSelectionRange(area.value.length, area.value.length); }
        }
        return;
      }
      if ((el = t.closest('[data-transcript-cancel]'))) {
        var f2 = el.closest('[data-transcript-form]');
        if (f2) f2.hidden = true;
        return;
      }

      // Click on a dialog backdrop closes it.
      if (t.tagName === 'DIALOG' && t.classList.contains('msg-sheet')) {
        var rect = t.getBoundingClientRect();
        if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
          closeDialog(t);
        }
      }
    });

    // Close the "+" menu when clicking elsewhere.
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.msg-plus')) {
        var menu = root.querySelector('[data-plus-menu]');
        if (menu && !menu.hidden) {
          menu.hidden = true;
          var tg = root.querySelector('[data-plus-toggle]');
          if (tg) tg.setAttribute('aria-expanded', 'false');
        }
      }
      if (!e.target.closest('[data-chat-menu]')) {
        root.querySelectorAll('[data-chat-menu][open]').forEach(function (d) { d.open = false; });
      }
    });

    // Star (form) and transcript edit (form) go through fetch when JS is on.
    root.addEventListener('submit', function (e) {
      var form = e.target;
      if (form.matches('[data-star-form]')) {
        e.preventDefault();
        var art = form.closest('[data-message]');
        closeMenus();
        postForm(form.action, new FormData(form)).then(function (j) {
          if (!j.ok) { toast(root, j.error || 'Could not star.', true); return; }
          replaceMessage(art.getAttribute('data-message-id'), j.html);
          toast(root, j.starred ? 'Starred' : 'Removed from starred');
        });
        return;
      }
      if (form.matches('[data-transcript-form]')) {
        e.preventDefault();
        var host = form.closest('[data-message]');
        postForm(form.action, new FormData(form)).then(function (j) {
          if (!j.ok) { toast(root, j.error || 'Could not save.', true); return; }
          form.hidden = true;
          replaceMessage(host.getAttribute('data-message-id'), j.html);
          toast(root, 'Transcript saved');
        });
      }
    });

    // Esc closes menus (dialogs close natively; recording is handled in voice-notes.js).
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      var openMenu = root.querySelector('[data-message-menu][open], [data-chat-menu][open]');
      var plus = root.querySelector('[data-plus-menu]');
      if (openMenu || (plus && !plus.hidden)) {
        closeMenus();
        var summary = openMenu && openMenu.querySelector('summary');
        if (summary) summary.focus();
      }
    });

    // Keyboard: the reply form clears its reference after send.
    if (replyField) {
      document.addEventListener('chat:sent', function () { replyField.value = ''; });
    }
  }

  // ================================================================== INBOX
  function initInbox(root) {
    var url = root.dataset.inboxUpdatesUrl;
    if (!url) return;
    var timer = null, failures = 0, stopped = false, inflight = false;
    var totalEl = root.querySelector('[data-total-unread]');

    function row(id) { return root.querySelector('[data-message-thread][data-thread-id="' + id + '"]'); }

    function reloadLists() {
      return fetch(window.location.href, { credentials: 'same-origin' })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, 'text/html');
          var fresh = doc.querySelector('[data-message-list="inbox"]');
          if (!fresh) return;
          var checked = {};
          root.querySelectorAll('input[data-uxb-item]:checked').forEach(function (c) { checked[c.value] = true; });
          ['pinned', 'all'].forEach(function (name) {
            var mine = root.querySelector('[data-section="' + name + '"]');
            var theirs = fresh.querySelector('[data-section="' + name + '"]');
            if (mine && theirs) mine.innerHTML = theirs.innerHTML;
            else if (!mine && theirs) {
              var anchor = name === 'pinned'
                ? root.querySelector('[data-section="all"]')
                : root.querySelector('[data-filter-empty]');
              if (anchor) anchor.parentNode.insertBefore(theirs, anchor);
              else window.location.reload();
            } else if (mine && !theirs) mine.remove();
          });
          root.querySelectorAll('input[data-uxb-item]').forEach(function (c) { if (checked[c.value]) c.checked = true; });
        });
    }

    function apply(data) {
      var missing = false;
      (data.threads || []).forEach(function (t) {
        var r = row(t.id);
        if (!r) { missing = true; return; }
        if ((r.getAttribute('data-pinned') === '1') !== !!t.pinned) { missing = true; return; }
        r.setAttribute('data-message-unread', t.unread ? 'true' : 'false');
        r.setAttribute('data-sort-at', t.sort_at || '');
        var link = r.querySelector('[data-row-link]');
        if (link) link.classList.toggle('is-unread', t.unread > 0);
        var badge = r.querySelector('[data-row-badge]');
        if (badge) { badge.textContent = t.unread; badge.hidden = !t.unread; }
        var prev = r.querySelector('[data-row-preview]');
        if (prev && t.preview) {
          prev.innerHTML = '<span class="msg-thread-sender">' + escapeHtml(t.sender) + ':</span> ' +
            escapeHtml(t.preview.length > 90 ? t.preview.slice(0, 89) + '…' : t.preview);
        }
        var time = r.querySelector('[data-row-time]');
        if (time && t.last_at) time.textContent = chatTime(t.last_at);
      });
      // Reorder rows inside each list by latest activity.
      root.querySelectorAll('[data-thread-list]').forEach(function (list) {
        var rows = Array.prototype.slice.call(list.querySelectorAll(':scope > [data-message-thread]'));
        var sorted = rows.slice().sort(function (a, b) {
          return (b.getAttribute('data-sort-at') || '').localeCompare(a.getAttribute('data-sort-at') || '');
        });
        var changed = sorted.some(function (r, i) { return r !== rows[i]; });
        if (changed) sorted.forEach(function (r) { list.appendChild(r); });
      });
      if (totalEl) totalEl.textContent = data.total_unread;
      var chip = root.querySelector('[data-unread-chip]');
      if (chip) chip.textContent = data.total_unread;
      var done = missing ? reloadLists() : Promise.resolve();
      return done.then(function () { root.dispatchEvent(new CustomEvent('messages:refilter')); });
    }

    function schedule(ms) { clearTimeout(timer); if (!stopped) timer = setTimeout(poll, ms); }
    function poll() {
      if (inflight || stopped) return;
      inflight = true;
      fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) { failures = 0; return apply(d); })
        .catch(function () { failures += 1; if (failures >= 5) stopped = true; })
        .finally(function () {
          inflight = false;
          if (!stopped) schedule((document.hidden ? 30000 : 10000) * Math.pow(2, Math.min(failures, 3)));
        });
    }
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && !stopped) schedule(300);
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('[data-row-menu]')) {
        root.querySelectorAll('[data-row-menu][open]').forEach(function (d) { d.open = false; });
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') root.querySelectorAll('[data-row-menu][open]').forEach(function (d) { d.open = false; });
    });
    schedule(10000);
  }

  function boot() {
    var t = document.querySelector('[data-chat]');
    if (t) {
      // Page-scoped layout hook (messaging.css): pinned header/composer, no footer.
      document.body.classList.add('is-chat-page');
      initThread(t);
    }
    var i = document.querySelector('[data-inbox-live]');
    if (i) initInbox(i);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
