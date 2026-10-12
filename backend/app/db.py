"""SQLite への保存と読み出し。"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .sources import RawArticle, Source

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "radar.db"
HOME_MARKET = "JP"  # 画面の「日本」に対応する市場コード

# v0.1 のスキーマ。既存DBとの互換のため変更せず、差分は MIGRATIONS で適用する。
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,           -- 情報源ID（sources.id）
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

# PRAGMA user_version で適用済みのバージョンを管理する。キー = 適用後のバージョン
MIGRATIONS: dict[int, str] = {
    2: """
    ALTER TABLE articles ADD COLUMN language TEXT;   -- 'en' / 'ja'
    ALTER TABLE articles ADD COLUMN metadata TEXT;   -- 情報源固有の項目（JSON）

    -- 情報源の属性。country は情報源の所在地で、記事の対象市場とは別
    CREATE TABLE sources (
        id          TEXT PRIMARY KEY,
        label       TEXT NOT NULL,
        country     TEXT NOT NULL,
        kind        TEXT NOT NULL,
        language    TEXT NOT NULL,
        homepage    TEXT NOT NULL,
        license     TEXT,
        attribution TEXT,
        updated_at  TEXT NOT NULL
    );

    -- 記事が対象とする市場（1記事に複数可）。行が無い記事は「未判定」
    CREATE TABLE article_markets (
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        market     TEXT NOT NULL,   -- ISO 3166-1 alpha-2 または 'GLOBAL'
        method     TEXT NOT NULL CHECK (method IN ('source_default', 'keyword', 'ai', 'manual')),
        created_at TEXT NOT NULL,
        PRIMARY KEY (article_id, market)
    );
    CREATE INDEX idx_article_markets_market ON article_markets (market);
    """,
    3: """
    -- 情報源ごとの利用条件。確認できていないものは 'unknown'（商用利用可とみなさない）
    ALTER TABLE sources ADD COLUMN terms_url TEXT;
    ALTER TABLE sources ADD COLUMN commercial_use TEXT NOT NULL DEFAULT 'unknown'
        CHECK (commercial_use IN ('allowed', 'restricted', 'unknown'));
    ALTER TABLE sources ADD COLUMN terms_checked_on TEXT;   -- 利用条件を確認した日（YYYY-MM-DD）

    -- ラベルの語彙。assertion_kind で「観測事実」と「需要の仮説」を区別する
    CREATE TABLE label_definitions (
        taxonomy       TEXT NOT NULL,
        label          TEXT NOT NULL,
        assertion_kind TEXT NOT NULL CHECK (assertion_kind IN ('observation', 'hypothesis')),
        PRIMARY KEY (taxonomy, label)
    );
    INSERT INTO label_definitions (taxonomy, label, assertion_kind) VALUES
        ('signal_type', 'new_service', 'observation'),
        ('signal_type', 'market_entry', 'observation'),
        ('signal_type', 'partnership', 'observation'),
        ('signal_type', 'funding', 'observation'),
        ('signal_type', 'expansion', 'observation'),
        ('signal_type', 'new_segment', 'observation'),
        ('signal_type', 'regulation', 'observation'),
        ('software_domain', 'marketing', 'hypothesis'),
        ('software_domain', 'sales', 'hypothesis'),
        ('software_domain', 'operations', 'hypothesis'),
        ('software_domain', 'finance', 'hypothesis'),
        ('software_domain', 'customer_success', 'hypothesis');

    -- 記事ラベル。判定の主体を method で区別し、AI 判定は model を必須とする
    CREATE TABLE article_labels (
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        taxonomy   TEXT NOT NULL,
        label      TEXT NOT NULL,
        method     TEXT NOT NULL CHECK (method IN ('source_default', 'keyword', 'ai', 'manual')),
        evidence   TEXT,              -- 判定の根拠（記事中の該当箇所の抜粋など）
        model      TEXT,              -- AI 判定時のモデル名・プロンプト版
        created_at TEXT NOT NULL,
        CHECK ((method = 'ai') = (model IS NOT NULL)),
        FOREIGN KEY (taxonomy, label) REFERENCES label_definitions (taxonomy, label)
    );
    -- 同じ判定主体による同一ラベルの重複を防ぐ（model 違いの AI 判定は別行として残す）
    CREATE UNIQUE INDEX ux_article_labels
        ON article_labels (article_id, taxonomy, label, method, IFNULL(model, ''));
    CREATE INDEX idx_article_labels_label ON article_labels (taxonomy, label);
    """,
}
SCHEMA_VERSION = max(MIGRATIONS)


def db_path() -> Path:
    return Path(os.environ.get("RADAR_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target in sorted(v for v in MIGRATIONS if v > version):
        # 1つのトランザクションで適用し、失敗時は途中状態を残さない
        conn.executescript(
            f"BEGIN;\n{MIGRATIONS[target]}\nPRAGMA user_version = {target};\nCOMMIT;"
        )


def commercial_use_of(source: Source) -> str:
    """商用利用の可否。利用条件のURLと確認日が揃っていない 'allowed' は 'unknown' として扱う。"""
    value = getattr(source, "commercial_use", None)
    if value not in ("allowed", "restricted"):
        return "unknown"
    if value == "allowed" and not (getattr(source, "terms_url", None) and getattr(source, "terms_checked_on", None)):
        return "unknown"
    return value


def sync_sources(conn: sqlite3.Connection, sources: list[Source]) -> None:
    """情報源の定義を sources テーブルへ反映し、既存記事の言語・既定市場を補完する。"""
    timestamp = now()
    for s in sources:
        conn.execute(
            """INSERT INTO sources (id, label, country, kind, language, homepage, license, attribution,
                                    terms_url, commercial_use, terms_checked_on, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                 label = excluded.label, country = excluded.country, kind = excluded.kind,
                 language = excluded.language, homepage = excluded.homepage,
                 license = excluded.license, attribution = excluded.attribution,
                 terms_url = excluded.terms_url, commercial_use = excluded.commercial_use,
                 terms_checked_on = excluded.terms_checked_on,
                 updated_at = excluded.updated_at""",
            (s.name, s.label, s.country, s.kind, s.language, s.homepage, s.license, s.attribution,
             getattr(s, "terms_url", None), commercial_use_of(s), getattr(s, "terms_checked_on", None),
             timestamp),
        )
        conn.execute(
            "UPDATE articles SET language = ? WHERE source = ? AND language IS NULL",
            (s.language, s.name),
        )
        if s.default_market:
            # 市場が1つも付いていない記事にだけ既定市場を付ける
            conn.execute(
                """INSERT INTO article_markets (article_id, market, method, created_at)
                   SELECT id, ?, 'source_default', ? FROM articles
                   WHERE source = ? AND id NOT IN (SELECT article_id FROM article_markets)""",
                (s.default_market, timestamp, s.name),
            )


def init_db(sources: list[Source]) -> None:
    with connect() as conn:
        migrate(conn)
        sync_sources(conn, sources)


def save_articles(source: Source, articles: list[tuple[RawArticle, str]]) -> int:
    """(記事, カテゴリー) を保存し、新規に登録された件数を返す。既存IDは無視する。"""
    fetched_at = now()
    inserted = 0
    with connect() as conn:
        sync_sources(conn, [source])
        for a, category in articles:
            cur = conn.execute(
                """INSERT OR IGNORE INTO articles
                   (source, external_id, title, url, summary, published_at, category,
                    fetched_at, language, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source.name, a.external_id, a.title, a.url, a.summary,
                 a.published_at.astimezone(timezone.utc).isoformat(), category, fetched_at,
                 source.language, json.dumps(a.metadata, ensure_ascii=False) if a.metadata else None),
            )
            if cur.rowcount == 0:
                continue
            inserted += 1
            if source.default_market:
                conn.execute(
                    "INSERT INTO article_markets (article_id, market, method, created_at) "
                    "VALUES (?, ?, 'source_default', ?)",
                    (cur.lastrowid, source.default_market, fetched_at),
                )
    return inserted


