"""Run DeepSeek provider gates G1 -> G2 -> G3 in order, stopping on first
failure.

Gate policy (spec "Provider qualification gate"):
- G1-G3 passing clears C0 development (no scored runs yet).
- G4 (20-request soak) must pass before any eval-valid baseline run.

Run:
    python -m src.probes.run_gates
"""

from __future__ import annotations

import sys

from . import deepseek_g1_identity, deepseek_g2_cache, deepseek_g3_tools
from .common import (
    DEFAULT_DEEPSEEK_MODEL,
    ProbeResult,
    exception_evidence,
    print_result,
    write_artifact,
)

GATES = [
    ("G1", deepseek_g1_identity),
    ("G2", deepseek_g2_cache),
    ("G3", deepseek_g3_tools),
]


def main() -> int:
    print(f"DeepSeek provider qualification — model={DEFAULT_DEEPSEEK_MODEL}")
    print("Running G1 -> G2 -> G3 (stop on first failure).")

    all_passed = True
    for label, module in GATES:
        try:
            result = module.run()
        except Exception as exc:  # preserve API/provider failures as evidence
            result = ProbeResult(
                gate=f"{label.lower()}_exception",
                provider="deepseek",
                requested_model=DEFAULT_DEEPSEEK_MODEL,
            )
            result.evidence = {"error": exception_evidence(exc)}
            result.add("probe_completed_without_exception", False, type(exc).__name__)
            result.finalize()
        artifact = write_artifact(result)
        print_result(result, artifact)
        if not result.passed:
            all_passed = False
            print(f"\n{label} failed. Stopping. Fix the provider before C0 development.")
            break

    if all_passed:
        print("\nG1-G3 PASS. Cleared to start C0 implementation (no scored runs).")
        print("Next: run the G4 20-request soak before any eval-valid baseline runs.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
