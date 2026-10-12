"""article_labels の登録・取得と、DB 側の制約のテスト。"""

import sqlite3

import pytest

from app import db
from app.labels import TAXONOMIES, add_article_label, list_article_labels
from app.sources import EgovPublicCommentSource, HackerNewsSource


@pytest.fixture
def article_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db([HackerNewsSource(), EgovPublicCommentSource()])
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO articles (source, external_id, title, url, published_at, category, fetched_at, language) "
            "VALUES ('hackernews', '1', 'Acme launches invoicing API', 'https://example.com', "
            "'2026-10-12T00:00:00+00:00', 'Fintech & Crypto', '2026-10-12T00:00:00+00:00', 'en')"
        )
        return cur.lastrowid


def test_add_and_list_labels_with_assertion_kind(article_id):
    assert add_article_label(article_id, "signal_type", "new_service", "manual", evidence="Acme launches")
    assert add_article_label(article_id, "software_domain", "finance", "ai",
                             evidence="invoicing", model="claude-opus-5-5/prompt-v1")

    labels = list_article_labels(article_id)
    assert [(l["taxonomy"], l["label"], l["assertion_kind"], l["method"], l["model"]) for l in labels] == [
        ("signal_type", "new_service", "observation", "manual", None),
        ("software_domain", "finance", "hypothesis", "ai", "claude-opus-5-5/prompt-v1"),
    ]
    assert labels[0]["evidence"] == "Acme launches"
    assert labels[0]["created_at"]


def test_filter_observations_and_hypotheses(article_id):
    add_article_label(article_id, "signal_type", "funding", "manual")
    add_article_label(article_id, "software_domain", "sales", "manual")
    assert [l["label"] for l in list_article_labels(article_id, assertion_kind="observation")] == ["funding"]
    assert [l["label"] for l in list_article_labels(article_id, assertion_kind="hypothesis")] == ["sales"]
    assert [l["label"] for l in list_article_labels(article_id, taxonomy="software_domain")] == ["sales"]


def test_duplicate_label_is_not_registered_twice(article_id):
    assert add_article_label(article_id, "signal_type", "partnership", "manual")
    assert not add_article_label(article_id, "signal_type", "partnership", "manual")
    assert len(list_article_labels(article_id)) == 1


def test_ai_and_manual_judgements_are_kept_separately(article_id):
    # 同じラベルでも、人手と AI、AI のモデル違いは別の判定として残す
    assert add_article_label(article_id, "software_domain", "operations", "manual")
    assert add_article_label(article_id, "software_domain", "operations", "ai", model="model-a")
    assert add_article_label(article_id, "software_domain", "operations", "ai", model="model-b")
    assert not add_article_label(article_id, "software_domain", "operations", "ai", model="model-a")
    assert [(l["method"], l["model"]) for l in list_article_labels(article_id)] == [
        ("ai", "model-a"), ("ai", "model-b"), ("manual", None),
    ]


@pytest.mark.parametrize(
    "args, message",
    [
        (("unknown_taxonomy", "x", "manual", None), "taxonomy"),
        (("signal_type", "marketing", "manual", None), "label"),  # 別 taxonomy のラベル
        (("software_domain", "new_service", "manual", None), "label"),
        (("signal_type", "funding", "guess", None), "method"),
        (("signal_type", "funding", "ai", None), "model"),  # AI 判定は model 必須
        (("signal_type", "funding", "manual", "model-a"), "model"),  # 人手判定に model は付けない
    ],
)
def test_invalid_labels_are_rejected(article_id, args, message):
    taxonomy, label, method, model = args
    with pytest.raises(ValueError, match=message):
        add_article_label(article_id, taxonomy, label, method, model=model)
    assert list_article_labels(article_id) == []


def test_unknown_article_is_rejected(article_id):
    with pytest.raises(ValueError):
        add_article_label(article_id + 999, "signal_type", "funding", "manual")


@pytest.mark.parametrize(
    "values",
    [
        ("signal_type", "marketing", "manual", None),  # 語彙にない組み合わせ（外部キー）
        ("signal_type", "funding", "ai", None),  # AI 判定で model なし（CHECK）
        ("signal_type", "funding", "manual", "model-a"),  # 人手判定で model あり（CHECK）
        ("signal_type", "funding", "guess", None),  # 未定義の method（CHECK）
    ],
)
def test_db_constraints_reject_invalid_rows_even_without_validation(article_id, values):
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO article_labels (article_id, taxonomy, label, method, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'now')",
            (article_id, *values),
        )


def test_code_vocabulary_matches_db(article_id):
    with db.connect() as conn:
        rows = {(r["taxonomy"], r["label"], r["assertion_kind"]) for r in conn.execute("SELECT * FROM label_definitions")}
    expected = {(t, label, kind) for t, (kind, labels) in TAXONOMIES.items() for label in labels}
    assert rows == expected


def test_labels_are_deleted_with_article(article_id):
    add_article_label(article_id, "signal_type", "funding", "manual")
    with db.connect() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        assert conn.execute("SELECT COUNT(*) FROM article_labels").fetchone()[0] == 0


def test_constraint_violation_is_not_reported_as_duplicate(article_id, monkeypatch):
    # Python 側の検証をすり抜けても、DB の CHECK 違反は「重複（False）」ではなく例外になる
    monkeypatch.setattr("app.labels.validate", lambda *args: None)
    with pytest.raises(ValueError, match="CHECK"):
        add_article_label(article_id, "signal_type", "funding", "ai", model=None)
    assert list_article_labels(article_id) == []
