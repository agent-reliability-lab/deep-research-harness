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

## Current task state

Four tasks share one three-source design:

1. `architecture-01-memory-placement`: draft, judge-required comparison of
   active-context placement versus external memory.
2. `architecture-02-retrieval-control`: draft, judge-required comparison of
   always-visible state versus on-demand retrieval.
3. `memory-lifecycle-01-mem0-add-only`: frozen deterministic check of
   Mem0's documented ADD-only behavior and stated temporal-context benefit.
4. `retrieval-ranking-01-letta-context-boundary`: frozen deterministic
   classification of Letta memory blocks and archival memory by visibility and
   retrieval boundary.

They deliberately share one minimal three-source freeze set:

| Source ID | Official page | Required role |
|---|---|---|
| `mem0-memory-evaluation` | [Mem0 memory evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation) | ADD-only extraction, temporal retention, stores, and retrieval signals |
| `letta-memory-blocks` | [Letta memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks) | persistent in-context state |
| `letta-archival-memory` | [Letta archival memory](https://docs.letta.com/guides/core-concepts/memory/archival-memory) | out-of-context, on-demand retrieval |

The official Markdown was retrieved on June 22, 2026. Full text lives only in
the gitignored cache. The public manifest pins SHA-256 hashes and short
excerpts. The deterministic tasks are verified against snapshot
`memory-systems-2026-06`; the broader judge-required tasks remain provisional.

## Evaluation modes

Each required claim declares its scoring method. The task contract rejects a
mode/scorer mismatch:

- `deterministic_fixture` and `deterministic_benchmark` require
  `pattern_contract`.
- `judge_required` requires `llm_judge`.

The deterministic evaluator is intentionally narrow. A claim passes only when
the final artifact satisfies **every** declared answer concept group and at
least one cited evidence record from an allowed source contains **every**
declared evidence pattern. Each answer group lists explicit acceptable
phrasings; one phrasing per group must match. This handles bounded variants such
as `not overwritten` versus `without overwriting` without global stemming or
punctuation normalization. Citation precision checks whether cited records come
from the task's allowlist; citation entailment independently checks whether
those records satisfy a required claim's evidence contract. A valid-source
citation therefore cannot impersonate an entailing citation.

The two deterministic tasks are designed to produce the first reproducible
EGTSR without making an unvalidated LLM judge part of MVP-1. They are now
frozen and runnable when the hash-locked local cache is present. The two
broader architecture tasks remain `judge_required`; the deterministic
evaluator refuses them rather than substituting citation precision for
entailment.

## Validate and generate the freeze checklist

```bash
python -m src.tasks.cli validate \
  data/tasks/drafts/*.json data/tasks/frozen/*.json
python -m src.tasks.cli freeze-plan \
  data/tasks/drafts/*.json data/tasks/frozen/*.json
```

`freeze-plan` deduplicates sources across tasks, merges the required topics, and
lists every task that depends on each source. It rejects conflicting URLs,
source types, or URL-check dates for the same source ID.

## Preflight evidence patterns against fetched text

Before freezing, fetch the official Markdown into the gitignored snapshot cache,
then audit every claim against the exact files that will be hashed:

```bash
curl -fsSL https://docs.mem0.ai/core-concepts/memory-evaluation.md \
  -o data/source_snapshots/cache/mem0-memory-evaluation.md
curl -fsSL https://docs.letta.com/guides/core-concepts/memory/memory-blocks.md \
  -o data/source_snapshots/cache/letta-memory-blocks.md
curl -fsSL https://docs.letta.com/guides/core-concepts/memory/archival-memory.md \
  -o data/source_snapshots/cache/letta-archival-memory.md

python -m src.tasks.cli preflight-patterns \
  data/tasks/drafts/*.json data/tasks/frozen/*.json \
  --source mem0-memory-evaluation=data/source_snapshots/cache/mem0-memory-evaluation.md \
  --source letta-memory-blocks=data/source_snapshots/cache/letta-memory-blocks.md \
  --source letta-archival-memory=data/source_snapshots/cache/letta-archival-memory.md
```

The preflight uses the same strict text rule as deterministic scoring:
case-folding only. It does not normalize hyphens, whitespace, or punctuation.
This is intentional: evidence patterns are exact source contracts, not fuzzy
search hints. A successful live preflight still does not freeze a task; the
snapshot hash and claim verification status remain separate gates.

## Convert a draft into a runnable task

1. Fetch and clean each official page using the exact source ID from the plan.
2. Add it to the snapshot manifest and verify the SHA-256 cache hash.
3. Read the frozen text, then rewrite each `description`,
   `evidence_patterns`, and `answer_pattern_groups` to match the intended
   source and answer contract.
4. Mark each checked claim `verification_status: verified`.
5. Set `lifecycle: frozen` and pin the exact `source_snapshot_id`.
6. Bump `task_version` for prompt/content changes and `rubric_version` for
   scoring changes.
7. Re-run task validation and snapshot verification before any model call.

The runner independently verifies that the task is frozen, the snapshot ID
matches, and all declared source IDs exist in the loaded corpus.
