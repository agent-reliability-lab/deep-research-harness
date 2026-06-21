"""Canonical tool schemas shared by provider probes and the agent runtime."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_sources",
            "description": (
                "Search the frozen source corpus and return candidate source ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": "search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "max ids to return",
                    },
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
            "description": "Return the cleaned text of one frozen source by id.",
            "parameters": {
                "type": "object",
                "properties": {"source_id": {"type": "string", "minLength": 1}},
                "required": ["source_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_evidence",
            "description": (
                "Persist one grounded evidence record linking a claim to a source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "minLength": 1},
                    "claim": {"type": "string", "minLength": 1},
                    "excerpt": {"type": "string", "minLength": 1},
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
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
                    "claim_a": {"type": "string", "minLength": 1},
                    "claim_b": {"type": "string", "minLength": 1},
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
                "Finalize the answer with a one-line summary and supporting "
                "evidence ids."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1},
                    "evidence_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "format": "uuid",
                        },
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                },
                "required": ["summary", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
]
