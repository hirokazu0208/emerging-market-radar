"""公式フィード（RSS 2.0）から、プラットフォームの変更情報を取得する汎用の情報源。

保存するのは事実（タイトル・元記事のURL・公開日時・短い抜粋・公式カテゴリー）だけで、本文は保存しない。
利用条件を確認できていない情報源は commercial_use を "unknown" とする。

安全対策
- タイムアウト、レスポンスサイズの上限（受信しながら数え、超えたら中止）
- DOCTYPE・ENTITY を含む XML は拒否する（フィードには不要で、エンティティ展開攻撃を防ぐ）
- 解析は標準ライブラリのみ。壊れた XML・HTTP エラーは errors に入れて返し、既存の記事は残す

差分取得
- 前回の ETag・Last-Modified を送り、304 なら何もしない
- 対応していないフィードでも、外部ID（guid → URL → タイトルと日時のハッシュ）で重複を防ぐ
- 前回の取得（成功・304）から最短間隔が経っていなければ取得しない

形式の追加（Atom など）は PARSERS にルート要素ごとの解析関数を登録する。
"""

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .base import FetchResult, RawArticle

USER_AGENT = "EmergingMarketRadar/0.8 (+local; personal research)"
_TAG = re.compile(r"<[^>]+>")
_FORBIDDEN_XML = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


@dataclass(frozen=True)
class FeedConfig:
    name: str
    label: str
    url: str
    country: str
    language: str
    homepage: str
    attribution: str
    platform: str                      # 監視対象の市場（例：'shopify'）。記事の metadata.platform に入る
    default_market: str | None = None  # 記事の対象市場の既定値（判断できなければ None＝未判定）
    kind: str = "platform_changelog"
    category: str = "Developer Tools"
    license: str | None = None
    terms_url: str | None = None
    commercial_use: str = "unknown"    # 利用条件を公式に確認できていない
    terms_checked_on: str | None = None
    max_items: int = 50
    max_bytes: int = 5_000_000
    timeout_seconds: float = 20.0
    excerpt_chars: int = 280
    min_interval: timedelta = timedelta(minutes=60)
    max_categories: int = 10


