# Jira

## Summary

Jira is Atlassian's hosted issue tracker. The agent talks to it through the **Atlassian
MCP server** — every read and write is an MCP tool call. Features map to Epics and tasks to
Stories under a project key.

## MCP / CLI Setup

Add the Atlassian MCP server and complete its OAuth flow on first use:

```json
{
  "mcpServers": {
    "atlassian": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.atlassian.com/v1/sse"]
    }
  }
}
```

Relevant config from `../config/project-management.config.md`:
`JIRA_PROJECT_KEY` (required to create items), `JIRA_DEFAULT_ASSIGNEE`.

## Terminology Mapping

| Generic term | Jira |
|---|---|
| `[Feature]` | Epic |
| `[Task]` | Story |
| `[Subtask]` | Bullet point in the story description |

## Feature Status Mapping

| Generic status | Jira (Epic) |
|---|---|
| `[Planned]` | To Do |
| `[Active]` | In Progress |
| `[Resolved]` | Done |

## Task Status Mapping

| Generic status | Jira (Story) |
|---|---|
| `[Planned]` | To Do |
| `[Active]` | In Progress |
| `[Review]` | In Review |
| `[Completed]` | Done |

> Jira workflows are per-project and often customized. If your board uses different status
> names (e.g. "Code Review", "Selected for Development"), edit these two tables only.

## ID Mapping

| Generic ID | Jira | Example |
|---|---|---|
| `featureId` | Epic key | `ENG-100` |
| `taskId` | Story key | `ENG-142` |

## Operations

| Generic operation | Jira (Atlassian MCP) |
|---|---|
| Create `[feature]` | Create issue of type **Epic** in `JIRA_PROJECT_KEY` |
| Create `[task]` under a feature | Create issue of type **Story**, parent/epic-link = `featureId` |
| Read a `[task]` | Get issue by key |
| List `[tasks]` in a feature | JQL search: `"Epic Link" = <featureId>` (or `parent = <featureId>`) |
| Set `[task]` status | Transition the issue to the mapped status (use the transition, not a raw field write) |
| Set `[feature]` status | Transition the Epic to the mapped status |
| Add a comment to a `[task]` | Add comment to the issue |

## Rules

- ALL operations MUST use MCP tools. If ANY call fails: **STOP** and report (andon cord).
  Never work around a failure.
- NEVER skip status updates.
- Status changes are **transitions**, not direct field edits — a status may be unreachable
  from the current one; if a transition is missing, pull the andon cord rather than forcing
  it.
- `featureId` is an Epic key; `taskId` is a Story key.

## Initialization

Call any read MCP tool (e.g. fetch the current user, or a 1-result JQL search) to confirm
authentication. If unavailable or auth fails: stop and tell the user to fix the MCP setup.
