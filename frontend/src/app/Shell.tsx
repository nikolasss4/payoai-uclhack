import { useState, type ReactNode } from "react";
import { useArmed, useDismiss, useAction, useExit } from "../hooks";
import { ROLE_HINT, ROLE_STYLE, words } from "../lib";
import { RecordView, TITLES } from "../records/RecordView";
import { Audit } from "../screens/Audit";
import { Cash } from "../screens/Cash";
import { ChatScreen } from "../screens/Chat";
import { Dashboard } from "../screens/Dashboard";
import { Expenses } from "../screens/Expenses";
import { Payments } from "../screens/Payments";
import { Avatar, ErrorLine, Icon } from "../ui";
import { setPaused, useApp, type Section } from "./context";
import { usePicture, type Picture } from "./pictures";

const NAV: [Section, string, string][] = [["chat", "Agent", "auto_awesome"], ["approvals", "Dashboard", "space_dashboard"], ["payments", "Payments", "payments"], ["cash", "Cash", "account_balance_wallet"], ["expenses", "Expenses", "receipt_long"], ["audit", "Audit", "history"]];

function Screen({ section }: { section: Section }) {
  switch (section) {
    case "chat": return <ChatScreen />;
    case "approvals": return <Dashboard />;
    case "payments": return <Payments />;
    case "cash": return <Cash />;
    case "expenses": return <Expenses />;
    case "audit": return <Audit />;
    default: return null;
  }
}

function PausedBanner({ mobile }: { mobile: boolean }) {
  const { controls, isA } = useApp();
  const confirm = useArmed();
  const act = useAction();
  if (!controls?.payments_paused) return null;
  const by = controls.paused_by_name;
  return (
    <div role="status" style={{ flex: "none", display: "flex", alignItems: "center", gap: mobile ? 8 : 12, padding: mobile ? "8px 10px 8px 14px" : "10px 20px", background: "var(--warn-bg)", borderBottom: "1px solid var(--warn-bd)", fontSize: mobile ? 13 : 14, lineHeight: 1.35, flexWrap: "wrap" }}>
      <Icon name="pause_circle" size={mobile ? 20 : 22} style={{ color: "var(--warn)" }} />
      <span style={{ flex: mobile ? 1 : undefined }}>
        <b style={{ fontWeight: 600 }}>New payments are paused.</b>{mobile ? (by ? ` By ${by}.` : "") : ` Nothing new will be approved or sent.${by ? ` Paused by ${by}.` : ""}`}
      </span>
      <ErrorLine error={act.error} />
      {isA && (
        <button type="button" className="btn-pill btn-primary" aria-busy={act.busy || undefined} style={{ marginLeft: mobile ? undefined : "auto", height: mobile ? 44 : 40, minHeight: 0, padding: mobile ? "0 14px" : "0 16px", fontSize: mobile ? 13 : 13.5, flex: "none" }}
          onClick={() => { if (!confirm.armed) return confirm.arm(); confirm.disarm(); act.run(() => setPaused(false)); }}>
          <Icon name="play_arrow" size={18} />{confirm.armed ? "Tap again to resume" : "Resume"}
        </button>
      )}
    </div>
  );
}

const ringOf = (role: string) => ROLE_STYLE[role]?.ring || "var(--border)";
const itemStyle = { border: 0, width: "100%", background: "transparent", textAlign: "left" as const };

/** Change or remove a picture, from inside a menu. The file input itself lives in the shell. */
function PictureItems({ pic, what }: { pic: Picture; what: string }) {
  return (
    <>
      <button type="button" className="menu-item" onClick={pic.choose} aria-busy={pic.busy || undefined} style={itemStyle}>
        <Icon name={pic.busy ? "progress_activity" : "add_a_photo"} size={19} className={pic.busy ? "spin" : ""} />{pic.current ? `Change ${what}` : `Add ${what}`}
      </button>
      {pic.current && (
        <button type="button" className="menu-item" onClick={pic.remove} style={itemStyle}>
          <Icon name="hide_image" size={19} />Remove {what}
        </button>
      )}
      {pic.error && <div style={{ padding: "4px 12px 8px" }}><ErrorLine error={pic.error} /></div>}
    </>
  );
}

