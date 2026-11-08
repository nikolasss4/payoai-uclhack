import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ACCEPT, api, upload } from "../api";
import { rememberChat, rememberedChat, useApp } from "../app/context";
import { Messages } from "../chat/Messages";
import { useChat, type ChatRow, type Outgoing } from "../chat/useChat";
import { useDismiss, useLive } from "../hooks";
import { dayLabel, fileIcon, fileKind, fileSize } from "../lib";
import { ErrorLine, Icon } from "../ui";
import { MemoryList, useMemories } from "./Memory";

const PROMPTS = ["Pay this bill", "Can we cover next week's bills?", "Which expenses are missing receipts?", "Why was this payment blocked?"];

interface Attachment { name: string; size: number; media: string; id?: string; uploading: boolean; error?: string }

export function ChatScreen() {
  const app = useApp();
  const { session, mobile, chatRequest, doneChatRequest, canWrite } = app;
  const chats = useLive<ChatRow[]>("/api/chats", 20000);
  const [active, setActiveState] = useState<string | null>(() => rememberedChat(session));
  const setActive = useCallback((id: string | null) => { setActiveState(id); rememberChat(session, id); }, [session]);
  const chat = useChat(active);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [memOpen, setMemOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const memories = useMemories();

  // A chat from another team or user is not readable, so a remembered ID that fails is forgotten.
  useEffect(() => { if (chat.error && /not found/i.test(chat.error)) setActive(null); }, [chat.error, setActive]);

  const pickFile = useCallback(() => fileInput.current?.click(), []);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    const base: Attachment = { name: file.name, size: file.size, media: file.type, uploading: true };
    setAttachment(base);
    try {
      const doc = await upload(file);
      setAttachment({ ...base, id: doc.id, uploading: false });
    } catch (e) {
      setAttachment({ ...base, uploading: false, error: (e as Error).message });
    }
  };

  const send = useCallback(async (textArg?: string, shownArg?: string, forceNew = false) => {
    const text = (textArg ?? draft).trim();
    const att = textArg === undefined ? attachment : null;
    if (!text && !att) return;
    if (att && (att.uploading || !att.id)) return;
    setSendError(null);
    const out: Outgoing = {
      text: text || "Here's a document.", shown: shownArg ?? text,
      documentId: att?.id || null, file: att ? { name: att.name, meta: `${fileKind(att.name, att.media)} · ${fileSize(att.size)}`, media: att.media } : null,
    };
    if (textArg === undefined) { setDraft(""); setAttachment(null); }
    try {
      let id = forceNew ? null : active;
      const created = !id;
      if (!id) id = (await api<{ id: string }>("/api/chats", { method: "POST" })).id;
      await chat.send(id, out);
      if (created) setActive(id);
      chats.reload();
    } catch (e) {
      setSendError((e as Error).message);
      if (textArg === undefined) { setDraft(text); setAttachment(att); }
    }
  }, [draft, attachment, active, chat, setActive, chats]);

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

  const newChat = () => { setActive(null); setDraft(""); setAttachment(null); };
  const prompt = (t: string) => {
    if (t === "Pay this bill" && canWrite) { setDraft(t); pickFile(); } else send(t);
  };
  const title = (chats.data || []).find((c) => c.id === active)?.title || (active ? "Chat" : "New chat");
  const empty = !active ? !chat.items.length : chat.empty;

  const thread = (
    <>
      {empty && <EmptyChat mobile={mobile} name={session.user.name} onPrompt={prompt} />}
      <ErrorLine error={chat.error} />
      <Messages items={chat.items} typing={chat.typing} onAnswer={(text, shown) => send(text, shown)} onDecideStart={chat.decideStart} />
    </>
  );
  const composer = (
    <Composer mobile={mobile} draft={draft} setDraft={setDraft} attachment={attachment} clearAttachment={() => setAttachment(null)}
      canAttach={canWrite} onAttach={pickFile} onSend={() => send()} error={sendError} />
  );
  const input = <input ref={fileInput} type="file" accept={ACCEPT} hidden aria-hidden="true" tabIndex={-1} onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />;

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
      <aside style={{ flex: "none", width: 248, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 10, padding: "14px 12px", overflowY: "auto" }}>
        <button type="button" className="btn-pill" onClick={newChat} style={{ gap: 8 }}><Icon name="add" size={19} />New chat</button>
        <button type="button" className="hoverable" onClick={() => setMemOpen(true)}
          style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 44, padding: "0 12px", borderRadius: 12, fontSize: 14, fontWeight: 500, cursor: "pointer", background: memOpen ? "var(--raised)" : "transparent", border: 0, textAlign: "left" }}>
          <Icon name="psychology" size={20} />Memory<span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--muted)" }}>{memories.data?.length || ""}</span>
        </button>
        <span className="kicker" style={{ padding: "8px 8px 2px" }}>Past chats</span>
        {chats.loading && <div className="skeleton" style={{ height: 48 }} />}
        {chats.data && !chats.data.length && <span style={{ fontSize: 13, color: "var(--muted)", padding: "0 10px" }}>No chats yet.</span>}
        {(chats.data || []).map((c) => (
          <button key={c.id} type="button" className="hoverable" onClick={() => setActive(c.id)}
            style={{ display: "flex", flexDirection: "column", gap: 2, padding: "9px 10px", borderRadius: 12, cursor: "pointer", background: c.id === active ? "var(--raised)" : "transparent", border: 0, textAlign: "left" }}>
            <span style={{ fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", width: "100%" }}>{c.title || "Untitled chat"}</span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{dayLabel(c.last_at)}</span>
          </button>
        ))}
      </aside>
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

function MemoryPanel({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: "28px 0 0 0", background: "var(--scrim)", zIndex: 50, animation: "rise .15s" }} />
      <aside role="dialog" aria-label="Memory" style={{ position: "fixed", top: 28, right: 0, bottom: 0, width: 560, maxWidth: "100%", zIndex: 51, background: "var(--bg)", boxShadow: "var(--shadow-panel)", display: "flex", flexDirection: "column", animation: "panelIn .22s ease-out", borderLeft: "1px solid var(--border)" }}>
        <div style={{ flex: "none", height: 60, display: "flex", alignItems: "center", gap: 8, padding: "0 10px 0 12px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
          <span style={{ fontSize: 15, fontWeight: 600, paddingLeft: 6 }}>Memory</span>
          <button type="button" className="icon-btn" aria-label="Close" style={{ marginLeft: "auto" }} onClick={onClose}><Icon name="close" size={22} /></button>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 12 }}><MemoryList /></div>
      </aside>
    </>
  );
}

