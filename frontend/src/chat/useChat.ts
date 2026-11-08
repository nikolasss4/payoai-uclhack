import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, authHeaders } from "../api";
import { refreshAll } from "../hooks";

export interface Part { type: "text" | "chip"; text?: string; kind?: string; id?: string; amount?: string | null; status?: string | null; label?: string | null; missing?: boolean }
export interface ChatEvent {
  id: number; kind: string; turn_id: string | null; run_id: string | null; payload: any; at: string; parts?: Part[];
  confirmation_status?: string | null; requested_by?: string | null; inputs_display?: [string, string][];
  question_status?: string; answer_label?: string | null; flagged?: { check_name: string; outcome: string; reason: string }[];
}
export interface Turn { id: string; seq: number; text: string; status: string; at: string; documents?: { id: string; filename: string; media_type: string }[] }
export interface ChatRow { id: string; title: string | null; created_at: string; last_at: string; last_message: string | null; last_from: "user" | "agent" | null }

export type Item =
  | { t: "user"; key: string; text: string; files: FileChip[]; sending?: boolean }
  | { t: "agent"; key: string; parts: Part[] }
  | { t: "steps"; key: string; runId: string | null; rows: string[]; running: boolean; finished?: any; flagged?: ChatEvent["flagged"]; at: string }
  | { t: "tasks"; key: string; tasks: { description: string; status: string }[] }
  | { t: "start"; key: string; ev: ChatEvent }
  | { t: "question"; key: string; ev: ChatEvent; answered: boolean; answer: string | null }
  | { t: "approval"; key: string; approvalId: string }
  | { t: "error"; key: string; message: string }
  | { t: "thinking"; key: string; turnId: string; lines: { text: string; source: string }[]; live: boolean; startedAt: string; endedAt: string }
  | { t: "draft"; key: string; text: string };

export interface FileChip { name: string; meta: string; media?: string }
export interface Outgoing { text: string; shown: string; documentIds: string[]; files: FileChip[] }

