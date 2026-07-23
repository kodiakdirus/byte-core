from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPOSITORY_ROOT / ".codex" / "config.toml"
HOOK = REPOSITORY_ROOT / ".codex" / "hooks" / "session_start.py"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "codex"


def _load_hook():
    spec = importlib.util.spec_from_file_location("byte_session_start", HOOK)
    if spec is None or spec.loader is None:
        raise RuntimeError("hook import unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodexIntegrationTests(unittest.TestCase):
    def test_project_config_has_one_bounded_session_hook(self) -> None:
        config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(config["features"], {"hooks": True})
        self.assertEqual(set(config["hooks"]), {"SessionStart"})
        entries = config["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "startup|resume|clear|compact")
        handler = entries[0]["hooks"][0]
        self.assertEqual(handler["type"], "command")
        self.assertEqual(handler["timeout"], 5)
        self.assertIn(".codex/hooks/session_start.py", handler["command"])
        serialized = CONFIG.read_text(encoding="utf-8")
        for forbidden in (
            "model =", "model_provider", "approval_policy",
            "sandbox_mode", "mcp_servers", "openai_base_url",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_valid_fixture_emits_only_generic_guidance(self) -> None:
        hook = _load_hook()
        fixture = (FIXTURES / "session-start.json").read_text(encoding="utf-8")
        output = io.StringIO()

        self.assertEqual(hook.run(io.StringIO(fixture), output), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["continue"], True)
        self.assertEqual(payload["systemMessage"], hook.GUIDANCE)
        self.assertNotIn("transcript", output.getvalue())
        self.assertNotIn("fictional-session", output.getvalue())
        self.assertNotIn("/fictional", output.getvalue())

    def test_unknown_malformed_and_oversized_inputs_degrade_safely(self) -> None:
        hook = _load_hook()
        inputs = (
            (FIXTURES / "future-event.json").read_text(encoding="utf-8"),
            "not json",
            "x" * (hook.MAX_INPUT_CHARACTERS + 1),
        )
        for value in inputs:
            with self.subTest(value=value[:20]):
                output = io.StringIO()
                self.assertEqual(hook.run(io.StringIO(value), output), 0)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["systemMessage"], hook.FALLBACK)
                self.assertTrue(payload["continue"])

    def test_hook_process_uses_stdio_and_writes_no_files(self) -> None:
        fixture = (FIXTURES / "session-start.json").read_text(encoding="utf-8")
        before = tuple(REPOSITORY_ROOT.rglob("*"))
        completed = subprocess.run(
            [sys.executable, str(HOOK)],
            input=fixture,
            text=True,
            capture_output=True,
            check=False,
            cwd=REPOSITORY_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        after = tuple(REPOSITORY_ROOT.rglob("*"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, after)
        self.assertEqual(json.loads(completed.stdout)["continue"], True)

    def test_agents_guidance_names_public_safety_and_validation(self) -> None:
        guidance = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("public repository", guidance)
        self.assertIn("python3 -m unittest discover -s tests", guidance)
        self.assertIn("Do not parse transcripts", guidance)
        self.assertIn("deployment-owned", guidance.lower())


if __name__ == "__main__":
    unittest.main()
