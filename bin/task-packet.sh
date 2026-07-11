#!/usr/bin/env bash
# Generate the immutable, task-local context packet consumed by one fresh worker.
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

[ $# -eq 7 ] || {
  echo "usage: task-packet.sh <team> <featureId> <taskId> <role> <attempt> <worktree> <branch>" >&2
  exit 2
}
team="$1"; feature="$2"; task="$3"; role="$4"; attempt="$5"; worktree="$6"; branch="$7"
repo="$(git rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$repo/$root/$team"
mkdir -p "$workspace"
"$SKILL_DIR/bin/tracker-ops.sh" export "$feature" "$workspace/tasks.json" >/dev/null
python3 "$SKILL_DIR/bin/runtime-state.py" packet \
  --workspace "$workspace" --tasks "$workspace/tasks.json" --feature "$feature" --task "$task" \
  --role "$role" --attempt "$attempt" --worktree "$worktree" --branch "$branch" \
  --config "$CONFIG" --contracts "$workspace/CONTRACTS.md" --baseline "$workspace/BASELINE.md"
