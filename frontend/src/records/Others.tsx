import { useEffect, useRef, useState, type ReactNode } from "react";
import { ACCEPT, api, upload } from "../api";
import { useApp } from "../app/context";
import { refreshAll, useAction, useLive } from "../hooks";
import { badge, dateTime, day, decisionBadge, label, stamp, words } from "../lib";
import { Badge, ErrorLine, Icon, LinkChip } from "../ui";
import { paymentBadge, type BillRow, type PaymentRow } from "../screens/Payments";
import { Suggestion } from "../screens/Expenses";
import { Checks, DecisionBox, Loading, RecordLayout, RowLink, Section, SecureBox } from "./Layout";

const Row = ({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) => (
  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", padding: "11px 0", borderBottom: "1px solid var(--border)", fontSize: 14 }}>
    <span style={{ color: "var(--muted)" }}>{k}</span><span className={mono ? "mono" : ""} style={{ textAlign: "right" }}>{v}</span>
  </div>
);

/* Bill */

interface Bill {
  id: string; counterparty: string | null; counterparty_id: string | null; invoice: string | null; amount: string | null; due_date: string | null; status: string;
  decision: { decision: string; explanation: string | null } | null; checks: { check_name: string; outcome: string; reason: string }[];
  document: string | null; document_at: string | null; uploaded_by: string | null; created_at: string; source_type: string; superseded_by: string | null;
  corrections: { field: string; new_value: any; reason: string | null; at: string; by: string }[]; payment_rows: PaymentRow[];
}
const FIELDS: [string, string, string][] = [
  ["invoice_reference", "Reference", "e.g. INV-1042"], ["due_date", "Due date", "e.g. 2026-11-14"], ["amount_minor", "Amount (in pence or minor units)", "e.g. 124000"],
  ["asset", "Currency", "e.g. GBP"], ["description", "Description", "What the bill is for"],
];

export function BillRecord({ id }: { id: string }) {
  const { open, canWrite } = useApp();
  const { data: b, error } = useLive<Bill>(`/api/bills/${id}`, 10000);
  if (!b) return <Loading error={error} />;
  const received = b.document ? `${b.document} · received ${dateTime(b.document_at || b.created_at)}${b.uploaded_by ? ` · ${b.uploaded_by}` : ""}` : `${words(b.source_type)} · recorded ${dateTime(b.created_at)}`;
  return (
    <RecordLayout>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {b.counterparty_id ? (
          <button type="button" onClick={() => open({ kind: "contact", id: b.counterparty_id }, true)} style={{ fontSize: 15, color: "var(--muted)", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3, alignSelf: "flex-start", background: "transparent", border: 0, padding: 0 }}>{b.counterparty}</button>
        ) : <span style={{ fontSize: 15, color: "var(--muted)" }}>Supplier not matched yet</span>}
        <span className="num wrap" style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.035em", lineHeight: 1.05 }}>{b.amount || "Amount not read"}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <Badge b={badge(b.status)} size="lg" />
          <span style={{ fontSize: 13.5, color: "var(--muted)" }}><span className="mono">{b.invoice || "No reference"}</span> · Due {day(b.due_date)}</span>
        </div>
      </div>
      {b.superseded_by && <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--raised)", fontSize: 14, alignItems: "center" }}><Icon name="swap_horiz" size={20} />Replaced by a corrected bill. <button type="button" className="link-btn" style={{ background: "transparent", border: 0, padding: 0, fontSize: 14 }} onClick={() => open({ kind: "bill", id: b.superseded_by }, true)}>Open it</button></div>}
      <DecisionBox b={b.decision ? decisionBadge(b.decision.decision) : null} foot={received}>
        {b.decision?.explanation || (b.decision ? label(b.decision.decision) : "Received. Checks haven't run yet.")}
      </DecisionBox>
      {!!b.checks.length && <Checks checks={b.checks} />}
      <Section title="Linked payments">
        {b.payment_rows.length ? b.payment_rows.map((p) => (
          <button key={p.id} type="button" className="hoverable" onClick={() => open({ kind: "payment", id: p.id }, true)}
            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", minHeight: 52, padding: "8px 14px", borderRadius: 12, border: "1px solid var(--border)", background: "transparent", textAlign: "left" }}>
            <span className="num" style={{ fontSize: 14 }}>{p.amount} <span style={{ color: "var(--muted)" }}>· {day(p.created_at)}</span></span><Badge b={paymentBadge(p)} size="sm" />
          </button>
        )) : <span style={{ fontSize: 14, color: "var(--muted)" }}>No payment yet.</span>}
      </Section>
      {!!b.corrections.length && (
        <Section title="Corrections">
          {b.corrections.map((c, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 2, padding: "10px 14px", borderRadius: 12, background: "var(--raised)", fontSize: 14 }}>
              <span>{words(c.field)} → {typeof c.new_value === "string" ? c.new_value : JSON.stringify(c.new_value)}</span>
              <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{c.by} · {dateTime(c.at)}{c.reason ? ` · ${c.reason}` : ""}</span>
            </div>
          ))}
        </Section>
      )}
      {canWrite && <CorrectionForm billId={b.id} />}
    </RecordLayout>
  );
}

