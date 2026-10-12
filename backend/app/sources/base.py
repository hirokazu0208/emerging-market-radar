"""情報源アダプタの共通インターフェース。

新しい情報源（PR TIMES、経済産業省など）を追加する場合は、
`Source` を実装したクラスを作り、`sources/__init__.py` の `SOURCES` に登録する。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class RawArticle:
    """情報源から取得した、分類前の記事データ。"""

    external_id: str  # 情報源内で一意なID
    title: str
    url: str
    published_at: datetime  # タイムゾーン付き
    summary: str | None = None  # 公開APIから取得できる場合のみ
    category: str | None = None  # 情報源側で分類が確定している場合のみ（未指定ならキーワード分類）
    metadata: dict[str, str] = field(default_factory=dict)  # 情報源固有の項目（締切日など）


@dataclass
class FetchResult:
    articles: list[RawArticle]
    errors: list[str]


class Source(Protocol):
    name: str  # DBの source 列に保存される識別子（例: "hackernews"）
    label: str  # 画面表示用の名称（例: "Hacker News"）
    country: str  # 情報源の所在地（ISO 3166-1 alpha-2）。記事の対象市場とは別
    kind: str  # 'tech_news' | 'press_release' | 'government' | 'policy'
    language: str  # 記事の言語（'en' / 'ja'）
    default_market: str | None  # 記事の対象市場の既定値。判断できない情報源は None（未判定）
    homepage: str
    license: str | None  # 利用条件（例: "PDL1.0"）
    attribution: str | None  # 画面・README に表示する出典表記
    terms_url: str | None  # 利用条件のページ
    commercial_use: Literal["allowed", "restricted", "unknown"]  # 確認できていなければ "unknown"
    terms_checked_on: str | None  # 利用条件を確認した日（YYYY-MM-DD）

    async def fetch(self, limit: int) -> FetchResult:
        """最新記事を取得する。外部APIエラーは例外ではなく errors に入れて返す。"""
        ...
