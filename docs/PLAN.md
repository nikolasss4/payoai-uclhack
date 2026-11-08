# PayoAI build plan

The complete plan for the UCL AgentVerse 2026 Finance track. One builder, due Sunday 8 November 2026.

Related documents:

- `CONTEXT.md`: glossary
- `docs/routines.md`: every Routine, Node by Node
- `docs/traces.md`: chats, Turns, traces and the Developer Console
- `docs/evaluation.md`: test suites and baselines
- `docs/adr/`: decisions that are hard to reverse

Where this plan and those documents overlap, those documents hold the detail.

**Status:** this began as the plan and has been updated to match the code at commit `d94d88b`. Section 15 lists every place the build differs from the original plan. Section 13 is kept as the original schedule, and isn't a log of what happened.

## 1. Product

**User:** a UK small business owner with no finance team.

**Decision:** "Is it safe to pay this bill now, and can we afford it?"

**Failure that hurts them:** paying a fraudster's changed bank details, paying twice, or missing a payment because cash ran short.

**Scope:** the target was all 18 Routines in `docs/routines.md`, Airwallex, Wise and Circle rails, four evaluation suites and the Developer Console. What exists is 18 Routines at the statuses listed in `docs/routines.md`, the simulator as the only rail, three evaluation suites, and the Developer Console as API routes.

**Hard limits:** synthetic data only, clearly labelled. No real money, real records, real account details or logins. A human approves anything that moves money.

## 2. Stack

| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.12, managed by `uv` | The machine's default Python is 3.9, which is too old |
| API | FastAPI, with server-sent events for live updates | |
| Database | Postgres | Also holds the job queue and LangGraph checkpoints. See section 3. |
| Database access | psycopg 3 with explicit SQL, plus numbered SQL migrations | Conditional updates stay visible and reviewable |
| Main Loop | A plain Python loop | Gemini's API stores the conversation. See the note in ADR 0001. |
| Routines | LangGraph with the Postgres checkpointer | ADR 0001 |
| Money path | Plain Python and a job queue | ADR 0001 |
| Models | `google-genai` 2.3.0 or later, Interactions API | ADR 0002 |
| Typed data | Pydantic v2 for API bodies and Routine inputs | LLM output is requested against a JSON schema and parsed as JSON. It isn't re-validated with Pydantic. |
| Crypto | Simulated only | `web3.py` isn't a dependency, and nothing is signed or broadcast |
| PDF text | `pypdf` | Lets code confirm that extraction quotes appear in the document |
| Scripted checkout | Fixed HTTP calls with `httpx` against the test shop | Playwright isn't used, because the test shop is the only supported merchant |
| Frontend | React and TypeScript in `frontend/`, built with Vite into `web/dist` and served by the API. Web and mobile layouts from the v2 designs | Next.js isn't used. There's no server rendering, so the API stays the only server |
| Tests | `pytest` | |

## 3. Infrastructure

### Runs locally for the demo

Docker, Postgres and `gcloud` aren't installed on this machine, and the guide says not to spend the weekend on deployment. The demo runs on the laptop, with a screen recording as the backup.

**Postgres:** Supabase, through its IPv4 pooler, in a private `payoai` schema that its web API does not expose. Connections turn off prepared statements for the pooler. It holds synthetic data only. Tests use a separate schema, which they drop and recreate.

**Migrations:** numbered SQL files in `migrations/`, applied in order, once each, by `payoai.db.migrate`.

**Hosting:** Cloud Run and Cloud SQL are optional and only considered after the final results are in.

### Processes

```text
Static web page (web/)
      |  HTTP + server-sent events
      v
api ---------------- chats, Turns, approvals, webhooks, Developer Console routes
      |
      |  Postgres: records, jobs, checkpoints, traces
      |
      +-- agent-worker -- Main Loop, Routines, category suggestions, Auto Mode, eval runs
      |                   Holds: Gemini key. No payment secrets.
      |
      +-- money-worker -- Execute Payment, Reconcile Payment and Purchase Checkout's final steps
      |                   Holds: provider keys when real rails exist. Calls no LLM.
      |
      +-- scheduler ----- Occurrences, daily Cash Analysis, missing-document reminders, stale job recovery
      |
      +-- provider adapter: Simulator only. Airwallex, Wise and Circle aren't built.

merchant-site -------- test shop for Purchase Checkout
```