function OrgMenuList({ onPick, pic }: { onPick: () => void; pic: Picture }) {
  const { session, switchTeam, isA } = useApp();
  return (
    <>
      <div className="kicker" style={{ padding: "8px 12px", fontSize: 11.5 }}>Organisation</div>
      {session.teams.map((t) => {
        const on = t.id === session.team.id;
        return (
          <button key={t.id} type="button" className="menu-item" onClick={() => { switchTeam(t.id); onPick(); }} style={{ ...itemStyle, minHeight: 48, gap: 10, background: on ? "var(--raised)" : "transparent" }}>
            <Avatar name={t.name} src={t.avatar_url} size={36} square />
            <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 500 }}>{t.name}</span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>{words(t.role)}{t.members ? ` · ${t.members} member${t.members === 1 ? "" : "s"}` : ""}</span>
            </div>
            {on && <Icon name="check" size={19} />}
          </button>
        );
      })}
      {isA && <><div style={{ height: 1, background: "var(--border)", margin: "6px 4px" }} /><PictureItems pic={pic} what="organisation picture" /></>}
    </>
  );
}

function UserMenuList({ onPick, pic }: { onPick: () => void; pic: Picture }) {
  const { session, isA, controls, signOut, theme, toggleTheme } = useApp();
  const act = useAction();
  const role = session.team.role;
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px 12px" }}>
        <Avatar name={session.user.name} src={session.user.avatar_url} size={40} ring={ringOf(role)} />
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{session.user.name}</span>
          <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{words(role)} · {ROLE_HINT[role]}</span>
        </div>
      </div>
      <PictureItems pic={pic} what="profile picture" />
      {isA && !controls?.payments_paused && (
        <button type="button" className="menu-item" onClick={() => act.run(async () => { await setPaused(true); onPick(); })} style={{ color: "var(--danger)", border: 0, width: "100%", background: "transparent", textAlign: "left" }}>
          <Icon name="pause_circle" size={19} />Pause all payments
        </button>
      )}
      <ErrorLine error={act.error} />
      <button type="button" className="menu-item" onClick={toggleTheme} style={{ border: 0, width: "100%", background: "transparent", textAlign: "left" }}>
        <Icon name={theme === "light" ? "dark_mode" : "light_mode"} size={19} />{theme === "light" ? "Dark mode" : "Light mode"}
      </button>
      {session.developer && <a className="menu-item" href="/dev.html" style={{ color: "var(--text)", textDecoration: "none" }}><Icon name="terminal" size={19} />Developer Console</a>}
      <button type="button" className="menu-item" onClick={() => { onPick(); signOut(); }} style={{ border: 0, width: "100%", background: "transparent", textAlign: "left" }}>
        <Icon name="switch_account" size={19} />Switch demo user
      </button>
    </>
  );
}

