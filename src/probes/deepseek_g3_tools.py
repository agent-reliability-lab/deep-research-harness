"""G3 - Tool fidelity across a deterministic five-stage tool sequence.

This is a provider protocol test, not a test of the model's planning quality.
Each tool type is forced in sequence so all five schemas cross the provider
boundary. DeepSeek supports parallel function calls, so a stage may return one
or more calls of the forced tool. Every call is appended with its exact id, a
canned result is returned using that id, and the next stage proves that the
conversation continued without adapter repair.

Run standalone:
    python -m src.probes.deepseek_g3_tools
"""

from __future__ import annotations

import json
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from src.tools.schemas import TOOL_SCHEMAS

from .common import (
    DEEPSEEK_NON_THINKING,
    DEFAULT_DEEPSEEK_MODEL,
    ProbeResult,
    deepseek_client,
    deepseek_model_allowlist,
    print_result,
    write_artifact,
)

TOOLS = TOOL_SCHEMAS

# Deterministic canned tool returns so the probe does not depend on live data.
SEARCH_RESULTS = ["src_mem0_docs", "src_mem0_paper"]

SOURCE_TEXTS = {
    "src_mem0_docs": "Mem0 stores memories in a vector store.",
    "src_mem0_paper": "Mem0 can add an optional graph layer.",
}


def _validate_args(tool_name: str, args: dict, tools_by_name: dict) -> list[str]:
    schema = tools_by_name[tool_name]["function"]["parameters"]
    return [
        f"{'.'.join(str(item) for item in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(args)
    ]


def _stub_result(tool_name: str, args: dict, evidence_index: int) -> tuple[dict, str | None]:
    """Return a deterministic tool result and an optional generated evidence id."""
    if tool_name == "search_sources":
        return {"source_ids": SEARCH_RESULTS}, None
    if tool_name == "read_source":
        source_id = args.get("source_id")
        return {"source_id": source_id, "text": SOURCE_TEXTS.get(source_id, "")}, None
    if tool_name == "record_evidence":
        evidence_id = str(UUID(int=evidence_index))
        return {"evidence_id": evidence_id}, evidence_id
    if tool_name == "check_contradiction":
        return {"contradiction": False}, None
    if tool_name == "finalize":
        return {"status": "ok"}, None
    return {"status": "unknown_tool"}, None


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
    observed_stages: list[list[str]] = []
    finish_reasons: list[str | None] = []
    parsed_args_by_tool: dict[str, list[dict]] = {}
    generated_evidence_ids: list[str] = []

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

        if not calls:
            arg_problems.append(
                f"turn{turn} {expected_name}: expected one or more tool calls, got 0"
            )
            break

        observed_stages.append([call.function.name for call in calls])
        call_ids.extend(call.id for call in calls)
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": item.id,
                        "type": "function",
                        "function": {
                            "name": item.function.name,
                            "arguments": item.function.arguments,
                        },
                    }
                    for item in calls
                ],
            }
        )

        for call in calls:
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
                parsed_args_by_tool.setdefault(name, []).append(args)
                arg_problems.extend(
                    f"turn{turn} {name}: {problem}"
                    for problem in _validate_args(name, args, tools_by_name)
                )
            tool_result, evidence_id = _stub_result(
                name,
                args,
                len(generated_evidence_ids) + 1,
            )
            if evidence_id:
                generated_evidence_ids.append(evidence_id)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(tool_result),
                }
            )

    # One final no-tool call verifies that the last tool result is accepted.
    final_content = None
    final_tool_calls = None
    stages_match = len(observed_stages) == len(expected_sequence) and all(
        stage and set(stage) == {expected}
        for stage, expected in zip(observed_stages, expected_sequence, strict=True)
    )
    if stages_match:
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
        "observed_stages": observed_stages,
        "tool_call_ids": call_ids,
        "parsed_args_by_tool": parsed_args_by_tool,
        "generated_evidence_ids": generated_evidence_ids,
        "returned_models": returned_models,
        "finish_reasons": finish_reasons,
        "final_content": final_content,
    }
    result.add(
        "all_five_tool_stages_called_in_order",
        stages_match,
        f"observed_stages={observed_stages}",
    )
    result.add(
        "all_tool_call_ids_present_and_unique",
        len(call_ids) >= len(expected_sequence)
        and all(bool(item) for item in call_ids)
        and len(set(call_ids)) == len(call_ids),
        f"tool_call_ids={call_ids}",
    )
    result.add(
        "arguments_schema_valid",
        len(arg_problems) == 0,
        "; ".join(arg_problems) or "all tool arguments parsed and matched declared schema",
    )
    read_ids = {args.get("source_id") for args in parsed_args_by_tool.get("read_source", [])}
    recorded_source_ids = {
        args.get("source_id") for args in parsed_args_by_tool.get("record_evidence", [])
    }
    final_calls = parsed_args_by_tool.get("finalize", [])
    final_evidence_ids = set(final_calls[0].get("evidence_ids", [])) if final_calls else set()
    result.add(
        "tool_state_preserved_across_turns",
        bool(read_ids)
        and read_ids.issubset(set(SEARCH_RESULTS))
        and bool(recorded_source_ids)
        and recorded_source_ids.issubset(read_ids)
        and bool(generated_evidence_ids)
        and set(generated_evidence_ids).issubset(final_evidence_ids),
        (
            f"read_source_ids={sorted(read_ids)} "
            f"recorded_source_ids={sorted(recorded_source_ids)} "
            f"generated_evidence_ids={generated_evidence_ids} "
            f"final_evidence_ids={sorted(final_evidence_ids)}"
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
        stages_match and bool(final_content) and not final_tool_calls,
        f"final_content_present={bool(final_content)} final_tool_calls={final_tool_calls}",
    )
    return result.finalize()


if __name__ == "__main__":
    r = run()
    art = write_artifact(r)
    print_result(r, art)
    raise SystemExit(0 if r.passed else 1)
