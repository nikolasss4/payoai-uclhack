import type { ReactNode } from "react";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge, day, decisionBadge, dayLabel, firstName, greeting, longToday, stamp, when } from "../lib";
import { Avatar, ErrorLine, Icon, Tile } from "../ui";
import { shortfallTitle, type Latest } from "./Cash";

export interface Balance { id: string; name: string; asset: string; balance: string | null; reserve?: string; spendable_after_reserve?: string; as_of?: string; stale?: boolean; note?: string }
export interface AuditEntry { id: number; at: string; actor_type: "USER" | "AGENT" | "SYSTEM" | "PROVIDER"; who: string; photo?: string | null; what: string; record: { kind: string; id: string; label: string } | null }

export const ACTOR: Record<string, [string, string]> = { USER: ["person", "Person"], AGENT: ["auto_awesome", "Agent"], SYSTEM: ["settings", "System"], PROVIDER: ["account_balance", "Provider"] };
export const freshness = (b: Balance) => (b.balance === null || b.balance === undefined ? badge("No balance") : b.stale ? badge("Stale") : badge("Fresh"));
/** A bank account in the asset's tint, a wallet in blue. */
export const accountLook = (b: Balance) => (b.asset === "GBP" || b.asset === "EUR" || b.asset === "USD"
  ? { icon: "account_balance", bg: "var(--ok-bg)", fg: "var(--ok)" }
  : { icon: "token", bg: "var(--info-bg)", fg: "var(--info)" });

const INK_MUTED = "rgba(244,244,242,.72)";
const INK_FAINT = "rgba(244,244,242,.55)";
const LIME = "#D4F53C";
const CORAL = "#FF7A6E";