| Process | Holds | Can't do |
|---|---|---|
| `api` | Database access | Call providers to send money, or read provider or signing keys |
| `agent-worker` | Gemini key | Read provider or signing keys. It has no handler for Execute Payment. |
| `money-worker` | Provider keys, once they exist | Its job handlers call no LLM, and it doesn't load the Gemini key |
| `scheduler` | Database access | Do the work itself. It only creates jobs. |

Each process loads its own env file, so a compromised agent process has no payment secret to reach. All processes share one `DATABASE_URL`, so this separation covers secrets, not database rights. A compromised process could write to any table. Real use would need a separate database role per process.

### Job queue

One Postgres table. Workers claim jobs using `SELECT ... FOR UPDATE SKIP LOCKED`.

A job created by an approval is saved in the same transaction as the approval. `dedupe_key` is unique, so a repeat webhook or timer can't create a second job.

Job kinds: `run_turn`, `run_routine`, `fire_occurrence`, `cash_analysis`, `suggest_category`, `attach_evidence`, `learn_memory` and `run_eval` for `agent-worker`, and `execute_payment`, `reconcile_payment` and `execute_checkout` for `money-worker`. Answering a Waiting Run resumes the Routine directly, so there is no `resume_routine` job.

## 4. Repository layout

```text
PayoAI/
  CONTEXT.md
  README.md
  pyproject.toml
  .env.example                 names only, no values
  docs/
  deck/                        judging deck, pitch script and data notes
  migrations/                  numbered SQL files
  scripts/                     dev.sh, smoke.py
  src/payoai/
    api/                       routes, sessions, server-sent events, webhooks
    domain/                    pure money and hash code, and combining check results, no I/O
    db/                        connection pool and migrations
    trace/                     Trace Steps and Audit Events
    queue/                     claiming and creating jobs
    agent/
      main_loop.py
      read_tools.py
      chips.py                 chip rendering and the reply check
      llm.py                   LLM calls for Nodes (Main Loop, Purchase Research and the LLM-only baseline use its client directly)
      config.py                model and thinking level per step
    routines/
      nodes/                   shared Nodes, including fiat and crypto checks
      graphs/                  payment.py and others.py hold every LangGraph definition
      engine.py                starts and resumes runs
      inputs.py                what the Main Loop may pass to each Routine
      registry.py              Routine config, including Start Confirmation
    money/                     Execute Payment, Reconcile Payment, approval and Record Expense, no LangGraph
    providers/                 base interface and simulator
    compliance/rules/          versioned UK rules
    memory/
    workers/                   agent_worker, money_worker, scheduler
  merchant_site/
  web/                         static HTML and JavaScript
  evals/
    cases/                     dev, validation and heldout .jsonl files, and MANIFEST.json
    generators/
    baselines/
    judges.py
    layouts.py
    runner.py
  tests/
```

Prompts are written inline in the code that uses them, not in versioned files. Only the Main Loop records a prompt version.

## 5. Data model

Every business table has `team_id`. Money is stored as an integer in the asset's smallest unit with its asset code, never as a float.

| Group | Tables |
|---|---|
| **Access** | `users`, `teams`, `memberships` (Viewer, Operator, Approver), `platform_roles` (Developer) |
| **Conversation** | `chats`, `turns`, `task_list_items`, `chat_events` |
| **Routines** | `routine_runs`, `run_questions`, `start_confirmations`, and LangGraph's own checkpoint tables |
| **Trace** | `trace_steps`, `audit_events` |
| **Contacts** | `counterparties`, `payment_destinations`, `destination_verifications`, `contact_channels` |
| **Bills** | `documents`, `extractions`, `field_corrections`, `obligations` |
| **Checks** | `check_results`, `decisions`, `investigations` |
| **Payments** | `payment_intents`, `approvals`, `payment_attempts`, `external_events` |
| **Cash** | `financial_accounts`, `balance_snapshots`, `cash_analyses`, `alerts`, `team_controls` (Pause All Payments) |
| **Recurring** | `templates`, `automation_rules`, `mandates`, `occurrences` |
| **Accounting** | `expense_versions`, `journal_proposals`, `exceptions` |
| **Purchases** | `research_reports`, `research_sources`, `purchase_summaries` |
| **Memory** | `memories` |
| **Simulator** | `sim_transfers` (its ledger), `sim_balances`, `sim_faults` |
| **Other** | `jobs`, `notifications`, `eval_runs`, `eval_results`, `schema_migrations` |

