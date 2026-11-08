import type { ReactNode } from "react";
import { Badge, ErrorLine, Icon, Skeleton } from "../ui";
import { badge, type B } from "../lib";

/** A record's scrolling body, with an optional footer that stays in view (the approval buttons). */
export function RecordLayout({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div className="overlay-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "20px 20px 28px", display: "flex", flexDirection: "column", gap: 20 }}>{children}</div>
      {footer && (
        <div style={{ flex: "none", borderTop: "1px solid var(--border)", background: "var(--surface)", padding: "14px 20px 16px", display: "flex", flexDirection: "column", gap: 12, boxShadow: "0 -8px 24px rgba(11,11,12,.05)" }}>{footer}</div>
      )}
    </div>
  );
}

export function Loading({ error }: { error?: string | null }) {
  return (
    <RecordLayout>
      {error ? <ErrorLine error={error} /> : <><Skeleton height={90} count={1} /><Skeleton height={60} count={3} /></>}
    </RecordLayout>
  );
}

/** The big number at the top of a record. */
export function Hero({ over, amount, badges, after }: { over: ReactNode; amount: ReactNode; badges?: ReactNode; after?: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span style={{ fontSize: 15, color: "var(--muted)" }}>{over}</span>
      <span className="num wrap" style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.035em", lineHeight: 1.05 }}>{amount}</span>
      {(badges || after) && <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>{badges}{after}</div>}
    </div>
  );
}

export function Section({ title, right, children, gap = 6 }: { title: ReactNode; right?: ReactNode; children: ReactNode; gap?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}><span className="kicker">{title}</span>{right}</div>
      {children}
    </div>
  );
}

export function DecisionBox({ b, children, foot }: { b?: B | null; children: ReactNode; foot?: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 16, borderRadius: 16, background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><span className="kicker">Decision</span>{b && <Badge b={b} size="lg" />}</div>
      <p style={{ margin: 0, fontSize: 14.5, lineHeight: 1.5, textWrap: "pretty" } as any}>{children}</p>
      {foot && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{foot}</span>}
    </div>
  );
}

export function Checks({ checks, title = "All checks", open, onToggle }: { checks: { check_name: string; outcome: string; reason: string }[]; title?: string; open?: boolean; onToggle?: () => void }) {
  const shown = open === undefined ? true : open;
  return (
    <div style={{ display: "flex", flexDirection: "column", border: "1px solid var(--border)", borderRadius: 16, overflow: "hidden", background: "var(--surface)" }}>
      {onToggle ? (
        <button type="button" onClick={onToggle} aria-expanded={shown} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, minHeight: 48, padding: "0 16px", cursor: "pointer", background: "transparent", border: 0 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>Checks <span style={{ fontWeight: 400, color: "var(--muted)" }}>· {checks.length} checks</span></span>
          <Icon name={shown ? "expand_less" : "expand_more"} size={22} style={{ color: "var(--muted)" }} />
        </button>
      ) : <div style={{ padding: "14px 16px", fontSize: 14, fontWeight: 600 }}>{title}</div>}
      {shown && checks.map((c, i) => <CheckRow key={i} c={c} />)}
    </div>
  );
}

function CheckRow({ c }: { c: { check_name: string; outcome: string; reason: string } }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: "11px 16px", borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 500 }}>{c.check_name}</span><Badge b={badge(c.outcome)} size="sm" />
      </div>
      <span style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.45 }}>{c.reason}</span>
    </div>
  );
}

export function RowLink({ onClick, left, right }: { onClick: () => void; left: ReactNode; right: ReactNode }) {
  return (
    <button type="button" onClick={onClick} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", minHeight: 48, padding: "6px 4px", borderBottom: "1px solid var(--border)", cursor: "pointer", background: "transparent", borderLeft: 0, borderRight: 0, borderTop: 0, textAlign: "left", width: "100%" }}>
      <span style={{ fontSize: 14 }}>{left}</span>{right}
    </button>
  );
}

export function SecureBox({ title, children, foot }: { title: string; children: ReactNode; foot?: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 16, borderRadius: 14, background: "var(--secure-bg)", color: "var(--secure-fg)", border: "1px solid var(--secure-bd)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 600, letterSpacing: ".05em", textTransform: "uppercase" }}><Icon name="lock_open" size={18} />{title}</div>
      {children}
      {foot}
    </div>
  );
}
