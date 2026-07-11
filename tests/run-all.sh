#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for test in tracker-ops-test.sh task-routing-test.sh task-runtime-test.sh parallel-integration-test.sh dispatch-test.sh launcher-test.sh; do
  [ -f "$ROOT/tests/$test" ] || continue
  echo "==> $test"
  TEAM_RUNNER=background bash "$ROOT/tests/$test"
done

echo "---"
echo "ALL TESTS PASS"
