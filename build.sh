#!/bin/bash
# Render build: deps + static files (hashed names, NBTEManifestStaticFilesStorage).
set -euo pipefail

pip install --upgrade pip
pip install --only-binary=cryptography,cbor2 -r requirements-prod.txt

# Fail fast on missing env before the first boot (settings.prod raises loudly).
python manage.py check --deploy
python manage.py collectstatic --noinput
