import { useEffect, useState } from "react";
import { api } from "../api";
import { useLive } from "../hooks";
import { Btn, ErrorLine, Icon, Skeleton } from "../ui";
import type { Balance } from "./Dashboard";

interface Bank { id: string; name: string; available: boolean; assets: string[] }

/** Add and remove the team's accounts. The banks are fictional and every account runs on the simulator. */
export function AccountsPanel({ accounts, mobile, leaving, onClose, onChanged }: { accounts: Balance[]; mobile: boolean; leaving?: boolean; onClose: () => void; onChanged: () => void }) {
  const banks = useLive<{ banks: Bank[] }>("/api/banks");
  const [bank, setBank] = useState<Bank | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") (bank ? setBank(null) : onClose()); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [bank, onClose]);

  const top = mobile ? 0 : 28;
  return (
    <>
      <div onClick={onClose} className={leaving ? "anim-scrim-out" : "anim-scrim"} style={{ position: "fixed", inset: `${top}px 0 0 0`, background: "var(--scrim)", zIndex: 50, pointerEvents: leaving ? "none" : undefined }} />
      <aside role="dialog" aria-label="Accounts" className={leaving ? "anim-panel-out" : "anim-panel"} style={{ position: "fixed", top, right: 0, bottom: 0, width: mobile ? "100%" : 520, maxWidth: "100%", zIndex: 51, background: "var(--bg)", boxShadow: "var(--shadow-panel)", display: "flex", flexDirection: "column", borderLeft: "1px solid var(--border)", pointerEvents: leaving ? "none" : undefined }}>
        <div style={{ flex: "none", height: 60, display: "flex", alignItems: "center", gap: 4, padding: "0 10px 0 6px", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
          {bank && <button type="button" className="icon-btn" aria-label="Back to the bank list" onClick={() => setBank(null)}><Icon name="arrow_back" size={22} /></button>}
          <span style={{ fontSize: 15, fontWeight: 600, paddingLeft: bank ? 0 : 10 }}>{bank ? `Add a ${bank.name} account` : "Accounts"}</span>
          <button type="button" className="icon-btn" aria-label="Close" style={{ marginLeft: "auto" }} onClick={onClose}><Icon name="close" size={22} /></button>
        </div>
        <div className="overlay-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: mobile ? 16 : 20, display: "flex", flexDirection: "column", gap: 20 }}>
          {bank ? (
            <ConnectForm bank={bank} onDone={() => { setBank(null); onChanged(); }} />
          ) : (
            <>
              <Connected accounts={accounts} onChanged={onChanged} />
              <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <span className="kicker">Add an account</span>
                <ErrorLine error={banks.error} />
                {banks.loading && <Skeleton height={60} count={3} />}
                <div className="card" style={{ overflow: "hidden" }}>
                  {(banks.data?.banks || []).map((b) => <BankRow key={b.id} b={b} onPick={() => setBank(b)} />)}
                </div>
                <span style={{ fontSize: 12.5, color: "var(--muted)", display: "flex", gap: 6 }}><Icon name="info" size={16} />These banks are fictional. Accounts start with a synthetic balance and no real money moves.</span>
              </section>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

function BankMark({ name, muted }: { name: string; muted?: boolean }) {
  return (
    <span aria-hidden="true" style={{ width: 36, height: 36, borderRadius: 10, flex: "none", display: "grid", placeItems: "center", fontSize: 13, fontWeight: 700, background: muted ? "var(--raised)" : "var(--primary-bg)", color: muted ? "var(--subtle)" : "var(--primary-fg)" }}>
      {name.replace(/ bank$/i, "").slice(0, 2).toUpperCase()}
    </span>
  );
}

function BankRow({ b, onPick }: { b: Bank; onPick: () => void }) {
  const inner = (
    <>
      <BankMark name={b.name} muted={!b.available} />
      <span style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0, textAlign: "left" }}>
        <span style={{ fontSize: 14.5, fontWeight: 600, color: b.available ? undefined : "var(--muted)" }}>{b.name}</span>
        <span style={{ fontSize: 12.5, color: "var(--muted)" }}>{b.available ? b.assets.join(" · ") : "Coming soon"}</span>
      </span>
      {b.available ? <Icon name="chevron_right" size={20} style={{ color: "var(--muted)" }} /> : <span className="bdg bdg-sm" style={{ color: "var(--neutral)", background: "var(--neutral-bg)", borderColor: "var(--neutral-bd)" }}>Soon</span>}
    </>
  );
  const style = { display: "flex", alignItems: "center", gap: 12, width: "100%", minHeight: 60, padding: "10px 14px", border: 0, borderBottom: "1px solid var(--border)", background: "transparent" } as const;
  return b.available
    ? <button type="button" className="hoverable" onClick={onPick} style={style}>{inner}</button>
    : <div aria-disabled="true" style={style}>{inner}</div>;
}

function Connected({ accounts, onChanged }: { accounts: Balance[]; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const remove = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      await api(`/api/accounts/${id}/disconnect`, { method: "POST", body: {} });
      setConfirm(null);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span className="kicker">Connected · {accounts.length}</span>
      <ErrorLine error={error} />
      {!accounts.length && <div className="empty" style={{ padding: "28px 16px", fontSize: 14 }}>No accounts connected yet.</div>}
      {!!accounts.length && (
        <div className="card" style={{ overflow: "hidden" }}>
          {accounts.map((a) => (
            <div key={a.id} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <BankMark name={a.name} muted />
                <span style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
                  <span className="wrap" style={{ fontSize: 14.5, fontWeight: 600 }}>{a.name}</span>
                  <span className="num" style={{ fontSize: 12.5, color: "var(--muted)" }}>{a.asset} · {a.balance ?? "No balance read yet"}</span>
                </span>
                {confirm !== a.id && <Btn kind="outline" onClick={() => { setError(null); setConfirm(a.id); }} style={{ minHeight: 36, padding: "0 12px", fontSize: 13 }}>Remove</Btn>}
              </div>
              {confirm === a.id && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", padding: "10px 12px", borderRadius: 12, background: "var(--danger-bg)", border: "1px solid var(--danger-bd)", fontSize: 13.5 }}>
                  <span style={{ flex: 1, minWidth: 180 }}>Remove this account? It won't be used for payments or cash analysis. Its history stays in Audit.</span>
                  <Btn onClick={() => setConfirm(null)} style={{ minHeight: 36, padding: "0 12px", fontSize: 13 }}>Keep</Btn>
                  <Btn kind="danger" busy={busy === a.id} onClick={() => remove(a.id)} style={{ minHeight: 36, padding: "0 12px", fontSize: 13 }}>Remove</Btn>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ConnectForm({ bank, onDone }: { bank: Bank; onDone: () => void }) {
  const [asset, setAsset] = useState(bank.assets[0]);
  const [name, setName] = useState("");
  const [reserve, setReserve] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const placeholder = `${bank.name} ${asset} account`;
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/accounts", { method: "POST", body: { bank: bank.id, asset, name: name.trim() || undefined, reserve: reserve.trim() || "0" } });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 16 }} onSubmit={(e) => { e.preventDefault(); if (!busy) submit(); }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <BankMark name={bank.name} />
        <span style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.45 }}>A simulated account at {bank.name}. It opens with a synthetic balance that Cash reads like any other.</span>
      </div>
      <label className="field">Currency
        <select className="input" value={asset} onChange={(e) => setAsset(e.target.value)}>
          {bank.assets.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <label className="field">Account name
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={placeholder} maxLength={80} />
      </label>
      <label className="field">Reserve
        <input className="input num" value={reserve} onChange={(e) => setReserve(e.target.value)} placeholder="0.00" inputMode="decimal" />
        <span style={{ fontSize: 12.5, fontWeight: 400, color: "var(--muted)" }}>Kept aside and never counted as spendable.</span>
      </label>
      <ErrorLine error={error} />
      <Btn type="submit" kind="primary" busy={busy} icon="add">Add account</Btn>
    </form>
  );
}
