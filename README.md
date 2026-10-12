# Emerging Market Radar v0.1

世界で新しく生まれる市場・産業の兆候を発見するため、公開ニュースを収集・分類・保存・表示するMVPです。

- 情報源：Hacker News 公式 Firebase API（APIキー不要）
- バックエンド：Python + FastAPI + SQLite
- フロントエンド：Next.js + TypeScript

## 実装済み機能（v0.1）

- Hacker News の新着記事を最大30件取得（タイトル・URL・公開日時・情報源・取得可能な場合は要約）
- SQLite への保存（情報源 + 外部記事IDで重複登録を防止）
- タイトルのキーワードによる簡易カテゴリー分類
- 記事一覧API（FastAPI）とニュース一覧画面（Next.js）
- 原文サイトへのリンク、日本時間での日時表示、カテゴリー絞り込み
- 「ニュースを取得」ボタンによる再取得（外部APIエラー時も画面にエラーを表示して継続）

## 必要環境

- Python 3.12 以上（3.14 で動作確認）
- Node.js 20 以上（22 で動作確認）

## 起動方法

ターミナルを2つ使います。

### 1. バックエンド（http://localhost:8000）

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

DBは初回起動時に `backend/data/radar.db` に作成されます（環境変数 `RADAR_DB_PATH` で変更可）。

### 2. フロントエンド（http://localhost:3000）

```bash
cd frontend
npm install
npm run dev
```

ブラウザで http://localhost:3000 を開き（3000番が使用中の場合は、Next.jsが自動で別ポートを使います。ターミナルに表示されたURLを開いてください）、「ニュースを取得」を押すと Hacker News の新着記事（最大30件）を取得・保存します。
APIの接続先は環境変数 `NEXT_PUBLIC_API_BASE` で変更できます（既定値：`http://localhost:8000`）。

## テスト

```bash
cd backend
.venv/bin/python -m pytest -q
```

Hacker News API はモックするため、テストは外部通信を行いません。

```bash
cd frontend
npm run lint
npm run build
```

## API

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/health` | ヘルスチェック |
| GET | `/api/categories` | カテゴリー一覧 |
| GET | `/api/articles?category=AI&limit=100` | 記事一覧（公開日時の新しい順） |
| POST | `/api/fetch` | 全情報源から最新記事を取得・保存。`{fetched, inserted, errors}` を返す |

外部APIでエラーが起きても、`/api/fetch` は 200 を返し、内容を `errors` に入れます。アプリ全体は停止しません。

## データ構造（`articles` テーブル）

| 列 | 内容 |
| --- | --- |
| `source` / `external_id` | 情報源IDと情報源内の記事ID。この2つの組で重複登録を防止 |
| `title` / `url` | タイトルと原文URL（HNで外部URLが無い記事は、HNの記事ページ） |
| `summary` | 公開APIで取得できる要約・本文。HNでは Ask HN などの `text`。無い場合は NULL |
| `published_at` / `fetched_at` | 公開日時・取得日時（UTC, ISO 8601）。画面では日本時間で表示 |
| `category` | タイトルのキーワードによる簡易分類（`backend/app/classifier.py`） |

記事本文のスクレイピングは行わず、公開APIから取得できる情報だけを保存します。

## 情報源の追加方法

情報源ごとの収集処理は `backend/app/sources/` に分かれています。

1. `backend/app/sources/<name>.py` を作り、`name`・`label` 属性と `async fetch(limit) -> FetchResult` を持つクラスを実装する（`base.py` の `Source` を参照）
2. `backend/app/sources/__init__.py` の `SOURCES` にインスタンスを追加する

保存・分類・API・画面は情報源に依存しないので、変更不要です。

## ディレクトリ構成

```
backend/
  app/
    main.py          FastAPI（エンドポイント）
    db.py            SQLite保存・取得
    classifier.py    キーワード分類
    sources/
      base.py        情報源インターフェース（RawArticle / FetchResult / Source）
      hackernews.py  Hacker News 取得処理
  tests/             pytest
frontend/
  src/app/page.tsx   ニュース一覧画面
```
