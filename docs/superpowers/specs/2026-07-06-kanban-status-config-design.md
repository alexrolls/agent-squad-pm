# Configurable Kanban Statuses — Design

**Date:** 2026-07-06
**Status:** Approved (pending user spec review)
**Branch:** `feature/agent-teams`

## Problem

The status machine is hard-coded across the port layer: tasks flow
`[Planned] → [Active] → [Review] → [Completed]` (features `[Planned] → [Active] → [Resolved]`),
with the tables baked into `reference/vocabulary.md`, transition ownership baked into
`reference/team-roles.md`, and per-tool mappings baked into each `adapters/<Tool>.md`.
Projects cannot rename statuses, add stages (e.g. a blocked queue, a deploy handoff), or
change who owns a stage without editing five-plus documents.

## Requirements (from user)

1. A configuration file (JSON) defines the kanban statuses.
2. Add statuses **`Blocked`** and **`Ready to deploy`**.
3. Remove `Done`/`[Completed]` as a generic status — `Ready to deploy` is the new terminal.
4. Each status is assigned an owner: a **team** or a **single agent**, who works items in
   that status.

## Decisions (clarified with user)

| Question | Decision |
|---|---|
| Relation to the fixed 4-status machine | **Fully configurable machine** — the config defines the complete ordered status list, legal transitions, and owner per status; docs/adapters defer to it. |
| What owner assignment does | **Ownership + routing** — the owner is the only party allowed to work items in that status, and the orchestration layer routes items entering a status to the owner's mailbox. Replaces the hard-coded transition-ownership table. |
| Terminal semantics | **`Ready to deploy` is terminal and commit-coupled** (`requiresCommit: true`): reviewed, verified, committed — awaiting human deployment. Feature-level `Resolved` fires when all tasks reach it. |
| Integration shape | **One global board config** (`config/statuses.config.json`), not per-team boards, no doc-generation tooling. |
| Naming | Terminal status is **`Ready to deploy`** (renamed from earlier draft "Ready for production"). |

## 1. Config file and default board

New file **`config/statuses.config.json`** — single source of truth for both state
machines (features and tasks).

```json
{
  "features": {
    "statuses": [
      { "name": "Planned",  "initial": true, "owner": { "role": "team-lead" }, "transitions": ["Active"],
        "tool": { "Linear": "Planned", "Jira": "To Do", "GitHubIssues": "milestone open, no task started", "Markdown": "[Planned]" } },
      { "name": "Active",   "owner": { "role": "team-lead" }, "transitions": ["Resolved"],
        "tool": { "Linear": "In Progress", "Jira": "In Progress", "GitHubIssues": "milestone open, >=1 task active (derived)", "Markdown": "[Active]" } },
      { "name": "Resolved", "terminal": true, "owner": { "role": "team-lead" }, "transitions": [],
        "tool": { "Linear": "Completed", "Jira": "Done", "GitHubIssues": "milestone closed", "Markdown": "[Resolved]" } }
    ]
  },
  "tasks": {
    "statuses": [
      { "name": "Planned",
        "initial": true,
        "owner": { "role": "team-lead" },
        "transitions": ["Active"],
        "tool": { "Linear": "Backlog", "Jira": "To Do", "GitHubIssues": "open", "Markdown": "[Planned]" } },
      { "name": "Active",
        "owner": { "role": "implementer" },
        "transitions": ["Review", "Blocked"],
        "tool": { "Linear": "In Progress", "Jira": "In Progress", "GitHubIssues": "open + label:active", "Markdown": "[Active]" } },
      { "name": "Review",
        "owner": { "role": "reviewer" },
        "transitions": ["Active", "Ready to deploy", "Blocked"],
        "tool": { "Linear": "In Review", "Jira": "In Review", "GitHubIssues": "open + label:review", "Markdown": "[Review]" } },
      { "name": "Blocked",
        "owner": { "role": "team-lead" },
        "transitions": ["Planned", "Active", "Review"],
        "tool": { "Linear": "Blocked", "Jira": "Blocked", "GitHubIssues": "open + label:blocked", "Markdown": "[Blocked]" } },
      { "name": "Ready to deploy",
        "terminal": true,
        "requiresCommit": true,
        "owner": { "role": "integrator" },
        "transitions": [],
        "tool": { "Linear": "Done", "Jira": "Done", "GitHubIssues": "closed", "Markdown": "[Ready to deploy]" } }
    ]
  }
}
```

Schema, per status:

| Field | Type | Meaning |
|---|---|---|
| `name` | string, required | Status name; referenced in bracket notation `[Name]` everywhere. |
| `initial` | bool, default false | Entry status. Exactly one per machine. |
| `terminal` | bool, default false | End status. At least one per machine; `transitions` must be empty. |
| `requiresCommit` | bool, default false | Entering this status is atomically coupled to a successful commit (performed by the status owner). |
| `owner` | object, required | Exactly one of `{"role": "<name>"}` (single agent — abstract role like `implementer`/`reviewer`, or a concrete role like `backend`) or `{"team": "<preset>"}` (a team preset from `teams/`, routed via that team's lead). |
| `transitions` | array of status names, required | The only legal outbound moves. |
| `tool` | object, optional | Per-adapter concrete status value. Missing entry for the active adapter = andon on write. |

Notes:

- `Done`/`Completed` no longer exists as a *generic* status. The tool-side value may
  still be "Done" (that's just what Linear/Jira call their terminal column — configurable).
- `Blocked` is entered from `Active` or `Review` and returns to any working status.
- Editing this file is how a project adds/renames/removes statuses; nothing else needs
  touching as long as owners and tool mappings are set.

## 2. Ownership and routing semantics

**Core rule:** the owner of status S is the only party allowed to work items sitting in S
and the only one allowed to perform S's *outbound* transitions. The hard-coded
transition-ownership table in `reference/team-roles.md` is replaced by this derived rule.

Against the default board:

- `Planned` (owner `team-lead`) — creates/refines tasks, hands them out; an implementer
  claiming a task is the team-lead-sanctioned `Planned → Active` move (claim-on-assignment).
- `Active` (owner `implementer`) — implements; moves to `Review`, or `Blocked` when stuck.
- `Review` (owner `reviewer`) — approves → `Ready to deploy` handoff to the integrator
  (who performs commit + status write as one atomic step, per `requiresCommit`), or
  sends back → `Active`.
- `Blocked` (owner `team-lead`) — works the unblock ladder (`reference/orchestration.md`),
  then routes the item back to the appropriate working status.

**Routing (team mode):** whenever an item enters a status, the mover notifies the new
owner's mailbox (existing mailbox mechanism). `{"team": ...}` owners route via that
team's lead, who dispatches internally. Single-agent mode (`TEAM_MODE=false`): one agent
plays every owner; routing is a no-op, but the state machine and pre-checks still apply.

**Blocked vs andon cord — distinct concepts:**

- `Blocked` = the *work* cannot proceed (missing dependency, unanswered question, failed
  external service). A normal, configured status with an owner resolving it.
- Andon cord = the *process* is broken (unexpected status, adapter operation failed,
  invalid config). Stop, write nothing, escalate — unchanged.
- `STRICT_STATUS=true` now means: before any write, verify the item's current status and
  that the intended move is in that status's `transitions` list; otherwise andon.

**Backward moves** are no longer special-cased; legality is purely the `transitions` graph.

## 3. Consumer updates

- **`reference/vocabulary.md`** — Status Model section reduces to: bracket notation rule
  (`[Status Name]`, exact case), pointer to `config/statuses.config.json` as single source
  of truth, and the invariants (fail loud; no skipping — "next status" = a status in the
  current status's `transitions` list). The default board appears as an example, labeled
  as the shipped default.
- **`reference/lifecycle.md`** — scenarios rewritten status-agnostically: "Start" claims
  from the *initial* status; "Complete" becomes "Finalize" targeting the *terminal* status
  with the `requiresCommit` check; new **Scenario — Block a [task]** (entering/leaving
  `Blocked`, who moves it, required unblock comment); quick-reference table regenerated
  against the default board.
- **`reference/team-roles.md`** — transition-ownership table replaced by the derived rule
  (Section 2) plus a worked example against the default board.
- **`reference/orchestration.md`** — adds the routing rule (status entry → owner mailbox;
  team owners via team lead). Comment markers unchanged (`[review-request]` written when
  moving into `Review`).
- **`roles/*.md`** (7 briefs) — status responsibilities reference the config ("you own the
  statuses whose `owner.role` matches your role") with default-board behavior spelled out.
  This also fixes the ultra-review role-protocol gaps (e.g. `backend.md` missing
  `[Review] → [Active]` rework), since transitions are config-derived rather than
  per-brief prose.
- **`config/project-management.config.md`** — add `STATUS_CONFIG=config/statuses.config.json`
  next to `TEAM_MODE`/`STRICT_STATUS`.
- **`README.md` / `SKILL.md` / `teams/_PLAYBOOK.md`** — short "Configure your board"
  subsection: edit the JSON; what the fields mean.

## 4. Adapter changes

Adapters stop owning the status vocabulary and become pure translators:

- **`adapters/_TEMPLATE.md` + Linear / Jira / GitHubIssues / Markdown** — "Status Mapping"
  tables are replaced by: *read the active status list from `config/statuses.config.json`;
  each status's `tool` map gives this tool's concrete value.* Each adapter documents its
  mapping *mechanism* (Linear: workspace state name; Jira: transition name; GitHubIssues:
  open/closed + labels; Markdown: literal bracket text) and keeps its current values only
  as shipped defaults for the default board.
- **Missing mapping = andon.** Moving to a status with no `tool` entry for the active
  adapter — or a tool workspace lacking that state (e.g. no "Blocked" column in Linear) —
  stops and reports; never invent a fallback. Each adapter's setup section gains one line:
  create these states/labels in your workspace to match the board config.
- **Markdown adapter** needs no workspace setup — it writes whatever bracket text the
  config specifies, so custom boards work out of the box.

## 5. Validation, error handling, testing

Validation rules (checked before any board-driven action; violation = andon, naming the
config file explicitly):

1. Valid JSON.
2. Exactly one `initial: true` per machine.
3. At least one `terminal: true` per machine.
4. Every `transitions` entry names a defined status in the same machine.
5. Terminal statuses have empty `transitions`.
6. Every status is reachable from the initial status.
7. `owner` is exactly one of `role` / `team`, and the name resolves to an existing
   `roles/*.md` role (or abstract role) or `teams/` preset.
8. `requiresCommit` is not set on the initial status (a commit cannot be required to
   enter the board).

Mechanics:

- **`bin/launch-team.sh`** gains a `validate-board` subcommand (bash 3.2-portable; uses
  `python3` for JSON parsing, degrading to a clear "python3 required" message). The
  launcher runs it automatically before launching a team.
- **`tests/launcher-test.sh`** gains cases: default config passes; broken configs (two
  initials, unknown transition target, unreachable status, bad owner, terminal with
  outbound transitions) each fail with the right message.

## Out of scope

- Per-team boards (one global board per project).
- Doc-generation tooling (docs defer to the config in prose; no build step).
- Migration of existing `.workspace/` task files — scratch data; old `[Completed]` text
  remains as history, new moves follow the new board.
- Changing the feature-status machine's semantics beyond configurability (default stays
  Planned/Active/Resolved).
