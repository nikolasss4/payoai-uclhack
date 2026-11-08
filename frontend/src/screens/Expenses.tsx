import { useState } from "react";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge } from "../lib";
import { Badge, Empty, ErrorLine, Icon, Segments, Skeleton } from "../ui";
import { Table } from "./Payments";

interface ExpenseRow { id: string; version: number; category: string | null; category_is_suggestion: boolean; status: string; gross: string | null; purpose: string | null; counterparty: string | null }

export const Suggestion = ({ small }: { small?: boolean }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: small ? 2 : 3, height: small ? 20 : 22, padding: small ? "0 6px" : "0 8px", borderRadius: 999, fontSize: small ? 11 : 11.5, border: "1px dashed var(--border-strong)", color: "var(--muted)" }}>
    <Icon name="auto_awesome" size={small ? 12 : 13} />Suggestion
  </span>
);

export function Expenses() {
  const { mobile, open } = useApp();
  const [filter, setFilter] = useState<"all" | "missing">("all");
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
      <Segments items={[["all", "All"], ["missing", "Missing evidence"]]} value={filter} onChange={setFilter} mobile={mobile} />
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
