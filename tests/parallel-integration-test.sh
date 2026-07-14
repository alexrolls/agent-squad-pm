#!/usr/bin/env bash
# End-to-end parallel branch integration and retry safety using the Markdown tracker.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; TMP="$(cd "$TMP" && pwd -P)"; trap 'rm -rf "$TMP"' EXIT
FAILURES=0
check() { local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok: $desc"; else echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); fi
}
refuse() { local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); else echo "ok: $desc"; fi
}

bind_review() {
  local task="$1" files="$2" key branch base head package package_digest snapshot prefix
  key="$(python3 .agent-squad/bin/runtime-state.py key "$task")"
  branch="agent-task/feature-integration/$key"
  base="$(git merge-base feature-integration "$branch")"
  head="$(git rev-parse "$branch")"
  package="$(.agent-squad/bin/review-package.sh feature-integration "$task")"
  package_digest="sha256:$(shasum -a 256 "$package" | awk '{print $1}')"
  snapshot=".teamwork/feature-integration/tasks.json"
  prefix="$TMP/review-$key-$(date +%s)-$$"
  printf '[review-request] exact package review\nFiles: %s\n\n- backend\n' "$files" > "$prefix.request"
  printf '[review-approval] exact package approved\nFiles: %s\n\n- reviewer\n' "$files" > "$prefix.review"
  printf '[architecture-approval] exact package approved\nFiles: %s\n\n- principal-architect\n' "$files" > "$prefix.architecture"
  .agent-squad/bin/review_evidence.py bind-request \
    "$prefix.request" "$base" "$head" "$package_digest" "$prefix.request.bound"
  .agent-squad/bin/tracker-ops.sh comment "$task" "$prefix.request.bound"
  .agent-squad/bin/tracker-ops.sh export "$FID" "$snapshot"
  .agent-squad/bin/review_evidence.py bind-approval \
    "$prefix.review" "$snapshot" "$task" "$prefix.review.bound"
  .agent-squad/bin/review_evidence.py bind-approval \
    "$prefix.architecture" "$snapshot" "$task" "$prefix.architecture.bound"
  .agent-squad/bin/tracker-ops.sh comment "$task" "$prefix.review.bound"
  .agent-squad/bin/tracker-ops.sh comment "$task" "$prefix.architecture.bound"
  .agent-squad/bin/tracker-ops.sh export "$FID" "$snapshot"
}

