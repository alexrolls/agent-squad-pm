# PR #6 Wave 1 — Implementation Report

Branch: `feature/v2-event-driven-redesign`
Wave: 1 (Blocking Findings 1 and 4 only; Findings 2 and 3 designed in parallel)

---

## Fix 1 — tmux pane liveness (commit `8d59294`)

### Changes

**`bin/launch-team.sh`** — `launch_one` tmux branch (1 line):

Before:
```bash
"cd '$REPO_ROOT' && $cmd; echo '[launch-team] $role exited'; sleep 86400"
```

After:
```bash
"cd '$REPO_ROOT' && { $cmd; rc=\$?; }; echo '[launch-team] $role exited ('\$rc')'; rm -f '$dir/pids/$role.pid'; sleep 86400"
```

`$dir`, `$role`, `$REPO_ROOT`, `$cmd` expand at window-creation time (host shell).
`\$?` and `\$rc` escape to expand inside the pane's shell.

When the agent command exits, the pid file is removed before the log-retention
sleep begins. `role_live`'s pid-file-first contract (`[ -f "$pf" ] || return 1`)
then returns false immediately — the tmux window check is never reached for an
exited process. Old sleeping panes are already killed by the existing
`tmux kill-window` call before `new-window` creates a replacement.

**`tests/launcher-test.sh`** — new tmux-guarded block (30 lines):

- Guard: `command -v tmux >/dev/null 2>&1 || { echo "skip: tmux tests (tmux unavailable)"; }` (else branch)
- Uses disposable team `tmux-liveness` (tmux session `team-tmux-liveness`)
- Launches `backend` role with no `TEAM_RUNNER` override → auto picks tmux
- Asserts pid file written on launch (synchronous)
- Polls up to 10 s (50 × 0.2 s) for pid file removal after the one-shot `BACKEND_CMD` exits
- Asserts relaunch succeeds — `start` output contains "launched backend in tmux session"
- Cleans up: `tmux kill-session -t team-tmux-liveness 2>/dev/null || true`

### Test evidence

```
ok: tmux: pid file written on launch
ok: tmux: pid file removed after agent exit
ok: tmux: relaunch succeeds (not considered live)
```

---

## Fix 4 — auto-unblock resume-status (commit `b2864e5`)

### Changes

**`bin/dispatch.sh`** — Python engine + bash handler:

Python additions:
- `blocked_transitions`: set of legal transitions from `Blocked` in the board config
- `last_resume_status(t)`: scans all comment bodies, returns the last line starting
  with `resume-status: ` (case-sensitive, line-anchored), or `None`
- Unblock loop now branches:
  - Valid `resume-status:` → `('unblock', tid, rs)` — bash uses `$detail` as target
  - Invalid `resume-status:` → stderr warning naming the illegal value + `('unblock-no-rs', tid, rs)`
  - No `resume-status:` → `('unblock-no-rs', tid, '')`
- `no_rs_blocks` list collected; appended to team-lead detail when non-empty

Bash handler changes:
- `unblock` auto case: `tracker-ops.sh state "$arg" "$detail"` (was hard-coded `Active`)
- Auto comment now includes "Resuming to [$detail]"
- New `unblock-no-rs` case: always prints the suggestion, never writes, regardless
  of `--unblock` mode (there is no proven status to write)

**`reference/lifecycle.md`** — Scenario 7 block comment shape updated:

Added the required three-field shape (`blocked-by:`, `resume-status:`, `reason:`)
with an explicit note that `resume-status` must name a legal `Blocked` transition.

**`reference/dispatch.md`** — auto-unblock rule augmented:

Added one sentence: auto-unblock writes only when the latest block-era comment
carries a legal `resume-status:` line; otherwise routes to team-lead.

**`reference/orchestration.md`** — freeze protocol note:

Added a parenthetical clause to the parked comment description stating that the
freeze-protocol parked comment deliberately carries no `resume-status:` — the
lead owns that resume assignment directly.

**`tests/dispatch-test.sh`** — fixture and new tests:

- Task 2's block comment gains `> resume-status: Active` so the existing
  auto-unblock assertions keep passing (`[Active]` move + comment present)
- New fixture `feat/rs-test.md` (5 tasks): terminal task 1, plus tasks 2–5
  covering Review/Planned/no-rs/invalid-rs cases
- New assertions (10 checks):
  - `resume-status: Review` → moved to `[Review]` with "Resuming to [Review]" comment
  - `resume-status: Planned` → moved to `[Planned]` with "Resuming to [Planned]" comment
  - no resume-status → `[Blocked]` unchanged; "NO RESUME STATUS" suggestion in output
  - invalid `resume-status: Nonesuch` → `[Blocked]` unchanged; "invalid resume-status" warning in stderr
  - suggest-only (Markdown default): valid-rs task still shows `— SUGGESTED` (no write)

