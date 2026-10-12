"""SaaS事業機会の比較・選定・検証計画（v0.7）のテスト。すべて一時DBで実行する。"""

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, hypotheses as hyp, main, opportunities as opp
from app.estat_import import import_file
from app.hypotheses import Conflict, InvalidRequest
from app.sources import SOURCES
from tests.test_estat_import import REAL_XLSX, SOURCE_URL, TABLE_REF

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db(SOURCES)
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    hyp.generate()
    return tmp_path


def hid(rule_key):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM hypotheses WHERE rule_key = ?", (rule_key,)).fetchone()[0]


def rows(sql, params=()):
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(sql, params)]


def raw(sql, params=()):
    with db.connect() as conn:
        conn.execute(sql, params)


def record(h, contact="C-001", result="supports", kind="interview", occurred_on=TODAY, **extra):
    return hyp.add_evidence(h, {"kind": kind, "contact_ref": contact, "result": result, "occurred_on": occurred_on,
                                "description": "月末の請求書作成に半日かかっていると回答", **extra})


def evidence_assessment(h, aspect="severity", state="supported", evidence_ids=(), activity_ids=(), **extra):
    return opp.add_assessment(h, {
        "aspect": aspect, "state": state, "basis": "evidence",
        "observed_facts": "3社中2社が月末に半日以上を請求書作成に使っていた",
        "relevance": "課題による時間の損失を示す", "judgement_reason": "具体的な時間で確認できたため",
        "evidence_ids": list(evidence_ids), "activity_ids": list(activity_ids), **extra})


def plan(h, activity_type="interview", **extra):
    return opp.create_activity(h, {"activity_type": activity_type, "purpose": "請求業務の負担を確認する",
                                   "target": "掛け売りのある個人企業3社", "decision_criteria": "2社以上が月3時間以上を使っていれば支持",
                                   **extra})


def done_activity(h, activity_type="interview", outcome="supports", evidence_ids=None, sources=False):
    aid = plan(h, activity_type)
    opp.start_activity(aid)
    if sources:
        opp.add_source(aid, {"url": "https://example.com/pricing", "accessed_on": TODAY, "title": "競合の料金ページ"})
    if evidence_ids is None and activity_type in opp.CUSTOMER_CONTACT_ACTIVITIES:
        kind = "interview" if activity_type == "interview" else "channel_test"
        evidence_ids = [record(h, contact="C-090", result=outcome if outcome != "inconclusive" else "supports", kind=kind)]
    opp.complete_activity(aid, {"outcome": outcome, "observed_facts": "事実", "interpretation": "基準に照らすと支持",
                                "judgement_reason": "理由", "evidence_ids": evidence_ids or []})
    return aid


# --- マイグレーションと既存データ ---------------------------------------------------

def test_v6_db_migrates_to_v7_preserving_existing_tables(env):
    path = env / "test.db"
    conn = sqlite3.connect(path)
    tables = ("articles", "article_markets", "article_labels", "stat_tables", "problem_observations",
              "hypotheses", "hypothesis_evidence", "hypothesis_events", "hypothesis_evidence_retractions")
    before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    conn.execute("DROP VIEW current_opportunity_selections")
    conn.execute("DROP INDEX ux_hypothesis_evidence_id_hypothesis")
    for t in ("opportunity_selections", "assessment_refs", "assessment_ref_policies", "opportunity_assessments",
              "activity_result_evidence", "validation_activity_sources", "validation_activity_results",
              "validation_activity_revisions", "validation_activities"):
        conn.execute(f"DROP TABLE {t}")
    conn.execute("DROP TRIGGER trg_hypotheses_reject_selected")
    for c in ("fetch_etag", "fetch_last_modified", "last_fetched_at", "last_fetch_status", "last_fetch_error"):
        conn.execute(f"ALTER TABLE sources DROP COLUMN {c}")  # v8 で追加した列
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    db.init_db(SOURCES)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables} == before
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_ref_policies_in_db_match_code(env):
    in_db = {(a, s, k) for a, s, k in rows("SELECT aspect, source, kind FROM assessment_ref_policies")}
    in_code = {(a, s, k) for a, p in opp.REF_POLICIES.items() for s, kinds in p.items() for k in kinds}
    assert in_db == in_code
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO assessment_ref_policies VALUES ('severity', 'evidence', 'note')")


