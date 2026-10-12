"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type Market = { market: string; method: string };

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
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchResult, setFetchResult] = useState<FetchResult | null>(null);

  const loadArticles = useCallback(async (selectedCategory: string, selectedMarket: MarketFilter) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (selectedCategory) params.set("category", selectedCategory);
      if (selectedMarket) params.set("market", selectedMarket);
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
    loadArticles(category, market);
  }, [category, market, loadArticles]);

  async function handleFetch() {
    setFetching(true);
    setFetchResult(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/fetch`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFetchResult(await res.json());
      await loadArticles(category, market);
    } catch (e) {
      setError(`ニュースの取得に失敗しました。(${e})`);
    } finally {
      setFetching(false);
    }
  }

  const emptyMessage =
    market || category
      ? "条件に該当する記事がありません。"
      : "記事がありません。「ニュースを取得」を押してください。";

  return (
    <main className="container">
      <header className="header">
        <div>
          <h1>Emerging Market Radar</h1>
          <p className="subtitle">公開ニュースから新しい市場・産業の兆候を探す</p>
        </div>
        <button className="fetch-button" onClick={handleFetch} disabled={fetching}>
          {fetching ? "取得中…" : "ニュースを取得"}
        </button>
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
