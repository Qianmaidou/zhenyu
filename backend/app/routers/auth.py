"""账号与会话。"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models import PointLedger, User
from app.deps import current_user, get_session
from app.errors import BizError, E_BAD_REQUEST, E_CONFLICT, ok
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from app.security import create_token, hash_password, verify_password
from app.services.points import REASON_TEXT, award_points
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/auth", tags=["账号"])


def _serialize(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        nickname=user.nickname,
        role=user.role,
        points=user.points,
        quota_free=user.quota_free,
        avatar_seed=user.avatar_seed,
    )


@router.post("/register", summary="注册")
def register(body: RegisterIn, db: Session = Depends(get_session)):
    if len(body.password) < settings.password_min_length:
        raise BizError(E_BAD_REQUEST, f"密码至少 {settings.password_min_length} 位")
    user = User(
        email=str(body.email).lower(),
        nickname=body.nickname,
        hashed_password=hash_password(body.password),
        avatar_seed=(body.nickname or "xiang")[:8],
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BizError(E_CONFLICT, "该邮箱已注册")
    db.refresh(user)
    award_points(db, user, "register", 50, ref_type="user", ref_id=user.id)
    db.commit()
    token = create_token({"sub": str(user.id)})
    return ok(TokenOut(token=token, expires_in=settings.access_token_ttl_min * 60,
                       user=_serialize(user)).model_dump())


@router.post("/login", summary="登录")
def login(body: LoginIn, db: Session = Depends(get_session)):
    user = db.query(User).filter(User.email == str(body.email).lower()).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise BizError(E_BAD_REQUEST, "邮箱或密码不正确")
    if user.status != "active":
        raise BizError(E_BAD_REQUEST, "账号已被停用")
    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = create_token({"sub": str(user.id)})
    return ok(TokenOut(token=token, expires_in=settings.access_token_ttl_min * 60,
                       user=_serialize(user)).model_dump())


@router.get("/me", summary="当前登录用户")
def me(user: User = Depends(current_user)):
    return ok(_serialize(user).model_dump())


@router.get("/points", summary="积分流水")
def my_points(user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = (
        db.query(PointLedger)
        .filter(PointLedger.user_id == user.id)
        .order_by(PointLedger.id.desc())
        .limit(50)
        .all()
    )
    return ok(
        {
            "balance": user.points,
            "items": [
                {
                    "id": r.id,
                    "delta": r.delta,
                    "balance_after": r.balance_after,
                    "reason": r.reason,
                    "reason_text": REASON_TEXT.get(r.reason, r.reason),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    )