The `templates` table exists, but no code uses it yet.

### Rules the database enforces

| Rule | How |
|---|---|
| One active attempt per payment | Partial unique index on `payment_attempts(intent_id)` while the attempt is active |
| One live payment per bill, and one open approval per payment | Partial unique indexes on `payment_intents(obligation_id)` and `approvals(intent_id)` |
| One Occurrence per Mandate and period | Unique `occurrences(mandate_id, period)` |
| One idempotency key per team, provider and operation | Unique index on `payment_attempts` |
| One step running per Chat | Partial unique index on `routine_runs(chat_id)` while `running` |
| One duplicate-safe job | Unique `jobs(dedupe_key)` |
| Unique invoice per supplier | Unique index excluding Superseded Bills |
| Append-only audit and expense history | Database trigger rejects `UPDATE` and `DELETE` |
| One exception per expense | Unique index on open exceptions |
| An Approval bound to one payment hash | A trigger rejects any change to an approval's hash or payment |

### Approval

An approval is one statement inside one transaction, in `src/payoai/money/approval.py`. See `docs/routines.md`, "Approval".

```sql
UPDATE approvals
SET status = :decision, decided_by = :user_id, decided_at = now(), reason = :reason
WHERE id = :approval_id
  AND team_id = :team_id
  AND status = 'PENDING'
  AND payload_hash = :hash_shown_on_screen
  AND (expires_at > now() OR :decision = 'REJECTED')
  AND EXISTS (approver role still held by :user_id)
  AND (:decision = 'REJECTED' OR NOT EXISTS (Pause All Payments is on for :team_id))
RETURNING id, intent_id;
```

A rejection is still allowed after expiry and during a pause. If an approval's row comes back, the same transaction queues `execute_payment`. If not, the API reads the record and says why.

## 6. Agent

### Main Loop

`agent/main_loop.py` is a plain loop of up to 10 steps, calling Argon with the client from `agent/llm.py`. It isn't a LangGraph graph, because Gemini's API stores the conversation with `previous_interaction_id`, so there is no conversation state to checkpoint. Every input and output is saved as a Trace Step.

Code adds this chat's Waiting Runs, task list, Start Confirmation cards, recent runs and active Memory at the end of the latest input, labelled as data. Stable instructions stay at the start, so implicit caching works.

| Tool | Effect |
|---|---|
| Read Tools | Bills, payments, counterparties, balances, expenses, run details, compliance rules, and document text labelled as untrusted |
| `start_routine(name, inputs)` | Starts a Routine, or returns a card if it needs a Start Confirmation. It returns when the Routine finishes or pauses. |
| `answer_run(run_id, value)` | Answers a Waiting Run's question. The Routine rejects anything outside the allowed answers. |
| `update_task_list(items)` | Saves the plan for multi-part requests. It gives no authority. |

The Main Loop has no tool for approval, verification, execution or changing check results. Team and role come from the session, never from tool inputs. Bank and wallet details are refused in every Routine's inputs except Add Counterparty, which saves them as `UNVERIFIED`, so they can't be paid.

### Reply handling

1. The Main Loop cites records as `[[payment:pay_123]]`.
2. `chips.py` checks the reply: numbers and dates must appear in this Turn's tool results, words like "paid" or "confirmed" must match the record's status, and record claims need a citation.
3. A failed check regenerates once. A second failure shows the raw records.
4. Code renders each citation as a Chip with live values.

### Routines

