# PayoAI build plan

The complete plan for the UCL AgentVerse 2026 Finance track. One builder, due Sunday 8 November 2026.

Related documents:

- `CONTEXT.md`: glossary
- `docs/routines.md`: every Routine, Node by Node
- `docs/traces.md`: chats, Turns, traces and the Developer Console
- `docs/evaluation.md`: test suites and baselines
- `docs/adr/`: decisions that are hard to reverse

Where this plan and those documents overlap, those documents hold the detail.

## 1. Product

**User:** a UK small business owner with no finance team.

**Decision:** "Is it safe to pay this bill now, and can we afford it?"

**Failure that hurts them:** paying a fraudster's changed bank details, paying twice, or missing a payment because cash ran short.

**Scope:** all 18 Routines in `docs/routines.md`, Airwallex, Wise and Circle rails, four evaluation suites and the Developer Console. If time runs short, follow the order in section 13.

**Hard limits:** synthetic data only, clearly labelled. No real money, real records, real account details or logins. A human approves anything that moves money.

## 2. Stack

| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.12, managed by `uv` | The machine's default Python is 3.9, which is too old |
| API | FastAPI, with server-sent events for live updates | |
| Database | Postgres | Also holds the job queue and LangGraph checkpoints. See section 3. |
| Database access | psycopg 3 with explicit SQL, plus numbered SQL migrations | Conditional updates stay visible and reviewable |
| Routines with LLM steps | LangGraph with the Postgres checkpointer | ADR 0001 |
| Money path | Plain Python and a job queue | ADR 0001 |
| Models | `google-genai` 2.3.0 or later, Interactions API | ADR 0002 |
| Typed data | Pydantic v2 | Validates every LLM output and API body |
| Crypto | `web3.py`, Base Sepolia if Circle's testnet USDC supports it | Never hand-written cryptography |
| PDF text | `pypdf` | Lets code confirm that extraction quotes appear in the document |
| Scripted checkout | Playwright | |
| Frontend | Next.js mobile-first web app | Screens follow the UI design still to come |
| Tests | `pytest` | |

## 3. Infrastructure

### Runs locally for the demo

Docker, Postgres and `gcloud` aren't installed on this machine, and the guide says not to spend the weekend on deployment. The demo runs on the laptop, with a screen recording as the backup.

**Postgres:** Supabase, through its IPv4 pooler, in a private `payoai` schema that its web API does not expose. It holds synthetic data only.

**Hosting:** Cloud Run and Cloud SQL are optional and only considered after the final results are in.

### Processes

```text
Next.js web app
      |  HTTPS + server-sent events
      v
api ---------------- chats, Turns, approvals, webhooks, Developer Console
      |
      |  Postgres: records, jobs, checkpoints, traces
      |
      +-- agent-worker -- Main Loop and LLM Routines
      |                   Holds: Gemini key. No payment secrets.
      |
      +-- money-worker -- Execute Payment and Reconcile Payment
      |                   Holds: provider keys and testnet signing key. No model access.
      |
      +-- scheduler ----- Occurrences, Cash Analysis, reconciliation polling
      |
      +-- provider adapters: Simulator, Airwallex, Wise, Circle/testnet

merchant-site -------- test shop for Purchase Checkout
```

| Process | Holds | Can't do |
|---|---|---|
| `api` | Database access | Call providers to send money, or read provider or signing keys |
| `agent-worker` | Gemini key | Read provider or signing keys, or start Execute Payment |
| `money-worker` | Provider keys and signing key | Call any LLM |
| `scheduler` | Database access | Do the work itself. It only creates jobs. |

Each process loads its own env file, so a compromised agent process has no payment secret to reach.

### Job queue

One Postgres table. Workers claim jobs using `SELECT ... FOR UPDATE SKIP LOCKED`.

A job created by an approval is saved in the same transaction as the approval. `dedupe_key` is unique, so a repeat webhook or timer can't create a second job.

Job kinds: `run_routine`, `resume_routine`, `execute_payment`, `reconcile_payment`, `fire_occurrence`, `cash_analysis`.

## 4. Repository layout

