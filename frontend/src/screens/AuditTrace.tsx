/* Turns and runs in the Audit: a summary row that opens to every step, in order, with what each step read and wrote. */
import { useState, type ReactNode } from "react";
import { useApp } from "../app/context";
import { useLive } from "../hooks";
import { badge, hm, words } from "../lib";
import { Badge, ErrorLine, Icon, Skeleton } from "../ui";

export interface TraceEntry {
  id: string; kind: "turn" | "run"; trace_id: string; at: string; actor_type: "USER" | "AGENT" | "SYSTEM" | "PROVIDER";
  who: string; photo?: string | null; what: string; reply?: string | null; chat?: string | null; status: string; model?: string | null; error?: string | null;
  duration_ms: number | null; steps: number; failed: number; runs: number; tokens: number; cost_micros: number;
}

interface StepEvent { id: number; at: string; who: string; what: string; event_type: string; data: unknown; record: { kind: string; id: string; label: string } | null }
interface Step {
  id: string; run_id: string | null; parent_id: string | null; type: string; name: string; status: string; trust: string;
  input: unknown; output: unknown; error: string | null; evidence_ids: string[]; model: string | null; thinking: string | null;
  input_tokens: number | null; output_tokens: number | null; cost_micros: number | null; at: string; duration_ms: number | null; events: StepEvent[];
}
interface Run { id: string; routine: string; label: string; version: string; trigger: string; status: string; inputs: unknown; result: unknown; error: string | null; at: string }
interface Detail {
  kind: "turn" | "run";
  turn: { id: string; input: string; reply: string | null; model: string | null; prompt_version: string | null; status: string; at: string; duration_ms: number | null } | null;
  runs: Run[]; steps: Step[];
}

const STEP: Record<string, [string, string]> = {
  LLM_CALL: ["psychology", "Model call"], READ_TOOL: ["search", "Read tool"], ROUTINE_START: ["play_arrow", "Routine start"], NODE: ["radio_button_checked", "Node"],
  DECISION: ["gavel", "Decision"], REPLY_CHECK: ["fact_check", "Reply check"], MEMORY_WRITE: ["bookmark", "Memory write"], PROVIDER_CALL: ["account_balance", "Provider call"], TAP: ["touch_app", "Tap"],
};

