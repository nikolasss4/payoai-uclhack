# Routines

Decisions from the design grilling, updated to match the code at commit `d94d88b`. Terms are defined in `CONTEXT.md`. Where the original design and the build differ, an "As built" note says so.

## Main Loop

The Main Loop answers free-form questions using Read Tools and starts Routines one at a time. It is a plain loop rather than a LangGraph graph, because Gemini's API stores the conversation (see the note in ADR 0001). It picks up Waiting Runs itself and offers to continue them. It keeps a saved task list for multi-part requests, which gives no authority. There is no separate planner LLM.

Read Tools return typed records with IDs, covering bills and obligations, payments and attempts, counterparties and destinations, balances and alerts, expenses, run and decision details, compliance rules, and document contents labelled as untrusted.

Money facts appear as chips. The Main Loop cites a record's ID, and code renders the record's live values.

A code check runs before each reply is shown. It looks for numbers or dates not in this turn's tool results, words like "paid" or "confirmed" alongside a record whose status disagrees, and claims about a record with no citation. A failing reply is regenerated once, and if it fails again, the raw records are shown instead.

- Team and role come from the session, so a tool can't return another team's data.
- A failed Read Tool gives "couldn't check", never an answer from memory. Each question re-reads the records.
- Code refuses a Routine the user's role doesn't allow.
- Verified account numbers, keys and tokens aren't available to any Read Tool. An invoice's own text may show bank details, and it can be read, labelled as untrusted. The approval card reveals full details through a route outside the model, and each view is recorded.
- Legal or tax claims must cite a rule from the ruleset or say the point isn't checked. The Main Loop never says a supplier is safe or an expense is deductible. This rule is an instruction in the system prompt, and code doesn't enforce it.

## Rules for every Routine

- A Routine is a fixed sequence of Nodes. Routines that need the same step share the same Node. The fiat and crypto destination checks are two code paths inside one Check Destination Node.
- A Node is either code or an LLM Node. LLM Nodes return typed output only and cannot change a check result, a decision or a state.
- A Routine's configuration says whether it needs a Start Confirmation. Any Routine that sets up a payment, destination or schedule needs one. Code enforces it.
- All checks run even if one fails, so the user sees every problem at once. Nodes that later checks depend on can stop a run early.
- The worst result wins: `BLOCK`, then `NEEDS_REVIEW`, then `NEEDS_INFORMATION`, then `ELIGIBLE_FOR_APPROVAL`.
- Each conversation runs one Routine step at a time. A run waiting for an answer in chat is a Waiting Run. A run waiting for approval sits in the approvals inbox.
- Approval and destination verification are never done through chat.

## Routine list

| Routine | Started from | Start Confirmation | Status |
|---|---|---|---|
| Pay Bill | Chat | Yes | Built. The only Routine in the benchmark. |
| Replace Bill | Chat | Yes | Built, with unit tests |
| Pay Someone | Chat | Yes | Built, in the shared Node order, with unit tests |
| Prepare Batch | Chat | Yes | Simplified: one Node, and one card per payment. See its section. |
| Execute Payment | Approver's tap | n/a | Built as plain code, on the simulator only. Crypto signing isn't built. |
| Reconcile Payment | Webhook, timer or timeout | n/a | Built as plain code, on the simulator only |
| Register Bill | Chat | No | Built, with unit tests |
| Check Bill Compliance | Chat | No | Built, with unit tests |
| Cash Analysis | Chat or daily timer | No | Simplified: two Nodes, and it doesn't re-run after a bill or payment changes |
| Add Counterparty | Chat | Yes | Built, with unit tests |
| Verify Destination | Dedicated API action | n/a, because it is a tap | Built as plain code, not a graph |
| Set Up Recurring | Chat | Yes | Simplified: drafts a Mandate. Templates and Mandate versions aren't built. |
| Prepare Occurrence | Timer | n/a | Simplified: the matching-invoice trigger isn't built |
| Revise Expense | Chat | Yes | Built, with unit tests |
| Chase Missing Document | Chat, or automatically when an expense has no evidence | No | Simplified: no graph pause. Evidence arrives through an API route. |
| Purchase Research | Chat | No | Simplified: no Explain Node |
| Purchase Checkout | Chat | Yes | Built as scripted HTTP calls against the test shop, not Playwright |
| Save Preference | Chat | Yes | Built, with unit tests |