- **Nodes:** each Node is wrapped so a Trace Step is saved when it starts and updated when it ends. If that save fails, the Node doesn't run.
- **Pauses:** pauses for questions live in their own small Node, because resuming restarts a paused Node from its beginning.
- **Source of truth:** `routine_runs.status` is authoritative, and LangGraph checkpoints only track progress.
- **Handing over money:** a Routine's graph ends after Prepare Payment, and the run is marked `AWAITING_APPROVAL`. Routines never submit a payment to a provider.
- **LLM Nodes:** Extract Fields, Investigate and Explain ask for JSON matching a schema. They have a timeout and one retry. Anything other than a completed result with valid JSON is a failed step.
- **Investigate:** code gathers the related records and the model gets them in one call, with no tool loop. A finding that cites no known record is dropped.
- **Decisions:** check and decision Nodes never receive Memory or LLM output as authority.

### Models

See ADR 0002. All model settings live in `agent/config.py` and are recorded on each Trace Step.

### Untrusted content

Documents, web pages and provider text are data. They're labelled when shown to a model and never reach the check or decision Nodes as instructions. Purchase Research receives no business data, and its `fetch_page` tool only opens URLs from search results and blocks internal addresses.

## 7. Payments

### Provider interface

The simulator implements balances, submission, status lookup and webhook verification, and declares capability flags. `get_provider` returns the simulator for every provider name.

| Adapter | Purpose | Status |
|---|---|---|
| `simulator` | Default for development and the benchmark. Injects timeouts, server errors, rejections, pending states, failures after pending, wrong settled amounts, an unreachable status and stale balances. | Built |
| `airwallex` | Real sandbox | Not built |
| `wise` | Real sandbox | Not built |
| `circle` | Testnet USDC, signed locally by `money-worker` | Not built |

Every attempt carries an evidence label, which is always `SIMULATED` today. The simulator charges no fee. The fee on a payment is a fixed maximum in code (`fee_cap`), which only reserves cash.

### Payment hash

`domain/` builds the canonical payment (team, source, destination and its details, rail, provider, amount, asset, chain and token if crypto, maximum fee, obligation and reference) and hashes it with SHA-256. Execute Payment recomputes the hash before sending.

Execution and reconciliation rules are in `docs/routines.md`.

## 8. API

| Area | Endpoints |
|---|---|
| **Session** | Sign in, list teams, switch team. Demo sign-in only, with seeded users. |
| **Chat** | Create and list Chats, send a Turn, stream a Turn's events |
| **Cards** | Accept a Start Confirmation, read a live card |
| **Approvals** | Inbox, decide single, decide batch |
| **Bills** | Upload a document, list, view, correct a field |
| **Contacts** | Counterparties, destinations, verify and revoke |
| **Payments** | List, view with its full trace |
| **Cash** | Balances, analyses, alerts |
| **Controls** | Pause and resume all payments |
| **Recurring** | Mandates (activate, pause, revoke). There are no template routes. |
| **Accounting** | Expenses, versions, manual corrections, exceptions |
| **Memory** | List, delete, Auto Mode setting. There is no edit route. |
| **Purchases** | List, approve |
| **Webhooks** | One signed route, keyed by provider name |
| **Developer** | List runs, read a run, read an eval team's trace, start a run. Separate role. |

Routes that touch team data check membership and role on the server. Screens only reflect those checks. Demo sign-in takes a seeded email with no password, so it isn't real authentication.

## 9. Security

- Tests attempt cross-team access through direct IDs, search, documents and tools.
- Secrets live in per-process env files, which are never committed. A secret scan runs before the repo is made public or pushed.
- Trace Steps and logs have keys, tokens and full account numbers removed before saving.
- Webhooks are signature-checked and only prompt a status query.
- A failed safety check, unclear model result or stale balance stops the action rather than guessing.

## 10. Evaluation

See `docs/evaluation.md`.

