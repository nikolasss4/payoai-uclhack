import { useState } from "react";
import { api } from "../api";
import { useApp } from "../app/context";
import { refreshAll, useAction, useLive } from "../hooks";
import { badge, dateTime, day, label, words } from "../lib";
import { Badge, ErrorLine, Icon, LinkChip } from "../ui";
import { Loading, RecordLayout } from "./Layout";

interface Detail {
  id: string; status: string; counterparty: string; counterparty_type: string; amount: string; max_fee: string; reference: string; rail: string; asset: string;
  chain: string | null; source_account: string; destination: string; destination_status: string; created_at: string;
  bill: { id: string; invoice: string | null; due_date: string | null; status: string; created_at: string; source_type: string; document: string | null; document_at: string | null; uploaded_by: string | null } | null;
  extraction: { at: string; failed_quotes: number } | null;
  checks: { check_name: string; outcome: string; reason: string }[];
  approvals: { id: string; status: string; created_at: string; decided_at: string | null; reason: string | null; decided_by: string | null }[];
  taps: { who: string | null; at: string; counted: boolean; message: string }[];
  attempt: { id: string; state: string; evidence_label: string; external_id: string | null; created_at: string } | null;
  outcomes: { source: string; status: string; at: string }[];
  resolution: { outcome: string; evidence: string; by: string; at: string } | null;
  stopped: { reason: string | null; at: string } | null;
  expense: { id: string; status: string; version: number } | null;
}

type StageState = "done" | "current" | "unknown" | "fail" | "warn" | "skip" | "todo";
const IC: Record<StageState, [string, string, string]> = {
  done: ["check", "var(--ok)", "var(--ok-bg)"], current: ["more_horiz", "var(--info)", "var(--info-bg)"], unknown: ["question_mark", "var(--unknown)", "var(--unknown-bg)"],
  fail: ["close", "var(--danger)", "var(--danger-bg)"], warn: ["priority_high", "var(--warn)", "var(--warn-bg)"], skip: ["remove", "var(--neutral)", "var(--neutral-bg)"], todo: ["", "var(--subtle)", "transparent"],
};
const STAGES = ["Received invoice", "Extracted details", "Matched supplier", "Checked (compliance, risk, cash)", "Prepared", "Approved", "Submitted", "Reconciled", "Expense recorded"];
const OK = ["PASS", "NOT_APPLICABLE", "ELIGIBLE_FOR_APPROVAL"];

type Ev = { k: string; v: string; tag?: string };

/** Which stage a payment has reached, from its status. Everything shown inside a stage comes from its records. */
function position(d: Detail): [number, StageState, string] {
  const rejected = d.approvals.find((a) => a.status === "REJECTED");
  if (d.attempt?.state === "MANUALLY_RESOLVED") return [8, d.expense ? "done" : "current", d.expense ? "" : "No expense recorded yet."];
  switch (d.status) {
    case "READY_FOR_APPROVAL": return [5, "current", "Waiting for an Approver"];
    case "APPROVED": return [6, "current", "Queued to send"];
    case "SUBMITTING": return [6, "current", "Being submitted now. Not confirmed."];
    case "SUBMITTED": return [7, "current", "Submitted. Not confirmed yet."];
    case "PENDING": return [7, "current", "The source says Pending."];
    case "UNKNOWN": return [6, "unknown", "Checking with the provider. This won't be resent."];
    case "CONFIRMED": return [8, d.expense ? "done" : "current", d.expense ? "" : "No expense recorded yet."];
    case "FAILED": return [7, "fail", "The source says it failed."];
    case "REJECTED": return [6, "fail", "The provider rejected it."];
    case "NEEDS_MANUAL_REVIEW": return [7, "warn", "The provider and the source disagree."];
    case "CANCELLED":
      if (rejected) return [5, "fail", `Rejected by ${rejected.decided_by || "an Approver"}${rejected.reason ? `: ${rejected.reason}` : ""}`];
      return [d.stopped ? 6 : 5, "skip", d.stopped?.reason || "Cancelled. Nothing was sent."];
    default: return [0, "current", ""];
  }
}

