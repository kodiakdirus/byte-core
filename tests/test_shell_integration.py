from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
SHELL_SCRIPT = REPOSITORY_ROOT / "shell" / "byte-shell.sh"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.shell_integration import (  # noqa: E402
    END_MARKER,
    START_MARKER,
    ShellIntegrationError,
    apply_shell_plan,
    build_shell_install_plan,
    build_shell_removal_plan,
    load_shell_plan,
    serialize_shell_plan,
    verify_shell_plan,
)


class ShellIntegrationTests(unittest.TestCase):
    def test_bash_install_verify_replay_and_remove_preserve_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            profile = home / ".bashrc"
            original = b"# operator content\nexport FICTIONAL_VALUE=yes\n"
            profile.write_bytes(original)
            profile.chmod(0o640)
            install = build_shell_install_plan(home, "bash", SHELL_SCRIPT)

            applied = apply_shell_plan(install)

            self.assertEqual(applied.code, "integrated")
            self.assertEqual(verify_shell_plan(install).code, "verified")
            self.assertEqual(apply_shell_plan(install).code, "already_integrated")
            self.assertTrue(profile.read_bytes().startswith(original))
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o640)
            apply_backup = Path(applied.backup_path)
            self.assertEqual(apply_backup.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(apply_backup.stat().st_mode), 0o600)

            removal = build_shell_removal_plan(home, "bash")
            removed = apply_shell_plan(removal)

            self.assertEqual(removed.code, "removed")
            self.assertEqual(profile.read_bytes(), original)
            self.assertEqual(verify_shell_plan(removal).code, "verified")
            self.assertEqual(apply_shell_plan(removal).code, "already_removed")
            self.assertNotEqual(removed.backup_path, applied.backup_path)
            self.assertIn(START_MARKER, Path(removed.backup_path).read_text())

    def test_new_profiles_and_missing_trailing_newlines_restore_exactly(self) -> None:
        for original in (None, b"operator setting", b"operator setting\n"):
            with self.subTest(original=original):
                with tempfile.TemporaryDirectory() as temporary:
                    home = Path(temporary)
                    profile = home / ".bashrc"
                    if original is not None:
                        profile.write_bytes(original)
                    install = build_shell_install_plan(home, "bash", SHELL_SCRIPT)
                    apply_shell_plan(install)
                    removal = build_shell_removal_plan(home, "bash")
                    apply_shell_plan(removal)

                    if original is None:
                        self.assertFalse(profile.exists())
                    else:
                        self.assertEqual(profile.read_bytes(), original)

    def test_zsh_syntax_highlighting_is_explicit_and_optional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            syntax = home / "fictional-syntax-highlighting.zsh"
            syntax.write_text(
                "FICTIONAL_SYNTAX_HIGHLIGHTING=enabled\n", encoding="utf-8"
            )
            without = build_shell_install_plan(home, "zsh", SHELL_SCRIPT)
            self.assertIsNone(without.syntax_highlighting_path)

            with_syntax = build_shell_install_plan(
                home, "zsh", SHELL_SCRIPT, syntax
            )
            self.assertEqual(
                with_syntax.syntax_highlighting_path, str(syntax.resolve())
            )
            apply_shell_plan(with_syntax)
            content = (home / ".zshrc").read_text(encoding="utf-8")
            self.assertIn(str(syntax.resolve()), content)

            other_home = home / "other"
            other_home.mkdir()
            with self.assertRaisesRegex(
                ShellIntegrationError, "syntax_highlighting_requires_zsh"
            ):
                build_shell_install_plan(
                    other_home, "bash", SHELL_SCRIPT, syntax
                )

    def test_conflicting_malformed_and_changed_profiles_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            profile = home / ".bashrc"
            profile.write_text(START_MARKER + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ShellIntegrationError, "managed_block_conflict"
            ):
                build_shell_install_plan(home, "bash", SHELL_SCRIPT)
            with self.assertRaisesRegex(
                ShellIntegrationError, "malformed_managed_block"
            ):
                build_shell_removal_plan(home, "bash")

            profile.write_text("operator content\n", encoding="utf-8")
            plan = build_shell_install_plan(home, "bash", SHELL_SCRIPT)
            profile.write_text("changed after planning\n", encoding="utf-8")
            with self.assertRaisesRegex(ShellIntegrationError, "profile_changed"):
                apply_shell_plan(plan)
            self.assertFalse((home / ".byte-backups").exists())

    def test_plan_round_trip_and_tampering_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            plan = build_shell_install_plan(home, "bash", SHELL_SCRIPT)
            path = home / "plan.json"
            path.write_text(serialize_shell_plan(plan), encoding="utf-8")
            self.assertEqual(load_shell_plan(path), plan)

            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["profile_path"] = str(home / ".zshrc")
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ShellIntegrationError):
                load_shell_plan(path)

    def test_linked_profiles_and_ambiguous_failures_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            target = home / "target"
            target.write_text("outside profile\n", encoding="utf-8")
            profile = home / ".bashrc"
            try:
                profile.symlink_to(target)
            except (NotImplementedError, OSError):
                return
            with self.assertRaisesRegex(ShellIntegrationError, "invalid_profile"):
                build_shell_install_plan(home, "bash", SHELL_SCRIPT)
            self.assertEqual(target.read_text(encoding="utf-8"), "outside profile\n")

            profile.unlink()
            profile.write_text("operator content\n", encoding="utf-8")
            plan = build_shell_install_plan(home, "bash", SHELL_SCRIPT)

            def make_ambiguous(active_plan):
                profile.write_text("changed during verification\n", encoding="utf-8")
                raise ShellIntegrationError("profile_changed")

            with mock.patch(
                "byte_core.shell_integration._verify_result",
                side_effect=make_ambiguous,
            ):
                with self.assertRaisesRegex(
                    ShellIntegrationError, "recovery_required"
                ):
                    apply_shell_plan(plan)
            self.assertEqual(
                profile.read_text(encoding="utf-8"),
                "changed during verification\n",
            )
            self.assertTrue(Path(
                home / ".byte-backups"
                / f".bashrc.{plan.plan_id}.shell_install.bak"
            ).is_file())

    def test_shell_asset_is_posix_and_idempotent_when_sourced_twice(self) -> None:
        syntax = subprocess.run(
            ["sh", "-n", str(SHELL_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        command = (
            f". '{SHELL_SCRIPT}'; . '{SHELL_SCRIPT}'; "
            "byte_status; printf '%s\\n' \"$BYTE_CORE_SHELL_LOADED\""
        )
        completed = subprocess.run(
            ["bash", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout,
            "Byte shell integration is active.\n1\n",
        )

    @unittest.skipUnless(shutil.which("zsh"), "zsh is optional")
    def test_shell_asset_sources_twice_in_zsh(self) -> None:
        completed = subprocess.run(
            [
                "zsh", "-c",
                f". '{SHELL_SCRIPT}'; . '{SHELL_SCRIPT}'; byte_status",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout, "Byte shell integration is active.\n"
        )


if __name__ == "__main__":
    unittest.main()