def test_v07_operations_do_not_modify_existing_tables(env):
    tables = ("articles", "stat_tables", "problem_observations", "hypotheses")
    before = {t: rows(f"SELECT * FROM {t}") for t in tables}
    h = hid("p05.finance")
    opp.select(h, "請求業務は対象者に接触しやすいと考えたため")
    opp.add_assessment(h, {"aspect": "reachability", "state": "researching", "basis": "assumption",
                           "assumption_note": "知人の会計事務所経由で紹介を受けられそう（推測）"})
    plan(h)
    assert {t: rows(f"SELECT * FROM {t}") for t in tables} == before


# --- 選定（最大3件） -----------------------------------------------------------

def test_selection_limit_and_rules(env):
    a, b, c, d = hid("p05.finance"), hid("p07.operations"), hid("p09.finance.food"), hid("p06.operations")
    for h in (a, b, c):
        opp.select(h, "最初の調査候補として選ぶ")
    with pytest.raises(Conflict, match="最大 3"):
        opp.select(d, "4件目")
    with pytest.raises(sqlite3.IntegrityError):  # DB でも上限を強制
        raw("INSERT INTO opportunity_selections (hypothesis_id, action, reason, created_at) VALUES (?, 'select', 'r', 'c')", (d,))
    with pytest.raises(Conflict, match="すでに"):
        opp.select(a, "重複")
    with pytest.raises(InvalidRequest, match="理由"):
        opp.unselect(a, "  ")
    opp.unselect(a, "接触経路が見つからなかったため")
    opp.select(d, "代わりに予約業務を調べる")
    assert sorted(r[0] for r in rows("SELECT hypothesis_id FROM current_opportunity_selections")) == sorted([b, c, d])
    with pytest.raises(Conflict, match="選定されていません"):
        opp.unselect(a, "再度")
    assert len(rows("SELECT * FROM opportunity_selections")) == 5  # 履歴は残る
    with pytest.raises(sqlite3.IntegrityError):
        raw("UPDATE opportunity_selections SET reason = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        raw("DELETE FROM opportunity_selections")


def test_selected_hypothesis_cannot_be_rejected_and_rejected_cannot_be_selected(env):
    h, other = hid("p05.finance"), hid("p12.cs")
    opp.select(h, "調査する")
    with pytest.raises(Conflict, match="選定を外して"):
        hyp.set_status(h, "rejected", "対象が見つからない")
    with pytest.raises(sqlite3.IntegrityError):
        raw("UPDATE hypotheses SET status = 'rejected', status_reason = 'r' WHERE id = ?", (h,))
    hyp.set_status(other, "rejected", "値上げの懸念は薄いと分かった")
    with pytest.raises(Conflict, match="棄却"):
        opp.select(other, "選ぶ")
    with pytest.raises(Conflict, match="棄却"):
        plan(other)
    with pytest.raises(Conflict, match="棄却"):
        opp.add_assessment(other, {"aspect": "severity", "state": "researching", "basis": "assumption", "assumption_note": "x"})


# --- 評価：参照の整合性・種類・向き・取り消し -------------------------------------------

def test_evidence_of_another_hypothesis_cannot_be_referenced(env):
    h, other = hid("p05.finance"), hid("p07.operations")
    foreign = record(other)
    with pytest.raises(InvalidRequest, match="同じ仮説"):
        evidence_assessment(h, evidence_ids=[foreign])
    with pytest.raises(InvalidRequest, match="同じ仮説"):
        evidence_assessment(h, evidence_ids=[99999])
    # DB でも複合外部キーで拒否する
    raw("INSERT INTO opportunity_assessments (hypothesis_id, aspect, state, basis, observed_facts, relevance,"
        " judgement_reason, created_at) VALUES (?, 'severity', 'supported', 'evidence', 'f', 'r', 'j', 'c')", (h,))
    aid = rows("SELECT MAX(id) FROM opportunity_assessments")[0][0]
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO assessment_refs (assessment_id, hypothesis_id, evidence_id) VALUES (?, ?, ?)", (aid, h, foreign))
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO assessment_refs (assessment_id, hypothesis_id, evidence_id) VALUES (?, ?, ?)", (aid, other, foreign))


def test_assumption_cannot_reach_supported_and_has_no_refs(env):
    h = hid("p05.finance")
    e = record(h)
    with pytest.raises(InvalidRequest, match="推測だけでは"):
        opp.add_assessment(h, {"aspect": "severity", "state": "supported", "basis": "assumption", "assumption_note": "x"})
    with pytest.raises(InvalidRequest, match="参照しません"):
        opp.add_assessment(h, {"aspect": "severity", "state": "researching", "basis": "assumption",
                               "assumption_note": "x", "evidence_ids": [e]})
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO opportunity_assessments (hypothesis_id, aspect, state, basis, assumption_note, created_at)"
            " VALUES (?, 'severity', 'supported', 'assumption', 'x', 'c')", (h,))


