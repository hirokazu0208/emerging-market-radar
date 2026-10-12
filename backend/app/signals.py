"""キーワード・ルールによる市場形成シグナル（signal_type）の抽出。

これは第1層「市場形成シグナル」の候補抽出であり、市場の成長やソフトウェア需要を判定するものではない。

strength の意味
- explicit：記事のタイトル・要約に、出来事を述べる明示的な記述がある（独立した事実確認ではない）
- inferred：関連語・投稿形式などからルールで推測した候補

誤判定を減らすための処理
- 否定・中止表現（not / cancels / 中止 / 解消 など）が一致箇所の近くにあれば、ラベルを付けない
- 検討・提案・予定（plans to / call for / 検討 など）が近くにあれば inferred に下げる
- 質問・解説・回顧の記事（? / How / Why / とは / 解説 など）は inferred に下げる
"""

import re
from dataclasses import dataclass, field
from typing import Literal

# ルールを変更したら更新する。article_labels.rule_version に保存される
RULESET_VERSION = "kw-2026.10.1"

Strength = Literal["explicit", "inferred"]

EGOV_NOTE = "改正案等の意見募集であり、成立・施行済みではありません"


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    strength: Strength
    pattern: re.Pattern[str]
    exclude: re.Pattern[str] | None = None  # 一致箇所の周辺にあれば無効とする表現
    requires: re.Pattern[str] | None = None  # タイトル全体に必要な追加条件
    title_only: bool = False
    tentative_check: bool = True  # 検討・予定などで inferred に下げるか
    note: str | None = None  # 根拠に添える注記


def _en(p: str) -> re.Pattern[str]:
    """英語：単語境界つき・大文字小文字を区別しない。"""
    return re.compile(rf"(?<![a-z0-9])(?:{p})(?![a-z0-9])", re.IGNORECASE)


def _ja(p: str) -> re.Pattern[str]:
    return re.compile(p)


_REGIONS = (r"japan|the us|the u\.s\.|us|europe|the eu|eu|the uk|uk|india|china|asia|africa|"
            r"latin america|canada|australia|korea|south korea|germany|france|brazil|mexico")
_SEGMENTS = (r"small businesses|small business|smbs?|smes?|freelancers|creators|seniors|older adults|"
             r"the elderly|students|teachers|clinics|hospitals|restaurants|retailers|farmers|lawyers|"
             r"accountants|nonprofits|enterprises|startups|families|parents|kids")
_JA_SEGMENTS = (r"中小企業|個人事業主|フリーランス|高齢者|シニア|学生|女性|子育て世帯|自治体|医療機関|"
                r"介護事業者|飲食店|小売店|農家|訪日客|インバウンド|法人")

# Show HN のうち、製品・サービスの公開と読み取れるもの（投稿形式だけでは判定しない）
_SHOW_HN_PRODUCT = re.compile(
    r"^show hn:\s*(?:"
    r".*(?<![a-z])(?:i|we)\s+(?:built|made|created|launched|released|wrote)\b"
    r"|.*(?<![a-z])(?:open[- ]source|app|tool|extension|plugin|library|framework|platform|service|api|saas|"
    r"cli|editor|engine|server|bot|assistant|dashboard|generator|tracker|manager)(?![a-z])"
    r"|\S+(?:\s\S+)?\s*[–—:]\s|\S+(?:\s\S+)?\s+-\s"
    r")",
    re.IGNORECASE,
)

