import { useState } from "react";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { dayLabel, hm } from "../lib";
import { ErrorLine, Icon, Skeleton } from "../ui";
import { ACTOR, type AuditEntry } from "./Dashboard";

type Filter = "ALL" | "USER" | "AGENT" | "SYSTEM" | "PROVIDER";
const FILTERS: [Filter, string][] = [["ALL", "All"], ["USER", "Person"], ["AGENT", "Agent"], ["SYSTEM", "System"], ["PROVIDER", "Provider"]];

export function Audit() {
  const { mobile, open } = useApp();
  const [filter, setFilter] = useState<Filter>("ALL");
  const { data, error, loading } = useLive<{ total: number; entries: AuditEntry[] }>(`/api/audit?limit=300${filter === "ALL" ? "" : `&actor=${filter}`}`, 15000);
  const entries = data?.entries || [];
  const days: { label: string; entries: AuditEntry[] }[] = [];
  for (const e of entries) {
    const l = dayLabel(e.at);
    if (!days.length || days[days.length - 1].label !== l) days.push({ label: l, entries: [] });
    days[days.length - 1].entries.push(e);
  }
  const count = data ? `${data.total} ${data.total === 1 ? "entry" : "entries"} · newest first · append-only${data.total > entries.length ? ` · showing the latest ${entries.length}` : ""}` : "";

  const node = (e: AuditEntry, size: number) => (
    <div style={{ width: size, height: size, flex: "none", borderRadius: "50%", display: "grid", placeItems: "center", background: e.actor_type === "AGENT" ? "var(--accent)" : "var(--raised)", color: e.actor_type === "AGENT" ? "#0B0B0C" : "var(--text)" }}>
      <Icon name={ACTOR[e.actor_type]?.[0] || "circle"} size={size === 28 ? 16 : 15} fill />
    </div>
  );
  const link = (e: AuditEntry, size: number) => e.record && (
    <button type="button" onClick={() => open(e.record!)} style={{ fontSize: size, cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3, color: "var(--text)", background: "transparent", border: 0, padding: 0, minHeight: mobile ? 28 : undefined, textAlign: "left" }}>{e.record.label}</button>
  );

  return (
    <div data-screen-label={mobile ? "M Audit" : "Audit"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "28px 32px 64px", display: "flex", flexDirection: "column", gap: 18, maxWidth: 820 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 6 }}>
        <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : 32, fontWeight: 600, letterSpacing: "-0.03em" }}>Audit</h1>
        <span style={{ fontSize: mobile ? 12.5 : 14, color: "var(--muted)" }}>{count}</span>
      </div>
      {mobile ? (
        <div role="tablist" className="scroll-x-none" style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 2 }}>
          {FILTERS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}
              style={{ flex: "none", minHeight: 40, padding: "0 14px", borderRadius: 999, display: "grid", placeItems: "center", fontSize: 13, fontWeight: 500, cursor: "pointer", border: "1px solid var(--border)", background: filter === k ? "var(--surface)" : "transparent", color: filter === k ? "var(--text)" : "var(--muted)" }}>{l}</button>
          ))}
        </div>
      ) : (
        <div role="tablist" style={{ display: "flex", gap: 4, padding: 4, borderRadius: 12, background: "var(--raised)", alignSelf: "flex-start", flexWrap: "wrap" }}>
          {FILTERS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}
              style={{ minHeight: 36, padding: "0 14px", borderRadius: 9, display: "flex", alignItems: "center", fontSize: 14, fontWeight: 500, cursor: "pointer", border: 0, background: filter === k ? "var(--surface)" : "transparent", color: filter === k ? "var(--text)" : "var(--muted)", boxShadow: filter === k ? "var(--shadow-1)" : "none" }}>{l}</button>
          ))}
        </div>
      )}
      {loading && <Skeleton height={48} count={4} />}
      <ErrorLine error={error} />
      {data && !entries.length && <div className="empty" style={mobile ? { padding: "40px 16px", fontSize: 14 } : undefined}>Nothing recorded{filter === "ALL" ? " yet" : " for this actor"}.</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {days.map((d) => (
          <div key={d.label} style={{ display: "flex", flexDirection: "column" }}>
            <div className="kicker" style={mobile ? { fontSize: 11.5, padding: "4px 0 10px" } : { padding: "6px 0 12px 66px" }}>{d.label}</div>
            {d.entries.map((e) => mobile ? (
              <div key={e.id} style={{ display: "grid", gridTemplateColumns: "26px minmax(0,1fr)", gap: "0 10px" }}>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>{node(e, 26)}<div style={{ flex: 1, width: 1, minHeight: 12, background: "var(--border)" }} /></div>
                <div style={{ minWidth: 0, padding: "3px 0 14px", display: "flex", flexDirection: "column", gap: 3 }}>
                  <span style={{ fontSize: 14 }}>{e.what}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}><span className="mono">{hm(e.at)}</span> · {e.who} · {ACTOR[e.actor_type]?.[1]}</span>
                  {link(e, 12.5)}
                </div>
              </div>
            ) : (
              <div key={e.id} style={{ display: "grid", gridTemplateColumns: "52px 28px minmax(0,1fr)", gap: "0 14px" }}>
                <span className="mono" style={{ fontSize: 12, color: "var(--muted)", paddingTop: 6, textAlign: "right" }}>{hm(e.at)}</span>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>{node(e, 28)}<div style={{ flex: 1, width: 1, minHeight: 14, background: "var(--border)" }} /></div>
                <div style={{ minWidth: 0, padding: "4px 0 18px", display: "flex", flexDirection: "column", gap: 4 }}>
                  <span style={{ fontSize: 14.5 }}><span style={{ color: "var(--muted)" }}>{e.who} · </span>{e.what}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 11.5, fontWeight: 500, padding: "1px 7px", borderRadius: 999, background: "var(--raised)", color: "var(--muted)" }}>{ACTOR[e.actor_type]?.[1]}</span>
                    {link(e, 13)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