"With unit tests" means tests exist in `tests/`, using a scripted model on the simulator. It doesn't mean the Routine was run through the benchmark.

Pause All Payments is a button available to Approvers, not a Routine, so an emergency stop never depends on a model. It is the `POST /api/controls/pause` route, and the approval statement and Execute Payment both check it.

## Pay Bill

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Load Document | Code | Saves the original file and its hash |
| 2 | Extract Fields | LLM + code | The LLM extracts typed fields and quotes its source for each. Code confirms each quote is in the document. |
| 3 | Match Counterparty | Code | Looks up the registry and asks the user if the match is unclear |
| 4 | Register Obligation | Code | Records what's owed |
| 5 | Check Duplicates | Code | Compares invoice number, file hash, and amount and period against past bills |
| 6 | Check Compliance | Code | Runs the applicable UK rules and arithmetic checks, and flags failed quotes and text aimed at an AI |
| 7 | Check Amount | Code | Compares the amount to this counterparty's usual range |
| 8 | Select Destination | Code | Picks a Verified Destination for this asset. Details on the invoice are never a source. |
| 9 | Check Destination | Code | Compares the invoice's payment details to the Verified Destination, with fiat and crypto code paths |
| 10 | Select Source Account | Code | Uses the only account holding that asset, or asks if there are several |
| 11 | Check Cash | Code | Checks balances, reserve and fees |
| 12 | Decide | Code | Combines all results into one decision |
| 13 | Investigate | LLM | Runs only on `NEEDS_REVIEW` or `NEEDS_INFORMATION` |
| 14 | Explain | LLM | Writes the plain-English reason from the results. A failure falls back to a plain list of the reasons. |
| 15 | Prepare Payment | Code | Builds the exact payment and its hash, for `ELIGIBLE_FOR_APPROVAL` and `NEEDS_REVIEW` only |

Extract Fields and Match Counterparty can stop a run early, and so can a stored document whose hash no longer matches. An unknown supplier name stops the run.

As built, there is no Await Approval pause Node. The graph ends after Prepare Payment and the run is marked `AWAITING_APPROVAL`. A `BLOCK` or `NEEDS_INFORMATION` decision prepares nothing. A `NEEDS_REVIEW` payment is prepared and marked as needing review, and an Approver's approval is refused unless the request says it was reviewed. That flag is sent by the client, so it records a person's statement and doesn't prove they looked.

Investigate gathers its records in code: this counterparty's earlier bills and any contracts on file. The model gets them in one LLM call with no tools, and they are labelled as untrusted. Every finding must cite a record, and a finding citing an unknown record is dropped. Findings appear as agent suggestions on the card. It doesn't run on `BLOCK`. If it fails, the Routine carries on without it.

## Pay Someone

As built, Pay Someone runs the same shared order as Pay Bill, without the document Nodes or Check Compliance.

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Require Purpose | Code | Asks if no purpose was given |
| 2 | Match Counterparty | Code | Shared with Pay Bill |
| 3 | Register Obligation | Code | Records what's owed, with the request as its source |
| 4 | Check Duplicates | Code | Shared. Looks for the same recipient and the same amount in a recent period. |
| 5 | Check Amount | Code | Shared. Compares the amount to this counterparty's usual range. |
| 6 | Select Destination | Code | Shared. Picks a Verified Destination that fits the requested asset, and asks if several fit. |
| 7 | Check Destination | Code | Shared. `BLOCK` if none is verified. |
| 8 | Select Source Account | Code | Shared. Uses the only account holding that asset, or asks if there are several. |
| 9 | Check Cash | Code | Shared |
| 10-13 | Decide, Investigate, Explain, Prepare Payment | Mixed | Shared |

