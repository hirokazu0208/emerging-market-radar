"""SaaS需要仮説の生成・保護・状態管理・検証条件・根拠の保持・API のテスト。

出発点の統計には、実データ（tests/data の e-Stat 公式 xlsx）を取り込んで使う。
"""

import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, hypotheses as hyp, hypothesis_rules, main
from app.estat_import import import_file
from app.hypotheses import Conflict, InvalidRequest, NoStatistics
from app.sources import SOURCES
from tests.test_estat_import import P, REAL_XLSX, SOURCE_URL, TABLE_REF, find_row, mutate, write_xlsx

TODAY = date.today().isoformat()


@pytest.fixture
def stats_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "test.db"))
    db.init_db(SOURCES)
    import_file(REAL_XLSX, TABLE_REF, SOURCE_URL)
    return tmp_path


@pytest.fixture
def generated(stats_db):
    return hyp.generate()


def hid(rule_key):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM hypotheses WHERE rule_key = ?", (rule_key,)).fetchone()[0]


def table_rows(sql, params=()):
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(sql, params)]


def interview(h, contact, result="supports", kind="interview", **extra):
    return hyp.add_evidence(h, {"kind": kind, "contact_ref": contact, "result": result, "occurred_on": TODAY,
                                "description": "予約の電話対応に毎日1時間かかっていると回答", **extra})


# --- 生成と重複防止 ---------------------------------------------------------------

def test_generate_creates_13_unverified_hypotheses_with_basis(generated):
    assert len(generated.created) == 13 and generated.basis_added == 27 and generated.missing_basis == []
    rows = table_rows("SELECT domain, status, origin, human_modified FROM hypotheses")
    assert len(rows) == 13 and {r[1] for r in rows} == {"unverified"} and {r[2] for r in rows} == {"rule"}
    assert {r[3] for r in rows} == {0}
    basis = table_rows(
        "SELECT industry_code, snapshot_value, snapshot_total, source_period, rule_version, kind, role, added_by "
        "FROM hypothesis_evidence WHERE hypothesis_id = ? ORDER BY industry_code", (hid("p09.finance.food"),))
    assert basis == [
        ("21", 34.0, 95.0, "2025", hypothesis_rules.HYPOTHESIS_RULESET_VERSION, "problem_observation", "basis", "rule"),
        ("42", 429.0, 911.0, "2025", hypothesis_rules.HYPOTHESIS_RULESET_VERSION, "problem_observation", "basis", "rule"),
    ]


def test_rerun_does_not_duplicate(generated):
    second = hyp.generate()
    assert (len(second.created), len(second.unchanged), second.basis_added) == (0, 13, 0)
    assert table_rows("SELECT COUNT(*) FROM hypotheses") == [(13,)]
    assert table_rows("SELECT COUNT(*) FROM hypothesis_evidence") == [(27,)]


def test_dry_run_writes_nothing(stats_db):
    result = hyp.generate(dry_run=True)
    assert len(result.created) == 13
    assert table_rows("SELECT COUNT(*) FROM hypotheses") == [(0,)]


def test_generate_without_statistics_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "empty.db"))
    with pytest.raises(NoStatistics):
        hyp.generate()
    from app import hypothesize
    with pytest.raises(SystemExit):
        hypothesize.main([])
    assert "統計表がありません" in capsys.readouterr().err


def test_generate_does_not_modify_existing_tables(stats_db):
    tables = ("articles", "article_markets", "article_labels", "stat_tables", "problem_observations")
    before = {t: table_rows(f"SELECT * FROM {t}") for t in tables}
    hyp.generate()
    hyp.generate()
    assert {t: table_rows(f"SELECT * FROM {t}") for t in tables} == before


# --- ルール変更時の保護 -------------------------------------------------------------

def bump_rules(monkeypatch, version="hyp-test.2"):
    rules = tuple(replace(r, title=r.title + "（改）") for r in hypothesis_rules.RULES)
    monkeypatch.setattr(hyp, "RULES", rules)
    monkeypatch.setattr(hyp, "HYPOTHESIS_RULESET_VERSION", version)