class FeedError(ValueError):
    pass


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def excerpt(raw_html: str, limit: int) -> str | None:
    """HTML を取り除き、空白を整えて先頭 limit 文字だけにする（全文は保存しない）。"""
    text = " ".join(html.unescape(_TAG.sub(" ", raw_html or "")).split())
    if not text:
        return None
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def parse_rfc822(value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None or dt.tzinfo is None:
        return None  # タイムゾーンの無い日時は推測で補わない
    return dt


def external_id_of(guid: str, link: str, title: str, published: str) -> str:
    """外部ID：guid → URL → タイトルと公開日時のハッシュ。"""
    if guid:
        return guid
    if link:
        return link
    return "sha256:" + hashlib.sha256(f"{title}\n{published}".encode()).hexdigest()


def parse_rss2(root: ET.Element, config: FeedConfig) -> tuple[list[RawArticle], list[str]]:
    channel = root.find("channel")
    if channel is None:
        raise FeedError("RSS 2.0 の channel 要素がありません")
    articles, errors = [], []
    for item in channel.findall("item"):
        title = " ".join(_text(item.find("title")).split())
        link = _text(item.find("link"))
        guid = _text(item.find("guid"))
        published_raw = _text(item.find("pubDate"))
        if not title:
            errors.append(f"{config.label}: タイトルの無い項目を除外しました")
            continue
        url = link or (guid if guid.startswith(("http://", "https://")) else "")
        if not url.startswith(("http://", "https://")):
            errors.append(f"{config.label}: 元記事のURLが無い項目を除外しました（{title[:40]}）")
            continue
        published = parse_rfc822(published_raw)
        if published is None:
            errors.append(f"{config.label}: 公開日時を解析できない項目を除外しました（{title[:40]}）")
            continue
        categories: list[str] = []
        for c in item.findall("category"):
            name = " ".join(_text(c).split())
            if name and name not in categories:
                categories.append(name)
        metadata: dict[str, Any] = {"platform": config.platform}
        if categories:
            metadata["feed_categories"] = categories[:config.max_categories]
        articles.append(RawArticle(
            external_id=external_id_of(guid, link, title, published_raw),
            title=title,
            url=url,
            published_at=published,
            summary=excerpt(_text(item.find("description")), config.excerpt_chars),
            category=config.category,
            metadata=metadata,
        ))
    return articles, errors


# ルート要素名 → 解析関数。Atom（{http://www.w3.org/2005/Atom}feed）などはここに追加する
PARSERS: dict[str, Callable[[ET.Element, FeedConfig], tuple[list[RawArticle], list[str]]]] = {
    "rss": parse_rss2,
}


def parse_feed_bytes(body: bytes, config: FeedConfig) -> tuple[list[RawArticle], list[str]]:
    if _FORBIDDEN_XML.search(body):
        raise FeedError("DOCTYPE・ENTITY を含む XML は受け付けません")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise FeedError(f"XML を解析できません（{e}）") from e
    parser = PARSERS.get(root.tag)
    if parser is None:
        raise FeedError(f"対応していないフィード形式です（{root.tag}）")
    return parser(root, config)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FeedSource:
    """FeedConfig から作る情報源。Source プロトコルの属性は config から取る。"""

    def __init__(
        self,
        config: FeedConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        state_loader: Callable[[str], dict] | None = None,
    ):
        self.config = config
        self._transport = transport  # テストでモックを差し込む
        self._clock = clock          # テストで時刻を制御する
        self._state_loader = state_loader

    # Source プロトコルの属性
    name = property(lambda self: self.config.name)
    label = property(lambda self: self.config.label)
    country = property(lambda self: self.config.country)
    kind = property(lambda self: self.config.kind)
    language = property(lambda self: self.config.language)
    default_market = property(lambda self: self.config.default_market)
    homepage = property(lambda self: self.config.homepage)
    license = property(lambda self: self.config.license)
    attribution = property(lambda self: self.config.attribution)
    terms_url = property(lambda self: self.config.terms_url)
    commercial_use = property(lambda self: self.config.commercial_use)
    terms_checked_on = property(lambda self: self.config.terms_checked_on)
    fetch_limit = property(lambda self: self.config.max_items)  # /api/fetch が使う取得上限

    def _load_state(self) -> dict:
        if self._state_loader is not None:
            return self._state_loader(self.name)
        from .. import db  # 循環 import を避ける
        return db.get_fetch_state(self.name)

    def _error(self, message: str) -> FetchResult:
        # 失敗時は last_fetched_at を更新しない（次回すぐに再試行できる）。前回の ETag 等も残す
        return FetchResult([], [f"{self.label}: {message}"],
                           fetch_state={"last_fetch_status": "error", "last_fetch_error": message[:500]})

    async def fetch(self, limit: int) -> FetchResult:
        cfg = self.config
        now = self._clock()
        state = self._load_state()
        last = state.get("last_fetched_at")
        if last:
            elapsed = now - datetime.fromisoformat(last)
            if elapsed < cfg.min_interval:
                wait = int((cfg.min_interval - elapsed).total_seconds() // 60) + 1
                return FetchResult([], [], fetch_state={"last_fetch_status": "skipped", "last_fetch_error": None,
                                                        "skipped_reason": f"前回の取得から{cfg.min_interval}経っていません（あと約{wait}分）"})

        headers = {"User-Agent": USER_AGENT}
        if state.get("fetch_etag"):
            headers["If-None-Match"] = state["fetch_etag"]
        if state.get("fetch_last_modified"):
            headers["If-Modified-Since"] = state["fetch_last_modified"]

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout_seconds), transport=self._transport,
                                         follow_redirects=True, max_redirects=3) as client:
                async with client.stream("GET", cfg.url, headers=headers) as resp:
                    if resp.status_code == 304:
                        return FetchResult([], [], fetch_state={"last_fetch_status": "not_modified",
                                                                "last_fetched_at": now.isoformat(),
                                                                "last_fetch_error": None})
                    if resp.status_code != 200:
                        return self._error(f"HTTP {resp.status_code} が返りました")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > cfg.max_bytes:
                        return self._error(f"レスポンスが大きすぎます（{declared} バイト > 上限 {cfg.max_bytes}）")
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > cfg.max_bytes:
                            return self._error(f"レスポンスが上限（{cfg.max_bytes} バイト）を超えたため中止しました")
                        chunks.append(chunk)
                    etag, last_modified = resp.headers.get("etag"), resp.headers.get("last-modified")
        except httpx.TimeoutException:
            return self._error(f"タイムアウトしました（{cfg.timeout_seconds}秒）")
        except httpx.HTTPError as e:
            return self._error(f"取得に失敗しました（{e}）")

        try:
            articles, errors = parse_feed_bytes(b"".join(chunks), cfg)
        except FeedError as e:
            return self._error(str(e))

        articles.sort(key=lambda a: a.published_at, reverse=True)
        return FetchResult(articles[:min(limit, cfg.max_items)], errors, fetch_state={
            "last_fetch_status": "ok", "last_fetched_at": now.isoformat(), "last_fetch_error": None,
            "fetch_etag": etag, "fetch_last_modified": last_modified,
        })


SHOPIFY_CHANGELOG = FeedConfig(
    name="shopify_changelog",
    label="Shopify Developer Changelog",
    url="https://shopify.dev/changelog/feed.xml",
    country="CA",
    language="en",
    homepage="https://shopify.dev/changelog",
    platform="shopify",
    attribution="出典：Shopify Developer Changelog（https://shopify.dev/changelog）。タイトル・抜粋・公式カテゴリーのみを保存し、本文は元記事を参照",
)

FREEE_DEVELOPERS = FeedConfig(
    name="freee_developers",
    label="freee Developers",
    url="https://developer.freee.co.jp/feed",
    country="JP",
    language="ja",
    homepage="https://developer.freee.co.jp/",
    platform="freee",
    attribution="出典：freee Developers（https://developer.freee.co.jp/）。タイトル・抜粋・公式カテゴリーのみを保存し、本文は元記事を参照",
)
