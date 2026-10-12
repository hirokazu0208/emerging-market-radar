"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const DOMAINS = ["marketing", "sales", "operations", "finance", "customer_success"] as const;
const DOMAIN_NAMES: Record<string, string> = {
  marketing: "Marketing（集客・認知）",
  sales: "Sales（販路・営業）",
  operations: "Operations（業務運営）",
  finance: "Finance（請求・収益・会計）",
  customer_success: "Customer Success（顧客維持）",
};
const STATUS_NAMES: Record<string, string> = {
  unverified: "未検証",
  in_progress: "検証中",
  rejected: "棄却",
  validated: "検証で支持",
};
const KIND_NAMES: Record<string, string> = {
  problem_observation: "統計の観測",
  article: "ニュース記事",
  note: "メモ",
  interview: "ヒアリング",
  channel_test: "販売経路のテスト",
  payment_intent: "支払意思",
};
const RESULT_NAMES: Record<string, string> = { supports: "支持", refutes: "反証", inconclusive: "判断できず" };
const PAYMENT_NAMES: Record<string, string> = {
  preorder: "事前予約",
  paid: "支払い済み",
  signed_quote: "見積の合意",
  letter_of_intent: "購入意向書",
};
const TEXT_FIELDS: { key: string; label: string }[] = [
  { key: "title", label: "タイトル" },
  { key: "target_customer", label: "想定顧客" },
  { key: "problem_statement", label: "想定する課題" },
  { key: "solution_idea", label: "解決案" },
  { key: "assumptions", label: "前提条件（1行1項目）" },
  { key: "unknowns", label: "未確認事項（1行1項目）" },
  { key: "not_implied", label: "この仮説が示さないこと" },
  { key: "next_action", label: "次の検証行動" },
];

type Validation = {
  independent_supporting_contacts: number;
  required_independent_contacts: number;
  has_supporting_payment_intent: boolean;
  refuting_records: number;
  requirements_met: boolean;
};

type Hypothesis = {
  id: number;
  domain: string;
  status: string;
  status_reason: string | null;
  origin: "rule" | "manual";
  rule_key: string | null;
  rule_version: string | null;
  rule_retired: boolean;
  human_modified: boolean;
  created_at: string;
  updated_at: string;
  basis: { industry_code: string; problem_code: string; snapshot_label: string }[];
  validation: Validation;
} & Record<string, unknown>;

type Evidence = {
  id: number;
  kind: string;
  role: string;
  added_by: string;
  industry_code: string | null;
  problem_code: string | null;
  snapshot_label: string | null;
  snapshot_value: number | null;
  snapshot_symbol: string | null;
  snapshot_total: number | null;
  source_title: string | null;
  source_url: string | null;
  source_period: string | null;
  rule_version: string | null;
  contact_ref: string | null;
  result: string | null;
  occurred_on: string | null;
  payment_signal: string | null;
  method_note: string | null;
  description: string | null;
  url: string | null;
  created_at: string;
  retraction: { reason: string; created_at: string } | null;
  article?: { title: string; url: string } | null;
  current?: {
    status: "same" | "revised_same_value" | "changed" | "missing";
    value: number | null;
    symbol: string | null;
    total: number | null;
    newer_table: { period: string; title: string } | null;
  };
};

type HistoryEvent = {
  id: number;
  event: string;
  from_status: string | null;
  to_status: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
};

type Detail = Hypothesis & {
  evidence: Evidence[];
  events: HistoryEvent[];
  allowed_transitions: string[];
  validated_note: string;
};

type ListResponse = { hypotheses: Hypothesis[]; domain_counts: Record<string, number>; validated_note: string };
type Rules = { version: string; no_hypothesis: { problem_code: string; reason: string }[] };

const EVENT_NAMES: Record<string, string> = {
  created: "作成",
  rule_updated: "ルールで更新",
  rule_update_skipped: "ルールの更新を見送り（人の操作を保護）",
  rule_retired: "ルール廃止",
  edited: "編集",
  status_changed: "状態の変更",
  evidence_added: "根拠の追加",
  evidence_retracted: "根拠の取り消し",
  basis_added: "観測の紐付け",
};