function evidence(d: Detail): Ev[][] {
  const b = d.bill;
  const flagged = d.checks.filter((c) => !OK.includes(c.outcome));
  const sim = d.attempt?.evidence_label ? label(d.attempt.evidence_label) : undefined;
  const submit = d.outcomes.filter((o) => o.source === "SUBMIT");
  const read = d.outcomes.filter((o) => o.source !== "SUBMIT");
  return [
    b?.document ? [{ k: "Document", v: b.document }, { k: "Received", v: `${dateTime(b.document_at || b.created_at)}${b.uploaded_by ? ` · uploaded by ${b.uploaded_by}` : ""}` }]
      : [{ k: "Source", v: b ? `${words(b.source_type)}, no document` : "No bill on record" }],
    [{ k: "Supplier", v: d.counterparty }, { k: "Amount", v: d.amount }, { k: "Reference", v: b?.invoice || d.reference }, ...(b?.due_date ? [{ k: "Due", v: day(b.due_date) }] : []),
      ...(d.extraction && d.extraction.failed_quotes > 0 ? [{ k: "Not confirmed", v: `${d.extraction.failed_quotes} field${d.extraction.failed_quotes === 1 ? "" : "s"} couldn't be matched to a quote in the document` }] : [])],
    [{ k: "Contact", v: [d.counterparty, d.counterparty_type].filter(Boolean).join(" · ") }, { k: "Destination", v: `${d.destination} · ${label(d.destination_status)}` }],
    [{ k: "Result", v: `${d.checks.length} checks · ${d.checks.length - flagged.length} pass or not applicable · ${flagged.length} flagged` }, ...flagged.map((c) => ({ k: c.check_name, v: `${label(c.outcome)}. ${c.reason}` }))],
    [{ k: "Payment", v: d.id }, { k: "From", v: d.source_account }, { k: "Maximum fee", v: d.max_fee }, { k: "Prepared", v: dateTime(d.created_at) }],
    d.taps.length ? d.taps.map((t) => ({ k: `${t.who || "Someone"} tapped`, v: `${dateTime(t.at)} · ${t.message}${t.counted ? "" : " (not counted)"}` }))
      : d.approvals.some((a) => a.status !== "PENDING" && a.status !== "APPROVED") ? d.approvals.map((a) => ({ k: label(a.status), v: `${a.decided_at ? dateTime(a.decided_at) : ""}${a.reason ? ` · ${a.reason}` : ""}` || "—" }))
      : [{ k: "Waiting", v: "No one has tapped yet" }],
    submit.length ? submit.map((o) => ({ k: "Provider's answer on submit", v: `${words(o.status)} · ${dateTime(o.at)}${d.attempt?.external_id ? ` · ref ${d.attempt.external_id}` : ""}`, tag: sim }))
      : d.stopped ? [{ k: "Stopped before sending", v: `${d.stopped.reason || "A check before sending failed"} · ${dateTime(d.stopped.at)}` }] : [{ k: "Provider's answer on submit", v: "—" }],
    read.length ? read.map((o) => ({ k: "Status read from the source", v: `${words(o.status)} · ${dateTime(o.at)}`, tag: sim }))
      : [{ k: "Status read from the source", v: d.status === "SUBMITTED" ? "Not read yet" : d.status === "UNKNOWN" ? "Checking with the provider" : "—" }],
    [{ k: "Expense", v: d.expense ? `${d.expense.id} · ${label(d.expense.status)}` : "—" }],
  ];
}

