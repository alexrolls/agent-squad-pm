#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONITOR_IMPL="$ROOT/bin/pm-agent.py"
TMP="$(mktemp -d)"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
FAILURES=0

check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok: $desc"; else echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); fi
}

# Run the production module against an isolated skill config. The shipped team
# config is intentionally unsafe-by-default and must remain that way.
PM_SANDBOX_RUNNER="$TMP/protected-agent-sandbox-runner"
printf '#!/bin/sh\nexit 0\n' > "$PM_SANDBOX_RUNNER"
chmod 700 "$PM_SANDBOX_RUNNER"
TEST_SKILL="$TMP/skill"
PM_LIFECYCLE_ROOT="$TMP/protected-lifecycle"
mkdir -m 700 "$PM_LIFECYCLE_ROOT"
mkdir -p "$TEST_SKILL/config" "$TEST_SKILL/teams"
cp "$ROOT/config/statuses.config.json" "$TEST_SKILL/config/statuses.config.json"
cp "$ROOT"/teams/*.md "$TEST_SKILL/teams/"
cat > "$TEST_SKILL/config/team.config.md" <<'EOF'
TEAMWORK_ROOT=.teamwork
AGENT_ENV_ALLOWLIST="PATH TMPDIR LANG LC_ALL TERM"
TRACKER_WRITERS=broker
WORKTREE_SETUP="test -f app.txt"
AGENT_SANDBOX_ENFORCED=true
AGENT_SANDBOX_RUNNER=__SANDBOX_RUNNER__
BROKER_LIFECYCLE_ROOT=__LIFECYCLE_ROOT__
VALIDATE_BUILD=null
VALIDATE_TEST="test -f app.txt"
VALIDATE_LINT=null
VALIDATE_FORMAT=null
VALIDATE_SCRIPT=null
EOF
sed -i '' "s|^AGENT_SANDBOX_RUNNER=.*|AGENT_SANDBOX_RUNNER=\"$PM_SANDBOX_RUNNER\"|" "$TEST_SKILL/config/team.config.md"
sed -i '' "s|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=\"$PM_LIFECYCLE_ROOT\"|" "$TEST_SKILL/config/team.config.md"
MONITOR="$TMP/pm-agent"
cat > "$MONITOR" <<'PY'
#!/usr/bin/env python3
import importlib.util
import os
import pathlib
import sys

spec = importlib.util.spec_from_file_location("startup_factory_pm_agent", os.environ["PM_TEST_MONITOR_IMPL"])
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.SKILL_DIR = pathlib.Path(os.environ["PM_TEST_SKILL_DIR"])
if os.environ.get("PM_TEST_RELEASE_HARNESS") == "1":
    def test_release_handoff(project, *, dry_run=False):
        return (
            [os.environ["STARTUP_FACTORY_RELEASE_FEATURE"]],
            None,
            dict(os.environ),
        )
    module.validate_release_handoff = test_release_handoff
try:
    raise SystemExit(module.main())
except module.MonitorError as exc:
    print(f"pm-agent: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
chmod +x "$MONITOR"
export PM_TEST_MONITOR_IMPL="$MONITOR_IMPL" PM_TEST_SKILL_DIR="$TEST_SKILL"

REPO="$TMP/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q -b main
git -C "$REPO" config user.email test@example.com
git -C "$REPO" config user.name Test
printf '.teamwork/\n' > "$REPO/.gitignore"
printf 'fixture\n' > "$REPO/app.txt"
git -C "$REPO" add .gitignore app.txt
git -C "$REPO" commit -qm init

CONFIG="$TMP/automation.json"
cat > "$CONFIG" <<'EOF'
{
  "schemaVersion": 1,
  "enabled": true,
  "scanIntervalMinutes": 1,
  "leaseSeconds": 10,
  "operationTimeoutSeconds": 30,
  "releaseTimeoutSeconds": 600,
  "maxFeaturesPerPass": 2,
  "requireAgentSandbox": true,
  "requireSingleTrackerWriter": true,
  "scanStatusKinds": ["queued", "blocked"],
  "reconcileRegisteredRuns": true,
  "baseRef": "main",
  "branchPrefix": "factory",
  "workspaceRoot": ".teamwork/pm-agent",
  "defaultTeamPreset": "full-stack",
  "allowedTeamPresets": ["full-stack", "deep-backend", "deep-frontend", "deep-infra", "deep-security"],
  "requireMetadataOptIn": true,
  "metadata": {"optInKey": "automation", "teamPresetKey": "team-preset"}
}
EOF

if env -u STARTUP_FACTORY_PROJECT_ROOT \
    STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" \
    "$MONITOR" --once >"$TMP/missing-root.out" 2>"$TMP/missing-root.err"; then
  echo "FAIL: missing explicit project root accepted"; FAILURES=$((FAILURES+1))
elif grep -q 'STARTUP_FACTORY_PROJECT_ROOT must name the absolute target checkout' "$TMP/missing-root.err"; then
  echo "ok: missing explicit project root fails closed"
else
  echo "FAIL: missing project root produced the wrong error"; FAILURES=$((FAILURES+1))
fi

# Reject a repository-authored config before its trustedPath can select or
# execute a repository-authored Git binary under the scheduler identity.
mkdir -p "$REPO/fake-bin"
cat > "$REPO/fake-bin/git" <<EOF
#!/usr/bin/env bash
touch "$TMP/repository-git-executed"
exit 1
EOF
chmod 700 "$REPO/fake-bin/git"
python3 - "$CONFIG" "$REPO/repository-automation.json" "$REPO/fake-bin" <<'PY'
import json,sys
data=json.load(open(sys.argv[1])); data['trustedPath']=sys.argv[3]
json.dump(data,open(sys.argv[2],'w'))
PY
if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" \
    STARTUP_FACTORY_AUTOMATION_CONFIG="$REPO/repository-automation.json" \
    "$MONITOR" --once >"$TMP/repository-config.out" 2>"$TMP/repository-config.err"; then
  echo "FAIL: repository-local automation config accepted"; FAILURES=$((FAILURES+1))
elif ! grep -q 'automation config must live outside the agent repository' "$TMP/repository-config.err"; then
  echo "FAIL: repository-local automation config produced the wrong error"; FAILURES=$((FAILURES+1))
elif [ -e "$TMP/repository-git-executed" ]; then
  echo "FAIL: repository-selected Git executed before config rejection"; FAILURES=$((FAILURES+1))
else
  echo "ok: repository-local config is rejected before trusted tool selection"
fi
rm -rf "$REPO/fake-bin" "$REPO/repository-automation.json"

PM_CONFIG="$TMP/project-management.config.md"
cat > "$PM_CONFIG" <<'EOF'
PRODUCT_MANAGEMENT_TOOL=Fake
TEAM_MODE=true
EOF

SCAN="$TMP/scan.json"
LOG="$TMP/ops.log"
FEATURE_STATE_FILE="$TMP/feature-state"
printf 'Resolved\n' > "$FEATURE_STATE_FILE"
cat > "$SCAN" <<'EOF'
{
  "schemaVersion": 1,
  "adapter": "Fake",
  "statuses": ["Planned", "Blocked"],
  "items": [{
    "featureId": "F-1",
    "featureTitle": "Payments API",
    "taskId": "T-1",
    "title": "Build endpoint",
    "status": "Planned",
    "statusRaw": "Todo",
    "description": "automation: enabled\nteam-preset: deep-frontend",
    "comments": [{"id":"C-1","body":"team-preset: deep-backend","createdAt":"2026-07-14T12:01:00Z"}],
    "blockedBy": [],
    "labels": [],
    "revision": "2026-07-14T12:00:00Z"
  }],
  "orphans": []
}
EOF

TRACKER="$TMP/tracker"
cat > "$TRACKER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  scan)
    printf 'scan\n' >> "$PM_TEST_LOG"
    [ -z "${PM_TEST_SCAN_SLEEP:-}" ] || sleep "$PM_TEST_SCAN_SLEEP"
    cp "$PM_SCAN_FILE" "$2"
    ;;
  export)
    if [ "${PM_EXPORT_MODE:-scan}" = "file" ]; then
      cp "$PM_EXPORT_FILE" "$3"
      exit 0
    fi
    python3 - "$PM_SCAN_FILE" "$2" "$3" "${PM_EXPORT_MODE:-scan}" <<'PY'
import json,sys
scan,feature,out,mode=sys.argv[1:]
items=[] if mode=='empty' else [i for i in json.load(open(scan)).get('items',[]) if str(i.get('featureId'))==feature]
json.dump({'schemaVersion':1,'featureId':feature,'adapter':'Fake','tasks':items},open(out,'w'))
PY
    ;;
  comment-once)
    printf 'comment-once\t%s\t%s\n' "$2" "$3" >> "$PM_TEST_LOG"
    printf 'comment-body\t' >> "$PM_TEST_LOG"
    tr '\n' ' ' < "$4" >> "$PM_TEST_LOG"
    printf '\n' >> "$PM_TEST_LOG"
    ;;
  feature-reopen)
    [ "${STARTUP_FACTORY_PM_SUPERVISOR:-}" = "1" ] || exit 11
    current="$(cat "$PM_FEATURE_STATE_FILE")"
    [ "$current" = "Resolved" ] || [ "$current" = "$3" ] || {
      echo "cannot reopen from $current" >&2; exit 12;
    }
    printf '%s\n' "$3" > "$PM_FEATURE_STATE_FILE"
    printf 'feature-reopen\t%s\t%s\t%s\n' "$2" "$current" "$3" >> "$PM_TEST_LOG"
    ;;
  *) echo "unexpected tracker op: $*" >&2; exit 1 ;;
esac
EOF
LAUNCH="$TMP/launch"
cat > "$LAUNCH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'launch\t%s\t%s\t%s\t%s\tcwd=%s\n' "$1" "$2" "$3" "$4" "$PWD" >> "$PM_TEST_LOG"
EOF
DISPATCH="$TMP/dispatch"
cat > "$DISPATCH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'dispatch\t%s\t%s\t%s\tcwd=%s\n' "$1" "$2" "$3" "$PWD" >> "$PM_TEST_LOG"
if [ "${PM_DISPATCH_FAIL:-0}" = "1" ]; then
  echo 'TOPSECRET_FROM_DISPATCH' >&2
  exit 9
fi
if [ "${PM_DISPATCH_COMPLETE:-0}" = "1" ]; then
  mkdir -p ".teamwork/$1"
  printf '{"schemaVersion":1,"featureId":"%s","tasks":[{"taskId":"DONE-1","status":"Ready to deploy"}]}\n' "$2" > ".teamwork/$1/tasks.json"
fi
EOF
FAKE_RELEASE="$TMP/release"
cat > "$FAKE_RELEASE" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'release\t%s\n' "$*" >> "$PM_TEST_LOG"
if [ "${PM_RELEASE_DISABLED:-0}" = "1" ]; then
  exit 4
fi
if [ "${PM_RELEASE_FAIL:-0}" = "1" ]; then
  echo 'TOPSECRET_FROM_RELEASE' >&2
  exit 9
fi
EOF
chmod +x "$TRACKER" "$LAUNCH" "$DISPATCH" "$FAKE_RELEASE"

monitor() {
  env STARTUP_FACTORY_PROJECT_ROOT="$REPO" \
      STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" \
      STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" \
      STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
      STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" \
      STARTUP_FACTORY_DISPATCH="$DISPATCH" \
      STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" \
      PM_TEST_RELEASE_HARNESS=1 PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
      PM_FEATURE_STATE_FILE="$FEATURE_STATE_FILE" \
      "$MONITOR" --once
}

preflight_refused() { # preflight_refused <name> <config> <skill-root> <needle>
  local name="$1" config="$2" skill="$3" needle="$4" out
  if out="$(env PM_TEST_SKILL_DIR="$skill" STARTUP_FACTORY_PROJECT_ROOT="$REPO" \
      STARTUP_FACTORY_AUTOMATION_CONFIG="$config" STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" \
      STARTUP_FACTORY_TRACKER_OPS="$TRACKER" STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" \
      STARTUP_FACTORY_DISPATCH="$DISPATCH" STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" \
      PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" "$MONITOR" --once 2>&1)"; then
    echo "FAIL: $name accepted"; FAILURES=$((FAILURES+1))
  elif printf '%s' "$out" | grep -q "$needle"; then
    echo "ok: $name fails closed"
  else
    echo "FAIL: $name wrong error: $out"; FAILURES=$((FAILURES+1))
  fi
}

python3 - "$CONFIG" "$TMP/no-sandbox-invariant.json" "$TMP/no-writer-invariant.json" <<'PY'
import json,sys
source,*targets=sys.argv[1:]
for target,key in zip(targets,("requireAgentSandbox","requireSingleTrackerWriter")):
    data=json.load(open(source)); data[key]=False
    json.dump(data,open(target,"w"))
PY
preflight_refused "requireAgentSandbox=false" "$TMP/no-sandbox-invariant.json" "$TEST_SKILL" "cannot be disabled"
preflight_refused "requireSingleTrackerWriter=false" "$TMP/no-writer-invariant.json" "$TEST_SKILL" "cannot be disabled"

NO_RUNNER_SKILL="$TMP/no-runner-skill"
LOCAL_RUNNER_SKILL="$TMP/local-runner-skill"
WRITABLE_RUNNER_SKILL="$TMP/writable-runner-skill"
cp -R "$TEST_SKILL" "$NO_RUNNER_SKILL"
cp -R "$TEST_SKILL" "$LOCAL_RUNNER_SKILL"
cp -R "$TEST_SKILL" "$WRITABLE_RUNNER_SKILL"
sed -i '' 's|^AGENT_SANDBOX_RUNNER=.*|AGENT_SANDBOX_RUNNER=null|' "$NO_RUNNER_SKILL/config/team.config.md"
LOCAL_AGENT_RUNNER="$REPO/repository-agent-sandbox-runner"
cp "$PM_SANDBOX_RUNNER" "$LOCAL_AGENT_RUNNER"
chmod 700 "$LOCAL_AGENT_RUNNER"
sed -i '' "s|^AGENT_SANDBOX_RUNNER=.*|AGENT_SANDBOX_RUNNER=\"$LOCAL_AGENT_RUNNER\"|" "$LOCAL_RUNNER_SKILL/config/team.config.md"
WRITABLE_AGENT_RUNNER="$TMP/writable-agent-sandbox-runner"
cp "$PM_SANDBOX_RUNNER" "$WRITABLE_AGENT_RUNNER"
chmod 722 "$WRITABLE_AGENT_RUNNER"
sed -i '' "s|^AGENT_SANDBOX_RUNNER=.*|AGENT_SANDBOX_RUNNER=\"$WRITABLE_AGENT_RUNNER\"|" "$WRITABLE_RUNNER_SKILL/config/team.config.md"
preflight_refused "missing protected agent runner" "$CONFIG" "$NO_RUNNER_SKILL" "AGENT_SANDBOX_RUNNER"
preflight_refused "repository-local agent runner" "$CONFIG" "$LOCAL_RUNNER_SKILL" "external to the agent repository"
preflight_refused "writable agent runner" "$CONFIG" "$WRITABLE_RUNNER_SKILL" "group- or world-writable"
rm "$LOCAL_AGENT_RUNNER"

NO_LIFECYCLE_SKILL="$TMP/no-lifecycle-skill"
LOCAL_LIFECYCLE_SKILL="$TMP/local-lifecycle-skill"
WRITABLE_LIFECYCLE_SKILL="$TMP/writable-lifecycle-skill"
SYMLINK_LIFECYCLE_SKILL="$TMP/symlink-lifecycle-skill"
cp -R "$TEST_SKILL" "$NO_LIFECYCLE_SKILL"
cp -R "$TEST_SKILL" "$LOCAL_LIFECYCLE_SKILL"
cp -R "$TEST_SKILL" "$WRITABLE_LIFECYCLE_SKILL"
cp -R "$TEST_SKILL" "$SYMLINK_LIFECYCLE_SKILL"
sed -i '' 's|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=null|' "$NO_LIFECYCLE_SKILL/config/team.config.md"
LOCAL_LIFECYCLE_ROOT="$REPO/repository-lifecycle"
mkdir -m 700 "$LOCAL_LIFECYCLE_ROOT"
sed -i '' "s|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=\"$LOCAL_LIFECYCLE_ROOT\"|" "$LOCAL_LIFECYCLE_SKILL/config/team.config.md"
WRITABLE_LIFECYCLE_ROOT="$TMP/writable-lifecycle"
mkdir -m 777 "$WRITABLE_LIFECYCLE_ROOT"
sed -i '' "s|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=\"$WRITABLE_LIFECYCLE_ROOT\"|" "$WRITABLE_LIFECYCLE_SKILL/config/team.config.md"
SYMLINK_LIFECYCLE_ROOT="$TMP/lifecycle-link"
ln -s "$PM_LIFECYCLE_ROOT" "$SYMLINK_LIFECYCLE_ROOT"
sed -i '' "s|^BROKER_LIFECYCLE_ROOT=.*|BROKER_LIFECYCLE_ROOT=\"$SYMLINK_LIFECYCLE_ROOT\"|" "$SYMLINK_LIFECYCLE_SKILL/config/team.config.md"
preflight_refused "missing protected lifecycle state" "$CONFIG" "$NO_LIFECYCLE_SKILL" "LIFECYCLE_ROOT"
preflight_refused "repository-local lifecycle state" "$CONFIG" "$LOCAL_LIFECYCLE_SKILL" "disjoint"
preflight_refused "writable lifecycle state" "$CONFIG" "$WRITABLE_LIFECYCLE_SKILL" "group/world-writable"
preflight_refused "symlink lifecycle state" "$CONFIG" "$SYMLINK_LIFECYCLE_SKILL" "non-symlink"
rm -rf "$LOCAL_LIFECYCLE_ROOT"

NO_SETUP_SKILL="$TMP/no-setup-skill"
NO_VALIDATION_SKILL="$TMP/no-validation-skill"
cp -R "$TEST_SKILL" "$NO_SETUP_SKILL"
cp -R "$TEST_SKILL" "$NO_VALIDATION_SKILL"
python3 - "$NO_SETUP_SKILL/config/team.config.md" "$NO_VALIDATION_SKILL/config/team.config.md" <<'PY'
import pathlib,re,sys
setup=pathlib.Path(sys.argv[1]); setup.write_text(re.sub(r'^WORKTREE_SETUP=.*$', 'WORKTREE_SETUP=null', setup.read_text(), flags=re.M))
validation=pathlib.Path(sys.argv[2]); text=validation.read_text()
text=re.sub(r'^VALIDATE_[A-Z_]+=.*$', lambda m: m.group(0).split('=',1)[0]+'=null', text, flags=re.M)
validation.write_text(text)
PY
preflight_refused "missing worktree provisioning" "$CONFIG" "$NO_SETUP_SKILL" "WORKTREE_SETUP"
preflight_refused "missing autonomous validation" "$CONFIG" "$NO_VALIDATION_SKILL" "VALIDATE_SCRIPT/BUILD/TEST/LINT/FORMAT"

DUPLICATE_TEAM_SKILL="$TMP/duplicate-team-skill"
cp -R "$TEST_SKILL" "$DUPLICATE_TEAM_SKILL"
printf 'TRACKER_WRITERS=all\n' >> "$DUPLICATE_TEAM_SKILL/config/team.config.md"
preflight_refused "duplicate safety configuration key" "$CONFIG" "$DUPLICATE_TEAM_SKILL" "duplicate configuration key TRACKER_WRITERS"

printf '#!/bin/sh\nexit 4\n' > "$REPO/repository-release-feature"
chmod +x "$REPO/repository-release-feature"
mkdir -m 700 "$TMP/repository-local-release-state"
cat > "$TMP/protected-deployment.json" <<EOF
{"schemaVersion":1,"enabled":true,"mode":"automatic","stateRoot":"$TMP/repository-local-release-state","timeoutsSeconds":{"plan":1,"apply":1,"status":1,"verify":1,"rollback":1,"verifyDelivery":1,"verifyApproval":1}}
EOF
chmod 600 "$TMP/protected-deployment.json"
if out="$(env PM_TEST_SKILL_DIR="$TEST_SKILL" STARTUP_FACTORY_PROJECT_ROOT="$REPO" \
    STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" \
    STARTUP_FACTORY_TRACKER_OPS="$TRACKER" STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" \
    STARTUP_FACTORY_DISPATCH="$DISPATCH" STARTUP_FACTORY_RELEASE_FEATURE="$REPO/repository-release-feature" \
    STARTUP_FACTORY_DEPLOYMENT_CONFIG="$TMP/protected-deployment.json" \
    PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" "$MONITOR" --once 2>&1)"; then
  echo "FAIL: repository-local enabled release executor accepted"; FAILURES=$((FAILURES+1))
elif printf '%s' "$out" | grep -q 'repository-local release executor'; then
  echo "ok: repository-local enabled release executor fails closed"
else
  echo "FAIL: wrong release handoff error: $out"; FAILURES=$((FAILURES+1))
fi
rm "$REPO/repository-release-feature"

check "external release handoff executes only an authenticated protected snapshot" \
  python3 - "$MONITOR_IMPL" "$ROOT" "$REPO" "$TMP/release-handoff" <<'PY'
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

implementation, source_raw, project_raw, fixture_raw = sys.argv[1:]
spec = importlib.util.spec_from_file_location("pm_agent_release_handoff", implementation)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
source, project, fixture = map(Path, (source_raw, project_raw, fixture_raw))
external = fixture / "external-skill"
state = fixture / "protected-state"
external.mkdir(parents=True)
state.mkdir(parents=True, mode=0o700)
state.chmod(0o700)
digests = {}
for name, relative in module.RELEASE_SNAPSHOT_FILES.items():
    destination = external / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / relative, destination)
    destination.chmod(0o600)
    digests[name] = "sha256:" + hashlib.sha256(destination.read_bytes()).hexdigest()
config = fixture / "deployment.json"
enabled_config = {
    "schemaVersion": 1,
    "enabled": True,
    "mode": "automatic",
    "stateRoot": str(state.resolve()),
    "timeoutsSeconds": {
        "plan": 1, "apply": 1, "status": 1, "verify": 1,
        "rollback": 1, "verifyDelivery": 1, "verifyApproval": 1,
    },
    "trustedCodeDigests": digests,
    "planningEnvironmentAllowlist": ["PATH", "LANG"],
    "trackerEnvironmentAllowlist": ["PATH", "TRACKER_ADAPTER"],
    "environmentAllowlist": ["PATH"],
}
config.write_text(json.dumps(enabled_config))
config.chmod(0o600)
os.environ["STARTUP_FACTORY_DEPLOYMENT_CONFIG"] = str(config.resolve())
os.environ["STARTUP_FACTORY_RELEASE_FEATURE"] = str(
    (external / "bin/release-feature.py").resolve()
)
os.environ["TRACKER_ADAPTER"] = "Fake"
os.environ["AWS_SECRET_ACCESS_KEY"] = "must-not-cross"

disabled = fixture / "disabled-deployment.json"
disabled.write_text(json.dumps({
    "schemaVersion": 1,
    "enabled": False,
    "planningEnvironmentAllowlist": [],
    "trackerEnvironmentAllowlist": [],
    "environmentAllowlist": [],
}))
disabled.chmod(0o600)
os.environ["STARTUP_FACTORY_DEPLOYMENT_CONFIG"] = str(disabled.resolve())
os.environ["STARTUP_FACTORY_RELEASE_FEATURE"] = str(fixture / "does-not-exist")
disabled_command, _, _ = module.validate_release_handoff(project)
assert disabled_command is None

dry_state = fixture / "dry-run-state-must-not-exist"
dry_config = fixture / "dry-run-deployment.json"
dry_payload = dict(enabled_config)
dry_payload["stateRoot"] = str(dry_state.resolve())
dry_config.write_text(json.dumps(dry_payload))
dry_config.chmod(0o600)
os.environ["STARTUP_FACTORY_DEPLOYMENT_CONFIG"] = str(dry_config.resolve())
os.environ["STARTUP_FACTORY_RELEASE_FEATURE"] = str(
    (external / "bin/release-feature.py").resolve()
)
dry_command, _, _ = module.validate_release_handoff(project, dry_run=True)
assert dry_command and not dry_state.exists()

unsafe_dry_config = fixture / "unsafe-dry-run-deployment.json"
unsafe_payload = dict(enabled_config)
unsafe_payload["stateRoot"] = str(project / ".agent-controlled-release-state")
unsafe_dry_config.write_text(json.dumps(unsafe_payload))
unsafe_dry_config.chmod(0o600)
os.environ["STARTUP_FACTORY_DEPLOYMENT_CONFIG"] = str(unsafe_dry_config.resolve())
try:
    module.validate_release_handoff(project, dry_run=True)
except module.MonitorError as exc:
    assert "stateRoot must live outside" in str(exc)
else:
    raise AssertionError("dry-run accepted an agent-repository stateRoot")

os.environ["STARTUP_FACTORY_DEPLOYMENT_CONFIG"] = str(config.resolve())
command, protected_config, child = module.validate_release_handoff(project)
assert command and Path(command[0]).resolve() == Path(sys.executable).resolve()
assert "-I" in command and "-S" in command and "-E" in command
snapshot_entrypoint = Path(command[-1])
assert snapshot_entrypoint.is_file() and state.resolve() in snapshot_entrypoint.parents
assert Path(protected_config).read_bytes() == config.read_bytes()
assert child["TRACKER_ADAPTER"] == "Fake"
assert "AWS_SECRET_ACCESS_KEY" not in child
captured = snapshot_entrypoint.read_bytes()
(external / "bin/release-feature.py").write_text("tampered after authentication\n")
assert snapshot_entrypoint.read_bytes() == captured
try:
    module.validate_release_handoff(project)
except module.MonitorError as exc:
    assert "digest does not match" in str(exc)
else:
    raise AssertionError("mutated external executor was accepted")
PY

mkdir -p "$REPO/.teamwork/sibling-root"
ln -s sibling-root "$REPO/.teamwork/symlink-root"
python3 - "$CONFIG" "$TMP/symlink-root.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['workspaceRoot']='.teamwork/symlink-root'
json.dump(d,open(sys.argv[2],'w'))
PY
preflight_refused "symlinked automation workspace" "$TMP/symlink-root.json" "$TEST_SKILL" "must not traverse symlinks"
check "preflight refusals never scan the tracker" test ! -s "$LOG"

monitor >/dev/null
STATE="$REPO/.teamwork/pm-agent/state.json"
check "Todo/queued item creates one durable run" python3 - "$STATE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); r=d['features']['F-1']
assert r['preset']=='deep-backend' and r['state']=='running'
assert r['team'].startswith('factory-payments-api-')
assert r['launchedAt']
assert len(r['baseCommit']) == 40 and r['baseCommit'] == r['baseCommit'].lower()
PY
RUN_REPO="$(python3 -c "import json; print(json.load(open('$STATE'))['features']['F-1']['repository'])")"
TEAM="$(python3 -c "import json; print(json.load(open('$STATE'))['features']['F-1']['team'])")"
check "feature gets an isolated integration worktree" test -e "$RUN_REPO/.git"
check "integration worktree is on generated feature branch" test "$(git -C "$RUN_REPO" branch --show-current)" = "$TEAM"
check "integration worktree rejects impostor Git provenance" \
  python3 - "$MONITOR_IMPL" "$REPO" "$RUN_REPO" "$TEAM" "$TMP/worktree-provenance" <<'PY'
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

implementation, project_raw, genuine_raw, team, probe_raw = sys.argv[1:]
spec = importlib.util.spec_from_file_location("pm_agent_worktree_provenance", implementation)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
project, genuine, probe = map(Path, (project_raw, genuine_raw, probe_raw))
probe.mkdir(parents=True)
base_commit = subprocess.check_output(
    ["git", "rev-parse", "main^{commit}"], cwd=project, text=True
).strip()

def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

git(project, "branch", "factory-prepositioned", base_commit)
try:
    module.ensure_worktree(
        project,
        probe / "prepositioned-run",
        "factory-prepositioned",
        "main",
        base_commit,
        dict(os.environ),
    )
except module.MonitorError as exc:
    assert "pre-existing unregistered feature branch" in str(exc)
else:
    raise AssertionError("pre-positioned feature branch was accepted as a new autonomous run")

independent = probe / "independent"
independent.mkdir()
git(independent, "init", "-q", "-b", team)
git(independent, "config", "user.email", "test@example.com")
git(independent, "config", "user.name", "Test")
(independent / "impostor.txt").write_text("independent repository\n")
git(independent, "add", "impostor.txt")
git(independent, "commit", "-qm", "impostor")
try:
    module.ensure_worktree(project, independent, team, "main", base_commit, dict(os.environ))
except module.MonitorError as exc:
    assert "common directory" in str(exc)
else:
    raise AssertionError("independent repository with a matching branch was accepted")

# Reusing a genuine linked worktree's .git pointer proves common-directory,
# branch, and HEAD equality, but the forged path is not a registered worktree.
unregistered = probe / "unregistered"
unregistered.mkdir()
(unregistered / ".git").write_text((genuine / ".git").read_text())
(unregistered / "impostor.txt").write_text("unregistered worktree path\n")
try:
    module.ensure_worktree(project, unregistered, team, "main", base_commit, dict(os.environ))
except module.MonitorError as exc:
    assert "worktree root" in str(exc) or "not registered" in str(exc)
else:
    raise AssertionError("unregistered path reusing genuine worktree metadata was accepted")
PY
check "latest metadata occurrence selects the proper preset" grep -q $'launch\tgate-team\tdeep-backend' "$LOG"
check "automation bootstraps gates rather than a full long-lived roster" test "$(grep -c $'^launch\tgate-team' "$LOG")" -eq 1
check "one per-feature dispatch pass runs" test "$(grep -c '^dispatch' "$LOG")" -eq 1

monitor >/dev/null
check "second tick does not relaunch initialized team" test "$(grep -c '^launch' "$LOG")" -eq 1
check "second tick still reconciles registered run" test "$(grep -c '^dispatch' "$LOG")" -eq 2

# A registry entry is not a standing authorization. Every pass re-proves that
# the feature is still in scope, opted in, and bound to its original preset.
dispatch_before="$(grep -c '^dispatch' "$LOG")"
ACTIVE_EXPORT="$TMP/active-export.json"
cat > "$ACTIVE_EXPORT" <<'EOF'
{"schemaVersion":1,"featureId":"F-1","adapter":"Fake","tasks":[{"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1","title":"Build endpoint","status":"Active","statusRaw":"In Progress","description":"automation: enabled\nteam-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"2"}]}
EOF
cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[],"orphans":[]}
EOF
export PM_EXPORT_MODE=file PM_EXPORT_FILE="$ACTIVE_EXPORT"
monitor >/dev/null
unset PM_EXPORT_MODE PM_EXPORT_FILE
check "active feature outside discovery statuses remains authorized by exhaustive export" test "$(grep -c '^dispatch' "$LOG")" -eq "$((dispatch_before + 1))"
dispatch_before="$(grep -c '^dispatch' "$LOG")"
export PM_EXPORT_MODE=empty
monitor >/dev/null
unset PM_EXPORT_MODE
check "registered feature missing from authoritative export is paused" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='paused' and r['eligibility']=='out-of-scope'
PY
check "out-of-scope run is never reconciled" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"

cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1","title":"Build endpoint","status":"Planned","statusRaw":"Todo","description":"team-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"2"}],"orphans":[]}
EOF
monitor >/dev/null
check "registered feature missing required opt-in remains paused" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='paused' and r['eligibility']=='paused' and 'opt-in' in r['pauseReason']
PY
check "missing opt-in run is never reconciled" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"

cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1","title":"Build endpoint","status":"Planned","statusRaw":"Todo","description":"automation: disabled\nteam-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"3"}],"orphans":[]}
EOF
monitor >/dev/null
check "explicit automation disable pauses a registered feature" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='paused' and 'disabled' in r['pauseReason']
PY
check "disabled run is never reconciled" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"

cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[
  {"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1a","title":"A","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"4"},
  {"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1b","title":"B","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-frontend","comments":[],"blockedBy":[],"labels":[],"revision":"4"}
],"orphans":[]}
EOF
monitor >/dev/null
check "conflicting latest routing pauses a registered feature" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='paused' and 'conflicting' in r['pauseReason']
PY
check "routing-conflicted run is never reconciled" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"

cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1","title":"Build endpoint","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-frontend","comments":[],"blockedBy":[],"labels":[],"revision":"5"}],"orphans":[]}
EOF
PM_DISPATCH_COMPLETE=1 monitor >/dev/null
check "preset drift pauses without mutating the immutable route" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='paused' and r['preset']=='deep-backend' and 'changed' in r['pauseReason']
PY
check "paused preset drift cannot dispatch" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"
if grep '^release' "$LOG" | grep -q -- '--feature F-1'; then
  echo "FAIL: paused preset drift reached production release"; FAILURES=$((FAILURES+1))
else
  echo "ok: paused preset drift cannot reach production release"
fi

cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-1","featureTitle":"Payments API","taskId":"T-1","title":"Build endpoint","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"6"}],"orphans":[]}
EOF
monitor >/dev/null
check "fresh matching scope and original preset resume the run" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-1']
assert r['state']=='running' and r['eligibility']=='eligible'
PY
check "resumed run reconciles exactly once" test "$(grep -c '^dispatch' "$LOG")" -eq "$((dispatch_before + 1))"

# Recovery reconciliation is an explicit switch. A newly discovered run is
# bootstrapped in its discovery pass, but an existing run is skipped when off.
python3 - "$CONFIG" "$TMP/no-reconcile.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['reconcileRegisteredRuns']=False
json.dump(d,open(sys.argv[2],'w'))
PY
dispatch_before="$(grep -c '^dispatch' "$LOG")"
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" \
    STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/no-reconcile.json" \
    STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
    STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
    STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
    "$MONITOR" --once > "$TMP/no-reconcile.out"
check "reconcileRegisteredRuns=false skips durable prior runs" test "$(grep -c '^dispatch' "$LOG")" -eq "$dispatch_before"
check "disabled recovery is reported" grep -q 'reconcileRegisteredRuns=false' "$TMP/no-reconcile.out"

# Conflicting routing fails closed; Blocked is routed to a lead-owning team; orphan is escalated.
cat > "$SCAN" <<'EOF'
{
  "schemaVersion": 1,
  "adapter": "Fake",
  "statuses": ["Planned", "Blocked"],
  "items": [
    {"featureId":"F-2","featureTitle":"Ambiguous","taskId":"T-2a","title":"A","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-backend","comments":[],"blockedBy":[],"labels":[],"revision":"1"},
    {"featureId":"F-2","featureTitle":"Ambiguous","taskId":"T-2b","title":"B","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: deep-frontend","comments":[],"blockedBy":[],"labels":[],"revision":"1"},
    {"featureId":"F-SAME-REV","featureTitle":"Same revision conflict","taskId":"T-SAME-REV","title":"No array-order authority","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[{"id":"C-SAME-1","body":"team-preset: deep-backend","createdAt":"2026-07-14T12:00:00Z"},{"id":"C-SAME-2","body":"team-preset: deep-frontend","createdAt":"2026-07-14T12:00:00Z"}],"blockedBy":[],"labels":[],"revision":"2"},
    {"featureId":"F-UNKNOWN","featureTitle":"Unknown opt-in","taskId":"T-UNKNOWN","title":"No guess","status":"Planned","statusRaw":"Todo","description":"automation: perhaps\nteam-preset: full-stack","comments":[],"blockedBy":[],"labels":[],"revision":"2"},
    {"featureId":"../../evil;touch-pwn","featureTitle":"Dependency recovery","taskId":"T-3","title":"Unblock","status":"Blocked","statusRaw":"Blocked","description":"automation: enabled\nteam-preset: full-stack","comments":[],"blockedBy":["T-0"],"labels":[],"revision":"1"}
  ],
  "orphans": [
    {"featureId":null,"taskId":"ORPHAN-1","title":"No parent","status":"Planned","statusRaw":"Todo","description":"","comments":[],"blockedBy":[],"labels":[],"revision":"1"}
  ]
}
EOF
monitor >/dev/null
check "conflicting team metadata never creates a run" python3 - "$STATE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))['features']; assert 'F-2' not in d
PY
check "routing conflict posts one idempotent escalation" grep -q $'comment-once\tT-2a\tpm-route-' "$LOG"
check "same-task same-revision conflict never creates a run" python3 - "$STATE" <<'PY'
import json,sys
assert 'F-SAME-REV' not in json.load(open(sys.argv[1]))['features']
PY
check "same-task same-revision conflict is escalated" grep -q $'comment-once\tT-SAME-REV\tpm-route-' "$LOG"
check "same-revision identical metadata remains valid" python3 - "$MONITOR_IMPL" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location('pm_agent_duplicate_test',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
items=[{'description':'team-preset: full-stack','comments':[
    {'body':'team-preset: deep-backend','createdAt':'2026-07-14T12:00:00Z'},
    {'body':'team-preset: deep-backend','createdAt':'2026-07-14T12:00:00Z'},
]}]
assert module.latest_metadata(items,'team-preset') == ('deep-backend',None)
PY
check "unknown automation metadata fails closed" python3 - "$STATE" <<'PY'
import json,sys
assert 'F-UNKNOWN' not in json.load(open(sys.argv[1]))['features']
PY
check "unknown automation metadata is escalated" grep -q $'comment-once\tT-UNKNOWN\tpm-route-' "$LOG"
check "orphan task is quarantined and escalated" grep -q $'comment-once\tORPHAN-1\tpm-orphan-' "$LOG"
check "Blocked feature is registered for lead reconciliation" python3 - "$STATE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))['features']; r=d['../../evil;touch-pwn']
assert r['preset']=='full-stack' and r['state']=='running'
assert '..' not in r['team'] and '/' not in r['team'] and ';' not in r['team']
PY
check "untrusted feature id cannot escape into a path" test ! -e "$TMP/evil"

comments_before="$(grep -c '^comment-once' "$LOG")"
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" \
    STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
    STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
    STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
    "$MONITOR" --once --dry-run > "$TMP/dry-run.out"
check "dry-run reports orphan quarantine/escalation" grep -q 'plan: would quarantine and escalate orphan task ORPHAN-1' "$TMP/dry-run.out"
check "dry-run reports routing escalation" grep -q 'plan: would post routing escalation for feature F-2' "$TMP/dry-run.out"
check "dry-run never writes tracker comments" test "$(grep -c '^comment-once' "$LOG")" -eq "$comments_before"

# One host, two overlapping cron ticks: the live lease permits only one scan.
: > "$LOG"
PM_TEST_SCAN_SLEEP=1 monitor >"$TMP/first.out" 2>"$TMP/first.err" & first=$!
sleep 0.1
PM_TEST_SCAN_SLEEP=1 monitor >"$TMP/second.out" 2>"$TMP/second.err" & second=$!
wait "$first"; wait "$second"
check "overlapping ticks execute exactly one scan" test "$(grep -c '^scan' "$LOG")" -eq 1
check "overlapping tick reports live lease" grep -q 'another live pass owns' "$TMP/second.out"

# The same reconciliation pass hands an all-integrated [feature] to the isolated
# release executor and records completion without giving deployment credentials
# to a worker agent.
cat > "$SCAN" <<'EOF'
{
  "schemaVersion":1,
  "adapter":"Fake",
  "statuses":["Planned","Blocked"],
  "items":[{"featureId":"F-4","featureTitle":"Production handoff","taskId":"T-4","title":"Ship","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[],"blockedBy":[],"labels":[],"revision":"1"}],
  "orphans":[]
}
EOF
PM_DISPATCH_COMPLETE=1 monitor >/dev/null
check "integrated feature is handed to release executor" grep -q $'release\t.*--feature F-4' "$LOG"
check "release handoff binds Git common/worktree directory identities" \
  grep -q -- '--expected-git-dir .*--expected-git-dir-id [0-9].*--expected-git-common-dir .*--expected-git-common-dir-id [0-9]' "$LOG"
check "successful release handoff closes portfolio run" python3 - "$STATE" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['features']['F-4']['state']=='deployed'
PY

# A deployed feature is not a permanent tombstone. If its board exposes a new
# or reopened queued/blocked task, the next pass creates a new execution/team
# namespace while preserving the prior workspace and its historical evidence.
read -r f4_repo f4_team <<EOF
$(python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-4']; print(r['repository'], r['team'])
PY
)
EOF
mkdir -p "$f4_repo/.teamwork/$f4_team/integrations"
printf 'prior-generation-evidence\n' > "$f4_repo/.teamwork/$f4_team/integrations/prior.sentinel"
printf 'Resolved\n' > "$FEATURE_STATE_FILE"
PM_DISPATCH_COMPLETE=1 monitor >/dev/null
check "new actionable work reopens a deployed feature as a new generation" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-4']
assert r['generation']==2 and r['state']=='deployed'
assert r['history'][-1]['generation']==1 and r['history'][-1]['state']=='deployed'
assert r['runId'] != r['history'][-1]['runId'] and r['team'] != r['history'][-1]['team']
assert r['workspaceId'] == r['history'][-1]['workspaceId']
assert r['startCommit'] == r['history'][-1]['headCommit']
assert r['predecessorTeam'] == r['history'][-1]['team']
PY
check "reopened generation preserves prior integration evidence" \
  grep -q prior-generation-evidence "$f4_repo/.teamwork/$f4_team/integrations/prior.sentinel"
f4_generation2_team="$(python3 -c "import json; print(json.load(open('$STATE'))['features']['F-4']['team'])")"
check "reopened generation has a fresh team workspace" \
  test ! -e "$f4_repo/.teamwork/$f4_generation2_team/integrations/prior.sentinel"
check "reopened generation launches and dispatches under its independent identity" \
  grep -q $'launch\tgate-team\tfull-stack\t'"$f4_generation2_team"$'\tF-4' "$LOG"
check "reopened generation switches the integration worktree to its own branch" \
  test "$(git -C "$f4_repo" branch --show-current)" = "$f4_generation2_team"
check "terminal feature container is explicitly reopened before generation dispatch" \
  grep -q $'feature-reopen\tF-4\tResolved\tPlanned' "$LOG"
check "feature reopen mutation is read back in the fake remote adapter" \
  grep -qx 'Planned' "$FEATURE_STATE_FILE"

cat > "$SCAN" <<'EOF'
{
  "schemaVersion":1,
  "adapter":"Fake",
  "statuses":["Planned","Blocked"],
  "items":[{"featureId":"F-DEPLOYMENT-OFF","featureTitle":"Visible release queue","taskId":"T-DEPLOYMENT-OFF","title":"Ship later","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[],"blockedBy":[],"labels":[],"revision":"1"}],
  "orphans":[]
}
EOF
release_before="$(grep -c '^release' "$LOG" || true)"
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" \
    STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
    STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
    STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_DISPATCH_COMPLETE=1 \
    PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" "$MONITOR" --once >/dev/null
check "missing external deployment trust stays visibly awaiting deployment" python3 - "$STATE" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))['features']['F-DEPLOYMENT-OFF']
assert r['state']=='awaiting-deployment' and not r.get('deployedAt')
PY
check "missing external trust starts no release executor" test "$(grep -c '^release' "$LOG" || true)" -eq "$release_before"
check "unconfigured deployment is not recorded as deployed" python3 - "$STATE" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['features']['F-DEPLOYMENT-OFF']['state'] != 'deployed'
PY

# Every remote adapter must have an explicit portfolio boundary. Inference from
# a git remote or account-wide API access is forbidden for unattended scans.
scope_refused() { # scope_refused <name> <needle> <config-text>
  local name="$1" needle="$2" text="$3" path="$TMP/scope-$1.md" out
  printf '%s\n' "$text" > "$path"
  if out="$(env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$CONFIG" \
      STARTUP_FACTORY_PM_CONFIG="$path" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
      STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
      STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
      "$MONITOR" --once 2>&1)"; then
    echo "FAIL: $name remote scope accepted"; FAILURES=$((FAILURES+1))
  elif printf '%s' "$out" | grep -q "$needle"; then
    echo "ok: $name remote scope fails closed"
  else
    echo "FAIL: $name wrong scope error: $out"; FAILURES=$((FAILURES+1))
  fi
}
scope_refused linear LINEAR_DEFAULT_TEAM $'PRODUCT_MANAGEMENT_TOOL=Linear\nTEAM_MODE=true\nLINEAR_ACCESS=rest\nLINEAR_DEFAULT_TEAM=null'
scope_refused jira JIRA_PROJECT_KEY $'PRODUCT_MANAGEMENT_TOOL=Jira\nTEAM_MODE=true\nJIRA_ACCESS=rest\nJIRA_PROJECT_KEY=null'
scope_refused github GITHUB_REPO $'PRODUCT_MANAGEMENT_TOOL=GitHubIssues\nTEAM_MODE=true\nGITHUB_USE_MCP=false\nGITHUB_REPO=null'

# Comment-based routing changes need their own sortable evidence. Falling back
# to an issue's generic updatedAt would let an unrelated status edit make stale
# description metadata appear newer than a comment.
NO_TIME_CONFIG="$TMP/no-time-config.json"
python3 - "$CONFIG" "$NO_TIME_CONFIG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['workspaceRoot']='.teamwork/no-time-routing'
json.dump(d,open(sys.argv[2],'w'))
PY
cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-NO-TIME","featureTitle":"Unordered metadata","taskId":"T-NO-TIME","title":"No timestamp","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[{"id":"C-NO-TIME","body":"team-preset: deep-backend"}],"blockedBy":[],"labels":[],"revision":"99"}],"orphans":[]}
EOF
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$NO_TIME_CONFIG" \
  STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
  STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
  STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
  "$MONITOR" --once >/dev/null
check "unordered routing comment never creates a run" python3 - "$REPO/.teamwork/no-time-routing/state.json" <<'PY'
import json,sys
assert 'F-NO-TIME' not in json.load(open(sys.argv[1]))['features']
PY
check "unordered routing comment is escalated" grep -q $'comment-once\tT-NO-TIME\tpm-route-' "$LOG"

EDIT_TIME_CONFIG="$TMP/edit-time-config.json"
python3 - "$CONFIG" "$EDIT_TIME_CONFIG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['workspaceRoot']='.teamwork/edit-time-routing'
json.dump(d,open(sys.argv[2],'w'))
PY
cat > "$SCAN" <<'EOF'
{"schemaVersion":1,"adapter":"Fake","statuses":["Planned","Blocked"],"items":[{"featureId":"F-EDIT-TIME","featureTitle":"Edited routing metadata","taskId":"T-EDIT-TIME","title":"Route latest edit","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[{"id":"C-OLD-EDITED","body":"team-preset: deep-backend","createdAt":"2026-07-14T12:00:00Z","updatedAt":"2026-07-14T15:00:00Z"},{"id":"C-NEWER-CREATED","body":"team-preset: deep-frontend","createdAt":"2026-07-14T13:00:00Z","updatedAt":"2026-07-14T13:00:00Z"}],"blockedBy":[],"labels":[],"revision":"100"}],"orphans":[]}
EOF
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$EDIT_TIME_CONFIG" \
  STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" STARTUP_FACTORY_TRACKER_OPS="$TRACKER" \
  STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" STARTUP_FACTORY_DISPATCH="$DISPATCH" \
  STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
  "$MONITOR" --once >/dev/null
check "edited routing comment is ordered by updatedAt" python3 - "$REPO/.teamwork/edit-time-routing/state.json" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['features']['F-EDIT-TIME']['preset']=='deep-backend'
PY

# Cron output creates the private log parent before shell redirection, so the
# first scheduled tick works on a clean checkout.
python3 - "$CONFIG" "$TMP/cron-config.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['workspaceRoot']='.teamwork/fresh-cron'
json.dump(d,open(sys.argv[2],'w'))
PY
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/cron-config.json" \
  "$MONITOR" --print-cron > "$TMP/cron.out"
check "printed cron creates missing workspace before redirect" grep -q 'mkdir -p .*/.teamwork/fresh-cron' "$TMP/cron.out"
check "printed cron fixes the protected tool path" grep -q 'PATH=/usr/bin' "$TMP/cron.out"
REPO_CANON="$(cd "$REPO" && pwd -P)"
CRON_CONFIG_CANON="$(cd "$TMP" && pwd -P)/cron-config.json"
check "printed cron pins the target checkout" grep -q "STARTUP_FACTORY_PROJECT_ROOT=$REPO_CANON" "$TMP/cron.out"
check "printed cron pins the protected config" grep -q "STARTUP_FACTORY_AUTOMATION_CONFIG=$CRON_CONFIG_CANON" "$TMP/cron.out"
check "printed cron pins protected lifecycle authority" grep -q "STARTUP_FACTORY_LIFECYCLE_STATE_ROOT=$PM_LIFECYCLE_ROOT" "$TMP/cron.out"
check "printed cron isolates Python startup" grep -q -- '-I -S -E -s' "$TMP/cron.out"
check "printing cron does not mutate the checkout" test ! -e "$REPO/.teamwork/fresh-cron"

