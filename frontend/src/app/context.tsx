import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onSignedOut, prefs, setSession, storage, type Session } from "../api";
import { refreshAll, useLive, useMedia } from "../hooks";

export type Section = "chat" | "approvals" | "payments" | "cash" | "expenses" | "audit";
export type PayTab = "payments" | "bills" | "contacts" | "automations";
export type RecKind = "approval" | "payment" | "bill" | "contact" | "dest" | "expense" | "notifications";
export interface Rec { kind: RecKind; id?: string }
export interface Opener { kind: string; id?: string | null }

const SECTIONS: Section[] = ["chat", "approvals", "payments", "cash", "expenses", "audit"];
const TABS: PayTab[] = ["payments", "bills", "contacts", "automations"];
const KINDS: RecKind[] = ["approval", "payment", "bill", "contact", "dest", "expense", "notifications"];

interface Nav { section: Section; payTab: PayTab; stack: Rec[] }

/** The address is "#/section[/tab][?open=kind:id,kind:id]", so a record can be linked and survives a reload. */
function readHash(): Nav {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const [s, t] = path.split("/");
  const section = (SECTIONS as string[]).includes(s) ? (s as Section) : "chat";
  const payTab = (TABS as string[]).includes(t) ? (t as PayTab) : "payments";
  const open = new URLSearchParams(query).get("open") || "";
  const stack = open.split(",").filter(Boolean).map((part) => {
    const [kind, id] = part.split(":");
    return { kind: kind as RecKind, id: id || undefined };
  }).filter((r) => KINDS.includes(r.kind));
  return { section, payTab, stack };
}
function writeHash(n: Nav) {
  const path = n.section === "payments" ? `payments/${n.payTab}` : n.section;
  const open = n.stack.map((r) => (r.id ? `${r.kind}:${r.id}` : r.kind)).join(",");
  const next = `#/${path}${open ? `?open=${open}` : ""}`;
  if (location.hash !== next) history.replaceState(null, "", next);
}

export interface Controls { payments_paused: boolean; paused_at: string | null; paused_by_name: string | null; auto_mode: boolean }
export interface NotificationItem { id: string; kind: string; label: string; title: string; at: string; unread: boolean; record: { kind: string; id: string; label?: string } | null }

export interface ChatRequest { n: number; send?: string; attach?: boolean; draft?: string; fresh?: boolean }

interface Ctx {
  session: Session;
  isA: boolean; isO: boolean; isV: boolean; canWrite: boolean;
  mobile: boolean; wide: boolean;
  nav: Nav;
  go: (s: Section) => void;
  setPayTab: (t: PayTab) => void;
  open: (o: Opener, nested?: boolean) => void;
  back: () => void;
  close: () => void;
  controls: Controls | undefined;
  pendingCount: number;
  notifications: { unread: number; items: NotificationItem[] } | undefined;
  reloadShell: () => void;
  signOut: () => void;
  switchTeam: (id: string) => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
  chatRequest: ChatRequest | null;
  requestChat: (r: Omit<ChatRequest, "n">) => void;
  doneChatRequest: (n: number) => void;
}

const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
};

export function useTheme() {
  const system = useMedia("(prefers-color-scheme: dark)");
  const [chosen, setChosen] = useState<string | null>(() => prefs.get("payo.theme"));
  const theme: "light" | "dark" = chosen === "dark" || chosen === "light" ? chosen : system ? "dark" : "light";
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const toggle = useCallback(() => {
    const next = theme === "light" ? "dark" : "light";
    prefs.set("payo.theme", next);
    setChosen(next);
  }, [theme]);
  return { theme, toggle };
}

export function AppProvider({ session, onSession, children }: { session: Session; onSession: (s: Session | null) => void; children: ReactNode }) {
  const mobile = !useMedia("(min-width: 760px)");
  const wide = useMedia("(min-width: 1180px)");
  const [nav, setNav] = useState<Nav>(readHash);
  const { theme, toggle } = useTheme();
  const [chatRequest, setChatRequest] = useState<ChatRequest | null>(null);

  useEffect(() => { writeHash(nav); }, [nav]);
  useEffect(() => {
    const onHash = () => setNav(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const signOut = useCallback(() => {
    setSession(null);
    onSession(null);
    history.replaceState(null, "", "#/");
  }, [onSession]);
  useEffect(() => { onSignedOut(signOut); }, [signOut]);

  const role = session.team.role;
  const isA = role === "APPROVER", isO = role === "OPERATOR", isV = role === "VIEWER";

  const controls = useLive<Controls>("/api/controls", 10000);
  const pending = useLive<any[]>("/api/approvals?status=PENDING", 10000);
  const notifications = useLive<{ unread: number; items: NotificationItem[] }>("/api/notifications", 15000);
  const pendingCount = (pending.data || []).filter((a) => !a.expired).length;

  const go = useCallback((section: Section) => setNav((n) => ({ ...n, section, stack: [] })), []);
  const setPayTab = useCallback((payTab: PayTab) => setNav((n) => ({ ...n, payTab, stack: [] })), []);
  const open = useCallback((o: Opener, nested?: boolean) => {
    if (o.kind === "cash" || o.kind === "account") return setNav((n) => ({ ...n, section: "cash", stack: [] }));
    if (!(KINDS as string[]).includes(o.kind)) return undefined;
    const item: Rec = { kind: o.kind as RecKind, id: o.id || undefined };
    return setNav((n) => ({ ...n, stack: mobile || nested ? [...n.stack, item] : [item] }));
  }, [mobile]);
  const back = useCallback(() => setNav((n) => ({ ...n, stack: n.stack.slice(0, -1) })), []);
  const close = useCallback(() => setNav((n) => ({ ...n, stack: [] })), []);

  const switchTeam = useCallback((id: string) => {
    const team = session.teams.find((t) => t.id === id);
    if (!team || team.id === session.team.id) return;
    const next = { ...session, team };
    setSession(next);
    onSession(next);
    setNav({ section: "chat", payTab: "payments", stack: [] });
  }, [session, onSession]);

  const requestChat = useCallback((r: Omit<ChatRequest, "n">) => {
    setNav((n) => ({ ...n, section: "chat", stack: [] }));
    setChatRequest({ ...r, n: Date.now() });
  }, []);
  const doneChatRequest = useCallback((n: number) => setChatRequest((c) => (c && c.n === n ? null : c)), []);

  const reloadShell = useCallback(() => { refreshAll(); }, []);

  const value = useMemo<Ctx>(() => ({
    session, isA, isO, isV, canWrite: isA || isO, mobile, wide, nav, go, setPayTab, open, back, close,
    controls: controls.data, pendingCount, notifications: notifications.data, reloadShell, signOut, switchTeam,
    theme, toggleTheme: toggle, chatRequest, requestChat, doneChatRequest,
  }), [session, isA, isO, isV, mobile, wide, nav, go, setPayTab, open, back, close, controls.data, pendingCount, notifications.data, reloadShell, signOut, switchTeam, theme, toggle, chatRequest, requestChat, doneChatRequest]);

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

/** Shell actions that change team controls. The server checks the role and records each one in Audit. */
export async function setPaused(paused: boolean) {
  await api("/api/controls/pause", { method: "POST", body: { paused } });
  refreshAll();
}

export const chatKey = (s: Session) => `payo.chat.${s.team.id}.${s.user.id}`;
export const rememberChat = (s: Session, id: string | null) => (id ? storage.set(chatKey(s), id) : storage.remove(chatKey(s)));
export const rememberedChat = (s: Session) => storage.get<string | null>(chatKey(s), null);
