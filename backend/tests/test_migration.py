"""v0.1 の SQLite DB を v0.2 スキーマへ移行しても既存データが失われないことを確認する。"""

import sqlite3

import pytest

from app import db
from app.sources import EgovPublicCommentSource, HackerNewsSource

# v0.1 時点のスキーマ（db.BASE_SCHEMA とは独立に固定しておく）
V01_SCHEMA = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
    summary TEXT, published_at TEXT NOT NULL, category TEXT NOT NULL, fetched_at TEXT NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX idx_articles_published_at ON articles (published_at DESC);
"""

SOURCES = [HackerNewsSource(), EgovPublicCommentSource()]


@pytest.fixture
def v01_db(tmp_path, monkeypatch):
    path = tmp_path / "radar.db"
    conn = sqlite3.connect(path)
    conn.executescript(V01_SCHEMA)
    conn.execute(
        "INSERT INTO articles (source, external_id, title, url, summary, published_at, category, fetched_at) "
        "VALUES ('hackernews', '42', 'Show HN: Something', 'https://example.com', 'text', "
        "'2026-10-12T01:00:00+00:00', 'Developer Tools', '2026-10-12T01:05:00+00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("RADAR_DB_PATH", str(path))
    return path


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migrates_v01_db_without_losing_data(v01_db):
    db.init_db(SOURCES)

    conn = sqlite3.connect(v01_db)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {"language", "metadata"} <= columns(conn, "articles")
    assert {"country", "license", "attribution"} <= columns(conn, "sources")
    assert {"market", "method"} <= columns(conn, "article_markets")

    row = dict(conn.execute("SELECT * FROM articles").fetchone())
    assert row["title"] == "Show HN: Something"
    assert row["summary"] == "text"
    assert row["fetched_at"] == "2026-10-12T01:05:00+00:00"  # 取得日時は保持
    assert row["language"] == "en"  # 情報源の定義から補完
    # HN は対象市場を判定できないため、市場は付けない（未判定）
    assert conn.execute("SELECT COUNT(*) FROM article_markets").fetchone()[0] == 0
    assert {r["id"]: r["country"] for r in conn.execute("SELECT * FROM sources")} == {
        "hackernews": "US", "egov_pcm": "JP",
    }
    conn.close()


def test_migration_is_idempotent(v01_db):
    db.init_db(SOURCES)
    db.init_db(SOURCES)  # 2回目の起動でもエラーにならず、データも重複しない
    conn = sqlite3.connect(v01_db)
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
    conn.close()


def test_fresh_db_gets_latest_schema(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    monkeypatch.setenv("RADAR_DB_PATH", str(path))
    db.init_db(SOURCES)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "language" in columns(conn, "articles")
    conn.close()


def test_market_method_is_constrained(v01_db):
    db.init_db(SOURCES)
    conn = sqlite3.connect(v01_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO article_markets (article_id, market, method, created_at) VALUES (1, 'JP', 'guess', 'now')"
        )
    conn.close()
