/* The API client. The server checks every permission. Role checks here only decide which buttons to show. */

export type Role = "APPROVER" | "OPERATOR" | "VIEWER";
export interface Team { id: string; name: string; role: Role; members?: number }
export interface User { id: string; name: string; email: string }
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

export function authHeaders(): Record<string, string> {
  return current ? { Authorization: `Bearer ${current.token}`, "X-Team-Id": current.team.id } : {};
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

export async function signIn(email: string): Promise<Session> {
  const result = await api<{ token: string; user: User; teams: Team[]; developer: boolean }>("/api/session", { method: "POST", body: { email }, auth: false });
  if (!result.teams.length) throw new ApiError("This user has no team. Developers use the Developer Console.", 0);
  return { token: result.token, user: result.user, team: result.teams[0], teams: result.teams, developer: result.developer };
}

export async function upload(file: File): Promise<{ id: string }> {
  const form = new FormData();
  form.append("file", file);
  return api("/api/documents", { method: "POST", form });
}

export const ACCEPT = ".pdf,.docx,.txt,.heic,.heif,image/*,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
