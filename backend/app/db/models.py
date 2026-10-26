"""数据模型：阶段2-3 目标的最小集（10 张业务表 + 2 张审计/日志表）。

字段设计遵循三条硬约束：
1. AI 合规标识：generation_images.visible_label / meta_json，缺一不可；
2. 积分流水：point_ledger 为 append-only，余额在事务内更新 users.points；
3. 专业守门：stitches / stitch_rules / knowledge_entries 均带 review_status。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ReviewMixin:
    """专家守门状态：所有对外可见的专业内容都必须经过确认。"""

    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending_expert", index=True
    )  # pending_expert | expert_confirmed
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(24), nullable=False, default="user"
    )  # user | reviewer | admin
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_free: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    avatar_seed: Mapped[str] = mapped_column(String(32), nullable=False, default="xiang")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Stitch(ReviewMixin, TimestampMixin, Base):
    """针法库：包含 SA（AI 承担的特征抽取）之后的展示内容。"""

    __tablename__ = "stitches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    family: Mapped[str] = mapped_column(String(48), nullable=False, default="未分类")
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 1-5
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    suitable_for: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    tips: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StitchRule(ReviewMixin, TimestampMixin, Base):
    """图案特征 → 针法 的确定性规则表。AI 只做特征识别，不发明针法。"""

    __tablename__ = "stitch_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feature_label: Mapped[str] = mapped_column(String(64), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list[str]
    stitch_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeEntry(ReviewMixin, TimestampMixin, Base):
    """知识库条目（RAG 语料）。每条带来源，回答可追溯。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False, default="综合", index=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    stitch_code: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Generation(TimestampMixin, Base):
    """一次生成任务：记录输入、状态、成本与合规信息。"""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="text")  # text|image
    source_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", index=True
    )  # queued | running | succeeded | failed
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="mock")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    features: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    stitch_suggestions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    images: Mapped[list["GenerationImage"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )


class GenerationImage(TimestampMixin, Base):
    """生成结果图：显式标识 + 隐式元数据缺一不可。"""

    __tablename__ = "generation_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(190), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible_label: Mapped[str] = mapped_column(String(190), nullable=False)
    meta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    generation: Mapped[Generation] = relationship(back_populates="images")


class Work(ReviewMixin, TimestampMixin, Base):
    """社区作品。"""

    __tablename__ = "works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cover: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stitch_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="handmade"
    )  # handmade | ai_assisted
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fav_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Interaction(Base):
    """点赞 / 收藏（同一用户对同一作品同一类型只允许一次）。"""

    __tablename__ = "interactions"
    __table_args__ = (UniqueConstraint("work_id", "user_key", "kind", name="uq_interaction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_key: Mapped[str] = mapped_column(String(64), nullable=False)  # 游客用设备 token
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # like | fav
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Comment(ReviewMixin, TimestampMixin, Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class PointLedger(Base):
    """积分流水：append-only，禁止 UPDATE / DELETE。"""

    __tablename__ = "point_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AuditLog(Base):
    """合规留痕：审核动作、后台操作、生成失败降级等。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class CrawledAsset(Base):
    """公开数据源采集到的图片素材：只存 URL 与许可信息，不落本地副本。"""

    __tablename__ = "crawled_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="commons")
    source_id: Mapped[str] = mapped_column(String(190), unique=True, nullable=False)
    term: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    page_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    license: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def set_json(obj: Any, field: str, value: Any) -> None:
    setattr(obj, field, _json(value))
