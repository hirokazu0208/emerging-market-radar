from .base import FetchResult, RawArticle, Source
from .egov_pcm import EgovPublicCommentSource
from .feed import FREEE_DEVELOPERS, SHOPIFY_CHANGELOG, FeedConfig, FeedSource
from .hackernews import HackerNewsSource

# 有効な情報源の一覧。情報源を追加する場合はここに登録する。
# 公式フィードは FeedConfig を追加して FeedSource で包む（Atlassian はフィードURL・取得条件の確認後に追加）。
SOURCES: list[Source] = [
    HackerNewsSource(),
    EgovPublicCommentSource(),
    FeedSource(SHOPIFY_CHANGELOG),
    FeedSource(FREEE_DEVELOPERS),
]

__all__ = [
    "FetchResult", "RawArticle", "Source", "SOURCES",
    "HackerNewsSource", "EgovPublicCommentSource", "FeedConfig", "FeedSource",
]
