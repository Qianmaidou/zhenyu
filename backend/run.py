#!/usr/bin/env python
"""针语平台运维入口：初始化 / 种子 / 采集 / 启动服务。

    python run.py init                  # 建表 + 装载本地种子数据
    python run.py crawl --source all    # 采集公开数据（维基百科 + Wikimedia Commons）
    python run.py serve --port 8130     # 启动服务
    python run.py all                   # 一键完成以上三步
    python run.py check                 # 数据与合规自检
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 保证以脚本方式运行时也能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _banner(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def cmd_init(args: argparse.Namespace) -> int:
    _banner("初始化数据库")
    from app.config import settings
    from app.db.base import init_db
    from app.db.seed import seed_all

    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    counts = seed_all()
    print("种子装载:", json.dumps(counts, ensure_ascii=False))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    _banner("重建数据库")
    from app.db.seed import reset_all

    print(json.dumps(reset_all(confirm=True), ensure_ascii=False))
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    _banner("装载种子数据（幂等）")
    from app.db.seed import seed_all

    print(json.dumps(seed_all(), ensure_ascii=False))
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    _banner("采集公开数据")
    from app.db.base import SessionLocal, engine, ensure_fts
    from app.ingest import sources
    from app.ingest.pipeline import table_stats, upsert_assets, upsert_knowledge

    session = SessionLocal()
    report: dict = {}
    try:
        if args.source in ("wiki", "all"):
            items, fetch_stats = sources.harvest_wikipedia()
            result = upsert_knowledge(session, items)
            report["wikipedia"] = {"fetched": fetch_stats.as_dict(), "result": result}
        if args.source in ("commons", "all"):
            items, fetch_stats = sources.harvest_commons(per_term=args.limit)
            result = upsert_assets(session, items)
            report["commons"] = {"fetched": fetch_stats.as_dict(), "result": result}
        if args.source in ("cleveland", "all"):
            items, fetch_stats = sources.harvest_cleveland(per_term=args.limit)
            result = upsert_assets(session, items)
            report["cleveland"] = {"fetched": fetch_stats.as_dict(), "result": result}
        report["tables"] = table_stats(session)
    finally:
        session.close()
    try:
        raw = engine.raw_connection()
        try:
            ensure_fts(raw.driver_connection)
        finally:
            raw.close()
    except Exception:  # noqa: BLE001
        pass

    print(json.dumps(report, ensure_ascii=False, indent=2))
    total_new = sum(v.get("result", {}).get("inserted", 0) for v in report.values()
                    if isinstance(v, dict) and "result" in v)
    print(f"\n本次新增 {total_new} 条；若接口不可用，请用 python run.py seed 载入本地种子数据。")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    _banner("数据与合规自检")
    from app.config import settings
    from app.db.base import SessionLocal
    from app.db.models import KnowledgeEntry, Stitch, StitchRule, Work
    from app.services.watermark import read_implicit_meta

    session = SessionLocal()
    stats = {
        "stitches": session.query(Stitch).count(),
        "rules": session.query(StitchRule).count(),
        "knowledge": session.query(KnowledgeEntry).count(),
        "pending_knowledge": session.query(KnowledgeEntry).filter(
            KnowledgeEntry.review_status == "pending_expert").count(),
        "works": session.query(Work).count(),
    }
    session.close()

    missing = []
    checked = 0
    for png in Path(settings.storage_dir).rglob("*.png"):
        meta = read_implicit_meta(png)
        checked += 1
        if "AI-Provider-Code" not in meta or "AI-Record-ID" not in meta:
            missing.append(str(png))
    stats["images_checked"] = checked
    stats["images_missing_implicit_label"] = missing[:5]
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    ok = not missing and stats["knowledge"] > 0
    print("结论:", "通过" if ok else "存在问题，见上")
    return 0 if ok else 1


def cmd_all(args: argparse.Namespace) -> int:
    cmd_init(args)
    try:
        cmd_crawl(argparse.Namespace(source="all", limit=args.limit))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 采集失败（将以内种子数据运行）: {exc}")
    print("\n服务即将启动，按 Ctrl+C 结束\n")
    return cmd_serve(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="针语平台运维入口")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8130)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--log-level", default="info")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=8130)
        sp.add_argument("--reload", action="store_true")
        sp.add_argument("--log-level", default="info")
        sp.add_argument("--source", default="all",
                        choices=["all", "wiki", "commons", "cleveland"])
        sp.add_argument("--limit", type=int, default=8, help="每个采集词的样本数")

    subs: dict[str, argparse.ArgumentParser] = {}
    for name, help_text in [
        ("init", "建表并装载种子数据"),
        ("reset", "重建数据库（清空后重来）"),
        ("seed", "幂等装载种子数据"),
        ("crawl", "采集公开数据"),
        ("serve", "启动服务"),
        ("check", "数据与合规自检"),
        ("all", "init + crawl + serve"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        _common(sp)
        subs[name] = sp
    return p


def main() -> int:
    args = build_parser().parse_args()
    return {
        "init": cmd_init, "reset": cmd_reset, "seed": cmd_seed,
        "crawl": cmd_crawl, "serve": cmd_serve, "check": cmd_check, "all": cmd_all,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