RULES: tuple[Rule, ...] = (
    # --- new_service ---
    Rule("new_service.en.launch", "new_service", "explicit",
         _en(r"launch(?:es|ed)?|unveil(?:s|ed)?|introduc(?:es|ed)|debut(?:s|ed)|rolls? out|rolled out|"
             r"now available|goes live|went live|generally available|general availability|open[- ]sources(?:d)?"),
         exclude=_en(r"rocket|satellite|missile|spacecraft|launch (?:pad|site|window|vehicle)")),
    Rule("new_service.en.show_hn", "new_service", "inferred",
         re.compile(r"^show hn:", re.IGNORECASE), requires=_SHOW_HN_PRODUCT, title_only=True),
    Rule("new_service.en.new_product", "new_service", "inferred",
         _en(r"new (?:service|product|app|platform|tool)")),
    Rule("new_service.ja.launch", "new_service", "explicit",
         _ja(r"(?:提供|販売|配信|受付|運用|サービス)を?開始|発売|リリース|ローンチ")),
    Rule("new_service.ja.new_product", "new_service", "inferred", _ja(r"新サービス|新製品|新商品|新機能")),

    # --- market_entry ---
    Rule("market_entry.en.enter", "market_entry", "explicit",
         _en(rf"enter(?:s|ed|ing)? (?:the )?(?:[\w'-]+ ){{0,3}}market|entry into|"
             rf"launch(?:es|ed)? in (?:{_REGIONS})|expands? into (?:{_REGIONS})")),
    Rule("market_entry.en.entrant", "market_entry", "inferred", _en(r"market entry|new entrants?")),
    Rule("market_entry.ja.enter", "market_entry", "explicit",
         _ja(r"(?:市場|業界|分野|事業)(?:へ|に)の?(?:新規)?参入|新規参入|参入(?:する|します|しました|を発表|を決定)|"
             r"(?:日本|国内|海外|米国|欧州|アジア)(?:市場)?(?:へ|に)の?(?:進出|参入)|"
             r"進出(?:する|します|しました|を発表|を決定)")),
    Rule("market_entry.ja.mention", "market_entry", "inferred", _ja(r"参入(?!障壁)")),

    # --- partnership ---
    Rule("partnership.en.partner", "partnership", "explicit",
         _en(r"partners? with|partnered with|teams? up with|teamed up with|joins? forces with|joined forces with|"
             r"strategic partnership|signs? (?:a |an )?(?:deal|agreement|mou) with|signed (?:a |an )?(?:deal|agreement|mou) with|"
             r"collaborat(?:es|ed) with")),
    Rule("partnership.en.mention", "partnership", "inferred", _en(r"partnerships?|collaboration")),
    Rule("partnership.ja.partner", "partnership", "explicit",
         _ja(r"(?:資本業務|業務|資本)提携|提携(?:を締結|を発表|しました|します|を開始)|"
             r"協業(?:を開始|を発表|します|しました)|連携(?:を開始|協定|を発表)|パートナーシップ(?:を締結|契約)")),
    Rule("partnership.ja.mention", "partnership", "inferred", _ja(r"提携|協業")),

    # --- funding ---
    Rule("funding.en.raise", "funding", "explicit",
         _en(r"rais(?:es|ed|ing) (?:a |an )?\$?\d[\d.,]*\s?(?:k|m|mn|million|b|bn|billion)|"
             r"\$\d[\d.,]*\s?(?:m|mn|million|b|bn|billion) (?:seed|series [a-f]|round|funding)|"
             r"(?:pre-seed|seed|series [a-f]) (?:round|funding|financing)|"
             r"secures? \$?\d[\d.,]*\s?(?:m|mn|million|b|bn|billion)|funding round")),
    Rule("funding.en.mention", "funding", "inferred",
         _en(r"funding|investors?|venture capital|vc-backed|backed by|raise (?:money|capital)")),
    Rule("funding.ja.raise", "funding", "explicit",
         _ja(r"資金調達(?:を実施|を完了|しました|を発表|を行|に成功)|[\d０-９,，.]+億円(?:の|を)?(?:資金)?調達|"
             r"第三者割当増資|シリーズ[A-FＡ-Ｆ]|(?:プレ)?シードラウンド|J-KISS")),
    Rule("funding.ja.mention", "funding", "inferred", _ja(r"資金調達|出資|投資家|ベンチャーキャピタル")),

    # --- expansion ---
    Rule("expansion.en.expand", "expansion", "explicit",
         _en(r"expand(?:s|ed|ing)? (?:into|to|its|their|operations|production|capacity|service|coverage|presence)|"
             r"expansion (?:into|to|of)|"
             r"opens? (?:a |its )?(?:new |first )?(?:office|factory|plant|stores?|data ?cent(?:er|re)s?|hub|headquarters|campus)|"
             r"doubl(?:es|ed) (?:its )?(?:capacity|production|workforce|headcount)|scal(?:es|ed) up|hir(?:es|ed|ing) \d+")),
    Rule("expansion.en.mention", "expansion", "inferred", _en(r"expand(?:s|ed|ing)?|expansion")),
    Rule("expansion.ja.expand", "expansion", "explicit",
         _ja(r"事業拡大|規模拡大|販路拡大|増設|新拠点|拠点を?(?:開設|新設)|海外展開|全国展開|出店|増産|増員|新工場")),
    Rule("expansion.ja.mention", "expansion", "inferred", _ja(r"(?<!感染)拡大")),

    # --- new_segment ---
    Rule("new_segment.en.target", "new_segment", "explicit",
         _en(rf"(?:targets?|targeting|aimed at|designed for|built for|tailored for) (?:{_SEGMENTS})")),
    Rule("new_segment.en.for", "new_segment", "inferred",
         _en(rf"for (?:{_SEGMENTS})|new (?:customer|user) segments?|underserved")),
    Rule("new_segment.ja.segment", "new_segment", "explicit",
         _ja(r"(?:新たな|新規|新しい)顧客層|新市場を?開拓|顧客層を?拡大|ターゲットを?拡大")),
    Rule("new_segment.ja.for", "new_segment", "inferred", _ja(rf"(?:{_JA_SEGMENTS})向け")),

    # --- regulation ---
    Rule("regulation.ja.public_comment", "regulation", "explicit",
         _ja(r"(?:御?意見)(?:・情報)?の?(?:募集|公募)|意見募集|意見公募|パブリックコメント"),
         tentative_check=False, note=EGOV_NOTE),
    Rule("regulation.ja.amendment", "regulation", "explicit",
         _ja(r"(?:改正|制定|廃止|変更)(?:する|案)|(?:省令|政令|告示)(?:案|（案）)"),
         tentative_check=False, note=EGOV_NOTE),
    Rule("regulation.ja.law", "regulation", "explicit",
         _ja(r"法改正|規制(?:を)?(?:強化|緩和|導入|開始)|施行|義務化|新法")),
    Rule("regulation.ja.mention", "regulation", "inferred", _ja(r"規制")),
    Rule("regulation.en.action", "regulation", "explicit",
         _en(r"bans?|banned|barred from|outlaws?|outlawed|signed into law|pass(?:es|ed)? (?:a |the )?(?:bill|law)|"
             r"(?:law|act|rule|regulation|ban) takes effect|court rules|rules that|ruling|mandates?|mandated|"
             r"eu ai act|gdpr|antitrust")),
    Rule("regulation.en.mention", "regulation", "inferred", _en(r"regulators?|regulations?|regulatory")),
)

