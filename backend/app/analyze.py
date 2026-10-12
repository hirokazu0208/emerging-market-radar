"""既存記事に対して、キーワード・ルールによる市場形成シグナル（signal_type）の判定を実行する CLI。

使い方（backend ディレクトリで実行）:
    .venv/bin/python -m app.analyze              # 全件
    .venv/bin/python -m app.analyze --limit 20   # 公開日時の新しい順に20件
    .venv/bin/python -m app.analyze --dry-run    # DBに書き込まず結果だけ表示

同じ記事を再分析すると、その記事の method='keyword' の signal_type ラベルだけを置き換える。
人手（manual）・AI（ai）の判定には触れない。記事・市場ラベルも変更しない。
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field

from . import db
from .labels import replace_keyword_labels
from .signals import RULESET_VERSION, extract_signals
from .sources import SOURCES

DISCLAIMER = (
    "※ 判定結果はキーワード・ルールによる「市場形成シグナルの候補」です。"
    "explicit は記事の記述に明示的な根拠があることを示し、事実確認済みを意味しません。"
    "市場の成長やソフトウェア需要を予測・確定するものではありません。"
)


@dataclass
class AnalysisResult:
    analyzed: int = 0
    labeled_articles: int = 0
    unlabeled_articles: int = 0
    labels_created: int = 0
    labels_replaced: int = 0  # 再計算で削除した既存の keyword ラベル数
    by_label: Counter = field(default_factory=Counter)  # (label, strength) → 件数
    dry_run: bool = False


def run(limit: int | None = None, dry_run: bool = False) -> AnalysisResult:
    db.init_db(SOURCES)  # 未適用のマイグレーションがあれば適用する
    result = AnalysisResult(dry_run=dry_run)
    query = "SELECT id, title, summary FROM articles ORDER BY published_at DESC, id DESC"
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    with db.connect() as conn:  # 全件を1トランザクションで置き換える（途中で失敗した場合は何も変更しない）
        for row in conn.execute(query, params).fetchall():
            signals = extract_signals(row["title"], row["summary"])
            result.analyzed += 1
            if signals:
                result.labeled_articles += 1
            else:
                result.unlabeled_articles += 1
            for s in signals:
                result.by_label[(s.label, s.strength)] += 1
            if not dry_run:
                deleted, created = replace_keyword_labels(
                    conn, row["id"], "signal_type",
                    [(s.label, s.strength, " / ".join(s.evidence)) for s in signals],
                    RULESET_VERSION,
                )
                result.labels_replaced += deleted
                result.labels_created += created
        if dry_run:
            conn.rollback()
    if dry_run:
        result.labels_created = sum(result.by_label.values())
    return result


def format_result(result: AnalysisResult) -> str:
    lines = [
        f"ルール版：{RULESET_VERSION}" + ("（dry-run：DBには書き込んでいません）" if result.dry_run else ""),
        f"分析件数：{result.analyzed}",
        f"ラベルが付いた記事：{result.labeled_articles}",
        f"未判定（根拠なし）の記事：{result.unlabeled_articles}",
        f"付与したラベル：{result.labels_created}"
        + ("" if result.dry_run else f"（置き換えた既存の keyword ラベル：{result.labels_replaced}）"),
    ]
    for (label, strength), count in sorted(result.by_label.items()):
        lines.append(f"  - {label} / {strength}: {count}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="市場形成シグナル（signal_type）のキーワード判定を実行します。")
    parser.add_argument("--limit", type=int, help="公開日時の新しい順に処理する件数（省略時は全件）")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず結果だけ表示する")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit は1以上を指定してください")
    print(format_result(run(limit=args.limit, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
