# C1 compaction

C1 keeps the C0 tool loop and adds active-context compaction behind
`configs/compaction.yaml`.

Run the offline integration fixture:

```bash
python -m src.agent.cli fixture \
  --configuration C1 \
  --max-active-context-tokens 500 \
  --max-uncached-input-tokens 10000 \
  --output "$(mktemp -d)/c1-fixture"
```

The deliberately small active-context budget makes the synthetic fixture fire
the control. Real DeepSeek stressing runs keep the controlled 100,000-token
active-context budget, so the committed 0.60 trigger fires at 60,000.

## Trigger

The trigger is a fraction of `max_active_context_tokens`, not cumulative input
tokens. With the committed configuration:

```text
100,000 active-context budget × 0.60 = 60,000 tokens
```

After a successful model call reports input usage at or above the trigger, C1
compacts before the next model call. Cost, cumulative uncached input, output,
call count, and duration remain independent fail-closed run budgets.

## Form A: tool-result clearing

The first implementation removes old `cleaned_text` payloads from
`read_source` tool messages and replaces each with:

```json
{
  "compacted": true,
  "reload_with": "read_source",
  "source_id": "..."
}
```

The full frozen text remains in the cache-only snapshot and the full tool result
remains in the append-only trace. The model can reload a source by ID, so
context reduction does not destroy the audit record or the reconstruction
path.

## Measurement sequence

```text
model_call crosses trigger
  -> checkpoint (reloadable source IDs + evidence IDs)
  -> compact request messages
  -> next model_call
  -> compaction event
```

`CompactionEvent.input_tokens` is the provider-reported size that crossed the
trigger. `output_tokens` is the provider-reported input size of the first call
after compaction. The ratio therefore compares real usage values rather than a
character-count estimate.

Critical facts are the union of:

- evidence IDs persisted in the external evidence store;
- source IDs whose raw tool result was cleared but remains reloadable.

The event records required and preserved fact IDs, and the metrics layer reports
their intersection as critical-fact retention.

## Form B boundary

A later summarizer may populate `summary_model_call_id` and
`summary_cost_usd`. The summary call must appear as its own `ModelCallEvent`;
trace validation requires the mirrored cost in `CompactionEvent` to match
exactly. Metrics count the `ModelCallEvent` cost once and do not add the mirror
field again.
