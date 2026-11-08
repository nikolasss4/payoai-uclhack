import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../app/context";
import { useExit, useLive } from "../hooks";
import { day, dayLabel, hm, when } from "../lib";
import { Badge, ErrorLine, Icon, PageTitle, Skeleton, Stat } from "../ui";
import { AccountsPanel } from "./Accounts";
import { AccountMark, accountLook, freshness, type Balance } from "./Dashboard";

interface Daily { date: string; position: string; ratio: number; short: boolean; bills: string[] }
export interface AssetResult {
  asset: string; confident: boolean; stale: boolean; unknown: boolean; note: string | null; projected: string; shortfall_date: string | null;
  bills_due: { id: string; invoice: string | null; amount: string; due_date: string }[]; driving?: { id: string; label: string }[]; daily?: Daily[]; start_position?: string;
  accounts: { id: string; name: string; as_of?: string; stale?: boolean }[];
}
export interface Latest { id?: string; created_at?: string; result?: { days: number; as_of: string; by_asset: AssetResult[] } }

/** "Shortfall of £400.00 on 14 Nov", from the first day the position goes below the reserve. */
export function shortfallTitle(entry: AssetResult): string {
  const d = entry.daily?.find((x) => x.short);
  return `Shortfall${d ? ` of ${d.position.replace(/^[−-]/, "")}` : ""} on ${day(entry.shortfall_date)}`;
}

export function Cash() {
  const { mobile, open, isA } = useApp();
  const [managing, setManaging] = useState(false);
  const [panelShown, panelLeaving] = useExit(managing);
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

  const header = (
    <PageTitle mobile={mobile} title="Cash" sub={mobile ? undefined : "Balances and the days ahead. Expected income is never counted."}>
      <div style={{ display: "flex", gap: mobile ? 6 : 8, flexWrap: "wrap" }}>
        {isA && (mobile
          ? <button type="button" className="btn-pill" aria-label="Add or remove accounts" onClick={() => setManaging(true)} style={{ width: 44, padding: 0, background: "var(--surface)" }}><Icon name="add" size={20} /></button>
          : <button type="button" className="btn-pill" onClick={() => setManaging(true)} style={{ height: 48, padding: "0 18px 0 14px", fontSize: 15, fontWeight: 600, background: "var(--surface)" }}><Icon name="add" size={20} />Add account</button>)}
        <button type="button" className="btn-pill btn-primary press" onClick={analyse} aria-busy={analysing || undefined}
          style={mobile ? { padding: "0 14px 0 12px", fontSize: 13, fontWeight: 600, gap: 4, whiteSpace: "nowrap" } : { height: 48, padding: "0 20px 0 16px", fontSize: 15, fontWeight: 600, gap: 8 }}>
          <Icon name="refresh" size={mobile ? 18 : 20} className={analysing ? "spin" : ""} />{mobile ? "Run analysis" : "Run a new analysis"}
        </button>
      </div>
    </PageTitle>
  );

  const status = analysing && (
    <div role="status" style={{ display: "flex", alignItems: "center", gap: mobile ? 8 : 10, padding: mobile ? "12px 14px" : "14px 18px", borderRadius: mobile ? 16 : 18, background: "var(--info-bg)", border: "1px solid var(--info-bd)", fontSize: mobile ? 13.5 : 14 }}>
      <Icon name="progress_activity" size={mobile ? 18 : 20} className="spin" style={{ color: "var(--info)" }} />
      {slow ? "Still running. The result appears here when it's ready." : "Reading balances and due bills…"}
    </div>
  );

  const accountCards = accounts.map((a) => <AccountCard key={a.id} a={a} mobile={mobile} />);
  const noAnalysis = latest.data && !result && (
    <div className="card" style={{ padding: mobile ? 18 : 24, borderRadius: mobile ? 24 : 28, display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="kicker">Latest analysis</span>
      <span style={{ fontSize: 15, color: "var(--muted)" }}>No cash analysis has been run yet. Run one to see the days ahead.</span>
    </div>
  );

  const panel = panelShown && <AccountsPanel accounts={accounts} mobile={mobile} leaving={panelLeaving} onClose={() => setManaging(false)} onChanged={() => balances.reload()} />;

  if (mobile) {
    return (
      <div data-screen-label="M Cash" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {header}
        <ErrorLine error={error || balances.error || latest.error} />
        {status}
        {balances.loading && <Skeleton height={160} count={2} radius={24} />}
        {balances.data && !accounts.length && <div className="empty" style={{ padding: "40px 16px", fontSize: 14 }}>No accounts yet.</div>}
        {accountCards}
        {latest.loading && <Skeleton height={200} count={1} radius={26} />}
        {noAnalysis}
        {analysisCards}
        <div style={{ display: "flex", gap: 6, fontSize: 12.5, color: "var(--muted)" }}><Icon name="info" size={16} />Expected income is never counted.</div>
        {panel}
      </div>
    );
  }
  return (
    <div data-screen-label="Cash" style={{ padding: "32px 32px 64px", display: "flex", flexDirection: "column", gap: 22, maxWidth: 1180, margin: "0 auto" }}>
      {header}
      <ErrorLine error={error || balances.error || latest.error} />
      {status}
      {balances.loading && <Skeleton height={200} count={1} radius={28} />}
      {balances.data && !accounts.length && <div className="empty">No accounts yet.</div>}
      {!!accounts.length && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: 16 }}>{accountCards}</div>}
      {latest.loading && <Skeleton height={300} count={1} radius={28} />}
      {noAnalysis}
      {!!analysisCards.length && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(440px,1fr))", gap: 16 }}>{analysisCards}</div>}
      <div style={{ display: "flex", gap: 8, fontSize: 13.5, color: "var(--muted)" }}><Icon name="info" size={18} />Expected income is never counted. Only money already in the account is used.</div>
      {panel}
    </div>
  );
}