def test_rule_change_updates_only_untouched_hypotheses(generated, monkeypatch):
    edited, working = hid("p05.finance"), hid("p06.operations")
    hyp.update_hypothesis(edited, {"title": "人が書き直したタイトル"})
    hyp.set_status(working, "in_progress", None)
    bump_rules(monkeypatch)
    result = hyp.generate()
    assert set(result.skipped) == {"p05.finance", "p06.operations"} and len(result.updated) == 11
    assert table_rows("SELECT title, rule_version FROM hypotheses WHERE id = ?", (edited,)) == [
        ("人が書き直したタイトル", "hyp-2026.10.1")]
    assert not table_rows("SELECT title FROM hypotheses WHERE id = ?", (working,))[0][0].endswith("（改）")
    assert table_rows("SELECT title, rule_version FROM hypotheses WHERE rule_key = 'p09.finance.food'") == [
        ("原価の変化を価格に反映できていない（改）", "hyp-test.2")]
    skipped = hyp.get_hypothesis(edited)["events"]
    assert any(e["event"] == "rule_update_skipped" and e["detail"]["proposed"]["title"].endswith("（改）") for e in skipped)
    # 同じ版で再実行しても、見送りの記録は増えない
    hyp.generate()
    assert sum(e["event"] == "rule_update_skipped" for e in hyp.get_hypothesis(edited)["events"]) == 1


def test_retired_rule_is_kept_and_marked(generated, monkeypatch):
    monkeypatch.setattr(hyp, "RULES", tuple(r for r in hypothesis_rules.RULES if r.key != "p07.operations"))
    result = hyp.generate()
    assert result.retired == ["p07.operations"]
    assert table_rows("SELECT rule_retired FROM hypotheses WHERE rule_key = 'p07.operations'") == [(1,)]
    monkeypatch.setattr(hyp, "RULES", hypothesis_rules.RULES)
    hyp.generate()
    assert table_rows("SELECT rule_retired FROM hypotheses WHERE rule_key = 'p07.operations'") == [(0,)]


def test_manual_hypothesis_is_not_touched_by_rules(generated):
    data = {f: "手作業の内容" for f in hyp.TEXT_FIELDS} | {"domain": "marketing"}
    new = hyp.create_manual(data)
    hyp.generate()
    h = hyp.get_hypothesis(new)
    assert (h["origin"], h["status"], h["title"]) == ("manual", "unverified", "手作業の内容")


# --- 統計の更新と根拠の保持 ----------------------------------------------------------

def test_revised_statistics_keep_old_basis_and_add_new(generated, tmp_path, monkeypatch):
    from tests.test_estat_import import read_xlsx_rows
    rows = read_xlsx_rows(REAL_XLSX)
    edited = hid("p09.finance.food")
    hyp.update_hypothesis(edited, {"next_action": "人が書いた次の行動"})
    interview(edited, "C-001")
    i = find_row(rows, "42_飲食サービス業")
    revised = write_xlsx(tmp_path / "revised.xlsx", mutate(rows, i + 1, P + 9, "400"))  # 09 の値を 429 → 400
    assert import_file(revised, TABLE_REF, SOURCE_URL).status == "replaced"

    # 再実行前：古い根拠は残り、今の値との差が分かる
    old = next(e for e in hyp.get_hypothesis(edited)["evidence"] if e["industry_code"] == "42")
    assert (old["snapshot_value"], old["current"]["status"], old["current"]["value"]) == (429.0, "changed", 400.0)

    hyp.generate()
    h = hyp.get_hypothesis(edited)
    basis42 = [e for e in h["evidence"] if e["kind"] == "problem_observation" and e["industry_code"] == "42"]
    assert [e["snapshot_value"] for e in basis42] == [429.0, 400.0]  # 古い根拠と新しい根拠の両方
    assert basis42[0]["source_sha256"] != basis42[1]["source_sha256"]
    assert basis42[1]["current"]["status"] == "same"
    assert h["next_action"] == "人が書いた次の行動"  # 人の編集は上書きしない
    assert sum(1 for e in h["evidence"] if e["kind"] == "interview") == 1  # 手動の検証記録も残る


