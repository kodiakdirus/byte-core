"""Build one deterministic, unpacked Byte Core release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
FIXED_FILES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "bin/byte",
    "shell/byte-shell.sh",
)
SOURCE_DIRECTORIES = (
    ".codex",
    "docs",
    "src/byte_core",
    "templates/canonical",
)


class BuildError(Exception):
    pass


def build(version: str, output_root: str | os.PathLike[str]) -> Path:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise BuildError("invalid_version")
    output = _new_output_root(output_root)
    notes = REPOSITORY_ROOT / "docs" / "release-notes" / f"v{version}.md"
    if not notes.is_file() or notes.is_symlink():
        raise BuildError("release_notes_missing")

    sources = [(Path(item), REPOSITORY_ROOT / item) for item in FIXED_FILES]
    for directory_name in SOURCE_DIRECTORIES:
        directory = REPOSITORY_ROOT / directory_name
        sources.extend(
            (path.relative_to(REPOSITORY_ROOT), path)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix in {".md", ".py", ".toml"}
            and "__pycache__" not in path.parts
        )
    sources.append((Path("RELEASE_NOTES.md"), notes))

    output.mkdir(mode=0o700)
    managed: list[dict[str, object]] = []
    try:
        for relative, source in sources:
            _validate_source(relative, source)
            target = output / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = source.read_bytes()
            mode = 0o700 if relative.as_posix() == "bin/byte" else 0o600
            target.write_bytes(data)
            target.chmod(mode)
            managed.append(
                {
                    "relative_path": relative.as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mode": mode,
                }
            )
        managed.sort(key=lambda item: str(item["relative_path"]))
        artifact_sha256 = _digest_json(managed)
        unsigned = {
            "schema_version": 1,
            "core_version": version,
            "configuration_schema_minimum": 1,
            "configuration_schema_maximum": 1,
            "migration": "none",
            "release_notes_path": "RELEASE_NOTES.md",
            "files": managed,
            "artifact_sha256": artifact_sha256,
        }
        descriptor = {
            **unsigned,
            "descriptor_sha256": _digest_json(unsigned),
        }
        descriptor_path = output / "release.json"
        descriptor_path.write_text(
            _canonical_json(descriptor) + "\n", encoding="utf-8"
        )
        descriptor_path.chmod(0o600)
    except Exception:
        shutil.rmtree(output)
        raise
    return output


def _new_output_root(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise BuildError("invalid_output_root")
        parent = path.parent.resolve(strict=True)
        target = parent / path.name
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise BuildError("invalid_output_root") from error
    if target.exists() or target.is_symlink():
        raise BuildError("output_exists")
    return target


def _validate_source(relative: Path, source: Path) -> None:
    pure = PurePosixPath(relative.as_posix())
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or source.is_symlink()
        or not source.is_file()
        or source.resolve(strict=True) != source
    ):
        raise BuildError("invalid_source")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    active = parser.parse_args(arguments)
    try:
        output = build(active.version, active.output)
    except BuildError as error:
        parser.error(str(error))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
