"""FastAPI 依赖注入：会话、登录态、角色。"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import User
from app.errors import BizError, E_FORBIDDEN, E_UNAUTHORIZED
from app.security import bearer_scheme, decode_token


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    token = request.headers.get("Authorization", "")
    if not token.lower().startswith("bearer "):
        raise BizError(E_UNAUTHORIZED, http_status=401)
    payload = decode_token(token.split(" ", 1)[1])
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        raise BizError(E_UNAUTHORIZED, http_status=401)
    return user


def optional_user(request: Request, db: Session = Depends(get_session)) -> User | None:
    """游客可访问、登录后可增强的接口使用。失败不报错。"""
    creds = bearer_scheme.__call__  # 保持 scheme 对象可用于 OpenAPI
    _ = creds
    token = request.headers.get("Authorization", "")
    if not token.lower().startswith("bearer "):
        return None
    try:
        payload = decode_token(token.split(" ", 1)[1])
    except BizError:
        return None
    return db.get(User, int(payload["sub"]))


def require_role(*roles: str):
    def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise BizError(E_FORBIDDEN, http_status=403)
        return user

    return _dep


def device_key(request: Request) -> str:
    """游客身份 key：用于点赞/收藏去重，避免强制登录。"""
    k = request.headers.get("X-Device-Key") or request.query_params.get("dk")
    if k:
        return k[:64]
    ip = request.client.host if request.client else "anon"
    ua = request.headers.get("User-Agent", "")[:64]
    return f"anon:{ip}:{hash(ua) & 0xFFFFFF}"
