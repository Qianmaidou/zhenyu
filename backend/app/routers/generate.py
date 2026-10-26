"""AI 湘绣成品图生成 + 针法推荐（核心闭环）。"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from PIL import Image
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    Generation,
    GenerationImage,
    User,
)
from app.deps import current_user, device_key, get_session, optional_user
from app.errors import BizError, E_BAD_REQUEST, E_DAILY_BUDGET, E_LIMITED, ok
from app.schemas import GenerateIn, GenerationOut
from app.services import image_gen, watermark
from app.services.image_gen import daily_cost
from app.services.points import award_points, spend_points, write_audit
from app.services.stitch_rules import recommend

router = APIRouter(prefix="/generate", tags=["AI 生成"])

_HOUR_WINDOW = 3600


def _usage_count(db: Session, user: User | None, key: str) -> int:
    since_ts = time.time() - _HOUR_WINDOW
    q = db.query(Generation)
    q = q.filter(Generation.client_ip == key) if user is None else q.filter(
        Generation.user_id == user.id
    )
    return int(q.filter(Generation.created_at >= datetime.fromtimestamp(
        since_ts, timezone.utc)).count())


def _build_meta(g: Generation, codes: list[str], provider: str, model: str, sim: bool):
    return watermark.implicit_meta(
        record_id=g.public_id,
        provider=provider,
        model=model,
        prompt=g.prompt,
        is_simulation=sim,
        stitch_codes=codes,
    )


def _serialize(g: Generation) -> dict:
    images = []
    for img in sorted(g.images, key=lambda x: x.sort_order):
        try:
            meta = json.loads(img.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        images.append(
            {
                "id": img.id,
                "file_name": img.file_name,
                "url": f"/files/{g.public_id}/{img.file_name}",
                "width": img.width,
                "height": img.height,
                "visible_label": img.visible_label,
                "meta": meta,
            }
        )
    try:
        feats = json.loads(g.features or "[]")
    except json.JSONDecodeError:
        feats = []
    try:
        sugg = json.loads(g.stitch_suggestions or "[]")
    except json.JSONDecodeError:
        sugg = []
    return {
        "public_id": g.public_id,
        "status": g.status,
        "prompt": g.prompt,
        "provider": g.provider,
        "is_simulation": g.provider in {"mock", "api-fallback"},
        "latency_ms": g.latency_ms,
        "cost_estimate": round(g.cost_estimate, 4),
        "features": feats,
        "suggestions": sugg,
        "images": images,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "error_message": g.error_message,
    }


@router.get("/quota", summary="可用额度与成本概览")
def quota(request: Request, user: User | None = Depends(optional_user),
          db: Session = Depends(get_session)):
    key = str(user.id) if user else device_key(request)
    used = _usage_count(db, user, key)
    left_quota = None
    if user:
        left_quota = user.quota_free
    else:
        left_quota = max(0, 3 - used)
    return ok(
        {
            "used_last_hour": used,
            "hour_limit": settings.rate_limit_generate_per_hour,
            "quota_left": left_quota,
            "daily_cost": round(daily_cost(db), 2),
            "daily_budget_limit": settings.daily_budget_limit,
            "provider": settings.image_provider,
        }
    )


@router.post("", summary="创建生成任务（同步出图，MVP 不做队列）")
async def create_generation(
    body: GenerateIn,
    request: Request,
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_session),
):
    key = str(user.id) if user else device_key(request)
    if _usage_count(db, user, key) >= settings.rate_limit_generate_per_hour:
        raise BizError(E_LIMITED, "生成次数超出限制，请稍后再试")
    if daily_cost(db) >= settings.daily_budget_limit:
        raise BizError(E_DAILY_BUDGET)

    # 先算针法建议：无论出图成功与否，用户都能得到专业侧反馈
    rec = recommend(db, body.prompt)
    codes = [s["stitch_code"] for s in rec.get("suggestions", [])]

    g = Generation(
        public_id="g" + secrets.token_hex(8),
        user_id=user.id if user else None,
        prompt=body.prompt,
        negative_prompt=body.negative_prompt,
        source_kind=body.source_kind,
        status="running",
        provider=settings.image_provider,
        features=json.dumps(rec.get("features", []), ensure_ascii=False),
        stitch_suggestions=json.dumps(rec.get("suggestions", []), ensure_ascii=False),
        client_ip=key[:64],
    )
    db.add(g)
    db.flush()

    t0 = time.perf_counter()
    try:
        result = await image_gen.generate_image(body.prompt, body.negative_prompt)
    except Exception as exc:  # noqa: BLE001
        g.status = "failed"
        g.error_message = str(exc)[:300]
        g.latency_ms = int((time.perf_counter() - t0) * 1000)
        g.finished_at = datetime.now(timezone.utc)
        db.commit()
        write_audit(db, "generation_failed", target_type="generation",
                    target_id=g.public_id, detail=str(exc)[:300])
        db.commit()
        raise BizError(50201, "生成失败，请稍后重试")

    g.status = "succeeded"
    g.provider = result.provider
    g.latency_ms = int((time.perf_counter() - t0) * 1000)
    g.cost_estimate = result.cost
    g.finished_at = datetime.now(timezone.utc)

    # ---- 合规：显式标识 + 隐式元数据，缺一不可 ----
    labeled = watermark.add_visible_label(result.image, settings.ai_visible_label, g.public_id)
    meta = _build_meta(g, codes, result.provider, result.model, result.is_simulation)
    dest_dir = settings.storage_path / g.public_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_name = "result.png"
    dest = dest_dir / file_name
    watermark.save_with_metadata(labeled, dest, meta)
    watermark.assert_compliant(dest)

    with Image.open(dest) as im:
        w, h = im.size
    db.add(
        GenerationImage(
            generation_id=g.id,
            file_name=file_name,
            width=w,
            height=h,
            visible_label=settings.ai_visible_label,
            meta_json=json.dumps(meta, ensure_ascii=False),
            sort_order=0,
        )
    )

    # 登录用户：消耗额度/积分；产出内容给少量返还，形成闭环雏形
    if user:
        if user.quota_free > 0:
            user.quota_free -= 1
        else:
            try:
                spend_points(db, user, "redeem_quota", 10, ref_type="generation", ref_id=g.public_id)
            except BizError:
                raise BizError(E_BAD_REQUEST, "免费额度已用完，且积分不足以继续生成")
        try:
            award_points(db, user, "generate", 2, ref_type="generation", ref_id=g.public_id)
        except BizError:
            pass
    db.commit()
    db.refresh(g)
    return ok(_serialize(g))


@router.get("/{public_id}", summary="查询生成结果")
def get_generation(public_id: str, db: Session = Depends(get_session)):
    g = db.query(Generation).filter(Generation.public_id == public_id).first()
    if not g:
        raise BizError(40401, "生成记录不存在")
    return ok(_serialize(g))


@router.get("", summary="我的生成历史")
def my_generations(
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
    limit: int = 20,
):
    rows = (
        db.query(Generation)
        .filter(Generation.user_id == user.id)
        .order_by(Generation.id.desc())
        .limit(min(limit, 60))
        .all()
    )
    return ok({"items": [_serialize(r) for r in rows]})
