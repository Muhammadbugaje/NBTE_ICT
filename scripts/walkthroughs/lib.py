"""Shared Playwright helpers for the walkthrough scripts.

Settings (environment variables):
  WALK_OUT       folder for screenshots (default: ./walkthrough_shots)
  WALK_INTERNAL  internal system URL (default http://localhost:8000)
  WALK_PORTAL    portal URL (default http://qa.localhost:8000)
  WALK_PASSWORD  the password every demo account was given (default Walk-Through#2026)
"""
import os, re, sys, time, json
from playwright.sync_api import sync_playwright, expect
S = os.path.abspath(os.environ.get("WALK_OUT", "walkthrough_shots"))
os.makedirs(S, exist_ok=True)
SHOTS = S
INT = os.environ.get("WALK_INTERNAL", "http://localhost:8000")
POR = os.environ.get("WALK_PORTAL", "http://qa.localhost:8000")
PW = os.environ.get("WALK_PASSWORD", "Walk-Through#2026")
pw = sync_playwright().start()
browser = pw.chromium.launch(args=["--host-resolver-rules=MAP qa.localhost 127.0.0.1"])
def ctx(mobile=False):
    if mobile:
        return browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    return browser.new_context(viewport={"width": 1440, "height": 900})
errors = []
def page_for(c):
    p = c.new_page()
    p.on("console", lambda m: errors.append(("console", m.type, m.text)) if m.type == "error" else None)
    p.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
    p.on("response", lambda r: errors.append(("http", r.status, r.url)) if r.status >= 500 else None)
    return p
def login_internal(p, email):
    p.goto(INT + "/accounts/login/")
    p.fill("#id_username", email); p.fill("#id_password", PW)
    p.locator("form button[type=submit]").first.click(); p.wait_for_load_state("networkidle")
def login_portal(p, email, pw=PW):
    p.goto(POR + "/login/")
    p.fill("#id_username", email); p.fill("#id_password", pw)
    p.click("button.auth-submit"); p.wait_for_load_state("networkidle")
def shot(p, name, full=True):
    p.wait_for_timeout(400)
    p.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); "
               "document.documentElement.classList.remove('reveal-ready'); }")  # full-page shots: no scroll reveal
    if full:
        p.add_style_tag(content=".skip-link,.skip-to-content,[href='#qa-main'].sr-only-focusable{display:none!important} .qa-topbar,.topbar,header{position:relative!important;top:auto!important} .qa-tabbar{position:static!important}")
    p.wait_for_timeout(200)
    p.screenshot(path=f"{SHOTS}/{name}.png", full_page=full)
def settle(p):
    p.wait_for_load_state("networkidle"); p.wait_for_timeout(300)
def confirm_dialog(p, text):
    p.wait_for_timeout(400)
    p.locator(f"button:has-text('{text}'):visible").last.click(); settle(p); p.wait_for_timeout(600)
