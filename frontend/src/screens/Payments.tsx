import type { ReactNode } from "react";
import { api } from "../api";
import { useApp, type PayTab } from "../app/context";
import { refreshAll, useAction, useArmed, useLive } from "../hooks";
import { badge, day, initials, plural, words } from "../lib";
import { Badge, Empty, ErrorLine, Icon, Skeleton } from "../ui";

export interface PaymentRow { id: string; amount: string; status: string; reference: string; invoice: string | null; counterparty: string; counterparty_id: string; obligation_id: string; evidence: string | null; attempt_state: string | null; rail: string; asset: string; created_at: string }
export interface BillRow { id: string; invoice: string | null; amount: string | null; due_date: string | null; status: string; counterparty: string | null; counterparty_id: string | null; created_at: string }

export const paymentBadge = (p: { status: string; attempt_state?: string | null }) => (p.attempt_state === "MANUALLY_RESOLVED" ? badge("MANUALLY_RESOLVED") : badge(p.status));

const TABS: [PayTab, string][] = [["payments", "Payments"], ["bills", "Bills"], ["contacts", "Contacts"], ["automations", "Automations"]];

export function Payments() {
  const { mobile, nav, setPayTab, canWrite, requestChat } = useApp();
  const tab = nav.payTab;
  return (
    <div data-screen-label={mobile ? "M Payments" : "Payments"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "28px 32px 64px", display: "flex", flexDirection: "column", gap: 18, maxWidth: 1120 }}>
      <div style={{ display: "flex", alignItems: mobile ? "center" : "flex-end", justifyContent: "space-between", gap: mobile ? 8 : 16, flexWrap: "wrap" }}>
        <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : 32, fontWeight: 600, letterSpacing: "-0.03em" }}>Payments</h1>
        {tab === "bills" && canWrite && (
          <button type="button" className="btn-pill btn-primary" onClick={() => requestChat({ attach: true, draft: "Pay this bill" })} style={mobile ? { padding: "0 14px", fontSize: 13.5, gap: 4, whiteSpace: "nowrap" } : undefined}>
            <Icon name="upload_file" size={mobile ? 18 : 19} />Upload a bill
          </button>
        )}
      </div>
      {mobile ? (
        <div role="tablist" style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 2, padding: 3, borderRadius: 12, background: "var(--raised)" }}>
          {TABS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setPayTab(k)}
              style={{ minHeight: 40, borderRadius: 9, display: "grid", placeItems: "center", fontSize: 12.5, fontWeight: 500, cursor: "pointer", border: 0, background: tab === k ? "var(--surface)" : "transparent", color: tab === k ? "var(--text)" : "var(--muted)", boxShadow: tab === k ? "var(--shadow-1)" : "none" }}>{l}</button>
          ))}
        </div>
      ) : (
        <div role="tablist" style={{ display: "flex", gap: 24, borderBottom: "1px solid var(--border)" }}>
          {TABS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setPayTab(k)}
              style={{ minHeight: 44, display: "flex", alignItems: "center", fontSize: 14.5, fontWeight: 500, cursor: "pointer", color: tab === k ? "var(--text)" : "var(--muted)", background: "transparent", border: 0, borderBottom: `2px solid ${tab === k ? "var(--text)" : "transparent"}`, marginBottom: -1, padding: 0 }}>{l}</button>
          ))}
        </div>
      )}
      {tab === "payments" && <PaymentsTab />}
      {tab === "bills" && <BillsTab />}
      {tab === "contacts" && <ContactsTab />}
      {tab === "automations" && <AutomationsTab />}
    </div>
  );
}

function Loading() {
  const { mobile } = useApp();
  return <Skeleton height={mobile ? 72 : 56} count={mobile ? 2 : 3} radius={mobile ? 14 : 12} />;
}

export function Table({ cols, head, children }: { cols: string; head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="table">
      <div className="table-head" style={{ gridTemplateColumns: cols }}>{head.map((h, i) => <span key={i} style={h === "Amount" || h === "Gross" ? { textAlign: "right" } : undefined}>{h}</span>)}</div>
      {children}
    </div>
  );
}

function Chevron() {
  return <Icon name="chevron_right" size={20} style={{ color: "var(--subtle)" }} />;
}

