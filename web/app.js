/* The PayoAI app. Every screen shows what the API returned: money values are shown as sent and never calculated here.
   Role checks only hide buttons. The server enforces them. */

const { h, store, client, label, words, tone, badge, time, ago, notice, facts, twoTap } = Payo;

const el = {
  main: document.getElementById("main"),
  topbar: document.getElementById("topbar"),
  tabs: document.getElementById("tabs"),
  paused: document.getElementById("paused-bar"),
};

/* Session */

let session = store.get("payo.session");
const api = client(() => (session ? { Authorization: `Bearer ${session.token}`, "X-Team-Id": session.team.id } : {}));

const ROLE_ABOUT = {
  APPROVER: "Can approve payments, verify destinations and pause all payments.",
  OPERATOR: "Can prepare bills and payments. An Approver has to approve them.",
  VIEWER: "Can read everything and ask questions. Can't change anything.",
};
const roleName = (role) => words(role);
const can = (...roles) => Boolean(session) && roles.includes(session.team.role);
const isApprover = () => can("APPROVER");

async function signIn(email) {
  const result = await client(() => ({}))("/api/session", { method: "POST", body: { email } });
  if (!result.teams.length) throw new Payo.ApiError("This user has no team. Developers use the Developer Console.", 0);
  session = { token: result.token, user: result.user, team: result.teams[0], teams: result.teams };
  store.set("payo.session", session);
  location.hash = "#/chat";
  startApp();
}

function signOut() {
  closeStream();
  session = null;
  chat.id = null;
  store.remove("payo.session");
  store.remove("payo.chat");
  location.hash = "";
  startApp();
}

/* Router. Each screen gets a context whose timers stop when you navigate away. */

let current = null;

