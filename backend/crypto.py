"""Field-level encryption at rest (Fernet) for sensitive PRIVATE fields.

Per §2: last4, credit_limit, point balances, and targeted-offer figures must be
encrypted at rest with a reversible cipher (they must be displayable). We use
SQLAlchemy ``TypeDecorator``s so encryption/decryption is transparent to the
rest of the app — values are decrypted only in-memory for display, and the DB
only ever holds ciphertext.

Run ``python -m backend.crypto`` to generate a fresh FERNET_KEY.
"""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from . import config


class MissingKeyError(RuntimeError):
    """Raised when an encrypted field is used but FERNET_KEY is not configured."""


def _fernet() -> Fernet:
    if not config.FERNET_KEY:
        raise MissingKeyError(
            "FERNET_KEY is not set. Generate one with `python -m backend.crypto` "
            "and put it in your .env before storing/reading PRIVATE data."
        )
    try:
        return Fernet(config.FERNET_KEY.encode("utf-8"))
    except (ValueError, TypeError) as exc:  # malformed key
        raise MissingKeyError(f"FERNET_KEY is invalid: {exc}") from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise MissingKeyError(
            "Could not decrypt a stored value — the FERNET_KEY likely differs "
            "from the one used to encrypt it. Restore the original key."
        ) from exc


class EncryptedString(TypeDecorator):
    """A reversibly-encrypted text column (stored as ciphertext, used as str)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return encrypt(str(value))

    def process_result_value(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return decrypt(value)


class EncryptedInt(TypeDecorator):
    """A reversibly-encrypted integer column (e.g. credit_limit, points)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return encrypt(str(int(value)))

    def process_result_value(self, value: Any, dialect) -> int | None:
        if value is None:
            return None
        return int(decrypt(value))


class EncryptedFloat(TypeDecorator):
    """A reversibly-encrypted float column (e.g. targeted cash offer)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return encrypt(str(float(value)))

    def process_result_value(self, value: Any, dialect) -> float | None:
        if value is None:
            return None
        return float(decrypt(value))


class EncryptedJSON(TypeDecorator):
    """A reversibly-encrypted JSON column (e.g. point_balances dict)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return encrypt(json.dumps(value, sort_keys=True))

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        return json.loads(decrypt(value))


if __name__ == "__main__":
    print(Fernet.generate_key().decode("utf-8"))
