from .base import FetchResult, RawArticle, Source
from .hackernews import HackerNewsSource

# 有効な情報源の一覧。情報源を追加する場合はここに登録する。
SOURCES: list[Source] = [HackerNewsSource()]

__all__ = ["FetchResult", "RawArticle", "Source", "SOURCES", "HackerNewsSource"]
