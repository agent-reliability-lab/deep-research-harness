"""G1 - Identity: the requested model is returned without silent substitution.

Spec: "Returned model identifier matches the allowlist on every probe; any
mismatch fails the provider." We also record ``system_fingerprint`` to detect
backend drift across otherwise-identical requests.

Run standalone:
    python -m src.probes.deepseek_g1_identity
"""

from __future__ import annotations

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


def run(model: str | None = None, client=None) -> ProbeResult:
    model = model or DEFAULT_DEEPSEEK_MODEL
    allowlist = deepseek_model_allowlist(model)

    client = client or deepseek_client()
    result = ProbeResult(gate="g1_identity", provider="deepseek", requested_model=model)

    listed_models = client.models.list()
    available_models = sorted(item.id for item in listed_models.data)
    returned_models: list[str] = []
    fingerprints: list[str | None] = []
    request_ids: list[str | None] = []
    last_usage: dict = {}
    for _ in range(2):
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=8,
            extra_body=DEEPSEEK_NON_THINKING,
        )
        returned_models.append(resp.model)
        fingerprints.append(getattr(resp, "system_fingerprint", None))
        request_ids.append(getattr(resp, "_request_id", None))
        last_usage = usage_dict(resp.usage)

    result.evidence = {
        "allowlist": sorted(allowlist),
        "available_models": available_models,
        "returned_models": returned_models,
        "system_fingerprints": fingerprints,
        "request_ids": request_ids,
        "usage_sample": last_usage,
    }
    result.add(
        "requested_model_listed",
        model in available_models,
        f"requested={model} available={available_models}",
    )
    result.add(
        "returned_model_in_allowlist",
        all(m in allowlist for m in returned_models),
        f"returned={returned_models} allowlist={sorted(allowlist)}",
    )
    result.add(
        "model_stable_across_calls",
        len(set(returned_models)) == 1,
        f"distinct_returned={sorted(set(returned_models))}",
    )
    result.add(
        "system_fingerprint_reported",
        all(bool(item) for item in fingerprints),
        f"system_fingerprints={fingerprints}",
    )
    return result.finalize()


if __name__ == "__main__":
    r = run()
    art = write_artifact(r)
    print_result(r, art)
    raise SystemExit(0 if r.passed else 1)
