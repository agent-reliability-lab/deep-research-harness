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
- [ ] DeepSeek G4 stability soak completed
- [ ] C0 baseline implemented
- [ ] 80-run primary matrix completed
- [ ] 32-run external-validity subset completed

Read the full [product and evaluation specification](spec.md).

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

## Related work

The preceding measurement-layer project is [Chinese Long-Context LLM Benchmark V2](https://github.com/agent-reliability-lab/llm-long-context-eval-zh-V2).

Built by [Melody Ling](https://github.com/melody-ling-L).
