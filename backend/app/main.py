import os
import re
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .classifier import CATEGORIES, classify
from .labels import TAXONOMIES
from . import hypotheses as hyp
from . import opportunities as opp
from . import problems as problem_stats
from .sources import SOURCES

FETCH_LIMIT = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db(SOURCES)
    yield


# この API は認証が無く、ローカルでの利用（127.0.0.1 で起動）専用。外部に公開する前に認証が必要
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d+)?")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("RADAR_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

app = FastAPI(title="Emerging Market Radar API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # ローカル開発用：Next.js がポート3000以外で起動した場合も許可する
    allow_origin_regex=LOCAL_ORIGIN.pattern,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)
# Host ヘッダーを localhost に限定する（DNS リバインディングで外部のページから読み書きされることを防ぐ）
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def reject_foreign_origin_writes(request: Request, call_next):
    """書き込み系のリクエストは、ローカルの画面（または Origin の無い CLI 等）からのみ受け付ける。"""
    origin = request.headers.get("origin")
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and origin and not LOCAL_ORIGIN.fullmatch(origin):
        return JSONResponse({"detail": "ローカル以外のオリジンからの書き込みは受け付けません"}, status_code=403)
    return await call_next(request)


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
    signal: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    if signal and signal not in TAXONOMIES["signal_type"][1]:
        raise HTTPException(status_code=422, detail=f"未定義の signal です: {signal}")
    return {"articles": db.list_articles(category=category, market=market, signal=signal, limit=limit)}


@app.get("/api/signal-types")
def signal_types():
    """市場形成シグナル（signal_type）のラベル一覧。"""
    return {"signal_types": list(TAXONOMIES["signal_type"][1])}


@app.get("/api/problem-tables")
def problem_tables():
    """取り込み済みの統計表（出典・調査対象・加工内容・注意事項を含む）。"""
    return {"tables": problem_stats.list_tables()}


@app.get("/api/problems")
def problems(
    table_id: int | None = None,
    industry: str | None = None,
    problem: str | None = None,
):
    """業種別の経営課題。元データの値（value：‰）と、本アプリの算出値（computed_*）を分けて返す。

    table_id を省略すると、最新の調査年の表を返す。
    """
    industry, problem = industry or None, problem or None  # 空文字は「指定なし」
    if table_id is None:
        tables = problem_stats.list_tables()
        if not tables:
            return {"table": None, "problems": [], "industries": []}
        table_id = tables[0]["id"]
    try:
        result = problem_stats.get_problems(table_id, industry=industry, problem=problem)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if result is None:
        raise HTTPException(status_code=404, detail=f"統計表がありません: {table_id}")
    return result


# --- SaaS需要仮説 ----------------------------------------------------------------

class HypothesisCreate(BaseModel):
    domain: str
    title: str
    target_customer: str
    problem_statement: str
    solution_idea: str
    assumptions: str
    unknowns: str
    not_implied: str
    next_action: str


class HypothesisPatch(BaseModel):
    domain: str | None = None
    title: str | None = None
    target_customer: str | None = None
    problem_statement: str | None = None
    solution_idea: str | None = None
    assumptions: str | None = None
    unknowns: str | None = None
    not_implied: str | None = None
    next_action: str | None = None


class StatusChange(BaseModel):
    status: str
    reason: str | None = None


class EvidenceCreate(BaseModel):
    kind: str
    role: Literal["supporting", "contradicting"] | None = None
    description: str | None = None
    method_note: str | None = None
    url: str | None = None
    article_id: int | None = None
    stat_table_id: int | None = None
    industry_code: str | None = None
    problem_code: str | None = None
    contact_ref: str | None = None
    result: str | None = None
    occurred_on: str | None = None
    payment_signal: str | None = None


class Retraction(BaseModel):
    reason: str


def _hyp_call(fn, *args):
    try:
        return fn(*args)
    except hyp.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except hyp.InvalidRequest as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except hyp.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.get("/api/hypotheses")
def list_hypotheses(
    domain: str | None = None,
    status: str | None = None,
    origin: str | None = None,
    problem: str | None = None,
    industry: str | None = None,
):
    """需要仮説の一覧。domain_counts は領域以外の条件で数えた5領域の件数（0件も含む）。"""
    return _hyp_call(hyp.list_hypotheses, domain or None, status or None, origin or None, problem or None, industry or None)


@app.get("/api/hypotheses/{hypothesis_id}")
def get_hypothesis(hypothesis_id: int):
    return _hyp_call(hyp.get_hypothesis, hypothesis_id)


@app.post("/api/hypotheses", status_code=201)
def create_hypothesis(body: HypothesisCreate):
    return {"id": _hyp_call(hyp.create_manual, body.model_dump())}


@app.patch("/api/hypotheses/{hypothesis_id}")
def update_hypothesis(hypothesis_id: int, body: HypothesisPatch):
    _hyp_call(hyp.update_hypothesis, hypothesis_id, body.model_dump(exclude_none=True))
    return _hyp_call(hyp.get_hypothesis, hypothesis_id)


@app.post("/api/hypotheses/{hypothesis_id}/status")
def change_hypothesis_status(hypothesis_id: int, body: StatusChange):
    _hyp_call(hyp.set_status, hypothesis_id, body.status, body.reason)
    return _hyp_call(hyp.get_hypothesis, hypothesis_id)


