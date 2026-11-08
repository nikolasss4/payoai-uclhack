import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge, day, dayLabel, when } from "../lib";
import { Badge, ErrorLine, Icon, Skeleton } from "../ui";
import { AccountsPanel } from "./Accounts";
import { freshness, type Balance } from "./Dashboard";

interface Daily { date: string; position: string; ratio: number; short: boolean; bills: string[] }
interface AssetResult {
  asset: string; confident: boolean; stale: boolean; unknown: boolean; note: string | null; projected: string; shortfall_date: string | null;
  bills_due: { id: string; invoice: string | null; amount: string; due_date: string }[]; driving?: { id: string; label: string }[]; daily?: Daily[]; start_position?: string;
  accounts: { id: string; name: string; as_of?: string; stale?: boolean }[];
}
interface Latest { id?: string; created_at?: string; result?: { days: number; as_of: string; by_asset: AssetResult[] } }

export function Cash() {
  const { mobile, open, isA } = useApp();
  const [managing, setManaging] = useState(false);
  const balances = useLive<{ balances: Balance[]; alerts: any[] }>("/api/balances", 15000);
  const latest = useLive<Latest>("/api/cash/latest", 15000);
  const [analysing, setAnalysing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const startedFrom = useRef<string | undefined>(undefined);

  // The analysis runs on a worker. A new result is the one with a different ID from the one shown when it started.
  useEffect(() => {
    if (!analysing) return undefined;
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const now = await api<Latest>("/api/cash/latest");
        if (now.id && now.id !== startedFrom.current) {
          setAnalysing(false);
          setSlow(false);
          latest.reload();
          balances.reload();
        } else if (Date.now() - started > 90000) {
          setSlow(true);
        }
      } catch { /* The next tick tries again. */ }
    }, 2000);
    return () => clearInterval(timer);
  }, [analysing, latest, balances]);

  const analyse = async () => {
    if (analysing) return;
    setError(null);
    startedFrom.current = latest.data?.id;
    try {
      await api("/api/cash/analyse", { method: "POST", body: {} });
      setAnalysing(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const accounts = balances.data?.balances || [];
  const result = latest.data?.result;
  const analysisCards = (result?.by_asset || []).map((entry) => <AnalysisCard key={entry.asset} entry={entry} days={result!.days} at={latest.data!.created_at!} mobile={mobile} onBill={(id) => open({ kind: "bill", id })} />);

  const header = mobile ? (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
      <h1 className="display" style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: "-0.03em" }}>Cash</h1>
      <div style={{ display: "flex", gap: 6 }}>
        {isA && <button type="button" className="btn-pill" aria-label="Add or remove accounts" onClick={() => setManaging(true)} style={{ width: 44, padding: 0 }}><Icon name="add" size={20} /></button>}
        <button type="button" className="btn-pill" onClick={analyse} aria-busy={analysing || undefined} style={{ padding: "0 12px", fontSize: 13, gap: 4, whiteSpace: "nowrap" }}><Icon name="refresh" size={18} />Run a new analysis</button>
      </div>
    </div>
  ) : (
    <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h1 className="display" style={{ margin: 0, fontSize: 32, fontWeight: 600, letterSpacing: "-0.03em" }}>Cash</h1>
        <span style={{ fontSize: 15, color: "var(--muted)" }}>Balances and the days ahead. Expected income is never counted.</span>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {isA && <button type="button" className="btn-pill" onClick={() => setManaging(true)}><Icon name="add" size={19} />Add account</button>}
        <button type="button" className="btn-pill" onClick={analyse} aria-busy={analysing || undefined}><Icon name="refresh" size={19} />Run a new analysis</button>
      </div>
    </div>
  );

  const status = analysing && (
    <div role="status" style={{ display: "flex", alignItems: "center", gap: mobile ? 8 : 10, padding: mobile ? 12 : "14px 16px", borderRadius: mobile ? 12 : 14, background: "var(--info-bg)", border: "1px solid var(--info-bd)", fontSize: mobile ? 13.5 : 14 }}>
      <Icon name="progress_activity" size={mobile ? 18 : 20} className="spin" style={{ color: "var(--info)" }} />
      {slow ? "Still running. The result appears here when it's ready." : "Reading balances and due bills…"}
    </div>
  );

  const accountCards = accounts.map((a) => <AccountCard key={a.id} a={a} mobile={mobile} />);
  const noAnalysis = latest.data && !result && (
    <div className="card" style={{ padding: mobile ? 16 : 20, display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="kicker">Latest analysis</span>
      <span style={{ fontSize: 15, color: "var(--muted)" }}>No cash analysis has been run yet. Run one to see the days ahead.</span>
    </div>
  );

  const panel = managing && <AccountsPanel accounts={accounts} mobile={mobile} onClose={() => setManaging(false)} onChanged={() => balances.reload()} />;

  if (mobile) {
    return (
      <div data-screen-label="M Cash" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {header}
        <ErrorLine error={error || balances.error || latest.error} />
        {status}
        {balances.loading && <Skeleton height={140} count={2} radius={16} />}
        {balances.data && !accounts.length && <div className="empty" style={{ padding: "40px 16px", fontSize: 14 }}>No accounts yet.</div>}
        {accountCards}
        {latest.loading && <Skeleton height={160} count={1} radius={16} />}
        {noAnalysis}
        {analysisCards}
        <div style={{ display: "flex", gap: 6, fontSize: 12.5, color: "var(--muted)" }}><Icon name="info" size={16} />Expected income is never counted.</div>
        {panel}
      </div>
    );
  }
  return (
    <div data-screen-label="Cash" style={{ padding: "28px 32px 64px", display: "flex", flexDirection: "column", gap: 22, maxWidth: 1120 }}>
      {header}
      <ErrorLine error={error || balances.error || latest.error} />
      {balances.loading && <Skeleton height={180} count={1} radius={18} />}
      {balances.data && !accounts.length && <div className="empty">No accounts yet.</div>}
      {!!accounts.length && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: 12 }}>{accountCards}</div>}
      {status}
      {latest.loading && <Skeleton height={240} count={1} radius={18} />}
      {noAnalysis}
      {!!analysisCards.length && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(420px,1fr))", gap: 12 }}>{analysisCards}</div>}
      <div style={{ display: "flex", gap: 8, fontSize: 13.5, color: "var(--muted)" }}><Icon name="info" size={18} />Expected income is never counted. Only money already in the account is used.</div>
      {panel}
    </div>
  );
}

