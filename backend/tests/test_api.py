"""API・保存処理のテスト。Hacker News API はモックし、外部通信は行わない。"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.sources import HackerNewsSource

NOW = 1_760_000_000  # 2025-10-09T08:53:20Z

ITEMS = {
    1: {"id": 1, "type": "story", "title": "OpenAI ships a new LLM", "url": "https://example.com/a", "time": NOW},
    2: {"id": 2, "type": "story", "title": "Ask HN: Who is hiring?", "text": "Post <i>jobs</i> here.<p>Thanks &amp; good luck", "time": NOW - 60},
    3: {"id": 3, "type": "comment", "text": "not a story", "time": NOW},
    4: {"id": 4, "type": "story", "title": "dead story", "dead": True, "time": NOW},
    5: None,  # 削除済みなどで null が返るケース
}


def mock_transport(fail_ids: bool = False, fail_items: set[int] = frozenset()):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/newstories.json"):
            if fail_ids:
                return httpx.Response(503)
            return httpx.Response(200, json=list(ITEMS))
        item_id = int(path.rsplit("/", 1)[1].removesuffix(".json"))
        if item_id in fail_items:
            return httpx.Response(500)
        if ITEMS[item_id] is None:
            return httpx.Response(200, content=b"null")
        return httpx.Response(200, json=ITEMS[item_id])

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))

    def use(transport):
        monkeypatch.setattr(main, "SOURCES", [HackerNewsSource(transport=transport)])

    with TestClient(main.app) as c:
        c.use_transport = use
        yield c


def test_fetch_saves_stories_and_skips_non_stories(client):
    client.use_transport(mock_transport())
    res = client.post("/api/fetch").json()
    assert res == {"fetched": 2, "inserted": 2, "errors": []}

    articles = client.get("/api/articles").json()["articles"]
    assert [a["external_id"] for a in articles] == ["1", "2"]  # 新しい順

    first, ask = articles
    assert first["category"] == "AI"
    assert first["source"] == "hackernews"
    assert first["source_label"] == "Hacker News"
    assert first["published_at"] == "2025-10-09T08:53:20+00:00"
    assert first["summary"] is None
    # URL の無い Ask HN は HN の記事ページを原文URLにし、本文を要約として保存する
    assert ask["url"] == "https://news.ycombinator.com/item?id=2"
    assert ask["summary"] == "Post jobs here.\nThanks & good luck"


def test_fetch_twice_does_not_duplicate(client):
    client.use_transport(mock_transport())
    client.post("/api/fetch")
    res = client.post("/api/fetch").json()
    assert res["inserted"] == 0
    assert len(client.get("/api/articles").json()["articles"]) == 2


def test_filter_by_category(client):
    client.use_transport(mock_transport())
    client.post("/api/fetch")
    articles = client.get("/api/articles", params={"category": "AI"}).json()["articles"]
    assert [a["external_id"] for a in articles] == ["1"]


def test_id_list_failure_returns_error_without_crashing(client):
    client.use_transport(mock_transport(fail_ids=True))
    res = client.post("/api/fetch")
    assert res.status_code == 200
    body = res.json()
    assert body["fetched"] == 0
    assert len(body["errors"]) == 1
    assert client.get("/api/articles").json()["articles"] == []


def test_single_item_failure_is_skipped(client):
    client.use_transport(mock_transport(fail_items={1}))
    body = client.post("/api/fetch").json()
    assert body["inserted"] == 1
    assert len(body["errors"]) == 1
    assert "item 1" in body["errors"][0]


def test_unexpected_source_exception_is_contained(client, monkeypatch):
    class Broken:
        name, label = "broken", "Broken"

        async def fetch(self, limit):
            raise RuntimeError("boom")

    monkeypatch.setattr(main, "SOURCES", [Broken()])
    res = client.post("/api/fetch")
    assert res.status_code == 200
    assert res.json()["errors"] == ["Broken: 予期しないエラー (boom)"]
