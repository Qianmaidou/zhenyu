"""SQLAlchemy 引擎、会话与建/删表工具。"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATA_DIR, settings

DATA_DIR.mkdir(parents=True, exist_ok=True)

_IS_SQLITE = settings.db_url.startswith("sqlite")

_engine_kwargs = {}
if _IS_SQLITE:
    _engine_kwargs.update({"connect_args": {"check_same_thread": False}})

engine: Engine = create_engine(settings.db_url, future=True, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """声明式基类。"""


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - sqlite only
    if _IS_SQLITE:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SQLite FTS5：知识库全文检索（不可用时自动降级为 LIKE）
# ---------------------------------------------------------------------------
FTS_AVAILABLE = False


def ensure_fts(conn: sqlite3.Connection) -> bool:
    """创建知识库全文索引与同步触发器；不支持 FTS5 时返回 False。"""
    global FTS_AVAILABLE
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
            USING fts5(question, answer, tags, content='knowledge_entries', content_rowid='id')
            """
        )
        conn.execute(
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')"
        )
        for sql in (
            """CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_entries BEGIN
                 INSERT INTO knowledge_fts(rowid, question, answer, tags)
                 VALUES (new.id, new.question, new.answer, new.tags); END""",
            """CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_entries BEGIN
                 INSERT INTO knowledge_fts(knowledge_fts, rowid, question, answer, tags)
                 VALUES('delete', old.id, old.question, old.answer, old.tags); END""",
            """CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_entries BEGIN
                 INSERT INTO knowledge_fts(knowledge_fts, rowid, question, answer, tags)
                 VALUES('delete', old.id, old.question, old.answer, old.tags);
                 INSERT INTO knowledge_fts(rowid, question, answer, tags)
                 VALUES (new.id, new.question, new.answer, new.tags); END""",
        ):
            conn.execute(sql)
        conn.commit()
        FTS_AVAILABLE = True
    except Exception:  # noqa: BLE001 - 降级到 LIKE 检索
        FTS_AVAILABLE = False
    return FTS_AVAILABLE


def init_db(create_fts: bool = True) -> None:
    """建表（幂等）。"""
    from app.db import models  # noqa: F401 - 确保模型注册

    Base.metadata.create_all(bind=engine)
    if _IS_SQLITE and create_fts:
        raw = engine.raw_connection()
        try:
            ensure_fts(raw.driver_connection)
        finally:
            raw.close()


def drop_db() -> None:
    Base.metadata.drop_all(bind=engine)
