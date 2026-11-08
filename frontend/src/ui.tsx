import { useState, type CSSProperties, type ReactNode } from "react";
import { initials, orgInitials, type B } from "./lib";

export function Icon({ name, size = 20, fill, style, className = "" }: { name: string; size?: number; fill?: boolean; style?: CSSProperties; className?: string }) {
  return <span aria-hidden="true" className={`ms ${fill ? "ms-fill" : ""} ${className}`} style={{ fontSize: size, ...style }}>{name}</span>;
}

export function Badge({ b, size = "md", icon = true, style }: { b: B; size?: "xs" | "sm" | "md" | "lg"; icon?: boolean; style?: CSSProperties }) {
  const cls = size === "md" ? "bdg" : `bdg bdg-${size}`;
  return (
    <span className={cls} style={{ color: b.fg, background: b.bg, borderColor: b.bd, borderStyle: b.bs, ...style }}>
      {icon && <Icon name={b.icon} size={size === "lg" ? 15 : size === "xs" ? 13 : 14} />}
      {b.label}
    </span>
  );
}

type BtnKind = "outline" | "primary" | "danger" | "strong";
export function Btn({ children, onClick, kind = "outline", disabled, busy, icon, style, label, type = "button" }: {
  children?: ReactNode; onClick?: () => void; kind?: BtnKind; disabled?: boolean; busy?: boolean; icon?: string; style?: CSSProperties; label?: string; type?: "button" | "submit";
}) {
  const cls = `btn-pill ${kind === "primary" ? "btn-primary" : kind === "danger" ? "btn-danger" : kind === "strong" ? "btn-strong" : ""}`;
  const off = disabled || busy;
  return (
    <button type={type} className={cls} aria-disabled={off || undefined} aria-label={label} aria-busy={busy || undefined} style={style}
      onClick={() => { if (!off && onClick) onClick(); }}>
      {busy ? <Icon name="progress_activity" size={18} className="spin" /> : icon ? <Icon name={icon} size={18} /> : null}
      {children}
    </button>
  );
}

export function IconBtn({ name, label, onClick, active, size = 22, style, badge }: { name: string; label: string; onClick: () => void; active?: boolean; size?: number; style?: CSSProperties; badge?: ReactNode }) {
  return (
    <button type="button" className="icon-btn" aria-label={label} onClick={onClick} style={{ position: "relative", background: active ? "var(--raised)" : undefined, ...style }}>
      <Icon name={name} size={size} />
      {badge}
    </button>
  );
}

export function Skeleton({ height, count = 2, radius = 12 }: { height: number; count?: number; radius?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-label="Loading" role="status">
      {Array.from({ length: count }, (_, i) => <div key={i} className="skeleton" style={{ height, borderRadius: radius, animationDelay: `${i * 0.15}s` }} />)}
    </div>
  );
}

export function Empty({ children, compact }: { children: ReactNode; compact?: boolean }) {
  return <div className="empty" style={compact ? { padding: "40px 16px", fontSize: 14 } : undefined}>{children}</div>;
}

type NoticeTone = "danger" | "warn" | "ok" | "info" | "neutral" | "raised";
export function Notice({ tone = "neutral", icon, children, style, role }: { tone?: NoticeTone; icon?: string; children: ReactNode; style?: CSSProperties; role?: string }) {
  const colours: CSSProperties = tone === "raised" ? { background: "var(--raised)" } : { background: `var(--${tone}-bg)`, border: `1px solid var(--${tone}-bd)` };
  return (
    <div className="notice" role={role ?? (tone === "danger" ? "alert" : undefined)} style={{ ...colours, ...style }}>
      {icon && <Icon name={icon} size={20} style={{ color: tone === "raised" || tone === "neutral" ? undefined : `var(--${tone})` }} />}
      <div style={{ minWidth: 0, flex: 1 }}>{children}</div>
    </div>
  );
}

export function ErrorLine({ error }: { error?: string | null }) {
  if (!error) return null;
  return <span role="alert" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--danger)" }}><Icon name="error" size={16} />{error}</span>;
}

export function Kicker({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span className="kicker" style={style}>{children}</span>;
}

/** A screen's heading: Unbounded at 40 on the web, 26 on a phone. `children` sits to its right. */
export function PageTitle({ title, sub, mobile, children }: { title: ReactNode; sub?: ReactNode; mobile?: boolean; children?: ReactNode }) {
  const heading = (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 10, minWidth: 0 }}>
      <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : 40, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: mobile ? 1.15 : 1.1 }}>{title}</h1>
      {sub && <span style={{ fontSize: mobile ? 13 : 15, color: "var(--muted)" }}>{sub}</span>}
    </div>
  );
  if (!children) return mobile ? <div style={{ padding: "2px 2px 0" }}>{heading}</div> : heading;
  return (
    <div style={{ display: "flex", alignItems: mobile ? "center" : "flex-end", justifyContent: "space-between", gap: mobile ? 8 : 16, flexWrap: "wrap", padding: mobile ? "2px 2px 0" : undefined }}>
      {heading}
      {children}
    </div>
  );
}

