#!/usr/bin/env python3
"""Master seed script for NBTE Digital (v2.1) — the ONE command you run on a
new database (or any existing one) to fill in the basic things.

    ./seed.py                    # migrate + full reference seed (settings,
                                 #   roles, departments, LGA, fees, flows,
                                 #   notification templates, request chains,
                                 #   request types and memo routing …)
    ./seed.py --demo             # also create demo institutions/accounts and
                                 #   set them all to the password "NBTE-Demo#2026"
                                 #   (a list is written to demo-accounts.csv)
    ./seed.py --fresh            # delete db.sqlite3 first (CHANGES DATA)
    ./seed.py --only qa --skip demo
    ./seed.py --dry-run          # show what would change, change nothing

Every amount in the system is DYNAMIC: fees, honoraria rates, fines and cost
defaults are settings rows (`qa.fee.*`, `honoraria.rate.*`,
`qa.visit.fine_per_falsified_item`, rate tables) seeded with editable
defaults — NBTE Finance changes them on Settings, never in code. This script
only creates those rows; it never invents amounts.

Safe to run twice: the underlying `seed_nbte` is idempotent and never
overwrites an edited row. A backup of db.sqlite3 is taken automatically before
migration unless --no-backup.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANAGE = ROOT / "manage.py"

# ---------------------------------------------------------------- utilities

def py():
    """The interpreter that should run manage.py (prefer the project venv)."""
    if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:  # already in a venv
        return sys.executable
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def run(args, *, env=None):
    e = dict(os.environ)
    e.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.dev")
    e.setdefault("PYTHONUNBUFFERED", "1")
    if env:
        e.update(env)
    return subprocess.run([py(), str(MANAGE), *args], cwd=ROOT, env=e).returncode


def say(step, msg=""):
    print(f"\033[36m\033[1m== {step} ==\033[0m {msg}", flush=True)


def sqlite_file():
    """The development SQLite file, when the settings use one."""
    out = subprocess.run([py(), str(MANAGE), "shell", "-c",
                          "from django.db import connection; print(connection.settings_dict['ENGINE'], "
                          "connection.settings_dict.get('NAME'))"],
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode or "sqlite3" not in out.stdout:
        return None, ""
    name = (out.stdout.strip().splitlines()[-1].split(" ", 1)[1]).strip()
    return Path(name), name


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--demo", action="store_true", help="also seed the tagged demo data and its passwords")
    p.add_argument("--fresh", action="store_true", help="DELETE db.sqlite3 first ( irreversible )")
    p.add_argument("--no-backup", action="store_true", help="skip the automatic db.sqlite3 backup")
    p.add_argument("--skip-migrate", action="store_true", help="assume migrations are applied")
    p.add_argument("--only", default="", help="seed only these set names/groups (comma list)")
    p.add_argument("--skip", default="", help="leave out these set names/groups (comma list)")
    p.add_argument("--update-unedited", action="store_true", help="refresh seeded rows nobody edited")
    p.add_argument("--dry-run", action="store_true", help="migrate for real; roll the seed back")
    p.add_argument("--account-csv", default="demo-accounts.csv", help="where --demo writes the login list")
    p.add_argument("--i-understand-production", action="store_true", help="allow demo rows when DEBUG=False")
    args = p.parse_args()

    if args.fresh:
        db, name = sqlite_file()
        if db is not None and db.exists():
            say("FRESH", f"deleting {name}")
            db.unlink()

    code = run(["check"])
    if code:
        sys.exit(code)

    if not args.skip_migrate:
        db, name = sqlite_file() if not args.no_backup else (None, "")
        if db is not None and db.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = db.with_name(f"db.sqlite3.bak-seed-{stamp}")
            say("BACKUP", f"{name} → {target.name}")
            target.write_bytes(db.read_bytes())
        say("MIGRATE")
        code = run(["migrate"])
        if code:
            sys.exit(code)

    seed_args = []
    if args.only:
        seed_args += ["--only", args.only]
    if args.skip:
        seed_args += ["--skip", args.skip]
    if args.update_unedited:
        seed_args.append("--update-unedited")
    if args.demo:
        seed_args.append("--with-demo")
    if args.dry_run:
        seed_args.append("--dry-run")
    if args.i_understand_production:
        seed_args.append("--i-understand-production")
    say("SEED", f"seed_nbte {' '.join(seed_args) or '(all sets)'}")
    code = run(["seed_nbte", *seed_args])
    if code:
        sys.exit(code)

    say("CHECK", "admin groups and defaults")
    code = run(["seed_default_groups"]) or run(["seed_staff_reference"]) or run(["check"])
    if code:
        sys.exit(code)

    if args.demo:
        say("DEMO ACCOUNTS", "one shared password for the demo portals")
        code = run(["set_demo_passwords", "--csv", args.account_csv])
        if code:
            sys.exit(code)

    say("DONE", f"seeded. Run: .venv/bin/python manage.py runserver   (then open http://localhost:8000/ "
                f"and the QA portal on your QA_PORTAL_HOST, default qa.localhost:8000)")


if __name__ == "__main__":
    main()
