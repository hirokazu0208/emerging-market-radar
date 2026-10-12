"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const DOMAIN_NAMES: Record<string, string> = {
  marketing: "Marketing",
  sales: "Sales",
  operations: "Operations",
  finance: "Finance",
  customer_success: "Customer Success",
};
const STATE_NAMES: Record<string, string> = {
  not_researched: "未調査",
  researching: "調査中",
  supported: "根拠あり",
  contradicted: "反証あり",
};
const HYP_STATUS: Record<string, string> = {
  unverified: "未検証",
  in_progress: "検証中",
  rejected: "棄却",
  validated: "検証で支持",
};
const ACTIVITY_TYPES: Record<string, string> = {
  interview: "ヒアリング（顧客接点）",
  sales_experiment: "販売実験（顧客接点）",
  competitor_research: "競合調査（机上の調査）",
  technical_check: "技術検証（机上の調査）",
};
const ACTIVITY_STATUS: Record<string, string> = {
  planned: "予定",
  in_progress: "実施中",
  done: "完了",
  cancelled: "中止",
};
const OUTCOMES: Record<string, string> = { supports: "支持", refutes: "反証", inconclusive: "判断できず" };
const EVIDENCE_KINDS: Record<string, string> = {
  interview: "ヒアリング",
  channel_test: "販売経路のテスト",
  payment_intent: "支払意思",
};
const PAYMENT_NAMES: Record<string, string> = {
  letter_of_intent: "購入意向書",
  signed_quote: "見積の合意",
  preorder: "事前予約",
  paid: "支払い済み",
};
const TIMING_NAMES: Record<string, string> = {
  pre_registered: "事前に基準を定めた検証",
  post_hoc: "事後評価",
  mixed: "一部が事後評価",
  unplanned: "計画外の記録（事後評価）",
  before_start: "開始前の記録（事後）",
  after_start: "開始後の記録",
};

type Aspect = {
  key: string;
  name: string;
  question: string;
  supported_means: string;
  contradicted_means: string;
  allowed_evidence: string[];
  allowed_activities: string[];
};
type AspectsResponse = {
  aspects: Aspect[];
  payment_signals: Record<string, string>;
  mode_notes: Record<string, string>;
  disclaimer: string;
};
type Ref = {
  type: "evidence" | "activity";
  id: number;
  kind: string;
  result: string | null;
  retracted: boolean;
  timing: string | null;
  timing_note: string | null;
  decision_criteria?: string | null;
  contact_ref?: string | null;
  payment_signal?: string | null;
  description?: string | null;
};
type Assessment = {
  id: number;
  aspect: string;
  state: string;
  basis: "evidence" | "assumption";
  human_judgement: { observed_facts: string | null; relevance: string | null; judgement_reason: string | null; assumption_note: string | null };
  mechanical_checks: { retracted_references_now: number; note: string };
  refs: Ref[];
  post_hoc: boolean;
  post_hoc_note: string | null;
  warnings: string[];
  needs_reevaluation: boolean;
  committed_at: string;
};
type Payment = {
  supporting_by_signal: Record<string, number>;
  refuting: number;
  actual_payments: number;
  signal_meanings: Record<string, string>;
  note: string;
};
type Validation = { independent_supporting_contacts: number; required_independent_contacts: number; has_supporting_payment_intent: boolean };
type Row = {
  id: number;
  domain: string;
  title: string;
  target_customer: string;
  status: string;
  rule_key: string | null;
  assessments: Record<string, Assessment | null>;
  activity_counts: Record<string, number>;
  validation: Validation;
  payment: Payment;
  selection: { selected: boolean; reason: string | null };
  warnings: string[];
};
type ListResponse = { opportunities: Row[]; selected_count: number; max_selections: number; disclaimer: string };
type Revision = { id: number; revision_no: number; purpose: string; target: string; decision_criteria: string; planned_on: string | null; change_reason: string | null; created_at: string };
type Activity = {
  id: number;
  activity_type: string;
  status: string;
  is_customer_contact: boolean;
  revisions: Revision[];
  current_revision: Revision | null;
  locked_revision: Revision | null;
  result: { outcome: string; observed_facts: string; interpretation: string; judgement_reason: string } | null;
  sources: { id: number; url: string; title: string | null; accessed_on: string }[];
  linked_evidence: { id: number; kind: string; result: string; contact_ref: string; payment_signal: string | null; occurred_on: string; timing: string; retracted: boolean }[];
  mode: string | null;
  mode_note: string | null;
  warnings: string[];
  cancel_reason: string | null;
  started_at: string | null;
};
type Candidate = {
  id: number;
  kind: string;
  result: string | null;
  contact_ref?: string | null;
  payment_signal?: string | null;
  occurred_on?: string | null;
  created_at?: string;
  mode?: string | null;
};
type Detail = {
  hypothesis: { id: number; domain: string; title: string; target_customer: string; status: string; next_action: string };
  assessments: Record<string, Assessment | null>;
  assessment_history: Record<string, Assessment[]>;
  activities: Activity[];
  ref_candidates: Record<string, { evidence: Candidate[]; activities: Candidate[] }>;
  linkable_evidence: Candidate[];
  selections: { id: number; action: string; reason: string; created_at: string }[];
  selection: { selected: boolean; reason: string | null };
  payment: Payment;
  validation: Validation;
  warnings: string[];
};

