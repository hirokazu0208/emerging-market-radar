# Emerging Market Radar v0.6

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

v0.6（第2層：SaaS需要仮説の管理）

- 統計上の経営課題から、5領域の需要仮説の候補をルールで作る CLI（`python -m app.hypothesize`）
  - 13件、すべて「未検証」で作成
  - ルールの定義：`backend/app/hypothesis_rules.py`
- 仮説の編集、状態の管理（未検証・検証中・棄却・検証で支持）、根拠と検証記録の追記・取り消し、履歴
  - 仮説・根拠・検証記録・履歴は、統計・記事とは別のテーブルに保存（v5 → v6 マイグレーション）
- 「検証で支持」の条件を、API と DB（トリガー・CHECK）の両方で強制
- `/hypotheses` 画面と API
- `/problems` との相互リンク
  - 経営課題の見出し →「仮説を見る」
  - 仮説の根拠 → 業種・課題を指定して `/problems` を表示

## SaaS需要仮説（v0.6）

**仮説は、人が検証するための作業単位です。需要の存在・支払意思・収益化を示すものではありません。**
観測（統計・記事）と仮説は別のテーブルで管理し、元の統計データは変更しません。

### ルールによる候補

| 項目 | 内容 |
| --- | --- |
| ルールセット | `hyp-2026.10.1`（`backend/app/hypothesis_rules.py`） |
| 件数 | 13件。Marketing 2、Sales 3、Operations 2、Finance 3、Customer Success 3 |
| 1つの課題から複数の仮説 | 可（例：「需要の停滞」→ Marketing・Sales・Customer Success） |
| 仮説を作らない課題 | 04 建物・設備、08 後継者難、11 家賃・地代。理由をルールに明記し、画面にも表示 |
| 必須の項目 | 想定顧客、想定する課題（推測であることを明記）、解決案、前提条件、未確認事項、この仮説が示さないこと、次の検証行動 |
| 出発点の観測 | ルールで固定した業種の観測を、根拠として紐付ける。観測値の大きさで業種を選んだり、仮説を順位付けしたりはしない |

領域ごとの件数を揃えるために、仮説を追加することはしません。

### CLI

```bash
cd backend
.venv/bin/python -m app.hypothesize            # 候補を作成・更新
.venv/bin/python -m app.hypothesize --dry-run  # DBに書き込まず結果だけ表示
```

再実行しても重複しません。既存の仮説の扱いは次のとおりです。

| 既存の仮説 | 再実行時の扱い |
| --- | --- |
| ない | 「未検証」で作成する |
| ルールのまま（人が操作しておらず、未検証） | 新しいルールの文面で更新する |
| 人が操作した（編集・状態の変更・根拠の追加のいずれか。状態を「未検証」に戻した場合も含む） | **上書きしない**。ルールの新しい文面との差分を、履歴「ルールの更新を見送り」に残す（版ごとに1回） |
| ルールが廃止された | 削除しない。「ルール廃止」と表示する |

手作業で作った仮説は、ルールの対象になりません。

### 識別子・版・調査年・記録日時の区別

- 仮説には、ルールの識別子（`rule_key`）と、作成・最終更新したルールセットの版（`rule_version`）を保存します。
- 根拠の観測には、次の情報を保存します。
  - 統計の調査年（`source_period`）
  - 統計ファイルのハッシュ（`source_sha256`）
  - 紐付けたルールセットの版（`rule_version`）
  - 記録日時（`created_at`）
  - 紐付けた時点の値の写し
- **統計が更新された場合**（修正版の取り込み、新しい年の統計）：
  - 古い根拠は残したまま、再実行時に新しい根拠を追加します。
  - 画面では、古い根拠と今の値の差、新しい調査年があることを表示します。
  - 人が操作した仮説の文面と、手動の検証記録は変更しません。

### 状態と「検証で支持」の条件

| 現在の状態 | 変更できる先 |
| --- | --- |
| 未検証 | 検証中、棄却 |
| 検証中 | 検証で支持、棄却、未検証 |
| 棄却 | 未検証（再開） |
| 検証で支持 | 検証中（再検証） |

棄却・検証で支持への変更と、そこからの変更には、新しい理由が必要です。

**「検証で支持」の条件**は次のとおりです（API と DB の両方で強制）。

- 別々の顧客接点から得た支持（`supports`）の記録が **2件以上**、または
- 具体的な支払意思（事前予約・支払い・見積の合意・購入意向書）を示す支持の記録が **1件以上**

数え方の注意：

- 統計・ニュース記事・メモは、検証記録として数えません。
- 同じ顧客との複数回の接点（同じ `contact_ref`）は、1つの接点として数えます。
- 取り消した記録は数えません。

**これは需要の証明ではなく、記録された接点での限定的な検証結果です。**

### 検証記録と個人情報

- 検証記録には、実施日、顧客接点の識別子、検証方法（種類と補足）、結果、観察内容を保存します。
- **顧客接点の識別子は匿名にします**（例：`C-001`、`LP-2026-10`）。
  - 英数字・ハイフン・下線の32文字以内に制限しています（DB の CHECK 制約）。
  - 大文字にそろえて保存し、`c-001` と `C-001` は同じ接点として数えます。
  - 氏名や連絡先は必須にせず、入力もできません。
- 仮説の文面・観察内容・補足・URL・理由に、メールアドレスや電話番号らしい文字列があると、保存を拒否します。自由記述は5,000文字までです。
  - ただし、それ以外の個人情報は自動で判別できないため、入力時に注意してください。
- 根拠・検証記録は**追記のみ**です（更新・削除は DB のトリガーで拒否）。
  - 誤りは「取り消し」の記録として残し、検証の条件には数えません。
  - 「検証で支持」の仮説の根拠は取り消せません。先に状態を「検証中」に戻してください。
  - 記録の更新・削除で、「検証で支持」の条件が崩れることはありません。
