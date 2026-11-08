import { useEffect, useState } from "react";
import { api, setSession, signIn, type Session } from "../api";
import { initials, words } from "../lib";
import { ErrorLine, Icon } from "../ui";

interface DemoUser { name: string; email: string; role: string; team_name: string }

export function SignIn({ mobile, onSession }: { mobile: boolean; onSession: (s: Session) => void }) {
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    api<DemoUser[]>("/api/demo-users", { auth: false }).then(setUsers).catch((e) => setError((e as Error).message));
  }, []);
  const pick = async (u: DemoUser) => {
    if (busy) return;
    setBusy(u.email);
    setError(null);
    try {
      const s = await signIn(u.email);
      setSession(s);
      onSession(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const team = users?.[0]?.team_name;
  return (
    <div data-screen-label="Sign in" style={mobile ? { flex: 1, overflowY: "auto", padding: "36px 20px 24px", display: "flex", flexDirection: "column", gap: 24 } : { flex: 1, overflow: "auto", display: "grid", placeItems: "center", padding: "40px 20px" }}>
      <div style={{ width: mobile ? "auto" : 440, maxWidth: "100%", display: "flex", flexDirection: "column", gap: mobile ? 24 : 28 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 24 : 14 }}>
          <span className="display" style={{ fontWeight: 700, fontSize: mobile ? 24 : 26, letterSpacing: "-0.03em" }}>PayoAI</span>
          <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 10 : 14 }}>
            <h1 className="display" style={{ margin: 0, fontSize: mobile ? 28 : 32, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.15 }}>A finance agent for a small UK business</h1>
            <p style={{ margin: 0, fontSize: 15, color: "var(--muted)" }}>Pick a demo user. There are no real accounts.</p>
          </div>
        </div>
        <ErrorLine error={error} />
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {!users && !error && [0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 64, borderRadius: 16, animationDelay: `${i * 0.1}s` }} />)}
          {users && !users.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No demo users. Run the seed first: uv run python -m payoai.seed</span>}
          {(users || []).map((u) => (
            <button key={u.email} type="button" className="hover-border" onClick={() => pick(u)} aria-busy={busy === u.email || undefined}
              style={{ display: "flex", alignItems: "center", gap: mobile ? 12 : 14, minHeight: 64, padding: mobile ? "10px 14px" : "10px 16px", borderRadius: 16, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", boxShadow: mobile ? undefined : "var(--shadow-1)", textAlign: "left" }}>
              <span style={{ width: 40, height: 40, borderRadius: "50%", background: "var(--raised)", display: "grid", placeItems: "center", fontSize: 13, fontWeight: 600, flex: "none" }}>{initials(u.name)}</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}><span style={{ fontSize: 15.5, fontWeight: 500 }}>{u.name}</span><span style={{ fontSize: 13, color: "var(--muted)" }}>{words(u.role)}</span></div>
              <Icon name={busy === u.email ? "progress_activity" : "arrow_forward"} size={22} className={busy === u.email ? "spin" : ""} style={{ color: "var(--muted)" }} />
            </button>
          ))}
        </div>
        {team && <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{team.replace(/\s*\(synthetic\)\s*$/i, "")} is a synthetic business. All suppliers, accounts and amounts are invented.</span>}
        <a href="/dev.html" style={{ fontSize: 12.5, color: "var(--muted)" }}>Developer Console</a>
      </div>
    </div>
  );
}
