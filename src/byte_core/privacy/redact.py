"""Per-report deterministic redaction for explicitly approved fields.

Redaction does not authorize collection, retention, or transmission. This
bootstrap interface is internal and is not a stable public API.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import NoReturn

_KEY_BYTES = 32
_MAX_VALUE_CHARACTERS = 1_000_000
_OUTPUT_HEX_CHARACTERS = 16
_DOMAIN = b"byte-core/redaction/v1\0"


class RedactionError(Exception):
    """A redaction failure whose message contains no input content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RedactionContext:
    """An ephemeral keyed namespace for one local report."""

    __slots__ = ("__key",)

    def __init__(self) -> None:
        self.__key = self._generate_key()

    @classmethod
    def create(cls) -> RedactionContext:
        """Create a context with a fresh cryptographically random key."""

        return cls()

    @staticmethod
    def _generate_key() -> bytes:
        try:
            key = secrets.token_bytes(_KEY_BYTES)
        except Exception as error:
            raise RedactionError("key_generation_failed") from error

        if type(key) is not bytes or len(key) != _KEY_BYTES:
            raise RedactionError("key_generation_failed")
        return key

    def redact_path(self, value: str) -> str:
        """Return a stable path placeholder within this context."""

        return self._redact("path", value)

    def redact_identity(self, value: str) -> str:
        """Return a stable identity placeholder within this context."""

        return self._redact("identity", value)

    def redact_address(self, value: str) -> str:
        """Return a stable address placeholder within this context."""

        return self._redact("address", value)

    def _redact(self, value_class: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise RedactionError("invalid_redaction_value")
        if len(value) > _MAX_VALUE_CHARACTERS:
            raise RedactionError("redaction_value_too_large")

        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise RedactionError("invalid_redaction_value") from error

        digest = hmac.new(
            self.__key,
            _DOMAIN + value_class.encode("ascii") + b"\0" + encoded,
            hashlib.sha256,
        ).hexdigest()
        return f"<{value_class}:{digest[:_OUTPUT_HEX_CHARACTERS]}>"

    def __repr__(self) -> str:
        return "RedactionContext(<ephemeral>)"

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        del protocol
        raise TypeError("redaction_context_not_serializable")