function AccountCard({ a, mobile }: { a: Balance; mobile: boolean }) {
  const fb = freshness(a);
  const look = accountLook(a);
  const asOf = a.as_of ? `as of ${when(a.as_of)}` : a.note || "No balance read yet";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 16 : 20, padding: mobile ? 18 : 24, borderRadius: mobile ? 24 : 28, background: "var(--surface)", border: "1px solid var(--border)", boxShadow: mobile ? undefined : "var(--shadow-1)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: mobile ? 10 : 12, flexWrap: "wrap" }}>
        <span style={{ width: mobile ? 40 : 44, height: mobile ? 40 : 44, flex: "none", borderRadius: mobile ? 13 : 14, background: look.bg, color: look.fg, display: "grid", placeItems: "center" }}><AccountMark a={a} size={mobile ? 22 : 26} /></span>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: mobile ? 15 : 16, fontWeight: 600 }}>{a.name}</span>
          <span style={{ fontSize: mobile ? 12 : 12.5, color: "var(--muted)" }}>{mobile ? asOf : `${a.asset} · ${asOf}`}</span>
        </div>
        <Badge b={fb} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 6 }}>
        <span style={{ fontSize: mobile ? 12 : 13, color: "var(--muted)" }}>Spendable after reserve</span>
        <span className="display num wrap" style={{ fontSize: mobile ? 24 : 34, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: mobile ? 1.15 : 1.1 }}>{a.spendable_after_reserve ?? "Unknown"}</span>
      </div>
      {a.stale && <span style={{ fontSize: 13, color: "var(--warn)" }}>This balance is out of date, so it can't be relied on for a payment decision.</span>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: mobile ? 6 : 8 }}>
        <Stat k="Balance" v={a.balance ?? "—"} />
        <Stat k="Reserve" v={a.reserve ?? "—"} />
      </div>
    </div>
  );
}

const CORAL = "#FF7A6E";
const LIME = "#D4F53C";

