"""Hacker News 公式 Firebase API（APIキー不要）からの取得処理。

https://github.com/HackerNews/API
"""

import asyncio
import html
import re
from datetime import datetime, timezone

import httpx

from .base import FetchResult, RawArticle

API_BASE = "https://hacker-news.firebaseio.com/v0"
ITEM_PAGE = "https://news.ycombinator.com/item?id={id}"
TIMEOUT = httpx.Timeout(10.0)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(value: str) -> str:
    """HN の text フィールド（HTML）をプレーンテキストに変換する。"""
    text = value.replace("<p>", "\n")
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def parse_item(item: dict | None) -> RawArticle | None:
    """HN の item を RawArticle に変換する。記事として扱えないものは None。"""
    if not item or item.get("type") != "story":
        return None
    if item.get("deleted") or item.get("dead"):
        return None
    title = item.get("title")
    timestamp = item.get("time")
    if not title or timestamp is None:
        return None

    item_id = item["id"]
    text = item.get("text")
    return RawArticle(
        external_id=str(item_id),
        title=html.unescape(title),
        # Ask HN などは外部URLが無いため、HN の記事ページを原文とする
        url=item.get("url") or ITEM_PAGE.format(id=item_id),
        published_at=datetime.fromtimestamp(timestamp, tz=timezone.utc),
        summary=_html_to_text(text) if text else None,
    )


class HackerNewsSource:
    name = "hackernews"
    label = "Hacker News"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # transport はテストでモックを差し込むためのもの
        self._transport = transport

    async def fetch(self, limit: int) -> FetchResult:
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
            try:
                resp = await client.get(f"{API_BASE}/newstories.json")
                resp.raise_for_status()
                ids = resp.json()[:limit]
            except (httpx.HTTPError, ValueError) as e:
                return FetchResult([], [f"Hacker News: 新着ID一覧の取得に失敗しました ({e})"])

            async def get_item(item_id: int) -> dict | None:
                try:
                    r = await client.get(f"{API_BASE}/item/{item_id}.json")
                    r.raise_for_status()
                    return r.json()
                except (httpx.HTTPError, ValueError) as e:
                    errors.append(f"Hacker News: item {item_id} の取得に失敗しました ({e})")
                    return None

            items = await asyncio.gather(*(get_item(i) for i in ids))

        articles = [a for a in (parse_item(item) for item in items) if a]
        return FetchResult(articles, errors)
