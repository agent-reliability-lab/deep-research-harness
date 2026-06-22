"""Validate benchmark tasks and generate a deduplicated source-freeze plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .load import load_task
from .models import BenchmarkTask


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "freeze-plan"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("tasks", nargs="+", type=Path)
    return parser


def build_freeze_plan(tasks: list[BenchmarkTask]) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for requirement in task.source_requirements:
            payload = {
                "source_id": requirement.source_id,
                "source_type": requirement.source_type,
                "canonical_url": str(requirement.canonical_url),
                "url_checked_at": requirement.url_checked_at.isoformat(),
            }
            current = sources.get(requirement.source_id)
            if current is not None:
                for field in (
                    "source_type",
                    "canonical_url",
                    "url_checked_at",
                ):
                    if current[field] != payload[field]:
                        raise ValueError(
                            f"conflicting {field} for {requirement.source_id}"
                        )
            else:
                current = {
                    **payload,
                    "required_topics": set(),
                    "referenced_by_tasks": set(),
                    "selection_rationales": set(),
                }
                sources[requirement.source_id] = current
            current["required_topics"].update(requirement.required_topics)
            current["referenced_by_tasks"].add(task.task_id)
            current["selection_rationales"].add(
                requirement.selection_rationale
            )

    serialized_sources = []
    for source_id in sorted(sources):
        source = sources[source_id]
        serialized_sources.append(
            {
                "source_id": source["source_id"],
                "source_type": source["source_type"],
                "canonical_url": source["canonical_url"],
                "url_checked_at": source["url_checked_at"],
                "required_topics": sorted(source["required_topics"]),
                "referenced_by_tasks": sorted(source["referenced_by_tasks"]),
                "selection_rationales": sorted(
                    source["selection_rationales"]
                ),
            }
        )
    return {
        "task_count": len(tasks),
        "source_count": len(serialized_sources),
        "all_tasks_frozen": all(task.lifecycle.value == "frozen" for task in tasks),
        "tasks": [
            {
                "task_id": task.task_id,
                "task_version": task.task_version,
                "lifecycle": task.lifecycle,
                "required_claims": len(task.required_claims),
            }
            for task in tasks
        ],
        "sources": serialized_sources,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tasks = [load_task(path) for path in args.tasks]
    if args.command == "validate":
        payload = {
            "valid": True,
            "tasks": [
                {
                    "path": str(path),
                    "task_id": task.task_id,
                    "lifecycle": task.lifecycle,
                    "claims": len(task.required_claims),
                    "sources": len(task.source_requirements),
                }
                for path, task in zip(args.tasks, tasks, strict=True)
            ],
        }
    else:
        payload = build_freeze_plan(tasks)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