@pytest.mark.parametrize("missing", ["observed_facts", "relevance", "judgement_reason"])
def test_human_judgement_fields_are_required(env, missing):
    h = hid("p05.finance")
    e = record(h)
    with pytest.raises(InvalidRequest, match="必須"):
        evidence_assessment(h, evidence_ids=[e], **{missing: ""})


def test_kind_direction_and_retraction_checks(env):
    h = hid("p05.finance")
    note = hyp.add_evidence(h, {"kind": "note", "description": "知人が困っていると言っていた"})
    observation = next(e["id"] for e in hyp.get_hypothesis(h)["evidence"] if e["kind"] == "problem_observation")
    refuting = record(h, contact="C-002", result="refutes")
    supporting = record(h, contact="C-003")
    channel = record(h, contact="LP-1", kind="channel_test")
    for eid, message in ((note, "種類"), (observation, "種類"), (channel, "種類"), (refuting, "一致しません")):
        with pytest.raises(InvalidRequest, match=message):
            evidence_assessment(h, aspect="severity", evidence_ids=[eid])
    with pytest.raises(InvalidRequest, match="一致しません"):
        evidence_assessment(h, state="contradicted", evidence_ids=[supporting])
    evidence_assessment(h, state="researching", evidence_ids=[refuting, supporting])  # 調査中は向きを問わない
    hyp.retract_evidence(h, supporting, "別の仮説の記録だった")
    with pytest.raises(InvalidRequest, match="取り消された"):
        evidence_assessment(h, evidence_ids=[supporting])


def test_db_rejects_disallowed_references_without_api(env):
    h = hid("p05.finance")
    note = hyp.add_evidence(h, {"kind": "note", "description": "メモ"})
    refuting = record(h, result="refutes")
    raw("INSERT INTO opportunity_assessments (hypothesis_id, aspect, state, basis, observed_facts, relevance,"
        " judgement_reason, created_at) VALUES (?, 'severity', 'supported', 'evidence', 'f', 'r', 'j', 'c')", (h,))
    aid = rows("SELECT MAX(id) FROM opportunity_assessments")[0][0]
    for eid in (note, refuting):
        with pytest.raises(sqlite3.IntegrityError):
            raw("INSERT INTO assessment_refs (assessment_id, hypothesis_id, evidence_id) VALUES (?, ?, ?)", (aid, h, eid))
    with pytest.raises(sqlite3.IntegrityError):  # 参照なしでは確定できない
        raw("UPDATE opportunity_assessments SET committed_at = 'now' WHERE id = ?", (aid,))


def test_willingness_to_pay_requires_payment_intent(env):
    h = hid("p05.finance")
    interview = record(h)
    with pytest.raises(InvalidRequest, match="支払意思を示す行動"):
        evidence_assessment(h, aspect="willingness_to_pay", evidence_ids=[interview])
    loi = record(h, contact="C-010", kind="payment_intent", payment_signal="letter_of_intent")
    evidence_assessment(h, aspect="willingness_to_pay", evidence_ids=[loi])
    detail = opp.get_opportunity(h)
    assert detail["assessments"]["willingness_to_pay"]["state"] == "supported"
    assert detail["payment"]["supporting_by_signal"] == {"letter_of_intent": 1, "signed_quote": 0, "preorder": 0, "paid": 0}
    assert detail["payment"]["actual_payments"] == 0
    assert "支払いの約束ではない" in detail["payment"]["signal_meanings"]["letter_of_intent"]