const jst = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const formatTime = (iso: string) => `${jst.format(new Date(iso))} JST`;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body as T;
}

function permille(value: number | null, symbol: string | null) {
  return symbol ?? (value === null ? "—" : `${value}‰`);
}

// 業種内割合（本アプリの算出）＝ 項目の値 ÷ 同じ業種行の総数。元データの‰とは分母が異なる
function share(value: number | null, total: number | null): string {
  return value === null || total === null || total <= 0 ? "—" : `${((value / total) * 100).toFixed(1)}%`;
}

function observationNote(e: Evidence): string {
  const c = e.current;
  if (!c) return "";
  const parts: string[] = [];
  if (c.status === "missing") parts.push("現在の統計に該当する観測はありません");
  if (c.status === "changed")
    parts.push(`統計が更新されています（現在：業種内割合 ${share(c.value, c.total)}、元データ ${permille(c.value, c.symbol)}／総数 ${c.total ?? "—"}‰）`);
  if (c.status === "revised_same_value") parts.push("統計ファイルは更新されていますが、値は同じです");
  if (c.newer_table) parts.push(`より新しい統計（${c.newer_table.period}年）があります`);
  return parts.join("。");
}

function HypothesesContent() {
  const params = useSearchParams();
  const problem = params.get("problem") ?? "";
  const industry = params.get("industry") ?? "";
  const [domain, setDomain] = useState(params.get("domain") ?? "");
  const [status, setStatus] = useState("");
  const [origin, setOrigin] = useState("");
  const [list, setList] = useState<ListResponse | null>(null);
  const [rules, setRules] = useState<Rules | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    api<Rules>("/api/hypothesis-rules").then(setRules).catch(() => setRules(null));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const q = new URLSearchParams();
    if (domain) q.set("domain", domain);
    if (status) q.set("status", status);
    if (origin) q.set("origin", origin);
    if (problem) q.set("problem", problem);
    if (industry) q.set("industry", industry);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    setLoading(true);
    fetch(`${API_BASE}/api/hypotheses?${q}`, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as ListResponse;
      })
      .then((data) => {
        setList(data);
        setError(null);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setList(null);
        setError(`仮説の一覧を取得できませんでした。バックエンドが起動しているか確認してください。(${e})`);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [domain, status, origin, problem, industry, reloadKey]);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);
  const total = list ? Object.values(list.domain_counts).reduce((a, b) => a + b, 0) : 0;

  return (
    <main className="container wide">
      <header className="header">
        <div>
          <h1>SaaS需要仮説</h1>
          <p className="subtitle">経営課題を手がかりにした、検証のための仮説（第2層）</p>
        </div>
        <div className="header-actions">
          <Link href="/problems" className="nav-link">業種別の経営課題</Link>
          <Link href="/" className="nav-link">ニュース一覧</Link>
        </div>
      </header>

      <div className="caution">
        <strong>注意：</strong>
        ここにあるのは、人が検証するための<strong>仮説</strong>です。需要の存在・支払意思・収益化を示すものではありません。
        ルールが作った候補はすべて「未検証」から始まります。統計の値の大きさは、需要の強さを示しません。
        {list && <> {list.validated_note}</>}
      </div>

      <div className="tabs domain-tabs" role="group" aria-label="領域">
        <button className={`tab${domain === "" ? " active" : ""}`} onClick={() => setDomain("")}>
          すべて（{total}）
        </button>
        {DOMAINS.map((d) => (
          <button key={d} className={`tab${domain === d ? " active" : ""}`} onClick={() => setDomain(d)}>
            {DOMAIN_NAMES[d]}（{list?.domain_counts[d] ?? 0}）
          </button>
        ))}
      </div>

      <div className="toolbar">
        <label>
          状態：
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">すべて</option>
            {Object.entries(STATUS_NAMES).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          由来：
          <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
            <option value="">すべて</option>
            <option value="rule">ルールの候補</option>
            <option value="manual">手作業</option>
          </select>
        </label>
        {(problem || industry) && (
          <span className="signal-filter">
            {problem && `経営課題 ${problem}`} {industry && `業種 ${industry}`} の観測に基づく仮説に絞り込み中
            <Link href="/hypotheses" className="clear-button">× 解除</Link>
          </span>
        )}
        {!loading && list && <span className="count">{list.hypotheses.length} 件</span>}
      </div>

      {error && <div className="error">{error}</div>}
      {loading && !list ? (
        <p className="empty">読み込み中…</p>
      ) : list && list.hypotheses.length === 0 ? (
        <p className="empty">
          条件に該当する仮説がありません。
          {total === 0 && !problem && !industry && " backend で python -m app.hypothesize を実行すると、ルールによる候補が作られます。"}
        </p>
      ) : (
        <ul className="articles">
          {list?.hypotheses.map((h) => (
            <li key={h.id} className="article hypothesis">
              <div className="meta">
                <span className="badge">{DOMAIN_NAMES[h.domain]}</span>
                <span className={`status status-${h.status}`}>{STATUS_NAMES[h.status]}</span>
                <span>{h.origin === "rule" ? `ルールの候補（${h.rule_key}・${h.rule_version}）` : "手作業"}</span>
                {h.human_modified && h.origin === "rule" && (
                  <span className="tag" title="編集・状態変更・根拠の追加のいずれかを行ったため、ルールの再実行で上書きしません">
                    人が操作
                  </span>
                )}
                {h.rule_retired && <span className="tag tag-warn">ルール廃止</span>}
              </div>
              <div className="title">{String(h.title)}</div>
              <p className="summary">想定顧客：{String(h.target_customer)}</p>
              <p className="summary">次の検証行動：{String(h.next_action)}</p>
              <div className="signals">
                {h.basis.map((b) => (
                  <Link
                    key={`${b.industry_code}-${b.problem_code}`}
                    href={`/problems?industry=${b.industry_code}&problem=${b.problem_code}`}
                    className="basis-link"
                    title="出発点の観測を業種別の経営課題で見る"
                  >
                    {b.snapshot_label}
                  </Link>
                ))}
                <span className="signal-note">
                  支持した独立の顧客接点 {h.validation.independent_supporting_contacts}/{h.validation.required_independent_contacts}
                  {h.validation.has_supporting_payment_intent && "・支払意思の支持あり"}
                </span>
                <button className="clear-button" onClick={() => setOpenId(openId === h.id ? null : h.id)}>
                  {openId === h.id ? "閉じる" : "詳細・編集・検証記録"}
                </button>
              </div>
              {openId === h.id && <HypothesisDetail id={h.id} onChanged={refresh} />}
            </li>
          ))}
        </ul>
      )}

      {rules && (
        <section className="footer notes">
          <h2>仮説を作っていない経営課題（ルールセット {rules.version}）</h2>
          <ul>
            {rules.no_hypothesis.map((n) => (
              <li key={n.problem_code}>
                {n.problem_code}：{n.reason}
              </li>
            ))}
          </ul>
          <h2>「検証で支持」の条件</h2>
          <p className="attribution">
            別々の顧客接点から得た支持の記録が2件以上、または具体的な支払意思（事前予約・支払い・見積の合意・購入意向書）を示す支持の記録が1件以上必要です。
            統計・ニュース記事・メモは検証記録として数えません。同じ顧客との複数回の接点は、1つの接点として数えます。
            これは需要の証明ではなく、限定的な検証結果です。
          </p>
        </section>
      )}
    </main>
  );
}

function HypothesisDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  // resetForm：編集フォームを保存済みの内容に戻すか（状態変更・記録の追加では、保存していない編集を残す）
  const load = useCallback(
    async (resetForm: boolean) => {
      try {
        const d = await api<Detail>(`/api/hypotheses/${id}`);
        setDetail(d);
        if (resetForm) {
          setForm(Object.fromEntries([["domain", d.domain], ...TEXT_FIELDS.map((f) => [f.key, String(d[f.key] ?? "")])]));
        }
      } catch (e) {
        setError(`詳細を取得できませんでした。(${(e as Error).message})`);
      }
    },
    [id],
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    load(true);
  }, [load]);

  async function run(action: () => Promise<unknown>, done: string, resetForm = false) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(done);
      await load(resetForm);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !detail) return <div className="error">{error}</div>;
  if (!detail) return <p className="empty">読み込み中…</p>;

  const changed = Object.fromEntries(
    Object.entries(form).filter(([k, v]) => v !== String(detail[k] ?? "")),
  );

  return (
    <div className="detail">
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}

      <section>
        <h3>状態：{STATUS_NAMES[detail.status]}</h3>
        {detail.status_reason && <p className="attribution">直近の理由：{detail.status_reason}</p>}
        <p className="attribution">
          支持した独立の顧客接点 {detail.validation.independent_supporting_contacts}/
          {detail.validation.required_independent_contacts}、支払意思の支持
          {detail.validation.has_supporting_payment_intent ? "あり" : "なし"}、反証の記録 {detail.validation.refuting_records} 件
        </p>
        <textarea
          className="field"
          placeholder="状態を変える理由（棄却・検証で支持・再開・再検証では必須）。氏名や連絡先は書かないでください。"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="signals">
          {detail.allowed_transitions.map((s) => {
            const blocked = s === "validated" && !detail.validation.requirements_met;
            return (
              <button
                key={s}
                className="clear-button"
                disabled={busy || blocked}
                title={blocked ? "条件を満たしていません：別々の顧客接点からの支持記録2件以上、または支払意思の支持記録1件以上" : undefined}
                onClick={() =>
                  run(
                    () =>
                      api(`/api/hypotheses/${id}/status`, {
                        method: "POST",
                        body: JSON.stringify({ status: s, reason: reason || null }),
                      }).then(() => setReason("")),
                    `状態を「${STATUS_NAMES[s]}」に変更しました`,
                  )
                }
              >
                「{STATUS_NAMES[s]}」にする
              </button>
            );
          })}
        </div>
        {detail.allowed_transitions.includes("validated") && (
          <p className="attribution">{detail.validated_note}</p>
        )}
      </section>

      <section>
        <h3>仮説の内容</h3>
        {detail.origin === "rule" && (
          <p className="attribution">
            ルール {detail.rule_key}（{detail.rule_version}）の候補です。編集・状態変更・根拠の追加を行うと、ルールの再実行で上書きされなくなります。
          </p>
        )}
        <label className="field-label">
          領域
          <select value={form.domain ?? ""} onChange={(e) => setForm({ ...form, domain: e.target.value })}>
            {DOMAINS.map((d) => (
              <option key={d} value={d}>{DOMAIN_NAMES[d]}</option>
            ))}
          </select>
        </label>
        {TEXT_FIELDS.map((f) => (
          <label key={f.key} className="field-label">
            {f.label}
            <textarea
              className="field"
              value={form[f.key] ?? ""}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
            />
          </label>
        ))}
        {Object.keys(changed).length > 0 && <p className="attribution warn-text">保存していない変更があります。</p>}
        <button
          className="fetch-button"
          disabled={busy || Object.keys(changed).length === 0}
          onClick={() =>
            run(() => api(`/api/hypotheses/${id}`, { method: "PATCH", body: JSON.stringify(changed) }), "仮説を保存しました", true)
          }
        >
          変更を保存
        </button>
      </section>

      <section>
        <h3>根拠と検証記録</h3>
        <ul className="evidence-list">
          {detail.evidence.map((e) => (
            <EvidenceItem key={e.id} e={e} hypothesisId={id} canRetract={detail.status !== "validated"} run={run} />
          ))}
        </ul>
        <EvidenceForm hypothesisId={id} run={run} busy={busy} />
      </section>

      <details className="evidence">
        <summary>履歴（{detail.events.length}件）</summary>
        <ul>
          {detail.events.map((ev) => (
            <li key={ev.id}>
              {formatTime(ev.created_at)}　{EVENT_NAMES[ev.event] ?? ev.event}
              {ev.to_status && `：${STATUS_NAMES[ev.from_status ?? ""] ?? "—"} → ${STATUS_NAMES[ev.to_status]}`}
              {ev.detail && <div className="evidence-text">{JSON.stringify(ev.detail)}</div>}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function EvidenceItem({
  e,
  hypothesisId,
  canRetract,
  run,
}: {
  e: Evidence;
  hypothesisId: number;
  canRetract: boolean;
  run: (action: () => Promise<unknown>, done: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const isValidation = e.role === "validation";
  return (
    <li className={e.retraction ? "retracted" : ""}>
      <strong>{KIND_NAMES[e.kind] ?? e.kind}</strong>
      {e.role === "basis" && "（出発点の観測）"}
      {e.role === "contradicting" && "（反対の根拠）"}
      {!isValidation && e.kind !== "problem_observation" && e.role === "supporting" && "（補足の根拠・検証には数えない）"}
      {e.retraction && <span className="tag tag-warn">取り消し済み：{e.retraction.reason}</span>}
      {e.kind === "problem_observation" && (
        <div>
          {e.snapshot_label}：業種内割合 {share(e.snapshot_value, e.snapshot_total)}（本アプリが算出）
          <div className="attribution">
            元データの千分率 {permille(e.snapshot_value, e.snapshot_symbol)}／総数 {e.snapshot_total ?? "—"}‰
            （中分類以下は所属する大分類の企業全体を1000とした値のため、そのまま割合として読まないでください）。
            事業主が最も大きな問題点として1つ選んだ回答の割合であり、需要の強さではありません。
          </div>
          <div className="attribution">
            統計の調査年 {e.source_period}年・記録 {formatTime(e.created_at)}
            {e.rule_version && `・ルールセット ${e.rule_version}`}・
            <Link href={`/problems?industry=${e.industry_code}&problem=${e.problem_code}`}>業種別の経営課題で見る</Link>
            {e.source_url && (
              <>
                ・<a href={e.source_url} target="_blank" rel="noopener noreferrer">出典</a>
              </>
            )}
          </div>
          {observationNote(e) && <div className="attribution warn-text">{observationNote(e)}</div>}
        </div>
      )}
      {e.kind === "article" && e.article && (
        <div>
          <a href={e.article.url} target="_blank" rel="noopener noreferrer">{e.article.title}</a>
        </div>
      )}
      {isValidation && (
        <div>
          接点 {e.contact_ref}・{e.occurred_on}・結果：{RESULT_NAMES[e.result ?? ""]}
          {e.payment_signal && `・${PAYMENT_NAMES[e.payment_signal]}`}
          {e.method_note && `・${e.method_note}`}
        </div>
      )}
      {e.description && <div className="evidence-text">{e.description}</div>}
      {e.added_by === "manual" && !e.retraction && canRetract && (
        <div className="signals">
          <input className="field-inline" placeholder="取り消しの理由" value={reason} onChange={(ev) => setReason(ev.target.value)} />
          <button
            className="clear-button"
            disabled={!reason.trim()}
            onClick={() =>
              run(
                () =>
                  api(`/api/hypotheses/${hypothesisId}/evidence/${e.id}/retract`, {
                    method: "POST",
                    body: JSON.stringify({ reason }),
                  }),
                "根拠を取り消しました（記録は残ります）",
              )
            }
          >
            取り消す
          </button>
        </div>
      )}
    </li>
  );
}

function EvidenceForm({
  hypothesisId,
  run,
  busy,
}: {
  hypothesisId: number;
  run: (action: () => Promise<unknown>, done: string) => Promise<void>;
  busy: boolean;
}) {
  const empty = {
    kind: "interview",
    contact_ref: "",
    occurred_on: "",
    result: "supports",
    payment_signal: "preorder",
    method_note: "",
    description: "",
    url: "",
    article_id: "",
    role: "supporting",
  };
  const [f, setF] = useState(empty);
  const isValidation = ["interview", "channel_test", "payment_intent"].includes(f.kind);

  function payload() {
    const base = { kind: f.kind, description: f.description || null, url: f.url || null };
    if (isValidation) {
      return {
        ...base,
        contact_ref: f.contact_ref,
        occurred_on: f.occurred_on,
        result: f.result,
        method_note: f.method_note || null,
        payment_signal: f.kind === "payment_intent" ? f.payment_signal : null,
      };
    }
    if (f.kind === "article") return { ...base, article_id: Number(f.article_id), role: f.role };
    return { ...base, role: f.role };
  }

  return (
    <div className="evidence-form">
      <h4>根拠・検証記録を追加（追記のみ。後から編集・削除はできず、取り消しの記録だけ残せます）</h4>
      <label className="field-label">
        種類
        <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          <option value="interview">ヒアリング（検証記録）</option>
          <option value="channel_test">販売経路のテスト（検証記録）</option>
          <option value="payment_intent">支払意思（検証記録）</option>
          <option value="note">メモ（検証には数えない）</option>
          <option value="article">ニュース記事（検証には数えない）</option>
        </select>
      </label>
      {isValidation ? (
        <>
          <label className="field-label">
            顧客接点の識別子（匿名。例：C-001、LP-2026-10。氏名・メール・電話番号は不可）
            <input className="field-inline" value={f.contact_ref} onChange={(e) => setF({ ...f, contact_ref: e.target.value })} />
          </label>
          <label className="field-label">
            実施日
            <input
              type="date"
              max={new Date().toLocaleDateString("sv-SE")}
              value={f.occurred_on}
              onChange={(e) => setF({ ...f, occurred_on: e.target.value })}
            />
          </label>
          <label className="field-label">
            結果
            <select value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}>
              {Object.entries(RESULT_NAMES).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          {f.kind === "payment_intent" && (
            <label className="field-label">
              支払意思の種類
              <select value={f.payment_signal} onChange={(e) => setF({ ...f, payment_signal: e.target.value })}>
                {Object.entries(PAYMENT_NAMES).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
          )}
          <label className="field-label">
            検証方法の補足（任意）
            <input className="field-inline" value={f.method_note} onChange={(e) => setF({ ...f, method_note: e.target.value })} />
          </label>
        </>
      ) : (
        <>
          {f.kind === "article" && (
            <label className="field-label">
              記事ID
              <input className="field-inline" value={f.article_id} onChange={(e) => setF({ ...f, article_id: e.target.value })} />
            </label>
          )}
          <label className="field-label">
            根拠の向き
            <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
              <option value="supporting">補足の根拠</option>
              <option value="contradicting">反対の根拠</option>
            </select>
          </label>
        </>
      )}
      <label className="field-label">
        {isValidation ? "観察内容（必須）" : "内容"}（個人を特定できる情報は書かないでください）
        <textarea className="field" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </label>
      <button
        className="fetch-button"
        disabled={busy}
        onClick={() =>
          run(
            () =>
              api(`/api/hypotheses/${hypothesisId}/evidence`, { method: "POST", body: JSON.stringify(payload()) }).then(() =>
                setF({ ...empty, kind: f.kind }),
              ),
            "記録を追加しました",
          )
        }
      >
        記録を追加
      </button>
    </div>
  );
}

export default function HypothesesPage() {
  // useSearchParams を使うため、Suspense の境界で囲む（Next.js の推奨）
  return (
    <Suspense fallback={<p className="empty">読み込み中…</p>}>
      <HypothesesContent />
    </Suspense>
  );
}
