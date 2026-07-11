#!/usr/bin/env bash
# dispatch.sh — one deterministic read-and-act pass (or a loop of them).
# Zero LLM per cycle. Logic spec: reference/dispatch.md.
#
# Usage:
#   dispatch.sh <team> <featureId> --once [--dry-run] [--unblock=auto|suggest|off]
#   dispatch.sh <team> <featureId> --watch [--unblock=...]
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SKILL_DIR/config/team.config.md"
PM_CONFIG="$SKILL_DIR/config/project-management.config.md"

die() { echo "dispatch: $*" >&2; exit 1; }

role_cmd_key() { # backend -> BACKEND_CMD ; principal-architect -> PRINCIPAL_ARCHITECT_CMD
  printf '%s_CMD' "$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"
}

key_is_null() { # key_is_null KEY -> 0 if the config sets KEY explicitly to null
  grep -qE "^$1=null[[:space:]]*(#.*)?$" "$CONFIG"
}

read_key() { # from team.config.md; quotes stripped; null -> empty; inline # stripped on unquoted
  local line _t; line="$(grep -m1 "^$1=" "$CONFIG" || true)"
  line="${line#*=}"
  if [ "${line#\"}" != "$line" ]; then
    line="${line#\"}"; line="${line%%\"*}"
  else
    line="${line%%[[:space:]]#*}"
    _t="${line##*[![:space:]]}"; line="${line%"$_t"}"
  fi
  [ "$line" = "null" ] && line=""
  printf '%s' "$line"
}

read_pm_key() { # read from project-management.config.md; quotes stripped; null -> empty; inline # stripped
  local line _t; line="$(grep -m1 "^$1=" "$PM_CONFIG" || true)"
  line="${line#*=}"
  if [ "${line#\"}" != "$line" ]; then
    line="${line#\"}"; line="${line%%\"*}"
  else
    line="${line%%[[:space:]]#*}"
    _t="${line##*[![:space:]]}"; line="${line%"$_t"}"
  fi
  [ "$line" = "null" ] && line=""
  printf '%s' "$line"
}

is_mcp_only() { # is_mcp_only <adapter> -> 0 if configured for MCP-only access
  case "$1" in
    Linear)       [ "$(read_pm_key LINEAR_ACCESS)"  = "mcp"  ] ;;
    Jira)         [ "$(read_pm_key JIRA_ACCESS)"    = "mcp"  ] ;;
    GitHubIssues) [ "$(read_pm_key GITHUB_USE_MCP)" = "true" ] ;;
    *)            return 1 ;;
  esac
}

resolve_role() { # resolve_role <team> <protocol-role> -> concrete role (or same if no mapping)
  local pf; pf="$(teamroot "$1")/preset.env"
  [ -f "$pf" ] || { printf '%s' "$2"; return; }
  local key; key="PROTOCOL_$(printf '%s' "$2" | tr 'a-z-' 'A-Z_')"
  local val; val="$(grep -m1 "^$key=" "$pf" | cut -d= -f2 || true)"
  printf '%s' "${val:-$2}"
}

teamroot() {
  local root; root="$(read_key TEAMWORK_ROOT)"; root="${root:-.teamwork}"
  printf '%s/%s/%s' "$(git rev-parse --show-toplevel)" "$root" "$1"
}

role_live() { # role_live <team> <role> -> 0 if a live instance exists
  local pf; pf="$(teamroot "$1")/pids/$2.pid"
  [ -f "$pf" ] || return 1
  local pid; pid="$(cat "$pf")"
  if [ "$pid" = "tmux" ]; then
    tmux list-windows -t "team-$1" -F '#{window_name}' 2>/dev/null | grep -qx "$2"
  else
    kill -0 "$pid" 2>/dev/null
  fi
}

task_live() { # task_live <team> <role> <taskId> <attempt>
  local key instance pf pid
  key="$(python3 "$SKILL_DIR/bin/runtime-state.py" key "$3")"
  instance="$2--$key--a$4"
  pf="$(teamroot "$1")/pids/tasks/$instance.pid"
  [ -f "$pf" ] || return 1
  pid="$(cat "$pf")"
  if [ "$pid" = "tmux" ]; then
    tmux list-windows -t "team-$1" -F '#{window_name}' 2>/dev/null | grep -qx "$instance"
  else
    kill -0 "$pid" 2>/dev/null
  fi
}

