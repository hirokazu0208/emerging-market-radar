# Emerging Market Radar v0.5

世界で新しく生まれる市場・産業の兆候を発見するため、公開ニュースを収集・分類・保存・表示するMVPです。
最終的には、個人開発者が自社SaaSを開発・販売できる事業機会の発見を目指します（[将来構想](#将来構想3層の分析と5つの業務領域)）。

- バックエンド：Python + FastAPI + SQLite
- フロントエンド：Next.js + TypeScript
- 情報源（いずれもAPIキー・登録不要）

| 情報源 | 取得方法 | 所在地 | 対象市場の既定値 | 利用条件 | 商用利用（`commercial_use`） |
| --- | --- | --- | --- | --- | --- |
| [Hacker News](https://news.ycombinator.com/) | [公式 Firebase API](https://github.com/HackerNews/API) | US | 未判定 | 未確認 | `unknown` |
| [e-Govパブリック・コメント](https://public-comment.e-gov.go.jp/) | [意見募集案件RSS](https://public-comment.e-gov.go.jp/rss/pcm_list.xml) | JP | JP | [公共データ利用規約（PDL1.0）](https://public-comment.e-gov.go.jp/contents/terms) | `allowed`（出典の記載と加工した旨の明示が条件。2026-10-12 確認） |

利用条件を確認できていない情報源は `unknown` とし、商用利用可とはみなしません。

統計データ（ニュースとは別に管理）は次のとおりです。

| 統計 | 取得方法 | 内容 | 利用条件 |
| --- | --- | --- | --- |
| [e-Stat](https://www.e-stat.go.jp/)「個人企業経済調査」（総務省統計局） | 公式 Excel（xlsx）を手動でダウンロードし、CLI で取り込む（APIキー不要） | 産業中分類 × 事業経営上の問題点（主なもの） | [e-Stat 利用規約](https://www.e-stat.go.jp/terms-of-use)（CC BY 4.0 互換、商用利用可。出典の記載と加工した旨の明示が条件） |

## 出典・加工について

> 出典：e-Govパブリック・コメント（https://public-comment.e-gov.go.jp/）
> （RSSを本アプリが加工して作成：書式変換・項目抽出・カテゴリー分類）

e-Gov のデータについて、本アプリは次の加工を行っています。国が作成した未加工の情報ではありません。

- RSS の `description`（HTML）をテキストに変換し、空白や改行を整えて `summary` に保存
- `description` から公示日・受付締切日時・カテゴリー・問合せ先（所管省庁・部局名等）を抽出し、`metadata` に保存
- カテゴリーを「Policy & Regulation」、対象市場を「JP」として本アプリが付与

画面のフッターにも、情報源ごとの出典表記を表示しています。

e-Stat の統計データの出典・加工は次のとおりです（`/problems` 画面にも表示）。

> 出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）「2025年（令和7年） 個人企業経済調査」第14表（総務省統計局）を加工して作成

- 本アプリによる加工：表形式の変換、業種内割合（各項目の値 ÷ 同じ業種行の総数）の算出、表に掲載のない回答（総数 − 12項目の合計）の算出
- 元データの千分率（‰）はそのまま保存しています。算出値は保存せず、API で毎回計算しています。

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

v0.4（第1層：市場形成シグナルの抽出）

- タイトル・要約のキーワード・ルールで `signal_type` を判定（日本語・英語）
  - ルールの定義：`backend/app/signals.py`
  - 判定結果は `method='keyword'` で `article_labels` に保存
- 判定の強さ（`strength`）と、判定ルールの版（`rule_version`）を保存（v3 → v4 マイグレーション）
- 既存記事を手動で分析する CLI（`python -m app.analyze`）
- 画面にシグナルの候補ラベルを表示
  - ラベルをクリックすると、そのシグナルの記事に絞り込み
  - 「判定の根拠」で根拠を表示
  - ラベルの無い記事もそのまま表示

v0.5（第2層の土台：業種別の経営課題統計）

- e-Stat「個人企業経済調査」の、事業経営上の問題点別企業分布（公式 Excel）を取り込む CLI（`python -m app.estat_import`）
  - 統計データは記事とは別の `stat_tables`・`problem_observations` に保存（v4 → v5 マイグレーション）
- 業種別の経営課題 API（`/api/problem-tables`、`/api/problems`）
- `/problems` 画面
  - 調査年・業種・経営課題で絞り込み、業種内割合で並べ替え
  - 元の千分率と、本アプリが算出した割合を区別して表示
  - 出典・加工内容・調査対象・注意事項を表示
- ニュース画面のヘッダーに `/problems` へのリンクを追加（それ以外のニュース画面・機能は変更なし）

## 業種別の経営課題統計（v0.5）

**統計上の経営課題であり、ソフトウェア需要ではありません。** 割合の大小を、SaaS需要や支払意思の強さと判断しないでください。

### 対象の統計表

| 項目 | 内容 |
| --- | --- |
| 統計 | 2025年（令和7年）個人企業経済調査（総務省統計局、政府統計コード 00200541） |
| 表 | 第14表 産業中分類，事業主の年齢階級，後継者の有無，事業経営上の問題点別企業分布 －全国（取り込むのは「事業経営上の問題点」の13列のみ） |
| ファイル | statInfId 000040413728（xlsx）。[ダウンロード](https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040413728&fileKind=0) |
| 調査期日 | 毎年6月1日現在 |
| 調査対象 | **個人企業（個人経営の事業所）**の標本約4万企業。**法人企業を含む中小企業全体を代表するものではありません** |
| 回答形式 | 単一回答（事業経営上の問題点のうち、最も大きな問題点として一つだけ回答したもの） |

### 表の読み方と注意点

- **単位は千分率（‰）**で、分母は階層によって異なります。
  - 全産業・大分類の行：その業種の企業全体 = 1000
  - 中分類・小分類の行：**所属する大分類**の企業全体 = 1000（例：総合工事業 329‰ は、建設業全体に占める割合）
- **業種内割合**（本アプリが算出）＝ 項目の値 ÷ 同じ業種行の総数。業種どうしの比較にはこちらを使います。
- 12項目の合計は総数に一致しません（全産業で 840‰）。差分は「**表に掲載のない回答（内訳未確認）**」として表示します。その内訳（その他・特になし等）は確認できていません。
- 「`-`」などの**記号は原文のまま**保存・表示し、0 には変換しません。記号の意味は確認できていません。
- 「うち〜」の業種（`is_subset`）は親業種の一部の内訳で、網羅的ではありません。
- 行の総数が 100‰ 未満の業種は、整数の千分率から計算するため誤差が大きく、画面に「丸め誤差大」と表示します。

### 取り込み手順

1. 上の「ダウンロード」から公式の xlsx を取得し、`backend/data/imports/` に置く（`backend/data/` は Git の管理対象外）
2. CLI を実行する

```bash
cd backend
.venv/bin/python -m app.estat_import data/imports/estat_kojinke_2025_t14.xlsx \
  --table-ref statInfId:000040413728 \
  --source-url "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040413728&fileKind=0"
```

CLI の動作は次のとおりです。

- **同じファイルの再取り込み**：SHA-256 が一致するため「変更なし」となり、何も変更しません。
  - 同じファイルを別の `--table-ref` で取り込もうとした場合は、重複を防ぐためエラーで中止します。
- **修正版のファイル**：同じ表（`table_ref` と調査年）の観測値を、1トランザクションで置き換えます。失敗した場合はロールバックされ、元のデータが残ります。
- **形式の確認**：次の項目が想定と完全に一致しない場合は、推測せずにエラーで中止します（DB は変更しません）。
  - 表題・注記（単一回答）・単位（‰）・表章項目・問題点の12項目名・軸の見出し
  - 業種コードの形式・重複・親業種との整合・階層
  - セルの値（数値または既知の記号）
- **集計の整合性**を確認し、問題があれば警告として表示します。
  - 12項目の合計が総数を超えないこと
  - 網羅的な内訳の合計が親業種の総数と一致すること
- 取り込みは記事・市場ラベル・ラベル・情報源のテーブルには触れません。

## 市場形成シグナル（第1層）の判定について

判定結果は、すべて**キーワード・ルールによる候補**です。

| strength | 意味 |
| --- | --- |
| `explicit`（画面：候補・明示記述） | 記事のタイトル・要約に、出来事を述べる明示的な記述がある。**独立に事実確認されたことを意味しない** |
| `inferred`（画面：候補・推測） | 関連語や投稿形式から、ルールで推測した候補 |

- タイトル・要約に根拠が無い記事には、ラベルを付けません（「未判定」）。
- キーワードが一致したことをもって、市場の成長やSaaS需要を確定するものではありません。

誤判定を減らすため、次の処理を行います。

- **否定・中止**：一致箇所の近くに否定・中止の表現があれば、ラベルを付けない
  - 英語：not, cancels, delayed, fails to, ends など
  - 日本語：中止, 延期, 解消, できず など
- **検討・提案・予定・目的**：inferred に下げる
  - 英語：plans to, call for, may, for expansion など
  - 日本語：検討, 予定, 見込み など
- **質問・解説・回顧の記事**：inferred に下げる
  - 英語：末尾の「?」, How, Why, Ask HN など
  - 日本語：とは, 解説, まとめ など
- **別の意味の語**は除外する
  - 英語：rocket launch, urban など
  - 日本語：参入障壁, 感染拡大 など

情報源ごとの扱いは次のとおりです。

- **Show HN**：製品・サービスの公開と読み取れる場合に限り `new_service` の inferred とする。名前とその説明、「I built」、app / tool などの語が手がかり。投稿形式だけでは判定しない
- **e-Gov パブリック・コメント**：意見募集・改正案を `regulation` の explicit とする
  - **改正案等の意見募集であり、成立・施行済みではありません**
  - この注記は、根拠と画面の両方に表示します

### 分析の実行（CLI）

```bash
cd backend
.venv/bin/python -m app.analyze              # 全件
.venv/bin/python -m app.analyze --limit 20   # 公開日時の新しい順に20件
.venv/bin/python -m app.analyze --dry-run    # DBに書き込まず結果だけ表示
```

- 表示する内容：分析件数、ラベルが付いた記事数、未判定の件数、付与したラベル数（種類・強さ別）、注意書き
- ニュース取得時に自動では実行しません。新しく取得した記事に対しては、CLI を再実行してください。
- **再計算**：同じ記事を再分析すると、その記事の `method='keyword'` の `signal_type` ラベルだけを置き換えます。
  - 重複はしません。ルールを変更した場合も、再実行すれば新しいルールで計算し直されます。
  - ルールを変更したら `signals.py` の `RULESET_VERSION` を更新してください。
  - 人手（`manual`）・AI（`ai`）の判定、記事、市場ラベルには触れません。
  - 全件を1トランザクションで処理するため、途中で失敗した場合は何も変更されません。

## 将来構想：3層の分析と5つの業務領域

このRadarの目的は、成長市場を見つけることだけではありません。**個人開発者が提供できるSaaSの事業機会**を見つけることを目指します。
分析は次の3層に分け、層ごとに「観測した事実」と「推測」を区別して扱います。

| 層 | 内容 | 状態 |
| --- | --- | --- |
| 第1層：市場形成シグナル | 新サービス、新規参入、提携、資金調達、事業拡大、新しい顧客層、規制 | **v0.4 で実装**（キーワード・ルールによる候補） |
| 第2層：顧客課題・ソフトウェア需要の兆候 | 5つの業務領域ごとの顧客課題（集客難、営業課題、手作業、請求漏れ、解約増加など） | 未実装 |
| 第3層：個人SaaS参入可能性 | 顧客の支払意思、開発規模、競合、販売経路、継続運用の負担 | 未実装 |

### 第2層の5つの業務領域（対等に扱う）

| 領域 | 対象 |
| --- | --- |
| Marketing | 認知拡大、集客、広告、SEO、SNS、顧客獲得 |
| Sales | 販路開拓、見込み客発掘、商談、提案、成約 |
| Operations | 業務管理、自動化、予約、在庫、人員配置 |
| Finance | 請求、入金、収益、価格、会計 |
| Customer Success | 顧客維持、継続利用、解約防止、リピート |

第2層・第3層の設計方針は次のとおりです。

- 5領域を対等に扱い、Operations に偏らないようにする。
- 特に、「商品・サービスはあるが、集客方法や販売方法が確立していない事業者」の課題（Marketing・Sales）を重要な分析対象とする。
- 第2層では、**記事に記載された顧客課題（観測）** と、**そこから推測したソフトウェア需要（仮説）** を区別する。
  - 既存の `article_labels` では、`signal_type` が観測、`software_domain` が仮説にあたる（`assertion_kind` で区別）。
  - 第2層の実装時に、顧客課題を観測として記録する taxonomy を追加する予定。
- 市場の新規性や成長性（第1層）だけで、SaaS需要（第2層）があると断定しない。
  - 第3層は、第2層の課題に対して、個人開発者が参入できるかを別途評価する。
- 各領域に適した情報源（例：集客・販売の課題が書かれやすい情報源）を後から追加できるよう、情報源は `backend/app/sources/` に独立したモジュールとして追加する。
  - 利用条件は `sources.commercial_use` で管理する。

## 未実装（今後の予定）

- 第2層（5領域の顧客課題・ソフトウェア需要）と、第3層（個人SaaS参入可能性）の分析
  - v0.5 では、その土台となる統計上の経営課題の取り込みと表示のみ
  - 統計上の課題を5領域へ対応づける処理（需要仮説）は、v0.6 以降で設計する
- e-Stat の他の年・他の表の取り込み（現在の CLI は、事業経営上の問題点別企業分布の表の形式だけに対応）
- AI による判定（`signal_type` / `software_domain`）
- `software_domain` の自動生成（現在、`software_domain` のラベルは1件もありません）
- ニュース取得後の自動分析（現在は CLI による手動実行）
- ラベルを登録・編集する HTTP API・画面（現在は内部関数のみ）
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
v0.1〜v0.4 のDBがある場合は、起動時に自動で最新のスキーマ（v5）へマイグレーションします（後述）。

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
統計の取り込みテストは、`backend/tests/data/` に保存した e-Stat の公式 xlsx（実データ）を使って、件数・階層・集計の整合性を確認します。

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
| GET | `/api/articles?market=home&category=AI&signal=funding&limit=100` | 記事一覧（公開日時の新しい順）。各記事に `signals`（signal_type ラベル）を含む |
| GET | `/api/signal-types` | 市場形成シグナル（signal_type）の一覧 |
| GET | `/api/problem-tables` | 取り込み済みの統計表（出典・調査対象・加工内容・注意事項を含む） |
| GET | `/api/problems?table_id=1&industry=6&problem=09` | 業種別の経営課題。省略時は最新の調査年。`industry` はその業種と配下に絞る |
| POST | `/api/fetch` | 全情報源から最新記事を取得・保存。`{fetched, inserted, errors}` を返す |

`market` パラメータは次のとおりです。

- 未指定：すべての記事
- `home`：日本市場（JP）を対象とする記事
- `overseas`：JP 以外の市場を対象とする記事

対象市場が未判定の記事は、`home` にも `overseas` にも含まれません。

`/api/problems` の返す値は、元データと算出値で項目名を分けています。

- 元データ：`value`（‰）、`symbol`（記号の原文）
- 本アプリの算出値：`computed_share_pct`、`unlisted.computed_value` など `computed_` で始まる項目

`signal` パラメータには `signal_type` のラベル（`new_service` など）を指定します。そのラベルを持つ記事だけに絞り込みます（判定方法は問いません）。未定義の値は 422 になります。

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
| `strength` | keyword 判定の強さ：`explicit`（記事に明示的な記述あり）／`inferred`（ルールによる推測）。他の method では NULL |
| `rule_version` | keyword 判定のルールの版（例：`kw-2026.10.1`） |

- 同じ記事・ラベル・判定主体・モデルの組は1件のみ登録できます。
- 人手の判定と AI の判定、AI のモデル違いは、別の行として残ります。

内部関数（`backend/app/labels.py`）は次のとおりです。

- `add_article_label(article_id, taxonomy, label, method, evidence=None, model=None) -> bool`：登録する。重複していれば `False`。不正な値は `ValueError`
- `list_article_labels(article_id, taxonomy=None, assertion_kind=None) -> list[dict]`：取得する。各行に `assertion_kind`・`strength`・`rule_version` を含む
- `replace_keyword_labels(conn, article_id, taxonomy, labels, rule_version)`：その記事の `method='keyword'` のラベルだけを置き換える（CLI が使用）

### `stat_tables` / `problem_observations`（v5）

統計データは記事とは別に管理します。

| テーブル | 内容 |
| --- | --- |
| `stat_tables` | 取り込んだ統計表。出典 URL・表題・調査年・作成機関・単位・回答形式（`single` / `multiple` / `unknown`）・注記・調査対象の注意（`scope_note`）・ファイルの SHA-256 を保持 |
| `problem_observations` | 表に書かれた値（業種 × 課題）。`value`（‰）または `value_symbol`（記号の原文）のどちらか一方を持つ（CHECK 制約） |

`problem_observations` の業種の列は次のとおりです。

- `industry_level`：表の階層
- `parent_code`：表の階層から決めた親業種
- `is_subset`：「うち〜」の内訳かどうか
- `row_order`：表での行の順序

業種内割合などの算出値は保存しません。5領域への対応づけ（需要仮説）は v0.6 以降で、観測とは別のテーブルに分けて設計します。

### マイグレーション

スキーマのバージョンは `PRAGMA user_version` で管理しています（`backend/app/db.py` の `MIGRATIONS`）。

- v0.1 のDB（`user_version = 0`）は、起動時に v2、v3 の順に移行します。各段階は1トランザクションで適用されます。
- v0.2 以降のDB は、未適用のバージョン（v3、v4、v5）を順に適用します。v5 は統計データ用のテーブルを追加するだけです。
- いずれの場合も、既存の記事・取得日時・市場ラベル・ラベルは保持されます。
- 既存記事の `language` は、情報源の定義から補完します。
- 移行ではラベルを追加しません。keyword のラベルは CLI を実行したときだけ作られます。

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
    signals.py       市場形成シグナルのキーワード・ルール
    analyze.py       シグナル判定の CLI
    estat_import.py  e-Stat 統計表（xlsx）の取り込み CLI
    problems.py      業種別経営課題の取得と算出（業種内割合など）
    sources/
      base.py        情報源インターフェース（RawArticle / FetchResult / Source）
      hackernews.py  Hacker News 取得処理
      egov_pcm.py    e-Govパブリック・コメント RSS 取得処理
  tests/             pytest
    data/            実データのテスト用ファイル（e-Stat の公式 xlsx を無加工で保存。出典は data/README.md）
frontend/
  src/app/page.tsx   ニュース一覧画面
  src/app/problems/page.tsx  業種別の経営課題画面
```
