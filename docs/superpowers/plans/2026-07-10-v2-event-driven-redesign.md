# v2 Event-Driven Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved spec `docs/superpowers/specs/2026-07-09-v2-event-driven-redesign-design.md` — dispatcher, preflight, provisioned instance-bound worktrees, evidence ledger, marker authorization, queue-consumer gates, integrator v2, and comment protocol v2.

**Architecture:** The loop moves outside the agents: a deterministic `bin/dispatch.sh` (CLI) / the harness orchestrator (harness) converts tracker+workspace state into role launches per a shared `reference/dispatch.md` event table. `bin/tracker-ops.sh` gains `blockedBy` export and `update-comment`; `bin/launch-team.sh` gains `preflight`, provisioned attempt-bound worktrees, and `worktree-remove`. Docs shift from "agents that wait" to "one-shot turns + queues", with rigor invariants I1–I7 untouched.

**Tech Stack:** bash + inline python3 (existing `tracker-ops.sh` heredoc pattern), git worktrees, Markdown-adapter offline test fixtures.

## Global Constraints

- All shell must pass under `set -euo pipefail`; every failure is fail-loud (`die`), never a fallback.
- Generic vocabulary only in all docs/comments: `[feature]`, `[task]`, board statuses — never "issue/epic/story/ticket" outside `adapters/`.
- Rigor invariants (spec §Rigor): QA and integrator ALWAYS re-run suites; QA approval last; file-list == diff; no self-approval; idle-without-artifact = Stuck; claims without evidence = NOT validated. No task may weaken these.
- New config keys default to `null`/absent = existing behavior (`WORKTREE_SETUP`, `VALIDATE_FORMAT`; `markers` table absent = check skipped).
- Tests: `bash tests/launcher-test.sh`, `bash tests/tracker-ops-test.sh`, and new suites must end `ALL PASS`. Tests run on macOS bash 3.2 (`sed -i ''` form, no `declare -A`, no `${var,,}`).
- Comment bodies always travel via file or stdin, never shell arguments.
- Commit after every task: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.

---

## Phase A — stalls & dead launches

### Task 1: `reference/dispatch.md` — the shared dispatch logic

**Files:**
- Create: `reference/dispatch.md`
- Modify: `SKILL.md` (sibling-files list, after the `reference/orchestration.md` bullet)

**Interfaces:**
- Produces: the event→action table that Task 4's script and the harness team-lead both implement; the terms "dispatch pass", "queue message", "auto-unblock" used by Tasks 2, 4, 9.

- [ ] **Step 1: Write `reference/dispatch.md`** with exactly this content:

````markdown
# Dispatch — the loop lives outside the agent

Both production runs proved the same thing: the tracker+marker state model
survives any restart, but an agent's promise to "check back in N minutes"
does not — one-shot runtimes exit, and the gate stalls. So no agent owns
time. **Dispatch is a stateless read-and-act pass** executed by machinery:

| Runtime | Loop owner | One pass = |
|---|---|---|
| CLI (tmux / background processes) | `bin/dispatch.sh <team> <featureId> --once` (or `--watch`, which repeats it every `POLL_INTERVAL_SECONDS`) | read tracker export + mailboxes + heartbeats → launch actionable roles via `launch-team.sh start` |
| Harness (in-session subagents) | the team-lead orchestrator itself — its native event loop | same table, executed directly: subagent spawn = role launch, idle notifications = heartbeats |

`--watch` needs a persistent shell (tmux window or `nohup`) — **the human
owns that process**, explicitly. Hiding this ownership is how a pipeline
silently stalls for hours.

## The event table

One pass reads the [feature]'s task export, the team mailboxes, and the
heartbeat files, then acts top to bottom:

| State observed | Action |
|---|---|
| `[Blocked]` [task] whose `blockedBy` [tasks] are all terminal | Auto-move `Blocked → Active` + comment — no agent launch (adapter caveat below) |
| `[design-note]` with no later `[design-approved]`/`[design-pushback]` | Launch principal-architect with the **whole** pending-design queue |
| [Task](s) in `[Review]` missing `[review-approval]` / `[architecture-approval]` since the last `[review-request]` | Launch reviewer / principal-architect with the **whole** review queue |
| [Task](s) in `[Review]` holding both approvals | Launch integrator with the merge queue in dependency order |
| Dispatchable `[Planned]` [tasks] (unassigned, blockers terminal) or stale/artifact-less-idle teammates | Launch team-lead (assignment and unblock-ladder judgment stay the lead's) |
| Nothing actionable | Exit cleanly, print "nothing actionable" |

## Rules

- **Dedup:** never two live instances of one role. CLI: a live pid / tmux
  window for the role skips its launch. Harness: the orchestrator awaits its
  subagents, so double-launch cannot happen.
- **Queue message before boot:** the pass writes the queue into the role's
  mailbox (`mailbox/<role>/NNN-dispatcher.md`) *before* launching, so the
  role boots as a queue consumer (drain every item, post per-[task] markers,
  exit).
- **End of turn = exit.** Role briefs contain no self-scheduling. An agent
  that finished its queue delivers its artifacts and exits; the next pass
  owns what happens next.
- **Auto-unblock scope:** performed automatically only where the adapter's
  `blockedBy` read is reliable (Linear, Jira). GitHubIssues and Markdown are
  **suggest-only** — the pass prints the suggestion and the team-lead
  confirms. Override per invocation with `--unblock=auto|suggest|off`.
- **Policy stays where it was:** the pipelined dispatch rules (independence,
  sweep gate, freeze protocol — `reference/orchestration.md` → *Execution
  modes*) are decisions the **team-lead** makes during its pass. The
  dispatcher is the trigger mechanism that makes "the moment [task] N enters
  `[Review]`" actually fire; it never overrides lead policy.
- **Overlap caution (CLI):** two overlapping passes can double-launch a
  role between the pid check and the boot. Keep `POLL_INTERVAL_SECONDS`
  above the worst-case agent boot time and don't run `--watch` twice.
- **Preset rosters:** the script launches the seven protocol roles. Where a
  preset maps a queue to a specialized role (e.g. `senior-qa-engineer` as
  reviewer), the launched team-lead routes the queue; the reviewer launch is
  skipped if its `_CMD` is null.
- **Long features (harness):** past ~20 [tasks] the orchestrator should
  compress processed-event state between turns (its context is the loop
  state); the tracker remains the source of truth for anything dropped.
````

- [ ] **Step 2: Add the sibling-file pointer in `SKILL.md`.** After the line
`- `reference/orchestration.md` — multi-agent protocol (mailboxes, gates, unblocking)` insert:

```markdown
- `reference/dispatch.md` — who converts tracker/mailbox events into role launches (the loop lives outside the agent)
```

- [ ] **Step 3: Verify and commit**

Run: `grep -q "dispatch.md" SKILL.md && grep -q "read-and-act pass" reference/dispatch.md && echo OK`
Expected: `OK`

```bash
git add reference/dispatch.md SKILL.md
git commit -m "feat: reference/dispatch.md — shared event->launch table for both runtimes"
```

---

### Task 2: Purge self-scheduling language from protocol + briefs

**Files:**
- Modify: `reference/orchestration.md` (three places), `roles/team-lead.md`, `roles/principal-architect.md`, `teams/roles/senior-full-stack-engineer.md`, `teams/roles/senior-frontend-engineer.md`, `teams/roles/senior-staff-engineer.md`, `teams/roles/senior-sre-engineer.md`

**Interfaces:**
- Consumes: `reference/dispatch.md` (Task 1) as the cross-reference target.

- [ ] **Step 1: `reference/orchestration.md` — mailbox Receive rule.** Replace the bullet
`- **Receive:** check your mailbox directory between work steps and at least every` / `` `POLL_INTERVAL_SECONDS` when idle. Process messages in number order; delete each`` / `  after acting on it.` with:

```markdown
- **Receive:** check your mailbox directory between work steps. Process messages in
  number order; delete each after acting on it. **Never sit idle polling:** when your
  turn's work is done, deliver your artifact and exit — you will not be alive later,
  so never plan to "check back". The dispatcher owns time (`reference/dispatch.md`);
  `POLL_INTERVAL_SECONDS` is *its* cadence, not yours.
```

- [ ] **Step 2: `reference/orchestration.md` — supervision loop opener.** Replace the line
`Every \`POLL_INTERVAL_SECONDS\`: read all heartbeats, your mailbox, and the tracker.` with:

```markdown
**On each invocation** — the dispatcher (`reference/dispatch.md`) or the harness
loop decides when that is — read all heartbeats, your mailbox, and the tracker,
act on **every** pending event in one pass, then exit.
```

- [ ] **Step 3: `reference/orchestration.md` — capability matrix row.** Replace
`| Long-running loop | team-lead, principal-architect, integrator | relaunch on a schedule; recovery makes restarts free |` with:

```markdown
| Long-running loop | nobody — the loop lives outside agents (`reference/dispatch.md`) | one-shot turns are the primary path: `bin/dispatch.sh --watch` (CLI) or the harness orchestrator converts events into launches; recovery makes restarts free |
```

- [ ] **Step 4: `roles/team-lead.md` — Phase 2 opener.** Replace
`Run the supervision loop from \`reference/orchestration.md\` (cadence` / `` `POLL_INTERVAL_SECONDS`): read heartbeats, mailbox, tracker → detect stuck /`` / `conflict / crash → apply the unblock ladder one rung at a time, recording every` / `rung as a comment on the affected [task]. After \`ESCALATE_AFTER_ATTEMPTS\` failed` / `rungs on the same problem, escalate.` with:

```markdown
Each time you are invoked (by the dispatcher — `reference/dispatch.md` — a
mailbox message, or your own harness loop), run one full supervision pass from
`reference/orchestration.md`: read heartbeats, mailbox, tracker → detect stuck /
conflict / crash → apply the unblock ladder one rung at a time, recording every
rung as a comment on the affected [task] → act on every pending dispatch decision
(claims, queues, unblocks) → exit. Never promise to "check back later" — the
dispatcher owns time. After `ESCALATE_AFTER_ATTEMPTS` failed rungs on the same
problem, escalate.
```

- [ ] **Step 5: `roles/principal-architect.md` — "Your loop" section.** Replace
`Every \`POLL_INTERVAL_SECONDS\`: mailbox, then tracker — pending \`[design-note]\`s` with:

```markdown
On each invocation (the dispatcher batches your queue into your mailbox —
`reference/dispatch.md`): mailbox, then tracker — pending `[design-note]`s
```

and in the same paragraph, after `answer gates before doing anything slow.` append the sentence:
`Drain the whole queue in one boot, post per-[task] verdicts, then exit.`

- [ ] **Step 6: Implementer briefs — gate waits become deliver-and-exit.** In each of
`teams/roles/senior-full-stack-engineer.md`, `teams/roles/senior-frontend-engineer.md`, `teams/roles/senior-staff-engineer.md`, `teams/roles/senior-sre-engineer.md`, find the "wait for" phrase on their `[design-note]` bullet (grep `wait for`) and reword only that clause to the same pattern, keeping each brief's surrounding content, e.g. for senior-full-stack-engineer:
`— and wait for the` → `— then either receive `[design-approved]` this turn or deliver the note and exit; you'll be relaunched or messaged when the gate opens. Never write code before`

(Adapt the splice so the sentence stays grammatical in each file; the invariant that must survive verbatim in all four: **no code before `[design-approved]`**.)

- [ ] **Step 7: Verify no self-scheduling remains and commit**

Run: `grep -rn "relaunch on a schedule\|at least every .POLL_INTERVAL_SECONDS. when idle" reference/ roles/ teams/ ; echo "exit=$?"`
Expected: no matches, `exit=1`

```bash
git add reference/orchestration.md roles/team-lead.md roles/principal-architect.md teams/roles/
git commit -m "feat: end-of-turn-exit semantics — dispatcher owns time, briefs stop self-scheduling"
```

---

### Task 3: `tracker-ops.sh export` gains `blockedBy`