task_any_live() { # task_any_live <team> <taskId> -> any role/attempt process for task
  local key pf pid instance
  key="$(python3 "$SKILL_DIR/bin/runtime-state.py" key "$2")"
  for pf in "$(teamroot "$1")"/pids/tasks/*--"$key"--a*.pid; do
    [ -f "$pf" ] || continue
    pid="$(cat "$pf")"
    instance="$(basename "$pf" .pid)"
    if [ "$pid" = "tmux" ]; then
      tmux list-windows -t "team-$1" -F '#{window_name}' 2>/dev/null | grep -qx "$instance" && return 0
    elif kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

next_mailbox_file() { # next_mailbox_file <mailbox-dir> -> path with next free NNN
  local mb="$1" max=0 n f
  mkdir -p "$mb"
  for f in "$mb"/[0-9][0-9][0-9]-*.md; do
    [ -e "$f" ] || continue
    n="${f##*/}"; n="${n%%-*}"; n=$((10#$n))
    [ "$n" -gt "$max" ] && max=$n
  done
  printf '%s/%03d-dispatcher.md' "$mb" $((max + 1))
}

adapter_default_unblock() {
  local a; a="$(grep -m1 '^PRODUCT_MANAGEMENT_TOOL=' "$PM_CONFIG" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"
  local effective="${TRACKER_ADAPTER:-$a}"
  [ -n "$effective" ] || die "cannot determine tracker adapter (PRODUCT_MANAGEMENT_TOOL in config/project-management.config.md or TRACKER_ADAPTER env)"
  case "$effective" in Linear|Jira) echo auto ;; *) echo suggest ;; esac
}

dispatch_once() { # dispatch_once <team> <featureId> <dry:yes|no> <unblock>
  local team="$1" fid="$2" dry="$3" unblock="$4"
  local dir; dir="$(teamroot "$team")"
  local _a; _a="$(grep -m1 '^PRODUCT_MANAGEMENT_TOOL=' "$PM_CONFIG" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)"
  local adapter="${TRACKER_ADAPTER:-$_a}"
  if is_mcp_only "$adapter"; then
    die "dispatch requires scriptable tracker access — $adapter is configured for MCP-only.
  Set the scriptable option in config/project-management.config.md or use harness mode."
  fi
  mkdir -p "$dir"
  local lock="$dir/dispatch.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    echo "dispatch: another pass owns $lock; skipping"
    return 0
  fi
  trap 'rmdir "$lock" 2>/dev/null || true' RETURN
  if [ "$dry" != "yes" ]; then
    "$SKILL_DIR/bin/process-outbox.sh" "$team" "$fid"
  fi
  "$SKILL_DIR/bin/tracker-ops.sh" export "$fid" "$dir/tasks.json" >/dev/null
  if [ "$dry" != "yes" ]; then
    "$SKILL_DIR/bin/sync-progress.sh" "$team" "$fid" "$dir/tasks.json"
  fi
  local stuck; stuck="$(read_key STUCK_AFTER_MINUTES)"; stuck="${stuck:-15}"
  local execution max_active plan
  execution="$(read_key EXECUTION)"; execution="${execution:-sequential}"
  max_active="$(read_key MAX_ACTIVE_IMPLEMENTERS)"
  local planner_args=(--skill "$SKILL_DIR" --workdir "$dir" --stuck-minutes "$stuck" --execution "$execution")
  [ -z "$max_active" ] || planner_args+=(--max-active "$max_active")
  plan="$(python3 "$SKILL_DIR/bin/dispatch-plan.py" "${planner_args[@]}")"
  if [ -z "$plan" ]; then echo "dispatch: nothing actionable"; return 0; fi
  local action arg detail extra
  while IFS="$(printf '\t')" read -r action arg detail extra; do
    case "$action" in
      unblock)
        case "$unblock" in
          off)     echo "plan: unblock $arg — suppressed (--unblock=off)" ;;
          suggest) echo "plan: unblock $arg — SUGGESTED (confirm and move via the team-lead; see reference/dispatch.md)" ;;
          auto)
            echo "plan: unblock $arg → [$detail] (all blockers terminal)"
            if [ "$dry" != "yes" ]; then
              "$SKILL_DIR/bin/tracker-ops.sh" state "$arg" "$detail"
              printf 'Auto-unblocked by dispatcher: every blocking [task] reached the terminal status. Resuming to [%s].\n\n— dispatcher (on behalf of team-lead)\n' "$detail" \
                | "$SKILL_DIR/bin/tracker-ops.sh" comment "$arg" -
            fi ;;
          *) die "unknown --unblock mode '$unblock'" ;;
        esac ;;
      unblock-no-rs)
        echo "plan: unblock $arg — NO RESUME STATUS (lead must resume; add 'resume-status: <Status>' to the block comment)" ;;
      launch)
        local concrete; concrete="$(resolve_role "$team" "$arg")"
        local _ck; _ck="$(role_cmd_key "$concrete")"
        if key_is_null "$_ck"; then
          echo "plan: launch $arg (→$concrete) — skipped (${_ck}=null; the team-lead routes this queue)"
        elif role_live "$team" "$concrete"; then
          echo "plan: launch $arg (→$concrete) — skipped (live instance)"
        else
          echo "plan: launch $arg (→$concrete) ($detail)"
          if [ "$dry" != "yes" ]; then
            if [ -n "$extra" ]; then
              local packages="" task_id package
              local _old_ifs="$IFS"; IFS='|'
              for task_id in $extra; do
                package="$("$SKILL_DIR/bin/review-package.sh" "$team" "$task_id" 2>/dev/null || true)"
                [ -z "$package" ] || packages="$packages $task_id=$package"
              done
              IFS="$_old_ifs"
              [ -z "$packages" ] || detail="$detail Review packages:$packages"
            fi
            local mf; mf="$(next_mailbox_file "$dir/mailbox/$concrete")"
            printf 'From: dispatcher\nRe: %s\n---\n%s\n' "$fid" "$detail" > "$mf"
            "$SKILL_DIR/bin/launch-team.sh" start "$team" "$fid" "$concrete"
          fi
        fi ;;
      claim-task)
        local claim_role; claim_role="$(resolve_role "$team" "$arg")"
        if task_live "$team" "$claim_role" "$detail" "$extra"; then
          echo "plan: claim $detail for $claim_role - skipped (live task instance)"
        else
          echo "plan: claim $detail for $claim_role (attempt $extra)"
          if [ "$dry" != "yes" ]; then
            "$SKILL_DIR/bin/tracker-ops.sh" claim "$detail" "$claim_role"
            "$SKILL_DIR/bin/runtime-event.sh" "$team" "$fid" "$detail" "$extra" "$claim_role" task.claimed claimed "task claimed by deterministic dispatcher" >/dev/null
            "$SKILL_DIR/bin/launch-team.sh" start-task "$team" "$fid" "$claim_role" "$detail" "$extra"
          fi
        fi ;;
      launch-task)
        local task_role; task_role="$(resolve_role "$team" "$arg")"
        if task_any_live "$team" "$detail"; then
          echo "plan: launch task $detail as $task_role - skipped (another task attempt is live)"
        else
          echo "plan: launch task $detail as $task_role (attempt $extra)"
          if [ "$dry" != "yes" ]; then
            "$SKILL_DIR/bin/launch-team.sh" start-task "$team" "$fid" "$task_role" "$detail" "$extra"
          fi
        fi ;;
    esac
  done <<EOF
