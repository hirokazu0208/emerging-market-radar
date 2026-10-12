"""SaaS事業機会の比較（6観点の評価）・優先調査候補の選定・検証計画。

方針
- 自動のランキング・点数・収益性予測・AIによる事業性判定は行わない。状態は人が記録した評価のみ。
- 顧客接点の記録は v0.6 の hypothesis_evidence を参照する（二重に記録しない）。
  競合調査・技術検証の結果は validation_activity_results に保存し、顧客接点の記録とは混同しない。
- 「根拠あり」は、6観点すべてで「事業化に有利な方向の根拠がある」こと。「反証あり」は不利な方向の根拠。
- 機械的な確認（参照の存在・同じ仮説・種類・結果の向き・取り消し）と、人の判断（事実・関係・理由）を分ける。
  機械的な確認に通っても、内容の妥当性は人の判断であり、保証されない。
- 評価・選定・計画の版・結果は追記のみ。根拠の取り消しや矛盾する記録の追加では履歴を変更せず、再評価を促す。

DB で保証すること（v7 マイグレーションの外部キー・CHECK・トリガー）
- 参照先の存在と、同じ仮説であること（複合外部キー）。固定した版が同じ計画の版であること（複合外部キー）
- 観点ごとの参照可能な種類、結果の向き、取り消し済み記録の参照禁止、完了済み計画のみ参照可能
- 評価の確定前後の不変性、確定条件、計画の状態遷移・開始時の版の固定・完了条件、選定の上限3件
アプリケーション側で判定すること（この層）
- 開始前／開始後の記録の区別（事後評価の表示）、矛盾・取り消しの警告、個人情報らしい文字列の拒否、入力の形式
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import db
from .hypotheses import (Conflict, InvalidRequest, NotFound, check_no_personal_info, validation_progress)

JST = timezone(timedelta(hours=9))

ASPECTS: dict[str, dict] = {
    "reachability": {
        "name": "顧客への到達性",
        "question": "想定顧客に、個人開発者が実際に接触できる経路があるか",
        "supported_means": "想定顧客に実際に接触できた",
        "contradicted_means": "接触を試みたが、到達できなかった",
    },
    "severity": {
        "name": "課題の深刻さ",
        "question": "課題による時間・お金・機会損失が具体的に生じているか",
        "supported_means": "課題による時間・お金・機会損失が具体的に確認できた",
        "contradicted_means": "課題が軽い、または顧客が困っていないことが確認された",
    },
    "alternatives": {
        "name": "代替手段",
        "question": "今の解決方法（既存製品・表計算・外注など）に具体的な不足や不満があるか",
        "supported_means": "今の解決方法に具体的な不足・不満がある",
        "contradicted_means": "今の解決方法で十分に満足している",
    },
    "willingness_to_pay": {
        "name": "支払意思",
        "question": "お金を払う意思を示す行動があるか",
        "supported_means": "支払意思を示す行動（購入意向書・見積の合意・事前予約・支払い）が確認できた",
        "contradicted_means": "支払わない意思、または支払いを伴う行動に至らないことが確認された",
    },
    "build_operate_burden": {
        "name": "開発・運用負担",
        "question": "個人で開発でき、サポート・法令・データ連携を含めて運用できるか",
        "supported_means": "個人で開発・運用できる範囲だと確認できた",
        "contradicted_means": "個人では開発・運用が難しい要素が確認された",
    },
    "retention": {
        "name": "継続利用",
        "question": "一度きりでなく、続けて使う理由（定期的な業務・データの蓄積）があるか",
        "supported_means": "定期的に使う業務やデータの蓄積があると確認できた",
        "contradicted_means": "一時的な利用にとどまることが確認された",
    },
}

# 観点ごとに参照できる記録（v7 マイグレーションの assessment_ref_policies と一致させる）
REF_POLICIES: dict[str, dict[str, tuple[str, ...]]] = {
    "reachability": {"evidence": ("interview", "channel_test", "payment_intent"), "activity": ("interview", "sales_experiment")},
    "severity": {"evidence": ("interview", "payment_intent"), "activity": ("interview",)},
    "alternatives": {"evidence": ("interview",), "activity": ("interview", "competitor_research")},
    "willingness_to_pay": {"evidence": ("interview", "channel_test", "payment_intent"), "activity": ("sales_experiment",)},
    "build_operate_burden": {"evidence": (), "activity": ("technical_check", "competitor_research")},
    "retention": {"evidence": ("interview", "payment_intent"), "activity": ("interview", "sales_experiment")},
}

STATES = ("not_researched", "researching", "supported", "contradicted")
STATE_DIRECTION = {"supported": "supports", "contradicted": "refutes"}
ACTIVITY_TYPES = ("interview", "competitor_research", "sales_experiment", "technical_check")
CUSTOMER_CONTACT_ACTIVITIES = ("interview", "sales_experiment")
ACTIVITY_EVIDENCE_KINDS = {"interview": ("interview",), "sales_experiment": ("channel_test", "payment_intent")}
OUTCOMES = ("supports", "refutes", "inconclusive")
MAX_SELECTIONS = 3

# 支払意思の種類。強さを点数化せず、意味を並べて表示する
PAYMENT_SIGNALS: dict[str, str] = {
    "letter_of_intent": "購入意向書：購入の意向を示した。支払いの約束ではない",
    "signed_quote": "見積の合意：条件に合意したが、支払いはまだない",
    "preorder": "事前予約：予約した。キャンセル・返金の条件で意味が変わる（v0.7では条件を記録しない）",
    "paid": "支払い済み：実際にお金を受け取った",
}

DISCLAIMER = (
    "点数・順位はありません。状態は人が記録した評価で、収益性を示すものではありません。"
    "機械的な確認（種類・向き・取り消し）に通っていても、内容の妥当性は人の判断です。"
)


# --- 共通 ---------------------------------------------------------------------

def _hypothesis(conn: sqlite3.Connection, hypothesis_id: int) -> sqlite3.Row:
    h = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    if h is None:
        raise NotFound(f"仮説がありません: {hypothesis_id}")
    return h


def _activity(conn: sqlite3.Connection, activity_id: int) -> sqlite3.Row:
    a = conn.execute("SELECT * FROM validation_activities WHERE id = ?", (activity_id,)).fetchone()
    if a is None:
        raise NotFound(f"検証計画がありません: {activity_id}")
    return a


def _text(data: dict, key: str, label: str, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InvalidRequest(f"{label}は必須です")
        return None
    if not isinstance(value, str):
        raise InvalidRequest(f"{label}は文字列で指定してください")
    check_no_personal_info(label, value)
    return value.strip()


def _ids(data: dict, key: str) -> list[int]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise InvalidRequest(f"{key} は整数の配列で指定してください")
    if len(set(value)) != len(value):
        raise InvalidRequest(f"{key} に重複があります")
    return value


def _date(text: str | None, label: str) -> str:
    try:
        d = date.fromisoformat(text or "")
    except ValueError as e:
        raise InvalidRequest(f"{label}は YYYY-MM-DD で指定してください") from e
    if d > datetime.now(JST).date():
        raise InvalidRequest(f"{label}に未来の日付は指定できません")
    return d.isoformat()


def _jst_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(JST).date().isoformat()


def _retracted_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT evidence_id FROM hypothesis_evidence_retractions")}


def _run(conn_fn):
    """DB の制約・トリガーによる拒否を 409 として返す。"""
    try:
        return conn_fn()
    except sqlite3.IntegrityError as e:
        raise Conflict(f"DB の制約により拒否されました（{e}）") from e


# --- 計画のタイミングの区別（事後評価の判定。アプリケーション側） --------------------------

def evidence_timing(evidence: sqlite3.Row, started_at: str | None) -> str:
    """計画の開始と、検証記録の実施日・記録日時の前後関係。

    - 'after_start'：開始（判断基準の固定）以降に実施・記録された（事前に基準を定めた検証）
    - 'before_start'：開始前に実施または記録された（事後評価。事前に基準を定めた検証として扱わない）
    """
    if started_at is None:
        return "before_start"
    started_date = _jst_date(started_at)
    if evidence["created_at"] < started_at or (evidence["occurred_on"] or "") < started_date:
        return "before_start"
    return "after_start"


def _activity_mode(timings: list[str]) -> str:
    """計画の結果の扱い：事前に基準を定めた検証（pre_registered）／事後評価（post_hoc）／混在（mixed）。"""
    if not timings:
        return "pre_registered"
    if all(t == "after_start" for t in timings):
        return "pre_registered"
    if all(t == "before_start" for t in timings):
        return "post_hoc"
    return "mixed"


MODE_NOTES = {
    "pre_registered": "開始時に固定した判断基準の後に得た記録に基づく検証",
    "post_hoc": "事後評価：開始前に得た記録だけに基づく。事前に判断基準を定めて実施した検証ではない",
    "mixed": "一部が事後評価：開始前に得た記録を含む",
    "unplanned": "計画外の記録：事前の判断基準なしに得た記録を直接参照した評価（事後評価）",
}


# --- 計画 ---------------------------------------------------------------------

def _activity_detail(conn: sqlite3.Connection, a: sqlite3.Row, retracted: set[int]) -> dict:
    revisions = [dict(r) for r in conn.execute(
        "SELECT * FROM validation_activity_revisions WHERE activity_id = ? ORDER BY revision_no", (a["id"],))]
    result = conn.execute("SELECT * FROM validation_activity_results WHERE activity_id = ?", (a["id"],)).fetchone()
    sources = [dict(s) for s in conn.execute(
        "SELECT * FROM validation_activity_sources WHERE activity_id = ? ORDER BY id", (a["id"],))]
    linked = []
    for e in conn.execute(
            """SELECT e.* FROM activity_result_evidence l JOIN hypothesis_evidence e ON e.id = l.evidence_id
               WHERE l.activity_id = ? ORDER BY e.id""", (a["id"],)):
        linked.append({
            "id": e["id"], "kind": e["kind"], "result": e["result"], "contact_ref": e["contact_ref"],
            "payment_signal": e["payment_signal"], "occurred_on": e["occurred_on"], "created_at": e["created_at"],
            "description": e["description"], "retracted": e["id"] in retracted,
            "timing": evidence_timing(e, a["started_at"]),
        })
    timings = [l["timing"] for l in linked]
    if a["activity_type"] not in CUSTOMER_CONTACT_ACTIVITIES and a["started_at"]:
        start_date = _jst_date(a["started_at"])
        timings += ["before_start" if s["accessed_on"] < start_date else "after_start" for s in sources]
    mode = _activity_mode(timings) if a["status"] == "done" else None
    locked = next((r for r in revisions if r["id"] == a["locked_revision_id"]), None)
    return {
        **dict(a),
        "is_customer_contact": a["activity_type"] in CUSTOMER_CONTACT_ACTIVITIES,
        "revisions": revisions,
        "current_revision": revisions[-1] if revisions else None,
        "locked_revision": locked,
        "result": dict(result) if result else None,
        "sources": sources,
        "linked_evidence": linked,
        "mode": mode,
        "mode_note": MODE_NOTES.get(mode) if mode else None,
        "warnings": (["根拠の取り消し：紐付けた検証記録が取り消されています。結果の再確認が必要です"]
                     if any(l["retracted"] for l in linked) else []),
    }


def _plan_fields(data: dict) -> dict:
    planned_on = data.get("planned_on") or None
    if planned_on is not None:
        try:
            planned_on = date.fromisoformat(planned_on).isoformat()
        except (TypeError, ValueError) as e:
            raise InvalidRequest("planned_on は YYYY-MM-DD で指定してください") from e
    return {
        "purpose": _text(data, "purpose", "目的"),
        "target": _text(data, "target", "対象"),
        "decision_criteria": _text(data, "decision_criteria", "判断基準"),
        "planned_on": planned_on,
    }


def create_activity(hypothesis_id: int, data: dict) -> int:
    activity_type = data.get("activity_type")
    if activity_type not in ACTIVITY_TYPES:
        raise InvalidRequest(f"activity_type の値が不正です: {activity_type}")
    fields = _plan_fields(data)
    with db.connect() as conn:
        if _hypothesis(conn, hypothesis_id)["status"] == "rejected":
            raise Conflict("棄却した仮説には計画を追加できません")
        now = db.now()

        def write():
            aid = conn.execute(
                "INSERT INTO validation_activities (hypothesis_id, activity_type, created_at) VALUES (?, ?, ?)",
                (hypothesis_id, activity_type, now)).lastrowid
            conn.execute(
                """INSERT INTO validation_activity_revisions
                   (activity_id, revision_no, purpose, target, decision_criteria, planned_on, created_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?)""",
                (aid, fields["purpose"], fields["target"], fields["decision_criteria"], fields["planned_on"], now))
            return aid
        return _run(write)


def revise_activity(activity_id: int, data: dict) -> None:
    """開始前の訂正。古い版は残り、理由が必要。"""
    fields = _plan_fields(data)
    change_reason = _text(data, "change_reason", "訂正の理由")
    with db.connect() as conn:
        a = _activity(conn, activity_id)
        if a["status"] != "planned":
            raise Conflict("開始後の計画は訂正できません（目的・対象・判断基準は開始時に固定されています）")
        latest = conn.execute("SELECT MAX(revision_no) FROM validation_activity_revisions WHERE activity_id = ?",
                              (activity_id,)).fetchone()[0]
        _run(lambda: conn.execute(
            """INSERT INTO validation_activity_revisions
               (activity_id, revision_no, purpose, target, decision_criteria, planned_on, change_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (activity_id, latest + 1, fields["purpose"], fields["target"], fields["decision_criteria"],
             fields["planned_on"], change_reason, db.now())))