function AnalysisCard({ entry, days, at, mobile, onBill }: { entry: AssetResult; days: number; at: string; mobile: boolean; onBill: (id: string) => void }) {
  if (!entry.confident) {
    const staleNames = entry.accounts.filter((a) => a.stale || !a.as_of).map((a) => `${a.name} was last read ${a.as_of ? when(a.as_of) : "never"}`);
    return (
      <div style={{ display: "flex", gap: mobile ? 12 : 16, alignItems: "flex-start", padding: mobile ? 16 : 24, borderRadius: mobile ? 24 : 28, background: "var(--surface)", border: "1.5px dashed var(--unknown-bd)" }}>
        <span style={{ width: mobile ? 36 : 44, height: mobile ? 36 : 44, flex: "none", borderRadius: mobile ? 12 : 14, background: "var(--raised)", display: "grid", placeItems: "center" }}><Icon name="question_mark" size={mobile ? 19 : 22} /></span>
        <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 8, minWidth: 0 }}>
          <span className="kicker" style={{ fontSize: mobile ? 11.5 : 12, letterSpacing: ".06em" }}>{mobile ? `${entry.asset} · Uncertain` : `Latest analysis · ${entry.asset} · Uncertain`}</span>
          <span style={{ fontSize: mobile ? 14.5 : 19, fontWeight: 600, lineHeight: mobile ? 1.35 : 1.3 }}>{entry.note || "A balance here is stale or unknown, so this can't be stated with confidence."}</span>
          {!mobile && (!!staleNames.length || !!entry.bills_due.length) && (
            <span style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.5 }}>
              {staleNames.join(". ")}{staleNames.length ? ". " : ""}
              {entry.bills_due.map((b) => `${b.amount} due ${day(b.due_date)}`).join(", ")}{entry.bills_due.length ? ". " : ""}
              Run a new analysis once the balance reads fresh.
            </span>
          )}
        </div>
      </div>
    );
  }
  const short = !!entry.shortfall_date;
  const shortDay = entry.daily?.find((d) => d.short);
  const ranAt = dayLabel(at) === "Today" ? `run at ${hm(at)} today` : `run ${when(at)}`;
  const kicker = mobile ? `Next ${days} days · ${entry.asset}` : `Latest analysis · ${entry.asset} · ${ranAt}`;
  const chip = short
    ? <span style={{ display: "inline-flex", alignItems: "center", gap: mobile ? 4 : 5, height: mobile ? 24 : 30, padding: mobile ? "0 10px 0 8px" : "0 12px 0 10px", borderRadius: 999, background: "rgba(255,122,110,.14)", color: CORAL, fontSize: mobile ? 11.5 : 13, fontWeight: 600 }}><Icon name="trending_down" size={mobile ? 14 : 16} />Shortfall</span>
    : <span style={{ display: "inline-flex", alignItems: "center", gap: mobile ? 4 : 5, height: mobile ? 24 : 30, padding: mobile ? "0 10px 0 8px" : "0 12px 0 10px", borderRadius: 999, background: "rgba(212,245,60,.12)", color: LIME, fontSize: mobile ? 11.5 : 13, fontWeight: 600 }}><Icon name="trending_flat" size={mobile ? 14 : 16} />No shortfall</span>;
  const headline = short
    ? <>Shortfall{shortDay ? <> of <span style={{ color: CORAL }}>{shortDay.position.replace(/^[−-]/, "")}</span></> : null} on {day(entry.shortfall_date)}</>
    : <><span style={{ color: LIME }}>{entry.projected}</span> left after reserve</>;
  const label = (d: { date: string }) => (dayLabel(d.date) === "Today" ? "Today" : day(d.date));
  return (
    <div className="ink-card" style={{ display: "flex", flexDirection: "column", gap: mobile ? 14 : 22, padding: mobile ? "20px 18px" : 28, borderRadius: mobile ? 26 : 28 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: mobile ? 8 : 12, flexWrap: "wrap", alignItems: mobile ? "center" : "flex-start" }}>
        {mobile ? <span style={{ fontSize: 11.5, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "rgba(244,244,242,.6)" }}>{kicker}</span> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: "1 1 260px", minWidth: 0 }}>
            <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "rgba(244,244,242,.6)" }}>{kicker}</span>
            <span className="display" style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.15 }}>{headline}</span>
            <span style={{ fontSize: 14, color: "rgba(244,244,242,.66)" }}>{short ? "Projected position after reserve, if every due bill is paid on its due date." : `After every bill due in the next ${days} days is paid, keeping the reserve.`}</span>
          </div>
        )}
        {chip}
      </div>
      {mobile && <span className="display" style={{ fontSize: 21, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.2 }}>{headline}</span>}
      {!!entry.daily?.length && (mobile ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {entry.daily.map((d, i) => {
            const fg = d.short ? CORAL : "rgba(244,244,242,.66)";
            return (
              <div key={d.date} style={{ display: "grid", gridTemplateColumns: "48px minmax(0,1fr) 76px", gap: 10, alignItems: "center", padding: "5px 0", fontSize: 12 }}>
                <span style={{ color: fg }}>{label(d)}</span>
                <div style={{ height: 10, borderRadius: 5, background: "rgba(244,244,242,.08)", overflow: "hidden" }}><div style={{ height: "100%", width: `${d.short ? 6 : Math.max(2, d.ratio * 100)}%`, background: d.short ? CORAL : i === 0 ? LIME : "rgba(244,244,242,.42)", borderRadius: 5 }} /></div>
                <span className="num" style={{ color: fg, textAlign: "right" }}>{d.position}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div role="img" aria-label={`Projected position for the next ${days} days`} style={{ display: "grid", gridTemplateColumns: `repeat(${entry.daily.length},minmax(0,1fr))`, gap: 10, alignItems: "end", height: 200 }}>
          {entry.daily.map((d, i) => {
            const fg = d.short ? CORAL : "rgba(244,244,242,.66)";
            return (
              <div key={d.date} title={d.bills.length ? `Due: ${d.bills.join(", ")}` : undefined} style={{ display: "flex", flexDirection: "column", gap: 8, height: "100%", minWidth: 0 }}>
                <span className="num" style={{ fontSize: 11.5, textAlign: "center", color: fg, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.position}</span>
                <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "flex-end" }}><div style={{ width: "100%", height: `${d.short ? 6 : Math.max(3, d.ratio * 100)}%`, minHeight: 6, borderRadius: 10, background: d.short ? CORAL : i === 0 ? LIME : "rgba(244,244,242,.42)" }} /></div>
                <span style={{ fontSize: 12, textAlign: "center", color: fg, whiteSpace: "nowrap" }}>{label(d)}</span>
              </div>
            );
          })}
        </div>
      ))}
      {!!entry.driving?.length && (
        <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 10, paddingTop: mobile ? 12 : 18, borderTop: "1px solid #2A2A2D" }}>
          <span style={{ fontSize: mobile ? 12.5 : 13, fontWeight: 600, color: "rgba(244,244,242,.72)" }}>Caused by</span>
          <div style={{ display: "flex", flexWrap: mobile ? undefined : "wrap", flexDirection: mobile ? "column" : "row", gap: mobile ? 6 : 8 }}>
            {entry.driving.map((b) => (
              <button key={b.id} type="button" className="ink-chip" onClick={() => onBill(b.id)}
                style={{ display: "inline-flex", alignItems: "center", gap: 8, minHeight: mobile ? 44 : 40, padding: mobile ? "0 12px" : "0 14px 0 12px", borderRadius: mobile ? 14 : 999, background: "rgba(244,244,242,.08)", border: mobile ? 0 : "1px solid rgba(244,244,242,.16)", color: "#F4F4F2", fontSize: mobile ? 12.5 : 13.5, cursor: "pointer", textAlign: "left" }}>
                <Icon name="description" size={17} style={{ color: LIME }} />{b.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {!entry.daily?.length && !!entry.bills_due.length && <span style={{ fontSize: 13, color: "rgba(244,244,242,.66)" }}>{entry.bills_due.length} bill{entry.bills_due.length === 1 ? "" : "s"} due. Run a new analysis to see the day-by-day view.</span>}
    </div>
  );
}
