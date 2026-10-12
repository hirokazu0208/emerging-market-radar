"""キーワード判定の実行（CLI）・再計算・人手判定の保護・API のテスト。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import analyze, db, main, signals
from app.labels import add_article_label, list_article_labels
from app.sources import SOURCES

ARTICLES = [
    # (source, external_id, title, summary, published_at)
    ("hackernews", "1", "Acme raises $12M Series A to automate invoicing", None, "2026-10-12T03:00:00+00:00"),
    ("hackernews", "2", "The Ethiopian Economic Miracle", None, "2026-10-12T02:00:00+00:00"),
    ("egov_pcm", "100", "「火薬類取締法施行規則の一部を改正する省令（案）」等に対する意見の募集について",
     "カテゴリー：工業", "2026-10-12T01:00:00+00:00"),
    ("hackernews", "3", "Show HN: Writing a Game in C", None, "2026-10-12T00:00:00+00:00"),
]


@pytest.fixture
def ids(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db(SOURCES)
    result = {}
    with db.connect() as conn:
        for source, ext, title, summary, published in ARTICLES:
            cur = conn.execute(
                "INSERT INTO articles (source, external_id, title, url, summary, published_at, category, fetched_at, language) "
                "VALUES (?, ?, ?, 'https://example.com', ?, ?, 'Other', ?, 'en')",
                (source, ext, title, summary, published, published),
            )
            result[ext] = cur.lastrowid
        conn.execute(
            "INSERT INTO article_markets (article_id, market, method, created_at) VALUES (?, 'JP', 'source_default', 't')",
            (result["100"],),
        )
    return result


def keyword_rows():
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT article_id, label, strength, rule_version FROM article_labels WHERE method = 'keyword' ORDER BY article_id, label"
        )]


def snapshot():
    with db.connect() as conn:
        return ([tuple(r) for r in conn.execute("SELECT * FROM articles ORDER BY id")],
                [tuple(r) for r in conn.execute("SELECT * FROM article_markets ORDER BY article_id")])


def test_run_labels_articles_and_reports_counts(ids):
    result = analyze.run()
    assert (result.analyzed, result.labeled_articles, result.unlabeled_articles) == (4, 2, 2)
    assert result.labels_created == 2
    assert result.by_label == {("funding", "explicit"): 1, ("regulation", "explicit"): 1}
    assert keyword_rows() == [
        (ids["1"], "funding", "explicit", signals.RULESET_VERSION),
        (ids["100"], "regulation", "explicit", signals.RULESET_VERSION),
    ]
    egov = list_article_labels(ids["100"])[0]
    assert egov["method"] == "keyword" and egov["model"] is None
    assert signals.EGOV_NOTE in egov["evidence"]


def test_rerun_does_not_duplicate(ids):
    analyze.run()
    second = analyze.run()
    assert second.labels_replaced == 2 and second.labels_created == 2
    assert len(keyword_rows()) == 2


def test_rule_change_recalculates_old_keyword_labels(ids, monkeypatch):
    analyze.run()
    # ルールを変更：funding のルールを外し、版を上げる
    monkeypatch.setattr(signals, "RULES", tuple(r for r in signals.RULES if r.label != "funding"))
    monkeypatch.setattr(analyze, "RULESET_VERSION", "kw-test.2")
    analyze.run()
    assert keyword_rows() == [(ids["100"], "regulation", "explicit", "kw-test.2")]


def test_manual_and_ai_labels_are_preserved(ids):
    add_article_label(ids["1"], "signal_type", "funding", "manual", evidence="人手で確認")
    add_article_label(ids["2"], "signal_type", "expansion", "ai", model="model-x")
    add_article_label(ids["1"], "software_domain", "finance", "manual")
    analyze.run()
    analyze.run()
    with db.connect() as conn:
        others = [tuple(r) for r in conn.execute(
            "SELECT article_id, taxonomy, label, method, model FROM article_labels WHERE method <> 'keyword' ORDER BY article_id, label"
        )]
    assert others == [
        (ids["1"], "software_domain", "finance", "manual", None),
        (ids["1"], "signal_type", "funding", "manual", None),
        (ids["2"], "signal_type", "expansion", "ai", "model-x"),
    ]


def test_articles_and_markets_are_not_modified(ids):
    before = snapshot()
    analyze.run()
    assert snapshot() == before


def test_limit_processes_newest_first(ids):
    result = analyze.run(limit=1)
    assert result.analyzed == 1
    assert keyword_rows() == [(ids["1"], "funding", "explicit", signals.RULESET_VERSION)]


def test_dry_run_writes_nothing(ids):
    result = analyze.run(dry_run=True)
    assert result.labels_created == 2
    assert keyword_rows() == []
    assert "dry-run" in analyze.format_result(result)


def test_cli_output_contains_counts_and_disclaimer(ids, capsys):
    analyze.main(["--limit", "4"])
    out = capsys.readouterr().out
    assert "分析件数：4" in out
    assert "未判定（根拠なし）の記事：2" in out
    assert "付与したラベル：2" in out
    assert "事実確認済みを意味しません" in out
    assert "予測・確定するものではありません" in out


def test_cli_rejects_invalid_limit(ids):
    with pytest.raises(SystemExit):
        analyze.main(["--limit", "0"])


# --- API -------------------------------------------------------------------

@pytest.fixture
def client(ids):
    with TestClient(main.app) as c:
        yield c


def test_articles_api_includes_signals_and_unlabeled_articles(client, ids):
    analyze.run()
    articles = {a["external_id"]: a for a in client.get("/api/articles").json()["articles"]}
    assert len(articles) == 4  # ラベルの無い記事も表示する
    assert articles["2"]["signals"] == []
    funding = articles["1"]["signals"]
    assert [(s["label"], s["method"], s["strength"]) for s in funding] == [("funding", "keyword", "explicit")]
    assert "raises $12M" in funding[0]["evidence"]


def test_articles_api_filters_by_signal_and_keeps_market_filter(client, ids):
    analyze.run()
    ids_of = lambda **p: [a["external_id"] for a in client.get("/api/articles", params=p).json()["articles"]]
    assert ids_of(signal="funding") == ["1"]
    assert ids_of(signal="regulation", market="home") == ["100"]
    assert ids_of(signal="regulation", market="overseas") == []
    assert ids_of(signal="partnership") == []


def test_articles_api_rejects_unknown_signal(client):
    assert client.get("/api/articles", params={"signal": "growth"}).status_code == 422
    assert client.get("/api/articles", params={"signal": "marketing"}).status_code == 422  # software_domain は対象外


def test_signal_types_endpoint(client):
    assert client.get("/api/signal-types").json()["signal_types"] == [
        "new_service", "market_entry", "partnership", "funding", "expansion", "new_segment", "regulation",
    ]


# --- マイグレーション v3 → v4 ---------------------------------------------------

def test_v3_db_migrates_to_v4_preserving_labels(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.BASE_SCHEMA)
    for v in (2, 3):
        conn.executescript(f"BEGIN;\n{db.MIGRATIONS[v]}\nPRAGMA user_version = {v};\nCOMMIT;")
    conn.executescript("""
        INSERT INTO articles (id, source, external_id, title, url, published_at, category, fetched_at, language)
          VALUES (1, 'hackernews', '1', 't', 'u', '2026-10-12T00:00:00+00:00', 'Other', 'f', 'en');
        INSERT INTO article_labels (article_id, taxonomy, label, method, evidence, model, created_at)
          VALUES (1, 'signal_type', 'funding', 'manual', '人手', NULL, 'c');
    """)
    conn.close()
    monkeypatch.setenv("RADAR_DB_PATH", str(path))

    db.init_db(SOURCES)

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute(
        "SELECT article_id, label, method, evidence, strength, rule_version FROM article_labels"
    ).fetchall() == [(1, "funding", "manual", "人手", None, None)]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE article_labels SET strength = 'certain'")
    conn.close()
