"""密码与令牌工具（M2 权限模块）。"""
import hashlib
import hmac
import secrets

from app.core.config import get_settings

PBKDF2_SALT = b"rs-v1"  # 与 003_seed.sql 一致；修改盐将使全部存量密码失效


def verify_password(plain: str, password_hash: str) -> bool:
    expected = hashlib.pbkdf2_hmac(
        "sha256", plain.encode(), PBKDF2_SALT, get_settings().password_pbkdf2_rounds
    ).hex()
    return hmac.compare_digest(expected, password_hash or "")


def hash_password(plain: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", plain.encode(), PBKDF2_SALT, get_settings().password_pbkdf2_rounds
    ).hex()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
