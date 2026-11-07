# Routines

Decisions from the design grilling. Terms are defined in `CONTEXT.md`.

## Main Loop

The Main Loop answers free-form questions using Read Tools and starts Routines one at a time. It picks up Waiting Runs itself and offers to continue them. It keeps a saved task list for multi-part requests, which gives no authority. There is no separate planner LLM.

Read Tools return typed records with IDs, covering bills and obligations, payments and attempts, counterparties and destinations, balances and alerts, expenses, run and decision details, compliance rules, and document contents labelled as untrusted.

Money facts appear as chips. The Main Loop cites a record's ID, and code renders the record's live values.

A code check runs before each reply is shown. It looks for numbers or dates not in this turn's tool results, words like "paid" or "confirmed" alongside a record whose status disagrees, and claims about a record with no citation. A failing reply is regenerated once, and if it fails again, the raw records are shown instead.

- Team and role come from the session, so a tool can't return another team's data.
- A failed Read Tool gives "couldn't check", never an answer from memory. Each question re-reads the records.
- Code refuses a Routine the user's role doesn't allow.
- Full account numbers, keys and tokens aren't available to any Read Tool. The approval card shows full details on a screen outside the model.
- Legal or tax claims must cite a rule from the ruleset or say the point isn't checked. The Main Loop never says a supplier is safe or an expense is deductible.

## Rules for every Routine

- A Routine is a fixed sequence of Nodes. Routines that need the same step share the same Node, including separate crypto Nodes.
- A Node is either code or an LLM Node. LLM Nodes return typed output only and cannot change a check result, a decision or a state.
- A Routine's configuration says whether it needs a Start Confirmation. Any Routine that sets up a payment, destination or schedule needs one. Code enforces it.
- All checks run even if one fails, so the user sees every problem at once. Nodes that later checks depend on can stop a run early.
- The worst result wins: `BLOCK`, then `NEEDS_REVIEW`, then `NEEDS_INFORMATION`, then `ELIGIBLE_FOR_APPROVAL`.
- Each conversation runs one Routine step at a time. A run waiting for an answer in chat is a Waiting Run. A run waiting for approval sits in the approvals inbox.
- Approval and destination verification are never done through chat.

## Routine list

| Routine | Started from | Start Confirmation |
|---|---|---|
| Pay Bill | Chat | Yes |
| Replace Bill | Chat | Yes |
| Pay Someone | Chat | Yes |
| Prepare Batch | Chat | Yes |
| Execute Payment | Approver's tap | n/a |
| Reconcile Payment | Webhook, timer or timeout | n/a |
| Register Bill | Chat | No |
| Check Bill Compliance | Chat | No |
| Cash Analysis | Chat | No |
| Add Counterparty | Chat | Yes |
| Verify Destination | Dedicated action | Yes |
| Set Up Recurring | Chat | Yes |
| Prepare Occurrence | Timer or matching invoice | n/a |
| Revise Expense | Chat | Yes |
| Chase Missing Document | Chat | No |
| Purchase Research | Chat | No |
| Purchase Checkout | Chat | Yes |
| Save Preference | Chat | Yes |

The target is this full scope. If time runs short, cut Purchase Checkout first, then Set Up Recurring and Prepare Occurrence together.

Pause All Payments is a button available to Approvers, not a Routine, so an emergency stop never depends on a model.

## Pay Bill

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Load Document | Code | Saves the original file and its hash |
| 2 | Extract Fields | LLM + code | The LLM extracts typed fields and quotes its source for each. Code confirms each quote is in the document. |
| 3 | Match Counterparty | Code | Looks up the registry and asks the user if the match is unclear |
| 4 | Check Duplicates | Code | Compares invoice number, amount, period, file hash and past payments |
| 5 | Check Compliance | Code | Runs the applicable UK rules and arithmetic checks |
| 6 | Check Destination | Code | Compares the invoice's payment details to the Verified Destination, with separate fiat and crypto versions |
| 7 | Check Cash | Code | Checks balances, reserve and fees |
| 8 | Decide | Code | Combines all results into one decision |
| 9 | Investigate | LLM | Runs only on `NEEDS_REVIEW` or `NEEDS_INFORMATION` |
| 10 | Explain | LLM | Writes the plain-English reason from the results |
| 11 | Prepare Payment | Code | Builds the exact payment and its hash, only if eligible |
| 12 | Await Approval | Pause | Shows the approval card |

