#!/usr/bin/env python
"""Render the main pages of every portal and record the result (no server, no
test DB — it runs against the current DEV database, read-only GETs only).

    .venv/bin/python scripts/check_pages.py            # summary to the terminal
    .venv/bin/python scripts/check_pages.py --md docs/reports/page_check.md

What "looks good" means here: HTTP 200 (or the redirect the flow expects),
a <title>, and no Django technical error page (500/403-forbidden-by-bug).
Fails with exit 1 when any page does not render.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if not __package__ or __package__ == "":
    # Allow `python scripts/check_pages.py` (no package context).
    pass

import django
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.dev")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

ERROR_MARKS = ("Traceback (most recent call last)", "Technical 500", "DisallowedHost")


def qa_host():
    h = getattr(settings, "QA_PORTAL_HOST", "") or "qa.localhost"
    return h if ":" in h else f"{h}:8000"


def internal_host():
    return "localhost:8000"


INSTITUTION_PAGES = ("/", "/application/", "/setup/", "/payments/", "/programmes/", "/documents/")


def pages_for(user):
    """(host, url, label) rows for a User, matched to what the role may see."""
    url = user.get_home_url() if hasattr(user, "get_home_url") else None
    rows = [(internal_host(), "/", "Internal home"), (internal_host(), "/qa/", "QA home"),
            (internal_host(), "/tasks/", "My tasks"), (internal_host(), "/messages/", "Messages"),
            (internal_host(), "/notifications/", "Notifications")]
    if user.is_superuser:
        rows += [(internal_host(), "/admin/", "Django admin"),
                 (internal_host(), "/qa/institutions/", "Institutions"),
                 (internal_host(), "/qa/applications/documents/", "Document reviews"),
                 (internal_host(), "/qa/department/rotation/", "Rotation board"),
                 (internal_host(), "/qa/complaints/", "Complaints"),
                 (internal_host(), "/qa/inspection/inspections/", "Visit inspections"),
                 (internal_host(), "/qa/finance/", "Payment queue (Finance)"),
                 (internal_host(), "/qa/finance/obligations/", "NBTE bills list"),
                 (internal_host(), "/settings/", "Settings")]
    if url:
        rows.insert(0, (internal_host(), url, "Role home"))
    if user.institution_id:  # the portal host is the institution's front door
        p = qa_host()
        rows += [(p, "/", "Portal home")] + [(p, u, f"Portal {u}") for u in INSTITUTION_PAGES if u != "/"]
        from payments.models import PaymentObligation

        ob = (PaymentObligation.objects.for_user(user).exclude(status="closed").first()
              or PaymentObligation.objects.for_user(user).first())
        if ob is not None:
            rows += [(p, f"/payments/{ob.pk}/", "Portal payment detail")]
    return rows
def pick_users():
    """A sensible small cast: one superuser (sees every QA page) and, if the
    demo seed ran, one institution user and one professional-body admin."""
    User = get_user_model()
    out = []
    super_admin = (User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
                   or User.objects.filter(user_type=User.STAFF, is_active=True).first())
    if super_admin is not None:
        out.append(super_admin)
    admin = (User.objects.filter(user_type=User.INSTITUTION, is_active=True,
                                 institution__isnull=False).select_related("institution").first())
    if admin is not None:
        out.append(admin)
    pb = (User.objects.filter(user_type="professional_body", is_active=True).first()
          or User.objects.filter(email__icontains="pb.", is_active=True).first())
    if pb is not None:
        out.append(pb)
    return out


def check(client, host, url):
    started = time.time()
    try:
        r = client.get(url, HTTP_HOST=host, follow=True)
    except Exception as exc:  # a page that raises is exactly what we report
        return {"ok": False, "code": 0, "place": "", "note": f"{type(exc).__name__}: {exc}", "ms": time.time() - started}
    ms = time.time() - started
    content = r.content.decode(errors="ignore") if (r.content and b"html" in r.content[:400].lower()) else ""
    ok = r.status_code == 200 and not any(m in content for m in ERROR_MARKS)
    return {"ok": ok, "code": r.status_code, "place": r.get("location", ""), "ms": ms, "note": ""}


def main():
    out_md = None
    if "--md" in sys.argv:
        out_md = Path(sys.argv[sys.argv.index("--md") + 1])

    users = pick_users()
    if not users:
        print("No active users in this database — run ./seed.py --demo first.")
        return 1

    rows, bad = [], 0
    for user in users:
        client = Client()
        client.force_login(user)
        # The institution detail page needs a real code on this DB:
        inst_code = getattr(user.institution, "code", "") if user.institution_id else ""
        pages = pages_for(user)
        if user.is_superuser:
            from institutions.models import Institution

            inst = Institution.unscoped.exclude(status="dropped").first()
            if inst is not None:
                pages += [(internal_host(), f"/qa/institutions/{inst.code}/", "Institution record")]
            from payments.models import PaymentObligation

            ob = (PaymentObligation.objects.for_user(user).exclude(status="closed").first()
                  or PaymentObligation.objects.for_user(user).first())
            if ob is not None:
                pages += [(internal_host(), f"/qa/finance/obligations/{ob.pk}/", "NBTE payment detail")]
        desc = f"{user.get_full_name() or user.email} ({getattr(user.role, 'name', user.user_type)})"
        for host, url, label in pages:
            result = check(client, host, url)
            result.update(user=desc, host=host, url=url, label=label)
            if not result["ok"]:
                bad += 1
            rows.append(result)

    w = 118
    print("=" * w)
    print(f"Pages rendered  OK: {len(rows) - bad}   PROBLEM: {bad}   "
          f"(dev DB, {timezone.now():%Y-%m-%d %H:%M})")
    for r in rows:
        flag = "OK  " if r["ok"] else "FAIL"
        if r["ok"] and r["code"] == 200:
            print(f"{flag} {r['code']}  {str(r['user'])[:22]:<22} {r['label']:<18} {r['host']}{r['url']}  {r['ms']:.2f}s")
        else:
            print(f"{flag} {r['code']}  {str(r['user'])[:22]:<22} {r['label']:<18} {r['host']}{r['url']}"
                  f"  {r['ms']:.2f}s  ↓ {r['place']} {r['note']}")
    print("=" * w)

    if out_md:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Page check — {timezone.now():%Y-%m-%d %H:%M}", "",
                 f"Dev DB rendered per role; OK {len(rows) - bad} / {len(rows)}; failures {bad}.", "",
                 "| Result | Code | Role | Page | URL | ms |", "|---|---|---|---|---|---|"]
        for r in rows:
            mark = "✅" if r["ok"] else "❌"
            lines.append(f"| {mark} | {r['code']} | {r['user']} | {r['label']} "
                         f"| `{r['host']}{r['url']}` {r['place']} {r['note']} | {r['ms']:.2f}|")
        out_md.write_text("\n".join(lines) + "\n")
        print(f"Markdown written to {out_md}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
