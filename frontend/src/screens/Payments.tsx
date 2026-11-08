import type { ReactNode } from "react";
import { api } from "../api";
import { useApp, type PayTab } from "../app/context";
import { refreshAll, useAction, useArmed, useLive } from "../hooks";
import { badge, day, plural, words } from "../lib";
import { Badge, Empty, ErrorLine, Icon, ListCard, PageTitle, Segments, Skeleton, Stat, Tile } from "../ui";

export interface PaymentRow { id: string; amount: string; status: string; reference: string; invoice: string | null; counterparty: string; counterparty_id: string; obligation_id: string; evidence: string | null; attempt_state: string | null; rail: string; asset: string; created_at: string }
export interface BillRow { id: string; invoice: string | null; amount: string | null; due_date: string | null; status: string; counterparty: string | null; counterparty_id: string | null; created_at: string }

export const paymentBadge = (p: { status: string; attempt_state?: string | null }) => (p.attempt_state === "MANUALLY_RESOLVED" ? badge("MANUALLY_RESOLVED") : badge(p.status));

const TABS: [PayTab, string][] = [["payments", "Payments"], ["bills", "Bills"], ["contacts", "Contacts"], ["automations", "Automations"]];

export function Payments() {
  const { mobile, nav, setPayTab, canWrite, requestChat } = useApp();
  const tab = nav.payTab;
  const btn = mobile ? { height: 44, minHeight: 0, padding: "0 12px", fontSize: 13.5, fontWeight: 600, whiteSpace: "nowrap" as const } : { height: 46, minHeight: 0, padding: "0 18px 0 14px", fontSize: 14.5, fontWeight: 600, whiteSpace: "nowrap" as const };
  const actions = canWrite && (
    <div style={mobile ? { display: "grid", gridAutoFlow: "column", gridAutoColumns: "minmax(0,1fr)", gap: 8 } : { marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button type="button" className="btn-pill" onClick={() => requestChat({ attach: true, draft: "Pay this bill" })} style={{ ...btn, background: "var(--surface)" }}><Icon name="upload_file" size={19} />Upload a bill</button>
      <button type="button" className="btn-pill btn-primary press" onClick={() => requestChat({ draft: "I want to pay " })} style={btn}><Icon name="add" size={19} />Add payment</button>
    </div>
  );
  const tabs = <Segments items={TABS} value={tab} onChange={setPayTab} mobile={mobile} />;
  return (
    <div data-screen-label={mobile ? "M Payments" : "Payments"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "32px 32px 64px", display: "flex", flexDirection: "column", gap: 22, maxWidth: 1180, margin: "0 auto" }}>
      <PageTitle mobile={mobile} title="Payments" />
      {mobile ? <>{tabs}{actions}</> : <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>{tabs}{actions}</div>}
      {tab === "payments" && <PaymentsTab />}
      {tab === "bills" && <BillsTab />}
      {tab === "contacts" && <ContactsTab />}
      {tab === "automations" && <AutomationsTab />}
    </div>
  );
}

function Loading() {
  return <Skeleton height={68} count={3} radius={18} />;
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

/** The first column of a web row: a tile, the name and a reference under it. */
function Who({ name, sub, round }: { name: string; sub?: ReactNode; round?: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
      <Tile name={name} round={round} />
      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <span className="wrap" style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.3 }}>{name}</span>
        {sub && <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</span>}
      </div>
    </div>
  );
}

/** A phone row: tile, name and amount on top, details and status below. */
export function MRow({ name, amount, meta, status, onOpen }: { name: string; amount?: ReactNode; meta: ReactNode; status: ReactNode; onOpen: () => void }) {
  return (
    <button type="button" className="m-row" onClick={onOpen}>
      <Tile name={name} size={42} />
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "4px 8px", alignItems: "baseline", flexWrap: "wrap" }}>
          <span style={{ fontSize: 14.5, fontWeight: 600, lineHeight: 1.3, minWidth: 0 }}>{name}</span>
          {amount && <span className="num" style={{ flex: "none", fontSize: 14.5, fontWeight: 600 }}>{amount}</span>}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", fontSize: 12, color: "var(--muted)" }}>{meta}</span>
          {status}
        </div>
      </div>
    </button>
  );
}