/** Reads a chat from its start, then follows new events. Cards re-read their records, so nothing shown is stale. */
export function useChat(chatId: string | null) {
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState<Outgoing | null>(null);
  const [awaitingRunAfter, setAwaitingRunAfter] = useState<number | null>(null);
  const [decided, setDecided] = useState<Record<string, string>>({});
  const last = useRef(0);

  const addEvents = useCallback((list: ChatEvent[]) => {
    if (!list.length) return;
    setEvents((prev) => {
      const seen = new Set(prev.map((e) => e.id));
      const fresh = list.filter((e) => !seen.has(e.id));
      if (!fresh.length) return prev;
      return [...prev, ...fresh].sort((a, b) => a.id - b.id);
    });
    last.current = Math.max(last.current, ...list.map((e) => e.id));
    // Approvals, bills and payments may have changed with these events.
    if (list.some((e) => e.kind === "APPROVAL_CARD" || e.kind === "REPLY" || e.payload?.finished)) refreshAll();
  }, []);

  const loadTurns = useCallback(async (id: string) => {
    const rows = await api<Turn[]>(`/api/chats/${id}/turns`);
    setTurns(rows);
    return rows;
  }, []);

  useEffect(() => {
    setEvents([]);
    setTurns([]);
    setError(null);
    setDecided({});
    setAwaitingRunAfter(null);
    last.current = 0;
    if (!chatId) return undefined;
    const controller = new AbortController();
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const [rows, evs] = await Promise.all([api<Turn[]>(`/api/chats/${chatId}/turns`, { signal: controller.signal }), api<ChatEvent[]>(`/api/chats/${chatId}/events`, { signal: controller.signal })]);
        if (!alive) return;
        setTurns(rows);
        addEvents(evs);
      } catch (e) {
        if (alive && (e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        if (alive) setLoading(false);
      }
      // The stream needs an Authorization header, which EventSource can't send, so it's read with fetch.
      let failures = 0;
      while (alive) {
        try {
          const response = await fetch(`/api/chats/${chatId}/stream?after=${last.current}`, { headers: authHeaders(), signal: controller.signal });
          if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
          failures = 0;
          const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += value;
            const blocks = buffer.split("\n\n");
            buffer = blocks.pop() || "";
            const parsed = blocks.map((b) => b.split("\n").find((l) => l.startsWith("data: "))).filter(Boolean).map((l) => JSON.parse((l as string).slice(6)) as ChatEvent);
            if (parsed.length && alive) addEvents(parsed);
          }
        } catch {
          if (!alive || controller.signal.aborted) return;
          failures += 1;
        }
        if (alive) await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** failures, 15000)));
      }
    })();
    return () => { alive = false; controller.abort(); };
  }, [chatId, addEvents]);

  const answeredTurns = useMemo(() => answered(events), [events]);
  const turnPending = turns.some((t) => t.status === "RUNNING" && !answeredTurns.has(t.id));
  const runStarting = awaitingRunAfter !== null && !events.some((e) => e.id > awaitingRunAfter);
  const typing = !!sending || turnPending || runStarting;

  // While a Turn is running, its status is re-read so a finished Turn stops the typing dots even without a reply event.
  useEffect(() => {
    if (!chatId || !turnPending) return undefined;
    const timer = setInterval(() => { loadTurns(chatId).catch(() => {}); }, 4000);
    return () => clearInterval(timer);
  }, [chatId, turnPending, loadTurns]);

  const send = useCallback(async (id: string, out: Outgoing) => {
    setSending(out);
    try {
      await api(`/api/chats/${id}/turns`, { method: "POST", body: { text: out.text, document_ids: out.documentIds } });
      await loadTurns(id);
    } finally {
      setSending(null);
    }
  }, [loadTurns]);

  const decideStart = useCallback(async (confirmationId: string, decision: "confirm" | "decline") => {
    const result = await api<{ status: string }>(`/api/confirmations/${confirmationId}/${decision}`, { method: "POST", body: {} });
    setDecided((d) => ({ ...d, [confirmationId]: result.status }));
    if (result.status === "CONFIRMED") setAwaitingRunAfter(last.current);
    return result.status;
  }, []);

  const items = useMemo(() => buildTimeline(turns, events, decided, sending, typing), [turns, events, decided, sending, typing]);
  return { items, typing, loading, error, send, decideStart, empty: !loading && !turns.length && !events.length && !sending };
}

function answered(events: ChatEvent[]) {
  return new Set(events.filter((e) => (e.kind === "REPLY" || e.kind === "ERROR") && e.turn_id).map((e) => e.turn_id as string));
}