MarketFilter = Literal["home", "overseas"]


def list_articles(
    category: str | None = None, market: MarketFilter | None = None, limit: int = 100
) -> list[dict]:
    """market: 'home' = 日本市場を対象とする記事、'overseas' = 日本以外の市場を対象とする記事。
    市場が未判定の記事はどちらにも含めない（market 未指定時のみ表示）。"""
    conditions: list[str] = []
    params: list = []
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    if market == "home":
        conditions.append("EXISTS (SELECT 1 FROM article_markets m WHERE m.article_id = a.id AND m.market = ?)")
        params.append(HOME_MARKET)
    elif market == "overseas":
        conditions.append("EXISTS (SELECT 1 FROM article_markets m WHERE m.article_id = a.id AND m.market <> ?)")
        params.append(HOME_MARKET)

    query = """
        SELECT a.*, COALESCE(s.label, a.source) AS source_label, s.country AS source_country,
               (SELECT json_group_array(json_object('market', m.market, 'method', m.method))
                  FROM article_markets m WHERE m.article_id = a.id) AS markets
        FROM articles a LEFT JOIN sources s ON s.id = a.source
    """
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY a.published_at DESC LIMIT ?"
    params.append(limit)

    with connect() as conn:
        rows = [dict(row) for row in conn.execute(query, params)]
    for row in rows:
        row["markets"] = json.loads(row["markets"])
        row["metadata"] = json.loads(row["metadata"]) if row["metadata"] else {}
    return rows


def list_sources() -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, label, country, kind, language, homepage, license, attribution,"
            " terms_url, commercial_use, terms_checked_on FROM sources ORDER BY id"
        )]
