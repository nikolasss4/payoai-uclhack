import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api";
import { useApp } from "../app/context";
import { useAction, useLive } from "../hooks";
import { badge, decisionBadge, fileIcon, hm, label, plural } from "../lib";
import { Badge, ErrorLine, Icon } from "../ui";
import type { ChatEvent, Item, Part } from "./useChat";

const AGENT_MARK = (
  <span aria-hidden="true" style={{ width: 28, height: 28, borderRadius: 8, background: "var(--accent)", color: "#0B0B0C", display: "grid", placeItems: "center", flex: "none", fontWeight: 700, fontSize: 14 }}>P</span>
);
const cardBase = { marginLeft: 38, display: "flex", flexDirection: "column" as const, background: "var(--surface)", animation: "rise .2s" };

export function Messages({ items, typing, onAnswer, onDecideStart }: {
  items: Item[]; typing: boolean;
  onAnswer: (text: string, shown: string) => Promise<void>;
  onDecideStart: (id: string, d: "confirm" | "decline") => Promise<string>;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }} aria-live="polite">
      {items.map((m) => {
        switch (m.t) {
          case "user": return <UserMsg key={m.key} m={m} />;
          case "agent": return <AgentMsg key={m.key} parts={m.parts} />;
          case "steps": return <StepsCard key={m.key} m={m} />;
          case "tasks": return <TasksCard key={m.key} tasks={m.tasks} />;
          case "start": return <StartCard key={m.key} ev={m.ev} onDecide={onDecideStart} />;
          case "question": return <QuestionCard key={m.key} m={m} onAnswer={onAnswer} />;
          case "approval": return <ApprovalChatCard key={m.key} id={m.approvalId} />;
          case "error": return m.code && m.fixable ? <KeyErrorMsg key={m.key} m={m} onRetry={onAnswer} /> : <ErrorMsg key={m.key} message={m.message} />;
          case "thinking": return <ThinkingCard key={m.key} m={m} />;
          case "draft": return <DraftMsg key={m.key} text={m.text} />;
          default: return null;
        }
      })}
      {typing && !showsProgress(items) && (
        <div style={{ display: "flex", gap: 10, alignItems: "center" }} role="status" aria-label="PayoAI is working">
          {AGENT_MARK}
          <div style={{ display: "flex", gap: 4, padding: "12px 14px", borderRadius: 14, background: "var(--surface)", border: "1px solid var(--border)" }}>
            {[0, 0.2, 0.4].map((d) => <span key={d} style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--muted)", animation: `blink 1s ${d}s infinite` }} />)}
          </div>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>Working…</span>
        </div>
      )}
    </div>
  );
}

/** The thinking block or a streaming draft already shows progress, so the dots would repeat it. */
function showsProgress(items: Item[]) {
  const tail = items[items.length - 1];
  return !!tail && ((tail.t === "thinking" && tail.live) || tail.t === "draft");
}