const ROUTES = [
  [/^#\/chat$/, screenChat, "chat"],
  [/^#\/approvals$/, screenApprovals, "approvals"],
  [/^#\/approvals\/([\w-]+)$/, screenApproval, "approvals"],
  [/^#\/payments$/, screenPayments, "payments"],
  [/^#\/payments\/([\w-]+)$/, screenPayment, "payments"],
  [/^#\/more$/, screenMore, "more"],
  [/^#\/bills$/, screenBills, "more"],
  [/^#\/bills\/([\w-]+)$/, screenBill, "more"],
  [/^#\/contacts$/, screenContacts, "more"],
  [/^#\/contacts\/([\w-]+)$/, screenContact, "more"],
  [/^#\/contacts\/([\w-]+)\/([\w-]+)$/, screenDestination, "more"],
  [/^#\/cash$/, screenCash, "more"],
  [/^#\/automations$/, screenAutomations, "more"],
  [/^#\/expenses$/, screenExpenses, "more"],
  [/^#\/expenses\/([\w-]+)$/, screenExpense, "more"],
  [/^#\/memory$/, screenMemory, "more"],
  [/^#\/notifications$/, screenNotifications, "more"],
];

function route() {
  if (current) current.stop();
  if (!session) return screenSignIn();
  const hash = location.hash || "#/chat";
  const match = ROUTES.map(([pattern, screen, tab]) => ({ found: hash.match(pattern), screen, tab })).find((r) => r.found);
  if (!match) {
    location.hash = "#/chat";
    return undefined;
  }
  const timers = [];
  const context = {
    alive: true,
    live(fn, ms) {
      timers.push(setInterval(() => { if (context.alive && !document.hidden) fn(); }, ms));
    },
    stop() {
      context.alive = false;
      timers.forEach(clearInterval);
      if (match.screen === screenChat) closeStream();
    },
  };
  current = context;
  el.main.className = "main";
  drawTabs(match.tab);
  const view = h("div.view");
  el.main.replaceChildren(view);
  el.main.scrollTop = 0;
  return match.screen(view, context, ...match.found.slice(1));
}

/** Draws fn's nodes into a holder and redraws on a timer. A failed refresh keeps the last good screen. */
function loader(view, context, fn, ms) {
  const holder = h("div.list", notice("neutral", "Loading…"));
  const problem = h("div.problem");
  view.append(problem, holder);
  const load = async () => {
    try {
      const nodes = await fn();
      if (!context.alive) return;
      problem.replaceChildren();
      holder.replaceChildren(...[nodes].flat(Infinity).filter(Boolean));
    } catch (error) {
      if (context.alive) problem.replaceChildren(notice("bad", error.message));
      if (holder.firstChild?.textContent === "Loading…") holder.replaceChildren();
    }
  };
  load();
  if (ms) context.live(load, ms);
  return load;
}

const title = (text, sub) => h("div", h("h1", text), sub ? h("p.muted", sub) : null);
const back = (href, text) => h("a.back", { href }, `‹ ${text}`);
const empty = (text) => notice("neutral", text);

/** Runs a server action, shows its message and stops double taps. */
function action(button, out, fn) {
  button.addEventListener("click", () => run(button, out, fn));
  return button;
}

async function run(button, out, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (error) {
    out.replaceChildren(notice("bad", error.message));
  } finally {
    button.disabled = false;
  }
}

/* Frame */

function startApp() {
  if (session) {
    el.topbar.hidden = false;
    el.tabs.hidden = false;
    drawTopbar();
    pollShell();
  } else {
    el.topbar.hidden = true;
    el.tabs.hidden = true;
    el.paused.hidden = true;
  }
  route();
}

function drawTopbar() {
  const parts = [h("span.brand", "PayoAI"), h("span.who", h("span.who-name", session.user.name), h("span.who-role", { id: "who-role" }, roleName(session.team.role)))];
  if (isApprover()) {
    const pause = h("button.btn.btn-small.btn-danger-outline", { id: "pause-all", type: "button", hidden: shell.paused }, "Pause all payments");
    pause.addEventListener("click", () => setPaused(true));
    parts.push(pause);
  }
  el.topbar.replaceChildren(...parts);
}

const shell = { paused: false, pending: 0, tab: "chat", timer: null, changes: 0 };

async function setPaused(paused) {
  try {
    await api("/api/controls/pause", { method: "POST", body: { paused } });
    shell.changes += 1;
    shell.paused = paused;
    drawPaused();
    drawTopbar();
  } catch (error) {
    el.paused.hidden = false;
    el.paused.replaceChildren(h("span", error.message));
  }
}

function drawPaused(pausedAt) {
  el.paused.hidden = !shell.paused;
  if (!shell.paused) return;
  const kids = [h("span", `All payments are paused${pausedAt ? ` since ${time(pausedAt)}` : ""}.`)];
  if (isApprover()) {
    const resume = h("button.btn.btn-small", { id: "resume-all", type: "button" }, "Resume");
    twoTap(resume, "Tap again to resume", () => setPaused(false));
    kids.push(resume);
  }
  el.paused.replaceChildren(...kids);
}

async function pollShell() {
  clearTimeout(shell.timer);
  if (!session) return;
  const changesAtStart = shell.changes;
  try {
    const [controls, pending] = await Promise.all([api("/api/controls"), api("/api/approvals?status=PENDING")]);
    shell.pending = pending.length;
    // A reading taken before a tap on Pause mustn't undo what the tap just showed.
    if (changesAtStart === shell.changes) {
      const changed = controls && controls.payments_paused !== shell.paused;
      shell.paused = Boolean(controls && controls.payments_paused);
      if (changed) drawTopbar();
      if (changed || el.paused.hidden === shell.paused) drawPaused(controls && controls.paused_at);
    }
    drawTabs(shell.tab);
  } catch {
    /* The screens report their own errors. This only updates the badge and banner. */
  }
  shell.timer = setTimeout(pollShell, 10000);
}

function drawTabs(active) {
  shell.tab = active;
  const tab = (id, text, count) => h("a.tab", { href: `#/${id}`, "aria-current": active === id ? "page" : undefined }, text, count ? h("span.tab-count", String(count)) : null);
  el.tabs.replaceChildren(tab("chat", "Chat"), tab("approvals", "Approvals", shell.pending), tab("payments", "Payments"), tab("more", "More"));
}

/* Sign-in */

function screenSignIn() {
  el.main.className = "main";
  const out = h("div");
  const list = h("div.list", notice("neutral", "Loading demo users…"));
  const view = h("div.view.signin", h("h1", "PayoAI"), h("p", "A finance agent for a small UK business."), h("p.muted", "Pick a demo user. There are no real accounts."), out, list, h("a.muted.small", { href: "/dev.html" }, "Developer Console"));
  el.main.replaceChildren(view);
  client(() => ({}))("/api/demo-users")
    .then((users) => {
      list.replaceChildren(...users.map((user) => {
        const button = h("button.item.user-pick", { type: "button", data: { email: user.email } },
          h("span.item-top", h("span.item-title", user.name), badge(user.role === "APPROVER" ? "VERIFIED" : "", roleName(user.role))),
          h("span.muted", ROLE_ABOUT[user.role] || ""));
        return action(button, out, () => signIn(user.email));
      }));
    })
    .catch((error) => list.replaceChildren(notice("bad", error.message)));
}

/* Chat */

const chat = {
  id: store.get("payo.chat"),
  stream: null,
  log: null,
  seen: new Set(),
  last: 0,
  pending: new Set(),
  turnsShown: new Set(),
  progress: new Map(),
  questions: new Map(),
  cards: new Map(),
  context: null,
  attachment: null,
};

const ROUTINE_STARTING = "routine";
const localTurns = (id) => store.get(`payo.turns.${id}`, {});
const decidedCards = () => store.get("payo.confirmations", {});

function closeStream() {
  if (chat.stream) chat.stream.abort();
  chat.stream = null;
}

function screenChat(view, context) {
  chat.context = context;
  el.main.className = "main main--chat";
  chat.log = h("div.chat-log", { id: "chat-log", "aria-live": "polite" });
  const picker = h("select.select", { id: "chat-picker", "aria-label": "Past chats" }, h("option", { value: "" }, "Past chats"));
  picker.addEventListener("change", () => openChat(picker.value || null));
  const newChat = h("button.btn.btn-small", { type: "button", onclick: () => { picker.value = ""; openChat(null); } }, "New chat");
  const screen = h("div.chat", h("div.chat-bar", picker, newChat), chat.log, composer());
  el.main.replaceChildren(screen);
  api("/api/chats").then((rows) => {
    if (!context.alive) return;
    for (const row of rows) picker.append(h("option", { value: row.id }, `${row.title || "Untitled"} · ${time(row.created_at)}`));
    if (chat.id) picker.value = chat.id;
  }).catch(() => {});
  context.live(refreshCards, 6000);
  openChat(chat.id);
}

async function openChat(id) {
  closeStream();
  chat.id = id;
  store.set("payo.chat", id);
  chat.seen = new Set();
  chat.turnsShown = new Set();
  chat.progress = new Map();
  chat.questions = new Map();
  chat.cards = new Map();
  chat.last = 0;
  chat.log.replaceChildren();
  chat.working = null;
  if (!id) return drawWelcome();
  try {
    // Reading from the start re-renders every Chip from the live record.
    const events = await api(`/api/chats/${id}/events`);
    if (chat.id !== id) return undefined;
    events.forEach(showEvent);
    drawWorking();
    scrollChat(true);
    return openStream(id);
  } catch (error) {
    chat.log.append(notice("bad", error.message));
    return undefined;
  }
}

function drawWelcome() {
  const ask = (text) => tapToSend(h("button.btn.btn-small", { type: "button" }, text), text);
  chat.log.append(h("div.card.welcome",
    h("p", `You're signed in as ${session.user.name}, ${roleName(session.team.role)}, at ${session.team.name}.`),
    h("p.muted", ROLE_ABOUT[session.team.role] || ""),
    h("div.btn-row", ask("What are our balances?"), ask("Which bills are due?"), ask("Why was a payment blocked?"))));
}

/** The stream needs an Authorization header, which EventSource can't send, so it's read with fetch. */
async function openStream(id) {
  const controller = new AbortController();
  chat.stream = controller;
  let failures = 0;
  while (!controller.signal.aborted && chat.id === id) {
    try {
      const response = await fetch(`/api/chats/${id}/stream?after=${chat.last}`, {
        headers: { Authorization: `Bearer ${session.token}`, "X-Team-Id": session.team.id },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`stream ${response.status}`);
      failures = 0;
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop();
        for (const block of blocks) {
          const line = block.split("\n").find((l) => l.startsWith("data: "));
          if (line) showEvent(JSON.parse(line.slice(6)));
        }
        drawWorking();
        scrollChat();
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      failures += 1;
    }
    if (!controller.signal.aborted) await new Promise((resolve) => { setTimeout(resolve, Math.min(1000 * 2 ** failures, 15000)); });
  }
}

function userBubble(turnId, text) {
  if (turnId) chat.turnsShown.add(turnId);
  chat.log.append(h("div.bubble.bubble-user", text));
}

/** Events carry no user text, so what you typed comes from this browser, matched by Turn. */
function showTurnOpening(event) {
  if (!event.turn_id || chat.turnsShown.has(event.turn_id)) return;
  const text = localTurns(chat.id)[event.turn_id];
  if (text) userBubble(event.turn_id, text);
  else chat.turnsShown.add(event.turn_id);
}

function showEvent(event) {
  if (chat.seen.has(event.id)) return;
  chat.seen.add(event.id);
  clearWorking();
  chat.last = Math.max(chat.last, event.id);
  showTurnOpening(event);
  closeQuestions(event);
  const payload = event.payload || {};
  if (event.kind === "QUESTION" || event.kind === "ERROR" || payload.finished) chat.pending.delete(ROUTINE_STARTING);
  if (event.kind === "STATUS") showStatus(event, payload);
  else if (event.kind === "QUESTION") showQuestion(event, payload);
  else if (event.kind === "START_CARD") chat.log.append(startCard(payload));
  else if (event.kind === "APPROVAL_CARD") showApprovalCard(payload.approval_id);
  else if (event.kind === "REPLY") {
    chat.pending.delete(event.turn_id);
    chat.log.append(replyBubble(event));
    chat.progress.delete(event.turn_id);
  } else if (event.kind === "ERROR") {
    chat.pending.delete(event.turn_id);
    chat.log.append(h("div.bubble.bubble-error", payload.message || "Something went wrong."));
  }
}

/** Later progress in a run means its question was answered, so the buttons can't be tapped again. */
function closeQuestions(event) {
  if (event.kind === "QUESTION" || !event.run_id) return;
  const open = chat.questions.get(event.run_id);
  if (open) {
    open.forEach((node) => node.remove());
    chat.questions.delete(event.run_id);
  }
}

function progressBlock(key) {
  let block = chat.progress.get(key);
  // A block is reused only while it's still the newest thing in the chat.
  if (!block || chat.log.lastElementChild !== block) {
    block = h("div.progress");
    chat.progress.set(key, block);
    chat.log.append(block);
  }
  return block;
}

function showStatus(event, payload) {
  const key = event.run_id || event.turn_id || "chat";
  if (payload.node) {
    progressBlock(key).append(h("div", payload.node));
  } else if (payload.task_list) {
    const block = progressBlock(key);
    for (const item of payload.task_list) block.append(h("div", `${item.status === "DONE" ? "✓" : item.status === "CANCELLED" ? "✕" : "○"} ${item.description}`));
  } else if (payload.finished) {
    foldProgress(key);
    chat.log.append(h("div.progress",
      h("div.progress-summary.badges", h("strong", payload.routine ? words(payload.routine) : "Routine"), badge(payload.status), payload.decision ? badge(payload.decision) : null),
      payload.stop ? h("div", payload.stop) : null,
      payload.explanation ? h("div.progress-summary", payload.explanation) : null,
      payload.counts ? h("div", Object.entries(payload.counts).map(([k, v]) => `${words(k)}: ${v}`).join(" · ")) : null));
  }
}

/** A finished run's steps fold into one line, so the outcome stays in view. */
function foldProgress(key) {
  const block = chat.progress.get(key);
  if (!block || !block.children.length) return;
  const steps = [...block.children];
  block.replaceChildren(h("details.fold", h("summary", `${steps.length} step${steps.length > 1 ? "s" : ""} run`), h("div.progress", steps)));
  chat.progress.delete(key);
}

function showQuestion(event, payload) {
  const options = payload.options || [];
  const buttons = options.map((option) => tapToSend(h("button.btn", { type: "button", data: { value: option.value } }, option.label), `${option.label} [answer value: ${option.value}]`, option.label));
  const actions = buttons.length ? h("div.btn-row", buttons) : h("p.muted", "Type your answer below.");
  chat.log.append(h("div.card.question-card", h("div.card-kicker", "Question"), h("p", payload.prompt), actions));
  if (event.run_id) chat.questions.set(event.run_id, [actions]);
}

function startCard(payload) {
  const out = h("div");
  const card = h("div.card.start-card", h("div.card-kicker", "Tap to start"), h("h2", payload.routine),
    facts(Object.entries(payload.inputs || {}).filter(([, v]) => v !== null && v !== "").map(([k, v]) => [k === "document_id" ? "Document" : words(k), typeof v === "object" ? JSON.stringify(v) : String(v)])),
    h("p.muted", "This only starts the checks. It doesn't pay anything or verify anyone."), out);
  const id = payload.confirmation_id;
  const settled = decidedCards()[id];
  if (settled) {
    card.append(badge(settled, label(settled)));
    return card;
  }
  const decide = (decision) => async () => {
    const result = await api(`/api/confirmations/${id}/${decision}`, { method: "POST", body: {} });
    store.set("payo.confirmations", { ...decidedCards(), [id]: result.status });
    if (result.status === "CONFIRMED") {
      chat.pending.add(ROUTINE_STARTING);
      drawWorking();
      scrollChat(true);
    }
    buttons.replaceWith(badge(result.status, result.status === "CONFIRMED" ? "Started" : label(result.status)));
    out.replaceChildren();
  };
  const start = action(h("button.btn.btn-primary", { type: "button", data: { action: "start" } }, "Start"), out, decide("confirm"));
  const decline = action(h("button.btn", { type: "button", data: { action: "decline" } }, "Not now"), out, decide("decline"));
  const buttons = h("div.btn-row", start, decline);
  card.append(buttons);
  return card;
}

/** A Chip's values only ever come from the parts array. The reply's raw text is never shown. */
function replyBubble(event) {
  const parts = Array.isArray(event.parts) ? event.parts : [];
  const bubble = h("div.bubble.bubble-agent");
  if (!parts.length) bubble.append(h("span.muted", "This reply has no content to show."));
  for (const part of parts) {
    if (part.type === "chip") bubble.append(chipNode(part));
    else if (part.type === "text") bubble.append(...richText(part.text));
  }
  return bubble;
}

function richText(text) {
  return String(text).split(/(\*\*[^*\n]+\*\*)/).filter(Boolean).map((piece) => {
    const bold = piece.match(/^\*\*(.+)\*\*$/);
    return bold ? h("strong", bold[1]) : document.createTextNode(piece);
  });
}

const CHIP_LINKS = { payment: "payments", bill: "bills", expense: "expenses" };

function chipNode(part) {
  if (part.missing) return h("span.chip", { data: { missing: "yes", kind: part.kind } }, part.label || "record not found");
  const inner = [
    part.label ? h("span.chip-label", part.label) : null,
    part.amount ? h("span.chip-amount", part.amount) : null,
    part.status ? badge(part.status) : null,
  ];
  const data = { kind: part.kind };
  if (part.kind === "account") return h("a.chip", { href: "#/cash", data }, inner);
  const base = CHIP_LINKS[part.kind];
  return base ? h("a.chip", { href: `#/${base}/${part.id}`, data }, inner) : h("span.chip", { data }, inner);
}

function showApprovalCard(id) {
  if (!id) return;
  const holder = h("div.card.approval-chat-card", { data: { approval: id } }, h("div.card-kicker", "Payment for approval"), h("p.muted", "Loading the live card…"));
  chat.log.append(holder);
  chat.cards.set(id, holder);
  drawApprovalCard(id, holder);
}

async function drawApprovalCard(id, holder) {
  try {
    const card = await api(`/api/approvals/${id}`);
    const kids = [
      h("div.card-kicker", "Payment for approval"),
      h("div.head-row", h("div", h("h2", card.counterparty), h("div.amount", card.amount)), badge(card.status)),
      h("div.badges", card.decision ? badge(card.decision.decision) : null, badge(card.destination_status, `Destination: ${label(card.destination_status)}`), card.status === "PENDING" ? null : badge(card.payment_status)),
      card.warnings?.length ? notice("warn", `${card.warnings.length} warning${card.warnings.length > 1 ? "s" : ""} to review.`) : null,
      h("a.btn", { href: `#/approvals/${id}` }, card.can_decide ? "Review and decide" : "Open"),
      !isApprover() && card.status === "PENDING" ? h("p.muted", "Waiting for an Approver.") : null,
    ];
    holder.replaceChildren(...kids.filter(Boolean));
    // The card only has its real height now, so the first draw brings it into view.
    if (!holder.dataset.drawn) scrollChat(true);
    holder.dataset.drawn = "yes";
  } catch (error) {
    holder.replaceChildren(notice("bad", error.message));
  }
}

function refreshCards() {
  chat.cards.forEach((holder, id) => drawApprovalCard(id, holder));
}

function clearWorking() {
  if (chat.working) chat.working.remove();
  chat.working = null;
}

function drawWorking() {
  clearWorking();
  if (chat.pending.size) {
    chat.working = h("div.working", { id: "chat-working" }, "PayoAI is working…");
    chat.log.append(chat.working);
  }
}

function scrollChat(force) {
  const log = chat.log;
  if (!log) return;
  if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 240) log.scrollTop = log.scrollHeight;
}

function composer() {
  const out = h("div");
  const input = h("textarea.textarea", { id: "chat-input", rows: 1, placeholder: "Ask PayoAI…", "aria-label": "Message" });
  const attached = h("div.attached", { hidden: !chat.attachment });
  const drawAttached = () => {
    attached.hidden = !chat.attachment;
    if (!chat.attachment) return attached.replaceChildren();
    return attached.replaceChildren(h("span", `Attached: ${chat.attachment.name}`), h("button.btn.btn-small", { type: "button", onclick: () => { chat.attachment = null; drawAttached(); } }, "Remove"));
  };
  drawAttached();
  const file = h("input.file-input", { id: "chat-file", type: "file", accept: ".pdf,.txt,.png,.jpg,.jpeg,application/pdf,text/plain,image/png,image/jpeg", "aria-label": "Attach a document" });
  file.addEventListener("change", async () => {
    const chosen = file.files[0];
    file.value = "";
    if (!chosen) return;
    out.replaceChildren(notice("neutral", `Uploading ${chosen.name}…`));
    try {
      const form = new FormData();
      form.append("file", chosen);
      const doc = await api("/api/documents", { method: "POST", form });
      chat.attachment = { id: doc.id, name: chosen.name };
      drawAttached();
      out.replaceChildren();
    } catch (error) {
      out.replaceChildren(notice("bad", error.message));
    }
  });
  const attach = can("OPERATOR", "APPROVER") ? h("button.btn.attach", { type: "button", id: "chat-attach", "aria-label": "Attach a document", onclick: () => file.click() }, "+") : null;
  const send = async () => {
    const text = input.value.trim();
    if (!text && !chat.attachment) return;
    input.value = "";
    try {
      await sendTurn(text || "Here's a document.");
      drawAttached();
      out.replaceChildren();
    } catch (error) {
      input.value = text;
      out.replaceChildren(notice("bad", error.message));
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  return h("form.composer", { onsubmit: (event) => { event.preventDefault(); send(); } },
    out, attached, file,
    h("div.composer-row", attach, input, h("button.btn.btn-primary.send", { id: "chat-send", type: "submit" }, "Send")));
}

/** Tapping sends a Turn. The buttons around it stay usable only if that fails. */
function tapToSend(button, text, shownText) {
  button.addEventListener("click", async () => {
    const group = [...(button.parentElement?.querySelectorAll("button") || [button])];
    group.forEach((b) => { b.disabled = true; });
    try {
      await sendTurn(text, shownText);
    } catch (error) {
      group.forEach((b) => { b.disabled = false; });
      chat.log.append(notice("bad", error.message));
    }
  });
  return button;
}

async function sendTurn(text, shownText) {
  let opened = false;
  if (!chat.id) {
    const created = await api("/api/chats", { method: "POST" });
    chat.id = created.id;
    store.set("payo.chat", created.id);
    chat.log.replaceChildren();
    opened = true;
  }
  const attachment = chat.attachment;
  const body = { text, document_id: attachment ? attachment.id : null };
  const turn = await api(`/api/chats/${chat.id}/turns`, { method: "POST", body });
  chat.attachment = null;
  const shown = attachment ? `${shownText || text}\nAttached: ${attachment.name}` : (shownText || text);
  store.set(`payo.turns.${chat.id}`, { ...localTurns(chat.id), [turn.id]: shown });
  userBubble(turn.id, shown);
  chat.pending.add(turn.id);
  drawWorking();
  scrollChat(true);
  if (opened) openStream(chat.id);
}

/* Approvals */

function batchEligible(card) {
  return card.can_decide && !card.requires_review && card.decision?.decision === "ELIGIBLE_FOR_APPROVAL" && !isExpired(card);
}

function whyNotBatch(card) {
  if (card.decision?.decision === "BLOCK") return "Blocked. Can't be approved.";
  if (isExpired(card)) return "Expired.";
  if (card.requires_review || card.decision?.decision !== "ELIGIBLE_FOR_APPROVAL") return "Needs its own review. Open it to decide.";
  return "";
}

const isExpired = (card) => card.status === "PENDING" && card.expires_at && new Date(card.expires_at) <= new Date();

function screenApprovals(view, context) {
  const state = { filter: "PENDING", selected: new Set(), results: h("div") };
  const select = h("select.select", { id: "approvals-filter", "aria-label": "Show approvals" },
    ["PENDING", "APPROVED", "REJECTED", "CANCELLED"].map((s) => h("option", { value: s }, label(s))));
  view.append(title("Approvals", "Each payment needs its own approval from an Approver."), select, state.results);
  const bar = h("div.btn-row");
  const load = loader(view, context, async () => {
    const rows = await api(`/api/approvals?status=${state.filter}`);
    const eligible = rows.filter(batchEligible);
    for (const id of [...state.selected]) if (!eligible.some((r) => r.approval_id === id)) state.selected.delete(id);
    drawBar(rows);
    if (!rows.length) return empty(state.filter === "PENDING" ? "Nothing is waiting for approval." : `No ${label(state.filter).toLowerCase()} approvals.`);
    return rows.map((card) => approvalRow(card, state, rows));
  }, 8000);
  view.insertBefore(bar, view.lastChild);
  select.addEventListener("change", () => { state.filter = select.value; state.selected.clear(); load(); });

  function drawBar(rows) {
    bar.replaceChildren();
    if (!isApprover() || state.filter !== "PENDING" || !rows.some(batchEligible)) return;
    const chosen = rows.filter((r) => state.selected.has(r.approval_id));
    const button = h("button.btn.btn-primary", { id: "batch-approve", type: "button", disabled: !chosen.length }, chosen.length ? `Approve ${chosen.length} selected` : "Tick payments to approve together");
    action(button, state.results, async () => {
      const items = chosen.map((r) => ({ approval_id: r.approval_id, hash: r.hash }));
      const result = await api("/api/approvals/batch", { method: "POST", body: { items } });
      const names = Object.fromEntries(chosen.map((r) => [r.approval_id, `${r.counterparty} ${r.amount}`]));
      state.results.replaceChildren(h("div.list", result.results.map((r) => notice(r.won ? "good" : "warn", `${names[r.approval_id]}: ${r.message}`))));
      state.selected.clear();
      await load();
      pollShell();
    });
    bar.append(button);
    state.redrawBar = () => drawBar(rows);
  }
}

function approvalRow(card, state) {
  const main = h("a.item-main", { href: `#/approvals/${card.approval_id}` },
    h("span.item-top", h("span.item-title", card.counterparty), h("span.item-amount", card.amount)),
    h("span.badges",
      isExpired(card) ? badge("EXPIRED") : badge(card.status),
      card.decision ? badge(card.decision.decision) : null,
      card.warnings?.length ? badge("NEEDS_REVIEW", `${card.warnings.length} warning${card.warnings.length > 1 ? "s" : ""}`) : null),
    h("span.muted.small", card.status === "PENDING" ? `Expires ${time(card.expires_at)}` : (card.decided_by ? `${label(card.status)} by ${card.decided_by}, ${time(card.decided_at)}` : label(card.status))));
  const row = h("div.item.item-row", { data: { approval: card.approval_id } });
  if (isApprover() && state.filter === "PENDING") {
    const eligible = batchEligible(card);
    const box = h("input", { type: "checkbox", disabled: !eligible, checked: state.selected.has(card.approval_id), title: eligible ? "Select for batch approval" : whyNotBatch(card), "aria-label": eligible ? `Select ${card.counterparty}` : whyNotBatch(card) });
    box.addEventListener("change", () => {
      if (box.checked) state.selected.add(card.approval_id);
      else state.selected.delete(card.approval_id);
      state.redrawBar?.();
    });
    row.append(h("label.item-select", box));
    if (!eligible) main.append(h("span.muted.small", whyNotBatch(card)));
  }
  row.append(main);
  return row;
}

function screenApproval(view, context, id) {
  view.append(back("#/approvals", "Approvals"));
  const state = { card: null, reviewed: false, changed: false, revealed: null };
  const factsBox = h("div.list", notice("neutral", "Loading…"));
  const actions = h("div.list");
  const result = h("div");
  view.append(factsBox, result, actions);

  const load = async () => {
    try {
      const card = await api(`/api/approvals/${id}`);
      if (!context.alive) return;
      if (state.card && state.card.hash !== card.hash) {
        // What was reviewed isn't what's on screen any more, so the review doesn't carry over.
        state.reviewed = false;
        state.revealed = null;
        state.changed = true;
      }
      state.card = card;
      draw();
    } catch (error) {
      if (context.alive) result.replaceChildren(notice("bad", error.message));
    }
  };

  function draw() {
    factsBox.replaceChildren(...detailBlocks(state, draw));
    drawActions();
  }

  function drawActions() {
    const card = state.card;
    actions.replaceChildren();
    if (!card.can_decide) {
      if (card.status === "PENDING") actions.append(notice("neutral", "Only an Approver can decide this payment."));
      return;
    }
    const approve = h("button.btn.btn-primary", { id: "approve", type: "button", disabled: card.requires_review && !state.reviewed }, "Approve");
    const reject = h("button.btn.btn-danger-outline", { id: "reject", type: "button" }, "Reject");
    const reason = h("input.input", { id: "reject-reason", type: "text", placeholder: "Reason for rejecting (optional)", "aria-label": "Reason for rejecting", value: state.reason || "" });
    reason.addEventListener("input", () => { state.reason = reason.value; });
    if (card.requires_review) {
      const tick = h("input", { id: "reviewed", type: "checkbox", checked: state.reviewed });
      tick.addEventListener("change", () => {
        state.reviewed = tick.checked;
        approve.disabled = !tick.checked;
      });
      actions.append(h("label.tick", tick, h("span", "I've reviewed the warnings")));
    }
    // Sends the hash from the card that's on screen, so a changed payment is refused by the server.
    const decide = (decision) => async () => {
      const body = { hash: card.hash, decision, reviewed: decision === "APPROVED" && state.reviewed, reason: decision === "REJECTED" ? (state.reason || null) : null };
      const answer = await api(`/api/approvals/${id}/decide`, { method: "POST", body });
      result.replaceChildren(notice(answer.won ? "good" : "warn", answer.message));
      await load();
      pollShell();
    };
    action(approve, result, decide("APPROVED"));
    action(reject, result, decide("REJECTED"));
    actions.append(h("div.btn-row", approve, reject), reason);
  }

  load();
  context.live(load, 5000);
}

function detailBlocks(state, redraw) {
  const card = state.card;
  const expired = isExpired(card);
  const blocks = [];
  if (state.changed) blocks.push(notice("bad", "This payment changed while you had it open. Review it again."));
  if (shell.paused) blocks.push(notice("bad", "All payments are paused. Approving won't work until they're resumed."));
  blocks.push(h("div.card",
    h("div.head-row", h("div", h("h1", card.counterparty), h("div.amount", { id: "detail-amount" }, card.amount)), expired ? badge("EXPIRED") : badge(card.status)),
    facts([
      ["Destination", h("span.badges", h("span", card.destination), badge(card.destination_status))],
      ["Maximum fee", card.max_fee],
      ["From", card.source_account],
      ["Reference", card.reference],
      ["Rail", words(card.rail)],
      ["Asset", card.asset],
      ["Chain", card.chain_id],
      ["Token contract", card.token_contract ? h("span.mono", card.token_contract) : null],
      ["Approval expires", h("span", time(card.expires_at), expired ? " (expired)" : "")],
      ["Added and verified by the same person", card.added_and_verified_by_same_person ? badge("NEEDS_REVIEW", "Yes") : "No"],
      ["Decided by", card.decided_by ? `${card.decided_by}, ${time(card.decided_at)}` : null],
      ["Payment", card.status === "PENDING" ? null : h("a", { href: `#/payments/${card.intent_id}` }, label(card.payment_status))],
    ]),
    revealBlock(state, redraw)));

  if (card.warnings?.length) {
    blocks.push(h("div.section-title", "Warnings"), h("div.list", card.warnings.map((text) => notice("warn", text))));
  }
  if (card.decision) {
    blocks.push(h("div.section-title", "Decision"), h("div.card", h("div.badges", badge(card.decision.decision)), h("p", card.decision.explanation)));
  }
  blocks.push(h("div.section-title", "Checks"), h("div.card.checks", (card.checks || []).map(checkRow)));
  const suggestions = card.agent_suggestions || [];
  if (suggestions.length) {
    blocks.push(h("div.section-title", "Agent suggestions"),
      h("div.card", h("p.muted", "Suggestions from the agent, not checks or decisions."), h("div.suggestions", { id: "agent-suggestions" }, suggestions.map((s) => h("p", typeof s === "string" ? s : s.finding)))));
  }
  blocks.push(h("div.muted.small", "Payment fingerprint, sent with your decision: ", h("span.mono", { id: "shown-hash" }, card.hash)));
  return blocks;
}

const DETAIL_NAMES = { iban: "IBAN", sort_code: "Sort code", account_number: "Account number", address: "Wallet address" };
const detailName = (key) => DETAIL_NAMES[key] || words(key);

function revealBlock(state, redraw) {
  const card = state.card;
  if (!isApprover()) return null;
  if (state.revealed) {
    return h("div.secret", { id: "revealed" }, facts(Object.entries(state.revealed).map(([k, v]) => [detailName(k), h("span.mono", String(v))])),
      h("button.btn.btn-small", { type: "button", onclick: () => { state.revealed = null; redraw(); } }, "Hide"));
  }
  const out = h("div");
  const button = h("button.btn.btn-small", { id: "reveal", type: "button" }, "Show full details");
  action(button, out, async () => {
    const result = await api(`/api/approvals/${card.approval_id}/reveal`);
    state.revealed = result.details || {};
    redraw();
  });
  return h("div", button, h("p.muted.small", "Each time you reveal these, it's recorded."), out);
}

const checkRow = (check) => h("div.check",
  h("div.head-row", h("strong", check.check_name), badge(check.outcome)),
  h("span", check.reason),
  h("span.muted.small", check.rule_id));

/* Payments */

function uniqueById(rows) {
  const seen = new Set();
  return rows.filter((row) => (seen.has(row.id) ? false : seen.add(row.id)));
}

function screenPayments(view, context) {
  view.append(title("Payments", "Submitted isn't the same as confirmed. Only Confirmed means the source shows it arrived."));
  loader(view, context, async () => {
    const rows = uniqueById(await api("/api/payments"));
    if (!rows.length) return empty("No payments yet.");
    return rows.map((p) => h("a.item", { href: `#/payments/${p.id}`, data: { payment: p.id } },
      h("span.item-top", h("span.item-title", p.counterparty), h("span.item-amount", p.amount)),
      h("span.badges", badge(p.status), p.evidence ? badge(p.evidence) : null),
      h("span.muted.small", p.reference || "")));
  }, 8000);
}

const STAGES = [
  ["Bill read", /^(Load Document|Extract Fields|extract_fields|Match Counterparty|Register Obligation)/],
  ["Safety checks", /^(Check |Select |Allocate )/],
  ["Decision", /^(Decide|Investigate|investigate|Explain|explain)/],
  ["Payment prepared", /^Prepare/],
  ["Human approval", /^Approval tap/],
  ["Checks before sending", /^(Verify Approval|Re-check|Record Attempt)/],
  ["Submitted to provider", /^(Submit|Record Result)/],
  ["Checked with the source", /^(Query Source|Compare Outcome|Update State|Reconcile)/],
];

function stageFor(step) {
  const found = STAGES.find(([, pattern]) => pattern.test(step.name));
  return found ? found[0] : "Other steps";
}

function buildStages(steps) {
  const stages = new Map();
  for (const step of steps) {
    const name = stageFor(step);
    if (!stages.has(name)) stages.set(name, []);
    stages.get(name).push(step);
  }
  return [...stages.entries()].map(([name, list]) => {
    const failed = list.filter((s) => s.status === "FAILED" || s.error);
    const running = list.some((s) => s.status === "RUNNING");
    return { name, steps: list, failed, toneName: failed.length ? "bad" : running ? "warn" : "good" };
  });
}

/** Model calls sit inside the step that made them, so only the named steps are listed. */
function stageSummary(stage) {
  const names = stage.steps.filter((s) => s.step_type !== "LLM_CALL" && !s.name.startsWith("Approval tap")).map((s) => s.name);
  if (stage.steps.some((s) => s.name.startsWith("Approval tap"))) names.push(`${stage.steps.length} tap${stage.steps.length > 1 ? "s" : ""}`);
  return [...new Set(names)].join(", ");
}

function tapLine(step) {
  const out = step.output || {};
  const counted = out.won === true;
  return h("div.small", h("span.badges", badge(counted ? "PASS" : "", counted ? "Counted" : "Not counted"), h("span", out.message || step.name)));
}

const STATUS_NOTES = {
  UNKNOWN: ["warn", "We don't know yet whether this payment went through. Don't pay it again. PayoAI keeps checking with the source."],
  NEEDS_MANUAL_REVIEW: ["warn", "The outcome couldn't be established automatically. Don't pay it again. An Approver has to settle it with evidence."],
  FAILED: ["bad", "This payment failed."],
  REJECTED: ["bad", "The provider rejected this payment."],
  CANCELLED: ["bad", "This payment was cancelled. Nothing was sent."],
  SUBMITTED: ["info", "Submitted to the provider, not yet confirmed."],
  SUBMITTING: ["info", "Being submitted now, not yet confirmed."],
  CONFIRMED: ["good", "Confirmed by the source."],
};

function screenPayment(view, context, id) {
  view.append(back("#/payments", "Payments"));
  // The same form is kept across refreshes, so what's being typed in it isn't lost.
  const forms = {};
  loader(view, context, async () => {
    const [list, trace] = await Promise.all([api("/api/payments"), api(`/api/payments/${id}/trace`)]);
    const payment = list.find((p) => p.id === id);
    return paymentBlocks(payment, trace, forms);
  }, 8000);
}

function paymentBlocks(payment, trace, forms) {
  const outcomes = trace.outcomes || [];
  const submit = outcomes.find((o) => o.source === "SUBMIT");
  const evidence = payment?.evidence || outcomes[0]?.evidence_label;
  const status = payment?.status;
  const note = STATUS_NOTES[status];
  const blocks = [h("div.card",
    h("div.head-row", h("div", h("h1", payment ? payment.counterparty : "Payment"), payment ? h("div.amount", payment.amount) : null), status ? badge(status) : null),
    facts([["Reference", payment?.reference], ["Evidence", evidence ? badge(evidence) : "None yet"]]),
    evidence === "SIMULATED" ? h("p.muted", "Simulated: this ran on the simulator, so no real money moved.") : null)];
  if (note) blocks.push(notice(note[0], note[1]));

  blocks.push(h("div.pair", { id: "submitted-confirmed" },
    h("div.card", h("h3", "Submitted"), submit ? badge("SUBMITTED", words(submit.observed_status)) : badge("", "Not submitted"), submit ? h("span.muted.small", time(submit.created_at)) : null),
    h("div.card", h("h3", "Confirmed"), status === "CONFIRMED" ? badge("CONFIRMED", "Confirmed") : badge(status === "UNKNOWN" || status === "NEEDS_MANUAL_REVIEW" ? "UNKNOWN" : "", status === "UNKNOWN" || status === "NEEDS_MANUAL_REVIEW" ? "Unknown" : "Not confirmed"))));

  if (status === "NEEDS_MANUAL_REVIEW" && isApprover()) {
    forms.resolve = forms.resolve || resolveForm(trace);
    blocks.push(forms.resolve);
  }

  blocks.push(h("div.section-title", "What happened"));
  const stages = buildStages(trace.steps || []);
  blocks.push(h("div.card", stages.length ? h("ol.stages", { id: "trace-stages" }, stages.map((stage) => h("li.stage", { data: { tone: stage.toneName } },
    h("span.stage-dot"),
    h("div.stage-title", stage.name),
    h("div.muted.small", `${time(stage.steps[0].started_at)} · ${stageSummary(stage)}`),
    stage.steps.filter((s) => s.name.startsWith("Approval tap")).map(tapLine),
    stage.failed.map((s) => h("div.small", `${s.name}: ${s.error || "failed"}`))))) : h("p.muted", "No steps recorded.")));

  if (outcomes.length) {
    blocks.push(h("div.section-title", "Outcomes seen"), h("div.card.checks", outcomes.map((o) => h("div.check",
      h("div.head-row", h("strong", o.source === "SUBMIT" ? "Provider's answer on submit" : o.source === "QUERY" ? "Status read from the source" : words(o.source)), o.evidence_label ? badge(o.evidence_label) : null),
      h("span", words(o.observed_status)),
      h("span.muted.small", time(o.created_at))))));
  }
  if (trace.audit?.length) {
    blocks.push(h("div.section-title", "Audit record"), h("div.card.checks", trace.audit.map((a) => h("div.check",
      h("span", a.event_type.replace(/([a-z])([A-Z])/g, "$1 $2")),
      h("span.muted.small", `${words(a.actor_type)} · ${time(a.created_at)}`)))));
  }
  return blocks;
}

function resolveForm(trace) {
  const attempt = (trace.steps || []).find((s) => s.name === "Record Attempt")?.input?.attempt_id;
  if (!attempt) return notice("warn", "The attempt ID isn't available, so this can't be settled from here.");
  const out = h("div");
  const outcome = h("select.select", h("option", { value: "NOT_PAID" }, "Not paid"), h("option", { value: "PAID" }, "Paid"));
  const evidence = h("textarea.textarea", { placeholder: "What shows this? Required." });
  const button = h("button.btn.btn-primary", { type: "button" }, "Record outcome");
  action(button, out, async () => {
    await api(`/api/attempts/${attempt}/resolve`, { method: "POST", body: { outcome: outcome.value, evidence: evidence.value } });
    out.replaceChildren(notice("good", "Recorded as settled by hand. It stays labelled as manual, not confirmed."));
  });
  return h("details.card.fold", { open: true }, h("summary", "Settle by hand"), h("div.fold-body", h("label.field", "Outcome", outcome), h("label.field", "Evidence", evidence), button, out));
}

/* More */

function screenMore(view) {
  const link = (href, text, sub) => h("a.item", { href }, h("span.item-title", text), h("span.muted.small", sub));
  view.append(title("More"), h("div.more-grid",
    link("#/bills", "Bills", "Status, checks and corrections"),
    link("#/contacts", "Contacts", "Suppliers and destinations"),
    link("#/cash", "Cash", "Balances and shortfalls"),
    link("#/automations", "Automations", "Mandates"),
    link("#/expenses", "Expenses", "Versions and evidence"),
    link("#/memory", "Memory", "What PayoAI remembers"),
    link("#/notifications", "Notifications", "What needs attention")));
  const sessionCard = h("div.card", facts([["Signed in as", session.user.name], ["Role", roleName(session.team.role)], ["Team", session.team.name]]));
  if (session.teams.length > 1) {
    const select = h("select.select", { "aria-label": "Switch team" }, session.teams.map((t) => h("option", { value: t.id, selected: t.id === session.team.id }, t.name)));
    select.addEventListener("change", () => {
      session.team = session.teams.find((t) => t.id === select.value);
      store.set("payo.session", session);
      store.remove("payo.chat");
      chat.id = null;
      startApp();
    });
    sessionCard.append(h("label.field", "Team", select));
  }
  sessionCard.append(h("button.btn", { id: "sign-out", type: "button", onclick: signOut }, "Sign out"));
  view.append(sessionCard);
}

/* Bills */

const BILL_STATUSES = ["", "NEEDS_REVIEW", "BLOCKED", "PAYMENT_IN_PROGRESS", "OUTCOME_UNKNOWN", "PAID", "FAILED"];

function screenBills(view, context) {
  let status = "";
  const select = h("select.select", { "aria-label": "Filter bills" }, BILL_STATUSES.map((s) => h("option", { value: s }, s ? label(s) : "All bills")));
  view.append(title("Bills"), select);
  const load = loader(view, context, async () => {
    const rows = await api(`/api/bills${status ? `?status=${status}` : ""}`);
    if (!rows.length) return empty("No bills here. Attach an invoice in Chat to add one.");
    return rows.map((b) => h("a.item", { href: `#/bills/${b.id}` },
      h("span.item-top", h("span.item-title", b.counterparty || "Unknown supplier"), h("span.item-amount", b.amount || "")),
      h("span.badges", badge(b.status)),
      h("span.muted.small", `${b.invoice || "No reference"}${b.due_date ? ` · due ${b.due_date}` : ""}`)));
  }, 15000);
  select.addEventListener("change", () => { status = select.value; load(); });
}

function screenBill(view, context, id) {
  view.append(back("#/bills", "Bills"));
  loader(view, context, async () => {
    const bill = await api(`/api/bills/${id}`);
    return [
      h("div.card",
        h("div.head-row", h("div", h("h1", bill.counterparty || "Unknown supplier"), h("div.amount", bill.amount || "")), badge(bill.status)),
        facts([["Invoice", bill.invoice], ["Due", bill.due_date], ["Document", bill.has_document ? "Attached" : "None"]])),
      bill.decision ? h("div.card", h("div.head-row", h("h2", "Decision"), badge(bill.decision.decision)), h("p", bill.decision.explanation || "")) : null,
      bill.checks?.length ? [h("div.section-title", "Checks"), h("div.card.checks", bill.checks.map(checkRow))] : null,
      bill.payments?.length ? [h("div.section-title", "Payments"), bill.payments.map((p) => h("a.item", { href: `#/payments/${p.id}` }, h("span.item-top", h("span.mono", p.id), badge(p.status))))] : null,
    ];
  });
  if (can("OPERATOR", "APPROVER")) view.append(correctionForm(id));
}

function correctionForm(id) {
  const out = h("div");
  const field = h("select.select", ["invoice_reference", "due_date", "amount_minor", "asset"].map((f) => h("option", { value: f }, words(f))));
  const value = h("input.input", { type: "text" });
  const reason = h("input.input", { type: "text" });
  const button = h("button.btn", { type: "button" }, "Record correction");
  action(button, out, async () => {
    const result = await api(`/api/bills/${id}/manual-correction`, { method: "POST", body: { field: field.value, value: value.value, reason: reason.value } });
    out.replaceChildren(notice("good", result.note || "Recorded."));
  });
  return h("details.card.fold", h("summary", "Correct a field"), h("div.fold-body",
    h("p.muted", "This records a correction and keeps the old value. It can't change where money goes or undo a payment."),
    h("label.field", "Field", field), h("label.field", "New value", value), h("label.field", "Reason", reason), button, out));
}

/* Contacts */

function screenContacts(view, context) {
  let term = "";
  const search = h("input.input", { type: "search", placeholder: "Search by name", "aria-label": "Search contacts" });
  view.append(title("Contacts", "New bank or wallet details can't be paid until an Approver verifies them."), search);
  const load = loader(view, context, async () => {
    const rows = await api(`/api/counterparties?name=${encodeURIComponent(term)}`);
    if (!rows.length) return empty("No contacts found.");
    return rows.map((c) => h("a.item", { href: `#/contacts/${c.id}`, data: { contact: c.id } },
      h("span.item-title", c.display_name),
      c.legal_name && c.legal_name !== c.display_name ? h("span.muted.small", c.legal_name) : null,
      h("span.badges", (c.destinations || []).length ? c.destinations.map((d) => badge(d.status, `${d.masked_display}: ${label(d.status)}`)) : badge("", "No destination"))));
  });
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { term = search.value.trim(); load(); }, 250);
  });
}

async function findContact(id) {
  const rows = await api("/api/counterparties");
  const contact = rows.find((c) => c.id === id);
  if (!contact) throw new Payo.ApiError("Contact not found.", 404);
  return contact;
}

function screenContact(view, context, id) {
  view.append(back("#/contacts", "Contacts"));
  loader(view, context, async () => {
    const [contact, channels] = await Promise.all([findContact(id), api(`/api/counterparties/${id}/channels`)]);
    return [
      h("div.card", h("h1", contact.display_name), facts([["Legal name", contact.legal_name], ["Type", (contact.classifications || []).map(words).join(", ")]])),
      h("div.section-title", "Destinations"),
      contact.destinations?.length ? contact.destinations.map((d) => h("a.item", { href: `#/contacts/${id}/${d.id}`, data: { destination: d.id } },
        h("span.item-top", h("span.item-title", d.masked_display), badge(d.status)),
        h("span.muted.small", `${words(d.rail)} · ${d.asset}`))) : empty("No destinations on file."),
      h("div.section-title", "Contact channels on file"),
      channels.length ? h("div.card.checks", channels.map((c) => h("div.check", h("strong", words(c.kind)), h("span", c.value), h("span.muted.small", `Added ${time(c.added_at)}`)))) : empty("No contact channels on file, so a call-back can't be done yet."),
    ];
  });
}

const METHODS = [
  ["CALL_BACK", "Call-back on a number already on file", true],
  ["SIGNED_CONTRACT", "Signed contract already on file", true],
  ["CRYPTO_WALLET", "Wallet confirmed over a channel on file", true],
  ["NEW_RELATIONSHIP", "New relationship (weaker trust)", false],
];

function screenDestination(view, context, contactId, destinationId) {
  view.append(back(`#/contacts/${contactId}`, "Contact"));
  const body = h("div.list", notice("neutral", "Loading…"));
  view.append(body);
  (async () => {
    try {
      const [contact, channels] = await Promise.all([findContact(contactId), api(`/api/counterparties/${contactId}/channels`)]);
      const destination = (contact.destinations || []).find((d) => d.id === destinationId);
      if (!destination) throw new Payo.ApiError("Destination not found.", 404);
      if (!context.alive) return;
      // Revealing is recorded by the server, so it's read once when this screen opens and never polled.
      const full = isApprover() ? await api(`/api/destinations/${destinationId}/full`) : null;
      if (!context.alive) return;
      const status = full ? full.status : destination.status;
      const blocks = [h("div.card",
        h("div.head-row", h("div", h("h1", contact.display_name), h("p.muted", `${words(destination.rail)} · ${destination.asset}`)), badge(status)),
        full ? h("div.secret", { id: "full-details" }, h("p.muted.small", "Full details. Check every character. Viewing is recorded."),
          facts([...Object.entries(full.details || {}).map(([k, v]) => [detailName(k), h("span.mono", String(v))]), ["Chain", full.chain_id], ["Token contract", full.token_contract ? h("span.mono", full.token_contract) : null]]))
          : facts([["Destination", destination.masked_display]]),
        full?.source ? facts([["Where these details came from", words(full.source)]]) : null)];
      if (!isApprover()) blocks.push(notice("neutral", "Only an Approver can see full details, verify or revoke."));
      if (isApprover() && status === "UNVERIFIED") blocks.push(verifyForm(destinationId, channels));
      if (isApprover() && status !== "REVOKED") blocks.push(revokeForm(destinationId));
      body.replaceChildren(...blocks);
    } catch (error) {
      if (context.alive) body.replaceChildren(notice("bad", error.message));
    }
  })();
}

function verifyForm(destinationId, channels) {
  const out = h("div");
  const method = h("select.select", { id: "verify-method" }, METHODS.map(([value, text]) => h("option", { value }, text)));
  const channel = h("select.select", { id: "verify-channel" }, h("option", { value: "" }, "Choose a channel on file"), channels.map((c) => h("option", { value: c.id }, `${words(c.kind)}: ${c.value}`)));
  const channelField = h("label.field", "Channel used", channel);
  const evidence = h("textarea.textarea", { id: "verify-evidence", placeholder: "Who you spoke to, when, and what they confirmed." });
  const syncChannel = () => { channelField.hidden = !METHODS.find(([value]) => value === method.value)[2]; };
  method.addEventListener("change", syncChannel);
  syncChannel();
  const button = h("button.btn.btn-primary", { id: "verify", type: "button" }, "Verify destination");
  action(button, out, async () => {
    const required = METHODS.find(([value]) => value === method.value)[2];
    const result = await api(`/api/destinations/${destinationId}/verify`, { method: "POST", body: { method: method.value, evidence: evidence.value, channel_id: required ? channel.value || null : null } });
    button.disabled = true;
    out.replaceChildren(notice(result.same_person ? "warn" : "good", `Verified.${result.same_person ? " Added and verified by the same person, which is shown on approvals." : ""} ${result.note || ""}`));
  });
  return h("div.card", h("h2", "Verify"), h("p.muted", "Verification is done here, never in chat. Details from an invoice, chat or history don't count."),
    h("label.field", "Method", method), channelField, h("label.field", "Evidence", evidence), button, out);
}

function revokeForm(destinationId) {
  const out = h("div");
  const reason = h("input.input", { id: "revoke-reason", type: "text", placeholder: "Reason" });
  const button = h("button.btn.btn-danger-outline", { id: "revoke", type: "button" }, "Revoke destination");
  twoTap(button, "Tap again to revoke", () => run(button, out, async () => {
    const result = await api(`/api/destinations/${destinationId}/revoke`, { method: "POST", body: { reason: reason.value } });
    out.replaceChildren(notice("good", `Revoked. ${result.cancelled_approvals} pending approval${result.cancelled_approvals === 1 ? "" : "s"} cancelled.`));
  }));
  return h("details.card.fold", h("summary", "Revoke"), h("div.fold-body", h("p.muted", "Takes effect immediately and cancels pending approvals that use this destination."), h("label.field", "Reason", reason), button, out));
}

/* Cash */

function screenCash(view, context) {
  view.append(title("Cash", "Saved balances, with how fresh they are."));
  loader(view, context, async () => {
    const [{ balances, alerts }, latest] = await Promise.all([api("/api/balances"), api("/api/cash/latest")]);
    const analysis = latest?.result;
    return [
      alerts.map((a) => notice("bad", h("strong", `Shortfall of ${a.shortfall} on ${a.account}`), ` by ${a.shortfall_date}.`)),
      h("div.section-title", "Balances"),
      balances.length ? balances.map(balanceCard) : empty("No accounts."),
      h("div.section-title", "Latest analysis"),
      analysis ? analysisBlock(analysis, latest.created_at) : empty("No cash analysis yet."),
      h("p.muted", "To run a fresh analysis, ask in ", h("a", { href: "#/chat" }, "Chat"), "."),
    ];
  }, 15000);
}

function balanceCard(b) {
  if (b.balance === null || b.balance === undefined) {
    return h("div.card", h("div.head-row", h("h2", b.name), badge("UNKNOWN", "No balance")), h("p.muted", b.note || "No balance read yet."));
  }
  return h("div.card", { data: { account: b.id } },
    h("div.head-row", h("div", h("h2", b.name), h("div.amount", b.balance)), b.stale ? badge("EXPIRED", "Stale") : badge("VERIFIED", "Fresh")),
    b.stale ? notice("warn", "This balance is out of date, so it can't be relied on for a payment decision.") : null,
    facts([["Reserve", b.reserve], ["Spendable after reserve", b.spendable_after_reserve], ["Updated", `${ago(b.as_of)} (${time(b.as_of)})`]]));
}

function analysisBlock(analysis, createdAt) {
  return h("div.list",
    h("p.muted", `Run ${ago(createdAt)}, looking ahead ${analysis.days} days.`),
    (analysis.by_asset || []).map((entry) => h("div.card",
      h("div.head-row", h("div", h("h2", entry.asset), h("div.amount", entry.projected ?? "Unknown"), h("p.muted", "Projected")), entry.shortfall_date ? badge("FAILED", `Short on ${entry.shortfall_date}`) : entry.confident ? badge("VERIFIED", "No shortfall") : badge("UNKNOWN", "Not confident")),
      entry.note ? notice("warn", entry.note) : null,
      entry.driving_bills?.length ? h("div.badges", h("span.muted.small", "Caused by:"), entry.driving_bills.map((id) => h("a.small", { href: `#/bills/${typeof id === "string" ? id : id.id}` }, "bill"))) : null,
      entry.expected_income ? h("p.muted.small", `Expected income: ${entry.expected_income}`) : null)));
}

/* Automations */

function screenAutomations(view, context) {
  view.append(title("Automations", "A Mandate limits what may be prepared automatically. Every payment still needs approval."));
  const out = h("div");
  view.append(out);
  const load = loader(view, context, async () => {
    const rows = await api("/api/mandates");
    if (!rows.length) return [empty("No Mandates yet."), h("p.muted", "Ask in ", h("a", { href: "#/chat" }, "Chat"), " to set one up.")];
    return rows.map((m) => {
      const change = (verb, done) => async () => {
        await api(`/api/mandates/${m.id}/${verb}`, { method: "POST", body: {} });
        out.replaceChildren(notice("good", `${m.counterparty}: ${done}.`));
        await load();
      };
      const buttons = [];
      if (isApprover() && m.status !== "REVOKED") {
        if (m.status !== "ACTIVE") buttons.push(action(h("button.btn.btn-small.btn-primary", { type: "button", data: { action: "activate" } }, "Activate"), out, change("activate", "activated")));
        if (m.status === "ACTIVE") buttons.push(action(h("button.btn.btn-small", { type: "button", data: { action: "pause" } }, "Pause"), out, change("pause", "paused")));
        const revoke = h("button.btn.btn-small.btn-danger-outline", { type: "button", data: { action: "revoke" } }, "Revoke");
        twoTap(revoke, "Tap again to revoke", () => run(revoke, out, change("revoke", "revoked")));
        buttons.push(revoke);
      }
      return h("div.card", { data: { mandate: m.id } },
        h("div.head-row", h("h2", m.counterparty), badge(m.status)),
        facts([
          ["Purpose", m.purpose],
          ["Maximum", m.max_amount ?? minorUnits(m.max_amount_minor, m.asset)],
          ["Valid until", m.valid_until ? time(m.valid_until) : null],
          ["Frequency", m.frequency ? words(m.frequency) : null],
          ["Next due", m.next_due],
        ]),
        buttons.length ? h("div.btn-row", buttons) : null);
    });
  }, 15000);
}

/** The API sent only raw minor units here. They're shown as they are, because the app doesn't calculate money. */
const minorUnits = (value, asset) => (value === null || value === undefined ? null : `${value} ${asset || ""} minor units`.replace("  ", " "));

/* Expenses */

const ACCOUNTANT_LABEL = "Prepared for accountant review, not certified compliant.";

function screenExpenses(view, context) {
  view.append(title("Expenses"), notice("info", ACCOUNTANT_LABEL));
  loader(view, context, async () => {
    const rows = await api("/api/expenses");
    if (!rows.length) return empty("No expenses yet.");
    return rows.map((e) => h("a.item", { href: `#/expenses/${e.id}`, data: { expense: e.id } },
      h("span.item-top", h("span.item-title", e.counterparty || "Unknown"), h("span.item-amount", e.gross || "")),
      h("span.badges", badge(e.status), badge("", `Version ${e.version}`)),
      h("span.muted.small", [e.category ? `${e.category}${e.category_is_suggestion ? " (suggested)" : ""}` : "No category", e.purpose].filter(Boolean).join(" · "))));
  }, 15000);
}

function screenExpense(view, context, id) {
  view.append(back("#/expenses", "Expenses"));
  loader(view, context, async () => {
    const [detail, list] = await Promise.all([api(`/api/expenses/${id}`), api("/api/expenses")]);
    const summary = list.find((e) => e.id === id);
    const latest = detail.versions[detail.versions.length - 1];
    return [
      notice("info", detail.label || ACCOUNTANT_LABEL),
      h("div.card",
        h("div.head-row", h("div", h("h1", summary?.counterparty || "Expense"), h("div.amount", summary?.gross || "")), badge(latest.status)),
        facts([
          ["Category", latest.category ? `${latest.category}${latest.category_is_suggestion ? " (suggested, not confirmed)" : ""}` : null],
          ["Purpose", latest.business_purpose],
          ["Period", latest.accounting_period],
          ["Payment", latest.intent_id ? h("a", { href: `#/payments/${latest.intent_id}` }, "View trace") : null],
          ["Bill", latest.obligation_id ? h("a", { href: `#/bills/${latest.obligation_id}` }, "View bill") : null],
        ])),
      detail.exceptions?.length ? [h("div.section-title", "Exceptions"), detail.exceptions.map((e) => notice("warn", h("strong", `${words(e.kind)}: `), label(e.status), e.details ? ` · ${typeof e.details === "string" ? e.details : JSON.stringify(e.details)}` : ""))] : null,
      h("div.section-title", "Versions"),
      h("div.card.checks", { id: "expense-versions" }, [...detail.versions].reverse().map((v) => h("div.check",
        h("div.head-row", h("strong", `Version ${v.version}`), badge(v.status)),
        h("span", v.reason || ""),
        h("span.muted.small", `${words(v.actor)} · ${time(v.created_at)}${v.category ? ` · ${v.category}` : ""}`),
        h("span.muted.small", [["Gross", v.gross ?? minorUnits(v.gross_minor, v.asset)], ["Net", v.net ?? minorUnits(v.net_minor, v.asset)], ["Tax", v.tax ?? minorUnits(v.tax_minor, v.asset)]].filter(([, x]) => x).map(([k, x]) => `${k}: ${x}`).join(" · "))))),
      detail.journal?.length ? [h("div.section-title", "Proposed journal"), h("div.card.checks", detail.journal.map((j) => h("div.check",
        h("div.head-row", h("strong", words(j.kind)), badge(j.reviewed ? "VERIFIED" : "PENDING", j.reviewed ? "Reviewed" : "Not reviewed")),
        (j.lines || []).map((line) => h("span.small", `${line.account}: debit ${line.debit ?? minorUnits(line.debit_minor, line.asset)}, credit ${line.credit ?? minorUnits(line.credit_minor, line.asset)}`)))))] : null,
    ];
  });
  if (can("OPERATOR", "APPROVER")) view.append(evidenceForm(id));
}

function evidenceForm(id) {
  const out = h("div");
  const file = h("input", { type: "file", id: "evidence-file", accept: ".pdf,.txt,.png,.jpg,.jpeg,application/pdf,text/plain,image/png,image/jpeg", "aria-label": "Evidence document" });
  const button = h("button.btn", { type: "button", id: "evidence-add" }, "Attach evidence");
  action(button, out, async () => {
    if (!file.files[0]) {
      out.replaceChildren(notice("warn", "Choose a file first."));
      return;
    }
    const form = new FormData();
    form.append("file", file.files[0]);
    const doc = await api("/api/documents", { method: "POST", form });
    await api(`/api/expenses/${id}/evidence`, { method: "POST", body: { document_id: doc.id } });
    file.value = "";
    out.replaceChildren(notice("good", "Evidence added. A new version appears once it's been processed."));
  });
  return h("div.card", h("h2", "Evidence"), h("p.muted", "Attach a receipt or invoice. It's checked and adds a new version."), file, button, out);
}

/* Memory */

function screenMemory(view, context) {
  view.append(title("Memory", "Preferences and business context. They can shape suggestions, never a decision, and give no authority."));
  const out = h("div");
  const auto = h("div");
  view.append(auto, out);
  const drawAuto = async () => {
    const controls = await api("/api/controls");
    const on = Boolean(controls?.auto_mode);
    const kids = [h("div.head-row", h("div", h("h2", "Auto Mode"), h("p.muted", "Learns Memory without asking you to confirm each one.")), badge(on ? "ACTIVE" : "", on ? "On" : "Off"))];
    if (isApprover()) {
      kids.push(action(h("button.btn", { id: "auto-mode", type: "button" }, on ? "Turn off" : "Turn on"), out, async () => {
        await api("/api/controls/auto-mode", { method: "POST", body: { on: !on } });
        await drawAuto();
      }));
    }
    auto.replaceChildren(h("div.card", kids));
  };
  drawAuto().catch((error) => out.replaceChildren(notice("bad", error.message)));
  const load = loader(view, context, async () => {
    const rows = await api("/api/memories");
    if (!rows.length) return empty("Nothing saved yet.");
    return rows.map((m) => h("div.card", { data: { memory: m.id } },
      h("div.head-row", h("h2", m.subject || words(m.type)), badge("", words(m.type))),
      h("p", typeof m.value === "string" ? m.value : JSON.stringify(m.value)),
      h("p.muted.small", `${words(m.origin || "unknown")} · ${m.user_id ? "Just you" : "Whole team"} · seen ${m.evidence_count ?? 0} time${m.evidence_count === 1 ? "" : "s"} · version ${m.version} · ${time(m.created_at)}`),
      can("OPERATOR", "APPROVER") ? twoTap(h("button.btn.btn-small", { type: "button", data: { action: "delete" } }, "Delete"), "Tap again to delete", async () => {
        try {
          await api(`/api/memories/${m.id}`, { method: "DELETE" });
          await load();
        } catch (error) {
          out.replaceChildren(notice("bad", error.message));
        }
      }) : null));
  });
}

/* Notifications */

const NOTIFICATION_TEXT = {
  PAYMENT_READY: "A payment is ready for approval.",
  PAYMENT_APPROVED: "A payment was approved.",
  PAYMENT_REJECTED: "A payment was rejected.",
  PAYMENT_FAILED: "A payment failed.",
  PAYMENT_STOPPED: "A payment was stopped before it was sent.",
  PAYMENT_MISMATCH: "A payment's outcome doesn't match what was approved.",
  PAYMENT_OUTCOME_UNKNOWN: "A payment's outcome is unknown. Don't pay it again.",
  PAYMENT_NEEDS_MANUAL_REVIEW: "A payment needs settling by hand.",
  PAYMENT_REJECTED_BY_PROVIDER: "The provider rejected a payment.",
  CRITICAL_LATE_CONFIRMATION: "A payment thought to have failed was later confirmed.",
  CASH_SHORTFALL: "A cash shortfall is forecast.",
  DESTINATION_REVOKED: "A destination was revoked.",
  EXPENSE_NEEDS_REVIEW: "An expense needs review.",
  MISSING_DOCUMENT: "A document is missing.",
  MISSING_DOCUMENT_REMINDER: "Reminder: a document is still missing.",
  PURCHASE_READY: "A purchase is ready for approval.",
};
const BAD_NOTIFICATIONS = ["PAYMENT_FAILED", "PAYMENT_STOPPED", "PAYMENT_MISMATCH", "PAYMENT_REJECTED", "PAYMENT_REJECTED_BY_PROVIDER", "CRITICAL_LATE_CONFIRMATION", "CASH_SHORTFALL", "DESTINATION_REVOKED"];
const WARN_NOTIFICATIONS = ["PAYMENT_READY", "PAYMENT_OUTCOME_UNKNOWN", "PAYMENT_NEEDS_MANUAL_REVIEW", "EXPENSE_NEEDS_REVIEW", "MISSING_DOCUMENT", "MISSING_DOCUMENT_REMINDER", "PURCHASE_READY"];

function notificationLink(n) {
  const p = n.payload || {};
  if (p.approval_id) return `#/approvals/${p.approval_id}`;
  if (p.intent_id) return `#/payments/${p.intent_id}`;
  if (p.expense_id) return `#/expenses/${p.expense_id}`;
  if (n.kind === "CASH_SHORTFALL") return "#/cash";
  return null;
}

function screenNotifications(view, context) {
  view.append(title("Notifications"));
  loader(view, context, async () => {
    const rows = await api("/api/notifications");
    if (!rows.length) return empty("Nothing yet.");
    return rows.map((n) => {
      const toneName = BAD_NOTIFICATIONS.includes(n.kind) ? "FAILED" : WARN_NOTIFICATIONS.includes(n.kind) ? "PENDING" : "VERIFIED";
      const kids = [h("span.item-top", h("span.item-title", NOTIFICATION_TEXT[n.kind] || words(n.kind)), badge(toneName, words(n.kind.replace(/^PAYMENT_/, "")))), h("span.muted.small", time(n.created_at))];
      const href = notificationLink(n);
      return href ? h("a.item", { href, data: { notification: n.id } }, kids) : h("div.item", { data: { notification: n.id } }, kids);
    });
  }, 15000);
}

/* Start */

window.addEventListener("hashchange", route);
startApp();
