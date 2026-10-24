"""Versioned scrypt hashes using a documented OWASP cost profile."""

import hashlib
import secrets


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = secrets.token_bytes(16) if salt is None else salt
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=5, dklen=32
    )
    return f"scrypt-v1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 64:
            return False
        actual = hash_password(password, bytes.fromhex(salt))
        return secrets.compare_digest(actual, encoded)
    except (ValueError, UnicodeError):
        return False
