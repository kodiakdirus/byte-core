from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.lifecycle import (  # noqa: E402
    MAX_PLAN_BYTES,
    LifecycleError,
    apply_initialization,
    build_initialization_plan,
    load_plan,
    serialize_plan,
    verify_initialization,
)


class LifecycleTests(unittest.TestCase):
    def test_plan_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            before = tuple(Path(temporary).iterdir())
            first = build_initialization_plan(root)
            second = build_initialization_plan(root)

            self.assertEqual(first, second)
            self.assertEqual(before, ())
            self.assertEqual(tuple(Path(temporary).iterdir()), ())

    def test_apply_and_verify_create_exact_document_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)

            applied = apply_initialization(plan)
            verified = verify_initialization(plan)

            self.assertEqual(applied.code, "initialized")
            self.assertEqual(verified.code, "verified")
            self.assertEqual(
                {path.name for path in root.iterdir()},
                {item.relative_path for item in plan.files},
            )

    def test_existing_target_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            root.mkdir()
            sentinel = root / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            plan = build_initialization_plan(root)

            with self.assertRaises(LifecycleError) as raised:
                apply_initialization(plan)

            self.assertEqual(raised.exception.code, "target_exists")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_exact_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)
            apply_initialization(plan)

            replayed = apply_initialization(plan)

            self.assertEqual(replayed.code, "already_initialized")

    def test_tampered_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan_path = Path(temporary) / "plan.json"
            payload = json.loads(serialize_plan(build_initialization_plan(root)))
            payload["files"][0]["sha256"] = "0" * 64
            plan_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(LifecycleError) as raised:
                load_plan(plan_path)

        self.assertEqual(raised.exception.code, "plan_integrity_failed")

    def test_oversized_and_symlinked_plans_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            oversized = Path(temporary) / "oversized.json"
            oversized.write_bytes(b"x" * (MAX_PLAN_BYTES + 1))
            with self.assertRaises(LifecycleError) as raised:
                load_plan(oversized)
            self.assertEqual(raised.exception.code, "plan_too_large")

            target = Path(temporary) / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = Path(temporary) / "plan.json"
            try:
                link.symlink_to(target.name)
            except (NotImplementedError, OSError):
                return
            with self.assertRaises(LifecycleError) as raised:
                load_plan(link)
            self.assertEqual(raised.exception.code, "plan_link_forbidden")

    def test_failed_apply_removes_only_its_own_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)
            real_open = __import__("os").open
            calls = 0

            def failing_open(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic failure")
                return real_open(*args, **kwargs)

            with mock.patch("byte_core.lifecycle.os.open", side_effect=failing_open):
                with self.assertRaises(LifecycleError) as raised:
                    apply_initialization(plan)

            self.assertEqual(raised.exception.code, "apply_failed")
            self.assertFalse(root.exists())

    def test_changed_created_file_requires_manual_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)
            real_open = __import__("os").open
            calls = 0

            def failing_open(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    (root / plan.files[0].relative_path).write_text(
                        "changed during apply", encoding="utf-8"
                    )
                    raise OSError("synthetic failure")
                return real_open(*args, **kwargs)

            with mock.patch("byte_core.lifecycle.os.open", side_effect=failing_open):
                with self.assertRaises(LifecycleError) as raised:
                    apply_initialization(plan)

            self.assertEqual(raised.exception.code, "recovery_required")
            self.assertTrue(root.exists())

    def test_modified_output_causes_verification_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)
            apply_initialization(plan)
            (root / "notebook.md").write_text("changed", encoding="utf-8")

            with self.assertRaises(LifecycleError) as raised:
                verify_initialization(plan)

        self.assertEqual(raised.exception.code, "verification_failed")


if __name__ == "__main__":
    unittest.main()