def test_newer_statistics_period_is_shown(generated, tmp_path):
    from tests.test_estat_import import read_xlsx_rows
    rows = mutate(read_xlsx_rows(REAL_XLSX), 1, 0, "2026年（令和8年） 個人企業経済調査")
    import_file(write_xlsx(tmp_path / "2026.xlsx", rows), "statInfId:000099999999", SOURCE_URL)
    h = hyp.get_hypothesis(hid("p05.finance"))
    old = next(e for e in h["evidence"] if e["kind"] == "problem_observation")
    assert old["current"]["newer_table"]["period"] == "2026"
    hyp.generate()
    periods = [e["source_period"] for e in hyp.get_hypothesis(hid("p05.finance"))["evidence"]]
    assert periods == ["2025", "2026"]


# --- 状態の遷移と「検証で支持」の条件 --------------------------------------------------

@pytest.fixture
def working(generated):
    h = hid("p06.operations")
    hyp.set_status(h, "in_progress", None)
    return h


def test_invalid_transitions_are_rejected(generated):
    h = hid("p05.finance")
    with pytest.raises(Conflict):
        hyp.set_status(h, "validated", "早すぎる")
    with pytest.raises(InvalidRequest):
        hyp.set_status(h, "rejected", None)  # 理由が必要
    hyp.set_status(h, "rejected", "対象顧客が掛け売りをほとんどしていなかった")
    with pytest.raises(Conflict):
        hyp.set_status(h, "in_progress", "再開")  # 棄却からは未検証にしか戻せない
    with pytest.raises(InvalidRequest):
        hyp.set_status(h, "unverified", "対象顧客が掛け売りをほとんどしていなかった")  # 前回と同じ理由
    hyp.set_status(h, "unverified", "別の業種で再検討する")
    with pytest.raises(InvalidRequest):
        hyp.set_status(h, "archived", "x")


def test_validated_requires_two_independent_contacts(working):
    with pytest.raises(Conflict, match="0/2"):
        hyp.set_status(working, "validated", "支持された")
    interview(working, "C-001")
    interview(working, "C-001")  # 同じ顧客への2回目の接点は、独立した2顧客として数えない
    interview(working, "C-002", result="refutes")  # 反証は数えない
    with pytest.raises(Conflict, match="1/2"):
        hyp.set_status(working, "validated", "支持された")
    interview(working, "LP-2026-10", kind="channel_test")
    hyp.set_status(working, "validated", "2つの接点で予約業務の負担が確認された")
    assert hyp.get_hypothesis(working)["status"] == "validated"


def test_validated_with_supporting_payment_intent(working):
    interview(working, "C-010", kind="payment_intent", payment_signal="preorder")
    hyp.set_status(working, "validated", "事前予約を1件得た")


def test_statistics_articles_and_notes_do_not_count(working):
    with db.connect() as conn:
        aid = conn.execute("INSERT INTO articles (source, external_id, title, url, published_at, category, fetched_at) "
                           "VALUES ('hackernews', 'x', 't', 'u', '2026-10-12T00:00:00+00:00', 'Other', 'f')").lastrowid
    hyp.add_evidence(working, {"kind": "article", "article_id": aid})
    hyp.add_evidence(working, {"kind": "note", "description": "知人から需要がありそうと聞いた"})
    hyp.add_evidence(working, {"kind": "problem_observation", "stat_table_id": 1, "industry_code": "51", "problem_code": "06"})
    with pytest.raises(Conflict):
        hyp.set_status(working, "validated", "根拠は集まった")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"kind": "interview", "contact_ref": "山田太郎", "result": "supports", "occurred_on": TODAY, "description": "x"}, "contact_ref"),
        ({"kind": "interview", "contact_ref": "a@b.com", "result": "supports", "occurred_on": TODAY, "description": "x"}, "contact_ref"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports", "occurred_on": TODAY,
          "description": "連絡先 taro@example.com"}, "個人情報"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports", "occurred_on": TODAY,
          "description": "電話 090-1234-5678 で確認"}, "個人情報"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports",
          "occurred_on": (date.today() + timedelta(days=1)).isoformat(), "description": "x"}, "未来"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports", "occurred_on": "2026/10/01", "description": "x"}, "YYYY-MM-DD"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "maybe", "occurred_on": TODAY, "description": "x"}, "result"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports", "occurred_on": TODAY}, "観察内容"),
        ({"kind": "payment_intent", "contact_ref": "C-1", "result": "supports", "occurred_on": TODAY, "description": "x"}, "payment_signal"),
        ({"kind": "interview", "contact_ref": "C-1", "result": "supports", "occurred_on": TODAY, "description": "x",
          "payment_signal": "paid"}, "payment_signal"),
        ({"kind": "note"}, "メモ"),
        ({"kind": "article", "article_id": 9999}, "記事"),
        ({"kind": "unknown"}, "kind"),
        ({"kind": "note", "description": "x", "url": "mailto:someone"}, "個人情報"),
    ],
)
def test_invalid_evidence_is_rejected(working, data, message):
    with pytest.raises(InvalidRequest, match=message):
        hyp.add_evidence(working, data)


