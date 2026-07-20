"""Read-only layered configuration resolution for Byte Core.

This bootstrap interface is internal and is not a stable public API.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
_LAYER_PRECEDENCE = ("core", "homelab", "platform", "host")

_SCHEMA: dict[str, object] = {
    "deployment": {
        "name": str,
        "domain": str,
        "tags": [str],
    },
    "selection": {
        "platform": str,
        "host": str,
    },
    "paths": {
        "workspace": str,
    },
    "retention": {
        "cache_days": int,
    },
}


class ConfigurationError(Exception):
    """A sanitized configuration-resolution failure."""

    def __init__(
        self,
        code: str,
        *,
        layer: str | None = None,
        key: str | None = None,
    ) -> None:
        self.code = code
        self.layer = layer
        self.key = key

        message = code
        if layer is not None:
            message += f" in layer {layer!r}"
        if key is not None:
            message += f" at {key!r}"

        super().__init__(message)


@dataclass(frozen=True)
class Layer:
    """A named configuration layer."""

    name: str
    path: Path


@dataclass(frozen=True)
class ResolvedConfiguration:
    """Resolved values and their logical source layers."""

    schema_version: int
    values: dict[str, Any]
    sources: dict[str, str]

    def source_for(self, dotted_key: str) -> str:
        """Return the layer that supplied a resolved leaf value."""

        return self.sources[dotted_key]


def resolve_configuration(
    layers: Sequence[Layer],
) -> ResolvedConfiguration:
    """Read, validate, and resolve participating configuration layers."""

    ordered_layers = _order_layers(layers)
    if not any(layer.name == "core" for layer in ordered_layers):
        raise ConfigurationError("missing_core_layer")

    expected_version: int | None = None
    resolved: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for layer in ordered_layers:
        document = _read_layer(layer)
        schema_version = _take_schema_version(document, layer.name)

        if expected_version is None:
            if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                raise ConfigurationError(
                    "unsupported_schema_version",
                    layer=layer.name,
                    key="schema_version",
                )
            expected_version = schema_version
        elif schema_version != expected_version:
            raise ConfigurationError(
                "schema_version_mismatch",
                layer=layer.name,
                key="schema_version",
            )

        _validate_table(document, _SCHEMA, layer.name)
        _merge_table(resolved, document, sources, layer.name)

    if expected_version is None:
        raise ConfigurationError("missing_core_layer")

    return ResolvedConfiguration(
        schema_version=expected_version,
        values=resolved,
        sources=sources,
    )


def _order_layers(layers: Sequence[Layer]) -> list[Layer]:
    by_name: dict[str, Layer] = {}

    for layer in layers:
        if layer.name not in _LAYER_PRECEDENCE:
            raise ConfigurationError("invalid_layer", layer=layer.name)
        if layer.name in by_name:
            raise ConfigurationError("duplicate_layer", layer=layer.name)
        by_name[layer.name] = layer

    return [
        by_name[name]
        for name in _LAYER_PRECEDENCE
        if name in by_name
    ]


def _read_layer(layer: Layer) -> dict[str, Any]:
    try:
        with layer.path.open("rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(
            "invalid_toml",
            layer=layer.name,
        ) from error
    except OSError as error:
        raise ConfigurationError(
            "read_error",
            layer=layer.name,
        ) from error


def _take_schema_version(
    document: dict[str, Any],
    layer_name: str,
) -> int:
    schema_version = document.pop("schema_version", None)

    if type(schema_version) is not int or schema_version <= 0:
        raise ConfigurationError(
            "invalid_schema_version",
            layer=layer_name,
            key="schema_version",
        )

    return schema_version


def _validate_table(
    values: dict[str, Any],
    schema: dict[str, object],
    layer_name: str,
    prefix: tuple[str, ...] = (),
) -> None:
    for key, value in values.items():
        path = prefix + (key,)
        dotted_key = ".".join(path)

        if key not in schema:
            raise ConfigurationError(
                "unknown_key",
                layer=layer_name,
                key=dotted_key,
            )

        expected = schema[key]

        if isinstance(expected, dict):
            if type(value) is not dict:
                raise ConfigurationError(
                    "type_mismatch",
                    layer=layer_name,
                    key=dotted_key,
                )
            _validate_table(
                value,
                expected,
                layer_name,
                path,
            )
            continue

        if isinstance(expected, list):
            item_type = expected[0]
            if type(value) is not list or any(
                type(item) is not item_type for item in value
            ):
                raise ConfigurationError(
                    "type_mismatch",
                    layer=layer_name,
                    key=dotted_key,
                )
            continue

        if type(value) is not expected:
            raise ConfigurationError(
                "type_mismatch",
                layer=layer_name,
                key=dotted_key,
            )


def _merge_table(
    target: dict[str, Any],
    incoming: dict[str, Any],
    sources: dict[str, str],
    layer_name: str,
    prefix: tuple[str, ...] = (),
) -> None:
    for key, value in incoming.items():
        path = prefix + (key,)

        if type(value) is dict:
            target_table = target.setdefault(key, {})
            _merge_table(
                target_table,
                value,
                sources,
                layer_name,
                path,
            )
            continue

        target[key] = copy.deepcopy(value)
        sources[".".join(path)] = layer_name