A payment with no invoice is allowed:

- The Routine requires a stated purpose and asks if none was given.
- The approval card shows "No invoice attached."
- Once the payment is confirmed, the expense is marked `MISSING_EVIDENCE` and a missing-document exception opens.
- A first payment to someone with no usual range, or an amount outside their usual range, gives `NEEDS_REVIEW`.
- The same recipient and amount in a recent period gives `NEEDS_REVIEW` and shows the earlier payment. `BLOCK` for an exact repeat relies on an invoice number or file, so a payment with no invoice can only reach `NEEDS_REVIEW` this way.

Currency is never converted silently. If no Verified Destination fits the requested asset, the decision is `BLOCK`. If no source account fits, it is `NEEDS_INFORMATION`. Both say why, and neither prepares a payment.

## Prepare Batch

As built, this is one Node that calls the shared check functions in this order:

| # | Step | Type | What it does |
|---|---|---|---|
| 1 | Select Bills | Code | Finds registered, unpaid bills using the date range from the Main Loop |
| 2 | Check Each Bill | Shared | Runs the shared checks on each bill, reusing extractions |
| 3 | Allocate Cash | Code | Runs cash across the batch per source account, so currencies stay separate |
| 4 | Decide Each Bill | Code | Gives each bill its own decision |
| 5 | Investigate | LLM | Same rules as Pay Bill, for at most 3 bills per batch |
| 6 | Explain | Code | A plain list of the reasons. The Explain LLM isn't called in a batch. |
| 7 | Prepare Payments | Code | Builds one exact payment and hash per bill that is `ELIGIBLE_FOR_APPROVAL` or `NEEDS_REVIEW` |

Allocate Cash orders bills by earliest due date, then smaller amount. A bill that would break the reserve gets `NEEDS_REVIEW` with the shortfall. Nothing is converted.

A batch never pauses for questions. Bills needing information are listed together in the result, and the rest carry on.

| Bill's decision | Result | Can it be approved here? |
|---|---|---|
| `ELIGIBLE_FOR_APPROVAL` | Payment prepared | Yes |
| `NEEDS_REVIEW` | Payment prepared and marked as needing review | Not through the batch route, because it never sends the reviewed flag. It must be decided individually. |
| `NEEDS_INFORMATION` | Listed with its reason | No payment is prepared |
| `BLOCK` | Listed with the reason | No payment is prepared |

`POST /api/approvals/batch` makes one decision per payment, each bound to its own hash, and reports which won. The money worker handles each as its own job, so a failed or `UNKNOWN` payment doesn't stop the others. Cash is re-checked at execution, and a payment that no longer fits is stopped rather than sent.

Two parts of the design aren't built. The chat shows one approval card per payment rather than one batch card, and changing the selection doesn't re-run Allocate Cash.

## Approval

A card in chat and an entry in the approvals inbox are two views of one approval record. Approval is never typed in chat. Any Approver can decide, whoever started the run, and the first decision to reach the database wins, including a rejection. A rejected payment needs a new payment and a new approval to try again.

Every tap sends the approval's ID and the payment hash shown on that screen. In one database transaction, the server sets the status only if it is still `PENDING`, the hash matches, the approval hasn't expired, the person is still an Approver and Pause All Payments is off. If the tap wins an approval, Execute Payment is queued in the same transaction. Otherwise the server reads the record and says why.

- A repeat tap by the same person returns the same result, and nothing runs twice.
- The database allows one active attempt per payment.
- Replace Bill locks the bill, refuses if an attempt exists, and otherwise cancels the approval, whether pending or approved.
- In a batch, each payment gets its own update, and the tap reports which were already decided.
- Cards are rendered from the live record, and updates are pushed to open screens. Safety comes from the database check, not the push.
- The tap doesn't go through LangGraph. The same transaction marks the run `COMPLETED` if it is still `AWAITING_APPROVAL`, so nothing needs resuming.
- Losing and stale taps are saved as Trace Steps.
- A rejection is allowed even if the approval has expired or payments are paused.