function PaymentsTab() {
  const { mobile, open } = useApp();
  const { data, error, loading } = useLive<PaymentRow[]>("/api/payments", 8000);
  if (loading) return <Loading />;
  const rows = data || [];
  return (
    <>
      <ErrorLine error={error} />
      {data && !rows.length && <Empty compact={mobile}>{mobile ? "No payments yet." : "No payments yet. Payments appear here once a bill is checked and prepared."}</Empty>}
      {!!rows.length && (mobile ? (
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {rows.map((p) => (
            <div key={p.id} className="list-row" role="button" tabIndex={0} onClick={() => open({ kind: "payment", id: p.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "payment", id: p.id })}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{p.counterparty}</span><span className="num wrap" style={{ fontSize: 14.5, fontWeight: 600 }}>{p.amount}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}><span style={{ fontSize: 12, color: "var(--muted)" }}>{day(p.created_at)} · {p.rail} · {p.asset}</span><Badge b={paymentBadge(p)} /></div>
            </div>
          ))}
        </div>
      ) : (
        <Table cols="minmax(0,1.6fr) 120px 90px minmax(140px,1fr) 190px 24px" head={["Counterparty", "Rail", "Date", "Amount", "Status", ""]}>
          {rows.map((p) => (
            <div key={p.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: "minmax(0,1.6fr) 120px 90px minmax(140px,1fr) 190px 24px" }} onClick={() => open({ kind: "payment", id: p.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "payment", id: p.id })}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{p.counterparty}</span><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{p.invoice || p.reference}</span></div>
              <span style={{ fontSize: 13, color: "var(--muted)" }}>{p.rail} · {p.asset}</span>
              <span style={{ fontSize: 13, color: "var(--muted)" }}>{day(p.created_at)}</span>
              <span className="num wrap" style={{ textAlign: "right", fontSize: 14.5, fontWeight: 500 }}>{p.amount}</span>
              <span><Badge b={paymentBadge(p)} /></span>
              <Chevron />
            </div>
          ))}
        </Table>
      ))}
    </>
  );
}

function BillsTab() {
  const { mobile, open } = useApp();
  const { data, error, loading } = useLive<BillRow[]>("/api/bills", 10000);
  if (loading) return <Loading />;
  const rows = data || [];
  return (
    <>
      <ErrorLine error={error} />
      {data && !rows.length && <Empty compact={mobile}>No bills yet. Upload one to start.</Empty>}
      {!!rows.length && (mobile ? (
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {rows.map((b) => (
            <div key={b.id} className="list-row" role="button" tabIndex={0} onClick={() => open({ kind: "bill", id: b.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "bill", id: b.id })}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{b.counterparty || "Unknown supplier"}</span><span className="num wrap" style={{ fontSize: 14.5, fontWeight: 600 }}>{b.amount || "—"}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}><span style={{ fontSize: 12, color: "var(--muted)" }}><span className="mono">{b.invoice || "No reference"}</span> · Due {day(b.due_date)}</span><Badge b={badge(b.status)} /></div>
            </div>
          ))}
        </div>
      ) : (
        <Table cols="minmax(0,1.6fr) 100px minmax(140px,1fr) 190px 24px" head={["Supplier", "Due", "Amount", "Status", ""]}>
          {rows.map((b) => (
            <div key={b.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: "minmax(0,1.6fr) 100px minmax(140px,1fr) 190px 24px" }} onClick={() => open({ kind: "bill", id: b.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "bill", id: b.id })}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{b.counterparty || "Unknown supplier"}</span><span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{b.invoice || "No reference"}</span></div>
              <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{day(b.due_date)}</span>
              <span className="num wrap" style={{ textAlign: "right", fontSize: 14.5, fontWeight: 500 }}>{b.amount || "—"}</span>
              <span><Badge b={badge(b.status)} /></span>
              <Chevron />
            </div>
          ))}
        </Table>
      ))}
    </>
  );
}

interface ContactRow { id: string; display_name: string; legal_name: string | null; classifications: string[]; destinations: { id: string; status: string }[] }

function ContactsTab() {
  const { mobile, open } = useApp();
  const { data, error, loading } = useLive<ContactRow[]>("/api/counterparties", 20000);
  if (loading) return <Loading />;
  const rows = data || [];
  const summary = (c: ContactRow) => {
    const verified = c.destinations.filter((d) => d.status === "VERIFIED").length;
    const unverified = c.destinations.filter((d) => d.status === "UNVERIFIED").length;
    return { verified, unverified, type: (c.classifications || []).map(words).join(", ") || "Contact", count: plural(c.destinations.length, "destination") };
  };
  const badges = (c: ContactRow, size: "sm" | "md") => {
    const s = summary(c);
    return (
      <div style={{ display: "flex", gap: mobile ? 4 : 6, flexWrap: "wrap" }}>
        <Badge b={badge("VERIFIED", `${s.verified} verified`)} size={size} />
        {s.unverified > 0 && <Badge b={badge("UNVERIFIED", `${s.unverified} unverified`)} size={size} />}
      </div>
    );
  };
  return (
    <>
      <ErrorLine error={error} />
      {data && !rows.length && <Empty compact={mobile}>No contacts yet. Contacts are added when a bill arrives or through Chat.</Empty>}
      {!!rows.length && (mobile ? (
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {rows.map((c) => {
            const s = summary(c);
            return (
              <div key={c.id} role="button" tabIndex={0} onClick={() => open({ kind: "contact", id: c.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "contact", id: c.id })}
                style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderBottom: "1px solid var(--border)", cursor: "pointer", minHeight: 64 }}>
                <span style={{ width: 38, height: 38, borderRadius: "50%", background: "var(--raised)", display: "grid", placeItems: "center", fontSize: 12, fontWeight: 600, flex: "none" }}>{initials(c.display_name)}</span>
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 5 }}>
                  <span style={{ fontSize: 14.5, fontWeight: 500 }}>{c.display_name}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{s.type} · {s.count}</span>
                  {badges(c, "sm")}
                </div>
                <Chevron />
              </div>
            );
          })}
        </div>
      ) : (
        <Table cols="minmax(0,1.6fr) 130px 140px minmax(200px,1fr) 24px" head={["Name", "Type", "Destinations", "Verification", ""]}>
          {rows.map((c) => {
            const s = summary(c);
            return (
              <div key={c.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: "minmax(0,1.6fr) 130px 140px minmax(200px,1fr) 24px", minHeight: 60 }} onClick={() => open({ kind: "contact", id: c.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "contact", id: c.id })}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
                  <span style={{ width: 36, height: 36, borderRadius: "50%", background: "var(--raised)", display: "grid", placeItems: "center", fontSize: 12, fontWeight: 600, flex: "none" }}>{initials(c.display_name)}</span>
                  <span style={{ fontSize: 14.5, fontWeight: 500 }}>{c.display_name}</span>
                </div>
                <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{s.type}</span>
                <span style={{ fontSize: 13.5 }}>{s.count}</span>
                {badges(c, "md")}
                <Chevron />
              </div>
            );
          })}
        </Table>
      ))}
    </>
  );
}