function buildTimeline(turns: Turn[], events: ChatEvent[], decided: Record<string, string>, sending: Outgoing | null, typing: boolean): Item[] {
  type Entry = { at: number; order: number; id: number; turn?: Turn; ev?: ChatEvent };
  const time = (s: string) => new Date(s).getTime() || 0;
  const entries: Entry[] = [
    ...turns.map((t) => ({ at: time(t.at), order: 0, id: t.seq, turn: t })),
    ...events.map((e) => ({ at: time(e.at), order: 1, id: e.id, ev: e })),
  ].sort((a, b) => a.at - b.at || a.order - b.order || a.id - b.id);

  const out: Item[] = [];
  const done = answered(events);
  const drafts = new Map<string, string>();
  const lastSteps = new Map<string, Extract<Item, { t: "steps" }>>();
  for (const entry of entries) {
    if (entry.turn) {
      const t = entry.turn;
      const files = (t.documents || []).map((d) => ({ name: d.filename, meta: kindOf(d.media_type), media: d.media_type }));
      out.push({ t: "user", key: `t-${t.id}`, text: t.text, files });
      continue;
    }
    const e = entry.ev as ChatEvent;
    const p = e.payload || {};
    const runKey = e.run_id || e.turn_id || "chat";
    if (e.kind === "THINKING" && e.turn_id) {
      const prev = out[out.length - 1];
      if (prev && prev.t === "thinking" && prev.turnId === e.turn_id) {
        prev.lines.push({ text: p.text || "", source: p.source || "step" });
        prev.endedAt = e.at;
      } else {
        out.push({ t: "thinking", key: `th-${e.id}`, turnId: e.turn_id, lines: [{ text: p.text || "", source: p.source || "step" }], live: false, startedAt: e.at, endedAt: e.at });
      }
      continue;
    }
    if (e.kind === "REPLY_DRAFT" && e.turn_id) {
      drafts.set(e.turn_id, p.text || "");
      continue;
    }
    if (e.kind === "STATUS" && p.node) {
      const prev = out[out.length - 1];
      if (prev && prev.t === "steps" && prev.runId === (e.run_id || null) && !prev.finished) {
        prev.rows.push(p.node);
      } else {
        const block: Extract<Item, { t: "steps" }> = { t: "steps", key: `s-${e.id}`, runId: e.run_id || null, rows: [p.node], running: false, at: e.at };
        out.push(block);
        lastSteps.set(runKey, block);
      }
    } else if (e.kind === "STATUS" && p.finished) {
      const block = lastSteps.get(runKey);
      if (block && !block.finished) {
        block.finished = p;
        block.flagged = e.flagged;
      } else {
        out.push({ t: "steps", key: `s-${e.id}`, runId: e.run_id || null, rows: [], running: false, finished: p, flagged: e.flagged, at: e.at });
      }
    } else if (e.kind === "STATUS" && p.task_list) {
      out.push({ t: "tasks", key: `k-${e.id}`, tasks: p.task_list });
    } else if (e.kind === "QUESTION") {
      out.push({ t: "question", key: `q-${e.id}`, ev: e, answered: e.question_status === "ANSWERED" || e.question_status === "EXPIRED", answer: e.answer_label ?? null });
    } else if (e.kind === "START_CARD") {
      const status = decided[p.confirmation_id] || e.confirmation_status;
      out.push({ t: "start", key: `c-${e.id}`, ev: { ...e, confirmation_status: status } });
    } else if (e.kind === "APPROVAL_CARD" && p.approval_id) {
      out.push({ t: "approval", key: `a-${e.id}`, approvalId: p.approval_id });
    } else if (e.kind === "REPLY") {
      out.push({ t: "agent", key: `r-${e.id}`, parts: e.parts || [] });
    } else if (e.kind === "SUGGESTION" && p.text) {
      out.push({ t: "agent", key: `g-${e.id}`, parts: [{ type: "text", text: p.text }] });
    } else if (e.kind === "ERROR") {
      out.push({ t: "error", key: `e-${e.id}`, message: p.message || "Something went wrong with this request." });
    }
  }
  // A question counts as answered once its run moves on, or once the person replies after it.
  out.forEach((item, i) => {
    if (item.t !== "question" || item.answered) return;
    const later = out.slice(i + 1);
    const reply = later.find((x) => x.t === "user") as Extract<Item, { t: "user" }> | undefined;
    const moved = events.some((e) => e.id > item.ev.id && e.run_id && e.run_id === item.ev.run_id);
    if (reply || moved) {
      item.answered = true;
      item.answer = item.answer ?? reply?.text ?? null;
    }
  });
  // A thinking block is live only while its Turn has no reply and nothing has come after it.
  const tailThinking = out[out.length - 1];
  if (tailThinking && tailThinking.t === "thinking" && !done.has(tailThinking.turnId) && typing) tailThinking.live = true;
  // Only the newest block can still be running, and only while something is in progress.
  const tail = out[out.length - 1];
  if (tail && tail.t === "steps" && !tail.finished && (typing || Date.now() - time(tail.at) < 120000)) tail.running = true;
  // A draft is shown until its Turn's checked reply arrives, which replaces it.
  for (const [turnId, text] of drafts) {
    if (!done.has(turnId) && text.trim()) out.push({ t: "draft", key: `d-${turnId}`, text });
  }
  if (sending) out.push({ t: "user", key: "sending", text: sending.shown, files: sending.files, sending: true });
  return out;
}

const kindOf = (media?: string) => (media?.includes("pdf") ? "PDF" : media?.startsWith("image/") ? "Image" : media?.includes("wordprocessingml") ? "Word" : media?.startsWith("text/") ? "Text" : "Document");
