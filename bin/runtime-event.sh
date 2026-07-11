#!/usr/bin/env bash
# Append one durable execution event and immediately reflect task progress in the tracker.
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

[ $# -ge 7 ] && [ $# -le 9 ] || {
  echo "usage: runtime-event.sh <team> <featureId> <taskId|-> <attempt> <actor> <type> <stage> [summary] [artifact]" >&2
  exit 2
}

team="$1"; feature="$2"; task="$3"; attempt="$4"; actor="$5"; type="$6"; stage="$7"
summary="${8:-}"; artifact="${9:-}"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$(git rev-parse --show-toplevel)/$root/$team"

args=(emit --workspace "$workspace" --team "$team" --feature "$feature" --task "$task"
      --attempt "$attempt" --actor "$actor" --type "$type" --stage "$stage"
      --summary "$summary")
[ "$(read_key TRACKER_WRITERS)" != "all" ] || args+=(--tracker-ops "$SKILL_DIR/bin/tracker-ops.sh")
[ -z "$artifact" ] || args+=(--artifact "$artifact")
python3 "$SKILL_DIR/bin/runtime-state.py" "${args[@]}"