## Execute Payment

Started only by an Approver's tap. It has no LLM Nodes, and the model cannot call it or see credentials or signing keys.

| # | Node | What it does |
|---|---|---|
| 1 | Verify Approval | Confirms the approval exists, hasn't expired and matches the payment's hash |
| 2 | Re-check Authority | Confirms the Approver still has the role and Pause All Payments is off |
| 3 | Re-check Payment | Confirms the destination is still verified, cash is sufficient and the fee is within the approved maximum |
| 4 | Record Attempt | Saves the attempt and its fixed idempotency key before anything is sent |
| 5 | Submit | Calls the provider. Today that is always the simulator, including for crypto. |
| 6 | Record Result | Saves what came back and hands over to Reconcile Payment |

If Nodes 1-3 fail, nothing is sent and the payment needs a fresh approval. Approvals expire after 24 hours by default, set per team. A repeat job finds the saved attempt and never sends again, and an attempt with no result becomes `UNKNOWN`.

| What happened | Result | Automatic resend? |
|---|---|---|
| Connection failed before the request was sent | Still unsent. If it fails again, it ends as `REJECTED`, because no money moved. | Yes, once, with the same idempotency key |
| Timeout or dropped connection after sending | `UNKNOWN` | No |
| Server error from the provider | `UNKNOWN` | No |
| Clear rejection | `REJECTED` | No |
| Accepted | `SUBMITTED` | Not needed |

Only Reconcile Payment can move a payment out of `UNKNOWN`. Sending again after a confirmed failure needs a new payment and a new approval.

**Not built.** The rule for real crypto is that the signed transaction and its hash are saved before broadcasting, and after a timeout, only those exact bytes can be sent again. No signing or broadcasting code exists yet, so this rule is a design only. Whether a real provider's sandbox enforces idempotency keys is untested, because no sandbox adapter exists.

## Reconcile Payment

Started by a webhook, a timer or an `UNKNOWN` result. It has no LLM Nodes. Webhooks only prompt a check, and the status comes from asking the provider or chain directly.

| # | Node | What it does |
|---|---|---|
| 1 | Verify Trigger | Checks the webhook's signature and ignores duplicates. An invalid signature is logged and goes no further. |
| 2 | Query Source | Asks the provider or chain directly, by external ID or by our reference |
| 3 | Compare Outcome | Compares the amount, currency, recipient and reference to the approved payment |
| 4 | Update State | Applies the proof rules and saves every observation as evidence |
| 5 | Follow Up | On a final state, updates the obligation and expense record, and notifies people |

As built, Verify Trigger is `handle_webhook`, and Update State includes Follow Up. The proof rules below are the design. Only the simulator's answers are implemented, so the crypto proof column (receipts, token transfer events, nonces and confirmations) is not built, and a simulated crypto payment is settled the same way as a fiat one.

| Status | Fiat proof | Crypto proof |
|---|---|---|
| `CONFIRMED` | The provider's query says completed, and the details match | A successful receipt whose token transfer event matches the token contract, recipient and amount, with the required confirmations |
| `FAILED` | The provider's query says failed or cancelled | A reverted receipt, or our transaction's nonce was used by a different transaction |
| `PENDING` | The provider has it but hasn't finished | In the mempool or below the required confirmations |
| `UNKNOWN` | The provider can't find it or can't be reached | The transaction isn't found, but could still land |

- "Not found" is not proof of failure.
- An outcome that doesn't match the approved payment goes to review with both values shown.
- Polling runs after 1, 5, 15 and 60 minutes, then hourly, even if no webhook arrives.
- After a set period per provider (24 hours in the demo), the payment moves to `NEEDS_MANUAL_REVIEW` and the Approver is notified. It never becomes `FAILED` just because time passed.
- A `CONFIRMED` result after a recorded `FAILED` changes the status and raises a critical alert, because a replacement payment may now be a duplicate.
- An Approver can settle a review case on a dedicated screen with evidence. It is recorded as manually resolved, not externally confirmed.
- Results are labelled by the attempt's evidence label, which is always `SIMULATED` today.

