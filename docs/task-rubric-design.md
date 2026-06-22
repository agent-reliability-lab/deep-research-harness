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

Two architecture-comparison drafts are under `data/tasks/drafts/`:

1. `architecture-01-memory-placement`: active-context placement versus external
   memory.
2. `architecture-02-retrieval-control`: always-visible state versus on-demand
   retrieval.

They deliberately share one minimal three-source freeze set:

| Source ID | Official page | Required role |
|---|---|---|
| `mem0-memory-evaluation` | [Mem0 memory evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation) | extraction, update decisions, stores, and retrieval signals |
| `letta-memory-blocks` | [Letta memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks) | persistent in-context state |
| `letta-archival-memory` | [Letta archival memory](https://docs.letta.com/guides/core-concepts/memory/archival-memory) | out-of-context, on-demand retrieval |

The URLs were checked on June 22, 2026. They are source hints, not frozen
evidence. The wording and patterns in both tasks remain provisional.

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
