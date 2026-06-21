# Provider qualification probes

Implements the spec's **Provider qualification gate**. A provider must pass
**G1–G3 before C0 development**, and **G4 (stability soak) before any
eval-valid run**.

| Gate | File | What it checks |
|---|---|---|
| G1 Identity | `deepseek_g1_identity.py` | Returned `model` is in the allowlist on every call; identity stable; `system_fingerprint` recorded. |
| G2 Cache accounting | `deepseek_g2_cache.py` | DeepSeek **automatic** prefix cache: a unique prefix is sent three times with settle intervals; the final call reports a larger cache hit; hit + miss reconciles to `prompt_tokens`. |
| G3 Tool fidelity | `deepseek_g3_tools.py` | Deterministic five-stage fixture: every declared tool is forced once, JSON args pass full schema validation, exact `tool_call_id` values round-trip, and the final tool result is accepted. |
| G4 Stability | _(separate soak, run before eval-valid runs)_ | 20-request soak: zero identity mismatches, ≥95% non-retried success. |

> DeepSeek uses **automatic** caching (no `cache_control`), so G2 checks
> `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`. The Claude/AiHubMix
> external-validity provider uses **explicit** `cache_control` and gets a
> separate probe (`cache_creation_input_tokens` / `cache_read_input_tokens`).

## Run

```bash
# from the repo root: deep-research-harness/
pip install -r requirements.txt
cp .env.example .env          # then fill in DEEPSEEK_API_KEY + confirm DEEPSEEK_MODEL

# all three in order (stops on first failure):
python -m src.probes.run_gates

# or one gate at a time:
python -m src.probes.deepseek_g1_identity
python -m src.probes.deepseek_g2_cache
python -m src.probes.deepseek_g3_tools
```

Each run writes a JSON artifact to `results/provider-probes/deepseek/`. Per the
gate policy, **failed probes are committed too** — they are evidence, not noise.
Artifacts contain only canned probe prompts/results, model metadata, usage, and
safe exception summaries. They never contain API keys or request headers.

## Before you run

Confirm the exact callable model id in the DeepSeek console and set it in
`.env` as `DEEPSEEK_MODEL`. The spec target is `deepseek-v4-flash`; G1 records
both the requested and returned ids and **fails on any mismatch**, so a wrong
id surfaces as an explicit G1 failure rather than silent drift.
