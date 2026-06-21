"""Offline tests for complete provider-probe decision paths."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.probes.common import Assertion, ProbeResult, write_artifact
from src.probes.deepseek_g1_identity import run as run_g1
from src.probes.deepseek_g2_cache import run as run_g2
from src.probes.deepseek_g3_tools import TOOLS, _validate_args
from src.probes.deepseek_g3_tools import run as run_g3

MODEL = "deepseek-v4-flash"


def completion(
    *,
    model: str = MODEL,
    tool_name: str | None = None,
    arguments: dict | None = None,
    call_id: str = "call_1",
    usage: dict | None = None,
):
    tool_calls = None
    finish_reason = "stop"
    content = "done"
    if tool_name:
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name=tool_name,
                    arguments=json.dumps(arguments or {}),
                ),
            )
        ]
        finish_reason = "tool_calls"
        content = None
    return SimpleNamespace(
        model=model,
        system_fingerprint="fp_test",
        usage=usage or {"prompt_tokens": 10},
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                ),
            )
        ],
        _request_id=f"req_{call_id}",
    )


class FakeCompletions:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class FakeClient:
    def __init__(self, responses, models=(MODEL,)) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model) for model in models])
        )


class ProbeResultTests(TestCase):
    def test_mixed_assertions_fail(self) -> None:
        result = ProbeResult(
            gate="g0_test",
            provider="offline",
            requested_model="fake",
            assertions=[
                Assertion("one", True),
                Assertion("two", False),
            ],
        ).finalize()
        self.assertFalse(result.passed)

    def test_artifacts_do_not_overwrite_with_unique_timestamps(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result_a = ProbeResult(
                gate="g0_test",
                provider="offline",
                requested_model="fake",
            )
            result_b = ProbeResult(
                gate="g0_test",
                provider="offline",
                requested_model="fake",
            )
            with patch("src.probes.common.PROBE_DIR", Path(temp_dir)):
                path_a = write_artifact(result_a)
                path_b = write_artifact(result_b)
            self.assertNotEqual(path_a, path_b)
            self.assertTrue(path_a.exists())
            self.assertTrue(path_b.exists())


class GateDecisionTests(TestCase):
    def test_g1_passes_for_listed_stable_identity(self) -> None:
        client = FakeClient([completion(), completion(call_id="call_2")])
        result = run_g1(client=client)
        self.assertTrue(result.passed)
        self.assertEqual(len(client.chat.completions.calls), 2)

    def test_g1_fails_on_silent_model_substitution(self) -> None:
        client = FakeClient(
            [
                completion(model="other-model"),
                completion(model="other-model", call_id="call_2"),
            ]
        )
        result = run_g1(client=client)
        self.assertFalse(result.passed)

    def test_g2_passes_after_cache_warmup(self) -> None:
        usages = [
            {
                "prompt_tokens": 300,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 300,
            },
            {
                "prompt_tokens": 301,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 301,
            },
            {
                "prompt_tokens": 302,
                "prompt_cache_hit_tokens": 256,
                "prompt_cache_miss_tokens": 46,
            },
        ]
        client = FakeClient(
            [completion(usage=usage, call_id=f"call_{index}") for index, usage in enumerate(usages)]
        )
        result = run_g2(client=client, settle_seconds=0)
        self.assertTrue(result.passed)
        self.assertEqual(len(client.chat.completions.calls), 3)

    def test_g2_fails_when_final_call_has_no_cache_hit(self) -> None:
        usage = {
            "prompt_tokens": 300,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 300,
        }
        client = FakeClient(
            [completion(usage=usage, call_id=f"call_{index}") for index in range(3)]
        )
        result = run_g2(client=client, settle_seconds=0)
        self.assertFalse(result.passed)

    def test_g3_passes_complete_five_tool_roundtrip(self) -> None:
        tool_responses = [
            completion(
                tool_name="search_sources",
                arguments={"query": "Mem0 architecture", "max_results": 2},
                call_id="call_search",
            ),
            completion(
                tool_name="read_source",
                arguments={"source_id": "src_mem0_docs"},
                call_id="call_read",
            ),
            completion(
                tool_name="record_evidence",
                arguments={
                    "source_id": "src_mem0_docs",
                    "claim": "Mem0 uses vector storage.",
                    "excerpt": "Mem0 stores memories in a vector store.",
                },
                call_id="call_record",
            ),
            completion(
                tool_name="check_contradiction",
                arguments={
                    "claim_a": "Mem0 uses vector storage.",
                    "claim_b": "Mem0 may add a graph layer.",
                },
                call_id="call_check",
            ),
            completion(
                tool_name="finalize",
                arguments={
                    "summary": "Mem0 uses vector storage with an optional graph layer.",
                    "evidence_ids": ["ev_1"],
                },
                call_id="call_finalize",
            ),
            completion(call_id="call_final"),
        ]
        client = FakeClient(tool_responses)
        result = run_g3(client=client)
        self.assertTrue(result.passed)
        self.assertEqual(len(client.chat.completions.calls), 6)

    def test_g3_fails_when_cross_turn_state_is_lost(self) -> None:
        tool_responses = [
            completion(
                tool_name="search_sources",
                arguments={"query": "Mem0 architecture"},
                call_id="call_search",
            ),
            completion(
                tool_name="read_source",
                arguments={"source_id": "invented_source"},
                call_id="call_read",
            ),
            completion(
                tool_name="record_evidence",
                arguments={
                    "source_id": "invented_source",
                    "claim": "A claim.",
                },
                call_id="call_record",
            ),
            completion(
                tool_name="check_contradiction",
                arguments={"claim_a": "A", "claim_b": "B"},
                call_id="call_check",
            ),
            completion(
                tool_name="finalize",
                arguments={"summary": "done", "evidence_ids": []},
                call_id="call_finalize",
            ),
            completion(call_id="call_final"),
        ]
        result = run_g3(client=FakeClient(tool_responses))
        self.assertFalse(result.passed)


class ToolSchemaTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = {tool["function"]["name"]: tool for tool in TOOLS}

    def test_missing_required_is_rejected(self) -> None:
        problems = _validate_args("read_source", {}, self.tools)
        self.assertTrue(any("required" in problem for problem in problems))

    def test_bool_is_rejected_for_integer(self) -> None:
        problems = _validate_args(
            "search_sources",
            {"query": "memory", "max_results": True},
            self.tools,
        )
        self.assertTrue(any("integer" in problem for problem in problems))

    def test_unknown_property_is_rejected(self) -> None:
        problems = _validate_args(
            "read_source",
            {"source_id": "src_1", "repair_me": True},
            self.tools,
        )
        self.assertTrue(any("not allowed" in problem for problem in problems))

    def test_array_item_types_are_validated(self) -> None:
        problems = _validate_args(
            "finalize",
            {"summary": "done", "evidence_ids": [123]},
            self.tools,
        )
        self.assertTrue(any("string" in problem for problem in problems))
