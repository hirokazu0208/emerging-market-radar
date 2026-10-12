"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type StatTable = {
  id: number;
  title: string;
  survey_name: string;
  period: string;
  publisher: string;
  source_url: string;
  unit: string;
  answer_type: string;
  note: string | null;
  scope_note: string;
  imported_at: string;
  attribution: string;
  processing_note: string;
  interpretation_note: string;
};

type Cell = {
  code: string;
  name: string;
  value: number | null;
  symbol: string | null;
  computed_share_pct: number | null;
};

type Industry = {
  code: string;
  name: string;
  level: number;
  parent_code: string | null;
  is_subset: boolean;
  base_industry: { code: string; name: string };
  base_note: string;
  total: { value: number | null; symbol: string | null } | null;
  items: Cell[];
  unlisted: {
    name: string;
    computed_value: number;
    computed_share_pct: number | null;
    includes_symbol_cells: boolean;
  } | null;
  low_precision: boolean;
};

type ProblemsResponse = {
  table: StatTable | null;
  problems: { code: string; name: string }[];
  industries: Industry[];
};

// 並べ替えのキー："order" は表の順序、"unlisted" は掲載のない回答、それ以外は課題コード
type SortKey = string;

const jstFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatPermille(value: number | null, symbol: string | null): string {
  if (symbol) return symbol; // 記号は原文のまま表示する
  return value === null ? "" : `${value}‰`;
}

function formatShare(pct: number | null | undefined): string {
  return pct === null || pct === undefined ? "—" : `${pct.toFixed(1)}%`;
}

function shareOf(ind: Industry, key: SortKey): number | null {
  if (key === "unlisted") return ind.unlisted?.computed_share_pct ?? null;
  return ind.items.find((c) => c.code === key)?.computed_share_pct ?? null;
}

export default function ProblemsPage() {
  // useSearchParams を使うため、Suspense の境界で囲む（Next.js の推奨）
  return (
    <Suspense fallback={<p className="empty">読み込み中…</p>}>
      <ProblemsContent />
    </Suspense>
  );
}

