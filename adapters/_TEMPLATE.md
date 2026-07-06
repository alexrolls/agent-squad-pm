# <ToolName>

> Copy this file to `<ToolName>.md`, fill every section, then set
> `PRODUCT_MANAGEMENT_TOOL=<ToolName>` in `../config/project-management.config.md`.
> An adapter is *pure translation*: it maps the generic port
> (`../reference/vocabulary.md`) to one concrete tool. It must not add new lifecycle rules —
> those live in `../reference/lifecycle.md`.

## Summary

One or two sentences: what this tool is and how the agent talks to it (MCP server / CLI /
files / REST). State the access mechanism up front.

## MCP / CLI Setup

Exactly what the user must do once so operations work. For an MCP tool, the config block:

```json
{
  "mcpServers": {
    "<server-name>": { "type": "http", "url": "https://..." }
  }
}
```

For a CLI tool, the install/auth commands. For a file-based tool, "none".

## Terminology Mapping

| Generic term | <ToolName> |
|---|---|
| `[Feature]` | <e.g. Project / Epic / Milestone> |
| `[Task]` | <e.g. Issue / Story / Ticket> |
| `[Subtask]` | Bullet/checklist item in the [task] description |

## Feature Status Mapping

| Generic status | <ToolName> |
|---|---|
| `[Planned]` | <...> |
| `[Active]` | <...> |
| `[Resolved]` | <...> |

## Task Status Mapping

| Generic status | <ToolName> |
|---|---|
| `[Planned]` | <...> |
| `[Active]` | <...> |
| `[Review]` | <...> |
| `[Completed]` | <...> |

## ID Mapping

| Generic ID | <ToolName> | Example |
|---|---|---|
| `featureId` | <what it is> | <...> |
| `taskId` | <what it is> | <...> |

## Operations

The concrete verb for each generic operation the lifecycle uses. Be explicit — name the
MCP tool / CLI command / file edit.

| Generic operation | How to perform it in <ToolName> |
|---|---|
| Create `[feature]` | <...> |
| Create `[task]` under a feature | <...> |
| Read a `[task]` | <...> |
| List `[tasks]` in a feature | <...> |
| Set `[task]` status | <...> |
| Set `[feature]` status | <...> |
| Add a comment to a `[task]` | <...> |

## Rules

- All operations use the mechanism above — never fabricate an update.
- On any failure: **stop and report** (andon cord). Never work around it.
- Never skip a status transition.
- <Any tool-specific gotchas: formatting quirks, rate limits, required fields.>

## Initialization

A cheap read that proves access works (e.g. list one item, `whoami`, check the file dir).
If it fails: stop, tell the user to fix `MCP / CLI Setup`, do not proceed.
