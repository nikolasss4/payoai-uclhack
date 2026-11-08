/* The API client. The server checks every permission. Role checks here only decide which buttons to show. */

export type Role = "APPROVER" | "OPERATOR" | "VIEWER";
export interface Team { id: string; name: string; role: Role; members?: number; avatar_url?: string | null }
export interface User { id: string; name: string; email: string; avatar_url?: string | null }
export interface Session { token: string; user: User; team: Team; teams: Team[]; developer: boolean }

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const KEY = "payo.session.v2";

export const storage = {
  get<T>(key: string, fallback: T): T {
    try {
      const raw = sessionStorage.getItem(key);
      return raw === null ? fallback : (JSON.parse(raw) as T);
    } catch {
      return fallback;
    }
  },
  set(key: string, value: unknown) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Works without storage, it just forgets on reload. */ }
  },
  remove(key: string) {
    try { sessionStorage.removeItem(key); } catch { /* Nothing to remove. */ }
  },
};

export const prefs = {
  get(key: string): string | null {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch { /* A preference that isn't saved is fine. */ }
  },
};

export const loadSession = () => storage.get<Session | null>(KEY, null);
export const saveSession = (s: Session | null) => (s ? storage.set(KEY, s) : storage.remove(KEY));

let current: Session | null = loadSession();
let onUnauthorised: (() => void) | null = null;
export function setSession(s: Session | null) {
  current = s;
  saveSession(s);
}
export function onSignedOut(fn: () => void) {
  onUnauthorised = fn;
}

/* This browser's own random secret. A Gemini key pasted here is filed under it in the agent's memory,
   so only requests carrying it can use that key. It's never the key itself, and it's sent with every request. */
const KEY_SESSION = "payo.key-session.v1";
let keySecret: string | null = null;
function keySession(): string {
  if (keySecret) return keySecret;
  try { keySecret = localStorage.getItem(KEY_SESSION); } catch { keySecret = null; }
  if (!keySecret) {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    keySecret = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem(KEY_SESSION, keySecret); } catch { /* Without storage it lasts until reload. */ }
  }
  return keySecret;
}

/** On sign-out the agent forgets this browser's key, and the browser starts a new secret. */
export function forgetGeminiKey() {
  const secret = keySecret || (() => { try { return localStorage.getItem(KEY_SESSION); } catch { return null; } })();
  if (secret) fetch("/api/gemini-key", { method: "DELETE", headers: { "X-Key-Session": secret }, keepalive: true }).catch(() => {});
  keySecret = null;
  try { localStorage.removeItem(KEY_SESSION); } catch { /* Nothing to remove. */ }
}

export function authHeaders(): Record<string, string> {
  return current ? { Authorization: `Bearer ${current.token}`, "X-Team-Id": current.team.id, "X-Key-Session": keySession() } : {};
}

interface Opts { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal; auth?: boolean }

export async function api<T = any>(path: string, { method = "GET", body, form, signal, auth = true }: Opts = {}): Promise<T> {
  const headers: Record<string, string> = auth ? authHeaders() : {};
  const init: RequestInit = { method, signal, headers };
  if (form) init.body = form;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!response.ok) {
    if (response.status === 401 && auth && onUnauthorised) onUnauthorised();
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail : `That didn't work (error ${response.status}).`;
    throw new ApiError(message, response.status);
  }
  return data as T;
}

/** Fetches a file with the session's headers and hands it to the browser as a download. */
export async function download(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, { headers: authHeaders() });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  if (!response.ok) {
    if (response.status === 401 && onUnauthorised) onUnauthorised();
    let message = `That didn't work (error ${response.status}).`;
    try {
      const detail = (await response.json())?.detail;
      if (typeof detail === "string") message = detail;
    } catch { /* Keep the generic message. */ }
    throw new ApiError(message, response.status);
  }
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function signIn(email: string): Promise<Session> {
  const result = await api<{ token: string; user: User; teams: Team[]; developer: boolean }>("/api/session", { method: "POST", body: { email }, auth: false });
  if (!result.teams.length) throw new ApiError("This user has no team. Developers use the Developer Console.", 0);
  return { token: result.token, user: result.user, team: result.teams[0], teams: result.teams, developer: result.developer };
}

interface Profile { user: User; teams: Team[]; developer: boolean }

/** Re-reads the person's profile, so a picture or team changed elsewhere shows without signing in again. */
export async function refreshProfile(s: Session): Promise<Session> {
  const p = await api<Profile>("/api/me");
  const team = p.teams.find((t) => t.id === s.team.id) || s.team;
  return { ...s, user: p.user, teams: p.teams, team, developer: p.developer };
}

export type PictureOwner = "me" | "team";
export const PICTURE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

/** Sets or removes the picture for the signed-in person or the current organisation. Returns its new URL. */
export async function setPicture(owner: PictureOwner, file: File | null): Promise<string | null> {
  const path = `/api/${owner}/avatar`;
  if (!file) return (await api<{ avatar_url: null }>(path, { method: "DELETE" })).avatar_url;
  const form = new FormData();
  form.append("file", file);
  return (await api<{ avatar_url: string }>(path, { method: "PUT", form })).avatar_url;
}

export async function upload(file: File): Promise<{ id: string }> {
  const form = new FormData();
  form.append("file", file);
  return api("/api/documents", { method: "POST", form });
}

export const ACCEPT = ".pdf,.docx,.txt,.heic,.heif,image/*,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
