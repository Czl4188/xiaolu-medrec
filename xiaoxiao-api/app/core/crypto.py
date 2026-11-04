"""敏感字段的对称加密。

身份证号、手机号属于敏感个人信息，不能明文躺在数据库里。
密钥从 `settings.secret_key` 派生（部署包里是固定的 secret.key），
换密钥会导致旧密文无法解密——那时按「未填写」处理，不让接口 500。
"""

import base64
import hashlib
import hmac
import logging
import re

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


def normalize_phone(value: str) -> str:
    """去掉空格、横线、+86 前缀，只留 11 位数字。"""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits


def phone_digest(phone: str) -> str:
    """手机号的确定性摘要，只用来查账号，不可逆。

    手机号是登录账号，必须能按它定位用户；但明文入库等于把
    全量号码摊开。用 secret_key 做 HMAC：同一个号码得到同一个摘要
    （可以建唯一索引），拿到库也反推不出号码。
    换密钥会让旧摘要全部失配——那时只能让用户重新绑定。
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return ""
    raw = settings.secret_key or "dev-only-insecure-change-me"
    return hmac.new(raw.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()

