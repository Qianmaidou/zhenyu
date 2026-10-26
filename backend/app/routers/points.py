"""积分：签到、兑换额度。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import PointLedger, User
from app.deps import current_user, get_session
from app.errors import BizError, ok
from app.services.points import REASON_TEXT, award_points, spend_points, write_audit

router = APIRouter(prefix="/points", tags=["积分"])

QUOTA_COST = 10  # 10 积分兑换 1 次生成额度


@router.post("/checkin", summary="每日签到")
def checkin(user: User = Depends(current_user), db: Session = Depends(get_session)):
    ledger = award_points(db, user, "daily_checkin", 5)
    db.commit()
    return ok({"delta": 5, "balance": ledger.balance_after, "text": REASON_TEXT["daily_checkin"]})


@router.post("/redeem-quota", summary="积分兑换 AI 生成额度")
def redeem(payload: dict, user: User = Depends(current_user), db: Session = Depends(get_session)):
    count = int(payload.get("count", 1) or 1)
    if count < 1 or count > 20:
        raise BizError(40001, "兑换数量需在 1-20 之间")
    cost = QUOTA_COST * count
    ledger = spend_points(db, user, "redeem_quota", cost,
                          ref_type="manual", ref_id=f"{user.id}:{count}")
    user.quota_free = (user.quota_free or 0) + count
    db.commit()
    write_audit(db, "points_redeem", actor=user.email, target_type="user",
                target_id=str(user.id), detail=f"cost={cost}, count={count}")
    db.commit()
    return ok({"cost": cost, "quota": user.quota_free, "balance": ledger.balance_after})


@router.get("/ledger", summary="积分流水（分页）")
def ledger(user: User = Depends(current_user), db: Session = Depends(get_session),
           limit: int = 30):
    rows = (
        db.query(PointLedger)
        .filter(PointLedger.user_id == user.id)
        .order_by(PointLedger.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return ok(
        {
            "balance": user.points,
            "items": [
                {
                    "id": r.id, "delta": r.delta, "balance_after": r.balance_after,
                    "reason": r.reason, "reason_text": REASON_TEXT.get(r.reason, r.reason),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    )