def test_retraction_rules(working):
    a = interview(working, "C-001")
    interview(working, "C-002")
    hyp.set_status(working, "validated", "2人の事業主が負担を認めた")
    with pytest.raises(Conflict, match="検証中"):
        hyp.retract_evidence(working, a, "記録の誤り")  # 検証で支持の間は取り消せない
    hyp.set_status(working, "in_progress", "記録を見直すため")
    hyp.retract_evidence(working, a, "別の仮説の記録だった")
    with pytest.raises(Conflict, match="1/2"):
        hyp.set_status(working, "validated", "再確認")  # 取り消した記録は数えない
    basis = next(e["id"] for e in hyp.get_hypothesis(working)["evidence"] if e["added_by"] == "rule")
    with pytest.raises(Conflict, match="ルール"):
        hyp.retract_evidence(working, basis, "不要")
    with pytest.raises(Conflict, match="すでに"):
        hyp.retract_evidence(working, a, "二重")


# --- DB 側の強制（API を通さない操作も拒否する） ------------------------------------------

def raw(sql, params=()):
    with db.connect() as conn:
        conn.execute(sql, params)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE hypotheses SET status = 'validated', status_reason = 'x' WHERE rule_key = 'p06.operations'",
        "UPDATE hypotheses SET status = 'rejected' WHERE rule_key = 'p05.finance'",  # 理由なし
        "UPDATE hypothesis_evidence SET snapshot_value = 1",
        "DELETE FROM hypothesis_evidence",
        "DELETE FROM hypotheses",
        "UPDATE hypothesis_events SET event = 'edited'",
        "INSERT INTO hypotheses (domain, title, target_customer, problem_statement, solution_idea, assumptions, unknowns,"
        " not_implied, next_action, status, status_reason, origin, created_at, updated_at)"
        " VALUES ('sales','t','c','p','s','a','u','n','x','validated','r','manual','c','u')",
        "INSERT INTO hypotheses (domain, title, target_customer, problem_statement, solution_idea, assumptions, unknowns,"
        " not_implied, next_action, origin, created_at, updated_at)"
        " VALUES ('sales','t','c','p','s','a','  ','n','x','manual','c','u')",  # 未確認事項が空
    ],
)
def test_db_rejects_invalid_operations(working, sql):
    with pytest.raises(sqlite3.IntegrityError):
        raw(sql)


def test_db_validation_trigger_counts_distinct_contacts(working):
    interview(working, "C-001")
    interview(working, "C-001")
    with pytest.raises(sqlite3.IntegrityError):
        raw("UPDATE hypotheses SET status = 'validated', status_reason = 'r' WHERE id = ?", (working,))
    interview(working, "C-002")
    raw("UPDATE hypotheses SET status = 'validated', status_reason = 'r' WHERE id = ?", (working,))


@pytest.mark.parametrize("contact", ["山田", "a@b.com", "C 001", "x" * 33])
def test_db_rejects_identifying_contact_refs(working, contact):
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO hypothesis_evidence (hypothesis_id, kind, role, added_by, contact_ref, result, occurred_on,"
            " description, created_at) VALUES (?, 'interview', 'validation', 'manual', ?, 'supports', '2026-10-01', 'd', 'c')",
            (working, contact))


def test_db_rejects_retraction_of_validated_evidence(working):
    a = interview(working, "C-001")
    interview(working, "C-002")
    hyp.set_status(working, "validated", "支持")
    with pytest.raises(sqlite3.IntegrityError):
        raw("INSERT INTO hypothesis_evidence_retractions (evidence_id, reason, created_at) VALUES (?, 'r', 'c')", (a,))


