from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .classifier import CATEGORIES, classify
from .sources import SOURCES

FETCH_LIMIT = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Emerging Market Radar API", version="0.1.0", lifespan=lifespan)
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


@app.get("/api/articles")
def articles(
    category: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    source_labels = {s.name: s.label for s in SOURCES}
    rows = db.list_articles(category=category, limit=limit)
    for row in rows:
        row["source_label"] = source_labels.get(row["source"], row["source"])
    return {"articles": rows}


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
            source.name, [(a, classify(a.title)) for a in result.articles]
        )
    return {"fetched": fetched, "inserted": inserted, "errors": errors}