/** Pill tabs, as on Payments, Expenses and Audit. On a phone they share the width, or scroll when `scroll` is set. */
export function Segments<T extends string>({ items, value, onChange, mobile, wrap, scroll }: { items: [T, string][]; value: T; onChange: (v: T) => void; mobile?: boolean; wrap?: boolean; scroll?: boolean }) {
  const grid = mobile && !scroll;
  return (
    <div role="tablist" className={scroll ? "scroll-x-none" : undefined}
      style={{ display: grid ? "grid" : "flex", gridTemplateColumns: grid ? `repeat(${items.length}, minmax(0,1fr))` : undefined, gap: mobile ? 3 : 4, padding: mobile ? 4 : 5, borderRadius: 999, background: "var(--raised)", alignSelf: mobile ? "stretch" : "flex-start", flexWrap: wrap ? "wrap" : undefined, overflowX: scroll ? "auto" : undefined, maxWidth: "100%" }}>
      {items.map(([k, l]) => {
        const on = k === value;
        return (
          <button key={k} type="button" role="tab" aria-selected={on} onClick={() => onChange(k)}
            style={{ flex: scroll ? "none" : undefined, minHeight: 40, padding: grid ? "0 6px" : mobile ? "0 14px" : "0 18px", borderRadius: 999, display: "grid", placeItems: "center", fontSize: mobile ? 12.5 : 14.5, fontWeight: 600, cursor: "pointer", border: 0, whiteSpace: "nowrap", background: on ? "var(--surface)" : "transparent", color: on ? "var(--text)" : "var(--muted)", boxShadow: on ? "var(--shadow-1)" : "none", transition: "background .15s" }}>
            {l}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A profile picture with the initials underneath, so a missing or broken picture still reads.
 * People are round; an organisation is a rounded square.
 */
export function Avatar({ name, src, size, square, ring, style }: { name?: string | null; src?: string | null; size: number; square?: boolean; ring?: string; style?: CSSProperties }) {
  const [failed, setFailed] = useState<string | null>(null);
  const show = !!src && failed !== src;
  return (
    <span aria-hidden="true" style={{ position: "relative", flex: "none", width: size, height: size, borderRadius: square ? Math.round(size * 0.3) : "50%", background: square ? "var(--text)" : "var(--raised)", color: square ? "var(--bg)" : "var(--text)", display: "grid", placeItems: "center", fontSize: Math.max(10, Math.round(size * (square ? 0.37 : 0.35))), fontWeight: 600, overflow: "hidden", boxShadow: ring ? `0 0 0 2px var(--surface), 0 0 0 4px ${ring}` : undefined, ...style }}>
      {square ? orgInitials(name) : initials(name)}
      {show && <img src={src!} alt="" onError={() => setFailed(src!)} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />}
    </span>
  );
}

/** The initials tile for a supplier or contact, on list rows. */
export function Tile({ name, size = 44, round, style }: { name?: string | null; size?: number; round?: boolean; style?: CSSProperties }) {
  return <span aria-hidden="true" style={{ flex: "none", width: size, height: size, borderRadius: round ? "50%" : Math.round(size * 0.32), background: "var(--raised)", display: "grid", placeItems: "center", fontSize: 13.5, fontWeight: 600, ...style }}>{orgInitials(name)}</span>;
}

/** A rounded card that holds list rows, as on Payments, Bills, Contacts and Expenses. */
export function ListCard({ mobile, children }: { mobile?: boolean; children: ReactNode }) {
  return <div className="list-card" style={mobile ? { gap: 6, padding: 8, borderRadius: 24 } : undefined}>{children}</div>;
}

/** A small label above a value, on a tinted tile. As on cash accounts and Mandates. */
export function Stat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, padding: "10px 12px", borderRadius: 14, background: "var(--bg)", minWidth: 0 }}>
      <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{k}</span>
      <span className="num wrap" style={{ fontSize: 14, fontWeight: 500 }}>{v}</span>
    </div>
  );
}

/** Label-value rows, as on record panels. */
export function Facts({ rows }: { rows: [string, ReactNode, { mono?: boolean; badge?: ReactNode }?][] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {rows.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v, o]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "11px 0", borderBottom: "1px solid var(--border)", fontSize: 14 }}>
          <span style={{ color: "var(--muted)" }}>{k}</span>
          <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", textAlign: "right" }}>
            <span className={`wrap ${o?.mono ? "mono" : ""}`}>{v}</span>
            {o?.badge}
          </span>
        </div>
      ))}
    </div>
  );
}

export function LinkChip({ icon, children, onClick }: { icon: string; children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className="hoverable" onClick={onClick}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, minHeight: 44, padding: "0 14px", borderRadius: 12, border: "1px solid var(--border)", fontSize: 14, background: "transparent", textAlign: "left" }}>
      <Icon name={icon} size={18} />{children}
    </button>
  );
}