- 仮説は削除せず、棄却で表します。

### ローカル専用の API

書き込み用の API（仮説の編集・状態の変更・検証記録の追加など）には認証がありません。**ローカルでの利用専用**です。

- バックエンドは `127.0.0.1` で起動してください（README の起動コマンドの既定）。`--host 0.0.0.0` などで外部に公開しないでください。
- `Host` ヘッダーは `localhost` と `127.0.0.1` だけを受け付けます。DNS リバインディングで、外部のページから読み書きされることを防ぐためです。
  - 環境変数 `RADAR_ALLOWED_HOSTS` で変更できます（カンマ区切り）。
- 書き込み系のリクエスト（POST・PATCH など）は、`Origin` がローカル（`http://localhost:*`・`http://127.0.0.1:*`）の場合、または `Origin` が無い場合（CLI 等）だけ受け付けます。
- CORS も、ローカルのオリジンだけを許可しています。
- 外部に公開する場合は、事前に認証の追加が必要です。

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

- 第3層（個人SaaS参入可能性：開発規模・競合・販売経路・継続運用の負担）の評価
  - v0.6 の支払意思・販売経路のテストの記録が、その入り口になる
- AI による仮説の生成・評価、事業性のスコアリングや順位付け
- ニュース記事から仮説を作るルール（記事は人が根拠として紐付けることのみ可能）
- 認証。書き込み用の API はローカルでの利用（CORS は localhost のみ）を前提としており、外部に公開する前に認証が必要
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
v0.1〜v0.5 のDBがある場合は、起動時に自動で最新のスキーマ（v6）へマイグレーションします（後述）。

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
| GET | `/api/hypotheses?domain=sales&status=unverified&origin=rule&problem=12&industry=42` | 需要仮説の一覧。`domain_counts` は領域以外の条件で数えた5領域の件数（0件を含む） |
| GET | `/api/hypotheses/{id}` | 仮説の詳細。根拠（写しと今の値の比較）・取り消し・履歴・変更できる状態・検証の進み具合を含む |
| POST | `/api/hypotheses` | 手作業で仮説を作成（必ず「未検証」） |
| PATCH | `/api/hypotheses/{id}` | 仮説の文面・領域を編集（以後、ルールの再実行で上書きしない） |
| POST | `/api/hypotheses/{id}/status` | 状態の変更（`{status, reason}`）。条件を満たさない場合は 409 |
| POST | `/api/hypotheses/{id}/evidence` | 根拠・検証記録の追記（記事・メモ・統計の観測・ヒアリング・販売経路のテスト・支払意思） |
| POST | `/api/hypotheses/{id}/evidence/{evidence_id}/retract` | 根拠の取り消し（記録は残る） |
| GET | `/api/hypothesis-rules` | ルールの一覧と、仮説を作らない課題とその理由 |
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

業種内割合などの算出値は保存しません。5領域への対応づけ（需要仮説）は、観測とは別のテーブル（v6）で管理します。

### `hypotheses` / `hypothesis_evidence` / `hypothesis_evidence_retractions` / `hypothesis_events`（v6）

| テーブル | 内容 |
| --- | --- |
| `hypotheses` | 需要仮説。領域・想定顧客・課題・解決案・前提条件・未確認事項・示さないこと・次の検証行動、状態と理由、由来（`rule` / `manual`）、ルールの識別子と版、人が操作したか（`human_modified`：編集・状態の変更・根拠の追加） |
| `hypothesis_evidence` | 根拠・検証記録（追記のみ）。種類、向き（`basis` / `supporting` / `contradicting` / `validation`）、統計の観測の写しと出典、記事、検証記録（匿名の接点・実施日・結果・支払意思の種類・観察内容） |
| `hypothesis_evidence_retractions` | 根拠の取り消し（追記のみ）。理由と日時 |
| `hypothesis_events` | 履歴（作成・編集・状態変更・ルールによる更新と見送り・廃止・根拠の追加と取り消し） |

DB 側では、トリガーで次のことを強制しています。

- 作成時は「未検証」のみ
- 許可された状態遷移のみ
- 理由が必要な変更での理由の有無
- 「検証で支持」の条件
- 根拠・取り消し・履歴は追記のみ（仮説も削除不可）
- 「検証で支持」の仮説の根拠は取り消し不可

### マイグレーション

スキーマのバージョンは `PRAGMA user_version` で管理しています（`backend/app/db.py` の `MIGRATIONS`）。

- v0.1 のDB（`user_version = 0`）は、起動時に v2、v3 の順に移行します。各段階は1トランザクションで適用されます。
- v0.2 以降のDB は、未適用のバージョン（v3〜v6）を順に適用します。v5・v6 は新しいテーブルを追加するだけで、既存のテーブルは変更しません。
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
    hypothesis_rules.py  需要仮説のルール（13件）と、仮説を作らない課題
    hypotheses.py    需要仮説の生成・編集・状態管理・根拠の記録
    hypothesize.py   需要仮説の候補を作る CLI
    sources/
      base.py        情報源インターフェース（RawArticle / FetchResult / Source）
      hackernews.py  Hacker News 取得処理
      egov_pcm.py    e-Govパブリック・コメント RSS 取得処理
  tests/             pytest
    data/            実データのテスト用ファイル（e-Stat の公式 xlsx を無加工で保存。出典は data/README.md）
frontend/
  src/app/page.tsx   ニュース一覧画面
  src/app/problems/page.tsx  業種別の経営課題画面
  src/app/hypotheses/page.tsx  SaaS需要仮説の画面
```
