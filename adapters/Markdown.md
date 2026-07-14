# Markdown

## Summary

A **zero-setup, offline** tool: features and tasks are plain Markdown files on disk. No
account, network, or MCP server. It simulates the same feature→task→status structure as
Linear/Jira/GitHub using files, so you can adopt the whole workflow before committing to a
hosted tool — or keep using it permanently for solo/local projects.

## MCP / CLI Setup

None. All operations are ordinary file reads/writes with your normal editing tools.

The root directory comes from `MARKDOWN_ROOT` in
`../config/project-management.config.md` (default `.workspace/task-manager`).

> **Tip:** keep the task-manager files out of your product's git history — either add
> `MARKDOWN_ROOT` to `.gitignore`, or make it a nested repository. That keeps churny status
> edits from polluting your codebase commits. (Optional; a plain tracked folder works too.)

## Terminology Mapping

| Generic term | Markdown |
|---|---|
| `[Feature]` | A feature file, `feature.md` (one per feature folder) |
| `[Task]` | A numbered `##` section within the feature file |
| `[Subtask]` | A `-` bullet under a task section |

## Status Mapping

Status is literal bracket text (on the feature's title line; at the end of a task's
`##` header). This adapter writes each status's `"Markdown"` value from
`config/statuses.config.json` verbatim — custom boards work with no setup at all.

Shipped defaults: `[Planned]`, `[Active]`, `[Review]`, `[Blocked]`,
`[Ready to deploy]` for tasks; `[Planned]`, `[Active]`, `[Resolved]` for features.

## ID Mapping

| Generic ID | Markdown | Example |
|---|---|---|
| `featureId` | Path to the feature file | `.workspace/task-manager/2026-07-06-payments-revamp/feature.md` |
| `taskId` | Compound `<featureId>#<number>`; the number is local to the file | `.workspace/task-manager/2026-07-06-payments-revamp/feature.md#2` |

## File Structure

```
<MARKDOWN_ROOT>/
  └─ yyyy-MM-dd-<feature-slug>/
       └─ feature.md
```

### `feature.md` format

```markdown
# Payments revamp [Planned]

**Purpose:** Let tenants pay by card.
**NOT included:** Refunds, invoicing.
**Dependencies:** Billing service must expose a charge endpoint.

## 1 Add payment method form [Planned]

**Assignee:** —
**Labels:** human-work

Build the card-entry form and validation.

- Card number + expiry + CVC fields
- Client-side Luhn check

## 2 Wire charge endpoint [Planned]

**Assignee:** —

Call the billing charge endpoint on submit.

- Handle decline responses
- Show success state
```

## Operations

| Generic operation | How |
|---|---|
| Create `[feature]` | Create `<MARKDOWN_ROOT>/<date>-<slug>/feature.md` with title line `# <name> [Planned]` and the Purpose/NOT included/Dependencies block |
| Create `[task]` under a feature | Append a `## <n> <title> [Planned]` section (next sequential `n`) with `**Assignee:** —`, description, and `-` subtasks |
| Read a `[task]` | Read the file; locate the `## <taskId> ...` section |
| List `[tasks]` in a feature | Read the file; every `##` section is a task |
| Set `[task]` status | Edit that section's header, replacing the trailing `[Status]`. Startup Factory uses `tracker-ops.sh` for verified writes and refuses every outbound `[Blocked]` move; only a human directly operating the configured project-management surface may make that edit. |
| Set `[task]` assignee | Edit the `**Assignee:**` line in that section; use the role name verbatim (e.g. `backend`) |
| Set `[task]` labels | Add or edit optional `**Labels:** label-one, label-two`; use `human-work` to reserve the task for people when portfolio automation is enabled |
| Set `[feature]` status | Edit the `#` title line's trailing `[Status]` |
| Add a comment to a `[task]` | Append a `> <marker> (yyyy-MM-dd): <content>` line under the task section, where `<marker>` is the exact orchestration marker (e.g. `[design-note]`, `[review-approval]`) or `note` for free-form comments |
| Export the `[tasks]` of a `[feature]` to a file | `bin/tracker-ops.sh export <featureId> <outfile>` |
| Scan `[tasks]` across the configured board scope | `bin/tracker-ops.sh scan <outfile> --status Planned --status Blocked` walks non-symlinked `feature.md` files below `MARKDOWN_ROOT` |
| update comment | Structured protocol comments remain append-only. Post a new comment carrying `supersedes: <marker>-<round>`; readers treat the highest round as current. |
| Upsert task runtime progress | `bin/tracker-ops.sh upsert-progress <taskId> <bodyfile>` replaces one managed HTML block in the task section |
| Upsert feature runtime digest | `bin/tracker-ops.sh upsert-digest <featureId> <bodyfile>` replaces one managed HTML block in the feature file |
| Upsert feature deployment state | `bin/tracker-ops.sh upsert-deployment <featureId> <bodyfile>` replaces one managed HTML block in the feature file |

