import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ACCEPT, api, prefs, upload } from "../api";
import { rememberChat, rememberedChat, useApp } from "../app/context";
import { Messages } from "../chat/Messages";
import { useChat, type ChatRow, type Outgoing } from "../chat/useChat";
import { useDismiss, useLive } from "../hooks";
import { dayLabel, fileIcon, fileKind, fileSize } from "../lib";
import { ErrorLine, Icon } from "../ui";
import { MemoryList, useMemories } from "./Memory";

const PROMPTS = ["Pay this bill", "Can we cover next week's bills?", "Which expenses are missing receipts?", "Why was this payment blocked?"];

interface Attachment { key: number; name: string; size: number; media: string; id?: string; uploading: boolean; error?: string }

// Matches the API's limit on documents in one message.
const MAX_ATTACHMENTS = 10;
let nextAttachmentKey = 0;

export function ChatScreen() {
  const app = useApp();
  const { session, mobile, chatRequest, doneChatRequest, canWrite } = app;
  const chats = useLive<ChatRow[]>("/api/chats", 20000);
  const [active, setActiveState] = useState<string | null>(() => rememberedChat(session));
  const setActive = useCallback((id: string | null) => { setActiveState(id); rememberChat(session, id); }, [session]);
  const chat = useChat(active);
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [memOpen, setMemOpen] = useState(false);
  // The web sidebar folds to a rail of icons. Each viewer's choice is remembered on this device.
  const [sideOpen, setSideOpenState] = useState(() => prefs.get("payo.chatSidebar") === "open");
  const setSideOpen = (open: boolean) => { setSideOpenState(open); prefs.set("payo.chatSidebar", open ? "open" : "closed"); };
  const fileInput = useRef<HTMLInputElement | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const memories = useMemories();

  // A chat from another team or user is not readable, so a remembered ID that fails is forgotten.
  useEffect(() => { if (chat.error && /not found/i.test(chat.error)) setActive(null); }, [chat.error, setActive]);

  const pickFile = useCallback(() => fileInput.current?.click(), []);

  // Each file uploads on its own, so one bad file doesn't hold up the rest.
  const onFiles = (list: FileList | null) => {
    const files = Array.from(list || []).slice(0, Math.max(0, MAX_ATTACHMENTS - attachments.length));
    if (!files.length) return;
    const added: Attachment[] = files.map((file) => ({ key: nextAttachmentKey++, name: file.name, size: file.size, media: file.type, uploading: true }));
    const update = (key: number, change: Partial<Attachment>) => setAttachments((all) => all.map((a) => (a.key === key ? { ...a, ...change } : a)));
    setAttachments((all) => [...all, ...added]);
    files.forEach((file, i) => {
      upload(file).then(
        (doc) => update(added[i].key, { id: doc.id, uploading: false }),
        (e) => update(added[i].key, { uploading: false, error: (e as Error).message }),
      );
    });
  };

  const send = useCallback(async (textArg?: string, shownArg?: string, forceNew = false) => {
    const text = (textArg ?? draft).trim();
    const atts = textArg === undefined ? attachments : [];
    if (atts.some((a) => a.uploading)) return;
    const ready = atts.filter((a) => a.id);
    if (!text && !ready.length) return;
    setSendError(null);
    const out: Outgoing = {
      text: text || (ready.length > 1 ? `Here are ${ready.length} documents.` : "Here's a document."), shown: shownArg ?? text,
      documentIds: ready.map((a) => a.id as string), files: ready.map((a) => ({ name: a.name, meta: `${fileKind(a.name, a.media)} · ${fileSize(a.size)}`, media: a.media })),
    };
    if (textArg === undefined) { setDraft(""); setAttachments([]); }
    try {
      let id = forceNew ? null : active;
      const created = !id;
      if (!id) id = (await api<{ id: string }>("/api/chats", { method: "POST" })).id;
      await chat.send(id, out);
      if (created) setActive(id);
      chats.reload();
    } catch (e) {
      setSendError((e as Error).message);
      if (textArg === undefined) { setDraft(text); setAttachments(atts); }
    }
  }, [draft, attachments, active, chat, setActive, chats]);

  // Requests from other screens: "Upload a bill", "Pay this contact".
  const handled = useRef(0);
  useEffect(() => {
    if (!chatRequest || handled.current === chatRequest.n) return;
    const r = chatRequest;
    handled.current = r.n;
    doneChatRequest(r.n);
    if (r.fresh) setActive(null);
    if (r.draft !== undefined) setDraft(r.draft);
    if (r.attach && canWrite) setTimeout(pickFile, 0);
    if (r.send) send(r.send, undefined, !!r.fresh);
  }, [chatRequest, doneChatRequest, setActive, pickFile, send, canWrite]);

  // Follows the conversation, unless the person has scrolled up to read.
  const nearBottom = useRef(true);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && nearBottom.current) el.scrollTop = el.scrollHeight;
  }, [chat.items.length, chat.typing, active]);
  const onScroll = () => {
    const el = scroller.current;
    if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 240;
  };

  const newChat = () => { setActive(null); setDraft(""); setAttachments([]); };
  const prompt = (t: string) => {
    if (t === "Pay this bill" && canWrite) { setDraft(t); pickFile(); } else send(t);
  };
  const title = (chats.data || []).find((c) => c.id === active)?.title || (active ? "Chat" : "New chat");
  const empty = !active ? !chat.items.length : chat.empty;

  const thread = (
    <>
      {empty && <EmptyChat mobile={mobile} onPrompt={prompt} />}
      <ErrorLine error={chat.error} />
      <Messages items={chat.items} typing={chat.typing} onAnswer={(text, shown) => send(text, shown)} onDecideStart={chat.decideStart} />
    </>
  );
  const composer = (
    <Composer mobile={mobile} draft={draft} setDraft={setDraft} attachments={attachments} removeAttachment={(key) => setAttachments((all) => all.filter((a) => a.key !== key))}
      canAttach={canWrite && attachments.length < MAX_ATTACHMENTS} onAttach={pickFile} onSend={() => send()} error={sendError} />
  );
  const input = <input ref={fileInput} type="file" accept={ACCEPT} multiple hidden aria-hidden="true" tabIndex={-1} onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />;

  if (mobile) {
    return (
      <div data-screen-label="M Chat" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", position: "relative" }}>
        {input}
        {memOpen && (
          <>
            <div onClick={() => setMemOpen(false)} style={{ position: "absolute", inset: 0, zIndex: 35, background: "rgba(11,11,12,.35)" }} />
            <aside aria-label="Memory" style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: "86%", maxWidth: 340, zIndex: 36, background: "var(--bg)", borderRight: "1px solid var(--border)", boxShadow: "12px 0 32px rgba(11,11,12,.18)", display: "flex", flexDirection: "column" }}>
              <div style={{ flex: "none", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 6px 6px 16px", borderBottom: "1px solid var(--border)", minHeight: 56 }}>
                <h2 className="display" style={{ margin: 0, fontSize: 18, fontWeight: 600, letterSpacing: "-0.02em" }}>Memory</h2>
                <button type="button" className="icon-btn" aria-label="Close memory" onClick={() => setMemOpen(false)}><Icon name="close" size={22} /></button>
              </div>
              <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10 }}><MemoryList mobile /></div>
            </aside>
          </>
        )}
        <MobileChatBar title={title} chats={chats.data || []} active={active} onPick={setActive} onNew={newChat} onMemory={() => setMemOpen(true)} />
        <div ref={scroller} onScroll={onScroll} style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 14px" }}>{thread}</div>
        {composer}
      </div>
    );
  }

  return (
    <div data-screen-label="Agent" style={{ height: "100%", display: "flex" }}>
      {input}
      {sideOpen ? (
        <aside aria-label="Chats" style={{ flex: "none", width: 300, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 12, padding: "16px 14px", overflowY: "auto", animation: "rise .18s" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button type="button" className="press" onClick={newChat} style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 8, minHeight: 48, borderRadius: 999, background: "var(--accent)", color: "#0B0B0C", fontSize: 16, fontWeight: 600, cursor: "pointer", border: 0 }}><Icon name="add" size={22} />New chat</button>
            <RailBtn icon="left_panel_close" label="Close sidebar" onClick={() => setSideOpen(false)} />
          </div>
          <button type="button" className="hoverable" onClick={() => setMemOpen(true)}
            style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 50, padding: "0 14px", borderRadius: 14, fontSize: 16, fontWeight: 500, cursor: "pointer", background: memOpen ? "var(--raised)" : "transparent", border: 0, textAlign: "left", color: "var(--text)" }}>
            <Icon name="psychology" size={23} />Memory<span style={{ marginLeft: "auto", fontSize: 14, color: "var(--muted)" }}>{memories.data?.length || ""}</span>
          </button>
          <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)", padding: "10px 10px 2px" }}>Past chats</span>
          {chats.loading && <div className="skeleton" style={{ height: 56, borderRadius: 14 }} />}
          {chats.data && !chats.data.length && <span style={{ fontSize: 14, color: "var(--muted)", padding: "0 12px" }}>No chats yet.</span>}
          {(chats.data || []).map((c) => (
            <button key={c.id} type="button" className="hoverable" onClick={() => setActive(c.id)} aria-current={c.id === active ? "true" : undefined}
              style={{ display: "flex", flexDirection: "column", gap: 3, padding: "11px 12px", borderRadius: 14, cursor: "pointer", background: c.id === active ? "var(--raised)" : "transparent", border: 0, textAlign: "left", color: "var(--text)" }}>
              <span style={{ width: "100%", fontSize: 16, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title || "Untitled chat"}</span>
              <span style={{ width: "100%", fontSize: 13.5, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {dayLabel(c.last_at)}{c.last_message ? ` · ${c.last_from === "user" ? "You: " : ""}${c.last_message}` : ""}
              </span>
            </button>
          ))}
        </aside>
      ) : (
        <aside aria-label="Chats" style={{ flex: "none", width: 72, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", alignItems: "center", gap: 10, padding: "16px 0" }}>
          <RailBtn icon="left_panel_open" label="Open sidebar" onClick={() => setSideOpen(true)} />
          <button type="button" className="press" aria-label="New chat" title="New chat" onClick={newChat} style={{ width: 48, height: 48, borderRadius: "50%", display: "grid", placeItems: "center", cursor: "pointer", background: "var(--accent)", color: "#0B0B0C", border: 0 }}><Icon name="add" size={24} /></button>
          <RailBtn icon="psychology" label="Memory" onClick={() => setMemOpen(true)} active={memOpen} />
          <RailBtn icon="history" label="Past chats" onClick={() => setSideOpen(true)} />
        </aside>
      )}
      <section style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div ref={scroller} onScroll={onScroll} style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "28px 24px" }}>
          <div style={{ maxWidth: 780, margin: "0 auto", display: "flex", flexDirection: "column", gap: 16 }}>{thread}</div>
        </div>
        {composer}
      </section>
      {memOpen && <MemoryPanel onClose={() => setMemOpen(false)} />}
    </div>
  );
}

