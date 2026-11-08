import { useState } from "react";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { dayLabel, hm } from "../lib";
import { ErrorLine, Icon, PageTitle, Segments, Skeleton } from "../ui";
import { ACTOR, ActorNode, type AuditEntry } from "./Dashboard";
import { TraceRow, type TraceEntry } from "./AuditTrace";

type Entry = (AuditEntry & { kind?: "event" }) | TraceEntry;
const isTrace = (e: Entry): e is TraceEntry => e.kind === "turn" || e.kind === "run";

type Filter = "ALL" | "USER" | "AGENT" | "SYSTEM" | "PROVIDER";
const FILTERS: [Filter, string][] = [["ALL", "All"], ["USER", "Person"], ["AGENT", "Agent"], ["SYSTEM", "System"], ["PROVIDER", "Provider"]];

const ActorChip = ({ type, mobile }: { type: string; mobile: boolean }) => (
  <span style={{ display: "inline-flex", alignItems: "center", height: mobile ? 22 : 26, padding: "0 10px", borderRadius: 999, background: "var(--raised)", color: "var(--muted)", fontSize: mobile ? 11 : 12, fontWeight: 600 }}>{ACTOR[type]?.[1]}</span>
);

export function Audit() {
  const { mobile, open } = useApp();
  const [filter, setFilter] = useState<Filter>("ALL");
  const { data, error, loading } = useLive<{ total: number; entries: Entry[] }>(`/api/audit?limit=300&traces=1${filter === "ALL" ? "" : `&actor=${filter}`}`, 15000);
  const entries = data?.entries || [];
  const days: { label: string; entries: Entry[] }[] = [];
  for (const e of entries) {
    const l = dayLabel(e.at);
    if (!days.length || days[days.length - 1].label !== l) days.push({ label: l, entries: [] });
    days[days.length - 1].entries.push(e);
  }
  const count = data ? `${data.total} ${data.total === 1 ? "entry" : "entries"} · events, agent turns and routine runs · newest first · append-only${data.total > entries.length ? ` · showing the latest ${entries.length}` : ""}` : "";
  const node = mobile ? 34 : 40;

  const recordLink = (e: AuditEntry) => e.record && (
    <button type="button" className="hoverable" onClick={() => open(e.record!)}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, minHeight: mobile ? 40 : 32, padding: "0 12px 0 10px", borderRadius: 999, border: "1px solid var(--border-strong)", fontSize: mobile ? 12.5 : 13, fontWeight: 500, cursor: "pointer", maxWidth: "100%", background: "transparent", color: "var(--text)", textAlign: "left", alignSelf: "flex-start" }}>
      <Icon name="description" size={16} /><span className="wrap">{e.record.label}</span>
    </button>
  );

  return (
    <div data-screen-label={mobile ? "M Audit" : "Audit"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "32px 32px 64px", display: "flex", flexDirection: "column", gap: 22, maxWidth: 940, margin: "0 auto" }}>
      <PageTitle mobile={mobile} title="Audit" sub={count || undefined} />
      <Segments items={FILTERS} value={filter} onChange={setFilter} mobile={mobile} scroll={mobile} wrap={!mobile} />
      {loading && <Skeleton height={64} count={4} radius={18} />}
      <ErrorLine error={error} />
      {data && !entries.length && <div className="empty" style={mobile ? { padding: "40px 16px", fontSize: 14, borderRadius: 24 } : undefined}>Nothing recorded{filter === "ALL" ? " yet" : " for this actor"}.</div>}
      {days.map((d) => (
        <section key={d.label} aria-label={d.label} style={{ display: "flex", flexDirection: "column", gap: mobile ? 14 : 18, padding: mobile ? "16px 14px 4px" : "22px 24px 8px", borderRadius: mobile ? 24 : 28, background: "var(--surface)", border: "1px solid var(--border)", boxShadow: mobile ? undefined : "var(--shadow-1)" }}>
          <span className="display" style={{ fontSize: mobile ? 15 : 17, fontWeight: 600, letterSpacing: "-0.02em" }}>{d.label}</span>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {d.entries.map((e) => {
              const rail = (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                  <ActorNode e={e} size={node} tone="accent" />
                  <div style={{ flex: 1, width: 2, minHeight: 12, margin: "4px 0", borderRadius: 1, background: "var(--border)" }} />
                </div>
              );
              if (mobile) {
                return (
                  <div key={e.id} style={{ display: "grid", gridTemplateColumns: "34px minmax(0,1fr)", gap: "0 12px" }}>
                    {rail}
                    <div style={{ minWidth: 0, padding: "1px 0 16px", display: "flex", flexDirection: "column", gap: 6 }}>
                      {isTrace(e) ? (
                        <>
                          <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{hm(e.at)}</span>
                          <TraceRow e={e} mobile />
                        </>
                      ) : (
                        <>
                          <span style={{ fontSize: 14, lineHeight: 1.4 }}><b style={{ fontWeight: 600 }}>{e.who}</b><span style={{ color: "var(--muted)" }}> · </span>{e.what}</span>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}><span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{hm(e.at)}</span><ActorChip type={e.actor_type} mobile /></div>
                          {recordLink(e)}
                        </>
                      )}
                    </div>
                  </div>
                );
              }
              return (
                <div key={e.id} style={{ display: "grid", gridTemplateColumns: "48px 40px minmax(0,1fr)", gap: "0 14px" }}>
                  <span className="mono" style={{ fontSize: 12.5, color: "var(--muted)", paddingTop: 11, textAlign: "right" }}>{hm(e.at)}</span>
                  {rail}
                  {isTrace(e) ? (
                    <div style={{ minWidth: 0, padding: "2px 0 20px" }}><TraceRow e={e} mobile={false} /></div>
                  ) : (
                    <div style={{ minWidth: 0, padding: "2px 0 20px", display: "flex", flexDirection: "column", gap: 8 }}>
                      <span style={{ fontSize: 15, lineHeight: 1.4 }}><b style={{ fontWeight: 600 }}>{e.who}</b><span style={{ color: "var(--muted)" }}> · </span>{e.what}</span>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}><ActorChip type={e.actor_type} mobile={false} />{recordLink(e)}</div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
