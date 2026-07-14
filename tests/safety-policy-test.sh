#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY="$ROOT/bin/policy-check.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILURES=0

allow() {
  local desc="$1" out; shift
  if out="$($POLICY "$@" 2>&1)" && printf '%s' "$out" | grep -q '"decision": "ALLOW"'; then
    echo "ok: $desc"
  else
    echo "FAIL: $desc ($out)"; FAILURES=$((FAILURES+1))
  fi
}

deny() {
  local desc="$1" out rc; shift
  set +e; out="$($POLICY "$@" 2>&1)"; rc=$?; set -e
  if [ "$rc" -eq 3 ] && printf '%s' "$out" | grep -q '"decision": "DENY"'; then
    echo "ok: $desc"
  else
    echo "FAIL: $desc (rc=$rc $out)"; FAILURES=$((FAILURES+1))
  fi
}

approval() {
  local desc="$1" out rc; shift
  set +e; out="$($POLICY "$@" 2>&1)"; rc=$?; set -e
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -q 'REQUIRE_HUMAN_APPROVAL'; then
    echo "ok: $desc"
  else
    echo "FAIL: $desc (rc=$rc $out)"; FAILURES=$((FAILURES+1))
  fi
}

CMD=(command --action deploy.plan --environment production --)
deny "recursive filesystem deletion denied" "${CMD[@]}" rm -rf /
deny "absolute filesystem deletion denied" "${CMD[@]}" /bin/rm -rf /
deny "parent filesystem deletion denied" "${CMD[@]}" rm -rf ..
deny "bulk find deletion denied" "${CMD[@]}" find /tmp -delete
deny "raw disk write denied" "${CMD[@]}" dd if=/dev/zero of=/dev/disk0
deny "database drop denied" "${CMD[@]}" psql -c "DROP DATABASE prod"
deny "database table drop denied" "${CMD[@]}" psql -c "DROP TABLE users"
deny "database truncate denied" "${CMD[@]}" psql -c "TRUNCATE TABLE users"
deny "unbounded data delete denied" "${CMD[@]}" psql -c "DELETE FROM users"
deny "infrastructure destroy denied" "${CMD[@]}" terraform destroy
deny "cluster deletion denied" "${CMD[@]}" kubectl delete namespace production
deny "release uninstall denied" "${CMD[@]}" helm uninstall api
deny "instance termination denied" "${CMD[@]}" cloud terminate-instance i-123
deny "provider instance deletion denied" "${CMD[@]}" gcloud compute instances delete prod-1
deny "destructive git reset denied" "${CMD[@]}" git reset --hard
deny "force push denied" "${CMD[@]}" git push --force origin main
deny "secret environment dump denied" "${CMD[@]}" printenv
deny "metadata credential read denied" "${CMD[@]}" curl http://169.254.169.254/latest/meta-data
deny "privilege escalation denied" "${CMD[@]}" sudo ./deploy
deny "absolute privilege escalation denied" "${CMD[@]}" /usr/bin/sudo ./deploy
deny "opaque shell denied" "${CMD[@]}" bash -c ./deploy
deny "absolute opaque shell denied" "${CMD[@]}" /bin/bash -c ./deploy
deny "inline Python bypass denied" "${CMD[@]}" /usr/bin/python3 -c "import os; os.remove('/tmp/x')"
deny "command substitution denied" "${CMD[@]}" ./deploy '$(printenv)'
deny "pipeline bypass denied" "${CMD[@]}" ./plan '|' sh

allow "structured plan hook allowed" command --action deploy.plan --environment production -- ./ops/release plan --commit abc
allow "structured health hook allowed" command --action deploy.verify --environment production -- ./ops/release verify --release r1
approval "production apply needs exact authorization" command --action deploy.apply --environment production -- ./ops/release apply --release r1
allow "digest-authorized exact apply allowed" command --action deploy.apply --environment production --authorization-digest "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" -- ./ops/release apply --release r1
approval "arbitrary rollback needs exact authorization" command --action deploy.rollback --environment production -- ./ops/release rollback --release r1
deny "unknown privileged action denied" command --action deploy.frob --environment production -- ./ops/release frob