def test_uncommitted_assessments_are_hidden_and_committed_are_immutable(env):
    h = hid("p05.finance")
    raw("INSERT INTO opportunity_assessments (hypothesis_id, aspect, state, basis, assumption_note, created_at)"
        " VALUES (?, 'severity', 'researching', 'assumption', '確定前', 'c')", (h,))
    assert opp.get_opportunity(h)["assessments"]["severity"] is None
    assert opp.list_opportunities()["opportunities"][h - 1]["assessments"]["severity"] is None
    e = record(h)
    aid = evidence_assessment(h, evidence_ids=[e])
    for sql in ("UPDATE opportunity_assessments SET state = 'contradicted' WHERE id = ?",
                "UPDATE opportunity_assessments SET committed_at = 'later' WHERE id = ?",
                "DELETE FROM opportunity_assessments WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            raw(sql, (aid,))
    with pytest.raises(sqlite3.IntegrityError):  # 確定後に参照を追加できない
        raw("INSERT INTO assessment_refs (assessment_id, hypothesis_id, evidence_id) VALUES (?, ?, ?)", (aid, h, record(h, "C-5")))


def test_latest_assessment_per_aspect_and_history(env):
    h = hid("p05.finance")
    opp.add_assessment(h, {"aspect": "severity", "state": "researching", "basis": "assumption", "assumption_note": "推測"})
    evidence_assessment(h, evidence_ids=[record(h)])
    d = opp.get_opportunity(h)
    assert d["assessments"]["severity"]["state"] == "supported"
    assert [a["state"] for a in d["assessment_history"]["severity"]] == ["researching", "supported"]
    latest = d["assessments"]["severity"]
    assert latest["human_judgement"]["observed_facts"].startswith("3社中2社")
    assert latest["mechanical_checks"]["kinds_allowed_for_aspect"] is True
    assert "内容の妥当性は人の判断" in latest["mechanical_checks"]["note"]


# --- 取り消し・矛盾の警告 ----------------------------------------------------------

def test_retraction_marks_assessment_for_reevaluation_without_changing_history(env):
    h = hid("p05.finance")
    hyp.set_status(h, "in_progress", None)
    e = record(h)
    aid = evidence_assessment(h, evidence_ids=[e])
    before = rows("SELECT * FROM opportunity_assessments WHERE id = ?", (aid,))
    hyp.retract_evidence(h, e, "誤記録")
    latest = opp.get_opportunity(h)["assessments"]["severity"]
    assert latest["needs_reevaluation"] and "取り消し" in latest["warnings"][0]
    assert rows("SELECT * FROM opportunity_assessments WHERE id = ?", (aid,)) == before
    evidence_assessment(h, evidence_ids=[record(h, contact="C-007")])  # 再評価
    assert opp.get_opportunity(h)["assessments"]["severity"]["needs_reevaluation"] is False


def test_later_opposite_record_warns(env):
    h = hid("p05.finance")
    evidence_assessment(h, evidence_ids=[record(h)])
    record(h, contact="C-008", result="refutes")
    latest = opp.get_opportunity(h)["assessments"]["severity"]
    assert latest["needs_reevaluation"] and "矛盾する記録" in latest["warnings"][0]


def test_validated_hypothesis_with_contradicted_aspect_warns(env):
    h = hid("p06.operations")
    hyp.set_status(h, "in_progress", None)
    record(h, contact="C-001")
    record(h, contact="C-002")
    hyp.set_status(h, "validated", "2接点で支持")
    evidence_assessment(h, aspect="severity", state="contradicted", evidence_ids=[record(h, contact="C-003", result="refutes")],
                        judgement_reason="負担は小さかった")
    assert hyp.get_hypothesis(h)["status"] == "validated"  # 状態は自動では変わらない
    assert any("反証ありの観点" in w for w in opp.get_opportunity(h)["warnings"])


def test_validated_with_letter_of_intent_only_is_flagged(env):
    h = hid("p06.operations")
    hyp.set_status(h, "in_progress", None)
    record(h, contact="C-001", kind="payment_intent", payment_signal="letter_of_intent")
    hyp.set_status(h, "validated", "購入意向書を得た")  # v0.6 の条件は変えない
    assert any("購入意向書のみ" in w for w in opp.get_opportunity(h)["warnings"])


# --- 検証計画：版・固定・状態・完了条件 ----------------------------------------------------

def test_revisions_before_start_and_lock_on_start(env):
    h = hid("p05.finance")
    aid = plan(h)
    with pytest.raises(InvalidRequest, match="訂正の理由"):
        opp.revise_activity(aid, {"purpose": "p", "target": "t", "decision_criteria": "c"})
    opp.revise_activity(aid, {"purpose": "請求と入金確認の負担を確認する", "target": "掛け売りのある個人企業5社",
                              "decision_criteria": "3社以上が月3時間以上なら支持", "change_reason": "対象数を見直した"})
    opp.start_activity(aid)
    a = next(x for x in opp.get_opportunity(h)["activities"] if x["id"] == aid)
    assert [r["revision_no"] for r in a["revisions"]] == [1, 2]
    assert a["locked_revision"]["revision_no"] == 2 and a["locked_revision"]["decision_criteria"].startswith("3社以上")
    with pytest.raises(Conflict, match="開始後"):
        opp.revise_activity(aid, {"purpose": "p", "target": "t", "decision_criteria": "結果に合わせた基準", "change_reason": "x"})
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO validation_activity_revisions (activity_id, revision_no, purpose, target, decision_criteria,"
            " change_reason, created_at) VALUES (?, 3, 'p', 't', 'c', 'r', 'c')", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        raw("UPDATE validation_activity_revisions SET decision_criteria = '変更' WHERE activity_id = ?", (aid,))


def test_locked_revision_must_belong_to_same_activity_and_be_latest(env):
    h = hid("p05.finance")
    a1, a2 = plan(h), plan(h)
    other_rev = rows("SELECT id FROM validation_activity_revisions WHERE activity_id = ?", (a2,))[0][0]
    with pytest.raises(sqlite3.IntegrityError):  # 別の計画の版は固定できない（複合外部キー・トリガー）
        raw("UPDATE validation_activities SET status = 'in_progress', locked_revision_id = ?, started_at = 'x' WHERE id = ?",
            (other_rev, a1))
    opp.revise_activity(a1, {"purpose": "p2", "target": "t2", "decision_criteria": "c2", "change_reason": "訂正"})
    old_rev = rows("SELECT id FROM validation_activity_revisions WHERE activity_id = ? AND revision_no = 1", (a1,))[0][0]
    with pytest.raises(sqlite3.IntegrityError):  # 最新でない版は固定できない
        raw("UPDATE validation_activities SET status = 'in_progress', locked_revision_id = ?, started_at = 'x' WHERE id = ?",
            (old_rev, a1))
    opp.start_activity(a1)
    with pytest.raises(sqlite3.IntegrityError):  # 固定後は変更できない
        raw("UPDATE validation_activities SET locked_revision_id = ? WHERE id = ?", (old_rev, a1))


def test_activity_transitions_and_completion_requirements(env):
    h = hid("p05.finance")
    aid = plan(h)
    with pytest.raises(Conflict, match="実施中"):
        opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                    "judgement_reason": "r", "evidence_ids": []})
    opp.start_activity(aid)
    base = {"outcome": "supports", "observed_facts": "f", "interpretation": "i", "judgement_reason": "r"}
    with pytest.raises(InvalidRequest, match="1件以上紐付け"):
        opp.complete_activity(aid, base | {"evidence_ids": []})
    with pytest.raises(InvalidRequest, match="紐付けられない種類"):
        opp.complete_activity(aid, base | {"evidence_ids": [record(h, kind="channel_test", contact="LP-1")]})
    with pytest.raises(InvalidRequest, match="向きに合う"):
        opp.complete_activity(aid, base | {"evidence_ids": [record(h, result="refutes")]})
    with pytest.raises(InvalidRequest, match="必須"):
        opp.complete_activity(aid, base | {"observed_facts": "", "evidence_ids": [record(h, contact="C-2")]})
    opp.complete_activity(aid, base | {"evidence_ids": [record(h, contact="C-3")]})
    with pytest.raises(Conflict):
        opp.cancel_activity(aid, "やめる")
    with pytest.raises(sqlite3.IntegrityError):
        raw("UPDATE validation_activities SET status = 'cancelled', cancel_reason = 'x', cancelled_at = 'x' WHERE id = ?", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        raw("DELETE FROM validation_activities WHERE id = ?", (aid,))


def test_cancel_requires_reason(env):
    h = hid("p05.finance")
    aid = plan(h)
    with pytest.raises(InvalidRequest, match="中止の理由"):
        opp.cancel_activity(aid, "")
    opp.cancel_activity(aid, "対象者の都合がつかない")
    with pytest.raises(Conflict):
        opp.start_activity(aid)


def test_desk_research_results_are_separate_from_customer_records(env):
    h = hid("p07.operations")
    before = rows("SELECT COUNT(*) FROM hypothesis_evidence")
    aid = plan(h, "competitor_research")
    opp.start_activity(aid)
    with pytest.raises(InvalidRequest, match="参照先"):
        opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                    "judgement_reason": "r"})
    with pytest.raises(InvalidRequest, match="顧客接点の検証記録を紐付けません"):
        opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                    "judgement_reason": "r", "evidence_ids": [record(h)]})
    opp.add_source(aid, {"url": "https://example.com/a", "accessed_on": TODAY})
    opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "主要3製品は月額5千円以上",
                                "interpretation": "小規模向けの安価な製品が少ない", "judgement_reason": "料金表で確認"})
    assert rows("SELECT COUNT(*) FROM hypothesis_evidence")[0][0] == before[0][0] + 1  # 上の record() の1件だけ
    assert hyp.get_hypothesis(h)["validation"]["independent_supporting_contacts"] == 1  # 競合調査は数えない
    with pytest.raises(InvalidRequest, match="参照できない種類の計画"):  # 競合調査は「課題の深刻さ」の根拠にならない
        evidence_assessment(h, aspect="severity", activity_ids=[aid])
    evidence_assessment(h, aspect="alternatives", activity_ids=[aid])
    tech = done_activity(h, "technical_check")
    evidence_assessment(h, aspect="build_operate_burden", activity_ids=[tech])


