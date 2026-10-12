"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type Market = { market: string; method: string };

type SignalLabel = {
  label: string;
  method: string;
  strength: "explicit" | "inferred" | null;
  evidence: string | null;
  model: string | null;
  rule_version: string | null;
};

type Article = {
  id: number;
  source: string;
  source_label: string;
  source_country: string | null;
  external_id: string;
  title: string;
  url: string;
  summary: string | null;
  published_at: string;
  category: string;
  language: string | null;
  markets: Market[];
  signals: SignalLabel[];
};

type Source = {
  id: string;
  label: string;
  country: string;
  homepage: string;
  license: string | null;
  attribution: string | null;
};

type FetchResult = { fetched: number; inserted: number; errors: string[] };

// API の market パラメータ。"" は「すべて」
type MarketFilter = "" | "home" | "overseas";

const MARKET_TABS: { value: MarketFilter; label: string }[] = [
  { value: "", label: "すべて" },
  { value: "home", label: "日本" },
  { value: "overseas", label: "海外" },
];

const MARKET_NAMES: Record<string, string> = { JP: "日本", US: "米国", GLOBAL: "グローバル" };

const METHOD_NAMES: Record<string, string> = {
  source_default: "情報源から判定",
  keyword: "キーワード判定",
  ai: "AI判定",
  manual: "手動設定",
};

// 市場形成シグナル（signal_type）の表示名
const SIGNAL_NAMES: Record<string, string> = {
  new_service: "新サービス",
  market_entry: "新規参入",
  partnership: "提携",
  funding: "資金調達",
  expansion: "事業拡大",
  new_segment: "新しい顧客層",
  regulation: "規制・制度変更",
};

const STRENGTH_NAMES: Record<string, string> = {
  explicit: "明示記述",
  inferred: "推測",
};

function signalName(label: string): string {
  return SIGNAL_NAMES[label] ?? label;
}

// キーワード判定は「候補（明示記述／推測）」、それ以外は判定方法を表示する
function signalBasis(s: SignalLabel): string {
  if (s.method === "keyword") return `候補・${STRENGTH_NAMES[s.strength ?? ""] ?? "キーワード"}`;
  return METHOD_NAMES[s.method] ?? s.method;
}

const jstFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatJst(iso: string): string {
  return `${jstFormatter.format(new Date(iso))} JST`;
}

function marketName(code: string): string {
  return MARKET_NAMES[code] ?? code;
}

