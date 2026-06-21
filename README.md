# Agent Reliability Lab

**Open experiments on how AI agents remember, recover, use tools, and earn trust.**

Agent Reliability Lab is a portfolio of reproducible evaluations focused on the infrastructure around an AI agent—not just the model at its center.

The lab studies a practical question:

> What makes an agent remain useful when tasks become long, tools become risky, context becomes crowded, and execution gets interrupted?

## Research thesis

Longer context windows expand what an agent can access. They do not determine:

- what should enter the active working set;
- what should be persisted or forgotten;
- when an action requires permission;
- how sub-agents should hand work back;
- whether a task can recover after interruption;
- how reliability should be measured.

The lab treats those choices as testable system and product decisions.

## Project map

| Project | Layer | Question | Status |
|---|---|---|---|
| [Chinese Long-Context LLM Benchmark V2](https://github.com/agent-reliability-lab/llm-long-context-eval-zh-V2) | Model measurement | How reliably do Chinese LLMs retrieve and reason across longer contexts? | Complete, frozen at v2.0.1 |
| Deep Research Harness Eval | Agent reliability infrastructure | How do compaction, permission gates, sub-agents, and recovery change quality and cost? | In progress |
| Agent Memory Systems Benchmark | Persistent memory | How do Mem0, Letta, Zep, and Cognee differ under controlled memory tasks? | Planned after Harness Eval |

```text
                       Agent Reliability Lab
                                  |
             +--------------------+--------------------+
             |                    |                    |
     Long-context V2      Deep Research Harness     Memory Benchmark
     model measurement     system reliability       persistent memory
        COMPLETE              IN PROGRESS               PLANNED
```

## Current focus: Deep Research Harness Eval

The current project builds one inspectable deep-research agent and runs a controlled four-configuration ablation:

1. ReAct baseline
2. Baseline + context compaction
3. Compaction + permission gate
4. Permission gate + structured sub-agents

The public demo asks:

> Compare the agent memory architectures of Mem0, Letta, Zep, and Cognee.

The evaluation uses twenty narrower tasks and a frozen source snapshot so results can be reproduced.

Primary metrics:

- **Evidence-Grounded Task Success Rate**
- **Cost per Successful Task**

Supporting metrics cover citation quality, required-claim coverage, peak active context, and interruption recovery.

Read the [project specification](spec.md).

## Completed foundation: Long-Context V2

The first lab project established the measurement layer:

- 1,050 Chinese NIAH calls;
- 96 multi-hop results;
- three models;
- harder numeric, stylistic, and multi-key distractors;
- confidence intervals and efficiency metrics;
- explicit separation of model failures, content filters, and infrastructure failures;
- a published badcase taxonomy.

Its central engineering lesson is simple:

> Persist failure metadata. Otherwise billing errors, safety filters, and timeouts are easily misreported as model intelligence failures.

Repository: [llm-long-context-eval-zh-V2](https://github.com/agent-reliability-lab/llm-long-context-eval-zh-V2)

## Evaluation principles

Every lab project follows the same rules:

1. **Freeze inputs.** Live systems may be used for collection, but evaluation inputs must be versioned.
2. **Separate capability from infrastructure.** API failures and policy blocks are not silently scored as reasoning failures.
3. **Measure systems, not screenshots.** Every headline result must be reproducible from raw traces.
4. **Publish badcases.** Failure categories are part of the product, not an appendix.
5. **Prefer controlled comparisons.** Change one architectural decision at a time whenever possible.
6. **Report quality and cost together.** A more capable configuration may still be a worse product choice.
7. **State limitations.** Small benchmarks are evidence, not universal truth.

## Planned artifacts

For the Deep Research Harness Eval:

- open-source implementation;
- frozen task and source dataset;
- four-configuration ablation results;
- trace viewer or inspectable run artifacts;
- interruption-and-recovery test;
- three-minute demo;
- Chinese and English technical write-ups;
- one-page product retrospective.

## Roadmap

### Now

- Freeze the harness specification.
- Build and measure the ReAct baseline.
- Complete the 20-task × 4-configuration evaluation.

### Next

- Reuse the harness to compare persistent-memory systems.
- Add multi-session memory write, update, conflict, and deletion tasks.

### Later

- Extend the reliability suite to GUI/computer-use agents.
- Add adversarial tool-use and prompt-injection evaluations.

## About

Built by [Melody Ling](https://github.com/melody-ling-L), an AI product builder focused on agent systems, context engineering, evaluation, and memory.

The work is designed to be useful in three ways:

- as reproducible engineering evidence;
- as a product-decision case study;
- as an open learning resource for people building reliable agents.