cd "$TMP"; git init -q repo && cd repo
git checkout -q -b feature-integration
LIFECYCLE_ROOT="$TMP/protected-lifecycle"
mkdir -m 700 "$LIFECYCLE_ROOT"
git config user.email test@example.com; git config user.name Test
mkdir -p .agent-squad/{bin,config,roles} .workspace/task-manager
cp "$SKILL_DIR"/bin/*.sh "$SKILL_DIR"/bin/*.py .agent-squad/bin/
cp "$SKILL_DIR/config/statuses.config.json" .agent-squad/config/
cp "$SKILL_DIR/roles/backend.md" .agent-squad/roles/
cat > .gitignore <<'EOF'
.teamwork/
.workspace/
EOF
echo base > app.txt
git add .gitignore app.txt .agent-squad
git commit -q -m init
cat > .agent-squad/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=Markdown
MARKDOWN_ROOT=.
STATUS_CONFIG=config/statuses.config.json
```
EOF
cat > .agent-squad/config/team.config.md <<'EOF'
```
BACKEND_CMD="true"
TEAM_DEFAULT_CMD="true"
TEAMWORK_ROOT=.teamwork
AGENT_ENV_ALLOWLIST="PATH TMPDIR LANG LC_ALL TERM"
AGENT_SANDBOX_ENFORCED=false
BROKER_LIFECYCLE_ROOT=__LIFECYCLE_ROOT__
TRACKER_WRITERS=all
EXECUTION=parallel
MAX_ACTIVE_IMPLEMENTERS=2
VALIDATE_BUILD=null
VALIDATE_TEST="grep -q task-change app.txt"
VALIDATE_LINT=null
VALIDATE_FORMAT=null
```
EOF
sed -i '' "s|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=\"$LIFECYCLE_ROOT\"|" .agent-squad/config/team.config.md
git add .agent-squad/config/project-management.config.md .agent-squad/config/team.config.md
git commit -q -m config
cat > .workspace/task-manager/feature.md <<'EOF'
# Integration fixture [Active]

## 1 Change app [Review]

**Assignee:** backend

parallel-safe: true
files: app.txt

> [review-request] round: 1
> Files: app.txt
>
> - backend

> [review-approval] round: 1
> Files: app.txt
>
> - reviewer

> [architecture-approval] round: 1
> Files: app.txt
>
> - principal-architect

## 3 Concurrent brokered change [Review]

**Assignee:** backend

parallel-safe: true
files: third.txt

> [review-request] round: 1
> Files: third.txt
>
> - backend

> [review-approval] round: 1
> Files: third.txt
>
> - reviewer

> [architecture-approval] round: 1
> Files: third.txt
>
> - principal-architect
EOF

FID=.workspace/task-manager/feature.md
TID="$FID#1"
LAUNCH=.agent-squad/bin/launch-team.sh
wt="$($LAUNCH worktree feature-integration backend "$TID" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID" backend 1 "$wt" "agent-task/feature-integration/$(python3 .agent-squad/bin/runtime-state.py key "$TID")" >/dev/null
echo task-change > "$wt/app.txt"
git -C "$wt" add app.txt
git -C "$wt" commit -q -m 'task checkpoint'
reviewed_head="$(git -C "$wt" rev-parse HEAD)"
package="$(.agent-squad/bin/review-package.sh feature-integration "$TID")"
check "review package contains task diff" grep -q '^+task-change$' "$package"
bind_review "$TID" app.txt

# Advancing the task branch after review must invalidate the old approvals even
# when the attacker changes only a previously approved filename.
echo post-review-change >> "$wt/app.txt"
git -C "$wt" add app.txt
git -C "$wt" commit -q -m 'unreviewed same-file checkpoint'
refuse "branch movement invalidates exact review approvals" \
  .agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID" backend 1
bind_review "$TID" app.txt
reviewed_head="$(git -C "$wt" rev-parse HEAD)"

.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID" backend 1 >/dev/null
check "feature branch receives task change" grep -q '^task-change$' app.txt
check "integration preserves reviewed task head" test "$(git rev-parse "agent-task/feature-integration/$(python3 .agent-squad/bin/runtime-state.py key "$TID")")" = "$reviewed_head"
check "integration commit has task trailer" git log -1 --format=%B --grep="Task-Id: $TID"
check "tracker reaches terminal status" grep -q '^## 1 Change app \[Ready to deploy\]$' "$FID"
key="$(python3 .agent-squad/bin/runtime-state.py key "$TID")"
check "integration transaction completes" grep -q '"phase": "completed"' ".teamwork/feature-integration/integrations/$key.json"
check "worktree is removed last" test ! -d "$wt"
before="$(git rev-list --count HEAD)"
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID" backend 1 >/dev/null
after="$(git rev-list --count HEAD)"
check "integration retry creates no duplicate commit" test "$before" -eq "$after"
check "integration retry creates no duplicate tracker comment" test "$(grep -c 'Integrated: commit' "$FID")" -eq 1

# Default-safe broker mode: the credentialless integrator stops after the exact
# merge transaction; the locked dispatcher owns tracker finalization + cleanup.
perl -0pi -e 's/TRACKER_WRITERS=all/TRACKER_WRITERS=broker/' .agent-squad/config/team.config.md
git add .agent-squad/config/team.config.md
git commit -q -m 'use tracker broker'
cat >> "$FID" <<'EOF'

## 2 Brokered change [Review]

**Assignee:** backend

parallel-safe: true
files: second.txt

> [review-request] round: 1
> Files: second.txt
>
> - backend

> [review-approval] round: 1
> Files: second.txt
>
> - reviewer

> [architecture-approval] round: 1
> Files: second.txt
>
> - principal-architect
EOF

TID2="$FID#2"
wt2="$($LAUNCH worktree feature-integration backend "$TID2" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID2" backend 1 "$wt2" "agent-task/feature-integration/$(python3 .agent-squad/bin/runtime-state.py key "$TID2")" >/dev/null
echo brokered > "$wt2/second.txt"
git -C "$wt2" add second.txt
git -C "$wt2" commit -q -m 'brokered checkpoint'
TID3="$FID#3"
wt3="$($LAUNCH worktree feature-integration backend "$TID3" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID3" backend 1 "$wt3" "agent-task/feature-integration/$(python3 .agent-squad/bin/runtime-state.py key "$TID3")" >/dev/null
echo concurrent > "$wt3/third.txt"
git -C "$wt3" add third.txt
git -C "$wt3" commit -q -m 'concurrent checkpoint'
bind_review "$TID2" second.txt
bind_review "$TID3" third.txt
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID2" backend 1 >/dev/null
key2="$(python3 .agent-squad/bin/runtime-state.py key "$TID2")"
tx2=".teamwork/feature-integration/integrations/$key2.json"
.agent-squad/bin/finalize-integrations.sh --authorize-prepared feature-integration "$FID" \
  ".teamwork/feature-integration/integrations/.prepared/$key2.json" >/dev/null
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID2" backend 1 >/dev/null
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID3" backend 1 >/dev/null
key3="$(python3 .agent-squad/bin/runtime-state.py key "$TID3")"
tx3=".teamwork/feature-integration/integrations/$key3.json"
.agent-squad/bin/finalize-integrations.sh --authorize-prepared feature-integration "$FID" \
  ".teamwork/feature-integration/integrations/.prepared/$key3.json" >/dev/null
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID3" backend 1 >/dev/null
check "broker mode records awaiting-tracker transaction" grep -q '"phase": "awaiting-tracker"' "$tx2"
check "broker mode does not move tracker from credentialless integrator" grep -q '^## 2 Brokered change \[Review\]$' "$FID"
check "broker mode retains worktree until tracker finalization" test -d "$wt2"
check "transaction binds exact reviewed task head" grep -q '"taskBranchHead": "[0-9a-f]\{40\}"' "$tx2"
check "transaction binds review package digest" grep -q '"reviewPackageSha256": "sha256:[0-9a-f]\{64\}"' "$tx2"
check "transaction binds current approval evidence" grep -q '"approvalEvidenceDigest": "sha256:[0-9a-f]\{64\}"' "$tx2"
check "parallel transaction separates integration parent from review base" python3 - "$tx3" <<'PY'
import json, sys
value=json.load(open(sys.argv[1]))
raise SystemExit(0 if value['baseCommit'] != value['reviewBaseCommit'] else 1)
PY

cat >> "$FID" <<'EOF'

> [review-findings] late finding after approval
> Files: second.txt
>
> - reviewer
EOF
if .agent-squad/bin/finalize-integrations.sh feature-integration "$FID" "$tx2"; then
  echo "ok: broker durably supersedes approvals invalidated after the merge"
else
  echo "FAIL: broker durably supersedes approvals invalidated after the merge"
  FAILURES=$((FAILURES+1))
fi
check "late invalidation preserves original transaction in history" \
  bash -c 'test "$(find .teamwork/feature-integration/integrations/history -name "*integration-*.json" | wc -l | tr -d " ")" -ge 1'
check "late invalidation creates an explicit revert commit" \
  git log -1 --format=%B --grep='Integration-Recovery:'
check "late invalidation returns tracker task to active rework" grep -q '^## 2 Brokered change \[Active\]$' "$FID"
check "superseded canonical transaction is retired" test ! -e "$tx2"

# Rework proceeds from the preserved history: a new attempt adds a fix, receives
# a new request/dual approval after the finding, and integrates normally.
$LAUNCH worktree-remove feature-integration backend "$TID2" 1 >/dev/null
wt2="$($LAUNCH worktree feature-integration backend "$TID2" 2)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID2" backend 2 "$wt2" "agent-task/feature-integration/$(python3 .agent-squad/bin/runtime-state.py key "$TID2")" >/dev/null
echo fixed-after-late-finding >> "$wt2/second.txt"
git -C "$wt2" add second.txt
git -C "$wt2" commit -q -m 'late-finding rework checkpoint'
# Preserve both histories and resolve the expected modify/delete divergence
# created by the explicit feature-branch revert. No reset/force-update occurs.
if ! git -C "$wt2" merge --no-edit feature-integration >/dev/null 2>&1; then
  git -C "$wt2" add second.txt
  git -C "$wt2" commit -q -m 'resolve rework against preserved revert'
fi
.agent-squad/bin/tracker-ops.sh state "$TID2" Review >/dev/null
bind_review "$TID2" second.txt
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID2" backend 2 >/dev/null
.agent-squad/bin/finalize-integrations.sh --authorize-prepared feature-integration "$FID" \
  ".teamwork/feature-integration/integrations/.prepared/$key2.json" >/dev/null
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID2" backend 2 >/dev/null
check "late-finding rework reaches a new awaiting-tracker transaction" grep -q '"phase": "awaiting-tracker"' "$tx2"

cp "$tx2" "$tx2.backup"
perl -0pi -e 's/"phase": "awaiting-tracker"/"phase": "completed"/' "$tx2"
refuse "broker rejects producer-forged completed phase" \
  .agent-squad/bin/finalize-integrations.sh feature-integration "$FID" "$tx2"
mv "$tx2.backup" "$tx2"

.agent-squad/bin/dispatch.sh feature-integration "$FID" --once --unblock=off >/dev/null
check "dispatcher broker performs terminal tracker move" grep -q '^## 2 Brokered change \[Ready to deploy\]$' "$FID"
check "dispatcher broker finalizes branch reviewed before prior merge" grep -q '^## 3 Concurrent brokered change \[Ready to deploy\]$' "$FID"
check "dispatcher broker completes transaction after cleanup" grep -q '"phase": "completed"' "$tx2"
check "dispatcher broker removes worktree last" test ! -d "$wt2"
check "dispatcher broker removes concurrent worktree" test ! -d "$wt3"
check "dispatcher emits broker-owned integration event" python3 - ".teamwork/feature-integration/events.ndjson" "$TID2" <<'PY'
import json, sys
events=[json.loads(line) for line in open(sys.argv[1])]
matches=[e for e in events if e.get('type') == 'task.integrated' and e.get('taskId') == sys.argv[2]]
raise SystemExit(0 if len(matches) == 1 and matches[0].get('actor') == 'dispatcher' else 1)
PY

comment_count="$(grep -c 'Integrated: commit' "$FID")"
event_count="$(python3 - ".teamwork/feature-integration/events.ndjson" "$TID2" <<'PY'
import json, sys
print(sum(1 for line in open(sys.argv[1]) if (lambda e: e.get('type') == 'task.integrated' and e.get('taskId') == sys.argv[2])(json.loads(line))))
PY
)"
.agent-squad/bin/dispatch.sh feature-integration "$FID" --once --unblock=off >/dev/null
check "broker retry creates no duplicate integration comment" test "$(grep -c 'Integrated: commit' "$FID")" -eq "$comment_count"
check "broker retry creates no duplicate integration event" test "$(python3 - ".teamwork/feature-integration/events.ndjson" "$TID2" <<'PY'
import json, sys
print(sum(1 for line in open(sys.argv[1]) if (lambda e: e.get('type') == 'task.integrated' and e.get('taskId') == sys.argv[2])(json.loads(line))))
PY
)" -eq "$event_count"

cp "$tx2" "$tx2.backup"
perl -0pi -e 's/("reviewPackageSha256": "sha256:)[0-9a-f]{64}/$1 . ("0" x 64)/e' "$tx2"
refuse "broker rejects a forged review package binding" \
  .agent-squad/bin/finalize-integrations.sh --validate-only feature-integration "$FID" "$tx2"
mv "$tx2.backup" "$tx2"
cp "$tx2" "$tx2.backup"
perl -0pi -e 's/("approvalEvidenceDigest": "sha256:)[0-9a-f]{64}/$1 . ("0" x 64)/e' "$tx2"
refuse "broker rejects a forged approval evidence binding" \
  .agent-squad/bin/finalize-integrations.sh --validate-only feature-integration "$FID" "$tx2"
mv "$tx2.backup" "$tx2"

printf '{"schemaVersion":1,"phase":"awaiting-tracker"}\n' > .teamwork/feature-integration/integrations/forged.json
refuse "broker rejects malformed transaction before tracker writes" \
  .agent-squad/bin/finalize-integrations.sh feature-integration "$FID"
rm .teamwork/feature-integration/integrations/forged.json
ln -s "$(pwd)/$tx2" .teamwork/feature-integration/integrations/symlink.json
refuse "broker rejects symlink transaction" \
  .agent-squad/bin/finalize-integrations.sh feature-integration "$FID"
rm .teamwork/feature-integration/integrations/symlink.json

# SIGKILL recovery: the preparation journal exists before Git mutation. A
# retry recognizes both an interrupted --no-commit merge and a landed merge
# commit whose final transaction write never ran.
cat >> "$FID" <<'EOF'

## 4 Crash during merge [Review]

**Assignee:** backend

parallel-safe: true
files: crash-merge.txt

## 5 Crash after commit [Review]

**Assignee:** backend

parallel-safe: true
files: crash-commit.txt
EOF

TID4="$FID#4"; key4="$(python3 .agent-squad/bin/runtime-state.py key "$TID4")"
wt4="$($LAUNCH worktree feature-integration backend "$TID4" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID4" backend 1 "$wt4" \
  "agent-task/feature-integration/$key4" >/dev/null
echo recover-merge > "$wt4/crash-merge.txt"; git -C "$wt4" add crash-merge.txt
git -C "$wt4" commit -q -m 'merge crash fixture'; bind_review "$TID4" crash-merge.txt
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID4" backend 1 >/dev/null
prep4=".teamwork/feature-integration/integrations/.prepared/$key4.json"
.agent-squad/bin/finalize-integrations.sh --authorize-prepared feature-integration "$FID" "$prep4" >/dev/null
refuse "SIGKILL lands in the journaled merge window" env INTEGRATION_TEST_CRASH_AT=after-merge \
  .agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID4" backend 1
check "crashed merge retains its durable preparation" test -f "$prep4"
check "crashed merge is recognizable through MERGE_HEAD" git rev-parse -q --verify MERGE_HEAD
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID4" backend 1 >/dev/null
tx4=".teamwork/feature-integration/integrations/$key4.json"
check "retry completes interrupted merge exactly once" grep -q '"phase": "awaiting-tracker"' "$tx4"
refuse "retry leaves no in-progress merge" git rev-parse -q --verify MERGE_HEAD
.agent-squad/bin/finalize-integrations.sh feature-integration "$FID" "$tx4" >/dev/null
printf '[review-findings] legitimate finding after tracker finalization, before release\nFiles: crash-merge.txt\n\n- reviewer\n' \
  | .agent-squad/bin/tracker-ops.sh comment "$TID4" - >/dev/null
check "completed integration can be durably superseded before release" \
  .agent-squad/bin/finalize-integrations.sh feature-integration "$FID" "$tx4"
check "completed-task recovery uses the broker-only terminal reopen" \
  grep -q '^## 4 Crash during merge \[Active\]$' "$FID"
check "completed invalidation retires canonical transaction without erasing history" test ! -e "$tx4"
check "completed invalidation remains available as preserved evidence" \
  bash -c 'find .teamwork/feature-integration/integrations/history -name "*integration-*.json" | grep -q .'

TID5="$FID#5"; key5="$(python3 .agent-squad/bin/runtime-state.py key "$TID5")"
wt5="$($LAUNCH worktree feature-integration backend "$TID5" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID5" backend 1 "$wt5" \
  "agent-task/feature-integration/$key5" >/dev/null
echo recover-commit > "$wt5/crash-commit.txt"; git -C "$wt5" add crash-commit.txt
git -C "$wt5" commit -q -m 'commit crash fixture'; bind_review "$TID5" crash-commit.txt
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID5" backend 1 >/dev/null
prep5=".teamwork/feature-integration/integrations/.prepared/$key5.json"
.agent-squad/bin/finalize-integrations.sh --authorize-prepared feature-integration "$FID" "$prep5" >/dev/null
head_before_crash="$(git rev-parse HEAD)"
first_parent_before="$(git rev-list --first-parent --count HEAD)"
refuse "SIGKILL lands after commit but before final transaction" env INTEGRATION_TEST_CRASH_AT=after-commit \
  .agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID5" backend 1
landed_head="$(git rev-parse HEAD)"
check "post-commit crash has one landed bound merge" test "$(git rev-list --first-parent --count HEAD)" -eq $((first_parent_before + 1))
check "post-commit crash preserves exact two-parent intent" test "$(git show -s --format=%P "$landed_head")" = \
  "$head_before_crash $(git rev-parse "agent-task/feature-integration/$key5")"
check "post-commit crash keeps preparation for deterministic recovery" test -f "$prep5"
tx5=".teamwork/feature-integration/integrations/$key5.json"
check "post-commit crash did not fabricate a final transaction" test ! -e "$tx5"
.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID5" backend 1 >/dev/null
check "post-commit retry journals the landed merge" grep -q '"phase": "awaiting-tracker"' "$tx5"
check "post-commit retry creates no duplicate commit" test "$(git rev-parse HEAD)" = "$landed_head"
.agent-squad/bin/finalize-integrations.sh feature-integration "$FID" "$tx5" >/dev/null

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