function RailBtn({ icon, label, onClick, active }: { icon: string; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button type="button" className="hoverable" aria-label={label} title={label} onClick={onClick}
      style={{ flex: "none", width: 48, height: 48, borderRadius: 14, display: "grid", placeItems: "center", cursor: "pointer", border: 0, background: active ? "var(--raised)" : "transparent", color: "var(--text)" }}>
      <Icon name={icon} size={24} />
    </button>
  );
}

function MemoryPanel({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "var(--scrim)", zIndex: 50, animation: "rise .15s" }} />
      <aside role="dialog" aria-label="Memory" style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 560, maxWidth: "100%", zIndex: 51, background: "var(--bg)", boxShadow: "var(--shadow-panel)", display: "flex", flexDirection: "column", animation: "panelIn .22s ease-out", borderLeft: "1px solid var(--border)" }}>
        <div style={{ flex: "none", height: 60, display: "flex", alignItems: "center", gap: 8, padding: "0 10px 0 12px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
          <span style={{ fontSize: 15, fontWeight: 600, paddingLeft: 6 }}>Memory</span>
          <button type="button" className="icon-btn" aria-label="Close" style={{ marginLeft: "auto" }} onClick={onClose}><Icon name="close" size={22} /></button>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 12 }}><MemoryList /></div>
      </aside>
    </>
  );
}