const amountCell = { textAlign: "right" as const, fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" };

const PAY_COLS = "minmax(0,2fr) 96px 72px minmax(0,1fr) 172px 20px";
const BILL_COLS = "minmax(0,2fr) 88px minmax(0,1fr) 172px 20px";
const CONTACT_COLS = "minmax(0,1.6fr) 120px 120px minmax(200px,1.2fr) 20px";

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
        <ListCard mobile>
          {rows.map((p) => <MRow key={p.id} name={p.counterparty} amount={p.amount} meta={`${day(p.created_at)} · ${p.rail}`} status={<Badge b={paymentBadge(p)} size="lg" />} onOpen={() => open({ kind: "payment", id: p.id })} />)}
        </ListCard>
      ) : (
        <Table cols={PAY_COLS} head={["Counterparty", "Rail", "Date", "Amount", "Status", ""]}>
          {rows.map((p) => (
            <div key={p.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: PAY_COLS }} onClick={() => open({ kind: "payment", id: p.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "payment", id: p.id })}>
              <Who name={p.counterparty} sub={p.invoice || p.reference} />
              <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{p.rail}</span>
              <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{day(p.created_at)}</span>
              <span className="num wrap" style={amountCell}>{p.amount}</span>
              <span><Badge b={paymentBadge(p)} size="lg" /></span>
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
        <ListCard mobile>
          {rows.map((b) => <MRow key={b.id} name={b.counterparty || "Unknown supplier"} amount={b.amount || "—"} meta={<><span className="mono">{b.invoice || "No reference"}</span> · Due {day(b.due_date)}</>} status={<Badge b={badge(b.status)} size="lg" />} onOpen={() => open({ kind: "bill", id: b.id })} />)}
        </ListCard>
      ) : (
        <Table cols={BILL_COLS} head={["Supplier", "Due", "Amount", "Status", ""]}>
          {rows.map((b) => (
            <div key={b.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: BILL_COLS }} onClick={() => open({ kind: "bill", id: b.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "bill", id: b.id })}>
              <Who name={b.counterparty || "Unknown supplier"} sub={b.invoice || "No reference"} />
              <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{day(b.due_date)}</span>
              <span className="num wrap" style={amountCell}>{b.amount || "—"}</span>
              <span><Badge b={badge(b.status)} size="lg" /></span>
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
        <ListCard mobile>
          {rows.map((c) => {
            const s = summary(c);
            return (
              <button key={c.id} type="button" className="m-row" onClick={() => open({ kind: "contact", id: c.id })}>
                <Tile name={c.display_name} size={42} round />
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                  <span style={{ fontSize: 14.5, fontWeight: 600 }}>{c.display_name}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>{s.type} · {s.count}</span>
                  {badges(c, "md")}
                </div>
                <Chevron />
              </button>
            );
          })}
        </ListCard>
      ) : (
        <Table cols={CONTACT_COLS} head={["Name", "Type", "Destinations", "Verification", ""]}>
          {rows.map((c) => {
            const s = summary(c);
            return (
              <div key={c.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: CONTACT_COLS }} onClick={() => open({ kind: "contact", id: c.id })} onKeyDown={(e) => e.key === "Enter" && open({ kind: "contact", id: c.id })}>
                <Who name={c.display_name} round />
                <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{s.type}</span>
                <span style={{ fontSize: 14 }}>{s.count}</span>
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
      <div className="ink-card" style={{ display: "flex", alignItems: mobile ? "flex-start" : "center", gap: mobile ? 12 : 14, padding: mobile ? 14 : "16px 18px", borderRadius: mobile ? 20 : 22, fontSize: mobile ? 13.5 : 14.5, lineHeight: mobile ? 1.45 : 1.5, border: 0 }}>
        <span style={{ width: mobile ? 32 : 38, height: mobile ? 32 : 38, flex: "none", borderRadius: mobile ? 10 : 12, background: "#D4F53C", color: "#0B0B0C", display: "grid", placeItems: "center" }}><Icon name="schedule" size={mobile ? 18 : 20} /></span>
        <span>{!mobile && "A Mandate prepares payments on a schedule. "}<b style={{ fontWeight: 600, color: "#D4F53C" }}>Each payment still needs its own approval.</b> To set up a new Mandate, ask in Chat.</span>
      </div>
      {loading && <Loading />}
      <ErrorLine error={error} />
      {data && !data.length && <Empty compact={mobile}>No Mandates yet.</Empty>}
      <div style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))", gap: 14 }}>
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
  const btn = { minHeight: 44, padding: "0 18px", fontSize: 13.5, fontWeight: 600 };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: mobile ? 16 : 20, borderRadius: mobile ? 22 : 24, background: "var(--surface)", border: "1px solid var(--border)", boxShadow: "var(--shadow-1)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Tile name={m.counterparty} size={42} />
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 16, fontWeight: 600 }}>{m.counterparty}</span>
          <span style={{ alignSelf: "flex-start" }}><Badge b={badge(m.status)} /></span>
        </div>
      </div>
      {m.purpose && <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{m.purpose}</span>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <Stat k="Maximum" v={m.max_amount || "—"} />
        <Stat k="Frequency" v={m.frequency ? words(m.frequency) : "—"} />
        <Stat k="Next due" v={m.status === "ACTIVE" && m.next_due ? day(m.next_due) : "—"} />
        <Stat k="Ends" v={m.valid_until ? day(m.valid_until) : "—"} />
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
