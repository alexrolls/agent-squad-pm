# Dispatch — the loop lives outside the agent

Both production runs proved the same thing: the tracker+marker state model
survives any restart, but an agent's promise to "check back in N minutes"
does not — one-shot runtimes exit, and the gate stalls. So no agent owns
time. **Dispatch is a stateless read-and-act pass** executed by machinery:

| Runtime | Loop owner | One pass = |
|---|---|---|
| CLI (tmux / background processes) | `bin/dispatch.sh <team> <featureId> --once` (or `--watch`) | process artifact outbox → read tracker export → sync PM projection → claim/launch task instances and gate roles |
| Harness (in-session subagents) | the team-lead orchestrator itself — its native event loop | same table, executed directly: subagent spawn = role launch, idle notifications = heartbeats |

`--watch` needs a persistent shell (tmux window or `nohup`) — **the human
owns that process**, explicitly. Hiding this ownership is how a pipeline
silently stalls for hours.

## The event table

One pass reads the [feature]'s task export, the team mailboxes, and the
heartbeat files, then acts top to bottom:

| State observed | Action |
|---|---|
| `[Blocked]` [task] whose `blockedBy` [tasks] are all terminal | Auto-move `Blocked → <resume-status from block comment>` + comment — no agent launch (adapter caveat below) |
| `[design-note]` with no later `[design-approved]`/`[design-pushback]` | Launch principal-architect with the **whole** pending-design queue |
| [Task](s) in `[Review]` missing `[review-approval]` / `[architecture-approval]` since the last `[review-request]` | Launch reviewer / principal-architect with the **whole** review queue |
| [Task](s) in `[Review]` holding both approvals | Launch integrator with the merge queue in dependency order |
| Dispatchable `[Planned]` [tasks] (design approved, blockers terminal, slot/resource-safe) | Atomically claim and launch one fresh task instance per ready-wave member |
| `[Planned]` task missing metadata/gate, resource conflict, stale/artifact-less-idle teammate | Launch team-lead with the whole exception queue |
| Nothing actionable | Exit cleanly, print "nothing actionable" |

## Rules

- **Dedup:** gate roles are deduplicated by role; workers by
  `<role>/<taskId>/<attempt>`. Two independent backend tasks may run at once,
  while the same task attempt never double-launches. An atomic dispatch lock
  prevents overlapping passes from racing the claim.
- **Queue message before boot:** the pass writes the queue into the role's
  mailbox (`mailbox/<role>/NNN-dispatcher.md`) *before* launching, so the
  role boots as a queue consumer (drain every item, post per-[task] markers,
  exit).
- **Task packet before boot:** a worker launch creates its task branch,
  provisioned worktree, immutable task packet, report path, execution record,
  and task-scoped pid before starting the model. The task prompt does not inline
  the full orchestration reference.
- **End of turn = exit.** Role briefs contain no self-scheduling. An agent
  that finished its queue delivers its artifacts and exits; the next pass
  owns what happens next.
- **Auto-unblock scope:** performed automatically only where the adapter's
  `blockedBy` read is reliable (Linear, Jira). GitHubIssues and Markdown are
  **suggest-only** — the pass prints the suggestion and the team-lead
  confirms. Override per invocation with `--unblock=auto|suggest|off`.
  Auto-unblock writes only when the latest comment containing `blocked-by:`
  also carries a legal `resume-status: <Status>` line (see lifecycle Scenario 7);
  without one the pass prints a lead-actionable suggestion and routes to the
  team-lead.
- **Policy stays where it was:** the pipelined dispatch rules (independence,
  sweep gate, freeze protocol — `reference/orchestration.md` → *Execution
  modes*) are decisions the **team-lead** makes during its pass. The
  dispatcher is the trigger mechanism that makes "the moment [task] N enters
  `[Review]`" actually fire; it never overrides lead policy.
- **Event wakeup with polling fallback:** every runtime/outbox/projection event
  appends to `events.ndjson`. `--watch` wakes within about one second when the
  count changes and otherwise falls back to `POLL_INTERVAL_SECONDS`. Events are
  hints only; every pass re-reads the tracker as truth.
- **PM projection:** every non-dry pass idempotently upserts one `[progress]`
  artifact per task and one `[digest]` per feature. No agent is trusted to keep
  the human view current manually.
- **Preset rosters:** the script launches the seven protocol roles. Where a
  preset maps a queue to a specialized role (e.g. `senior-qa-engineer` as
  reviewer), the launched team-lead routes the queue; the reviewer launch is
  skipped if its `_CMD` is null.
- **Long features (harness):** past ~20 [tasks] the orchestrator should
  compress processed-event state between turns (its context is the loop
  state); the tracker remains the source of truth for anything dropped.
