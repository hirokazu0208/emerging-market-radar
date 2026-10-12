# Emerging Market Radar v0.3

世界で新しく生まれる市場・産業の兆候を発見するため、公開ニュースを収集・分類・保存・表示するMVPです。

- バックエンド：Python + FastAPI + SQLite
- フロントエンド：Next.js + TypeScript
- 情報源（いずれもAPIキー・登録不要）

| 情報源 | 取得方法 | 所在地 | 対象市場の既定値 | 利用条件 | 商用利用（`commercial_use`） |
| --- | --- | --- | --- | --- | --- |
| [Hacker News](https://news.ycombinator.com/) | [公式 Firebase API](https://github.com/HackerNews/API) | US | 未判定 | 未確認 | `unknown` |
| [e-Govパブリック・コメント](https://public-comment.e-gov.go.jp/) | [意見募集案件RSS](https://public-comment.e-gov.go.jp/rss/pcm_list.xml) | JP | JP | [公共データ利用規約（PDL1.0）](https://public-comment.e-gov.go.jp/contents/terms) | `allowed`（出典の記載と加工した旨の明示が条件。2026-10-12 確認） |

利用条件を確認できていない情報源は `unknown` とし、商用利用可とはみなしません。

## 出典・加工について

> 出典：e-Govパブリック・コメント（https://public-comment.e-gov.go.jp/）
> （RSSを本アプリが加工して作成：書式変換・項目抽出・カテゴリー分類）

e-Gov のデータについて、本アプリは次の加工を行っています。国が作成した未加工の情報ではありません。

- RSS の `description`（HTML）をテキストに変換し、空白や改行を整えて `summary` に保存
- `description` から公示日・受付締切日時・カテゴリー・問合せ先（所管省庁・部局名等）を抽出し、`metadata` に保存
- カテゴリーを「Policy & Regulation」、対象市場を「JP」として本アプリが付与

画面のフッターにも、情報源ごとの出典表記を表示しています。

## 実装済み機能

v0.1

- Hacker News の新着記事を最大30件取得（タイトル・URL・公開日時・情報源・取得可能な場合は要約）
- SQLite への保存（情報源 + 外部記事IDで重複登録を防止）
- タイトルのキーワードによる簡易カテゴリー分類
- 記事一覧API（FastAPI）とニュース一覧画面（Next.js）
- 原文サイトへのリンク、日本時間での日時表示、カテゴリー絞り込み
- 「ニュースを取得」ボタンによる再取得（外部APIエラー時も画面にエラーを表示して継続）

v0.2

- e-Govパブリック・コメントの意見募集案件を取得（タイトル・URL・公示日・概要・締切・カテゴリー・所管省庁等）
- 情報源の所在地（`sources.country`）と、記事が対象とする市場（`article_markets`）を区別して保存
- 画面に「すべて／日本／海外」の切替を追加。市場未判定の記事は「すべて」にのみ表示し、海外と断定しない
- 記事の言語（`articles.language`）を保存し、日本語・英語のタイトルをそのまま表示
- v0.1 の既存DBを、データを失わずに自動マイグレーション

v0.3（データ構造の整備のみ。画面・取得機能は v0.2 と同じ）

- 情報源ごとの利用条件（`terms_url` / `commercial_use` / `terms_checked_on`）を `sources` に追加
- 記事ラベル `article_labels` と、ラベルの語彙 `label_definitions` を追加
  - 観測事実（`signal_type`）と、ソフトウェア需要の仮説（`software_domain`）を区別
  - 判定の主体（人手・AIなど）を区別
- ラベルの登録・取得の内部関数（`backend/app/labels.py`）
- v2 → v3 の自動マイグレーション（既存の記事・市場ラベルは変更しない）

## 未実装（今後の予定）

- AI による `signal_type` / `software_domain` の自動判定（現在、ラベルは1件も登録されていません）
- キーワードによるラベル判定
- ラベルの画面表示と、ラベルを登録・取得する HTTP API（現在は内部関数のみ）
- 企業情報（`organizations`）と国税庁法人番号との照合
- 受信データの原文保存（`raw_payload`）
- Hacker News・e-Gov 以外の情報源

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
v0.1・v0.2 のDBがある場合は、起動時に自動で最新のスキーマ（v3）へマイグレーションします（後述）。

### 2. フロントエンド（http://localhost:3000）

```bash
cd frontend
npm install
npm run dev
```

ブラウザで http://localhost:3000 を開き（3000番が使用中の場合は、Next.jsが自動で別ポートを使います。ターミナルに表示されたURLを開いてください）、「ニュースを取得」を押すと全情報源から最新記事を取得・保存します。
APIの接続先は環境変数 `NEXT_PUBLIC_API_BASE` で変更できます（既定値：`http://localhost:8000`）。

## テスト

```bash
cd backend
.venv/bin/python -m pytest -q
```

外部API・RSSはモックするため、テストは外部通信を行いません。

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
| GET | `/api/sources` | 情報源一覧（所在地・利用条件・商用利用の可否・確認日・出典表記） |
| GET | `/api/articles?market=home&category=AI&limit=100` | 記事一覧（公開日時の新しい順） |
| POST | `/api/fetch` | 全情報源から最新記事を取得・保存。`{fetched, inserted, errors}` を返す |

`market` パラメータは次のとおりです。

- 未指定：すべての記事
- `home`：日本市場（JP）を対象とする記事
- `overseas`：JP 以外の市場を対象とする記事

対象市場が未判定の記事は、`home` にも `overseas` にも含まれません。

外部APIでエラーが起きても、`/api/fetch` は 200 を返し、内容を `errors` に入れます。アプリ全体は停止せず、保存済みの記事も削除しません。

## データ構造

### `articles`

| 列 | 内容 |
| --- | --- |
| `source` / `external_id` | 情報源IDと情報源内の記事ID。この2つの組で重複登録を防止。e-Gov は案件URLの `id` パラメータ |
| `title` / `url` | タイトルと原文URL（HNで外部URLが無い記事は、HNの記事ページ） |
| `summary` | 公開APIで取得できる要約・本文。HNでは Ask HN などの `text`、e-Gov では RSS の概要。無い場合は NULL |
| `published_at` / `fetched_at` | 公開日時・取得日時（UTC, ISO 8601）。画面では日本時間で表示 |
| `category` | 簡易分類。HN はタイトルのキーワード（`backend/app/classifier.py`）、e-Gov は「Policy & Regulation」 |
| `language` | 記事の言語（`en` / `ja`） |
| `metadata` | 情報源固有の項目（JSON）。e-Gov：`announced_on`（公示日）, `deadline`（受付締切日時）, `egov_category`, `contact`（問合せ先・所管省庁等） |

### `sources`

情報源の属性です。`country` は**情報源の所在地**で、記事の対象市場とは別です。

| 列 | 内容 |
| --- | --- |
| `license` / `attribution` | 利用条件の名称（例：PDL1.0）と、出典表記 |
| `terms_url` | 利用条件のページ |
| `commercial_use` | `allowed` / `restricted` / `unknown`（既定値は `unknown`） |
| `terms_checked_on` | 利用条件を確認した日 |

### `article_markets`

記事が**対象とする市場**です。1記事に複数付けられます。行が無い記事は「未判定」として扱います。

| 列 | 内容 |
| --- | --- |
| `market` | ISO 3166-1 alpha-2（`JP` など）または `GLOBAL` |
| `method` | 判定の根拠。`source_default`（情報源の既定値）, `keyword`, `ai`, `manual` |

現在は `source_default` のみを使用しています（e-Gov → JP）。
既存記事の対象市場を、推測で後から付けることはしません。

### `article_labels` / `label_definitions`

記事に付けるラベルです。種類（taxonomy）ごとに、性質（`assertion_kind`）が決まっています。

| taxonomy | 性質 | ラベル |
| --- | --- | --- |
| `signal_type` | `observation`（記事に書かれた事実の観測） | `new_service`, `market_entry`, `partnership`, `funding`, `expansion`, `new_segment`, `regulation` |
| `software_domain` | `hypothesis`（観測から推定したソフトウェア需要の仮説） | `marketing`, `sales`, `operations`, `finance`, `customer_success` |

`article_labels` の列は次のとおりです。

| 列 | 内容 |
| --- | --- |
| `article_id` | 対象の記事。記事を削除するとラベルも削除 |
| `taxonomy` / `label` | `label_definitions` にある組み合わせのみ登録可能（外部キー） |
| `method` | 判定の主体：`source_default` / `keyword` / `ai` / `manual` |
| `evidence` | 判定の根拠（記事中の該当箇所の抜粋など） |
| `model` | AI 判定時のモデル名・プロンプト版。`method = 'ai'` のときは必須、それ以外は NULL（CHECK 制約） |
| `created_at` | 登録日時（UTC） |

- 同じ記事・ラベル・判定主体・モデルの組は1件のみ登録できます。
- 人手の判定と AI の判定、AI のモデル違いは、別の行として残ります。

内部関数（`backend/app/labels.py`）は次のとおりです。

- `add_article_label(article_id, taxonomy, label, method, evidence=None, model=None) -> bool`：登録する。重複していれば `False`。不正な値は `ValueError`
- `list_article_labels(article_id, taxonomy=None, assertion_kind=None) -> list[dict]`：取得する。各行に `assertion_kind` を含む

### マイグレーション

スキーマのバージョンは `PRAGMA user_version` で管理しています（`backend/app/db.py` の `MIGRATIONS`）。

- v0.1 のDB（`user_version = 0`）は、起動時に v2、v3 の順に移行します。各段階は1トランザクションで適用されます。
- v0.2 のDB（`user_version = 2`）は、v3 に移行します。
- どちらの場合も、既存の記事・取得日時・市場ラベルは保持されます。
- 既存記事の `language` は、情報源の定義から補完します。
- v3 の移行では、ラベルは追加しません。

記事本文のスクレイピングは行わず、公開API・RSSから取得できる情報だけを保存します。

## 情報源の追加方法

情報源ごとの収集処理は `backend/app/sources/` に分かれています。

1. `backend/app/sources/<name>.py` を作り、クラスを実装する（`base.py` の `Source` を参照）
   - 属性：`name`, `label`, `country`, `kind`, `language`, `default_market`, `homepage`, `license`, `attribution`, `terms_url`, `commercial_use`, `terms_checked_on`
   - 利用条件を公式に確認できない場合は `commercial_use = "unknown"` にする
   - `commercial_use` が未指定または不正な値の場合は `unknown` として登録する
   - `allowed` でも `terms_url` と `terms_checked_on` が揃っていなければ `unknown` として登録する
   - メソッド：`async fetch(limit) -> FetchResult`
2. `backend/app/sources/__init__.py` の `SOURCES` にインスタンスを追加する

`sources` テーブルへの登録は起動時と取得時に自動で行います。保存・API・画面の変更は不要です。

## ディレクトリ構成

```
backend/
  app/
    main.py          FastAPI（エンドポイント）
    db.py            SQLite保存・取得・マイグレーション
    classifier.py    キーワード分類
    labels.py        記事ラベルの語彙・登録・取得
    sources/
      base.py        情報源インターフェース（RawArticle / FetchResult / Source）
      hackernews.py  Hacker News 取得処理
      egov_pcm.py    e-Govパブリック・コメント RSS 取得処理
  tests/             pytest
frontend/
  src/app/page.tsx   ニュース一覧画面
```
