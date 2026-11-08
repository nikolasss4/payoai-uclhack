/* Labels, tones and dates. Amounts always arrive formatted from the server and are never calculated here. */

export type Tone = "ok" | "info" | "warn" | "danger" | "neutral" | "unknown";
export interface B { label: string; icon: string; tone: Tone; fg: string; bg: string; bd: string; bs: "solid" | "dashed" }

// The design's status table: label → tone and icon.
const ST: Record<string, [Tone, string]> = {
  Confirmed: ["ok", "check_circle"], Paid: ["ok", "check_circle"], Verified: ["ok", "verified"], Pass: ["ok", "check"],
  Accounted: ["ok", "task_alt"], Active: ["ok", "play_circle"], "Eligible for approval": ["ok", "check_circle"], Fresh: ["ok", "bolt"],
  Approved: ["info", "thumb_up"], Submitted: ["info", "north_east"], Submitting: ["info", "north_east"], Pending: ["info", "schedule"], "In progress": ["info", "sync"],
  "Payment prepared": ["info", "inventory_2"], Supported: ["info", "attach_file"], Reviewed: ["info", "rate_review"],
  "Ready for approval": ["warn", "front_hand"], "Needs review": ["warn", "visibility"], "Needs information": ["warn", "help"],
  "Needs manual review": ["warn", "person_search"], "Missing evidence": ["warn", "receipt_long"], Stale: ["warn", "history"], Unverified: ["warn", "gpp_maybe"],
  Unknown: ["unknown", "question_mark"], "Outcome unknown": ["unknown", "question_mark"], "Record not found": ["unknown", "link_off"], "No balance": ["unknown", "question_mark"],
  Failed: ["danger", "error"], Rejected: ["danger", "cancel"], Blocked: ["danger", "block"], Block: ["danger", "block"], Revoked: ["danger", "remove_moderator"],
  Cancelled: ["neutral", "do_not_disturb_on"], Superseded: ["neutral", "swap_horiz"], Expired: ["neutral", "timer_off"], "Not applicable": ["neutral", "remove"],
  Completed: ["ok", "task_alt"], Running: ["info", "sync"], "Waiting for an answer": ["warn", "help"], "Awaiting approval": ["warn", "front_hand"], "Awaiting document": ["warn", "receipt_long"],
  Draft: ["neutral", "edit_note"], Registered: ["neutral", "inbox"], Paused: ["neutral", "pause_circle"], "Manually resolved": ["neutral", "person_check"], Simulated: ["neutral", "science"],
};

// Server status codes → the design's words.
const CODES: Record<string, string> = {
  READY_FOR_APPROVAL: "Ready for approval", APPROVED: "Approved", SUBMITTING: "Submitting", SUBMITTED: "Submitted", PENDING: "Pending",
  CONFIRMED: "Confirmed", FAILED: "Failed", REJECTED: "Rejected", UNKNOWN: "Unknown", NEEDS_MANUAL_REVIEW: "Needs manual review", CANCELLED: "Cancelled",
  REGISTERED: "Registered", NEEDS_INFORMATION: "Needs information", NEEDS_REVIEW: "Needs review", VERIFIED: "Verified", BLOCKED: "Blocked",
  PAYMENT_PREPARED: "Payment prepared", PAYMENT_IN_PROGRESS: "In progress", PAID: "Paid", OUTCOME_UNKNOWN: "Outcome unknown", SUPERSEDED: "Superseded",
  ELIGIBLE_FOR_APPROVAL: "Eligible for approval", BLOCK: "Blocked", PASS: "Pass", NOT_APPLICABLE: "Not applicable",
  UNVERIFIED: "Unverified", REVOKED: "Revoked", DRAFT: "Draft", SUPPORTED: "Supported", REVIEWED: "Reviewed", ACCOUNTED: "Accounted",
  MISSING_EVIDENCE: "Missing evidence", ACTIVE: "Active", PAUSED: "Paused", EXPIRED: "Expired", SIMULATED: "Simulated",
  MANUALLY_RESOLVED: "Manually resolved", COMPLETED: "Completed", RUNNING: "Running", WAITING_ANSWER: "Waiting for an answer",
  AWAITING_APPROVAL: "Awaiting approval", AWAITING_DOCUMENT: "Awaiting document", SANDBOX_OBSERVED: "Sandbox", TESTNET_OBSERVED: "Testnet",
};