# 否定・中止（一致箇所の直前・直後）
_EN_NEG_BEFORE = re.compile(
    r"(?<![a-z])(?:not|no|never|won't|wont|didn't|doesn't|don't|isn't|aren't|fails? to|failed to|unable to|"
    r"denies|denied|cancels?|cancell?ed|delays?|delayed|halts?|halted|scraps?|scrapped|abandons?|abandoned|"
    r"ends?|ended|drops?|dropped|pulls? out of|exits?|exited|withdraws?|withdrew)\s+(?:\S+\s+){0,3}$",
    re.IGNORECASE,
)
_EN_NEG_AFTER = re.compile(
    r"^\W*(?:\S+\s+){0,2}(?:delayed|cancell?ed|scrapped|postponed|halted|called off|abandoned|denied|ended)(?![a-z])",
    re.IGNORECASE,
)
_JA_NEG_AFTER = re.compile(r"^.{0,12}?(?:しない|しません|せず|できず|できない|中止|延期|撤回|見送|終了|解消|断念|否定|白紙|停止)")
_JA_NEG_BEFORE = re.compile(r"(?:未|非|不)$")

# 検討・提案・予定（inferred に下げる）
_EN_TENTATIVE = re.compile(
    r"(?<![a-z])(?:calls? for|propos\w*|plans? to|planning to|considers?|considering|may|might|could|would|"
    r"reportedly|rumou?r\w*|seeks? to|urges?|to vote on|votes? on|in talks|weighs?|eyes|eyeing)\s+(?:\S+\s+){0,3}$"
    r"|(?<![a-z])(?:for|to fund|to support)\s+(?:its |an |a |the |further )?$",  # 目的（for expansion など）
    re.IGNORECASE,
)
_JA_TENTATIVE = re.compile(r"検討|予定|見込み|可能性|方針|計画|噂|視野")

