import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../app/context";
import { refreshAll, useAction, useLive } from "../hooks";
import { badge, dateTime, decisionBadge, firstName, hm, label, words } from "../lib";
import { Badge, ErrorLine, Facts, Icon, LinkChip } from "../ui";
import { Checks, DecisionBox, Loading, RecordLayout, SecureBox } from "./Layout";

interface Card {
  approval_id: string; status: string; hash: string; expires_at: string; decided_by: string | null; decided_at: string | null; decided_reason: string | null;
  intent_id: string; payment_status: string; counterparty: string; destination: string; destination_status: string; destination_id: string;
  rail: string; asset: string; chain: string | null; token_contract_short: string | null; source_account: string; amount: string; max_fee: string; reference: string;
  warnings: string[]; requires_review: boolean; decision: { decision: string; explanation: string | null } | null;
  checks: { check_name: string; rule_id: string; outcome: string; reason: string }[];
  suggestions: { text: string; cites: { kind: string; id: string; label: string }[] }[];
  added_and_verified_by_same_person: boolean; destination_verified_by: string | null; expired: boolean; can_decide: boolean;
  obligation_id: string; invoice: string | null; due_date: string | null;
}
interface Outcome { won: boolean; status: string; message: string }

const OK = ["PASS", "NOT_APPLICABLE", "ELIGIBLE_FOR_APPROVAL"];
const DETAIL_NAMES: Record<string, string> = { iban: "IBAN", sort_code: "Sort code", account_number: "Account number", address: "Wallet address" };

/** The server's answer to a tap, in the design's words and colours. The message itself always comes from the server. */
function describe(o: Outcome, reason: string): { tone: string; icon: string; title: string; body: string; stale?: boolean } {
  if (o.won && o.status === "APPROVED") return { tone: "ok", icon: "check_circle", title: o.message, body: "Follow it in the payment trace." };
  if (o.won && o.status === "REJECTED") return { tone: "danger", icon: "cancel", title: "Rejected. Nothing will be sent.", body: reason ? `Reason: ${reason}` : "No reason given." };
  const m = o.message.toLowerCase();
  if (m.includes("already")) return { tone: "info", icon: "group", title: o.message, body: "Your tap was recorded. The payment can only be sent once." };
  if (m.includes("changed")) return { tone: "warn", icon: "refresh", title: o.message, body: "Nothing was approved. Tick the review again before deciding.", stale: true };
  if (m.includes("expired")) return { tone: "neutral", icon: "timer_off", title: o.message, body: "Ask for a new approval in Chat. Nothing was sent." };
  if (m.includes("paused")) return { tone: "neutral", icon: "pause_circle", title: o.message, body: "An Approver can resume payments from the banner." };
  if (m.includes("replaced") || m.includes("cancelled")) return { tone: "neutral", icon: "swap_horiz", title: o.message, body: "Review the new bill when it's ready." };
  return { tone: "warn", icon: "info", title: o.message, body: "Nothing was approved by this tap." };
}

