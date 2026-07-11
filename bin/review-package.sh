#!/usr/bin/env bash
# Write one task's commit list, stat, and full diff to a reviewer handoff file.
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

[ $# -eq 2 ] || { echo "usage: review-package.sh <team> <taskId>" >&2; exit 2; }
team="$1"; task="$2"; repo="$(git rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$repo/$root/$team"
key="$(python3 "$SKILL_DIR/bin/runtime-state.py" key "$task")"
execution="$workspace/executions/$key.json"
[ -f "$execution" ] || { echo "review-package: no execution record for $task" >&2; exit 1; }
branch="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["branch"])' "$execution")"
worktree="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"])' "$execution")"
[ -d "$worktree" ] || { echo "review-package: missing worktree $worktree" >&2; exit 1; }
[ -z "$(git -C "$worktree" status --porcelain -uall)" ] || {
  echo "review-package: $task has uncommitted changes; worker must create task-branch checkpoint commits first" >&2
  exit 1
}
base="$(git -C "$repo" merge-base "$team" "$branch")"
head="$(git -C "$repo" rev-parse "$branch")"
out="$workspace/artifacts/$key/review-$(git -C "$repo" rev-parse --short "$base")..$(git -C "$repo" rev-parse --short "$head").diff"
mkdir -p "$(dirname "$out")"
{
  echo "# Review package: $task"
  echo
  echo "Base: $base"
  echo "Head: $head"
  echo
  echo "## Commits"
  git -C "$repo" log --oneline "$base..$head"
  echo
  echo "## Files changed"
  git -C "$repo" diff --stat "$base..$head"
  echo
  echo "## Diff"
  git -C "$repo" diff -U10 "$base..$head"
} > "$out"
echo "$out"