export function Dashboard() {
  const { mobile, isA, open, go, session } = useApp();
  const pending = useLive<any[]>("/api/approvals?status=PENDING", 8000);
  const balances = useLive<{ balances: Balance[] }>("/api/balances", 15000);
  const latest = useLive<Latest>("/api/cash/latest", 30000);
  const activity = useLive<{ entries: AuditEntry[] }>("/api/audit?limit=5", 15000);
  const queue = (pending.data || []).filter((a) => !a.expired);
  const accounts = balances.data?.balances || [];
  const main = accounts.find((a) => a.balance !== null) || accounts[0];
  const rest = accounts.filter((a) => a !== main);
  const forecast = main ? latest.data?.result?.by_asset.find((e) => e.asset === main.asset) : undefined;
  const daily = forecast?.confident ? forecast.daily || [] : [];

  // The dark card: what can be spent today, and the days ahead from the latest analysis.
  const hero = (
    <div className="ink-card" style={{ flex: mobile ? undefined : "2 1 560px", alignSelf: mobile ? undefined : "flex-start", minWidth: 0, display: "flex", flexDirection: "column", gap: mobile ? 16 : 24, padding: mobile ? "20px 18px 16px" : "26px 28px 22px", borderRadius: mobile ? 26 : 28 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: mobile ? "center" : "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: mobile ? 13 : 14, fontWeight: 500, color: INK_MUTED }}>Spendable today{!mobile && main ? ` · ${main.name}` : ""}</span>
          {!mobile && main && <span style={{ fontSize: 12.5, color: INK_FAINT }}>{reserveLine(main)}</span>}
        </div>
        {main && <LiveChip b={main} mobile={mobile} />}
      </div>
      {balances.loading && <div className="skeleton" style={{ height: mobile ? 36 : 58, background: "rgba(244,244,242,.08)" }} />}
      {balances.data && !main && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="display" style={{ fontSize: mobile ? 24 : 34, fontWeight: 600, letterSpacing: "-0.04em" }}>No accounts yet</span>
          <span style={{ fontSize: 14, color: INK_MUTED }}>Connect a bank account or wallet on Cash to see what can be spent.</span>
        </div>
      )}
      {main && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="display num wrap" style={{ fontSize: mobile ? 36 : 58, fontWeight: 600, letterSpacing: "-0.045em", lineHeight: 1 }}>{main.spendable_after_reserve ?? "Unknown"}</span>
          {mobile && <span style={{ fontSize: 12, color: "rgba(244,244,242,.58)" }}>{reserveLine(main)}</span>}
        </div>
      )}
      {!!daily.length && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {!mobile && <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: INK_FAINT }}>Next {latest.data?.result?.days ?? daily.length} days</span>}
          <div role="img" aria-label="Projected position after reserve for the days ahead" style={{ display: "grid", gridTemplateColumns: `repeat(${daily.length},minmax(0,1fr))`, gap: mobile ? 5 : 8, alignItems: "end", height: mobile ? 72 : 110 }}>
            {daily.map((d, i) => {
              const today = dayLabel(d.date) === "Today";
              return (
                <div key={d.date} title={`${day(d.date)}: ${d.position}`} style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 8, height: "100%", justifyContent: "flex-end", minWidth: 0 }}>
                  <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "flex-end" }}>
                    <div style={{ width: "100%", height: `${d.short ? 6 : Math.max(4, d.ratio * 100)}%`, minHeight: mobile ? 5 : 6, borderRadius: mobile ? 6 : 8, background: d.short ? CORAL : i === 0 ? LIME : "rgba(244,244,242,.2)" }} />
                  </div>
                  <span style={{ fontSize: mobile ? 10 : 11.5, textAlign: "center", color: d.short ? CORAL : "rgba(244,244,242,.62)", whiteSpace: "nowrap" }}>{today ? "Today" : new Date(`${d.date}T00:00:00`).getDate()}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {main && <ForecastLink forecast={forecast} days={latest.data?.result?.days} loaded={!!latest.data} mobile={mobile} onOpen={() => go("cash")} />}
    </div>
  );

  const waiting = (
    <div style={{ flex: mobile ? undefined : 1, display: "flex", flexDirection: "column", justifyContent: "space-between", gap: mobile ? 14 : 20, padding: mobile ? 16 : 24, borderRadius: mobile ? 22 : 28, background: LIME, color: "#0B0B0C", minHeight: mobile ? 132 : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: mobile ? 13 : 14.5, fontWeight: 600 }}>Waiting on you</span>
        <span style={{ width: mobile ? 30 : 40, height: mobile ? 30 : 40, borderRadius: "50%", background: "#0B0B0C", color: LIME, display: "grid", placeItems: "center" }}><Icon name="approval" size={mobile ? 17 : 21} fill /></span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="display" style={{ fontSize: mobile ? 40 : 64, fontWeight: 600, letterSpacing: "-0.05em", lineHeight: 1 }}>{pending.data ? queue.length : "–"}</span>
        {!mobile && <span style={{ fontSize: 14, fontWeight: 500, color: "#2E3A00" }}>{queue.length === 1 ? "payment needs a decision" : "payments need a decision"}</span>}
      </div>
    </div>
  );

  const restCards = rest.map((a) => <AccountTile key={a.id} a={a} mobile={mobile} onOpen={() => go("cash")} />);

  const approvals = (
    <Panel title="Approvals" count={pending.data ? queue.length : undefined} mobile={mobile} grow={2}>
      {pending.loading && <div className="skeleton" style={{ height: 76, borderRadius: 18 }} />}
      <ErrorLine error={pending.error} />
      {!!queue.length && (
        <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 8 }}>
          {queue.map((a) => {
            const dec = a.decision ? decisionBadge(a.decision.decision) : badge("READY_FOR_APPROVAL");
            const sub = [a.invoice || a.reference, a.due_date ? `Due ${day(a.due_date)}` : null, dec.label].filter(Boolean).join(" · ");
            const btn = { background: isA ? "var(--primary-bg)" : "var(--raised)", color: isA ? "var(--primary-fg)" : "var(--text)" };
            const cta = isA ? "Review" : "View";
            return (
              <button key={a.approval_id} type="button" className="m-row" onClick={() => open({ kind: "approval", id: a.approval_id })} style={{ gap: mobile ? 12 : 14, padding: mobile ? 12 : 14, borderRadius: mobile ? 18 : 20, animation: "rise .25s" }}>
                <span style={{ position: "relative", flex: "none" }}>
                  <Tile name={a.counterparty} size={mobile ? 42 : 48} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: mobile ? 14 : 16, fontSize: mobile ? 13 : 14 }} />
                  <span style={{ position: "absolute", top: -3, right: -3, width: mobile ? 11 : 12, height: mobile ? 11 : 12, borderRadius: "50%", background: dec.fg, border: "2px solid var(--bg)" }} />
                </span>
                <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 3 : 4, minWidth: 0, flex: 1 }}>
                  <span style={{ fontSize: mobile ? 14.5 : 15.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.counterparty}</span>
                  <span style={{ fontSize: mobile ? 12 : 13, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</span>
                </div>
                {mobile ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flex: "none" }}>
                    <span className="num" style={{ fontSize: 15, fontWeight: 600 }}>{a.amount}</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 2, height: 30, padding: "0 8px 0 12px", borderRadius: 999, fontSize: 12.5, fontWeight: 600, ...btn }}>{cta}<Icon name="arrow_forward" size={15} /></span>
                  </div>
                ) : (
                  <>
                    <span className="num" style={{ flex: "none", fontSize: 17, fontWeight: 600, letterSpacing: "-0.01em" }}>{a.amount}</span>
                    <span style={{ flex: "none", display: "flex", alignItems: "center", gap: 4, height: 42, padding: "0 14px 0 18px", borderRadius: 999, fontSize: 14, fontWeight: 600, ...btn }}>{cta}<Icon name="arrow_forward" size={18} /></span>
                  </>
                )}
              </button>
            );
          })}
        </div>
      )}
      {pending.data && !queue.length && (
        <div style={{ display: "flex", alignItems: "center", gap: mobile ? 12 : 14, padding: mobile ? "16px 14px" : "22px 18px", borderRadius: mobile ? 18 : 20, background: "var(--ok-bg)", fontSize: mobile ? 14 : 15, fontWeight: 500 }}>
          <span style={{ width: mobile ? 36 : 40, height: mobile ? 36 : 40, flex: "none", borderRadius: "50%", background: "var(--accent)", color: "#0B0B0C", display: "grid", placeItems: "center" }}><Icon name="task_alt" size={mobile ? 20 : 22} /></span>
          {mobile ? "All clear. Nothing is waiting." : "All clear. Nothing is waiting for approval."}
        </div>
      )}
    </Panel>
  );

  const recent = (
    <Panel title="Activity" mobile={mobile} grow={1} action={<button type="button" onClick={() => go("audit")} style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 2, minHeight: 36, fontSize: mobile ? 13 : 13.5, fontWeight: 500, color: "var(--muted)", cursor: "pointer", background: "transparent", border: 0, padding: 0 }}>Audit<Icon name="chevron_right" size={mobile ? 17 : 18} /></button>}>
      {activity.loading && <div className="skeleton" style={{ height: 44 }} />}
      <ErrorLine error={activity.error} />
      {activity.data && !activity.data.entries.length && <div style={{ padding: "10px 8px", fontSize: 13.5, color: "var(--muted)" }}>Nothing has happened yet.</div>}
      {(activity.data?.entries || []).map((e) => (
        <button key={e.id} type="button" className="hoverable" onClick={() => e.record && open(e.record)}
          style={{ display: "flex", alignItems: "center", gap: mobile ? 10 : 12, padding: mobile ? "8px 6px" : "10px 8px", minHeight: mobile ? 52 : undefined, borderRadius: mobile ? 12 : 14, cursor: e.record ? "pointer" : "default", width: "100%", background: "transparent", border: 0, textAlign: "left", color: "inherit" }}>
          <ActorNode e={e} size={mobile ? 34 : 36} tone="plain" />
          <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: mobile ? 13.5 : 14, fontWeight: 500 }}>{e.what}</span>
            <span style={{ fontSize: mobile ? 12 : 12.5, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.who}{e.record ? ` · ${e.record.label}` : ""}</span>
          </div>
          <span className="mono" style={{ fontSize: mobile ? 11.5 : 12, color: "var(--subtle)" }}>{stamp(e.at)}</span>
        </button>
      ))}
    </Panel>
  );

  const title = (
    <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : 40, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: mobile ? 1.15 : 1.1 }}>
      {greeting()}, <span style={{ background: "var(--accent)", color: "#0B0B0C", padding: "0 .16em", borderRadius: ".2em" }}>{firstName(session.user.name)}</span>
    </h1>
  );

  if (mobile) {
    return (
      <div data-screen-label="M Dashboard" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "2px 2px 4px" }}><span style={{ fontSize: 13, color: "var(--muted)" }}>{longToday()}</span>{title}</div>
        <ErrorLine error={balances.error} />
        {hero}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 10 }}>{waiting}{restCards}</div>
        {approvals}
        {recent}
      </div>
    );
  }
  return (
    <div data-screen-label="Dashboard" style={{ padding: "32px 32px 64px", display: "flex", flexDirection: "column", gap: 28, maxWidth: 1180, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "flex-end", flexWrap: "wrap", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}><span style={{ fontSize: 14.5, color: "var(--muted)" }}>{longToday()}</span>{title}</div>
        <button type="button" className="btn-pill btn-primary press" onClick={() => go("chat")} style={{ marginLeft: "auto", height: 48, padding: "0 20px 0 16px", fontSize: 15, fontWeight: 600, gap: 8 }}><Icon name="auto_awesome" size={20} fill />Ask PayoAI</button>
      </div>
      <ErrorLine error={balances.error} />
      {/* Two columns that stack on their own, so Approvals sits right under the balance card whatever the height of the column beside it. */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 560px", minWidth: 0, display: "grid", gap: 16 }}>{hero}{approvals}</div>
        <div style={{ flex: "1 1 280px", minWidth: 0, display: "grid", gap: 16 }}>{waiting}{restCards}{recent}</div>
      </div>
    </div>
  );
}

function reserveLine(b: Balance) {
  const asOf = b.as_of ? when(b.as_of) : b.note || "no balance read yet";
  return b.reserve ? `After ${b.reserve} reserve · ${asOf}` : asOf;
}

function LiveChip({ b, mobile }: { b: Balance; mobile: boolean }) {
  const ok = b.balance !== null && b.balance !== undefined && !b.stale;
  const colour = ok ? LIME : "#F5B53D";
  const text = ok ? (mobile ? "Live" : "Live balance") : b.balance === null ? "No balance" : "Stale";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: mobile ? 5 : 6, height: mobile ? 24 : 28, padding: mobile ? "0 10px 0 8px" : "0 12px 0 10px", borderRadius: 999, background: ok ? "rgba(212,245,60,.12)" : "rgba(245,181,61,.14)", color: colour, fontSize: mobile ? 11.5 : 12.5, fontWeight: 600 }}>
      <span style={{ width: mobile ? 6 : 7, height: mobile ? 6 : 7, borderRadius: "50%", background: colour }} />{text}
    </span>
  );
}