```text
PayoAI/
  CONTEXT.md
  README.md
  pyproject.toml
  .env.example                 names only, no values
  docs/
  migrations/
  src/payoai/
    api/                       routes, sessions, server-sent events, webhooks
    domain/                    pure money, hash and state-machine code, no I/O
    db/                        team-scoped queries
    trace/                     Trace Steps and Audit Events
    queue/                     claiming and creating jobs
    agent/
      main_loop.py
      read_tools.py
      chips.py                 chip rendering and the reply check
      llm.py                   the only module that calls Gemini
      config.py                model and thinking level per step
      prompts/                 versioned prompt files
    routines/
      nodes/                   shared Nodes, including fiat and crypto versions
      graphs/                  one LangGraph file per Routine
      registry.py              Routine config, including Start Confirmation
    money/                     Execute Payment and Reconcile Payment, no LangGraph
    providers/                 base interface, simulator, airwallex, wise, circle
    compliance/rules/          versioned UK rules
    memory/
    workers/                   agent_worker, money_worker, scheduler
  merchant_site/
  web/                         Next.js
  evals/
    cases/{dev,validation,heldout}/
    generators/
    judges/
    baselines/
    runner.py
  tests/
```

## 5. Data model

Every business table has `team_id`. Money is stored as an integer in the asset's smallest unit with its asset code, never as a float.

| Group | Tables |
|---|---|
| **Access** | `users`, `teams`, `memberships` (Viewer, Operator, Approver), `platform_roles` (Developer) |
| **Conversation** | `chats`, `turns`, `task_list_items` |
| **Routines** | `routine_runs`, `run_questions`, `start_confirmations`, `langgraph_*` (library-managed) |
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
| **Other** | `jobs`, `notifications`, `eval_cases`, `eval_runs`, `eval_results` |

### Rules the database enforces

| Rule | How |
|---|---|
| One active attempt per payment | Partial unique index on `payment_attempts(intent_id)` while the attempt is active |
| One Occurrence per Mandate and period | Unique `occurrences(mandate_id, period)` |
| One idempotency key per team, provider and operation | Unique index on `payment_attempts` |
| One step running per Chat | Partial unique index on `routine_runs(chat_id)` while `running` |
| One duplicate-safe job | Unique `jobs(dedupe_key)` |
| Unique invoice per supplier | Unique index excluding Superseded Bills |
| Append-only audit and expense history | Database trigger rejects `UPDATE` and `DELETE` |
| One exception per expense | Unique index on open exceptions |
| An Approval bound to one payment hash | `approvals(intent_id, payload_hash)`, with no way to change the hash |

### Approval

An approval is one statement inside one transaction. See `docs/routines.md`, "Approval".

```sql
UPDATE approvals
SET status = 'APPROVED', decided_by = :user_id, decided_at = now()
WHERE id = :approval_id
  AND team_id = :team_id
  AND status = 'PENDING'
  AND payload_hash = :hash_shown_on_screen
  AND expires_at > now()
  AND EXISTS (approver role still held by :user_id)
  AND NOT EXISTS (Pause All Payments is on for :team_id)
RETURNING id;
```

If a row comes back, the same transaction queues `execute_payment`. If not, the API reads the record and says why.

## 6. Agent

### Main Loop

`agent/main_loop.py` is a LangGraph agent calling Argon through `agent/llm.py`. Its conversation is stored by the API with `previous_interaction_id`, and every input and output is saved as a Trace Step.

Code adds this user's Waiting Runs, task list and active Memory at the end of the latest input, labelled as data. Stable instructions stay at the start, so implicit caching works.

| Tool | Effect |
|---|---|
| Read Tools | Bills, payments, counterparties, balances, expenses, run details, compliance rules, and document text labelled as untrusted |
| `start_routine(name, inputs)` | Starts a Routine, or returns a card if it needs a Start Confirmation. It returns when the Routine finishes or pauses. |
| `answer_run(run_id, value)` | Answers a Waiting Run's question. The Routine rejects anything outside the allowed answers. |
| `update_task_list(items)` | Saves the plan for multi-part requests. It gives no authority. |

The Main Loop has no tool for approval, verification, execution or changing check results. Team and role come from the session, never from tool inputs. Bank and wallet details aren't accepted as inputs at all.

### Reply handling

1. The Main Loop cites records as `[[payment:pay_123]]`.
2. `chips.py` checks the reply: numbers and dates must appear in this Turn's tool results, words like "paid" or "confirmed" must match the record's status, and record claims need a citation.
3. A failed check regenerates once. A second failure shows the raw records.
4. Code renders each citation as a Chip with live values.

### Routines

- **Nodes:** each Node is wrapped so a Trace Step is saved when it starts and updated when it ends. If that save fails, the Node doesn't run.
- **Pauses:** pauses live in their own small Nodes, because resuming restarts a paused Node from its beginning.
- **Source of truth:** `routine_runs.status` is authoritative, and LangGraph checkpoints only track progress.
- **Handing over money:** Routines stop at Await Approval. They never call a provider.
- **LLM Nodes:** Extract Fields, Investigate and Explain return validated typed output. They have a timeout and a capped retry count. Anything other than a completed, valid result is a failed step.
- **Decisions:** check and decision Nodes never receive Memory or LLM output as authority.

