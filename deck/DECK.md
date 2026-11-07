# PayoAI judging deck

Slide-by-slide content, with short slide text and speaker notes. The judges score from this deck, so the evidence has to be on the slides.

**Before this goes out:** every `[[RESULT: ...]]` must be replaced with a number from a real run, using the table at the end. A result that misses a benchmark stays in as it is. If a run didn't happen, say so on the slide instead of filling the cell. Items marked `[[CONFIRM: ...]]` depend on work that wasn't finished when this was written.

**Every results slide carries this label:** "Synthetic data written by the PayoAI team. Simulated payment provider. Not real-world performance."

| Slide | Deliverable | Judging criterion |
|---|---|---|
| 1. Problem | The problem | Relevance and impact (20%) |
| 2. User | Who it helps | Relevance and impact |
| 3. What it does | | Relevance and impact, ambition (15%) |
| 4. Architecture | | Ambition and technical quality |
| 5. Where the LLM is allowed | Limits | Traceable reasoning (25%), ambition |
| 6. One decision, traced | | Traceable reasoning |
| 7. Data | Your data | Testing on unseen data (25%) |
| 8. Evaluation | Evaluation against a benchmark | Testing on unseen data |
| 9. Results | Key statistics | Testing on unseen data |
| 10. Controls | Limits and risk controls | Risk controls and oversight (15%) |
| 11. Limits | Limits | Testing on unseen data, risk controls |

---

## Slide 1: The problem

**Is it safe to pay this bill now, and can we afford it?**

A small business owner answers this alone, between other jobs, from an invoice and a banking app.

Three mistakes hurt:

- Paying a fraudster's changed bank details
- Paying the same bill twice
- Missing a payment because cash ran short

**Speaker notes:** Nobody at a small business has the job of checking this. The owner sees an invoice saying "our bank details have changed", and nothing in the banking app compares it with last month's. We aren't quoting a national fraud figure, because we haven't sourced one we'd stand behind. The point is what these mistakes cost one business, and a payment sent to a fraudster may not come back.

---

## Slide 2: Who it helps

**A UK small business owner with no finance team.**

Our example person is Sam, who runs Northwind Studio Ltd. (Sam and Northwind are fictional.)

| What Sam gets wrong or does too slowly | What Sam needs |
|---|---|
| Trusts changed details on an invoice | A check against details already verified |
| Can't easily recall what was already paid | A check against every earlier bill |
| Doesn't know the cash position after bills due | The position after bills, payments in flight and a reserve |
| Doesn't have time to check VAT invoice details | The missing items, listed for the accountant |

**Sam keeps the final decision.** PayoAI never pays on its own.

**Speaker notes:** We picked one person and one decision, as the brief asks. We haven't interviewed real owners, so this person is our reasoned assumption and not research. PayoAI also supports a second role, so a staff member can prepare a payment that only Sam can approve.

---

## Slide 3: What it does

```text
Bill or question -> reads the invoice -> runs fixed checks -> one decision -> Sam approves -> pays -> confirms with the provider
```

| The failure | What happens on screen |
|---|---|
| **Changed bank details** | `BLOCK`. No payment is prepared. The invoice's IBAN doesn't match the Verified Destination, and the owner is told to verify through a channel already on file. |
| **Paying twice** | Same invoice or file: `BLOCK`. Same amount recently: `NEEDS_REVIEW`. A timeout after sending is `UNKNOWN`, and it is never resent. |
| **Cash runs short** | `NEEDS_REVIEW`, stating the shortfall against the reserve. A daily 7-day projection raises alerts. A stale balance can't report "fine". |

**What a conventional program couldn't do:** read invoices in layouts it hasn't seen, quote its source for each field, and answer free-form questions in chat, with code checking the answers.

**Speaker notes:** The value is the combination. A fixed program can compare two IBANs, but it can't read an invoice laid out in a way nobody wrote a template for. A model can read that invoice, but it shouldn't decide whether to pay. Slide 9 tests that claim against both alternatives. PayoAI warns about a shortfall, but it can't fix one, because it never moves money between accounts.

---

## Slide 4: Architecture

```text
 Web page ---> API -----------------> Postgres: records, jobs, traces, audit
                |                        ^              ^
                | queues a job           |              |
                v                        |              |
          agent-worker ------------------+              |
          Main Loop and Routines                        |
          Holds the Gemini key, no payment secrets      |
                                                        |
          Approver's tap -> approval check -> job       |
                                               |        |
                                               v        |
                                          money-worker -+
                                          Execute and Reconcile, plain code
                                          Calls no LLM
                                               |
                                               v
                                          Simulated provider
                                          (Airwallex, Wise, Circle: not built)
```

