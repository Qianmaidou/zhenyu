"""安全工具：密码哈希、JWT 签发与校验（不引入第三方加密依赖）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import HTTPException, status
from fastapi.security import HTTPBearer

from app.config import settings

PBKDF2_ROUNDS = 120_000
_SEPARATOR = "$"

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return _SEPARATOR.join(
        ["pbkdf2_sha256", str(PBKDF2_ROUNDS), _b64(salt), _b64(dk)]
    )


def verify_password(plain: str, stored: str) -> bool:
    parts = stored.split(_SEPARATOR)
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    rounds = int(parts[1])
    salt = _unb64(parts[2])
    expected = _unb64(parts[3])
    actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ---------------------------------------------------------------------------
# JWT（HS256，自实现，避免额外依赖）
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def create_token(payload: Dict[str, Any], ttl_min: int | None = None) -> str:
    now = int(time.time())
    body = dict(payload)
    body.update(
        {
            "iat": now,
            "exp": now + int((ttl_min or settings.access_token_ttl_min) * 60),
            "iss": "zhenyu",
        }
    )
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(body, separators=(',', ':')).encode())}"
    sig = hmac.new(
        settings.secret_key.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(sig)}"


def decode_token(token: str) -> Dict[str, Any]:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as exc:  # noqa: F841
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 格式错误")
    expected = _b64url(
        hmac.new(
            settings.secret_key.encode("utf-8"),
            f"{head_b64}.{body_b64}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(expected, sig_b64):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 签名无效")
    body = json.loads(_b64url_decode(body_b64))
    if int(body.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 已过期")
    return body


def utc_expiry(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{os.urandom(12).hex()}"