export function ApprovalRecord({ id }: { id: string }) {
  const { isA, isO, isV, open, controls } = useApp();
  const { data: card, error } = useLive<Card>(`/api/approvals/${id}`, 5000);
  const [reviewed, setReviewed] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [changed, setChanged] = useState(false);
  const [checksOpen, setChecksOpen] = useState(true);
  const [revealed, setRevealed] = useState<Record<string, string> | null>(null);
  const reveal = useAction();
  const decide = useAction();
  const shownHash = useRef<string | null>(null);

  // What was reviewed must be what is approved. If the payment changes underneath, the review doesn't carry over.
  useEffect(() => {
    if (!card) return;
    if (shownHash.current && shownHash.current !== card.hash) {
      setChanged(true);
      setReviewed(false);
      setRevealed(null);
    }
    shownHash.current = card.hash;
  }, [card]);
  useEffect(() => { setReviewed(false); setOutcome(null); setRejectOpen(false); setReason(""); setRevealed(null); setChanged(false); shownHash.current = null; }, [id]);

  if (!card) return <Loading error={error} />;

  const paused = !!controls?.payments_paused;
  const pending = card.status === "PENDING" && !card.expired;
  const dec = card.decision?.decision || "ELIGIBLE_FOR_APPROVAL";
  const blocked = dec === "BLOCK" || dec === "NEEDS_INFORMATION";
  const needsTick = card.requires_review;
  const res = outcome ? describe(outcome, reason) : null;
  const decidedNow = !!outcome && outcome.won;
  const footer = pending && !decidedNow;
  const approveOff = (needsTick && !reviewed) || paused;

  const flagged = card.checks.filter((c) => !OK.includes(c.outcome));
  const extra = (card.warnings || []).filter((w) => !flagged.some((c) => c.reason === w));
  const warnings = [...flagged.map((c) => ({ text: c.reason, b: badge(c.outcome) })), ...extra.map((w) => ({ text: w, b: badge("NEEDS_REVIEW") }))];

  const st = card.expired && card.status === "PENDING" ? badge("EXPIRED")
    : card.status !== "PENDING" && card.decided_by ? badge(card.status, `${label(card.status)} · ${firstName(card.decided_by)} ${hm(card.decided_at)}`) : badge(card.status);
  const closedText = card.status === "REJECTED" ? `Rejected by ${card.decided_by || "an Approver"} at ${dateTime(card.decided_at)}${card.decided_reason ? `. Reason: ${card.decided_reason}` : ""}`
    : card.status === "CANCELLED" ? `Cancelled${card.decided_at ? ` at ${dateTime(card.decided_at)}` : ""}. ${card.decided_reason || "The bill was replaced or its destination was revoked. Nothing was sent."}`
    : `Approved by ${card.decided_by || "an Approver"} at ${dateTime(card.decided_at)}`;

  const send = (decision: "APPROVED" | "REJECTED") => decide.run(async () => {
    // The hash on screen goes with the tap, so the server refuses it if the payment has changed since.
    const result = await api<Outcome>(`/api/approvals/${id}/decide`, { method: "POST", body: { hash: card.hash, decision, reviewed: decision === "APPROVED" && reviewed, reason: decision === "REJECTED" ? reason.trim() || null : null } });
    setOutcome(result);
    if (!result.won && result.message.toLowerCase().includes("changed")) setReviewed(false);
    refreshAll();
  });
  const onReject = () => (rejectOpen ? send("REJECTED") : setRejectOpen(true));
  const onApprove = () => { if (!approveOff) send("APPROVED"); };

  const crypto = card.rail === "CRYPTO";
  const facts: [string, React.ReactNode, { mono?: boolean; badge?: React.ReactNode }?][] = [
    ["Destination", card.destination, { mono: true, badge: <Badge b={badge(card.destination_status)} size="sm" /> }],
    ["Rail", crypto ? "Crypto" : "Bank"],
    ["Asset", card.asset],
    ...(crypto ? [["Chain", card.chain], ["Token contract", card.token_contract_short, { mono: true }]] as any : []),
    ["Source account", card.source_account],
    ["Maximum fee", card.max_fee],
    ["Reference", card.reference, { mono: true }],
    ["Approval expires", dateTime(card.expires_at)],
  ];

  const footerEl = footer ? (
    <>
      {isO && <div style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, fontSize: 14, color: "var(--muted)" }}><Icon name="lock_person" size={20} />Only an Approver can decide.</div>}
      {isV && <div style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, fontSize: 14, color: "var(--muted)" }}><Icon name="visibility" size={20} />View only.</div>}
      {isA && blocked && <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13.5, color: "var(--muted)" }}><Icon name="block" size={18} style={{ color: "var(--danger)" }} />{dec === "BLOCK" ? "Blocked payments can't be approved. You can reject it." : "Approve is unavailable until the missing information is resolved. You can reject it."}</div>}
      {isA && warnings.length > 0 && <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--warn)", fontWeight: 500 }}><Icon name="warning" size={18} />{warnings.length} warning{warnings.length === 1 ? "" : "s"} on this payment. Scroll up to read them.</div>}
      {isA && needsTick && !blocked && (
        <button type="button" role="checkbox" aria-checked={reviewed} onClick={() => setReviewed(!reviewed)} style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 44, cursor: "pointer", fontSize: 14.5, fontWeight: 500, background: "transparent", border: 0, padding: 0, textAlign: "left" }}>
          <Icon name={reviewed ? "check_box" : "check_box_outline_blank"} size={24} fill={reviewed} />I've reviewed the warnings
        </button>
      )}
      {isA && rejectOpen && (
        <label className="field">Reason for rejecting (optional)
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Amount doesn't match the quote" autoFocus />
        </label>
      )}
      <ErrorLine error={decide.error} />
      {isA && !blocked && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <button type="button" className="btn-pill btn-strong" aria-busy={decide.busy || undefined} onClick={onReject}><Icon name="close" size={20} />{rejectOpen ? "Confirm reject" : "Reject"}</button>
          <button type="button" className="btn-pill btn-strong" aria-disabled={approveOff || decide.busy || undefined} onClick={onApprove}
            title={paused ? "Payments are paused" : approveOff ? "Tick the review first" : undefined}><Icon name={decide.busy ? "progress_activity" : "check"} size={20} className={decide.busy ? "spin" : ""} />Approve</button>
        </div>
      )}
      {isA && blocked && (
        <button type="button" className="btn-pill btn-strong" aria-busy={decide.busy || undefined} onClick={onReject}><Icon name="close" size={20} />{rejectOpen ? "Confirm reject" : "Reject"}</button>
      )}
    </>
  ) : undefined;

  return (
    <RecordLayout footer={footerEl}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span style={{ fontSize: 15, color: "var(--muted)" }}>{card.counterparty}</span>
        <span className="num wrap" style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.035em", lineHeight: 1.05 }}>{card.amount}</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}><Badge b={st} size="lg" /></div>
      </div>

      {changed && !res && (
        <div role="status" style={{ display: "flex", gap: 12, padding: "14px 16px", borderRadius: 14, background: "var(--warn-bg)", border: "1px solid var(--warn-bd)" }}>
          <Icon name="refresh" size={22} style={{ color: "var(--warn)" }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}><span style={{ fontSize: 15, fontWeight: 600 }}>This payment changed while you had it open.</span><span style={{ fontSize: 13.5, color: "var(--muted)" }}>Review it again before deciding.</span></div>
        </div>
      )}
      {res && (
        <div role="status" style={{ display: "flex", gap: 12, padding: "14px 16px", borderRadius: 14, background: `var(--${res.tone}-bg)`, border: `1px solid var(--${res.tone}-bd)`, animation: "rise .2s" }}>
          <Icon name={res.icon} size={22} style={{ color: `var(--${res.tone})` }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>{res.title}</span>
            <span style={{ fontSize: 13.5, color: "var(--muted)", lineHeight: 1.45 }}>{res.body}</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
              <button type="button" className="link-btn" style={{ background: "transparent", border: 0, padding: 0 }} onClick={() => open({ kind: "payment", id: card.intent_id }, true)}>Open payment trace<Icon name="arrow_forward" size={16} /></button>
              {res.stale && <button type="button" className="link-btn" style={{ background: "transparent", border: 0, padding: 0 }} onClick={() => { setOutcome(null); setChanged(false); }}>Review again</button>}
            </div>
          </div>
        </div>
      )}
      {card.status === "PENDING" && card.expired && !res && (
        <div style={{ display: "flex", gap: 12, padding: "14px 16px", borderRadius: 14, background: "var(--neutral-bg)", border: "1px solid var(--neutral-bd)" }}>
          <Icon name="timer_off" size={22} style={{ color: "var(--neutral)" }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}><span style={{ fontSize: 15, fontWeight: 600 }}>Expired</span><span style={{ fontSize: 13.5, color: "var(--muted)" }}>This approval expired at {dateTime(card.expires_at)}. Nothing was sent. Ask for a new approval in Chat.</span></div>
        </div>
      )}
      {card.status !== "PENDING" && !res && (
        <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--raised)", fontSize: 14, alignItems: "center" }}><Icon name="history" size={20} style={{ color: "var(--muted)" }} />{closedText}</div>
      )}
      {isA && pending && paused && !res && (
        <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--neutral-bg)", border: "1px solid var(--neutral-bd)", fontSize: 14, alignItems: "center" }}><Icon name="pause_circle" size={20} />New payments are paused. Approve is unavailable until payments resume.</div>
      )}

      <DecisionBox b={card.decision ? decisionBadge(card.decision.decision) : null}>{card.decision?.explanation || (card.decision ? label(card.decision.decision) : "No decision recorded.")}</DecisionBox>

      {card.added_and_verified_by_same_person && (
        <div style={{ display: "flex", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--warn-bg)", border: "1px solid var(--warn-bd)", fontSize: 14, alignItems: "flex-start" }}>
          <Icon name="group_off" size={20} style={{ color: "var(--warn)" }} /><span><b style={{ fontWeight: 600 }}>Notice.</b> Added and verified by the same person{card.destination_verified_by ? ` (${card.destination_verified_by})` : ""}.</span>
        </div>
      )}

      {warnings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className="kicker">Warnings</span>
          {warnings.map((w, i) => (
            <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "12px 14px", borderRadius: 12, background: w.b.bg, border: `1px solid ${w.b.bd}` }}>
              <Icon name={w.b.icon} size={20} style={{ color: w.b.fg }} />
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: 12, fontWeight: 600, color: w.b.fg }}>{w.b.label}</span><span style={{ fontSize: 14, lineHeight: 1.45 }}>{w.text}</span></div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column" }}>
        <span className="kicker" style={{ paddingBottom: 6 }}>Facts</span>
        <Facts rows={facts} />
      </div>

      {isA && revealed && (
        <SecureBox title="Full details" foot={
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12.5, opacity: 0.85 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}><Icon name="visibility" size={16} />Viewing is recorded in Audit.</span>
            <button type="button" onClick={() => setRevealed(null)} style={{ cursor: "pointer", textDecoration: "underline", minHeight: 32, background: "transparent", border: 0, color: "inherit", fontSize: 12.5 }}>Hide</button>
          </div>
        }>
          {Object.entries(revealed).map(([k, v]) => (
            <div key={k} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ fontSize: 11.5, opacity: 0.7 }}>{DETAIL_NAMES[k] || words(k)}</span>
              <span className="mono wrap" style={{ fontSize: 16, letterSpacing: ".02em" }}>{String(v)}</span>
            </div>
          ))}
        </SecureBox>
      )}
      {isA && !revealed && (
        <button type="button" className="btn-pill" aria-busy={reveal.busy || undefined} style={{ gap: 8 }}
          onClick={() => reveal.run(async () => { const r = await api<{ details: Record<string, string> }>(`/api/approvals/${id}/reveal`); setRevealed(r.details || {}); })}>
          <Icon name="lock" size={18} />Reveal full details
        </button>
      )}
      <ErrorLine error={reveal.error} />

      <Checks checks={card.checks} open={checksOpen} onToggle={() => setChecksOpen(!checksOpen)} />

      {card.suggestions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 16, borderRadius: 16, border: "1.5px dashed var(--border-strong)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600 }}>
            <span style={{ width: 22, height: 22, borderRadius: 6, background: "var(--accent)", color: "#0B0B0C", display: "grid", placeItems: "center" }}><Icon name="auto_awesome" size={15} /></span>
            Suggestions from the agent. Not facts.
          </div>
          {card.suggestions.map((g, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, lineHeight: 1.45, color: "var(--muted)" }}>
              <span>{g.text}</span>
              {!!g.cites.length && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {g.cites.map((c) => (
                    <button key={`${c.kind}${c.id}`} type="button" onClick={() => open(c, true)} className="mono"
                      style={{ display: "inline-flex", alignItems: "center", gap: 4, minHeight: 28, padding: "0 10px", borderRadius: 999, background: "var(--raised)", color: "var(--text)", fontSize: 12, cursor: "pointer", border: 0 }}>
                      <Icon name="link" size={14} />{c.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <LinkChip icon="timeline" onClick={() => open({ kind: "payment", id: card.intent_id }, true)}>Payment trace</LinkChip>
        <LinkChip icon="description" onClick={() => open({ kind: "bill", id: card.obligation_id }, true)}>Bill</LinkChip>
      </div>
      <span className="mono" style={{ fontSize: 11.5, color: "var(--subtle)", overflowWrap: "anywhere" }}>Payment fingerprint, sent with your decision: {card.hash}</span>
    </RecordLayout>
  );
}
