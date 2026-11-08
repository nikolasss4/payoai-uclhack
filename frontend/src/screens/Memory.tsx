import { api } from "../api";
import { useApp } from "../app/context";
import { refreshAll, useAction, useArmed, useLive } from "../hooks";
import { badge, words } from "../lib";
import { Badge, ErrorLine, Icon, Skeleton } from "../ui";

export interface Memory { id: string; user_id: string | null; type: string; subject: string | null; value: any; origin: string; evidence_count: number; version: number; created_at: string }

const TYPE: Record<string, string> = {
  merchant_preference: "Shop preference", category_hint: "Category hint", source_account_hint: "Preference", purchase_constraint: "Purchase rule",
  reply_style: "Reply style", business_context: "Business context",
};
const ORIGIN: Record<string, [string, string]> = {
  confirmed: ["Verified", "Confirmed"],
  stated: ["Draft", "Learned from what you said"],
  observed: ["Draft", "Learned from what you did"],
};

function valueText(m: Memory): string {
  const v = m.value;
  const body = typeof v === "string" ? v : v && typeof v === "object" ? (v.text || v.value || v.note || Object.entries(v).map(([k, x]) => `${words(k)}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join(" · ")) : String(v ?? "");
  return m.subject ? `${m.subject}: ${body}` : body;
}

export const useMemories = () => useLive<Memory[]>("/api/memories", 30000);

export function MemoryList({ mobile }: { mobile?: boolean }) {
  const { isA, isV, controls } = useApp();
  const { data, error, loading } = useMemories();
  const toggle = useAction();
  const on = !!controls?.auto_mode;
  const pad = mobile ? 14 : 16;
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 8 : 10, padding: pad, borderRadius: 16, background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>Auto Mode</span>
          {isA ? (
            <button type="button" role="switch" aria-checked={on} aria-busy={toggle.busy || undefined}
              onClick={() => toggle.run(async () => { await api("/api/controls/auto-mode", { method: "POST", body: { on: !on } }); refreshAll(); })}
              style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, cursor: "pointer", background: "transparent", border: 0, padding: 0 }}>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{on ? "On" : "Off"}</span>
              <span style={{ width: 44, height: 24, borderRadius: 999, background: on ? "var(--text)" : "var(--neutral-bd)", position: "relative", display: "block", transition: "background .2s" }}>
                <span style={{ position: "absolute", top: 2, left: 2, width: 20, height: 20, borderRadius: "50%", background: "var(--surface)", transform: `translateX(${on ? 20 : 0}px)`, transition: "transform .2s" }} />
              </span>
            </button>
          ) : <span style={{ fontSize: 13, color: "var(--muted)" }}>{on ? "On" : "Off"} · Approvers only</span>}
        </div>
        <span style={{ fontSize: mobile ? 13 : 13.5, color: "var(--muted)", lineHeight: 1.45 }}>Learns preferences without asking you to confirm each one.</span>
        <ErrorLine error={toggle.error} />
      </div>
      {loading && <Skeleton height={84} count={2} radius={14} />}
      <ErrorLine error={error} />
      {(data || []).map((m) => <MemoryCard key={m.id} m={m} canDelete={!isV} mobile={mobile} />)}
      {data && !data.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No memories yet.</span>}
      <div style={{ display: "flex", gap: 8, padding: mobile ? 12 : "12px 14px", borderRadius: 12, background: "var(--raised)", fontSize: mobile ? 13 : 13.5, lineHeight: 1.45 }}>
        <Icon name="shield" size={18} />Memory can shape suggestions. It never changes a check or approves anything.
      </div>
    </>
  );
}

function MemoryCard({ m, canDelete, mobile }: { m: Memory; canDelete: boolean; mobile?: boolean }) {
  const del = useAction();
  const confirm = useArmed();
  const [tone, originText] = ORIGIN[m.origin] || ["Draft", words(m.origin)];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 8, padding: mobile ? "12px 14px" : "14px 16px", borderRadius: 14, background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: mobile ? 11.5 : 12, fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase", color: "var(--muted)" }}>{TYPE[m.type] || words(m.type)}{m.user_id ? " · Just you" : ""}</span>
        {canDelete && (
          <button type="button" aria-busy={del.busy || undefined}
            onClick={() => { if (!confirm.armed) return confirm.arm(); confirm.disarm(); del.run(async () => { await api(`/api/memories/${m.id}`, { method: "DELETE" }); refreshAll(); }); }}
            style={{ display: "flex", alignItems: "center", gap: 4, minHeight: mobile ? 44 : 36, padding: "0 10px", borderRadius: 999, fontSize: 13, cursor: "pointer", color: "var(--danger)", background: confirm.armed ? "var(--danger-bg)" : "transparent", border: 0 }}>
            <Icon name="delete" size={16} />{confirm.armed ? "Tap again to delete" : "Delete"}
          </button>
        )}
      </div>
      <span style={{ fontSize: mobile ? 14 : 14.5, lineHeight: 1.4 }}>{valueText(m)}</span>
      <Badge b={badge(tone, originText)} size="sm" style={{ alignSelf: "flex-start", fontSize: mobile ? 11.5 : 12 }} />
      <ErrorLine error={del.error} />
    </div>
  );
}