## Add Counterparty

Creates or updates a contact. Any new bank or wallet details are saved as `UNVERIFIED`, with where they came from. An existing Verified Destination stays valid until a new one is verified, and an unverified destination can't be selected for a payment.

## Verify Destination

A dedicated action, never in chat. As built, it is plain code behind `POST /api/destinations/{id}/verify`, not a LangGraph Routine, and it needs no Start Confirmation because the Approver's tap is the action.

| Method | What the Approver records | Strength |
|---|---|---|
| Call back | Called a number on file before the request, spoke to a named person and confirmed the full details | Strong |
| Signed contract | A contract already on file names these exact details | Strong |
| Provider check | The provider's name match, if the sandbox offers one | Refused on its own |
| Crypto wallet | The counterparty confirmed the full address through a channel on file. The evidence is recorded as a note, and no wallet signature is checked by code. | Strong |
| New relationship | How the relationship was established, when no earlier channel exists | Recorded as weaker trust |

- The contact channel must have been on file before the details came in.
- A method and written evidence are required, and the date is recorded automatically.
- The Approver can see the full IBAN or address through a route that records each view.
- Verifying a new destination doesn't update existing payments. They need preparing and approving again.
- Revoking takes effect immediately and cancels pending approvals using that destination.
- A new counterparty has no earlier channel, so the Approver records how the relationship was established. This shows as weaker trust, and the first payment gets `NEEDS_REVIEW`.
- One person may both add and verify. The approval card and audit trail show "added and verified by the same person". Requiring two people can come later as a team setting.

PayoAI enforces the process and records the evidence, but it can't prove a call happened.

## Register Bill

Uses Pay Bill Nodes 1-12 and Explain, plus Investigate on `NEEDS_REVIEW` or `NEEDS_INFORMATION`. It saves a payable and prepares no payment.

## Check Bill Compliance

Uses Load Document, Extract Fields, Check Compliance and Explain. It saves a report on the document and creates no payable, so it can't count as a duplicate later. If the user then pays the bill, the extraction is reused and the full checks run.

Compliance answers whether a document is good enough for the books, which is separate from whether it is safe to pay. Compliance results can't produce `BLOCK` on their own. `BLOCK` is reserved for safety rules such as changed destinations, wrong chain or token, and exact duplicates.

| Result | Effect on the decision |
|---|---|
| Pass | None |
| Not applicable | None, and nothing is reported as missing |
| Fail, on a record-keeping field | `NEEDS_REVIEW`, stating what's missing and why it matters |
| Fail, on the amount | `NEEDS_REVIEW`, with both figures on the card |
| Essential field missing | `NEEDS_INFORMATION` |
| `UNKNOWN` | `NEEDS_REVIEW` |
| Unsupported invoice type | `NEEDS_REVIEW`, saying the type isn't checked |

Code selects the rules from extracted facts, such as whether VAT is charged and whether a VAT number appears. If the type is still unclear, the result is `NEEDS_REVIEW`. No LLM suggests a type.

### UK ruleset

The ruleset is `uk-invoice-1.1`, in `src/payoai/compliance/rules/uk_vat.py`. It covers UK domestic full and simplified VAT invoices, and invoices from suppliers not registered for VAT. A foreign supplier, a reverse charge or VAT in another currency gets `NEEDS_REVIEW`, saying the type isn't checked.

- **Source:** HMRC's VAT Notice 700/21, "Record keeping", sections 4.1 and 4.5, last updated 18 March 2024, read on 7 November 2026.
- **Simplified invoices:** allowed at or below £250 including VAT, so fewer fields are required at or below that total.
- **Our own settings, with no confirmed source:** the rounding tolerance of one penny per line, because the pages we read state no rounding rule, and the VAT number pattern, which checks format only. Neither comes from HMRC.
- **Not checked:** whether a VAT number is really registered. Nothing is checked with HMRC.