function ProblemsContent() {
  // 仮説の根拠などから ?industry=42&problem=09 で開けるようにする
  const params = useSearchParams();
  const [tables, setTables] = useState<StatTable[] | null>(null);
  const [tableId, setTableId] = useState<number | null>(null);
  const [data, setData] = useState<ProblemsResponse | null>(null);
  const [industry, setIndustry] = useState(params.get("industry") ?? "");
  const [problem, setProblem] = useState(params.get("problem") ?? "");
  const [sortKey, setSortKey] = useState<SortKey>(params.get("problem") || "order");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // 業種の選択肢は、絞り込みに関係なく表の全業種から作る
  const [allIndustries, setAllIndustries] = useState<Industry[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/problem-tables`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((d: { tables: StatTable[] }) => {
        setTables(d.tables);
        setTableId(d.tables[0]?.id ?? null);
        if (!d.tables.length) setLoading(false);
      })
      .catch((e) => {
        setError(`統計表の一覧を取得できませんでした。バックエンドが起動しているか確認してください。(${e})`);
        setLoading(false);
      });
  }, []);

  const load = useCallback(async (id: number, selectedIndustry: string, signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ table_id: String(id) });
      if (selectedIndustry) params.set("industry", selectedIndustry);
      const res = await fetch(`${API_BASE}/api/problems?${params}`, { signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: ProblemsResponse = await res.json();
      if (signal.aborted) return;
      setData(json);
    } catch (e) {
      if (signal.aborted) return; // 新しい条件で取得し直しているため、古い結果・エラーは反映しない
      setData(null);
      setError(`経営課題のデータを取得できませんでした。(${e})`);
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, []);

  // 業種の選択肢は、業種で絞り込まない一覧から作る（?industry= で開いた場合も全業種を選べるように）
  useEffect(() => {
    if (tableId === null) return;
    const controller = new AbortController();
    fetch(`${API_BASE}/api/problems?table_id=${tableId}`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((json: ProblemsResponse) => setAllIndustries(json.industries))
      .catch(() => {
        if (!controller.signal.aborted) setAllIndustries([]);
      });
    return () => controller.abort();
  }, [tableId]);

  useEffect(() => {
    if (tableId === null) return;
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    load(tableId, industry, controller.signal);
    return () => controller.abort();
  }, [tableId, industry, load]);

  const problems = data?.problems ?? [];
  // URL などで存在しない課題コードが指定された場合は、絞り込み・並べ替えに使わない
  const validProblem = problems.some((p) => p.code === problem) ? problem : "";
  const effectiveSort = sortKey === "unlisted" || problems.some((p) => p.code === sortKey) ? sortKey : "order";

  const rows = useMemo(() => {
    const list = [...(data?.industries ?? [])];
    if (effectiveSort === "order") return list;
    return list.sort((a, b) => (shareOf(b, effectiveSort) ?? -1) - (shareOf(a, effectiveSort) ?? -1));
  }, [data, effectiveSort]);

  const shownProblems = validProblem ? problems.filter((p) => p.code === validProblem) : problems;
  const table = data?.table ?? null;

  function selectTable(id: number) {
    // 業種・課題のコードは表ごとに異なり得るため、調査年を変えたら絞り込みを解除する
    setTableId(id);
    setIndustry("");
    setProblem("");
    setSortKey("order");
  }

  function selectProblem(code: string) {
    setProblem(code);
    setSortKey(code || "order");
  }

  return (
    <main className="container wide">
      <header className="header">
        <div>
          <h1>業種別の経営課題</h1>
          <p className="subtitle">公的統計に記載された経営課題（第2層の分析に向けた土台）</p>
        </div>
        <div className="header-actions">
          <Link href="/hypotheses" className="nav-link">
            SaaS需要仮説 →
          </Link>
          <Link href="/" className="nav-link">
            ← ニュース一覧へ
          </Link>
        </div>
      </header>

      <div className="caution">
        <strong>注意：</strong>
        ここに示すのは<strong>統計上の経営課題</strong>です。割合の大小は、ソフトウェアの需要や支払意思の強さを示すものではありません。
        {table && <> {table.scope_note}</>}
      </div>

      {tables && tables.length > 0 && (
        <div className="toolbar">
          <label>
            調査年：
            <select value={tableId ?? ""} onChange={(e) => selectTable(Number(e.target.value))}>
              {tables.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.period}年（第{t.title.match(/^第(\S+)表/)?.[1] ?? "?"}表）
                </option>
              ))}
            </select>
          </label>
          <label>
            業種：
            <select value={industry} onChange={(e) => setIndustry(e.target.value)}>
              <option value="">すべて</option>
              {allIndustries.map((i) => (
                <option key={i.code} value={i.code}>
                  {"　".repeat(i.level - 1)}
                  {i.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            経営課題：
            <select value={validProblem} onChange={(e) => selectProblem(e.target.value)}>
              <option value="">すべて</option>
              {problems.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            並べ替え：
            <select value={effectiveSort} onChange={(e) => setSortKey(e.target.value)}>
              <option value="order">表の順序（業種の階層）</option>
              {shownProblems.map((p) => (
                <option key={p.code} value={p.code}>
                  「{p.name}」の業種内割合が高い順
                </option>
              ))}
              <option value="unlisted">表に掲載のない回答が多い順</option>
            </select>
          </label>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="empty">読み込み中…</p>
      ) : tables && tables.length === 0 ? (
        <div className="empty">
          統計データがまだ取り込まれていません。README の「経営課題統計の取り込み」の手順で、e-Stat の公式 Excel を取り込んでください。
        </div>
      ) : (
        data && (
          <div className="table-scroll">
            <table className="problems-table">
              <thead>
                <tr>
                  <th className="sticky-col">業種</th>
                  <th>
                    総数
                    <span className="th-sub">元データ</span>
                  </th>
                  {shownProblems.map((p) => (
                    <th key={p.code}>
                      <button
                        className={`th-sort${effectiveSort === p.code ? " active" : ""}`}
                        onClick={() => setSortKey(p.code)}
                        title="業種内割合が高い順に並べ替え"
                      >
                        {p.name}
                      </button>
                      <Link href={`/hypotheses?problem=${p.code}`} className="th-sub th-link" title="この経営課題を出発点とする需要仮説">
                        仮説を見る
                      </Link>
                    </th>
                  ))}
                  <th>
                    <button
                      className={`th-sort${effectiveSort === "unlisted" ? " active" : ""}`}
                      onClick={() => setSortKey("unlisted")}
                    >
                      表に掲載のない回答（内訳未確認）
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((ind) => (
                  <tr key={ind.code} className={`level-${ind.level}`}>
                    <th className="sticky-col industry-cell" title={ind.base_note}>
                      <span style={{ paddingLeft: `${(ind.level - 1) * 0.9}em` }}>{ind.name}</span>
                      <span className="tags">
                        {ind.is_subset && <span className="tag">一部の内訳</span>}
                        {ind.low_precision && <span className="tag tag-warn">丸め誤差大</span>}
                      </span>
                    </th>
                    <td className="num">
                      {ind.total ? formatPermille(ind.total.value, ind.total.symbol) : ""}
                      <span className="cell-sub">{ind.base_industry.code === ind.code ? "＝1000" : `${ind.base_industry.name}＝1000`}</span>
                    </td>
                    {shownProblems.map((p) => {
                      const c = ind.items.find((x) => x.code === p.code);
                      return (
                        <td key={p.code} className="num">
                          <span className="share">{formatShare(c?.computed_share_pct)}</span>
                          <span className="cell-sub">{c ? formatPermille(c.value, c.symbol) : ""}</span>
                        </td>
                      );
                    })}
                    <td className="num">
                      <span className="share">
                        {formatShare(ind.unlisted?.computed_share_pct)}
                        {ind.unlisted?.includes_symbol_cells && <span title="記号のセルを除いて計算">※</span>}
                      </span>
                      <span className="cell-sub">{ind.unlisted ? `${ind.unlisted.computed_value}‰` : ""}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {table && (
        <section className="footer notes">
          <h2>表の見方</h2>
          <ul>
            <li>
              各セルの上段は<strong>業種内割合（本アプリが算出）</strong>＝ 項目の値 ÷ 同じ業種行の総数 です。
              下段の小さな数字は<strong>元データの千分率（‰）</strong>です。
            </li>
            <li>
              元データの千分率の分母は階層によって異なります。全産業・大分類の行はその業種の企業全体、中分類以下の行は所属する大分類の企業全体を1000としています。
              中分類以下の‰をそのまま業種間で比べず、業種内割合で比較してください。
            </li>
            <li>回答は単一回答です{table.note && `（${table.note.replace(/^1\)\s*/, "")}）`}。</li>
            <li>
              「表に掲載のない回答（内訳未確認）」は、総数から12項目の合計を引いた値です（本アプリが算出）。その内訳（その他・特になし等）は確認できていません。
              「※」は、記号のセル（「-」など）を合計に含めずに計算した値です。
            </li>
            <li>「-」などの記号は元データの記号をそのまま表示しています（0とはみなしていません。記号の意味は未確認です）。</li>
            <li>「一部の内訳」は元の表で「うち〜」とされている業種で、親業種の内訳を網羅していません。</li>
            <li>「丸め誤差大」は、行の総数が100‰未満で、整数の千分率からの割合の誤差が大きい業種です。</li>
          </ul>
          <h2>出典・加工・調査対象</h2>
          <p className="attribution">
            {table.attribution}
            <br />
            統計表：{table.title}（
            <a href={table.source_url} target="_blank" rel="noopener noreferrer">
              e-Stat の取得元
            </a>
            ）
          </p>
          <p className="attribution">{table.processing_note}</p>
          <p className="attribution">調査対象：{table.scope_note}</p>
          <p className="attribution">{table.interpretation_note}</p>
          <p className="attribution">取り込み日時：{jstFormatter.format(new Date(table.imported_at))} JST</p>
        </section>
      )}
    </main>
  );
}
