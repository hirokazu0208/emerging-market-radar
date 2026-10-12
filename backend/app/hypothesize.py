"""統計上の経営課題から、SaaS需要仮説の候補をルールで作る CLI。

使い方（backend ディレクトリで実行）:
    .venv/bin/python -m app.hypothesize            # 候補を作成・更新する
    .venv/bin/python -m app.hypothesize --dry-run  # DBに書き込まず結果だけ表示

再実行しても重複しない。人が操作した仮説（編集・状態の変更・根拠の追加）は上書きしない（見送った差分は履歴に残る）。
"""

import argparse
from collections import Counter

from .hypotheses import NoStatistics, generate
from .hypothesis_rules import DOMAINS, HYPOTHESIS_RULESET_VERSION, RULES

DISCLAIMER = (
    "※ 作成されるのは、人が検証するための「未検証」の仮説の候補です。"
    "統計の値は需要の強さを示すものではなく、需要・支払意思・収益化を示すものでもありません。"
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="統計上の経営課題から、SaaS需要仮説の候補をルールで作ります。")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず結果だけ表示する")
    args = parser.parse_args(argv)
    try:
        r = generate(dry_run=args.dry_run)
    except NoStatistics as e:
        parser.exit(1, f"中止しました：{e}\n")
    by_domain = Counter(rule.domain for rule in RULES)
    print(f"ルールセット：{HYPOTHESIS_RULESET_VERSION}" + ("（dry-run：DBには書き込んでいません）" if r.dry_run else ""))
    print(f"出発点の統計：{r.stat_table['period']}年 {r.stat_table['title']}")
    print(f"新規作成：{len(r.created)}、ルールで更新：{len(r.updated)}、変更なし：{len(r.unchanged)}、"
          f"人の操作を保護して更新を見送り：{len(r.skipped)}、ルール廃止：{len(r.retired)}")
    print(f"紐付けた観測（新規）：{r.basis_added}")
    if r.missing_basis:
        print("統計に該当する観測がないため紐付けなかったもの：" + "、".join(f"{k}（業種 {i}）" for k, i in r.missing_basis))
    print("ルールの領域別の件数：" + "、".join(f"{d} {by_domain.get(d, 0)}" for d in DOMAINS))
    print(DISCLAIMER)


if __name__ == "__main__":
    main()
