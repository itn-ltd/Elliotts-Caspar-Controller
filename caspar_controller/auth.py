"""Password hashing and API key helpers."""

import hashlib
import secrets

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _utf8(text: str) -> bytes:
    # surrogatepass: JSON can carry lone surrogates, which plain .encode() rejects.
    return text.encode("utf-8", "surrogatepass")


def hash_password(password: str) -> str:
    """Return a salted scrypt hash as ``scrypt$<salt hex>$<hash hex>``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(_utf8(password), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True if ``password`` matches a hash from ``hash_password``."""
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(_utf8(password), salt=salt, **_SCRYPT)
    return secrets.compare_digest(digest.hex(), digest_hex)


def new_api_key() -> str:
    """Return a new random API key."""
    return secrets.token_urlsafe(32)


def key_matches(supplied: str | None, expected: str) -> bool:
    """Constant-time API key check; False if either key is empty."""
    return bool(supplied and expected) and secrets.compare_digest(_utf8(supplied), _utf8(expected))