/** What the Main Loop is doing: code's own steps, plus the model's thought summaries when it gives any. */
function ThinkingCard({ m }: { m: Extract<Item, { t: "thinking" }> }) {
  const [openState, setOpen] = useState(false);
  const open = m.live || openState;
  const seconds = Math.max(1, Math.round((new Date(m.endedAt).getTime() - new Date(m.startedAt).getTime()) / 1000));
  const latest = m.lines[m.lines.length - 1];
  const summary = m.live ? headline(latest?.text || "Thinking") : `Thought for ${seconds}s · ${plural(m.lines.length, "step")}`;
  return (
    <div style={{ marginLeft: 38, display: "flex", flexDirection: "column", maxWidth: 560, animation: "rise .2s" }}>
      <button type="button" onClick={() => !m.live && setOpen(!openState)} aria-expanded={open}
        style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 32, padding: 0, background: "transparent", border: 0, textAlign: "left", cursor: m.live ? "default" : "pointer", color: "var(--muted)", fontSize: 13.5 }}>
        <Icon name={m.live ? "progress_activity" : "psychology"} size={17} className={m.live ? "spin" : ""} style={{ color: m.live ? "var(--info)" : "var(--muted)" }} />
        <span className="wrap" style={{ flex: 1, fontWeight: m.live ? 500 : 400, color: m.live ? "var(--text)" : "var(--muted)" }}>{summary}</span>
        {!m.live && <Icon name={openState ? "expand_less" : "expand_more"} size={18} />}
      </button>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "2px 0 0 8px", padding: "2px 0 2px 16px", borderLeft: "2px solid var(--border)" }}>
          {m.lines.map((line, k) => {
            const current = m.live && k === m.lines.length - 1;
            const body = line.source === "thought" ? line.text.replace(/^\*\*[^*]+\*\*\s*/, "").trim() : "";
            return (
              <div key={k} style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12.5, lineHeight: 1.45, color: current ? "var(--text)" : "var(--muted)" }}>
                <span className="wrap" style={{ fontWeight: line.source === "thought" ? 500 : 400 }}>{headline(line.text)}</span>
                {body && <span className="wrap" style={{ color: "var(--muted)" }}>{body}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** A thought summary starts with a bold title. A code step is one short line already. */
const headline = (text: string) => (text.match(/^\*\*([^*]+)\*\*/)?.[1] || text.split("\n")[0]).trim();

/** The reply as it streams. It hasn't passed the Reply Check yet, so it has no Chips and is marked as unchecked. */
function DraftMsg({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }} aria-busy="true">
      {AGENT_MARK}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0, flex: 1, maxWidth: 620, paddingTop: 3 }}>
        <span className="wrap" style={{ fontSize: 17, lineHeight: 1.55, whiteSpace: "pre-wrap", color: "var(--muted)" }}>
          {text.replace(/\*\*/g, "").replace(/\[\[(\w+):[^\]]*\]\]/g, "($1)")}<span style={{ display: "inline-block", width: 7, height: 17, marginLeft: 2, verticalAlign: "text-bottom", background: "var(--muted)", animation: "blink 1s infinite" }} />
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--muted)" }}><Icon name="fact_check" size={14} />Checking figures against the records…</span>
      </div>
    </div>
  );
}

function UserMsg({ m }: { m: Extract<Item, { t: "user" }> }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, animation: "rise .2s", opacity: m.sending ? 0.7 : 1 }}>
      {m.files.map((file, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px 10px 10px", borderRadius: 14, background: "var(--surface)", border: "1px solid var(--border)", maxWidth: "85%" }}>
          <span style={{ width: 36, height: 36, borderRadius: 9, background: "var(--danger-bg)", color: "var(--danger)", display: "grid", placeItems: "center", flex: "none" }}><Icon name={fileIcon(file.name, file.media)} size={20} /></span>
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <span className="wrap" style={{ fontSize: 13.5, fontWeight: 500 }}>{file.name}</span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{file.meta}</span>
          </div>
        </div>
      ))}
      {m.text && <div className="wrap" style={{ maxWidth: "85%", padding: "11px 16px", borderRadius: "20px 20px 6px 20px", background: "var(--primary-bg)", color: "var(--primary-fg)", fontSize: 17, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>{m.text}</div>}
      {m.sending && <span style={{ fontSize: 12, color: "var(--muted)" }}>Sending…</span>}
    </div>
  );
}

type Seg = { kind: "text"; text: string; bold: boolean } | { kind: "chip"; part: Part };

