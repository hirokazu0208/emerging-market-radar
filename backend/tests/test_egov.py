"""e-Gov パブリック・コメント RSS の解析・保存・市場絞り込みのテスト。外部通信は行わない。"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.sources import EgovPublicCommentSource, HackerNewsSource
from app.sources.egov_pcm import parse_datetime, parse_description, parse_feed

DETAIL = "https://public-comment.e-gov.go.jp/servlet/Public?CLASSNAME=PCMMSTDETAIL&amp;id={id}&amp;Mode=0"


def item(id_: str, title: str, date: str | None, description: str) -> str:
    date_xml = f"<dc:date>{date}</dc:date>" if date is not None else ""
    return f"""
  <item rdf:about="{DETAIL.format(id=id_)}">
    <title>{title}</title>
    <link>{DETAIL.format(id=id_)}</link>
    <description>{description}</description>
    {date_xml}
  </item>"""


# 実際の e-Gov RSS と同じ構造（RSS 1.0、description は &lt;br/&gt; 区切り、dc:date は秒なし）
FEED = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <channel rdf:about="https://public-comment.e-gov.go.jp/rss/pcm_list.xml">
    <title>パブリックコメント・意見募集案件一覧</title>
  </channel>
{item("100000001", "テスト省令案に関する御意見の募集について", "2026-10-09T15:00:Z",
       "案の公示日：2026/10/10&lt;br/&gt;受付締切日時：2026/11/09 09:00&lt;br/&gt;カテゴリー：厚生&lt;br/&gt;"
       "問合せ先（所管省庁・部局名等）：厚生労働省テスト局テスト課\n03-0000-0000　内線0000&lt;br/&gt;")}
{item("100000002", "テスト告示案についての意見募集", "不正な日付",
       "案の公示日：2026/10/08&lt;br/&gt;カテゴリー：工業&lt;br/&gt;")}
{item("100000003", "日時が取れない案件", None, "カテゴリー：工業&lt;br/&gt;")}
</rdf:RDF>"""


# --- 解析 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-09T15:00:Z", datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)),  # e-Gov 特有（秒なし）
        ("2026-10-09T15:00:30Z", datetime(2026, 10, 9, 15, 0, 30, tzinfo=timezone.utc)),
        ("2026-10-10T00:00+09:00", datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)),
        ("2026-10-10T00:00", datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)),  # TZなし → 日本時間
    ],
)
def test_parse_datetime(value, expected):
    assert parse_datetime(value) == expected


@pytest.mark.parametrize("value", ["", "不正な日付", "2026-13-40T99:00Z"])
def test_parse_datetime_invalid(value):
    assert parse_datetime(value) is None


def test_parse_description_extracts_fields():
    summary, metadata = parse_description(
        "案の公示日：2026/10/10<br/>受付締切日時：2026/11/09 09:00<br/>カテゴリー：厚生<br/>"
        "問合せ先（所管省庁・部局名等）：厚生労働省テスト局\n03-0000-0000　内線0000<br/>"
    )
    assert summary.splitlines() == [
        "案の公示日：2026/10/10",
        "受付締切日時：2026/11/09 09:00",
        "カテゴリー：厚生",
        "問合せ先（所管省庁・部局名等）：厚生労働省テスト局 03-0000-0000 内線0000",
    ]
    assert metadata == {
        "announced_on": "2026/10/10",
        "deadline": "2026/11/09 09:00",
        "egov_category": "厚生",
        "contact": "厚生労働省テスト局 03-0000-0000 内線0000",
    }


def test_parse_feed():
    articles, errors = parse_feed(FEED.encode())
    assert [a.external_id for a in articles] == ["100000001", "100000002"]

    first, second = articles
    assert first.title == "テスト省令案に関する御意見の募集について"
    assert first.url == "https://public-comment.e-gov.go.jp/servlet/Public?CLASSNAME=PCMMSTDETAIL&id=100000001&Mode=0"
    assert first.published_at == datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
    assert first.category == "Policy & Regulation"
    assert first.metadata["egov_category"] == "厚生"
    assert first.metadata["contact"].startswith("厚生労働省テスト局テスト課")
    # dc:date が不正な場合は公示日（日本時間0時）で代用する
    assert second.published_at == datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=9)))
    # 日時が全く取れない案件は除外し、理由を errors に残す
    assert len(errors) == 1 and "日時が取れない案件" in errors[0]


