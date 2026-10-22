#!/bin/bash
# Digital NBTE — start the development server for the website and the app.
#
# Double-click this file in Finder (or run ./start_server.command in
# Terminal). It finds this Mac's Wi-Fi address, lets the phone reach the
# server on it, and prints the address to use in the app. Close the window
# (or press Ctrl+C) to stop the server.

cd "$(dirname "$0")" || exit 1

if [ -x ".venv312/bin/python" ]; then
  PY=".venv312/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  echo "No Python environment found in $(pwd)."
  echo "Create one:  python3 -m venv .venv312 && .venv312/bin/python -m pip install --only-binary=cryptography,cbor2 -r requirements-dev.txt"
  read -r -p "Press Enter to close." _
  exit 1
fi

IP="$(ipconfig getifaddr en0 2>/dev/null)"
[ -z "$IP" ] && IP="$(ipconfig getifaddr en1 2>/dev/null)"

# Allowed hosts: the usual local names, this Mac's Wi-Fi address, and
# whatever .env already lists (the environment wins over .env).
EXTRA="$(grep -E '^ALLOWED_HOSTS=' .env 2>/dev/null | head -n1 | cut -d= -f2-)"
export ALLOWED_HOSTS="localhost,127.0.0.1,qa.localhost${IP:+,$IP}${EXTRA:+,$EXTRA}"
export PYTHONUTF8=1

echo
echo "  Digital NBTE server"
echo "  -------------------"
if [ -n "$IP" ]; then
  echo "  Website on this Mac:   http://localhost:8000/"
  echo "  Website on the phone:  http://$IP:8000/"
  echo "  Server address for the app (Settings > About > tap the version 7 times > Developer):"
  echo
  echo "      http://$IP:8000/api/v1/"
  echo
  echo "  The phone must be on the same Wi-Fi as this Mac."
else
  echo "  This Mac is not on Wi-Fi: only http://localhost:8000/ will work."
fi
echo "  Stop the server: close this window or press Ctrl+C."
echo

"$PY" manage.py migrate --check >/dev/null 2>&1 || {
  echo "  Applying database updates…"
  "$PY" manage.py migrate
}
exec "$PY" manage.py runserver 0.0.0.0:8000