/** Splits a reply into lines, bullet points and bold runs. Chips stay where the reply put them. */
function toLines(parts: Part[]) {
  const lines: { li: boolean; segs: Seg[] }[] = [{ li: false, segs: [] }];
  for (const part of parts) {
    if (part.type === "chip") { lines[lines.length - 1].segs.push({ kind: "chip", part }); continue; }
    String(part.text || "").split("\n").forEach((piece, i) => {
      if (i > 0) lines.push({ li: false, segs: [] });
      if (piece) lines[lines.length - 1].segs.push({ kind: "text", text: piece, bold: false });
    });
  }
  return lines.map((line) => {
    const first = line.segs[0];
    if (first && first.kind === "text" && /^\s*[-*•]\s+/.test(first.text)) {
      line.li = true;
      first.text = first.text.replace(/^\s*[-*•]\s+/, "");
    }
    let bold = false;
    const segs: Seg[] = [];
    for (const s of line.segs) {
      if (s.kind === "chip") { segs.push(s); continue; }
      s.text.split("**").forEach((t, i) => {
        if (i > 0) bold = !bold;
        if (t) segs.push({ kind: "text", text: t, bold });
      });
    }
    return { li: line.li, segs };
  }).filter((l) => l.segs.some((s) => s.kind === "chip" || s.text.trim()));
}

function AgentMsg({ parts }: { parts: Part[] }) {
  const lines = toLines(parts);
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start", animation: "rise .2s" }}>
      {AGENT_MARK}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0, flex: 1, maxWidth: 620, paddingTop: 3 }}>
        {!lines.length && <span style={{ fontSize: 17, color: "var(--muted)" }}>This reply has no content to show.</span>}
        {lines.map((line, i) => (
          <div key={i} style={{ display: "flex", gap: 8, fontSize: 17, lineHeight: 1.55, textWrap: "pretty" } as any}>
            {line.li && <span style={{ flex: "none", width: 5, height: 5, borderRadius: "50%", background: "var(--text)", margin: "10px 4px 0 4px" }} />}
            <span className="wrap">{line.segs.map((s, j) => (s.kind === "chip" ? <Chip key={j} part={s.part} /> : <span key={j} style={{ fontWeight: s.bold ? 600 : 400 }}>{s.text}</span>))}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const CHIP_KINDS: Record<string, string> = { payment: "payment", bill: "bill", expense: "expense", account: "account" };

/** A money fact from a live record. Its values come from the record, never from the reply's words. */
function Chip({ part }: { part: Part }) {
  const { open } = useApp();
  const b = part.missing ? badge("Record not found", "record not found") : part.status === "stale balance" ? badge("Stale") : part.status ? badge(part.status) : null;
  const text = part.missing ? `${part.kind} ${part.id}` : [part.label, part.amount].filter(Boolean).join(" · ");
  const target = !part.missing && part.kind ? CHIP_KINDS[part.kind] : undefined;
  const body = (
    <>
      <span className="wrap num">{text || "Record"}</span>
      {b && <span className="bdg" style={{ color: b.fg, background: b.bg, borderColor: "transparent" }}><Icon name={b.icon} size={14} />{b.label}</span>}
    </>
  );
  const style = { display: "inline-flex", alignItems: "center", gap: 8, minHeight: 36, padding: "4px 6px 4px 12px", margin: "2px 2px", borderRadius: 999, background: "var(--surface)", border: `1px ${b?.bs || "solid"} ${b?.bd || "var(--border)"}`, fontSize: 13.5, maxWidth: "100%", verticalAlign: "middle", lineHeight: 1.3 } as const;
  if (!target) return <span style={style}>{body}</span>;
  return <button type="button" className="hoverable" style={style} onClick={() => open({ kind: target, id: part.id })}>{body}</button>;
}

function StepsCard({ m }: { m: Extract<Item, { t: "steps" }> }) {
  const [openState, setOpen] = useState(false);
  const open = m.running || openState;
  const flagged = m.flagged || [];
  const f = m.finished;
  const decision = f?.decision ? decisionBadge(f.decision) : f?.status ? badge(f.status) : null;
  const researching = m.rows[0] === "Web Research";
  const summary = m.running
    ? researching ? (m.rows.length > 1 ? `Searching the web · ${m.rows[m.rows.length - 1]}` : "Searching the web") : `Running checks · step ${m.rows.length}`
    : [f?.routine ? label(f.routine) : null, m.rows.length ? plural(m.rows.length, "step") : null, flagged.length ? `${flagged.length} flagged` : f?.decision === "ELIGIBLE_FOR_APPROVAL" ? "all passed" : null, !f ? "paused" : null].filter(Boolean).join(" · ");
  return (
    <div style={{ ...cardBase, borderRadius: 14, border: "1px solid var(--border)", overflow: "hidden", maxWidth: 560 }}>
      <button type="button" onClick={() => !m.running && setOpen(!openState)} aria-expanded={open}
        style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, padding: "0 14px", cursor: m.running ? "default" : "pointer", fontSize: 13.5, background: "transparent", border: 0, textAlign: "left" }}>
        {m.running ? <Icon name="progress_activity" size={18} className="spin" style={{ color: "var(--info)" }} /> : <Icon name="checklist" size={18} style={{ color: "var(--muted)" }} />}
        <span style={{ fontWeight: 500, flex: 1 }}>{summary || "Routine"}</span>
        {!m.running && decision && <Badge b={decision} size="sm" />}
        {!m.running && <Icon name={openState ? "expand_less" : "expand_more"} size={20} style={{ color: "var(--muted)" }} />}
      </button>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", padding: "0 14px 10px" }}>
          {m.rows.map((name, k) => {
            const live = m.running && k === m.rows.length - 1;
            return (
              <div key={k} className="mono" style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0", fontSize: 12.5 }}>
                <Icon name={live ? "progress_activity" : "check"} size={15} className={live ? "spin" : ""} style={{ color: "var(--muted)" }} />
                <span>{name}</span>
              </div>
            );
          })}
          {flagged.map((c, k) => {
            const bad = c.outcome === "BLOCK";
            const fg = bad ? "var(--danger)" : "var(--warn)";
            return (
              <div key={`f${k}`} style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "3px 0", fontSize: 12.5 }}>
                <Icon name={bad ? "close" : "priority_high"} size={15} style={{ color: fg, alignSelf: "center" }} />
                <span className="mono">{c.check_name}</span>
                <span style={{ color: fg }}>· {c.reason}</span>
              </div>
            );
          })}
          {f?.stop && <div style={{ fontSize: 13, color: "var(--muted)", paddingTop: 6 }}>{f.stop}</div>}
        </div>
      )}
    </div>
  );
}

