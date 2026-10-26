"""礼貌抓取器：robots.txt 校验 + 限速 + 退避重试。

合规底线：
- 每个站点首次访问前读取 robots.txt，被禁止的路径直接跳过；
- 声明 UA 与联系方式，串行请求、按站点限速；
- 只取元数据/API 返回值，不抓整页正文、不抓个人信息。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any

USER_AGENT = os.environ.get(
    "ZHENYU_UA",
    "ZhenyuBot/0.1 ( Xiang embroidery research demo; contact: dev@zhenyu.example )",
)

_SITE_DELAY: dict[str, float] = {}
_LAST_HIT: dict[str, float] = {}
_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}


@dataclass
class FetchResult:
    ok: bool
    status: int = 0
    data: Any = None
    text: str = ""
    error: str = ""


@dataclass
class Stats:
    fetched: int = 0
    failed: int = 0
    skipped_robots: int = 0
    consecutive_failures: int = 0
    aborted: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "failed": self.failed,
            "skipped_by_robots": self.skipped_robots,
            "consecutive_failures": self.consecutive_failures,
            "aborted_reason": self.aborted,
            "errors": self.errors[:5],
        }

    def note_success(self) -> None:
        self.fetched += 1
        self.consecutive_failures = 0

    def note_failure(self, reason: str) -> None:
        self.failed += 1
        self.consecutive_failures += 1
        if len(self.errors) < 8:
            self.errors.append(reason)


def _origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def robots_of(url: str) -> urllib.robotparser.RobotFileParser | None:
    """读取并缓存 robots.txt；不可读时按「不禁止」处理（仅对 API 端点适用）。"""
    origin = _origin(url)
    if origin in _ROBOTS:
        return _ROBOTS[origin]
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(origin + "/robots.txt")
    try:
        req = urllib.request.Request(origin + "/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as r:
            rp.parse(r.read().decode("utf-8", "ignore").splitlines())
    except Exception:  # noqa: BLE001 - 取不到 robots 时放行，由限速与配额兜底
        rp = None
    _ROBOTS[origin] = rp  # type: ignore[assignment]
    return rp


def allowed(url: str) -> bool:
    rp = robots_of(url)
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def wait_turn(url: str, delay_sec: float) -> None:
    """同站点串行限速：避免对公开 API 造成压力。"""
    origin = _origin(url)
    last = _LAST_HIT.get(origin, 0.0)
    pending = delay_sec - (time.time() - last)
    if pending > 0:
        time.sleep(pending)
    _LAST_HIT[origin] = time.time()


def fetch_json(url: str, *, delay_sec: float = 1.2, retries: int = 1,
               timeout: int = 10, stats: Stats | None = None) -> FetchResult:
    if not allowed(url):
        if stats:
            stats.skipped_robots += 1
        return FetchResult(False, error="robots.txt 禁止抓取")
    for attempt in range(retries + 1):
        wait_turn(url, delay_sec)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
            if stats:
                stats.note_success()
            return FetchResult(True, status=r.status, data=json.loads(raw), text=raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries:
                time.sleep(2 ** attempt * 2)
                continue
            if stats:
                stats.note_failure(f"HTTP {exc.code} {url[:70]}")
            return FetchResult(False, status=exc.code, error=f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if stats:
                stats.note_failure(f"{type(exc).__name__} {url[:70]}")
            return FetchResult(False, error=f"{type(exc).__name__}: {exc}")
    return FetchResult(False, error="重试耗尽")


def should_abort(stats: Stats, threshold: int = 2) -> bool:
    """熔断：同一数据源连续失败达到阈值即提前放弃，避免任务被不可达域名拖死。"""
    return stats.consecutive_failures >= threshold
