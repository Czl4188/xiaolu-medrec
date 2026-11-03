"""口令散列与无状态令牌。

只用标准库实现：生产环境要换 bcrypt/argon2 也只改这一个文件。
令牌是 JWT(HS256)，API 侧不落会话表 —— 这是「API 无状态可扩容」的前提。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import settings

PBKDF2_ROUNDS = 210_000


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = _b64d(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _b64d(salt_b64), int(rounds)
        )
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


def new_session_token() -> str:
    """不透明令牌：32 字节随机数，服务端只存哈希。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sign(msg: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).digest()


def issue_token(user_id: str, username: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "name": username,
        "iat": now,
        "exp": now + settings.access_token_ttl_seconds,
        "jti": secrets.token_hex(8),
    }
    head = _b64e(json.dumps(header, separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64e(_sign(f"{head}.{body}".encode()))
    return f"{head}.{body}.{signature}"


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        head, body, signature = token.split(".")
        if not hmac.compare_digest(signature, _b64e(_sign(f"{head}.{body}".encode()))):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
