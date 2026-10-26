"""社区作品、互动与评论。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import Comment, Interaction, User, Work
from app.deps import current_user, device_key, get_session, optional_user
from app.errors import BizError, E_BAD_REQUEST, ok
from app.schemas import CommentIn, WorkIn
from app.services.points import award_points

router = APIRouter(prefix="/works", tags=["社区"])


def _user_key(user: User | None, request: Request) -> str:
    return f"u:{user.id}" if user else f"d:{device_key(request)}"


def _out(w: Work, db: Session, user_key: str) -> dict:
    author = db.get(User, w.user_id) if w.user_id else None
    liked = (
        db.query(Interaction)
        .filter_by(work_id=w.id, user_key=user_key, kind="like")
        .first()
        is not None
    )
    faved = (
        db.query(Interaction)
        .filter_by(work_id=w.id, user_key=user_key, kind="fav")
        .first()
        is not None
    )
    return {
        "id": w.id,
        "title": w.title,
        "description": w.description,
        "cover": w.cover,
        "tags": w.tags,
        "stitch_code": w.stitch_code,
        "is_seed": w.is_seed,
        "source_type": w.source_type,
        "like_count": w.like_count,
        "fav_count": w.fav_count,
        "comment_count": w.comment_count,
        "view_count": w.view_count,
        "review_status": w.review_status,
        "nickname": author.nickname if author else "平台示例",
        "liked": liked,
        "faved": faved,
    }


@router.get("", summary="作品列表")
def list_works(
    request: Request,
    db: Session = Depends(get_session),
    user: User | None = Depends(optional_user),
    q: str = "",
    stitch: str = "",
    sort: str = "new",
    limit: int = 24,
    offset: int = 0,
):
    query = db.query(Work)
    if stitch:
        query = query.filter(Work.stitch_code == stitch)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Work.title.like(like)) | (Work.description.like(like)) | (Work.tags.like(like))
        )
    order = {
        "hot": Work.like_count.desc(),
        "like": Work.like_count.desc(),
        "new": Work.id.desc(),
    }.get(sort, Work.id.desc())
    total = query.count()
    rows = query.order_by(order).offset(max(0, offset)).limit(max(1, min(limit, 60))).all()
    key = _user_key(user, request)
    return ok({"items": [_out(r, db, key) for r in rows], "total": total})


@router.get("/{work_id}", summary="作品详情")
def detail(work_id: int, request: Request, db: Session = Depends(get_session),
           user: User | None = Depends(optional_user)):
    w = db.get(Work, work_id)
    if not w:
        raise BizError(40401, "作品不存在")
    w.view_count = (w.view_count or 0) + 1
    db.commit()
    key = _user_key(user, request)
    data = _out(w, db, key)
    data["comments"] = [
        {"id": c.id, "author": c.author, "content": c.content,
         "review_status": c.review_status}
        for c in db.query(Comment).filter_by(work_id=work_id).order_by(Comment.id.desc()).limit(30)
    ]
    return ok(data)


@router.post("", summary="发布作品")
def publish(body: WorkIn, db: Session = Depends(get_session),
            user: User = Depends(current_user)):
    w = Work(
        user_id=user.id, title=body.title, description=body.description,
        cover=body.cover or _default_cover(body.title), tags=body.tags,
        stitch_code=body.stitch_code, source_type=body.source_type,
        review_status="pending_expert",
    )
    db.add(w)
    db.flush()
    try:
        award_points(db, user, "publish_work", 15, ref_type="work", ref_id=w.id)
    except BizError:
        pass
    db.commit()
    db.refresh(w)
    return ok({"id": w.id, "title": w.title})


def _default_cover(title: str) -> str:
    """给没有图的用户一个确定性色块封面，避免列表出现破图。"""
    palette = ["#b22222", "#2f6f4e", "#c8a04a", "#426a82", "#7c589e", "#c05a54"]
    idx = sum(ord(c) for c in title) % len(palette)
    return palette[idx]


@router.post("/{work_id}/toggle", summary="点赞/收藏切换")
def toggle(work_id: int, payload: dict, request: Request,
           db: Session = Depends(get_session), user: User | None = Depends(optional_user)):
    kind = str(payload.get("kind", "like"))
    if kind not in {"like", "fav"}:
        raise BizError(E_BAD_REQUEST, "kind 只能是 like 或 fav")
    w = db.get(Work, work_id)
    if not w:
        raise BizError(40401, "作品不存在")
    key = _user_key(user, request)
    exist = db.query(Interaction).filter_by(work_id=work_id, user_key=key, kind=kind).first()
    if exist:
        db.delete(exist)
        if kind == "like":
            w.like_count = max(0, (w.like_count or 0) - 1)
        else:
            w.fav_count = max(0, (w.fav_count or 0) - 1)
        active = False
    else:
        db.add(Interaction(work_id=work_id, user_key=key, kind=kind))
        if kind == "like":
            w.like_count = (w.like_count or 0) + 1
        else:
            w.fav_count = (w.fav_count or 0) + 1
        active = True
        if user:
            try:
                award_points(db, user, "like_others" if kind == "like" else "favorite",
                             1, ref_type="work", ref_id=f"{work_id}:{kind}")
            except BizError:
                pass
    db.commit()
    return ok({"active": active, "like_count": w.like_count, "fav_count": w.fav_count})


@router.post("/{work_id}/comments", summary="评论")
def add_comment(work_id: int, body: CommentIn, db: Session = Depends(get_session),
                user: User | None = Depends(optional_user)):
    w = db.get(Work, work_id)
    if not w:
        raise BizError(40401, "作品不存在")
    c = Comment(work_id=work_id, author=body.author, content=body.content,
                review_status="pending_expert")
    db.add(c)
    w.comment_count = (w.comment_count or 0) + 1
    db.flush()
    if user:
        try:
            award_points(db, user, "comment", 2, ref_type="comment", ref_id=c.id)
        except BizError:
            pass
    db.commit()
    return ok({"id": c.id, "created_at": datetime.now(timezone.utc).isoformat()})