**Files:**
- Modify: `bin/tracker-ops.sh` (all four backends' `export`), `adapters/Markdown.md` (BlockedBy line convention)
- Test: `tests/tracker-ops-test.sh`

**Interfaces:**
- Produces: every task object in the export JSON gains `"blockedBy": [<taskId>, ...]` (possibly empty; GitHubIssues always `[]`). Task 4's decision engine consumes it.

- [ ] **Step 1: Write the failing test.** In `tests/tracker-ops-test.sh`, add to the fixture `feat/feature.md` task 2 section (after its `**Assignee:** —` line):

```
**BlockedBy:** 1
```

and extend the export assertion block (the `python3 -c` heredoc) with:

```python
assert byid['$T#2']['blockedBy'] == ['$T#1'], byid['$T#2'].get('blockedBy')
assert byid['$T#1']['blockedBy'] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash tests/tracker-ops-test.sh`
Expected: `FAIL: export writes JSON` (KeyError/assert on `blockedBy`)

- [ ] **Step 3: Implement.** In `bin/tracker-ops.sh`:

**Markdown.export** — inside the section loop, after the `am = re.search(...Assignee...)` line add:

```python
            bb = re.search(r'^\*\*BlockedBy:\*\* (.*)$', section, re.M)
            blocked_by = ['%s#%s' % (feature_id, n.strip().lstrip('#'))
                          for n in bb.group(1).split(',') if n.strip()] if bb else []
```

and add `'blockedBy': blocked_by,` to the appended task dict.

**Linear.export** — extend the GraphQL issue selection with
`inverseRelations { nodes { type issue { identifier } } }` and per issue:

```python
            blocked_by = [r['issue']['identifier']
                          for r in (i.get('inverseRelations') or {}).get('nodes', [])
                          if r.get('type') == 'blocks' and r.get('issue')]
```

(NOTE: in Linear's schema an inverse relation of type `blocks` means the related issue blocks this one; `issue` here is the *other* side — if the live API nests it as `relatedIssue`, use that key; verify against one live read before merging, and add `'blockedBy': blocked_by,` to the dict either way.)

**Jira.export** — add `issuelinks` to the `fields=` list and per issue:

```python
            blocked_by = [l['inwardIssue']['key'] for l in f.get('issuelinks', [])
                          if l.get('type', {}).get('name') == 'Blocks' and l.get('inwardIssue')]
```

**GitHubIssues.export** — add `'blockedBy': [],` (no native relation; dispatch is suggest-only there anyway).

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/tracker-ops-test.sh`
Expected: `ALL PASS`

- [ ] **Step 5: Document the Markdown convention.** In `adapters/Markdown.md`, in the task-section format description, add one line:

```markdown
- `**BlockedBy:** <n>[, <n>...]` — optional; task numbers in the same feature file. Read by `tracker-ops.sh export` into `blockedBy` (used by `bin/dispatch.sh` for unblock *suggestions* — Markdown is never auto-unblocked).
```

- [ ] **Step 6: Commit**

```bash
git add bin/tracker-ops.sh adapters/Markdown.md tests/tracker-ops-test.sh
git commit -m "feat: tracker-ops export carries blockedBy across all four adapters"
```

---

### Task 4: `bin/dispatch.sh` — the deterministic pass

**Files:**
- Create: `bin/dispatch.sh` (executable)
- Test: `tests/dispatch-test.sh` (new, executable)

**Interfaces:**
- Consumes: `tracker-ops.sh export` JSON incl. `blockedBy` (Task 3); `launch-team.sh start` for launches; `read_key` keys `POLL_INTERVAL_SECONDS`, `STUCK_AFTER_MINUTES`, `TEAMWORK_ROOT`.
- Produces: `plan:` output lines (`plan: launch <role> (<detail>)`, `plan: unblock <taskId> ...`), mailbox queue files `mailbox/<role>/NNN-dispatcher.md`.

- [ ] **Step 1: Write the failing test `tests/dispatch-test.sh`:**

```bash
#!/usr/bin/env bash
# dispatch smoke test: offline, Markdown adapter, stub agent commands.
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILURES=0
check() { local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok: $desc"; else echo "FAIL: $desc"; FAILURES=$((FAILURES+1)); fi; }

cd "$TMP"; git init -q repo && cd repo
git commit -q --allow-empty -m init; git checkout -q -b feat-team
mkdir -p .claude/skills/pm
cp -R "$SKILL_DIR/roles" "$SKILL_DIR/reference" "$SKILL_DIR/bin" "$SKILL_DIR/teams" .claude/skills/pm/
mkdir -p .claude/skills/pm/config
cp "$SKILL_DIR/config/statuses.config.json" .claude/skills/pm/config/
cat > .claude/skills/pm/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=Markdown
STATUS_CONFIG=config/statuses.config.json
```
EOF
cat > .claude/skills/pm/config/team.config.md <<'EOF'
```
TEAM_DEFAULT_CMD="true"
TEAMWORK_ROOT=.teamwork
POLL_INTERVAL_SECONDS=1
STUCK_AFTER_MINUTES=15
EXECUTION=sequential
VALIDATE_BUILD=null
VALIDATE_TEST=null
VALIDATE_LINT=null
```
EOF
DISPATCH=".claude/skills/pm/bin/dispatch.sh"

mkdir -p feat
cat > feat/feature.md <<'EOF'
# Fixture [Active]

## 1 Done thing [Ready to deploy]

**Assignee:** backend

> [review-request] round 1
> [review-approval] files ok — reviewer
> [architecture-approval] files ok — principal-architect

## 2 Blocked thing [Blocked]

**Assignee:** backend
**BlockedBy:** 1

Blocked on 1.

## 3 In review [Review]

**Assignee:** backend

> [review-request] please review — backend

## 4 Needs design verdict [Active]

**Assignee:** backend

> [design-note] approach — backend

## 5 Ready to start [Planned]

**Assignee:** —

Independent.

## 6 Dual approved [Review]

**Assignee:** backend

> [review-request] round 1 — backend
> [review-approval] files ok — reviewer
> [architecture-approval] files ok — principal-architect
EOF
FID="feat/feature.md"

# -- dry-run prints the full action plan, changes nothing ----------------------
plan="$(TEAM_RUNNER=background "$DISPATCH" feat-team "$FID" --once --dry-run --unblock=auto)"
echo "$plan" | grep -q "unblock $FID#2" && echo "ok: plans unblock" || { echo "FAIL: plans unblock"; FAILURES=$((FAILURES+1)); }
echo "$plan" | grep -q "launch reviewer" && echo "ok: plans reviewer queue" || { echo "FAIL: reviewer queue"; FAILURES=$((FAILURES+1)); }
echo "$plan" | grep -q "launch principal-architect" && echo "ok: plans PA queue" || { echo "FAIL: PA queue"; FAILURES=$((FAILURES+1)); }
echo "$plan" | grep -q "launch team-lead" && echo "ok: plans lead (Planned #5)" || { echo "FAIL: lead launch"; FAILURES=$((FAILURES+1)); }
echo "$plan" | grep -q "launch integrator" && echo "ok: plans integrator merge queue (#6)" || { echo "FAIL: integrator queue"; FAILURES=$((FAILURES+1)); }
check "dry-run does not move status" grep -q '^## 2 Blocked thing \[Blocked\]$' "$FID"
check "dry-run launches nothing"     test ! -d .teamwork/feat-team/pids

# -- real pass: auto-unblock writes, queues land in mailboxes, roles launch ----
TEAM_RUNNER=background "$DISPATCH" feat-team "$FID" --once --unblock=auto
check "auto-unblock moved status"    grep -q '^## 2 Blocked thing \[Active\]$' "$FID"
check "auto-unblock left a comment"  grep -q 'Auto-unblocked by dispatcher' "$FID"
check "reviewer queue in mailbox"    grep -rq "$FID#3" .teamwork/feat-team/mailbox/reviewer/
check "PA queue in mailbox"          grep -rq "$FID#4" .teamwork/feat-team/mailbox/principal-architect/
check "reviewer launched"            test -f .teamwork/feat-team/pids/reviewer.pid

# -- dedup: a live pid suppresses relaunch -------------------------------------
mkdir -p .teamwork/feat-team/pids
echo $$ > .teamwork/feat-team/pids/reviewer.pid
plan2="$(TEAM_RUNNER=background "$DISPATCH" feat-team "$FID" --once --dry-run)"
echo "$plan2" | grep -q "launch reviewer — skipped (live instance)" \
  && echo "ok: dedup skips live reviewer" || { echo "FAIL: dedup"; FAILURES=$((FAILURES+1)); }

# -- suggest mode: Markdown default never writes -------------------------------
# (state resets between blocks use sed on the fixture file, never tracker ops)
sed -i '' 's/^## 2 Blocked thing \[Active\]$/## 2 Blocked thing [Blocked]/' "$FID"
plan3="$(TEAM_RUNNER=background "$DISPATCH" feat-team "$FID" --once --dry-run)"
echo "$plan3" | grep -q "unblock $FID#2 — SUGGESTED" \
  && echo "ok: Markdown defaults to suggest-only" || { echo "FAIL: suggest default"; FAILURES=$((FAILURES+1)); }

# -- nothing actionable exits cleanly ------------------------------------------
cat > feat/quiet.md <<'EOF'
# Quiet [Active]

## 1 Done [Ready to deploy]

**Assignee:** backend
EOF
out="$("$DISPATCH" feat-team feat/quiet.md --once --dry-run)"
echo "$out" | grep -q "nothing actionable" && echo "ok: clean exit" || { echo "FAIL: clean exit"; FAILURES=$((FAILURES+1)); }

echo "---"
[ "$FAILURES" -eq 0 ] && echo "ALL PASS" || { echo "$FAILURES FAILURE(S)"; exit 1; }
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash tests/dispatch-test.sh`
Expected: fails — `dispatch.sh` does not exist.

- [ ] **Step 3: Implement `bin/dispatch.sh`:**

```bash
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

read_key() { # from team.config.md; quotes stripped; null -> empty
  local line; line="$(grep -m1 "^$1=" "$CONFIG" || true)"
  line="${line#*=}"; line="${line%\"}"; line="${line#\"}"
  [ "$line" = "null" ] && line=""
  printf '%s' "$line"
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
  local a; a="$(grep -m1 '^PRODUCT_MANAGEMENT_TOOL=' "$PM_CONFIG" | cut -d= -f2 | tr -d '"' || true)"
  case "${TRACKER_ADAPTER:-$a}" in Linear|Jira) echo auto ;; *) echo suggest ;; esac
}

dispatch_once() { # dispatch_once <team> <featureId> <dry:yes|no> <unblock>
  local team="$1" fid="$2" dry="$3" unblock="$4"
  local dir; dir="$(teamroot "$team")"
  mkdir -p "$dir"
  "$SKILL_DIR/bin/tracker-ops.sh" export "$fid" "$dir/tasks.json" >/dev/null
  local stuck; stuck="$(read_key STUCK_AFTER_MINUTES)"; stuck="${stuck:-15}"
  local plan
  plan="$(python3 - "$SKILL_DIR" "$dir" "$stuck" 3<&- <<'PYEOF'
import json, os, sys, time
skill, workdir, stuck_min = sys.argv[1], sys.argv[2], int(sys.argv[3])
board = json.load(open(os.path.join(skill, 'config', 'statuses.config.json')))
terminal = {s['name'] for s in board['tasks']['statuses'] if s.get('terminal')}
tasks = json.load(open(os.path.join(workdir, 'tasks.json')))['tasks']
by_id = {str(t['taskId']): t for t in tasks}

def last(t, *names):  # index of the last comment starting with any [name]
    idx = -1
    for i, c in enumerate(t.get('comments') or []):
        b = (c.get('body') or '').lstrip()
        if any(b.startswith('[%s]' % n) for n in names):
            idx = i
    return idx

def blockers_terminal(t):
    bb = t.get('blockedBy') or []
    return all(by_id.get(str(b), {}).get('status') in terminal for b in bb)

acts = []
for t in tasks:  # 1. auto-unblock candidates
    if t.get('status') == 'Blocked' and (t.get('blockedBy') or []) and blockers_terminal(t):
        acts.append(('unblock', str(t['taskId']), ''))

design_q = [str(t['taskId']) for t in tasks
            if last(t, 'design-note') > last(t, 'design-approved', 'design-pushback')]
review_q, arch_q, merge_q = [], [], []
for t in tasks:
    if t.get('status') != 'Review':
        continue
    tid, req = str(t['taskId']), last(t, 'review-request')
    ra, aa = last(t, 'review-approval'), last(t, 'architecture-approval')
    if ra > req and aa > req:
        merge_q.append(tid)
    else:
        if ra <= req: review_q.append(tid)
        if aa <= req: arch_q.append(tid)

if design_q or arch_q:
    acts.append(('launch', 'principal-architect',
                 'Dispatch queue — design gates: %s; architecture reviews: %s. '
                 'Drain every item, post per-[task] markers, exit.'
                 % (', '.join(design_q) or 'none', ', '.join(arch_q) or 'none')))
if review_q:
    acts.append(('launch', 'reviewer',
                 'Dispatch queue — [Review]: %s. Drain every item, post per-[task] verdicts, exit.'
                 % ', '.join(review_q)))
if merge_q:
    acts.append(('launch', 'integrator',
                 'Dispatch queue — dual-approved, integrate in dependency order: %s. '
                 'Per-[task] atomic commit+move, then exit.' % ', '.join(merge_q)))

planned = [str(t['taskId']) for t in tasks
           if t.get('status') == 'Planned' and not t.get('assignee') and blockers_terminal(t)]
stale = []
hb = os.path.join(workdir, 'heartbeats')
if os.path.isdir(hb):
    now = time.time()
    stale = [f for f in os.listdir(hb)
             if now - os.path.getmtime(os.path.join(hb, f)) > stuck_min * 60]
if planned or stale:
    acts.append(('launch', 'team-lead',
                 'Lead-actionable — dispatchable [Planned]: %s; stale heartbeats: %s. '
                 'One supervision pass, then exit.'
                 % (', '.join(planned) or 'none', ', '.join(stale) or 'none')))

for a in acts:
    print('\t'.join(a))
PYEOF
)"
  if [ -z "$plan" ]; then echo "dispatch: nothing actionable"; return 0; fi
  local action arg detail
  while IFS="$(printf '\t')" read -r action arg detail; do
    case "$action" in
      unblock)
        case "$unblock" in
          off)     echo "plan: unblock $arg — suppressed (--unblock=off)" ;;
          suggest) echo "plan: unblock $arg — SUGGESTED (confirm and move via the team-lead; see reference/dispatch.md)" ;;
          auto)
            echo "plan: unblock $arg (all blockers terminal)"
            if [ "$dry" != "yes" ]; then
              "$SKILL_DIR/bin/tracker-ops.sh" state "$arg" Active
              printf 'Auto-unblocked by dispatcher: every blocking [task] reached the terminal status.\n\n— dispatcher (on behalf of team-lead)\n' \
                | "$SKILL_DIR/bin/tracker-ops.sh" comment "$arg" -
            fi ;;
          *) die "unknown --unblock mode '$unblock'" ;;
        esac ;;
      launch)
        if role_live "$team" "$arg"; then
          echo "plan: launch $arg — skipped (live instance)"
        else
          echo "plan: launch $arg ($detail)"
          if [ "$dry" != "yes" ]; then
            local mf; mf="$(next_mailbox_file "$dir/mailbox/$arg")"
            printf 'From: dispatcher\nRe: %s\n---\n%s\n' "$fid" "$detail" > "$mf"
            "$SKILL_DIR/bin/launch-team.sh" start "$team" "$fid" "$arg"
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
      dispatch_once "$TEAM" "$FID" no "$UNBLOCK" || echo "dispatch: pass failed — retrying next interval" >&2
      sleep "$INTERVAL"
    done ;;
  *) die "mode must be --once or --watch" ;;
esac
```

`chmod +x bin/dispatch.sh`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/dispatch-test.sh && bash tests/launcher-test.sh && bash tests/tracker-ops-test.sh`
Expected: `ALL PASS` ×3

- [ ] **Step 5: Commit**

```bash
git add bin/dispatch.sh tests/dispatch-test.sh
git commit -m "feat: bin/dispatch.sh — deterministic event->launch pass (--once/--watch, dedup, auto-unblock)"
```

---

### Task 5: `launch-team.sh preflight`

**Files:**
- Modify: `bin/launch-team.sh` (new `preflight` function + case; `compose_prompt` injection; `team` auto-runs it), `adapters/_TEMPLATE.md` (Initialization note)
- Test: `tests/launcher-test.sh`

**Interfaces:**
- Consumes: `tracker-ops.sh export` (Task 3) as the read probe.
- Produces: `.teamwork/<team>/preflight/utc.txt`, `.teamwork/<team>/preflight/tool-prefix.txt` (LLM-written for MCP adapters); composed prompts carry `Preflight UTC pin:` and `Verified tracker tool prefix:` lines. `SKIP_PREFLIGHT=1` env bypass.

- [ ] **Step 1: Write the failing tests.** In `tests/launcher-test.sh`:

(a) The existing fixture has no PM config, so `team` launches must bypass preflight: prefix the two existing `"$LAUNCH" team ...` invocations (FEAT-2 and the `nonesuch` refusal) with `SKIP_PREFLIGHT=1 `.

(b) Append new preflight tests before the `status + stop` section:

```bash
# -- preflight: aborts before any launch when the adapter probe fails -----------
cat > .claude/skills/pm/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=Markdown
STATUS_CONFIG=config/statuses.config.json
```
EOF
if out="$(TEAM_RUNNER=background "$LAUNCH" team full-stack pf-team missing/feature.md 2>&1)"; then
  echo "FAIL: preflight should abort on a broken adapter read"; FAILURES=$((FAILURES+1))
elif printf '%s' "$out" | grep -q "preflight"; then
  echo "ok: preflight aborts team launch on probe failure"
else
  echo "FAIL: wrong preflight abort message: $out"; FAILURES=$((FAILURES+1))
fi
check "preflight abort launched nothing" test ! -d .teamwork/pf-team/prompts

# -- preflight: passes on a working adapter; prompts carry the UTC pin ----------
mkdir -p pf && printf '# F [Planned]\n\n## 1 T [Planned]\n\n**Assignee:** —\n\nx.\n' > pf/feature.md
TEAM_RUNNER=background "$LAUNCH" start pf-team pf/feature.md backend   # start skips preflight
"$LAUNCH" preflight pf-team pf/feature.md
check "preflight writes UTC pin" test -s .teamwork/pf-team/preflight/utc.txt
out="$("$LAUNCH" compose pf-team pf/feature.md backend)"
check "composed prompt carries UTC pin" grep -q "Preflight UTC pin" "$out"

# -- preflight: MCP-style adapter needs the recorded tool prefix ----------------
cat > .claude/skills/pm/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=SomeMcpTool
STATUS_CONFIG=config/statuses.config.json
```
EOF
if "$LAUNCH" preflight pf-team pf/feature.md >/dev/null 2>&1; then
  echo "FAIL: MCP adapter without tool-prefix.txt should fail preflight"; FAILURES=$((FAILURES+1))
else
  echo "ok: MCP preflight demands a recorded tool prefix"
fi
printf 'mcp__sometool__' > .teamwork/pf-team/preflight/tool-prefix.txt
check "MCP preflight passes with prefix on record" "$LAUNCH" preflight pf-team pf/feature.md
out="$("$LAUNCH" compose pf-team pf/feature.md backend)"
check "composed prompt carries verified prefix" grep -q "mcp__sometool__" "$out"
```

(c) Restore the Markdown PM config after these tests (some earlier sections rerun on `compose`):

```bash
cat > .claude/skills/pm/config/project-management.config.md <<'EOF'
```
PRODUCT_MANAGEMENT_TOOL=Markdown
STATUS_CONFIG=config/statuses.config.json
```
EOF
```

- [ ] **Step 2: Run to verify failure**

Run: `bash tests/launcher-test.sh`
Expected: FAILs on the new preflight checks (`unknown subcommand`).

- [ ] **Step 3: Implement.** In `bin/launch-team.sh`:

(a) New function after `validate_board`:

```bash
preflight() { # preflight <team> <featureId> — fail before five agents do
  local team="$1" fid="$2"
  local dir; dir="$(teamroot "$team")"
  validate_board >/dev/null
  mkdir -p "$dir/preflight" 2>/dev/null || die "preflight: cannot create workspace $dir"
  ( : > "$dir/preflight/.write-test" && rm "$dir/preflight/.write-test" ) \
    || die "preflight: workspace not writable: $dir"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$dir/preflight/utc.txt"
  local probe_err
  if probe_err="$("$SKILL_DIR/bin/tracker-ops.sh" export "$fid" /dev/null 2>&1 >/dev/null)"; then
    echo "preflight OK: adapter read verified, workspace writable, UTC pinned"
  elif printf '%s' "$probe_err" | grep -q "no tracker-ops backend" \
       && [ -s "$dir/preflight/tool-prefix.txt" ]; then
    echo "preflight OK: MCP tool prefix on record ($(cat "$dir/preflight/tool-prefix.txt")), workspace writable, UTC pinned"
  else
    die "preflight FAILED — no agent was launched.
  probe: $probe_err
  Scriptable adapter (REST/CLI/files): fix credentials/config, then verify with:
    bin/tracker-ops.sh export $fid /dev/null
  MCP adapter: run ONE probe agent that loads the tracker tools (deferred tools
  via ToolSearch), performs one read, and writes the exact tool prefix
  (e.g. mcp__linear__) to $dir/preflight/tool-prefix.txt — then relaunch."
  fi
}
```

(b) In `compose_prompt`, after the `echo "- Team workspace: $dir"` line insert:

```bash
    if [ -s "$dir/preflight/utc.txt" ]; then
      echo "- Preflight UTC pin: $(cat "$dir/preflight/utc.txt") — generate every timestamp with: date -u +%Y-%m-%dT%H:%M:%SZ"
    fi
    if [ -s "$dir/preflight/tool-prefix.txt" ]; then
      echo "- Verified tracker tool prefix: $(cat "$dir/preflight/tool-prefix.txt") (preflight-verified — use it verbatim; do not re-derive from adapter docs)"
    fi
```

(c) In the `team)` case, after `validate_board >/dev/null` add:

```bash
    [ "${SKIP_PREFLIGHT:-}" = "1" ] || preflight "$team" "$fid"
```

(d) New case + usage-line update:

```bash
  preflight)
    [ $# -eq 3 ] || die "usage: preflight <team> <featureId>"
    preflight "$2" "$3"
    ;;
```

and add `preflight` to both usage strings (header comment and the `*` case).

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/launcher-test.sh`
Expected: `ALL PASS`

- [ ] **Step 5: Adapter template note.** In `adapters/_TEMPLATE.md`, in the *Initialization* section, add:

```markdown
> Executed **once by `launch-team.sh preflight`** (automatic before `team`), not
> per-agent. Agents receive the verified access mechanism (and, for MCP tools, the
> verified tool prefix) in their startup prompt and must not re-derive it.
```

- [ ] **Step 6: Commit**

```bash
git add bin/launch-team.sh adapters/_TEMPLATE.md tests/launcher-test.sh
git commit -m "feat: launcher preflight — adapter probe, workspace write test, UTC pin, verified tool prefix injection"
```

---

## Phase B — claims & authorization

### Task 6: Provisioned, attempt-bound worktrees + cleanup

**Files:**
- Modify: `bin/launch-team.sh` (`worktree` case, new `worktree-remove` case, usage), `config/team.config.md` (`WORKTREE_SETUP` key), `reference/orchestration.md` (workspace tree line, Claiming step 5, Relaunch hygiene), `roles/integrator.md` (worktree path + cleanup step)
- Test: `tests/launcher-test.sh`

**Interfaces:**
- Produces: worktree path scheme `worktrees/<role>#<attempt>-<taskId>` (branch stays `<role>-<taskId>`); subcommands `worktree <team> <role> <taskId> [attempt]` and `worktree-remove <team> <role> <taskId> [attempt]`; config key `WORKTREE_SETUP=null`.

- [ ] **Step 1: Update/extend tests.** In `tests/launcher-test.sh`:

(a) Update the existing worktree assertions: every `worktrees/backend-T-42` → `worktrees/backend#1-T-42` (three places incl. the `git worktree remove` re-add test). Branch-name assertion stays `backend-T-42`.

(b) Append after the re-add test:

```bash
# -- worktree provisioning: WORKTREE_SETUP runs once, fail-loud -----------------
CFG_WT=.claude/skills/pm/config/team.config.md
printf 'WORKTREE_SETUP="touch provisioned.txt"\n' >> "$CFG_WT"
"$LAUNCH" worktree test-feature backend T-77
check "WORKTREE_SETUP provisioned the tree" test -f .teamwork/test-feature/worktrees/backend#1-T-77/provisioned.txt
sed -i '' '/^WORKTREE_SETUP="touch provisioned.txt"$/d' "$CFG_WT"
printf 'WORKTREE_SETUP="false"\n' >> "$CFG_WT"
if "$LAUNCH" worktree test-feature backend T-78 >/dev/null 2>&1; then
  echo "FAIL: failing WORKTREE_SETUP should die"; FAILURES=$((FAILURES+1))
else
  echo "ok: failing WORKTREE_SETUP is fail-loud"
fi
check "failed provisioning removed the tree" test ! -d .teamwork/test-feature/worktrees/backend#1-T-78
sed -i '' '/^WORKTREE_SETUP="false"$/d' "$CFG_WT"

# -- attempt-bound relaunch isolation ------------------------------------------
"$LAUNCH" worktree-remove test-feature backend T-77
check "worktree-remove cleaned the dir" test ! -d .teamwork/test-feature/worktrees/backend#1-T-77
git worktree list | grep -q 'backend#1-T-77' && { echo "FAIL: stale worktree registration"; FAILURES=$((FAILURES+1)); } || echo "ok: worktree pruned"
"$LAUNCH" worktree test-feature backend T-77 2
check "attempt 2 gets a fresh tree on the same branch" test -d .teamwork/test-feature/worktrees/backend#2-T-77
[ "$(git -C .teamwork/test-feature/worktrees/backend#2-T-77 rev-parse --abbrev-ref HEAD)" = "backend-T-77" ] \
  && echo "ok: attempt 2 reuses branch backend-T-77" || { echo "FAIL: attempt-2 branch"; FAILURES=$((FAILURES+1)); }
```


- [ ] **Step 2: Run to verify failure**

Run: `bash tests/launcher-test.sh`
Expected: FAILs (old path scheme, unknown `worktree-remove`, no provisioning).

- [ ] **Step 3: Implement.** Replace the `worktree)` case in `bin/launch-team.sh` with:

```bash
  worktree)
    [ $# -ge 4 ] && [ $# -le 5 ] || die "usage: worktree <team> <role> <taskId> [attempt]"
    team="$2"; role="$3"; task="$4"; attempt="${5:-1}"
    case "$attempt" in ''|*[!0-9]*) die "attempt must be a positive integer" ;; esac
    wt="$(teamroot "$team")/worktrees/$role#$attempt-$task"
    [ -d "$wt" ] && { echo "$wt"; exit 0; }
    mkdir -p "$(dirname "$wt")"
    if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$role-$task"; then
      git -C "$REPO_ROOT" worktree add "$wt" "$role-$task" >/dev/null
    else
      git -C "$REPO_ROOT" worktree add "$wt" -b "$role-$task" "$team" >/dev/null
    fi
    setup="$(read_key WORKTREE_SETUP)"
    if [ -n "$setup" ]; then
      if ! ( cd "$wt" && eval "$setup" ) >/dev/null 2>&1; then
        git -C "$REPO_ROOT" worktree remove --force "$wt" >/dev/null 2>&1 || true
        git -C "$REPO_ROOT" worktree prune
        die "WORKTREE_SETUP failed in $wt — worktree removed. Fix the command or the environment; never claim validations in an unprovisioned tree."
      fi
    fi
    echo "$wt"
    ;;
  worktree-remove)
    [ $# -ge 4 ] && [ $# -le 5 ] || die "usage: worktree-remove <team> <role> <taskId> [attempt]"
    wt="$(teamroot "$2")/worktrees/$3#${5:-1}-$4"
    git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null || true
    git -C "$REPO_ROOT" worktree prune
    echo "removed $wt (registration pruned)"
    ;;
```

Add both subcommands to the usage strings.

- [ ] **Step 4: `config/team.config.md` — new key.** In the *Validation commands* fenced block's sibling *Coordination* block, after the `MAX_ACTIVE_IMPLEMENTERS` entry add:

```
WORKTREE_SETUP=null              # Run once inside every freshly created worktree, fail-loud
                                 # (e.g. "pnpm install --frozen-lockfile && pnpm build").
                                 # null = bare worktree. Provisioning is what makes
                                 # implementer validation claims executable — an
                                 # unprovisioned tree produced the false-green failure class.
```

- [ ] **Step 5: Doc alignment.**

(a) `reference/orchestration.md` workspace tree: `├── worktrees/<role>-<taskId>/   # implementer working copies (parallel execution only)` → `├── worktrees/<role>#<attempt>-<taskId>/ # per-instance working copies, provisioned via WORKTREE_SETUP (parallel execution only)`.

(b) Claiming step 5: after ``create your worktree — `bin/launch-team.sh worktree <team> <role> <taskId>`.`` append: `The worktree is your **instance's** scratch space (attempt-numbered); it arrives provisioned when `WORKTREE_SETUP` is set — validation claims may only cite commands actually executed inside it.`

(c) Relaunch hygiene, parallel clause: `parallel execution — move the dead instance's worktree aside and let the successor recreate it;` → `parallel execution — discard the dead instance's worktree (`bin/launch-team.sh worktree-remove <team> <role> <taskId> [attempt]`; use `git worktree move` first if salvage is on the table) and let the successor create attempt N+1 on the same task branch;`

(d) `roles/integrator.md`: pipeline location line `(\`<TEAMWORK_ROOT>/<team>/worktrees/<role>-<taskId>\`, ...)` → `(\`<TEAMWORK_ROOT>/<team>/worktrees/<role>#<attempt>-<taskId>\`, latest attempt, `<role>` from the [task]'s assignee)`; step 6 `remove the worktree (\`git worktree remove\`).` → `remove the worktree and its registration: \`bin/launch-team.sh worktree-remove <team> <role> <taskId> [attempt]\` (runs \`git worktree remove --force\` + \`git worktree prune\` — a leaked registration blocks the next feature-branch checkout).`

- [ ] **Step 6: Run tests, commit**

Run: `bash tests/launcher-test.sh`
Expected: `ALL PASS`

```bash
git add bin/launch-team.sh config/team.config.md reference/orchestration.md roles/integrator.md tests/launcher-test.sh
git commit -m "feat: provisioned attempt-bound worktrees (WORKTREE_SETUP) + worktree-remove cleanup"
```

---

### Task 7: Marker-ownership table + integrator authorization

**Files:**
- Modify: `config/statuses.config.json` (new top-level `markers`), `bin/launch-team.sh` (`validate_board` python), `roles/integrator.md` (step 1.5), `reference/orchestration.md` (Structured comments intro), `roles/principal-architect.md`, `roles/reviewer.md`, `roles/qa.md`, `roles/team-lead.md`, `teams/roles/senior-qa-engineer.md` (allowed-markers lines)
- Test: `tests/launcher-test.sh`

**Interfaces:**
- Produces: board config schema gains optional `"markers": { "<marker>": { "authorizedRoles": [<role>, ...] } }`; presence of the table = integrator enforces; absence = check skipped (backward compat).

- [ ] **Step 1: Write the failing tests.** In `tests/launcher-test.sh`, after the existing `bad ...` block add:

```bash
GOODTASKS='"tasks":{"statuses":[{"name":"A","initial":true,"owner":{"role":"team-lead"},"transitions":["Z"]},{"name":"Z","terminal":true,"owner":{"role":"team-lead"},"transitions":[]}]}'
bad "markers with unknown role refused"  "unknown role" "{$MINF,$GOODTASKS,\"markers\":{\"review-approval\":{\"authorizedRoles\":[\"nobody-such\"]}}}"
bad "markers with empty list refused"    "non-empty list" "{$MINF,$GOODTASKS,\"markers\":{\"review-approval\":{\"authorizedRoles\":[]}}}"
bad "markers non-object refused"         "must be a non-empty object" "{$MINF,$GOODTASKS,\"markers\":[]}"
check "shipped config still passes with markers" "$LAUNCH" validate-board
check "integrator prompt carries the markers table" grep -q '"authorizedRoles"' .teamwork/test-feature/prompts/integrator.md
```

(The integrator prompt exists from the earlier `team full-stack` launch; board config is already composed into every prompt, so the check passes once the shipped config gains the table.)

- [ ] **Step 2: Run to verify failure**

Run: `bash tests/launcher-test.sh`
Expected: the three `bad` markers checks FAIL (configs accepted), plus the prompt check FAILs.

- [ ] **Step 3: Implement config + validation.**

(a) `config/statuses.config.json` — add a top-level key after `"tasks"`:

```json
  "markers": {
    "design-approved":       { "authorizedRoles": ["principal-architect"] },
    "design-pushback":       { "authorizedRoles": ["principal-architect"] },
    "architecture-approval": { "authorizedRoles": ["principal-architect"] },
    "review-approval":       { "authorizedRoles": ["reviewer", "qa"] },
    "review-findings":       { "authorizedRoles": ["reviewer", "qa", "principal-architect"] },
    "product-approval":      { "authorizedRoles": ["team-lead", "senior-technical-product-manager"] },
    "product-pushback":      { "authorizedRoles": ["team-lead", "senior-technical-product-manager"] }
  }
```

(b) `validate_board` python — before the final `if errors:` block add:

```python
markers = cfg.get("markers")
if markers is not None:
    if not isinstance(markers, dict) or not markers:
        errors.append("markers: must be a non-empty object of marker -> {authorizedRoles: [...]}")
    else:
        for mname, spec in markers.items():
            roles = (spec or {}).get("authorizedRoles") if isinstance(spec, dict) else None
            if not isinstance(roles, list) or not roles:
                errors.append("markers/%s: 'authorizedRoles' must be a non-empty list" % mname)
                continue
            for r in roles:
                if not role_exists(r):
                    errors.append("markers/%s: unknown role '%s'" % (mname, r))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/launcher-test.sh`
Expected: `ALL PASS`

- [ ] **Step 5: Enforcement + docs.**

(a) `roles/integrator.md` — insert between pipeline steps 1 and 2 (renumber is not needed; use "1.5" literally):

```markdown
1.5 **Authorization check** (skip only if the board config has no `markers`
   table). For each required approval comment, read its signature (`— <role>`;
   in scribe mode the *authoring* role before `(posted by team-lead)`). Resolve
   a specialized signer to the protocol role(s) it acts as (its brief's
   *Protocol mapping*, restated on the [task] at first signing). The resolved
   role must be in that marker's `authorizedRoles` list, and the signer must
   not be the [task]'s implementer (**no self-approval** — when the only
   authorized role IS the implementer, an independent verifier must have been
   substituted and its signature is the one you check). Unauthorized or
   self-signed approval → `[andon]`, [task] back to `[Active]` — no override
   path, regardless of who asks.
```

(b) `reference/orchestration.md` — in *Structured comments*, after the "never invent new ones, never misspell them." sentence add:

```markdown
Marker **authorship is enforced, not narrated**: the board config's `markers`
table names the role(s) authorized to post each gate marker (presets may
override it). The integrator refuses any approval whose signer is not
authorized (its step 1.5). When a marker's only authorized role is the [task]'s
own implementer, an **independent verifier** from the roster substitutes — no
role ever approves its own work; none available → `[andon]`.
```

(c) One line near the top of each brief's "You own"/role statement:
- `roles/principal-architect.md`: `Markers you are authorized to post: [design-approved], [design-pushback], [architecture-approval], [review-findings].`
- `roles/reviewer.md`: `Markers you are authorized to post: [review-approval], [review-findings].`
- `roles/qa.md`: `Markers you are authorized to post: [review-approval], [review-findings].`
- `roles/team-lead.md`: `Markers you are authorized to post: [handoff], [escalation], [product-approval]/[product-pushback] (only where no product role exists) — never any review or design approval.`
- `teams/roles/senior-qa-engineer.md`: `Markers you are authorized to post: [review-approval], [review-findings] (as reviewer/qa).`

- [ ] **Step 6: Run both suites, commit**

Run: `bash tests/launcher-test.sh && bash tests/tracker-ops-test.sh`
Expected: `ALL PASS` ×2

```bash
git add config/statuses.config.json bin/launch-team.sh roles/ teams/roles/senior-qa-engineer.md reference/orchestration.md tests/launcher-test.sh
git commit -m "feat: marker-ownership table + integrator authorization check (no self-approval)"
```

---

### Task 8: Evidence ledger + trust-then-spot-check policy (docs)

**Files:**
- Modify: `reference/orchestration.md` (`[review-request]` marker row + new section), `roles/principal-architect.md`, `roles/qa.md`, `teams/roles/senior-qa-engineer.md`, `roles/integrator.md`, `roles/backend.md`, `roles/frontend.md`, `teams/_PLAYBOOK.md` (REVIEW template Evidence line)

**Interfaces:**
- Produces: the `Evidence:` block format and the `NOT validated:` section name; the severity label `trust-breach (severity: critical)`. Task 12 references artifact paths under `.teamwork/<team>/artifacts/<taskId>/`.

- [ ] **Step 1: `[review-request]` marker definition.** In `reference/orchestration.md`'s marker table, replace the `[review-request]` row's content with:

```markdown
| `[review-request]` | implementer | Ready for review: what changed, list of changed files, an **evidence record per validated command** (see *Evidence and re-execution*), an explicit `NOT validated:` section for anything not run (with reason), and any index-only staging operation performed. A claimed result without its evidence record **is** NOT validated. Written when moving to `[Review]`. |
```

- [ ] **Step 2: New section.** In `reference/orchestration.md`, insert after the *Dual review* section:

```markdown
## Evidence and re-execution — verify instead of re-derive

Every validated command in a `[review-request]` carries an evidence record:

```
Evidence:
  commit:   <sha of the working copy HEAD when the command ran>
  command:  <exact command>
  exit:     <code>
  counts:   <e.g. 47 passed, 0 failed, 2 skipped>
  duration: <seconds>
  log:      <TEAMWORK_ROOT>/<team>/artifacts/<taskId>/validate-<round>-<role>.log
NOT validated:
  <command> — <reason (e.g. worktree unprovisioned, N/A for this change)>
```

Who executes suites (the two independent executions that catch real defects are
**never** traded away):

| Role | Suites | Condition |
|---|---|---|
| Implementer | runs; records evidence | always — in the provisioned working copy |
| Principal architect | inspect + spot-check, no blind re-run | only while `Evidence.commit` == branch HEAD; else re-run |
| QA final gate | **always re-runs** | unconditional — evidence is context, not gate |
| Integrator | **always re-runs** | unconditional |

Any mismatch between an evidence record and a re-run (exit code or counts) is an
automatic `[review-findings]` labeled `trust-breach (severity: critical)` —
resolvable only by a fresh implementer run and a new record, never by explanation.
```

- [ ] **Step 3: Brief lines.**
- `roles/principal-architect.md` checkpoint 3, append: `If the [review-request]'s evidence record is complete and its commit equals the branch HEAD, inspect and spot-check — do not re-run suites blind; a stale or missing record means you re-run (protocol: *Evidence and re-execution*).`
- `roles/qa.md` and `teams/roles/senior-qa-engineer.md`, append to their review duties: `You always re-run the applicable suites yourself — the implementer's evidence record is context, never a substitute (protocol: *Evidence and re-execution*). A result that contradicts the record is a `[review-findings]` labeled `trust-breach (severity: critical)`.`
- `roles/integrator.md` step 4, append: `Your run is always independent — evidence records never substitute for it; a contradiction with the record is a trust-breach finding (protocol: *Evidence and re-execution*).`
- `roles/backend.md` and `roles/frontend.md`, in their review-request duty: `Your `[review-request]` carries an evidence record per validated command and a `NOT validated:` section for the rest — claiming a result without its record is a protocol violation equal to not running it.`
- `teams/_PLAYBOOK.md`: in the REVIEW template/checklist, change the Evidence line (grep `Evidence`) to reference the record: `Evidence: the [review-request]'s evidence records (commit, command, exit, counts, log path) — spot-check per the protocol's *Evidence and re-execution* matrix; QA re-runs regardless.`

- [ ] **Step 4: Verify and commit**

Run: `grep -q "Evidence and re-execution" reference/orchestration.md && grep -q "trust-breach" roles/qa.md && bash tests/launcher-test.sh >/dev/null && echo OK`
Expected: `OK`

```bash
git add reference/orchestration.md roles/ teams/roles/senior-qa-engineer.md teams/_PLAYBOOK.md
git commit -m "feat: evidence ledger + trust-then-spot-check review policy (QA/integrator always re-run)"
```

---

## Phase C — batching & integrator v2

### Task 9: Queue-consumer gate briefs

**Files:**
- Modify: `roles/reviewer.md`, `roles/qa.md`, `roles/principal-architect.md` (done partially in Task 2 — verify), `roles/integrator.md`, `teams/roles/senior-qa-engineer.md`, `teams/_PLAYBOOK.md`

**Interfaces:**
- Consumes: dispatcher queue message format from Task 4 (`Dispatch queue — ...: <taskId>, <taskId>`).

- [ ] **Step 1: Add the queue-consumer opener** to `roles/reviewer.md`, `roles/qa.md`, and `teams/roles/senior-qa-engineer.md` (near the top, after the role statement):

```markdown
**You are launched as a queue consumer.** On boot, read your mailbox: the
dispatcher (or lead) lists every [task] awaiting you. No queue message → query
the tracker for every [task] in your owned status. Either way, **drain the whole
queue in one boot** — an independent per-[task] verdict comment for each (same
rigor as one-at-a-time; batching shares the boot, never the judgment) — then exit.
```

- [ ] **Step 2: Integrator queue framing.** In `roles/integrator.md`, replace the *Ordering* section's first sentence `When several [tasks] await integration, merge in dependency order (backend before the frontend that consumes it).` with:

```markdown
You drain the whole merge queue in one boot: every dual-approved [task], in
dependency order (backend before the frontend that consumes it — the dispatcher
or lead passes the order; absent that, derive it from `blockedBy` and
`CONTRACTS.md`), each through the full pipeline with its own atomic commit+move.
```

- [ ] **Step 3: Playbook language.** In `teams/_PLAYBOOK.md`, where review dispatch is described (grep `enters \[Review\]` or the REVIEW assignment template), add one sentence: `Gate roles drain queues: one boot reviews every [task] currently awaiting that gate ("drain the [Review] queue"), posting per-[task] verdicts — never one boot per [task] when several wait.`

- [ ] **Step 4: Verify Task 2's PA edit already covers the PA queue** (`Drain the whole queue in one boot`) — if missing, add it per Task 2 Step 5.

- [ ] **Step 5: Verify and commit**

Run: `grep -l "queue consumer\|drain the whole" roles/reviewer.md roles/qa.md roles/integrator.md teams/roles/senior-qa-engineer.md teams/_PLAYBOOK.md | wc -l`
Expected: `5`

```bash
git add roles/ teams/
git commit -m "feat: gate roles are queue consumers — one boot drains the whole gate queue"
```

---

### Task 10: Integrator v2 — `VALIDATE_FORMAT`, stale-base rule

**Files:**
- Modify: `config/team.config.md` (new key), `roles/integrator.md` (step 4 + new stale-base step), `reference/orchestration.md` (Integration step 3)

**Interfaces:**
- Produces: config key `VALIDATE_FORMAT=null`; the stale-base procedure name ("stale-base re-merge") referenced by dispatch/lead docs.

- [ ] **Step 1: Config key.** In `config/team.config.md`'s validation block, after `VALIDATE_LINT=null` add:

```
VALIDATE_FORMAT=null             # e.g. "pnpm format:check" / "black --check ." — the CI
                                 # formatting gate. Runs after VALIDATE_LINT; null skips
                                 # (recorded). A formatter CI enforces but integration
                                 # doesn't run is a post-merge CI failure waiting to happen.
```

- [ ] **Step 2: Integrator step 4.** In `roles/integrator.md` step 4, change `run \`VALIDATE_BUILD\`, then \`VALIDATE_TEST\`, then \`VALIDATE_LINT\` (skip \`null\` keys)` to `run \`VALIDATE_BUILD\`, then \`VALIDATE_TEST\`, then \`VALIDATE_LINT\`, then \`VALIDATE_FORMAT\` (skip \`null\` keys)`. Make the identical replacement in `reference/orchestration.md` Integration step 3.

- [ ] **Step 3: Stale-base rule.** In `roles/integrator.md`, insert after pipeline step 5 (the re-check-diff step):

```markdown
5.5 **Stale base (parallel execution).** If the feature branch has moved since
   the approval diff (`git merge-base` of the task branch ≠ feature-branch HEAD):
   merge the **feature branch into the task branch** first, re-run step 4's full
   validation set there, and only then merge back (step 6). Conflicts in that
   first merge follow the existing rule — hand back to the implementer, never
   resolve code yourself. Approvals stay valid only because the re-validation
   re-proves them on the moved base; record `stale-base: re-merged + revalidated`
   in the completion comment.
```

- [ ] **Step 4: Verify and commit**

Run: `grep -q VALIDATE_FORMAT config/team.config.md && grep -q "Stale base" roles/integrator.md && grep -q VALIDATE_FORMAT reference/orchestration.md && echo OK`
Expected: `OK`

```bash
git add config/team.config.md roles/integrator.md reference/orchestration.md
git commit -m "feat: integrator v2 — VALIDATE_FORMAT gate + codified stale-base re-merge rule"
```

---

## Phase D — comment protocol v2

### Task 11: `tracker-ops.sh update-comment` + comment-id capture + size warning

**Files:**
- Modify: `bin/tracker-ops.sh`
- Test: `tests/tracker-ops-test.sh`

**Interfaces:**
- Produces: `tracker-ops.sh update-comment <taskId> <commentId> [bodyfile]` (Linear/Jira/GitHubIssues; Markdown refuses with "append-only"); `comment` prints `(id: <id>)` where the backend returns one; `comment` warns on stderr for bodies > 50 lines (still succeeds).

- [ ] **Step 1: Write the failing tests.** In `tests/tracker-ops-test.sh` add before the fail-loud block:

```bash
# -- comment size warning: >50 lines still posts but warns ----------------------
long="$(python3 -c "print('\n'.join('line %d' % i for i in range(60)))")"
out="$(printf '%s\n' "$long" | "$OPS" comment "$T#2" - 2>&1)"
printf '%s' "$out" | grep -q "exceeds the 50-line budget" \
  && echo "ok: oversize comment warns" || { echo "FAIL: no size warning"; FAILURES=$((FAILURES+1)); }
check "oversize comment still posted" grep -q 'line 59' "$T"
```

and to the fail-loud block append:

```bash
refuse "Markdown update-comment refused"  "append-only"  bash -c "printf 'x\n' | '$OPS' update-comment '$T#2' some-id -"
refuse "update-comment arg check"         "usage:"       "$OPS" update-comment onlyone
```

- [ ] **Step 2: Run to verify failure**

Run: `bash tests/tracker-ops-test.sh`
Expected: FAILs (no warning, unknown op).

- [ ] **Step 3: Implement in `bin/tracker-ops.sh`:**

(a) Backend methods:
- `Linear.comment`: request the id — mutation becomes `commentCreate(input:{issueId:$id, body:$body}) { success comment { id } }`; return `d['commentCreate']['comment']['id']` (adjust: capture the `gql` result). Add:

```python
    def update_comment(self, task_id, comment_id, body):
        self.gql('mutation($cid: String!, $body: String!) { commentUpdate(id: $cid, input: {body: $body}) { success } }',
                 {'cid': comment_id, 'body': body})
```

- `Jira.comment`: capture the POST response and return `resp.get('id')`. Add:

```python
    def update_comment(self, task_id, comment_id, body):
        self.api('/rest/api/3/issue/%s/comment/%s' % (task_id, comment_id),
                 {'body': self.adf(body)}, method='PUT')
```

- `GitHubIssues.comment`: the `gh issue comment` stdout ends with the comment URL — return the trailing `#issuecomment-<id>` digits if present, else `None`. Add:

```python
    def update_comment(self, task_id, comment_id, body):
        repo = PM_CONFIG.get('GITHUB_REPO')
        if not repo:
            repo = json.loads(self.gh('repo', 'view', '--json', 'nameWithOwner'))['nameWithOwner']
        self.gh('api', '-X', 'PATCH', 'repos/%s/issues/comments/%s' % (repo, comment_id),
                '-f', 'body=%s' % body)
```

  (note: `gh api` must not receive the trailing `self.repo_args` `-R` flag — call `subprocess.run(['gh', ...])` directly for this method, reusing the error-handling shape of `self.gh`.)
- `Markdown`: add

```python
    def update_comment(self, task_id, comment_id, body):
        die("Markdown adapter is append-only — no stable comment ids; post a new comment with 'supersedes: %s' instead" % comment_id)
```

(b) `op_comment` — add the size warning and id echo:

```python
def op_comment(args):
    if len(args) not in (1, 2):
        die("usage: comment <taskId> [bodyfile]  (no file / '-' = stdin)")
    body = read_body(args[1] if len(args) == 2 else None)
    if body.count('\n') + 1 > 50:
        print("tracker-ops: warning — comment body exceeds the 50-line budget "
              "(protocol: move detail to <TEAMWORK_ROOT>/<team>/artifacts/ and cite the path)",
              file=sys.stderr)
    cid = backend.comment(args[0], body)
    print("comment added to %s%s" % (args[0], " (id: %s)" % cid if cid else ""))
```

(existing `comment` methods that return nothing keep working — `None` id prints nothing extra).

(c) New op + registration:

```python
def op_update_comment(args):
    if len(args) not in (2, 3):
        die("usage: update-comment <taskId> <commentId> [bodyfile]  (no file / '-' = stdin)")
    if not hasattr(backend, 'update_comment'):
        die("adapter '%s' does not support update-comment" % ADAPTER)
    backend.update_comment(args[0], args[1], read_body(args[2] if len(args) == 3 else None))
    print("comment %s updated on %s" % (args[1], args[0]))
```

Add `'update-comment': op_update_comment` to `OPS` and to the usage string and header comment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/tracker-ops-test.sh && bash tests/dispatch-test.sh`
Expected: `ALL PASS` ×2

- [ ] **Step 5: Commit**

```bash
git add bin/tracker-ops.sh tests/tracker-ops-test.sh
git commit -m "feat: tracker-ops update-comment (Linear/Jira/GitHub), comment id capture, 50-line budget warning"
```

---

### Task 12: Comment protocol v2 + human digest (docs + adapters)

**Files:**
- Modify: `reference/orchestration.md` (Structured comments: budgets/round/supersedes; two new marker rows; artifacts dir in workspace tree), `reference/vocabulary.md` (marker vocabulary), `adapters/_TEMPLATE.md` + `adapters/Linear.md` + `adapters/Jira.md` + `adapters/GitHubIssues.md` + `adapters/Markdown.md` (update-comment operation row), `roles/team-lead.md` (digest + escalation duties)

**Interfaces:**
- Consumes: `update-comment` op (Task 11), artifacts path convention (Task 8).

- [ ] **Step 1: Budgets + supersession.** In `reference/orchestration.md` → *Structured comments*, after the authorship paragraph (Task 7) add:

```markdown
**Budgets and supersession.** A gate-marker comment is ≤ **30 lines**: marker,
`round: N`, `supersedes: <comment-id>` (round ≥ 2; Markdown adapter:
`<marker>-<round>` stands in for the id), verdict, delta since the last round,
file list, evidence/artifact paths, signature. Full checklists, logs, and long
rationale live in `<TEAMWORK_ROOT>/<team>/artifacts/<taskId>/` and are cited by
path — the integrator verifies cited paths exist. Reconstructing current state
from a trail: per marker type, the comment with the highest `round:` not named
by a later `supersedes:` is current; everything else is history (unnumbered
pre-v2 comments count as round 0). WIP narration, setup chatter, and restated
[task] descriptions never enter the tracker — design notes are **delta-only**.
```

- [ ] **Step 2: Two new marker rows** in the markers table (after `[handoff]`):

```markdown
| `[progress]` | implementer (via lead in scribe mode) | **One per [task], edited in place** (`tracker-ops.sh update-comment`; Markdown adapter: append a superseding one). Content: stage (`claimed / design-approved / implementing / validating / review-round-N`), updated-at (UTC), ≤ 3 lines of state. Edit on stage boundaries only, ≥ 10 min apart. First post: capture the comment id in `<TEAMWORK_ROOT>/<team>/progress-ids/<taskId>`; a relaunched scribe re-reads the trail to find it. |
| `[digest]` | team-lead | **One per [feature], on the [feature] itself, edited in place** at milestones only (a [task] hits terminal status, a gate rejects, an `[andon]`, feature done): one line per [task] (`<taskId> <title> — [Status] (<reason if blocked/rejected>)`) + `⚠ escalation open: <taskId>` lines. The human reads this one comment, never the trails. GitHubIssues: milestones take no comments — keep the digest in the milestone description (`gh api PATCH`). |
```

- [ ] **Step 3: Escalation contract.** In the same table, replace the `[escalation]` row content with:

```markdown
| `[escalation]` | team-lead | Needs the human. Required shape: `question:` (one sentence), `context:` (≤ 4 lines), `options:` (≥ 2, each with a one-line consequence), `default-if-silent: <option> after <N hours>`. Also appended to `ESCALATIONS.md`. An `[escalation]` without options + default is a protocol error (`[andon]`). |
```

- [ ] **Step 4: Workspace tree** — add under the `tasks.json` line:

```
├── artifacts/<taskId>/          # full logs, checklists, evidence files — cited by path from budgeted comments
├── progress-ids/<taskId>        # comment id of the [task]'s editable [progress] comment
```

- [ ] **Step 5: Vocabulary.** In `reference/vocabulary.md`, add `[progress]` and `[digest]` to wherever markers are enumerated (grep `handoff` for the list), with one-line meanings matching Step 2.

- [ ] **Step 6: Adapter operation rows.** In each adapter's *Operations* table add an `update comment` row:
- `_TEMPLATE.md`: `| update comment | <tool mechanism for editing an existing comment; optional — if the tool can't edit, document the append-a-superseding-comment degradation> |`
- `Linear.md`: `| update comment | GraphQL \`commentUpdate(id: $commentId, input: {body: $body})\`; or \`bin/tracker-ops.sh update-comment <taskId> <commentId> <bodyfile>\` |`
- `Jira.md`: `| update comment | REST \`PUT /rest/api/3/issue/<issueId>/comment/<commentId>\` (ADF body); or \`bin/tracker-ops.sh update-comment ...\` |`
- `GitHubIssues.md`: `| update comment | \`gh api -X PATCH repos/<owner>/<repo>/issues/comments/<commentId> -f body=...\`; or \`bin/tracker-ops.sh update-comment ...\`. Feature digest: milestones take no comments — edit the milestone description instead. |`
- `Markdown.md`: `| update comment | **Not supported — append-only.** Post a new comment carrying \`supersedes: <marker>-<round>\`; readers treat the highest round as current. |`

- [ ] **Step 7: Team-lead duties.** In `roles/team-lead.md` *You own* list add: `- The [feature] digest: one editable comment on the [feature], updated at milestones only, one line per [task] — the human's whole view (protocol: [digest] marker). And the escalation contract: every [escalation] carries question, options, and default-if-silent.`

- [ ] **Step 8: Verify and commit**

Run: `grep -q "progress-ids" reference/orchestration.md && grep -c "update comment" adapters/*.md | grep -v ":0" | wc -l | grep -q 5 && bash tests/launcher-test.sh >/dev/null && echo OK`
Expected: `OK`

```bash
git add reference/ adapters/ roles/team-lead.md
git commit -m "feat: comment protocol v2 — budgets, supersession, editable [progress]/[digest], escalation contract"
```

---

### Task 13: Consistency review + full verification

**Files:** none new — fixes land in the files above.

- [ ] **Step 1: Run everything**

Run: `bash tests/launcher-test.sh && bash tests/tracker-ops-test.sh && bash tests/dispatch-test.sh`
Expected: `ALL PASS` ×3

- [ ] **Step 2: Cross-doc consistency pass** (repo's established practice). Check and fix:
- No doc still says worktrees are `<role>-<taskId>` or unconditional per role.
- No brief or protocol section still self-schedules (`grep -rn "poll again\|check back\|relaunch on a schedule" reference/ roles/ teams/` → empty).
- `reference/dispatch.md`'s event table matches `bin/dispatch.sh` behavior (queues, auto-unblock adapters, dedup) and `roles/team-lead.md`'s pipelined dispatch rules are referenced, not contradicted.
- The markers table in `statuses.config.json` agrees with every brief's "Markers you are authorized to post" line and the orchestration markers table's "Written by" column.
- Evidence-record field names identical in orchestration.md, briefs, and playbook.
- SKILL.md's sibling-file list mentions dispatch.md; usage strings in both bin scripts list every subcommand.

- [ ] **Step 3: Commit fixes, then report** — summary of tests + any consistency fixes made.

```bash
git add -A ':!*.mp4' && git commit -m "fix: cross-doc consistency pass for v2 redesign"
```
