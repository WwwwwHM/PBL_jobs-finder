"""Versioned, salted password hashes using the Python standard library."""

import hashlib
import hmac
import secrets

ITERATIONS = 600_000
PASSWORD_HINT = "密码需为 8–128 个字符，且包含字母和数字"


def valid_password(password: str) -> bool:
    return (
        isinstance(password, str)
        and 8 <= len(password) <= 128
        and any(char.isalpha() for char in password)
        and any(char.isdigit() for char in password)
    )


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


# A valid dummy hash makes unknown accounts perform the same expensive check.
_DUMMY_HASH = f"pbkdf2_sha256${ITERATIONS}${'00' * 16}${'00' * 32}"


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        algorithm, rounds, salt, expected = (encoded or _DUMMY_HASH).split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)
        )
        matches = hmac.compare_digest(actual, bytes.fromhex(expected))
        return encoded is not None and matches
    except (ValueError, TypeError):
        return False
