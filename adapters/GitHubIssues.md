# GitHub Issues

## Summary

Uses native GitHub Issues, grouped by Milestone. Default access is the **`gh` CLI** (no MCP
server required); set `GITHUB_USE_MCP=true` to use the GitHub MCP server instead. A
`[feature]` is a Milestone, a `[task]` is an Issue, task status is carried by labels, and
`[Ready to deploy]` closes the issue.

## MCP / CLI Setup

**CLI (default):** install and authenticate the GitHub CLI once:

```bash
gh auth login          # one-time, interactive
gh auth status         # verify
```

Interactive/manual use may infer the repo from the current git remote when `GITHUB_REPO`
is `null`; unattended automation requires an explicit `GITHUB_REPO=owner/repository` and
refuses inference. Create the `status:*` labels for every
non-terminal status in the board once:

```bash
gh label create "status:planned" --color BFD4F2 2>/dev/null || true
gh label create "status:active"  --color 0E8A16 2>/dev/null || true
gh label create "status:review"  --color FBCA04 2>/dev/null || true
gh label create "status:blocked" --color E4E669 2>/dev/null || true
```
(`[Ready to deploy]` needs no label — it's the closed state.)

> Create `status:*` labels for every non-terminal status in `config/statuses.config.json`
> before running the workflow — a missing label is an andon stop.

**MCP (optional):** add the GitHub MCP server and set `GITHUB_USE_MCP=true`:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "<token>" }
    }
  }
}
```

`dispatch.sh --watch` (CLI mode) requires `GITHUB_USE_MCP=false` (the default `gh` CLI path). Set `GITHUB_USE_MCP=true` only for harness/agent mode.

## Terminology Mapping

| Generic term | GitHub |
|---|---|
| `[Feature]` | Milestone |
| `[Task]` | Issue |
| `[Subtask]` | Task-list checkbox (`- [ ] ...`) in the issue body |

## Status Mapping

Statuses come from `config/statuses.config.json` — each status's `tool` map holds this
adapter's concrete value under the `"GitHubIssues"` key. This adapter's *mechanism* for
setting a status is: open/closed + one `status:*` label (setting a status removes the
previous `status:*` label).

**Missing mapping = andon.** If a status has no `"GitHubIssues"` entry, or the repo
lacks the required `status:*` label, stop and report — never invent a fallback status.

Shipped defaults (the default board):

| Status | GitHub (Issue) |
|---|---|
| `[Planned]` | open + `status:planned` |
| `[Active]` | open + `status:active` |
| `[Review]` | open + `status:review` |
| `[Blocked]` | open + `status:blocked` |
| `[Ready to deploy]` | closed |

Feature statuses `[Planned]` / `[Active]` / `[Resolved]` map to milestone open (no task
started) / milestone open (≥1 task active, derived) / milestone closed.

> Setting a status label means **removing the previous** status label and adding the new
> one — never leave two status labels on one issue.

## ID Mapping

| Generic ID | GitHub | Example |
|---|---|---|
| `featureId` | Milestone number or title | `Payments revamp` / `3` |
| `taskId` | Issue number | `142` |

## Operations

CLI column assumes `-R <GITHUB_REPO>` is appended when `GITHUB_REPO` is set.

| Generic operation | `gh` CLI |
|---|---|
| Create `[feature]` | `gh api repos/:owner/:repo/milestones -f title="<name>" -f description="<...>"` |
| Create `[task]` under a feature | `gh issue create --title "<t>" --body "<...>" --milestone "<featureId>" --label status:planned` |
| Read a `[task]` | `gh issue view <taskId> --comments` |
| List `[tasks]` in a feature | `gh issue list --milestone "<featureId>" --state all` |
| Set `[task]` status | `gh issue edit <taskId> --remove-label "status:planned" --add-label "status:active"` (remove the label matching the [task]'s current status — read it first; globs do not work with `--remove-label`) |
| Set `[task]` → `[Ready to deploy]` | `gh issue close <taskId>` |
| Reopen (rework) | `gh issue reopen <taskId> --add-label status:active` |
| Set `[feature]` → `[Resolved]` | `gh api -X PATCH repos/:owner/:repo/milestones/<n> -f state=closed` |
| Add a comment to a `[task]` | `gh issue comment <taskId> --body-file <file>` (or `--body-file -` for stdin — prefer files over `--body` to avoid shell-quoting) |
| Export the `[tasks]` of a `[feature]` to a file | `bin/tracker-ops.sh export <featureId> <outfile>` exhaustively paginates milestones, repository issues, comments, and `GET /issues/<n>/dependencies/blocked_by` through `gh api`; pull requests are excluded |
| Scan `[tasks]` across the configured board scope | `bin/tracker-ops.sh scan <outfile> --status Planned --status Blocked` exhaustively paginates the configured repository, normalizes milestone/state labels, and hydrates `blockedBy` from the issue-dependency REST endpoint; pull requests are excluded |
| update comment | `gh api -X PATCH repos/<owner>/<repo>/issues/comments/<commentId> -f body=...`; or `bin/tracker-ops.sh update-comment ...`. Feature digest: milestones take no comments — edit the milestone description instead. |
| Upsert task runtime progress | `bin/tracker-ops.sh upsert-progress <taskId> <bodyfile>` updates one managed issue comment |
| Upsert feature runtime digest | `bin/tracker-ops.sh upsert-digest <milestone> <bodyfile>` updates one managed block in the milestone description |
| Upsert feature deployment state | `bin/tracker-ops.sh upsert-deployment <milestone> <bodyfile>` updates one managed block in the milestone description |

> **Helper script.** `bin/tracker-ops.sh` wraps the recurring operations over the `gh`
> CLI — `claim`, `state` (does the label juggling and open/close for you), `comment`
> (body from a file or stdin), `upsert-progress`, `upsert-digest`, idempotent
> `integrate <hash>`, `export`, `scan`, `feature-state`, and
> `upsert-deployment`. This table remains the spec; the script is
> the ergonomic path.

## Rules

- Every write goes through `gh` (or the GitHub MCP tools when `GITHUB_USE_MCP=true`). On a
  non-zero exit / MCP error: **stop and report** (andon cord). Never fake success.
- `[Blocked]` is a task-scoped human lock. The deterministic backend may apply
  its label under configured authority but rejects every outbound transition;
  only a human changes the issue back to the planned label/state (resume
  barrier when a local hold exists) or another working/review label (manual
  takeover). Independent planned work continues.
  The adapter cannot prove who changed a label. Enforce outbound status-label
  changes with an external permission/provenance control restricted to human
  principals; if the repository cannot do that, human-only exit is an
  operational policy and autonomous portfolio automation must remain disabled.
- A `human-work` label prevents new automatic claims/launches and stops/fences a
  matching in-flight task at the next reconcile; independent tasks continue.
- Automated reads use `gh api --paginate --slurp` and reject malformed pages instead of
  treating a partial response as a complete board snapshot.
- `blockedBy` comes only from GitHub's first-class
  `GET /repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by`
  connection. If that endpoint is unavailable on the GitHub deployment,
  unsupported, unauthorized, or cannot be paginated completely, export/scan
  fails closed; it never substitutes an empty dependency list.
- Exported comments include `createdAt`, `updatedAt`, and revision (`updated_at`) and
  are normalized by last modification. Editing an older approval or pushback makes
  that verdict fresh and triggers complete authorization-envelope revalidation.
- Exactly one `status:*` label at a time on an open issue.
- `[Ready to deploy]` = closed; reopening for rework re-adds `status:active`.
- `featureId` is a Milestone (title or number); `taskId` is an Issue number.
- Hold-control marker text is acted on only with the matching local published
  broker receipt. GitHub authorship and a copied team-lead signature cannot
  impersonate `[dependency-hold]`, `[resume-review]`, or `[resume-plan]`.

## Initialization

Run `gh auth status` (CLI) or a 1-item list via MCP to confirm access. If it fails: stop
and tell the user to run `gh auth login` / fix the token — do not proceed.