def test_unfinished_activity_cannot_be_referenced(env):
    h = hid("p05.finance")
    aid = plan(h)
    with pytest.raises(InvalidRequest, match="完了していない"):
        evidence_assessment(h, activity_ids=[aid])


# --- 開始前の記録（事後評価）の区別 ------------------------------------------------------

def test_pre_start_records_are_marked_post_hoc(env):
    h = hid("p05.finance")
    early = record(h, contact="C-001", occurred_on=YESTERDAY)  # 計画より前に実施・記録した
    aid = plan(h)
    opp.start_activity(aid)
    late = record(h, contact="C-002")
    opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                "judgement_reason": "r", "evidence_ids": [early, late]})
    a = next(x for x in opp.get_opportunity(h)["activities"] if x["id"] == aid)
    assert {e["id"]: e["timing"] for e in a["linked_evidence"]} == {early: "before_start", late: "after_start"}
    assert a["mode"] == "mixed" and "事後評価" in a["mode_note"]

    post_hoc_only = plan(h)
    opp.start_activity(post_hoc_only)
    opp.complete_activity(post_hoc_only, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                          "judgement_reason": "r", "evidence_ids": [early]})
    a2 = next(x for x in opp.get_opportunity(h)["activities"] if x["id"] == post_hoc_only)
    assert a2["mode"] == "post_hoc" and "事前に判断基準を定めて実施した検証ではない" in a2["mode_note"]

    evidence_assessment(h, activity_ids=[post_hoc_only])
    s = opp.get_opportunity(h)["assessments"]["severity"]
    assert s["post_hoc"] is True and s["refs"][0]["timing"] == "post_hoc"


