from .base import FetchResult, RawArticle, Source
from .egov_pcm import EgovPublicCommentSource
from .hackernews import HackerNewsSource

# 有効な情報源の一覧。情報源を追加する場合はここに登録する。
SOURCES: list[Source] = [HackerNewsSource(), EgovPublicCommentSource()]

__all__ = [
    "FetchResult", "RawArticle", "Source", "SOURCES",
    "HackerNewsSource", "EgovPublicCommentSource",
]