export function PaymentRecord({ id }: { id: string }) {
  const { open, isA } = useApp();
  const { data: d, error } = useLive<Detail>(`/api/payments/${id}/detail`, 6000);
  const [stageOpen, setStageOpen] = useState<Record<number, boolean>>({});
  if (!d) return <Loading error={error} />;

  const [at, kind, note] = position(d);
  const ev = evidence(d);
  const blocked = d.checks.some((c) => c.outcome === "BLOCK");
  const manual = d.attempt?.state === "MANUALLY_RESOLVED";
  const st = manual && d.resolution ? badge("MANUALLY_RESOLVED", `Manually resolved · ${words(d.resolution.outcome)}`) : badge(d.status);
  const stages = STAGES.map((name, k) => {
    let s: StageState = k < at ? "done" : k === at ? kind : "todo";
    if (k === 3 && blocked && k < at) s = "fail";
    const isOpen = stageOpen[k] ?? (k === at || (d.status === "CONFIRMED" && k === 7));
    let rows = ev[k];
    if (manual && d.resolution && k === 7) rows = [{ k: "Manually resolved", v: `${words(d.resolution.outcome)} · by ${d.resolution.by}` }, { k: "Evidence", v: d.resolution.evidence }, ...rows];
    if (manual && k === 7) s = "done";
    return { name, s, rows, isOpen, note: k === at ? note : "" };
  });

  return (
    <RecordLayout>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span style={{ fontSize: 15, color: "var(--muted)" }}>{d.counterparty} · <span className="mono" style={{ fontSize: 13 }}>{d.bill?.invoice || d.reference}</span></span>
        <span className="num wrap" style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.035em", lineHeight: 1.05 }}>{d.amount}</span>
        <div><Badge b={st} size="lg" /></div>
      </div>

      {d.status === "UNKNOWN" && <Note border="1.5px dashed var(--unknown-bd)" icon="question_mark"><b style={{ fontWeight: 600 }}>Unknown: checking with the provider.</b> This won't be resent. Don't pay it again.</Note>}
      {(d.status === "SUBMITTED" || d.status === "SUBMITTING") && <Note tone="info" icon="north_east"><b style={{ fontWeight: 600 }}>Submitted, not confirmed yet.</b> The provider accepted it. We're waiting for the source to say it settled.</Note>}
      {d.status === "PENDING" && <Note tone="info" icon="schedule"><b style={{ fontWeight: 600 }}>Pending.</b> The source says the receiving bank hasn't settled it yet.</Note>}
      {d.status === "FAILED" && <Note tone="danger" icon="error"><b style={{ fontWeight: 600 }}>Failed.</b> No money left the account. The bill needs a new payment.</Note>}
      {d.status === "REJECTED" && <Note tone="danger" icon="cancel"><b style={{ fontWeight: 600 }}>Rejected by the provider.</b> Nothing was sent.</Note>}
      {d.status === "NEEDS_MANUAL_REVIEW" && !manual && (isA && d.attempt ? <ManualForm attemptId={d.attempt.id} /> : (
        <Note tone="warn" icon="person_search">Needs manual review. Only an Approver can resolve it.</Note>
      ))}

      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingBottom: 10 }}>
          <span className="kicker">Trace</span>
          {d.attempt?.evidence_label === "SIMULATED" && <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--muted)" }}><Icon name="science" size={15} />Outcomes are simulated</span>}
        </div>
        {stages.map((g, k) => {
          const [icon, fg, bg] = IC[g.s];
          const todo = g.s === "todo";
          return (
            <div key={g.name} style={{ display: "grid", gridTemplateColumns: "28px minmax(0,1fr)", gap: "0 12px" }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                <span style={{ width: 28, height: 28, borderRadius: "50%", display: "grid", placeItems: "center", background: bg, color: fg, border: `1.5px ${g.s === "unknown" ? "dashed" : "solid"} ${todo ? "var(--border)" : fg}`, flex: "none" }}>{icon && <Icon name={icon} size={16} />}</span>
                {k < stages.length - 1 && <span style={{ flex: 1, width: 1.5, minHeight: 12, background: "var(--border)" }} />}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "3px 0 16px", minWidth: 0 }}>
                <button type="button" disabled={todo} onClick={() => setStageOpen((o) => ({ ...o, [k]: !g.isOpen }))} aria-expanded={!todo && g.isOpen}
                  style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, cursor: todo ? "default" : "pointer", minHeight: 24, background: "transparent", border: 0, padding: 0, textAlign: "left" }}>
                  <span style={{ fontSize: 14.5, fontWeight: 500, color: todo ? "var(--subtle)" : "var(--text)" }}>{g.name}</span>
                  {todo && <span style={{ fontSize: 12, color: "var(--subtle)" }}>Not reached</span>}
                </button>
                {g.note && <span style={{ fontSize: 13.5, fontWeight: 500, color: fg }}>{g.note}</span>}
                {!todo && g.isOpen && (
                  <div style={{ display: "flex", flexDirection: "column", borderRadius: 12, background: "var(--raised)", padding: "4px 12px" }}>
                    {g.rows.map((e, i) => (
                      <div key={i} style={{ display: "flex", flexDirection: "column", gap: 2, padding: "8px 0", borderBottom: i === g.rows.length - 1 ? 0 : "1px solid var(--border)", fontSize: 13 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                          <span style={{ color: "var(--muted)" }}>{e.k}</span>
                          {e.tag && <span className="bdg bdg-xs" style={{ color: "var(--neutral)", background: "var(--neutral-bg)", borderColor: "var(--neutral-bd)" }}><Icon name="science" size={13} />{e.tag}</span>}
                        </div>
                        <span className="wrap" style={{ lineHeight: 1.4 }}>{e.v}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {d.bill && <LinkChip icon="description" onClick={() => open({ kind: "bill", id: d.bill!.id }, true)}>Bill · {d.bill.invoice || "No reference"} · {label(d.bill.status)}</LinkChip>}
        {d.expense && <LinkChip icon="receipt_long" onClick={() => open({ kind: "expense", id: d.expense!.id }, true)}>Expense · {label(d.expense.status)}</LinkChip>}
      </div>
    </RecordLayout>
  );
}

function Note({ tone, border, icon, children }: { tone?: string; border?: string; icon: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 10, padding: "14px 16px", borderRadius: 14, background: tone ? `var(--${tone}-bg)` : undefined, border: border || `1px solid var(--${tone}-bd)`, fontSize: 14.5, alignItems: "flex-start" }}>
      <Icon name={icon} size={22} style={{ color: tone ? `var(--${tone})` : undefined }} /><span>{children}</span>
    </div>
  );
}

function ManualForm({ attemptId }: { attemptId: string }) {
  const [choice, setChoice] = useState<"PAID" | "NOT_PAID" | null>(null);
  const [evidence, setEvidence] = useState("");
  const act = useAction();
  const submit = () => {
    if (!choice) return act.setError("Choose Paid or Not paid.");
    if (!evidence.trim()) return act.setError("Evidence is required.");
    return act.run(async () => { await api(`/api/attempts/${attemptId}/resolve`, { method: "POST", body: { outcome: choice, evidence: evidence.trim() } }); refreshAll(); });
  };
  const opt = (value: "PAID" | "NOT_PAID", text: string) => (
    <button type="button" role="radio" aria-checked={choice === value} onClick={() => { setChoice(value); act.setError(null); }}
      style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 48, padding: "0 12px", borderRadius: 12, background: "var(--surface)", border: `1.5px solid ${choice === value ? "var(--text)" : "var(--border)"}`, cursor: "pointer", fontSize: 14, fontWeight: 500 }}>
      <Icon name={choice === value ? "radio_button_checked" : "radio_button_unchecked"} size={20} />{text}
    </button>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, borderRadius: 16, background: "var(--warn-bg)", border: "1px solid var(--warn-bd)" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Needs manual review</span>
        <span style={{ fontSize: 13.5, color: "var(--muted)", lineHeight: 1.45 }}>The provider and the source disagree about this payment. Check the bank statement and record what happened.</span>
      </div>
      <div role="radiogroup" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>{opt("PAID", "Paid")}{opt("NOT_PAID", "Not paid")}</div>
      <label className="field">Evidence (required)<textarea className="input" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="e.g. Bank statement line on the day, with the amount and destination" /></label>
      <ErrorLine error={act.error} />
      <button type="button" className="btn-pill btn-primary" aria-busy={act.busy || undefined} onClick={submit}>Record as manually resolved</button>
      <span style={{ fontSize: 12.5, color: "var(--muted)" }}>The result is labelled "Manually resolved", not "Confirmed".</span>
    </div>
  );
}