- **Each process loads only its own secrets.**
- **Routines:** fixed LangGraph sequences of Nodes.
- **Main Loop:** a plain loop, with the conversation stored by Gemini's API.
- **Money path:** plain code and a Postgres job queue, with no framework resume behaviour to depend on.
- **Models:** `gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.5-flash-lite`.

**Speaker notes:** The important line is between `agent-worker` and `money-worker`. The model runs in one process, and payments run in another that has no job handler calling a model. All processes share one database credential, so this separates secrets and not database rights. Real use would need separate database roles. It runs locally for the demo, against Supabase Postgres in a private schema holding synthetic data only.

---

## Slide 5: Where the LLM is and isn't allowed

| The LLM may | The LLM may not |
|---|---|
| Extract invoice fields, quoting the document | Decide whether a bill is eligible |
| Answer questions using read-only tools | Change a check result or a state |
| Start a Routine, which code then restricts | Approve a payment or verify a destination |
| Suggest findings, citing real records | Supply bank or wallet details for a payment |
| Word an explanation of a decision | See verified account numbers, keys or tokens |
| Suggest an expense category | Call Execute Payment or Reconcile Payment |

**Code checks what the model says:**

- Each key extracted value must be quoted from the document, or the result is `NEEDS_REVIEW`.
- A finding citing an unknown record is dropped.
- A chat reply with figures outside this Turn's tool results is regenerated once, then replaced by raw records.

**Speaker notes:** The Main Loop has no tool for approval, verification or execution. The tools don't exist for it, so nothing depends on its instructions being followed. Invoice text is labelled as untrusted data, and the checks compare fields in code, so injected text can't change a comparison. One weak point we should name: code doesn't check Explain's wording. It only falls back to a plain list if the call fails. An LLM judge scores a sample in evaluation.

---

## Slide 6: One decision, traced from raw data to output

A changed-details invoice:

| Step | Who | What is recorded |
|---|---|---|
| 1. Raw document | Code | File and SHA-256 hash |
| 2. Extract Fields | **LLM** | Fields and quotes, labelled untrusted. Code confirms the quotes. |
| 3. Check Destination | Code | Rule `DESTINATION_CHANGED`, outcome `BLOCK`, the invoice's IBAN and the masked verified one |
| 4. Other checks | Code | Every check runs, so all problems show at once |
| 5. Decide | Code | Worst result wins. Policy version `payment-risk-1.0`. |
| 6. Explain | **LLM** | Plain-English wording only. It can't change step 5. |

**The maths is kept apart.** Amounts are integer minor units, converted with exact decimals. Totals, VAT, cash and the payment hash are computed by code, never by the model.

**Every step is saved when it starts and updated when it ends.** LLM steps also record the model, thinking level, tokens and cost. Financial events go to an append-only audit table, and a database trigger rejects changes.

**Speaker notes:** A professional could check this without trusting the model. They read the rule, the inputs and the evidence for each check. Each compliance result carries its source and each decision carries a policy version. There is one float in the maths path: the VAT rate in the per-line VAT check, whose result is rounded and compared within a penny. For a paid bill, `GET /api/payments/{id}/trace` gives the whole trail through to the provider's status. For a blocked bill with no payment, `GET /api/bills/{id}` gives its checks and decision. `[[CONFIRM: which screen shows this trace in the final frontend]]`

---

## Slide 7: Our data

**All synthetic. Written by us, using a script. No external dataset, so no dataset licence applies.**

| Split | Cases | Suppliers | Layouts |
|---|---|---|---|
| Development | 90 | 6 | labelled, table, email |
| Validation | 20 | 3 | labelled, email |
| **Held-out** | **40** | **6 new** | **compact, letter (new)** |

Held-out contains 5 legitimate, 6 invalid, 11 fraud, 4 duplicate, 3 cash and 11 provider-fault cases.

**Held-out differs in:** supplier names, layouts, seed and 5 scenario combinations never seen in development.

**It doesn't differ in:** who wrote it, or most of the rules it tests.

**Speaker notes:** A seeded script writes each case by editing a clean invoice and stating the right answer. Expected results come from the scenario's definition, not from running PayoAI. They aren't independent though, because we wrote both and the scenarios name our own rule IDs. There are only 5 legitimate held-out cases, so a false-block figure rests on very few. The full account is in `deck/DATA.md`.

---

## Slide 8: How we evaluated it

**Benchmark: the two obvious ways to build this.**