function CorrectionForm({ billId }: { billId: string }) {
  const [field, setField] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const act = useAction();
  const save = () => {
    setSaved(null);
    if (!field || !value.trim() || !reason.trim()) return act.setError("Field, new value and reason are all required.");
    return act.run(async () => {
      const r = await api<{ note?: string }>(`/api/bills/${billId}/manual-correction`, { method: "POST", body: { field, value: value.trim(), reason: reason.trim() } });
      setSaved(r.note || "Correction recorded.");
      setValue(""); setReason("");
      refreshAll();
    });
  };
  const placeholder = FIELDS.find(([f]) => f === field)?.[2] || "The corrected value";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, borderRadius: 16, border: "1px solid var(--border)" }}>
      <span style={{ fontSize: 15, fontWeight: 600 }}>Correct this bill</span>
      <label className="field">Field
        <select className="input" value={field} onChange={(e) => { setField(e.target.value); setSaved(null); }}>
          <option value="">Choose a field</option>
          {FIELDS.map(([f, l]) => <option key={f} value={f}>{l}</option>)}
        </select>
      </label>
      <label className="field">New value<input className="input" value={value} onChange={(e) => { setValue(e.target.value); setSaved(null); }} placeholder={placeholder} /></label>
      <label className="field">Reason<input className="input" value={reason} onChange={(e) => { setReason(e.target.value); setSaved(null); }} placeholder="Why it's wrong" /></label>
      <ErrorLine error={act.error} />
      {saved && <span role="status" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ok)" }}><Icon name="check_circle" size={16} />{saved}</span>}
      <button type="button" className="btn-pill" aria-busy={act.busy || undefined} onClick={save}>Save correction</button>
      <span style={{ display: "flex", gap: 6, fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45 }}><Icon name="info" size={16} />The old value is kept. A sent payment can't be undone here, and a refund or credit is a separate record.</span>
    </div>
  );
}

/* Contact */

interface Contact {
  id: string; display_name: string; legal_name: string | null; entity_type: string; classifications: string[]; vat_number: string | null;
  destinations: { id: string; rail: string; asset: string; masked_display: string; status: string; chain: string | null }[];
  channels: { id: string; kind: string; value: string; added_at: string }[]; bills: BillRow[]; payments: PaymentRow[];
}