### Test evidence

```
ok: auto-unblock moved status           (→ [Active] via resume-status: Active on task 2)
ok: auto-unblock left a comment         (contains "Auto-unblocked by dispatcher")
ok: resume-status Review: moved to [Review]
ok: resume-status Planned: moved to [Planned]
ok: no-rs: not moved (still Blocked)
ok: invalid-rs: not moved (still Blocked)
ok: Review unblock comment present
ok: Planned unblock comment present
ok: no-rs: lead-actionable suggestion printed
ok: invalid-rs: warning printed
ok: suggest-only: valid-rs task shows SUGGESTED
ok: suggest-only: no write for valid-rs task
```

---

## Validation

```
bash tests/dispatch-test.sh   → ALL PASS
bash tests/launcher-test.sh   → ALL PASS
bash tests/tracker-ops-test.sh → ALL PASS
bin/launch-team.sh validate-board → board config OK
```

### shellcheck summary

No new warnings in production scripts (`bin/dispatch.sh`, `bin/launch-team.sh`,
`bin/tracker-ops.sh` — zero shellcheck findings).

Test-file warnings are all pre-existing idioms the reviewer explicitly said would
not block merge:
- SC2015 (`A && B || C` idiom) — throughout all test files, unchanged pattern
- SC2034 (unused `i` loop var in launcher-test.sh line 118) — pre-existing
- SC2016 (single-quote expressions in tracker-ops-test.sh) — pre-existing

My new test code adds one additional SC2015 instance (line 274 of launcher-test.sh)
matching the same pattern already used throughout the test suite.

---

## Deviations

None from the review's stated requirements. Both fixes follow the preferred
implementation option (Option 1 for Finding 1; the `resume-status:` line approach
for Finding 4). The freeze-protocol parked comment note in orchestration.md is a
single parenthetical clause, not a restructuring.

---

## Commits

| Hash | Message |
|------|---------|
| `8d59294` | fix: tmux pane liveness — pid file removed on agent exit; guarded regression test |
| `b2864e5` | fix: auto-unblock requires a proven resume-status; suggest-and-route-to-lead otherwise |

---

# PR #6 Wave 2 — Implementation Report

Branch: `feature/v2-event-driven-redesign`
Wave: 2 (Blocking Findings 2 and 3 + docs consistency fixes)

---

## Fix 2 — MCP/Scriptable Access Contract (commit `96b9fec`)

### Changes

**`bin/launch-team.sh`:**
- Added `PM_CONFIG` variable at the top
- Added `read_pm_key()` helper (same pattern as `read_key` but targets PM config)
- Added `is_mcp_only()`: returns 0 when Linear/Jira/GitHub is configured for MCP-only access
- `preflight()`: MCP guard fires before the tracker-ops probe and before the `tool-prefix.txt`
  fallback — a tracker-ops-capable adapter in MCP mode gets a clear die with fix instructions
- tool-prefix.txt success echo now appends `(harness prompt composition only; CLI dispatch.sh
  requires scriptable access)`

**`bin/dispatch.sh`:**
- Same `read_pm_key()` + `is_mcp_only()` functions added
- `dispatch_once()`: MCP guard fires before `tracker-ops.sh export` — dispatcher never stalls
  with a misleading auth error

**`adapters/Linear.md` / `adapters/Jira.md` / `adapters/GitHubIssues.md`:**
- One sentence each: CLI dispatch requires rest/gh access; MCP is harness-only

**`tests/launcher-test.sh`:**
- 2 new checks: Linear+MCP preflight fails with "CLI dispatcher requires scriptable tracker
  access for Linear"; tool-prefix.txt does not bypass the guard

**`tests/dispatch-test.sh`:**
- 1 dedup-test fix (message now includes `(→<concrete>)`, updated grep to `.*`)
- 1 new check: Linear+MCP dispatch exits non-zero before tracker-ops

---

## Fix 3 — Preset Protocol Mapping (commit `e64865c`)

### Changes

**`teams/full-stack.md`, `teams/deep-backend.md`, `teams/deep-frontend.md`,
`teams/deep-infra.md`, `teams/deep-security.md`:**
- PROTOCOL_* keys added in the config block alongside ROSTER=; derived from each
  Roster table's Protocol mapping column; PROTOCOL_REVIEWER = senior-qa-engineer
  (final gate) in all five presets

**`bin/launch-team.sh`:**
- `team` case: writes `preset.env` (PRESET + grep of PROTOCOL_* from team file) after
  preflight; `$dir` is now set via `teamroot "$team"` just before the write