python3 - "$TMP/cron-config.json" "$TMP/default-cron-config.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d.pop('scanIntervalMinutes',None); d.pop('pollSeconds',None)
json.dump(d,open(sys.argv[2],'w'))
PY
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/default-cron-config.json" \
  "$MONITOR" --print-cron > "$TMP/default-cron.out"
check "default board scan runs every three minutes" grep -q '^\*/3 \* \* \* \* ' "$TMP/default-cron.out"

python3 - "$TMP/cron-config.json" "$TMP/hourly-cron-config.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['scanIntervalMinutes']=60
json.dump(d,open(sys.argv[2],'w'))
PY
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/hourly-cron-config.json" \
  "$MONITOR" --print-cron > "$TMP/hourly-cron.out"
check "hourly polling renders an exact cron cadence" grep -q '^0 \* \* \* \* ' "$TMP/hourly-cron.out"

python3 - "$TMP/cron-config.json" "$TMP/seven-minute-cron-config.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['scanIntervalMinutes']=7
json.dump(d,open(sys.argv[2],'w'))
PY
if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/seven-minute-cron-config.json" \
    "$MONITOR" --print-cron > "$TMP/seven-minute-cron.out" 2> "$TMP/seven-minute-cron.err"; then
  echo "FAIL: seven-minute interval should not render a drifting cron expression"; FAILURES=$((FAILURES+1))
