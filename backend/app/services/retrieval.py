"""轻量 RAG 检索：关键词 + 全文索引双路召回，统一打分、带来源、无结果有兜底。

阶段3 的完整形态会是「向量检索 + 重排 + 大模型组织」；MVP 先用
SQLite FTS5（不可用时降级 LIKE）+ 词项重叠打分，把下面三件事先做扎实：
  1. 答案永远附来源；
  2. 低置信度时不硬答，直接给兜底话术；
  3. 命中内容若未过专家审核，明确告知用户。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import KnowledgeEntry

TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}")
STOPWORDS = {"怎么", "如何", "什么", "哪些", "可以", "是否", "请问", "我想", "的针法", "一下"}


def tokenize(query: str) -> list[str]:
    toks = [t for t in TOKEN_RE.findall(query or "") if t not in STOPWORDS]
    # 长串不成词时退化为二元切分，保证短查询也有召回
    if not toks and query:
        toks = [query[i : i + 2] for i in range(0, min(len(query), 8))]
    return list(dict.fromkeys(toks))[:12]


def _fts_candidates(session: Session, tokens: list[str]) -> dict[int, float]:
    """用 FTS5 做召回，返回 {id: fts_score}（0-1，越大越好）。"""
    if not tokens:
        return {}
    match = " OR ".join(f'"{t}"' for t in tokens[:8])
    try:
        rows = session.execute(
            text(
                "SELECT rowid AS rid, bm25(knowledge_fts) AS score "
                "FROM knowledge_fts WHERE knowledge_fts MATCH :m "
                "ORDER BY score LIMIT :k"
            ),
            {"m": match, "k": max(20, settings.retrieval_top_k * 5)},
        ).all()
    except Exception:  # noqa: BLE001 - 无 FTS5 或语法不支持 → 走 LIKE
        return {}
    out: dict[int, float] = {}
    # bm25 返回负数，越负越相关：映射到 (0,1]
    vals = [float(r[1]) for r in rows] or [0.0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    for rid, sc in rows:
        out[int(rid)] = round(1.0 - (float(sc) - lo) / span, 4)
    return out


def _like_candidates(session: Session, tokens: list[str]) -> dict[int, float]:
    """FTS 不可用时的降级召回：逐词 LIKE，按命中词数归一打分。"""
    if not tokens:
        return {}
    entries = session.query(KnowledgeEntry).all()
    out: dict[int, float] = {}
    for e in entries:
        blob = f"{e.question} {e.answer} {e.tags}".lower()
        hit = sum(1 for t in tokens if t.lower() in blob)
        if hit:
            out[e.id] = round(hit / len(tokens), 4)
    return out


def _lexical_boost(entry: KnowledgeEntry, tokens: list[str]) -> float:
    q = (entry.question or "").lower()
    tags = (entry.tags or "").lower()
    score = 0.0
    for t in tokens:
        tl = t.lower()
        if tl in q:
            score += 0.18
        if tl in tags:
            score += 0.12
    return score


def search(session: Session, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    top_k = top_k or settings.retrieval_top_k
    tokens = tokenize(query)
    fts = _fts_candidates(session, tokens)
    like = _like_candidates(session, tokens) if not fts else {}

    ids = set(fts) | set(like)
    if not ids:
        return []
    entries = {e.id: e for e in session.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(ids)).all()}

    scored: list[tuple[float, dict[str, Any]]] = []
    for eid in ids:
        e = entries.get(eid)
        if not e:
            continue
        base = max(fts.get(eid, 0.0), like.get(eid, 0.0))
        score = min(1.0, 0.7 * base + _lexical_boost(e, tokens))
        if score < settings.retrieval_min_score:
            continue
        try:
            sources = json.loads(e.sources or "[]")
        except json.JSONDecodeError:
            sources = []
        scored.append(
            (
                score,
                {
                    "id": e.id,
                    "question": e.question,
                    "answer": e.answer,
                    "category": e.category,
                    "tags": e.tags,
                    "sources": sources,
                    "review_status": e.review_status,
                    "stitch_code": e.stitch_code,
                    "score": round(score, 4),
                },
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    top = scored[:top_k]
    for hit in (h for _, h in top):
        e = entries[hit["id"]]
        e.hit_count = (e.hit_count or 0) + 1
    session.commit()
    return [h for _, h in top]


NO_ANSWER_TEXT = (
    "暂时没有找到可靠资料，因此不做推测。"
    "你可以在「针法库」查看相近内容，或带着具体问题咨询湘绣传承人、专业机构。"
)


def compose_answer(query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    confident = [h for h in hits if h["score"] >= settings.retrieval_min_score]
    if not confident:
        return {
            "answer": NO_ANSWER_TEXT,
            "sources": [],
            "review_status": "pending_expert",
            "confident": False,
            "related": [],
        }
    top = confident[0]
    answer_parts = [top["answer"]]
    if len(confident) > 1:
        answer_parts.append(
            "\n\n相关可参考：" + "、".join(h["question"][:32] for h in confident[1:3])
        )
    if top["review_status"] != "expert_confirmed":
        answer_parts.append(
            "\n\n【说明】本条目整理自公开资料，尚未经湘绣专业顾问确认，仅供参考。"
        )
    sources: list[dict[str, Any]] = []
    for h in confident[:3]:
        for s in h["sources"]:
            if s not in sources:
                sources.append(s)
    return {
        "answer": "".join(answer_parts),
        "sources": sources[:4],
        "review_status": top["review_status"],
        "confident": True,
        "related": [{"id": h["id"], "question": h["question"]} for h in confident[1:4]],
        "stitch_code": top.get("stitch_code"),
    }
