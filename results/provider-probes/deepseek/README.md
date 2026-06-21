# DeepSeek provider qualification

- Model: `deepseek-v4-flash`
- Provider: DeepSeek official API
- Run date: June 21, 2026
- Probe code revision: `fc2569de5d91ae3819c54beace248d32b1798907`

## Current status

| Gate | Result | Evidence |
|---|---|---|
| G1 — Identity | PASS | Model appeared in the provider model list; two calls returned `deepseek-v4-flash` with one stable system fingerprint. |
| G2 — Cache accounting | PASS | Cache hits increased from 0 to 2,560 tokens after warm-up; hit plus miss reconciled to prompt tokens on all three calls. |
| G3 — Tool fidelity | PASS | Five tool stages completed with valid parallel calls, unique IDs, schema-valid arguments, and preserved source-to-evidence lineage. |
| G4 — Stability | PENDING | Required before any eval-valid baseline run. |

Passing artifacts:

- [`g1_identity_20260621T123904.990600Z.json`](g1_identity_20260621T123904.990600Z.json)
- [`g2_cache_20260621T123907.243922Z.json`](g2_cache_20260621T123907.243922Z.json)
- [`g3_tools_20260621T123917.032116Z.json`](g3_tools_20260621T123917.032116Z.json)

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

## Interpretation

G1–G3 passing clears unscored C0 implementation. It does not authorize
eval-valid runs. The 20-request G4 stability soak remains a hard gate.