const jst = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const formatTime = (iso: string) => `${jst.format(new Date(iso))} JST`;
const today = () => new Date().toLocaleDateString("sv-SE");

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: init?.body ? { "Content-Type": "application/json" } : undefined });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body as T;
}

function paymentText(p: Payment): string {
  const parts = Object.entries(PAYMENT_NAMES).map(([k, v]) => `${v} ${p.supporting_by_signal[k] ?? 0}`);
  return `${parts.join("・")}（実際の支払い ${p.actual_payments} 件）`;
}

function StateCell({ a }: { a: Assessment | null }) {
  if (!a) return <span className="state state-none">未記録</span>;
  return (
    <span className={`state state-${a.state}${a.needs_reevaluation ? " state-stale" : ""}`} title={a.warnings.join("\n") || undefined}>
      {a.needs_reevaluation && "⚠ "}
      {STATE_NAMES[a.state]}
      <span className="cell-sub">
        {a.basis === "evidence" ? "記録に基づく" : "推測"}
        {a.post_hoc && "・事後評価を含む"}
      </span>
    </span>
  );
}

function OpportunitiesContent() {
  const params = useSearchParams();
  const [aspects, setAspects] = useState<AspectsResponse | null>(null);
  const [list, setList] = useState<ListResponse | null>(null);
  const [domain, setDomain] = useState("");
  const [onlySelected, setOnlySelected] = useState(false);
  const [openId, setOpenId] = useState<number | null>(() => Number(params.get("hypothesis")) || null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    api<AspectsResponse>("/api/opportunity-aspects").then(setAspects).catch(() => setAspects(null));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const q = domain ? `?domain=${domain}` : "";
    fetch(`${API_BASE}/api/opportunities${q}`, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as ListResponse;
      })
      .then((d) => {
        setList(d);
        setError(null);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(`一覧を取得できませんでした。バックエンドが起動しているか確認してください。(${e})`);
      });
    return () => controller.abort();
  }, [domain, reloadKey]);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);
  const rows = (list?.opportunities ?? []).filter((r) => !onlySelected || r.selection.selected);
  const selected = (list?.opportunities ?? []).filter((r) => r.selection.selected);

  return (
    <main className="container wide">
      <header className="header">
        <div>
          <h1>事業機会の比較・選定</h1>
          <p className="subtitle">13件の需要仮説を6観点で比較し、優先して調査する候補（最大3件）と検証計画を管理する</p>
        </div>
        <div className="header-actions">
          <Link href="/hypotheses" className="nav-link">SaaS需要仮説</Link>
          <Link href="/" className="nav-link">ニュース一覧</Link>
        </div>
      </header>

      <div className="caution">
        <strong>注意：</strong>
        {list?.disclaimer ?? "点数・順位はありません。"}
        「根拠あり」は事業化に有利な方向の根拠、「反証あり」は不利な方向の根拠がある、という意味です（6観点すべて同じ）。
        需要・収益化を示すものではありません。
      </div>

      <section className="selection-panel">
        <h2>
          優先調査候補（{list?.selected_count ?? 0}/{list?.max_selections ?? 3}）
        </h2>
        {selected.length === 0 ? (
          <p className="attribution">まだ選定していません。比較表の行を開き、理由を記録して選んでください（自動では選びません）。</p>
        ) : (
          <ul>
            {selected.map((r) => (
              <li key={r.id}>
                <button className="link-button" onClick={() => setOpenId(r.id)}>#{r.id} {r.title}</button>
                <div className="attribution">理由：{r.selection.reason}</div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="toolbar">
        <label>
          領域：
          <select value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">すべて</option>
            {Object.entries(DOMAIN_NAMES).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} /> 選定中のみ
        </label>
        <span className="count">{rows.length} 件（ID順。点数による並べ替えはありません）</span>
      </div>

      {error && <div className="error">{error}</div>}

      {list && (
        <div className="table-scroll">
          <table className="problems-table opportunities-table">
            <thead>
              <tr>
                <th className="sticky-col">仮説</th>
                {aspects?.aspects.map((a) => (
                  <th key={a.key} title={`${a.question}\n根拠あり：${a.supported_means}\n反証あり：${a.contradicted_means}`}>{a.name}</th>
                ))}
                <th>支払意思の記録</th>
                <th>計画</th>
                <th>v0.6 の検証</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={openId === r.id ? "row-open" : ""}>
                  <th className="sticky-col industry-cell">
                    <button className="link-button" onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                      #{r.id} {r.title}
                    </button>
                    <span className="tags">
                      <span className="tag">{DOMAIN_NAMES[r.domain]}</span>
                      <span className="tag">{HYP_STATUS[r.status]}</span>
                      {r.selection.selected && <span className="tag tag-selected">選定中</span>}
                      {r.warnings.length > 0 && <span className="tag tag-warn" title={r.warnings.join("\n")}>⚠</span>}
                    </span>
                  </th>
                  {aspects?.aspects.map((a) => (
                    <td key={a.key}>
                      <StateCell a={r.assessments[a.key]} />
                    </td>
                  ))}
                  <td className="cell-sub">{paymentText(r.payment)}</td>
                  <td className="cell-sub">
                    予定 {r.activity_counts.planned}・実施中 {r.activity_counts.in_progress}・完了 {r.activity_counts.done}
                  </td>
                  <td className="cell-sub">
                    接点 {r.validation.independent_supporting_contacts}/{r.validation.required_independent_contacts}
                    {r.validation.has_supporting_payment_intent && "・支払意思あり"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openId !== null && aspects && (
        <OpportunityDetail key={openId} id={openId} aspects={aspects} onChanged={refresh} onClose={() => setOpenId(null)} />
      )}

      {aspects && (
        <section className="footer notes">
          <h2>6観点の意味</h2>
          <ul>
            {aspects.aspects.map((a) => (
              <li key={a.key}>
                <strong>{a.name}</strong>：{a.question}。根拠あり＝{a.supported_means}／反証あり＝{a.contradicted_means}
              </li>
            ))}
          </ul>
          <h2>支払意思の種類（強さを点数にまとめていません）</h2>
          <ul>
            {Object.entries(aspects.payment_signals).map(([k, v]) => (
              <li key={k}>{v}</li>
            ))}
          </ul>
          <h2>事前に基準を定めた検証と事後評価</h2>
          <ul>
            {Object.entries(aspects.mode_notes).map(([k, v]) => (
              <li key={k}>
                {TIMING_NAMES[k]}：{v}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

function OpportunityDetail({ id, aspects, onChanged, onClose }: { id: number; aspects: AspectsResponse; onChanged: () => void; onClose: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selReason, setSelReason] = useState("");

  const load = useCallback(async () => {
    try {
      setD(await api<Detail>(`/api/opportunities/${id}`));
    } catch (e) {
      setError(`詳細を取得できませんでした。(${(e as Error).message})`);
    }
  }, [id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部APIとの同期のための取得
    load();
  }, [load]);

  const run = useCallback(
    async (action: () => Promise<unknown>, done: string) => {
      setBusy(true);
      setError(null);
      setMessage(null);
      try {
        await action();
        setMessage(done);
        await load();
        onChanged();
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load, onChanged],
  );

  if (!d) return <section className="detail-panel">{error ? <div className="error">{error}</div> : <p className="empty">読み込み中…</p>}</section>;
  const readOnly = d.hypothesis.status === "rejected";

  return (
    <section className="detail-panel">
      <div className="header">
        <h2>
          #{d.hypothesis.id} {d.hypothesis.title}
        </h2>
        <div className="header-actions">
          <Link href="/hypotheses" className="nav-link">仮説の詳細（/hypotheses）</Link>
          <button className="clear-button" onClick={onClose}>閉じる</button>
        </div>
      </div>
      <p className="attribution">
        想定顧客：{d.hypothesis.target_customer}／仮説の状態：{HYP_STATUS[d.hypothesis.status]}（v0.6 の条件で決まり、ここでの評価では変わりません）
      </p>
      {d.warnings.map((w) => (
        <div key={w} className="error">⚠ {w}</div>
      ))}
      {message && <div className="notice">{message}</div>}
      {error && <div className="error">{error}</div>}

      <div className="detail">
        <section>
          <h3>優先調査候補の選定</h3>
          <p className="attribution">
            {d.selection.selected ? `選定中（理由：${d.selection.reason}）` : "選定していません"}。選定は調査の順番を決めるもので、事業性の判断ではありません。
          </p>
          {!readOnly && (
            <>
              <textarea
                className="field"
                placeholder={d.selection.selected ? "選定を外す理由（必須）" : "この候補を優先して調べる理由（必須）"}
                value={selReason}
                onChange={(e) => setSelReason(e.target.value)}
              />
              <button
                className="fetch-button"
                disabled={busy || !selReason.trim()}
                onClick={async () => {
                  const ok = await run(
                    () =>
                      api(`/api/opportunities/${id}/${d.selection.selected ? "unselect" : "select"}`, {
                        method: "POST",
                        body: JSON.stringify({ reason: selReason }),
                      }),
                    d.selection.selected ? "選定を外しました" : "優先調査候補に選びました",
                  );
                  if (ok) setSelReason("");
                }}
              >
                {d.selection.selected ? "選定を外す" : "調査候補に選ぶ"}
              </button>
            </>
          )}
          {d.selections.length > 0 && (
            <details className="evidence">
              <summary>選定の履歴（{d.selections.length}件）</summary>
              <ul>
                {d.selections.map((s) => (
                  <li key={s.id}>
                    {formatTime(s.created_at)}　{s.action === "select" ? "選定" : "解除"}：{s.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>

        <section>
          <h3>支払意思の記録（種類ごと）</h3>
          <p className="attribution">{paymentText(d.payment)}・反証 {d.payment.refuting} 件。{d.payment.note}</p>
        </section>

        <section>
          <h3>6観点の評価</h3>
          {aspects.aspects.map((a) => (
            <AspectCard key={a.key} aspect={a} d={d} hypothesisId={id} readOnly={readOnly} run={run} busy={busy} />
          ))}
        </section>

        <section>
          <h3>検証計画</h3>
          <p className="attribution">
            顧客接点の結果（ヒアリング・販売実験）は /hypotheses の検証記録として記録し、ここで計画の結果に紐付けます。
            競合調査・技術検証の結果は計画の結果として保存し、顧客接点の記録には数えません。
            開始すると、その時点の目的・対象・判断基準が固定され、以後は訂正できません。
          </p>
          {d.activities.map((a) => (
            <ActivityCard key={a.id} a={a} d={d} run={run} busy={busy} readOnly={readOnly} />
          ))}
          {!readOnly && <ActivityForm hypothesisId={id} run={run} busy={busy} />}
        </section>
      </div>
    </section>
  );
}

function AspectCard({
  aspect,
  d,
  hypothesisId,
  readOnly,
  run,
  busy,
}: {
  aspect: Aspect;
  d: Detail;
  hypothesisId: number;
  readOnly: boolean;
  run: (action: () => Promise<unknown>, done: string) => Promise<boolean>;
  busy: boolean;
}) {
  const latest = d.assessments[aspect.key];
  const history = d.assessment_history[aspect.key] ?? [];
  const candidates = d.ref_candidates[aspect.key];
  const empty = { state: "researching", basis: "assumption", observed_facts: "", relevance: "", judgement_reason: "", assumption_note: "" };
  const [f, setF] = useState(empty);
  const [evidenceIds, setEvidenceIds] = useState<number[]>([]);
  const [activityIds, setActivityIds] = useState<number[]>([]);
  const [open, setOpen] = useState(false);
  const needsEvidence = f.state === "supported" || f.state === "contradicted";
  const basis = needsEvidence ? "evidence" : f.state === "not_researched" ? "assumption" : f.basis;
  const toggle = (list: number[], set: (v: number[]) => void, v: number) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <div className="aspect-card">
      <div className="meta">
        <strong>{aspect.name}</strong>
        <StateCell a={latest} />
        <span className="attribution">{aspect.question}</span>
      </div>
      {latest && (
        <div className="assessment">
          {latest.warnings.map((w) => (
            <div key={w} className="attribution warn-text">⚠ {w}</div>
          ))}
          {latest.post_hoc_note && <div className="attribution warn-text">{latest.post_hoc_note}</div>}
          <div className="two-col">
            <div>
              <h4>人の判断</h4>
              {latest.basis === "evidence" ? (
                <ul>
                  <li>観察された事実：{latest.human_judgement.observed_facts}</li>
                  <li>観点との関係：{latest.human_judgement.relevance}</li>
                  <li>判断の理由：{latest.human_judgement.judgement_reason}</li>
                </ul>
              ) : (
                <p>推測：{latest.human_judgement.assumption_note}</p>
              )}
            </div>
            <div>
              <h4>機械的な確認</h4>
              <ul>
                {latest.refs.map((r) => (
                  <li key={`${r.type}-${r.id}`} className={r.retracted ? "retracted" : ""}>
                    {r.type === "evidence" ? `記録 #${r.id} ${EVIDENCE_KINDS[r.kind] ?? r.kind}` : `計画 #${r.id} ${ACTIVITY_TYPES[r.kind]}`}・
                    {OUTCOMES[r.result ?? ""] ?? "—"}
                    {r.payment_signal && `・${PAYMENT_NAMES[r.payment_signal]}`}
                    {r.retracted && "・取り消し済み"}
                    {r.timing && `・${TIMING_NAMES[r.timing]}`}
                  </li>
                ))}
                {latest.refs.length === 0 && <li>参照なし（推測）</li>}
              </ul>
              <p className="attribution">{latest.mechanical_checks.note}</p>
            </div>
          </div>
          <p className="attribution">確定 {formatTime(latest.committed_at)}</p>
        </div>
      )}
      {history.length > 1 && (
        <details className="evidence">
          <summary>評価の履歴（{history.length}件。追記のみ）</summary>
          <ul>
            {history.map((h) => (
              <li key={h.id}>
                {formatTime(h.committed_at)}　{STATE_NAMES[h.state]}（{h.basis === "evidence" ? "記録に基づく" : "推測"}）
              </li>
            ))}
          </ul>
        </details>
      )}
      {!readOnly && (
        <>
          <button className="clear-button" onClick={() => setOpen(!open)}>
            {open ? "評価の入力を閉じる" : latest ? "再評価を追加" : "評価を追加"}
          </button>
          {open && (
            <div className="evidence-form">
              <label className="field-label">
                状態
                <select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
                  {Object.entries(STATE_NAMES).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </label>
              {!needsEvidence && f.state !== "not_researched" && (
                <label className="field-label">
                  根拠の種類
                  <select value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })}>
                    <option value="assumption">推測（記録を参照しない）</option>
                    <option value="evidence">記録に基づく</option>
                  </select>
                </label>
              )}
              {basis === "evidence" ? (
                <>
                  <fieldset className="field-label">
                    <legend>参照する記録（この観点で参照できるもののみ表示。統計・記事・メモは根拠に数えません）</legend>
                    {candidates.evidence.map((c) => (
                      <label key={`e${c.id}`}>
                        <input type="checkbox" checked={evidenceIds.includes(c.id)} onChange={() => toggle(evidenceIds, setEvidenceIds, c.id)} />
                        記録 #{c.id} {EVIDENCE_KINDS[c.kind]}・{OUTCOMES[c.result ?? ""]}・接点 {c.contact_ref}・{c.occurred_on}
                        {c.payment_signal && `・${PAYMENT_NAMES[c.payment_signal]}`}
                      </label>
                    ))}
                    {candidates.activities.map((c) => (
                      <label key={`a${c.id}`}>
                        <input type="checkbox" checked={activityIds.includes(c.id)} onChange={() => toggle(activityIds, setActivityIds, c.id)} />
                        計画 #{c.id} {ACTIVITY_TYPES[c.kind]}・結果 {OUTCOMES[c.result ?? ""]}・{TIMING_NAMES[c.mode ?? ""] ?? ""}
                      </label>
                    ))}
                    {candidates.evidence.length + candidates.activities.length === 0 && (
                      <span className="attribution">参照できる記録がありません。先に /hypotheses で検証記録を追加するか、計画を完了してください。</span>
                    )}
                  </fieldset>
                  {aspect.key === "willingness_to_pay" && f.state === "supported" && (
                    <p className="attribution warn-text">支払意思の「根拠あり」には、支払意思の記録（支持）が必要です。購入意向書・見積の合意は支払いではありません。</p>
                  )}
                  {(["observed_facts", "relevance", "judgement_reason"] as const).map((k) => (
                    <label key={k} className="field-label">
                      {{ observed_facts: "観察された具体的な事実", relevance: "その事実が観点にどう関係するか", judgement_reason: "支持・反証と判断した理由" }[k]}（必須）
                      <textarea className="field" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
                    </label>
                  ))}
                </>
              ) : (
                <label className="field-label">
                  推測のメモ（必須。推測では「根拠あり」「反証あり」にはできません）
                  <textarea className="field" value={f.assumption_note} onChange={(e) => setF({ ...f, assumption_note: e.target.value })} />
                </label>
              )}
              <button
                className="fetch-button"
                disabled={busy}
                onClick={async () => {
                  const body =
                    basis === "evidence"
                      ? { aspect: aspect.key, state: f.state, basis, observed_facts: f.observed_facts, relevance: f.relevance,
                          judgement_reason: f.judgement_reason, evidence_ids: evidenceIds, activity_ids: activityIds }
                      : { aspect: aspect.key, state: f.state, basis, assumption_note: f.assumption_note };
                  const ok = await run(
                    () => api(`/api/opportunities/${hypothesisId}/assessments`, { method: "POST", body: JSON.stringify(body) }),
                    `「${aspect.name}」の評価を記録しました（追記のみ）`,
                  );
                  if (ok) {
                    setF(empty);
                    setEvidenceIds([]);
                    setActivityIds([]);
                    setOpen(false);
                  }
                }}
              >
                評価を記録（確定後は変更できません）
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ActivityCard({
  a,
  d,
  run,
  busy,
  readOnly,
}: {
  a: Activity;
  d: Detail;
  run: (action: () => Promise<unknown>, done: string) => Promise<boolean>;
  busy: boolean;
  readOnly: boolean;
}) {
  const rev = a.locked_revision ?? a.current_revision;
  const [mode, setMode] = useState<"" | "revise" | "complete" | "source" | "cancel">("");
  const [plan, setPlan] = useState({ purpose: rev?.purpose ?? "", target: rev?.target ?? "", decision_criteria: rev?.decision_criteria ?? "", change_reason: "" });
  const [res, setRes] = useState({ outcome: "supports", observed_facts: "", interpretation: "", judgement_reason: "" });
  const [linked, setLinked] = useState<number[]>([]);
  const [src, setSrc] = useState({ url: "", title: "", accessed_on: "" });
  const [reason, setReason] = useState("");
  const kinds = a.activity_type === "interview" ? ["interview"] : a.activity_type === "sales_experiment" ? ["channel_test", "payment_intent"] : [];
  const linkable = d.linkable_evidence.filter((e) => kinds.includes(e.kind));
  const close = (ok: boolean) => ok && setMode("");

  return (
    <div className="aspect-card">
      <div className="meta">
        <strong>計画 #{a.id} {ACTIVITY_TYPES[a.activity_type]}</strong>
        <span className="tag">{ACTIVITY_STATUS[a.status]}</span>
        {a.mode && <span className={`tag${a.mode === "pre_registered" ? "" : " tag-warn"}`}>{TIMING_NAMES[a.mode]}</span>}
      </div>
      {a.warnings.map((w) => (
        <div key={w} className="attribution warn-text">⚠ {w}</div>
      ))}
      {rev && (
        <ul className="attribution">
          <li>{a.locked_revision ? `固定した版（第${rev.revision_no}版）` : `現在の版（第${rev.revision_no}版・開始前は訂正可）`}</li>
          <li>目的：{rev.purpose}</li>
          <li>対象：{rev.target}</li>
          <li><strong>判断基準：{rev.decision_criteria}</strong></li>
        </ul>
      )}
      {a.revisions.length > 1 && (
        <details className="evidence">
          <summary>訂正の履歴（{a.revisions.length}版）</summary>
          <ul>
            {a.revisions.map((r) => (
              <li key={r.id}>
                第{r.revision_no}版 {formatTime(r.created_at)}：{r.decision_criteria}
                {r.change_reason && `（訂正の理由：${r.change_reason}）`}
              </li>
            ))}
          </ul>
        </details>
      )}
      {a.result && (
        <div className="assessment">
          <h4>結果：{OUTCOMES[a.result.outcome]}</h4>
          <ul className="attribution">
            <li>観察された事実：{a.result.observed_facts}</li>
            <li>判断基準に照らした解釈：{a.result.interpretation}</li>
            <li>判断の理由：{a.result.judgement_reason}</li>
          </ul>
          {a.mode_note && <p className={`attribution${a.mode === "pre_registered" ? "" : " warn-text"}`}>{a.mode_note}</p>}
        </div>
      )}
      {a.linked_evidence.length > 0 && (
        <ul className="attribution">
          {a.linked_evidence.map((e) => (
            <li key={e.id} className={e.retracted ? "retracted" : ""}>
              記録 #{e.id} {EVIDENCE_KINDS[e.kind]}・{OUTCOMES[e.result]}・接点 {e.contact_ref}・{e.occurred_on}・{TIMING_NAMES[e.timing]}
              {e.payment_signal && `・${PAYMENT_NAMES[e.payment_signal]}`}
              {e.retracted && "・取り消し済み"}
            </li>
          ))}
        </ul>
      )}
      {a.sources.length > 0 && (
        <ul className="attribution">
          {a.sources.map((s) => (
            <li key={s.id}>
              <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a>（参照日 {s.accessed_on}）
            </li>
          ))}
        </ul>
      )}
      {a.cancel_reason && <p className="attribution">中止の理由：{a.cancel_reason}</p>}

      {!readOnly && (
        <div className="signals">
          {a.status === "planned" && (
            <>
              <button className="clear-button" onClick={() => setMode(mode === "revise" ? "" : "revise")}>訂正（版を追加）</button>
              <button className="clear-button" disabled={busy} onClick={() => run(() => api(`/api/activities/${a.id}/start`, { method: "POST" }), "開始しました（判断基準を固定しました）")}>
                開始（判断基準を固定）
              </button>
            </>
          )}
          {a.status === "in_progress" && (
            <>
              <button className="clear-button" onClick={() => setMode(mode === "complete" ? "" : "complete")}>結果を記録して完了</button>
              {!a.is_customer_contact && <button className="clear-button" onClick={() => setMode(mode === "source" ? "" : "source")}>参照先を追加</button>}
            </>
          )}
          {(a.status === "planned" || a.status === "in_progress") && (
            <button className="clear-button" onClick={() => setMode(mode === "cancel" ? "" : "cancel")}>中止</button>
          )}
        </div>
      )}

      {mode === "revise" && (
        <div className="evidence-form">
          {(["purpose", "target", "decision_criteria", "change_reason"] as const).map((k) => (
            <label key={k} className="field-label">
              {{ purpose: "目的", target: "対象（個人名・連絡先は書かない）", decision_criteria: "判断基準", change_reason: "訂正の理由（必須）" }[k]}
              <textarea className="field" value={plan[k]} onChange={(e) => setPlan({ ...plan, [k]: e.target.value })} />
            </label>
          ))}
          <button
            className="fetch-button"
            disabled={busy}
            onClick={async () => close(await run(() => api(`/api/activities/${a.id}/revisions`, { method: "POST", body: JSON.stringify(plan) }), "計画を訂正しました（古い版も残ります）"))}
          >
            訂正を保存
          </button>
        </div>
      )}
      {mode === "source" && (
        <div className="evidence-form">
          <label className="field-label">URL<input className="field-inline" value={src.url} onChange={(e) => setSrc({ ...src, url: e.target.value })} /></label>
          <label className="field-label">タイトル（任意）<input className="field-inline" value={src.title} onChange={(e) => setSrc({ ...src, title: e.target.value })} /></label>
          <label className="field-label">参照日<input type="date" max={today()} value={src.accessed_on} onChange={(e) => setSrc({ ...src, accessed_on: e.target.value })} /></label>
          <button
            className="fetch-button"
            disabled={busy}
            onClick={async () => close(await run(() => api(`/api/activities/${a.id}/sources`, { method: "POST", body: JSON.stringify({ ...src, title: src.title || null }) }), "参照先を追加しました"))}
          >
            追加
          </button>
        </div>
      )}
      {mode === "complete" && (
        <div className="evidence-form">
          {rev && <p className="attribution"><strong>固定した判断基準：{rev.decision_criteria}</strong></p>}
          <label className="field-label">
            結果
            <select value={res.outcome} onChange={(e) => setRes({ ...res, outcome: e.target.value })}>
              {Object.entries(OUTCOMES).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          {a.is_customer_contact && (
            <fieldset className="field-label">
              <legend>結果として紐付ける検証記録（1件以上。開始前の記録は「事後評価」として扱われます）</legend>
              {linkable.map((e) => {
                // 目安の表示（確定した区別はサーバーが完了時に判定する）：開始前に記録・実施されたものは事後評価
                const before =
                  a.started_at !== null &&
                  ((e.created_at ?? "") < a.started_at || (e.occurred_on ?? "") < new Date(a.started_at).toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }));
                return (
                  <label key={e.id}>
                    <input type="checkbox" checked={linked.includes(e.id)} onChange={() => setLinked(linked.includes(e.id) ? linked.filter((x) => x !== e.id) : [...linked, e.id])} />
                    記録 #{e.id} {EVIDENCE_KINDS[e.kind]}・{OUTCOMES[e.result ?? ""]}・接点 {e.contact_ref}・{e.occurred_on}
                    {before && "・開始前に記録（事後評価）"}
                  </label>
                );
              })}
              {linkable.length === 0 && <span className="attribution">紐付けられる記録がありません。先に /hypotheses で検証記録を追加してください。</span>}
            </fieldset>
          )}
          {(["observed_facts", "interpretation", "judgement_reason"] as const).map((k) => (
            <label key={k} className="field-label">
              {{ observed_facts: "観察された事実", interpretation: "判断基準に照らした解釈", judgement_reason: "判断の理由" }[k]}（必須）
              <textarea className="field" value={res[k]} onChange={(e) => setRes({ ...res, [k]: e.target.value })} />
            </label>
          ))}
          <button
            className="fetch-button"
            disabled={busy}
            onClick={async () =>
              close(await run(() => api(`/api/activities/${a.id}/complete`, { method: "POST", body: JSON.stringify({ ...res, evidence_ids: linked }) }), "結果を記録して完了しました"))
            }
          >
            完了（以後変更できません）
          </button>
        </div>
      )}
      {mode === "cancel" && (
        <div className="evidence-form">
          <input className="field-inline" placeholder="中止の理由（必須）" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button
            className="clear-button"
            disabled={busy || !reason.trim()}
            onClick={async () => close(await run(() => api(`/api/activities/${a.id}/cancel`, { method: "POST", body: JSON.stringify({ reason }) }), "計画を中止しました"))}
          >
            中止する
          </button>
        </div>
      )}
    </div>
  );
}

function ActivityForm({ hypothesisId, run, busy }: { hypothesisId: number; run: (action: () => Promise<unknown>, done: string) => Promise<boolean>; busy: boolean }) {
  const empty = { activity_type: "interview", purpose: "", target: "", decision_criteria: "", planned_on: "" };
  const [f, setF] = useState(empty);
  return (
    <div className="evidence-form">
      <h4>計画を追加</h4>
      <label className="field-label">
        種類
        <select value={f.activity_type} onChange={(e) => setF({ ...f, activity_type: e.target.value })}>
          {Object.entries(ACTIVITY_TYPES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </label>
      {(["purpose", "target", "decision_criteria"] as const).map((k) => (
        <label key={k} className="field-label">
          {{ purpose: "目的（何を確かめるか）", target: "対象（例：個人経営の飲食店3店。個人名・連絡先は書かない）", decision_criteria: "判断基準（どんな結果なら支持・反証とみなすか。開始時に固定）" }[k]}
          <textarea className="field" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
        </label>
      ))}
      <label className="field-label">
        予定日（任意）
        <input type="date" value={f.planned_on} onChange={(e) => setF({ ...f, planned_on: e.target.value })} />
      </label>
      <button
        className="fetch-button"
        disabled={busy}
        onClick={async () => {
          const ok = await run(
            () => api(`/api/opportunities/${hypothesisId}/activities`, { method: "POST", body: JSON.stringify({ ...f, planned_on: f.planned_on || null }) }),
            "計画を追加しました",
          );
          if (ok) setF(empty);
        }}
      >
        計画を追加
      </button>
    </div>
  );
}

export default function OpportunitiesPage() {
  // useSearchParams を使うため、Suspense の境界で囲む（Next.js の推奨）
  return (
    <Suspense fallback={<p className="empty">読み込み中…</p>}>
      <OpportunitiesContent />
    </Suspense>
  );
}
