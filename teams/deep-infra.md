# Team: Deep Infra

A cross-functional team for work that lives in cloud infrastructure: environment topology,
infrastructure-as-code, delivery pipelines, reliability engineering, and
operability. Use this preset when the deliverable is a safe, observable, and
cost-efficient platform layer — not a product feature.

```
ROSTER=team-lead principal-cloud-infrastructure-architect sceptical-architect senior-security-engineer senior-technical-product-manager senior-cloud-engineer senior-sre-engineer senior-qa-engineer integrator
REQUIRED_REVIEW_GATES=security
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-cloud-infrastructure-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-architect
PROTOCOL_SECURITY_REVIEWER=senior-security-engineer
PROTOCOL_QA=senior-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_BACKEND=senior-cloud-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and authors `[team-lead-approval]` |
| Principal Cloud and Infrastructure Architect | `teams/roles/principal-cloud-infrastructure-architect.md` | `principal-architect` | Primary infrastructure architecture position |
| Sceptical Architect — **independent gate** | `roles/sceptical-architect.md` | `sceptical-architect` | Blind-first design challenge and release-bound architecture review |
| Senior Security Engineer — **security gate** | `roles/senior-security-engineer.md` | `security-reviewer` | Independent IAM, supply-chain, secret, and blast-radius review |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope and acceptance criteria |
| Senior Cloud Engineer | `teams/roles/senior-cloud-engineer.md` | `backend` | Implements IaC, pipelines, cloud services, and networking |
| Senior SRE Engineer | `teams/roles/senior-sre-engineer.md` | `backend` (own [tasks]); `reviewer` (operability pass on cloud engineer's [tasks]) | Observability, SLOs, alerting, runbooks; operability review |
| Senior QA Engineer | `teams/roles/senior-qa-engineer.md` | `qa` | Test implementation and optional specialist findings |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Collaboration flow

The standard playbook (`teams/_PLAYBOOK.md`); one team-specific rule: every
`[design-note]` — whether from the cloud engineer or the SRE engineer — must
explicitly state the **rollback strategy** and **blast radius** before the
both architects will approve the design. No exceptions.

## Required review board

Deep infrastructure is the exception to the default-disabled security policy:
`REQUIRED_REVIEW_GATES=security` makes the Security Engineer an always-on roster
member and requires this four-party review for every bound package:

1. Principal Architect — `[architecture-approval]`
2. Sceptical Principal Architect — `[sceptical-architecture-approval]`
3. Senior Security Engineer — `[security-approval]`
4. Team Lead — `[team-lead-approval]`

The SRE may additionally provide an operability pass (plain comment or
`[review-findings]`). Security approval must precede the Team Lead's final
approval. Only after all four required approvals exist may the `integrator`
merge, commit, and mark the [task] `[Ready to deploy]` (mapped to `Ready for
production`).

## Launch

```bash
bin/launch-team.sh team deep-infra <feature-branch> <featureId>
```
