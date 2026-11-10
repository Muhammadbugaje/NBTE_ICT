"""Manual-walk helper: a stand-in browser for driving the REAL dev server's
pages exactly like a human (GET -> parse forms -> POST with CSRF -> follow).
No service functions, no model writes outside what the page forms submit."""
import re
import sys
from django.test import Client

BASE = {"internal": "localhost:8000", "portal": "qa.localhost:8000"}
LOG = open("docs/reports/manual_walk_log.txt", "a")

_sessions = {}


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line)
    LOG.write(line + "\n")
    LOG.flush()


def attr(html, name):
    m = re.search(r'<[^>]+?%s="([^"]*)"' % name, html)
    return m.group(1) if m else None


def forms(html):
    """[(action, method, {hidden: value}, {button_name: value})] — the real
    buttons on the page a user could click."""
    out = []
    for fm in re.finditer(r"<form[^>]*>(.*?)</form>", html, re.S):
        block = fm.group(0)
        action = attr(block, "action") or ""
        method = "post" if re.search(r'method="post"', block) else "get"
        hiddens = {m.group(1): m.group(2) for m in
                   re.finditer(r'<input[^>]+type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"', block)}
        for m in re.finditer(r'<input[^>]+type="hidden"[^>]*value="([^"]*)"[^>]*name="([^"]*)"', block):
            hiddens.setdefault(m.group(2), m.group(1))
        buttons = {}
        for m in re.finditer(r'<button[^>]*name="([^"]*)"[^>]*value="([^"]*)"[^>]*>(.*?)</button>', block, re.S):
            buttons[m.group(1)] = m.group(2)
        out.append({"action": action, "method": method, "hidden": hiddens, "buttons": buttons})
    return out


def session(who):
    if who not in _sessions:
        _sessions[who] = Client()
    return _sessions[who]


def get(page):
    host, _, url = _split(page)
    r = session(host_id(host)).get(_u(url), HTTP_HOST=host)
    _log_resp(page, r)
    return r


def post(page, data, follow=True):
    host, _, url = _split(page)
    r = session(host_id(host)).post(_u(url), data, HTTP_HOST=host, follow=follow)
    _log_resp(page + " POST", r)
    return r


def host_id(host):
    return "internal" if host.startswith("localhost") else "portal"


def _split(page):
    host, url = page.split(":", 1) if page.count(":") and page.split(":")[0] in ("internal", "portal") else (
        "internal", page)
    return BASE[host if page.count(":") and page.split(":")[0] in BASE else "internal"], None, url


def _u(url):
    return url if url.startswith("/") else "/" + url


def _log_resp(page, r):
    title = ""
    if hasattr(r, "content"):
        m = re.search(r"<title>(.*?)</title>", r.content.decode(errors="replace"), re.S)
        title = m.group(1).strip() if m else ""
    log(f"  [{page}] -> {r.status_code}" + (f" | {title}" if title else "") +
        (f" | -> {r['Location']}" if r.status_code in (301, 302) and not getattr(r, 'is_redirect', False) else ""))


def login(who, host, email, password):
    c = session(host_id(host))
    r = c.get(_u("/accounts/login/"), HTTP_HOST=host)
    csrf = c.cookies.get("csrftoken", None)
    token = csrf.value if csrf else attr(r.content.decode(), "csrfmiddlewaretoken")
    r2 = c.post(_u("/accounts/login/"), {"username": email, "password": password,
                                         "csrfmiddlewaretoken": token}, HTTP_HOST=host, follow=True)
    title = re.search(r"<title>(.*?)</title>", r2.content.decode(errors="replace"), re.S)
    ok = r2.status_code == 200
    log(f"LOGIN {who} ({email} @ {host}) -> {r2.status_code} | {title.group(1).strip() if title else ''}")
    assert ok, "login failed: " + who + " " + str(r2.status_code)
    return r2


def logout(who):
    c = session(host_id(BASE.get(who, who)))
    r = c.get(_u("/logout/"), HTTP_HOST=BASE.get(who, who), follow=True)
    _sessions.pop(host_id(BASE.get(who, who)), None)
    log(f"LOGOUT {who}")
    return r