function TasksCard({ tasks }: { tasks: { description: string; status: string }[] }) {
  return (
    <div style={{ ...cardBase, borderRadius: 14, border: "1px solid var(--border)", maxWidth: 560, padding: "10px 14px", gap: 2 }}>
      <span className="kicker" style={{ fontSize: 11.5, paddingBottom: 4 }}>Plan</span>
      {tasks.map((t, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0", fontSize: 13.5 }}>
          <Icon name={t.status === "DONE" ? "check_circle" : t.status === "CANCELLED" ? "cancel" : "radio_button_unchecked"} size={16} style={{ color: t.status === "DONE" ? "var(--ok)" : "var(--muted)" }} />
          <span style={{ textDecoration: t.status === "CANCELLED" ? "line-through" : undefined }}>{t.description}</span>
        </div>
      ))}
    </div>
  );
}

function StartCard({ ev, onDecide }: { ev: ChatEvent; onDecide: (id: string, d: "confirm" | "decline") => Promise<string> }) {
  const { canWrite, isV } = useApp();
  const { busy, error, run } = useAction();
  const p = ev.payload || {};
  const status = ev.confirmation_status || "PENDING";
  const decided = status !== "PENDING";
  const decidedText = status === "CONFIRMED" ? `Started by ${ev.requested_by || "you"}` : status === "EXPIRED" ? "Expired. Nothing was started." : "Not now. Nothing was started.";
  return (
    <div style={{ ...cardBase, gap: 12, padding: 16, borderRadius: 16, border: "1px solid var(--border-strong)", maxWidth: 480, boxShadow: "var(--shadow-1)" }}>
      <span className="kicker" style={{ fontSize: 11.5, letterSpacing: ".06em" }}>Tap to start</span>
      <span style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.01em" }}>{p.routine}</span>
      {!!ev.inputs_display?.length && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {ev.inputs_display.map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", padding: "8px 0", borderTop: "1px solid var(--border)", fontSize: 13.5 }}>
              <span style={{ color: "var(--muted)" }}>{k}</span><span className="wrap" style={{ textAlign: "right" }}>{v}</span>
            </div>
          ))}
        </div>
      )}
      <span style={{ display: "flex", gap: 6, fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45 }}><Icon name="info" size={16} />This only starts the checks. It doesn't pay anything or verify anyone.</span>
      {!decided && canWrite && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <button type="button" className="btn-pill" aria-disabled={busy || undefined} onClick={() => run(() => onDecide(p.confirmation_id, "decline"))}>Not now</button>
          <button type="button" className="btn-pill btn-primary" aria-disabled={busy || undefined} onClick={() => run(() => onDecide(p.confirmation_id, "confirm"))}>
            <Icon name={busy ? "progress_activity" : "play_arrow"} size={18} className={busy ? "spin" : ""} />Start
          </button>
        </div>
      )}
      {!decided && isV && <span style={{ fontSize: 13, color: "var(--muted)" }}>View only. An Operator or Approver can start this.</span>}
      <ErrorLine error={error} />
      {decided && <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13.5, color: "var(--muted)", paddingTop: 2 }}><Icon name={status === "CONFIRMED" ? "play_circle" : "do_not_disturb_on"} size={18} />{decidedText}</div>}
    </div>
  );
}