def test_direct_evidence_reference_is_marked_unplanned(env):
    h = hid("p05.finance")
    evidence_assessment(h, evidence_ids=[record(h)])
    s = opp.get_opportunity(h)["assessments"]["severity"]
    assert s["refs"][0]["timing"] == "unplanned" and s["post_hoc"] is True


def test_fully_pre_registered_activity(env):
    h = hid("p05.finance")
    aid = done_activity(h)
    a = next(x for x in opp.get_opportunity(h)["activities"] if x["id"] == aid)
    assert a["mode"] == "pre_registered"
    evidence_assessment(h, activity_ids=[aid])
    assert opp.get_opportunity(h)["assessments"]["severity"]["post_hoc"] is False


def test_retracted_linked_evidence_flags_activity_and_assessment(env):
    h = hid("p05.finance")
    hyp.set_status(h, "in_progress", None)
    aid = plan(h)
    opp.start_activity(aid)
    e = record(h)
    opp.complete_activity(aid, {"outcome": "supports", "observed_facts": "f", "interpretation": "i",
                                "judgement_reason": "r", "evidence_ids": [e]})
    evidence_assessment(h, activity_ids=[aid])
    hyp.retract_evidence(h, e, "誤り")
    d = opp.get_opportunity(h)
    assert d["activities"][0]["warnings"] and d["assessments"]["severity"]["needs_reevaluation"]


