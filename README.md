# Project Management Skill

A **portable, tool-agnostic bridge** that lets an AI coding agent (Claude Code, or any
agent that can read Markdown instructions) create, track, and update work items in *any*
project-management tool — Linear, Jira, GitHub Issues, a plain-Markdown fallback, or one
you add yourself — **without changing a single workflow.**

It is deliberately **language- and framework-agnostic.** Nothing in this bundle knows or
cares whether your codebase is TypeScript, Go, Rust, Python, .NET, or COBOL. It manages
*tickets*, not *code*.

---

## The problem it solves

Most teams hard-code their tracker into their AI instructions: "create a Jira story",
"move the Linear issue to In Review". The moment you switch tools — or work across
projects that use different tools — every workflow breaks and has to be rewritten.

This bundle applies the **ports-and-adapters (hexagonal) pattern** to project management:

```
                    ┌─────────────────────────────────────────┐
   Your workflows   │            THE PORT (stable)             │
   & the agent  ───▶│  vocabulary.md  ·  lifecycle.md          │
   speak only this  │  [feature] [task] [subtask]              │
                    │  [Planned] [Active] [Review] [Completed] │
                    └───────────────────┬─────────────────────┘
                                        │  selected by one config line
                      ┌─────────────────┼──────────────────┐
                      ▼                 ▼                  ▼
              ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
              │ adapters/    │  │ adapters/    │  │ adapters/    │
              │ Linear.md    │  │ Jira.md      │  │ Markdown.md  │   ← swap freely
              └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                     ▼                 ▼                 ▼
                Linear MCP        Atlassian MCP      local .md files
```

- **The port** (`reference/`) — the generic vocabulary and lifecycle scenarios. Your
  workflows and the agent *only ever* speak this. It never changes when you switch tools.
- **The adapters** (`adapters/`) — one file per tool. Each maps the generic vocabulary to
  a concrete tool and says exactly how to perform each operation.
- **The config** (`config/`) — a single line, `PRODUCT_MANAGEMENT_TOOL=<Name>`, selects
  the active adapter.
- **The consumer** — `SKILL.md`, plus any of your own workflows. They reference `[task]`,
  `[feature]`, and generic statuses — never "issue", "epic", or "story".

**Result:** change trackers by editing one line. Add a new tool by writing one adapter
file. No workflow, prompt, or agent instruction ever mentions a tool by name.

---

## Install into any project (2 minutes)

1. **Copy this folder** into the target repo. For Claude Code, the natural home is:
   ```
   <your-repo>/.claude/skills/project-management/
   ```
   (Any agent that reads Markdown can use it from anywhere — the path is not magic.)

2. **Pick your tool.** Edit `config/project-management.config.md` and set:
   ```
   PRODUCT_MANAGEMENT_TOOL=Markdown      # or Linear, Jira, GitHubIssues, ...
   ```
   `Markdown` needs zero external setup and works offline — a good first choice.

3. **Wire the tool's access** (skip for `Markdown`). Follow the *MCP / CLI Setup* section
   of your chosen `adapters/<Tool>.md`. For MCP tools, add the shown block to your
   agent's MCP config; for GitHub, ensure the `gh` CLI is authenticated.

4. **Use it.** Ask the agent to *"plan a feature"*, *"start task <id>"*, or
   *"move <id> to review"*. The skill resolves your active tool and does the rest.

To **switch tools later**, change the one config line (and wire the new tool's access).
Every existing workflow keeps working untouched.

---

## Add a brand-new tool (one file)

1. Copy `adapters/_TEMPLATE.md` to `adapters/<YourTool>.md`.
2. Fill in the five tables/sections (terminology, feature status, task status, ID mapping,
   how-to-operate) plus setup and init.
3. Set `PRODUCT_MANAGEMENT_TOOL=<YourTool>` in the config.

That's the entire integration. Nothing else in the bundle changes.

---

## What's in the box

| File | Role | Edit it? |
|---|---|---|
| `README.md` | This guide | — |
| `SKILL.md` | The operational skill the agent runs | Rarely |
| `config/project-management.config.md` | Selects the active tool + per-tool settings | **Yes, per project** |
| `reference/vocabulary.md` | The tool-agnostic contract (the port) | No — it's the stable interface |
| `reference/lifecycle.md` | The scenarios: create → work → track → complete | Tune to taste |
| `reference/team-roles.md` | Optional status-ownership model for multi-agent teams | If you run teams |
| `adapters/_TEMPLATE.md` | Scaffold for connecting any new tool | Copy it |
| `adapters/Linear.md` | Linear via MCP | — |
| `adapters/Jira.md` | Jira via Atlassian MCP | — |
| `adapters/GitHubIssues.md` | GitHub Issues via `gh` CLI or GitHub MCP | — |
| `adapters/Markdown.md` | Local Markdown files — no network, no setup | — |

---

## Design credit

The pattern is generalized from the PlatformPlatform codebase's
`.claude/reference/product-management/` design, extracted here so it can be reused in any
repository regardless of stack.
