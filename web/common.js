/* Shared by the app and the Developer Console. All text goes in through text nodes, never as HTML,
   because replies, documents and supplier names are untrusted. */

const Payo = (() => {
  function h(spec, props, ...kids) {
    const [tag, ...classes] = spec.split(".");
    const node = document.createElement(tag || "div");
    if (classes.length) node.className = classes.join(" ");
    let children = kids;
    if (props instanceof Node || typeof props !== "object" || props === null || Array.isArray(props)) {
      children = [props, ...kids];
    } else {
      for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else if (key === "data") Object.entries(value).forEach(([k, v]) => { if (v !== undefined && v !== null) node.dataset[k] = v; });
        else if (key in node && key !== "list") node[key] = value;
        else node.setAttribute(key, value === true ? "" : value);
      }
    }
    add(node, children);
    return node;
  }

  function add(node, kids) {
    for (const kid of kids) {
      if (kid === null || kid === undefined || kid === false) continue;
      if (Array.isArray(kid)) add(node, kid);
      else node.append(kid instanceof Node ? kid : String(kid));
    }
  }

  const store = {
    get(key, fallback = null) {
      try {
        const raw = sessionStorage.getItem(key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Works without storage, just forgets on reload. */ }
    },
    remove(key) {
      try { sessionStorage.removeItem(key); } catch { /* Nothing to remove. */ }
    },
  };

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  /** headers() supplies the session's headers. The server does every permission check. */
  function client(headers) {
    return async function api(path, { method = "GET", body, form, signal } = {}) {
      const opts = { method, signal, headers: { ...headers() } };
      if (form) opts.body = form;
      else if (body !== undefined) {
        opts.body = JSON.stringify(body);
        opts.headers["Content-Type"] = "application/json";
      }
      let response;
      try {
        response = await fetch(path, opts);
      } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
      }
      const text = await response.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = null; }
      if (!response.ok) {
        const detail = data && data.detail;
        const message = typeof detail === "string" ? detail : `That didn't work (error ${response.status}).`;
        throw new ApiError(message, response.status);
      }
      return data;
    };
  }

  const LABELS = {
    ACTIVE: "Active",
    APPROVED: "Approved",
    AWAITING_APPROVAL: "Waiting for approval",
    BLOCK: "Blocked",
    BLOCKED: "Blocked",
    CANCELLED: "Cancelled",
    COMPLETED: "Completed",
    CONFIRMED: "Confirmed",
    DECLINED: "Declined",
    ELIGIBLE_FOR_APPROVAL: "Eligible for approval",
    EXPIRED: "Expired",
    FAILED: "Failed",
    MANUALLY_RESOLVED: "Resolved by hand",
    MISSING_EVIDENCE: "Missing evidence",
    NEEDS_INFORMATION: "Needs information",
    NEEDS_MANUAL_REVIEW: "Needs manual review",
    NEEDS_REVIEW: "Needs review",
    NOT_APPLICABLE: "Not applicable",
    OUTCOME_UNKNOWN: "Outcome unknown",
    PAID: "Paid",
    PASS: "Passed",
    PAUSED: "Paused",
    PAYMENT_IN_PROGRESS: "Payment in progress",
    PENDING: "Pending",
    READY_FOR_APPROVAL: "Ready for approval",
    REJECTED: "Rejected",
    REVOKED: "Revoked",
    RUNNING: "Running",
    SIMULATED: "Simulated",
    STOPPED: "Stopped",
    SUBMITTED: "Submitted, not confirmed",
    SUBMITTING: "Submitting, not confirmed",
    SUPPORTED: "Supported",
    UNKNOWN: "Unknown: not confirmed",
    UNVERIFIED: "Not verified",
    VERIFIED: "Verified",
    WAITING_ANSWER: "Waiting for your answer",
  };

  const TONES = {
    good: ["ACTIVE", "APPROVED", "COMPLETED", "CONFIRMED", "ELIGIBLE_FOR_APPROVAL", "PAID", "PASS", "SUPPORTED", "VERIFIED"],
    warn: ["AWAITING_APPROVAL", "MANUALLY_RESOLVED", "MISSING_EVIDENCE", "NEEDS_INFORMATION", "NEEDS_MANUAL_REVIEW", "NEEDS_REVIEW", "OUTCOME_UNKNOWN", "PAUSED", "PENDING", "READY_FOR_APPROVAL", "UNKNOWN", "UNVERIFIED", "WAITING_ANSWER"],
    bad: ["BLOCK", "BLOCKED", "CANCELLED", "DECLINED", "EXPIRED", "FAILED", "REJECTED", "REVOKED", "STOPPED"],
    info: ["PAYMENT_IN_PROGRESS", "RUNNING", "SIMULATED", "SUBMITTED", "SUBMITTING"],
  };

  function words(code) {
    const text = String(code ?? "").replaceAll("_", " ").toLowerCase();
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  const label = (code) => LABELS[code] || words(code);
  function tone(code) {
    return Object.keys(TONES).find((name) => TONES[name].includes(code)) || "neutral";
  }
  const badge = (code, text) => h("span.badge", { data: { tone: tone(code) } }, text || label(code));

  function time(iso) {
    if (!iso) return "not set";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    return date.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  function ago(iso) {
    const ms = Date.now() - new Date(iso).getTime();
    if (!iso || Number.isNaN(ms)) return "unknown";
    const minutes = Math.round(ms / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
  }

  const notice = (toneName, ...text) => h("div.notice", { data: { tone: toneName }, role: toneName === "bad" ? "alert" : undefined }, ...text);

  function facts(rows) {
    const dl = h("dl.facts");
    for (const [name, value] of rows) {
      if (value === null || value === undefined || value === false) continue;
      dl.append(h("dt", name), h("dd", value));
    }
    return dl;
  }

  /** A destructive control needs a second tap, so a slip can't trigger it. */
  function twoTap(button, armedText, action) {
    const resting = button.textContent;
    let timer = null;
    button.addEventListener("click", async () => {
      if (!button.dataset.armed) {
        button.dataset.armed = "yes";
        button.textContent = armedText;
        timer = setTimeout(() => { delete button.dataset.armed; button.textContent = resting; }, 5000);
        return;
      }
      clearTimeout(timer);
      delete button.dataset.armed;
      button.textContent = resting;
      await action();
    });
    return button;
  }

  return { h, store, client, ApiError, label, words, tone, badge, time, ago, notice, facts, twoTap };
})();
