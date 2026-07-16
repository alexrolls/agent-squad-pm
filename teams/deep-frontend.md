# Team: Deep Frontend

A cross-functional team for work that lives entirely in the UI layer: component
architecture, complex client state, design-system work, accessibility, and
rendering performance. Use this preset when the deliverable is a correct,
accessible, and performant frontend — the backend contract is stable or can
be mocked until `[api-ready]` arrives.

```
ROSTER=team-lead principal-frontend-architect sceptical-architect senior-security-engineer senior-technical-product-manager senior-frontend-engineer senior-qa-engineer integrator
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-frontend-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-architect
PROTOCOL_SECURITY_REVIEWER=senior-security-engineer
PROTOCOL_QA=senior-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_FRONTEND=senior-frontend-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and authors `[team-lead-approval]` |
| Principal Frontend Architect | `teams/roles/principal-frontend-architect.md` | `principal-architect` | Primary frontend architecture position |
| Sceptical Architect — **independent gate** | `roles/sceptical-architect.md` | `sceptical-architect` | Blind-first design challenge and release-bound architecture review |
| Senior Security Engineer — **security gate** | `roles/senior-security-engineer.md` | `security-reviewer` | Independent browser/client security review and `[security-approval]` |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope and acceptance criteria |
| Senior Frontend Engineer | `teams/roles/senior-frontend-engineer.md` | `frontend` | Implements components, state wiring, and accessibility |
| Senior QA Engineer | `teams/roles/senior-qa-engineer.md` | `qa` | Test implementation, accessibility verification, and optional findings |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Collaboration flow

The standard playbook (`teams/_PLAYBOOK.md`); one team-specific rule: every
[task]'s acceptance criteria must include explicit accessibility expectations
(WCAG level, interaction patterns, or assistive-technology behaviour), and QA
verifies them the same way it verifies any other criterion — with a `file:line`
citation and a test citation.

## Required review board

These four agents review the same bound package independently and may run in
parallel:

1. Principal Architect — `[architecture-approval]`
2. Sceptical Principal Architect — `[sceptical-architecture-approval]`
3. Senior Security Engineer — `[security-approval]`
4. Team Lead — `[team-lead-approval]`

Only after all four approvals exist may the `integrator` merge, commit, and mark
the [task] `[Ready to deploy]` (mapped to `Ready for production`).

## Launch

```bash
bin/launch-team.sh team deep-frontend <feature-branch> <featureId>
```