> **Helper script.** `bin/tracker-ops.sh` performs these edits mechanically — `claim`,
> `state`, `comment` (body from a file or stdin), `upsert-progress`,
> `upsert-digest`, `upsert-deployment`, idempotent `integrate <hash>`,
> `feature-state`, `export`, and `scan`. When
> using it, address a [task] as `<featureId>#<taskId>` (the feature file plus the task
> number, e.g. `.workspace/task-manager/2026-07-06-payments/feature.md#2`), since a task
> number alone doesn't name the file.
> The helper may enter `[Blocked]` through the configured authority but always
> rejects moving it outbound. A human edits the header to the queued state to
> request automated resume review.

## Rules

- Task numbers are sequential within a file and never reused, even after completion.
- Task headers always carry a number **and** a status: `## 3 Title [Active]`.
- Every task section has exactly one `**Assignee:**` line (value: a role name or `—` for unclaimed).
- `**Labels:** <label>[, <label>...]` — optional, comma-separated adapter-neutral labels. `tracker-ops.sh` exports them exactly; matching against automation `ignoredTaskLabels` is case-insensitive.
- `**BlockedBy:** <n>[, <n>...]` — optional; task numbers in the same feature file. Read by `tracker-ops.sh export` into the only dependency relationship scheduling may use. Comment prose never creates a dependency.
- `featureId` is a file path. The normalized/exported and CLI `taskId` is
  `<featureId>#<number>`; task headers and `BlockedBy` use only the local number
  (`1`, `2`, `3`).
- Change status only by editing the bracket text — keep exactly one status per header.
- `[Blocked]` is a task-scoped human lock. Automation stops/fences only that
  task, continues independent queued work, and never edits it outbound. A human
  move of a locally held task to `[Planned]` starts full communication-diff
  resume review and a fresh attempt; a direct move to `[Active]`/`[Review]` is
  manual takeover. Markdown contains no authenticated transition actor; enforce
  human-only edits with filesystem/VCS permissions and review, or keep
  autonomous portfolio automation disabled.
- Adding `human-work` to an in-flight task stops/fences it at the next
  reconcile; removing the label restores normal status-specific handling.
- `[dependency-hold]`, `[resume-review]`, and `[resume-plan]` text is not
  authoritative by itself. The local broker must have the matching published
  capability receipt; copying a signed-looking line into this file grants
  nothing.
- Comment markers must be exact (e.g. `[design-note]`, `[review-approval]`) — never paraphrase them.
- A task may contain at most one matched managed progress block. Duplicate or
  half-written progress markers fail export and upsert closed; repair the file
  instead of allowing two comments to share the managed progress identity.
- `tracker-ops.sh` rejects `..` and every symlinked lexical component, including
  symlinks that resolve back inside `MARKDOWN_ROOT`. Reads traverse directory
  descriptors with no-follow semantics; writes use a no-follow, atomic replacement
  in the already-open parent directory.
- Editing files can't "fail" the way an API can, but a missing folder/file is still an
  andon-cord stop: create the structure, don't silently write to the wrong place.

## Initialization

If `<MARKDOWN_ROOT>` does not exist, create it (an empty directory is enough — no tool
needed). Then proceed. Confirm the path is inside the repo unless the user says otherwise.