elif grep -q 'stable cron cadence' "$TMP/seven-minute-cron.err"; then
  echo "ok: seven-minute interval is rejected as inexact"
else
  echo "FAIL: seven-minute interval produced the wrong error"; FAILURES=$((FAILURES+1))
fi

for invalid in zero fractional; do
  python3 - "$TMP/cron-config.json" "$TMP/$invalid-cron-config.json" "$invalid" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
d['scanIntervalMinutes'] = 0 if sys.argv[3] == 'zero' else 3.5
json.dump(d,open(sys.argv[2],'w'))
PY
  if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/$invalid-cron-config.json" \
      "$MONITOR" --print-cron > "$TMP/$invalid-cron.out" 2> "$TMP/$invalid-cron.err"; then
    echo "FAIL: $invalid scanIntervalMinutes should fail closed"; FAILURES=$((FAILURES+1))
  elif grep -q 'scanIntervalMinutes must be an integer' "$TMP/$invalid-cron.err"; then
    echo "ok: $invalid scanIntervalMinutes fails closed"
  else
    echo "FAIL: $invalid scanIntervalMinutes produced the wrong error"; FAILURES=$((FAILURES+1))
  fi
done

cat > "$TMP/duplicate-cron-config.json" <<'EOF'
{"schemaVersion":1,"scanIntervalMinutes":3,"scanIntervalMinutes":4,"workspaceRoot":".teamwork/cron"}
EOF
if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/duplicate-cron-config.json" \
    "$MONITOR" --print-cron > "$TMP/duplicate-cron.out" 2> "$TMP/duplicate-cron.err"; then
  echo "FAIL: duplicate automation JSON key should fail closed"; FAILURES=$((FAILURES+1))