| System | What it is | What it tests |
|---|---|---|
| **LLM-only** | The same model with a direct send-payment tool, told to be careful | What the fixed checks add in safety |
| **Rules-only** | The same checks with template extraction and no LLM steps | What the model adds in completion |
| **PayoAI** | Both | The combination |

**Three suites, all on Pay Bill:**

- **Full Agent:** real model, from message and document to final state
- **Routines:** checks only, with the model replaced by correct fields
- **Faults:** timeouts, errors, pending states and mismatches, with the model replaced

**Code judges safety,** using the simulator's own ledger. An LLM judge only scores wording and can't override code.

**The held-out split is hash-locked and unlocked by an explicit phrase. Each run is logged.**

**Speaker notes:** Read the comparison carefully. Against LLM-only, compare unsafe counts rather than pass rates, because that baseline gives no typed decision, so a sensible refusal still fails the decision check. Against rules-only, use Full Agent, because the Routines suite gives PayoAI perfect extraction. The rules-only templates only fit the labelled layout, which isn't in held-out, so that comparison favours PayoAI by construction. We show both to be open about it. The eval's human is a script that approves clean payments and leaves anything flagged. So `NEEDS_REVIEW` cases test the flag, not what a real person would do next. Only `BLOCK` cases fully test that code stops the money.

---

## Slide 9: Results on the held-out set

*Synthetic data written by the PayoAI team. Simulated payment provider. Not real-world performance.*

| Measure | PayoAI (Full Agent) | LLM-only | Rules-only |
|---|---|---|---|
| **Unsafe executions** | `[[RESULT: unsafe executions, PayoAI Full Agent, held-out, count of cases]]` | `[[RESULT: unsafe executions, LLM-only, held-out, count of cases]]` | `[[RESULT: unsafe executions, rules-only, held-out, count of cases]]` |
| Wrong recipient | `[[RESULT: wrong recipient, PayoAI Full Agent, held-out, count]]` | `[[RESULT: wrong recipient, LLM-only, held-out, count]]` | `[[RESULT: wrong recipient, rules-only, held-out, count]]` |
| Duplicate or unauthorised payment | `[[RESULT: duplicate or unauthorised, PayoAI Full Agent, held-out, count]]` | `[[RESULT: duplicate or unauthorised, LLM-only, held-out, count]]` | `[[RESULT: duplicate or unauthorised, rules-only, held-out, count]]` |
| Legitimate bills completed | `[[RESULT: legitimate completed, PayoAI Full Agent, held-out, count of 5]]` | `[[RESULT: legitimate completed, LLM-only, held-out, count of 5]]` | `[[RESULT: legitimate completed, rules-only, held-out, count of 5]]` |
| Cases fully correct | `[[RESULT: cases passed, PayoAI Full Agent, held-out, count of cases]]` | Not comparable, see notes | `[[RESULT: cases passed, rules-only, held-out, count of cases]]` |
| Cost per case | `[[RESULT: model cost per case, PayoAI Full Agent, held-out, USD]]` | `[[RESULT: model cost per case, LLM-only, held-out, USD]]` | No model cost |

**Also on PayoAI:**

- Checks only (Routines suite): `[[RESULT: cases passed, PayoAI Routines suite, held-out, count of cases]]`
- Faults, no duplicate sent: `[[RESULT: cases passed, PayoAI Faults suite, held-out, count of cases]]`
- Extraction, fields correct: `[[RESULT: extraction fields right, PayoAI Full Agent, held-out, count of total]]`
- Reported outcome matched the provider's ledger: `[[RESULT: outcome truthful, PayoAI Full Agent, held-out, count of cases]]`
- Time per case, median and 95th percentile: `[[RESULT: latency median and p95, PayoAI Full Agent, held-out, seconds]]`
- Explain's wording, LLM judge, mean score out of 5: `[[RESULT: semantic faithful and uncertainty means, sample size, held-out]]`

**Uncertainty:** `[[RESULT: exact 95% binomial interval for unsafe executions, PayoAI Full Agent, held-out]]`. With this few cases, the interval is wide.

**What failed:** `[[RESULT: every failed held-out case, PayoAI Full Agent, case ID and reason]]`

**Runs of the held-out set:** `[[RESULT: number of held-out runs, and each result if more than one]]`

**Speaker notes:** Say the numbers as counts, and read out the failures. The interval only describes this synthetic set, because these cases aren't a random sample of real invoices. Nothing here tells you how often real fraud would be caught. Cost is model tokens at list price, with no provider fees, because the simulator charges none. If PayoAI misses a benchmark, this slide says so plainly.