python3 - "$ROOT/config/guardrails.config.json" "$TMP/guardrails-extra-approval.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['additionalApprovalRequiredActions']=['deploy.verify']; d['maximumAutomaticPlanChanges']=1
json.dump(d,open(sys.argv[2],'w'))
PY
approval "project-required command approval is enforceable" --config "$TMP/guardrails-extra-approval.json" command --action deploy.verify --environment production -- ./ops/release verify --release r1
allow "project-required command approval accepts an exact digest" --config "$TMP/guardrails-extra-approval.json" command --action deploy.verify --environment production --authorization-digest "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" -- ./ops/release verify --release r1

cat > "$TMP/good.json" <<'EOF'
{
  "schemaVersion": 1,
  "environment": "production",
  "target": {"id": "prod"},
  "commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "sourceArchiveDigest": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "artifactDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "changes": [{
    "action": "UPDATE", "resourceClass": "application", "resourceId": "api",
    "destructive": false, "reversible": true, "publicExposure": false,
    "dataEffect": "none", "estimatedCostDelta": 0,
    "secretValueAccess": false, "privilegeEscalation": false,
    "disablesSafeguard": false
  }],
  "rollback": {"automaticSafe": true, "previousArtifactDigest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}
}
EOF
allow "non-destructive immutable automatic plan allowed" plan --mode automatic "$TMP/good.json"
approval "approval mode requires external authorization" plan --mode approval-required "$TMP/good.json"
allow "approval mode accepts externally verified exact plan" plan --mode approval-required --approved "$TMP/good.json"

python3 - "$TMP/good.json" "$TMP/two-changes.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['changes']=[dict(d['changes'][0]),dict(d['changes'][0],resourceId='worker')]
json.dump(d,open(sys.argv[2],'w'))
PY
approval "large plan requires exact approval" --config "$TMP/guardrails-extra-approval.json" plan --mode approval-required "$TMP/two-changes.json"
allow "large non-destructive plan accepts exact approval" --config "$TMP/guardrails-extra-approval.json" plan --mode approval-required --approved "$TMP/two-changes.json"

python3 - "$TMP/good.json" "$TMP/delete.json" "$TMP/sensitive.json" "$TMP/unknown.json" "$TMP/nonreversible-create.json" "$TMP/compute-scale.json" "$TMP/cost-bool.json" "$TMP/cost-string.json" "$TMP/cost-nan.json" "$TMP/cost-inf.json" <<'PY'
import json,sys
base=json.load(open(sys.argv[1]))
safe={"secretValueAccess":False,"privilegeEscalation":False,"disablesSafeguard":False}
for path,change in [
    (sys.argv[2], {"action":"DELETE","resourceClass":"compute","resourceId":"prod-1","destructive":True,"reversible":False,"publicExposure":False,"dataEffect":"destructive","estimatedCostDelta":0}),
    (sys.argv[3], {"action":"UPDATE","resourceClass":"database","resourceId":"users","destructive":False,"reversible":True,"publicExposure":False,"dataEffect":"additive","estimatedCostDelta":0}),
    (sys.argv[4], {"action":"EXPLODE","resourceClass":"application","resourceId":"api","destructive":False,"reversible":True,"publicExposure":False,"dataEffect":"none","estimatedCostDelta":0}),
    (sys.argv[5], {"action":"CREATE","resourceClass":"application","resourceId":"worker","destructive":False,"reversible":False,"publicExposure":False,"dataEffect":"none","estimatedCostDelta":0}),
    (sys.argv[6], {"action":"UPDATE","resourceClass":"compute","resourceId":"critical-service-capacity","destructive":False,"reversible":True,"publicExposure":False,"dataEffect":"none","estimatedCostDelta":0}),
]:
    change.update(safe)
    value=dict(base); value["changes"]=[change]
    json.dump(value,open(path,"w"))
for path,cost in zip(sys.argv[7:], [True, "1.5", float("nan"), float("inf")]):
    value=dict(base); value["changes"]=[dict(base["changes"][0], estimatedCostDelta=cost)]
    json.dump(value,open(path,"w"),allow_nan=True)
PY
deny "delete in normalized plan always denied" plan --mode approval-required --approved "$TMP/delete.json"
deny "sensitive change cannot use automatic mode" plan --mode automatic "$TMP/sensitive.json"
approval "sensitive change requires exact human approval" plan --mode approval-required "$TMP/sensitive.json"
allow "verified sensitive non-delete plan may proceed" plan --mode approval-required --approved "$TMP/sensitive.json"
deny "unknown normalized plan action denied" plan --mode approval-required --approved "$TMP/unknown.json"
deny "non-reversible create cannot use automatic mode" plan --mode automatic "$TMP/nonreversible-create.json"
approval "non-reversible create requires exact human approval" plan --mode approval-required "$TMP/nonreversible-create.json"
allow "verified non-reversible create may proceed" plan --mode approval-required --approved "$TMP/nonreversible-create.json"
deny "compute capacity change cannot use automatic mode" plan --mode automatic "$TMP/compute-scale.json"
approval "compute capacity change requires exact human approval" plan --mode approval-required "$TMP/compute-scale.json"
deny "boolean cost delta is not a number" plan --mode approval-required --approved "$TMP/cost-bool.json"
deny "string cost delta is not a JSON number" plan --mode approval-required --approved "$TMP/cost-string.json"
deny "NaN cost delta is denied" plan --mode approval-required --approved "$TMP/cost-nan.json"
deny "infinite cost delta is denied" plan --mode approval-required --approved "$TMP/cost-inf.json"

python3 - "$TMP/good.json" "$TMP/unknown-top.json" "$TMP/unknown-change.json" "$TMP/secret-read.json" "$TMP/secret-access.json" "$TMP/admin.json" "$TMP/disable-control.json" <<'PY'
import json,sys
base=json.load(open(sys.argv[1]))
def write(path, change=None, **extra):
    value=dict(base); value.update(extra)
    if change is not None: value['changes']=[dict(base['changes'][0], **change)]
    json.dump(value,open(path,'w'))
write(sys.argv[2], provider='aws')
write(sys.argv[3], {'providerAction':'DELETE'})
write(sys.argv[4], {'action':'READ', 'resourceClass':'secret', 'resourceId':'prod-secret'})
write(sys.argv[5], {'secretValueAccess':True})
write(sys.argv[6], {'resourceClass':'identity', 'privilegeEscalation':True})
write(sys.argv[7], {'resourceClass':'security-control', 'disablesSafeguard':True})
PY
deny "unknown top-level provider semantics denied" plan --mode automatic "$TMP/unknown-top.json"
deny "unknown change-level provider semantics denied" plan --mode automatic "$TMP/unknown-change.json"
deny "secret-resource reads remain denied with human approval" plan --mode approval-required --approved "$TMP/secret-read.json"
deny "declared secret-value access remains denied with human approval" plan --mode approval-required --approved "$TMP/secret-access.json"
deny "wildcard administrator escalation remains denied with human approval" plan --mode approval-required --approved "$TMP/admin.json"
deny "safeguard disabling remains denied with human approval" plan --mode approval-required --approved "$TMP/disable-control.json"

python3 - "$TMP/good.json" "$ROOT/config/guardrails.config.json" "$TMP/duplicate-top.json" "$TMP/duplicate-secret-flag.json" "$TMP/duplicate-config.json" <<'PY'
import sys

plan = open(sys.argv[1]).read()
config = open(sys.argv[2]).read()

top_marker = '"schemaVersion": 1,'
secret_marker = '"secretValueAccess": false,'
config_marker = '"schemaVersion": 1,'
assert top_marker in plan and secret_marker in plan and config_marker in config

open(sys.argv[3], 'w').write(plan.replace(top_marker, '"schemaVersion": 2,\n  "schemaVersion": 1,', 1))
open(sys.argv[4], 'w').write(plan.replace(secret_marker, '"secretValueAccess": true, "secretValueAccess": false,', 1))
open(sys.argv[5], 'w').write(config.replace(config_marker, '"schemaVersion": 2,\n  "schemaVersion": 1,', 1))
PY
deny "duplicate top-level plan key is denied before semantic evaluation" plan --mode automatic "$TMP/duplicate-top.json"
deny "duplicate nested safety flag cannot hide secret access" plan --mode automatic "$TMP/duplicate-secret-flag.json"
deny "duplicate guardrail config key is denied" --config "$TMP/duplicate-config.json" plan --mode automatic "$TMP/good.json"

python3 - "$ROOT/config/guardrails.config.json" "$TMP/guardrails-nan-limit.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['maximumAutomaticCostDelta']=float('nan')
json.dump(d,open(sys.argv[2],'w'),allow_nan=True)
PY
deny "non-finite configured cost limit is denied" --config "$TMP/guardrails-nan-limit.json" plan --mode approval-required --approved "$TMP/good.json"

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
