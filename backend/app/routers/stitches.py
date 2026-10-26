"""针法库与规则表（只读查询 + 顾问确认状态）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import KnowledgeEntry, Stitch, StitchRule
from app.deps import get_session
from app.errors import BizError, ok
from app.schemas import StitchOut

router = APIRouter(prefix="/stitches", tags=["针法库"])

SOURCES_FALLBACK = [
    {"title": "内容整理自公开资料（待湘绣专业顾问确认）", "url": ""}
]


def _sources(raw: str, extra: list[dict] | None = None):
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        items = []
    items = list(items) + (extra or [])
    return items or SOURCES_FALLBACK


def _out(s: Stitch) -> StitchOut:
    try:
        steps = json.loads(s.steps or "[]")
    except json.JSONDecodeError:
        steps = []
    return StitchOut(
        id=s.id, code=s.code, name=s.name, alias=s.alias, family=s.family,
        difficulty=s.difficulty, summary=s.summary, suitable_for=s.suitable_for,
        steps=steps, tips=s.tips, sources=_sources(s.sources),
        review_status=s.review_status, view_count=s.view_count,
    )


@router.get("", summary="针法列表")
def list_stitches(db: Session = Depends(get_session), family: str = "", q: str = ""):
    query = db.query(Stitch)
    if family:
        query = query.filter(Stitch.family == family)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Stitch.name.like(like)) | (Stitch.summary.like(like)) | (Stitch.alias.like(like))
        )
    rows = query.order_by(Stitch.difficulty.asc(), Stitch.id.asc()).all()
    families = sorted({r.family for r in db.query(Stitch).all()})
    return ok({"items": [_out(r).model_dump() for r in rows], "families": families})


@router.get("/{code}", summary="针法详情")
def get_stitch(code: str, db: Session = Depends(get_session)):
    s = db.query(Stitch).filter(Stitch.code == code).first()
    if not s:
        raise BizError(40401, "针法不存在")
    s.view_count = (s.view_count or 0) + 1
    db.commit()
    data = _out(s).model_dump()
    rules = db.query(StitchRule).filter(StitchRule.stitch_code == code).all()
    data["rules"] = [
        {
            "feature": r.feature_label,
            "rationale": r.rationale,
            "weight": r.weight,
            "review_status": r.review_status,
        }
        for r in rules
    ]
    kbs = (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.stitch_code == code)
        .order_by(KnowledgeEntry.id.asc())
        .limit(10)
        .all()
    )
    data["related_knowledge"] = [
        {"id": k.id, "question": k.question, "review_status": k.review_status} for k in kbs
    ]
    return ok(data)


@router.post("/preview-rules", summary="预览：给定描述会命中哪些规则（不生成图）")
def preview_rules(payload: dict, db: Session = Depends(get_session)):
    from app.services.stitch_rules import recommend

    return ok(recommend(db, str(payload.get("prompt", ""))))
