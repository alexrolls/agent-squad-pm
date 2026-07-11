#!/usr/bin/env bash
# Publish queued artifacts idempotently; tracker state remains the durable source of truth.
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

[ $# -ge 2 ] && [ $# -le 3 ] || { echo "usage: process-outbox.sh <team> <featureId> [entry.json]" >&2; exit 2; }
team="$1"; feature="$2"; only="${3:-}"
repo="$(git rev-parse --show-toplevel)"
root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
workspace="$repo/$root/$team"
mkdir -p "$workspace/outbox/pending" "$workspace/outbox/done" "$workspace/outbox/locks"

if [ -n "$only" ]; then
  set -- "$only"
else
  set -- "$workspace"/outbox/pending/*.json
fi

for entry in "$@"; do
  [ -f "$entry" ] || continue
  fields="$(python3 - "$entry" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
for key in ('id','taskId','attempt','actor','marker','bodyPath','targetStatus','phase'):
    value=d.get(key)
    print('' if value is None else value)
PY
)"
  id="$(printf '%s\n' "$fields" | sed -n '1p')"
  task="$(printf '%s\n' "$fields" | sed -n '2p')"
  attempt="$(printf '%s\n' "$fields" | sed -n '3p')"
  actor="$(printf '%s\n' "$fields" | sed -n '4p')"
  marker="$(printf '%s\n' "$fields" | sed -n '5p')"
  body="$(printf '%s\n' "$fields" | sed -n '6p')"
  target="$(printf '%s\n' "$fields" | sed -n '7p')"
  phase="$(printf '%s\n' "$fields" | sed -n '8p')"
  [ -s "$body" ] || { echo "process-outbox: missing body $body" >&2; exit 1; }
  lock="$workspace/outbox/locks/$id.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    owner="$(cat "$lock/owner" 2>/dev/null || true)"
    if [ -z "$owner" ] || kill -0 "$owner" 2>/dev/null; then
      continue
    fi
    rm -f "$lock/owner"
    rmdir "$lock" 2>/dev/null || continue
    mkdir "$lock" 2>/dev/null || continue
  fi
  echo $$ > "$lock/owner"
  if [ ! -f "$entry" ]; then
    rm -f "$lock/owner"
    rmdir "$lock" 2>/dev/null || true
    continue
  fi

  if [ "$phase" = "pending" ]; then
    "$SKILL_DIR/bin/tracker-ops.sh" comment-once "$task" "$id" "$body"
    python3 - "$entry" <<'PY'
import json, os, sys
p=sys.argv[1]; d=json.load(open(p)); d['phase']='commented'
t=p+'.tmp'; open(t,'w').write(json.dumps(d, indent=2)+'\n'); os.replace(t,p)
PY
    phase=commented
  fi
  if [ "$phase" = "commented" ] && [ -n "$target" ]; then
    "$SKILL_DIR/bin/tracker-ops.sh" state "$task" "$target"
    python3 - "$entry" <<'PY'
import json, os, sys
p=sys.argv[1]; d=json.load(open(p)); d['phase']='transitioned'
t=p+'.tmp'; open(t,'w').write(json.dumps(d, indent=2)+'\n'); os.replace(t,p)
PY
  fi
  if [ "$phase" != "published" ]; then
    python3 "$SKILL_DIR/bin/runtime-state.py" emit --workspace "$workspace" --team "$team" \
      --feature "$feature" --task "$task" --attempt "$attempt" --actor "$actor" \
      --type artifact.published --stage "${target:-artifact-published}" \
      --summary "[$marker] published to tracker" --artifact "$body" >/dev/null
    python3 - "$entry" <<'PY'
import json, os, sys
p=sys.argv[1]; d=json.load(open(p)); d['phase']='published'
t=p+'.tmp'; open(t,'w').write(json.dumps(d, indent=2)+'\n'); os.replace(t,p)
PY
  fi
  [ ! -f "$entry" ] || mv "$entry" "$workspace/outbox/done/$id.json"
  rm -f "$lock/owner"
  rmdir "$lock" 2>/dev/null || true
  echo "published [$marker] for $task"
done
