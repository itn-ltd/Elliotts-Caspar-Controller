"""Password hashing and API key helpers."""

import hashlib
import secrets

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    """Return a salted scrypt hash as ``scrypt$<salt hex>$<hash hex>``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return secrets.compare_digest(digest.hex(), digest_hex)


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


def key_matches(supplied: str | None, expected: str) -> bool:
    # Bytes: compare_digest raises TypeError on non-ASCII str.
    return bool(supplied and expected) and secrets.compare_digest(
        supplied.encode(), expected.encode()
    )