export function ContactRecord({ id }: { id: string }) {
  const { open, canWrite, requestChat } = useApp();
  const { data: c, error } = useLive<Contact>(`/api/counterparties/${id}`, 20000);
  if (!c) return <Loading error={error} />;
  const verified = c.destinations.filter((d) => d.status === "VERIFIED").length;
  return (
    <RecordLayout>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.025em" }}>{c.display_name}</span>
        {c.legal_name && <span style={{ fontSize: 14, color: "var(--muted)" }}>Legal name: {c.legal_name}</span>}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
          {(c.classifications.length ? c.classifications : [c.entity_type]).map((t) => <span key={t} style={{ display: "inline-flex", alignItems: "center", height: 24, padding: "0 10px", borderRadius: 999, fontSize: 12, fontWeight: 500, background: "var(--raised)" }}>{t}</span>)}
          {c.vat_number && <span style={{ fontSize: 13, color: "var(--muted)", display: "flex", alignItems: "center" }}>VAT {c.vat_number}</span>}
        </div>
      </div>
      {canWrite && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <button type="button" className="btn-pill btn-primary" aria-disabled={!verified || undefined} style={{ minHeight: 48, fontSize: 15, gap: 8, ...(verified ? {} : { background: "var(--raised)", color: "var(--subtle)", opacity: 1 }) }}
            onClick={() => verified && requestChat({ fresh: true, send: `Pay ${c.display_name}` })}>
            <Icon name="send_money" size={18} />Pay this contact
          </button>
          <span style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45 }}>{verified ? `Offers only verified destinations (${verified}). Payments to unverified details aren't possible.` : "No verified destination. Payments to unverified details aren't possible."}</span>
        </div>
      )}
      <Section title="Destinations">
        {!c.destinations.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No destinations on file.</span>}
        {c.destinations.map((d) => (
          <button key={d.id} type="button" className="hoverable" onClick={() => open({ kind: "dest", id: d.id }, true)}
            style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 60, padding: "10px 14px", borderRadius: 12, border: "1px solid var(--border)", background: "var(--surface)", textAlign: "left" }}>
            <Icon name={d.rail === "Crypto" ? "account_balance_wallet" : "account_balance"} size={22} style={{ color: "var(--muted)" }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}><span className="mono" style={{ fontSize: 14 }}>{d.masked_display}</span><span style={{ fontSize: 12.5, color: "var(--muted)" }}>{d.rail} · {d.asset}{d.chain ? ` · ${d.chain}` : ""}</span></div>
            <Badge b={badge(d.status)} size="sm" />
            <Icon name="chevron_right" size={20} style={{ color: "var(--subtle)" }} />
          </button>
        ))}
      </Section>
      <Section title="Contact channels on file">
        {!c.channels.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>None on file, so a call-back can't be done yet.</span>}
        {c.channels.map((ch) => <Row key={ch.id} k={words(ch.kind)} v={<>{ch.value} <span style={{ color: "var(--muted)" }}>· added {day(ch.added_at)}</span></>} />)}
      </Section>
      <Section title="Recent bills">
        {!c.bills.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No bills yet.</span>}
        {c.bills.map((b) => <RowLink key={b.id} onClick={() => open({ kind: "bill", id: b.id }, true)} left={<><span className="mono" style={{ fontSize: 13 }}>{b.invoice || "No reference"}</span> <span style={{ color: "var(--muted)" }}>· {b.amount || "—"}</span></>} right={<Badge b={badge(b.status)} size="sm" />} />)}
      </Section>
      <Section title="Recent payments">
        {!c.payments.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No payments yet.</span>}
        {c.payments.map((p) => <RowLink key={p.id} onClick={() => open({ kind: "payment", id: p.id }, true)} left={<span className="num">{p.amount} <span style={{ color: "var(--muted)" }}>· {day(p.created_at)}</span></span>} right={<Badge b={paymentBadge(p)} size="sm" />} />)}
      </Section>
    </RecordLayout>
  );
}

/* Destination */

interface Destination {
  id: string; counterparty: string; counterparty_id: string; rail: string; asset: string; masked: string; status: string; chain: string | null; token_contract: string | null;
  source: string; added_at: string; added_by: string | null; revoked_at: string | null; revoke_reason: string | null;
  verification: { method: string; at: string; by: string; same_person: boolean; via: string | null } | null;
  channels: { id: string; kind: string; label: string; added_at: string; independent: boolean }[];
}
const METHODS: [string, string, boolean][] = [["CALL_BACK", "Call back", true], ["SIGNED_CONTRACT", "Signed contract", true], ["CRYPTO_WALLET", "Crypto wallet confirmation", true], ["NEW_RELATIONSHIP", "New relationship", false]];
const DETAIL_NAMES: Record<string, string> = { iban: "IBAN", sort_code: "Sort code", account_number: "Account number", address: "Wallet address" };

