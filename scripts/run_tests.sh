#!/usr/bin/env bash
# Usage: bash scripts/run_tests.sh [quick|phases|all]
set -e
MODE="${1:-quick}"
ARGS="-q -p no:cacheprovider -W ignore"
case "$MODE" in
  quick)  python -m pytest $ARGS inspection/tests ;;
  phases) python -m pytest $ARGS qa_core/tests institutions/tests accreditation/tests payments/tests inspection/tests ;;
  all)    python -m pytest $ARGS ;;
  *) echo "Usage: bash scripts/run_tests.sh [quick|phases|all]"; exit 2 ;;
esac
