# Vocabulary — The Tool-Agnostic Contract

This is the **port**: the stable interface every workflow and agent speaks. It never
changes when you switch tools. Adapters translate it to a concrete tool; consumers must
never bypass it by naming a tool directly.

> **The one rule that makes everything else work:** in any workflow, prompt, commit
> message, comment, or agent instruction, refer to work items **only** by the generic
> terms below. Never write "issue", "epic", "story", "ticket", or "work item". If you
> find yourself typing a tool-specific word, you've leaked an implementation detail into
> the port — stop and use the generic term.

---

## Work-Item Hierarchy

| Generic term | Meaning | Notation (use consistently) |
|---|---|---|
| **Feature** | A collection of related tasks — one shippable capability or initiative. | `[feature]` `[features]` `[Feature]` `[Features]` |
| **Task** | A complete vertical slice of work — independently reviewable and completable. | `[task]` `[tasks]` `[Task]` `[Tasks]` |
| **Subtask** | A checklist item *inside* a task's description. **Not tracked as its own item.** | `[subtask]` `[subtasks]` `[Subtask]` `[Subtasks]` |

Preserve bracket, case, and pluralization exactly (`[task]` vs `[Task]` vs `[tasks]`) so
the terms are unambiguous and greppable.

### Banned terms (never appear in a workflow)

`Issue` · `Epic` · `Story` · `User Story` · `Work Item` · `Ticket` · `Bug` (as a type) ·
`Card` · `Backlog Item`

These belong **only** inside `adapters/<Tool>.md`, where the mapping is defined once.

---

## Status Model

Statuses are **configured, not fixed**. The single source of truth is
`config/statuses.config.json`: for features and for tasks it defines the status list,
each status's legal outbound `transitions`, its `owner` — the team or single agent that
works items sitting in that status — and per-adapter `tool` mappings.

Rules that hold for every board:

- **Bracket notation.** Write any status exactly as `[Status Name]` — bracketed, exact
  case, greppable (`[Planned]`, `[Ready to deploy]`).
- **Exactly one `initial` status** per machine — where new items are created.
- **At least one `terminal` status** — where work ends; it has no outbound transitions.
- **`requiresCommit`** — entering such a status is atomically coupled to a successful
  commit, performed by that status's owner.
- **"Next status" means a status listed in the current status's `transitions`.** Any
  other move is illegal — an **andon cord** condition (see `lifecycle.md`).

### The default board (shipped)

Features: `[Planned]` → `[Active]` → `[Resolved]`.

Tasks:

| Status | Owner (default) | Transitions to | Notes |
|---|---|---|---|
| `[Planned]` | team-lead | Active | initial |
| `[Active]` | implementer | Review, Blocked | |
| `[Review]` | reviewer | Active, Ready to deploy, Blocked | rework returns to Active |
| `[Blocked]` | team-lead | Planned, Active, Review | work is stuck; owner unblocks |
| `[Ready to deploy]` | integrator | — | terminal; `requiresCommit` |

This table is an **example** — the JSON is authoritative. Projects add, rename, or
remove statuses by editing the config; no other file changes as long as owners and
tool mappings are set. Validate edits with `bin/launch-team.sh validate-board`.

---

## Identifiers

| Generic ID | Meaning |
|---|---|
| `featureId` | Opaque handle for a feature. Its concrete form is defined per adapter (a project id, an epic key, a milestone, a file path…). |
| `taskId` | Opaque handle for a task (an issue key like `ENG-142`, a number, a file+section…). |

Treat both as **opaque strings**. Never parse, construct, or assume their format in a
workflow — only the adapter knows the shape.

---

## Invariants every adapter must honour

1. **All operations go through the tool's real interface** (MCP, CLI, or files) — never
   fabricate an update you didn't actually perform.
2. **Fail loud.** If an operation fails, stop and report it. Never silently work around a
   failure or pretend a status changed.
3. **Status moves follow the configured `transitions` graph.** Never skip, invent, or reverse a move the board does not define.
4. **Reads are cheap, writes are deliberate.** Confirm the current status before a write
   when `STRICT_STATUS=true`.