### Models

See ADR 0002. All model settings live in `agent/config.py` and are recorded on each Trace Step.

### Untrusted content

Documents, web pages and provider text are data. They're labelled when shown to a model and never reach the check or decision Nodes as instructions. Purchase Research receives no business data, and its `fetch_page` tool only opens URLs from search results and blocks internal addresses.

## 7. Payments

### Provider interface

Each adapter implements listing accounts, balances, destination validation, quotes, submission, status lookup, transaction lookup and webhook verification. It also declares capability flags, because providers differ.

| Adapter | Purpose |
|---|---|
| `simulator` | Default for development and the benchmark. Injects timeouts, duplicate and reordered webhooks, pending states, reverted transactions and stale balances. |
| `airwallex` | Real sandbox |
| `wise` | Real sandbox |
| `circle` | Testnet USDC, signed locally by `money-worker` using an audited library if Stage 1 confirms it |

Results are always labelled as simulated, sandbox-observed or testnet-observed. If a real rail isn't accessible, the plan and the deck say so plainly.

### Payment hash

`domain/` builds the canonical payment (team, source, destination, rail, amount, asset, chain and token if crypto, maximum fee, obligation and reference) and hashes it with SHA-256. Execute Payment recomputes the hash before sending.

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
| **Recurring** | Mandates (activate, pause, revoke), templates |
| **Accounting** | Expenses, versions, manual corrections, exceptions |
| **Memory** | List, edit, delete, Auto Mode setting |
| **Webhooks** | One signed endpoint per provider |
| **Developer** | Browse traces, replay, re-run a case or suite, compare results. Separate role, synthetic eval teams only. |

Every route checks team membership and role on the server. Screens only reflect those checks.

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
| **Suites** | Full Agent, Routines, Main Loop, Faults |
| **Baselines** | Rules-only, LLM-only (simulator only), PayoAI, plus Investigate and Memory on/off |
| **Data** | Synthetic cases written by a separate generator, with independently written expected results, and labelled with how they were made |
| **Size** | About 150 cases: 90 development, 20 validation, 40 held-out |
| **Held-out set** | Hash committed before the first run. Run once at the freeze. |
| **Recorded** | Unsafe executions, wrong recipients, duplicates, completion, false blocks, extraction accuracy, truthfulness, latency, real cost per decision |
| **Reported** | Counts such as "0 of 40", with failures and limits shown |

The kit in the organisers' zip isn't used.

## 11. Configuration

`.env.example` lists names only.

| Process | Variables |
|---|---|
| `agent-worker` | `GEMINI_API_KEY` |
| `money-worker` | `AIRWALLEX_*`, `WISE_*`, `CIRCLE_*`, `TESTNET_SIGNER_KEY`, `CHAIN_RPC_URL` |
| `api` | `WEBHOOK_SECRET_*`, `SESSION_SECRET` |
| All | `DATABASE_URL`, `PROVIDER_MODE` (`simulator` or `sandbox`) |

Keys are added to local env files by hand and are never pasted into chat.

## 12. Things Stage 1 must establish

| Question | If the answer is no |
|---|---|
| Does the Gemini key work with Argon and Flash, and are credits enough? | Stop and fix this first |
| How does Gemini report safety blocks? | Treat any unclear result as a failed step |
| Does LangGraph restart a paused Node on resume? | The design is safe either way, because pauses have their own Nodes |
| Does the database connect? | Yes, through the IPv4 pooler, because this network has no IPv6 |
| Can we pay out in the Airwallex sandbox? | Use Wise or the simulator, and say so |
| Can we create transfers in the Wise sandbox? | Use Airwallex or the simulator, and say so |
| Is testnet USDC available with local signing? | Clearly label a simulated crypto rail |
| Do sandboxes enforce idempotency keys? | Rely on never resending without knowing the outcome, which is already the rule |

Evaluation runs on the simulator and doesn't need the real rails, so a blocked sandbox can't block the submission.

## 13. Schedule

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

- A Pay Bill flow runs from an uploaded synthetic invoice to a reconciled expense, with every step traceable.
- A changed wallet is blocked on screen, and a timeout after sending produces no duplicate.
- An Operator can't approve, and a revoked Approver is refused.
- Held-out results are reported honestly, with at least one failure or limit disclosed.
- The public repo has a short README naming the user problem, exact model IDs and the verification loop, with no secrets.
- The deck covers the problem, who it helps, the data, the benchmark, the agent's limits and risk controls, and the key statistics.