| Part | Plan |
|---|---|
| **Suites** | Full Agent, Routines and Faults. The planned Main Loop suite isn't in the runner, and is covered by `tests/test_main_loop.py`. |
| **Baselines** | Rules-only, LLM-only (simulator only) and PayoAI. The Investigate and Memory on/off variations aren't in the runner. |
| **Data** | Synthetic cases from one generator, `evals/generators/cases.py`, with expected results stated in each scenario's definition. The same team wrote the generator and the agent, so they aren't independent. See `deck/DATA.md`. |
| **Size** | 150 cases: 90 development, 20 validation, 40 held-out |
| **Held-out set** | Hash in `evals/cases/MANIFEST.json`, checked before each run. Run once at the freeze. |
| **Recorded** | Unsafe executions, wrong recipients, duplicates, completion, false blocks, extraction accuracy, truthfulness, latency, real cost per decision |
| **Reported** | Counts in the form "N of 40", with failures and limits shown |

The kit in the organisers' zip isn't used.

## 11. Configuration

`.env.example` lists names only.

| Process | Variables |
|---|---|
| `agent-worker` (`.env.agent`) | `GEMINI_API_KEY` |
| `money-worker` (`.env.money`) | `AIRWALLEX_API_KEY`, `WISE_API_TOKEN`, `CIRCLE_API_KEY`, `TESTNET_SIGNER_KEY`, `CHAIN_RPC_URL`, `TEST_CARD_TOKEN` |
| `api` (`.env.api`) | `WEBHOOK_SECRET_SIMULATOR`, `SESSION_SECRET` |
| All (`.env.shared`) | `DATABASE_URL`, `PROVIDER_MODE` |

Only `TEST_CARD_TOKEN` is read from the `.env.money` names today, because the other names belong to rails that aren't built. No code reads `PROVIDER_MODE` yet. Keys are added to local env files by hand and are never pasted into chat.

## 12. Things Stage 1 had to establish

| Question | Fallback if the answer is no | Where it stands |
|---|---|---|
| Does the Gemini key work with Argon and Flash, and are credits enough? | Stop and fix this first | Commit `4242a94` records fixes from a first live run. Credits aren't recorded in the repository. |
| How does Gemini report safety blocks? | Treat any unclear result as a failed step | `agent/llm.py` applies the fallback |
| Does LangGraph restart a paused Node on resume? | The design is safe either way, because pauses have their own Nodes | The design relies on the fallback |
| Does the database connect? | Yes, through the IPv4 pooler, because this network has no IPv6 | In use |
| Can we pay out in the Airwallex sandbox? | Use Wise or the simulator, and say so | Not built. The simulator is used. |
| Can we create transfers in the Wise sandbox? | Use Airwallex or the simulator, and say so | Not built. The simulator is used. |
| Is testnet USDC available with local signing? | Clearly label a simulated crypto rail | Not built. Crypto is simulated and labelled. |
| Do sandboxes enforce idempotency keys? | Rely on never resending without knowing the outcome, which is already the rule | Untested on any real provider |

Evaluation runs on the simulator and doesn't need the real rails, so a blocked sandbox can't block the submission.

## 13. Original schedule

This is the schedule as planned, kept for the record.

Times assume the submission is due at 18:00 on Sunday. **Confirm the exact time**, because the guide also mentions Sunday afternoon judging. Everything moves earlier if it's sooner.

