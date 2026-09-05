"""Password and session primitives with no third-party dependencies."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets


SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
USERNAME_PATTERN = re.compile(r"^[\w\u3400-\u9fff.@-]{3,64}$", re.UNICODE)


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("管理员账号须为 3-64 个中英文字符，可包含 . @ _ -")
    return normalized


def validate_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(f"管理员密码须为 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 个字符")
    if password.isspace():
        raise ValueError("管理员密码不能全部为空白字符")
    return password


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    password = validate_password(password)
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return actual_salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    if len(password) > PASSWORD_MAX_LENGTH:
        return False
    try:
        _, actual = hash_password(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