/** The row under the bars: the shortfall when there is one, otherwise how the days ahead look. */
function ForecastLink({ forecast, days, loaded, mobile, onOpen }: { forecast?: NonNullable<Latest["result"]>["by_asset"][number]; days?: number; loaded: boolean; mobile: boolean; onOpen: () => void }) {
  if (!loaded) return null;
  const short = !!forecast?.confident && !!forecast.shortfall_date;
  const title = !forecast ? "No cash analysis yet" : !forecast.confident ? "The days ahead are uncertain" : short ? shortfallTitle(forecast) : `No shortfall in the next ${days ?? 7} days`;
  const sub = !forecast ? "Run one on Cash to see the days ahead" : !forecast.confident ? "A balance is stale, so this can't be stated with confidence" : short ? "If every due bill is paid on its due date" : `${forecast.projected} left after reserve`;
  const tint = short ? CORAL : forecast?.confident ? LIME : "rgba(244,244,242,.6)";
  const icon = short ? "trending_down" : !forecast ? "insights" : forecast.confident ? "trending_flat" : "question_mark";
  return (
    <button type="button" onClick={onOpen} style={{ display: "flex", alignItems: "center", gap: mobile ? 10 : 12, minHeight: mobile ? 52 : undefined, padding: mobile ? "8px 10px" : "12px 14px 12px 12px", borderRadius: mobile ? 14 : 16, background: short ? "rgba(255,122,110,.12)" : "rgba(244,244,242,.08)", cursor: "pointer", border: 0, color: "#F4F4F2", textAlign: "left", width: "100%" }}>
      <span style={{ width: mobile ? 32 : 36, height: mobile ? 32 : 36, borderRadius: mobile ? 10 : 12, background: tint, color: "#0B0B0C", display: "grid", placeItems: "center", flex: "none" }}><Icon name={icon} size={mobile ? 18 : 20} /></span>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: mobile ? 13.5 : 14.5, fontWeight: 600 }}>{title}</span>
        {!mobile && <span style={{ fontSize: 12.5, color: "rgba(244,244,242,.66)" }}>{sub}</span>}
      </div>
      {mobile ? <Icon name="arrow_forward" size={20} style={{ color: LIME }} /> : <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13.5, fontWeight: 600, color: LIME, whiteSpace: "nowrap" }}>Cash plan<Icon name="arrow_forward" size={18} /></span>}
    </button>
  );
}

