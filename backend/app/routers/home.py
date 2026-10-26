"""首页聚合数据与元信息（数据来源标注集中在此，便于合规审查）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CrawledAsset, KnowledgeEntry, Stitch, StitchRule, Work
from app.deps import get_session
from app.errors import ok

router = APIRouter(prefix="/meta", tags=["元信息"])


@router.get("/stats", summary="首页统计")
def stats(db: Session = Depends(get_session)):
    return ok(
        {
            "stitches": db.query(Stitch).count(),
            "rules": db.query(StitchRule).count(),
            "knowledge": db.query(KnowledgeEntry).count(),
            "works": db.query(Work).count(),
            "provider": settings.image_provider,
            "app_version": settings.app_version,
        }
    )


@router.get("/sources", summary="数据来源与版权声明")
def sources(db: Session = Depends(get_session)):
    crawled = 0
    try:
        from sqlalchemy import text

        crawled = int(
            db.execute(text("SELECT COUNT(*) FROM knowledge_entries WHERE tags LIKE '%crawled%'"))
            .scalar()
            or 0
        )
    except Exception:  # noqa: BLE001
        crawled = 0
    return ok(
        {
            "items": [
                {
                    "name": "中文维基百科",
                    "endpoint": "https://zh.wikipedia.org/w/api.php",
                    "scope": "湘绣历史、非遗与工艺背景",
                    "license": "CC BY-SA 4.0",
                    "note": "使用时请遵循对应许可并保留来源。",
                },
                {
                    "name": "克利夫兰艺术博物馆开放数据",
                    "endpoint": "https://openaccess-api.clevelandart.org/api/artworks/",
                    "scope": "中国织绣类藏品图片",
                    "license": "CC0 1.0（公共领域贡献）",
                    "note": "图片与作者信息来自博物馆开放数据，使用时请遵守许可。",
                },
                {
                    "name": "Wikimedia Commons",
                    "endpoint": "https://commons.wikimedia.org/w/api.php",
                    "scope": "公有领域或开放许可的图片素材",
                    "license": "以每张素材的标注为准",
                    "note": "图片由原作者或机构发布，作者与许可以原件页面为准。",
                },
                {
                    "name": "平台整理内容",
                    "endpoint": "本地 data/seed/*.json",
                    "scope": "针法、知识问答与示例作品",
                    "license": "整理自公开资料，未经专业顾问确认",
                    "note": "未经顾问确认的内容会标注「待确认」。",
                },
            ],
            "crawled_entries": crawled,
        }
    )


@router.get("/gallery", summary="公开素材库（Wikimedia Commons，仅存 URL 与许可）")
def gallery(db: Session = Depends(get_session), term: str = "", limit: int = 24):
    q = db.query(CrawledAsset).filter(CrawledAsset.is_visible.is_(True))
    if term:
        q = q.filter(CrawledAsset.term == term)
    total = q.count()
    rows = q.order_by(CrawledAsset.id.desc()).limit(max(1, min(limit, 60))).all()
    if not rows:
        return ok({"items": [], "total": 0, "terms": [], "notice": "暂无公开素材，请稍后再来看看。"})
    terms = sorted({r.term for r in db.query(CrawledAsset).all()})
    return ok(
        {
            "items": [
                {
                    "id": r.id, "title": r.title, "url": r.url, "page_url": r.page_url,
                    "license": r.license, "author": r.author,
                    "description": r.description[:160], "term": r.term,
                }
                for r in rows
            ],
            "total": total,
            "terms": terms,
            "notice": "素材来自 Wikimedia Commons，作者与许可以原件页面为准。",
        }
    )


@router.get("/weekly", summary="本周推荐针法")
def weekly(db: Session = Depends(get_session)):
    from datetime import date

    rows = db.query(Stitch).order_by(Stitch.id.asc()).all()
    if not rows:
        return ok(None)
    idx = date.today().toordinal() % len(rows)
    s = rows[idx]
    try:
        steps = json.loads(s.steps or "[]")
    except json.JSONDecodeError:
        steps = []
    return ok(
        {
            "code": s.code, "name": s.name, "summary": s.summary,
            "difficulty": s.difficulty, "steps": steps,
            "review_status": s.review_status,
        }
    )