- `launch_one()`: auto-reads PRESET= from workspace preset.env when `$preset` is not
  passed; compose_prompt then includes the team file and playbook

**`bin/dispatch.sh`:**
- `resolve_role <team> <protocol-role>`: reads preset.env, converts protocol role to
  PROTOCOL_<UPPER> key, returns concrete role or passthrough when no mapping
- `launch` bash handler: resolves arg to concrete before CMD-key check, liveness check,
  mailbox write, and start call; plan output: `launch <proto> (→<concrete>)`
- Python block: `import re as _re`; reads `preset.env` at startup to extract
  PROTOCOL_REVIEWER; in the merge-queue check, when `protocol_reviewer` is set,
  extracts the `[review-approval]` signer via `r'—\s*([\w-]+)...'`; signer mismatch
  → anomaly + stderr warning; no-preset branch preserves existing behavior

**`roles/integrator.md` step 1.5:**
- One sentence: for preset teams, verify [review-approval] signer matches PROTOCOL_REVIEWER
  in preset.env; generic `reviewer` does not satisfy a preset's final QA gate

**`tests/dispatch-test.sh`:**
- D3.8 tests (7 new checks): preset routing (PA→principal-software-architect, concrete
  pid written, generic pid absent); signer check (warning, task 2 out of merge queue,
  task 3 unlocks integrator, team-lead notified)

---

## Docs Consistency (commit `26dcf9c`)

**`SKILL.md` Mandatory Preparation step 4:**
- Split into three mode paths: single-agent (probe via adapter), team CLI (preflight
  owns probe; use injected prefix verbatim), harness (MCP tools pre-resolved; don't
  re-derive prefix)

**`README.md`:**
- Expanded launcher command table: added `preflight`, `worktree [attempt]`,
  `worktree-remove`
- Added `dispatch.sh` command table with --once / --watch entries
- Added note: CLI dispatch requires scriptable tracker access; MCP is harness-only
- Added `update-comment` to tracker-ops.sh description

**`reference/lifecycle.md` Scenario 2 step 5:**
- "wait for the principal-architect's `[design-approved]`" → "post `[design-note]` and
  exit; the dispatcher/harness relaunches you when `[design-approved]` arrives"

---

## Validation

```
bash tests/dispatch-test.sh   → ALL PASS (39 checks)
bash tests/launcher-test.sh   → ALL PASS (66 checks)
bash tests/tracker-ops-test.sh → ALL PASS
bin/launch-team.sh validate-board → board config OK
```

### shellcheck

Zero new findings on production scripts (`bin/dispatch.sh`, `bin/launch-team.sh`,
`bin/tracker-ops.sh`). Test-file warnings are all pre-existing SC2015 `A && B || C`
idiom.

### rg "wait for" scan

7 remaining instances — all benign:
- `reference/dispatch.md:4` — NEGATION: "an agent's promise to 'check back in N minutes'"
- `roles/team-lead.md:35` — orchestrator waiting for PA (team-lead IS the loop)
- `roles/team-lead.md:86` — ANTI-self-scheduling: "Never promise to 'check back later'"
- `roles/backend.md:30` — anti-coordination: "Don't wait for review; frontend is..."
- `reference/orchestration.md:73` — ANTI-self-scheduling: "never plan to 'check back'"
- `reference/orchestration.md:360` — pipeline diagram gate notation, not an agent instruction
- `roles/reviewer.md:20` — anti-coordination: "Do not wait for each other"
The lifecycle.md instance (the only instruction to a one-shot agent) has been fixed.

---

## Deviations from PA Design

- **resolve_role** is in the same hunk as `read_pm_key` and `is_mcp_only` in dispatch.sh
  because all three are adjacent function definitions; split across commits 1 and 2 to
  keep commit 1 cleanly testable (D2 state passes all tests at commit 1 boundary)
- `teams/deep-infra.md`: SRE Engineer has no dedicated PROTOCOL_ key (hybrid role: backend
  for own tasks, informal reviewer for cloud engineer tasks); PROTOCOL_BACKEND maps to
  senior-cloud-engineer (primary implementer); PA design confirmed this via the "no
  PROTOCOL_FRONTEND for backend-only presets" precedent

---

## Commits

| Hash | Message |
|------|---------|
| `96b9fec` | fix: CLI dispatch requires scriptable tracker access — MCP is harness-mode only |
| `e64865c` | fix: dispatcher resolves preset protocol mappings; preset final gate guards the merge queue |
| `26dcf9c` | docs: SKILL preparation modes, README command table, lifecycle wait-language |
