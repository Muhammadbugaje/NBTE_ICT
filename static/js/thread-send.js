(function () {
  'use strict';

  var root = document.querySelector('[data-messaging-thread]');
  if (!root) return;

  var form       = root.querySelector('[data-reply-form]');
  var bodyField  = root.querySelector('[data-reply-body]');
  var replyTo    = root.querySelector('[data-reply-to-field]');
  var replyRef   = root.querySelector('[data-reply-reference]');
  var replyRefTx = root.querySelector('[data-reply-reference-text]');
  var clearReply = root.querySelector('[data-clear-reply]');
  var thread     = root.querySelector('[data-thread]');
  var sendBtn    = root.querySelector('[data-send-button]');
  var sendLabel  = root.querySelector('[data-send-label]');
  var summary    = root.querySelector('[data-attachment-summary]');
  var fileInput  = root.querySelector('[data-message-files]');

  if (!form || !bodyField || !thread) return;

  var initialStateSummary = summary ? summary.textContent : '';
  var submitting = false;

  // -------- Scroll helpers ------------------------------------------------

  // Which element actually scrolls? Usually window, but a wrapper with
  // `overflow: auto` would take over.
  function getScrollContainer() {
    var el = thread.parentElement;
    while (el && el !== document.body && el !== document.documentElement) {
      var style = window.getComputedStyle(el);
      var oy = style.overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight) {
        return el;
      }
      el = el.parentElement;
    }
    return window;
  }

  function scrollThreadToBottom(smooth) {
    var container = getScrollContainer();
    var useWindow =
      container === window ||
      container === document.documentElement ||
      container === document.body;

    if (useWindow) {
      var targetY = Math.max(
        document.documentElement.scrollHeight,
        document.body.scrollHeight
      );
      if (smooth) {
        window.scrollTo({ top: targetY, behavior: 'smooth' });
      } else {
        window.scrollTo(0, targetY);
      }
    } else {
      if (smooth) {
        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
      } else {
        container.scrollTop = container.scrollHeight;
      }
    }
  }

  function isNearBottom() {
    var container = getScrollContainer();
    var useWindow =
      container === window ||
      container === document.documentElement ||
      container === document.body;

    if (useWindow) {
      return (
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 60
      );
    }
    return (
      container.scrollTop + container.clientHeight >=
      container.scrollHeight - 60
    );
  }

  // Two rAFs so the browser has completed layout before we measure.
  function scrollAfterLayout(smooth) {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        scrollThreadToBottom(smooth);
      });
    });
  }

  // Focus without scrolling — this is what stops the "jump to top".
  function focusBody() {
    try {
      bodyField.focus({ preventScroll: true });
    } catch (e) {
      bodyField.focus();
    }
  }

  // -------- Auto-focus + auto-resize --------------------------------------

  function autoResize() {
    bodyField.style.height = 'auto';
    bodyField.style.height = Math.min(bodyField.scrollHeight, 240) + 'px';
  }
  bodyField.addEventListener('input', autoResize);
  autoResize();

  if (!document.activeElement || document.activeElement === document.body) {
    focusBody();
  }

  // Land at the bottom on load and again once images/fonts have settled.
  scrollAfterLayout(false);
  window.addEventListener('load', function () { scrollAfterLayout(false); });

  // -------- Pending bubble ------------------------------------------------

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }

  function createPendingBubble(text, replyPreview, attachmentCount) {
    var initials = root.dataset.currentUserInitials || '?';
    var color    = root.dataset.currentUserColor || 'c5';

    var art = document.createElement('article');
    art.className = 'msg-bubble is-mine is-pending msg-enter';
    art.setAttribute('data-message', '');
    art.setAttribute('data-pending', '');

    var replyHtml = replyPreview
      ? '<div class="msg-reply-quote"><strong>Replying to</strong><span>' +
          escapeHtml(replyPreview).slice(0, 180) + '</span></div>'
      : '';
    var textHtml = text
      ? '<div class="msg-bubble-text" data-message-text>' +
          escapeHtml(text).replace(/\n/g, '<br>') + '</div>'
      : '';
    var attachHtml = attachmentCount > 0
      ? '<div class="msg-pending-files">Uploading ' +
          attachmentCount + ' attachment' + (attachmentCount > 1 ? 's' : '') +
          '…</div>'
      : '';

    // Phase 9 bubble layout: no avatar on my own messages, time + clock below.
    art.innerHTML =
      '<div class="msg-bubble-col"><div class="msg-bubble-body">' +
        replyHtml +
        textHtml +
        attachHtml +
        '<div class="msg-bubble-meta"><time>sending…</time>' +
        '<span class="msg-ticks is-pending" aria-label="Sending">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>' +
        '</span></div>' +
      '</div></div>';
    return art;
  }

  function triggerEnter(el) {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.classList.add('msg-enter-active');
      });
    });
    setTimeout(function () {
      el.classList.remove('msg-enter', 'msg-enter-active');
    }, 500);
  }

  // -------- Error banner --------------------------------------------------

  function showError(msg) {
    var banner = document.createElement('div');
    banner.className = 'msg-inline-error msg-inline-error-flash';
    banner.setAttribute('role', 'alert');
    banner.textContent = msg;
    var first = root.querySelector('.msg-inline-error');
    if (first) {
      first.replaceWith(banner);
    } else {
      var hero = root.querySelector('.msg-thread-hero');
      if (hero) hero.insertAdjacentElement('afterend', banner);
    }
    setTimeout(function () { banner.classList.add('is-fading'); }, 3500);
    setTimeout(function () { banner.remove(); }, 4300);
  }

  // -------- Send-button state ---------------------------------------------

  function setSending(on) {
    submitting = on;
    if (sendBtn) {
      sendBtn.disabled = on;
      sendBtn.classList.toggle('is-sending', on);
    }
    if (sendLabel) sendLabel.textContent = on ? 'Sending…' : 'Send';
    bodyField.readOnly = on;
  }

  // -------- Composer reset ------------------------------------------------

  function clearComposer() {
    bodyField.value = '';
    bodyField.classList.remove('is-fading');
    bodyField.style.height = '';
    if (replyTo)    replyTo.value = '';
    if (replyRef)   replyRef.hidden = true;
    if (replyRefTx) replyRefTx.textContent = '';
    if (fileInput)  fileInput.value = '';
    if (summary)    summary.textContent = initialStateSummary;
    autoResize();
    focusBody();
  }

  // -------- Submit --------------------------------------------------------

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (submitting) return;

    var text = (bodyField.value || '').trim();
    var files = fileInput && fileInput.files ? fileInput.files : null;
    var hasFiles = !!(files && files.length);
    if (!text && !hasFiles) {
      focusBody();
      return;
    }

    var replyPreview = (replyRef && !replyRef.hidden && replyRefTx)
      ? (replyRefTx.textContent || '').trim()
      : '';

    var empty = thread.querySelector('.msg-empty-thread');
    if (empty) empty.remove();

    var pending = createPendingBubble(text, replyPreview, hasFiles ? files.length : 0);
    thread.appendChild(pending);
    triggerEnter(pending);

    // Bring the pending bubble into view — smooth, so it feels like the
    // message travelled from the form to the thread.
    scrollAfterLayout(true);

    bodyField.classList.add('is-fading');
    setSending(true);

    var data = new FormData(form);

    fetch(form.action, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then(function (r) {
        return r.json().catch(function () {
          return { ok: false, error: 'Unexpected server response.' };
        });
      })
      .then(function (payload) {
        if (!payload || !payload.ok) {
          pending.remove();
          bodyField.classList.remove('is-fading');
          showError((payload && payload.error) || 'Could not send message.');
          focusBody();
          return;
        }

        var wrap = document.createElement('div');
        wrap.innerHTML = (payload.html || '').trim();
        var real = wrap.firstElementChild;
        // Phase 9: the live poller may already have inserted this message.
        if (real && payload.message_id &&
            thread.querySelector('[data-message-id="' + payload.message_id + '"]')) {
          pending.remove();
          real = null;
          clearComposer();
          scrollAfterLayout(false);
          document.dispatchEvent(new CustomEvent('chat:sent', { detail: payload }));
          return;
        }
        if (real) {
          real.classList.add('msg-new', 'msg-replace');
          pending.replaceWith(real);
          setTimeout(function () {
            real.classList.remove('msg-replace');
          }, 250);
        } else {
          pending.remove();
        }

        clearComposer();
        document.dispatchEvent(new CustomEvent('chat:sent', { detail: payload }));

        // Re-assert the bottom AFTER the composer has shrunk back. If we're
        // already there, leave it alone so we don't fight an in-flight smooth
        // scroll — but if the user somehow ended up elsewhere (short layout,
        // mid-send window resize), pull them back to the last message.
        scrollAfterLayout(false);
      })
      .catch(function () {
        pending.remove();
        bodyField.classList.remove('is-fading');
        showError('Network error — message not sent.');
        focusBody();
      })
      .finally(function () {
        setSending(false);
      });
  });

  // -------- Cmd/Ctrl + Enter sends ----------------------------------------

  bodyField.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      if (typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      }
    }
  });

  // -------- Clear-reply button --------------------------------------------

  if (clearReply) {
    clearReply.addEventListener('click', function () {
      if (replyTo)    replyTo.value = '';
      if (replyRef)   replyRef.hidden = true;
      if (replyRefTx) replyRefTx.textContent = '';
      focusBody();
    });
  }

  // -------- Delegation for actions on NEWLY inserted bubbles only ---------

  thread.addEventListener('click', function (e) {
    var bubble = e.target.closest('.msg-bubble.msg-new');
    if (!bubble) return;

    var replyBtn = e.target.closest('[data-reply-to]');
    if (replyBtn) {
      e.preventDefault();
      var id = replyBtn.getAttribute('data-reply-to');
      if (replyTo) replyTo.value = id;
      var textEl = bubble.querySelector('[data-message-text]');
      var preview = textEl ? (textEl.textContent || '').trim().slice(0, 180) : '';
      if (replyRef && replyRefTx) {
        replyRefTx.textContent = preview;
        replyRef.hidden = false;
      }
      focusBody();
      return;
    }

    var editBtn = e.target.closest('[data-edit-message]');
    if (editBtn) {
      e.preventDefault();
      var editForm = bubble.querySelector('[data-edit-form]');
      if (editForm) {
        editForm.hidden = false;
        var ta = editForm.querySelector('textarea');
        if (ta) {
          try { ta.focus({ preventScroll: true }); } catch (e) { ta.focus(); }
          ta.setSelectionRange(ta.value.length, ta.value.length);
        }
      }
      return;
    }

    var cancelBtn = e.target.closest('[data-cancel-edit]');
    if (cancelBtn) {
      e.preventDefault();
      var f = cancelBtn.closest('[data-edit-form]');
      if (f) f.hidden = true;
    }
  });
})();