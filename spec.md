# Deep Research Harness Eval — Product & Evaluation Spec

> Status: v0.2 — implementation baseline
>
> Target: 14-day portfolio MVP
>
> Parent brand: Agent Reliability Lab
>
> Demo question: “Compare the agent memory architectures of Mem0, Letta, Zep, and Cognee.”

## Contents

- [Scope](#5-scope)
- [Research question](#6-research-question)
- [Experimental configurations](#7-experimental-configurations)
- [Provider qualification gate](#provider-qualification-gate)
- [Cross-model external-validity subset](#cross-model-external-validity-subset)
- [Metrics](#10-metrics)
- [Permission model](#11-permission-model)
- [Compaction design](#12-compaction-design)
- [Sub-agent handoff contract](#13-sub-agent-handoff-contract)
- [Interruption and recovery](#14-interruption-and-recovery-test)
- [Evaluation procedure](#15-evaluation-procedure)
- [MVP gates](#17-mvp-gates)
- [Risks and countermeasures](#20-risks-and-countermeasures)
- [Provider decision record](#22-provider-decision-record)

## 1. One-line definition

Build a small, inspectable deep-research agent and measure how context compaction, permission gates, and sub-agent delegation change its reliability, cost, and failure modes.

This is not a Claude Code clone. It is a controlled experiment about the reliability infrastructure around an agent.

## 2. Why this project exists

Long-context models can read more material, but a production agent still has to decide:

- what enters the working context;
- what gets summarized, persisted, or discarded;
- which actions require approval;
- when work should be delegated;
- what a sub-agent is allowed to return;
- how execution resumes after interruption.

The project turns those design decisions into measurable product trade-offs rather than a feature checklist.

## 3. Portfolio claim

After completing this project, the repository should support this claim:

> I designed and evaluated an agent harness across four controlled configurations, built a reproducible research-task benchmark, and quantified the quality, cost, safety, and recovery trade-offs of compaction, permission gates, and sub-agent delegation.

## 4. Target user and job-to-be-done

### Target user

An AI product or research team deciding which reliability controls should be added to a long-running research agent.

### Job-to-be-done

When a research task requires many sources and can exceed the model’s practical working context, the user wants a cited, complete, and recoverable report without hidden unsafe actions or uncontrolled token growth.

## 5. Scope

### In scope

- One deep-research workflow: plan, search, read, extract evidence, synthesize, and write a cited report.
- Four cumulative harness configurations.
- A frozen, versioned source snapshot for reproducible scoring.
- Twenty task cases derived from the memory-architecture research domain.
- Structured traces, checkpoints, and failure labels.
- Automated scoring plus a small, blinded human audit.
- `deepseek-v4-flash` through the official DeepSeek API for the primary ablation.
- Claude Sonnet 4.6 through AiHubMix for a separately reported external-validity subset, subject to provider qualification.
- CLI execution; a lightweight local viewer is optional only after evaluation works.

### Out of scope for the 14-day MVP

- Comparing Mem0, Letta, Zep, and Cognee as memory backends. That is the follow-on S1 project.
- A broad multi-model benchmark. The Claude subset tests external validity only.
- Production authentication or real destructive actions.
- General-purpose browser automation.
- A polished SaaS interface.
- More than one orchestration framework.
- Claiming statistical generality beyond this task suite.

## 6. Research question

Primary question:

> Which harness controls materially improve evidence-grounded task success without making successful runs disproportionately expensive?

Secondary questions:

1. Does compaction preserve decision-critical facts while reducing context growth?
2. Do permission gates prevent policy violations without causing excessive user interruptions?
3. Do sub-agents improve source coverage, and does their handoff format prevent context pollution?
4. Can a run resume from a checkpoint without losing its plan, evidence, or citation mapping?

## 7. Experimental configurations

The experiment is cumulative so each step answers a product decision.

| ID | Configuration | Added capability | Decision being tested |
|---|---|---|---|
| C0 | ReAct baseline | Single agent, tool loop, full running transcript | What fails without harness controls? |
| C1 | C0 + compaction | Triggered summary plus external evidence store | Can context growth be reduced without losing answer-critical evidence? |
| C2 | C1 + permission gate | Policy engine and simulated approval flow | Can unsafe or out-of-scope actions be blocked with tolerable friction? |
| C3 | C2 + sub-agents | Parallel research workers and structured handoffs | Does delegation improve coverage and reliability enough to justify overhead? |

### Required controls

- Same `deepseek-v4-flash` model identifier, official provider, and model parameters throughout the primary matrix.
- Same task inputs and frozen source corpus.
- Same tool schemas where a configuration does not explicitly change them.
- Same run budget.
- Deterministic seeds where supported.
- At least one repeated run per task/configuration; increase repeats only after the 80-run matrix completes.
- Persist the returned model identifier, provider, endpoint class, token usage, cache usage, and request ID in every trace.

## Provider qualification gate

A provider must pass all four gates before it can produce eval-valid runs. Probe artifacts are versioned under `results/provider-probes/`.

| Gate | Requirement | Passing evidence |
|---|---|---|
| G1 — Identity | The requested model is returned without silent substitution. | Returned model identifier matches the allowlist on every probe; any mismatch fails the provider. |
| G2 — Cache accounting | Repeated static prefixes produce observable cache hits and attributable token or cost accounting. | A unique static prefix is sent three times with settle intervals; the final request reports a larger cache hit than the warm-up request, and provider billing agrees with recorded usage within the documented tolerance. |
| G3 — Tool fidelity | Tool schemas, arguments, result IDs, and multi-turn tool state survive the provider boundary. | A deterministic multi-turn fixture forces five tool stages in sequence, permits valid parallel calls within a stage, validates every argument against its schema, round-trips exact tool-call IDs, preserves cross-turn data lineage, and accepts the final tool result with no adapter repair. |
| G4 — Stability | The endpoint can complete the planned matrix without unacceptable throttling or identity drift. | A 20-request soak test has zero identity mismatches and at least 95% non-retried success; limits and retry behavior are recorded. |

Gate policy:

- Failed probes are preserved; they are not deleted or rerun until they pass invisibly.
- A provider failure is `infra_api_failed`, not an agent-quality failure.
- A provider cannot be changed mid-matrix. If replacement is necessary, the affected matrix restarts under a new run-group ID.
- Pricing, model aliases, and provider documentation are frozen with a retrieval date before the first eval-valid run.

### Qualification status

| Role | Model | Provider | Status |
|---|---|---|---|
| Primary | `deepseek-v4-flash` | DeepSeek official API | G1–G4 passed June 21, 2026 |
| External validity | Claude Sonnet 4.6 | AiHubMix Anthropic-compatible endpoint | Pending G1–G4 probes |

The legacy alias `deepseek-chat` is not used in configuration files because DeepSeek has announced its retirement on July 24, 2026.

## Cross-model external-validity subset

The primary causal claim comes only from the fixed-model 80-run DeepSeek matrix.

After the primary matrix completes, run a stratified Claude subset:

```text
8 tasks × 4 configurations = 32 validation runs
```

The eight tasks contain:

- two architecture-comparison tasks;
- two lifecycle or retrieval tasks;
- two operational or recommendation tasks;
- two adversarial or recovery-injection tasks.

Rules:

- Use the same frozen sources, task rubrics, tool schemas, configuration flags, and run budgets.
- Treat provider-specific cache mechanisms as measured implementation details, not equivalent internals.
- Report Claude results beside the DeepSeek results, never pooled with them.
- Describe agreement or disagreement in configuration rankings; do not claim a general model comparison from 32 runs.
- If AiHubMix fails a qualification gate, omit the subset and report the failed gate instead of substituting another provider silently.

## 8. Reference workflow

```text
User query
  -> planner
  -> source discovery
  -> source reading
  -> evidence records
  -> optional compaction
  -> optional sub-agent handoffs
  -> synthesis
  -> citation verification
  -> final report
```

Each evidence record must contain:

- `claim`
- `source_id`
- `source_url`
- `retrieved_at`
- `evidence_excerpt`
- `source_date`
- `confidence`

The final report may cite only evidence records present in the trace.

## 9. Reproducible task suite

### Source policy

The live web is used to create a dated source snapshot, not as the only evaluation environment. Store:

- canonical URL;
- retrieval timestamp;
- content hash;
- cleaned text or an allowed excerpt;
- source type;
- version or publication date.

Use official documentation, technical papers, and official repositories as primary sources. Clearly label third-party analysis.

For sources that cannot be redistributed, commit only a public manifest with
metadata, a content hash, and a short excerpt. Keep the full cleaned text in a
gitignored local cache. A missing cache may be re-fetched only when the cleaned
content reproduces the committed hash; otherwise verification fails closed.
This makes corpus integrity verifiable without republishing third-party text,
but it is not a self-contained archival copy.

### Twenty tasks

Create five task families with four variants each:

1. Architecture comparison
2. Memory write/read/update lifecycle
3. Retrieval and ranking strategy
4. Deployment, observability, and operational trade-offs
5. Product recommendation under a stated use case

Each task receives:

- required claims;
- acceptable evidence sources;
- known distractors;
- required comparison dimensions;
- citation expectations;
- a scoring rubric.

The machine-readable task contract is exported as
`schemas/benchmark-task.schema.json`. Synthetic integration fixtures use the
`deterministic_fixture` evaluator and are never eligible for the primary
headline metric. Narrow real tasks whose full evidence contract can be
specified in advance use `deterministic_benchmark`; a claim passes only when
all answer concept groups and all evidence patterns match. Each answer group
may enumerate explicit acceptable phrasings, but the evaluator performs no
global stemming or punctuation normalization. Broader tasks use
`judge_required`, and the deterministic evaluator must reject them rather than
substitute citation precision for entailment. Every run records an
`evaluation_scope`, and metric aggregation rejects mixed scopes so fixture,
development, primary, and external-validity traces cannot be pooled
accidentally.

Citation precision and citation entailment are independent measurements. A
citation is precise when it points to a task-allowed source. It is entailing
only when the cited evidence satisfies a required claim's evidence contract or
an independently validated judge determines that it supports the claim.

Real tasks also use a fail-closed lifecycle. A `draft` task records source
requirements and provisional claims but cannot pin a snapshot or enter the
runner. A `frozen` task must pin the exact source snapshot and mark every
required claim verified against that frozen text. Task and rubric versions are
tracked independently so scoring changes do not masquerade as prompt changes.

The public demo uses the full comparison question. The benchmark uses narrower cases so failures can be attributed.

## 10. Metrics

### North-star metric 1: Evidence-Grounded Task Success Rate

A run succeeds only if all hard gates pass:

1. required claims coverage meets the task threshold;
2. factual correctness meets the rubric threshold;
3. citation precision and citation entailment meet the threshold;
4. no critical policy violation occurs;
5. the final artifact is produced within the run budget.

Report:

```text
EGTSR = successful runs / eval-valid runs
```

Do not silently score infrastructure failures as agent failures. Preserve the V2-style status split:

- `eval_valid`
- `agent_failed`
- `policy_blocked_expected`
- `infra_api_failed`
- `source_unavailable`
- `judge_required`

### North-star metric 2: Cost per Successful Task

```text
cost_per_success = total eval-valid inference and tool cost / successful runs
```

Also report tokens per successful task if provider pricing changes.

### Supporting metrics

1. **Citation precision:** supported citations / all citations.
2. **Required-claim coverage:** correctly supported required claims / required claims.
3. **Peak active-context tokens:** maximum tokens sent in any single model call.
4. **Recovery success rate:** interrupted runs that finish correctly after resuming / interrupted eval-valid runs.

### Diagnostic metrics

- end-to-end latency;
- total input/output tokens;
- compaction ratio;
- critical-fact retention after compaction;
- approval requests per task;
- false-positive and false-negative permission decisions;
- sub-agent handoff size;
- duplicated sources;
- tool-call errors;
- unsupported-claim count.

## 11. Permission model

Use simulated policies so the experiment is safe and reproducible.

| Action | Default policy |
|---|---|
| Read a frozen source | Allow |
| Search the frozen index | Allow |
| Write inside the run artifact directory | Allow and log |
| Access a non-allowlisted domain | Ask |
| Execute code or shell commands | Ask |
| Read credentials or environment secrets | Deny |
| Send, publish, purchase, delete, or modify external state | Deny |

The permission evaluator must distinguish:

- correct allow;
- correct ask;
- correct deny;
- over-block;
- under-block.

## 12. Compaction design

Trigger compaction at a configurable percentage of the per-call active-context
budget (`max_active_context_tokens`), not cumulative input tokens across model
calls. Cumulative uncached input, output, cost, call count, and duration remain
independent run-level guardrails.

The compacted state must preserve:

- current plan and completed steps;
- unresolved questions;
- evidence record IDs;
- source-to-claim mapping;
- user constraints;
- permission decisions;
- failures and retry state.

Raw source text stays outside the active context and can be reloaded by ID. Measure both compression ratio and critical-fact retention.

## 13. Sub-agent handoff contract

Sub-agents must not return their full transcript. They return:

```json
{
  "assigned_question": "...",
  "conclusion": "...",
  "evidence_ids": ["..."],
  "confidence": 0.0,
  "contradictions": ["..."],
  "open_questions": ["..."],
  "failed_actions": ["..."]
}
```

The parent agent validates evidence IDs before accepting a conclusion.

## 14. Interruption and recovery test

Inject interruption at three deterministic points:

1. after planning;
2. after half the evidence has been collected;
3. immediately after compaction or a sub-agent handoff.

A resumed run passes only if it:

- restores the plan and completed-step state;
- preserves collected evidence and citation IDs;
- does not repeat gated actions without reason;
- completes the report within the remaining budget.

## 15. Evaluation procedure

### Run matrix

```text
20 tasks × 4 configurations = 80 primary runs
8 stratified tasks × 4 configurations = 32 external-validity runs
```

Run the 80-case primary matrix before the 32-case external-validity subset or any repeats. If budget permits, repeat a stratified subset containing:

- four easy tasks;
- four adversarial tasks;
- all recovery-injection cases.

### Automated judges

Use deterministic checks wherever possible:

- schema validity;
- required-section presence;
- URL/source-ID validity;
- citation-to-evidence mapping;
- cost, latency, token, and policy logs.

Use an LLM judge only for rubric-scored factual coverage and entailment. Store judge prompts, model version, rationale, and raw output.

### Human audit

Blind-review at least eight runs:

- two per configuration;
- include both high- and low-scoring cases;
- adjudicate disagreement with the automated judge;
- publish the disagreement rate.

## 16. Badcase taxonomy

Every unsuccessful eval-valid run receives one primary failure label:

- planning omission;
- source discovery miss;
- retrieval miss;
- compaction information loss;
- distractor confusion;
- unsupported synthesis;
- citation mismatch;
- permission over-block;
- permission under-block;
- sub-agent handoff loss;
- duplicate or circular research;
- recovery-state loss;
- budget exhaustion;
- output-format failure.

## 17. MVP gates

### MVP-1 — Demonstrable baseline, Day 5

- The primary provider passes G1–G4 and probe artifacts are committed.
- C0 completes one end-to-end task.
- Five benchmark tasks are runnable.
- Every model/tool call is captured in a trace.
- The first EGTSR number is reproducible from a command.
- A demo branch or tag is created.

### MVP-2 — Comparable system, Day 9

- C0–C3 all run through one interface.
- Twenty tasks and the 80-run matrix complete or have explicit status labels.
- The 32-run external-validity subset completes or has an explicit provider-gate failure record.
- Both north-star metrics and four supporting metrics are generated.
- At least one interruption/recovery test passes.
- A result table and badcase taxonomy are produced.

### MVP-3 — Publishable portfolio, Day 14

- Reproduction instructions work from a clean environment.
- A three-minute demo is recorded.
- English and Chinese articles explain decisions and failures, not just features.
- A one-page PM retrospective is published.
- README includes architecture, results, limitations, and links to V2.

## 18. Fourteen-day execution plan

| Day | Deliverable |
|---|---|
| 1 | Freeze spec v0.2, provider gates, model allowlists, and trace schemas |
| 2 | Create repository skeleton, source snapshot format, five seed tasks |
| 3 | Run provider probes; implement tool interface, evidence store, and trace format |
| 4 | Implement C0 ReAct baseline |
| 5 | Run five tasks, calculate first EGTSR, tag MVP-1 |
| 6 | Implement compaction and critical-fact retention check |
| 7 | Implement permission policy and test fixtures |
| 8 | Implement sub-agent handoff contract and checkpointing |
| 9 | Run 20 × 4 primary matrix, generate results, tag MVP-2 |
| 10 | Run 8 × 4 external-validity subset; audit evaluation failures |
| 11 | Run recovery tests and blinded human sample |
| 12 | Record demo and create architecture/results visuals |
| 13 | Draft English and Chinese articles plus PM retrospective |
| 14 | Reproduce from clean environment, publish, tag MVP-3 |

## 19. Repository shape

```text
deep-research-harness/
├── README.md
├── spec.md
├── pyproject.toml
├── configs/
│   ├── baseline.yaml
│   ├── compaction.yaml
│   ├── permission.yaml
│   └── subagents.yaml
├── data/
│   ├── source_snapshots/
│   └── tasks/
├── src/
│   ├── agent/
│   ├── evidence/
│   ├── harness/
│   ├── snapshots/
│   ├── trace/
│   ├── tools/
│   └── evals/
├── schemas/
├── tests/
├── results/
│   ├── provider-probes/
│   ├── raw/
│   ├── processed/
│   └── figures/
└── docs/
    ├── demo.md
    └── pm-retrospective.md
```

## 20. Risks and countermeasures

| Risk | Countermeasure |
|---|---|
| Live sources change during evaluation | Freeze dated source snapshots and hashes |
| Model alias changes during the project | Pin the canonical model identifier and freeze returned identity in every trace |
| Provider silently substitutes a model | Enforce G1 on probes and every run; invalidate the run group on any mismatch |
| Cache accounting is unavailable or misleading | Enforce G2 and report raw token usage alongside provider-reported cost |
| Provider fails during the matrix | Do not mix providers; restart under a new run-group ID or publish the incomplete matrix |
| Scope expands into S1 memory comparison | Treat memory products as research subjects, not integrated backends |
| LLM judge becomes the entire evaluation | Prefer deterministic checks and publish human disagreement |
| Cumulative configs obscure component effects | Keep feature flags and run targeted rollback cases for surprising results |
| Permission gate looks artificial | Use explicit threat fixtures and report over-block/under-block |
| Eighty runs exceed budget | Complete one run per cell first; repeat only a stratified subset |
| Demo works but cannot be reproduced | Make clean-environment reproduction an MVP-3 gate |

## 21. Definition of done

The project is done when a reviewer can answer these questions from the repository alone:

1. What changed between C0, C1, C2, and C3?
2. Which change improved task success, and at what cost?
3. What failed, and how was the failure classified?
4. Can the results be reproduced from frozen inputs?
5. What product decision would the evidence support?

## 22. Provider decision record

### Selected paths

- **Primary:** `deepseek-v4-flash` through the official DeepSeek API. This keeps the 80-run causal comparison on one model and one first-party provider.
- **External validity:** Claude Sonnet 4.6 through AiHubMix, conditional on G1–G4. AiHubMix documents an Anthropic-shaped `/v1/messages` endpoint and Claude prompt-cache controls, but documentation is not treated as proof until the probes pass.

### Excluded providers

| Provider | Decision | Reason |
|---|---|---|
| 302.AI Claude Code endpoint | Excluded from eval-valid runs | Its documentation states that requests may be automatically switched to GLM or K2-family models during Claude risk-control events. Silent substitution violates the fixed-model control and G1 identity gate. |
| Any unqualified aggregator | Excluded until qualified | Low price or API compatibility does not establish model identity, cache accounting, tool fidelity, or matrix stability. |

### Decision-change protocol

Any provider or model change requires:

1. a new spec revision;
2. fresh G1–G4 probes;
3. a new run-group ID;
4. separate reporting from earlier results;
5. a public rationale in the repository history.

Reference documentation is frozen before evaluation:

- DeepSeek model lifecycle and canonical identifiers: <https://api-docs.deepseek.com/quick_start/pricing>
- AiHubMix Anthropic-compatible messages API: <https://docs.aihubmix.com/cn/api-reference/anthropic-compatible/create-a-message>
- AiHubMix Claude cache controls: <https://docs.aihubmix.com/en/api/Claude-Cache>
- 302.AI Claude Code routing disclosure: <https://doc.302.ai/393804750e0>