function AccountTile({ a, mobile, onOpen }: { a: Balance; mobile: boolean; onOpen: () => void }) {
  const fb = freshness(a);
  const look = accountLook(a);
  const icon = <span style={{ width: mobile ? 30 : 30, height: 30, borderRadius: 10, background: look.bg, color: look.fg, display: "grid", placeItems: "center", flex: "none" }}><Icon name={look.icon} size={mobile ? 17 : 18} /></span>;
  const chip = <span className="bdg" style={{ color: fb.fg, background: fb.bg, borderColor: fb.bd, borderStyle: fb.bs }}>{fb.label}</span>;
  return (
    <button type="button" onClick={onOpen} className="hover-border"
      style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", gap: 12, padding: mobile ? 16 : "20px 22px", borderRadius: mobile ? 22 : 24, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", boxShadow: mobile ? undefined : "var(--shadow-1)", minHeight: mobile ? 132 : undefined, textAlign: "left", color: "inherit" }}>
      {mobile ? (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6, width: "100%" }}>{icon}{chip}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{a.name}</span>
            <span className="num wrap" style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.25 }}>{a.spendable_after_reserve ?? "Unknown"}</span>
          </div>
        </>
      ) : (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", width: "100%" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 500, minWidth: 0 }}>{icon}{a.name}</span>{chip}
          </div>
          <span className="num wrap" style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em" }}>{a.spendable_after_reserve ?? "Unknown"}</span>
        </>
      )}
    </button>
  );
}

