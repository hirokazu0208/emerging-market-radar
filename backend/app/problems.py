"""統計上の経営課題（problem_observations）の取得と、業種内割合などの算出。

元データの値（value：‰）と、本アプリが算出した値（computed_*）を明確に分けて返す。
算出値は DB に保存せず、ここで毎回計算する。

分母について（個人企業経済調査の表の構造）
- 全産業・大分類の行：その業種の企業全体を 1000 とした千分率
- 中分類・小分類の行：所属する大分類の企業全体を 1000 とした千分率
いずれの行も、同じ行の「総数」と各項目は同じ分母なので、業種内割合 = 項目の値 ÷ 同じ行の総数 で求める。
"""

from . import db

TOTAL_CODE = "00"
LOW_PRECISION_TOTAL = 100  # 行の総数がこれ（‰）未満の場合、丸め誤差が大きいとして注記する

PROCESSING_NOTE = (
    "本アプリによる加工：表形式の変換、業種内割合（各項目の値 ÷ 同じ業種行の総数）の算出、"
    "表に掲載のない回答（総数 − 12項目の合計）の算出。"
)
INTERPRETATION_NOTE = (
    "統計上の経営課題（事業主が最も大きな問題点として1つだけ回答したもの）です。"
    "割合の大小は、ソフトウェアの需要や支払意思の強さを示すものではありません。"
)
UNLISTED_NAME = "表に掲載のない回答（内訳未確認）"


def attribution(table: dict) -> str:
    return (
        f"出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）「{table['survey_name']}」"
        f"第{table['table_no']}表（{table['publisher']}）を加工して作成"
    )


def list_tables() -> list[dict]:
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT id, source, stat_code, table_ref, table_no, title, survey_name, period, publisher,
                      source_url, unit, answer_type, note, scope_note, imported_at
               FROM stat_tables ORDER BY period DESC, id DESC"""
        )]
    for r in rows:
        r["attribution"] = attribution(r)
        r["processing_note"] = PROCESSING_NOTE
        r["interpretation_note"] = INTERPRETATION_NOTE
    return rows


def _share(value: float | None, total: float | None) -> float | None:
    if value is None or total is None or total <= 0:
        return None
    return round(value / total * 100, 2)


def get_problems(table_id: int, industry: str | None = None, problem: str | None = None) -> dict | None:
    """業種ごとに、元データの値と算出値をまとめて返す。表が無ければ None。

    industry を指定すると、その業種と配下の業種に絞る。problem を指定すると、その項目だけを返す。
    存在しない業種・項目の場合は ValueError。
    """
    tables = {t["id"]: t for t in list_tables()}
    table = tables.get(table_id)
    if table is None:
        return None
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM problem_observations WHERE stat_table_id = ? ORDER BY row_order, problem_code",
            (table_id,),
        )]

    industries: dict[str, dict] = {}
    problems: dict[str, str] = {}
    for r in rows:
        ind = industries.setdefault(r["industry_code"], {
            "code": r["industry_code"], "name": r["industry_name"], "level": r["industry_level"],
            "parent_code": r["parent_code"], "is_subset": bool(r["is_subset"]), "row_order": r["row_order"],
            "total": None, "items": [],
        })
        cell = {"code": r["problem_code"], "name": r["problem_name"], "value": r["value"], "symbol": r["value_symbol"]}
        if r["problem_code"] == TOTAL_CODE:
            ind["total"] = {"value": r["value"], "symbol": r["value_symbol"]}
        else:
            problems[r["problem_code"]] = r["problem_name"]
            ind["items"].append(cell)

    if industry is not None and industry not in industries:
        raise ValueError(f"業種コードがありません: {industry}")
    if problem is not None and problem not in problems:
        raise ValueError(f"経営課題のコードがありません: {problem}")

    def base_of(ind: dict) -> dict:
        """元の千分率の分母となる業種（全産業・大分類は自身、それ以下は所属する大分類）。"""
        node = ind
        while node["level"] > 2:
            node = industries[node["parent_code"]]
        return {"code": node["code"], "name": node["name"]}

    def is_within(ind: dict, ancestor: str) -> bool:
        node = ind
        while node is not None:
            if node["code"] == ancestor:
                return True
            node = industries.get(node["parent_code"]) if node["parent_code"] else None
        return False

    result = []
    for ind in industries.values():
        if industry is not None and not is_within(ind, industry):
            continue
        total = ind["total"]["value"] if ind["total"] else None
        listed = [c["value"] for c in ind["items"] if c["value"] is not None]
        unlisted = None
        if total is not None:
            remainder = total - sum(listed)
            if remainder >= 0:
                unlisted = {
                    "name": UNLISTED_NAME,
                    "computed_value": remainder,
                    "computed_share_pct": _share(remainder, total),
                    "includes_symbol_cells": any(c["symbol"] for c in ind["items"]),
                }
        items = [
            {**c, "computed_share_pct": _share(c["value"], total)}
            for c in ind["items"] if problem is None or c["code"] == problem
        ]
        base = base_of(ind)
        result.append({
            **{k: ind[k] for k in ("code", "name", "level", "parent_code", "is_subset")},
            "base_industry": base,
            "base_note": f"「{base['name']}」の企業全体を1000とした千分率",
            "total": ind["total"],
            "items": items,
            "unlisted": unlisted,
            "low_precision": total is not None and total < LOW_PRECISION_TOTAL,
        })

    return {
        "table": table,
        "problems": [{"code": c, "name": n} for c, n in sorted(problems.items())],
        "industries": result,
    }
