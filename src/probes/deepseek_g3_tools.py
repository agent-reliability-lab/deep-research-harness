"""G3 - Tool fidelity across a deterministic five-stage tool sequence.

This is a provider protocol test, not a test of the model's planning quality.
Each tool is forced once so all five schemas cross the provider boundary.
Every assistant tool call is appended with its exact id, the canned tool result
is returned using that id, and the next tool call proves that the conversation
continued without adapter repair.

Run standalone:
    python -m src.probes.deepseek_g3_tools
"""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from .common import (
    DEEPSEEK_NON_THINKING,
    DEFAULT_DEEPSEEK_MODEL,
    ProbeResult,
    deepseek_client,
    deepseek_model_allowlist,
    print_result,
    write_artifact,
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_sources",
            "description": "Search the frozen source corpus and return candidate source ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query"},
                    "max_results": {"type": "integer", "description": "max ids to return"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source",
            "description": "Return the cleaned text of one source by id.",
            "parameters": {
                "type": "object",
                "properties": {"source_id": {"type": "string"}},
                "required": ["source_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_evidence",
            "description": "Persist one evidence record linking a claim to a source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "claim": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
                "required": ["source_id", "claim"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_contradiction",
            "description": "Check whether two claims contradict each other.",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_a": {"type": "string"},
                    "claim_b": {"type": "string"},
                },
                "required": ["claim_a", "claim_b"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize",
            "description": (
                "Finalize the answer with a one-line summary and the supporting evidence ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
]

# Deterministic canned tool returns so the probe does not depend on live data.
STUBS = {
    "search_sources": {"source_ids": ["src_mem0_docs", "src_mem0_paper"]},
    "read_source": {"text": "Mem0 stores memories in a vector store with an optional graph layer."},
    "record_evidence": {"evidence_id": "ev_1"},
    "check_contradiction": {"contradiction": False},
    "finalize": {"status": "ok"},
}


def _validate_args(tool_name: str, args: dict, tools_by_name: dict) -> list[str]:
    schema = tools_by_name[tool_name]["function"]["parameters"]
    return [
        f"{'.'.join(str(item) for item in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(args)
    ]


def run(model: str | None = None, client=None) -> ProbeResult:
    model = model or DEFAULT_DEEPSEEK_MODEL
    client = client or deepseek_client()
    result = ProbeResult(gate="g3_tools", provider="deepseek", requested_model=model)
    allowlist = deepseek_model_allowlist(model)
    tools_by_name = {t["function"]["name"]: t for t in TOOLS}
    expected_sequence = [
        "search_sources",
        "read_source",
        "record_evidence",
        "check_contradiction",
        "finalize",
    ]

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a research agent. You MUST use the provided tools to "
                "gather and record information before answering. Do not answer "
                "from prior knowledge."
            ),
        },
        {
            "role": "user",
            "content": (
                "Exercise the research workflow using the tools. Follow the "
                "tool selected by the caller and use prior tool results when "
                "constructing each next call."
            ),
        },
    ]

    transcript: list[dict] = []
    returned_models: list[str] = []
    arg_problems: list[str] = []
    call_ids: list[str] = []
    observed_sequence: list[str] = []
    finish_reasons: list[str | None] = []
    parsed_args_by_tool: dict[str, dict] = {}

    for turn, expected_name in enumerate(expected_sequence):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice={
                "type": "function",
                "function": {"name": expected_name},
            },
            extra_body=DEEPSEEK_NON_THINKING,
            max_tokens=512,
        )
        returned_models.append(resp.model)
        finish_reasons.append(resp.choices[0].finish_reason)
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        transcript.append(
            {
                "turn": turn,
                "tool_calls": [
                    {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                    for c in calls
                ],
                "content": msg.content,
            }
        )

        if len(calls) != 1:
            arg_problems.append(
                f"turn{turn} {expected_name}: expected exactly 1 tool call, got {len(calls)}"
            )
            break

        call = calls[0]
        observed_sequence.append(call.function.name)
        call_ids.append(call.id)
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                ],
            }
        )

        name = call.function.name
        try:
            args = json.loads(call.function.arguments) if call.function.arguments else {}
            parsed = True
        except json.JSONDecodeError as exc:
            args, parsed = {}, False
            arg_problems.append(f"turn{turn} {name}: invalid JSON ({exc})")
        if name != expected_name:
            arg_problems.append(f"turn{turn}: expected tool '{expected_name}', got '{name}'")
        if name not in tools_by_name:
            arg_problems.append(f"turn{turn}: unknown tool '{name}'")
        elif parsed:
            parsed_args_by_tool[name] = args
            arg_problems.extend(
                f"turn{turn} {name}: {problem}"
                for problem in _validate_args(name, args, tools_by_name)
            )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(STUBS.get(name, {"status": "ok"})),
            }
        )

    # One final no-tool call verifies that the last tool result is accepted.
    final_content = None
    final_tool_calls = None
    if observed_sequence == expected_sequence:
        final_resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="none",
            extra_body=DEEPSEEK_NON_THINKING,
            max_tokens=64,
        )
        returned_models.append(final_resp.model)
        finish_reasons.append(final_resp.choices[0].finish_reason)
        final_message = final_resp.choices[0].message
        final_content = final_message.content
        final_tool_calls = final_message.tool_calls

    result.evidence = {
        "transcript": transcript,
        "expected_sequence": expected_sequence,
        "observed_sequence": observed_sequence,
        "tool_call_ids": call_ids,
        "parsed_args_by_tool": parsed_args_by_tool,
        "returned_models": returned_models,
        "finish_reasons": finish_reasons,
        "final_content": final_content,
    }
    result.add(
        "all_five_tools_called_in_order",
        observed_sequence == expected_sequence,
        f"observed={observed_sequence}",
    )
    result.add(
        "all_tool_call_ids_present_and_unique",
        len(call_ids) == len(expected_sequence)
        and all(bool(item) for item in call_ids)
        and len(set(call_ids)) == len(call_ids),
        f"tool_call_ids={call_ids}",
    )
    result.add(
        "arguments_schema_valid",
        len(arg_problems) == 0,
        "; ".join(arg_problems) or "all tool arguments parsed and matched declared schema",
    )
    search_ids = STUBS["search_sources"]["source_ids"]
    read_id = parsed_args_by_tool.get("read_source", {}).get("source_id")
    recorded_source_id = parsed_args_by_tool.get("record_evidence", {}).get("source_id")
    final_evidence_ids = parsed_args_by_tool.get("finalize", {}).get(
        "evidence_ids",
        [],
    )
    result.add(
        "tool_state_preserved_across_turns",
        read_id in search_ids
        and recorded_source_id == read_id
        and STUBS["record_evidence"]["evidence_id"] in final_evidence_ids,
        (
            f"read_source_id={read_id} recorded_source_id={recorded_source_id} "
            f"final_evidence_ids={final_evidence_ids}"
        ),
    )
    result.add(
        "identity_stable_across_tool_turns",
        bool(returned_models)
        and all(item in allowlist for item in returned_models)
        and len(set(returned_models)) == 1,
        f"returned={returned_models} allowlist={sorted(allowlist)}",
    )
    result.add(
        "tool_finish_reason_reported",
        len(finish_reasons) >= len(expected_sequence)
        and all(item == "tool_calls" for item in finish_reasons[: len(expected_sequence)]),
        f"finish_reasons={finish_reasons}",
    )
    result.add(
        "final_tool_result_accepted",
        observed_sequence == expected_sequence and bool(final_content) and not final_tool_calls,
        f"final_content_present={bool(final_content)} final_tool_calls={final_tool_calls}",
    )
    return result.finalize()


if __name__ == "__main__":
    r = run()
    art = write_artifact(r)
    print_result(r, art)
    raise SystemExit(0 if r.passed else 1)
