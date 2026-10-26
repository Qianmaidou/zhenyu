"""种子数据与幂等装载。可重复执行：已存在的记录不会被覆盖。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.base import SessionLocal, engine, ensure_fts
from app.db.models import (
    KnowledgeEntry,
    Stitch,
    StitchRule,
    User,
    Work,
)
from app.security import hash_password

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def seed_stitches(db: Session) -> int:
    data = _load("seed_stitches.json")
    existing = {s.code for s in db.query(Stitch.code).all()}
    n = 0
    for item in data["stitches"]:
        if item["code"] in existing:
            continue
        db.add(
            Stitch(
                code=item["code"],
                name=item["name"],
                alias=item.get("alias") or None,
                family=item.get("family", "未分类"),
                difficulty=item.get("difficulty", 3),
                summary=item["summary"],
                suitable_for=item["suitable_for"],
                steps=json.dumps(item.get("steps", []), ensure_ascii=False),
                tips=item.get("tips", ""),
                sources=json.dumps(item.get("sources", []), ensure_ascii=False),
                review_status="pending_expert",
            )
        )
        n += 1
    db.commit()
    return n


def seed_rules(db: Session) -> int:
    data = _load("seed_stitches.json")
    existing = {(r.feature_key, r.stitch_code) for r in db.query(
        StitchRule.feature_key, StitchRule.stitch_code).all()}
    n = 0
    for item in data["rules"]:
        key = (item["feature_key"], item["stitch_code"])
        if key in existing:
            continue
        db.add(
            StitchRule(
                feature_key=item["feature_key"],
                feature_label=item["feature_label"],
                keywords=json.dumps(item["keywords"], ensure_ascii=False),
                stitch_code=item["stitch_code"],
                weight=item.get("weight", 1.0),
                rationale=item["rationale"],
                review_status="pending_expert",
            )
        )
        n += 1
    db.commit()
    return n


def seed_knowledge(db: Session) -> int:
    data = _load("seed_knowledge.json")
    existing = {e.question for e in db.query(KnowledgeEntry.question).all()}
    n = 0
    for item in data["entries"]:
        if item["question"] in existing:
            continue
        db.add(
            KnowledgeEntry(
                question=item["question"],
                answer=item["answer"],
                category=item.get("category", "综合"),
                tags=item.get("tags", ""),
                sources=json.dumps(item.get("sources", []), ensure_ascii=False),
                stitch_code=item.get("stitch_code"),
                review_status="pending_expert",
            )
        )
        n += 1
    db.commit()
    return n


def seed_works(db: Session) -> tuple[int, int]:
    data = _load("seed_works.json")
    user_map: dict[str, int] = {}
    n_users = 0
    for u in data["users"]:
        user = db.query(User).filter(User.email == u["email"]).first()
        if not user:
            user = User(
                email=u["email"],
                nickname=u["nickname"],
                hashed_password=hash_password(u["password"]),
                role=u.get("role", "user"),
                points=u.get("points", 0),
                avatar_seed=u["nickname"][:8],
            )
            db.add(user)
            db.flush()
            n_users += 1
        user_map[u["email"]] = user.id

    demo_id = user_map.get("demo@zhenyu.dev")

    # 有初始积分的账号必须有对应流水，否则「余额 = 流水之和」这条账不平
    from app.db.models import PointLedger

    for u in data["users"]:
        if not u.get("points"):
            continue
        uid = user_map[u["email"]]
        exists = db.query(PointLedger).filter_by(user_id=uid, reason="admin_adjust").first()
        if exists:
            continue
        db.add(
            PointLedger(
                user_id=uid, delta=int(u["points"]), balance_after=int(u["points"]),
                reason="admin_adjust", ref_type="seed", ref_id=u["email"],
            )
        )
    db.flush()
    existing_titles = {w.title for w in db.query(Work.title).all()}
    n_works = 0
    for w in data["works"]:
        if w["title"] in existing_titles:
            continue
        db.add(
            Work(
                user_id=demo_id,
                title=w["title"],
                description=w["description"],
                cover=w.get("cover", "#b22222"),
                tags=w.get("tags", ""),
                stitch_code=w.get("stitch_code") or None,
                source_type=w.get("source_type", "handmade"),
                is_seed=True,
                is_featured=bool(w.get("is_featured")),
                like_count=w.get("like_count", 0),
                fav_count=w.get("fav_count", 0),
                comment_count=w.get("comment_count", 0),
                review_status="pending_expert",
            )
        )
        n_works += 1
    db.commit()
    return n_users, n_works


def _refresh_fts() -> None:
    try:
        raw = engine.raw_connection()
        try:
            ensure_fts(raw.driver_connection)
        finally:
            raw.close()
    except Exception:  # noqa: BLE001
        pass


def seed_all() -> dict:
    db = SessionLocal()
    try:
        counts = {
            "stitches": seed_stitches(db),
            "rules": seed_rules(db),
            "knowledge": seed_knowledge(db),
        }
        counts["users"], counts["works"] = seed_works(db)
        _refresh_fts()
        return counts
    finally:
        db.close()


def reset_all(confirm: bool = False) -> dict:
    """重建全部业务表并重新装载种子（仅在初始化脚本里显式调用）。"""
    if not confirm:
        raise ValueError("reset_all 需要显式确认")
    from app.db.base import Base, SessionLocal as _SL
    from app.db import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = _SL()
    try:
        counts = {
            "stitches": seed_stitches(db),
            "rules": seed_rules(db),
            "knowledge": seed_knowledge(db),
        }
        counts["users"], counts["works"] = seed_works(db)
    finally:
        db.close()
    _refresh_fts()
    return counts
