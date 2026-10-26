"""Pydantic 出入参契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------- 账号 ----------------
class RegisterIn(BaseModel):
    email: EmailStr
    nickname: str = Field(min_length=1, max_length=24)
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    id: int
    email: str
    nickname: str
    role: str
    points: int
    quota_free: int
    avatar_seed: str

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    token: str
    expires_in: int
    user: UserOut


# ---------------- 生成 ----------------
class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=600)
    negative_prompt: str = Field(default="", max_length=400)
    source_kind: str = Field(default="text", pattern="^(text|image)$")

    @field_validator("prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("描述不能为空")
        return v


class ImageOut(BaseModel):
    id: int
    file_name: str
    url: str
    width: int
    height: int
    visible_label: str
    meta: dict[str, Any] = {}


class SuggestionOut(BaseModel):
    stitch_code: str
    name: str
    score: float
    rationale: str
    review_status: str
    matched_features: list[str] = []
    family: str = ""


class GenerationOut(BaseModel):
    public_id: str
    status: str
    prompt: str
    provider: str
    is_simulation: bool
    latency_ms: int
    cost_estimate: float
    features: list[str]
    suggestions: list[dict[str, Any]]
    images: list[ImageOut]
    created_at: Optional[datetime] = None
    error_message: Optional[str] = None


# ---------------- 针法 ----------------
class StitchOut(BaseModel):
    id: int
    code: str
    name: str
    alias: Optional[str] = None
    family: str
    difficulty: int
    summary: str
    suitable_for: str
    steps: list[str] = []
    tips: str = ""
    sources: list[dict[str, Any]] = []
    review_status: str
    view_count: int

    class Config:
        from_attributes = True


# ---------------- 知识 ----------------
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=4, ge=1, le=10)


class KnowledgeOut(BaseModel):
    id: int
    question: str
    answer: str
    category: str
    tags: str = ""
    sources: list[dict[str, Any]] = []
    review_status: str


# ---------------- 社区 ----------------
class WorkIn(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=1000)
    cover: str = Field(default="", max_length=255)
    tags: str = Field(default="", max_length=120)
    stitch_code: Optional[str] = None
    source_type: str = Field(default="handmade", pattern="^(handmade|ai_assisted)$")


class CommentIn(BaseModel):
    author: str = Field(default="匿名绣友", max_length=32)
    content: str = Field(min_length=1, max_length=300)


class WorkOut(BaseModel):
    id: int
    title: str
    description: str
    cover: str
    tags: str
    stitch_code: Optional[str] = None
    is_seed: bool
    source_type: str
    like_count: int
    fav_count: int
    comment_count: int
    view_count: int
    review_status: str
    nickname: Optional[str] = None
    liked: bool = False
    faved: bool = False

    class Config:
        from_attributes = True
