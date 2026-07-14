#!/usr/bin/env bash
# End-to-end parallel branch integration and retry safety using the Markdown tracker.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILURES=0
check() { local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok: $desc"; else echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); fi
}

cd "$TMP"; git init -q repo && cd repo
git checkout -q -b feature-integration
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
TRACKER_WRITERS=all
EXECUTION=parallel
MAX_ACTIVE_IMPLEMENTERS=2
VALIDATE_BUILD=null
VALIDATE_TEST="grep -q task-change app.txt"
VALIDATE_LINT=null
VALIDATE_FORMAT=null
```
EOF
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
EOF

FID=.workspace/task-manager/feature.md
TID="$FID#1"
LAUNCH=.agent-squad/bin/launch-team.sh
wt="$($LAUNCH worktree feature-integration backend "$TID" 1)"
.agent-squad/bin/task-packet.sh feature-integration "$FID" "$TID" backend 1 "$wt" "agent-task/$(python3 .agent-squad/bin/runtime-state.py key "$TID")" >/dev/null
echo task-change > "$wt/app.txt"
git -C "$wt" add app.txt
git -C "$wt" commit -q -m 'task checkpoint'
reviewed_head="$(git -C "$wt" rev-parse HEAD)"
package="$(.agent-squad/bin/review-package.sh feature-integration "$TID")"
check "review package contains task diff" grep -q '^+task-change$' "$package"

.agent-squad/bin/integrate-task.sh feature-integration "$FID" "$TID" backend 1 >/dev/null
check "feature branch receives task change" grep -q '^task-change$' app.txt
check "integration preserves reviewed task head" test "$(git rev-parse "agent-task/$(python3 .agent-squad/bin/runtime-state.py key "$TID")")" = "$reviewed_head"
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

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
