"""采集流水线：批次内去重 → 内容哈希比对 → 增量 upsert。

去重三级：
  L1 批内去重：同 dedupe_key（或 source_id）只保留信息量更大的一条；
  L2 跨批去重：唯一键约束，同一来源条目不会重复入库；
  L3 增量判断：content_hash 未变则不写库，记为 unchanged，避免无意义写入。
"""

from __future__ import annotations

import json
from typing import Callable

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models import CrawledAsset, KnowledgeEntry


def _dedupe_batch(items: list[dict], key_of: Callable[[dict], str]) -> tuple[list[dict], int]:
    seen: dict[str, dict] = {}
    dup = 0
    for item in items:
        k = key_of(item)
        if not k:
            continue
        if k in seen:
            # 保留内容更完整（字段更多 / 文本更长）的一条
            cur = seen[k]
            if sum(len(str(v)) for v in item.values()) > sum(len(str(v)) for v in cur.values()):
                seen[k] = item
            dup += 1
        else:
            seen[k] = item
    return list(seen.values()), dup


def upsert_knowledge(session: Session, items: list[dict]) -> dict:
    items, dup = _dedupe_batch(items, lambda i: str(i.get("dedupe_key") or i.get("question", "")))
    inserted = updated = unchanged = 0
    for item in items:
        row = session.query(KnowledgeEntry).filter(
            KnowledgeEntry.question == item["question"]
        ).first()
        payload = {
            "answer": item["answer"],
            "category": item.get("category", "公开资料"),
            "tags": item.get("tags", ""),
            "sources": json.dumps(item.get("sources", []), ensure_ascii=False),
            "stitch_code": item.get("stitch_code"),
            "review_status": item.get("review_status", "pending_expert"),
        }
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if row is None:
            session.add(KnowledgeEntry(question=item["question"], **payload))
            inserted += 1
        elif json.dumps(
            {
                "answer": row.answer, "category": row.category, "tags": row.tags,
                "sources": row.sources, "review_status": row.review_status,
            },
            ensure_ascii=False, sort_keys=True,
        ) != fingerprint:
            for k, v in payload.items():
                setattr(row, k, v)
            updated += 1
        else:
            unchanged += 1
    session.commit()
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged,
            "dup_in_batch": dup, "received": len(items) + dup}


def upsert_assets(session: Session, items: list[dict]) -> dict:
    items, dup = _dedupe_batch(items, lambda i: str(i.get("source_id", "")))
    inserted = updated = unchanged = 0
    for item in items:
        row = session.query(CrawledAsset).filter(
            CrawledAsset.source_id == item["source_id"]
        ).first()
        if row is None:
            session.add(CrawledAsset(**item))
            inserted += 1
        elif row.content_hash != item["content_hash"]:
            for k in ("url", "title", "license", "author", "description",
                      "page_url", "term", "content_hash"):
                setattr(row, k, item.get(k))
            updated += 1
        else:
            unchanged += 1
    session.commit()
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged,
            "dup_in_batch": dup, "received": len(items) + dup}


def table_stats(session: Session) -> dict:
    counts: dict[str, int] = {}
    for model in (KnowledgeEntry, CrawledAsset):
        mapper = inspect(model)
        counts[mapper.local_table.name] = int(session.query(model).count())  # type: ignore[arg-type]
    return counts
