# Linear

## Summary

Linear is a hosted issue tracker. The agent talks to it exclusively through the **Linear
MCP server** — every read and write is an MCP tool call (`list_issues`, `create_issue`,
`update_issue`, `create_comment`, etc.).

## MCP / CLI Setup

Add the Linear MCP server to your agent's MCP config, then authenticate in the browser flow
it triggers on first use:

```json
{
  "mcpServers": {
    "linear-server": {
      "type": "http",
      "url": "https://mcp.linear.app/mcp"
    }
  }
}
```

Relevant config from `../config/project-management.config.md`:
`LINEAR_DEFAULT_TEAM`, `LINEAR_DEFAULT_PROJECT`.

## Terminology Mapping

| Generic term | Linear |
|---|---|
| `[Feature]` | Project |
| `[Task]` | Issue |
| `[Subtask]` | Bullet point in the issue description |

## Feature Status Mapping

Linear Projects use states like Backlog / Planned / In Progress / Completed.

| Generic status | Linear (Project state) |
|---|---|
| `[Planned]` | Planned |
| `[Active]` | In Progress |
| `[Resolved]` | Completed |

## Task Status Mapping

| Generic status | Linear (Issue workflow state) |
|---|---|
| `[Planned]` | Todo |
| `[Active]` | In Progress |
| `[Review]` | In Review |
| `[Completed]` | Done |

> Team workflow states are configurable in Linear. If a team renames "In Review", update
> this table — the port never changes, only this mapping does.

## ID Mapping

| Generic ID | Linear | Example |
|---|---|---|
| `featureId` | Project id or name | `Payments revamp` |
| `taskId` | Issue identifier | `PP-445` |

## Operations

| Generic operation | Linear MCP call |
|---|---|
| Create `[feature]` | `create_project` (team = `LINEAR_DEFAULT_TEAM` if set) |
| Create `[task]` under a feature | `create_issue` with `project: "<featureId>"` |
| Read a `[task]` | `get_issue` (or `list_issues` filtered) |
| List `[tasks]` in a feature | `list_issues` with `project: "<featureId>"` |
| Set `[task]` status | `update_issue` with the mapped workflow `state` |
| Set `[feature]` status | `update_project` with the mapped state |
| Add a comment to a `[task]` | `create_comment` on the issue |

## Rules

- All operations use MCP tools. If a call fails: **stop immediately** and report the error
  (andon cord) — do not work around it.
- Never skip status updates; move issues through Todo → In Progress → In Review → Done.
- `featureId` is a Project id/name; `taskId` is an issue identifier like `PP-445`.
- Query a feature's tasks with `list_issues` + `project`, never by guessing identifiers.

## Initialization

Call any read MCP tool (e.g. `list_issues` limited to 1) to confirm authentication. If it
is unavailable or auth fails: stop the workflow and tell the user to fix the MCP setup.