function QuestionCard({ m, onAnswer }: { m: Extract<Item, { t: "question" }>; onAnswer: (text: string, shown: string) => Promise<void> }) {
  const { isV } = useApp();
  const { busy, error, run } = useAction();
  const p = m.ev.payload || {};
  const options: { label: string; value: string }[] = p.options || [];
  return (
    <div style={{ ...cardBase, gap: 10, padding: 16, borderRadius: 16, border: "1px solid var(--border)", maxWidth: 480 }}>
      <span className="kicker" style={{ fontSize: 11.5, letterSpacing: ".06em" }}>Question</span>
      <span style={{ fontSize: 15.5, fontWeight: 500, lineHeight: 1.4 }}>{p.prompt}</span>
      {!m.answered && !isV && (
        <>
          {!!options.length && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {options.map((o) => (
                <button key={String(o.value)} type="button" className="btn-pill" style={{ padding: "0 16px" }} aria-disabled={busy || undefined}
                  onClick={() => run(() => onAnswer(`${o.label} [answer value: ${o.value}]`, o.label))}>{o.label}</button>
              ))}
            </div>
          )}
          <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{options.length ? "Or type your answer below." : "Type your answer below."}</span>
        </>
      )}
      <ErrorLine error={error} />
      {m.answered && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14 }}>
          <Icon name="check" size={18} style={{ color: "var(--ok)" }} />Answered{m.answer ? <>: <b style={{ fontWeight: 600 }}>{m.answer}</b></> : null}
        </div>
      )}
    </div>
  );
}

