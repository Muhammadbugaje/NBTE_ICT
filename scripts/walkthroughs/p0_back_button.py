"""Phase 0 (N2, V6, T-UX-1) Playwright check: Back after hx-push-url shows a
full, styled page (sidebar present, stylesheet applied), on the internal
system and on the QA portal.

    WALK_INTERNAL=http://localhost:8000 WALK_PORTAL=http://qa.localhost:8000 \
    WALK_PASSWORD=<demo password> python scripts/walkthroughs/p0_back_button.py

Demo accounts only (qa.es@demo.nbte.local, admin@demo-poly.local by default;
override with P0_STAFF / P0_INSTITUTION). Exits non-zero on a failure.
"""
import os
import sys

from playwright.sync_api import sync_playwright

INT = os.environ.get("WALK_INTERNAL", "http://localhost:8000")
POR = os.environ.get("WALK_PORTAL", "http://qa.localhost:8000")
PW = os.environ.get("WALK_PASSWORD", "")
STAFF = os.environ.get("P0_STAFF", "qa.es@demo.nbte.local")
INST = os.environ.get("P0_INSTITUTION", "admin@demo-poly.local")
STAFF_START = os.environ.get("P0_STAFF_START", "/qa/institutions/")
PORTAL_START = os.environ.get("P0_PORTAL_START", "/qa-requests/")

STYLED_JS = """(shell) => {
  const el = document.querySelector(shell);
  const css = Array.from(document.styleSheets).some(s => { try { return s.cssRules.length > 20; } catch (e) { return true; } });
  return {shell: !!el, css: css, html: document.documentElement.outerHTML.length,
          title: document.title};
}"""


def login(page, base, path, email):
    page.goto(base + path)
    page.fill("input[name=username]", email)
    page.fill("input[name=password]", PW)
    page.locator("form:has(input[name=password]) [type=submit]").first.click()
    page.wait_for_load_state("networkidle")


def check(page, base, start, shell, label):
    page.goto(base + start)
    page.wait_for_load_state("networkidle")
    visited = [page.url]
    for _ in range(3):
        links = page.locator("a[hx-push-url='true'][hx-get]:visible")
        target = None
        for i in range(links.count()):
            href = links.nth(i).get_attribute("hx-get")
            if href and (base + href if href.startswith("/") else href) not in visited:
                target = links.nth(i)
                break
        if target is None:
            break
        target.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        visited.append(page.url)
    problems = []
    if len(visited) < 3:
        problems.append(f"{label}: only {len(visited) - 1} HTMX navigations found on {start}")
    for _ in range(2):
        page.go_back()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)
        state = page.evaluate(STYLED_JS, shell)
        if not (state["shell"] and state["css"]):
            problems.append(f"{label}: Back to {page.url} is not a styled full page: {state}")
    # Reload of a pushed URL (cache miss / new tab) is a full page too.
    page.reload()
    page.wait_for_load_state("networkidle")
    state = page.evaluate(STYLED_JS, shell)
    if not (state["shell"] and state["css"]):
        problems.append(f"{label}: reload of {page.url} is not a styled full page")
    print(f"{label}: visited {len(visited)} pages, back x2 + reload -> {'OK' if not problems else 'FAIL'}")
    return problems


def main():
    if not PW:
        sys.exit("Set WALK_PASSWORD to the demo password.")
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--host-resolver-rules=MAP qa.localhost 127.0.0.1"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        login(page, INT, "/accounts/login/", STAFF)
        problems += check(page, INT, STAFF_START, ".app-shell", "internal")
        ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page2 = ctx2.new_page()
        login(page2, POR, "/login/", INST)
        problems += check(page2, POR, PORTAL_START, ".qa-footer, .qa-brand", "portal")
        # A fragment URL opened directly lands on its full page (shot 58b).
        page2.goto(POR + "/application/programmes/")
        page2.wait_for_load_state("networkidle")
        if not page2.evaluate(STYLED_JS, ".qa-brand")["shell"]:
            problems.append("portal: /application/programmes/ opened directly is not a full page")
        print("portal: /application/programmes/ ->", page2.url)
        browser.close()
    for line in problems:
        print("PROBLEM:", line)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
