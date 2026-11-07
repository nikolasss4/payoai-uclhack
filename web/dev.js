/* The Developer Console: stored eval runs, replay of stored traces, and starting new runs. Synthetic eval teams only. */

const { h, store, client, badge, words, time, notice, facts } = Payo;

const UNLOCK_PHRASE = "I am running the locked held-out set";
const DEV_EMAIL = "dev@payoai.example";

const main = document.getElementById("main");
const topbar = document.getElementById("topbar");
let token = store.get("payo.dev.token");
const api = client(() => (token ? { Authorization: `Bearer ${token}` } : {}));
let stopTimer = () => {};

function live(fn, ms) {
  const timer = setInterval(() => { if (!document.hidden) fn(); }, ms);
  stopTimer = () => clearInterval(timer);
}

function show(...nodes) {
  stopTimer();
  stopTimer = () => {};
  const view = h("div.view", nodes);
  main.replaceChildren(view);
  main.scrollTop = 0;
  return view;
}

function drawTopbar() {
  const kids = [h("span.brand", "Developer Console")];
  kids.push(h("span.who"));
  if (token) kids.push(h("button.btn.btn-small", { type: "button", id: "dev-sign-out", onclick: signOut }, "Sign out"));
  else kids.push(h("a.btn.btn-small", { href: "/" }, "App"));
  topbar.replaceChildren(...kids);
}

function signOut() {
  token = null;
  store.remove("payo.dev.token");
  route();
}

function screenSignIn() {
  const out = h("div");
  const button = h("button.btn.btn-primary", { id: "dev-sign-in", type: "button" }, `Sign in as ${DEV_EMAIL}`);
  button.addEventListener("click", async () => {
    try {
      const session = await client(() => ({}))("/api/session", { method: "POST", body: { email: DEV_EMAIL } });
      if (!session.developer) throw new Payo.ApiError("That user doesn't have developer access.", 403);
      token = session.token;
      store.set("payo.dev.token", token);
      route();
    } catch (error) {
      out.replaceChildren(notice("bad", error.message));
    }
  });
  show(h("h1", "Developer Console"), h("p.muted", "Browse eval runs and traces. It only reads synthetic eval teams."), out, button);
}

/* Screens: #/ is the run list, #/run/ID a run, #/replay and #/new. */

