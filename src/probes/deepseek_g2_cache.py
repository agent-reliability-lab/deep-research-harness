"""G2 - Cache accounting (DeepSeek automatic context cache).

DeepSeek caches identical prompt prefixes automatically; there is no
``cache_control`` field (that is the Claude/AiHubMix mechanism, probed
separately). Cache construction is asynchronous and best-effort, so we send a
unique long prefix three times with settle intervals. The final call must report
more ``prompt_cache_hit_tokens`` than the warm-up call, and hit + miss must
reconcile to ``prompt_tokens`` on every call.

Ref: https://api-docs.deepseek.com/guides/kv_cache

Run standalone:
    python -m src.probes.deepseek_g2_cache
"""

from __future__ import annotations

import os
import time
import uuid

from .common import (
    DEEPSEEK_NON_THINKING,
    DEFAULT_DEEPSEEK_MODEL,
    ProbeResult,
    deepseek_client,
    deepseek_model_allowlist,
    print_result,
    usage_dict,
    write_artifact,
)

# A long, byte-identical prefix within one run. A run-specific marker appears
# first so old probe runs cannot create a false-positive cache hit.
STATIC_PREFIX_BODY = (
    "You are a meticulous research assistant for the Agent Reliability Lab. "
    "Follow these standing instructions on every task.\n"
) + "\n".join(
    f"Instruction {i}: keep every answer grounded in provided evidence and "
    f"cite source ids explicitly; never invent sources."
    for i in range(120)
)


def _cache_fields(usage: dict) -> tuple[int | None, int | None]:
    return usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")


def run(
    model: str | None = None,
    client=None,
    settle_seconds: float | None = None,
) -> ProbeResult:
    model = model or DEFAULT_DEEPSEEK_MODEL
    client = client or deepseek_client()
    result = ProbeResult(gate="g2_cache", provider="deepseek", requested_model=model)
    allowlist = deepseek_model_allowlist(model)
    probe_id = uuid.uuid4().hex
    static_prefix = f"CACHE_PROBE_ID={probe_id}\n{STATIC_PREFIX_BODY}"
    if settle_seconds is None:
        settle_seconds = float(os.environ.get("DEEPSEEK_CACHE_SETTLE_SECONDS", "3"))

    usages: list[dict] = []
    returned_models: list[str] = []
    fingerprints: list[str | None] = []
    request_ids: list[str | None] = []
    tails = [
        "Warm-up question: restate instruction 1 in one sentence.",
        "Verification question A: restate instruction 2 in one sentence.",
        "Verification question B: restate instruction 3 in one sentence.",
    ]
    for index, tail in enumerate(tails):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": static_prefix},
                {"role": "user", "content": tail},
            ],
            max_tokens=16,
            extra_body=DEEPSEEK_NON_THINKING,
        )
        usages.append(usage_dict(resp.usage))
        returned_models.append(resp.model)
        fingerprints.append(getattr(resp, "system_fingerprint", None))
        request_ids.append(getattr(resp, "_request_id", None))
        if index < len(tails) - 1 and settle_seconds > 0:
            time.sleep(settle_seconds)

    cache_pairs = [_cache_fields(usage) for usage in usages]
    result.evidence = {
        "probe_id": probe_id,
        "settle_seconds": settle_seconds,
        "calls": [
            {
                "usage": usage,
                "returned_model": returned_model,
                "system_fingerprint": fingerprint,
                "request_id": request_id,
            }
            for usage, returned_model, fingerprint, request_id in zip(
                usages,
                returned_models,
                fingerprints,
                request_ids,
                strict=True,
            )
        ],
    }

    fields_present = all(hit is not None and miss is not None for hit, miss in cache_pairs)
    result.add(
        "cache_fields_reported",
        fields_present,
        f"cache_pairs={cache_pairs}",
    )
    if fields_present:
        hits = [hit or 0 for hit, _ in cache_pairs]
        result.add(
            "final_call_shows_cache_hit",
            hits[-1] > 0,
            f"cache_hit_tokens={hits}",
        )
        result.add(
            "cache_hit_increases_after_warmup",
            hits[-1] > hits[0],
            f"first_hit={hits[0]} final_hit={hits[-1]}",
        )
        reconciliation = [
            usage.get("prompt_tokens") is not None and hit + miss == usage["prompt_tokens"]
            for usage, (hit, miss) in zip(usages, cache_pairs, strict=True)
        ]
        result.add(
            "hit_plus_miss_reconciles_prompt_tokens",
            all(reconciliation),
            f"per_call={reconciliation}",
        )
    result.add(
        "identity_stable_during_cache_probe",
        all(item in allowlist for item in returned_models) and len(set(returned_models)) == 1,
        f"returned={returned_models} allowlist={sorted(allowlist)}",
    )
    return result.finalize()


if __name__ == "__main__":
    r = run()
    art = write_artifact(r)
    print_result(r, art)
    raise SystemExit(0 if r.passed else 1)