# --- v0.6 への影響がないこと -------------------------------------------------------

def test_v06_validation_and_rules_unchanged(env):
    h = hid("p05.finance")
    done_activity(h)
    plan(h)
    assert hyp.get_hypothesis(h)["validation"]["requirements_met"] is False  # 1接点のみ
    result = hyp.generate()
    assert len(result.created) == 0
    assert rows("SELECT COUNT(*) FROM validation_activities WHERE hypothesis_id = ?", (h,)) == [(2,)]


# --- API ----------------------------------------------------------------------

@pytest.fixture
def client(env):
    with TestClient(main.app) as c:
        yield c


def test_list_api_has_no_scores(client):
    data = client.get("/api/opportunities").json()
    assert len(data["opportunities"]) == 13 and data["max_selections"] == 3
    keys = set(data["opportunities"][0])
    assert not keys & {"score", "rank", "priority", "profitability"}
    assert set(data["opportunities"][0]["assessments"]) == set(opp.ASPECTS)
    assert "点数・順位はありません" in data["disclaimer"]
    fin = client.get("/api/opportunities", params={"domain": "finance"}).json()["opportunities"]
    assert [o["rule_key"] for o in fin] == ["p05.finance", "p09.finance.food", "p12.finance"]
    assert client.get("/api/opportunities", params={"domain": "hr"}).status_code == 422


def test_api_flow_and_errors(client):
    h = hid("p05.finance")
    assert client.get("/api/opportunities/99999").status_code == 404
    assert client.post("/api/activities/99999/start").status_code == 404
    r = client.post(f"/api/opportunities/{h}/activities", json={"activity_type": "survey", "purpose": "p", "target": "t",
                                                                "decision_criteria": "c"})
    assert r.status_code == 422
    aid = client.post(f"/api/opportunities/{h}/activities", json={"activity_type": "interview", "purpose": "p",
                                                                  "target": "t", "decision_criteria": "c"}).json()["id"]
    assert client.post(f"/api/activities/{aid}/start").status_code == 200
    assert client.post(f"/api/activities/{aid}/start").status_code == 409
    assert client.post(f"/api/activities/{aid}/revisions", json={"purpose": "p", "target": "t", "decision_criteria": "c2",
                                                                 "change_reason": "r"}).status_code == 409
    r = client.post(f"/api/opportunities/{h}/assessments", json={"aspect": "severity", "state": "supported",
                                                                 "basis": "assumption", "assumption_note": "x"})
    assert r.status_code == 422
    assert client.post(f"/api/opportunities/{h}/select", json={"reason": "調査する"}).status_code == 200
    assert client.post(f"/api/opportunities/{h}/select", json={"reason": "重複"}).status_code == 409
    assert client.post(f"/api/opportunities/{h}/unselect", json={"reason": "外す"}).json()["selection"]["selected"] is False
    assert client.get("/api/opportunity-aspects").json()["aspects"][3]["key"] == "willingness_to_pay"


def test_personal_info_in_plans_is_rejected(env):
    h = hid("p05.finance")
    with pytest.raises(InvalidRequest, match="個人情報"):
        plan(h, target="山田商店 090-1234-5678")


def test_foreign_origin_cannot_write(env):
    h = hid("p05.finance")
    with TestClient(main.app, base_url="http://localhost") as c:
        r = c.post(f"/api/opportunities/{h}/select", json={"reason": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
    assert rows("SELECT COUNT(*) FROM opportunity_selections") == [(0,)]
