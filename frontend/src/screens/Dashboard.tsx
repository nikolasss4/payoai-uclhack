import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge, day, decisionBadge, longToday, stamp } from "../lib";
import { ErrorLine, Icon, Skeleton } from "../ui";

export interface Balance { id: string; name: string; asset: string; balance: string | null; reserve?: string; spendable_after_reserve?: string; as_of?: string; stale?: boolean; note?: string }
export interface AuditEntry { id: number; at: string; actor_type: "USER" | "AGENT" | "SYSTEM" | "PROVIDER"; who: string; what: string; record: { kind: string; id: string; label: string } | null }

export const ACTOR: Record<string, [string, string]> = { USER: ["person", "Person"], AGENT: ["auto_awesome", "Agent"], SYSTEM: ["settings", "System"], PROVIDER: ["account_balance", "Provider"] };
export const freshness = (b: Balance) => (b.balance === null || b.balance === undefined ? badge("No balance") : b.stale ? badge("Stale") : badge("Fresh"));

export function Dashboard() {
  const { mobile, isA, open, go } = useApp();
  const pending = useLive<any[]>("/api/approvals?status=PENDING", 8000);
  const balances = useLive<{ balances: Balance[] }>("/api/balances", 15000);
  const activity = useLive<{ entries: AuditEntry[] }>("/api/audit?limit=5", 15000);
  const queue = (pending.data || []).filter((a) => !a.expired);
  const accounts = balances.data?.balances || [];
  const main = accounts.find((a) => a.balance !== null);

  const row = (a: any) => {
    const dec = a.decision ? decisionBadge(a.decision.decision) : badge("READY_FOR_APPROVAL");
    const sub = [a.invoice || a.reference, a.due_date ? `Due ${day(a.due_date)}` : null, dec.label].filter(Boolean).join(" · ");
    const cta = isA ? "Review" : "View";
    const btn = { background: isA ? "var(--primary-bg)" : "var(--raised)", color: isA ? "var(--primary-fg)" : "var(--text)" };
    const go = () => open({ kind: "approval", id: a.approval_id });
    if (mobile) {
      return (
        <button key={a.approval_id} type="button" onClick={go} style={{ display: "flex", alignItems: "center", gap: 12, padding: 14, borderRadius: 16, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", textAlign: "left", animation: "rise .25s" }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", flex: "none", background: dec.fg }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, flex: 1 }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{a.counterparty}</span><span style={{ fontSize: 12.5, color: "var(--muted)" }}>{sub}</span></div>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flex: "none" }}>
            <span className="num" style={{ fontSize: 14.5, fontWeight: 600 }}>{a.amount}</span>
            <span style={{ display: "flex", alignItems: "center", height: 30, padding: "0 12px", borderRadius: 999, fontSize: 12.5, fontWeight: 500, ...btn }}>{cta}</span>
          </div>
        </button>
      );
    }
    return (
      <div key={a.approval_id} style={{ display: "flex", alignItems: "center", gap: 16, padding: "16px 18px", borderRadius: 18, background: "var(--surface)", border: "1px solid var(--border)", animation: "rise .25s" }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", flex: "none", background: dec.fg }} />
        <button type="button" onClick={go} style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1, cursor: "pointer", background: "transparent", border: 0, padding: 0, textAlign: "left" }}>
          <span style={{ fontSize: 15, fontWeight: 500 }}>{a.counterparty} · <span className="num">{a.amount}</span></span>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>{sub}</span>
        </button>
        <button type="button" onClick={go} style={{ flex: "none", display: "flex", alignItems: "center", height: 40, padding: "0 18px", borderRadius: 999, fontSize: 13.5, fontWeight: 500, cursor: "pointer", border: 0, ...btn }}>{cta}</button>
      </div>
    );
  };

  const waiting = (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 10 : 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14 }}><span style={{ fontWeight: 600 }}>Waiting on you</span><span style={{ color: "var(--muted)" }}>{pending.data ? queue.length : ""}</span></div>
      {pending.loading && <Skeleton height={mobile ? 84 : 76} radius={mobile ? 16 : 18} count={mobile ? 1 : 2} />}
      <ErrorLine error={pending.error} />
      {queue.map(row)}
      {pending.data && !queue.length && (
        <div style={{ display: "flex", alignItems: "center", gap: mobile ? 10 : 12, padding: mobile ? "18px 16px" : "24px 20px", borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1px solid var(--border)", fontSize: mobile ? 14 : 15 }}>
          <Icon name="task_alt" size={mobile ? 22 : 24} style={{ color: "var(--accent-text)" }} />{mobile ? "All clear. Nothing is waiting." : "All clear. Nothing is waiting for approval."}
        </div>
      )}
    </div>
  );

  const cash = (
    <>
      <div style={{ display: "flex", alignItems: "center", fontSize: 14 }}>
        <span style={{ fontWeight: 600 }}>Cash</span>
        <button type="button" onClick={() => go("cash")} style={{ marginLeft: "auto", color: "var(--muted)", cursor: "pointer", background: "transparent", border: 0, minHeight: 32, fontSize: 14 }}>Details</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 12 : 14, padding: mobile ? 16 : "18px 20px", borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1px solid var(--border)" }}>
        {balances.loading && <div className="skeleton" style={{ height: 48 }} />}
        <ErrorLine error={balances.error} />
        {balances.data && !accounts.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No accounts yet.</span>}
        {accounts.map((a) => {
          const fb = freshness(a);
          return (
            <div key={a.id} style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: mobile ? 13 : 13.5, color: "var(--muted)" }}>{a.name}</span>
                <span className="bdg" style={{ color: fb.fg, background: fb.bg, borderColor: fb.bd, borderStyle: fb.bs }}>{fb.label}</span>
              </div>
              <span className="num wrap" style={{ fontSize: mobile ? 19 : 20, fontWeight: 600, letterSpacing: "-0.02em" }}>{a.spendable_after_reserve ?? "No balance read yet"}</span>
            </div>
          );
        })}
        {!mobile && !!accounts.length && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>Spendable after reserve</span>}
      </div>
    </>
  );

  const recent = (
    <>
      {mobile ? <span style={{ fontSize: 14, fontWeight: 600 }}>Recent activity</span> : (
        <div style={{ display: "flex", alignItems: "center", fontSize: 14, paddingTop: 6 }}>
          <span style={{ fontWeight: 600 }}>Recent activity</span>
          <button type="button" onClick={() => go("audit")} style={{ marginLeft: "auto", color: "var(--muted)", cursor: "pointer", background: "transparent", border: 0, fontSize: 14 }}>Audit</button>
        </div>
      )}
      <div style={{ padding: mobile ? "2px 16px" : "4px 20px", borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1px solid var(--border)" }}>
        {activity.loading && <div className="skeleton" style={{ height: 40, margin: "12px 0" }} />}
        <ErrorLine error={activity.error} />
        {activity.data && !activity.data.entries.length && <div style={{ padding: "14px 0", fontSize: 13.5, color: "var(--muted)" }}>Nothing has happened yet.</div>}
        {(activity.data?.entries || []).map((e, i, all) => (
          <button key={e.id} type="button" onClick={() => e.record && open(e.record)}
            style={{ display: "flex", gap: 10, padding: "12px 0", borderBottom: i === all.length - 1 ? 0 : "1px solid var(--border)", fontSize: 13, cursor: e.record ? "pointer" : "default", width: "100%", background: "transparent", borderLeft: 0, borderRight: 0, borderTop: 0, textAlign: "left" }}>
            <Icon name={ACTOR[e.actor_type]?.[0] || "circle"} size={18} style={{ color: "var(--muted)" }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}><span>{e.what}</span><span style={{ color: "var(--muted)" }}>{e.record?.label || e.who}</span></div>
            <span className="num" style={{ color: "var(--subtle)" }}>{stamp(e.at)}</span>
          </button>
        ))}
      </div>
    </>
  );

  if (mobile) {
    return (
      <div data-screen-label="M Dashboard" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h1 className="display" style={{ margin: 0, fontSize: 24, fontWeight: 600, letterSpacing: "-0.02em" }}>Dashboard</h1>
          <span style={{ fontSize: 13.5, color: "var(--muted)" }}>{longToday()}</span>
        </div>
        {waiting}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>{cash}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>{recent}</div>
      </div>
    );
  }
  return (
    <div data-screen-label="Dashboard" style={{ padding: "28px 32px 64px", display: "flex", flexDirection: "column", gap: 24, maxWidth: 1120 }}>
      <div style={{ display: "flex", alignItems: "flex-end", flexWrap: "wrap", gap: "12px 16px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h1 className="display" style={{ margin: 0, fontSize: 30, fontWeight: 600, letterSpacing: "-0.02em" }}>Dashboard</h1>
          <span style={{ fontSize: 15, color: "var(--muted)" }}>{longToday()}{main?.spendable_after_reserve && <> · <span className="num" style={{ color: "var(--text)" }}>{main.spendable_after_reserve}</span> spendable{accounts.length > 1 ? ` in ${main.name}` : ""}</>}</span>
        </div>
        <button type="button" className="btn-pill" onClick={() => go("chat")} style={{ marginLeft: "auto", padding: "0 18px 0 14px" }}><Icon name="auto_awesome" size={19} />Ask PayoAI</button>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 520px", minWidth: 0 }}>{waiting}</div>
        <div style={{ flex: "1 1 300px", maxWidth: "100%", display: "flex", flexDirection: "column", gap: 12 }}>{cash}{recent}</div>
      </div>
    </div>
  );
}