An accountant hasn't reviewed this ruleset, and reports are labelled "Prepared for accountant review, not certified compliant."

## Cash Analysis

Started from chat, and once a day on a timer. Re-running after a bill is registered or a payment reaches a final state isn't built.

As built, steps 1-4 are one Cash Analysis Node, with a Trace Step for each balance refresh.

| # | Step | Type | What it does |
|---|---|---|---|
| 1 | Refresh Balances | Code | Fetches current balances and records when each was last updated |
| 2 | Normalise | Code | Corrects for whether each provider's "available" figure already excludes in-flight payments |
| 3 | Project | Code | Calculates the 7-day position per currency |
| 4 | Raise Alerts | Code | Creates or updates shortfall alerts without repeating them |
| 5 | Explain | LLM | Writes the summary from a list of figures built by code. A stale-data warning is added by code if the summary leaves it out. |

| Item | Treatment |
|---|---|
| Confirmed balance | The starting point |
| Payment in flight | Deducted, unless the provider's figure already excludes it |
| Approved, not yet sent | Deducted as reserved |
| Bill due, with no approved payment | Deducted by its due date, shown as due |
| Reserve | Deducted |
| Expected income | Never included in the number. No expected-income records exist yet. |
| Other currencies | Never added in |

Each bill counts in only one row.

Beyond a set age (15 minutes in the demo), a balance is labelled stale and the result can't report that everything is fine. Freshness is a typed field that Explain must include. Check Cash gives `NEEDS_REVIEW` on stale data, and Execute Payment stops the payment if it can't get a fresh balance.

An alert is identified by team, account and shortfall date. It re-notifies only if the shortfall gets materially worse or a new bill causes it. Cash Analysis can suggest fixes but can't move money.

A simple balance question stays a Read Tool answer.

## Set Up Recurring

Saves a Mandate and an Automation Rule. An Operator can draft a Mandate, but only an Approver can activate it, through a dedicated route. Setting it up never pays anything.

Two parts of the design aren't built. Changing a Mandate doesn't create a new version, because only activate, pause and revoke exist. Templates have a table but no code.

## Prepare Occurrence

Started by a timer. The matching-invoice trigger isn't built. The Main Loop isn't involved.

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Claim Occurrence | Code | Claims this Mandate and period. The database allows one claim, so a second one stops here. |
| 2 | Load Mandate | Code | Stops if the Mandate isn't active |
| 3 | Register Obligation | Code | Records what's owed and links it to the Mandate |
| 4 | Check Mandate | Code | Confirms the amount, date and evidence fit its limits |
| 5-13 | Shared Nodes | Mixed | Check Duplicates through Prepare Payment, as in Pay Someone |

- Every occurrence needs its own approval.
- A timer-started occurrence is prepared at the Mandate's maximum amount, because no invoice supplies the real one. The Approver sees that amount on the card.
- An amount above the Mandate's maximum gives `NEEDS_REVIEW` with both figures.
- A timer run after an outage is labelled as late. Each missed period gets its own visible occurrence and approval.
- Pausing or revoking stops new occurrences and cancels unapproved prepared payments from that Mandate.
- Under Pause All Payments, preparation continues but nothing can execute.
- If an invoice is required and missing, the run gets `NEEDS_INFORMATION`. This doesn't open a missing-document exception yet.
- Upcoming occurrences don't count as bills due in Cash Analysis until they are prepared.

## Record Expense

Part of Reconcile Payment's Update State step, in `src/payoai/money/expense.py`. An expense exists only after `CONFIRMED`. Failed and pending payments create no expense, and a payment to a counterparty classified as `internal` creates none.