function Panel({ title, count, action, mobile, grow, children }: { title: string; count?: number; action?: ReactNode; mobile: boolean; grow: number; children: ReactNode }) {
  return (
    <div style={{ flex: mobile ? undefined : grow === 2 ? "2 1 560px" : "1 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: mobile ? 10 : grow === 2 ? 14 : 6, padding: mobile ? (grow === 2 ? "16px 12px 12px" : "16px 10px 8px") : 22, borderRadius: mobile ? 24 : 28, background: "var(--surface)", border: "1px solid var(--border)", boxShadow: mobile ? undefined : "var(--shadow-1)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: mobile ? 8 : 10, padding: grow === 2 ? "0 4px" : mobile ? "0 6px 6px" : "0 4px 8px" }}>
        <span className="display" style={{ fontSize: mobile ? 16 : 18, fontWeight: 600, letterSpacing: "-0.02em" }}>{title}</span>
        {count !== undefined && <span style={{ minWidth: mobile ? 24 : 26, height: mobile ? 24 : 26, padding: mobile ? "0 7px" : "0 8px", borderRadius: 999, background: "var(--raised)", display: "grid", placeItems: "center", fontSize: mobile ? 12 : 13, fontWeight: 600 }}>{count}</span>}
        {action}
      </div>
      {children}
    </div>
  );
}

/** The circle for who did something: their picture when they have one, otherwise the actor's icon. */
export function ActorNode({ e, size, tone }: { e: { actor_type: string; who: string; photo?: string | null; kind?: string }; size: number; tone: "plain" | "accent" }) {
  const agent = tone === "accent" && e.actor_type === "AGENT";
  const icon = e.kind === "turn" ? "auto_awesome" : e.kind === "run" ? "account_tree" : ACTOR[e.actor_type]?.[0] || "circle";
  return (
    <span aria-hidden="true" style={{ position: "relative", width: size, height: size, flex: "none", borderRadius: "50%", display: "grid", placeItems: "center", overflow: "hidden", background: agent ? "var(--accent)" : "var(--raised)", color: agent ? "#0B0B0C" : "var(--text)" }}>
      <Icon name={icon} size={Math.round(size * 0.48)} fill={tone === "accent"} />
      {e.photo && <Avatar name={e.who} src={e.photo} size={size} style={{ position: "absolute", inset: 0, background: "transparent", color: "transparent" }} />}
    </span>
  );
}
