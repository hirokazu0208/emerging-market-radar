"""e-Gov パブリック・コメント（意見募集案件）RSS からの取得処理。

RSS 1.0（RDF）形式。APIキー不要。公共データ利用規約（PDL1.0）に基づき利用する。
https://public-comment.e-gov.go.jp/contents/terms
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from .base import FetchResult, RawArticle

FEED_URL = "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"
TIMEOUT = httpx.Timeout(10.0)
JST = timezone(timedelta(hours=9))

_NS = {"rss": "http://purl.org/rss/1.0/", "dc": "http://purl.org/dc/elements/1.1/"}

# e-Gov の dc:date は "2026-10-09T15:00:Z" のように秒が欠けることがあるため独自に解析する
_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?|:)?(Z|[+-]\d{2}:?\d{2})?$"
)
_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# description 内のラベル → metadata のキー
_FIELDS = {
    "案の公示日": "announced_on",
    "受付締切日時": "deadline",
    "カテゴリー": "egov_category",
    "問合せ先（所管省庁・部局名等）": "contact",
}


def parse_datetime(value: str) -> datetime | None:
    """e-Gov の dc:date を解析する。タイムゾーン指定が無い場合は日本時間とみなす。"""
    m = _DATETIME_RE.match(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, tz = m.groups()
    if tz is None:
        tzinfo = JST
    elif tz == "Z":
        tzinfo = timezone.utc
    else:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        tzinfo = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute),
                        int(second or 0), tzinfo=tzinfo)
    except ValueError:
        return None


def parse_description(value: str) -> tuple[str, dict[str, str]]:
    """description（<br/> 区切りの「ラベル：値」）を、要約テキストと metadata に分ける。"""
    lines = []
    metadata: dict[str, str] = {}
    for part in _BR_RE.split(value):
        text = " ".join(_TAG_RE.sub("", part).split())  # 改行・全角空白を含む連続空白を1つに
        if not text:
            continue
        lines.append(text)
        label, sep, field_value = text.partition("：")
        if sep and label in _FIELDS:
            metadata[_FIELDS[label]] = field_value.strip()
    return "\n".join(lines), metadata


def parse_feed(xml_text: str | bytes) -> tuple[list[RawArticle], list[str]]:
    root = ET.fromstring(xml_text)
    articles: list[RawArticle] = []
    errors: list[str] = []
    for item in root.findall("rss:item", _NS):
        title = (item.findtext("rss:title", "", _NS)).strip()
        link = (item.findtext("rss:link", "", _NS)).strip()
        if not title or not link:
            continue
        summary, metadata = parse_description(item.findtext("rss:description", "", _NS))

        published_at = parse_datetime(item.findtext("dc:date", "", _NS))
        if published_at is None and (m := _DATE_RE.match(metadata.get("announced_on", ""))):
            # dc:date が解析できない場合は公示日（日本時間0時）で代用する
            published_at = datetime(*map(int, m.groups()), tzinfo=JST)
        if published_at is None:
            errors.append(f"e-Gov パブコメ: 公開日時を解析できないため除外しました ({title[:30]})")
            continue

        # 案件ID（URLの id パラメータ）を外部IDにする。取れない場合はURL全体
        external_id = parse_qs(urlsplit(link).query).get("id", [link])[0]
        articles.append(RawArticle(
            external_id=external_id,
            title=title,
            url=link,
            published_at=published_at,
            summary=summary or None,
            category="Policy & Regulation",  # 意見募集案件はすべて規制・政策の変更案
            metadata=metadata,
        ))
    return articles, errors


class EgovPublicCommentSource:
    name = "egov_pcm"
    label = "e-Gov パブリック・コメント"
    country = "JP"
    kind = "policy"
    language = "ja"
    default_market = "JP"  # 日本の法令・規制の改正案のため
    homepage = "https://public-comment.e-gov.go.jp/"
    license = "PDL1.0"
    attribution = (
        "出典：e-Govパブリック・コメント（https://public-comment.e-gov.go.jp/）"
        "（RSSを本アプリが加工して作成：書式変換・項目抽出・カテゴリー分類）"
    )
    terms_url = "https://public-comment.e-gov.go.jp/contents/terms"
    commercial_use = "allowed"  # PDL1.0：出典の記載と、加工した旨の明示が条件
    terms_checked_on = "2026-10-12"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # transport はテストでモックを差し込むためのもの
        self._transport = transport

    async def fetch(self, limit: int) -> FetchResult:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                resp = await client.get(FEED_URL)
                resp.raise_for_status()
            articles, errors = parse_feed(resp.content)
        except httpx.HTTPError as e:
            return FetchResult([], [f"e-Gov パブコメ: RSSの取得に失敗しました ({e})"])
        except ET.ParseError as e:
            return FetchResult([], [f"e-Gov パブコメ: RSSの解析に失敗しました ({e})"])
        return FetchResult(articles[:limit], errors)