# --- API ---------------------------------------------------------------------------

@pytest.fixture
def client(generated):
    with TestClient(main.app) as c:
        yield c


def test_list_api_filters_and_domain_counts(client):
    data = client.get("/api/hypotheses").json()
    assert len(data["hypotheses"]) == 13
    assert data["domain_counts"] == {"marketing": 2, "sales": 3, "operations": 2, "finance": 3, "customer_success": 3}
    assert "需要の存在や収益化を証明するものではありません" in data["validated_note"]
    p12 = client.get("/api/hypotheses", params={"problem": "12"}).json()
    assert sorted(h["rule_key"] for h in p12["hypotheses"]) == ["p12.cs", "p12.finance", "p12.sales"]
    assert p12["domain_counts"] == {"marketing": 0, "sales": 1, "operations": 0, "finance": 1, "customer_success": 1}
    sales = client.get("/api/hypotheses", params={"domain": "sales", "problem": "12"}).json()
    assert [h["rule_key"] for h in sales["hypotheses"]] == ["p12.sales"]
    assert sales["domain_counts"]["finance"] == 1  # 領域の件数は、領域以外の条件で数える
    food = client.get("/api/hypotheses", params={"industry": "42"}).json()
    assert sorted(h["rule_key"] for h in food["hypotheses"]) == ["p02.cs", "p06.operations", "p09.finance.food"]


@pytest.mark.parametrize("params", [{"domain": "hr"}, {"status": "done"}, {"origin": "ai"}])
def test_list_api_rejects_invalid_filters(client, params):
    assert client.get("/api/hypotheses", params=params).status_code == 422


def test_detail_edit_status_and_evidence_api(client):
    h = hid("p06.operations")
    assert client.get("/api/hypotheses/99999").status_code == 404
    detail = client.get(f"/api/hypotheses/{h}").json()
    assert detail["allowed_transitions"] == ["in_progress", "rejected"] and detail["status"] == "unverified"
    assert len([e for e in detail["evidence"] if e["kind"] == "problem_observation"]) == 2

    r = client.patch(f"/api/hypotheses/{h}", json={"next_action": "3人にヒアリングする"})
    assert r.status_code == 200 and r.json()["human_modified"] is True
    assert client.patch(f"/api/hypotheses/{h}", json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/hypotheses/{h}", json={"domain": "hr"}).status_code == 422

    assert client.post(f"/api/hypotheses/{h}/status", json={"status": "validated", "reason": "x"}).status_code == 409
    assert client.post(f"/api/hypotheses/{h}/status", json={"status": "in_progress"}).status_code == 200
    ev = {"kind": "interview", "contact_ref": "C-001", "result": "supports", "occurred_on": TODAY, "description": "負担あり"}
    assert client.post(f"/api/hypotheses/{h}/evidence", json=ev).status_code == 201
    assert client.post(f"/api/hypotheses/{h}/evidence", json=ev | {"contact_ref": "佐藤"}).status_code == 422
    r = client.post(f"/api/hypotheses/{h}/status", json={"status": "validated", "reason": "1人だけ"})
    assert r.status_code == 409 and "1/2" in r.json()["detail"]
    eid = client.post(f"/api/hypotheses/{h}/evidence", json=ev | {"contact_ref": "C-002"}).json()["id"]
    r = client.post(f"/api/hypotheses/{h}/status", json={"status": "validated", "reason": "2人が負担を認めた"})
    assert r.status_code == 200 and r.json()["validation"]["requirements_met"] is True
    assert client.post(f"/api/hypotheses/{h}/evidence/{eid}/retract", json={"reason": "誤り"}).status_code == 409


def test_create_manual_api(client):
    body = {f: "内容" for f in hyp.TEXT_FIELDS} | {"domain": "customer_success"}
    r = client.post("/api/hypotheses", json=body)
    assert r.status_code == 201
    h = client.get(f"/api/hypotheses/{r.json()['id']}").json()
    assert (h["origin"], h["status"], h["rule_key"]) == ("manual", "unverified", None)
    assert client.post("/api/hypotheses", json=body | {"unknowns": ""}).status_code == 422


def test_rules_api(client):
    data = client.get("/api/hypothesis-rules").json()
    assert data["version"] == hypothesis_rules.HYPOTHESIS_RULESET_VERSION
    assert len(data["rules"]) == 13
    assert [n["problem_code"] for n in data["no_hypothesis"]] == ["04", "08", "11"]


# --- マイグレーション v5 → v6 ---------------------------------------------------------

def test_v5_db_migrates_to_v6_preserving_data(stats_db):
    path = stats_db / "test.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 5")
    # v6 以降のテーブル・ビューを削除して v5 の状態を再現する
    conn.execute("DROP VIEW current_opportunity_selections")
    for t in ("opportunity_selections", "assessment_refs", "assessment_ref_policies", "opportunity_assessments",
              "activity_result_evidence", "validation_activity_sources", "validation_activity_results",
              "validation_activity_revisions", "validation_activities",
              "hypothesis_events", "hypothesis_evidence_retractions", "hypothesis_evidence", "hypotheses"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    tables = ("articles", "article_markets", "article_labels", "stat_tables", "problem_observations")
    before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    conn.close()
    db.init_db(SOURCES)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables} == before
    conn.close()


