/* NBTE — shared-poller.js (v2.1 Phase E, L9 / T-PERF-5)

   One poller per browser instead of one per tab. Every tab subscribes to the
   streams it needs (notification badge, chat inbox ...); ONE tab, the leader,
   fetches each stream and shares the answer with the other tabs over a
   BroadcastChannel. With five tabs open the server sees one poll stream.

   * Leader election: the Web Locks API (navigator.locks) when available - the
     lock passes to another tab the moment the leader closes. A visible tab
     takes the lock from a hidden leader (hidden tabs' timers are throttled).
     Without Web Locks: a localStorage lease renewed every 2 s.
   * No BroadcastChannel (or storage blocked): every tab polls for itself,
     exactly as before.
   * Versioned streams: the leader sends back the `v` of the last answer; the
     server answers {"unchanged": true} when nothing changed, and nothing is
     broadcast.

   API:
     var sub = NBTEPoll.subscribe("inbox", {url, visibleMs, hiddenMs, onData});
     sub.kick();   // ask for a fresh answer soon (after an action)
     sub.stop();
     NBTEPoll.mode  // "shared" | "solo"
*/
(function () {
  'use strict';
  if (window.NBTEPoll) return;

  var CHANNEL = 'nbte-poll-v1';
  var LOCK = 'nbte-poll-leader';
  var LEASE_KEY = 'nbte-poll-lease';
  var HEARTBEAT_MS = 4000;
  var PEER_TTL_MS = 13000;
  var TAB = Math.random().toString(36).slice(2) + Date.now().toString(36);

  var bc = null;
  try { if ('BroadcastChannel' in window) bc = new BroadcastChannel(CHANNEL); } catch (e) { bc = null; }
  var hasLocks = !!(navigator.locks && navigator.locks.request);
  var hasStorage = (function () {
    try { localStorage.setItem('nbte-poll-test', '1'); localStorage.removeItem('nbte-poll-test'); return true; }
    catch (e) { return false; }
  })();
  var mode = bc && (hasLocks || hasStorage) ? 'shared' : 'solo';

  var local = {};          // key -> {spec, handlers: []}
  var isLeader = mode === 'solo';
  var leaderVisible = null;
  var peers = {};          // leader only: tab -> {keys: {key: spec}, visible, seen}
  var streams = {};        // leader only: key -> {timer, inflight, failures, v, last, due}

  function visible() { return !document.hidden; }
  function post(msg) { if (bc) { msg.from = TAB; try { bc.postMessage(msg); } catch (e) { /* closed */ } } }

  // ------------------------------------------------------------- delivery
  function deliver(key, data) {
    var entry = local[key];
    if (!entry) return;
    entry.last = data;
    entry.handlers.slice().forEach(function (h) { try { h(data); } catch (e) { /* page bug */ } });
  }

  // --------------------------------------------------------------- leader
  function mySpecs() {
    var keys = {};
    Object.keys(local).forEach(function (k) { if (local[k].handlers.length) keys[k] = local[k].spec; });
    return keys;
  }

  function wantedStreams() {
    var now = Date.now(), out = {};
    peers[TAB] = { keys: mySpecs(), visible: visible(), seen: now };
    Object.keys(peers).forEach(function (tab) {
      var p = peers[tab];
      if (tab !== TAB && now - p.seen > PEER_TTL_MS) { delete peers[tab]; return; }
      Object.keys(p.keys).forEach(function (k) {
        var spec = p.keys[k];
        var o = out[k] || (out[k] = { url: spec.url, visibleMs: spec.visibleMs, hiddenMs: spec.hiddenMs, anyVisible: false });
        o.visibleMs = Math.min(o.visibleMs, spec.visibleMs);
        o.hiddenMs = Math.min(o.hiddenMs, spec.hiddenMs);
        if (p.visible) o.anyVisible = true;
      });
    });
    return out;
  }

  function interval(key) {
    var w = wantedStreams()[key];
    if (!w) return null;
    var s = streams[key];
    var base = w.anyVisible ? w.visibleMs : w.hiddenMs;
    if (s && s.failures) base = Math.min(120000, base * Math.pow(2, Math.min(s.failures, 4)));
    return base;
  }

  function schedule(key, ms) {
    if (!isLeader) return;
    var s = streams[key] || (streams[key] = { timer: null, inflight: false, failures: 0, v: '', last: null, due: 0 });
    var at = Date.now() + ms;
    if (s.timer && s.due <= at) return;          // already due sooner
    clearTimeout(s.timer);
    s.due = at;
    s.timer = setTimeout(function () { s.timer = null; poll(key); }, ms);
  }

  function poll(key) {
    var s = streams[key];
    var w = wantedStreams()[key];
    if (!isLeader || !s) return;
    if (!w) { delete streams[key]; return; }      // nobody needs it any more
    if (s.inflight) { schedule(key, 500); return; }
    s.inflight = true;
    var url = w.url + (s.v ? (w.url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + encodeURIComponent(s.v) : '');
    fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (data) {
        s.failures = 0;
        if (data && data.v) s.v = data.v;
        if (data && data.unchanged) return;
        s.last = data;
        deliver(key, data);
        post({ type: 'data', key: key, data: data });
      })
      .catch(function () { s.failures += 1; })
      .finally(function () {
        s.inflight = false;
        var ms = interval(key);
        if (ms != null) schedule(key, ms);
      });
  }

  function reconcile() {
    if (!isLeader) return;
    var wanted = wantedStreams();
    Object.keys(wanted).forEach(function (k) {
      if (!streams[k]) schedule(k, 50);
      else if (!streams[k].timer && !streams[k].inflight) schedule(k, interval(k));
    });
  }

  var leaderBeat = null;
  function becomeLeader() {
    if (isLeader && mode === 'shared') return;
    isLeader = true;
    peers = {};
    post({ type: 'who' });
    post({ type: 'leader', visible: visible() });
    clearInterval(leaderBeat);
    leaderBeat = setInterval(function () {
      post({ type: 'leader', visible: visible() });
      reconcile();
    }, HEARTBEAT_MS);
    reconcile();
  }

  function stepDown() {
    if (!isLeader || mode === 'solo') return;
    isLeader = false;
    clearInterval(leaderBeat);
    Object.keys(streams).forEach(function (k) { clearTimeout(streams[k].timer); });
    streams = {};
    peers = {};
    announce();
  }

  // ------------------------------------------------------------ election
  function requestLock(steal) {
    navigator.locks.request(LOCK, steal ? { steal: true } : {}, function () {
      becomeLeader();
      return new Promise(function () { /* held until the tab closes or another tab steals it */ });
    }).catch(function () {
      // Stolen by a visible tab: follow, and queue up again.
      stepDown();
      requestLock(false);
    });
  }

  function leaseTick() {
    var now = Date.now(), lease = null;
    try { lease = JSON.parse(localStorage.getItem(LEASE_KEY) || 'null'); } catch (e) { lease = null; }
    var mine = lease && lease.tab === TAB;
    var stale = !lease || now - lease.ts > 6000;
    var takeOver = !mine && lease && !lease.visible && visible() && now - lease.ts > 2500;
    if (mine || stale || takeOver) {
      try { localStorage.setItem(LEASE_KEY, JSON.stringify({ tab: TAB, ts: now, visible: visible() })); } catch (e) { /* ignore */ }
      if (!isLeader) becomeLeader();
    } else if (isLeader) {
      stepDown();
    }
  }

  // ------------------------------------------------------------- follower
  function announce() {
    if (mode !== 'shared' || isLeader) return;
    post({ type: 'subs', keys: mySpecs(), visible: visible() });
  }

  if (bc) {
    bc.onmessage = function (ev) {
      var m = ev.data || {};
      if (!m || m.from === TAB) return;
      if (m.type === 'data') {
        if (!m.to || m.to === TAB) deliver(m.key, m.data);
      } else if (m.type === 'leader') {
        leaderVisible = !!m.visible;
        if (isLeader && m.from !== TAB) {
          // Two leaders (lease race): the lower id keeps it.
          if (!hasLocks && m.from < TAB) stepDown();
        }
      } else if (m.type === 'who') {
        if (!isLeader) announce();
      } else if (isLeader && m.type === 'subs') {
        var known = peers[m.from] ? peers[m.from].keys : {};
        peers[m.from] = { keys: m.keys || {}, visible: !!m.visible, seen: Date.now() };
        Object.keys(m.keys || {}).forEach(function (k) {
          var s = streams[k];
          if (s && s.last && !known[k]) post({ type: 'data', key: k, data: s.last, to: m.from });
        });
        if (m.visible) Object.keys(m.keys || {}).forEach(function (k) {
          var s = streams[k];
          if (s && s.due - Date.now() > (m.keys[k].visibleMs || 10000)) schedule(k, 300);
        });
        reconcile();
      } else if (isLeader && m.type === 'bye') {
        delete peers[m.from];
      } else if (isLeader && m.type === 'kick') {
        schedule(m.key, 300);
      }
    };
  }

  function onVisibility() {
    if (isLeader) { reconcile(); if (visible()) Object.keys(streams).forEach(function (k) { schedule(k, 300); }); return; }
    announce();
    if (visible() && leaderVisible === false && hasLocks) requestLock(true);
  }
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('pagehide', function () { post({ type: 'bye' }); });

  if (mode === 'shared') {
    if (hasLocks) requestLock(false);
    else { leaseTick(); setInterval(leaseTick, 2000); }
    setInterval(announce, HEARTBEAT_MS);
  }

  // ------------------------------------------------------------------ API
  function subscribe(key, opts) {
    var spec = { url: opts.url, visibleMs: opts.visibleMs || 10000, hiddenMs: opts.hiddenMs || 30000 };
    var entry = local[key] || (local[key] = { spec: spec, handlers: [], last: null });
    entry.spec = spec;
    var h = opts.onData;
    entry.handlers.push(h);
    if (isLeader) {
      peers[TAB] = { keys: mySpecs(), visible: visible(), seen: Date.now() };
      var s = streams[key];
      if (s && s.last) setTimeout(function () { h(s.last); }, 0);
      reconcile();
    } else {
      announce();
    }
    return {
      kick: function () { if (isLeader) schedule(key, 200); else post({ type: 'kick', key: key }); },
      stop: function () {
        entry.handlers = entry.handlers.filter(function (x) { return x !== h; });
        announce();
      },
    };
  }

  window.NBTEPoll = {
    subscribe: subscribe,
    get mode() { return mode; },
    get isLeader() { return isLeader; },
    tab: TAB,
  };
})();
