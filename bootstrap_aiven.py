#!/usr/bin/env python3
"""Safely migrate and seed a fresh PostgreSQL/Aiven database.

Run from the web project directory after configuring DATABASE_URL in the
environment or .env file:

    python bootstrap_aiven.py

This never creates accounts, demo data, or institutions. It refuses to run
against SQLite and does not offer a database reset option.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANAGE = ROOT / "manage.py"
TARGET_PREFIX = "BOOTSTRAP_DATABASE:"


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "nbte_ict.settings.dev"
    env["DATABASE_BACKEND"] = "url"
    env["PYTHONUNBUFFERED"] = "1"

    # FIX: The original script had an extra closing brace '}}' in this string.
    inspect_target = (
        "import json; from django.db import connection; "
        f"print('{TARGET_PREFIX}' + json.dumps({{'engine': connection.settings_dict['ENGINE'], "
        "'host': connection.settings_dict.get('HOST', '')}))"
    )
    
    result = subprocess.run(
        [sys.executable, str(MANAGE), "shell", "-c", inspect_target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    target_line = next(
        (line for line in result.stdout.splitlines() if line.startswith(TARGET_PREFIX)),
        None,
    )
    if target_line is None:
        sys.stderr.write(result.stdout)
        sys.stderr.write("Could not verify the configured database. No changes were made.\n")
        return 1

    target = json.loads(target_line[len(TARGET_PREFIX):])
    
    # SAFETY CHECKS
    if target["engine"] != "django.db.backends.postgresql":
        sys.stderr.write(
            f"Refusing to bootstrap non-PostgreSQL database ({target['engine']}). "
            "No changes were made.\n"
        )
        return 2
    if not target["host"]:
        sys.stderr.write("The PostgreSQL database has no configured host. No changes were made.\n")
        return 2

    print(f"✅ Verified PostgreSQL target host: {target['host']}")
    
    if not sys.stdin.isatty():
        sys.stderr.write("Run this command in an interactive terminal to confirm the target. No changes were made.\n")
        return 2
        
    try:
        confirmation = input("⚠️  Type 'yes' to migrate and seed this database: ")
    except EOFError:
        sys.stderr.write("\nNo confirmation received. No changes were made.\n")
        return 2
        
    if confirmation.strip().lower() != "yes":
        print("Cancelled. No changes were made.")
        return 0

    # COMMANDS TO RUN
    commands = [
        ["check"],
        ["migrate", "--noinput"],
        ["seed_nbte"],
        ["seed_default_groups"],
        ["seed_staff_reference"],
        ["check"],
    ]
    
    for command in commands:
        print(f"\n🚀 ==> manage.py {' '.join(command)}", flush=True)
        completed = subprocess.run(
            [sys.executable, str(MANAGE), *command],
            cwd=ROOT,
            env=env,
        )
        if completed.returncode:
            print(f"\n❌ Command failed: manage.py {' '.join(command)}")
            return completed.returncode

    print(
        "\n🎉 Bootstrap complete. Baseline system data is ready; no accounts or "
        "demo records were created.\n"
        "Create the first administrator with: "
        "python manage.py createsuperuser\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())