function EmptyChat({ mobile, name, onPrompt }: { mobile: boolean; name: string; onPrompt: (t: string) => void }) {
  if (mobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16, paddingTop: 24 }}>
        <span aria-hidden="true" style={{ width: 40, height: 40, borderRadius: 11, background: "var(--accent)", color: "#0B0B0C", display: "grid", placeItems: "center", fontWeight: 700, fontSize: 20 }}>P</span>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.025em" }}>Hello {name}.</span>
          <span style={{ fontSize: 14.5, color: "var(--muted)", lineHeight: 1.5 }}>I read invoices and run fixed checks. Every payment needs an Approver to tap a card.</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {PROMPTS.map((t) => <PromptBtn key={t} t={t} onClick={() => onPrompt(t)} mobile />)}
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22, paddingTop: "9vh", alignItems: "flex-start" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h1 className="display" style={{ margin: 0, fontSize: 34, fontWeight: 600, letterSpacing: "-0.03em" }}>Hello {name}.</h1>
        <p style={{ margin: 0, fontSize: 16, color: "var(--muted)", maxWidth: 560, lineHeight: 1.5 }}>I read invoices and run fixed checks written in code. Every payment needs an Approver to tap a card. I never approve anything.</p>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 10, width: "100%" }}>
        {PROMPTS.map((t) => <PromptBtn key={t} t={t} onClick={() => onPrompt(t)} />)}
      </div>
    </div>
  );
}