export default function Home() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [category, setCategory] = useState("");
  const [market, setMarket] = useState<MarketFilter>("");
  const [signal, setSignal] = useState("");
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchResult, setFetchResult] = useState<FetchResult | null>(null);

  const loadArticles = useCallback(async (selectedCategory: string, selectedMarket: MarketFilter, selectedSignal: string) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (selectedCategory) params.set("category", selectedCategory);
      if (selectedMarket) params.set("market", selectedMarket);
      if (selectedSignal) params.set("signal", selectedSignal);
      const query = params.toString() ? `?${params}` : "";
      const res = await fetch(`${API_BASE}/api/articles${query}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: { articles: Article[] } = await res.json();
      setArticles(data.articles);
    } catch (e) {
      setArticles([]);
      setError(`記事一覧を取得できませんでした。バックエンドが起動しているか確認してください。(${e})`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/categories`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data: { categories: string[] }) => setCategories(data.categories))
      .catch(() => setCategories([]));
    fetch(`${API_BASE}/api/sources`)
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data: { sources: Source[] }) => setSources(data.sources))
      .catch(() => setSources([]));
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    loadArticles(category, market, signal);
  }, [category, market, signal, loadArticles]);

  async function handleFetch() {
    setFetching(true);
    setFetchResult(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/fetch`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFetchResult(await res.json());
      await loadArticles(category, market, signal);
    } catch (e) {
      setError(`ニュースの取得に失敗しました。(${e})`);
    } finally {
      setFetching(false);
    }
  }

  const emptyMessage =
    market || category || signal
      ? "条件に該当する記事がありません。"
      : "記事がありません。「ニュースを取得」を押してください。";

  return (
    <main className="container">
      <header className="header">
        <div>
          <h1>Emerging Market Radar</h1>
          <p className="subtitle">公開ニュースから新しい市場・産業の兆候を探す</p>
        </div>
        <div className="header-actions">
          <Link href="/problems" className="nav-link">
            業種別の経営課題 →
          </Link>
          <Link href="/hypotheses" className="nav-link">
            SaaS需要仮説 →
          </Link>
          <Link href="/opportunities" className="nav-link">
            事業機会の比較 →
          </Link>
          <button className="fetch-button" onClick={handleFetch} disabled={fetching}>
            {fetching ? "取得中…" : "ニュースを取得"}
          </button>
        </div>
      </header>

      <div className="toolbar">
        <div className="tabs" role="group" aria-label="対象市場">
          {MARKET_TABS.map((tab) => (
            <button
              key={tab.value}
              className={`tab${market === tab.value ? " active" : ""}`}
              aria-pressed={market === tab.value}
              onClick={() => setMarket(tab.value)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <label>
          カテゴリー：
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">すべて</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        {!loading && <span className="count">{articles.length} 件</span>}
      </div>
      {market === "overseas" && (
        <p className="hint">対象市場が未判定の記事（Hacker News など）は「すべて」にのみ表示されます。</p>
      )}
      {signal && (
        <div className="signal-filter">
          シグナル「{signalName(signal)}」で絞り込み中
          <button className="clear-button" onClick={() => setSignal("")}>
            × 解除
          </button>
        </div>
      )}
      <p className="disclaimer">
        市場形成シグナルは、タイトル・要約へのキーワード・ルール判定による<strong>候補</strong>です。
        「明示記述」は記事に明示的な記述があることを示し、事実確認済みを意味しません。「推測」はルールによる推測候補です。
        市場の成長やソフトウェア需要を示すものではありません。根拠が見つからない記事にはラベルを付けていません。
      </p>

      {fetchResult && (
        <div className="notice">
          {fetchResult.fetched} 件取得、新規 {fetchResult.inserted} 件を保存しました。
          {fetchResult.errors.length > 0 && (
            <ul className="error-list">
              {fetchResult.errors.map((msg, i) => (
                <li key={i}>{msg}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="empty">読み込み中…</p>
      ) : articles.length === 0 ? (
        !error && <p className="empty">{emptyMessage}</p>
      ) : (
        <ul className="articles">
          {articles.map((a) => (
            <li key={a.id} className="article">
              <div className="meta">
                <span className="badge">{a.category}</span>
                <span>{formatJst(a.published_at)}</span>
                <span>
                  {a.source_label}
                  {a.source_country && `（${a.source_country}）`}
                </span>
                <span
                  className="market"
                  title={a.markets.map((m) => METHOD_NAMES[m.method] ?? m.method).join("、") || undefined}
                >
                  対象市場：
                  {a.markets.length ? a.markets.map((m) => marketName(m.market)).join("・") : "未判定"}
                </span>
              </div>
              <a
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                className="title"
                lang={a.language ?? undefined}
              >
                {a.title}
              </a>
              {a.summary && <p className="summary">{a.summary}</p>}
              {a.signals.length > 0 && (
                <div className="signals">
                  {a.signals.map((s) => (
                    <button
                      key={`${s.label}-${s.method}-${s.model ?? ""}`}
                      className={`signal signal-${s.strength ?? "other"}${signal === s.label ? " active" : ""}`}
                      onClick={() => setSignal(s.label)}
                      title={`「${signalName(s.label)}」の記事で絞り込む`}
                    >
                      {signalName(s.label)}
                      <span className="signal-basis">{signalBasis(s)}</span>
                    </button>
                  ))}
                  {a.source === "egov_pcm" && a.signals.some((s) => s.label === "regulation") && (
                    <span className="signal-note">改正案等の意見募集であり、成立・施行済みではありません</span>
                  )}
                  <details className="evidence">
                    <summary>判定の根拠</summary>
                    <ul>
                      {a.signals.map((s) => (
                        <li key={`${s.label}-${s.method}-${s.model ?? ""}`}>
                          <strong>{signalName(s.label)}</strong>（{signalBasis(s)}
                          {s.method === "keyword" && "・キーワード判定"}
                          {s.rule_version && `・ルール ${s.rule_version}`}
                          {s.model && `・${s.model}`}）
                          {s.evidence && <div className="evidence-text">{s.evidence}</div>}
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {sources.length > 0 && (
        <footer className="footer">
          <h2>情報源・出典</h2>
          <ul>
            {sources.map((s) => (
              <li key={s.id}>
                <a href={s.homepage} target="_blank" rel="noopener noreferrer">
                  {s.label}
                </a>
                （所在地：{s.country}
                {s.license && `、利用条件：${s.license}`}）
                {s.attribution && <div className="attribution">{s.attribution}</div>}
              </li>
            ))}
          </ul>
          <p className="attribution">
            カテゴリーと対象市場は本アプリが自動で付与したもので、情報源による分類ではありません。
          </p>
        </footer>
      )}
    </main>
  );
}
