# Team: Deep Security

A cross-functional team for security-feature development, codebase hardening, and
threat-model-driven work — including authorized adversarial verification of the
team's own changes. Use when the primary deliverable is a security property of
the codebase itself.

```
ROSTER=team-lead principal-security-architect sceptical-architect senior-security-engineer senior-technical-product-manager senior-security-implementation-engineer senior-penetration-tester senior-qa-engineer integrator
REQUIRED_REVIEW_GATES=security
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-security-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-architect
PROTOCOL_SECURITY_REVIEWER=senior-security-engineer
PROTOCOL_QA=senior-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_BACKEND=senior-security-implementation-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and authors `[team-lead-approval]` |
| Principal Security Architect | `teams/roles/principal-security-architect.md` | `principal-architect` | Primary security architecture position; writes the threat model |
| Sceptical Architect — **independent gate** | `roles/sceptical-architect.md` | `sceptical-architect` | Blind-first threat/design challenge and release-bound architecture review |
| Senior Security Engineer — **required security gate** | `roles/senior-security-engineer.md` | `security-reviewer` | Reviews every package for exploitable flaws and grants `[security-approval]` |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope and acceptance criteria; co-authors the threat model |
| Senior Security Implementation Engineer | `teams/roles/senior-security-implementation-engineer.md` | `backend` | Implements security features, hardening, and vulnerability fixes |
| Senior Penetration Tester | `teams/roles/senior-penetration-tester.md` | specialist reviewer (stage 5.2); `qa` for test-tooling [tasks] | Authorized adversarial verification of the team's own implemented changes |
| Senior QA Engineer | `teams/roles/senior-qa-engineer.md` | `qa` | Test implementation and independent verification evidence |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Collaboration flow

The standard playbook (`teams/_PLAYBOOK.md`), plus one team-specific stage
inserted between Intake and Planning:

**Threat model (planning).** Before any [task] is created, the Principal
Security Architect and the TPM jointly produce a threat model for the [feature]:
assets, trust boundaries, identified threats, and mitigations. Each mitigation
maps directly to a [task] acceptance criterion. The TPM must approve the threat
model's scope before the architect creates [tasks] in the tracker. [tasks] that
address threat-model mitigations must name the mitigation ID(s) in their
acceptance criteria so QA and the penetration tester can trace coverage.

## Required review board

Deep Security requires Security on every bound package through
`REQUIRED_REVIEW_GATES=security`:

1. Principal Architect — `[architecture-approval]`
2. Sceptical Principal Architect — `[sceptical-architecture-approval]`
3. Senior Security Engineer — `[security-approval]`
4. Team Lead — `[team-lead-approval]`

The penetration tester additionally performs the authorized adversarial pass
(plain pass evidence or `[review-findings]`). Security approval must precede the
Team Lead's final approval. Integration requires the three core approvals,
Security, and any other declared gate before `[Ready to deploy]`.

## Launch

```bash
bin/launch-team.sh team deep-security <feature-branch> <featureId>
```
