"""两个公开数据源的采集实现：仅取 API 返回值，不做整站爬取。"""

from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from typing import Any

from app.ingest.fetcher import Stats, fetch_json, should_abort

# ---------------------------------------------------------------------------
# 1) 中文维基百科 —— 事实性背景资料（CC BY-SA 4.0）
# ---------------------------------------------------------------------------
WIKI_API = "https://zh.wikipedia.org/w/api.php"
WIKI_TERMS = [
    "湘绣", "苏绣", "粤绣", "蜀绣", "刺绣", "中国传统工艺",
    "非物质文化遗产", "缂丝", "长沙", "云锦",
]


def _wiki_url(title: str) -> str:
    return "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(title)


def _clean(text: str) -> str:
    text = re.sub(r"\n{2,}", "\n", text or "").strip()
    return text


def _split_paragraphs(text: str, max_len: int = 700) -> list[str]:
    paras = [p.strip() for p in (text or "").split("\n") if len(p.strip()) > 30]
    out: list[str] = []
    for p in paras:
        if len(p) <= max_len:
            out.append(p)
        else:
            out.append(p[:max_len] + "…")
    return out[:3]


def harvest_wikipedia(terms: list[str] | None = None, limit_per_term: int = 2) -> tuple[list[dict], Stats]:
    stats = Stats()
    items: list[dict] = []
    for term in (terms or WIKI_TERMS):
        url = (
            f"{WIKI_API}?action=query&format=json&redirects=1&prop=extracts"
            f"&exintro=1&explaintext=1&titles={urllib.parse.quote(term)}"
        )
        if should_abort(stats):
            stats.aborted = "数据源连续不可达，已提前中止（可用本地种子数据运行）"
            break
        res = fetch_json(url, delay_sec=1.0, retries=0, timeout=8, stats=stats)
        if not res.ok or not res.data:
            continue
        pages = (res.data.get("query") or {}).get("pages") or {}
        for page in pages.values():
            if "missing" in page:
                continue
            title = page.get("title", term)
            paras = _split_paragraphs(_clean(page.get("extract", "")))
            if not paras:
                continue
            src = [{"title": f"中文维基百科 · {title}", "url": _wiki_url(title),
                    "license": "CC BY-SA 4.0"}]
            for i, para in enumerate(paras[:limit_per_term]):
                question = f"{title}是什么？" if i == 0 else f"关于{title}，还有哪些要点？"
                items.append(
                    {
                        "question": question,
                        "answer": para,
                        "category": "公开资料",
                        "tags": f"crawled wikipedia {title}",
                        "sources": src,
                        "review_status": "pending_expert",
                        "dedupe_key": hashlib.sha1(
                            f"wiki:{title}:{i}".encode("utf-8")
                        ).hexdigest(),
                    }
                )
    return items, stats


# ---------------------------------------------------------------------------
# 2) Wikimedia Commons —— CC / 公有领域图片素材（只存 URL 与许可）
# ---------------------------------------------------------------------------
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_TERMS = ["Xiang embroidery", "Hunan embroidery", "Chinese embroidery", "湘绣"]


def _strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(value).strip()[:200]


def harvest_commons(terms: list[str] | None = None, per_term: int = 8) -> tuple[list[dict], Stats]:
    stats = Stats()
    items: list[dict] = []
    for term in (terms or COMMONS_TERMS):
        q = urllib.parse.quote(f"filetype:bitmap {term}")
        url = (
            f"{COMMONS_API}?action=query&format=json&generator=search"
            f"&gsrnamespace=6&gsrlimit={per_term}&gsrsearch={q}"
            f"&prop=imageinfo&iiprop=url|extmetadata&iiurlwidth=800"
        )
        if should_abort(stats):
            stats.aborted = "数据源连续不可达，已提前中止（可用本地种子数据运行）"
            break
        res = fetch_json(url, delay_sec=1.0, retries=0, timeout=8, stats=stats)
        if not res.ok or not res.data:
            continue
        pages = (res.data.get("query") or {}).get("pages") or {}
        for page in pages.values():
            title = page.get("title", "")
            info = (page.get("imageinfo") or [{}])[0]
            thumb = info.get("thumburl") or info.get("url") or ""
            if not thumb:
                continue
            meta = info.get("extmetadata") or {}
            lic = _strip_tags((meta.get("LicenseShortName") or {}).get("value", ""))
            author = _strip_tags((meta.get("Artist") or {}).get("value", ""))
            desc = _strip_tags((meta.get("ImageDescription") or {}).get("value", ""))
            page_url = info.get("descriptionurl") or ""
            raw = f"{title}|{thumb}|{lic}|{author}|{desc}"
            items.append(
                {
                    "source": "commons",
                    "source_id": title[:180],
                    "term": term,
                    "title": title.replace("File:", ""),
                    "url": thumb,
                    "page_url": page_url,
                    "license": lic or "未标注",
                    "author": author or "未标注",
                    "description": desc,
                    "content_hash": hashlib.sha1(raw.encode("utf-8")).hexdigest(),
                }
            )
    return items, stats


# ---------------------------------------------------------------------------
# 2b) 克利夫兰艺术博物馆开放数据 —— CC0 授权的中国刺绣实物图片
#     开放访问 API，返回即带 share_license_status=CC0，可用于二次传播与演示。
# ---------------------------------------------------------------------------
CMA_API = "https://openaccess-api.clevelandart.org/api/artworks/"
CMA_TERMS = ["embroidery", "silk embroidery", "Chinese textile", "needlework"]


def harvest_cleveland(terms: list[str] | None = None, per_term: int = 8) -> tuple[list[dict], Stats]:
    stats = Stats()
    items: list[dict] = []
    for term in (terms or CMA_TERMS):
        url = (
            f"{CMA_API}?q={urllib.parse.quote(term)}&cc0=1&has_image=1"
            f"&limit={per_term}&skip=0"
        )
        if should_abort(stats):
            stats.aborted = "数据源连续不可达，已提前中止"
            break
        res = fetch_json(url, delay_sec=0.9, timeout=10, stats=stats)
        if not res.ok or not res.data:
            continue
        for art in (res.data.get("data") or []):
            img = ((art.get("images") or {}).get("web") or {}).get("url")
            if not img:
                continue
            culture = " / ".join(art.get("culture") or [])
            technique = art.get("technique") or ""
            desc_parts = [p for p in [
                f"年代：{art.get('creation_date', '未标注')}",
                f"产地/文化：{culture}" if culture else "",
                f"工艺/材质：{technique}" if technique else "",
                (art.get("description") or ""),
            ] if p]
            description = "；".join(desc_parts)
            raw = f"{art.get('id')}|{img}|{art.get('title')}|{description}"
            items.append(
                {
                    "source": "cleveland",
                    "source_id": f"cleveland:{art.get('id')}"[:180],
                    "term": term,
                    "title": art.get("title") or "未命名",
                    "url": img,
                    "page_url": art.get("url") or "",
                    "license": art.get("share_license_status") or "未标注",
                    "author": (art.get("creditline") or "The Cleveland Museum of Art")[:200],
                    "description": description,
                    "content_hash": hashlib.sha1(raw.encode("utf-8")).hexdigest(),
                }
            )
    return items, stats


def all_terms() -> dict[str, list[str]]:
    return {"wikipedia": WIKI_TERMS, "commons": COMMONS_TERMS, "cleveland": CMA_TERMS}


def as_dict(items: list[dict]) -> list[dict[str, Any]]:
    return items