interface Mandate { id: string; status: string; max_amount: string | null; asset: string; valid_until: string | null; purpose: string | null; counterparty: string; frequency: string | null; next_due: string | null }

function AutomationsTab() {
  const { mobile } = useApp();
  const { data, error, loading } = useLive<Mandate[]>("/api/mandates", 20000);
  return (
    <>
      <div style={{ display: "flex", gap: mobile ? 8 : 10, padding: mobile ? "12px 14px" : "14px 16px", borderRadius: 14, background: "var(--raised)", fontSize: mobile ? 13.5 : 14, lineHeight: 1.5 }}>
        <Icon name="info" size={mobile ? 18 : 20} />
        <span>{!mobile && "A Mandate prepares payments on a schedule. "}<b style={{ fontWeight: 600 }}>Each payment still needs its own approval.</b> To set up a new Mandate, ask in Chat.</span>
      </div>
      {loading && <Loading />}
      <ErrorLine error={error} />
      {data && !data.length && <Empty compact={mobile}>No Mandates yet.</Empty>}
      <div style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))", gap: 12 }}>
        {(data || []).map((m) => <MandateCard key={m.id} m={m} />)}
      </div>
    </>
  );
}

function MandateCard({ m }: { m: Mandate }) {
  const { mobile, isA } = useApp();
  const act = useAction();
  const revoke = useArmed();
  const change = (verb: string) => act.run(async () => { await api(`/api/mandates/${m.id}/${verb}`, { method: "POST", body: {} }); refreshAll(); });
  const cell = (k: string, v: ReactNode) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: mobile ? 11.5 : 12, color: "var(--muted)" }}>{k}</span><span className={k === "Maximum" ? "num" : ""} style={{ fontWeight: k === "Maximum" && !mobile ? 500 : undefined }}>{v}</span></div>
  );
  const btn = { minHeight: 44, padding: mobile ? "0 14px" : "0 16px", fontSize: mobile ? 13 : 13.5 };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 12 : 14, padding: mobile ? 14 : 18, borderRadius: 16, background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
        <span style={{ fontSize: mobile ? 15 : 16, fontWeight: 600 }}>{m.counterparty}</span><Badge b={badge(m.status)} />
      </div>
      {m.purpose && <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{m.purpose}</span>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: mobile ? "8px 12px" : "10px 16px", fontSize: mobile ? 13 : 13.5 }}>
        {cell("Maximum", m.max_amount || "—")}
        {cell("Frequency", m.frequency ? words(m.frequency) : "—")}
        {cell("Next due", m.status === "ACTIVE" && m.next_due ? day(m.next_due) : "—")}
        {cell("Ends", m.valid_until ? day(m.valid_until) : "—")}
      </div>
      {isA && m.status !== "REVOKED" && m.status !== "SUPERSEDED" && (
        <div style={{ display: "flex", gap: mobile ? 6 : 8, flexWrap: "wrap" }}>
          {(m.status === "DRAFT" || m.status === "PAUSED") && <button type="button" className="btn-pill" style={btn} aria-disabled={act.busy || undefined} onClick={() => change("activate")}>Activate</button>}
          {m.status === "ACTIVE" && <button type="button" className="btn-pill" style={btn} aria-disabled={act.busy || undefined} onClick={() => change("pause")}>Pause</button>}
          <button type="button" className="btn-pill btn-danger" style={{ ...btn, background: revoke.armed ? "var(--danger-bg)" : undefined }} aria-disabled={act.busy || undefined}
            onClick={() => { if (!revoke.armed) return revoke.arm(); revoke.disarm(); change("revoke"); }}>{revoke.armed ? "Tap again to revoke" : "Revoke"}</button>
        </div>
      )}
      <ErrorLine error={act.error} />
    </div>
  );
}
