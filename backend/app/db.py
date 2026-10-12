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
    4: """
    -- keyword 判定の強さ（explicit：記事に明示的な記述あり / inferred：ルールによる推測）と、判定ルールの版
    ALTER TABLE article_labels ADD COLUMN strength TEXT CHECK (strength IN ('explicit', 'inferred'));
    ALTER TABLE article_labels ADD COLUMN rule_version TEXT;
    """,
    5: """
    -- 取り込んだ統計表（出典と取り込みの単位）。ニュース記事とは別に管理する
    CREATE TABLE stat_tables (
        id           INTEGER PRIMARY KEY,
        source       TEXT NOT NULL,              -- 'estat'
        stat_code    TEXT NOT NULL,              -- 政府統計コード（例：'00200541'）
        table_ref    TEXT NOT NULL,              -- 'statInfId:000040413728' など
        table_no     TEXT NOT NULL,              -- 表番号（例：'14'）
        title        TEXT NOT NULL,              -- 表題の原文
        survey_name  TEXT NOT NULL,              -- 例：'2025年（令和7年） 個人企業経済調査'
        period       TEXT NOT NULL,              -- 調査年（例：'2025'）
        publisher    TEXT NOT NULL,              -- 作成機関
        source_url   TEXT NOT NULL,
        unit         TEXT NOT NULL,              -- 元データの単位（例：'‰'）
        answer_type  TEXT NOT NULL CHECK (answer_type IN ('single', 'multiple', 'unknown')),
        note         TEXT,                       -- 表の注記の原文
        scope_note   TEXT NOT NULL,              -- 調査対象の範囲と代表性の注意
        file_sha256  TEXT NOT NULL,              -- 取り込んだファイルのハッシュ（重複取り込みの判定）
        imported_at  TEXT NOT NULL,
        UNIQUE (source, table_ref, period)
    );

    -- 統計上の経営課題（表に書かれた値そのもの）。ソフトウェア需要ではない
    CREATE TABLE problem_observations (
        stat_table_id  INTEGER NOT NULL REFERENCES stat_tables(id) ON DELETE CASCADE,
        area_code      TEXT NOT NULL,            -- '00'（全国）
        industry_code  TEXT NOT NULL,            -- 例：'11'
        industry_name  TEXT NOT NULL,            -- 原文（「うち〜」を含む）
        industry_level INTEGER NOT NULL,         -- 表の階層レベル（1：全産業 〜 4）
        parent_code    TEXT,                     -- 表の階層から決めた親業種
        is_subset      INTEGER NOT NULL CHECK (is_subset IN (0, 1)),  -- 1：「うち〜」（親の一部の内訳で、網羅的でない）
        row_order      INTEGER NOT NULL,         -- 表での行の順序
        problem_code   TEXT NOT NULL,            -- '00'（総数）〜 '12'
        problem_name   TEXT NOT NULL,
        value          REAL,                     -- 元データの値（unit の単位）。記号のセルは NULL
        value_symbol   TEXT,                     -- 記号の原文（'-' など）。数値のセルは NULL
        CHECK ((value IS NULL) <> (value_symbol IS NULL)),
        PRIMARY KEY (stat_table_id, area_code, industry_code, problem_code)
    );
    CREATE INDEX idx_problem_observations_problem ON problem_observations (problem_code);
    """,
    6: """
    -- SaaS需要仮説。観測（統計・記事）ではなく、人が検証するための作業単位
    CREATE TABLE hypotheses (
        id                INTEGER PRIMARY KEY,
        domain            TEXT NOT NULL CHECK (domain IN
                            ('marketing', 'sales', 'operations', 'finance', 'customer_success')),
        title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
        target_customer   TEXT NOT NULL CHECK (length(trim(target_customer)) > 0),
        problem_statement TEXT NOT NULL CHECK (length(trim(problem_statement)) > 0),
        solution_idea     TEXT NOT NULL CHECK (length(trim(solution_idea)) > 0),
        assumptions       TEXT NOT NULL CHECK (length(trim(assumptions)) > 0),   -- 前提条件（1行1項目）
        unknowns          TEXT NOT NULL CHECK (length(trim(unknowns)) > 0),      -- 未確認事項（1行1項目）
        not_implied       TEXT NOT NULL CHECK (length(trim(not_implied)) > 0),   -- この仮説が示さないこと
        next_action       TEXT NOT NULL CHECK (length(trim(next_action)) > 0),   -- 次の検証行動
        status            TEXT NOT NULL DEFAULT 'unverified'
                            CHECK (status IN ('unverified', 'in_progress', 'rejected', 'validated')),
        status_reason     TEXT,
        origin            TEXT NOT NULL CHECK (origin IN ('rule', 'manual')),
        rule_key          TEXT,             -- ルールの識別子（例：'p09.finance.food'）
        rule_version      TEXT,             -- 作成・最終更新したルールセットの版
        rule_retired      INTEGER NOT NULL DEFAULT 0 CHECK (rule_retired IN (0, 1)),
        human_modified    INTEGER NOT NULL DEFAULT 0 CHECK (human_modified IN (0, 1)),
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        CHECK ((origin = 'rule') = (rule_key IS NOT NULL AND rule_version IS NOT NULL))
    );
    CREATE UNIQUE INDEX ux_hypotheses_rule_key ON hypotheses (rule_key) WHERE origin = 'rule';

    -- 仮説の根拠と検証記録。追記のみ（更新・削除しない）
    CREATE TABLE hypothesis_evidence (
        id               INTEGER PRIMARY KEY,
        hypothesis_id    INTEGER NOT NULL REFERENCES hypotheses(id),
        kind             TEXT NOT NULL CHECK (kind IN
                           ('problem_observation', 'article', 'note',            -- 観測・メモ（検証には数えない）
                            'interview', 'channel_test', 'payment_intent')),     -- 顧客接点による検証記録
        role             TEXT NOT NULL CHECK (role IN ('basis', 'supporting', 'contradicting', 'validation')),
        added_by         TEXT NOT NULL CHECK (added_by IN ('rule', 'manual')),
        -- 統計の観測への参照（修正版の取り込みで観測が置き換わっても根拠を残すため、外部キーにしない）
        stat_table_id    INTEGER,
        industry_code    TEXT,
        problem_code     TEXT,
        -- 紐付けた時点の写し
        snapshot_label   TEXT,
        snapshot_value   REAL,
        snapshot_symbol  TEXT,
        snapshot_total   REAL,
        source_title     TEXT,
        source_url       TEXT,
        source_period    TEXT,              -- 統計の調査年
        source_sha256    TEXT,              -- 統計ファイルのハッシュ（同じ年の修正版を区別する）
        rule_version     TEXT,              -- 紐付けたルールセットの版
        article_id       INTEGER REFERENCES articles(id),
        -- 検証記録
        contact_ref      TEXT,              -- 匿名化した顧客接点の識別子（氏名・連絡先は入れない）
        result           TEXT CHECK (result IN ('supports', 'refutes', 'inconclusive')),
        occurred_on      TEXT,              -- 実施日（YYYY-MM-DD）
        payment_signal   TEXT CHECK (payment_signal IN ('preorder', 'paid', 'signed_quote', 'letter_of_intent')),
        method_note      TEXT,              -- 検証方法の補足
        description      TEXT,              -- 観察内容・メモ
        url              TEXT,
        created_at       TEXT NOT NULL,     -- 根拠を記録した日時
        CHECK ((kind = 'problem_observation') = (stat_table_id IS NOT NULL AND industry_code IS NOT NULL
                                                 AND problem_code IS NOT NULL AND source_period IS NOT NULL)),
        CHECK ((kind = 'article') = (article_id IS NOT NULL)),
        CHECK (kind <> 'note' OR (added_by = 'manual' AND length(trim(ifnull(description, ''))) > 0)),
        -- 検証記録：人が追加し、識別子・実施日・結果・観察内容が必須。role は validation のみ
        CHECK ((kind IN ('interview', 'channel_test', 'payment_intent')) = (role = 'validation')),
        CHECK (kind NOT IN ('interview', 'channel_test', 'payment_intent') OR (
                 added_by = 'manual' AND result IS NOT NULL AND occurred_on IS NOT NULL
                 AND length(trim(ifnull(description, ''))) > 0
                 AND contact_ref IS NOT NULL AND length(contact_ref) BETWEEN 1 AND 32
                 AND contact_ref NOT GLOB '*[^A-Za-z0-9_-]*')),
        CHECK ((kind = 'payment_intent') = (payment_signal IS NOT NULL)),
        CHECK (contact_ref IS NULL OR kind IN ('interview', 'channel_test', 'payment_intent'))
    );
    CREATE INDEX idx_hypothesis_evidence_hypothesis ON hypothesis_evidence (hypothesis_id);
    CREATE INDEX idx_hypothesis_evidence_problem ON hypothesis_evidence (problem_code, industry_code);
    -- ルールが紐付ける観測は、同じ統計ファイルの同じ観測につき1件
    CREATE UNIQUE INDEX ux_hypothesis_evidence_rule_basis ON hypothesis_evidence
        (hypothesis_id, stat_table_id, industry_code, problem_code, source_sha256)
        WHERE kind = 'problem_observation' AND added_by = 'rule';

    -- 根拠の取り消し（追記のみ）。取り消した根拠は検証の条件に数えない
    CREATE TABLE hypothesis_evidence_retractions (
        evidence_id INTEGER PRIMARY KEY REFERENCES hypothesis_evidence(id),
        reason      TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        created_at  TEXT NOT NULL
    );

    -- 履歴（作成・編集・状態変更・ルールによる更新と見送り・根拠の追加と取り消し）
    CREATE TABLE hypothesis_events (
        id            INTEGER PRIMARY KEY,
        hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
        event         TEXT NOT NULL CHECK (event IN ('created', 'rule_updated', 'rule_update_skipped', 'rule_retired',
                                                     'edited', 'status_changed', 'evidence_added', 'evidence_retracted',
                                                     'basis_added')),
        from_status   TEXT,
        to_status     TEXT,
        detail        TEXT,                -- JSON
        created_at    TEXT NOT NULL
    );
    CREATE INDEX idx_hypothesis_events_hypothesis ON hypothesis_events (hypothesis_id);

    -- 仮説はすべて「未検証」で作成する
    CREATE TRIGGER trg_hypotheses_insert_unverified BEFORE INSERT ON hypotheses
    WHEN NEW.status <> 'unverified' OR NEW.status_reason IS NOT NULL
    BEGIN SELECT RAISE(ABORT, 'hypothesis must be created as unverified'); END;

    -- 許可された状態遷移のみ
    CREATE TRIGGER trg_hypotheses_status_transition BEFORE UPDATE OF status ON hypotheses
    WHEN OLD.status <> NEW.status AND (OLD.status, NEW.status) NOT IN (
        VALUES ('unverified', 'in_progress'), ('unverified', 'rejected'),
               ('in_progress', 'validated'), ('in_progress', 'rejected'), ('in_progress', 'unverified'),
               ('rejected', 'unverified'), ('validated', 'in_progress'))
    BEGIN SELECT RAISE(ABORT, 'invalid status transition'); END;

    -- 棄却・検証で支持、およびそこからの変更には、新しい理由が必要
    CREATE TRIGGER trg_hypotheses_status_reason BEFORE UPDATE OF status ON hypotheses
    WHEN OLD.status <> NEW.status
     AND (NEW.status IN ('rejected', 'validated') OR OLD.status IN ('rejected', 'validated'))
     AND (NEW.status_reason IS NULL OR length(trim(NEW.status_reason)) = 0
          OR NEW.status_reason IS OLD.status_reason)
    BEGIN SELECT RAISE(ABORT, 'status reason required'); END;

    -- 「検証で支持」：別々の顧客接点からの支持記録が2件以上、または支払意思を示す支持記録が1件以上
    CREATE TRIGGER trg_hypotheses_validated_requires_evidence BEFORE UPDATE OF status ON hypotheses
    WHEN NEW.status = 'validated' AND OLD.status <> 'validated' AND NOT (
        (SELECT COUNT(DISTINCT e.contact_ref) FROM hypothesis_evidence e
          WHERE e.hypothesis_id = NEW.id AND e.role = 'validation' AND e.result = 'supports'
            AND e.id NOT IN (SELECT evidence_id FROM hypothesis_evidence_retractions)) >= 2
        OR EXISTS (SELECT 1 FROM hypothesis_evidence e
          WHERE e.hypothesis_id = NEW.id AND e.kind = 'payment_intent' AND e.result = 'supports'
            AND e.id NOT IN (SELECT evidence_id FROM hypothesis_evidence_retractions)))
    BEGIN SELECT RAISE(ABORT, 'validation requirements not met'); END;

    -- 根拠・検証記録は追記のみ（更新・削除で条件が崩れることを防ぐ）
    CREATE TRIGGER trg_hypothesis_evidence_no_update BEFORE UPDATE ON hypothesis_evidence
    BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER trg_hypothesis_evidence_no_delete BEFORE DELETE ON hypothesis_evidence
    BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER trg_retractions_no_update BEFORE UPDATE ON hypothesis_evidence_retractions
    BEGIN SELECT RAISE(ABORT, 'retractions are append-only'); END;
    CREATE TRIGGER trg_retractions_no_delete BEFORE DELETE ON hypothesis_evidence_retractions
    BEGIN SELECT RAISE(ABORT, 'retractions are append-only'); END;

    -- 取り消しは、人が追加した根拠のみ。「検証で支持」の仮説の根拠は取り消せない（先に状態を戻す）
    CREATE TRIGGER trg_retractions_guard BEFORE INSERT ON hypothesis_evidence_retractions
    WHEN (SELECT added_by FROM hypothesis_evidence WHERE id = NEW.evidence_id) IS NOT 'manual'
      OR (SELECT h.status FROM hypotheses h JOIN hypothesis_evidence e ON e.hypothesis_id = h.id
           WHERE e.id = NEW.evidence_id) = 'validated'
    BEGIN SELECT RAISE(ABORT, 'retraction not allowed'); END;

    -- 仮説は削除しない（棄却で表す）
    CREATE TRIGGER trg_hypotheses_no_delete BEFORE DELETE ON hypotheses
    BEGIN SELECT RAISE(ABORT, 'hypotheses are not deleted; reject instead'); END;
    CREATE TRIGGER trg_hypothesis_events_no_change BEFORE UPDATE ON hypothesis_events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
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
    category: str | None = None,
    market: MarketFilter | None = None,
    signal: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """market: 'home' = 日本市場を対象とする記事、'overseas' = 日本以外の市場を対象とする記事。
    市場が未判定の記事はどちらにも含めない（market 未指定時のみ表示）。
    signal: その signal_type ラベルを持つ記事に絞る（判定方法は問わない）。"""
    conditions: list[str] = []
    params: list = []
    if signal:
        conditions.append(
            "EXISTS (SELECT 1 FROM article_labels l "
            "WHERE l.article_id = a.id AND l.taxonomy = 'signal_type' AND l.label = ?)"
        )
        params.append(signal)
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
                  FROM article_markets m WHERE m.article_id = a.id) AS markets,
               (SELECT json_group_array(json_object(
                         'label', l.label, 'method', l.method, 'strength', l.strength,
                         'evidence', l.evidence, 'model', l.model, 'rule_version', l.rule_version))
                  FROM (SELECT * FROM article_labels WHERE article_id = a.id AND taxonomy = 'signal_type'
                        ORDER BY label, method) l) AS signals
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
        row["signals"] = json.loads(row["signals"])
        row["metadata"] = json.loads(row["metadata"]) if row["metadata"] else {}
    return rows


def list_sources() -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, label, country, kind, language, homepage, license, attribution,"
            " terms_url, commercial_use, terms_checked_on FROM sources ORDER BY id"
        )]