export function DestinationRecord({ id }: { id: string }) {
  const { isA, isO } = useApp();
  const { data: d, error } = useLive<Destination>(`/api/destinations/${id}`, 20000);
  const [full, setFull] = useState<{ details: Record<string, string> } | null>(null);
  const [fullError, setFullError] = useState<string | null>(null);
  const fetched = useRef<string | null>(null);

  // Full details are read once per opening, because every read is recorded in Audit. They're never re-read on a timer.
  useEffect(() => {
    if (!isA || fetched.current === id) return;
    fetched.current = id;
    api<{ details: Record<string, string> }>(`/api/destinations/${id}/full`).then(setFull).catch((e) => setFullError((e as Error).message));
  }, [id, isA]);

  if (!d) return <Loading error={error} />;
  return (
    <RecordLayout>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span style={{ fontSize: 15, color: "var(--muted)" }}>{d.counterparty}</span>
        <span className="mono wrap" style={{ fontSize: 28, fontWeight: 500, letterSpacing: "-0.02em" }}>{d.masked}</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><Badge b={badge(d.status)} size="lg" /><span style={{ fontSize: 13.5, color: "var(--muted)" }}>{d.rail} · {d.asset}{d.chain ? ` · ${d.chain}` : ""}</span></div>
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <Row k="Added" v={`${day(d.added_at)}${d.added_by ? ` · ${d.added_by}` : ""}`} />
        <Row k="Came from" v={d.source} />
        {d.token_contract && <Row k="Token contract" v={d.token_contract} mono />}
        {d.verification && <Row k="Verified" v={`${d.verification.method} · by ${d.verification.by} · ${day(d.verification.at)}${d.verification.via ? ` · via ${d.verification.via}` : ""}`} />}
        {d.status === "REVOKED" && <Row k="Revoked" v={`${d.revoke_reason || "No reason recorded"}${d.revoked_at ? ` · ${day(d.revoked_at)}` : ""}`} />}
      </div>
      {d.verification?.same_person && <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--warn-bg)", border: "1px solid var(--warn-bd)", fontSize: 14 }}><Icon name="group_off" size={20} style={{ color: "var(--warn)" }} />Added and verified by the same person. Approvals show this.</div>}
      {isA && full && (
        <SecureBox title="Full details · Approvers only" foot={<span style={{ fontSize: 12.5, opacity: 0.85 }}>Viewing is recorded in Audit. Check every character.</span>}>
          {Object.entries(full.details || {}).map(([k, v]) => (
            <div key={k} style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: 11.5, opacity: 0.7 }}>{DETAIL_NAMES[k] || words(k)}</span><span className="mono wrap" style={{ fontSize: 15 }}>{String(v)}</span></div>
          ))}
        </SecureBox>
      )}
      <ErrorLine error={fullError} />
      <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--raised)", fontSize: 14, lineHeight: 1.45 }}><Icon name="shield" size={20} /><span>Verification is done here, never in chat. Details from an invoice, chat or history don't count.</span></div>
      {!isA && <span style={{ fontSize: 14, color: "var(--muted)" }}>{isO ? "Only an Approver can verify or revoke destinations." : "View only."}</span>}
      {isA && d.status === "UNVERIFIED" && <VerifyForm d={d} />}
      {isA && d.status === "VERIFIED" && <RevokeForm id={d.id} />}
    </RecordLayout>
  );
}

