"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type Article = {
  id: number;
  source: string;
  source_label: string;
  external_id: string;
  title: string;
  url: string;
  summary: string | null;
  published_at: string;
  category: string;
};

type FetchResult = { fetched: number; inserted: number; errors: string[] };

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

export default function Home() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchResult, setFetchResult] = useState<FetchResult | null>(null);

  const loadArticles = useCallback(async (selected: string) => {
    setLoading(true);
    setError(null);
    try {
      const params = selected ? `?category=${encodeURIComponent(selected)}` : "";
      const res = await fetch(`${API_BASE}/api/articles${params}`);
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
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    loadArticles(category);
  }, [category, loadArticles]);

  async function handleFetch() {
    setFetching(true);
    setFetchResult(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/fetch`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFetchResult(await res.json());
      await loadArticles(category);
    } catch (e) {
      setError(`ニュースの取得に失敗しました。(${e})`);
    } finally {
      setFetching(false);
    }
  }

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
        !error && <p className="empty">記事がありません。「ニュースを取得」を押してください。</p>
      ) : (
        <ul className="articles">
          {articles.map((a) => (
            <li key={a.id} className="article">
              <div className="meta">
                <span className="badge">{a.category}</span>
                <span>{formatJst(a.published_at)}</span>
                <span>{a.source_label}</span>
              </div>
              <a href={a.url} target="_blank" rel="noopener noreferrer" className="title">
                {a.title}
              </a>
              {a.summary && <p className="summary">{a.summary}</p>}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
