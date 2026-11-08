import { useEffect, useState } from "react";
import { api, setSession, signIn, type Session } from "../api";
import { ROLE_STYLE, words } from "../lib";
import { Avatar, ErrorLine, Icon } from "../ui";

interface DemoUser { name: string; email: string; role: string; team_name: string; avatar_url?: string | null }

const Highlight = ({ children }: { children: string }) => (
  <span style={{ background: "#D4F53C", color: "#0B0B0C", padding: "0 .14em", borderRadius: ".18em", boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone" }}>{children}</span>
);

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
  const photo = mobile ? 52 : 56;
  return (
    <div data-screen-label={mobile ? "M Sign in" : "Sign in"} style={mobile ? { flex: 1, overflowY: "auto", padding: "36px 20px 24px", display: "flex", flexDirection: "column" } : { flex: 1, overflow: "auto", display: "grid", placeItems: "center", padding: "40px 20px" }}>
      <div style={{ width: mobile ? "auto" : 600, maxWidth: "100%", display: "flex", flexDirection: "column", gap: mobile ? 24 : 36 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 24 : 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: mobile ? 9 : 10 }}>
            <span style={{ width: mobile ? 30 : 34, height: mobile ? 30 : 34, borderRadius: mobile ? 9 : 10, background: "#0B0B0C", color: "#D4F53C", display: "grid", placeItems: "center" }}><Icon name="auto_awesome" size={mobile ? 19 : 21} fill /></span>
            <span className="display" style={{ fontWeight: 700, fontSize: mobile ? 22 : 26, letterSpacing: "-0.03em" }}>PayoAI</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 12 : 20 }}>
            <h1 className="display" style={{ margin: 0, fontSize: mobile ? 38 : 58, fontWeight: 600, letterSpacing: "-0.045em", lineHeight: mobile ? 1.06 : 1.04, textWrap: "balance" }}>A finance agent for a <Highlight>small UK business</Highlight></h1>
            <p style={{ margin: 0, fontSize: mobile ? 17 : 19, lineHeight: mobile ? 1.45 : 1.5, color: "var(--muted)" }}>Pick a demo user. There are no real accounts.</p>
          </div>
        </div>
        <ErrorLine error={error} />
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {!users && !error && [0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: mobile ? 76 : 80, borderRadius: 20, animationDelay: `${i * 0.1}s` }} />)}
          {users && !users.length && <span style={{ fontSize: 14, color: "var(--muted)" }}>No demo users. Run the seed first: uv run python -m payoai.seed</span>}
          {(users || []).map((u) => {
            const rs = ROLE_STYLE[u.role] || ROLE_STYLE.VIEWER;
            const isBusy = busy === u.email;
            return (
              <button key={u.email} type="button" className={mobile ? "press" : "lift"} onClick={() => pick(u)} aria-busy={isBusy || undefined}
                style={{ display: "flex", alignItems: "center", gap: mobile ? 14 : 16, minHeight: mobile ? 76 : 80, padding: mobile ? "10px 12px" : "12px 14px", borderRadius: 20, background: "var(--surface)", border: "1px solid var(--border)", cursor: "pointer", boxShadow: mobile ? undefined : "var(--shadow-1)", textAlign: "left", color: "var(--text)" }}>
                <span style={{ flex: "none", width: photo, height: photo, borderRadius: "50%", padding: 3, background: rs.ring, display: "grid", placeItems: "center" }}>
                  <Avatar name={u.name} src={u.avatar_url} size={photo - 6} style={{ border: "2px solid var(--surface)" }} />
                </span>
                <div style={{ display: "flex", flexDirection: "column", gap: mobile ? 5 : 6, flex: 1, minWidth: 0 }}>
                  <span style={{ fontSize: mobile ? 17 : 18, fontWeight: 600, letterSpacing: "-0.01em" }}>{u.name}</span>
                  <span style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 5, height: 24, padding: "0 10px 0 7px", borderRadius: 999, fontSize: 12.5, fontWeight: 600, background: rs.chipBg, color: rs.chipFg }}><Icon name={rs.icon} size={15} fill />{words(u.role)}</span>
                </div>
                <span style={{ flex: "none", width: 44, height: 44, borderRadius: "50%", background: "#0B0B0C", color: "#D4F53C", display: "grid", placeItems: "center" }}>
                  <Icon name={isBusy ? "progress_activity" : "arrow_forward"} size={22} className={isBusy ? "spin" : ""} />
                </span>
              </button>
            );
          })}
        </div>
        {team && <span style={{ fontSize: 13.5, lineHeight: 1.5, color: "var(--muted)" }}>{team.replace(/\s*\(synthetic\)\s*$/i, "")} is a synthetic business. All suppliers, accounts and amounts are invented.</span>}
        <a href="/dev.html" style={{ fontSize: 12.5, color: "var(--muted)" }}>Developer Console</a>
      </div>
    </div>
  );
}