Extract Fields and Match Counterparty can stop a run early.

Investigate has Read Tools only and a capped number of steps. Every finding must cite a record and is labelled as the agent's suggestion. It doesn't run on `BLOCK`. If it fails, the Routine carries on without it.

## Pay Someone

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Match Counterparty | Code | Shared with Pay Bill |
| 2 | Select Destination | Code | Picks a Verified Destination that fits the requested asset. Asks if several fit and stops if none do. |
| 3 | Select Source Account | Code | Uses the only account holding that asset, or asks if there are several |
| 4 | Register Obligation | Code | Records what's owed, with the request as its source |
| 5 | Check Duplicates | Code | Looks for the same recipient and a similar amount in a recent period |
| 6 | Check Destination | Code | Shared, and confirms the destination is still valid |
| 7 | Check Cash | Code | Shared |
| 8 | Check Amount | Code | Compares the amount to this counterparty's usual range |
| 9-13 | Decide, Investigate, Explain, Prepare Payment, Await Approval | Mixed | Shared |

A payment with no invoice is allowed:

- The Routine requires a stated purpose and asks if none was given.
- The approval card shows "no invoice attached".
- Once the payment is confirmed, the expense is marked `MISSING_EVIDENCE` and a Chase Missing Document exception opens.
- A first payment to someone, or an amount outside their usual range, gives `NEEDS_REVIEW`.
- The same recipient and amount in a recent period gives `NEEDS_REVIEW` and shows the earlier payment. An exact repeat of the same obligation gives `BLOCK`.

Currency is never converted silently. If no Verified Destination or source account fits the requested asset, the Routine stops and says so.

## Prepare Batch

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Select Bills | Code | Finds unpaid bills using the date range from the Main Loop |
| 2 | Check Each Bill | Shared | Runs the shared checks on each bill, reusing extractions |
| 3 | Allocate Cash | Code | Runs cash across the batch for each currency separately |
| 4 | Decide Each Bill | Code | Gives each bill its own decision |
| 5 | Investigate and Explain | LLM | Same rules as Pay Bill, with a capped number of bills per batch |
| 6 | Prepare Payments | Code | Builds one exact payment and hash per eligible bill |
| 7 | Await Approval | Pause | Shows one batch card |

Allocate Cash orders bills by earliest due date, then smaller amount. A bill that would break the reserve gets `NEEDS_REVIEW` with the shortfall. Currencies stay separate and nothing is converted. If the Approver changes the selection, Allocate Cash re-runs before anything can be approved.

A batch never pauses for questions. Bills needing information are listed together at the end, and the rest carry on.

| Bill's decision | On the card | Can it be approved here? |
|---|---|---|
| `ELIGIBLE_FOR_APPROVAL` | Ticked, with amount and recipient | Yes |
| `NEEDS_REVIEW` | Unticked, with the reason | Only after opening it and clearing it individually |
| `NEEDS_INFORMATION` | Listed separately with its question | No |
| `BLOCK` | Listed with the reason | No, and it can't be selected |

One tap creates a separate approval for each payment, each bound to its own hash. Payments run one at a time, and a failed or `UNKNOWN` payment doesn't stop the others. Cash is re-checked at execution, and a payment that no longer fits is stopped rather than sent.

## Approval

A card in chat and an entry in the approvals inbox are two views of one approval record. Approval is never typed in chat. Any Approver can decide, whoever started the run, and the first decision to reach the database wins, including a rejection. A rejected payment needs a new payment and a new approval to try again.

Every tap sends the approval's ID and the payment hash shown on that screen. In one database transaction, the server sets the status only if it is still `PENDING`, the hash matches, the approval hasn't expired, the person is still an Approver and Pause All Payments is off. If the tap wins an approval, Execute Payment is queued in the same transaction. Otherwise the server reads the record and says why.

- A repeat tap by the same person returns the same result, and nothing runs twice.
- The database allows one active attempt per payment.
- Replace Bill cancels an approval with the same "only if still `PENDING`" update.
- In a batch, each payment gets its own update, and the tap reports which were already decided.
- Cards are rendered from the live record, and updates are pushed to open screens. Safety comes from the database check, not the push.
- The tap doesn't go through LangGraph. The run then gets a signal to move on, and receiving it twice has no effect.
- Losing and stale taps are saved as Trace Steps.

## Execute Payment

Started only by an Approver's tap. It has no LLM Nodes, and the model cannot call it or see credentials or signing keys.

