"""公式フィード（FeedSource）の解析・重複防止・差分取得・安全対策のテスト。

フィードは構造だけを模した合成データを使う（実際のフィードの内容はリポジトリに含めない）。外部通信は行わない。
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.sources import SOURCES, FeedSource
from app.sources.feed import FREEE_DEVELOPERS, SHOPIFY_CHANGELOG, excerpt, external_id_of, parse_feed_bytes

T0 = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
CFG = replace(SHOPIFY_CHANGELOG, max_items=50)


def item(i, *, guid=True, link=True, date="Fri, 09 Oct 2026 16:00:00 +0000", cats=("API", "New"), desc=None):
    parts = [f"<title>Update {i}</title>"]
    if link:
        parts.append(f"<link>https://example.com/changelog/{i}</link>")
    if guid:
        parts.append(f'<guid isPermaLink="false">guid-{i}</guid>')
    if date is not None:
        parts.append(f"<pubDate>{date}</pubDate>")
    parts += [f"<category>{c}</category>" for c in cats]
    parts.append(f"<description><![CDATA[{desc or f'<p>Detail <b>{i}</b> &amp; more</p>'}]]></description>")
    return "<item>" + "".join(parts) + "</item>"


def rss(*items):
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
            + "".join(items) + "</channel></rss>").encode()


def dated(i):
    return (T0 - timedelta(hours=i)).strftime("%a, %d %b %Y %H:%M:%S +0000")


class Server:
    """ETag・Last-Modified に対応する（または対応しない）フィードのモック。"""

    def __init__(self, body: bytes, etag: str | None = '"v1"', last_modified: str | None = "Fri, 09 Oct 2026 03:14:10 GMT",
                 status: int = 200):
        self.body, self.etag, self.last_modified, self.status = body, etag, last_modified, status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.etag and request.headers.get("if-none-match") == self.etag:
            return httpx.Response(304)
        headers = {}
        if self.etag:
            headers["ETag"] = self.etag
        if self.last_modified:
            headers["Last-Modified"] = self.last_modified
        return httpx.Response(self.status, content=self.body, headers=headers)

    def transport(self):
        return httpx.MockTransport(self.handler)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db(SOURCES)
    return tmp_path


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def run(source, limit=30):
    return asyncio.run(source.fetch(limit))


def fetch_and_save(source, limit=30):
    """/api/fetch と同じ手順：取得 → 取得状態の保存 → 記事の保存。"""
    result = run(source, limit)
    if result.fetch_state:
        db.update_fetch_state(source.name, result.fetch_state)
    inserted = db.save_articles(source, [(a, a.category) for a in result.articles])
    return result, inserted


def count(source_name):
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM articles WHERE source = ?", (source_name,)).fetchone()[0]


# --- 解析 ---------------------------------------------------------------------

def test_parse_rss2_stores_facts_only():
    articles, errors = parse_feed_bytes(rss(item(1, desc="<p>" + "x" * 1000 + "</p>"), item(2)), CFG)
    assert errors == [] and len(articles) == 2
    a = articles[0]
    assert (a.external_id, a.title, a.url) == ("guid-1", "Update 1", "https://example.com/changelog/1")
    assert a.published_at == datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
    assert len(a.summary) <= CFG.excerpt_chars + 1 and a.summary.endswith("…")  # 全文は保存しない
    assert a.metadata == {"platform": "shopify", "feed_categories": ["API", "New"]}
    assert a.category == "Developer Tools"
    assert articles[1].summary == "Detail 2 & more"  # HTML を除去し、実体参照を戻す


def test_external_id_fallbacks():
    articles, _ = parse_feed_bytes(rss(item(1, guid=False), item(2, guid=False, link=False)), CFG)
    assert articles[0].external_id == "https://example.com/changelog/1"  # guid が無ければ URL
    assert len(articles) == 1  # URL も guid も無い項目は、元記事へ辿れないので除外
    assert external_id_of("", "", "t", "d").startswith("sha256:")


def test_items_with_bad_dates_or_no_title_are_skipped_with_errors():
    body = rss(item(1, date="not a date"), item(2, date=None), item(3, date="Fri, 09 Oct 2026 16:00:00"),
               "<item><link>https://example.com/x</link><pubDate>Fri, 09 Oct 2026 16:00:00 +0000</pubDate></item>", item(4))
    articles, errors = parse_feed_bytes(body, CFG)
    assert [a.external_id for a in articles] == ["guid-4"]
    assert len(errors) == 4  # 日時なし・不正・タイムゾーンなし・タイトルなし（推測で補わない）


def test_categories_are_deduplicated_and_limited():
    cats = ["API"] * 3 + [f"c{i}" for i in range(20)]
    a = parse_feed_bytes(rss(item(1, cats=cats)), CFG)[0][0]
    assert a.metadata["feed_categories"][0] == "API" and len(a.metadata["feed_categories"]) == CFG.max_categories


@pytest.mark.parametrize(
    "body, message",
    [
        (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel/></rss>', "DOCTYPE"),
        (b"<rss><channel><item><title>x</title>", "解析できません"),
        (b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>', "対応していない"),
        (b"<rss></rss>", "channel"),
    ],
)
def test_unsafe_or_malformed_xml_is_rejected(body, message):
    from app.sources.feed import FeedError
    with pytest.raises(FeedError, match=message):
        parse_feed_bytes(body, CFG)


def test_excerpt_none_for_empty():
    assert excerpt("<p> </p>", 10) is None


# --- 取得・保存・重複防止 ---------------------------------------------------------

def test_first_fetch_takes_latest_50_and_second_adds_only_new(tmp_db):
    server = Server(rss(*[item(i, date=dated(i)) for i in range(80)]), etag=None, last_modified=None)  # 条件付き取得に未対応
    clock = Clock()
    source = FeedSource(CFG, transport=server.transport(), clock=clock)
    result, inserted = fetch_and_save(source, limit=50)
    assert inserted == 50 and count("shopify_changelog") == 50
    assert result.articles[0].external_id == "guid-0"  # 新しい順
    clock.now += timedelta(minutes=61)
    server.body = rss(*[item(i, date=dated(i)) for i in range(-3, 80)])  # 新しい3件が増えた
    _, inserted = fetch_and_save(source, limit=50)
    assert inserted == 3 and count("shopify_changelog") == 53  # 未登録の記事だけ追加
    clock.now += timedelta(minutes=61)
    _, inserted = fetch_and_save(source, limit=50)
    assert inserted == 0  # 条件付き取得に未対応でも重複しない


def test_etag_and_last_modified_are_sent_and_304_does_nothing(tmp_db):
    server = Server(rss(item(1)))
    clock = Clock()
    source = FeedSource(CFG, transport=server.transport(), clock=clock)
    fetch_and_save(source)
    state = db.get_fetch_state("shopify_changelog")
    assert (state["fetch_etag"], state["last_fetch_status"]) == ('"v1"', "ok")
    clock.now += timedelta(minutes=61)
    result, inserted = fetch_and_save(source)
    assert server.requests[-1].headers["if-none-match"] == '"v1"'
    assert server.requests[-1].headers["if-modified-since"] == "Fri, 09 Oct 2026 03:14:10 GMT"
    assert (inserted, result.errors) == (0, [])
    state = db.get_fetch_state("shopify_changelog")
    assert state["last_fetch_status"] == "not_modified" and state["fetch_etag"] == '"v1"'
    assert state["last_fetched_at"] == clock.now.isoformat()


def test_min_interval_skips_without_request(tmp_db):
    server = Server(rss(item(1)))
    clock = Clock()
    source = FeedSource(CFG, transport=server.transport(), clock=clock)
    fetch_and_save(source)
    clock.now += timedelta(minutes=59)
    result, _ = fetch_and_save(source)
    assert len(server.requests) == 1  # 60分以内は取得しない
    assert result.fetch_state["last_fetch_status"] == "skipped" and result.errors == []
    assert db.get_fetch_state("shopify_changelog")["last_fetched_at"] == T0.isoformat()  # 取得日時は変えない
    clock.now = T0 + timedelta(minutes=60)
    fetch_and_save(source)
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    "server, message",
    [
        (Server(b"x", status=500), "HTTP 500"),
        (Server(b"<rss><channel><item>", etag=None), "解析できません"),
        (Server(b"x" * 2000, etag=None), "上限"),
    ],
)
def test_errors_keep_existing_articles_and_allow_retry(tmp_db, server, message):
    good = Server(rss(item(1)), etag=None, last_modified=None)
    clock = Clock()
    fetch_and_save(FeedSource(CFG, transport=good.transport(), clock=clock))
    clock.now += timedelta(minutes=61)
    small = replace(CFG, max_bytes=1000)
    result, inserted = fetch_and_save(FeedSource(small, transport=server.transport(), clock=clock))
    assert inserted == 0 and message in result.errors[0]
    assert count("shopify_changelog") == 1  # 既存の記事は残る
    state = db.get_fetch_state("shopify_changelog")
    assert state["last_fetch_status"] == "error" and message in state["last_fetch_error"]
    assert state["last_fetched_at"] == T0.isoformat()  # 失敗時は取得日時を進めず、すぐ再試行できる


def test_declared_content_length_over_limit_is_rejected(tmp_db):
    def handler(request):
        return httpx.Response(200, content=b"<rss/>", headers={"Content-Length": "999999999"})
    result = run(FeedSource(CFG, transport=httpx.MockTransport(handler), clock=Clock()))
    assert "大きすぎます" in result.errors[0]


def test_timeout_is_reported(tmp_db):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    result = run(FeedSource(CFG, transport=httpx.MockTransport(handler), clock=Clock()))
    assert "タイムアウト" in result.errors[0] and result.fetch_state["last_fetch_status"] == "error"


# --- 情報源の登録と API ------------------------------------------------------------

def test_sources_are_registered_as_commercial_use_unknown(tmp_db):
    with db.connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources WHERE kind = 'platform_changelog'")}
    assert set(rows) == {"shopify_changelog", "freee_developers"}
    assert {r["commercial_use"] for r in rows.values()} == {"unknown"}
    assert rows["freee_developers"]["language"] == "ja" and rows["shopify_changelog"]["language"] == "en"
    assert all(r["attribution"] and "本文" in r["attribution"] for r in rows.values())


def test_fetch_api_saves_state_and_articles_without_duplicates(tmp_db, monkeypatch):
    shopify = Server(rss(item(1, date=dated(1)), item(2, date=dated(2))))
    freee = Server(rss(item(10, date=dated(1), cats=("新規機能・改善",))), etag=None, last_modified=None)
    clock = Clock()
    sources = [FeedSource(SHOPIFY_CHANGELOG, transport=shopify.transport(), clock=clock),
               FeedSource(FREEE_DEVELOPERS, transport=freee.transport(), clock=clock)]
    monkeypatch.setattr(main, "SOURCES", sources)
    with TestClient(main.app) as c:
        assert c.post("/api/fetch").json() == {"fetched": 3, "inserted": 3, "errors": []}
        clock.now += timedelta(minutes=61)
        assert c.post("/api/fetch").json()["inserted"] == 0  # Shopify は 304、freee は重複で 0
        articles = {a["source"]: a for a in c.get("/api/articles").json()["articles"]}
        assert articles["freee_developers"]["metadata"]["feed_categories"] == ["新規機能・改善"]
        assert articles["freee_developers"]["metadata"]["platform"] == "freee"
        assert articles["shopify_changelog"]["markets"] == []  # 対象市場は未判定（推測で付けない）
        status = {s["id"]: s["last_fetch_status"] for s in c.get("/api/sources").json()["sources"]}
        assert status["shopify_changelog"] == "not_modified" and status["freee_developers"] == "ok"


def test_feed_articles_get_keyword_signals_like_other_articles(tmp_db):
    from app import analyze
    server = Server(rss(item(1, date=dated(1)).replace("Update 1", "Shopify launches a new Billing API")))
    fetch_and_save(FeedSource(CFG, transport=server.transport(), clock=Clock()))
    analyze.run()
    with db.connect() as conn:
        labels = conn.execute("SELECT label, method FROM article_labels a JOIN articles r ON r.id = a.article_id "
                              "WHERE r.source = 'shopify_changelog'").fetchall()
    assert [tuple(l) for l in labels] == [("new_service", "keyword")]  # 観測の候補（事実の分類）。事業機会は作らない


def test_v7_db_migrates_to_v8_adding_columns_only(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "v7.db"
    monkeypatch.setenv("RADAR_DB_PATH", str(path))
    db.init_db(SOURCES)
    conn = sqlite3.connect(path)
    for c in db.FETCH_STATE_COLUMNS:
        conn.execute(f"ALTER TABLE sources DROP COLUMN {c}")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    kept = [r[1] for r in conn.execute("PRAGMA table_info(sources)") if r[1] != "updated_at"]  # updated_at は起動ごとに変わる
    before = conn.execute(f"SELECT {', '.join(kept)} FROM sources ORDER BY id").fetchall()
    conn.close()
    db.init_db(SOURCES)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sources)")]
    assert all(c in cols for c in db.FETCH_STATE_COLUMNS)
    assert conn.execute(f"SELECT {', '.join(kept)} FROM sources ORDER BY id").fetchall() == before  # 既存の値は保持
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_streamed_body_over_limit_is_aborted_without_content_length(tmp_db):
    # Content-Length を送らないサーバー（チャンク転送）でも、受信しながら数えて上限で中止する
    async def chunks():
        for _ in range(10):
            yield b"x" * 500

    def handler(request):
        return httpx.Response(200, content=chunks())
    result = run(FeedSource(replace(CFG, max_bytes=1000), transport=httpx.MockTransport(handler), clock=Clock()))
    assert "超えたため中止" in result.errors[0] and result.fetch_state["last_fetch_status"] == "error"


def test_fetch_api_uses_feed_specific_limit(tmp_db, monkeypatch):
    server = Server(rss(*[item(i, date=dated(i)) for i in range(80)]), etag=None, last_modified=None)
    monkeypatch.setattr(main, "SOURCES", [FeedSource(CFG, transport=server.transport(), clock=Clock())])
    with TestClient(main.app) as c:
        assert c.post("/api/fetch").json()["inserted"] == 50  # 共通の上限（30）ではなく、フィードの上限（50）
