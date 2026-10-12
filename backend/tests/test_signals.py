"""キーワード・ルールによる signal_type 抽出のテスト（日本語・英語の肯定例・否定例・誤判定例）。"""

import pytest

from app.signals import EGOV_NOTE, extract_signals, is_meta_title


def labels(title, summary=None):
    return {(s.label, s.strength) for s in extract_signals(title, summary)}


# --- 肯定例 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        # 英語
        ("Nvidia, Microsoft Launch Windows PCs with RTX Spark and AI Agents", {("new_service", "explicit")}),
        ("Acme raises $12M Series A to automate invoicing", {("funding", "explicit")}),
        ("Acme closes $3.5M seed round led by Example Ventures", {("funding", "explicit")}),
        ("Stripe partners with Shopify on instant payouts", {("partnership", "explicit")}),
        ("Uber enters the Japanese taxi market", {("market_entry", "explicit")}),
        ("Revolut launches in Japan", {("market_entry", "explicit"), ("new_service", "explicit")}),
        ("Figma opens new office in Tokyo", {("expansion", "explicit")}),
        ("New accounting tool designed for small businesses", {("new_segment", "explicit")}),
        ("EU passes the bill on AI liability", {("regulation", "explicit")}),
        ("Beijing residents dispose of drones ahead of ban on ownership", {("regulation", "explicit")}),
        # 日本語
        ("株式会社サンプル、クラウド請求書サービスの提供を開始", {("new_service", "explicit")}),
        ("サンプル社、シリーズAで5億円の資金調達を実施", {("funding", "explicit")}),
        ("A社とB社が業務提携を締結", {("partnership", "explicit")}),
        ("C社、介護市場へ新規参入", {("market_entry", "explicit")}),
        ("D社が九州に新拠点を開設、事業拡大へ", {("expansion", "explicit")}),
        ("E社、中小企業向けに新たな顧客層を開拓", {("new_segment", "explicit")}),
        ("個人事業主向けの会計アプリ", {("new_segment", "inferred")}),
    ],
)
def test_positive_examples(title, expected):
    assert labels(title) == expected


def test_egov_public_comment_is_explicit_regulation_with_note():
    # 実際の e-Gov 案件名。改正案の公示であり、成立・施行済みではないことを根拠に注記する
    signals = extract_signals(
        "「火薬類取締法施行規則の一部を改正する省令（案）」等に対する意見の募集について",
        "案の公示日：2026/10/08\n受付締切日時：2026/11/06 23:59\nカテゴリー：工業",
    )
    assert [(s.label, s.strength) for s in signals] == [("regulation", "explicit")]
    assert EGOV_NOTE in signals[0].evidence[0]
    assert signals[0].evidence[0].startswith("title: ")


def test_egov_title_with_rekishi_is_not_treated_as_retrospective():
    # 「歴史的風致」は回顧記事ではない（「歴史」だけで inferred に下げない）
    assert labels(
        "「地域における歴史的風致の維持及び向上に関する法律施行規則の一部を改正する省令案」に関する意見募集について"
    ) == {("regulation", "explicit")}


@pytest.mark.parametrize(
    "title",
    [
        "Show HN: SwapBackdrop – reuse one cutout across video backgrounds and frames",
        "Show HN: I made an agent can self-improving while working",
        "Show HN: Skills Hub, a desktop app for managing AI agent skills",
        "Show HN: Foretype: type on remote machines at local speed",
    ],
)
def test_show_hn_product_is_inferred_new_service(title):
    assert labels(title) == {("new_service", "inferred")}


@pytest.mark.parametrize(
    "title",
    ["Show HN: Writing a Game in C", "Show HN: If MIT OCW Was Like Cable TV"],
)
def test_show_hn_without_product_is_not_labeled(title):
    # 投稿形式（Show HN）だけでは新サービスと判定しない
    assert labels(title) == set()


def test_summary_is_used_as_evidence_source():
    signals = extract_signals("Acme update", "Today Acme partners with Globex to resell its API.")
    assert [(s.label, s.strength) for s in signals] == [("partnership", "explicit")]
    assert signals[0].evidence[0].startswith("summary: ")


# --- 否定例（ラベルを付けない） ----------------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Acme cancels launch of its new app",
        "Acme's launch delayed until 2027",
        "Acme fails to raise $10M for expansion into Europe",
        "Acme ends partnership with Globex",
        "Acme won't enter the Japanese market",
        "新サービスの提供開始を延期",
        "A社とB社、業務提携を解消",
        "資金調達できず事業を終了",
        "C社、介護市場への参入を中止",
    ],
)
def test_negated_events_are_not_labeled(title):
    found = labels(title)
    assert not any(strength == "explicit" for _, strength in found), found
    # 否定された出来事そのもののラベルは付かない
    assert not {"new_service", "funding", "partnership", "market_entry"} & {label for label, s in found if s == "explicit"}


@pytest.mark.parametrize(
    "title, label",
    [
        ("Acme cancels launch of its new app", "new_service"),
        ("A社とB社、業務提携を解消", "partnership"),
        ("C社、介護市場への参入を中止", "market_entry"),
        ("Acme ends partnership with Globex", "partnership"),
    ],
)
def test_negated_label_is_absent(title, label):
    assert label not in {lbl for lbl, _ in labels(title)}


# --- 誤判定例（単なる言及・別の意味の語） ----------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Murphy's law and software estimates",  # law だけでは規制にしない
        "Urban planning in Tokyo",  # ban を語の一部で誤検出しない
        "SpaceX rocket launch scrubbed due to weather",  # ロケットの打ち上げは新サービスではない
        "USGS Tsunami Warning System",
        "参入障壁の高い業界の特徴",  # 参入障壁は参入の出来事ではない
        "新型コロナの感染拡大が続く",  # 感染拡大は事業拡大ではない
        "Things that don't bother me as your manager",
    ],
)
def test_unrelated_mentions_are_not_labeled(title):
    assert labels(title) == set()


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Why startups raise $10M seed rounds too early?", {("funding", "inferred")}),  # 質問形
        ("How Stripe partners with banks", {("partnership", "inferred")}),  # 解説
        ("資金調達とは？シリーズAの基礎を解説", {("funding", "inferred")}),
        ("American Academy of Pediatrics renews call for ban on raw milk", {("regulation", "inferred")}),  # 提案段階
        ("Acme plans to launch a payments app", {("new_service", "inferred")}),  # 予定
        ("E社、海外市場への進出を検討", {("market_entry", "inferred")}),  # 検討
        ("Regulators look at AI pricing tools", {("regulation", "inferred")}),  # 関連語のみ
    ],
)
def test_mentions_and_tentative_statements_are_downgraded_to_inferred(title, expected):
    assert labels(title) == expected


def test_no_evidence_means_no_labels():
    assert extract_signals("The Ethiopian Economic Miracle", None) == []
    assert extract_signals("Fall To One (Math Song) [video]", "") == []


@pytest.mark.parametrize(
    "title, expected",
    [("Why is bun so disliked?", True), ("Ask HN: Who is hiring?", True), ("資金調達とは", True),
     ("A社が業務提携を締結", False), ("取りまとめ結果の公表について", False)],
)
def test_meta_title_detection(title, expected):
    assert is_meta_title(title) is expected


def test_evidence_contains_matched_text_and_rule_id():
    s = extract_signals("Acme raises $12M Series A to automate invoicing")[0]
    assert "raises $12M" in s.evidence[0]
    assert "（funding.en.raise）" in s.evidence[0]