Code takes net and VAT from the extracted invoice fields, and gross from the payment, or leaves net and VAT blank if there's no invoice. A later job in `agent-worker` suggests the category, labelled as a suggestion, so the LLM stays out of `money-worker`. Code builds the journal preview, marked as unreviewed. A crypto payment's GBP value is left as unknown, because no rate source is built.

## Revise Expense

| Expense status | What a change does |
|---|---|
| `DRAFT` or `SUPPORTED` | Creates a new version with a reason, keeping the old value. An Operator can do this. |
| `REVIEWED` | Same, but an Approver must accept it, and the status returns to `SUPPORTED` until they do |
| `ACCOUNTED` | Never changed directly. Creates a reversal and adjustment proposal for the accountant. |

## Chase Missing Document

An exception opens automatically when an expense is `MISSING_EVIDENCE`, in which case no message is drafted. The Routine can also be started from chat.

As built, the chat Routine is one Node covering steps 1-2, and it ends there. Steps 3-5 run later, as plain code, when a document arrives through `POST /api/expenses/{id}/evidence`.

| # | Step | Type | What it does |
|---|---|---|---|
| 1 | Open Exception | Code | Creates one tracked exception per expense and notifies the team |
| 2 | Draft Request | LLM | Optionally drafts a message for the user to send |
| 3 | Extract | LLM + code | The same prompt and quote check as Pay Bill |
| 4 | Match to Payment | Code | Compares the amount, counterparty and date to the actual payment |
| 5 | Update Evidence | Code | Moves it to `SUPPORTED` if it matches, or shows the differences if it doesn't |

It can never create a payment or an obligation. A mismatch keeps the expense `MISSING_EVIDENCE`, and a person decides what to do. PayoAI drafts requests but doesn't send them. Reminders are weekly.

## Purchase Research

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Capture Constraints | Code | Saves budget, specs, delivery need and preferred merchants as typed fields, and asks if the budget is missing |
| 2 | Research | LLM loop | Searches and browses, returning candidates with exact model, price, URL, time and quoted source |
| 3 | Verify Quotes | Code | Confirms each quote appeared on the saved page and drops candidates that fail |
| 4 | Filter and Rank | Code | Removes anything over budget and ranks the rest using a visible rubric based on the constraints |

As built, there is no Explain Node. The Main Loop describes the saved report in its reply, and the reply check applies.

The Research Node receives only the constraints, never business data. It can search and fetch pages but can't use write tools or submit forms. Its steps are capped at 8, and each call is saved as a Trace Step. The report says which pages were read and what the model says wasn't covered. A candidate with no confirmed delivery cost can't be reported as under budget.

## Purchase Checkout

As built, the script makes fixed HTTP calls with `httpx` against the test shop in `merchant_site/app.py`. Playwright isn't used, and no real website is driven. Steps 1-4 are one Node, in `agent-worker`. Steps 6-7 are plain code in `money-worker`, started by an Approver's approval.

| # | Step | Type | What it does |
|---|---|---|---|
| 1 | Check Merchant | Code | Allows supported merchants only, otherwise stops and tells the user to buy it there |
| 2 | Fill Checkout | Script | Creates a cart with the fixed business shipping details |
| 3 | Read Summary | Script | Reads merchant, item, quantity, tax, delivery, address and total |
| 4 | Compare | Code | Compares these to the request and budget |
| 5 | Approve | Tap | An Approver approves the exact summary, through its hash |
| 6 | Re-read and Submit | Script | Reads the summary again and submits only if it's identical |
| 7 | Capture Result | Code | Saves the order number, and records an obligation and an expense |

It has no LLM Nodes. A changed form or a CAPTCHA or login prompt stops the run and hands over to the user. A total over budget stops the run. `money-worker` holds the test card token, and the model never sees it. A timeout after submitting is recorded as `UNKNOWN` and nothing is resubmitted. The order page isn't checked automatically afterwards, so a person has to look. Order placed, payment taken and delivered are recorded separately.

A purchase approval has no expiry, unlike a payment approval. The only supported merchant is the test shop, which takes no real payment.