export function WebShell() {
  const app = useApp();
  const { session, nav, go, wide, pendingCount, notifications, open, close, back } = app;
  const [menu, setMenu] = useState<"org" | "user" | null>(null);
  const mePic = usePicture("me");
  const teamPic = usePicture("team");
  const orgRef = useDismiss(menu === "org", () => setMenu(null));
  const userRef = useDismiss(menu === "user", () => setMenu(null));
  const top = nav.stack[nav.stack.length - 1];
  const [panel, leaving] = useExit(top);
  const unread = notifications?.unread || 0;
  const bellOn = top?.kind === "notifications";

  return (
    <div data-screen-label="PayoAI Web" style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--text)", overflow: "hidden", position: "relative" }}>
      {mePic.input}{teamPic.input}
      <header style={{ flex: "none", height: 60, display: "flex", alignItems: "center", gap: 12, padding: "0 20px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
        <span className="display" style={{ marginRight: 12, fontWeight: 700, fontSize: 19, letterSpacing: "-0.03em", whiteSpace: "nowrap" }}>PayoAI</span>
        <nav aria-label="Main" className="scroll-x-none" style={{ display: "flex", flexWrap: "nowrap", minWidth: 0, overflowX: "auto", flex: "0 1 auto", gap: 4, padding: 4, borderRadius: 999, background: "var(--raised)" }}>
          {NAV.map(([id, label]) => {
            const active = nav.section === id;
            if (id === "chat") {
              return (
                <button key={id} type="button" aria-current={active ? "page" : undefined} onClick={() => go(id)}
                  style={{ flex: "none", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 6, minHeight: 36, padding: "0 16px 0 12px", borderRadius: 999, cursor: "pointer", fontSize: 14, fontWeight: 600, background: "var(--primary-bg)", color: "var(--primary-fg)", boxShadow: active ? "0 0 0 2px var(--raised), 0 0 0 4px var(--primary-bg)" : "none", border: 0 }} className="press">
                  <Icon name="auto_awesome" size={18} fill weight={600} />{label}
                </button>
              );
            }
            return (
              <button key={id} type="button" aria-current={active ? "page" : undefined} onClick={() => go(id)}
                style={{ flex: "none", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 6, minHeight: 36, padding: "0 14px", borderRadius: 999, cursor: "pointer", fontSize: 14, fontWeight: 500, transition: "background .2s", background: active ? "var(--surface)" : "transparent", color: active ? "var(--text)" : "var(--muted)", boxShadow: active ? "var(--shadow-1)" : "none", border: 0 }}>
                {label}
                {id === "approvals" && pendingCount > 0 && <span style={{ minWidth: 18, height: 18, padding: "0 6px", borderRadius: 999, background: "var(--primary-bg)", color: "var(--primary-fg)", fontSize: 11, fontWeight: 600, display: "grid", placeItems: "center" }}>{pendingCount}</span>}
              </button>
            );
          })}
        </nav>
        <div style={{ marginLeft: "auto", flex: "none", display: "flex", alignItems: "center", gap: 4 }}>
          <button type="button" className="icon-btn" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} onClick={() => (bellOn ? close() : open({ kind: "notifications" }))} style={{ position: "relative", background: bellOn ? "var(--raised)" : undefined }}>
            <Icon name="notifications" size={23} />
            {unread > 0 && <span style={{ position: "absolute", top: 4, right: 2, minWidth: 18, height: 18, padding: "0 5px", borderRadius: 999, background: "var(--danger)", color: "#fff", fontSize: 11, fontWeight: 600, display: "grid", placeItems: "center", border: "2px solid var(--surface)" }}>{unread > 99 ? "99+" : unread}</span>}
          </button>
          <div ref={orgRef} style={{ position: "relative" }}>
            <button type="button" aria-label="Switch organisation" aria-expanded={menu === "org"} onClick={() => setMenu(menu === "org" ? null : "org")} className="hoverable"
              style={{ display: "flex", alignItems: "center", gap: 8, height: 44, padding: "0 10px 0 6px", borderRadius: 999, cursor: "pointer", background: menu === "org" ? "var(--raised)" : "transparent", border: 0 }}>
              <Avatar name={session.team.name} src={session.team.avatar_url} size={30} square />
              {wide && <span style={{ fontSize: 13.5, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 170 }}>{session.team.name}</span>}
              <Icon name="expand_more" size={20} style={{ color: "var(--muted)" }} />
            </button>
            {menu === "org" && <div className="menu" style={{ position: "absolute", right: 0, top: 50, zIndex: 40, width: 300 }}><OrgMenuList onPick={() => setMenu(null)} pic={teamPic} /></div>}
          </div>
          <div ref={userRef} style={{ position: "relative" }}>
            <button type="button" aria-label="Account" aria-expanded={menu === "user"} onClick={() => setMenu(menu === "user" ? null : "user")} className="hoverable"
              style={{ display: "flex", alignItems: "center", gap: 10, height: 44, padding: "0 10px 0 4px", borderRadius: 999, cursor: "pointer", border: 0, background: menu === "user" ? "var(--raised)" : "transparent" }}>
              <Avatar name={session.user.name} src={session.user.avatar_url} size={34} ring={ringOf(session.team.role)} />
              {wide && <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2, textAlign: "left" }}><span style={{ fontSize: 13.5, fontWeight: 500, whiteSpace: "nowrap" }}>{session.user.name}</span><span style={{ fontSize: 12, color: "var(--muted)", whiteSpace: "nowrap" }}>{words(session.team.role)}</span></div>}
              <Icon name="expand_more" size={20} style={{ color: "var(--muted)" }} />
            </button>
            {menu === "user" && <div className="menu" style={{ position: "absolute", right: 0, top: 50, zIndex: 40, width: 280 }}><UserMenuList onPick={() => setMenu(null)} pic={mePic} /></div>}
          </div>
        </div>
      </header>
      <PausedBanner mobile={false} />
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <main style={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto", position: "relative" }}><Screen section={nav.section} /></main>
      </div>
      {panel && (
        <>
          <div onClick={close} className={leaving ? "anim-scrim-out" : "anim-scrim"} style={{ position: "absolute", inset: 0, background: "var(--scrim)", zIndex: 50, pointerEvents: leaving ? "none" : undefined }} />
          <aside role="dialog" aria-label={TITLES[panel.kind]} className={leaving ? "anim-panel-out" : "anim-panel"} style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 560, maxWidth: "100%", zIndex: 51, background: "var(--bg)", boxShadow: "var(--shadow-panel)", display: "flex", flexDirection: "column", borderLeft: "1px solid var(--border)", pointerEvents: leaving ? "none" : undefined }}
            onKeyDown={(e) => { if (e.key === "Escape") close(); }}>
            <div style={{ flex: "none", height: 60, display: "flex", alignItems: "center", gap: 8, padding: "0 10px 0 12px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
              {nav.stack.length > 1 && <button type="button" className="icon-btn" aria-label="Back" onClick={back}><Icon name="arrow_back" size={22} /></button>}
              <span style={{ fontSize: 15, fontWeight: 600, paddingLeft: 6 }}>{TITLES[panel.kind]}</span>
              <button type="button" className="icon-btn" aria-label="Close" style={{ marginLeft: "auto" }} onClick={close}><Icon name="close" size={22} /></button>
            </div>
            <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateRows: "minmax(0,1fr)" }}><RecordView rec={panel} /></div>
          </aside>
        </>
      )}
    </div>
  );
}