function PromptBtn({ t, onClick, mobile }: { t: string; onClick: () => void; mobile?: boolean }) {
  return (
    <button type="button" className="hover-border" onClick={onClick}
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, minHeight: mobile ? 52 : 56, padding: mobile ? "8px 14px" : "12px 16px", borderRadius: 14, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", fontSize: 14.5, boxShadow: mobile ? undefined : "var(--shadow-1)", textAlign: "left" }}>
      {t}<Icon name="arrow_forward" size={18} style={{ color: "var(--muted)" }} />
    </button>
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
              <span style={{ fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title || "Untitled chat"}</span>
              <span style={{ fontSize: 12, color: "var(--muted)", flex: "none" }}>{dayLabel(c.last_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Composer({ mobile, draft, setDraft, attachment, clearAttachment, canAttach, onAttach, onSend, error }: {
  mobile: boolean; draft: string; setDraft: (s: string) => void; attachment: Attachment | null; clearAttachment: () => void;
  canAttach: boolean; onAttach: () => void; onSend: () => void; error: string | null;
}) {
  const ready = !!(draft.trim() || (attachment && attachment.id)) && !attachment?.uploading;
  const sendStyle = { background: ready ? "var(--primary-bg)" : "var(--raised)", color: ready ? "var(--primary-fg)" : "var(--subtle)" };
  const chipMeta = attachment ? (attachment.uploading ? "Uploading…" : attachment.error ? attachment.error : `${fileKind(attachment.name, attachment.media)} · ${fileSize(attachment.size)} · attached`) : "";
  const box = (
    <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 6 : 8, padding: mobile ? 6 : 8, borderRadius: 20, background: "var(--surface)", border: "1px solid var(--border-strong)", boxShadow: mobile ? undefined : "var(--shadow-1)" }}>
      {attachment && (
        <div style={{ display: "flex", alignItems: "center", gap: mobile ? 8 : 10, padding: mobile ? "4px 4px 4px 10px" : "6px 6px 6px 10px", borderRadius: 12, background: "var(--raised)", alignSelf: mobile ? "stretch" : "flex-start", maxWidth: "100%", minWidth: 0 }}>
          <Icon name={attachment.uploading ? "progress_activity" : fileIcon(attachment.name, attachment.media)} size={mobile ? 18 : 20} className={attachment.uploading ? "spin" : ""} style={{ color: attachment.error ? "var(--danger)" : "var(--danger)" }} />
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: mobile ? 1 : undefined }}>
            <span className="wrap" style={{ fontSize: mobile ? 12.5 : 13, fontWeight: 500 }}>{attachment.name}</span>
            <span style={{ fontSize: 11.5, color: attachment.error ? "var(--danger)" : "var(--muted)" }}>{chipMeta}</span>
          </div>
          <button type="button" className="icon-btn" aria-label="Remove attachment" onClick={clearAttachment} style={{ width: mobile ? 36 : 32, height: mobile ? 36 : 32 }}><Icon name="close" size={18} /></button>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "flex-end", gap: mobile ? 6 : 8 }}>
        {canAttach && <button type="button" className="icon-btn" aria-label="Attach a document" onClick={onAttach} style={{ border: "1px solid var(--border)" }}><Icon name="add" size={22} /></button>}
        <textarea className="textarea-auto" value={draft} onChange={(e) => setDraft(e.target.value)} rows={1} placeholder="Ask PayoAI…" aria-label="Message"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
          style={{ flex: 1, minWidth: 0, minHeight: 44, maxHeight: mobile ? 120 : 160, resize: "none", border: "none", outline: "none", background: "transparent", color: "var(--text)", font: "inherit", fontSize: 15, lineHeight: 1.45, padding: mobile ? "11px 2px" : "11px 4px" }} />
        {mobile ? (
          <button type="button" aria-label="Send" aria-disabled={!ready || undefined} onClick={onSend} style={{ flex: "none", width: 44, height: 44, borderRadius: "50%", display: "grid", placeItems: "center", cursor: "pointer", border: 0, ...sendStyle }}><Icon name="arrow_upward" size={20} /></button>
        ) : (
          <button type="button" aria-label="Send" aria-disabled={!ready || undefined} onClick={onSend} style={{ flex: "none", height: 44, padding: "0 18px", borderRadius: 999, display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 14, fontWeight: 500, border: 0, ...sendStyle }}>Send<Icon name="arrow_upward" size={18} /></button>
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
