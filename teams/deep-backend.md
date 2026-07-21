# Team: Deep Backend

A cross-functional team for work that lives entirely in the backend: domain logic, data
models, APIs, migrations, and performance or scale concerns. Use this preset when
there is little or no UI surface — the deliverable is a correct, safe, and
performant service boundary.

```
ROSTER=team-lead principal-backend-architect sceptical-architect senior-technical-product-manager senior-staff-engineer senior-qa-engineer integrator
REQUIRED_REVIEW_GATES=null
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-backend-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-architect
PROTOCOL_SECURITY_REVIEWER=senior-security-engineer
PROTOCOL_QA=senior-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_BACKEND=senior-staff-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and authors `[team-lead-approval]` |
| Principal Backend Architect | `teams/roles/principal-backend-architect.md` | `principal-architect` | Primary service/data architecture position |
| Sceptical Architect — **independent gate** | `roles/sceptical-architect.md` | `sceptical-architect` | Blind-first design challenge and release-bound architecture review |
| Senior Security Engineer — **on-demand (not in startup roster)** | `roles/senior-security-engineer.md` | `security-reviewer` | Joins declared auth, data, abuse-path, or privileged-operation work |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope and acceptance criteria |
| Senior Staff Engineer | `teams/roles/senior-staff-engineer.md` | `backend` | Implements domain logic, APIs, and migrations |
| Senior QA Engineer | `teams/roles/senior-qa-engineer.md` | `qa` | Test implementation and optional specialist findings |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Collaboration flow

The standard playbook (`teams/_PLAYBOOK.md`); one team-specific rule: every
migration [task] must include a tested rollback [subtask]. The rollback path is
verified before the Team Lead may grant `[team-lead-approval]` — no exceptions.

## Required review board

Three core agents review every bound package:

1. Principal Architect — `[architecture-approval]`
2. Sceptical Principal Architect — `[sceptical-architecture-approval]`
3. Team Lead — `[team-lead-approval]`

Security is disabled at startup. Planning adds `review-gates: security` for
authn/authz, tenancy, secrets, cryptography, sensitive data, untrusted input,
abuse resistance, migrations with destructive or privileged effects, and
network/deployment concerns. The mapped Senior Security Engineer then joins
for review or operational work, and `[security-approval]` must precede the Team
Lead's final approval. Either architect pushes back when risk exists but the
gate is absent. Integration requires the core three plus declared gates.

## Launch

```bash
bin/launch-team.sh team deep-backend <feature-branch> <featureId>
```