function AccountCard({ a, mobile }: { a: Balance; mobile: boolean }) {
  const fb = freshness(a);
  const asOf = a.as_of ? `as of ${when(a.as_of)}` : a.note || "No balance read yet";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 12 : 16, padding: mobile ? 16 : 20, borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: mobile ? 15 : 16, fontWeight: 600 }}>{a.name}</span>
          <span style={{ fontSize: mobile ? 12 : 12.5, color: "var(--muted)" }}>{mobile ? asOf : a.asset}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <Badge b={fb} />
          {!mobile && <span style={{ fontSize: 12, color: "var(--muted)" }}>{asOf}</span>}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Spendable after reserve</span>
        <span className="num wrap" style={{ fontSize: mobile ? 26 : 32, fontWeight: 600, letterSpacing: "-0.03em" }}>{a.spendable_after_reserve ?? "Unknown"}</span>
      </div>
      {a.stale && <span style={{ fontSize: 13, color: "var(--warn)" }}>This balance is out of date, so it can't be relied on for a payment decision.</span>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: mobile ? 8 : 12, paddingTop: mobile ? 10 : 14, borderTop: "1px solid var(--border)", fontSize: mobile ? 13 : 13.5 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: mobile ? 11.5 : 12, color: "var(--muted)" }}>Balance</span><span className="num">{a.balance ?? "—"}</span></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: mobile ? 11.5 : 12, color: "var(--muted)" }}>Reserve</span><span className="num">{a.reserve ?? "—"}</span></div>
      </div>
    </div>
  );
}