function VerifyForm({ d }: { d: Destination }) {
  const [method, setMethod] = useState<string | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [evidence, setEvidence] = useState("");
  const [done, setDone] = useState<{ same_person: boolean; note?: string } | null>(null);
  const act = useAction();
  const needsChannel = METHODS.find(([m]) => m === method)?.[2] ?? true;
  const pick = (box: { bd: boolean }) => ({ border: `1.5px solid ${box.bd ? "var(--text)" : "var(--border)"}`, background: box.bd ? "var(--raised)" : "transparent" });
  const submit = () => {
    if (!method) return act.setError("Choose a method.");
    if (needsChannel && !channel) return act.setError("Choose a contact channel on file.");
    if (!evidence.trim()) return act.setError("Add evidence. Say who you spoke to and what they confirmed.");
    return act.run(async () => {
      const r = await api<{ same_person: boolean; note?: string }>(`/api/destinations/${d.id}/verify`, { method: "POST", body: { method, evidence: evidence.trim(), channel_id: needsChannel ? channel : null } });
      setDone(r);
      refreshAll();
    });
  };
  if (done) {
    return (
      <div role="status" style={{ display: "flex", gap: 10, padding: "14px 16px", borderRadius: 14, background: "var(--ok-bg)", border: "1px solid var(--ok-bd)", fontSize: 14.5, alignItems: "flex-start", animation: "rise .2s" }}>
        <Icon name="verified" size={22} style={{ color: "var(--ok)" }} />
        <span><b style={{ fontWeight: 600 }}>Verified.</b> Recorded in Audit.{done.same_person ? " You added these details too, so approvals will show that." : ""} {done.note}</span>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: 16, borderRadius: 16, border: "1px solid var(--border)", background: "var(--surface)" }}>
      <span style={{ fontSize: 16, fontWeight: 600 }}>Verify these details</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>Method</span>
        <div role="radiogroup" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 6 }}>
          {METHODS.map(([m, l]) => (
            <button key={m} type="button" role="radio" aria-checked={method === m} onClick={() => { setMethod(m); act.setError(null); }}
              style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, padding: "0 10px", borderRadius: 10, fontSize: 13.5, cursor: "pointer", textAlign: "left", ...pick({ bd: method === m }) }}>
              <Icon name={method === m ? "radio_button_checked" : "radio_button_unchecked"} size={18} />{l}
            </button>
          ))}
        </div>
        {method === "NEW_RELATIONSHIP" && <span style={{ fontSize: 12.5, color: "var(--warn)" }}>Weaker trust. Use it only for a supplier you've never paid before.</span>}
      </div>
      {needsChannel && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>Contact channel on file</span>
          {!d.channels.length && <span style={{ fontSize: 13.5, color: "var(--muted)" }}>No channels on file for this contact.</span>}
          {d.channels.map((ch) => (
            <button key={ch.id} type="button" role="radio" aria-checked={channel === ch.id} onClick={() => { setChannel(ch.id); act.setError(null); }}
              style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 52, padding: "6px 12px", borderRadius: 10, cursor: "pointer", textAlign: "left", ...pick({ bd: channel === ch.id }) }}>
              <Icon name={channel === ch.id ? "radio_button_checked" : "radio_button_unchecked"} size={18} />
              <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                <span className="wrap" style={{ fontSize: 14 }}>{ch.label}</span>
                <span style={{ fontSize: 12, color: ch.independent ? "var(--muted)" : "var(--warn)" }}>Added {day(ch.added_at)}{ch.independent ? "" : " · after these details came in, so not independent"}</span>
              </div>
            </button>
          ))}
        </div>
      )}
      <label className="field">Evidence (required)<textarea className="input" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="Who you spoke to, when, and what they confirmed" /></label>
      {act.error && <div role="alert" style={{ display: "flex", gap: 8, padding: "10px 12px", borderRadius: 10, background: "var(--danger-bg)", border: "1px solid var(--danger-bd)", fontSize: 13.5 }}><Icon name="error" size={18} style={{ color: "var(--danger)" }} />{act.error}</div>}
      <button type="button" className="btn-pill btn-primary" aria-busy={act.busy || undefined} onClick={submit} style={{ minHeight: 48, fontSize: 15, gap: 8 }}><Icon name="verified_user" size={18} />Verify destination</button>
    </div>
  );
}

