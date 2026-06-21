"""Shared utilities for provider qualification probes.

Spec reference: "Provider qualification gate". These probes verify a provider
before it produces eval-valid runs:

    G1 Identity, G2 Cache accounting, G3 Tool fidelity, G4 Stability.

Design notes:
- Artifacts are written under ``results/provider-probes/<provider>/`` and are
  committed as evidence. Failed probes are preserved, not silently rerun.
- The script never holds the API key; it reads ``DEEPSEEK_API_KEY`` from the
  environment (or a local ``.env`` that is gitignored).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # optional: load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a convenience, not required
    pass

from openai import OpenAI

# Repo root resolved from src/probes/common.py -> repo/
REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO_ROOT / "results" / "provider-probes"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_NON_THINKING = {"thinking": {"type": "disabled"}}

# Model under qualification. Spec primary is ``deepseek-v4-flash``; the retired
# ``deepseek-chat`` alias is intentionally not used (spec: retired 2026-07-24).
# G1 records both the requested id and the returned id and fails on any
# mismatch, so an incorrect id here surfaces as an explicit G1 failure rather
# than as silent drift. Override with DEEPSEEK_MODEL when the console id differs.
DEFAULT_DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def utc_stamp() -> str:
    # Microseconds prevent a failed probe from being overwritten by a quick rerun.
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def deepseek_model_allowlist(model: str) -> set[str]:
    return {
        item.strip()
        for item in os.environ.get("DEEPSEEK_MODEL_ALLOWLIST", model).split(",")
        if item.strip()
    }


def deepseek_client() -> OpenAI:
    """Return an OpenAI-compatible client pointed at the DeepSeek endpoint."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit(
            "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and fill it "
            "in, or export DEEPSEEK_API_KEY=... before running."
        )
    return OpenAI(
        api_key=key,
        base_url=DEEPSEEK_BASE_URL,
        max_retries=0,
        timeout=120.0,
    )


def usage_dict(usage: Any) -> dict[str, Any]:
    """Usage object as a plain dict, including provider-specific extras such as
    ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``."""
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return dict(usage)
    return {k: getattr(usage, k) for k in dir(usage) if not k.startswith("_")}


@dataclass
class Assertion:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ProbeResult:
    gate: str
    provider: str
    requested_model: str
    passed: bool = False
    assertions: list[Assertion] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_stamp)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.assertions.append(Assertion(name, passed, detail))

    def finalize(self) -> ProbeResult:
        self.passed = len(self.assertions) > 0 and all(a.passed for a in self.assertions)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "passed": self.passed,
            "assertions": [asdict(a) for a in self.assertions],
            "evidence": self.evidence,
            "timestamp": self.timestamp,
        }


def write_artifact(result: ProbeResult) -> Path:
    out_dir = PROBE_DIR / result.provider
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.gate}_{result.timestamp}.json"
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def exception_evidence(exc: Exception) -> dict[str, Any]:
    """Return a safe, header-free summary suitable for a public artifact."""
    evidence: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "message": str(exc)[:1000],
    }
    for attr in ("status_code", "code", "request_id"):
        value = getattr(exc, attr, None)
        if value is not None:
            evidence[attr] = value
    return evidence


def print_result(result: ProbeResult, artifact: Path | None = None) -> None:
    mark = "PASS" if result.passed else "FAIL"
    print(f"\n[{mark}] {result.gate} — provider={result.provider} model={result.requested_model}")
    for a in result.assertions:
        amark = "ok" if a.passed else "XX"
        print(f"   [{amark}] {a.name}: {a.detail}")
    if artifact is not None:
        print(f"   artifact: {artifact.relative_to(REPO_ROOT)}")