# --- 取得・保存 ----------------------------------------------------------

HN_ITEM = {"id": 1, "type": "story", "title": "OpenAI ships a new LLM", "url": "https://example.com/a", "time": 1_760_000_000}


def transport(egov_status: int = 200, egov_body: bytes = FEED.encode()):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public-comment.e-gov.go.jp":
            return httpx.Response(egov_status, content=egov_body)
        if request.url.path.endswith("/newstories.json"):
            return httpx.Response(200, json=[1])
        return httpx.Response(200, json=HN_ITEM)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))

    def use(t):
        monkeypatch.setattr(main, "SOURCES", [HackerNewsSource(transport=t), EgovPublicCommentSource(transport=t)])

    with TestClient(main.app) as c:
        c.use_transport = use
        yield c


def ids(client, **params):
    return [a["external_id"] for a in client.get("/api/articles", params=params).json()["articles"]]


def test_fetch_saves_egov_with_market_and_metadata(client):
    client.use_transport(transport())
    body = client.post("/api/fetch").json()
    assert body["inserted"] == 3  # HN 1件 + e-Gov 2件
    assert len(body["errors"]) == 1  # 日時が取れない e-Gov 案件

    egov = next(a for a in client.get("/api/articles").json()["articles"] if a["external_id"] == "100000001")
    assert egov["source"] == "egov_pcm"
    assert egov["source_label"] == "e-Gov パブリック・コメント"
    assert egov["source_country"] == "JP"  # 情報源の所在地
    assert egov["markets"] == [{"market": "JP", "method": "source_default"}]  # 記事の対象市場と根拠
    assert egov["language"] == "ja"
    assert egov["published_at"] == "2026-10-09T15:00:00+00:00"
    assert egov["category"] == "Policy & Regulation"
    assert egov["metadata"]["deadline"] == "2026/11/09 09:00"
    assert egov["fetched_at"]


def test_market_filter_keeps_undetermined_out_of_overseas(client):
    client.use_transport(transport())
    client.post("/api/fetch")

    hn = next(a for a in client.get("/api/articles").json()["articles"] if a["source"] == "hackernews")
    assert hn["source_country"] == "US"
    assert hn["markets"] == []  # 対象市場は未判定（海外と断定しない）

    assert sorted(ids(client)) == ["1", "100000001", "100000002"]  # すべて
    assert sorted(ids(client, market="home")) == ["100000001", "100000002"]  # 日本
    assert ids(client, market="overseas") == []  # 海外：未判定の HN は含めない


def test_invalid_market_param_is_rejected(client):
    assert client.get("/api/articles", params={"market": "mars"}).status_code == 422


def test_fetch_twice_does_not_duplicate_articles_or_markets(client):
    client.use_transport(transport())
    client.post("/api/fetch")
    assert client.post("/api/fetch").json()["inserted"] == 0
    articles = client.get("/api/articles", params={"market": "home"}).json()["articles"]
    assert len(articles) == 2
    assert all(len(a["markets"]) == 1 for a in articles)


@pytest.mark.parametrize(
    "status, body, message",
    [(503, b"", "RSSの取得に失敗"), (200, b"<rdf:RDF><broken", "RSSの解析に失敗")],
)
def test_rss_error_keeps_existing_articles(client, status, body, message):
    client.use_transport(transport())
    client.post("/api/fetch")

    client.use_transport(transport(egov_status=status, egov_body=body))
    res = client.post("/api/fetch")
    assert res.status_code == 200
    assert any(message in e for e in res.json()["errors"])
    assert sorted(ids(client, market="home")) == ["100000001", "100000002"]


def test_sources_endpoint_includes_attribution(client):
    sources = {s["id"]: s for s in client.get("/api/sources").json()["sources"]}
    assert sources["egov_pcm"]["license"] == "PDL1.0"
    assert "出典：e-Govパブリック・コメント" in sources["egov_pcm"]["attribution"]
    assert "加工" in sources["egov_pcm"]["attribution"]
    assert sources["hackernews"]["country"] == "US"
