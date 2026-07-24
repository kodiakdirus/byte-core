"""Advisory, privacy-minimal SessionStart hook for trusted Codex projects."""

from __future__ import annotations

import json
import sys
from typing import TextIO

MAX_INPUT_CHARACTERS = 64 * 1024
SUPPORTED_START_SOURCES = frozenset({"startup", "resume", "clear", "compact"})
GUIDANCE = (
    "Byte Core is public: preserve deployment-owned identity and truth, "
    "use exact reviewed targets, and verify before claiming success."
)
FALLBACK = (
    "Byte Core hook input was not recognized; repository AGENTS.md remains "
    "the authoritative safety baseline."
)


def run(input_stream: TextIO, output_stream: TextIO) -> int:
    try:
        payload = input_stream.read(MAX_INPUT_CHARACTERS + 1)
        if len(payload) > MAX_INPUT_CHARACTERS:
            raise ValueError
        raw = json.loads(payload)
        if (
            type(raw) is not dict
            or raw.get("hook_event_name") != "SessionStart"
            or raw.get("source") not in SUPPORTED_START_SOURCES
        ):
            raise ValueError
        message = GUIDANCE
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        message = FALLBACK
    output_stream.write(
        json.dumps(
            {"continue": True, "systemMessage": message},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.stdin, sys.stdout))
