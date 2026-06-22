# Trace and evidence contract

The harness uses two append-only JSONL files per run:

```text
results/raw/<run_id>/
├── trace.jsonl
└── evidence.jsonl
```

`trace.jsonl` records orchestration and measurement events. `evidence.jsonl`
stores source-grounded claims. An `evidence_recorded` trace event links each
evidence record into the run history.

## Why append-only JSONL

- a process interruption does not invalidate earlier events;
- sequence gaps and duplicate IDs are detectable;
- C3 sub-agent events can retain actor and parent relationships;
- checkpoints and recovery attempts remain auditable;
- raw events can be streamed without rewriting one large document.

## Trace invariants

- Event sequence starts at zero and is contiguous.
- Exactly one `run_started` event appears first.
- `run_started` pins task, rubric, source-snapshot, pricing, and model versions.
- All events have one `run_id`.
- Event IDs are unique; parent IDs must refer to earlier events.
- `run_ended`, when present, appears once and last.
- Final reports, checkpoints, and sub-agent handoffs may reference only
  evidence IDs already introduced by `evidence_recorded`.
- Every `evidence_recorded` ID must exist in the evidence store.

## Metric coverage

| Metric | Required event fields |
|---|---|
| Evidence-Grounded Task Success Rate | `evaluation.included_in_egtsr_denominator`, `evaluation.task_success` |
| Cost per Successful Task | model/tool call cost plus evaluation denominator and success |
| Citation precision and entailment | citation counts in `evaluation` |
| Required-claim coverage | required and supported claim counts in `evaluation` |
| Peak active context | `model_call.usage.input_tokens` |
| Recovery success | `recovery` state restoration, repeated gated actions, remaining budget |

Diagnostic fields support latency, total tokens, cache hits, compaction ratio,
critical-fact retention, permission errors, sub-agent handoff size, duplicated
sources, tool errors, and unsupported claims.

## Schema export

```bash
python -m src.trace.export_schemas
```

This produces:

- `schemas/trace-event.schema.json`
- `schemas/evidence-record.schema.json`

## Validation and metrics

```bash
python -m src.trace.cli validate results/raw/<run_id>/trace.jsonl \
  --evidence results/raw/<run_id>/evidence.jsonl

python -m src.trace.cli metrics results/raw/*/trace.jsonl
```

The metrics command emits run-level measurements plus aggregate EGTSR and cost
per successful task. It requires an `evidence.jsonl` beside every trace and
refuses runs that fail cross-event or evidence-link validation.