## Save Preference

| # | Node | What it does |
|---|---|---|
| 1 | Validate | Code checks the type and rejects account numbers, addresses, keys or instructions about checks |
| 2 | Confirm | The card shows the exact text to remember |
| 3 | Save | Stores it with the team, its origin and a link to the run |

As built, Confirm is the Start Confirmation card, and Validate and Save are one Node.

Memory is designed to influence research ranking, suggested categories, wording and which source account is suggested first. As built, only two consumers read it: the Main Loop and category suggestion. Memory can never affect a check result, decision, destination, recipient, approval, Start Confirmation, Mandate limit or role. Nodes that make decisions don't receive memory as input.

Memory is one Postgres table of typed records, read by direct lookup on type and subject. It is separate from conversations and from financial records, which remain the source of truth. Each record holds its scope, type, subject, typed value, origin (`confirmed`, `stated` or `observed`), source IDs, evidence count, status and version. A newer value replaces an older one, which stays in history.

Each consumer receives only what it needs:

| Consumer | Receives | Status |
|---|---|---|
| Main Loop | A short list of active preferences, labelled as preferences | Built |
| Category suggestion | Category hints for that counterparty | Built |
| Purchase Research | Merchant preferences and purchase constraints | Not wired up. It uses the preferred merchants passed in its inputs. |
| Select Source Account | A source account hint, shown as a suggestion on the card | Not wired up |
| Check and decision Nodes | Nothing | Built |

Auto Mode is designed to learn silently from two sources:

- **Explicit statements:** after each turn, a small LLM step sees only the user's message and returns typed candidates. This is built.
- **Repeated actions:** code counts typed events and saves after 3 consistent ones. The counting code exists in `memory/`, but nothing in the product calls it yet.

Documents, web pages, tool results and Main Loop replies are never used. Auto Mode has these limits:

- It is off by default, and an Approver turns it on for the team.
- It stores no free text, only a fixed set of typed preferences.
- Auto-learned memories apply only to the user who taught them. Sharing with the team isn't built.
- It can't save that a recipient is safe or an amount is normal. A pattern check refuses this wording, and it can be missed by other wording.
- Messages over 500 characters are excluded. Pasted text is still a remaining gap, so this is not fully injection-proof.

`tests/test_routines.py` checks that decisions are identical with memory on and off, using a scripted model. The benchmark doesn't test Memory.

Every memory can be listed and deleted through the API, and shows its origin. An edit route and an undo aren't built. Nothing carries across teams. Payment policies and limits aren't memory.

## Replace Bill

A paid bill cannot be edited in chat. The user is told to change it manually.

An unpaid bill is edited by replacing it: the previous bill is marked as a Superseded Bill, any pending approval is cancelled, and the new bill runs through the full Pay Bill flow. The Superseded Bill is kept in the history and linked to its replacement.

The duplicate check ignores the Superseded Bill. A correction is applied on top of the new extraction. A corrected money field shows a warning on the approval card and gives at least `NEEDS_REVIEW`. Payment details can't be corrected into a new destination.

Replacing is only allowed if no money can have moved:

| Payment state | Replace in chat? |
|---|---|
| No payment yet, or awaiting approval | Yes |
| Approved, with no submission attempt yet | Yes, and the approval is cancelled in the same step |
| `SUBMITTING`, `SUBMITTED` or `PENDING` | No, wait for the outcome |
| `UNKNOWN` | No, reconciliation must finish first |
| `REJECTED` or provider-confirmed `FAILED` | Yes, and the failed attempt stays on record |
| `CONFIRMED` or partially paid | No, change it manually |

Approval and replacement exclude each other, so whichever comes first wins.

"Manually" means a dedicated screen for people. It corrects records by creating a new version and keeps the old values. It cannot undo a sent payment. A refund or credit note is a separate record linked to the original.

Revise Expense can still correct an expense's category or business purpose through chat, as a new version, after a bill is paid. It cannot change the amount, recipient or invoice details.