---

## Slide 10: Risk controls and oversight

**Limits enforced in code:**

| Control | Where it is enforced |
|---|---|
| Only a person with the Approver role can approve | One conditional database statement, and re-checked before sending |
| Approval binds to one exact payment | SHA-256 hash, shown on screen and recomputed before sending. A trigger stops it being rebound. |
| Payments only go to Verified Destinations | Select Destination reads only verified records |
| No duplicate sends | Unique indexes on attempts, bills, jobs and invoices. A saved attempt is never resent. |
| Reserve and fresh balance required | Check Cash, then re-checked before sending |
| Approvals expire | 24 hours by default |

**Every action logged:** Trace Steps for each step, and an append-only audit table.

**Shown live: Pause All Payments.** An Approver presses it. A pending payment can no longer be approved: "New payments are paused for this team." No model takes part.

**Also shown live:** an Operator's approval is refused, and a changed-details invoice is blocked.

**Speaker notes:** The pause check sits inside the same database statement as the approval, and Execute Payment checks it again, so a payment approved before the pause but not yet sent still stops. An Approver can still reject during a pause. There's no per-payment spending limit beyond the reserve and the Approver's decision. A `NEEDS_REVIEW` payment needs a "reviewed" flag from the client, which records a person's statement, not proof that they looked.

---

## Slide 11: Our limits, stated plainly

- **Synthetic data, written by the same team that wrote the agent.** We tested failures we had already thought of.
- **Simulated provider.** No fees, no real settlement and no real bank behaviour.
- **No real rails.** Airwallex, Wise and Circle adapters aren't built. Crypto is simulated, and nothing is signed or broadcast. No real money has moved.
- **Benchmark covers Pay Bill only.** Other Routines have unit tests with a scripted model.

**Not tested:**

- Real invoices, PDFs or scans in the benchmark
- Real users, or a careless human approver
- Whether a verification call really happened. We record it, and we can't prove it.
- Any real provider's idempotency or webhook behaviour

**Not production-ready:** demo sign-in with no password, one shared database credential, and a UK VAT ruleset with no accountant review. The rounding tolerance and VAT number pattern are our own settings, with no confirmed source. VAT numbers aren't checked with HMRC.

**Code:** https://github.com/nikolasss4/payoai-uclhack

**Speaker notes:** We would rather you hear these from us. The most important one is the first: a good result here means the code does what we intended, on cases we imagined. Before real money, this would need real invoices with consent, an independent test set, a real provider sandbox, separate database roles, real authentication and an accountant's review. PayoAI gives no legal, tax or accounting advice.

---

## Filling in the results

Each placeholder comes from a summary file in `evals/results/`, which is git-ignored, or from `eval_runs`. All commands use `PYTHONPATH="$PWD:$PWD/src" uv run python -m evals.runner`, and held-out needs `--split heldout --unlock-heldout` with the exact phrase. This matches `evals/runner.py` at commit `d94d88b`, so check it hasn't changed.

| Column or line | Arguments |
|---|---|
| PayoAI Full Agent | `--suite full_agent --system payoai`, with `--judge-sample N` for the wording score |
| PayoAI Routines suite | `--suite routines --system payoai` |
| PayoAI Faults suite | `--suite faults --system payoai` |
| LLM-only | `--suite full_agent --system llm_only` |
| Rules-only | `--suite routines --system rules_only` and `--suite faults --system rules_only`, added together |

| Placeholder | Key in the summary |
|---|---|
| unsafe executions | `unsafe_executions` out of `cases` |
| wrong recipient | `wrong_recipient` |
| duplicate or unauthorised | `duplicate_or_unauthorised` |
| legitimate completed | `legitimate_completed` out of `legitimate_cases` |
| cases passed | `passed` out of `cases` |
| model cost per case | `cost_usd_per_case` |
| extraction fields right | `extraction_fields_right` out of `extraction_fields_total` |
| outcome truthful | `outcome_truthful` out of `cases` |
| latency | `latency_ms_median` and `latency_ms_p95` |
| semantic means | `semantic_faithful_mean`, `semantic_uncertainty_mean` and `semantic_sampled` |
| fraud category passed (used in `deck/PITCH.md`) | `by_category.fraud.passed` out of `by_category.fraud.cases` |
| failed cases | `failures`, which the summary cuts off at 40 |
| errors, to report if not zero | `errors` |
| number of held-out runs | Count the rows in `eval_runs` where `split` is `heldout` |
| exact binomial interval | Compute it from the unsafe count and `cases` |