// Each suggestion's icon and colours, in the order of PROMPTS.
const PROMPT_LOOK: [string, string, string][] = [["receipt_long", "var(--accent)", "#0B0B0C"], ["account_balance", "#0B0B0C", "var(--accent)"], ["shield", "var(--raised)", "var(--text)"], ["schedule", "var(--raised)", "var(--text)"]];

function ReadyChip({ mobile }: { mobile: boolean }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, alignSelf: "flex-start", height: mobile ? 30 : 34, padding: mobile ? "0 12px 0 9px" : "0 14px 0 10px", borderRadius: 999, background: "#0B0B0C", color: "#F4F4F2", fontSize: mobile ? 13 : 14, fontWeight: 500 }}>
      <span style={{ width: mobile ? 9 : 10, height: mobile ? 9 : 10, borderRadius: "50%", background: "#D4F53C", boxShadow: "0 0 0 4px rgba(212,245,60,.25)" }} />Your AI Agent is ready.
    </span>
  );
}

function EmptyChat({ mobile, onPrompt }: { mobile: boolean; onPrompt: (t: string) => void }) {
  const headline = (
    <h1 className="display" style={{ margin: 0, fontSize: mobile ? 30 : 52, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: mobile ? 1.15 : 1.1, textWrap: "balance" }}>
      Drop your bills. Pay suppliers. Run cash analysis.{" "}
      <span style={{ background: "#D4F53C", color: "#0B0B0C", padding: mobile ? "0 8px" : "0 12px", borderRadius: mobile ? 10 : 14, boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone" }}>Whatever you need, just ask…</span>
    </h1>
  );
  if (mobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16, paddingTop: 24 }}>
        <ReadyChip mobile />
        {headline}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {PROMPTS.slice(0, 3).map((t) => (
            <button key={t} type="button" onClick={() => onPrompt(t)}
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, minHeight: 52, padding: "8px 14px", borderRadius: 14, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", fontSize: 14.5, textAlign: "left", color: "var(--text)" }}>
              {t}<Icon name="arrow_forward" size={18} style={{ color: "var(--muted)" }} />
            </button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32, paddingTop: "7vh", alignItems: "flex-start" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}><ReadyChip mobile={false} />{headline}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 12, width: "100%" }}>
        {PROMPTS.map((t, i) => {
          const [icon, bg, fg] = PROMPT_LOOK[i % PROMPT_LOOK.length];
          return (
            <button key={t} type="button" className="lift" onClick={() => onPrompt(t)}
              style={{ display: "flex", flexDirection: "column", gap: 18, minHeight: 132, padding: "18px 20px", borderRadius: 22, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", boxShadow: "var(--shadow-1)", textAlign: "left", color: "var(--text)" }}>
              <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                <span style={{ width: 44, height: 44, borderRadius: 14, display: "grid", placeItems: "center", background: bg, color: fg }}><Icon name={icon} size={24} /></span>
                <Icon name="north_east" size={22} style={{ color: "var(--muted)" }} />
              </span>
              <span style={{ fontSize: 17, fontWeight: 500, lineHeight: 1.35, textWrap: "pretty" }}>{t}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MobileChatBar({ title, chats, active, onPick, onNew, onMemory }: { title: string; chats: ChatRow[]; active: string | null; onPick: (id: string) => void; onNew: () => void; onMemory: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  return (
    <div ref={ref} style={{ flex: "none", display: "flex", alignItems: "center", gap: 6, padding: "6px 8px 6px 4px", borderBottom: "1px solid var(--border)", position: "relative" }}>
      <button type="button" className="icon-btn" aria-label="Open memory" onClick={onMemory}><Icon name="psychology" size={22} /></button>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 4, minHeight: 44, cursor: "pointer", background: "transparent", border: 0, padding: 0, textAlign: "left" }}>
        <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{title}</span>
        <Icon name="expand_more" size={20} style={{ color: "var(--muted)" }} />
      </button>
      <button type="button" className="btn-pill" onClick={onNew} style={{ height: 44, padding: "0 12px", fontSize: 13.5, gap: 4 }}><Icon name="add" size={18} />New chat</button>
      {open && (
        <div className="menu" style={{ position: "absolute", left: 8, right: 8, top: 54, zIndex: 30, boxShadow: "0 12px 32px rgba(11,11,12,.18)", maxHeight: 360, overflowY: "auto" }}>
          {!chats.length && <div style={{ padding: "10px 12px", fontSize: 13.5, color: "var(--muted)" }}>No past chats yet.</div>}
          {chats.map((c) => (
            <button key={c.id} type="button" onClick={() => { onPick(c.id); setOpen(false); }}
              style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", minHeight: 44, padding: "4px 10px", borderRadius: 10, cursor: "pointer", background: c.id === active ? "var(--raised)" : "transparent", border: 0, width: "100%", textAlign: "left" }}>
              <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                <span style={{ fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title || "Untitled chat"}</span>
                {c.last_message && <span style={{ fontSize: 12.5, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.last_from === "user" ? "You: " : ""}{c.last_message}</span>}
              </span>
              <span style={{ fontSize: 12, color: "var(--muted)", flex: "none" }}>{dayLabel(c.last_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Composer({ mobile, draft, setDraft, attachments, removeAttachment, canAttach, onAttach, onSend, error }: {
  mobile: boolean; draft: string; setDraft: (s: string) => void; attachments: Attachment[]; removeAttachment: (key: number) => void;
  canAttach: boolean; onAttach: () => void; onSend: () => void; error: string | null;
}) {
  const ready = !!(draft.trim() || attachments.some((a) => a.id)) && !attachments.some((a) => a.uploading);
  const sendStyle = { background: ready ? "var(--primary-bg)" : "var(--raised)", color: ready ? "var(--primary-fg)" : "var(--subtle)" };
  const chipMeta = (a: Attachment) => (a.uploading ? "Uploading…" : a.error ? a.error : `${fileKind(a.name, a.media)} · ${fileSize(a.size)} · attached`);
  const box = (
    <div className={mobile ? undefined : "composer"} style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 8, padding: mobile ? 6 : 10, borderRadius: mobile ? 20 : 26, background: "var(--surface)", border: `${mobile ? 1 : 1.5}px solid var(--border-strong)`, boxShadow: mobile ? undefined : "0 10px 30px rgba(11,11,12,.08)" }}>
      {attachments.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: mobile ? 6 : 8, flexDirection: mobile ? "column" : "row" }}>
          {attachments.map((a) => (
            <div key={a.key} style={{ display: "flex", alignItems: "center", gap: mobile ? 8 : 10, padding: mobile ? "4px 4px 4px 10px" : "6px 6px 6px 10px", borderRadius: 12, background: "var(--raised)", alignSelf: mobile ? "stretch" : "flex-start", maxWidth: "100%", minWidth: 0 }}>
              <Icon name={a.uploading ? "progress_activity" : fileIcon(a.name, a.media)} size={mobile ? 18 : 20} className={a.uploading ? "spin" : ""} style={{ color: "var(--danger)" }} />
              <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: mobile ? 1 : undefined }}>
                <span className="wrap" style={{ fontSize: mobile ? 12.5 : 13, fontWeight: 500 }}>{a.name}</span>
                <span style={{ fontSize: 11.5, color: a.error ? "var(--danger)" : "var(--muted)" }}>{chipMeta(a)}</span>
              </div>
              <button type="button" className="icon-btn" aria-label={`Remove ${a.name}`} onClick={() => removeAttachment(a.key)} style={{ width: mobile ? 36 : 32, height: mobile ? 36 : 32 }}><Icon name="close" size={18} /></button>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "flex", alignItems: "flex-end", gap: mobile ? 6 : 8 }}>
        {canAttach && <button type="button" className="icon-btn" aria-label="Attach a document" onClick={onAttach} style={{ border: "1px solid var(--border)" }}><Icon name="add" size={22} /></button>}
        <textarea className="textarea-auto" value={draft} onChange={(e) => setDraft(e.target.value)} rows={1} placeholder="Ask PayoAI…" aria-label="Message"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
          style={{ flex: 1, minWidth: 0, minHeight: 44, maxHeight: mobile ? 120 : 160, resize: "none", border: "none", outline: "none", background: "transparent", color: "var(--text)", font: "inherit", fontSize: mobile ? 15 : 17, lineHeight: 1.45, padding: mobile ? "11px 2px" : "11px 4px" }} />
        {mobile ? (
          <button type="button" aria-label="Send" aria-disabled={!ready || undefined} onClick={onSend} style={{ flex: "none", width: 44, height: 44, borderRadius: "50%", display: "grid", placeItems: "center", cursor: "pointer", border: 0, ...sendStyle }}><Icon name="arrow_upward" size={20} /></button>
        ) : (
          <button type="button" aria-label="Send" aria-disabled={!ready || undefined} onClick={onSend} style={{ flex: "none", height: 44, padding: "0 18px", borderRadius: 999, display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 16, fontWeight: 500, border: 0, ...sendStyle }}>Send<Icon name="arrow_upward" size={18} /></button>
        )}
      </div>
    </div>
  );
  return (
    <div style={{ flex: "none", padding: mobile ? "8px 10px 10px" : "0 24px 18px" }}>
      <div style={{ maxWidth: mobile ? undefined : 780, margin: "0 auto", display: "flex", flexDirection: "column", gap: mobile ? 6 : 8 }}>
        <ErrorLine error={error} />
        {box}
      </div>
    </div>
  );
}
