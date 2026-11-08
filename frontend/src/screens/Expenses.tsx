import { useState } from "react";
import { download } from "../api";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge } from "../lib";
import { Badge, Btn, Empty, ErrorLine, Icon, ListCard, PageTitle, Segments, Skeleton, Tile } from "../ui";
import { MRow, Table } from "./Payments";

interface ExpenseRow { id: string; version: number; category: string | null; category_is_suggestion: boolean; status: string; gross: string | null; purpose: string | null; counterparty: string | null }

export const Suggestion = ({ small }: { small?: boolean }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: small ? 2 : 3, height: small ? 20 : 24, padding: small ? "0 7px 0 5px" : "0 9px 0 7px", borderRadius: 999, fontSize: small ? 11 : 12, fontWeight: 500, background: "var(--ok-bg)", color: "var(--accent-text)" }}>
    <Icon name="auto_awesome" size={small ? 12 : 14} />Suggestion
  </span>
);

type Preset = "month" | "last_month" | "quarter" | "year" | "custom";
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function presetRange(p: Exclude<Preset, "custom">, now = new Date()): [string, string] {
  const y = now.getFullYear(), m = now.getMonth();
  if (p === "month") return [iso(new Date(y, m, 1)), iso(now)];
  if (p === "last_month") return [iso(new Date(y, m - 1, 1)), iso(new Date(y, m, 0))];
  if (p === "quarter") return [iso(new Date(y, m - (m % 3), 1)), iso(now)];
  return [iso(new Date(y, 0, 1)), iso(now)];
}

/** Downloads the expenses recorded in a chosen period as a CSV for the accountant. */
function ExportPanel({ status, onClose, mobile }: { status?: string; onClose: () => void; mobile: boolean }) {
  const [preset, setPreset] = useState<Preset>("month");
  const [[start, end], setRange] = useState<[string, string]>(() => presetRange("month"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choose = (p: Preset) => {
    setPreset(p);
    if (p !== "custom") setRange(presetRange(p));
  };
  const invalid = !start || !end ? "Choose a start and an end date." : end < start ? "The end date is before the start date." : null;
  const run = async () => {
    if (invalid) return setError(invalid);
    setBusy(true);
    setError(null);
    try {
      const q = new URLSearchParams({ start, end });
      if (status) q.set("status", status);
      await download(`/api/expenses/export?${q}`, `expenses_${start}_to_${end}.csv`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card" role="region" aria-label="Export expenses" style={{ display: "flex", flexDirection: "column", gap: 14, padding: mobile ? 14 : 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Export expenses</span>
        <span style={{ fontSize: 12.5, color: "var(--muted)" }}>CSV, by the date each expense was recorded{status ? ", missing evidence only" : ""}</span>
      </div>
      <Segments items={[["month", "This month"], ["last_month", "Last month"], ["quarter", "This quarter"], ["year", "This year"], ["custom", "Custom"]]} value={preset} onChange={choose} wrap />
      <div style={{ display: "grid", gridTemplateColumns: mobile ? "1fr" : "1fr 1fr", gap: 10 }}>
        <label className="field">From<input className="input" type="date" value={start} max={end || undefined} onChange={(e) => { setPreset("custom"); setRange([e.target.value, end]); }} /></label>
        <label className="field">To<input className="input" type="date" value={end} min={start || undefined} onChange={(e) => { setPreset("custom"); setRange([start, e.target.value]); }} /></label>
      </div>
      <ErrorLine error={error} />
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <Btn onClick={onClose}>Cancel</Btn>
        <Btn kind="primary" icon="download" busy={busy} disabled={!!invalid} onClick={run}>Download CSV</Btn>
      </div>
    </div>
  );
}

export function Expenses() {
  const { mobile, open } = useApp();
  const [filter, setFilter] = useState<"all" | "missing">("all");
  const [exporting, setExporting] = useState(false);
  const { data, error, loading } = useLive<ExpenseRow[]>(filter === "missing" ? "/api/expenses?status=MISSING_EVIDENCE" : "/api/expenses", 15000);
  const rows = data || [];
  const cols = "minmax(0,1.6fr) minmax(0,1.2fr) minmax(0,1fr) 164px 20px";
  const category = (e: ExpenseRow, small?: boolean) => (
    <>
      {e.category || <span style={{ color: "var(--muted)" }}>No category</span>}
      {e.category && e.category_is_suggestion && <Suggestion small={small} />}
    </>
  );
  return (
    <div data-screen-label={mobile ? "M Expenses" : "Expenses"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "32px 32px 64px", display: "flex", flexDirection: "column", gap: 22, maxWidth: 1180, margin: "0 auto" }}>
      <PageTitle mobile={mobile} title="Expenses" sub="Prepared for accountant review, not certified compliant." />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: mobile ? "nowrap" : "wrap" }}>
        <div style={{ flex: mobile ? 1 : undefined, minWidth: 0, display: "flex" }}><Segments items={[["all", "All"], ["missing", "Missing evidence"]]} value={filter} onChange={setFilter} mobile={mobile} /></div>
        {!exporting && <Btn icon="download" onClick={() => setExporting(true)} label={mobile ? "Export" : undefined} style={mobile ? { width: 48, padding: 0, background: "var(--surface)" } : { height: 46, minHeight: 0, fontSize: 14.5, fontWeight: 600, padding: "0 18px 0 14px", background: "var(--surface)" }}>{mobile ? null : "Export"}</Btn>}
      </div>
      {exporting && <ExportPanel status={filter === "missing" ? "MISSING_EVIDENCE" : undefined} onClose={() => setExporting(false)} mobile={mobile} />}
      {loading && <Skeleton height={68} count={3} radius={18} />}
      <ErrorLine error={error} />
      {data && !rows.length && <Empty compact={mobile}>No expenses here.</Empty>}
      {!!rows.length && (mobile ? (
        <ListCard mobile>
          {rows.map((e) => <MRow key={e.id} name={e.counterparty || "Unknown"} amount={e.gross || "—"} meta={category(e, true)} status={<Badge b={badge(e.status)} size="lg" />} onOpen={() => open({ kind: "expense", id: e.id })} />)}
        </ListCard>
      ) : (
        <Table cols={cols} head={["Counterparty", "Category", "Gross", "Status", ""]}>
          {rows.map((e) => (
            <div key={e.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: cols }} onClick={() => open({ kind: "expense", id: e.id })} onKeyDown={(k) => k.key === "Enter" && open({ kind: "expense", id: e.id })}>
              <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}><Tile name={e.counterparty || "Unknown"} /><span className="wrap" style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.3 }}>{e.counterparty || "Unknown"}</span></div>
              <span style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 14 }}>{category(e)}</span>
              <span className="num wrap" style={{ textAlign: "right", fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>{e.gross || "—"}</span>
              <span><Badge b={badge(e.status)} size="lg" /></span>
              <Icon name="chevron_right" size={20} style={{ color: "var(--subtle)" }} />
            </div>
          ))}
        </Table>
      ))}
    </div>
  );
}