| When | Stage | What gets built | Must be true when finished |
|---|---|---|---|
| Sat 19:15-20:00 | **1. Checks** | Everything in section 12, repo skeleton | Answers recorded |
| Sat 20:00-21:30 | **2. Foundations** | Schema, roles, traces, audit, queue, simulator | Cross-team tests pass |
| Sat 21:30-00:30 | **3. Money path** | Pay Bill, approval, Execute, Reconcile, fraud checks | Changed wallet blocked, timeout gives no duplicate |
| Sun 00:30-06:30 | **Sleep** | | |
| Sun 06:30-08:30 | **4. Agent and core screens** | Main Loop, Read Tools, Chips, reply check, chat and approval screens | Live demo works |
| Sun 08:30-10:30 | **5. Evaluation, first pass** | Cases, runner, judges, baselines, first deck draft | **Submittable project, pushed** |
| Sun 10:30-12:00 | **6. Payment Routines** | Pay Someone, Prepare Batch, Replace Bill, Register Bill, Check Bill Compliance, Cash Analysis, Add Counterparty, Verify Destination, real adapters | Runs against shared Nodes |
| Sun 12:00-12:45 | **7. Accounting** | Record Expense, Revise Expense, Chase Missing Document | Payment traces through to expense |
| Sun 12:45-13:30 | **8. Recurring and memory** | Set Up Recurring, Prepare Occurrence, Save Preference, Auto Mode | One payment per Occurrence |
| Sun 13:30-14:30 | **9. Purchases** | Purchase Research, test shop, Purchase Checkout | Price change forces re-approval |
| Sun 14:30-15:15 | **10. Developer Console** | Trace browser, replay, re-run | Trace viewable end to end |
| Sun 15:15-16:00 | **Screens and polish** | Remaining screens from the UI design | No dead buttons |
| Sun 16:00 | **Feature freeze** | No new features after this | |
| Sun 16:00-16:30 | **11. Final results** | Run the locked held-out set once | Numbers recorded |
| Sun 16:30-17:30 | **Deck and repo** | Final deck, README, secret scan, public repo | Evidence in the deck |
| Sun 17:30-18:00 | **Rehearse and submit** | Pitch run-through, screen recording, submit | Submitted |

**Cut order if behind:** Purchase Checkout, then Purchase Research, then Set Up Recurring with Prepare Occurrence, then Auto Mode, then the Developer Console, then real rails beyond the first working one.

**Never cut:** stage 3, stage 5 or the final results. The judges score from the deck, and that's where the evidence comes from.

## 14. Definition of done

These are the targets, and this list doesn't say which have passed.

- A Pay Bill flow runs from an uploaded synthetic invoice to a reconciled expense, with every step traceable.
- A changed wallet is blocked on screen, and a timeout after sending produces no duplicate.
- An Operator can't approve, and a revoked Approver is refused.
- Held-out results are reported honestly, with at least one failure or limit disclosed.
- The public repo has a short README naming the user problem, exact model IDs and the verification loop, with no secrets.
- The deck covers the problem, who it helps, the data, the benchmark, the agent's limits and risk controls, and the key statistics.

## 15. Where the build differs from the plan

Each row was checked against the code at commit `d94d88b`.

| Plan | What was built | Evidence |
|---|---|---|
| Main Loop on LangGraph | A plain loop, because Gemini's API stores the conversation | `agent/main_loop.py`, note in ADR 0001 |
| Investigate as a capped tool loop with Read Tools | Code gathers records, then one LLM call | `investigate` and `_related_records` in `routines/nodes/decision.py` |
| Pay Someone with its own Node order | Runs the shared order: Check Duplicates, Check Amount, Select Destination, Check Destination, Select Source Account, Check Cash | `_add_checks` in `routines/graphs/payment.py` |
| Purchase Checkout scripted with Playwright | Fixed HTTP calls against the test shop | `routines/purchases.py`, `merchant_site/app.py` |
| Migrations | Numbered SQL files, as planned | `migrations/`, `db/migrate.py` |
| Next.js frontend | Static page in `web/`, served by the API, pending the user's design | `index` route in `api/app.py` |
| Postgres | Supabase in a private schema, with prepared statements off for the pooler | `db/__init__.py` |
| Airwallex, Wise and Circle rails | Not built. The simulator is the only rail. | `providers/` |
| Crypto signing and broadcasting | Not built. `signed_tx` and `tx_hash` columns exist but are unused. | `money/execute.py`, `migrations/0001_core.sql` |
| Four evaluation suites and on/off variations | Three suites, and no variations | `SUITE_FILTER` in `evals/runner.py` |
| Tests in CI | No CI is configured | No workflow files in the repository |
| One LangGraph file per Routine, and versioned prompt files | Two graph files, and inline prompts | `routines/graphs/`, `agent/` |
| Developer Console screens | API routes only, as far as the code at this commit shows | `/api/dev` routes in `api/app.py` |

Routine-level differences are in `docs/routines.md`.
