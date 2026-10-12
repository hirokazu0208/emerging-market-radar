"""SaaS需要仮説の生成・取得・編集・状態管理・根拠の記録。

仮説は観測（統計・記事）とは別のもので、人が検証するための作業単位として扱う。
- ルールが作るのは「未検証」の候補だけ。人が操作した仮説（編集・状態の変更・根拠の追加）は、再実行で上書きしない。
- 根拠と検証記録は追記のみ（取り消しは別の記録として残す）。
- 「検証で支持」は、別々の顧客接点からの支持記録が2件以上、または支払意思を示す支持記録が1件以上ある場合のみ。
  統計・記事・メモは数えない。これは需要の証明ではなく、限定的な検証結果である。
条件は API（この層）と DB（トリガー・CHECK）の両方で強制する。
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from . import db
from .hypothesis_rules import DOMAINS, HYPOTHESIS_RULESET_VERSION, RULES, HypothesisRule

STATUSES = ("unverified", "in_progress", "rejected", "validated")
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "unverified": ("in_progress", "rejected"),
    "in_progress": ("validated", "rejected", "unverified"),
    "rejected": ("unverified",),
    "validated": ("in_progress",),
}
REASON_REQUIRED = frozenset({"rejected", "validated"})  # この状態への変更・この状態からの変更には理由が必要
VALIDATION_KINDS = ("interview", "channel_test", "payment_intent")
MANUAL_EVIDENCE_KINDS = ("article", "note", "problem_observation", *VALIDATION_KINDS)
RESULTS = ("supports", "refutes", "inconclusive")
PAYMENT_SIGNALS = ("preorder", "paid", "signed_quote", "letter_of_intent")
REQUIRED_INDEPENDENT_CONTACTS = 2

TEXT_FIELDS = ("title", "target_customer", "problem_statement", "solution_idea",
               "assumptions", "unknowns", "not_implied", "next_action")
EDITABLE_FIELDS = ("domain", *TEXT_FIELDS)

VALIDATED_NOTE = (
    "「検証で支持」は、記録された顧客接点での検証が仮説を支持したことを示す限定的な結果です。"
    "需要の存在や収益化を証明するものではありません。"
)

MAX_TEXT = 5000  # 自由記述の上限（文字数）

_CONTACT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\d.])(?:\+81[-\s]?|0)\d{1,4}[-ー－‐\s]?\d{1,4}[-ー－‐\s]?\d{3,4}(?!\d)")


class NotFound(LookupError):
    pass


class InvalidRequest(ValueError):
    pass


class Conflict(RuntimeError):
    pass


class NoStatistics(RuntimeError):
    pass


# --- 共通 --------------------------------------------------------------------

def _event(conn: sqlite3.Connection, hypothesis_id: int, event: str, detail: dict | None = None,
           from_status: str | None = None, to_status: str | None = None) -> None:
    conn.execute(
        "INSERT INTO hypothesis_events (hypothesis_id, event, from_status, to_status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hypothesis_id, event, from_status, to_status,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None, db.now()),
    )


def check_no_personal_info(label: str, text: str | None) -> None:
    """メールアドレス・電話番号らしい文字列を保存しない（検証記録に個人情報を不用意に残さないため）。"""
    if not text:
        return
    if len(text) > MAX_TEXT:
        raise InvalidRequest(f"{label}は{MAX_TEXT}文字以内で入力してください")
    if _EMAIL.search(text) or _PHONE.search(text) or re.search(r"(?i)\b(?:mailto|tel):", text):
        raise InvalidRequest(f"{label}にメールアドレス・電話番号らしい文字列が含まれています。個人情報は保存しないでください")


def _get_hypothesis(conn: sqlite3.Connection, hypothesis_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    if row is None:
        raise NotFound(f"仮説がありません: {hypothesis_id}")
    return row


def validation_progress(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    rows = conn.execute(
        """SELECT kind, result, contact_ref FROM hypothesis_evidence
           WHERE hypothesis_id = ? AND role = 'validation'
             AND id NOT IN (SELECT evidence_id FROM hypothesis_evidence_retractions)""",
        (hypothesis_id,),
    ).fetchall()
    supporting_contacts = {r["contact_ref"] for r in rows if r["result"] == "supports"}
    payment = any(r["kind"] == "payment_intent" and r["result"] == "supports" for r in rows)
    return {
        "independent_supporting_contacts": len(supporting_contacts),
        "required_independent_contacts": REQUIRED_INDEPENDENT_CONTACTS,
        "has_supporting_payment_intent": payment,
        "refuting_records": sum(1 for r in rows if r["result"] == "refutes"),
        "requirements_met": len(supporting_contacts) >= REQUIRED_INDEPENDENT_CONTACTS or payment,
    }


# --- ルールによる候補の生成 ------------------------------------------------------

@dataclass
class GenerationResult:
    stat_table: dict
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 人が操作した（編集・状態変更・根拠の追加）ため上書きしなかった
    unchanged: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    basis_added: int = 0
    missing_basis: list[tuple[str, str]] = field(default_factory=list)  # (ルール, 業種コード)
    dry_run: bool = False


def rule_fields(rule: HypothesisRule) -> dict[str, str]:
    return {
        "domain": rule.domain,
        "title": rule.title,
        "target_customer": rule.target_customer,
        "problem_statement": rule.problem_statement,
        "solution_idea": rule.solution_idea,
        "assumptions": "\n".join(rule.assumptions),
        "unknowns": "\n".join(rule.unknowns),
        "not_implied": rule.not_implied,
        "next_action": rule.next_action,
    }


def _latest_stat_table(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM stat_tables ORDER BY period DESC, id DESC LIMIT 1").fetchone()


def _add_rule_basis(conn: sqlite3.Connection, hypothesis_id: int, rule: HypothesisRule, table: sqlite3.Row,
                    result: GenerationResult) -> None:
    for industry in rule.basis_industries:
        obs = conn.execute(
            """SELECT o.*, t.value AS total_value FROM problem_observations o
               LEFT JOIN problem_observations t ON t.stat_table_id = o.stat_table_id AND t.area_code = o.area_code
                    AND t.industry_code = o.industry_code AND t.problem_code = '00'
               WHERE o.stat_table_id = ? AND o.area_code = '00' AND o.industry_code = ? AND o.problem_code = ?""",
            (table["id"], industry, rule.problem_code),
        ).fetchone()
        if obs is None:
            result.missing_basis.append((rule.key, industry))
            continue
        exists = conn.execute(
            """SELECT 1 FROM hypothesis_evidence WHERE hypothesis_id = ? AND kind = 'problem_observation'
                 AND added_by = 'rule' AND stat_table_id = ? AND industry_code = ? AND problem_code = ?
                 AND source_sha256 = ?""",
            (hypothesis_id, table["id"], industry, rule.problem_code, table["file_sha256"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO hypothesis_evidence
               (hypothesis_id, kind, role, added_by, stat_table_id, industry_code, problem_code,
                snapshot_label, snapshot_value, snapshot_symbol, snapshot_total,
                source_title, source_url, source_period, source_sha256, rule_version, created_at)
               VALUES (?, 'problem_observation', 'basis', 'rule', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hypothesis_id, table["id"], industry, rule.problem_code,
             f"{obs['industry_name']} × {obs['problem_name']}", obs["value"], obs["value_symbol"], obs["total_value"],
             f"{table['survey_name']} {table['title']}", table["source_url"], table["period"], table["file_sha256"],
             HYPOTHESIS_RULESET_VERSION, db.now()),
        )
        _event(conn, hypothesis_id, "basis_added", {
            "industry_code": industry, "problem_code": rule.problem_code, "period": table["period"],
            "source_sha256": table["file_sha256"], "rule_version": HYPOTHESIS_RULESET_VERSION,
        })
        result.basis_added += 1


def generate(dry_run: bool = False) -> GenerationResult:
    """ルールから仮説の候補を作る（再実行しても重複しない）。最新の統計表を出発点の観測として紐付ける。"""
    with db.connect() as conn:  # 1トランザクション。失敗時は何も変更しない
        db.migrate(conn)
        table = _latest_stat_table(conn)
        if table is None:
            raise NoStatistics("統計表がありません。先に e-Stat の統計を取り込んでください（python -m app.estat_import）")
        result = GenerationResult(
            stat_table={"id": table["id"], "period": table["period"], "title": table["title"]}, dry_run=dry_run,
        )
        existing = {r["rule_key"]: r for r in conn.execute("SELECT * FROM hypotheses WHERE origin = 'rule'")}
        now = db.now()

        for rule in RULES:
            fields = rule_fields(rule)
            h = existing.get(rule.key)
            if h is None:
                cols = ", ".join(fields)
                hid = conn.execute(
                    f"""INSERT INTO hypotheses ({cols}, status, origin, rule_key, rule_version, created_at, updated_at)
                        VALUES ({", ".join("?" * len(fields))}, 'unverified', 'rule', ?, ?, ?, ?)""",
                    (*fields.values(), rule.key, HYPOTHESIS_RULESET_VERSION, now, now),
                ).lastrowid
                _event(conn, hid, "created", {"rule_key": rule.key, "rule_version": HYPOTHESIS_RULESET_VERSION},
                       to_status="unverified")
                result.created.append(rule.key)
            else:
                hid = h["id"]
                changed = {k: v for k, v in fields.items() if h[k] != v}
                if not h["human_modified"] and h["status"] == "unverified":
                    if changed or h["rule_version"] != HYPOTHESIS_RULESET_VERSION or h["rule_retired"]:
                        sets = ", ".join(f"{k} = ?" for k in changed)
                        conn.execute(
                            f"UPDATE hypotheses SET {sets + ', ' if sets else ''}rule_version = ?, rule_retired = 0, "
                            "updated_at = ? WHERE id = ?",
                            (*changed.values(), HYPOTHESIS_RULESET_VERSION, now, hid),
                        )
                        _event(conn, hid, "rule_updated", {
                            "from_version": h["rule_version"], "to_version": HYPOTHESIS_RULESET_VERSION,
                            "changed_fields": sorted(changed),
                        })
                        result.updated.append(rule.key)
                    else:
                        result.unchanged.append(rule.key)
                else:
                    if h["rule_retired"]:
                        conn.execute("UPDATE hypotheses SET rule_retired = 0 WHERE id = ?", (hid,))
                    already_logged = any(
                        json.loads(e["detail"] or "{}").get("rule_version") == HYPOTHESIS_RULESET_VERSION
                        for e in conn.execute(
                            "SELECT detail FROM hypothesis_events WHERE hypothesis_id = ? AND event = 'rule_update_skipped'",
                            (hid,),
                        )
                    )
                    if changed and not already_logged:
                        _event(conn, hid, "rule_update_skipped", {
                            "rule_version": HYPOTHESIS_RULESET_VERSION,
                            "reason": "人が操作した（編集・状態の変更・根拠の追加）仮説のため、ルールの文面で上書きしませんでした",
                            "proposed": changed,
                        })
                    (result.skipped if changed else result.unchanged).append(rule.key)
            _add_rule_basis(conn, hid, rule, table, result)

        rule_keys = {r.key for r in RULES}
        for key, h in existing.items():
            if key not in rule_keys and not h["rule_retired"]:
                conn.execute("UPDATE hypotheses SET rule_retired = 1, updated_at = ? WHERE id = ?", (now, h["id"]))
                _event(conn, h["id"], "rule_retired", {"rule_key": key, "rule_version": HYPOTHESIS_RULESET_VERSION})
                result.retired.append(key)

        if dry_run:
            conn.rollback()
    return result


# --- 取得 --------------------------------------------------------------------

def _summary(conn: sqlite3.Connection, h: sqlite3.Row) -> dict:
    basis = conn.execute(
        """SELECT DISTINCT industry_code, problem_code, snapshot_label FROM hypothesis_evidence
           WHERE hypothesis_id = ? AND kind = 'problem_observation' ORDER BY problem_code, industry_code""",
        (h["id"],),
    ).fetchall()
    seen, basis_list = set(), []
    for b in basis:
        if (b["industry_code"], b["problem_code"]) not in seen:
            seen.add((b["industry_code"], b["problem_code"]))
            basis_list.append(dict(b))
    return {
        **{k: h[k] for k in ("id", "domain", "status", "status_reason", "origin", "rule_key", "rule_version",
                             "created_at", "updated_at", *TEXT_FIELDS)},
        "rule_retired": bool(h["rule_retired"]),
        "human_modified": bool(h["human_modified"]),
        "basis": basis_list,
        "validation": validation_progress(conn, h["id"]),
    }


def _validate_filter(name: str, value: str | None, allowed: tuple[str, ...]) -> None:
    if value is not None and value not in allowed:
        raise InvalidRequest(f"{name} の値が不正です: {value}")


def list_hypotheses(domain: str | None = None, status: str | None = None, origin: str | None = None,
                    problem: str | None = None, industry: str | None = None) -> dict:
    _validate_filter("domain", domain, DOMAINS)
    _validate_filter("status", status, STATUSES)
    _validate_filter("origin", origin, ("rule", "manual"))
    conditions, params = [], []
    if status:
        conditions.append("h.status = ?"); params.append(status)
    if origin:
        conditions.append("h.origin = ?"); params.append(origin)
    for col, value in (("problem_code", problem), ("industry_code", industry)):
        if value:
            conditions.append(f"EXISTS (SELECT 1 FROM hypothesis_evidence e WHERE e.hypothesis_id = h.id "
                              f"AND e.kind = 'problem_observation' AND e.{col} = ?)")
            params.append(value)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with db.connect() as conn:
        db.migrate(conn)
        rows = conn.execute(f"SELECT h.* FROM hypotheses h{where} ORDER BY h.id", params).fetchall()
        # 領域別の件数は、領域以外の条件で数える（5領域とも、0件でも返す）
        domain_counts = {d: 0 for d in DOMAINS}
        for r in rows:
            domain_counts[r["domain"]] += 1
        items = [_summary(conn, r) for r in rows if domain is None or r["domain"] == domain]
    return {"hypotheses": items, "domain_counts": domain_counts, "validated_note": VALIDATED_NOTE}


def _observation_status(conn: sqlite3.Connection, e: sqlite3.Row) -> dict:
    """紐付けた時点の写しと、今の統計の値を比べる（統計が更新されても根拠は残す）。"""
    table = conn.execute("SELECT * FROM stat_tables WHERE id = ?", (e["stat_table_id"],)).fetchone()
    latest = _latest_stat_table(conn)
    info: dict = {"newer_table": None}
    if latest is not None and latest["period"] > e["source_period"]:
        info["newer_table"] = {"id": latest["id"], "period": latest["period"], "title": latest["title"]}
    if table is None:
        return {**info, "status": "missing", "value": None, "symbol": None, "total": None}
    row = conn.execute(
        "SELECT value, value_symbol FROM problem_observations WHERE stat_table_id = ? AND area_code = '00' "
        "AND industry_code = ? AND problem_code = ?",
        (e["stat_table_id"], e["industry_code"], e["problem_code"]),
    ).fetchone()
    total = conn.execute(
        "SELECT value FROM problem_observations WHERE stat_table_id = ? AND area_code = '00' "
        "AND industry_code = ? AND problem_code = '00'",
        (e["stat_table_id"], e["industry_code"]),
    ).fetchone()
    if row is None:
        return {**info, "status": "missing", "value": None, "symbol": None, "total": None}
    same_file = table["file_sha256"] == e["source_sha256"]
    same_value = (row["value"], row["value_symbol"], total["value"] if total else None) == (
        e["snapshot_value"], e["snapshot_symbol"], e["snapshot_total"])
    return {
        **info,
        "status": "same" if same_file and same_value else ("revised_same_value" if same_value else "changed"),
        "value": row["value"], "symbol": row["value_symbol"], "total": total["value"] if total else None,
        "period": table["period"],
    }


def get_hypothesis(hypothesis_id: int) -> dict:
    with db.connect() as conn:
        db.migrate(conn)
        h = _get_hypothesis(conn, hypothesis_id)
        retractions = {r["evidence_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM hypothesis_evidence_retractions WHERE evidence_id IN "
            "(SELECT id FROM hypothesis_evidence WHERE hypothesis_id = ?)", (hypothesis_id,))}
        evidence = []
        for e in conn.execute("SELECT * FROM hypothesis_evidence WHERE hypothesis_id = ? ORDER BY id", (hypothesis_id,)):
            item = dict(e)
            item["retraction"] = retractions.get(e["id"])
            if e["kind"] == "problem_observation":
                item["current"] = _observation_status(conn, e)
            if e["kind"] == "article":
                a = conn.execute("SELECT title, url, source FROM articles WHERE id = ?", (e["article_id"],)).fetchone()
                item["article"] = dict(a) if a else None
            evidence.append(item)
        events = [dict(r) | {"detail": json.loads(r["detail"]) if r["detail"] else None}
                  for r in conn.execute("SELECT * FROM hypothesis_events WHERE hypothesis_id = ? ORDER BY id", (hypothesis_id,))]
        summary = _summary(conn, h)
    return {**summary, "evidence": evidence, "events": events,
            "allowed_transitions": list(ALLOWED_TRANSITIONS[h["status"]]), "validated_note": VALIDATED_NOTE}


# --- 人による操作 ---------------------------------------------------------------

def _clean_text_fields(data: dict, required: bool) -> dict:
    unknown = set(data) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidRequest(f"変更できない項目です: {sorted(unknown)}")
    cleaned = {}
    for k, v in data.items():
        if not isinstance(v, str) or not v.strip():
            raise InvalidRequest(f"{k} は空にできません")
        if k == "domain" and v not in DOMAINS:
            raise InvalidRequest(f"domain の値が不正です: {v}")
        check_no_personal_info(k, v)
        cleaned[k] = v.strip()
    if required:
        missing = [k for k in EDITABLE_FIELDS if k not in cleaned]
        if missing:
            raise InvalidRequest(f"必須項目がありません: {missing}")
    return cleaned


def create_manual(data: dict) -> int:
    fields = _clean_text_fields(data, required=True)
    now = db.now()
    with db.connect() as conn:
        db.migrate(conn)
        hid = conn.execute(
            f"""INSERT INTO hypotheses ({", ".join(fields)}, status, origin, human_modified, created_at, updated_at)
                VALUES ({", ".join("?" * len(fields))}, 'unverified', 'manual', 1, ?, ?)""",
            (*fields.values(), now, now),
        ).lastrowid
        _event(conn, hid, "created", {"origin": "manual"}, to_status="unverified")
    return hid


def update_hypothesis(hypothesis_id: int, data: dict) -> None:
    fields = _clean_text_fields(data, required=False)
    if not fields:
        raise InvalidRequest("変更する項目がありません")
    with db.connect() as conn:
        h = _get_hypothesis(conn, hypothesis_id)
        changed = {k: v for k, v in fields.items() if h[k] != v}
        if not changed:
            return
        sets = ", ".join(f"{k} = ?" for k in changed)
        conn.execute(f"UPDATE hypotheses SET {sets}, human_modified = 1, updated_at = ? WHERE id = ?",
                     (*changed.values(), db.now(), hypothesis_id))
        _event(conn, hypothesis_id, "edited", {"changed_fields": sorted(changed),
                                                "previous": {k: h[k] for k in changed}})


def set_status(hypothesis_id: int, status: str, reason: str | None) -> None:
    if status not in STATUSES:
        raise InvalidRequest(f"status の値が不正です: {status}")
    reason = reason.strip() if isinstance(reason, str) else None
    check_no_personal_info("理由", reason)
    with db.connect() as conn:
        h = _get_hypothesis(conn, hypothesis_id)
        current = h["status"]
        if status == current:
            raise Conflict(f"すでに {status} です")
        if status not in ALLOWED_TRANSITIONS[current]:
            raise Conflict(f"{current} から {status} には変更できません")
        if (status in REASON_REQUIRED or current in REASON_REQUIRED) and not reason:
            raise InvalidRequest("この状態の変更には理由が必要です")
        if reason and reason == h["status_reason"] and (status in REASON_REQUIRED or current in REASON_REQUIRED):
            raise InvalidRequest("前回と同じ理由です。今回の変更の理由を記入してください")
        if status == "validated":
            progress = validation_progress(conn, hypothesis_id)
            if not progress["requirements_met"]:
                raise Conflict(
                    f"「検証で支持」の条件を満たしていません（別々の顧客接点からの支持記録 "
                    f"{progress['independent_supporting_contacts']}/{REQUIRED_INDEPENDENT_CONTACTS} 件、"
                    f"支払意思を示す支持記録 {'あり' if progress['has_supporting_payment_intent'] else 'なし'}）"
                )
        try:
            # 状態を変えた仮説は、ルールの再実行で文面を上書きしない
            conn.execute("UPDATE hypotheses SET status = ?, status_reason = ?, human_modified = 1, updated_at = ? "
                         "WHERE id = ?", (status, reason or h["status_reason"], db.now(), hypothesis_id))
        except sqlite3.IntegrityError as e:  # DB 側の条件（トリガー）による拒否
            raise Conflict(f"状態を変更できません（{e}）") from e
        _event(conn, hypothesis_id, "status_changed", {"reason": reason}, from_status=current, to_status=status)


def _valid_date(text: str) -> str:
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError) as e:
        raise InvalidRequest("occurred_on は YYYY-MM-DD で指定してください") from e
    if d > date.today():
        raise InvalidRequest("occurred_on に未来の日付は指定できません")
    return d.isoformat()


def add_evidence(hypothesis_id: int, data: dict) -> int:
    kind = data.get("kind")
    if kind not in MANUAL_EVIDENCE_KINDS:
        raise InvalidRequest(f"kind の値が不正です: {kind}")
    description = (data.get("description") or "").strip() or None
    method_note = (data.get("method_note") or "").strip() or None
    url = (data.get("url") or "").strip() or None
    for label, text in (("観察内容・メモ", description), ("検証方法の補足", method_note), ("URL", url)):
        check_no_personal_info(label, text)
    if url and not re.match(r"^https?://", url):
        raise InvalidRequest("url は http:// または https:// で始まる必要があります")

    row: dict = {"kind": kind, "added_by": "manual", "description": description, "method_note": method_note,
                 "url": url, "created_at": db.now()}
    with db.connect() as conn:
        _get_hypothesis(conn, hypothesis_id)
        if kind in VALIDATION_KINDS:
            contact_ref = (data.get("contact_ref") or "").strip().upper()  # 大文字・小文字の違いで別の接点にしない
            if not _CONTACT_REF.match(contact_ref):
                raise InvalidRequest("contact_ref は英数字・ハイフン・下線の匿名の識別子（32文字以内、例：C-001）で指定してください")
            if data.get("result") not in RESULTS:
                raise InvalidRequest("result は supports / refutes / inconclusive のいずれかです")
            if not description:
                raise InvalidRequest("観察内容（description）は必須です")
            payment_signal = data.get("payment_signal")
            if kind == "payment_intent" and payment_signal not in PAYMENT_SIGNALS:
                raise InvalidRequest(f"payment_signal は {', '.join(PAYMENT_SIGNALS)} のいずれかです")
            if kind != "payment_intent" and payment_signal:
                raise InvalidRequest("payment_signal は payment_intent の場合のみ指定できます")
            row |= {"role": "validation", "contact_ref": contact_ref, "result": data["result"],
                    "occurred_on": _valid_date(data.get("occurred_on")), "payment_signal": payment_signal}
        else:
            role = data.get("role", "supporting")
            if role not in ("supporting", "contradicting"):
                raise InvalidRequest("role は supporting / contradicting のいずれかです")
            row["role"] = role
            if kind == "note" and not description:
                raise InvalidRequest("メモ（description）は必須です")
            if kind == "article":
                article_id = data.get("article_id")
                if not isinstance(article_id, int) or conn.execute(
                        "SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is None:
                    raise InvalidRequest(f"記事がありません: {article_id}")
                row["article_id"] = article_id
            if kind == "problem_observation":
                obs = conn.execute(
                    """SELECT o.*, t.value AS total_value, s.survey_name, s.title AS table_title, s.source_url, s.period,
                              s.file_sha256
                       FROM problem_observations o JOIN stat_tables s ON s.id = o.stat_table_id
                       LEFT JOIN problem_observations t ON t.stat_table_id = o.stat_table_id AND t.area_code = o.area_code
                            AND t.industry_code = o.industry_code AND t.problem_code = '00'
                       WHERE o.stat_table_id = ? AND o.area_code = '00' AND o.industry_code = ? AND o.problem_code = ?""",
                    (data.get("stat_table_id"), data.get("industry_code"), data.get("problem_code")),
                ).fetchone()
                if obs is None:
                    raise InvalidRequest("指定した統計の観測がありません")
                row |= {"stat_table_id": obs["stat_table_id"], "industry_code": obs["industry_code"],
                        "problem_code": obs["problem_code"],
                        "snapshot_label": f"{obs['industry_name']} × {obs['problem_name']}",
                        "snapshot_value": obs["value"], "snapshot_symbol": obs["value_symbol"],
                        "snapshot_total": obs["total_value"], "source_title": f"{obs['survey_name']} {obs['table_title']}",
                        "source_url": obs["source_url"], "source_period": obs["period"], "source_sha256": obs["file_sha256"]}
        cols = ", ".join(row)
        try:
            eid = conn.execute(
                f"INSERT INTO hypothesis_evidence (hypothesis_id, {cols}) VALUES (?, {', '.join('?' * len(row))})",
                (hypothesis_id, *row.values()),
            ).lastrowid
        except sqlite3.IntegrityError as e:
            raise InvalidRequest(f"根拠を記録できません（{e}）") from e
        # 人が根拠を追加した仮説は、ルールの再実行で文面を上書きしない（記録と文面の食い違いを防ぐ）
        conn.execute("UPDATE hypotheses SET human_modified = 1, updated_at = ? WHERE id = ?", (db.now(), hypothesis_id))
        _event(conn, hypothesis_id, "evidence_added", {"evidence_id": eid, "kind": kind, "role": row["role"],
                                                       "result": row.get("result")})
    return eid


def retract_evidence(hypothesis_id: int, evidence_id: int, reason: str) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise InvalidRequest("取り消しの理由が必要です")
    check_no_personal_info("理由", reason)
    with db.connect() as conn:
        h = _get_hypothesis(conn, hypothesis_id)
        e = conn.execute("SELECT * FROM hypothesis_evidence WHERE id = ? AND hypothesis_id = ?",
                         (evidence_id, hypothesis_id)).fetchone()
        if e is None:
            raise NotFound(f"根拠がありません: {evidence_id}")
        if e["added_by"] != "manual":
            raise Conflict("ルールが紐付けた観測は取り消せません")
        if conn.execute("SELECT 1 FROM hypothesis_evidence_retractions WHERE evidence_id = ?", (evidence_id,)).fetchone():
            raise Conflict("すでに取り消されています")
        if h["status"] == "validated":
            raise Conflict("「検証で支持」の仮説の根拠は取り消せません。先に状態を「検証中」に戻してください")
        try:
            conn.execute("INSERT INTO hypothesis_evidence_retractions (evidence_id, reason, created_at) VALUES (?, ?, ?)",
                         (evidence_id, reason, db.now()))
        except sqlite3.IntegrityError as err:
            raise Conflict(f"取り消せません（{err}）") from err
        _event(conn, hypothesis_id, "evidence_retracted", {"evidence_id": evidence_id, "reason": reason})


def rules_overview() -> dict:
    from .hypothesis_rules import NO_HYPOTHESES
    return {
        "version": HYPOTHESIS_RULESET_VERSION,
        "rules": [{"key": r.key, "problem_code": r.problem_code, "domain": r.domain, "title": r.title,
                   "basis_industries": list(r.basis_industries)} for r in RULES],
        "no_hypothesis": [{"problem_code": n.problem_code, "reason": n.reason} for n in NO_HYPOTHESES],
    }
