"""情報源アダプタの共通インターフェース。

新しい情報源（PR TIMES、経済産業省など）を追加する場合は、
`Source` を実装したクラスを作り、`sources/__init__.py` の `SOURCES` に登録する。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class RawArticle:
    """情報源から取得した、分類前の記事データ。"""

    external_id: str  # 情報源内で一意なID
    title: str
    url: str
    published_at: datetime  # タイムゾーン付き（UTC）
    summary: str | None = None  # 公開APIから取得できる場合のみ


@dataclass
class FetchResult:
    articles: list[RawArticle]
    errors: list[str]


class Source(Protocol):
    name: str  # DBの source 列に保存される識別子（例: "hackernews"）
    label: str  # 画面表示用の名称（例: "Hacker News"）

    async def fetch(self, limit: int) -> FetchResult:
        """最新記事を取得する。外部APIエラーは例外ではなく errors に入れて返す。"""
        ...