# --- 最終レビューで追加したテスト -------------------------------------------------

def test_status_round_trip_still_protects_from_rule_overwrite(generated, monkeypatch):
    h = hid("p07.operations")
    hyp.set_status(h, "in_progress", None)
    hyp.set_status(h, "unverified", None)  # 状態を戻しても「人が操作した」記録は残る
    bump_rules(monkeypatch)
    result = hyp.generate()
    assert "p07.operations" in result.skipped
    assert not table_rows("SELECT title FROM hypotheses WHERE id = ?", (h,))[0][0].endswith("（改）")


def test_adding_evidence_protects_from_rule_overwrite(generated, monkeypatch):
    h = hid("p03.cs")
    hyp.add_evidence(h, {"kind": "note", "description": "店頭で要望カードを試す予定"})
    bump_rules(monkeypatch)
    assert "p03.cs" in hyp.generate().skipped
    assert table_rows("SELECT human_modified FROM hypotheses WHERE id = ?", (h,)) == [(1,)]


def test_contact_ref_case_is_normalized(working):
    interview(working, "c-001")
    interview(working, "C-001")
    assert hyp.get_hypothesis(working)["validation"]["independent_supporting_contacts"] == 1
    assert {e["contact_ref"] for e in hyp.get_hypothesis(working)["evidence"] if e["contact_ref"]} == {"C-001"}


def test_text_length_and_personal_info_in_hypothesis_text(generated):
    h = hid("p05.finance")
    with pytest.raises(InvalidRequest, match="5000文字"):
        hyp.update_hypothesis(h, {"next_action": "あ" * 5001})
    with pytest.raises(InvalidRequest, match="個人情報"):
        hyp.update_hypothesis(h, {"target_customer": "山田商店（03-1234-5678）"})
    with pytest.raises(InvalidRequest, match="5000文字"):
        hyp.add_evidence(h, {"kind": "note", "description": "x" * 5001})


def test_api_rejects_foreign_host_and_origin(generated):
    h = hid("p05.finance")
    with TestClient(main.app, base_url="http://localhost") as c:
        assert c.get("/api/hypotheses", headers={"Host": "attacker.example"}).status_code == 400  # DNS リバインディング対策
        r = c.post(f"/api/hypotheses/{h}/status", json={"status": "in_progress"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert table_rows("SELECT status FROM hypotheses WHERE id = ?", (h,)) == [("unverified",)]
        r = c.post(f"/api/hypotheses/{h}/status", json={"status": "in_progress"}, headers={"Origin": "http://localhost:3001"})
        assert r.status_code == 200
        assert c.post("/api/fetch", headers={"Origin": "http://evil.example"}).status_code == 403
        assert c.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 200  # 読み取りは Host で制限


def test_cors_preflight_allows_patch_only_from_local_origin(client):
    ok = client.options("/api/hypotheses/1", headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "PATCH"})
    assert ok.status_code == 200 and "PATCH" in ok.headers["access-control-allow-methods"]
    ng = client.options("/api/hypotheses/1", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PATCH"})
    assert "access-control-allow-origin" not in ng.headers