@app.post("/api/hypotheses/{hypothesis_id}/evidence", status_code=201)
def add_hypothesis_evidence(hypothesis_id: int, body: EvidenceCreate):
    return {"id": _hyp_call(hyp.add_evidence, hypothesis_id, body.model_dump(exclude_none=True))}


@app.post("/api/hypotheses/{hypothesis_id}/evidence/{evidence_id}/retract")
def retract_hypothesis_evidence(hypothesis_id: int, evidence_id: int, body: Retraction):
    _hyp_call(hyp.retract_evidence, hypothesis_id, evidence_id, body.reason)
    return _hyp_call(hyp.get_hypothesis, hypothesis_id)


@app.get("/api/hypothesis-rules")
def hypothesis_rules():
    """仮説を作るルールの一覧と、仮説を作らない経営課題とその理由。"""
    return hyp.rules_overview()


# --- SaaS事業機会の比較・選定・検証計画 ------------------------------------------------

class AssessmentCreate(BaseModel):
    aspect: str
    state: str
    basis: str
    observed_facts: str | None = None
    relevance: str | None = None
    judgement_reason: str | None = None
    assumption_note: str | None = None
    evidence_ids: list[int] = []
    activity_ids: list[int] = []


class ActivityCreate(BaseModel):
    activity_type: str
    purpose: str
    target: str
    decision_criteria: str
    planned_on: str | None = None


class ActivityRevision(BaseModel):
    purpose: str
    target: str
    decision_criteria: str
    planned_on: str | None = None
    change_reason: str


class SourceCreate(BaseModel):
    url: str
    accessed_on: str
    title: str | None = None
    note: str | None = None


class ActivityComplete(BaseModel):
    outcome: str
    observed_facts: str
    interpretation: str
    judgement_reason: str
    evidence_ids: list[int] = []


class ReasonBody(BaseModel):
    reason: str


@app.get("/api/opportunity-aspects")
def opportunity_aspects():
    """6観点の定義、状態の意味、支払意思の種類、事後評価の区別。"""
    return opp.aspects_overview()


@app.get("/api/opportunities")
def list_opportunities(domain: str | None = None):
    """13仮説の比較表。点数・順位は返さない。"""
    return _hyp_call(opp.list_opportunities, domain or None)


@app.get("/api/opportunities/{hypothesis_id}")
def get_opportunity(hypothesis_id: int):
    return _hyp_call(opp.get_opportunity, hypothesis_id)


@app.post("/api/opportunities/{hypothesis_id}/assessments", status_code=201)
def add_assessment(hypothesis_id: int, body: AssessmentCreate):
    return {"id": _hyp_call(opp.add_assessment, hypothesis_id, body.model_dump())}


@app.post("/api/opportunities/{hypothesis_id}/activities", status_code=201)
def create_activity(hypothesis_id: int, body: ActivityCreate):
    return {"id": _hyp_call(opp.create_activity, hypothesis_id, body.model_dump())}


@app.post("/api/activities/{activity_id}/revisions")
def revise_activity(activity_id: int, body: ActivityRevision):
    _hyp_call(opp.revise_activity, activity_id, body.model_dump())
    return {"ok": True}


@app.post("/api/activities/{activity_id}/start")
def start_activity(activity_id: int):
    _hyp_call(opp.start_activity, activity_id)
    return {"ok": True}


@app.post("/api/activities/{activity_id}/sources", status_code=201)
def add_activity_source(activity_id: int, body: SourceCreate):
    return {"id": _hyp_call(opp.add_source, activity_id, body.model_dump())}


@app.post("/api/activities/{activity_id}/complete")
def complete_activity(activity_id: int, body: ActivityComplete):
    _hyp_call(opp.complete_activity, activity_id, body.model_dump())
    return {"ok": True}


@app.post("/api/activities/{activity_id}/cancel")
def cancel_activity(activity_id: int, body: ReasonBody):
    _hyp_call(opp.cancel_activity, activity_id, body.reason)
    return {"ok": True}


@app.post("/api/opportunities/{hypothesis_id}/select")
def select_opportunity(hypothesis_id: int, body: ReasonBody):
    _hyp_call(opp.select, hypothesis_id, body.reason)
    return _hyp_call(opp.get_opportunity, hypothesis_id)


@app.post("/api/opportunities/{hypothesis_id}/unselect")
def unselect_opportunity(hypothesis_id: int, body: ReasonBody):
    _hyp_call(opp.unselect, hypothesis_id, body.reason)
    return _hyp_call(opp.get_opportunity, hypothesis_id)


@app.post("/api/fetch")
async def fetch():
    """全情報源から最新記事を取得して保存する。一部の失敗は errors に入れて返す。"""
    fetched = inserted = 0
    errors: list[str] = []
    for source in SOURCES:
        try:
            # 情報源ごとの取得上限（公式フィードは FeedConfig.max_items）。無ければ共通の上限
            result = await source.fetch(getattr(source, "fetch_limit", FETCH_LIMIT))
        except Exception as e:  # 1つの情報源の想定外エラーで全体を止めない
            errors.append(f"{source.label}: 予期しないエラー ({e})")
            continue
        errors.extend(result.errors)
        if result.fetch_state:
            db.update_fetch_state(source.name, result.fetch_state)
        fetched += len(result.articles)
        inserted += db.save_articles(
            source, [(a, a.category or classify(a.title)) for a in result.articles]
        )
    return {"fetched": fetched, "inserted": inserted, "errors": errors}
