#!/usr/bin/env bash
# Project the tracker snapshot into one editable task progress artifact and one feature digest.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SKILL_DIR/config/team.config.md"

read_key() {
  local line value
  line="$(grep -m1 "^$1=" "$CONFIG" || true)"
  value="${line#*=}"
  value="${value%%[[:space:]]#*}"
  value="${value%\"}"; value="${value#\"}"
  [ "$value" = "null" ] && value=""
  printf '%s' "$value"
}

[ $# -eq 3 ] || { echo "usage: sync-progress.sh <team> <featureId> <tasks.json>" >&2; exit 2; }
team="$1"; feature="$2"; tasks="$3"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$(git rev-parse --show-toplevel)/$root/$team"
args=(sync --workspace "$workspace" --team "$team" --feature "$feature" --tasks "$tasks"
      --tracker-ops "$SKILL_DIR/bin/tracker-ops.sh")
while IFS= read -r status; do
  args+=(--terminal "$status")
done < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); [print(s["name"]) for s in d["tasks"]["statuses"] if s.get("terminal")]' "$SKILL_DIR/config/statuses.config.json")
python3 "$SKILL_DIR/bin/runtime-state.py" "${args[@]}"