| # | Node | What it does |
|---|---|---|
| 1 | Verify Approval | Confirms the approval exists, hasn't expired and matches the payment's hash |
| 2 | Re-check Authority | Confirms the Approver still has the role and Pause All Payments is off |
| 3 | Re-check Payment | Confirms the destination is still verified, cash is sufficient and the fee is within the approved maximum |
| 4 | Record Attempt | Saves the attempt and its fixed idempotency key before anything is sent |
| 5 | Submit | Calls the provider, or signs and broadcasts for crypto |
| 6 | Record Result | Saves what came back and hands over to Reconcile Payment |

If Nodes 1-3 fail, nothing is sent and the payment needs a fresh approval. Approvals expire after 24 hours by default, set per team.

| What happened | Result | Automatic resend? |
|---|---|---|
| Connection failed before the request was sent | Still unsent | Yes, once, with the same idempotency key |
| Timeout or dropped connection after sending | `UNKNOWN` | No |
| Server error from the provider | `UNKNOWN` | No |
| Clear rejection | `REJECTED` | No |
| Accepted | `SUBMITTED` | Not needed |

For crypto, the signed transaction and its hash are saved before broadcasting. After a timeout, only those exact bytes can be sent again. A new signature for the same payment is forbidden unless reconciliation proves the first transaction can never land.

Only Reconcile Payment can move a payment out of `UNKNOWN`. Sending again after a confirmed failure needs a new payment and a new approval.

Whether each provider's sandbox enforces idempotency keys must be tested during the integration spike.

## Reconcile Payment

Started by a webhook, a timer or an `UNKNOWN` result. It has no LLM Nodes. Webhooks only prompt a check, and the status comes from asking the provider or chain directly.

| # | Node | What it does |
|---|---|---|
| 1 | Verify Trigger | Checks the webhook's signature and ignores duplicates. An invalid signature is logged and goes no further. |
| 2 | Query Source | Asks the provider or chain directly, by external ID or by our reference |
| 3 | Compare Outcome | Compares the amount, currency, recipient and reference to the approved payment |
| 4 | Update State | Applies the proof rules and saves every observation as evidence |
| 5 | Follow Up | On a final state, updates the obligation, expense record and cash, and notifies people |

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
- Results are labelled as simulated or sandbox-observed.

## Add Counterparty

Creates or updates a contact. Any new bank or wallet details are saved as `UNVERIFIED`, with where they came from. An existing Verified Destination stays valid until a new one is verified, and an unverified destination can't be selected for a payment.

## Verify Destination

A dedicated action on a screen, never in chat.

| Method | What the Approver records | Strength |
|---|---|---|
| Call back | Called a number on file before the request, spoke to a named person and confirmed the full details | Strong |
| Signed contract | A contract already on file names these exact details | Strong |
| Provider check | The provider's name match, if the sandbox offers one | Supporting only |
| Crypto wallet | The counterparty confirmed the full address through a channel on file, or signed a message with that wallet in response to a request sent through that channel | Strong |

- The contact channel must have been on file before the details came in.
- Method, date and an attachment or note are required.
- The Approver sees the full IBAN or address when confirming.
- Verifying a new destination doesn't update existing payments. They need preparing and approving again.
- Revoking takes effect immediately and cancels pending approvals using that destination.
- A new counterparty has no earlier channel, so the Approver records how the relationship was established. This shows as weaker trust, and the first payment gets `NEEDS_REVIEW`.
- One person may both add and verify. The approval card and audit trail show "added and verified by the same person". Requiring two people can come later as a team setting.

PayoAI enforces the process and records the evidence, but it can't prove a call happened.

## Register Bill

Uses Pay Bill Nodes 1-8 and Explain, plus Investigate on `NEEDS_REVIEW` or `NEEDS_INFORMATION`. It saves a payable and prepares no payment.

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

Code selects the rules from extracted facts, such as whether VAT is charged and whether a VAT number appears. If the type is still unclear, an LLM can suggest one, and an uncertain outcome gives `NEEDS_REVIEW`.

The starting scope is standard UK domestic VAT invoices and invoices from non-VAT-registered suppliers. Rounding is a versioned ruleset setting, checked against HMRC's current guidance before coding.

## Cash Analysis

