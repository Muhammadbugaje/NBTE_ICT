/* NBTE ICT — voice-notes.js (Phase 9)
   Recording (MediaRecorder) and playback of chat voice notes.

   Recording: hold the mic button to record and release to send (slide left to
   cancel), or click it to start and use Send / Cancel. Esc cancels. Max length
   comes from data-voice-max (seconds). Prefers audio/webm;codecs=opus and
   falls back to audio/mp4 (Safari). The server normalises everything to AAC
   .m4a, so playback works everywhere.

   Playback: play/pause, waveform that fills while playing, click/keyboard
   seek, 1× / 1.5× / 2× speed. One note plays at a time. */
(function () {
  'use strict';

  var root = document.querySelector('[data-chat]');

  // =============================================================== PLAYBACK
  var players = {};       // voiceId -> { audio, rate, raf }
  var current = null;     // voiceId currently playing
  var RATES = [1, 1.5, 2];

  function voiceEl(id) {
    return document.querySelector('[data-voice][data-voice-id="' + id + '"]');
  }

  function fmt(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }

  function durationOf(id, audio) {
    var el = voiceEl(id);
    var ms = el ? parseInt(el.getAttribute('data-duration-ms') || '0', 10) : 0;
    if (ms > 0) return ms / 1000;
    return audio && isFinite(audio.duration) ? audio.duration : 0;
  }

  function render(id) {
    var p = players[id];
    var el = voiceEl(id);
    if (!el || !p) return;
    var total = durationOf(id, p.audio);
    var pos = p.audio.currentTime || 0;
    var frac = total ? Math.min(1, pos / total) : 0;
    var bars = el.querySelectorAll('[data-voice-wave] span');
    var upto = Math.round(frac * bars.length);
    for (var i = 0; i < bars.length; i++) bars[i].classList.toggle('is-played', i < upto);
    var playing = !p.audio.paused && !p.audio.ended;
    el.classList.toggle('is-playing', playing);
    var time = el.querySelector('[data-voice-time]');
    if (time) time.textContent = (playing || pos > 0) ? fmt(pos) : fmt(total);
    var wave = el.querySelector('[data-voice-wave]');
    if (wave) {
      wave.setAttribute('aria-valuenow', String(Math.round(pos)));
      wave.setAttribute('aria-valuetext', fmt(pos) + ' of ' + fmt(total));
    }
    var btn = el.querySelector('[data-voice-play]');
    if (btn) btn.setAttribute('aria-label', (playing ? 'Pause' : 'Play') + ' voice note, ' + fmt(total));
    var speed = el.querySelector('[data-voice-speed]');
    if (speed) {
      speed.textContent = String(p.rate).replace('.5', '.5') + '×';
      speed.setAttribute('aria-label', 'Playback speed ' + p.rate + ' times');
    }
  }

  function loop(id) {
    var p = players[id];
    if (!p) return;
    render(id);
    if (!p.audio.paused && !p.audio.ended) p.raf = requestAnimationFrame(function () { loop(id); });
  }

  function markPlayed(id) {
    var el = voiceEl(id);
    if (!el || el.hasAttribute('data-own') || !el.classList.contains('is-unplayed')) return;
    el.classList.remove('is-unplayed');
    var dot = el.querySelector('.msg-voice-dot');
    if (dot) dot.remove();
    var url = el.getAttribute('data-played-url');
    var token = document.querySelector('[name=csrfmiddlewaretoken]');
    if (url) {
      fetch(url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': token ? token.value : '' },
      }).catch(function () {});
    }
  }

  function player(id) {
    if (players[id]) return players[id];
    var el = voiceEl(id);
    var audio = new Audio();
    audio.preload = 'metadata';
    audio.src = el.getAttribute('data-src');
    var p = { audio: audio, rate: 1, raf: null };
    audio.addEventListener('play', function () { loop(id); });
    audio.addEventListener('pause', function () { render(id); });
    audio.addEventListener('ended', function () {
      audio.currentTime = 0;
      render(id);
      if (current === id) current = null;
    });
    audio.addEventListener('error', function () {
      var host = voiceEl(id);
      if (host) {
        host.classList.add('has-error');
        // e.g. a Chromium build without the AAC decoder: offer the file instead.
        var time = host.querySelector('[data-voice-time]');
        var dl = host.getAttribute('data-download');
        if (time && dl && !host.querySelector('.msg-voice-download')) {
          var a = document.createElement('a');
          a.className = 'msg-voice-download';
          a.href = dl;
          a.textContent = 'Download';
          time.insertAdjacentElement('afterend', a);
        }
      }
      var noAac = !audio.canPlayType('audio/mp4; codecs="mp4a.40.2"');
      if (root && root.chatToast) {
        root.chatToast(noAac
          ? 'This browser cannot play AAC audio. Use Download, or open the chat in Chrome, Edge, Firefox or Safari.'
          : 'This voice note could not be played.', true);
      }
    });
    players[id] = p;
    return p;
  }

  function toggle(id) {
    var p = player(id);
    if (!p.audio.paused) { p.audio.pause(); return; }
    if (current && current !== id && players[current]) players[current].audio.pause();
    current = id;
    p.audio.playbackRate = p.rate;
    var playing = p.audio.play();
    if (playing && playing.catch) playing.catch(function () {});
    markPlayed(id);
  }

  function seek(id, frac) {
    var p = player(id);
    var total = durationOf(id, p.audio);
    if (!total) return;
    var to = Math.max(0, Math.min(total - 0.05, frac * total));
    try { p.audio.currentTime = to; } catch (e) { /* not seekable yet */ }
    render(id);
  }

  document.addEventListener('click', function (e) {
    var host = e.target.closest('[data-voice]');
    if (!host) return;
    var id = host.getAttribute('data-voice-id');
    if (e.target.closest('[data-voice-play]')) { toggle(id); return; }
    if (e.target.closest('[data-voice-speed]')) {
      var p = player(id);
      p.rate = RATES[(RATES.indexOf(p.rate) + 1) % RATES.length];
      p.audio.playbackRate = p.rate;
      render(id);
      return;
    }
    var wave = e.target.closest('[data-voice-wave]');
    if (wave) {
      var r = wave.getBoundingClientRect();
      seek(id, (e.clientX - r.left) / r.width);
      if (player(id).audio.paused) toggle(id);
    }
  });

  document.addEventListener('keydown', function (e) {
    var wave = e.target.closest && e.target.closest('[data-voice-wave]');
    if (!wave) return;
    var host = wave.closest('[data-voice]');
    var id = host.getAttribute('data-voice-id');
    var p = player(id);
    var total = durationOf(id, p.audio);
    if (!total) return;
    var pos = p.audio.currentTime || 0;
    if (e.key === 'ArrowRight') pos += 5;
    else if (e.key === 'ArrowLeft') pos -= 5;
    else if (e.key === 'Home') pos = 0;
    else if (e.key === 'End') pos = total - 0.1;
    else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(id); return; }
    else return;
    e.preventDefault();
    seek(id, pos / total);
  });

  // Live updates replace message HTML: re-render state for that note.
  document.addEventListener('chat:message-replaced', function (e) {
    var el = e.detail && e.detail.el;
    var v = el && el.querySelector('[data-voice]');
    if (v && players[v.getAttribute('data-voice-id')]) render(v.getAttribute('data-voice-id'));
  });

  // ============================================================== RECORDING
  if (!root) return;
  var form = root.querySelector('[data-reply-form]');
  var inner = root.querySelector('.msg-reply-inner');
  var mic = root.querySelector('[data-voice-mic]');
  var panel = root.querySelector('[data-recorder]');
  if (!form || !mic || !panel) return;
  var timeEl = panel.querySelector('[data-rec-time]');
  var hintEl = panel.querySelector('[data-rec-hint]');
  var meterBars = panel.querySelectorAll('[data-rec-meter] span');
  var sendBtn = panel.querySelector('[data-rec-send]');
  var cancelBtn = panel.querySelector('[data-rec-cancel]');
  var replyField = root.querySelector('[data-reply-to-field]');
  var replyRef = root.querySelector('[data-reply-reference]');
  var thread = root.querySelector('[data-thread]');
  var uploadUrl = root.dataset.voiceUrl;
  var maxSeconds = parseInt(root.dataset.voiceMax || '900', 10) || 900;

  var state = 'idle';     // idle | starting | recording | stopping | sending
  var mode = null;        // 'hold' | 'click'
  var stream = null, recorder = null, chunks = [], mime = '';
  var startedAt = 0, tick = null, audioCtx = null, analyser = null, raf = null;
  var pressAt = 0, pressX = 0, pressing = false, cancelRequested = false;

  function say(text, isError) {
    if (root.chatToast) root.chatToast(text, isError);
    else if (isError) window.alert(text);
  }

  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
    var list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=mp4a.40.2',
                'audio/mp4', 'audio/ogg;codecs=opus'];
    for (var i = 0; i < list.length; i++) {
      if (MediaRecorder.isTypeSupported(list[i])) return list[i];
    }
    return '';
  }

  function permissionMessage(err) {
    var name = err && err.name;
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      return 'Microphone access is blocked. Allow the microphone for this site (the icon in the address bar), then try again.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return 'No microphone was found. Connect one and try again.';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return 'The microphone is in use by another app. Close it and try again.';
    }
    if (name === 'SecurityError') return 'Recording needs a secure (https) connection.';
    return 'Could not start recording' + (err && err.message ? ': ' + err.message : '.');
  }

  function showPanel(on) {
    panel.hidden = !on;
    if (inner) inner.classList.toggle('is-recording', on);
  }

  function setHint() {
    if (!hintEl) return;
    hintEl.textContent = mode === 'hold'
      ? 'Release to send · slide left to cancel'
      : 'Recording… press Send when done · Esc to cancel';
  }

  function meterLoop() {
    if (!analyser) return;
    var data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    var sum = 0;
    for (var i = 0; i < data.length; i++) { var v = (data[i] - 128) / 128; sum += v * v; }
    var level = Math.min(1, Math.sqrt(sum / data.length) * 4);
    // Shift bars left, newest on the right.
    for (var j = 0; j < meterBars.length - 1; j++) {
      meterBars[j].style.height = meterBars[j + 1].style.height || '12%';
    }
    if (meterBars.length) meterBars[meterBars.length - 1].style.height = Math.max(12, Math.round(level * 100)) + '%';
    raf = requestAnimationFrame(meterLoop);
  }

  function cleanup() {
    clearInterval(tick); tick = null;
    if (raf) cancelAnimationFrame(raf); raf = null;
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} }
    audioCtx = null; analyser = null;
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null; recorder = null;
    meterBars.forEach(function (b) { b.style.height = ''; });
    showPanel(false);
    mic.classList.remove('is-recording');
    state = 'idle'; mode = null; pressing = false;
  }

  function elapsed() { return startedAt ? (Date.now() - startedAt) / 1000 : 0; }

  function start(startMode) {
    if (state !== 'idle') return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      say('Voice notes need a browser that can record audio (current Chrome, Edge, Firefox or Safari 14.1+).', true);
      return;
    }
    state = 'starting';
    mode = startMode;
    cancelRequested = false;
    navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    }).then(function (s) {
      if (cancelRequested || state !== 'starting') { s.getTracks().forEach(function (t) { t.stop(); }); state = 'idle'; return; }
      stream = s;
      mime = pickMime();
      try {
        recorder = mime ? new MediaRecorder(s, { mimeType: mime, audioBitsPerSecond: 32000 })
                        : new MediaRecorder(s);
      } catch (e) {
        recorder = new MediaRecorder(s);
      }
      chunks = [];
      recorder.addEventListener('dataavailable', function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); });
      recorder.start(250);
      startedAt = Date.now();
      state = 'recording';
      // A quick tap that ended while permission was being granted = click mode.
      if (mode === 'hold' && !pressing) mode = 'click';
      setHint();
      showPanel(true);
      mic.classList.add('is-recording');
      if (timeEl) timeEl.textContent = '0:00';
      tick = setInterval(function () {
        var s2 = elapsed();
        if (timeEl) timeEl.textContent = fmt(Math.floor(s2));
        if (s2 >= maxSeconds) { say('Maximum length reached — sending.'); stop(true); }
      }, 250);
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        audioCtx = new Ctx();
        var src = audioCtx.createMediaStreamSource(s);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        src.connect(analyser);
        meterLoop();
      } catch (e) { /* meter is optional */ }
      if (sendBtn) sendBtn.focus({ preventScroll: true });
    }).catch(function (err) {
      state = 'idle'; mode = null;
      say(permissionMessage(err), true);
    });
  }

  function stop(send) {
    if (state === 'starting') { cancelRequested = true; state = 'idle'; showPanel(false); return; }
    if (state !== 'recording' || !recorder) return;
    state = 'stopping';
    var durationMs = Date.now() - startedAt;
    var rec = recorder;
    rec.addEventListener('stop', function () {
      var type = rec.mimeType || mime || 'audio/webm';
      var blob = new Blob(chunks, { type: type });
      chunks = [];
      cleanup();
      if (!send) return;
      if (durationMs < 700 || blob.size < 200) { say('Too short — hold the mic a little longer.'); return; }
      upload(blob, durationMs, type);
    }, { once: true });
    try { rec.stop(); } catch (e) { cleanup(); }
  }

  function upload(blob, durationMs, type) {
    state = 'sending';
    var ext = /mp4|m4a|aac/.test(type) ? 'm4a' : /ogg/.test(type) ? 'ogg' : 'webm';
    var fd = new FormData();
    fd.append('audio', blob, 'voice-note.' + ext);
    fd.append('duration_ms', String(Math.round(durationMs)));
    if (replyField && replyField.value) fd.append('reply_to', replyField.value);
    var token = form.querySelector('[name=csrfmiddlewaretoken]');
    fd.append('csrfmiddlewaretoken', token ? token.value : '');

    var pending = document.createElement('article');
    pending.className = 'msg-bubble is-mine is-pending';
    pending.setAttribute('data-pending', '');
    pending.innerHTML = '<div class="msg-bubble-col"><div class="msg-bubble-body"><div class="msg-voice-pending">' +
      '<span class="msg-spinner" aria-hidden="true"></span> Sending voice note (' + fmt(durationMs / 1000) + ')…</div></div></div>';
    if (thread) {
      var empty = thread.querySelector('.msg-empty-thread');
      if (empty) empty.remove();
      thread.appendChild(pending);
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
    }

    fetch(uploadUrl, {
      method: 'POST', body: fd, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Upload failed (' + r.status + ').' }; });
    }).then(function (j) {
      pending.remove();
      if (!j.ok) { say(j.error || 'Could not send the voice note.', true); return; }
      if (root.chatAppendMessage) root.chatAppendMessage(j.html, j.message_id);
      if (replyField) replyField.value = '';
      if (replyRef) replyRef.hidden = true;
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
      document.dispatchEvent(new CustomEvent('chat:sent', { detail: j }));
    }).catch(function () {
      pending.remove();
      say('Network error — voice note not sent.', true);
    }).finally(function () { state = 'idle'; });
  }

  // ---- mic button: hold to record, or click to start ----
  mic.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || state !== 'idle') return;
    e.preventDefault();
    pressing = true;
    pressAt = Date.now();
    pressX = e.clientX;
    try { mic.setPointerCapture(e.pointerId); } catch (err) {}
    start('hold');
  });
  mic.addEventListener('pointermove', function (e) {
    if (!pressing || mode !== 'hold') return;
    var dx = e.clientX - pressX;
    panel.style.setProperty('--slide', Math.min(0, dx) + 'px');
    if (dx < -90) {
      pressing = false;
      panel.style.removeProperty('--slide');
      stop(false);
      say('Recording cancelled');
    }
  });
  function release() {
    if (!pressing) return;
    pressing = false;
    panel.style.removeProperty('--slide');
    var held = Date.now() - pressAt;
    if (held < 350) {
      mode = 'click';
      setHint();
      return;
    }
    if (mode === 'hold') stop(true);
  }
  mic.addEventListener('pointerup', release);
  mic.addEventListener('pointercancel', function () {
    if (pressing && mode === 'hold') { pressing = false; stop(false); }
  });
  // Keyboard (Enter / Space) produces a click with detail 0.
  mic.addEventListener('click', function (e) {
    if (e.detail === 0 && state === 'idle') start('click');
  });
  mic.addEventListener('contextmenu', function (e) { e.preventDefault(); });

  document.addEventListener('voice:start', function () { start('click'); });
  if (sendBtn) sendBtn.addEventListener('click', function () { stop(true); });
  if (cancelBtn) cancelBtn.addEventListener('click', function () { stop(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && (state === 'recording' || state === 'starting')) {
      e.preventDefault();
      stop(false);
      say('Recording cancelled');
      mic.focus({ preventScroll: true });
    }
  });
  window.addEventListener('beforeunload', function () { if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); });
})();
