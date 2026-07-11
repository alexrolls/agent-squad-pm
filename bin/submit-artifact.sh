#!/usr/bin/env bash
# Put one structured agent artifact in the durable outbox; the dispatcher publishes it.
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

[ $# -eq 8 ] || {
  echo "usage: submit-artifact.sh <team> <featureId> <taskId> <attempt> <actor> <marker> <bodyfile> <target-status|->" >&2
  exit 2
}
team="$1"; feature="$2"; task="$3"; attempt="$4"; actor="$5"; marker="$6"; source="$7"; target="$8"
[ -s "$source" ] || { echo "submit-artifact: missing or empty body file $source" >&2; exit 1; }
first="$(sed -n '1p' "$source")"
case "$first" in
  "[$marker]"*) ;;
  *) echo "submit-artifact: body must begin with [$marker]" >&2; exit 1 ;;
esac

repo="$(git rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$repo/$root/$team"
id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
mkdir -p "$workspace/outbox/pending" "$workspace/outbox/bodies" "$workspace/outbox/done"
body="$workspace/outbox/bodies/$id.md"
cp "$source" "$body"
python3 - "$workspace/outbox/pending/$id.json" "$id" "$team" "$feature" "$task" "$attempt" "$actor" "$marker" "$body" "$target" <<'PY'
import json, sys
from datetime import datetime, timezone
path, ident, team, feature, task, attempt, actor, marker, body, target = sys.argv[1:]
temp = path + '.tmp'
with open(temp, 'w') as handle:
    json.dump({
        'schemaVersion': 1, 'id': ident, 'team': team, 'featureId': feature,
        'taskId': task, 'attempt': int(attempt), 'actor': actor, 'marker': marker,
        'bodyPath': body, 'targetStatus': None if target == '-' else target,
        'phase': 'pending', 'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds')
    }, handle, indent=2)
    handle.write('\n')
import os
os.replace(temp, path)
PY
python3 "$SKILL_DIR/bin/runtime-state.py" emit --workspace "$workspace" --team "$team" \
  --feature "$feature" --task "$task" --attempt "$attempt" --actor "$actor" \
  --type artifact.ready --stage artifact-ready --summary "[$marker] queued for tracker publication" --artifact "$body" >/dev/null

if [ "$(read_key TRACKER_WRITERS)" = "all" ]; then
  "$SKILL_DIR/bin/process-outbox.sh" "$team" "$feature" "$workspace/outbox/pending/$id.json"
else
  echo "$workspace/outbox/pending/$id.json"
fi