function AnalysisCard({ entry, days, at, mobile, onBill }: { entry: AssetResult; days: number; at: string; mobile: boolean; onBill: (id: string) => void }) {
  const kicker = `${mobile ? `Next ${days} days` : "Latest analysis"} · ${entry.asset}`;
  if (!entry.confident) {
    const staleNames = entry.accounts.filter((a) => a.stale || !a.as_of).map((a) => `${a.name} was last read ${a.as_of ? when(a.as_of) : "never"}`);
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 8 : 14, padding: mobile ? 16 : 20, borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1.5px dashed var(--unknown-bd)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="kicker" style={{ fontSize: mobile ? 11.5 : 12 }}>{kicker}</span>
          <Badge b={badge("Unknown", "Uncertain")} />
        </div>
        <span style={{ fontSize: mobile ? 15.5 : 18, fontWeight: 600, lineHeight: 1.3 }}>{entry.note || "A balance here is stale or unknown, so this can't be stated with confidence."}</span>
        {(!!staleNames.length || !!entry.bills_due.length) && (
          <span style={{ fontSize: mobile ? 13.5 : 14, color: "var(--muted)", lineHeight: 1.5 }}>
            {staleNames.join(". ")}{staleNames.length ? ". " : ""}
            {entry.bills_due.map((b) => `${b.amount} due ${day(b.due_date)}`).join(", ")}{entry.bills_due.length ? ". " : ""}
            Run a new analysis once the balance reads fresh.
          </span>
        )}
      </div>
    );
  }
  const short = !!entry.shortfall_date;
  const shortfallDay = entry.daily?.find((d) => d.short);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 12 : 16, padding: mobile ? 16 : 20, borderRadius: mobile ? 16 : 18, background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span className="kicker" style={{ fontSize: mobile ? 11.5 : 12 }}>{kicker}</span>
          {!mobile && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>Run {when(at)} · next {days} days</span>}
        </div>
        {short ? <Badge b={{ ...badge("Failed", "Shortfall"), icon: "trending_down" }} /> : <Badge b={{ ...badge("Pass", "No shortfall"), icon: "trending_flat" }} />}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontSize: mobile ? 18 : 22, fontWeight: 600, letterSpacing: "-0.02em" }}>
          {short ? `Shortfall${shortfallDay ? ` of ${shortfallDay.position.replace(/^−/, "")}` : ""} on ${day(entry.shortfall_date)}` : `${entry.projected} left after reserve`}
        </span>
        {!mobile && <span style={{ fontSize: 14, color: "var(--muted)" }}>{short ? "Projected position after reserve, if every due bill is paid on its due date." : `After every bill due in the next ${days} days is paid, keeping the reserve.`}</span>}
      </div>
      {!!entry.daily?.length && (mobile ? (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {entry.daily.map((d) => (
            <div key={d.date} style={{ display: "grid", gridTemplateColumns: "56px minmax(0,1fr) auto", gap: 8, alignItems: "center", padding: "5px 0", fontSize: 12.5 }}>
              <span style={{ color: "var(--muted)" }}>{dayLabel(d.date) === "Today" ? "Today" : day(d.date)}</span>
              <div style={{ height: 8, borderRadius: 4, background: "var(--raised)", overflow: "hidden" }}><div style={{ height: "100%", width: `${d.short ? 6 : Math.max(2, d.ratio * 100)}%`, background: d.short ? "var(--danger)" : "var(--neutral-bd)", borderRadius: 4 }} /></div>
              <span className="num" style={{ color: d.short ? "var(--danger)" : "var(--muted)", minWidth: 72, textAlign: "right" }}>{d.position}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${entry.daily.length},minmax(0,1fr))`, gap: 8, alignItems: "end", height: 170 }} role="img" aria-label={`Projected position for the next ${days} days`}>
          {entry.daily.map((d) => (
            <div key={d.date} title={d.bills.length ? `Due: ${d.bills.join(", ")}` : undefined} style={{ display: "flex", flexDirection: "column", alignItems: "stretch", gap: 6, height: "100%", justifyContent: "flex-end", minWidth: 0 }}>
              <span className="num" style={{ fontSize: 11, textAlign: "center", color: d.short ? "var(--danger)" : "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.position}</span>
              <div style={{ height: `${d.short ? 6 : Math.max(3, d.ratio * 100)}%`, minHeight: 4, borderRadius: 6, background: d.short ? "var(--danger)" : "var(--neutral-bd)" }} />
              <span style={{ fontSize: 11.5, textAlign: "center", color: "var(--muted)" }}>{dayLabel(d.date) === "Today" ? "Today" : day(d.date)}</span>
            </div>
          ))}
        </div>
      ))}
      {!!entry.driving?.length && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Caused by</span>
          <div style={{ display: "flex", flexWrap: mobile ? undefined : "wrap", flexDirection: mobile ? "column" : "row", gap: 6 }}>
            {entry.driving.map((b) => (
              <button key={b.id} type="button" className="hoverable" onClick={() => onBill(b.id)}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, minHeight: mobile ? 44 : 36, padding: "0 12px", borderRadius: mobile ? 12 : 999, border: "1px solid var(--border-strong)", fontSize: 13, background: "transparent", textAlign: "left" }}>
                <Icon name="description" size={16} />{b.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {!entry.daily?.length && !!entry.bills_due.length && <span style={{ fontSize: 13, color: "var(--muted)" }}>{entry.bills_due.length} bill{entry.bills_due.length === 1 ? "" : "s"} due. Run a new analysis to see the day-by-day view.</span>}
    </div>
  );
}