export const duration = (ms: number | null | undefined) => (ms == null ? "" : ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`);
const cost = (micros: number | null | undefined) => (!micros ? "" : micros < 100 ? "<$0.0001" : `$${(micros / 1e6).toFixed(micros < 10000 ? 4 : 3)}`);
const tokens = (n: number | null | undefined) => (!n ? "" : n >= 1000 ? `${(n / 1000).toFixed(1)}k tok` : `${n} tok`);
const meta = (...parts: (string | null | undefined | false)[]) => parts.filter(Boolean).join(" · ");
const isEmpty = (v: unknown) => v == null || (typeof v === "object" && Object.keys(v as object).length === 0);

function statusBadge(status: string) {
  return badge(status);
}

/** The row in the timeline. Opening it loads the steps. */
export function TraceRow({ e, mobile }: { e: TraceEntry; mobile: boolean }) {
  const [open, setOpen] = useState(false);
  const icon = e.kind === "turn" ? "forum" : "account_tree";
  const summary = meta(
    `${e.steps} ${e.steps === 1 ? "step" : "steps"}`,
    e.kind === "turn" && e.runs > 0 && `${e.runs} ${e.runs === 1 ? "run" : "runs"}`,
    e.failed > 0 && `${e.failed} failed`,
    duration(e.duration_ms), tokens(e.tokens), cost(e.cost_micros),
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="hoverable"
        style={{ display: "flex", alignItems: "flex-start", gap: 10, textAlign: "left", background: "transparent", border: "1px solid var(--border)", borderRadius: 12, padding: mobile ? "10px 10px" : "10px 12px", cursor: "pointer", color: "var(--text)", minWidth: 0 }}>
        <Icon name={icon} size={18} style={{ color: "var(--muted)", marginTop: 1 }} />
        <span style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: mobile ? 14 : 14.5, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", whiteSpace: "pre-wrap" }}>
            <span style={{ color: "var(--muted)" }}>{e.kind === "turn" ? `${e.who} asked · ` : `${e.who} · `}</span>{e.what}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12, color: "var(--muted)" }}>
            {e.status !== "COMPLETED" && <Badge b={statusBadge(e.status)} size="xs" />}
            <span>{e.kind === "turn" ? "Agent turn" : "Routine run"}</span>
            {summary && <span className="mono" style={{ fontSize: 11.5 }}>{summary}</span>}
          </span>
        </span>
        <Icon name={open ? "expand_less" : "expand_more"} size={20} style={{ color: "var(--muted)" }} />
      </button>
      {open && <TraceDetail kind={e.kind} id={e.trace_id} mobile={mobile} />}
    </div>
  );
}

function TraceDetail({ kind, id, mobile }: { kind: "turn" | "run"; id: string; mobile: boolean }) {
  const { data, error, loading } = useLive<Detail>(`/api/audit/${kind}/${encodeURIComponent(id)}`);
  if (loading) return <Skeleton height={36} count={3} />;
  if (error || !data) return <ErrorLine error={error || "Couldn't load the steps."} />;

  // The turn's own steps in order; each run's steps sit together under the run, where the run first shows up.
  const runs = new Map(data.runs.map((r) => [r.id, r]));
  const blocks: ({ run: null; step: Step } | { run: Run; steps: Step[] })[] = [];
  const seen = new Map<string, Step[]>();
  for (const s of data.steps) {
    if (s.run_id && runs.has(s.run_id)) {
      let group = seen.get(s.run_id);
      if (!group) { group = []; seen.set(s.run_id, group); blocks.push({ run: runs.get(s.run_id)!, steps: group }); }
      group.push(s);
    } else blocks.push({ run: null, step: s });
  }
  for (const r of data.runs) if (!seen.has(r.id)) blocks.push({ run: r, steps: [] });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: mobile ? "4px 0 6px 6px" : "4px 0 6px 12px", borderLeft: "2px solid var(--border)", marginLeft: mobile ? 4 : 8, minWidth: 0 }}>
      {data.turn && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <Quote label="Asked">{data.turn.input}</Quote>
          {data.turn.reply && <Quote label="Replied">{data.turn.reply}</Quote>}
          <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{meta(data.turn.model, data.turn.prompt_version && `prompt ${data.turn.prompt_version}`, duration(data.turn.duration_ms), data.turn.id)}</span>
        </div>
      )}
      {!data.steps.length && <span style={{ fontSize: 13, color: "var(--muted)" }}>No steps were recorded.</span>}
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        {blocks.map((b, i) => b.run === null ? (
          <li key={b.step.id}><StepRow s={b.step} n={blocks.slice(0, i + 1).filter((x) => x.run === null).length} mobile={mobile} /></li>
        ) : (
          <li key={b.run.id}><RunBlock run={b.run} steps={b.steps} mobile={mobile} /></li>
        ))}
      </ol>
    </div>
  );
}

function RunBlock({ run, steps, mobile }: { run: Run; steps: Step[]; mobile: boolean }) {
  const [details, setDetails] = useState(false);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, background: "var(--surface)", padding: mobile ? 8 : 10, display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Icon name="account_tree" size={16} style={{ color: "var(--muted)" }} />
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{run.label} routine</span>
        <Badge b={statusBadge(run.status)} size="xs" />
        <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{meta(words(run.trigger), `v${run.version}`, hm(run.at), run.id)}</span>
        <button type="button" onClick={() => setDetails(!details)} style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)", background: "transparent", border: 0, cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}>
          {details ? "Hide inputs" : "Inputs and result"}
        </button>
      </div>
      {run.error && <Pre label="Error" value={run.error} danger />}
      {details && (<><Pre label="Inputs" value={run.inputs} />{!isEmpty(run.result) && <Pre label="Result" value={run.result} />}</>)}
      {!steps.length && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>No steps recorded for this run.</span>}
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        {steps.map((s, i) => <li key={s.id}><StepRow s={s} n={i + 1} mobile={mobile} /></li>)}
      </ol>
    </div>
  );
}

function StepRow({ s, n, mobile }: { s: Step; n: number; mobile: boolean }) {
  const { open } = useApp();
  const [show, setShow] = useState(false);
  const [icon, type] = STEP[s.type] || ["circle", words(s.type)];
  const failed = s.status === "FAILED";
  const facts = meta(s.model, s.thinking && `thinking ${s.thinking.toLowerCase()}`, s.input_tokens != null && `${s.input_tokens} in`, s.output_tokens != null && `${s.output_tokens} out`, cost(s.cost_micros));
  return (
    <div style={{ borderRadius: 10, background: show ? "var(--raised)" : "transparent", minWidth: 0 }}>
      <button type="button" aria-expanded={show} onClick={() => setShow(!show)} className="hoverable"
        style={{ width: "100%", display: "grid", gridTemplateColumns: mobile ? "20px 18px minmax(0,1fr) auto" : "24px 18px minmax(0,1fr) auto auto", alignItems: "center", gap: 8, padding: "6px 8px", border: 0, borderRadius: 10, background: "transparent", color: "var(--text)", cursor: "pointer", textAlign: "left", minHeight: mobile ? 40 : 34 }}>
        <span className="mono" style={{ fontSize: 11, color: "var(--subtle)", textAlign: "right" }}>{n}</span>
        <Icon name={icon} size={16} style={{ color: failed ? "var(--danger)" : "var(--muted)" }} />
        <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
          <span style={{ fontSize: 13.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name}
            {s.trust === "UNTRUSTED" && <span style={{ marginLeft: 6, fontSize: 11, color: "var(--warn)" }}>untrusted input</span>}
            {s.events.length > 0 && <span style={{ marginLeft: 6, fontSize: 11, color: "var(--muted)" }}>· {s.events.length} audit {s.events.length === 1 ? "event" : "events"}</span>}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{type}{mobile && s.duration_ms != null ? ` · ${duration(s.duration_ms)}` : ""}</span>
        </span>
        {s.status !== "COMPLETED" ? <Badge b={statusBadge(s.status)} size="xs" /> : <span />}
        {!mobile && <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 56, textAlign: "right" }}>{duration(s.duration_ms)}</span>}
      </button>
      {show && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: mobile ? "2px 8px 10px" : "2px 10px 10px 58px", minWidth: 0 }}>
          <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)", wordBreak: "break-all" }}>{meta(hm(s.at), facts, s.id)}</span>
          {s.error && <Pre label="Error" value={s.error} danger />}
          <Pre label="Input" value={s.input} />
          <Pre label="Output" value={s.output} />
          {s.evidence_ids.length > 0 && <Pre label="Evidence" value={s.evidence_ids} />}
          {s.events.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="kicker" style={{ fontSize: 11 }}>Audit events</span>
              {s.events.map((ev) => (
                <div key={ev.id} style={{ fontSize: 13, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "baseline" }}>
                  <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{hm(ev.at)}</span>
                  <span>{ev.who} · {ev.what}</span>
                  {ev.record && <button type="button" onClick={() => open(ev.record!)} style={{ fontSize: 12.5, color: "var(--text)", background: "transparent", border: 0, padding: 0, cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 3 }}>{ev.record.label}</button>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Quote({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <span className="kicker" style={{ fontSize: 11 }}>{label}</span>
      <div style={{ fontSize: 13.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 220, overflow: "auto", padding: "8px 10px", borderRadius: 10, background: "var(--raised)" }}>{children}</div>
    </div>
  );
}

function Pre({ label, value, danger }: { label: string; value: unknown; danger?: boolean }) {
  const text = value == null ? "—" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
      <span className="kicker" style={{ fontSize: 11, color: danger ? "var(--danger)" : undefined }}>{label}</span>
      <pre className="mono" style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 320, overflow: "auto", padding: "8px 10px", borderRadius: 8, background: "var(--surface)", border: `1px solid ${danger ? "var(--danger-bd)" : "var(--border)"}`, color: danger ? "var(--danger)" : "var(--text)" }}>{text}</pre>
    </div>
  );
}
