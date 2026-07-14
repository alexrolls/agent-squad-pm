---
name: startup-factory
description: Create, track, and update [features] and [tasks] in any project-management tool — Linear, Jira, GitHub Issues, or a Markdown fallback — through one tool-agnostic workflow. Use when the user wants to plan a feature, break work into tasks, start/review/complete a task, change a [task]'s status, connect/switch the project-management tool, run a multi-agent team on a feature (orchestration with a team lead, principal architect, and cross-functional implementers), or fetch/update the latest Startup Factory skill itself. Language- and framework-agnostic.
---

# Project Management Workflow

You manage [features] and [tasks] in whatever project-management tool this project is configured to
use, speaking **only** the generic vocabulary — never a tool-specific word. This skill is
the operational front door; the details live in sibling files (paths are relative to this
skill's directory):

- `config/project-management.config.md` — selects the active tool + settings
- `reference/vocabulary.md` — the generic contract (terms, statuses, IDs, banned words)
- `reference/lifecycle.md` — the numbered scenarios you execute
- `reference/team-roles.md` — status ownership (only if `TEAM_MODE=true`)
- `reference/orchestration.md` — multi-agent protocol (mailboxes, gates, unblocking)
- `reference/dispatch.md` — who converts tracker/mailbox events into role launches (the loop lives outside the agent)
- `roles/<role>.md` + `config/team.config.md` + `bin/launch-team.sh` — the agent team
- `bin/tracker-ops.sh` — ergonomic CLI for recurring tracker operations (scriptable mechanisms)
- `bin/update-installed-skill.sh` — fetch the latest upstream skill bundle into the current repository
- `bin/runtime-state.py` + `bin/task-packet.sh` — durable events, PM projections,
  and minimal task-local context
- `bin/submit-artifact.sh` + `bin/process-outbox.sh` — idempotent agent handoffs
- `bin/review-package.sh` + `bin/integrate-task.sh` — exact review input and
  recoverable task-branch integration
- `adapters/<Tool>.md` — how to perform each operation in the active tool

> **Golden rule:** in everything you write — comments, commit messages, messages to the
> user — use only the generic vocabulary — terms `[feature]`, `[task]`, `[subtask]` and the statuses defined in `config/statuses.config.json` (default board: `[Planned]`/`[Active]`/`[Review]`/`[Blocked]`/`[Ready to deploy]`). Never write "issue", "epic",
> "story", or "ticket" outside the adapter. See the banned-terms list in `vocabulary.md`.

## Self-update request

If the user asks to "fetch latest Startup Factory", "update startup-factory skill",
"sync this skill from upstream", or equivalent, do this before the normal mandatory
preparation:

1. From the target repository, run:

   ```bash
   bash .claude/skills/startup-factory/bin/update-installed-skill.sh
   ```

   If this skill is installed somewhere else, run the same script from this skill's
   `bin/` directory with `--install-dir <path-to-installed-skill>`.
2. Keep the default config-preserving behavior unless the user explicitly asks to
   replace project config. Existing `config/project-management.config.md`,
   `config/team.config.md`, and `config/statuses.config.json` are preserved.
3. Report the script's target path and git status/diff summary. Do not commit unless
   the user asks.

## Mandatory Preparation (every invocation)

1. **Read the config** (`config/project-management.config.md`). Note
   `PRODUCT_MANAGEMENT_TOOL`, the per-tool settings block, and the flags `TEAM_MODE` /
   `STRICT_STATUS`.
2. **Load the adapter** for that tool: `adapters/<PRODUCT_MANAGEMENT_TOOL>.md`. This is
   your only source for concrete operations, terminology, status, and ID mappings. If the
   file doesn't exist, stop and tell the user to create it from `adapters/_TEMPLATE.md`.
3. **Read `reference/vocabulary.md` and `reference/lifecycle.md`** if not already in
   context. If `TEAM_MODE=true`, also read `reference/team-roles.md` and `reference/orchestration.md`.
4. **Initialize the tool** — steps depend on your execution mode:
   - **Single-agent** (`TEAM_MODE` unset or false): run the adapter's *Initialization*
     section probe (a cheap read proving access works). If it fails, stop and tell the
     user to fix the *MCP / CLI Setup* — do not proceed.
   - **Team CLI** (launched by `bin/launch-team.sh`): `preflight` owns the shared adapter
     probe; do not re-run it. If a `Verified tracker tool prefix` appears in your startup
     context, use it verbatim — do not re-derive from adapter docs.
   - **Harness** (subagent from a `compose` prompt): the orchestrator resolved the MCP
     tools before spawning you. Use the `Verified tracker tool prefix` from your startup
     context; do not call ToolSearch to re-derive it.
   - **Task instance** (startup prompt names a task packet, worktree, and report):
     read the packet and your role brief only. The dispatcher already resolved the
     tracker, task state, baseline, contracts, and validation commands. Do not load
     the whole orchestration reference or tracker history.

## Executing the request

Map the user's ask to a scenario in `reference/lifecycle.md` and follow it, translating
each generic operation through the adapter's *Operations* table:

| The user wants to… | Scenario |
|---|---|
| Plan / spec a feature, break work into tasks | 1 — Plan a `[feature]` |
| Start / pick up / work on a task | 2 — Start a `[task]` |
| Note a change from what a task said | 3 — Diverge |
| Send a task for review | 4 — Request review |
| Finish / close out a task | 5 — Finalize a `[task]` |
| File a bug / follow-up found mid-work | 6 — File newly-discovered work |
| Work is stuck / blocked / cannot proceed | 7 — Block a `[task]` |
| (anything wrong / blocked / failed) | 8 — Andon cord: stop & report |
| Run an agent team on a feature ("launch the team") | Team: set `TEAM_MODE=true`; gate roles use `start`/`compose`, task workers use `start-task`/`compose-task`, and `dispatch.sh` owns claims and bounded scheduling |
| Connect a new tool / switch tools | 9 — Connect / switch |
| Design/plan everything up front, sign off all designs before coding | 10 — Pre-flight design pass |

## Non-negotiables (the fail-loud contract)

- **Every status change is a real write** through the adapter's mechanism — then confirm
  it. Never claim a status you didn't set.
- **If any operation fails, stop and report it** (Scenario 8). Never work around a failure
  or fabricate a result.
- **Never skip a status transition.** Legal moves are the `transitions` graph in
  `config/statuses.config.json` (default board:
  `[Planned]` → `[Active]` → `[Review]` → `[Ready to deploy]`, rework `[Review]` → `[Active]`,
  `[Blocked]` for stuck work).
- **When `STRICT_STATUS=true`, verify the current status before writing** and that the
  intended move is in its `transitions` list. If not, pull the andon cord instead of
  forcing the change.
- **`[Ready to deploy]` means verified-done** — reviewed, tests/build green, and
  merged to the feature branch. Git plus tracker completion is a durable,
  idempotent transaction recorded under `.teamwork/<team>/integrations/`; never
  pretend two systems are physically atomic.

## Reporting back

After acting, tell the user: the `featureId`/`taskId` affected, the status transition you
made (`from → to`), and any comment you added — in generic vocabulary. If you created a
feature and tasks, list each `taskId` with its title and status.
