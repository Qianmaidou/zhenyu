"""积分服务：流水 append-only + 余额同事务更新 + 幂等防刷。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLog, PointLedger, User
from app.errors import BizError, E_BAD_REQUEST

REASON_TEXT = {
    "register": "新用户注册赠送",
    "daily_checkin": "每日签到",
    "generate": "AI 生成抵扣/返还",
    "publish_work": "发布作品",
    "receive_like": "作品被点赞",
    "like_others": "给他人作品点赞",
    "favorite": "收藏作品",
    "comment": "评论作品",
    "redeem_quota": "兑换 AI 生成额度",
    "admin_adjust": "管理员调整",
}

# 每个 reason 的自然日上限，防刷：0 表示不限
DAILY_CAPS = {
    "daily_checkin": 1,
    "publish_work": 5,
    "receive_like": 20,
    "like_others": 30,
    "comment": 10,
    "favorite": 30,
}


def _today_count(session: Session, user_id: int, reason: str) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    since = datetime.now(timezone.utc) - timedelta(days=1)
    row = session.query(func.count(PointLedger.id)).filter(
        PointLedger.user_id == user_id,
        PointLedger.reason == reason,
        PointLedger.created_at >= since,
    ).scalar()
    return int(row or 0)


def award_points(
    session: Session,
    user: User,
    reason: str,
    delta: int,
    *,
    ref_type: str = "",
    ref_id: str = "",
    idempotent: bool = True,
) -> PointLedger:
    """记一笔积分。同一 ref 只生效一次（幂等），并返回本次是否真实发放。"""
    if delta == 0:
        raise BizError(E_BAD_REQUEST, "积分变动不能为 0")

    cap = DAILY_CAPS.get(reason, 0)
    if cap and _today_count(session, user.id, reason) >= cap:
        raise BizError(E_BAD_REQUEST, f"「{REASON_TEXT.get(reason, reason)}」今日已达上限")

    if idempotent and ref_type and ref_id:
        exists = session.query(PointLedger).filter_by(
            user_id=user.id, reason=reason, ref_type=ref_type, ref_id=str(ref_id)
        ).first()
        if exists:
            raise BizError(E_BAD_REQUEST, "该奖励已发放")

    # 同事务：先更新余额，再落流水，保证 balance_after 与 users.points 一致
    new_balance = max(0, (user.points or 0) + delta)
    user.points = new_balance
    session.flush()

    ledger = PointLedger(
        user_id=user.id,
        delta=delta,
        balance_after=new_balance,
        reason=reason,
        ref_type=ref_type,
        ref_id=str(ref_id),
    )
    session.add(ledger)
    session.flush()
    return ledger


def spend_points(
    session: Session, user: User, reason: str, cost: int, **kw
) -> PointLedger:
    if (user.points or 0) < cost:
        raise BizError(E_BAD_REQUEST, "积分不足")
    return award_points(session, user, reason, -cost, idempotent=False, **kw)


def write_audit(
    session: Session,
    action: str,
    *,
    actor: str = "system",
    target_type: str = "",
    target_id: str = "",
    detail: str = "",
) -> AuditLog:
    log = AuditLog(
        actor=actor, action=action, target_type=target_type,
        target_id=str(target_id), detail=detail,
    )
    session.add(log)
    session.flush()
    return log