# 質問・解説・回顧の記事（inferred に下げる）
_EN_META = re.compile(
    r"^(?:how|why|what|when|where|who|should|is|are|can|could|do|does|did|will)(?![a-z])|ask hn|history of|"
    r"lessons from|what we learned|a look at|the story of|post-?mortem|explained|explainer|guide to|"
    r"in defense of|thoughts on",
    re.IGNORECASE,
)
_JA_META = re.compile(r"とは(?:何|[？?]|$)|解説|(?<!取り)まとめ|事例(?:紹介|集)|考察|振り返|の歴史|入門")

_CLAUSE_END = re.compile(r"[。！？!?\n]|\.(?=\s|$)")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


@dataclass
class Signal:
    label: str
    strength: Strength
    evidence: list[str] = field(default_factory=list)


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    """一致箇所を含む文（節）の範囲。"""
    left = 0
    for m in _CLAUSE_END.finditer(text, 0, start):
        left = m.end()
    m = _CLAUSE_END.search(text, end)
    return left, (m.start() if m else len(text))


def _is_negated(text: str, start: int, end: int, c_start: int, c_end: int) -> bool:
    before, after = text[c_start:start], text[end:c_end]
    if _JAPANESE.search(text[start:end]):
        return bool(_JA_NEG_AFTER.search(after) or _JA_NEG_BEFORE.search(before))
    return bool(_EN_NEG_BEFORE.search(before) or _EN_NEG_AFTER.search(after))


def _is_tentative(text: str, start: int, end: int, c_start: int, c_end: int) -> bool:
    if _JAPANESE.search(text[start:end]):
        return bool(_JA_TENTATIVE.search(text[c_start:c_end]))
    return bool(_EN_TENTATIVE.search(text[c_start:start]))


def is_meta_title(title: str) -> bool:
    """質問・解説・回顧の記事か（出来事の報告ではなく、話題としての言及）。"""
    t = title.strip()
    return t.endswith(("?", "？")) or bool(_EN_META.search(t) or _JA_META.search(t))


def _snippet(text: str, start: int, end: int, width: int = 20) -> str:
    s, e = max(0, start - width), min(len(text), end + width)
    return f"{'…' if s > 0 else ''}{' '.join(text[s:e].split())}{'…' if e < len(text) else ''}"


def extract_signals(title: str, summary: str | None = None) -> list[Signal]:
    """タイトルと要約から signal_type の候補を抽出する。根拠が無ければ空リスト。"""
    meta = is_meta_title(title)
    fields = [("title", title)] + ([("summary", summary)] if summary else [])
    found: dict[str, Signal] = {}

    for rule in RULES:
        if rule.requires and not rule.requires.search(title):
            continue
        for field_name, text in fields:
            if rule.title_only and field_name != "title":
                continue
            for m in rule.pattern.finditer(text):
                c_start, c_end = _clause(text, m.start(), m.end())
                if _is_negated(text, m.start(), m.end(), c_start, c_end):
                    continue
                if rule.exclude and rule.exclude.search(text[max(c_start, m.start() - 20):min(c_end, m.end() + 20)]):
                    continue
                strength: Strength = rule.strength
                if strength == "explicit" and (
                    meta or (rule.tentative_check and _is_tentative(text, m.start(), m.end(), c_start, c_end))
                ):
                    strength = "inferred"

                evidence = f"{field_name}: {_snippet(text, m.start(), m.end())}（{rule.id}）"
                if rule.note:
                    evidence += f" ※{rule.note}"

                current = found.get(rule.label)
                if current is None or (current.strength == "inferred" and strength == "explicit"):
                    found[rule.label] = Signal(rule.label, strength, [evidence])
                elif current.strength == strength and len(current.evidence) < 2 and evidence not in current.evidence:
                    current.evidence.append(evidence)
                break  # 1つのルールにつき、各フィールドで最初の有効な一致だけを使う

    return sorted(found.values(), key=lambda s: s.label)
