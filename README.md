# Deep Research Harness Eval

**A controlled evaluation of the infrastructure that makes long-running research agents reliable.**

This project builds one inspectable deep-research agent, then changes one harness control at a time to measure the effect on answer quality, cost, safety, and recovery.

It is the active system-reliability project from [Agent Reliability Lab](https://github.com/agent-reliability-lab).

## Research question

> Which harness controls materially improve evidence-grounded task success without making successful runs disproportionately expensive?

Long context alone does not decide:

- what belongs in the active working set;
- what should be summarized, persisted, or discarded;
- which actions require permission;
- how sub-agents return evidence without polluting context;
- whether execution can recover after interruption.

This repository treats those choices as testable engineering decisions.

## Controlled configurations

| ID | Configuration | Added control |
|---|---|---|
| C0 | ReAct baseline | Single-agent tool loop with a full running transcript |
| C1 | C0 + compaction | Triggered summary and external evidence store |
| C2 | C1 + permission gate | Policy engine and simulated approval flow |
| C3 | C2 + sub-agents | Parallel research workers with structured handoffs |

The primary matrix contains twenty frozen tasks across all four configurations.

## Model and provider strategy

- **Primary ablation:** `deepseek-v4-flash` through the official DeepSeek API.
- **External-validity subset:** Claude Sonnet 4.6 through AiHubMix, only after the provider passes deterministic qualification gates.
- **Excluded:** providers that permit silent model substitution, because model identity is a controlled variable.

Primary and validation results are reported separately; they are never pooled into one headline metric.

See the [provider qualification gate](spec.md#provider-qualification-gate) and [cross-model validation design](spec.md#cross-model-external-validity-subset).

## Metrics

North-star metrics:

1. **Evidence-Grounded Task Success Rate**
2. **Cost per Successful Task**

Supporting metrics:

- citation precision;
- required-claim coverage;
- peak active-context tokens;
- interruption recovery success.

Infrastructure failures, source failures, expected policy blocks, and agent failures receive separate status labels.

## Reproducibility contract

- Freeze task inputs and source snapshots.
- Keep the primary model, provider, parameters, tools, and run budget fixed.
- Store model identity, token usage, cache usage, latency, cost, tool calls, and failure metadata in every trace.
- Permit final-report citations only when the cited evidence record exists in the trace.
- Publish badcases and limitations with headline results.

## Current status

**Spec v0.2 — implementation baseline**

- [x] Four-configuration ablation defined
- [x] Twenty-task evaluation design defined
- [x] Provider qualification and exclusion policy defined
- [x] DeepSeek G1–G3 probes executed and recorded
- [x] Trace and evidence contracts implemented
- [x] Frozen snapshot and canonical tool interface implemented
- [x] C0 ReAct loop implemented and validated against a synthetic fixture
- [x] DeepSeek G4 stability soak completed
- [x] Benchmark lifecycle, two frozen deterministic tasks, and two judge drafts defined
- [x] Deterministic benchmark scoring path isolated from judge-required tasks
- [x] First two-task development EGTSR reproduced from frozen official sources
- [x] C1 Form A compaction validated against an offline integration fixture
- [ ] C0 primary-provider run completed after G4
- [ ] 80-run primary matrix completed
- [ ] 32-run external-validity subset completed

Read the full [product and evaluation specification](spec.md).

## Run the C0 fixture

The committed fixture is synthetic, offline, and deliberately excluded from
the primary experiment. It proves the loop, budget enforcement, trace, evidence,
final report, and metric path in one command:

```bash
python -m src.agent.cli fixture --output "$(mktemp -d)/c0-fixture"
```

The command prints a fixture-only EGTSR and
`"eligible_for_primary_egtsr": false`. See
[C0 baseline usage and limits](docs/c0-baseline.md).

Two frozen deterministic tasks, two judge-required drafts, and their shared
three-source snapshot are documented in
[Benchmark task and rubric workflow](docs/task-rubric-design.md). Draft tasks
are schema-valid for review but the runner refuses to execute them until their
claims and source snapshot are frozen.

The first two-task C0 development run is published as a copyright-safe
[processed metrics summary](results/processed/c0-development-frozen-v1.json);
raw traces remain local because `read_source` events contain cache-only text.

The C1 trigger, reload-pointer strategy, checkpoint contract, and measurement
sequence are documented in [C1 compaction](docs/c1-compaction.md).

## Planned repository shape

```text
deep-research-harness/
├── README.md
├── spec.md
├── pyproject.toml
├── configs/
├── data/
│   ├── source_snapshots/
│   └── tasks/
├── src/
│   ├── agent/
│   ├── evidence/
│   ├── harness/
│   ├── snapshots/
│   ├── tasks/
│   ├── trace/
│   ├── tools/
│   └── evals/
├── schemas/
├── tests/
├── results/
│   ├── raw/
│   ├── processed/
│   └── figures/
└── docs/
```

## Status & findings

Updated 2026-06. Status is honest, not aspirational — the spec is the target;
this section is what is actually shipped.

### Done
- **Provider qualification gates G1–G4** — identity, cache accounting, tool
  fidelity, stability. All four real-PASS on `deepseek-v4-flash`; failed probes
  are preserved as evidence rather than retried.
- **Append-only trace + evidence contracts** — 12 event types, 7-field
  evidence records, lineage invariants enforced (no orphan citations,
  no unknown tool_call_ids, no sequence gaps).
- **Frozen-corpus tool bridge** — SHA-256 fail-closed integrity; full source
  text stays in a gitignored cache (no third-party redistribution); manifest
  is publicly verifiable.
- **C0 ReAct baseline** — budgeted (cache-aware: caps on uncached input,
  active-context per-call, cost), end-to-end traceable. First real C0 run on
  two deterministic frozen tasks: EGTSR 1.0, cost-per-success ≈ $0.00086,
  98% prompt-cache hit. The "1.0" is a two-task smoke result, not capability.
- **Real-run debugging surfaced four measurement bugs** that scripted fixtures
  could not (markdown cleaning, rubric false-negative, citation parsing,
  trace versioning). Fixing the ruler, not the agent, was the real work.
- **C1 compaction (Form A: tool-result clearing + reload pointers)** —
  offline integration fixture: compaction fires, 100% critical-fact retention.
  See [C1 compaction](docs/c1-compaction.md).
- **Anchor-based grounding** — `record_evidence` accepts short start/end
  anchors; the tool extracts the verbatim span from frozen source text with
  whitespace-flexible matching. Partial anchors are rejected at schema
  validation so they cannot silently fall back to the broken excerpt path.

### Headline finding (counter-intuitive, kept on purpose)

> **Context saved ≠ cost saved.** Form A compaction can shrink a single model
> call's active context while **raising** run cost, because rewriting earlier
> turns invalidates the prompt-cache prefix. Run-level peak can rebound via
> reload even when an isolated compaction ratio looks excellent. The naive
> "compaction = savings" framing is wrong under prefix caching; cost-per-success
> is the honest metric.

This is why this lab exists: in evaluation, the measurement bugs are usually
more interesting than the headline number.

### In progress
- **C1 Form B** (summarize + new window) — designed; not yet measured. The
  anchor interface above is the prerequisite that lets compaction not force
  a reload-and-rebound.
- **Clean C0-vs-C1 quality comparison** — unblocked now that grounding no
  longer fails on literal `\n` escapes.

### Deferred (explicit non-goals for this milestone)
- C2 permission gate and threat fixtures.
- C3 sub-agent delegation.
- LLM-as-judge and its own calibration gate.
- 80-run primary matrix and the Claude / AiHubMix external-validity subset.

These are spec'd; the next milestone gates them.

### What this repo can and cannot claim
**Can**: a reproducible, cache-aware, end-to-end harness with explicit
provider trust gates, evidence-grounded scoring, and an honest cost-vs-peak
finding under prefix caching.
**Cannot**: statistical generality, clean per-component causal attribution
(configs are cumulative), or cross-model/domain transfer. One run per cell;
single model; single domain.

## Related work

The preceding measurement-layer project is [Chinese Long-Context LLM Benchmark V2](https://github.com/agent-reliability-lab/llm-long-context-eval-zh-V2).

Built by [Melody Ling](https://github.com/melody-ling-L).
