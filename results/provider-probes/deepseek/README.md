# DeepSeek provider qualification

- Model: `deepseek-v4-flash`
- Provider: DeepSeek official API
- Run date: June 21, 2026
- Latest passing probe code revision: `f487dd0110e2b19d8e7a4676011151475d33100d`

## Current status

| Gate | Result | Evidence |
|---|---|---|
| G1 — Identity | PASS | Model appeared in the provider model list; two calls returned `deepseek-v4-flash` with one stable system fingerprint. |
| G2 — Cache accounting | PASS | Cache hits increased from 0 to 2,560 tokens after warm-up; hit plus miss reconciled to prompt tokens on all three calls. |
| G3 — Tool fidelity | PASS | Five tool stages completed with valid parallel calls, unique IDs, schema-valid arguments, and preserved source-to-evidence lineage. |
| G4 — Stability | PASS | Twenty independent requests completed with SDK/manual retries disabled: 20/20 success, zero identity or output drift, twenty unique completion IDs, complete usage accounting, and one stable recorded fingerprint. |

Passing artifacts:

- [`g1_identity_20260621T123904.990600Z.json`](g1_identity_20260621T123904.990600Z.json)
- [`g2_cache_20260621T123907.243922Z.json`](g2_cache_20260621T123907.243922Z.json)
- [`g3_tools_20260621T123917.032116Z.json`](g3_tools_20260621T123917.032116Z.json)
- [`g4_stability_20260621T152744.694577Z.json`](g4_stability_20260621T152744.694577Z.json)

## Preserved failed probe

The first G3 run failed because the fixture incorrectly required exactly one
tool call per stage. DeepSeek validly returned two parallel `read_source`
calls for the two source IDs produced by the preceding search.

The failure artifact is intentionally preserved:

- [`g3_tools_20260621T123405.138520Z.json`](g3_tools_20260621T123405.138520Z.json)

The fixture was revised to permit one or more calls of the forced tool within
each stage, return a result for every exact tool-call ID, and validate lineage
across parallel reads, evidence records, and final evidence IDs. The passing G3
artifact above was generated from the revised, clean code revision.

The first G4 run completed all twenty requests successfully but the probe looked
only for the SDK-specific `_request_id` field. DeepSeek returned the standard
completion `id` instead, so the gate correctly failed its traceability
assertion. The failure artifact is intentionally preserved:

- [`g4_stability_20260621T152506.046664Z.json`](g4_stability_20260621T152506.046664Z.json)

The probe was revised to record `_request_id` when present and otherwise use
`response.id`, matching the C0 provider adapter. The passing rerun recorded
twenty unique identifiers, all sourced from `response.id`.

## Interpretation

G1–G4 now pass for the official DeepSeek endpoint. This clears the provider
gate, but it does not by itself create a primary result: the real frozen corpus,
five benchmark tasks, versioned pricing, and scored C0 run are still required.