Started from chat, once a day on a timer, and after a bill is registered or a payment reaches a final state.

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Refresh Balances | Code | Fetches current balances and records when each was last updated |
| 2 | Normalise | Code | Corrects for whether each provider's "available" figure already excludes in-flight payments |
| 3 | Project | Code | Calculates the 7-day position per currency |
| 4 | Raise Alerts | Code | Creates or updates shortfall alerts without repeating them |
| 5 | Explain | LLM | Writes the summary, with figures taken only from the Project results |

| Item | Treatment |
|---|---|
| Confirmed balance | The starting point |
| Payment in flight | Deducted, unless the provider's figure already excludes it |
| Approved, not yet sent | Deducted as reserved |
| Bill due, with no approved payment | Deducted by its due date, shown as due |
| Reserve | Deducted |
| Expected income | Never included in the number. Shown separately as "if received". |
| Other currencies | Never added in |

Each bill counts in only one row.

Beyond a set age (15 minutes in the demo), a balance is labelled stale and the result can't report that everything is fine. Freshness is a typed field that Explain must include. Check Cash gives `NEEDS_REVIEW` on stale data, and Execute Payment stops the payment if it can't get a fresh balance.

An alert is identified by team, account and shortfall date. It re-notifies only if the shortfall gets materially worse or a new bill causes it. Cash Analysis can suggest fixes but can't move money.

A simple balance question stays a Read Tool answer.

## Set Up Recurring

Saves a Mandate and an Automation Rule. An Operator can draft a Mandate, but only an Approver can activate it, on a dedicated screen. Setting it up never pays anything. Any change creates a new version that needs activating again.

A Template is separate and has no authority. One tap starts Pay Someone.

## Prepare Occurrence

Started by a timer or a matching invoice. The Main Loop isn't involved, and it runs under a limited system identity tied to that team and Mandate.

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Claim Occurrence | Code | Claims this Mandate and period. The database allows one claim, so a second one stops here. |
| 2 | Check Mandate | Code | Confirms it's active and the amount, destination, date and evidence fit its limits |
| 3 | Register Obligation | Code | Records what's owed and links it to the Mandate |
| 4-11 | Shared Nodes | Mixed | Check Duplicates through Await Approval, as in Pay Someone |

- Every occurrence needs its own approval.
- If the timer and an invoice both arrive for a period, the first claims it and the other attaches to it, so there is only one payment.
- An amount above the Mandate's maximum gives `NEEDS_REVIEW` with both figures. A lower-amount payment is never prepared automatically instead.
- A timer run after an outage is labelled as late. Each missed period gets its own visible occurrence and approval.
- Pausing or revoking stops new occurrences and cancels unapproved prepared payments from that Mandate.
- Under Pause All Payments, preparation continues but nothing can execute.
- A fuzzy invoice match can't override a changed destination, a different currency or a period already claimed.
- A contract on file can meet the evidence requirement. If an invoice is required and missing, the run gets `NEEDS_INFORMATION` and a Chase Missing Document exception opens.
- Upcoming occurrences inside the 7-day window count as bills due in Cash Analysis.

## Record Expense

A Node inside Reconcile Payment's Follow Up step. An expense exists only after `CONFIRMED`. Failed and pending payments create no expense, and transfers between a team's own accounts are recorded as transfers.

Code takes net, VAT and gross from the extracted invoice fields, or leaves them blank and flagged if there's no invoice. An LLM Node suggests the category, labelled as a suggestion. Code builds the journal preview, marked as unreviewed. A crypto payment's GBP value is recorded with its rate source and time, or left as unknown if there's no rate.

## Revise Expense

| Expense status | What a change does |
|---|---|
| `DRAFT` or `SUPPORTED` | Creates a new version with a reason, keeping the old value. An Operator can do this. |
| `REVIEWED` | Same, but an Approver must accept it, and the status returns to `SUPPORTED` until they do |
| `ACCOUNTED` | Never changed directly. Creates a reversal and adjustment proposal for the accountant. |

## Chase Missing Document

Starts automatically when an expense is `MISSING_EVIDENCE`, or from chat.

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Open Exception | Code | Creates one tracked exception per expense and notifies the team |
| 2 | Draft Request | LLM | Optionally drafts a message for the user to send |
| 3 | Await Document | Pause | Waits for an upload |
| 4 | Load and Extract | Shared | The same Nodes as Pay Bill |
| 5 | Match to Payment | Code | Compares the amount, counterparty and date to the actual payment |
| 6 | Update Evidence | Code | Moves it to `SUPPORTED` if it matches, or shows the differences if it doesn't |

