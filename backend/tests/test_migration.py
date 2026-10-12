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


@pytest.fixture
def v2_db(tmp_path, monkeypatch):
    """v0.2 時点の DB（user_version = 2）。HN 1件（市場未判定）と e-Gov 1件（JP）を持つ。"""
    path = tmp_path / "radar.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.BASE_SCHEMA)
    conn.executescript(f"BEGIN;\n{db.MIGRATIONS[2]}\nPRAGMA user_version = 2;\nCOMMIT;")
    conn.executescript("""
        INSERT INTO sources (id, label, country, kind, language, homepage, license, attribution, updated_at) VALUES
          ('hackernews', 'Hacker News', 'US', 'tech_news', 'en', 'https://news.ycombinator.com/', NULL, NULL, 't'),
          ('egov_pcm', 'e-Gov パブリック・コメント', 'JP', 'policy', 'ja', 'https://public-comment.e-gov.go.jp/', 'PDL1.0', 'x', 't');
        INSERT INTO articles (id, source, external_id, title, url, summary, published_at, category, fetched_at, language, metadata) VALUES
          (1, 'hackernews', '42', 'Show HN: Something', 'https://example.com', NULL,
           '2026-10-12T01:00:00+00:00', 'Developer Tools', '2026-10-12T01:05:00+00:00', 'en', NULL),
          (2, 'egov_pcm', '495260216', '省令案に関する御意見の募集について', 'https://public-comment.e-gov.go.jp/x', '概要',
           '2026-10-09T15:00:00+00:00', 'Policy & Regulation', '2026-10-12T01:05:00+00:00', 'ja', '{"deadline": "2026/11/09 09:00"}');
        INSERT INTO article_markets (article_id, market, method, created_at) VALUES (2, 'JP', 'source_default', 't');
    """)
    conn.close()
    monkeypatch.setenv("RADAR_DB_PATH", str(path))
    return path


def test_migrates_v2_db_to_v3_without_losing_data(v2_db):
    before = sqlite3.connect(v2_db)
    articles_before = before.execute("SELECT * FROM articles ORDER BY id").fetchall()
    markets_before = before.execute("SELECT * FROM article_markets").fetchall()
    before.close()

    db.init_db(SOURCES)

    conn = sqlite3.connect(v2_db)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # 記事・市場ラベルはそのまま（HN に市場を推測して付けない）
    assert [tuple(r) for r in conn.execute("SELECT * FROM articles ORDER BY id")] == articles_before
    assert [tuple(r) for r in conn.execute("SELECT * FROM article_markets")] == markets_before
    # 記事ラベルは自動では付けない
    assert conn.execute("SELECT COUNT(*) FROM article_labels").fetchone()[0] == 0
    # 利用条件：e-Gov は確認済み（PDL1.0）、HN は未確認
    terms = {r["id"]: dict(r) for r in conn.execute("SELECT id, terms_url, commercial_use, terms_checked_on FROM sources")}
    assert terms["egov_pcm"] == {
        "id": "egov_pcm", "terms_url": "https://public-comment.e-gov.go.jp/contents/terms",
        "commercial_use": "allowed", "terms_checked_on": "2026-10-12",
    }
    assert terms["hackernews"] == {
        "id": "hackernews", "terms_url": None, "commercial_use": "unknown", "terms_checked_on": None,
    }
    conn.close()


def test_new_source_rows_default_to_unknown_commercial_use(v2_db):
    db.init_db(SOURCES)
    conn = sqlite3.connect(v2_db)
    conn.execute(
        "INSERT INTO sources (id, label, country, kind, language, homepage, updated_at) "
        "VALUES ('other', 'Other', 'JP', 'press_release', 'ja', 'https://example.com', 't')"
    )
    assert conn.execute("SELECT commercial_use FROM sources WHERE id = 'other'").fetchone()[0] == "unknown"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE sources SET commercial_use = 'probably' WHERE id = 'other'")
    conn.close()


def test_market_method_is_constrained(v01_db):
    db.init_db(SOURCES)
    conn = sqlite3.connect(v01_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO article_markets (article_id, market, method, created_at) VALUES (1, 'JP', 'guess', 'now')"
        )
    conn.close()


class _Src:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


@pytest.mark.parametrize(
    "attrs, expected",
    [
        ({}, "unknown"),  # 未指定
        ({"commercial_use": "probably"}, "unknown"),  # 不正な値
        ({"commercial_use": "allowed"}, "unknown"),  # 利用条件のURL・確認日が無い
        ({"commercial_use": "allowed", "terms_url": "https://example.com/terms"}, "unknown"),
        ({"commercial_use": "allowed", "terms_url": "https://example.com/terms", "terms_checked_on": "2026-10-12"}, "allowed"),
        ({"commercial_use": "restricted"}, "restricted"),
        ({"commercial_use": "unknown"}, "unknown"),
    ],
)
def test_commercial_use_defaults_to_unknown_unless_confirmed(attrs, expected):
    assert db.commercial_use_of(_Src(**attrs)) == expected


def test_source_without_terms_attributes_is_registered_as_unknown(v2_db):
    minimal = _Src(name="new_source", label="New", country="JP", kind="press_release", language="ja",
                   default_market=None, homepage="https://example.com", license=None, attribution=None)
    db.init_db([*SOURCES, minimal])
    conn = sqlite3.connect(v2_db)
    row = conn.execute("SELECT commercial_use, terms_url, terms_checked_on FROM sources WHERE id = 'new_source'").fetchone()
    assert row == ("unknown", None, None)
    conn.close()
