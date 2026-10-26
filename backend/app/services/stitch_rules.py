"""图案特征 → 针法 的确定性规则引擎。

硬约束落地：**AI 只负责把用户描述映射成特征，不负责发明针法**。
匹配结果全部来自 stitch_rules 表（规则表由湘绣专家/文献来源维护），
每条建议都会带上 review_status，未经专家确认的内容一律标注「待湘绣顾问确认」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import Stitch, StitchRule


@dataclass
class Suggestion:
    stitch_code: str
    name: str
    score: float
    rationale: str
    review_status: str
    matched_features: list[str] = field(default_factory=list)
    family: str = ""


KEYWORD_SYNONYMS = {
    # 口语化描述 → 特征词，提升召回
    "绒毛": "毛发质感", "毛茸茸": "毛发质感", "毛感": "毛发质感",
    "蓬松": "毛发质感", "丝毛": "毛发质感",
    "过渡": "色彩渐变", "晕色": "色彩渐变", "渐变": "色彩渐变", "层次": "色彩渐变",
    "轮廓": "轮廓线条", "线条": "轮廓线条", "勾边": "轮廓线条", "描线": "轮廓线条",
    "花瓣": "花叶造型", "叶子": "花叶造型", "花草": "花叶造型",
    "平整": "大色块", "纯色": "大色块", "底色": "大色块",
    "眼睛": "传神细节", "瞳孔": "传神细节", "神态": "传神细节",
    "颗粒": "点状肌理", "花心": "点状肌理", "蕊": "点状肌理",
    "通透": "轻薄通透", "薄纱": "轻薄通透", "纱": "轻薄通透",
}


def normalize_text(text: str) -> str:
    """把口语化描述归一到特征词，缩小规则表要覆盖的表面积。"""
    out = text
    for k, v in KEYWORD_SYNONYMS.items():
        if k in out and v not in out:
            out += f"，{v}"
    return out


def extract_features(session: Session, prompt: str) -> list[tuple[StitchRule, str]]:
    """返回命中的 (规则, 特征标签)。同特征只取权重最高的一条规则。"""
    text = normalize_text(prompt or "")
    rules = session.query(StitchRule).all()
    best_by_feature: dict[str, StitchRule] = {}
    for rule in rules:
        try:
            kws = json.loads(rule.keywords or "[]")
        except json.JSONDecodeError:
            kws = [k.strip() for k in (rule.keywords or "").split(",") if k.strip()]
        hit = sum(1 for k in kws if k and k in text)
        if hit:
            cur = best_by_feature.get(rule.feature_key)
            if cur is None or rule.weight > cur.weight:
                best_by_feature[rule.feature_key] = rule
    # 权重降序，保证建议顺序稳定
    items = sorted(best_by_feature.values(), key=lambda r: -r.weight)
    return [(r, r.feature_label) for r in items]


def recommend(
    session: Session, prompt: str, top_n: int = 2
) -> dict:
    features = extract_features(session, prompt)
    if not features:
        return {"features": [], "suggestions": [], "fallback": True}

    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}
    status: dict[str, str] = {}
    for rule, label in features:
        scores[rule.stitch_code] = scores.get(rule.stitch_code, 0.0) + rule.weight
        reasons.setdefault(rule.stitch_code, []).append(f"{label}→{rule.rationale}")
        # 一条建议里只要有一条规则未确认，整体即为待确认（保守策略）
        if status.get(rule.stitch_code) != "pending_expert":
            status[rule.stitch_code] = rule.review_status

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:top_n]
    total = sum(scores.values()) or 1.0
    stitches = {s.code: s for s in session.query(Stitch).all()}

    suggestions: list[Suggestion] = []
    for code, score in ranked:
        st = stitches.get(code)
        if not st:
            continue
        suggestions.append(
            Suggestion(
                stitch_code=code,
                name=st.name,
                score=round(score / total, 4),
                rationale="；".join(reasons.get(code, [])[:2]),
                review_status=status.get(code, st.review_status),
                matched_features=[lbl for r, lbl in features if r.stitch_code == code],
                family=st.family,
            )
        )
    return {
        "features": [lbl for _, lbl in features],
        "suggestions": [s.__dict__ for s in suggestions],
        "fallback": False,
    }


TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]{2,}")


def quick_tokens(text: str) -> list[str]:
    return list({t for t in TOKEN_RE.findall(text or "")})[:20]