function RevokeForm({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<number | null>(null);
  const act = useAction();
  if (done !== null) {
    return <div role="status" style={{ display: "flex", gap: 10, padding: "14px 16px", borderRadius: 14, background: "var(--danger-bg)", border: "1px solid var(--danger-bd)", fontSize: 14 }}><Icon name="remove_moderator" size={20} style={{ color: "var(--danger)" }} />Revoked. {done} pending approval{done === 1 ? "" : "s"} cancelled.</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16, borderRadius: 16, border: "1px solid var(--danger-bd)" }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", minHeight: 32, background: "transparent", border: 0, padding: 0 }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: "var(--danger)" }}>Revoke destination</span><Icon name={open ? "expand_less" : "expand_more"} size={22} style={{ color: "var(--muted)" }} />
      </button>
      {open && (
        <>
          <label className="field">Reason (required)<input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Supplier closed this account" /></label>
          <span style={{ display: "flex", gap: 6, fontSize: 13, color: "var(--danger)" }}><Icon name="warning" size={16} />Takes effect immediately and cancels pending approvals that use it.</span>
          <ErrorLine error={act.error} />
          <button type="button" className="btn-pill" aria-busy={act.busy || undefined} style={{ border: "1.5px solid var(--danger)", color: "var(--danger)" }}
            onClick={() => { if (!reason.trim()) return act.setError("A reason is required."); return act.run(async () => { const r = await api<{ cancelled_approvals: number }>(`/api/destinations/${id}/revoke`, { method: "POST", body: { reason: reason.trim() } }); setDone(r.cancelled_approvals); refreshAll(); }); }}>Revoke now</button>
        </>
      )}
    </div>
  );
}

/* Expense */

interface Expense {
  id: string; counterparty: string | null; label: string;
  versions: { version: number; status: string; category: string | null; category_is_suggestion: boolean; business_purpose: string | null; accounting_period: string | null; gross: string | null; net: string | null; tax: string | null; reason: string | null; actor: string | null; created_at: string; intent_id: string | null; obligation_id: string | null }[];
  journal: { kind: string; reviewed: boolean; lines: { account: string; debit: string | null; credit: string | null }[] }[];
  exceptions: { kind: string; status: string; details: any }[];
}

