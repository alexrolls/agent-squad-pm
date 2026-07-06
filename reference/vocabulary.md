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

Two small state machines. Adapters map each generic status to a concrete tool status.

### Feature statuses

| Generic status | Meaning |
|---|---|
| `[Planned]` | Defined, not yet started. |
| `[Active]` | At least one task is in progress. |
| `[Resolved]` | All tasks complete; the feature is delivered. |

### Task statuses

| Generic status | Meaning | Typical transition owner (single-agent = you) |
|---|---|---|
| `[Planned]` | Ready to be picked up. | — |
| `[Active]` | Being implemented (or being fixed after review). | Implementer |
| `[Review]` | Implementation done; awaiting review. | Reviewer |
| `[Completed]` | Reviewed, verified, and committed. | Whoever finalizes/commits |

### Legal task transitions

```
[Planned] ──▶ [Active] ──▶ [Review] ──▶ [Completed]
                 ▲             │
                 └─────────────┘        (review found problems → back to [Active])
```

Any other transition is a mistake. If an item is not in the status a step expects, that's
an **andon cord** condition — see `lifecycle.md`.

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
3. **Status is never skipped.** Move through the state machine in order.
4. **Reads are cheap, writes are deliberate.** Confirm the current status before a write
   when `STRICT_STATUS=true`.