function ApprovalChatCard({ id }: { id: string }) {
  const { open, isA } = useApp();
  const { data: card, error } = useLive<any>(`/api/approvals/${id}`, 6000);
  if (!card) {
    return (
      <div style={{ ...cardBase, gap: 8, padding: 16, borderRadius: 16, border: "1.5px solid var(--text)", maxWidth: 480 }}>
        <span className="kicker" style={{ fontSize: 11.5 }}>Approval · live</span>
        {error ? <ErrorLine error={error} /> : <div className="skeleton" style={{ height: 60 }} />}
      </div>
    );
  }
  const pending = card.status === "PENDING" && !card.expired;
  const st = pending ? badge("READY_FOR_APPROVAL") : card.status === "PENDING" && card.expired ? badge("EXPIRED") : badge(card.payment_status);
  const sub: ReactNode = pending ? "Waiting for an Approver" : card.expired && card.status === "PENDING" ? "Expired. Nothing was sent." : card.decided_by ? `${label(card.status)} by ${card.decided_by}${card.decided_at ? ` at ${hm(card.decided_at)}` : ""}` : label(card.status);
  return (
    <div style={{ ...cardBase, gap: 12, padding: 16, borderRadius: 16, border: "1.5px solid var(--text)", maxWidth: 480, boxShadow: "var(--shadow-1)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span className="kicker" style={{ fontSize: 11.5, letterSpacing: ".06em" }}>Approval · live</span><Badge b={st} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontSize: 14, color: "var(--muted)" }}>{card.counterparty}{card.invoice && <> · <span className="mono" style={{ fontSize: 12.5 }}>{card.invoice}</span></>}</span>
        <span className="num wrap" style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.03em" }}>{card.amount}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13, color: "var(--muted)" }}>
        {card.decision && <Badge b={decisionBadge(card.decision.decision)} size="sm" />}{sub}
      </div>
      <button type="button" className="btn-pill btn-primary" style={{ minHeight: 46, fontSize: 14.5 }} onClick={() => open({ kind: "approval", id })}>
        {isA && pending ? "Review and decide" : "Open"}<Icon name="arrow_forward" size={18} />
      </button>
    </div>
  );
}

/** No Gemini key, or a rejected one: takes a key for the team's agent, then sends the message again. */
function KeyErrorMsg({ m, onRetry }: { m: Extract<Item, { t: "error" }>; onRetry: (text: string, shown: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    run(async () => {
      await api("/api/gemini-key", { method: "PUT", body: { api_key: value.trim() } });
      setValue("");
      setSaved(true);
      if (m.retry) await onRetry(m.retry, m.retry);
    });
  };
  return (
    <div role="alert" style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px", borderRadius: 14, background: "var(--neutral-bg)", border: "1px solid var(--neutral-bd)", fontSize: 14, lineHeight: 1.45, maxWidth: 560, marginLeft: 38 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <Icon name="key" size={20} style={{ color: "var(--muted)" }} />
        <span>{saved ? "Key added for this browser. It's used until you sign out or the app restarts." : m.message}</span>
      </div>
      {!saved && (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input className="input" type="password" autoComplete="off" spellCheck={false} aria-label="Gemini API key" placeholder="Paste your Gemini API key" value={value} onChange={(e) => setValue(e.target.value)} style={{ flex: "1 1 220px", minWidth: 0 }} />
            <button type="submit" className="btn-pill btn-primary" aria-disabled={busy || !value.trim()}>{busy ? "Adding…" : m.retry ? "Use key and retry" : "Use key"}</button>
          </div>
          <span style={{ fontSize: 12.5, color: "var(--muted)" }}>
            Get one from <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">Google AI Studio</a>. It's used only from this browser and kept in the server's memory, never saved. Signing out forgets it.
          </span>
          <ErrorLine error={error} />
        </form>
      )}
    </div>
  );
}

function ErrorMsg({ message }: { message: string }) {
  return (
    <div role="alert" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "12px 14px", borderRadius: 14, background: "var(--neutral-bg)", border: "1px solid var(--neutral-bd)", fontSize: 14, lineHeight: 1.45, maxWidth: 560, marginLeft: 38 }}>
      <Icon name="cloud_off" size={20} style={{ color: "var(--muted)" }} />
      <span>{message}</span>
    </div>
  );
}
