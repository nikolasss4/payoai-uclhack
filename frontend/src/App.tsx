import { useState } from "react";
import { loadSession, type Session } from "./api";
import { AppProvider, useApp, useTheme } from "./app/context";
import { MobileShell, WebShell } from "./app/Shell";
import { useMedia } from "./hooks";
import { SignIn } from "./screens/SignIn";

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
