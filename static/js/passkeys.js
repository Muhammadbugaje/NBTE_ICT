/* ==========================================================================
   NBTE — Passkeys (Phase 9)
   Sign in with fingerprint, face or device PIN (WebAuthn), and add a passkey
   from the profile / account page. Safe to load more than once (the QA
   portal swaps pages in with htmx): listeners are attached once, on document.

     <button data-passkey-signin data-begin-url data-finish-url data-next hidden>
     <button data-passkey-add data-begin-url data-finish-url hidden>
     <p data-passkey-status> ... </p>   (nearest one is used for messages)

   Buttons stay hidden when the browser has no window.PublicKeyCredential.
   ========================================================================== */
(function () {
  "use strict";
  if (window.__nbtePasskeys) { window.__nbtePasskeys.reveal(); return; }

  var supported = typeof window.PublicKeyCredential === "function" &&
    !!(navigator.credentials && navigator.credentials.create);

  // ---- base64url <-> ArrayBuffer ------------------------------------------
  function b64urlToBuf(value) {
    var s = String(value).replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }
  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf), bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrfToken() {
    var input = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (input && input.value) return input.value;
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(),
                 "X-Requested-With": "fetch" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.ok === false) {
          throw new Error(data.error || "Something went wrong (" + r.status + "). Please try again.");
        }
        return data;
      });
    });
  }

  function creationOptions(o) {
    var pk = Object.assign({}, o);
    pk.challenge = b64urlToBuf(o.challenge);
    pk.user = Object.assign({}, o.user, { id: b64urlToBuf(o.user.id) });
    pk.excludeCredentials = (o.excludeCredentials || []).map(function (c) {
      return Object.assign({}, c, { id: b64urlToBuf(c.id) });
    });
    return pk;
  }
  function requestOptions(o) {
    var pk = Object.assign({}, o);
    pk.challenge = b64urlToBuf(o.challenge);
    pk.allowCredentials = (o.allowCredentials || []).map(function (c) {
      return Object.assign({}, c, { id: b64urlToBuf(c.id) });
    });
    return pk;
  }

  function credentialJSON(cred) {
    var r = cred.response, out = {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      authenticatorAttachment: cred.authenticatorAttachment || null,
      response: { clientDataJSON: bufToB64url(r.clientDataJSON) }
    };
    if (r.attestationObject) {
      out.response.attestationObject = bufToB64url(r.attestationObject);
      out.response.transports = r.getTransports ? r.getTransports() : [];
    }
    if (r.authenticatorData) {
      out.response.authenticatorData = bufToB64url(r.authenticatorData);
      out.response.signature = bufToB64url(r.signature);
      out.response.userHandle = r.userHandle ? bufToB64url(r.userHandle) : null;
    }
    return out;
  }

  function statusFor(button) {
    var scope = button.closest("[data-passkey-scope]") || button.parentElement;
    return scope ? scope.querySelector("[data-passkey-status]") : null;
  }
  function say(button, text, tone) {
    var el = statusFor(button);
    if (!el) { if (text && tone === "error") window.alert(text); return; }
    el.textContent = text || "";
    el.hidden = !text;
    el.setAttribute("data-tone", tone || "info");
  }
  function friendly(err) {
    if (err && err.name === "NotAllowedError") return "Passkey request was cancelled or timed out.";
    if (err && err.name === "InvalidStateError") return "This device already has a passkey for your account.";
    return (err && err.message) || "Passkey failed. Please try again.";
  }

  function busy(button, on) {
    button.disabled = on;
    button.setAttribute("aria-busy", on ? "true" : "false");
  }

  function signIn(button) {
    busy(button, true);
    say(button, "Waiting for your device…", "info");
    post(button.dataset.beginUrl).then(function (options) {
      return navigator.credentials.get({ publicKey: requestOptions(options) });
    }).then(function (cred) {
      return post(button.dataset.finishUrl, { credential: credentialJSON(cred), next: button.dataset.next || "" });
    }).then(function (data) {
      say(button, "Signed in. Opening your workspace…", "success");
      window.location.assign(data.redirect || "/");
    }).catch(function (err) {
      say(button, friendly(err), "error");
      busy(button, false);
    });
  }

  function add(button) {
    busy(button, true);
    say(button, "Follow the prompt on your device…", "info");
    var nameInput = button.dataset.nameInput ? document.querySelector(button.dataset.nameInput) : null;
    post(button.dataset.beginUrl).then(function (options) {
      return navigator.credentials.create({ publicKey: creationOptions(options) });
    }).then(function (cred) {
      return post(button.dataset.finishUrl, { credential: credentialJSON(cred),
                                              name: nameInput ? nameInput.value : "" });
    }).then(function () {
      say(button, "Passkey added.", "success");
      window.location.reload();
    }).catch(function (err) {
      say(button, friendly(err), "error");
      busy(button, false);
    });
  }

  function reveal() {
    var nodes = document.querySelectorAll("[data-passkey-signin], [data-passkey-add], [data-passkey-only]");
    Array.prototype.forEach.call(nodes, function (el) { el.hidden = !supported; });
    var notes = document.querySelectorAll("[data-passkey-unsupported]");
    Array.prototype.forEach.call(notes, function (el) { el.hidden = supported; });
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest ? event.target.closest("[data-passkey-signin], [data-passkey-add]") : null;
    if (!target || !supported) return;
    event.preventDefault();
    if (target.hasAttribute("data-passkey-signin")) signIn(target); else add(target);
  });

  window.__nbtePasskeys = { reveal: reveal, supported: supported };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
  else reveal();
  document.addEventListener("htmx:afterSettle", reveal);
})();
