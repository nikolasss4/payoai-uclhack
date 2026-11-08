import { useState } from "react";
import { loadSession, type Session } from "./api";
import { AppProvider, useApp, useTheme } from "./app/context";
import { MobileShell, WebShell } from "./app/Shell";
import { useMedia } from "./hooks";
import { SignIn } from "./screens/SignIn";
import { Icon } from "./ui";

function Shell() {
  const { mobile } = useApp();
  return mobile ? <MobileShell /> : <WebShell />;
}

export function App() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const mobile = !useMedia("(min-width: 760px)");
  useTheme();
  if (!session) {
    return (
      <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--text)" }}>
        <div role="note" style={{ flex: "none", height: mobile ? 24 : 28, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, background: "var(--text)", color: "var(--bg)", fontFamily: "var(--mono)", fontSize: mobile ? 11.5 : 12, letterSpacing: ".02em" }}>
          <Icon name="science" size={mobile ? 14 : 15} />Synthetic data. No real money.
        </div>
        <SignIn mobile={mobile} onSession={setSession} />
      </div>
    );
  }
  // Keyed by user and team, so switching either starts every screen fresh.
  return (
    <AppProvider key={`${session.user.id}:${session.team.id}`} session={session} onSession={setSession}>
      <Shell />
    </AppProvider>
  );
}