def start_activity(activity_id: int) -> None:
    with db.connect() as conn:
        a = _activity(conn, activity_id)
        if a["status"] != "planned":
            raise Conflict(f"{a['status']} の計画は開始できません")
        if _hypothesis(conn, a["hypothesis_id"])["status"] == "rejected":
            raise Conflict("棄却した仮説の計画は開始できません")
        latest = conn.execute(
            "SELECT id FROM validation_activity_revisions WHERE activity_id = ? ORDER BY revision_no DESC LIMIT 1",
            (activity_id,)).fetchone()[0]
        _run(lambda: conn.execute(
            "UPDATE validation_activities SET status = 'in_progress', locked_revision_id = ?, started_at = ? WHERE id = ?",
            (latest, db.now(), activity_id)))


def add_source(activity_id: int, data: dict) -> int:
    url = _text(data, "url", "URL")
    if not url.startswith(("http://", "https://")):
        raise InvalidRequest("URL は http:// または https:// で始まる必要があります")
    title = _text(data, "title", "タイトル", required=False)
    note = _text(data, "note", "メモ", required=False)
    accessed_on = _date(data.get("accessed_on"), "参照日")
    with db.connect() as conn:
        a = _activity(conn, activity_id)
        if a["status"] != "in_progress":
            raise Conflict("参照先は実施中の計画にのみ追加できます")
        return _run(lambda: conn.execute(
            "INSERT INTO validation_activity_sources (activity_id, url, title, accessed_on, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (activity_id, url, title, accessed_on, note, db.now())).lastrowid)


def complete_activity(activity_id: int, data: dict) -> None:
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise InvalidRequest("outcome は supports / refutes / inconclusive のいずれかです")
    facts = _text(data, "observed_facts", "観察された事実")
    interpretation = _text(data, "interpretation", "判断基準に照らした解釈")
    reason = _text(data, "judgement_reason", "判断の理由")
    evidence_ids = _ids(data, "evidence_ids")
    with db.connect() as conn:
        a = _activity(conn, activity_id)
        if a["status"] != "in_progress":
            raise Conflict("完了できるのは実施中の計画だけです（予定の計画は先に開始してください）")
        retracted = _retracted_ids(conn)
        kinds = ACTIVITY_EVIDENCE_KINDS.get(a["activity_type"], ())
        if a["activity_type"] in CUSTOMER_CONTACT_ACTIVITIES:
            if not evidence_ids:
                raise InvalidRequest("ヒアリング・販売実験の完了には、結果として記録した検証記録を1件以上紐付けてください")
        elif evidence_ids:
            raise InvalidRequest("競合調査・技術検証には、顧客接点の検証記録を紐付けません（結果と参照先に記録します）")
        rows = []
        for eid in evidence_ids:
            e = conn.execute("SELECT * FROM hypothesis_evidence WHERE id = ?", (eid,)).fetchone()
            if e is None or e["hypothesis_id"] != a["hypothesis_id"]:
                raise InvalidRequest(f"同じ仮説の検証記録ではありません: {eid}")
            if e["kind"] not in kinds:
                raise InvalidRequest(f"この計画に紐付けられない種類の記録です: {eid}（{e['kind']}）")
            if eid in retracted:
                raise InvalidRequest(f"取り消された記録は紐付けられません: {eid}")
            rows.append(e)
        if outcome in ("supports", "refutes") and rows and not any(e["result"] == outcome for e in rows):
            raise InvalidRequest("結果の向きに合う記録（支持なら支持、反証なら反証）を1件以上含めてください")
        if a["activity_type"] == "competitor_research" and not conn.execute(
                "SELECT 1 FROM validation_activity_sources WHERE activity_id = ?", (activity_id,)).fetchone():
            raise InvalidRequest("競合調査の完了には、参照先を1件以上追加してください")
        now = db.now()

        def write():
            conn.execute(
                "INSERT INTO validation_activity_results (activity_id, outcome, observed_facts, interpretation, "
                "judgement_reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (activity_id, outcome, facts, interpretation, reason, now))
            conn.executemany(
                "INSERT INTO activity_result_evidence (activity_id, hypothesis_id, evidence_id, created_at) VALUES (?, ?, ?, ?)",
                [(activity_id, a["hypothesis_id"], eid, now) for eid in evidence_ids])
            conn.execute("UPDATE validation_activities SET status = 'done', completed_at = ? WHERE id = ?", (now, activity_id))
        _run(write)


def cancel_activity(activity_id: int, reason: str | None) -> None:
    reason = _text({"reason": reason}, "reason", "中止の理由")
    with db.connect() as conn:
        a = _activity(conn, activity_id)
        if a["status"] not in ("planned", "in_progress"):
            raise Conflict(f"{a['status']} の計画は中止できません")
        _run(lambda: conn.execute(
            "UPDATE validation_activities SET status = 'cancelled', cancel_reason = ?, cancelled_at = ? WHERE id = ?",
            (reason, db.now(), activity_id)))


# --- 評価 ---------------------------------------------------------------------

def add_assessment(hypothesis_id: int, data: dict) -> int:
    aspect, state, basis = data.get("aspect"), data.get("state"), data.get("basis")
    if aspect not in ASPECTS:
        raise InvalidRequest(f"aspect の値が不正です: {aspect}")
    if state not in STATES:
        raise InvalidRequest(f"state の値が不正です: {state}")
    if basis not in ("evidence", "assumption"):
        raise InvalidRequest("basis は evidence（記録に基づく）または assumption（推測）です")
    if state in STATE_DIRECTION and basis != "evidence":
        raise InvalidRequest("推測だけでは「根拠あり」「反証あり」にできません。記録を参照してください")
    if state == "not_researched" and basis != "assumption":
        raise InvalidRequest("未調査の評価は、推測のメモとして記録します")
    evidence_ids, activity_ids = _ids(data, "evidence_ids"), _ids(data, "activity_ids")
    if basis == "evidence":
        texts = {"observed_facts": _text(data, "observed_facts", "観察された事実"),
                 "relevance": _text(data, "relevance", "観点との関係"),
                 "judgement_reason": _text(data, "judgement_reason", "判断の理由"), "assumption_note": None}
        if not evidence_ids and not activity_ids:
            raise InvalidRequest("記録に基づく評価には、参照する検証記録または完了した計画を1件以上指定してください")
    else:
        texts = {"observed_facts": None, "relevance": None, "judgement_reason": None,
                 "assumption_note": _text(data, "assumption_note", "推測のメモ")}
        if evidence_ids or activity_ids:
            raise InvalidRequest("推測の評価には記録を参照しません（記録を参照する場合は basis を evidence にしてください）")

    policy = REF_POLICIES[aspect]
    direction = STATE_DIRECTION.get(state)
    with db.connect() as conn:
        if _hypothesis(conn, hypothesis_id)["status"] == "rejected":
            raise Conflict("棄却した仮説には評価を追加できません")
        retracted = _retracted_ids(conn)
        for eid in evidence_ids:
            e = conn.execute("SELECT * FROM hypothesis_evidence WHERE id = ?", (eid,)).fetchone()
            if e is None or e["hypothesis_id"] != hypothesis_id:
                raise InvalidRequest(f"同じ仮説の検証記録ではありません: {eid}")
            if e["kind"] not in policy["evidence"]:
                raise InvalidRequest(f"この観点では参照できない種類の記録です: {eid}（{e['kind']}）。統計・記事・メモは根拠に数えません")
            if eid in retracted:
                raise InvalidRequest(f"取り消された記録は参照できません: {eid}")
            if direction and e["result"] != direction:
                raise InvalidRequest(f"記録 {eid} の結果（{e['result']}）が評価の状態（{state}）と一致しません")
        for aid in activity_ids:
            a = conn.execute("SELECT * FROM validation_activities WHERE id = ?", (aid,)).fetchone()
            if a is None or a["hypothesis_id"] != hypothesis_id:
                raise InvalidRequest(f"同じ仮説の検証計画ではありません: {aid}")
            if a["activity_type"] not in policy["activity"]:
                raise InvalidRequest(f"この観点では参照できない種類の計画です: {aid}（{a['activity_type']}）")
            if a["status"] != "done":
                raise InvalidRequest(f"完了していない計画は参照できません: {aid}")
            outcome = conn.execute("SELECT outcome FROM validation_activity_results WHERE activity_id = ?", (aid,)).fetchone()[0]
            if direction and outcome != direction:
                raise InvalidRequest(f"計画 {aid} の結果（{outcome}）が評価の状態（{state}）と一致しません")
        if aspect == "willingness_to_pay" and state == "supported" and not any(
                conn.execute("SELECT kind FROM hypothesis_evidence WHERE id = ?", (eid,)).fetchone()[0] == "payment_intent"
                for eid in evidence_ids):
            raise InvalidRequest("支払意思の「根拠あり」には、支払意思を示す行動の記録（支持）を参照してください")

        now = db.now()

        def write():
            assessment_id = conn.execute(
                """INSERT INTO opportunity_assessments (hypothesis_id, aspect, state, basis, observed_facts, relevance,
                     judgement_reason, assumption_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (hypothesis_id, aspect, state, basis, texts["observed_facts"], texts["relevance"],
                 texts["judgement_reason"], texts["assumption_note"], now)).lastrowid
            conn.executemany(
                "INSERT INTO assessment_refs (assessment_id, hypothesis_id, evidence_id) VALUES (?, ?, ?)",
                [(assessment_id, hypothesis_id, eid) for eid in evidence_ids])
            conn.executemany(
                "INSERT INTO assessment_refs (assessment_id, hypothesis_id, activity_id) VALUES (?, ?, ?)",
                [(assessment_id, hypothesis_id, aid) for aid in activity_ids])
            # 参照を登録し終えてから確定する（確定前の行は API・画面に出さない）
            conn.execute("UPDATE opportunity_assessments SET committed_at = ? WHERE id = ?", (now, assessment_id))
            return assessment_id
        return _run(write)


def _assessment_detail(conn: sqlite3.Connection, s: sqlite3.Row, retracted: set[int]) -> dict:
    refs, retracted_refs = [], 0
    for r in conn.execute("SELECT * FROM assessment_refs WHERE assessment_id = ? ORDER BY id", (s["id"],)):
        if r["evidence_id"] is not None:
            e = conn.execute("SELECT * FROM hypothesis_evidence WHERE id = ?", (r["evidence_id"],)).fetchone()
            is_retracted = e["id"] in retracted
            retracted_refs += is_retracted
            refs.append({"type": "evidence", "id": e["id"], "kind": e["kind"], "result": e["result"],
                         "contact_ref": e["contact_ref"], "payment_signal": e["payment_signal"],
                         "occurred_on": e["occurred_on"], "description": e["description"],
                         "retracted": is_retracted, "timing": "unplanned", "timing_note": MODE_NOTES["unplanned"]})
        else:
            a = _activity_detail(conn, _activity(conn, r["activity_id"]), retracted)
            activity_retracted = any(l["retracted"] for l in a["linked_evidence"])
            retracted_refs += activity_retracted
            refs.append({"type": "activity", "id": a["id"], "kind": a["activity_type"],
                         "result": a["result"]["outcome"] if a["result"] else None,
                         "decision_criteria": a["locked_revision"]["decision_criteria"] if a["locked_revision"] else None,
                         "retracted": activity_retracted, "timing": a["mode"], "timing_note": a["mode_note"]})

    # 確定後に、同じ観点で参照できる種類の、逆の向きの記録が追加されたか
    later_opposite = 0
    direction = STATE_DIRECTION.get(s["state"])
    if direction:
        opposite = "refutes" if direction == "supports" else "supports"
        kinds = REF_POLICIES[s["aspect"]]["evidence"]
        if kinds:
            later_opposite += conn.execute(
                f"""SELECT COUNT(*) FROM hypothesis_evidence WHERE hypothesis_id = ? AND result = ?
                      AND kind IN ({",".join("?" * len(kinds))}) AND created_at > ?
                      AND id NOT IN (SELECT evidence_id FROM hypothesis_evidence_retractions)""",
                (s["hypothesis_id"], opposite, *kinds, s["committed_at"])).fetchone()[0]
        akinds = REF_POLICIES[s["aspect"]]["activity"]
        later_opposite += conn.execute(
            f"""SELECT COUNT(*) FROM validation_activities a JOIN validation_activity_results r ON r.activity_id = a.id
                WHERE a.hypothesis_id = ? AND r.outcome = ? AND a.activity_type IN ({",".join("?" * len(akinds))})
                  AND a.completed_at > ?""",
            (s["hypothesis_id"], opposite, *akinds, s["committed_at"])).fetchone()[0]

    warnings = []
    if retracted_refs:
        warnings.append(f"根拠の取り消し：参照した記録のうち {retracted_refs} 件が取り消されています。再評価が必要です")
    if later_opposite:
        warnings.append(f"矛盾する記録：評価の確定後に、逆の向きの記録が {later_opposite} 件追加されています。再評価が必要です")
    timings = {r["timing"] for r in refs}
    post_hoc = bool(timings & {"unplanned", "post_hoc", "mixed"})
    return {
        "id": s["id"], "aspect": s["aspect"], "state": s["state"], "basis": s["basis"],
        "human_judgement": {"observed_facts": s["observed_facts"], "relevance": s["relevance"],
                            "judgement_reason": s["judgement_reason"], "assumption_note": s["assumption_note"]},
        "mechanical_checks": {
            "references_exist_in_same_hypothesis": True,  # 複合外部キーで保証
            "kinds_allowed_for_aspect": True,  # 確定時に DB・API で確認済み
            "direction_matches_state": bool(direction) or None,
            "retracted_references_now": retracted_refs,
            "note": "機械的な確認は記録の種類・向き・取り消しのみ。内容の妥当性は人の判断です",
        },
        "refs": refs,
        "post_hoc": post_hoc,
        "post_hoc_note": "事後評価を含む：事前に判断基準を定めて実施した検証だけに基づく評価ではありません" if post_hoc else None,
        "warnings": warnings,
        "needs_reevaluation": bool(warnings),
        "created_at": s["created_at"], "committed_at": s["committed_at"],
    }


# --- 支払意思の内訳 -------------------------------------------------------------

def payment_summary(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    rows = conn.execute(
        """SELECT payment_signal, result FROM hypothesis_evidence WHERE hypothesis_id = ? AND kind = 'payment_intent'
             AND id NOT IN (SELECT evidence_id FROM hypothesis_evidence_retractions)""", (hypothesis_id,)).fetchall()
    supporting = {k: 0 for k in PAYMENT_SIGNALS}
    for r in rows:
        if r["result"] == "supports":
            supporting[r["payment_signal"]] += 1
    return {
        "supporting_by_signal": supporting,
        "refuting": sum(1 for r in rows if r["result"] == "refutes"),
        "actual_payments": supporting["paid"],
        "signal_meanings": PAYMENT_SIGNALS,
        "note": "種類ごとの件数です。点数にまとめていません。購入意向書・見積の合意は支払いではありません",
    }


# --- 一覧・詳細 -----------------------------------------------------------------

def _selection_state(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    current = conn.execute("SELECT reason, created_at FROM current_opportunity_selections WHERE hypothesis_id = ?",
                           (hypothesis_id,)).fetchone()
    return {"selected": current is not None, "reason": current["reason"] if current else None,
            "selected_at": current["created_at"] if current else None}


def _hypothesis_warnings(h: sqlite3.Row, latest: dict, validation: dict, payment: dict) -> list[str]:
    warnings = []
    if h["status"] == "validated" and any(a and a["state"] == "contradicted" for a in latest.values()):
        warnings.append("仮説は「検証で支持」ですが、反証ありの観点があります（状態は自動では変わりません）")
    if (h["status"] == "validated" and validation["independent_supporting_contacts"] < validation["required_independent_contacts"]
            and payment["actual_payments"] == 0 and sum(payment["supporting_by_signal"].values()) > 0
            and payment["supporting_by_signal"]["preorder"] == 0 and payment["supporting_by_signal"]["signed_quote"] == 0):
        warnings.append("「検証で支持」の根拠は購入意向書のみです（支払いの約束ではありません）")
    if h["status"] == "rejected":
        warnings.append("棄却された仮説です（評価・計画は読み取り専用）")
    return warnings


def _latest_assessments(conn: sqlite3.Connection, hypothesis_id: int, retracted: set[int]) -> dict:
    latest = {}
    for aspect in ASPECTS:
        s = conn.execute(
            """SELECT * FROM opportunity_assessments WHERE hypothesis_id = ? AND aspect = ? AND committed_at IS NOT NULL
               ORDER BY id DESC LIMIT 1""", (hypothesis_id, aspect)).fetchone()
        latest[aspect] = _assessment_detail(conn, s, retracted) if s else None
    return latest


def list_opportunities(domain: str | None = None) -> dict:
    from .hypothesis_rules import DOMAINS
    if domain is not None and domain not in DOMAINS:
        raise InvalidRequest(f"domain の値が不正です: {domain}")
    with db.connect() as conn:
        db.migrate(conn)
        retracted = _retracted_ids(conn)
        rows = []
        for h in conn.execute("SELECT * FROM hypotheses ORDER BY id"):
            if domain and h["domain"] != domain:
                continue
            latest = _latest_assessments(conn, h["id"], retracted)
            validation = validation_progress(conn, h["id"])
            payment = payment_summary(conn, h["id"])
            counts = {s: 0 for s in ("planned", "in_progress", "done", "cancelled")}
            for r in conn.execute("SELECT status, COUNT(*) FROM validation_activities WHERE hypothesis_id = ? GROUP BY status",
                                  (h["id"],)):
                counts[r[0]] = r[1]
            rows.append({
                "id": h["id"], "domain": h["domain"], "title": h["title"], "target_customer": h["target_customer"],
                "status": h["status"], "rule_key": h["rule_key"],
                "assessments": latest, "activity_counts": counts, "validation": validation, "payment": payment,
                "selection": _selection_state(conn, h["id"]),
                "warnings": _hypothesis_warnings(h, latest, validation, payment),
            })
        selected = conn.execute("SELECT COUNT(*) FROM current_opportunity_selections").fetchone()[0]
    return {"opportunities": rows, "selected_count": selected, "max_selections": MAX_SELECTIONS, "disclaimer": DISCLAIMER}


def get_opportunity(hypothesis_id: int) -> dict:
    with db.connect() as conn:
        db.migrate(conn)
        h = _hypothesis(conn, hypothesis_id)
        retracted = _retracted_ids(conn)
        history = {aspect: [] for aspect in ASPECTS}
        for s in conn.execute(
                "SELECT * FROM opportunity_assessments WHERE hypothesis_id = ? AND committed_at IS NOT NULL ORDER BY id",
                (hypothesis_id,)):
            history[s["aspect"]].append(_assessment_detail(conn, s, retracted))
        latest = {a: (items[-1] if items else None) for a, items in history.items()}
        activities = [_activity_detail(conn, a, retracted) for a in conn.execute(
            "SELECT * FROM validation_activities WHERE hypothesis_id = ? ORDER BY id", (hypothesis_id,))]
        # 評価で参照できる候補（観点ごと）。統計・記事・メモは含めない
        evidence = [dict(e) | {"retracted": e["id"] in retracted} for e in conn.execute(
            "SELECT * FROM hypothesis_evidence WHERE hypothesis_id = ? AND role = 'validation' ORDER BY id", (hypothesis_id,))]
        candidates = {}
        for aspect, policy in REF_POLICIES.items():
            candidates[aspect] = {
                "evidence": [{"id": e["id"], "kind": e["kind"], "result": e["result"], "contact_ref": e["contact_ref"],
                              "payment_signal": e["payment_signal"], "occurred_on": e["occurred_on"],
                              "description": e["description"]}
                             for e in evidence if e["kind"] in policy["evidence"] and not e["retracted"]],
                "activities": [{"id": a["id"], "kind": a["activity_type"], "result": a["result"]["outcome"],
                                "mode": a["mode"]}
                               for a in activities if a["activity_type"] in policy["activity"] and a["status"] == "done"],
            }
        selections = [dict(r) for r in conn.execute(
            "SELECT * FROM opportunity_selections WHERE hypothesis_id = ? ORDER BY id", (hypothesis_id,))]
        validation = validation_progress(conn, hypothesis_id)
        payment = payment_summary(conn, hypothesis_id)
        # 計画の結果に紐付けられる候補（未取り消しの顧客接点の記録）
        linkable = [{"id": e["id"], "kind": e["kind"], "result": e["result"], "contact_ref": e["contact_ref"],
                     "payment_signal": e["payment_signal"], "occurred_on": e["occurred_on"], "created_at": e["created_at"]}
                    for e in evidence if not e["retracted"]]
        return {
            "hypothesis": {k: h[k] for k in ("id", "domain", "title", "target_customer", "status", "rule_key", "next_action")},
            "assessments": latest, "assessment_history": history, "activities": activities,
            "ref_candidates": candidates, "linkable_evidence": linkable, "selections": selections,
            "selection": _selection_state(conn, hypothesis_id), "validation": validation, "payment": payment,
            "warnings": _hypothesis_warnings(h, latest, validation, payment), "disclaimer": DISCLAIMER,
        }


def aspects_overview() -> dict:
    return {
        "aspects": [{"key": k, **v, "allowed_evidence": list(REF_POLICIES[k]["evidence"]),
                     "allowed_activities": list(REF_POLICIES[k]["activity"])} for k, v in ASPECTS.items()],
        "states": {"not_researched": "未調査", "researching": "調査中", "supported": "根拠あり（事業化に有利な方向の根拠）",
                   "contradicted": "反証あり（事業化に不利な方向の根拠）"},
        "payment_signals": PAYMENT_SIGNALS,
        "mode_notes": MODE_NOTES,
        "disclaimer": DISCLAIMER,
    }


# --- 選定（最大3件） -------------------------------------------------------------

def select(hypothesis_id: int, reason: str | None) -> None:
    reason = _text({"reason": reason}, "reason", "選定の理由")
    with db.connect() as conn:
        h = _hypothesis(conn, hypothesis_id)
        if h["status"] == "rejected":
            raise Conflict("棄却した仮説は選定できません")
        if _selection_state(conn, hypothesis_id)["selected"]:
            raise Conflict("すでに選定しています")
        if conn.execute("SELECT COUNT(*) FROM current_opportunity_selections").fetchone()[0] >= MAX_SELECTIONS:
            raise Conflict(f"選定できるのは最大 {MAX_SELECTIONS} 件です。先に別の候補の選定を外してください")
        _run(lambda: conn.execute(
            "INSERT INTO opportunity_selections (hypothesis_id, action, reason, created_at) VALUES (?, 'select', ?, ?)",
            (hypothesis_id, reason, db.now())))


def unselect(hypothesis_id: int, reason: str | None) -> None:
    reason = _text({"reason": reason}, "reason", "選定を外す理由")
    with db.connect() as conn:
        _hypothesis(conn, hypothesis_id)
        if not _selection_state(conn, hypothesis_id)["selected"]:
            raise Conflict("選定されていません")
        _run(lambda: conn.execute(
            "INSERT INTO opportunity_selections (hypothesis_id, action, reason, created_at) VALUES (?, 'unselect', ?, ?)",
            (hypothesis_id, reason, db.now())))


def is_selected(conn: sqlite3.Connection, hypothesis_id: int) -> bool:
    return conn.execute("SELECT 1 FROM current_opportunity_selections WHERE hypothesis_id = ?",
                        (hypothesis_id,)).fetchone() is not None
