#!/usr/bin/env bash
# Migrate, then seed settings/flows and the Phase 4 demo (institution, accounts, QA case, tasks).
set -e
python manage.py migrate
python manage.py seed_startup
python manage.py seed_phase4_demo "$@"