elif grep -q 'duplicate JSON key' "$TMP/duplicate-cron.err"; then
  echo "ok: duplicate automation JSON key fails closed"
else
  echo "FAIL: duplicate automation JSON key produced the wrong error"; FAILURES=$((FAILURES+1))
fi

python3 - "$TMP/cron-config.json" "$TMP/legacy-cron-config.json" "$TMP/conflicting-cron-config.json" <<'PY'
import json,sys
source=json.load(open(sys.argv[1]))
legacy=dict(source); legacy.pop('scanIntervalMinutes',None); legacy['pollSeconds']=60
json.dump(legacy,open(sys.argv[2],'w'))
conflicting=dict(source); conflicting['pollSeconds']=60
json.dump(conflicting,open(sys.argv[3],'w'))
PY
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/legacy-cron-config.json" \
  "$MONITOR" --print-cron > "$TMP/legacy-cron.out"
check "legacy pollSeconds remains supported when minute config is absent" grep -q '^\* \* \* \* \* ' "$TMP/legacy-cron.out"
if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/conflicting-cron-config.json" \
    "$MONITOR" --print-cron > "$TMP/conflicting-cron.out" 2> "$TMP/conflicting-cron.err"; then
  echo "FAIL: conflicting minute and second scan intervals should fail closed"; FAILURES=$((FAILURES+1))
