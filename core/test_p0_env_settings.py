"""Phase 0 (X8 slice, S-27): .env.example lists every variable the code reads;
production settings fail fast when a required one is missing."""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_CALLS = ("environ.get", "getenv", "environ.setdefault", "_require")
# Read only by developer scripts / tests, or prefixed per backup destination.
NOT_LISTED_OK = {"DJANGO_SETTINGS_MODULE"}


def _env_vars_read_by_code():
    names = set()
    for p in ROOT.rglob("*.py"):
        parts = p.relative_to(ROOT).parts
        rel = "/".join(parts)
        if any(part in {"docs", "media", "protected_media", "scripts", "node_modules", ".git", "staticfiles"}
               or part.startswith((".venv", "venv")) for part in parts) or "/tests/" in rel \
                or p.name.startswith(("test_", "conftest")) or "/migrations/" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                func = ast.unparse(node.func)
                if func.endswith(ENV_CALLS) and isinstance(node.args[0].value, str) and node.args[0].value.isupper():
                    names.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ") \
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                names.add(node.slice.value)
    return names


def _listed():
    names = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip().lstrip("#").strip()
        if "=" in line and line.split("=", 1)[0].isupper() and " " not in line.split("=", 1)[0]:
            names.add(line.split("=", 1)[0])
    return names


def test_s27_env_example_lists_every_variable():
    read = _env_vars_read_by_code()
    assert {"SECRET_KEY", "DATABASE_URL", "PAYMENT_QR_SIGNING_KEY"} <= read
    missing = read - _listed() - NOT_LISTED_OK
    assert not missing, f".env.example lacks {sorted(missing)}"


def test_s27_env_example_has_no_secrets():
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(w in key for w in ("SECRET", "KEY", "PASSWORD", "TOKEN")) and key != "SECRET_KEY":
            assert value == "", f"{key} must be blank in .env.example"
    assert "change-me" in text


def _import_prod(env):
    code = "import django; from django.conf import settings; settings.SECRET_KEY; print('OK')"
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "SECRET", "DATABASE", "REDIS",
                                                                          "CELERY", "SITE_", "ALLOWED", "FIELD_",
                                                                          "DEFAULT_FROM", "BREVO", "GMAIL"))}
    clean.update({"DJANGO_SETTINGS_MODULE": "nbte_ict.settings.prod", **env})
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True,
                          timeout=120)


def _import_dev(env):
    code = (
        "from django.conf import settings; "
        "db=settings.DATABASES['default']; "
        "print('|'.join(str(db.get(k, '')) for k in ('ENGINE', 'NAME', 'HOST', 'PORT')))"
    )
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("DJANGO_", "DATABASE_BACKEND", "DATABASE_URL"))}
    clean.update({"DJANGO_SETTINGS_MODULE": "nbte_ict.settings.dev", **env})
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True,
                          timeout=120)


REQUIRED = {"SECRET_KEY": "x" * 50, "ALLOWED_HOSTS": "ict.example.test", "DATABASE_URL": "sqlite:////tmp/x.db",
            "SITE_URL": "https://ict.example.test", "DEFAULT_FROM_EMAIL": "NBTE <no-reply@example.test>",
            "REDIS_URL": "redis://localhost:6379/1", "CELERY_BROKER_URL": "redis://localhost:6379/0",
            "BREVO_API_KEY": "placeholder", "FIELD_ENCRYPTION_KEY": "k" * 44}


def test_prod_settings_fail_fast():
    # Every required variable blank: one error naming all of them (a blank
    # value also beats anything a stray .env file could supply).
    blank = {k: "" for k in REQUIRED}
    r = _import_prod(blank)
    assert r.returncode != 0
    assert "ImproperlyConfigured" in r.stderr
    for name in ("SECRET_KEY", "ALLOWED_HOSTS", "DATABASE_URL", "SITE_URL", "DEFAULT_FROM_EMAIL", "REDIS_URL",
                 "CELERY_BROKER_URL"):
        assert name in r.stderr
    # One missing.
    r = _import_prod({**REQUIRED, "REDIS_URL": ""})
    assert r.returncode != 0 and "REDIS_URL" in r.stderr and "SECRET_KEY," not in r.stderr
    # A weak secret key.
    r = _import_prod({**REQUIRED, "SECRET_KEY": "dev-only-insecure-key"})
    assert r.returncode != 0 and "too weak" in r.stderr
    # Everything set: settings load.
    r = _import_prod(REQUIRED)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "OK" in r.stdout


def test_dev_database_backend_can_be_selected_without_connecting():
    url = "postgresql://test-user:test-password@aiven.example.test:25000/nbte_test?sslmode=require"
    local = _import_dev({"DATABASE_BACKEND": "sqlite", "DATABASE_URL": url})
    assert local.returncode == 0, local.stderr[-2000:]
    assert local.stdout.strip().split("|")[0] == "django.db.backends.sqlite3"

    remote = _import_dev({"DATABASE_BACKEND": "url", "DATABASE_URL": url})
    assert remote.returncode == 0, remote.stderr[-2000:]
    engine, _name, host, port = remote.stdout.strip().split("|")
    assert engine == "django.db.backends.postgresql"
    assert host == "aiven.example.test" and port == "25000"

    missing_url = _import_dev({"DATABASE_BACKEND": "url", "DATABASE_URL": ""})
    assert missing_url.returncode != 0
    assert "DATABASE_BACKEND=url requires DATABASE_URL" in missing_url.stderr