$plan
EOF
}

[ $# -ge 3 ] || die "usage: dispatch.sh <team> <featureId> --once|--watch [--dry-run] [--unblock=auto|suggest|off]"
TEAM="$1"; FID="$2"; MODE="$3"; shift 3
DRY=no; UNBLOCK="$(adapter_default_unblock)"
for opt in "$@"; do
  case "$opt" in
    --dry-run) DRY=yes ;;
    --unblock=*) UNBLOCK="${opt#*=}" ;;
    *) die "unknown option $opt" ;;
  esac
done
case "$MODE" in
  --once) dispatch_once "$TEAM" "$FID" "$DRY" "$UNBLOCK" ;;
  --watch)
    [ "$DRY" = "no" ] || die "--watch does not combine with --dry-run"
    INTERVAL="$(read_key POLL_INTERVAL_SECONDS)"; INTERVAL="${INTERVAL:-120}"
    echo "dispatch: watching (every ${INTERVAL}s) — this shell is the loop owner; keep it alive (tmux/nohup)"
    while true; do
      before="$(python3 "$SKILL_DIR/bin/runtime-state.py" count --workspace "$(teamroot "$TEAM")")"
      dispatch_once "$TEAM" "$FID" no "$UNBLOCK" || echo "dispatch: pass failed — retrying next interval" >&2
      after="$(python3 "$SKILL_DIR/bin/runtime-state.py" count --workspace "$(teamroot "$TEAM")")"
      [ "$after" != "$before" ] && continue
      python3 "$SKILL_DIR/bin/runtime-state.py" wait --workspace "$(teamroot "$TEAM")" --count "$after" --timeout "$INTERVAL" >/dev/null
    done ;;
  *) die "mode must be --once or --watch" ;;
esac