export function ExpenseRecord({ id }: { id: string }) {
  const { open, canWrite } = useApp();
  const { data: e, error } = useLive<Expense>(`/api/expenses/${id}`, 15000);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const act = useAction();
  const [uploaded, setUploaded] = useState<string | null>(null);
  if (!e) return <Loading error={error} />;
  const latest = e.versions[e.versions.length - 1];
  const payment = [...e.versions].reverse().find((v) => v.intent_id)?.intent_id;
  const bill = [...e.versions].reverse().find((v) => v.obligation_id)?.obligation_id;
  const open_ = e.exceptions.filter((x) => x.status === "OPEN");
  const lines = e.journal.flatMap((j) => j.lines.flatMap((l) => [l.debit ? { side: "Debit", acct: l.account, amt: l.debit } : null, l.credit ? { side: "Credit", acct: l.account, amt: l.credit } : null]).filter(Boolean) as { side: string; acct: string; amt: string }[]);
  const reviewed = e.journal.length > 0 && e.journal.every((j) => j.reviewed);
  const onFile = (file?: File) => {
    if (!file) return;
    act.run(async () => {
      const doc = await upload(file);
      await api(`/api/expenses/${id}/evidence`, { method: "POST", body: { document_id: doc.id } });
      setUploaded(file.name);
      refreshAll();
    });
  };
  const detail = (d: any) => (typeof d === "string" ? d : d && typeof d === "object" ? Object.entries(d).map(([k, v]) => `${words(k)}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ") : "");
  return (
    <RecordLayout>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span style={{ fontSize: 15, color: "var(--muted)" }}>{e.counterparty || "Expense"}</span>
        <span className="num wrap" style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.035em", lineHeight: 1.05 }}>{latest.gross || "—"}</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Badge b={badge(latest.status)} size="lg" />
          <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{latest.category || "No category"}</span>
          {latest.category && latest.category_is_suggestion && <Suggestion />}
        </div>
        {latest.business_purpose && <span style={{ fontSize: 14 }}>{latest.business_purpose}</span>}
      </div>
      <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--raised)", fontSize: 13.5 }}><Icon name="info" size={18} />{e.label || "Prepared for accountant review, not certified compliant."}</div>
      {(payment || bill) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {payment && <LinkChip icon="timeline" onClick={() => open({ kind: "payment", id: payment }, true)}>Payment trace</LinkChip>}
          {bill && <LinkChip icon="description" onClick={() => open({ kind: "bill", id: bill }, true)}>Bill</LinkChip>}
        </div>
      )}
      {!!open_.length && (
        <Section title="Open exceptions" gap={8}>
          {open_.map((x, i) => (
            <div key={i} style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 12, background: "var(--warn-bg)", border: "1px solid var(--warn-bd)" }}>
              <Icon name="priority_high" size={20} style={{ color: "var(--warn)" }} />
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: 14, fontWeight: 600 }}>{words(x.kind)}</span>{detail(x.details) && <span style={{ fontSize: 13.5, lineHeight: 1.45 }}>{detail(x.details)}</span>}</div>
            </div>
          ))}
        </Section>
      )}
      {canWrite && (
        <>
          <input ref={fileInput} type="file" accept={ACCEPT} hidden onChange={(ev) => { onFile(ev.target.files?.[0]); ev.target.value = ""; }} />
          <button type="button" className="hoverable" onClick={() => fileInput.current?.click()} aria-busy={act.busy || undefined}
            style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, padding: 18, borderRadius: 14, border: "1.5px dashed var(--border-strong)", cursor: "pointer", textAlign: "center", background: "transparent" }}>
            <Icon name={act.busy ? "progress_activity" : "upload_file"} size={24} className={act.busy ? "spin" : ""} style={{ color: "var(--muted)" }} />
            <span style={{ fontSize: 14, fontWeight: 500 }}>{act.busy ? "Uploading…" : "Upload evidence"}</span>
            <span style={{ fontSize: 12.5, color: "var(--muted)" }}>Receipt, invoice or statement · PDF, text or image</span>
          </button>
          <ErrorLine error={act.error} />
          {uploaded && <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}><Icon name="attach_file" size={18} style={{ color: "var(--ok)" }} />{uploaded} attached. A new version appears once it's been checked.</div>}
        </>
      )}
      <Section title="Journal preview" right={<Badge b={reviewed ? badge("Reviewed") : badge("Draft", "Unreviewed")} size="sm" style={reviewed ? undefined : { borderStyle: "dashed" }} />}>
        {lines.length ? (
          <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
            {lines.map((j, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "56px minmax(0,1fr) auto", gap: 10, padding: "10px 12px", borderBottom: i === lines.length - 1 ? 0 : "1px solid var(--border)", fontSize: 13.5 }}>
                <span style={{ color: "var(--muted)" }}>{j.side}</span><span className="wrap">{j.acct}</span><span className="num" style={{ textAlign: "right" }}>{j.amt}</span>
              </div>
            ))}
          </div>
        ) : <span style={{ fontSize: 14, color: "var(--muted)" }}>No journal proposed yet.</span>}
      </Section>
      <Section title="Version history">
        {[...e.versions].reverse().map((v) => (
          <div key={v.version} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "12px 14px", borderRadius: 12, border: "1px solid var(--border)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
              <span className="mono" style={{ fontSize: 13, fontWeight: 500 }}>v{v.version} · {label(v.status)}</span>
              <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{v.actor === "SYSTEM" ? "System" : v.actor === "AGENT" ? "PayoAI agent" : words(v.actor) || "—"} · {stamp(v.created_at)}</span>
            </div>
            <div className="num" style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8, fontSize: 13 }}>
              <span><span style={{ color: "var(--muted)", display: "block", fontSize: 11.5 }}>Gross</span>{v.gross || "—"}</span>
              <span><span style={{ color: "var(--muted)", display: "block", fontSize: 11.5 }}>Net</span>{v.net || "—"}</span>
              <span><span style={{ color: "var(--muted)", display: "block", fontSize: 11.5 }}>Tax</span>{v.tax || "—"}</span>
            </div>
            <span style={{ fontSize: 13 }}>{v.category ? `${v.category}${v.category_is_suggestion ? " (suggested)" : ""}` : "No category"}{v.reason && <span style={{ color: "var(--muted)" }}> · {v.reason}</span>}</span>
          </div>
        ))}
      </Section>
    </RecordLayout>
  );
}

/* Notifications */

const NK: Record<string, [string, string]> = {
  PAYMENT_READY: ["front_hand", "warn"], PAYMENT_APPROVED: ["thumb_up", "info"], PAYMENT_REJECTED: ["cancel", "danger"], PAYMENT_STOPPED: ["block", "danger"],
  PAYMENT_OUTCOME_UNKNOWN: ["question_mark", "unknown"], PAYMENT_FAILED: ["error", "danger"], PAYMENT_MISMATCH: ["compare_arrows", "warn"], CASH_SHORTFALL: ["trending_down", "warn"],
  DESTINATION_REVOKED: ["remove_moderator", "danger"], MISSING_DOCUMENT: ["receipt_long", "warn"], MISSING_DOCUMENT_REMINDER: ["receipt_long", "warn"], PURCHASE_READY: ["shopping_bag", "info"],
  CRITICAL_LATE_CONFIRMATION: ["priority_high", "danger"], PAYMENT_NEEDS_MANUAL_REVIEW: ["person_search", "warn"], PAYMENT_REJECTED_BY_PROVIDER: ["cancel", "danger"], EXPENSE_NEEDS_REVIEW: ["visibility", "warn"],
};

export function NotificationsRecord() {
  const { notifications, open, mobile, reloadShell } = useApp();
  const act = useAction();
  const markRead = (ids?: string[]) => act.run(async () => { await api("/api/notifications/read", { method: "POST", body: { ids: ids ?? null } }); reloadShell(); });
  const items = notifications?.items;
  return (
    <RecordLayout>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontSize: 14, color: "var(--muted)" }}>Newest first</span>
        {!!notifications?.unread && <button type="button" onClick={() => markRead()} style={{ fontSize: 14, fontWeight: 500, cursor: "pointer", minHeight: 44, display: "flex", alignItems: "center", textDecoration: "underline", textUnderlineOffset: 3, background: "transparent", border: 0, padding: 0 }}>Mark all as read</button>}
      </div>
      <ErrorLine error={act.error} />
      {!items && <div className="skeleton" style={{ height: 64 }} />}
      {items && !items.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>Nothing yet. Payments ready for approval, failures and shortfalls appear here.</span>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(items || []).map((n) => {
          const [icon, tone] = NK[n.kind] || ["notifications", "neutral"];
          const urgent = n.kind === "CRITICAL_LATE_CONFIRMATION";
          return (
            <button key={n.id} type="button" className="hoverable" onClick={() => { if (n.unread) markRead([n.id]); if (n.record) open(n.record, mobile); }}
              style={{ display: "grid", gridTemplateColumns: "36px minmax(0,1fr) auto", gap: 12, alignItems: "start", padding: 12, borderRadius: 14, cursor: n.record ? "pointer" : "default", background: urgent ? "var(--danger-bg)" : n.unread ? "var(--raised)" : "transparent", border: `1.5px solid ${urgent ? "var(--danger)" : "transparent"}`, textAlign: "left" }}>
              <span style={{ width: 36, height: 36, borderRadius: "50%", display: "grid", placeItems: "center", background: `var(--${tone}-bg)`, color: `var(--${tone})`, border: tone === "unknown" ? "1px dashed var(--unknown-bd)" : undefined }}><Icon name={icon} size={19} /></span>
              <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".03em", textTransform: "uppercase", color: `var(--${tone})` }}>{n.label}</span>
                  {n.unread && <span style={{ fontSize: 11, fontWeight: 600, padding: "1px 7px", borderRadius: 999, background: "var(--text)", color: "var(--bg)" }}>New</span>}
                </div>
                <span style={{ fontSize: 14, lineHeight: 1.4, textWrap: "pretty" } as any}>{n.title}</span>
              </div>
              <span className="mono" style={{ fontSize: 12, color: "var(--muted)", whiteSpace: "nowrap" }}>{stamp(n.at)}</span>
            </button>
          );
        })}
      </div>
    </RecordLayout>
  );
}
