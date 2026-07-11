#!/usr/bin/env bash
# Task-scoped runtime test: lean packets, model routing, events, outbox, and PM projection.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILURES=0
check() { local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok: $desc"; else echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); fi
}

cd "$TMP"; git init -q repo && cd repo
git commit -q --allow-empty -m init; git checkout -q -b feature-runtime
mkdir -p .agent-squad/{bin,config,roles} feat
cp "$SKILL_DIR"/bin/*.sh "$SKILL_DIR"/bin/*.py .agent-squad/bin/
cp "$SKILL_DIR/config/statuses.config.json" .agent-squad/config/
cp "$SKILL_DIR/roles/backend.md" .agent-squad/roles/
cat > .agent-squad/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=Markdown
STATUS_CONFIG=config/statuses.config.json
```
EOF
cat > .agent-squad/config/team.config.md <<'EOF'
```
BACKEND_CMD="false"
TASK_FAST_CMD="cat {prompt_file} > task-fast-prompt.txt"
TEAM_DEFAULT_CMD="false"
TEAMWORK_ROOT=.teamwork
TRACKER_WRITERS=lead
EXECUTION=parallel
MAX_ACTIVE_IMPLEMENTERS=2
VALIDATE_BUILD=null
VALIDATE_TEST="true"
VALIDATE_LINT=null
VALIDATE_FORMAT=null
```
EOF
cat > feat/feature.md <<'EOF'
# Runtime fixture [Active]

## 1 Implement endpoint [Planned]

**Assignee:** -

track: backend
parallel-safe: true
files: src/endpoint.py
resources: api:endpoint
model-profile: fast

Implement the endpoint with tests.

> [design-note] round: 1
> Approach approved for the fixture.
>
> - backend

> [design-approved] round: 1
> 1. Endpoint behavior is tested.
>
> - principal-architect
EOF

LAUNCH=.agent-squad/bin/launch-team.sh
OPS=.agent-squad/bin/tracker-ops.sh
EVENT=.agent-squad/bin/runtime-event.sh
FID=feat/feature.md
TID="$FID#1"
key="$(python3 .agent-squad/bin/runtime-state.py key "$TID")"

wt="$($LAUNCH worktree feature-runtime backend "$TID" 1)"
check "task worktree uses collision-safe key" test "$(basename "$wt")" = "backend#1-$key"
check "task branch is namespaced" test "$(git -C "$wt" branch --show-current)" = "agent-task/$key"
prompt="$($LAUNCH compose-task feature-runtime "$FID" backend "$TID" 1)"
check "lean task prompt exists" test -f "$prompt"
check "lean prompt points to task packet" grep -q 'Task packet:' "$prompt"
if grep -q 'The Multi-Agent Protocol' "$prompt"; then
  echo "FAIL: lean prompt inlined full protocol"; FAILURES=$((FAILURES+1))
else
  echo "ok: lean prompt excludes full protocol"
fi
packet_json=".teamwork/feature-runtime/artifacts/$key/attempt-1/task-packet.json"
packet_md=".teamwork/feature-runtime/artifacts/$key/attempt-1/task-packet.md"
check "packet records fast model profile" grep -q '"modelProfile": "fast"' "$packet_json"
check "packet requirement excludes Markdown comment history" python3 -c '
import json, sys
d=json.load(open(sys.argv[1]))
assert "[design-note]" not in d["description"]
assert "**Assignee:**" not in d["description"]
' "$packet_json"
packet_checksum="$(cksum "$packet_md")"
printf '[handoff]\nThis arrives after packet creation.\n' | "$OPS" comment "$TID" - >/dev/null
"$LAUNCH" compose-task feature-runtime "$FID" backend "$TID" 1 >/dev/null
check "same attempt reuses immutable packet" test "$(cksum "$packet_md")" = "$packet_checksum"

TEAM_RUNNER=background "$LAUNCH" start-task feature-runtime "$FID" backend "$TID" 1
for _i in $(seq 1 30); do [ -f "$wt/task-fast-prompt.txt" ] && break; sleep 0.1; done
check "task-specific model command ran" test -f "$wt/task-fast-prompt.txt"
check "task pid uses task instance directory" test -d .teamwork/feature-runtime/pids/tasks

"$OPS" claim "$TID" backend >/dev/null
"$EVENT" feature-runtime "$FID" "$TID" 1 backend task.started implementing "writing endpoint" >/dev/null
check "event journal records task event" grep -q '"type":"task.started"' .teamwork/feature-runtime/events.ndjson
if grep -q 'agent-squad:progress:start' "$FID"; then
  echo "FAIL: worker wrote tracker directly in scribe mode"; FAILURES=$((FAILURES+1))
else
  echo "ok: scribe mode keeps task event local until dispatcher sync"
fi
"$OPS" export "$FID" .teamwork/feature-runtime/tasks.json >/dev/null
.agent-squad/bin/sync-progress.sh feature-runtime "$FID" .teamwork/feature-runtime/tasks.json >/dev/null
check "dispatcher projects event stage to tracker progress" grep -q '^> stage: implementing$' "$FID"

cat > review.md <<'EOF'
[review-request]
round: 1
Files: src/endpoint.py, tests/test_endpoint.py
Evidence: focused tests passed

- backend
EOF
entry="$(.agent-squad/bin/submit-artifact.sh feature-runtime "$FID" "$TID" 1 backend review-request review.md Review)"
check "scribe mode leaves a durable outbox entry" test -f "$entry"
.agent-squad/bin/process-outbox.sh feature-runtime "$FID" >/dev/null & outbox_pid_1=$!
.agent-squad/bin/process-outbox.sh feature-runtime "$FID" >/dev/null & outbox_pid_2=$!
wait "$outbox_pid_1" "$outbox_pid_2"
check "outbox publishes review request" grep -q '\[review-request\]' "$FID"
check "outbox performs requested transition" grep -q '^## 1 Implement endpoint \[Review\]$' "$FID"
done_entry=".teamwork/feature-runtime/outbox/done/$(basename "$entry")"
check "outbox entry moves to done" test -f "$done_entry"
check "concurrent outbox drains keep one tracker comment" test "$(grep -c 'delivery-id:' "$FID")" -eq 1
mv "$done_entry" "$entry"
python3 - "$entry" <<'PY'
import json, os, sys
p=sys.argv[1]; d=json.load(open(p)); d['phase']='pending'
t=p+'.tmp'; open(t,'w').write(json.dumps(d, indent=2)+'\n'); os.replace(t,p)
PY
.agent-squad/bin/process-outbox.sh feature-runtime "$FID" >/dev/null
check "outbox retry keeps one tracker comment" test "$(grep -c 'delivery-id:' "$FID")" -eq 1
check "outbox retry keeps target status" grep -q '^## 1 Implement endpoint \[Review\]$' "$FID"

rm -f "$wt/task-fast-prompt.txt"
cat > findings.md <<'EOF'
[review-findings]
round: 1
1. Add the missing edge-case assertion.

- reviewer
EOF
"$OPS" comment "$TID" findings.md >/dev/null
"$OPS" state "$TID" Active >/dev/null
TEAM_RUNNER=background .agent-squad/bin/dispatch.sh feature-runtime "$FID" --once --unblock=off >/dev/null
attempt2_wt=".teamwork/feature-runtime/worktrees/backend#2-$key"
for _i in $(seq 1 30); do [ -f "$attempt2_wt/task-fast-prompt.txt" ] && break; sleep 0.1; done
check "review findings launch a fresh attempt" grep -q '"attempt": 2' ".teamwork/feature-runtime/executions/$key.json"
check "fresh rework packet includes findings" grep -q '\[review-findings\]' ".teamwork/feature-runtime/artifacts/$key/attempt-2/task-packet.md"
check "clean prior worktree is retired" test ! -d "$wt"

"$OPS" export "$FID" .teamwork/feature-runtime/tasks.json >/dev/null
.agent-squad/bin/sync-progress.sh feature-runtime "$FID" .teamwork/feature-runtime/tasks.json >/dev/null
check "sync creates one feature digest" test "$(grep -c 'agent-squad:digest:start' "$FID")" -eq 1
check "sync projection is durable" test -f .teamwork/feature-runtime/pm-projection.json

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
