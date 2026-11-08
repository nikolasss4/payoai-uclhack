import { useState } from "react";
import { download } from "../api";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge } from "../lib";
import { Badge, Btn, Empty, ErrorLine, Icon, Segments, Skeleton } from "../ui";
import { Table } from "./Payments";

interface ExpenseRow { id: string; version: number; category: string | null; category_is_suggestion: boolean; status: string; gross: string | null; purpose: string | null; counterparty: string | null }

export const Suggestion = ({ small }: { small?: boolean }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: small ? 2 : 3, height: small ? 20 : 22, padding: small ? "0 6px" : "0 8px", borderRadius: 999, fontSize: small ? 11 : 11.5, border: "1px dashed var(--border-strong)", color: "var(--muted)" }}>
    <Icon name="auto_awesome" size={small ? 12 : 13} />Suggestion
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
  const cols = "minmax(0,1.4fr) minmax(0,1.2fr) minmax(140px,1fr) 170px 24px";
  const category = (e: ExpenseRow, small?: boolean) => (
    <>
      {e.category || <span style={{ color: "var(--muted)" }}>No category</span>}
      {e.category && e.category_is_suggestion && <Suggestion small={small} />}
    </>
  );
  return (
    <div data-screen-label={mobile ? "M Expenses" : "Expenses"} style={mobile ? { display: "flex", flexDirection: "column", gap: 14 } : { padding: "28px 32px 64px", display: "flex", flexDirection: "column", gap: 18, maxWidth: 1120 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 4 : 6 }}>
        <h1 className="display" style={{ margin: 0, fontSize: mobile ? 26 : 32, fontWeight: 600, letterSpacing: "-0.03em" }}>Expenses</h1>
        <span style={{ fontSize: mobile ? 13 : 15, color: "var(--muted)" }}>Prepared for accountant review, not certified compliant.</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Segments items={[["all", "All"], ["missing", "Missing evidence"]]} value={filter} onChange={setFilter} mobile={mobile} />
        {!exporting && <Btn icon="download" onClick={() => setExporting(true)}>Export</Btn>}
      </div>
      {exporting && <ExportPanel status={filter === "missing" ? "MISSING_EVIDENCE" : undefined} onClose={() => setExporting(false)} mobile={mobile} />}
      {loading && <Skeleton height={mobile ? 72 : 56} count={2} radius={mobile ? 14 : 12} />}
      <ErrorLine error={error} />
      {data && !rows.length && <Empty compact={mobile}>No expenses here.</Empty>}
      {!!rows.length && (mobile ? (
        <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {rows.map((e) => (
            <div key={e.id} className="list-row" role="button" tabIndex={0} onClick={() => open({ kind: "expense", id: e.id })} onKeyDown={(k) => k.key === "Enter" && open({ kind: "expense", id: e.id })}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}><span style={{ fontSize: 14.5, fontWeight: 500 }}>{e.counterparty || "Unknown"}</span><span className="num wrap" style={{ fontSize: 14.5, fontWeight: 600 }}>{e.gross || "—"}</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", fontSize: 12.5, color: "var(--muted)" }}>{category(e, true)}</span>
                <Badge b={badge(e.status)} />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Table cols={cols} head={["Counterparty", "Category", "Gross", "Status", ""]}>
          {rows.map((e) => (
            <div key={e.id} className="table-row" role="button" tabIndex={0} style={{ gridTemplateColumns: cols }} onClick={() => open({ kind: "expense", id: e.id })} onKeyDown={(k) => k.key === "Enter" && open({ kind: "expense", id: e.id })}>
              <span style={{ fontSize: 14.5, fontWeight: 500 }}>{e.counterparty || "Unknown"}</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 13.5 }}>{category(e)}</span>
              <span className="num wrap" style={{ textAlign: "right", fontSize: 14.5, fontWeight: 500 }}>{e.gross || "—"}</span>
              <span><Badge b={badge(e.status)} /></span>
              <Icon name="chevron_right" size={20} style={{ color: "var(--subtle)" }} />
            </div>
          ))}
        </Table>
      ))}
    </div>
  );
}
