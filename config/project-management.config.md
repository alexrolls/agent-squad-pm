# Project Management Configuration

This is the **one file you edit per project.** It selects the active tool and holds any
per-tool settings. The skill reads this file first, then loads the matching adapter from
`../adapters/<Tool>.md`.

---

## Active Tool

Set exactly one value. It must match an adapter filename in `../adapters/` (without `.md`).

```
PRODUCT_MANAGEMENT_TOOL=Markdown
```

Available out of the box: `Linear`, `Jira`, `GitHubIssues`, `Markdown`.
Add your own by creating `../adapters/<Name>.md` (copy `_TEMPLATE.md`).

> `Markdown` is the zero-setup default: it stores features and tasks as local files and
> needs no network, account, or MCP server. Switch to a real tool when you're ready.

---

## Per-Tool Settings

Only the block for your active tool is used. Leave the rest as-is. `null` means "let the
adapter/tool decide" (e.g. prompt, or use the tool's default).

### Linear
```
LINEAR_DEFAULT_TEAM=null          # Team key or name new features/tasks are created under
LINEAR_DEFAULT_PROJECT=null       # Optional default Project ([feature]) to file tasks into
```

### Jira
```
JIRA_PROJECT_KEY=null             # e.g. "ENG" — required to create Epics/Stories
JIRA_DEFAULT_ASSIGNEE=null        # Optional accountId or email
```

### GitHubIssues
```
GITHUB_REPO=null                  # "owner/repo"; null = infer from the current git remote
GITHUB_USE_MCP=false              # false = use the `gh` CLI; true = use the GitHub MCP server
```

### Markdown
```
MARKDOWN_ROOT=.workspace/task-manager   # Where feature/task files live (repo-relative)
```

---

## Optional Behaviour Flags

Apply regardless of tool.

```
TEAM_MODE=false        # true enables the status-ownership model in reference/team-roles.md
STRICT_STATUS=true     # true = refuse an action if the item is not in the expected status
                       #        (the "andon cord" — see reference/lifecycle.md)
```