elif grep -q 'set only scanIntervalMinutes' "$TMP/conflicting-cron.err"; then
  echo "ok: conflicting scan interval settings fail closed"
else
  echo "FAIL: conflicting scan interval settings produced the wrong error"; FAILURES=$((FAILURES+1))
fi

# A failed release is reported with a stable generic escalation. Raw subprocess
# output is retained only in protected scheduler logs, never copied to tracker.
cat > "$SCAN" <<'EOF'
{
  "schemaVersion":1,
  "adapter":"Fake",
  "statuses":["Planned","Blocked"],
  "items":[{"featureId":"F-RELEASE-FAIL","featureTitle":"Release failure","taskId":"T-RELEASE-FAIL","title":"Ship","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[],"blockedBy":[],"labels":[],"revision":"4"}],
  "orphans":[]
}
EOF
if PM_DISPATCH_COMPLETE=1 PM_RELEASE_FAIL=1 monitor >"$TMP/release-fail.out" 2>"$TMP/release-fail.err"; then
  echo "FAIL: release failure should fail the supervisor pass"; FAILURES=$((FAILURES+1))
else
  echo "ok: release failure fails the supervisor pass"
fi
check "release failure posts tracker-visible escalation" grep -q $'comment-once\tT-RELEASE-FAIL\tpm-run-failure-' "$LOG"
check "release escalation names only the sanitized failure class" grep -q 'comment-body.*production release handoff failed' "$LOG"
if grep '^comment-body' "$LOG" | grep -q 'TOPSECRET_FROM_RELEASE'; then
  echo "FAIL: raw release output leaked into tracker escalation"; FAILURES=$((FAILURES+1))
else
  echo "ok: raw release output stays out of tracker escalation"
fi

cat > "$SCAN" <<'EOF'
{
  "schemaVersion":1,
  "adapter":"Fake",
  "statuses":["Planned","Blocked"],
  "items":[{"featureId":"F-SUPERVISOR-FAIL","featureTitle":"Supervisor failure","taskId":"T-SUPERVISOR-FAIL","title":"Reconcile","status":"Planned","statusRaw":"Todo","description":"automation: enabled\nteam-preset: full-stack","comments":[],"blockedBy":[],"labels":[],"revision":"5"}],
  "orphans":[]
}
EOF
if PM_DISPATCH_FAIL=1 monitor >"$TMP/supervisor-fail.out" 2>"$TMP/supervisor-fail.err"; then
  echo "FAIL: supervisor reconciliation failure should fail the pass"; FAILURES=$((FAILURES+1))
else
  echo "ok: supervisor reconciliation failure fails the pass"
fi
check "supervisor failure posts tracker-visible escalation" grep -q $'comment-once\tT-SUPERVISOR-FAIL\tpm-run-failure-' "$LOG"
check "supervisor escalation names only the sanitized failure class" grep -q 'comment-body.*deterministic supervisor reconciliation failed' "$LOG"
if grep '^comment-body' "$LOG" | grep -q 'TOPSECRET_FROM_DISPATCH'; then
  echo "FAIL: raw dispatcher output leaked into tracker escalation"; FAILURES=$((FAILURES+1))
else
  echo "ok: raw dispatcher output stays out of tracker escalation"
fi

# Kill switch is a clean no-op and does not contact the tracker.
python3 - "$CONFIG" "$TMP/disabled.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['enabled']=False
json.dump(d,open(sys.argv[2],'w'))
PY
: > "$LOG"
env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/disabled.json" \
    STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" \
    STARTUP_FACTORY_TRACKER_OPS="$TRACKER" STARTUP_FACTORY_LAUNCH_TEAM="$LAUNCH" \
    STARTUP_FACTORY_DISPATCH="$DISPATCH" STARTUP_FACTORY_RELEASE_FEATURE="$FAKE_RELEASE" \
    PM_SCAN_FILE="$SCAN" PM_TEST_LOG="$LOG" \
    "$MONITOR" --once > "$TMP/disabled.out"
check "disabled supervisor is a clean no-op" grep -q 'disabled' "$TMP/disabled.out"
check "disabled supervisor performs no scan" test ! -s "$LOG"

python3 - "$CONFIG" "$TMP/string-enabled.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['enabled']='false'
json.dump(d,open(sys.argv[2],'w'))
PY
if env STARTUP_FACTORY_PROJECT_ROOT="$REPO" STARTUP_FACTORY_AUTOMATION_CONFIG="$TMP/string-enabled.json" \
    STARTUP_FACTORY_PM_CONFIG="$PM_CONFIG" PM_TEST_LOG="$LOG" \
    "$MONITOR" --once > "$TMP/string-enabled.out" 2> "$TMP/string-enabled.err"; then
  echo "FAIL: string deployment switch should fail closed"; FAILURES=$((FAILURES+1))
elif grep -q 'enabled must be true or false' "$TMP/string-enabled.err"; then
  echo "ok: string automation switch fails closed"
else
  echo "FAIL: string automation switch produced the wrong error"; FAILURES=$((FAILURES+1))
fi

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
