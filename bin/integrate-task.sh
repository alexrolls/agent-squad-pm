#!/usr/bin/env bash
# Merge one approved task branch into the feature branch as a recoverable transaction.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SKILL_DIR/config/team.config.md"

die() { echo "integrate-task: $*" >&2; exit 1; }
read_key() {
  local line value
  line="$(grep -m1 "^$1=" "$CONFIG" || true)"
  value="${line#*=}"
  if [ "${value#\"}" != "$value" ]; then value="${value#\"}"; value="${value%%\"*}"
  else value="${value%%[[:space:]]#*}"; fi
  [ "$value" = "null" ] && value=""
  printf '%s' "$value"
}

[ $# -ge 5 ] && [ $# -le 6 ] || {
  die "usage: integrate-task.sh <team> <featureId> <taskId> <role> <attempt> [completion-bodyfile]"
}
team="$1"; feature="$2"; task="$3"; role="$4"; attempt="$5"; supplied_body="${6:-}"
repo="$(git rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$repo/$root/$team"
key="$(python3 "$SKILL_DIR/bin/runtime-state.py" key "$task")"
execution="$workspace/executions/$key.json"
[ -f "$execution" ] || die "no execution record for $task"
branch="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["branch"])' "$execution")"
worktree="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"])' "$execution")"
transaction="$workspace/integrations/$key.json"
mkdir -p "$(dirname "$transaction")"

phase=""
commit=""
if [ -f "$transaction" ]; then
  phase="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("phase", ""))' "$transaction")"
  commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("commit", ""))' "$transaction")"
fi
if [ "$phase" = "completed" ]; then
  echo "$task already integrated at $commit"
  exit 0
fi

run_validation() {
  local where="$1" changed_file="$2" command value
  value="$(read_key VALIDATE_SCRIPT)"
  if [ -n "$value" ]; then
    local changed_files=() item
    while IFS= read -r item; do [ -z "$item" ] || changed_files+=("$item"); done < "$changed_file"
    ( cd "$where" && "$value" "${changed_files[@]}" ) || return $?
    return
  fi
  for command in VALIDATE_BUILD VALIDATE_TEST VALIDATE_LINT VALIDATE_FORMAT; do
    value="$(read_key "$command")"
    [ -z "$value" ] || ( cd "$where" && eval "$value" ) || return $?
  done
}

write_transaction() {
  local next_phase="$1" next_commit="$2"
  python3 - "$transaction" "$team" "$feature" "$task" "$role" "$attempt" "$branch" "$next_phase" "$next_commit" <<'PY'
import json, os, sys
from datetime import datetime, timezone
path, team, feature, task, role, attempt, branch, phase, commit = sys.argv[1:]
value = {
    'schemaVersion': 1, 'team': team, 'featureId': feature, 'taskId': task,
    'role': role, 'attempt': int(attempt), 'branch': branch, 'phase': phase,
    'commit': commit or None, 'updatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds')
}
temp=path+'.tmp'; open(temp,'w').write(json.dumps(value, indent=2)+'\n'); os.replace(temp,path)
PY
}

if [ "$phase" != "merged" ]; then
  [ "$(git -C "$repo" branch --show-current)" = "$team" ] || die "repository checkout must be on feature branch '$team'"
  [ -d "$worktree" ] || die "missing task worktree $worktree"
  [ -z "$(git -C "$worktree" status --porcelain -uall)" ] || die "task worktree is dirty; checkpoint commits are required before integration"
  ahead="$(git -C "$repo" rev-list --count "$team..$branch")"
  [ "$ahead" -gt 0 ] || die "task branch $branch has no checkpoint commits to integrate"

  changed_file_list="$workspace/artifacts/$key/integration-files.txt"
  mkdir -p "$(dirname "$changed_file_list")"
  git -C "$repo" diff --name-only "$team...$branch" > "$changed_file_list"
  [ -s "$changed_file_list" ] || die "task branch has no changed files"
  run_validation "$worktree" "$changed_file_list"
  package="$("$SKILL_DIR/bin/review-package.sh" "$team" "$task")"

  [ -z "$(git -C "$repo" status --porcelain -uall)" ] || die "feature-branch checkout is dirty"
  if ! git -C "$repo" merge --no-ff --no-commit "$branch"; then
    git -C "$repo" merge --abort >/dev/null 2>&1 || true
    die "merge conflict; return the task branch to the worker"
  fi
  if ! run_validation "$repo" "$changed_file_list"; then
    git -C "$repo" merge --abort >/dev/null 2>&1 || true
    die "feature-branch validation failed; merge aborted"
  fi
  branch_head="$(git -C "$repo" rev-parse "$branch")"
  git -C "$repo" commit -m "integrate: $task" -m "Task-Id: $task" -m "Task-Branch-Head: $branch_head"
  commit="$(git -C "$repo" rev-parse HEAD)"
  write_transaction merged "$commit"
  phase=merged
fi

git -C "$repo" merge-base --is-ancestor "$commit" "$team" || die "recorded integration commit $commit is not on $team"
body="$workspace/artifacts/$key/integration-completion.md"
if [ -n "$supplied_body" ]; then
  cp "$supplied_body" "$body"
else
  package="${package:-$(python3 - "$workspace/artifacts/$key" <<'PY'
from pathlib import Path
import sys
files=sorted(Path(sys.argv[1]).glob('review-*.diff'), key=lambda p: p.stat().st_mtime, reverse=True)
print(files[0] if files else '')
PY
)}"
  {
    echo "Task branch: $branch"
    echo "Integration commit: $commit"
    [ -z "$package" ] || echo "Review package: $package"
    echo "Independent feature-branch validation completed."
  } > "$body"
fi
"$SKILL_DIR/bin/tracker-ops.sh" integrate "$task" "$commit" "$body"
python3 "$SKILL_DIR/bin/runtime-state.py" emit --workspace "$workspace" --team "$team" --feature "$feature" \
  --task "$task" --attempt "$attempt" --actor integrator --type task.integrated --stage integrated \
  --summary "merged and tracker completion recorded" --artifact "$body" >/dev/null
"$SKILL_DIR/bin/launch-team.sh" worktree-remove "$team" "$role" "$task" "$attempt" >/dev/null
write_transaction completed "$commit"
echo "$task integrated at $commit"
