from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .classifier import CATEGORIES, classify
from .sources import SOURCES

FETCH_LIMIT = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db(SOURCES)
    yield


app = FastAPI(title="Emerging Market Radar API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # ローカル開発用：Next.js がポート3000以外で起動した場合も許可する
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/categories")
def categories():
    return {"categories": CATEGORIES}


@app.get("/api/sources")
def sources():
    """情報源の一覧（所在地・利用条件・出典表記）。"""
    return {"sources": db.list_sources()}


@app.get("/api/articles")
def articles(
    category: str | None = None,
    market: db.MarketFilter | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    return {"articles": db.list_articles(category=category, market=market, limit=limit)}


@app.post("/api/fetch")
async def fetch():
    """全情報源から最新記事を取得して保存する。一部の失敗は errors に入れて返す。"""
    fetched = inserted = 0
    errors: list[str] = []
    for source in SOURCES:
        try:
            result = await source.fetch(FETCH_LIMIT)
        except Exception as e:  # 1つの情報源の想定外エラーで全体を止めない
            errors.append(f"{source.label}: 予期しないエラー ({e})")
            continue
        errors.extend(result.errors)
        fetched += len(result.articles)
        inserted += db.save_articles(
            source, [(a, a.category or classify(a.title)) for a in result.articles]
        )
    return {"fetched": fetched, "inserted": inserted, "errors": errors}
