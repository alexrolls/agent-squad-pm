# Team: Deep LLM

A cross-functional team for production LLM systems, applied data science,
retrieval-augmented generation, model and prompt experimentation, evaluation
science, inference services, and the product surfaces around them. Use this
preset when success depends on measured model behaviour rather than ordinary
deterministic application logic alone.

```
ROSTER=team-lead principal-llm-architect sceptical-principal-llm-architect senior-technical-product-manager senior-llm-engineer senior-staff-backend-engineer senior-llm-full-stack-engineer senior-llm-qa-engineer integrator
REQUIRED_REVIEW_GATES=null
REVIEW_MODE=parallel
PROTOCOL_TEAM_LEAD=team-lead
PROTOCOL_PRODUCT_MANAGER=senior-technical-product-manager
PROTOCOL_PRINCIPAL_ARCHITECT=principal-llm-architect
PROTOCOL_SCEPTICAL_ARCHITECT=sceptical-principal-llm-architect
PROTOCOL_SECURITY_REVIEWER=senior-llm-security-engineer
PROTOCOL_QA=senior-llm-qa-engineer
PROTOCOL_INTEGRATOR=integrator
PROTOCOL_LLM=senior-llm-engineer
PROTOCOL_BACKEND=senior-staff-backend-engineer
PROTOCOL_FRONTEND=senior-llm-full-stack-engineer
```

## Roster

| Role | Brief | Protocol mapping | Charter |
|---|---|---|---|
| Team Lead — **process and final quality gate** | `roles/team-lead.md` | `team-lead` | Runs the team and independently authors `[team-lead-approval]` |
| Principal LLM Architect | `teams/roles/principal-llm-architect.md` | `principal-architect` | Primary architecture position across models, data, evaluation, serving, and product integration |
| Sceptical Principal LLM Architect — **independent gate** | `teams/roles/sceptical-principal-llm-architect.md` | `sceptical-architect` | Blind-first falsification of model, data, evaluation, cost, and complexity claims |
| Senior LLM Security Engineer — **on-demand (not in startup roster)** | `teams/roles/senior-llm-security-engineer.md` | `security-reviewer` | Joins declared prompt-injection, data-exposure, tool-abuse, or model-supply-chain work |
| Senior Technical Product Manager | `teams/roles/senior-technical-product-manager.md` | no status writes | Scope, user outcome, acceptance criteria, and acceptable failure boundaries |
| Senior LLM Engineer | `teams/roles/senior-llm-engineer.md` | `llm` dispatch lane; backend implementer mechanics | Applied modeling, data science, prompts, retrieval, fine-tuning, tools, and experiment evidence |
| Senior Staff Backend Engineer | `teams/roles/senior-staff-backend-engineer.md` | `backend` | Inference gateway, data/RAG pipelines, persistence, reliability, observability, and cost controls |
| Senior Full Stack Engineer | `teams/roles/senior-llm-full-stack-engineer.md` | `frontend` | LLM product UX, streaming, citations, feedback, human handoff, and client/server seams |
| Senior QA Engineer | `teams/roles/senior-llm-qa-engineer.md` | `qa`; independent LLM verification specialist | Evaluation harnesses, statistical regression testing, deterministic contracts, and release evidence |
| integrator (standard) | `roles/integrator.md` | `integrator` | Mechanical merge gate; commit + `[Ready to deploy]` |

## Task routing

Every [task] must declare one explicit scheduler track:

- `track: llm` — model/prompt/retrieval experiments, data-science analysis,
  fine-tuning, tool-use policy, structured-output behaviour, or evaluation
  implementation owned by the Senior LLM Engineer.
- `track: backend` — inference APIs, ingestion/indexing pipelines, vector or
  relational persistence, queues, caching, rate limits, migrations,
  observability, and reliability owned by the Senior Staff Backend Engineer.
- `track: frontend` — user-facing application slices, streaming interaction,
  citations, feedback, human review, and accessibility owned by the Senior Full
  Stack Engineer.
- `track: qa` — independent test/evaluation infrastructure and verification
  work owned by the Senior QA Engineer.

Cross-track work is decomposed into contract-linked vertical slices. One [task]
has one accountable implementer; shared ownership is not a substitute for a
clear interface.

## LLM evidence contract

During Intake and Planning, classify every [task] as either:

1. **Research / decision task.** It must state the hypothesis, alternatives,
   bounded experiment, dataset or sample, artifact to produce, decision rule,
   time/cost budget, and what remains explicitly unproven. It may inform a later
   production design but cannot itself claim production quality.
2. **Production behaviour task.** Its acceptance criteria must define:
   - target behaviour, critical failure modes, and explicit non-goals;
   - the simplest meaningful baseline, including a deterministic or non-LLM
     baseline where feasible;
   - versioned tuning, validation, and frozen holdout data with provenance,
     contamination controls, and a stable digest;
   - metrics, critical slices, minimum practical improvement, release
     thresholds, and how stochastic variance will be measured;
   - safety/privacy criteria and forbidden outcomes;
   - latency, throughput, token, and monetary cost budgets;
   - exact model/provider/version, prompt/template version, retrieval/index
     version, tool schemas, and decoding parameters needed for reproducibility;
   - observability, fallback/degraded behaviour, rollout, and rollback.

No production-quality claim may rely only on demos, hand-picked examples,
aggregate averages, an uncalibrated LLM judge, or the implementer's own
evaluation run.

## Design gate — additional required fields

Every `[design-note]` in this preset must include:

1. `Task class: research|production`;
2. hypothesis and the baseline/alternatives considered;
3. affected model, prompt, retrieval, data, tool, API, and UI contracts;
4. data provenance, split discipline, sensitive-data handling, and
   contamination/leakage controls;
5. evaluation plan, metrics, critical slices, sample size/repetitions, and
   success/failure thresholds;
6. latency, throughput, token, and cost budgets;
7. failure handling, observability, fallback, rollout, and rollback;
8. `Architectural impact: yes/no — <why>`.

Both architects push back if any applicable field is absent or merely asserted
without evidence.

## Independent QA pass

The Senior QA Engineer performs an independent specialist verification pass on
every `track: llm` [task] and every [task] that changes prompts, model/provider
configuration, retrieval/reranking, evaluation data, tool schemas, citations,
or structured-output parsing. Each applicable [task] must declare
`review-gates: qa`; both architects push back when that metadata is missing. A
clean pass is `[review-approval]`; problems are `[review-findings]`. This
evidence does not replace any mandatory review-board approval. Even under
`REVIEW_MODE=parallel`, the dispatcher routes QA first, then accepts a newer
`[team-lead-approval]`; the integrator independently enforces both bindings.

## Required review board

Three core agents review every immutable package. The architects may run in
parallel with declared supporting gates; Team Lead runs after those gates are
current:

1. Principal Architect — `[architecture-approval]`
2. Sceptical Principal Architect — `[sceptical-architecture-approval]`
3. Team Lead — `[team-lead-approval]`

The Senior LLM Security Engineer is disabled at startup. Planning adds
`review-gates: security` for prompt/tool injection, cross-tenant or sensitive
data flow, privileged tool use, model/artifact supply chain, unsafe output
execution, or another credible threat. Either architect pushes back when such
risk exists but the gate is absent. The dispatcher launches the specialist on
demand, and `[security-approval]` must precede Team Lead. The integrator
requires the three core approvals, required QA where declared, and every other
declared supporting gate.

## Launch

```bash
bin/launch-team.sh team deep-llm <feature-branch> <featureId>
```