function route() {
  drawTopbar();
  if (!token) return screenSignIn();
  const hash = location.hash;
  const run = hash.match(/^#\/run\/([\w-]+)$/);
  if (run) return screenRun(run[1]);
  const replay = hash.match(/^#\/replay(?:\/([\w-]+))?$/);
  if (replay) return screenReplay(replay[1]);
  if (hash === "#/new") return screenNew();
  return screenRuns();
}

const back = () => h("a.back", { href: "#/" }, "‹ Eval runs");
const modeLabel = (kind) => (kind === "replay"
  ? notice("info", h("strong", "Replay. "), "Shows stored steps. It calls nothing and changes nothing.")
  : notice("warn", h("strong", "Re-run. "), "Calls the model again and makes a new record. It can cost money."));

function runSummaryLine(run) {
  const s = run.summary;
  if (!s) return "No summary yet: still running, or it didn't finish.";
  return `${s.passed ?? "?"} of ${s.cases ?? "?"} passed · unsafe executions: ${s.unsafe_executions ?? "?"}`;
}

function runTone(run) {
  const s = run.summary;
  if (!s) return ["RUNNING", "No summary"];
  return s.passed === s.cases && !s.unsafe_executions ? ["PASS", "All passed"] : ["FAILED", "Has failures"];
}

function screenRuns() {
  const list = h("div.list", notice("neutral", "Loading…"));
  const problem = h("div");
  show(
    h("h1", "Eval runs"),
    h("div.btn-row", h("a.btn.btn-primary", { id: "dev-new", href: "#/new" }, "Start a run"), h("a.btn", { id: "dev-replay", href: "#/replay" }, "Replay traces")),
    problem,
    list,
  );
  const load = async () => {
    try {
      const runs = await api("/api/dev/runs");
      problem.replaceChildren();
      list.replaceChildren(...(runs.length ? runs.map((run) => {
        const [code, text] = runTone(run);
        return h("a.item", { href: `#/run/${run.id}`, data: { run: run.id } },
          h("span.item-top", h("span.item-title", `${words(run.suite)} · ${run.system}`), badge(code, text)),
          h("span.badges", badge(run.split === "heldout" ? "NEEDS_REVIEW" : "", `Split: ${run.split}`)),
          h("span.muted.small", runSummaryLine(run)),
          h("span.muted.small", time(run.created_at)));
      }) : [notice("neutral", "No eval runs yet.")]));
    } catch (error) {
      if (error.status === 401 || error.status === 403) return signOut();
      problem.replaceChildren(notice("bad", error.message));
    }
    return undefined;
  };
  load();
  live(load, 6000);
}

const SUMMARY_FIELDS = [
  ["cases", "Cases"], ["passed", "Passed"], ["errors", "Errors"], ["unsafe_executions", "Unsafe executions"],
  ["paid_when_it_should_not", "Paid when it shouldn't"], ["duplicate_or_unauthorised", "Duplicate or unauthorised"],
  ["wrong_recipient", "Wrong recipient"], ["outcome_truthful", "Outcome truthful"], ["false_blocks_or_escalations", "False blocks or escalations"],
  ["legitimate_completed", "Legitimate completed"], ["legitimate_cases", "Legitimate cases"], ["cost_usd_per_case", "Cost per case (USD)"],
  ["cost_usd_total", "Cost in total (USD)"], ["latency_ms_median", "Median latency (ms)"], ["latency_ms_p95", "p95 latency (ms)"],
];

function screenRun(id) {
  const body = h("div.list", notice("neutral", "Loading…"));
  show(back(), modeLabel("replay"), body);
  const load = async () => {
    try {
      const run = await api(`/api/dev/runs/${id}`);
      const summary = run.summary || {};
      const [code, text] = runTone(run);
      const failed = run.results.filter((r) => !r.passed);
      const results = [...failed, ...run.results.filter((r) => r.passed)];
      const open = new Set([...body.querySelectorAll("details[open]")].map((d) => d.dataset.case));
      body.replaceChildren(
        h("div.card",
          h("div.head-row", h("h1", `${words(run.suite)} · ${run.system}`), badge(code, text)),
          facts([["Split", run.split], ["Started", time(run.created_at)], ["Started by", run.started_by || "Script"], ["Provider", run.config?.provider || summary.provider], ["Results stored", String(run.results.length)]]),
          run.summary ? facts(SUMMARY_FIELDS.filter(([key]) => summary[key] !== undefined && summary[key] !== null).map(([key, name]) => [name, String(summary[key])])) : h("p.muted", "No summary yet. It's still running or it didn't finish.")),
        h("div.section-title", `Results: ${failed.length} failed of ${run.results.length}`),
        ...results.map((result) => h("details.item.fold", { open: open.has(result.case_id), data: { case: result.case_id } },
          h("summary", h("span.item-top", h("span.mono", result.case_id), badge(result.passed ? "PASS" : "FAILED", result.passed ? "Passed" : "Failed"))),
          h("p.muted.small", `Latency ${result.latency_ms ?? "?"} ms · cost ${result.cost_micros ?? 0} micro-USD`),
          result.team_id ? h("a.small", { href: `#/replay/${result.team_id}` }, "Replay this team's trace") : null,
          h("pre.code", JSON.stringify(result.details, null, 1)))),
      );
    } catch (error) {
      body.replaceChildren(notice("bad", error.message));
    }
  };
  load();
  live(load, 8000);
}

function screenReplay(teamId) {
  const input = h("input.input", { id: "replay-team", type: "text", placeholder: "Eval team ID, such as evalteam_…", value: teamId || "", autocapitalize: "off", spellcheck: false });
  const go = h("button.btn.btn-primary", { id: "replay-go", type: "button" }, "Replay stored trace");
  const body = h("div.list");
  show(back(), h("h1", "Replay traces"), modeLabel("replay"), h("label.field", "Eval team", input), go, body);
  const load = async () => {
    const team = input.value.trim();
    if (!team) {
      body.replaceChildren(notice("warn", "Enter an eval team ID."));
      return;
    }
    body.replaceChildren(notice("neutral", "Loading…"));
    try {
      const steps = await api(`/api/dev/traces/${encodeURIComponent(team)}`);
      body.replaceChildren(...drawTrace(steps));
    } catch (error) {
      body.replaceChildren(notice("bad", error.message));
    }
  };
  go.addEventListener("click", load);
  if (teamId) load();
}

function drawTrace(steps) {
  if (!steps.length) return [notice("neutral", "No steps stored for this team.")];
  return [
    h("div.section-title", `${steps.length} stored steps`),
    ...steps.map((step) => h("details.item.fold", { data: { step: step.id } },
      h("summary", h("span.item-top", h("span.item-title", step.name), badge(step.status))),
      h("p.muted.small", [words(step.step_type), time(step.started_at), step.trust_label, step.model].filter(Boolean).join(" · ")),
      step.error ? notice("bad", step.error) : null,
      h("div.section-title", "Input"), h("pre.code", JSON.stringify(step.input, null, 1)),
      h("div.section-title", "Output"), h("pre.code", JSON.stringify(step.output, null, 1)),
      step.input_tokens || step.output_tokens ? h("p.muted.small", `Tokens ${step.input_tokens ?? 0} in, ${step.output_tokens ?? 0} out · ${step.cost_micros ?? 0} micro-USD`) : null)),
  ];
}

function screenNew() {
  const out = h("div");
  const options = (id, values) => h("select.select", { id }, values.map((value) => h("option", { value }, value)));
  const suite = options("run-suite", ["routines", "faults", "full_agent"]);
  const split = options("run-split", ["dev", "validation", "heldout"]);
  const system = options("run-system", ["payoai", "rules_only", "llm_only"]);
  const caseId = h("input.input", { id: "run-case", type: "text", placeholder: "Optional: one case ID", autocapitalize: "off", spellcheck: false });
  const phrase = h("input.input", { id: "run-unlock", type: "text", autocomplete: "off", autocapitalize: "off", spellcheck: false });
  phrase.addEventListener("paste", (event) => event.preventDefault());
  const unlock = h("div.card", { id: "run-unlock-box", hidden: true },
    notice("bad", h("strong", "The held-out set is locked. "), "Running it spends it. To unlock this run, type this exactly:"),
    h("p.mono", UNLOCK_PHRASE),
    h("label.field", "Unlock phrase", phrase));
  const start = h("button.btn.btn-primary", { id: "run-start", type: "button" }, "Re-run: start a new run");
  const sync = () => {
    const held = split.value === "heldout";
    unlock.hidden = !held;
    start.disabled = held && phrase.value !== UNLOCK_PHRASE;
  };
  split.addEventListener("change", sync);
  phrase.addEventListener("input", sync);
  sync();
  start.addEventListener("click", async () => {
    start.disabled = true;
    const held = split.value === "heldout";
    try {
      const run = await api("/api/dev/runs", { method: "POST", body: { suite: suite.value, split: split.value, system: system.value, case_id: caseId.value.trim() || null, unlock_heldout: held ? phrase.value : null } });
      location.hash = `#/run/${run.id}`;
    } catch (error) {
      out.replaceChildren(notice("bad", error.message));
      sync();
    }
  });
  show(back(), h("h1", "Start a run"), modeLabel("rerun"),
    h("label.field", "Suite", suite), h("label.field", "Split", split), h("label.field", "System", system), h("label.field", "Case", caseId), unlock, out, start);
}

window.addEventListener("hashchange", route);
route();
