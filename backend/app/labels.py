"""記事ラベル（article_labels）の語彙と登録・取得。

ラベルは taxonomy ごとに、性質（assertion_kind）が異なる。
- observation：記事に書かれている事実の観測（例：新サービスの発表、資金調達）
- hypothesis ：観測から推定したソフトウェア需要の仮説（例：Sales 領域の需要）
両者を同じ taxonomy に混在させないことで、事実と仮説を区別する。

判定の主体は method で区別する（ai は model 必須、それ以外は model なし）。
"""

import sqlite3
from typing import Literal

from . import db

AssertionKind = Literal["observation", "hypothesis"]
Method = Literal["source_default", "keyword", "ai", "manual"]

# taxonomy → (性質, ラベル一覧)。DB 側の label_definitions（MIGRATIONS[3]）と一致させる
TAXONOMIES: dict[str, tuple[AssertionKind, tuple[str, ...]]] = {
    "signal_type": (
        "observation",
        ("new_service", "market_entry", "partnership", "funding", "expansion", "new_segment", "regulation"),
    ),
    "software_domain": (
        "hypothesis",
        ("marketing", "sales", "operations", "finance", "customer_success"),
    ),
}
METHODS: tuple[str, ...] = ("source_default", "keyword", "ai", "manual")


def validate(taxonomy: str, label: str, method: str, model: str | None) -> None:
    if taxonomy not in TAXONOMIES:
        raise ValueError(f"未定義の taxonomy です: {taxonomy}")
    if label not in TAXONOMIES[taxonomy][1]:
        raise ValueError(f"taxonomy '{taxonomy}' に未定義の label です: {label}")
    if method not in METHODS:
        raise ValueError(f"未定義の method です: {method}")
    if method == "ai" and not model:
        raise ValueError("method='ai' の場合は model（モデル名・プロンプト版）が必須です")
    if method != "ai" and model is not None:
        raise ValueError("model は method='ai' の場合のみ指定できます")


def add_article_label(
    article_id: int,
    taxonomy: str,
    label: str,
    method: Method,
    evidence: str | None = None,
    model: str | None = None,
) -> bool:
    """記事にラベルを登録する。同じ (記事, taxonomy, label, method, model) が既にあれば登録せず False。"""
    validate(taxonomy, label, method, model)
    with db.connect() as conn:
        try:
            # OR IGNORE は CHECK 違反も黙って捨てるため使わず、重複だけを判別する
            conn.execute(
                """INSERT INTO article_labels
                   (article_id, taxonomy, label, method, evidence, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (article_id, taxonomy, label, method, evidence, model, db.now()),
            )
        except sqlite3.IntegrityError as e:
            if e.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE":
                return False
            raise ValueError(f"ラベルを登録できません ({e})") from e  # 存在しない article_id など
        return True


def list_article_labels(
    article_id: int,
    taxonomy: str | None = None,
    assertion_kind: AssertionKind | None = None,
) -> list[dict]:
    """記事のラベルを取得する。各行に assertion_kind（observation / hypothesis）を含める。"""
    query = """
        SELECT l.article_id, l.taxonomy, l.label, d.assertion_kind, l.method,
               l.evidence, l.model, l.created_at
        FROM article_labels l
        JOIN label_definitions d ON d.taxonomy = l.taxonomy AND d.label = l.label
        WHERE l.article_id = ?
    """
    params: list = [article_id]
    if taxonomy:
        query += " AND l.taxonomy = ?"
        params.append(taxonomy)
    if assertion_kind:
        query += " AND d.assertion_kind = ?"
        params.append(assertion_kind)
    query += " ORDER BY l.taxonomy, l.label, l.method, l.created_at"
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(query, params)]
