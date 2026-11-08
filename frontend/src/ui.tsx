import type { CSSProperties, ReactNode } from "react";
import type { B } from "./lib";

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

export function PageTitle({ title, sub, size = 32, mobile }: { title: string; sub?: ReactNode; size?: number; mobile?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 6 }}>
      <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : size, fontWeight: 600, letterSpacing: "-0.03em" }}>{title}</h1>
      {sub && <span style={{ fontSize: mobile ? 13 : 15, color: "var(--muted)" }}>{sub}</span>}
    </div>
  );
}

/** A segmented control, as on Expenses and Audit. */
export function Segments<T extends string>({ items, value, onChange, mobile, wrap }: { items: [T, string][]; value: T; onChange: (v: T) => void; mobile?: boolean; wrap?: boolean }) {
  return (
    <div role="tablist" style={{ display: mobile ? "grid" : "flex", gridTemplateColumns: mobile ? `repeat(${items.length}, minmax(0,1fr))` : undefined, gap: mobile ? 2 : 4, padding: mobile ? 3 : 4, borderRadius: 12, background: "var(--raised)", alignSelf: "flex-start", flexWrap: wrap ? "wrap" : undefined, width: mobile ? "100%" : undefined }}>
      {items.map(([k, l]) => {
        const on = k === value;
        return (
          <button key={k} type="button" role="tab" aria-selected={on} onClick={() => onChange(k)}
            style={{ minHeight: mobile ? 40 : 36, padding: mobile ? "0 6px" : "0 14px", borderRadius: 9, display: "grid", placeItems: "center", fontSize: mobile ? 13 : 14, fontWeight: 500, cursor: "pointer", border: 0, background: on ? "var(--surface)" : "transparent", color: on ? "var(--text)" : "var(--muted)", boxShadow: on ? "var(--shadow-1)" : "none" }}>
            {l}
          </button>
        );
      })}
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
