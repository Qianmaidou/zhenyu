"""湘绣知识问答与知识库浏览。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import KnowledgeEntry
from app.deps import get_session
from app.errors import BizError, ok
from app.schemas import AskIn, KnowledgeOut
from app.services.retrieval import compose_answer, search

router = APIRouter(prefix="/knowledge", tags=["知识问答"])


def _out(e: KnowledgeEntry) -> KnowledgeOut:
    try:
        sources = json.loads(e.sources or "[]")
    except json.JSONDecodeError:
        sources = []
    return KnowledgeOut(
        id=e.id, question=e.question, answer=e.answer, category=e.category,
        tags=e.tags, sources=sources, review_status=e.review_status,
    )


@router.post("/ask", summary="提问（带来源、低置信度有兜底）")
def ask(body: AskIn, db: Session = Depends(get_session)):
    hits = search(db, body.question, body.top_k)
    ans = compose_answer(body.question, hits)
    return ok({"question": body.question, **ans, "hits": [h for h in hits]})


@router.get("/search", summary="知识条目检索")
def ksearch(q: str = "", category: str = "", limit: int = 20,
            db: Session = Depends(get_session)):
    limit = max(1, min(limit, 60))
    if q:
        return ok({"items": [h for h in search(db, q, limit)], "total": len(search(db, q, limit))})
    query = db.query(KnowledgeEntry)
    if category:
        query = query.filter(KnowledgeEntry.category == category)
    rows = query.order_by(KnowledgeEntry.id.asc()).limit(limit).all()
    return ok({"items": [_out(r).model_dump() for r in rows],
               "total": db.query(KnowledgeEntry).count()})


@router.get("/categories", summary="分类与统计")
def categories(db: Session = Depends(get_session)):
    rows = db.query(KnowledgeEntry).all()
    stats: dict[str, dict] = {}
    total = len(rows)
    pending = 0
    for r in rows:
        st = stats.setdefault(
            r.category, {"category": r.category, "count": 0, "confirmed": 0, "pending": 0}
        )
        st["count"] += 1
        if r.review_status == "expert_confirmed":
            st["confirmed"] += 1
        else:
            st["pending"] += 1
            pending += 1
    return ok(
        {
            "total": total,
            "pending": pending,
            "confirmed": total - pending,
            "categories": sorted(stats.values(), key=lambda x: -x["count"]),
        }
    )


@router.get("/{entry_id}", summary="条目详情")
def detail(entry_id: int, db: Session = Depends(get_session)):
    e = db.get(KnowledgeEntry, entry_id)
    if not e:
        raise BizError(40401, "条目不存在")
    return ok(_out(e).model_dump())