It can never create a payment or an obligation. A mismatch keeps the expense `MISSING_EVIDENCE`, and a person decides what to do. PayoAI drafts requests but doesn't send them. Reminders start weekly.

## Purchase Research

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Capture Constraints | Code | Saves budget, specs, delivery need and preferred merchants as typed fields, and asks if the budget is missing |
| 2 | Research | LLM loop | Searches and browses, returning candidates with exact model, price, URL, time and quoted source |
| 3 | Verify Quotes | Code | Confirms each quote appeared on the saved page and drops candidates that fail |
| 4 | Filter and Rank | Code | Removes anything over budget and scores the rest using a visible rubric based on the constraints |
| 5 | Explain | LLM | Writes the comparison and flags unknowns |

The Research Node receives only the constraints, never business data. It can search and fetch pages but can't use write tools or submit forms. Its steps are capped, and every query and URL is logged. The report says what was and wasn't searched. A candidate with no confirmed delivery cost can't be reported as under budget.

## Purchase Checkout

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Check Merchant | Code | Allows supported merchants only, otherwise gives the user a link |
| 2 | Fill Checkout | Script | A fixed script per merchant fills in approved business details |
| 3 | Read Summary | Script | Reads merchant, item, quantity, tax, delivery, address and total |
| 4 | Compare | Code | Compares these to the request and budget |
| 5 | Await Approval | Pause | Approves the exact summary, with its hash |
| 6 | Re-read and Submit | Script | Reads the summary again and submits only if it's identical |
| 7 | Capture Result | Code | Saves the order number and receipt, and records an obligation |

It has no LLM Nodes. A changed form, domain change, CAPTCHA or login prompt stops the run and hands over to the user. A total over budget stops the run, and continuing needs a new approval. The Executor holds the test card token, and the model never sees it. A timeout after submitting is `UNKNOWN`, the order page is checked, and nothing is resubmitted blindly. Order placed, payment taken and delivered are recorded separately.

The demo uses one small test merchant site. If a research result isn't sold there, the user gets a link.

## Save Preference

| # | Node | What it does |
|---|---|---|
| 1 | Validate | Code checks the type and rejects account numbers, addresses, keys or instructions about checks |
| 2 | Confirm | The card shows the exact text to remember |
| 3 | Save | Stores it with who approved it, when, the team and a link to the conversation |

Memory can influence research ranking, suggested categories, wording and which source account is suggested first. A suggested source account is always shown on the approval card. Memory can never affect a check result, decision, destination, recipient, approval, Start Confirmation, Mandate limit or role. Nodes that make decisions don't receive memory as input.

Memory is one Postgres table of typed records, read by direct lookup on type and subject. It is separate from conversations and from financial records, which remain the source of truth. Each record holds its scope, type, subject, typed value, origin (`confirmed`, `stated` or `observed`), source IDs, evidence count, status and version. A newer value replaces an older one, which stays in history.

Each consumer receives only what it needs:

| Consumer | Receives |
|---|---|
| Main Loop | A short list of active preferences, labelled as preferences |
| Purchase Research | Merchant preferences and purchase constraints |
| Category suggestion | Category hints for that counterparty |
| Select Source Account | A source account hint, shown as a suggestion on the card |
| Check and decision Nodes | Nothing |

Auto Mode learns silently from two sources only:

- **Explicit statements:** after each turn, a small LLM Node sees only the user's messages and returns typed candidates.
- **Repeated actions:** code counts typed events and saves after 3 consistent ones.

Documents, web pages, tool results and Main Loop replies are never used. Auto Mode has these limits:

- It is off by default, and an Approver turns it on for the team.
- It stores no free text, and only `merchant_preference`, `category_hint`, `purchase_constraint`, `reply_style` and `source_account_hint`.
- Auto-learned memories apply only to the user who taught them. Sharing with the team needs one tap.
- Each memory appears on the Memory screen, labelled as learned automatically, with its source and an undo.
- Acting against a memory resets its count, and repeating the new behaviour replaces it.
- It can't save that a recipient is safe or an amount is normal.
- Long pasted blocks are excluded by a length check. Pasted text is still a remaining gap, so this is not fully injection-proof.

The benchmark asserts that no memory comes from a document, and that decisions are identical with memory on and off.

Every memory can be seen, edited and deleted on a screen, and shows its source. Nothing carries across teams. Payment policies and limits aren't memory, and only an Approver can change them on a dedicated screen.

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
