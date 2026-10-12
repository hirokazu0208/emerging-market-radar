"""統計上の経営課題から、SaaS需要仮説の「候補」を作るルール。

- ルールが作るのは、人が検証するための未検証の候補だけ。需要・支払意思・収益化を示すものではない。
- 1つの経営課題から複数の領域の仮説を作ってよい。ソフトウェアの仮説を作らない課題は理由を明示する。
- 対象の業種（出発点の観測）はルールで固定する。観測値の大きさで業種を選んだり、仮説を順位付けしたりしない。
- 文面を変更したら HYPOTHESIS_RULESET_VERSION を更新する。
"""

from dataclasses import dataclass

HYPOTHESIS_RULESET_VERSION = "hyp-2026.10.1"

DOMAINS: tuple[str, ...] = ("marketing", "sales", "operations", "finance", "customer_success")

COMMON_NOT_IMPLIED = (
    "出発点の統計は、個人企業の事業主が最も大きな問題点として1つだけ選んだ回答の割合です。"
    "ソフトウェアの必要性・支払意思・市場規模・収益化の可能性を示すものではありません。"
)


@dataclass(frozen=True)
class HypothesisRule:
    key: str
    problem_code: str
    domain: str
    title: str
    target_customer: str
    problem_statement: str
    solution_idea: str
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    next_action: str
    basis_industries: tuple[str, ...]  # 出発点として紐付ける業種（ルールで固定）
    not_implied_extra: str = ""

    @property
    def not_implied(self) -> str:
        return COMMON_NOT_IMPLIED + (f"\n{self.not_implied_extra}" if self.not_implied_extra else "")


@dataclass(frozen=True)
class NoHypothesis:
    problem_code: str
    reason: str


RETAIL_AND_LOCAL_SERVICES = ("32", "33", "5")  # 飲食料品小売業、衣料品・その他の小売業、生活関連サービス業，娯楽業
FOOD_AND_LOCAL_SERVICES = ("42", "51")  # 飲食サービス業、うち生活関連サービス業
B2B = ("2", "31")  # 製造業、卸売業

