"""敏感字段的对称加密。

身份证号、手机号属于敏感个人信息，不能明文躺在数据库里。
密钥从 `settings.secret_key` 派生（部署包里是固定的 secret.key），
换密钥会导致旧密文无法解密——那时按「未填写」处理，不让接口 500。
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger("app.crypto")

_cached_secret = None
_cached_fernet = None


def _fernet() -> Fernet:
    global _cached_secret, _cached_fernet
    raw = settings.secret_key or "dev-only-insecure-change-me"
    if _cached_fernet is None or _cached_secret != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        _cached_fernet = Fernet(base64.urlsafe_b64encode(digest))
        _cached_secret = raw
    return _cached_fernet


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # 密钥变更或历史脏数据：当作未填写，避免整个资料接口报错
        logger.warning("敏感字段解密失败，按未填写处理")
        return ""
