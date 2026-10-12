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
    7: """
    -- 複合外部キーで「同じ仮説の記録だけを参照できる」ことを保証するための索引（データは変わらない）
    CREATE UNIQUE INDEX ux_hypothesis_evidence_id_hypothesis ON hypothesis_evidence (id, hypothesis_id);

    -- 検証の計画（見出し）。内容は版（validation_activity_revisions）で持つ
    CREATE TABLE validation_activities (
        id                 INTEGER PRIMARY KEY,
        hypothesis_id      INTEGER NOT NULL REFERENCES hypotheses(id),
        activity_type      TEXT NOT NULL CHECK (activity_type IN
                             ('interview', 'competitor_research', 'sales_experiment', 'technical_check')),
        status             TEXT NOT NULL DEFAULT 'planned'
                             CHECK (status IN ('planned', 'in_progress', 'done', 'cancelled')),
        locked_revision_id INTEGER,          -- 開始時に固定した版（同じ計画の版のみ。複合外部キー）
        cancel_reason      TEXT,
        created_at         TEXT NOT NULL,
        started_at         TEXT,
        completed_at       TEXT,
        cancelled_at       TEXT,
        UNIQUE (id, hypothesis_id),
        FOREIGN KEY (locked_revision_id, id) REFERENCES validation_activity_revisions (id, activity_id),
        CHECK (status <> 'planned' OR (locked_revision_id IS NULL AND started_at IS NULL)),
        CHECK (status NOT IN ('in_progress', 'done') OR (locked_revision_id IS NOT NULL AND started_at IS NOT NULL)),
        CHECK ((status = 'done') = (completed_at IS NOT NULL)),
        CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL AND length(trim(ifnull(cancel_reason, ''))) > 0))
    );

    -- 計画の内容（追記のみ）。開始前に限り、理由を付けて版を重ねられる
    CREATE TABLE validation_activity_revisions (
        id                INTEGER PRIMARY KEY,
        activity_id       INTEGER NOT NULL REFERENCES validation_activities(id),
        revision_no       INTEGER NOT NULL CHECK (revision_no >= 1),
        purpose           TEXT NOT NULL CHECK (length(trim(purpose)) > 0),
        target            TEXT NOT NULL CHECK (length(trim(target)) > 0),
        decision_criteria TEXT NOT NULL CHECK (length(trim(decision_criteria)) > 0),
        planned_on        TEXT,
        change_reason     TEXT,
        created_at        TEXT NOT NULL,
        UNIQUE (activity_id, revision_no),
        UNIQUE (id, activity_id),
        CHECK (revision_no = 1 OR length(trim(ifnull(change_reason, ''))) > 0)
    );

    -- 計画の結果（1計画1件、追記のみ）。競合調査・技術検証の結果もここに保存する（顧客接点の記録ではない）
    CREATE TABLE validation_activity_results (
        activity_id      INTEGER PRIMARY KEY REFERENCES validation_activities(id),
        outcome          TEXT NOT NULL CHECK (outcome IN ('supports', 'refutes', 'inconclusive')),
        observed_facts   TEXT NOT NULL CHECK (length(trim(observed_facts)) > 0),
        interpretation   TEXT NOT NULL CHECK (length(trim(interpretation)) > 0),
        judgement_reason TEXT NOT NULL CHECK (length(trim(judgement_reason)) > 0),
        created_at       TEXT NOT NULL
    );

    -- 競合調査・技術検証などの参照先（追記のみ）
    CREATE TABLE validation_activity_sources (
        id          INTEGER PRIMARY KEY,
        activity_id INTEGER NOT NULL REFERENCES validation_activities(id),
        url         TEXT NOT NULL CHECK (url LIKE 'http://%' OR url LIKE 'https://%'),
        title       TEXT,
        accessed_on TEXT NOT NULL,
        note        TEXT,
        created_at  TEXT NOT NULL
    );

    -- 計画の結果として紐付けた顧客接点の検証記録（同じ仮説のもの。複合外部キー）
    CREATE TABLE activity_result_evidence (
        activity_id   INTEGER NOT NULL,
        hypothesis_id INTEGER NOT NULL,
        evidence_id   INTEGER NOT NULL,
        created_at    TEXT NOT NULL,
        PRIMARY KEY (activity_id, evidence_id),
        FOREIGN KEY (activity_id, hypothesis_id) REFERENCES validation_activities (id, hypothesis_id),
        FOREIGN KEY (evidence_id, hypothesis_id) REFERENCES hypothesis_evidence (id, hypothesis_id)
    );

    -- 6観点の評価（追記のみ）。参照を登録してから確定（committed_at）する。確定前の行は表示・集計しない
    CREATE TABLE opportunity_assessments (
        id               INTEGER PRIMARY KEY,
        hypothesis_id    INTEGER NOT NULL REFERENCES hypotheses(id),
        aspect           TEXT NOT NULL CHECK (aspect IN ('reachability', 'severity', 'alternatives',
                           'willingness_to_pay', 'build_operate_burden', 'retention')),
        state            TEXT NOT NULL CHECK (state IN ('not_researched', 'researching', 'supported', 'contradicted')),
        basis            TEXT NOT NULL CHECK (basis IN ('evidence', 'assumption')),
        observed_facts   TEXT,               -- 人の判断：観察された具体的な事実
        relevance        TEXT,               -- 人の判断：その事実が観点にどう関係するか
        judgement_reason TEXT,               -- 人の判断：支持・反証と判断した理由
        assumption_note  TEXT,               -- 担当者の推測
        committed_at     TEXT,
        created_at       TEXT NOT NULL,
        UNIQUE (id, hypothesis_id),
        CHECK (state NOT IN ('supported', 'contradicted') OR basis = 'evidence'),
        CHECK (state <> 'not_researched' OR basis = 'assumption'),
        CHECK (basis <> 'evidence' OR (length(trim(ifnull(observed_facts, ''))) > 0
               AND length(trim(ifnull(relevance, ''))) > 0 AND length(trim(ifnull(judgement_reason, ''))) > 0)),
        CHECK (basis <> 'assumption' OR length(trim(ifnull(assumption_note, ''))) > 0)
    );
    CREATE INDEX idx_opportunity_assessments_hypothesis ON opportunity_assessments (hypothesis_id, aspect);

    -- 評価が参照する記録（顧客接点の検証記録か、完了した計画のどちらか一方。同じ仮説のもの）
    CREATE TABLE assessment_refs (
        id            INTEGER PRIMARY KEY,
        assessment_id INTEGER NOT NULL,
        hypothesis_id INTEGER NOT NULL,
        evidence_id   INTEGER,
        activity_id   INTEGER,
        CHECK ((evidence_id IS NULL) <> (activity_id IS NULL)),
        FOREIGN KEY (assessment_id, hypothesis_id) REFERENCES opportunity_assessments (id, hypothesis_id),
        FOREIGN KEY (evidence_id, hypothesis_id) REFERENCES hypothesis_evidence (id, hypothesis_id),
        FOREIGN KEY (activity_id, hypothesis_id) REFERENCES validation_activities (id, hypothesis_id),
        UNIQUE (assessment_id, evidence_id),
        UNIQUE (assessment_id, activity_id)
    );

    -- 観点ごとに参照できる記録の種類（トリガーが照合する）
    CREATE TABLE assessment_ref_policies (
        aspect TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('evidence', 'activity')),
        kind   TEXT NOT NULL,
        PRIMARY KEY (aspect, source, kind)
    );
    INSERT INTO assessment_ref_policies (aspect, source, kind) VALUES
        ('reachability', 'evidence', 'interview'), ('reachability', 'evidence', 'channel_test'),
        ('reachability', 'evidence', 'payment_intent'),
        ('reachability', 'activity', 'interview'), ('reachability', 'activity', 'sales_experiment'),
        ('severity', 'evidence', 'interview'), ('severity', 'evidence', 'payment_intent'),
        ('severity', 'activity', 'interview'),
        ('alternatives', 'evidence', 'interview'),
        ('alternatives', 'activity', 'interview'), ('alternatives', 'activity', 'competitor_research'),
        ('willingness_to_pay', 'evidence', 'interview'), ('willingness_to_pay', 'evidence', 'channel_test'),
        ('willingness_to_pay', 'evidence', 'payment_intent'),
        ('willingness_to_pay', 'activity', 'sales_experiment'),
        ('build_operate_burden', 'activity', 'technical_check'),
        ('build_operate_burden', 'activity', 'competitor_research'),
        ('retention', 'evidence', 'interview'), ('retention', 'evidence', 'payment_intent'),
        ('retention', 'activity', 'interview'), ('retention', 'activity', 'sales_experiment');

    -- 優先して調査する候補の選定（追記のみ。各仮説の最新の操作が現在の状態）
    CREATE TABLE opportunity_selections (
        id            INTEGER PRIMARY KEY,
        hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
        action        TEXT NOT NULL CHECK (action IN ('select', 'unselect')),
        reason        TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        created_at    TEXT NOT NULL
    );
    CREATE INDEX idx_opportunity_selections_hypothesis ON opportunity_selections (hypothesis_id, id);

    -- 現在選定中の仮説（各仮説の最新の操作が select のもの）
    CREATE VIEW current_opportunity_selections AS
        SELECT s.hypothesis_id, s.reason, s.created_at FROM opportunity_selections s
        WHERE s.action = 'select'
          AND s.id = (SELECT MAX(s2.id) FROM opportunity_selections s2 WHERE s2.hypothesis_id = s.hypothesis_id);

    -- --- 計画 ---
    CREATE TRIGGER trg_activities_insert BEFORE INSERT ON validation_activities
    WHEN NEW.status <> 'planned' OR (SELECT status FROM hypotheses WHERE id = NEW.hypothesis_id) = 'rejected'
    BEGIN SELECT RAISE(ABORT, 'activity must start as planned on a non-rejected hypothesis'); END;

    CREATE TRIGGER trg_activities_update BEFORE UPDATE ON validation_activities
    WHEN OLD.status IN ('done', 'cancelled')
      OR NEW.id IS NOT OLD.id OR NEW.hypothesis_id IS NOT OLD.hypothesis_id
      OR NEW.activity_type IS NOT OLD.activity_type OR NEW.created_at IS NOT OLD.created_at
      OR (OLD.locked_revision_id IS NOT NULL AND NEW.locked_revision_id IS NOT OLD.locked_revision_id)
      OR (OLD.started_at IS NOT NULL AND NEW.started_at IS NOT OLD.started_at)
      OR (OLD.status, NEW.status) NOT IN (VALUES ('planned', 'in_progress'), ('planned', 'cancelled'),
                                                 ('in_progress', 'done'), ('in_progress', 'cancelled'))
      OR (SELECT status FROM hypotheses WHERE id = OLD.hypothesis_id) = 'rejected'
    BEGIN SELECT RAISE(ABORT, 'invalid activity update'); END;

    -- 開始時は、その計画の最新の版を固定する
    CREATE TRIGGER trg_activities_start BEFORE UPDATE OF status ON validation_activities
    WHEN NEW.status = 'in_progress' AND NEW.locked_revision_id IS NOT (
        SELECT id FROM validation_activity_revisions WHERE activity_id = OLD.id ORDER BY revision_no DESC LIMIT 1)
    BEGIN SELECT RAISE(ABORT, 'must lock the latest revision when starting'); END;

    -- 完了の条件：結果があり、顧客接点の計画は記録を1件以上紐付け、結果の向きに合う記録を含む。競合調査は参照先が必要
    CREATE TRIGGER trg_activities_done BEFORE UPDATE OF status ON validation_activities
    WHEN NEW.status = 'done' AND (
        NOT EXISTS (SELECT 1 FROM validation_activity_results WHERE activity_id = OLD.id)
        OR (OLD.activity_type IN ('interview', 'sales_experiment') AND (
              NOT EXISTS (SELECT 1 FROM activity_result_evidence WHERE activity_id = OLD.id)
              OR ((SELECT outcome FROM validation_activity_results WHERE activity_id = OLD.id) IN ('supports', 'refutes')
                  AND NOT EXISTS (SELECT 1 FROM activity_result_evidence l JOIN hypothesis_evidence e ON e.id = l.evidence_id
                                  WHERE l.activity_id = OLD.id
                                    AND e.result = (SELECT outcome FROM validation_activity_results WHERE activity_id = OLD.id)))))
        OR (OLD.activity_type = 'competitor_research'
            AND NOT EXISTS (SELECT 1 FROM validation_activity_sources WHERE activity_id = OLD.id)))
    BEGIN SELECT RAISE(ABORT, 'completion requirements not met'); END;

    CREATE TRIGGER trg_activities_no_delete BEFORE DELETE ON validation_activities
    BEGIN SELECT RAISE(ABORT, 'activities are not deleted; cancel instead'); END;

    -- 版：開始前（planned）に限り、連番で追記できる
    CREATE TRIGGER trg_revisions_insert BEFORE INSERT ON validation_activity_revisions
    WHEN (SELECT status FROM validation_activities WHERE id = NEW.activity_id) IS NOT 'planned'
      OR NEW.revision_no <> 1 + ifnull((SELECT MAX(revision_no) FROM validation_activity_revisions
                                        WHERE activity_id = NEW.activity_id), 0)
    BEGIN SELECT RAISE(ABORT, 'revisions can only be added sequentially before the activity starts'); END;
    CREATE TRIGGER trg_revisions_no_update BEFORE UPDATE ON validation_activity_revisions
    BEGIN SELECT RAISE(ABORT, 'revisions are append-only'); END;
    CREATE TRIGGER trg_revisions_no_delete BEFORE DELETE ON validation_activity_revisions
    BEGIN SELECT RAISE(ABORT, 'revisions are append-only'); END;

    -- 結果・参照先・記録の紐付けは、実施中（in_progress）にのみ追加でき、変更・削除できない
    CREATE TRIGGER trg_results_insert BEFORE INSERT ON validation_activity_results
    WHEN (SELECT status FROM validation_activities WHERE id = NEW.activity_id) IS NOT 'in_progress'
    BEGIN SELECT RAISE(ABORT, 'results can only be recorded while in progress'); END;
    CREATE TRIGGER trg_results_no_update BEFORE UPDATE ON validation_activity_results
    BEGIN SELECT RAISE(ABORT, 'results are append-only'); END;
    CREATE TRIGGER trg_results_no_delete BEFORE DELETE ON validation_activity_results
    BEGIN SELECT RAISE(ABORT, 'results are append-only'); END;

    CREATE TRIGGER trg_sources_insert BEFORE INSERT ON validation_activity_sources
    WHEN (SELECT status FROM validation_activities WHERE id = NEW.activity_id) IS NOT 'in_progress'
    BEGIN SELECT RAISE(ABORT, 'sources can only be added while in progress'); END;
    CREATE TRIGGER trg_sources_no_update BEFORE UPDATE ON validation_activity_sources
    BEGIN SELECT RAISE(ABORT, 'sources are append-only'); END;
    CREATE TRIGGER trg_sources_no_delete BEFORE DELETE ON validation_activity_sources
    BEGIN SELECT RAISE(ABORT, 'sources are append-only'); END;

    -- 計画の結果に紐付けられるのは、顧客接点の記録（ヒアリング→ヒアリング、販売実験→テスト・支払意思）で、取り消されていないもの
    CREATE TRIGGER trg_activity_evidence_insert BEFORE INSERT ON activity_result_evidence
    WHEN (SELECT status FROM validation_activities WHERE id = NEW.activity_id) IS NOT 'in_progress'
      OR NEW.evidence_id IN (SELECT evidence_id FROM hypothesis_evidence_retractions)
      OR NOT (
        ((SELECT activity_type FROM validation_activities WHERE id = NEW.activity_id) = 'interview'
           AND (SELECT kind FROM hypothesis_evidence WHERE id = NEW.evidence_id) = 'interview')
        OR ((SELECT activity_type FROM validation_activities WHERE id = NEW.activity_id) = 'sales_experiment'
           AND (SELECT kind FROM hypothesis_evidence WHERE id = NEW.evidence_id) IN ('channel_test', 'payment_intent')))
    BEGIN SELECT RAISE(ABORT, 'evidence cannot be linked to this activity'); END;
    CREATE TRIGGER trg_activity_evidence_no_update BEFORE UPDATE ON activity_result_evidence
    BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;
    CREATE TRIGGER trg_activity_evidence_no_delete BEFORE DELETE ON activity_result_evidence
    BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;

    -- --- 評価 ---
    CREATE TRIGGER trg_assessments_insert BEFORE INSERT ON opportunity_assessments
    WHEN NEW.committed_at IS NOT NULL OR (SELECT status FROM hypotheses WHERE id = NEW.hypothesis_id) = 'rejected'
    BEGIN SELECT RAISE(ABORT, 'assessment must be inserted uncommitted on a non-rejected hypothesis'); END;

    -- 変更できるのは確定（committed_at を入れる）だけ。確定後は変更できない
    CREATE TRIGGER trg_assessments_update BEFORE UPDATE ON opportunity_assessments
    WHEN OLD.committed_at IS NOT NULL OR NEW.committed_at IS NULL
      OR NEW.id IS NOT OLD.id OR NEW.hypothesis_id IS NOT OLD.hypothesis_id OR NEW.aspect IS NOT OLD.aspect
      OR NEW.state IS NOT OLD.state OR NEW.basis IS NOT OLD.basis OR NEW.observed_facts IS NOT OLD.observed_facts
      OR NEW.relevance IS NOT OLD.relevance OR NEW.judgement_reason IS NOT OLD.judgement_reason
      OR NEW.assumption_note IS NOT OLD.assumption_note OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'assessments are immutable once committed'); END;

    -- 確定の条件：記録に基づく評価は参照が1件以上、推測は参照なし。支払意思の「根拠あり」は支払意思の支持記録が必須
    CREATE TRIGGER trg_assessments_commit BEFORE UPDATE OF committed_at ON opportunity_assessments
    WHEN OLD.committed_at IS NULL AND (
        (OLD.basis = 'evidence' AND NOT EXISTS (SELECT 1 FROM assessment_refs WHERE assessment_id = OLD.id))
        OR (OLD.basis = 'assumption' AND EXISTS (SELECT 1 FROM assessment_refs WHERE assessment_id = OLD.id))
        OR (OLD.aspect = 'willingness_to_pay' AND OLD.state = 'supported' AND NOT EXISTS (
              SELECT 1 FROM assessment_refs r JOIN hypothesis_evidence e ON e.id = r.evidence_id
              WHERE r.assessment_id = OLD.id AND e.kind = 'payment_intent' AND e.result = 'supports')))
    BEGIN SELECT RAISE(ABORT, 'assessment commit requirements not met'); END;

    CREATE TRIGGER trg_assessments_no_delete BEFORE DELETE ON opportunity_assessments
    BEGIN SELECT RAISE(ABORT, 'assessments are append-only'); END;

    -- 参照：確定前の評価にのみ追加。観点で許可された種類、取り消されていない記録・完了した計画、状態と向きが一致すること
    CREATE TRIGGER trg_assessment_refs_insert BEFORE INSERT ON assessment_refs
    WHEN (SELECT committed_at FROM opportunity_assessments WHERE id = NEW.assessment_id) IS NOT NULL
      OR (NEW.evidence_id IS NOT NULL AND (
            NOT EXISTS (SELECT 1 FROM assessment_ref_policies p
                        WHERE p.aspect = (SELECT aspect FROM opportunity_assessments WHERE id = NEW.assessment_id)
                          AND p.source = 'evidence'
                          AND p.kind = (SELECT kind FROM hypothesis_evidence WHERE id = NEW.evidence_id))
            OR NEW.evidence_id IN (SELECT evidence_id FROM hypothesis_evidence_retractions)
            OR ((SELECT state FROM opportunity_assessments WHERE id = NEW.assessment_id) = 'supported'
                AND (SELECT result FROM hypothesis_evidence WHERE id = NEW.evidence_id) IS NOT 'supports')
            OR ((SELECT state FROM opportunity_assessments WHERE id = NEW.assessment_id) = 'contradicted'
                AND (SELECT result FROM hypothesis_evidence WHERE id = NEW.evidence_id) IS NOT 'refutes')))
      OR (NEW.activity_id IS NOT NULL AND (
            NOT EXISTS (SELECT 1 FROM assessment_ref_policies p
                        WHERE p.aspect = (SELECT aspect FROM opportunity_assessments WHERE id = NEW.assessment_id)
                          AND p.source = 'activity'
                          AND p.kind = (SELECT activity_type FROM validation_activities WHERE id = NEW.activity_id))
            OR (SELECT status FROM validation_activities WHERE id = NEW.activity_id) IS NOT 'done'
            OR ((SELECT state FROM opportunity_assessments WHERE id = NEW.assessment_id) = 'supported'
                AND (SELECT outcome FROM validation_activity_results WHERE activity_id = NEW.activity_id) IS NOT 'supports')
            OR ((SELECT state FROM opportunity_assessments WHERE id = NEW.assessment_id) = 'contradicted'
                AND (SELECT outcome FROM validation_activity_results WHERE activity_id = NEW.activity_id) IS NOT 'refutes')))
    BEGIN SELECT RAISE(ABORT, 'reference not allowed for this assessment'); END;
    CREATE TRIGGER trg_assessment_refs_no_update BEFORE UPDATE ON assessment_refs
    BEGIN SELECT RAISE(ABORT, 'references are append-only'); END;
    CREATE TRIGGER trg_assessment_refs_no_delete BEFORE DELETE ON assessment_refs
    BEGIN SELECT RAISE(ABORT, 'references are append-only'); END;

    CREATE TRIGGER trg_ref_policies_no_update BEFORE UPDATE ON assessment_ref_policies
    BEGIN SELECT RAISE(ABORT, 'policies are fixed by migration'); END;
    CREATE TRIGGER trg_ref_policies_no_delete BEFORE DELETE ON assessment_ref_policies
    BEGIN SELECT RAISE(ABORT, 'policies are fixed by migration'); END;
    CREATE TRIGGER trg_ref_policies_no_insert BEFORE INSERT ON assessment_ref_policies
    BEGIN SELECT RAISE(ABORT, 'policies are fixed by migration'); END;

    -- --- 選定（最大3件） ---
    CREATE TRIGGER trg_selections_select BEFORE INSERT ON opportunity_selections
    WHEN NEW.action = 'select' AND (
        (SELECT status FROM hypotheses WHERE id = NEW.hypothesis_id) = 'rejected'
        OR NEW.hypothesis_id IN (SELECT hypothesis_id FROM current_opportunity_selections)
        OR (SELECT COUNT(*) FROM current_opportunity_selections) >= 3)
    BEGIN SELECT RAISE(ABORT, 'selection not allowed'); END;
    CREATE TRIGGER trg_selections_unselect BEFORE INSERT ON opportunity_selections
    WHEN NEW.action = 'unselect' AND NEW.hypothesis_id NOT IN (SELECT hypothesis_id FROM current_opportunity_selections)
    BEGIN SELECT RAISE(ABORT, 'not selected'); END;
    CREATE TRIGGER trg_selections_no_update BEFORE UPDATE ON opportunity_selections
    BEGIN SELECT RAISE(ABORT, 'selections are append-only'); END;
    CREATE TRIGGER trg_selections_no_delete BEFORE DELETE ON opportunity_selections
    BEGIN SELECT RAISE(ABORT, 'selections are append-only'); END;

    -- 選定中の仮説は棄却できない（先に選定を外す）
    CREATE TRIGGER trg_hypotheses_reject_selected BEFORE UPDATE OF status ON hypotheses
    WHEN NEW.status = 'rejected' AND OLD.status <> 'rejected'
     AND OLD.id IN (SELECT hypothesis_id FROM current_opportunity_selections)
    BEGIN SELECT RAISE(ABORT, 'unselect the opportunity before rejecting'); END;
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
