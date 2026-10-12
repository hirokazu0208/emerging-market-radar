"""SQLite への保存と読み出し。"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .sources import RawArticle

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "radar.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,           -- 情報源ID（例: hackernews）
    external_id  TEXT NOT NULL,           -- 情報源内の記事ID
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    summary      TEXT,                    -- 公開APIで取得できた要約・本文（任意）
    published_at TEXT NOT NULL,           -- ISO 8601（UTC）
    category     TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,           -- ISO 8601（UTC）
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at DESC);
"""


def db_path() -> Path:
    return Path(os.environ.get("RADAR_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def save_articles(source: str, articles: list[tuple[RawArticle, str]]) -> int:
    """(記事, カテゴリー) を保存し、新規に登録された件数を返す。既存IDは無視する。"""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (source, a.external_id, a.title, a.url, a.summary,
         a.published_at.astimezone(timezone.utc).isoformat(), category, fetched_at)
        for a, category in articles
    ]
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO articles
               (source, external_id, title, url, summary, published_at, category, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        return conn.total_changes - before


def list_articles(category: str | None = None, limit: int = 100) -> list[dict]:
    query = "SELECT * FROM articles"
    params: list = []
    if category:
        query += " WHERE category = ?"
        params.append(category)
    query += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params)]
