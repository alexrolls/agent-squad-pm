#!/usr/bin/env bash
# Write one task's commit list, stat, and full diff to a reviewer handoff file.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SKILL_DIR/config/team.config.md"

read_key() {
  local line value _t
  line="$(grep -m1 "^$1=" "$CONFIG" || true)"
  value="${line#*=}"
  if [ "${value#\"}" != "$value" ]; then value="${value#\"}"; value="${value%%\"*}"
  else value="${value%%[[:space:]]#*}"; _t="${value##*[![:space:]]}"; value="${value%"$_t"}"; fi
  [ "$value" = "null" ] && value=""
  printf '%s' "$value"
}

git_unprivileged() {
  local args=(-i "PATH=${PATH:-/usr/bin:/bin}" "GIT_CONFIG_GLOBAL=/dev/null" "GIT_CONFIG_NOSYSTEM=1")
  [ -z "${TMPDIR-}" ] || args+=("TMPDIR=$TMPDIR")
  [ -z "${LANG-}" ] || args+=("LANG=$LANG")
  [ -z "${LC_ALL-}" ] || args+=("LC_ALL=$LC_ALL")
  /usr/bin/env "${args[@]}" git -c core.hooksPath=/dev/null -c core.fsmonitor=false "$@"
}

[ $# -eq 2 ] || { echo "usage: review-package.sh <team> <taskId>" >&2; exit 2; }
team="$1"; task="$2"; repo="$(git_unprivileged rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$(python3 "$SKILL_DIR/bin/teamwork-path.py" workspace --repo "$repo" --root "$root" --team "$team")"
key="$(python3 "$SKILL_DIR/bin/runtime-state.py" key "$task")"
execution="$(python3 "$SKILL_DIR/bin/teamwork-path.py" child --repo "$repo" --workspace "$workspace" --relative "executions/$key.json")"
python3 "$SKILL_DIR/bin/teamwork-path.py" child --repo "$repo" --workspace "$workspace" --relative "artifacts/$key" >/dev/null
[ -f "$execution" ] && [ ! -L "$execution" ] || { echo "review-package: no safe execution record for $task" >&2; exit 1; }
branch="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["branch"])' "$execution")"
read -r role attempt worktree <<EOF
$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["role"], d["attempt"], d["worktree"])' "$execution")
EOF
case "$role" in ''|*[!a-z0-9-]*) echo "review-package: unsafe execution role" >&2; exit 1 ;; esac
case "$attempt" in ''|*[!0-9]*) echo "review-package: unsafe execution attempt" >&2; exit 1 ;; esac
[ "$branch" = "agent-task/$team/$key" ] || { echo "review-package: execution branch does not match task/team generation" >&2; exit 1; }
expected_worktree="$(python3 "$SKILL_DIR/bin/teamwork-path.py" child --repo "$repo" --workspace "$workspace" --relative "worktrees/$role#$attempt-$key")"
[ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$worktree")" = "$expected_worktree" ] \
  || { echo "review-package: execution worktree is outside its task slot" >&2; exit 1; }
worktree="$expected_worktree"
[ -d "$worktree" ] && [ ! -L "$worktree" ] || { echo "review-package: missing safe worktree $worktree" >&2; exit 1; }
[ -z "$(git_unprivileged -C "$worktree" status --porcelain -uall)" ] || {
  echo "review-package: $task has uncommitted changes; worker must create task-branch checkpoint commits first" >&2
  exit 1
}
base="$(git_unprivileged -C "$repo" merge-base "$team" "$branch")"
head="$(git_unprivileged -C "$repo" rev-parse "$branch")"
out="$(python3 "$SKILL_DIR/bin/teamwork-path.py" child --repo "$repo" --workspace "$workspace" --relative "artifacts/$key/review-$(git_unprivileged -C "$repo" rev-parse --short "$base")..$(git_unprivileged -C "$repo" rev-parse --short "$head").diff")"
mkdir -p "$(dirname "$out")"
{
  echo "# Review package: $task"
  echo
  echo "Base: $base"
  echo "Head: $head"
  echo
  echo "## Commits"
  git_unprivileged -C "$repo" log --oneline "$base..$head"
  echo
  echo "## Files changed"
  git_unprivileged -C "$repo" diff --stat "$base..$head"
  echo
  echo "## Diff"
  git_unprivileged -C "$repo" diff -U10 "$base..$head"
} > "$out"
echo "$out"
