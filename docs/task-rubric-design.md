# Benchmark task and rubric workflow

The benchmark task schema has a fail-closed `draft` → `frozen` lifecycle.

- `draft`: source URLs and claims are design hypotheses. The task has no
  `source_snapshot_id`, claims remain `verification_status: draft`, and C0
  refuses to run it.
- `frozen`: the source snapshot exists, every required claim has been checked
  against the frozen text, claims are marked `verified`, and the exact
  `source_snapshot_id` is pinned.

This prevents a plausible-sounding task draft from becoming a scored run before
its evidence contract is real.

## Current task drafts

Four drafts are under `data/tasks/drafts/`:

1. `architecture-01-memory-placement`: judge-required comparison of
   active-context placement versus external memory.
2. `architecture-02-retrieval-control`: judge-required comparison of
   always-visible state versus on-demand retrieval.
3. `memory-lifecycle-01-mem0-add-only`: deterministic check of Mem0's
   documented ADD-only behavior and stated temporal-context benefit.
4. `retrieval-ranking-01-letta-context-boundary`: deterministic classification
   of Letta memory blocks and archival memory by visibility and retrieval
   boundary.

They deliberately share one minimal three-source freeze set:

| Source ID | Official page | Required role |
|---|---|---|
| `mem0-memory-evaluation` | [Mem0 memory evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation) | ADD-only extraction, temporal retention, stores, and retrieval signals |
| `letta-memory-blocks` | [Letta memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks) | persistent in-context state |
| `letta-archival-memory` | [Letta archival memory](https://docs.letta.com/guides/core-concepts/memory/archival-memory) | out-of-context, on-demand retrieval |

The URLs were checked on June 22, 2026. They are source hints, not frozen
evidence. The wording and patterns in all four tasks remain provisional.

## Evaluation modes

Each required claim declares its scoring method. The task contract rejects a
mode/scorer mismatch:

- `deterministic_fixture` and `deterministic_benchmark` require
  `pattern_contract`.
- `judge_required` requires `llm_judge`.

The deterministic evaluator is intentionally narrow. A claim passes only when
the final answer contains **every** declared answer pattern and at least one
cited evidence record from an allowed source contains **every** declared
evidence pattern. Citation precision checks whether cited records come from the
task's allowlist; citation entailment independently checks whether those
records satisfy a required claim's evidence contract. A valid-source citation
therefore cannot impersonate an entailing citation.

The two deterministic drafts are designed to produce the first reproducible
EGTSR without making an unvalidated LLM judge part of MVP-1. They still cannot
run while `draft`: their patterns must be checked against the frozen text and
their lifecycle must become `frozen` first. The two broader architecture tasks
remain `judge_required`; the deterministic evaluator refuses them rather than
substituting citation precision for entailment.

## Validate and generate the freeze checklist

```bash
python -m src.tasks.cli validate data/tasks/drafts/*.json
python -m src.tasks.cli freeze-plan data/tasks/drafts/*.json
```

`freeze-plan` deduplicates sources across tasks, merges the required topics, and
lists every task that depends on each source. It rejects conflicting URLs,
source types, or URL-check dates for the same source ID.

## Convert a draft into a runnable task

1. Fetch and clean each official page using the exact source ID from the plan.
2. Add it to the snapshot manifest and verify the SHA-256 cache hash.
3. Read the frozen text, then rewrite each `description`,
   `evidence_patterns`, and `answer_patterns` to match what the snapshot
   actually says.
4. Mark each checked claim `verification_status: verified`.
5. Set `lifecycle: frozen` and pin the exact `source_snapshot_id`.
6. Bump `task_version` for prompt/content changes and `rubric_version` for
   scoring changes.
7. Re-run task validation and snapshot verification before any model call.

The runner independently verifies that the task is frozen, the snapshot ID
matches, and all declared source IDs exist in the loaded corpus.
