"""仮説ルールの網羅性・整合性のテスト（実データの業種・課題コードと照合する）。"""

from collections import Counter
from pathlib import Path

from app.estat_import import PROBLEMS, parse_problem_table, read_xlsx_rows
from app.hypothesis_rules import COMMON_NOT_IMPLIED, DOMAINS, NO_HYPOTHESES, RULES

REAL_XLSX = Path(__file__).parent / "data" / "estat_kojinke_2025_t14.xlsx"
PROBLEM_CODES = {c for c, _ in PROBLEMS if c != "00"}


def test_every_problem_has_rules_or_an_explicit_reason():
    with_rules = {r.problem_code for r in RULES}
    without = {n.problem_code for n in NO_HYPOTHESES}
    assert with_rules | without == PROBLEM_CODES
    assert not with_rules & without  # 両方に入る課題はない
    assert all(n.reason.strip() for n in NO_HYPOTHESES)


def test_thirteen_rules_across_five_domains_without_padding():
    assert len(RULES) == 13
    assert len({r.key for r in RULES}) == 13
    counts = Counter(r.domain for r in RULES)
    assert set(counts) == set(DOMAINS)
    assert counts == {"marketing": 2, "sales": 3, "operations": 2, "finance": 3, "customer_success": 3}


def test_one_problem_can_lead_to_multiple_domains():
    domains_for_02 = {r.domain for r in RULES if r.problem_code == "02"}
    assert domains_for_02 == {"marketing", "sales", "customer_success"}


def test_rules_have_required_fields_and_do_not_assert_demand():
    for r in RULES:
        assert r.assumptions and all(a.strip() for a in r.assumptions), r.key
        assert r.unknowns and all(u.strip() for u in r.unknowns), r.key
        assert r.next_action.strip() and r.target_customer.strip() and r.solution_idea.strip(), r.key
        assert r.not_implied.startswith(COMMON_NOT_IMPLIED), r.key
        assert "（推測）" in r.problem_statement, r.key  # 観測ではなく推測であることを明記
        assert "個人" in r.target_customer, r.key  # 個人企業経済調査の範囲を超えて広げない


def test_basis_industries_exist_in_real_statistics():
    table = parse_problem_table(read_xlsx_rows(REAL_XLSX))
    industries = {o.industry_code for o in table.observations}
    for r in RULES:
        assert r.basis_industries, r.key
        assert set(r.basis_industries) <= industries, r.key
