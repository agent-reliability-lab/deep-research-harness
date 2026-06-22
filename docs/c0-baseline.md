# C0 baseline

C0 is a single-agent ReAct loop with a full running transcript. It adds no
compaction, permission gate, sub-agent, checkpoint, or recovery behavior.

## Offline integration fixture

Run the committed synthetic corpus and task:

```bash
python -m src.agent.cli fixture --output "$(mktemp -d)/c0-fixture"
```

The fixture reuses the two synthetic source statements from G3. It is committed
because it contains no third-party text. A successful run emits:

```text
run_started
  -> model_call / tool_execution
  -> evidence_recorded
  -> model_call / tool_execution
  -> final_report
  -> evaluation
  -> run_ended
```

Every model and tool call is present in `trace.jsonl`; evidence is stored in
`evidence.jsonl`; the report is under `artifacts/`.

The printed EGTSR is an integration-fixture result only. The CLI always reports
`evaluation_scope: fixture` and `eligible_for_primary_egtsr: false`. The metrics
aggregator rejects mixed scopes, so fixture traces cannot silently contaminate
primary results.

## Budget termination

C0 fails closed on:

- maximum iterations;
- model-call and tool-call counts;
- input and output tokens;
- total model cost;
- elapsed duration.

A provider/network failure is `infra_api_failed` and excluded from the EGTSR
denominator. Invalid model protocol, missing tool use, tool failure, and budget
exhaustion are `agent_failed` and remain in the denominator.

## Official DeepSeek development run

Live runs require a pricing file so cost accounting and the cost budget cannot
silently degrade to zero. Obtain current prices from the official provider and
record the retrieval date:

```json
{
  "pricing_version": "deepseek-official-YYYY-MM-DD",
  "uncached_input_usd_per_million": "<decimal>",
  "cache_hit_input_usd_per_million": "<decimal>",
  "output_usd_per_million": "<decimal>"
}
```

The committed `deepseek-v4-flash` pricing snapshot was retrieved from the
[official DeepSeek pricing page](https://api-docs.deepseek.com/quick_start/pricing)
on June 22, 2026. Re-check the provider page before a later scored run because
DeepSeek explicitly reserves the right to change prices.

Run a frozen deterministic development task:

```bash
python -m src.agent.cli deepseek \
  --task data/tasks/frozen/retrieval-ranking-01-letta-context-boundary.json \
  --manifest data/source_snapshots/manifest.json \
  --pricing-file configs/pricing/deepseek-v4-flash-2026-06-22.json \
  --output "results/local/c0-deepseek-$(date -u +%Y%m%dT%H%M%SZ)"
```

This run uses `evaluation_scope: development`. It is a real scored task but is
not eligible for the primary headline metric. Only a separately designated
primary run group may enter the primary denominator. `results/local/` is
gitignored because tool traces contain the full cache-only source text; publish
only derived metrics and appropriately short evidence excerpts.
