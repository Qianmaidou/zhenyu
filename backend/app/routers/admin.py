"""管理后台最小页：待确认队列、专家确认操作、生成日志与成本台账、审计留痕。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    Comment,
    Generation,
    KnowledgeEntry,
    Stitch,
    StitchRule,
    User,
    Work,
)
from app.deps import get_session, require_role
from app.errors import BizError, ok
from app.services.points import write_audit

router = APIRouter(prefix="/admin", tags=["后台"])

_GUARD = Depends(require_role("admin", "reviewer"))

TARGETS = {
    "stitch": (Stitch, "针法"),
    "rule": (StitchRule, "针法规则"),
    "knowledge": (KnowledgeEntry, "知识条目"),
    "work": (Work, "社区作品"),
    "comment": (Comment, "评论"),
}


@router.get("/overview", summary="后台总览", dependencies=[_GUARD])
def overview(db: Session = Depends(get_session)):
    since = datetime.now(timezone.utc) - timedelta(days=1)
    gen_today = db.query(func.count(Generation.id)).filter(
        Generation.created_at >= since).scalar() or 0
    cost_today = db.query(func.coalesce(func.sum(Generation.cost_estimate), 0.0)).filter(
        Generation.created_at >= since).scalar() or 0.0
    fail_today = db.query(func.count(Generation.id)).filter(
        Generation.created_at >= since, Generation.status == "failed").scalar() or 0
    avg_lat = db.query(func.coalesce(func.avg(Generation.latency_ms), 0)).filter(
        Generation.created_at >= since, Generation.status == "succeeded").scalar() or 0
    return ok(
        {
            "users": db.query(func.count(User.id)).scalar() or 0,
            "generations_today": gen_today,
            "failed_today": fail_today,
            "cost_today": round(float(cost_today), 4),
            "avg_latency_ms": int(avg_lat),
            "pending": {
                "stitches": db.query(func.count(Stitch.id)).filter(
                    Stitch.review_status == "pending_expert").scalar() or 0,
                "rules": db.query(func.count(StitchRule.id)).filter(
                    StitchRule.review_status == "pending_expert").scalar() or 0,
                "knowledge": db.query(func.count(KnowledgeEntry.id)).filter(
                    KnowledgeEntry.review_status == "pending_expert").scalar() or 0,
                "works": db.query(func.count(Work.id)).filter(
                    Work.review_status == "pending_expert").scalar() or 0,
            },
            "totals": {
                "stitches": db.query(func.count(Stitch.id)).scalar() or 0,
                "rules": db.query(func.count(StitchRule.id)).scalar() or 0,
                "knowledge": db.query(func.count(KnowledgeEntry.id)).scalar() or 0,
                "works": db.query(func.count(Work.id)).scalar() or 0,
            },
        }
    )


@router.get("/pending", summary="待顾问确认队列", dependencies=[_GUARD])
def pending(db: Session = Depends(get_session), target: str = "knowledge", limit: int = 30):
    model_pair = TARGETS.get(target)
    if not model_pair:
        raise BizError(40001, "未知类型")
    model, label = model_pair
    rows = (
        db.query(model)
        .filter(model.review_status == "pending_expert")
        .order_by(model.id.asc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    items = []
    for r in rows:
        title = getattr(r, "name", None) or getattr(r, "question", None) \
            or getattr(r, "title", None) or getattr(r, "feature_label", None) \
            or getattr(r, "content", "")
        items.append(
            {
                "id": r.id,
                "title": str(title)[:60],
                "review_status": r.review_status,
                "updated_at": r.updated_at.isoformat() if getattr(r, "updated_at", None) else None,
            }
        )
    return ok({"target": target, "label": label, "items": items})


@router.post("/review", summary="顾问确认 / 驳回", dependencies=[_GUARD])
def review(payload: dict, db: Session = Depends(get_session), user=Depends(require_role("admin"))):
    target = str(payload.get("target", ""))
    obj_id = int(payload.get("id", 0))
    status = str(payload.get("status", "expert_confirmed"))
    note = str(payload.get("note", ""))[:300]
    if target not in TARGETS:
        raise BizError(40001, "未知类型")
    if status not in {"expert_confirmed", "pending_expert", "rejected"}:
        raise BizError(40001, "状态不合法")
    model, _ = TARGETS[target]
    row = db.get(model, obj_id)
    if not row:
        raise BizError(40401, "目标不存在")
    before = row.review_status
    row.review_status = status
    row.reviewer = user.nickname
    row.review_note = note
    write_audit(
        db, "expert_review", actor=user.email, target_type=target, target_id=str(obj_id),
        detail=f"{before} → {status}; {note}",
    )
    db.commit()
    return ok({"id": obj_id, "review_status": status})


@router.get("/generations", summary="生成日志与成本台账", dependencies=[_GUARD])
def generations(db: Session = Depends(get_session), limit: int = 30):
    rows = db.query(Generation).order_by(Generation.id.desc()).limit(
        max(1, min(limit, 100))).all()
    return ok(
        {
            "items": [
                {
                    "public_id": r.public_id,
                    "prompt": r.prompt[:60],
                    "status": r.status,
                    "provider": r.provider,
                    "latency_ms": r.latency_ms,
                    "cost_estimate": round(r.cost_estimate, 4),
                    "user_id": r.user_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total_cost": round(sum(r.cost_estimate for r in rows), 4),
        }
    )


@router.get("/audit", summary="审计留痕", dependencies=[_GUARD])
def audits(db: Session = Depends(get_session), limit: int = 30):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(
        max(1, min(limit, 100))).all()
    return ok(
        [
            {
                "id": r.id, "actor": r.actor, "action": r.action,
                "target_type": r.target_type, "target_id": r.target_id,
                "detail": r.detail, "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    )
