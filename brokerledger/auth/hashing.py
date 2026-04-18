"""Argon2id password hashing."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_value: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_value, password)
    except VerifyMismatchError:
        return False


def needs_rehash(hash_value: str) -> bool:
    return _hasher.check_needs_rehash(hash_value)