function NavItem({ id, label, icon, active, badge, onClick }: { id: string; label: string; icon: string; active: boolean; badge?: number; onClick: () => void }) {
  return (
    <button key={id} type="button" aria-current={active ? "page" : undefined} onClick={onClick}
      style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 1, height: 52, borderRadius: 26, cursor: "pointer", transition: "background .2s", background: active ? "var(--raised)" : "transparent", color: active ? "var(--text)" : "var(--muted)", border: 0 }}>
      <Icon name={icon} size={23} fill={active} /><span style={{ fontSize: 10, fontWeight: 500 }}>{label}</span>
      {!!badge && <span style={{ position: "absolute", top: 4, right: 10, minWidth: 16, height: 16, padding: "0 4px", borderRadius: 8, background: "var(--primary-bg)", color: "var(--primary-fg)", fontSize: 10, fontWeight: 600, display: "grid", placeItems: "center" }}>{badge}</span>}
    </button>
  );
}

export function MobileShell() {
  const { session, nav, go, pendingCount, notifications, open, back } = useApp();
  const [menu, setMenu] = useState<"org" | "user" | null>(null);
  const mePic = usePicture("me");
  const teamPic = usePicture("team");
  const ref = useDismiss(menu !== null, () => setMenu(null));
  const top = nav.stack[nav.stack.length - 1];
  const unread = notifications?.unread || 0;
  const isChat = nav.section === "chat";
  const content: ReactNode = top ? (
    <div className="anim-page" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 4, height: 52, padding: "0 8px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
        <button type="button" onClick={back} aria-label="Back" style={{ display: "flex", alignItems: "center", gap: 2, height: 44, padding: "0 10px 0 4px", borderRadius: 999, cursor: "pointer", fontSize: 15, fontWeight: 500, background: "transparent", border: 0 }}><Icon name="chevron_left" size={24} />Back</button>
        <span style={{ flex: 1, textAlign: "center", fontSize: 15, fontWeight: 600, paddingRight: 72 }}>{TITLES[top.kind]}</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateRows: "minmax(0,1fr)" }}><RecordView rec={top} /></div>
    </div>
  ) : isChat ? <ChatScreen /> : (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "18px 14px 24px", display: "flex", flexDirection: "column", gap: 14 }}><Screen section={nav.section} /></div>
  );

  return (
    <div data-screen-label="PayoAI Mobile" style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--bg)", position: "relative", overflow: "hidden" }}>
      {mePic.input}{teamPic.input}
      <div ref={ref} style={{ flex: "none", position: "relative", zIndex: 40 }}>
        <header style={{ display: "flex", alignItems: "center", gap: 2, padding: "6px 6px 6px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface)", minHeight: 56 }}>
          <span className="display" style={{ flex: 1, minWidth: 0, fontWeight: 700, fontSize: 17, letterSpacing: "-0.03em", whiteSpace: "nowrap" }}>PayoAI</span>
          <button type="button" className="icon-btn" aria-label="Audit" onClick={() => go("audit")} style={{ background: nav.section === "audit" && !top ? "var(--raised)" : undefined }}><Icon name="history" size={22} /></button>
          <button type="button" className="icon-btn" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} onClick={() => open({ kind: "notifications" })} style={{ position: "relative" }}>
            <Icon name="notifications" size={22} />
            {unread > 0 && <span style={{ position: "absolute", top: 4, right: 1, minWidth: 18, height: 18, padding: "0 5px", borderRadius: 999, background: "var(--danger)", color: "#fff", fontSize: 10.5, fontWeight: 600, display: "grid", placeItems: "center", border: "2px solid var(--surface)" }}>{unread > 99 ? "99+" : unread}</span>}
          </button>
          <button type="button" className="icon-btn" aria-label="Switch organisation" onClick={() => setMenu(menu === "org" ? null : "org")} style={{ borderRadius: 12 }}>
            <Avatar name={session.team.name} src={session.team.avatar_url} size={30} square />
          </button>
          <button type="button" className="icon-btn" aria-label="Account" onClick={() => setMenu(menu === "user" ? null : "user")}>
            <Avatar name={session.user.name} src={session.user.avatar_url} size={32} ring={ringOf(session.team.role)} />
          </button>
        </header>
        {menu && (
          <div className="menu" style={{ position: "absolute", left: 12, right: 12, top: 62, zIndex: 40, boxShadow: "0 12px 32px rgba(11,11,12,.18)" }}>
            {menu === "org" ? <OrgMenuList onPick={() => setMenu(null)} pic={teamPic} /> : <UserMenuList onPick={() => setMenu(null)} pic={mePic} />}
          </div>
        )}
      </div>
      <PausedBanner mobile />
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", position: "relative" }}>{content}</div>
      {!top && (
        <div style={{ flex: "none", padding: "6px 14px calc(10px + env(safe-area-inset-bottom))", background: "var(--bg)" }}>
          <nav aria-label="Main" style={{ height: 64, borderRadius: 32, background: "var(--glass)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)", border: "1px solid var(--border)", display: "grid", gridTemplateColumns: "repeat(5,minmax(0,1fr))", alignItems: "center", padding: "0 6px", boxShadow: "var(--shadow-1)" }}>
            <NavItem id="approvals" label="Dashboard" icon="space_dashboard" active={nav.section === "approvals"} badge={pendingCount} onClick={() => go("approvals")} />
            <NavItem id="payments" label="Payments" icon="payments" active={nav.section === "payments"} onClick={() => go("payments")} />
            <div style={{ display: "grid", placeItems: "center" }}>
              <button type="button" aria-label="Agent" aria-current={isChat ? "page" : undefined} onClick={() => go("chat")}
                style={{ width: 54, height: 54, borderRadius: "50%", display: "grid", placeItems: "center", cursor: "pointer", background: "var(--primary-bg)", color: "var(--primary-fg)", boxShadow: isChat ? "0 0 0 3px var(--bg), 0 0 0 5px var(--text)" : "0 6px 20px rgba(212,245,60,.25)", border: 0 }} className="press">
                <Icon name="auto_awesome" size={26} fill />
              </button>
            </div>
            <NavItem id="cash" label="Cash" icon="account_balance_wallet" active={nav.section === "cash"} onClick={() => go("cash")} />
            <NavItem id="expenses" label="Expenses" icon="receipt_long" active={nav.section === "expenses"} onClick={() => go("expenses")} />
          </nav>
        </div>
      )}
    </div>
  );
}
