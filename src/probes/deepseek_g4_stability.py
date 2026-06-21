"""G4 - 20-request endpoint stability soak with no retries.

Passing criteria from the provider qualification gate:

- exactly 20 independent requests are attempted;
- the SDK retry budget is zero and the probe performs no manual retries;
- at least 95% of requests succeed without retry;
- every successful response returns an allowlisted model identity;
- every successful response includes a request ID and usage accounting;
- every successful response follows the deterministic ``ok`` contract.

Fingerprint changes are recorded but do not fail the gate because a legitimate
backend rollout can change a fingerprint without changing the controlled model
identity.

Run standalone:
    python -m src.probes.deepseek_g4_stability
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Callable
from statistics import median
from typing import Any
from uuid import uuid4

from .common import (
    DEEPSEEK_MAX_RETRIES,
    DEEPSEEK_NON_THINKING,
    DEEPSEEK_TIMEOUT_SECONDS,
    DEFAULT_DEEPSEEK_MODEL,
    ProbeResult,
    deepseek_client,
    deepseek_model_allowlist,
    exception_evidence,
    print_result,
    usage_dict,
    write_artifact,
)

G4_REQUEST_COUNT = 20
G4_MIN_SUCCESS_RATE = 0.95
G4_MAX_TOKENS = 8
DEFAULT_INTERVAL_SECONDS = float(
    os.environ.get("DEEPSEEK_G4_INTERVAL_SECONDS", "0.25")
)
SAFE_LIMIT_HEADERS = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _safe_limit_headers(value: Any) -> dict[str, str]:
    candidates = [
        value,
        getattr(value, "response", None),
        getattr(value, "http_response", None),
        getattr(value, "_response", None),
    ]
    for candidate in candidates:
        headers = getattr(candidate, "headers", None)
        if headers is None:
            continue
        observed = {
            header: str(headers[header])
            for header in SAFE_LIMIT_HEADERS
            if header in headers
        }
        if observed:
            return observed
    return {}


def _safe_error(exc: Exception) -> dict[str, Any]:
    evidence = exception_evidence(exc)
    limit_headers = _safe_limit_headers(exc)
    if limit_headers:
        evidence["rate_limit_headers"] = limit_headers
    return evidence


def run(
    model: str | None = None,
    client=None,
    *,
    request_count: int = G4_REQUEST_COUNT,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> ProbeResult:
    if request_count < 1:
        raise ValueError("request_count must be positive")
    if interval_seconds < 0:
        raise ValueError("interval_seconds must not be negative")

    model = model or DEFAULT_DEEPSEEK_MODEL
    client = client or deepseek_client()
    allowlist = deepseek_model_allowlist(model)
    result = ProbeResult(
        gate="g4_stability",
        provider="deepseek",
        requested_model=model,
    )
    probe_id = str(uuid4())
    calls: list[dict[str, Any]] = []
    started = clock()

    for index in range(request_count):
        request_started = clock()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Reply with exactly the lowercase word ok and nothing "
                            f"else. Stability probe {probe_id} request "
                            f"{index + 1}/{request_count}."
                        ),
                    }
                ],
                max_tokens=G4_MAX_TOKENS,
                extra_body=DEEPSEEK_NON_THINKING,
            )
            message = response.choices[0].message
            content = (message.content or "").strip()
            calls.append(
                {
                    "index": index + 1,
                    "success": True,
                    "requested_model": model,
                    "returned_model": response.model,
                    "identity_match": response.model in allowlist,
                    "system_fingerprint": getattr(
                        response,
                        "system_fingerprint",
                        None,
                    ),
                    "request_id": getattr(response, "_request_id", None),
                    "finish_reason": response.choices[0].finish_reason,
                    "content": content,
                    "output_contract_match": content.casefold() == "ok",
                    "usage": usage_dict(response.usage),
                    "rate_limit_headers": _safe_limit_headers(response),
                    "latency_ms": max(
                        0,
                        round((clock() - request_started) * 1000),
                    ),
                }
            )
        except Exception as exc:
            calls.append(
                {
                    "index": index + 1,
                    "success": False,
                    "requested_model": model,
                    "latency_ms": max(
                        0,
                        round((clock() - request_started) * 1000),
                    ),
                    "error": _safe_error(exc),
                }
            )
        if index + 1 < request_count and interval_seconds:
            sleep(interval_seconds)

    successes = [call for call in calls if call["success"]]
    failures = [call for call in calls if not call["success"]]
    success_rate = len(successes) / request_count
    identity_mismatches = [
        call for call in successes if not call["identity_match"]
    ]
    output_mismatches = [
        call for call in successes if not call["output_contract_match"]
    ]
    missing_request_ids = [
        call for call in successes if not call["request_id"]
    ]
    missing_usage = [
        call for call in successes if not call["usage"]
    ]
    latency_values = [call["latency_ms"] for call in calls]
    returned_models = Counter(
        call["returned_model"] for call in successes
    )
    fingerprints = Counter(
        call["system_fingerprint"] for call in successes
    )
    error_types = Counter(
        call["error"]["exception_type"] for call in failures
    )
    status_codes = Counter(
        str(call["error"].get("status_code")) for call in failures
        if call["error"].get("status_code") is not None
    )
    observed_limit_headers = [
        {
            "index": call["index"],
            "headers": call["rate_limit_headers"],
        }
        for call in successes
        if call["rate_limit_headers"]
    ]
    observed_limit_headers.extend(
        {
            "index": call["index"],
            "headers": call["error"]["rate_limit_headers"],
        }
        for call in failures
        if call["error"].get("rate_limit_headers")
    )

    result.evidence = {
        "probe_id": probe_id,
        "request_policy": {
            "request_count": request_count,
            "minimum_success_rate": G4_MIN_SUCCESS_RATE,
            "interval_seconds": interval_seconds,
            "sdk_max_retries": DEEPSEEK_MAX_RETRIES,
            "manual_retries": 0,
            "timeout_seconds": DEEPSEEK_TIMEOUT_SECONDS,
            "max_tokens_per_request": G4_MAX_TOKENS,
            "thinking": "disabled",
        },
        "summary": {
            "attempted": len(calls),
            "successful": len(successes),
            "failed": len(failures),
            "non_retried_success_rate": success_rate,
            "identity_mismatches": len(identity_mismatches),
            "output_contract_mismatches": len(output_mismatches),
            "missing_request_ids": len(missing_request_ids),
            "missing_usage": len(missing_usage),
            "returned_models": dict(returned_models),
            "system_fingerprints": {
                str(key): value for key, value in fingerprints.items()
            },
            "error_types": dict(error_types),
            "status_codes": dict(status_codes),
            "observed_rate_limit_headers": observed_limit_headers,
            "latency_ms": {
                "min": min(latency_values) if latency_values else None,
                "median": round(median(latency_values)) if latency_values else None,
                "p95": _percentile(latency_values, 0.95),
                "max": max(latency_values) if latency_values else None,
            },
            "duration_ms": max(0, round((clock() - started) * 1000)),
        },
        "calls": calls,
    }
    result.add(
        "exactly_twenty_requests_attempted",
        request_count == G4_REQUEST_COUNT and len(calls) == G4_REQUEST_COUNT,
        f"configured={request_count} attempted={len(calls)}",
    )
    result.add(
        "retry_budget_is_zero",
        DEEPSEEK_MAX_RETRIES == 0,
        (
            f"sdk_max_retries={DEEPSEEK_MAX_RETRIES} "
            "manual_retries=0"
        ),
    )
    result.add(
        "non_retried_success_rate_at_least_95_percent",
        success_rate >= G4_MIN_SUCCESS_RATE,
        (
            f"successful={len(successes)}/{request_count} "
            f"rate={success_rate:.3f}"
        ),
    )
    result.add(
        "zero_model_identity_mismatches",
        not identity_mismatches,
        (
            f"allowlist={sorted(allowlist)} mismatched_indices="
            f"{[call['index'] for call in identity_mismatches]}"
        ),
    )
    result.add(
        "deterministic_output_contract_preserved",
        not output_mismatches,
        f"mismatched_indices={[call['index'] for call in output_mismatches]}",
    )
    result.add(
        "request_ids_recorded_for_every_success",
        not missing_request_ids,
        f"missing_indices={[call['index'] for call in missing_request_ids]}",
    )
    result.add(
        "usage_recorded_for_every_success",
        not missing_usage,
        f"missing_indices={[call['index'] for call in missing_usage]}",
    )
    return result.finalize()


if __name__ == "__main__":
    probe = run()
    artifact = write_artifact(probe)
    print_result(probe, artifact)
    raise SystemExit(0 if probe.passed else 1)