RULES: tuple[HypothesisRule, ...] = (
    # --- Marketing ---
    HypothesisRule(
        key="p01.marketing", problem_code="01", domain="marketing",
        title="価格以外の強みを伝える手段の不足",
        target_customer="個人経営の小売業・生活関連サービス業",
        problem_statement="大手・同業者との競争の中で、口コミや専門性など価格以外の強みを見込み客に伝える手段が乏しい（推測）。",
        solution_idea="既存顧客からの口コミ・事例を集め、店舗の紹介ページやSNSに載せる作業を簡単にするツール。",
        assumptions=("競争の主な軸が価格ではなく、伝え方で差別化できる余地がある", "事業主が口コミ・事例の発信に時間を割けていない"),
        unknowns=("競争の相手と軸（価格・立地・品揃え）", "既存の口コミサイトやSNSで足りているか"),
        next_action="対象業種の事業主に、競合と比べて選ばれている理由と、それを伝えている手段をヒアリングする。",
        basis_industries=RETAIL_AND_LOCAL_SERVICES,
        not_implied_extra="価格競争が主な原因の場合、このソフトウェアで解決できる範囲は小さい。",
    ),
    HypothesisRule(
        key="p02.marketing", problem_code="02", domain="marketing",
        title="新規顧客に知ってもらう手段の不足",
        target_customer="個人経営の小売業・生活関連サービス業",
        problem_statement="需要の停滞の一因として、新しい顧客に存在を知ってもらう手段が手作業の告知に限られている可能性がある（推測）。",
        solution_idea="地域の見込み客向けの告知（地図サービス・SNS・チラシ）を、少ない手間でまとめて更新できるツール。",
        assumptions=("需要の停滞の原因が、市場の縮小ではなく認知の不足である", "事業主が告知に使える時間が限られている"),
        unknowns=("需要の停滞の原因の内訳（市場縮小・競合・立地・認知不足）", "現在使っている集客手段と費用"),
        next_action="需要の停滞を感じている事業主に、新規顧客の来店経路と、試した集客手段・その結果をヒアリングする。",
        basis_industries=RETAIL_AND_LOCAL_SERVICES,
        not_implied_extra="「需要の停滞」の回答は、集客ソフトウェアの需要を示すものではない。",
    ),
    # --- Sales ---
    HypothesisRule(
        key="p02.sales.b2b", problem_code="02", domain="sales",
        title="新しい取引先の開拓手段の不足",
        target_customer="個人経営の製造業・卸売業（取引先が法人）",
        problem_statement="需要の停滞の中で、新しい取引先の開拓が紹介や既存の関係に限られている可能性がある（推測）。",
        solution_idea="見込み取引先の候補リストと、提案・問い合わせの記録を管理する小規模な営業管理ツール。",
        assumptions=("取引の中心が法人間である", "事業主自身が営業を担っており、開拓の時間と手段が限られている"),
        unknowns=("新しい取引先を増やす意向があるか", "既存の取引先への依存度"),
        next_action="対象業種の事業主に、直近1年の新規取引先の獲得経路と、開拓で困っていることをヒアリングする。",
        basis_industries=B2B,
    ),
    HypothesisRule(
        key="p10.sales", problem_code="10", domain="sales",
        title="値引要請に対する根拠資料の作成負担",
        target_customer="個人経営の製造業・卸売業",
        problem_statement="販売価格の低下・値引要請に対して、見積や価格の根拠を短時間で示せていない可能性がある（推測）。",
        solution_idea="原価・工数から見積の根拠を整理し、取引先に示す資料を作れるツール。",
        assumptions=("根拠を示せば、値引要請に対抗・交渉できる余地がある", "見積や根拠資料を手作業で作っている"),
        unknowns=("取引先との力関係（交渉の余地があるか）", "現在の見積作成の方法と所要時間"),
        next_action="値引要請を受けた経験のある事業主に、交渉の進め方と、根拠資料を作っているかをヒアリングする。",
        basis_industries=B2B,
    ),
    HypothesisRule(
        key="p12.sales", problem_code="12", domain="sales",
        title="価格改定を取引先に説明する資料の作成負担",
        target_customer="個人経営の製造業・卸売業",
        problem_statement="コストの増加を販売価格に転嫁する際、取引先への説明資料の作成に手間がかかっている可能性がある（推測）。",
        solution_idea="原材料・人件費の変化を示し、価格改定の説明資料を作成できるツール。",
        assumptions=("説明すれば価格改定が受け入れられる取引関係である", "説明資料の作成が転嫁の妨げになっている"),
        unknowns=("転嫁できない主な理由（取引上の力関係・競合・資料不足）", "取引先が求める説明の形式"),
        next_action="価格転嫁に取り組んだ事業主に、転嫁できた・できなかった理由と、説明に使った資料をヒアリングする。",
        basis_industries=B2B,
        not_implied_extra="取引上の力関係が主な理由の場合、このソフトウェアで解決できる範囲は小さい。",
    ),
    # --- Operations ---
    HypothesisRule(
        key="p06.operations", problem_code="06", domain="operations",
        title="少人数での予約・受付・シフト運営の負担",
        target_customer="個人経営の飲食サービス業・生活関連サービス業",
        problem_statement="従業員の確保が難しい中で、予約・受付・シフトの手作業が少人数での運営の負担になっている可能性がある（推測）。",
        solution_idea="予約受付とシフトの調整を一つにまとめ、電話や紙の作業を減らすツール。",
        assumptions=("人手不足の業務の一部が、予約・受付・シフトなどソフトウェアで置き換えられる作業である", "既存の予約サービスが合わない、または高い"),
        unknowns=("人手が足りない業務の内訳（接客・調理・施術・受付）", "現在使っている予約・シフト管理の方法"),
        next_action="対象業種の事業主に、1日の業務のうち受付・予約・シフト調整にかかる時間と、使っている道具をヒアリングする。",
        basis_industries=FOOD_AND_LOCAL_SERVICES,
        not_implied_extra="人手不足の業務が接客・調理などの場合、このソフトウェアで解決できる範囲は小さい。",
    ),
    HypothesisRule(
        key="p07.operations", problem_code="07", domain="operations",
        title="作業時間の内訳が見えないことによる省力化の難しさ",
        target_customer="従業員を雇用している個人企業",
        problem_statement="人件費の増加に対して、どの作業に時間がかかっているか把握できず、省力化の対象を決めにくい可能性がある（推測）。",
        solution_idea="作業ごとの時間を簡単に記録し、時間のかかっている作業を示すツール。",
        assumptions=("人件費の増加の一部が、業務の非効率によるものである", "作業時間の記録が負担にならない方法がある"),
        unknowns=("人件費の増加の主な要因（賃金上昇・人員増・非効率）", "作業時間を記録する意向があるか"),
        next_action="従業員のいる事業主に、人件費が増えた理由と、作業時間を把握しているかをヒアリングする。",
        basis_industries=("0",),
        not_implied_extra="最低賃金の上昇など外部の要因による増加は、このソフトウェアでは解決しない。",
    ),
    # --- Finance ---
    HypothesisRule(
        key="p05.finance", problem_code="05", domain="finance",
        title="請求・入金管理の手作業による入金遅れ・請求漏れ",
        target_customer="掛け売り（後払い）の取引がある個人企業",
        problem_statement="資金繰りの悪化の一因として、請求・入金の管理が手作業で、入金の遅れや請求漏れに気づきにくい可能性がある（推測）。",
        solution_idea="請求書の発行と入金の確認をまとめて管理し、未入金を知らせるツール。",
        assumptions=("資金繰りの悪化の一因が、売上の減少ではなく回収の管理にある", "掛け売りの取引がある"),
        unknowns=("資金繰りの悪化の主な原因", "既存の会計・請求ソフトの利用状況"),
        next_action="資金繰りに困った経験のある事業主に、原因と、請求・入金の管理方法をヒアリングする。",
        basis_industries=("0",),
        not_implied_extra="売上の減少が主な原因の場合、このソフトウェアで解決できる範囲は小さい。",
    ),
    HypothesisRule(
        key="p09.finance.food", problem_code="09", domain="finance",
        title="原価の変化を価格に反映できていない",
        target_customer="個人経営の飲食店・食料品製造業",
        problem_statement="原材料・仕入価格の上昇が、メニューや商品の価格に反映されていない可能性がある（推測）。",
        solution_idea="仕入価格の変化から、メニュー・商品ごとの原価率と価格改定の目安を示すツール。",
        assumptions=("原価計算を定期的に行っていない事業者がいる", "価格を改定できる余地がある"),
        unknowns=("原価計算をしている事業者の割合", "価格改定をためらう理由"),
        next_action="対象業種の事業主に、原価の把握方法と、直近の価格改定の有無・判断の方法をヒアリングする。",
        basis_industries=("42", "21"),
    ),
    HypothesisRule(
        key="p12.finance", problem_code="12", domain="finance",
        title="値上げの影響を試算する手段の不足",
        target_customer="個人企業全般",
        problem_statement="コストの増加を販売価格に転嫁できない理由の一つとして、値上げした場合の利益や客数への影響を試算できていない可能性がある（推測）。",
        solution_idea="価格・客数・原価を変えた場合の利益を簡単に試算できるツール。",
        assumptions=("転嫁できない理由の一部が、試算の不足による判断の難しさである",),
        unknowns=("転嫁できない主な理由", "表計算などで試算している事業者の割合"),
        next_action="価格転嫁を検討した事業主に、判断に使った情報と、試算をしたかをヒアリングする。",
        basis_industries=("0",),
        not_implied_extra="取引上の力関係や競合が主な理由の場合、このソフトウェアで解決できる範囲は小さい。",
    ),
    # --- Customer Success ---
    HypothesisRule(
        key="p02.cs", problem_code="02", domain="customer_success",
        title="既存顧客の来店頻度の低下に気づけない",
        target_customer="個人経営の飲食サービス業・生活関連サービス業",
        problem_statement="需要の停滞の一因として、既存顧客の来店頻度が下がっていても、来店履歴を把握できていない可能性がある（推測）。",
        solution_idea="来店・利用の履歴を簡単に記録し、しばらく来ていない顧客に連絡できるツール。",
        assumptions=("需要の停滞の一因が、既存顧客の離反である", "顧客の連絡先を、本人の同意のもとで管理できる"),
        unknowns=("顧客台帳を持っているか", "リピーターの売上に占める割合"),
        next_action="対象業種の事業主に、リピーターの把握方法と、離れた顧客への働きかけの有無をヒアリングする。",
        basis_industries=FOOD_AND_LOCAL_SERVICES,
    ),
    HypothesisRule(
        key="p03.cs", problem_code="03", domain="customer_success",
        title="顧客の要望の変化を把握する手段の不足",
        target_customer="個人経営の小売業・生活関連サービス業",
        problem_statement="製品・商品ニーズの変化への対応が課題となる中で、顧客の要望を体系的に集める手段がない可能性がある（推測）。",
        solution_idea="来店客・利用者から短いアンケートや要望を集め、傾向をまとめるツール。",
        assumptions=("課題が、変化を知る手段の不足である（知っていても対応できない場合は別）",),
        unknowns=("事業主が顧客の要望をどう把握しているか", "顧客がアンケートに回答する意向"),
        next_action="対象業種の事業主に、ニーズの変化に気づいたきっかけと、顧客の声の集め方をヒアリングする。",
        basis_industries=RETAIL_AND_LOCAL_SERVICES,
    ),
    HypothesisRule(
        key="p12.cs", problem_code="12", domain="customer_success",
        title="値上げによる顧客離れへの懸念",
        target_customer="個人経営の小売業・生活関連サービス業",
        problem_statement="コストの増加を価格に転嫁できない理由の一つとして、値上げによる顧客離れを恐れている可能性がある（推測）。",
        solution_idea="値上げの前後で、常連客への説明や特典を管理し、離反の兆しを確認できるツール。",
        assumptions=("顧客離れへの懸念が、転嫁できない主な理由の一つである",),
        unknowns=("値上げ後に実際に顧客が離れた経験の有無", "常連客の割合"),
        next_action="価格転嫁を見送った事業主に、見送った理由と、顧客の反応をどう予想したかをヒアリングする。",
        basis_industries=RETAIL_AND_LOCAL_SERVICES,
    ),
)

NO_HYPOTHESES: tuple[NoHypothesis, ...] = (
    NoHypothesis("04", "建物・設備の狭小・老朽化は、設備投資や不動産の課題であり、ソフトウェアで解決できる範囲が小さいため、仮説を作らない。"),
    NoHypothesis("08", "後継者難は、事業承継（専門家・M&A等）の領域であり、個人開発のSaaSが直接解決できる範囲が限られるため、仮説を作らない。"),
    NoHypothesis("11", "家賃・地代の上昇は、外部のコスト要因であり、ソフトウェアで解決できる範囲が小さいため、仮説を作らない。"),
)
