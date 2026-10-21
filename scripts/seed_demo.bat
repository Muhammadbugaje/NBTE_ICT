@echo off
REM Migrate, then seed settings/flows and the Phase 4 demo (institution, accounts, QA case, tasks).
python manage.py migrate || goto :eof
python manage.py seed_startup || goto :eof
python manage.py seed_phase4_demo %*
