#!/usr/bin/env bash
# U5 / T-SEC-1: build a clean, shareable zip of the project.
#
# The zip is built ONLY from files tracked by git (`git ls-files`), so local
# databases, media/, protected_media/, caches, .env and logs never leave the
# machine. protected_media/ is never included, whatever flags are passed.
#
# Usage (from anywhere inside the repository):
#   bash scripts/make_share_zip.sh                 # -> ../nbte-share-<date>.zip
#   bash scripts/make_share_zip.sh -o /tmp/x.zip   # choose the output file
#   bash scripts/make_share_zip.sh --with-db       # also add db.sqlite3, after
#                                                  # checking it is migrated
#
# --with-db is for handing over a DEMO database only. The script refuses when
# `manage.py migrate --check` reports unapplied migrations. Never use it on a
# database that holds real staff or institution data.
set -euo pipefail

WITH_DB=0
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-db) WITH_DB=1 ;;
    -o|--output) shift; OUT="${1:-}" ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
NAME="$(basename "$ROOT")"
[ -n "$OUT" ] || OUT="$(dirname "$ROOT")/${NAME}-share-$(date +%Y%m%d-%H%M).zip"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT

# Tracked files only; drop anything that must never be shared even if tracked
# by mistake (databases, protected or public media, env files, caches).
git ls-files -z | tr '\0' '\n' \
  | grep -Ev '(^|/)protected_media/|(^|/)media/|\.sqlite3|(^|/)db\.sqlite3|(^|/)\.env($|\.)|__pycache__/|\.pyc$|\.log$' \
  | while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done > "$LIST" || true
# keep .env.example (filtered above by the .env rule)
[ -f .env.example ] && echo ".env.example" >> "$LIST"

# CI-style check: fail loudly if git tracks any database file.
if git ls-files | grep -Eq '\.sqlite3'; then
  echo "ERROR: git tracks a .sqlite3 file; untrack it (git rm --cached) before sharing." >&2
  git ls-files | grep -E '\.sqlite3' >&2
  exit 1
fi

if [ "$WITH_DB" = 1 ]; then
  [ -f db.sqlite3 ] || { echo "ERROR: --with-db given but db.sqlite3 not found" >&2; exit 1; }
  PY="${PYTHON:-python}"
  if ! "$PY" manage.py migrate --check >/dev/null 2>&1; then
    echo "ERROR: db.sqlite3 has unapplied migrations; run 'python manage.py migrate' first." >&2
    exit 1
  fi
  echo "db.sqlite3" >> "$LIST"
fi

rm -f "$OUT"
if command -v zip >/dev/null 2>&1; then
  zip -q -X "$OUT" -@ < "$LIST"
else
  python3 - "$OUT" "$LIST" <<'PY'
import sys, zipfile
out, lst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for line in open(lst, encoding="utf-8"):
        p = line.rstrip("\n")
        if p:
            z.write(p)
PY
fi

# Final guard: the archive must not contain protected media.
if python3 -c "import sys,zipfile; n=zipfile.ZipFile(sys.argv[1]).namelist(); sys.exit(any(x.startswith(('protected_media/','media/')) or '/protected_media/' in x for x in n))" "$OUT"; then
  echo "Wrote $OUT ($(wc -l < "$LIST") files$([ "$WITH_DB" = 1 ] && echo ', with migrated db.sqlite3'))"
else
  rm -f "$OUT"; echo "ERROR: media found in archive; aborted." >&2; exit 1
fi
