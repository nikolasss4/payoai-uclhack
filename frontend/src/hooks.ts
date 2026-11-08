import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "./api";

/* Data changes elsewhere (a worker, another person), so screens re-read on a timer and after any action here. */

let version = 0;
const listeners = new Set<() => void>();
/** Call after a change, so every live view re-reads now instead of on its next tick. */
export function refreshAll() {
  version += 1;
  listeners.forEach((fn) => fn());
}
function useVersion() {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => listeners.delete(fn); }, () => version);
}

export interface Live<T> { data: T | undefined; error: string | null; loading: boolean; reload: () => Promise<void> }

/** Reads `path`, then again every `ms` while the tab is visible. A failed re-read keeps the last good data. */
export function useLive<T = any>(path: string | null, ms = 0): Live<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const v = useVersion();
  const current = useRef(path);
  current.current = path;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (current.current !== path) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (current.current === path) setError((e as Error).message);
    } finally {
      if (current.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(undefined);
    setError(null);
    setLoading(!!path);
  }, [path]);

  useEffect(() => {
    load();
  }, [load, v]);

  useEffect(() => {
    if (!path || !ms) return undefined;
    const timer = setInterval(() => { if (!document.hidden) load(); }, ms);
    const onVisible = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [path, ms, load]);

  return { data, error, loading: loading && data === undefined, reload: load };
}

export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (fn) => { const m = window.matchMedia(query); m.addEventListener("change", fn); return () => m.removeEventListener("change", fn); },
    () => window.matchMedia(query).matches,
  );
}

/** Closes a menu on a click outside it or on Escape. */
export function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open, close]);
  return ref;
}

/** Runs an action once at a time and keeps its error message. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    if (busy) return undefined;
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [busy]);
  return { busy, error, setError, run };
}

/** A second tap within five seconds confirms. Used for actions that are hard to take back. */
export function useArmed(ms = 5000) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), ms);
    return () => clearTimeout(t);
  }, [armed, ms]);
  return { armed, arm: () => setArmed(true), disarm: () => setArmed(false) };
}

/**
 * Keeps the last open value mounted for `ms` after it closes, so an overlay can play its exit.
 * Returns what to render and whether it is on its way out.
 */
export function useExit<T>(value: T | null | undefined | false, ms = 150): [T | null, boolean] {
  const [kept, setKept] = useState<T | null>(value || null);
  const open = !!value;
  useEffect(() => {
    if (open) { setKept(value as T); return; }
    const t = setTimeout(() => setKept(null), ms);
    return () => clearTimeout(t);
  }, [open, value, ms]);
  return [open ? (value as T) : kept, !open && kept !== null];
}
