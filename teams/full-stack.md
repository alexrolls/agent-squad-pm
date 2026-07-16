# Team: Full Stack

A cross-functional team for product features that cut through the whole stack — schema
to API to UI. The default preset when the work isn't deeply specialized.

```
ROSTER=team-lead principal-software-architect sceptical-architect senior-security-engineer senior-technical-product-manager senior-full-stack-engineer senior-qa-engineer integrator
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-software-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-architect
PROTOCOL_SECURITY_REVIEWER=senior-security-engineer
PROTOCOL_QA=senior-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_BACKEND=senior-full-stack-engineer
PROTOCOL_FRONTEND=senior-full-stack-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and authors `[team-lead-approval]` |
| Principal Software Architect | `teams/roles/principal-software-architect.md` | `principal-architect` | Primary architecture position across the stack |
| Sceptical Architect — **independent gate** | `roles/sceptical-architect.md` | `sceptical-architect` | Blind-first design challenge and release-bound architecture review |
| Senior Security Engineer — **security gate** | `roles/senior-security-engineer.md` | `security-reviewer` | Independent threat-focused review and `[security-approval]` |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope and acceptance criteria |
| Senior Full Stack Engineer | `teams/roles/senior-full-stack-engineer.md` | `backend` + `frontend` | Implements complete vertical slices |
| Senior QA Engineer | `teams/roles/senior-qa-engineer.md` | `qa` | Test implementation and optional specialist findings |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Collaboration flow

The standard playbook (`teams/_PLAYBOOK.md`); no team-specific stages.

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
bin/launch-team.sh team full-stack <feature-branch> <featureId>
```