export function words(code?: string | null): string {
  const text = String(code ?? "").replaceAll("_", " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const label = (code?: string | null) => (code ? CODES[code] || words(code) : "");

/** A badge from a server code or a design label. `text` overrides what it says, not its colour. */
export function badge(code?: string | null, text?: string): B {
  const name = code ? CODES[code] || code : "";
  const [tone, icon] = ST[name] || ["neutral", "circle"];
  return { label: text || name || "Unknown", icon, tone, fg: `var(--${tone})`, bg: `var(--${tone}-bg)`, bd: `var(--${tone}-bd)`, bs: tone === "unknown" ? "dashed" : "solid" };
}

export const decisionBadge = (decision?: string | null) => (decision ? badge(decision) : badge("Registered", "Not checked yet"));

export function initials(name?: string | null): string {
  return (name || "?").replace(/\(.*?\)/g, "").split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}
export const firstName = (name?: string | null) => (name || "").split(" ")[0];

// Dates. Dates without a time ("2026-11-14") are read as local calendar days.

function parse(value?: string | null): Date | null {
  if (!value) return null;
  const d = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}
const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** "10 Nov", or "10 Nov 2025" outside this year. */
export function day(value?: string | null): string {
  const d = parse(value);
  if (!d) return "—";
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString("en-GB", opts);
}
export function hm(value?: string | null): string {
  const d = parse(value);
  return d ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "—";
}
/** "Today", "Yesterday" or "6 Nov". */
export function dayLabel(value?: string | null): string {
  const d = parse(value);
  if (!d) return "—";
  const now = new Date();
  if (sameDay(d, now)) return "Today";
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (sameDay(d, y)) return "Yesterday";
  return day(value);
}
/** "14:05 today", "09:12 yesterday" or "6 Nov 09:12". */
export function when(value?: string | null): string {
  const d = parse(value);
  if (!d) return "—";
  const l = dayLabel(value);
  if (l === "Today") return `${hm(value)} today`;
  if (l === "Yesterday") return `${hm(value)} yesterday`;
  return `${day(value)} ${hm(value)}`;
}
/** For lists: the time today, otherwise the day. */
export function stamp(value?: string | null): string {
  return dayLabel(value) === "Today" ? hm(value) : day(value);
}
export function dateTime(value?: string | null): string {
  return parse(value) ? `${day(value)} ${hm(value)}` : "—";
}
export const isPast = (value?: string | null) => {
  const d = parse(value);
  return !!d && d.getTime() <= Date.now();
};
export const longToday = () => new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
export function fileKind(name?: string | null, media?: string | null): string {
  const m = (media || "").toLowerCase();
  if (m.includes("pdf") || /\.pdf$/i.test(name || "")) return "PDF";
  if (m.startsWith("image/") || /\.(png|jpe?g|gif|webp|heic|heif|bmp|tiff?|avif|svg)$/i.test(name || "")) return "Image";
  if (m.includes("wordprocessingml") || /\.docx$/i.test(name || "")) return "Word";
  if (m.startsWith("text/") || /\.txt$/i.test(name || "")) return "Text";
  return "Document";
}
export const fileIcon = (name?: string | null, media?: string | null) => {
  const kind = fileKind(name, media);
  return kind === "PDF" ? "picture_as_pdf" : kind === "Image" ? "image" : "description";
};

export const ROLE_HINT: Record<string, string> = {
  APPROVER: "Sees every action",
  OPERATOR: "Chat, upload, start Routines, correct records",
  VIEWER: "Read-only",
};

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
