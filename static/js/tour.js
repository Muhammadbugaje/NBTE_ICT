/* NBTE guided tours ("Guide me").
 *
 * Dims and blurs the page, leaves a hole round one element, draws a curved
 * arrow to it and shows a short card. Tours are plain data from
 * qa_core/tours.py. {% tour_boot %} puts a few bytes on the page in
 * <script id="nbte-tour-data"> (what this person has seen, and dataUrl); the
 * tours themselves are fetched once from dataUrl and kept by the browser.
 * No dependencies.
 *
 * Step types: info (Next), click (wait for a click on the target),
 * fill (wait for a value), navigate (go to a URL, then carry on).
 * Progress is kept in localStorage, so a tour continues after a page load
 * or a boosted HTMX swap.
 *
 * Entry points
 *   - the floating "Guide me" button (tour picker with search)
 *   - any element with data-tour-start="tour-id" (or data-tour-picker)
 *   - a link to  /some/page/?tour=tour-id
 *   - window.NBTETour.start(id) / .open() / .end()
 */
(function () {
  'use strict';
  if (window.NBTETour) return;
  var D = document, W = window;
  var dataEl = D.getElementById('nbte-tour-data');
  if (!dataEl) return;
  var BOOT;
  try { BOOT = JSON.parse(dataEl.textContent || '{}'); } catch (e) { return; }
  var TOURS = [], BY = {}, loaded = false, loadFailed = false, waiters = [];
  if (!(BOOT.tours && BOOT.tours.length) && !BOOT.dataUrl) return;
  function adopt(list) {
    (list || []).forEach(function (t) { if (t && t.id && !BY[t.id]) { BY[t.id] = t; TOURS.push(t); } });
    loaded = true;
    var w = waiters; waiters = [];
    w.forEach(function (f) { try { f(); } catch (e) { /* one listener must not stop the rest */ } });
  }
  /* Run `then` once the tours are here. They come with the page (BOOT.tours) or from BOOT.dataUrl. */
  function whenLoaded(then) {
    if (loaded) return then();
    waiters.push(then);
    if (waiters.length > 1) return;
    if (BOOT.tours) return adopt(BOOT.tours);
    var fail = function () { loadFailed = true; adopt([]); };
    if (!W.fetch) return fail();
    W.fetch(BOOT.dataUrl, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('tours ' + r.status); return r.json(); })
      .then(function (d) { adopt(d && d.tours); })
      .catch(fail);
  }

  var LS_ACTIVE = 'nbte.tour.active', LS_SEEN = 'nbte.tour.seen', LS_USED = 'nbte.tour.used';
  var RESUME_MS = 3 * 60 * 60 * 1000;
  var NS = 'http://www.w3.org/2000/svg';

  /* ---------------- small helpers ---------------- */
  var store = {
    get: function (k) { try { return JSON.parse(W.localStorage.getItem(k) || 'null'); } catch (e) { return null; } },
    set: function (k, v) { try { W.localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
    del: function (k) { try { W.localStorage.removeItem(k); } catch (e) { /* ignore */ } }
  };
  function now() { return Date.now(); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function rich(s) { return esc(s).replace(/\*([^*]+)\*/g, '<strong>$1</strong>'); }
  function plain(s) { return String(s || '').replace(/\*/g, ''); }
  function normPath(p) { p = String(p || '/').split('?')[0].split('#')[0]; return p.charAt(p.length - 1) === '/' ? p : p + '/'; }
  /* When the portal is served by path (Render: /qa-portal/), tour data keeps its
     plain paths. herePath() compares them without the mount; withBase() puts it
     back on every link the engine follows. */
  var BASE = String(BOOT.base || '').replace(/\/+$/, '');
  function withBase(u) {
    u = String(u || '');
    if (!BASE || !u || u.charAt(0) !== '/' || /^\/\//.test(u)) return u;
    if (u === BASE || u.indexOf(BASE + '/') === 0 || u.indexOf(BASE + '?') === 0) return u;
    return BASE + u;
  }
  function herePath() {
    var p = W.location.pathname;
    if (BASE && (p === BASE || p.indexOf(BASE + '/') === 0)) p = p.slice(BASE.length) || '/';
    return normPath(p);
  }
  function reducedMotion() {
    var m = D.documentElement.getAttribute('data-motion');
    if (m === 'off' || m === 'reduced') return true;
    try { return W.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }
  function isPhone() { return W.innerWidth < 680; }
  function el(tag, cls, html) { var n = D.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function icon(d, size) {
    return '<svg width="' + (size || 16) + '" height="' + (size || 16) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + d + '</svg>';
  }
  var IC = {
    compass: '<circle cx="12" cy="12" r="10"/><polygon points="16.2 7.8 14.1 14.1 7.8 16.2 9.9 9.9 16.2 7.8"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    arrow: '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    back: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    pointer: '<path d="M9 11V5a2 2 0 0 1 4 0v6"/><path d="M13 10.5a2 2 0 0 1 4 0V13"/><path d="M17 12.5a2 2 0 0 1 4 0V16a6 6 0 0 1-6 6h-2a6 6 0 0 1-5-2.7L4.3 15a2 2 0 0 1 3.2-2.4L9 14"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    pin: '<path d="M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
  };

  /* ---------------- seen / saved state ---------------- */
  var seen = (function () {
    var s = BOOT.seen || {}, local = store.get(LS_SEEN) || {};
    function merge(k) { var o = {}; (s[k] || []).concat(local[k] || []).forEach(function (i) { o[i] = 1; }); return Object.keys(o); }
    var done = merge('done');
    var dismissed = merge('dismissed').filter(function (i) { return done.indexOf(i) < 0; });
    return { done: done, dismissed: dismissed };
  })();
  function touched(id) { return seen.done.indexOf(id) >= 0 || seen.dismissed.indexOf(id) >= 0; }
  function csrf() {
    try { var h = JSON.parse(D.body.getAttribute('hx-headers') || '{}'); if (h['X-CSRFToken']) return h['X-CSRFToken']; } catch (e) { /* fall through */ }
    var m = D.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function remember(id, state) {
    if (state === 'dismissed' && seen.done.indexOf(id) >= 0) return;
    ['done', 'dismissed'].forEach(function (k) { var i = seen[k].indexOf(id); if (i >= 0) seen[k].splice(i, 1); });
    seen[state].push(id);
    store.set(LS_SEEN, seen);
    if (!BOOT.canSave || !BOOT.saveUrl || !W.fetch) return;
    try {
      W.fetch(BOOT.saveUrl, {
        method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ id: id, state: state })
      }).catch(function () { /* the local copy is enough */ });
    } catch (e) { /* ignore */ }
  }

  /* ---------------- DOM (built once) ---------------- */
  var ui = null;
  function build() {
    if (ui && ui.root.isConnected) return ui;
    var root = el('div', 'nt-root');
    root.setAttribute('data-nt-root', '');
    root.hidden = true;
    var shades = ['top', 'right', 'bottom', 'left'].map(function (side) {
      var s = el('div', 'nt-shade nt-shade--' + side); root.appendChild(s); return s;
    });
    var ring = el('div', 'nt-ring'); root.appendChild(ring);
    var block = el('div', 'nt-block'); root.appendChild(block);
    var svg = D.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'nt-arrow'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    svg.innerHTML = '<defs><marker id="nt-head" markerUnits="userSpaceOnUse" markerWidth="22" markerHeight="22" refX="17" refY="11" orient="auto">' +
      '<path class="nt-arrow__head" d="M5 3 L18 11 L5 19"/></marker></defs>' +
      '<path class="nt-arrow__halo"/><path class="nt-arrow__line" marker-end="url(#nt-head)"/>';
    root.appendChild(svg);
    var card = el('div', 'nt-card');
    card.setAttribute('role', 'dialog'); card.setAttribute('aria-labelledby', 'nt-title'); card.setAttribute('aria-describedby', 'nt-text');
    card.tabIndex = -1;
    root.appendChild(card);
    var live = el('div', 'nt-live'); live.setAttribute('aria-live', 'polite'); live.setAttribute('role', 'status');
    root.appendChild(live);
    D.body.appendChild(root);
    ui = { root: root, shades: shades, ring: ring, block: block, svg: svg, halo: svg.querySelector('.nt-arrow__halo'), line: svg.querySelector('.nt-arrow__line'), card: card, live: live };
    shades.concat([block]).forEach(function (n) {
      n.addEventListener('click', function () { nudge(); });
    });
    card.addEventListener('click', onCardClick);
    return ui;
  }
  function nudge() {
    if (!ui) return;
    ui.card.classList.remove('is-nudge'); void ui.card.offsetWidth; ui.card.classList.add('is-nudge');
  }

  /* ---------------- run state ---------------- */
  var run = null;   // { tour, i, dir, mode: waiting|step|missing|finish, el, via, deadline, filled }
  var ticker = null, observer = null, rafUntil = 0, rafOn = false, lastFocus = null;

  function persist() { if (run) store.set(LS_ACTIVE, { id: run.tour.id, i: run.i, t: now() }); }
  function steps() { return run.tour.steps; }
  function cur() { return run ? steps()[run.i] : null; }
  function countable(tour) { return tour.steps.filter(function (s) { return s.type !== 'navigate'; }).length; }
  function ordinal(tour, i) { var n = 0; for (var k = 0; k <= i; k++) if (tour.steps[k].type !== 'navigate') n++; return n; }
  function prevIndex(i) { for (var k = i - 1; k >= 0; k--) if (steps()[k].type !== 'navigate') return k; return -1; }
  function pathOk(step) {
    var p = herePath();
    if (step.path_re) { try { return new RegExp(step.path_re).test(p); } catch (e) { return true; } }
    if (step.path) return normPath(step.path) === p;
    return true;
  }
  function visible(n) {
    if (!n || !n.getBoundingClientRect) return false;
    if (ui && ui.root.contains(n)) return false;
    var r = n.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (r.right <= 1 || r.left >= W.innerWidth - 1) return false;   // off-canvas drawer
    var cs = W.getComputedStyle(n);
    return !(cs.visibility === 'hidden' || cs.display === 'none');
  }
  function first(selectors) {
    var list = [].concat(selectors || []);
    for (var i = 0; i < list.length; i++) {
      var found; try { found = D.querySelectorAll(list[i]); } catch (e) { continue; }
      for (var k = 0; k < found.length; k++) if (visible(found[k])) return found[k];
    }
    return null;
  }
  function resolve(step) {
    if (!step.target) return { el: null, via: false };
    var n = first(step.target);
    if (n) {
      if (step.closest) { var up = n.closest(step.closest); if (up && visible(up)) n = up; }
      return { el: n, via: false };
    }
    if (step.via) { var v = first(step.via); if (v) return { el: v, via: true }; }
    return null;
  }
  function busy() { return D.readyState !== 'complete' || !!D.querySelector('.htmx-request'); }

  /* ---------------- starting / moving ---------------- */
  function start(id, opts) {
    var tour = BY[id];
    if (!tour) return false;
    opts = opts || {};
    closePicker();
    if (run) teardown();
    var i = opts.step || 0;
    var s0 = tour.steps[i];
    var startPath = normPath(tour.start || herePath());
    // Begin on the tour's own page unless the first step can already be shown here.
    if (!opts.resume && i === 0 && herePath() !== startPath && !(s0 && (s0.path || s0.path_re) && pathOk(s0))) {
      store.set(LS_ACTIVE, { id: id, i: 0, t: now() });
      W.location.assign(withBase(tour.start));
      return true;
    }
    lastFocus = D.activeElement;
    run = { tour: tour, i: i, dir: 1, mode: 'waiting', el: null, via: false, deadline: 0, flipped: false };
    build();
    D.documentElement.classList.add('nt-on');
    listen(true);
    go(i, 1);
    return true;
  }

  function go(i, dir) {
    if (!run) return;
    var list = steps();
    if (i >= list.length) return finish();
    if (i < 0) {                      // nothing earlier can be shown: turn round once
      if (run.flipped) return showMissing();
      run.flipped = true; return go(run.i + 1, 1);
    }
    var step = list[i];
    run.i = i; run.dir = dir || 1; run.el = null; run.via = false; run.filled = false;
    persist();
    if (step.type === 'navigate') {
      if (run.dir < 0) return go(i - 1, -1);
      if (normPath(step.url) === herePath()) return go(i + 1, 1);
      store.set(LS_ACTIVE, { id: run.tour.id, i: i + 1, t: now() });
      hideOverlay();
      W.location.assign(withBase(step.url));
      return;
    }
    run.mode = 'waiting';
    setDeadline(step);
    hideOverlay();
    tick();
  }
  function setDeadline(step) {
    var on = pathOk(step);
    run.since = now();
    run.deadline = now() + (step.wait || (step.optional ? (on ? 650 : 1600) : (on ? 2600 : 4000)));
  }

  function next() {
    if (!run) return;
    if (run.mode === 'finish') return end(false);
    run.flipped = false;
    var j = run.i + 1, list = steps();
    // Skipping a step that could not be shown: its follow-ups on that same missing page go with it.
    if (run.mode === 'missing') while (j < list.length - 1 && list[j].type !== 'navigate' && !pathOk(list[j])) j++;
    go(j, 1);
  }
  function back() {
    if (!run) return;
    var j = prevIndex(run.i);
    if (j < 0) return;
    var step = steps()[j];
    run.flipped = false;
    if (!pathOk(step)) {
      if (step.path) {
        store.set(LS_ACTIVE, { id: run.tour.id, i: j, t: now() });
        hideOverlay(); W.location.assign(withBase(step.path)); return;
      }
      store.set(LS_ACTIVE, { id: run.tour.id, i: j, t: now() });
      run.i = j; hideOverlay(); W.history.back(); run.mode = 'waiting'; run.dir = -1; setDeadline(step); return;
    }
    go(j, -1);
  }

  function finish() {
    var tour = run.tour;
    remember(tour.id, 'done');
    store.del(LS_ACTIVE);
    run.mode = 'finish'; run.i = steps().length; run.el = first('[data-nt-launcher] .nt-launch__btn'); run.via = false;
    var nxt = tour.next && BY[tour.next];
    renderCard({
      kicker: 'Guide finished', done: true,
      title: nxt ? 'Done. Ready for the next one?' : 'That is it',
      text: nxt ? 'Next: *' + nxt.title + '*. ' + nxt.summary : 'Run this guide again, or pick another, from *Guide me* whenever you are stuck.',
      buttons: (nxt ? [{ act: 'start', id: nxt.id, label: 'Start next guide', primary: true }, { act: 'end', label: 'Not now' }]
        : [{ act: 'end', label: 'Done', primary: true }])
    });
    ui.root.classList.remove('is-interactive', 'is-centred');
    showOverlay(); layout(true); follow(); focusCard(); refreshPicker();
    ui.live.textContent = 'Guide finished.';
  }

  function end(dismiss) {
    if (!run) return;
    if (dismiss && run.mode !== 'finish') remember(run.tour.id, 'dismissed');
    store.del(LS_ACTIVE);
    teardown();
    refreshPicker();
    var ret = lastFocus && lastFocus.isConnected && lastFocus !== D.body ? lastFocus : first('[data-nt-launcher] .nt-launch__btn');
    try { if (ret) ret.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
  }
  function teardown() {
    listen(false);
    clearMargin();
    run = null;
    if (ui) { ui.root.hidden = true; ui.root.className = 'nt-root'; }
    D.documentElement.classList.remove('nt-on', 'nt-docked');
  }

  /* ---------------- the loop: find the target, keep up with the page ---------------- */
  /* A dialog the page itself opened (a confirmation, "complete your profile first?"). It needs the
     person's answer before anything else, so the guide steps aside until it is gone. */
  function foreignModal() {
    var list = D.querySelectorAll('[aria-modal="true"], dialog[open]');
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (ui && ui.root.contains(m)) continue;
      if (launch && launch.wrap.contains(m)) continue;
      if (visible(m) || (m.firstElementChild && visible(m.firstElementChild))) return m;
    }
    return null;
  }
  function tick() {
    if (!run || run.mode === 'finish') { if (run) layout(); return; }
    if (!ui || !ui.root.isConnected) { ui = null; build(); run.mode = 'waiting'; }
    var step = cur();
    if (!step) return;
    var modal = foreignModal();
    if (modal && !(run.el && modal.contains(run.el)) && !(step.target && first(step.target) && modal.contains(first(step.target)))) {
      if (!run.paused) { run.paused = true; hideOverlay(); }
      return;
    }
    if (run.paused) {
      run.paused = false;
      if (run.mode === 'step') { run.mode = 'waiting'; setDeadline(step); }   // show the step again, freshly placed
      else if (run.mode === 'missing') { showMissing(); return; }
    }
    // The click this step waits for was made some other way (the page asked a question first,
    // then moved on by itself): carry on from the page we are now on.
    if (step.type === 'click' && !pathOk(step)) {
      var after = steps()[run.i + 1];
      if (after && after.type !== 'navigate' && (after.path || after.path_re) && pathOk(after)) { run.flipped = false; return go(run.i + 1, 1); }
    }
    var found = pathOk(step) ? resolve(step) : null;
    if (found) {
      if (run.mode !== 'step' || run.el !== found.el || run.via !== found.via) present(found); else layout();
      return;
    }
    if (run.mode === 'step') { run.mode = 'waiting'; setDeadline(step); hideOverlay(); return; }
    if (run.mode === 'waiting') {
      // A page that is still loading gets more time, but not for ever (some pages poll all day).
      if (busy() && now() < run.since + 9000) run.deadline = Math.max(run.deadline, now() + 1200);
      if (now() > run.deadline) {
        if (step.optional) return go(run.i + run.dir, run.dir);
        showMissing();
      }
    }
  }

  function present(found) {
    var step = cur();
    clearMargin();
    run.mode = 'step'; run.el = found.el; run.via = found.via; run.flipped = false;
    var tour = run.tour, n = ordinal(tour, run.i), total = countable(tour);
    var last = run.i >= steps().length - 1;
    var interactive = found.via || step.type === 'click' || step.type === 'fill';
    var buttons = [];
    if (prevIndex(run.i) >= 0) buttons.push({ act: 'back', label: 'Back', icon: IC.back });
    if (found.via) buttons.push({ act: 'next', label: last ? 'Finish' : 'Skip this step' });
    else if (step.type === 'click') buttons.push({ act: 'next', label: 'Skip this step' });
    else buttons.push({ act: 'next', label: step.next_label || (last ? 'Finish' : 'Next'), primary: true, icon: last ? IC.check : IC.arrow, after: true });
    var hint = '';
    if (found.via) hint = icon(IC.pointer, 15) + '<span>Open the menu first. It is highlighted.</span>';
    else if (step.type === 'click') hint = icon(IC.pointer, 15) + '<span>Click the highlighted item to continue.</span>';
    else if (step.type === 'fill') hint = icon(IC.edit, 15) + '<span>Fill in the highlighted part, then press Next.</span>';
    renderCard({
      kicker: tour.title, title: step.title, text: step.text, hint: hint, n: n, total: total, buttons: buttons,
      skip: step.skip_label || 'End guide'
    });
    ui.root.classList.toggle('is-interactive', !!interactive);
    ui.root.classList.toggle('is-centred', !found.el);
    showOverlay();
    if (found.el && !found.via) closeDrawerFor(found.el);
    if (found.el) ensureVisible(found.el);
    layout(true);
    follow();
    focusCard();
    ui.live.textContent = 'Step ' + n + ' of ' + total + '. ' + plain(step.title) + '. ' + plain(step.text);
  }

  function closeDrawerFor(target) {      // a phone menu left open by the last step would cover this one
    var drawer = D.querySelector('#pp-side, #app-sidebar');
    if (!drawer || drawer.contains(target) || target.contains(drawer)) return;
    var closer = first(['#pp-side [data-pp-menu-close]', '.app-sidebar-backdrop']);
    if (closer) { try { closer.click(); } catch (e) { /* ignore */ } }
  }

  function showMissing() {
    var step = cur(), tour = run.tour;
    run.mode = 'missing'; run.el = null;
    var on = pathOk(step);
    var link = step.open || (!on && step.path ? { label: 'Take me there', url: step.path } : null);
    if (!link && !on) link = { label: 'Back to the start of this guide', url: tour.start };
    var last = run.i >= steps().length - 1;
    var buttons = [];
    if (link) buttons.push({ act: 'link', url: link.url, label: link.label, primary: true, icon: IC.arrow, after: true });
    // A step with its own "missing" message is a gate: what follows depends on it, so there is nothing to skip to.
    if (!last && (!step.missing || step.skippable)) buttons.push({ act: 'next', label: 'Skip this step' });
    else if (!link) buttons.push({ act: 'close', label: 'End guide', primary: true });
    renderCard({
      kicker: tour.title, n: ordinal(tour, run.i), total: countable(tour),
      title: !on ? 'This step is on another page' : (step.missing ? 'Nothing to show here yet' : 'I cannot find this here'),
      text: step.missing || (on ? 'The next step is *' + plain(step.title) + '*, but it is not on this page right now. It may not be open for you yet.'
        : 'The next step is *' + plain(step.title) + '*. Open that page and I will carry on.'),
      buttons: buttons, skip: buttons.some(function (b) { return b.act === 'close'; }) ? '' : 'End guide', warn: true
    });
    ui.root.hidden = false;
    ui.root.className = 'nt-root is-docked';
    D.documentElement.classList.add('nt-docked');
    ui.card.removeAttribute('style');
    if (launch && !isPhone()) ui.card.style.left = launch.wrap.style.left;
    ui.live.textContent = plain(step.missing || 'This step cannot be shown on this page.');
  }

  /* ---------------- card ---------------- */
  function renderCard(c) {
    var html = '<div class="nt-card__bar"><span style="width:' + (c.total ? Math.round(100 * c.n / c.total) : 100) + '%"></span></div>' +
      '<div class="nt-card__top"><span class="nt-card__kicker' + (c.done ? ' is-done' : '') + (c.warn ? ' is-warn' : '') + '">' + (c.done ? icon(IC.check, 13) : icon(IC.compass, 13)) + '<span>' + esc(c.kicker) + '</span></span>' +
      '<button type="button" class="nt-x" data-nt="close" aria-label="End this guide" title="End this guide (Esc)">' + icon(IC.x, 16) + '</button></div>' +
      '<h2 class="nt-card__title" id="nt-title">' + rich(c.title) + '</h2>' +
      '<p class="nt-card__text" id="nt-text">' + rich(c.text) + '</p>' +
      (c.hint ? '<p class="nt-card__hint">' + c.hint + '</p>' : '') +
      '<div class="nt-card__foot"><span class="nt-card__count">' + (c.total ? 'Step ' + c.n + ' of ' + c.total : '') + '</span><span class="nt-card__btns">';
    (c.buttons || []).forEach(function (b) {
      var inner = (b.icon && !b.after ? icon(b.icon, 15) : '') + '<span>' + esc(b.label) + '</span>' + (b.icon && b.after ? icon(b.icon, 15) : '');
      if (b.act === 'link') html += '<a class="nt-btn' + (b.primary ? ' nt-btn--primary' : '') + '" data-nt="link" href="' + esc(withBase(b.url)) + '">' + inner + '</a>';
      else html += '<button type="button" class="nt-btn' + (b.primary ? ' nt-btn--primary' : '') + '" data-nt="' + b.act + '"' + (b.id ? ' data-id="' + esc(b.id) + '"' : '') + '>' + inner + '</button>';
    });
    html += '</span></div>';
    if (c.skip) html += '<button type="button" class="nt-card__skip" data-nt="close">' + esc(c.skip) + '</button>';
    ui.card.innerHTML = html;
  }
  function onCardClick(e) {
    var b = e.target.closest('[data-nt]');
    if (!b || !run) return;
    var act = b.getAttribute('data-nt');
    if (act === 'link') { persist(); return; }           // a real link: the tour resumes on arrival
    e.preventDefault();
    if (act === 'close') end(true);
    else if (act === 'end') end(false);
    else if (act === 'next') next();
    else if (act === 'back') back();
    else if (act === 'start') { var id = b.getAttribute('data-id'); teardown(); start(id); }
  }
  function focusables() {
    var list = [].slice.call(ui.card.querySelectorAll('button:not([disabled]), a[href]'));
    var step = cur();
    if (run && run.mode === 'step' && run.el && (run.via || (step && (step.type === 'click' || step.type === 'fill')))) {
      var t = run.el.matches('a[href],button,input,select,textarea,[tabindex]') ? run.el : run.el.querySelector('a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select,textarea');
      if (t) list.unshift(t);
    }
    return list;
  }
  function focusCard() {
    var primary = ui.card.querySelector('.nt-btn--primary') || ui.card;
    try { primary.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
  }

  /* ---------------- overlay geometry ---------------- */
  function showOverlay() {
    ui.root.hidden = false;
    ui.root.classList.remove('is-docked');
    ui.root.classList.add('is-on');
    D.documentElement.classList.remove('nt-docked');
  }
  function hideOverlay() {
    if (!ui) return;
    ui.root.hidden = true;
    D.documentElement.classList.remove('nt-docked');
  }
  var marginEl = null;
  function clearMargin() { if (marginEl) { marginEl.style.scrollMarginTop = marginEl._ntOld || ''; marginEl = null; } }
  function isFixed(n) {
    for (var p = n; p && p !== D.body; p = p.parentElement) {
      var pos = W.getComputedStyle(p).position;
      if (pos === 'fixed') return true;
    }
    return false;
  }
  function ensureVisible(n) {
    var r = n.getBoundingClientRect(), vh = W.innerHeight;
    var top = 96, bottom = isPhone() ? Math.min(vh * 0.5, ui.card.offsetHeight + 40) : 48;
    if (r.top >= top && r.bottom <= vh - bottom) return;
    var tall = r.height > vh * 0.5;
    if (isFixed(n) && !tall) { try { n.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ } return; }
    marginEl = n; n._ntOld = n.style.scrollMarginTop; n.style.scrollMarginTop = '104px';
    try { n.scrollIntoView({ block: tall || isPhone() ? 'start' : 'center', inline: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' }); }
    catch (e) { n.scrollIntoView(); }
  }
  function follow() {            // keep the hole glued to the target while the page scrolls or settles
    rafUntil = now() + 1100;
    if (rafOn) return;
    rafOn = true;
    (function loop() {
      if (!run || now() > rafUntil) { rafOn = false; return; }
      layout();
      W.requestAnimationFrame(loop);
    })();
  }
  function setBox(n, l, t, w, h) {
    n.style.left = l + 'px'; n.style.top = t + 'px'; n.style.width = Math.max(0, w) + 'px'; n.style.height = Math.max(0, h) + 'px';
  }
  var lastPath = '';
  function layout(fresh) {
    if (!run || !ui || ui.root.hidden || run.mode === 'missing' || run.mode === 'waiting') return;
    var vw = W.innerWidth, vh = W.innerHeight, card = ui.card, n = run.el, phone = isPhone();
    if (n && !n.isConnected) { tick(); return; }
    card.style.maxWidth = Math.min(380, vw - 24) + 'px';
    if (!n) {                                   // centred card, whole page dimmed
      setBox(ui.shades[0], 0, 0, vw, vh);
      for (var k = 1; k < 4; k++) setBox(ui.shades[k], 0, 0, 0, 0);
      ui.ring.style.display = 'none'; ui.block.style.display = 'none'; ui.svg.style.display = 'none';
      ui.root.classList.add('is-centred');
      card.style.left = Math.round((vw - card.offsetWidth) / 2) + 'px';
      card.style.top = Math.round(Math.max(16, (vh - card.offsetHeight) / 2 - vh * 0.04)) + 'px';
      return;
    }
    ui.root.classList.remove('is-centred');
    var r = n.getBoundingClientRect(), pad = 8, cw = card.offsetWidth, ch = card.offsetHeight, G = 68, M = 12;
    var hole = { l: Math.max(4, r.left - pad), t: Math.max(4, r.top - pad), r: Math.min(vw - 4, r.right + pad), b: Math.min(vh - 4, r.bottom + pad) };
    // A target too big to leave room for the card anywhere: highlight its top part only.
    var need = ch + G + M;
    if (!phone && vh - hole.b < need && hole.t < need && vw - hole.r < cw + G + M && hole.l < cw + G + M) {
      hole.b = Math.max(hole.t + 120, Math.min(hole.b, vh - need - 2));
    }
    if (phone && hole.b - hole.t > vh * 0.42) hole.b = hole.t + vh * 0.42;
    if (hole.b < hole.t + 8) hole.b = hole.t + 8;
    var hw = hole.r - hole.l, hh = hole.b - hole.t;
    setBox(ui.shades[0], 0, 0, vw, hole.t);
    setBox(ui.shades[2], 0, hole.b, vw, vh - hole.b);
    setBox(ui.shades[3], 0, hole.t, hole.l, hh);
    setBox(ui.shades[1], hole.r, hole.t, vw - hole.r, hh);
    ui.ring.style.display = ''; setBox(ui.ring, hole.l, hole.t, hw, hh);
    var blocked = !ui.root.classList.contains('is-interactive');
    ui.block.style.display = blocked ? '' : 'none';
    if (blocked) setBox(ui.block, hole.l, hole.t, hw, hh);

    // Where the card goes.
    var left, top, side;
    if (phone) {
      side = (hole.t + hh / 2) > vh * 0.5 ? 'top' : 'bottom';
      left = Math.round((vw - cw) / 2);
      top = side === 'top' ? 10 : vh - ch - 10;
      var tb = D.querySelector('.qa-tabbar, .app-tabbar');
      if (side === 'bottom' && tb && visible(tb) && !(hole.b > tb.getBoundingClientRect().top)) top = Math.min(top, tb.getBoundingClientRect().top - ch - 8);
      top = Math.max(8, top);
    } else {
      var space = { bottom: vh - hole.b, top: hole.t, right: vw - hole.r, left: hole.l };
      var fits = function (s) { return (s === 'bottom' || s === 'top') ? space[s] >= ch + G + M : space[s] >= cw + G + M; };
      var order = ['bottom', 'top', 'right', 'left'];
      var step = cur();
      if (step && step.placement) order.unshift(step.placement);
      if (hw < 320 && hh > 220) order = ['right', 'left', 'bottom', 'top'];
      side = null;
      for (var i = 0; i < order.length; i++) if (fits(order[i])) { side = order[i]; break; }
      var cx = hole.l + hw / 2, cy = hole.t + hh / 2;
      if (side === 'bottom') { top = hole.b + G; left = cx - cw / 2; }
      else if (side === 'top') { top = hole.t - G - ch; left = cx - cw / 2; }
      else if (side === 'right') { left = hole.r + G; top = cy - ch / 2; }
      else if (side === 'left') { left = hole.l - G - cw; top = cy - ch / 2; }
      else { side = 'inside'; left = vw - cw - 24; top = vh - ch - 24; }
      // Nudge the card sideways a little so the arrow gets a visible curve.
      if ((side === 'bottom' || side === 'top') && hw < cw * 0.9) left += (cx < vw / 2 ? 1 : -1) * Math.min(70, cw * 0.22);
      left = clamp(Math.round(left), M, vw - cw - M);
      top = clamp(Math.round(top), M, vh - ch - M);
    }
    card.style.left = left + 'px'; card.style.top = top + 'px';
    card.setAttribute('data-side', side);

    // The arrow: from the card's edge to the edge of the hole.
    var c = { l: left, t: top, r: left + cw, b: top + ch };
    var overlap = !(c.r < hole.l || c.l > hole.r || c.b < hole.t || c.t > hole.b);
    if (overlap) { ui.svg.style.display = 'none'; return; }
    var ccx = (c.l + c.r) / 2, ccy = (c.t + c.b) / 2;
    var hx = hole.l + hw / 2, hy = hole.t + hh / 2;
    var ex = clamp(ccx, hole.l + Math.min(18, hw / 2), hole.r - Math.min(18, hw / 2)), ey = clamp(ccy, hole.t + Math.min(14, hh / 2), hole.b - Math.min(14, hh / 2));
    // Aim nearer the middle of small targets, and at the nearest edge of the hole.
    if (c.t >= hole.b) ey = hole.b; else if (c.b <= hole.t) ey = hole.t;
    if (c.l >= hole.r) ex = hole.r; else if (c.r <= hole.l) ex = hole.l;
    if (ey === hole.b || ey === hole.t) ex = clamp(hx + (ccx - hx) * 0.25, hole.l + Math.min(18, hw / 2), hole.r - Math.min(18, hw / 2));
    if (ex === hole.r || ex === hole.l) ey = clamp(hy + (ccy - hy) * 0.25, hole.t + Math.min(14, hh / 2), hole.b - Math.min(14, hh / 2));
    var sx = clamp(ex, c.l + 28, c.r - 28), sy = clamp(ey, c.t + 18, c.b - 18);
    if (c.t >= hole.b) sy = c.t; else if (c.b <= hole.t) sy = c.b;
    if (c.l >= hole.r) sx = c.l; else if (c.r <= hole.l) sx = c.r;
    var dx = ex - sx, dy = ey - sy, dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < 26) { ui.svg.style.display = 'none'; return; }
    var ux = dx / dist, uy = dy / dist;
    ex -= ux * 9; ey -= uy * 9; sx += ux * 3; sy += uy * 3;
    var bow = Math.min(54, dist * 0.3) * ((sx + ex) / 2 < vw / 2 ? -1 : 1) * (uy < 0 ? -1 : 1);
    var mx = (sx + ex) / 2 - uy * bow, my = (sy + ey) / 2 + ux * bow;
    var d = 'M' + sx.toFixed(1) + ' ' + sy.toFixed(1) + ' Q' + mx.toFixed(1) + ' ' + my.toFixed(1) + ' ' + ex.toFixed(1) + ' ' + ey.toFixed(1);
    ui.svg.style.display = '';
    ui.svg.setAttribute('viewBox', '0 0 ' + vw + ' ' + vh);
    ui.svg.setAttribute('width', vw); ui.svg.setAttribute('height', vh);
    if (d !== lastPath) { ui.line.setAttribute('d', d); ui.halo.setAttribute('d', d); lastPath = d; }
    if (fresh) {                                // draw the arrow in, then let it "flow" toward the target
      ui.svg.classList.remove('is-drawn', 'is-flow');
      var len = 0; try { len = ui.line.getTotalLength(); } catch (e) { len = dist; }
      ui.line.style.strokeDasharray = len + ' ' + len;
      ui.line.style.strokeDashoffset = len;
      void ui.line.getBoundingClientRect();
      ui.svg.classList.add('is-drawn');
      ui.line.style.strokeDashoffset = '0';
      var mine = run, at = run.i;
      W.setTimeout(function () {
        if (run !== mine || run.i !== at) return;
        ui.line.style.strokeDasharray = ''; ui.line.style.strokeDashoffset = '';
        ui.svg.classList.add('is-flow');
      }, reducedMotion() ? 0 : 620);
    }
  }

  /* ---------------- events while a tour runs ---------------- */
  function onDocClick(e) {
    if (!run || run.mode !== 'step' || !run.el) return;
    var step = cur();
    if (!step || run.via) { if (run.via) W.setTimeout(tick, 250); return; }
    if (step.type === 'click' && run.el.contains(e.target)) {
      // Save first: the click may load a new page straight away.
      var nextI = run.i + 1;
      store.set(LS_ACTIVE, { id: run.tour.id, i: nextI, t: now() });
      var mine = run;
      W.setTimeout(function () { if (run === mine && run.i === nextI - 1) { run.flipped = false; go(nextI, 1); } }, 0);
    }
  }
  function hasValue(n) {
    var fields = n.matches('input,select,textarea') ? [n] : [].slice.call(n.querySelectorAll('input,select,textarea'));
    return fields.some(function (f) {
      if (f.type === 'hidden' || f.disabled) return false;
      if (f.type === 'checkbox' || f.type === 'radio') return f.checked;
      if (f.type === 'file') return f.files && f.files.length > 0;
      return String(f.value || '').trim().length > 0;
    });
  }
  function onInput(e) {
    if (!run || run.mode !== 'step' || !run.el || run.via) return;
    var step = cur();
    if (!step || step.type !== 'fill' || !run.el.contains(e.target) && run.el !== e.target) return;
    var ok = hasValue(run.el);
    if (ok === run.filled) return;
    run.filled = ok;
    var hint = ui.card.querySelector('.nt-card__hint');
    if (hint) { hint.classList.toggle('is-ok', ok); hint.innerHTML = ok ? icon(IC.check, 15) + '<span>Good. Press Next to carry on.</span>' : icon(IC.edit, 15) + '<span>Fill in the highlighted part, then press Next.</span>'; }
    var nb = ui.card.querySelector('.nt-btn--primary'); if (nb) nb.classList.toggle('is-ready', ok);
  }
  function onKey(e) {
    if (!run || run.paused) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); end(true); return; }
    var inCard = ui.card.contains(D.activeElement);
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((D.activeElement || {}).tagName || '') || (D.activeElement && D.activeElement.isContentEditable);
    if (e.key === 'Tab' && run.mode !== 'missing') {
      var list = focusables();
      if (!list.length) return;
      var i = list.indexOf(D.activeElement);
      if (i < 0 && typing && run.el && run.el.contains(D.activeElement)) return;   // moving between fields of the target
      e.preventDefault();
      var j = i < 0 ? 0 : (i + (e.shiftKey ? -1 : 1) + list.length) % list.length;
      try { list[j].focus({ preventScroll: true }); } catch (err) { /* ignore */ }
      return;
    }
    if (typing && !inCard) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var step = cur();
    if (e.key === 'ArrowRight') {
      if (run.mode === 'step' && step && step.type !== 'click' && !run.via) { e.preventDefault(); next(); }
    } else if (e.key === 'ArrowLeft') {
      if (run.mode === 'step' || run.mode === 'missing') { e.preventDefault(); back(); }
    }
  }
  var relayoutQueued = false;
  function relayout() {
    if (relayoutQueued) return;
    relayoutQueued = true;
    W.requestAnimationFrame(function () { relayoutQueued = false; layout(); });
  }
  function onSettle() { W.setTimeout(tick, 60); }
  function listen(on) {
    var f = on ? 'addEventListener' : 'removeEventListener';
    D[f]('click', onDocClick, true);
    D[f]('input', onInput, true);
    D[f]('change', onInput, true);
    D[f]('keydown', onKey, true);
    W[f]('resize', relayout);
    W[f]('scroll', relayout, true);
    D[f]('htmx:afterSettle', onSettle);
    W[f]('popstate', onSettle);
    if (on) {
      ticker = W.setInterval(tick, 300);
      if (W.MutationObserver) {
        var queued = false;
        observer = new MutationObserver(function () {
          if (queued || !run || run.mode === 'step') return;   // a shown step is kept in place by the interval
          queued = true; W.setTimeout(function () { queued = false; tick(); }, 80);
        });
        observer.observe(D.body, { childList: true, subtree: true });
      }
    } else {
      if (ticker) W.clearInterval(ticker); ticker = null;
      if (observer) observer.disconnect(); observer = null;
    }
  }

  /* ---------------- launcher + picker ---------------- */
  var launch = null;
  function buildLauncher() {
    if (launch && launch.wrap.isConnected) return;
    var wrap = el('div', 'nt-launch'); wrap.setAttribute('data-nt-launcher', '');
    var btn = el('button', 'nt-launch__btn', icon(IC.compass, 20) + '<span class="nt-launch__label">Guide me</span>');
    btn.type = 'button'; btn.setAttribute('aria-haspopup', 'dialog'); btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-controls', 'nt-picker');
    btn.title = 'Guide me: step-by-step help on the real pages';
    var panel = el('div', 'nt-picker'); panel.id = 'nt-picker'; panel.hidden = true;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Guides');
    panel.innerHTML = '<div class="nt-picker__head"><div><strong>What do you want to do?</strong><span>Pick one and I will point at each step on the real pages.</span></div>' +
      '<button type="button" class="nt-x" data-nt-close aria-label="Close">' + icon(IC.x, 16) + '</button></div>' +
      '<label class="nt-picker__search">' + icon(IC.search, 16) + '<span class="nt-sr">Search the guides</span><input type="search" placeholder="Search, e.g. pay a bill" autocomplete="off"></label>' +
      '<div class="nt-picker__list" role="list"></div>' +
      (BOOT.helpUrl ? '<a class="nt-picker__foot" href="' + esc(BOOT.helpUrl) + '">' + icon(IC.book, 15) + '<span>Read the written guides</span></a>' : '');
    wrap.appendChild(panel); wrap.appendChild(btn);
    D.body.appendChild(wrap);
    launch = { wrap: wrap, btn: btn, panel: panel, input: panel.querySelector('input'), list: panel.querySelector('.nt-picker__list') };
    if (!store.get(LS_USED)) wrap.classList.add('is-fresh');   // idle glow until the guides are first opened (tour.css)
    btn.addEventListener('click', function () { if (run && run.mode !== 'missing') return; panel.hidden ? openPicker() : closePicker(); });
    panel.querySelector('[data-nt-close]').addEventListener('click', function () { closePicker(true); });
    launch.input.addEventListener('input', refreshPicker);
    launch.list.addEventListener('click', function (e) {
      var b = e.target.closest('[data-id]'); if (!b) return;
      if (run) { store.del(LS_ACTIVE); teardown(); }
      start(b.getAttribute('data-id'));
    });
    panel.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.stopPropagation(); closePicker(true); }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        var items = [].slice.call(launch.list.querySelectorAll('[data-id]')); if (!items.length) return;
        var i = items.indexOf(D.activeElement);
        e.preventDefault();
        if (e.key === 'ArrowUp' && i <= 0) { launch.input.focus(); return; }
        items[clamp(i + (e.key === 'ArrowDown' ? 1 : -1), 0, items.length - 1)].focus();
      }
    });
    D.addEventListener('click', function (e) {
      if (!launch.panel.hidden && !launch.wrap.contains(e.target) && !e.target.closest('[data-tour-picker]')) closePicker();
    });
    placeLauncher();
  }
  /* The launcher is a 44px round button. On wide screens it sits in the gutter
     between the side menu and the page, so it never lies on top of a control;
     on narrow screens it sits in the corner and the page gets matching bottom
     space (--nt-clear, used by ux-affordance.css). It also moves above the
     reminder card when the two would touch. */
  var SIZE = 44;
  function placeLauncher() {
    if (!launch) return;
    var main = D.querySelector('#qa-main, #app-content, main');
    var left = 12, gutter = false;
    if (main && W.innerWidth >= 900) {
      var ml = Math.round(main.getBoundingClientRect().left);
      if (ml >= 40) { left = ml - 12; gutter = true; }      // straddles the menu edge, clear of the page column
    }
    launch.wrap.style.left = left + 'px';
    var bottom = 16, tb = D.querySelector('.qa-tabbar, .app-tabbar');
    if (tb) {
      var r = tb.getBoundingClientRect();
      if (r.height > 0 && W.getComputedStyle(tb).display !== 'none' && r.top > W.innerHeight * 0.6) bottom = Math.round(W.innerHeight - r.top) + 12;
    }
    var rcard = D.querySelector('.rc-card:not([hidden])');
    if (rcard) {
      var c = rcard.getBoundingClientRect();
      if (c.width > 0 && c.left < left + SIZE + 8 && c.bottom > W.innerHeight - bottom - SIZE - 8) bottom = Math.round(W.innerHeight - c.top) + 10;
    }
    launch.wrap.style.bottom = bottom + 'px';
    launch.wrap.classList.toggle('is-gutter', gutter);
    D.documentElement.style.setProperty('--nt-clear', gutter ? '0px' : (SIZE + 20) + 'px');
  }
  /* Scrolling down shrinks the button out of the way; scrolling up brings it back. */
  var lastY = 0, scrollQueued = false;
  function onScroll() {
    if (scrollQueued) return;
    scrollQueued = true;
    (W.requestAnimationFrame || W.setTimeout)(function () {
      scrollQueued = false;
      if (!launch) return;
      var y = W.pageYOffset || D.documentElement.scrollTop || 0;
      if (!launch.panel.hidden || y < 40) launch.wrap.classList.remove('is-min');
      else if (y > lastY + 6) launch.wrap.classList.add('is-min');
      else if (y < lastY - 6) launch.wrap.classList.remove('is-min');
      lastY = y;
    });
  }
  /* The words "Guide me" show for a few seconds on the first page of a browser session, then on hover / focus. */
  function introLabel() {
    if (!launch) return;
    try { if (W.sessionStorage.getItem('nbte.tour.intro')) return; W.sessionStorage.setItem('nbte.tour.intro', '1'); } catch (e) { return; }
    launch.wrap.classList.add('is-intro');
    W.setTimeout(function () { if (launch) launch.wrap.classList.remove('is-intro'); }, 4500);
  }
  function relevant(t) {
    var p = herePath();
    return (t.pages || []).some(function (x) {
      x = String(x);
      if (x.charAt(x.length - 1) === '$') return normPath(x.slice(0, -1)) === p;   // "/home/$": that page only
      return p.indexOf(normPath(x)) === 0;
    });
  }
  function hereArea() { return herePath().indexOf('/qa/') === 0 ? 'qa' : 'main'; }
  function refreshPicker() {
    if (!launch) return;
    if (!loaded) { launch.list.innerHTML = '<p class="nt-picker__empty">Loading the guides…</p>'; return; }
    if (!TOURS.length) { launch.list.innerHTML = '<p class="nt-picker__empty">The guides could not be loaded. Check your connection and reload the page.</p>'; return; }
    var q = (launch.input.value || '').trim().toLowerCase();
    var words = q.split(/\s+/).filter(Boolean);
    var rows = TOURS.filter(function (t) {
      if (!words.length) return true;
      var hay = (t.title + ' ' + t.summary + ' ' + (t.keywords || '') + ' ' + (t.role || '') + ' ' + (t.group || '')).toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) >= 0; });
    });
    var here = rows.filter(relevant), rest = rows.filter(function (t) { return !relevant(t); });
    // The rest: this workspace's guides first (main system or QA), then under their own headings.
    var area = hereArea(), order = [], byGroup = {};
    rest.filter(function (t) { return !t.area || t.area === area; })
      .concat(rest.filter(function (t) { return t.area && t.area !== area; }))
      .forEach(function (t) {
        var g = t.group || '';
        if (!byGroup[g]) { byGroup[g] = []; order.push(g); }
        byGroup[g].push(t);
      });
    function item(t) {
      var done = seen.done.indexOf(t.id) >= 0;
      return '<button type="button" class="nt-item' + (done ? ' is-done' : '') + '" role="listitem" data-id="' + esc(t.id) + '">' +
        '<span class="nt-item__ic">' + (done ? icon(IC.check, 15) : icon(IC.compass, 15)) + '</span>' +
        '<span class="nt-item__main"><strong>' + esc(t.title) + '</strong><small>' + esc(t.summary) + '</small>' +
        '<em>' + countable(t) + ' steps' + (t.role ? ' · for the ' + esc(t.role) : '') + (done ? ' · done' : '') + '</em></span>' +
        '<span class="nt-item__go">' + icon(IC.arrow, 15) + '</span></button>';
    }
    var html = '';
    if (here.length) html += '<p class="nt-picker__group">' + icon(IC.pin, 12) + ' On this page</p>' + here.map(item).join('');
    order.forEach(function (g) {
      html += '<p class="nt-picker__group">' + esc(g || (here.length ? 'More guides' : 'All guides')) + '</p>' + byGroup[g].map(item).join('');
    });
    if (!rows.length) html = '<p class="nt-picker__empty">No guide matches “' + esc(q) + '”. Try another word, or read the written guides below.</p>';
    launch.list.innerHTML = html;
  }
  function openPicker() {
    buildLauncher();
    refreshPicker();
    launch.panel.hidden = false;
    launch.btn.setAttribute('aria-expanded', 'true');
    launch.wrap.classList.add('is-open'); launch.wrap.classList.remove('is-min');
    launch.wrap.classList.remove('is-fresh'); store.set(LS_USED, 1);
    try { launch.input.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
  }
  function closePicker(refocus) {
    if (!launch || launch.panel.hidden) return;
    launch.panel.hidden = true;
    launch.btn.setAttribute('aria-expanded', 'false');
    launch.wrap.classList.remove('is-open');
    if (refocus) { try { launch.btn.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
  }

  /* ---------------- boot ---------------- */
  function fromUrl() {
    var m = W.location.search.match(/[?&]tour=([^&#]*)/);
    if (!m) return '';
    var id = decodeURIComponent(m[1] || '');
    try {
      var url = new URL(W.location.href); url.searchParams.delete('tour');
      W.history.replaceState(W.history.state, '', url.pathname + url.search + url.hash);
    } catch (e) { /* ignore */ }
    return id || 'menu';
  }
  function autostart(force) {
    var id = BOOT.autostart;
    if (!id || !BY[id] || run) return false;
    if (!force && (touched(id) || W.navigator.webdriver)) return false;   // never interrupt automated browsers
    if (normPath(BY[id].start) !== herePath()) return false;
    return start(id);
  }
  function init() {
    buildLauncher();
    introLabel();
    W.addEventListener('resize', placeLauncher);
    W.addEventListener('scroll', onScroll, { passive: true });
    D.addEventListener('nbte:floaters', placeLauncher);          // the reminder card came or went (ux-affordance.js)
    D.addEventListener('htmx:afterSettle', function () { placeLauncher(); if (launch && !launch.panel.hidden) refreshPicker(); });
    D.addEventListener('click', function (e) {
      var s = e.target.closest && e.target.closest('[data-tour-start]');
      if (s) {
        var id = s.getAttribute('data-tour-start');
        if (BY[id]) { e.preventDefault(); e.stopPropagation(); start(id); }
        return;
      }
      var p = e.target.closest && e.target.closest('[data-tour-picker]');
      if (p) { e.preventDefault(); openPicker(); }
    }, true);

    var asked = fromUrl();
    whenLoaded(function () { refreshPicker(); begin(asked); });
  }
  function begin(asked) {
    if (asked) {
      store.del(LS_ACTIVE);
      if (BY[asked]) W.setTimeout(function () { start(asked); }, 350);
      else W.setTimeout(openPicker, 350);
      return;
    }
    var saved = store.get(LS_ACTIVE);
    if (saved && BY[saved.id] && now() - (saved.t || 0) < RESUME_MS && saved.i < BY[saved.id].steps.length) {
      W.setTimeout(function () { start(saved.id, { step: saved.i || 0, resume: true }); }, 250);
      return;
    }
    if (saved) store.del(LS_ACTIVE);
    var kick = function () { W.setTimeout(function () { autostart(false); }, 900); };
    if (D.readyState === 'complete') kick(); else W.addEventListener('load', kick, { once: true });
  }

  W.NBTETour = {
    start: start, end: function () { end(true); }, open: openPicker, close: closePicker, autostart: autostart,
    tours: TOURS, seen: seen, ready: whenLoaded, loaded: function () { return loaded; },
    state: function () { return run ? { id: run.tour.id, step: run.i, mode: run.mode, via: run.via } : null; },
    target: function () { return run ? run.el : null; },
    reset: function () {
      seen.done.length = 0; seen.dismissed.length = 0; store.del(LS_SEEN); store.del(LS_ACTIVE);
      if (BOOT.canSave && W.fetch) W.fetch(BOOT.saveUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify({ state: 'reset' }) });
    }
  };

  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
