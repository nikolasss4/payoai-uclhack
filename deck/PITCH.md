# PayoAI live pitch

Four minutes, then two minutes of questions. Every number comes from `deck/DECK.md`, and each `[[RESULT: ...]]` must be filled from a real run before rehearsal.

## Before going on

- On the demo laptop, run `uv run python -m payoai.seed`, then `scripts/dev.sh`. Keep a screen recording ready as the backup.
- Prepare two chats in advance, because a live model call may take longer than the pitch can wait. Latency is `[[RESULT: latency median and p95, PayoAI Full Agent, held-out, seconds]]`.
  - **Chat A:** a clean invoice, confirmed through to a pending approval card.
  - **Chat B:** an invoice with changed bank details, ready to send or already blocked.
- Sign in as Jo (Operator) and Sam (Approver) in two browser windows. Both are fictional seeded users.
- `[[CONFIRM: the approval card, the Pause All Payments button and the blocked result all exist in the final frontend. If not, drive the demo from the recording.]]`
- `[[CONFIRM: the seeded supplier is renamed or labelled as fictional. The seed uses the name of a real company, so don't present an invoice from it as if it were real.]]`

If you show a run prepared earlier, say so out loud.

## Script

### 0:00 The problem (30 seconds)

> This is Sam. Sam is fictional, but the job is real. Sam runs a small UK studio with no finance team. An invoice arrives saying "our bank details have changed". Sam has one question: is it safe to pay this bill now, and can we afford it?
>
> Three mistakes hurt here. Paying a fraudster. Paying the same bill twice. Or missing a payment because the cash ran short. PayoAI is built for that one decision.

### 0:30 The failure, on screen (50 seconds)

*Show Chat B. Point to the blocked result and the reason.*

> Here's the failure that would hurt most. This invoice looks normal, but its bank details differ from the ones Sam verified. It also contains a line telling any AI assistant to skip the checks.
>
> The model read the invoice, and that is all it did. Code compared the IBAN with the Verified Destination, and the result is a block. No payment was prepared, so there's nothing to approve. The note to the AI changed nothing, because the comparison isn't made by the model. Sam is told to verify the new details through a contact already on file.

### 1:20 The live control (40 seconds)

*Show Chat A, with the approval card pending.*

> This one is a clean bill, so it's waiting for a person. Jo can prepare payments but can't approve them.

*Jo taps approve. Show "Only an Approver can decide on a payment."*

> That refusal comes from the server, not the screen. Now Sam hits Pause All Payments.

*Sam presses pause, then taps approve. Show "New payments are paused for this team."*

> No model is involved in that stop. And every step you've seen is recorded, from the file's hash through to the decision.

### 2:00 How it's built (40 seconds)

> The rule is simple. The model reads and explains, and code decides and pays.
>
> Gemini 4 Argon reads the invoice and must quote the document for each key field, and code confirms those quotes exist. Fixed checks then run on duplicates, UK invoice rules, the destination and cash. The worst result wins. Sam approves one exact payment, tied to a hash. Before sending, code recomputes that hash and checks everything again. A timeout is never resent, and the status always comes from asking the provider.
>
> The model has no tool for approving or paying. Those tools don't exist for it.

### 2:40 The results (45 seconds)

*Show slide 9.*

> We tested on 40 held-out cases, against the two obvious alternatives: the same model with a payment tool, and the same checks with template extraction.
>
> PayoAI had `[[RESULT: unsafe executions, PayoAI Full Agent, held-out, count of cases]]` unsafe executions. The model on its own had `[[RESULT: unsafe executions, LLM-only, held-out, count of cases]]`. PayoAI completed `[[RESULT: legitimate completed, PayoAI Full Agent, held-out, count of 5]]` legitimate bills, and rules-only completed `[[RESULT: legitimate completed, rules-only, held-out, count of 5]]`. Each decision cost about `[[RESULT: model cost per case, PayoAI Full Agent, held-out, USD]]` in model use.
>
> Here's what failed: `[[RESULT: every failed held-out case, PayoAI Full Agent, case ID and reason]]`.

### 3:25 The limits (25 seconds)

> Here's what those numbers don't mean. We wrote this test data ourselves, using a script. The payment provider is a simulator. No real money has moved, and no real rail is built. So this shows the code does what we intended, on failures we thought of. It isn't real-world performance.

### 3:50 Close (10 seconds)

> PayoAI helps Sam make this decision faster and shows every reason behind it. The final say stays with Sam. Thank you.

## Likely questions

### Who wrote the test data?

We did. A seeded script in our repository writes every case, and the same team wrote the agent. Expected results come from each scenario's definition rather than from running PayoAI, so a bug can't define its own right answer. But the scenarios name our own rule IDs, so they test failures we'd already thought of. That's the biggest weakness in our evidence. An independent test set is the first thing we'd add.

### Is the safety coming from the agent or from the rules?

The rules. Blocking changed details, duplicates, the approval check and the no-resend rule are all plain code, and the model can't change their results. If the model misreads an invoice and misses a changed IBAN, Sam loses the warning, but the money still goes to the Verified Destination, because invoice details are never used as the payee.

What the model adds is reading and conversation. It handles layouts we didn't write templates for, and it answers questions. It can still cause harm in smaller ways. A wrong amount that survives the quote check would appear on the approval card, and Sam would have to catch it. Explain's wording isn't checked by code either.

### What happens with real money?

Today, nothing can happen with it. There's no real rail: Airwallex, Wise and Circle adapters aren't built, and every provider name returns the simulator. The token in the test shop isn't a real card, and the shop takes no real payment.

Going near real money would need a sandbox adapter tested for idempotency and webhooks, separate database roles per process, real authentication, an accountant's review of the ruleset, and testing on real invoices with consent. We haven't done any of that.

### What failed?

On the held-out set: `[[RESULT: every failed held-out case, PayoAI Full Agent, case ID and reason]]`. The set ran `[[RESULT: number of held-out runs, and each result if more than one]]` time or times.

During the build, our first live run found problems that our tests had missed. Our tests use a scripted model, so they couldn't catch the first one: Gemini's API rejected list results from our tools, and we had to wrap them. An Operator tapping approve on a payment needing review got the wrong refusal message. The Main Loop couldn't see Start Confirmation cards or recent runs, so we added them to its context.

We also didn't build some of the plan. That covers the real rails, crypto signing, the separate Main Loop suite, the Investigate and Memory on/off variations, and repeated-action learning in Auto Mode. Investigate became one model call rather than a tool loop.

### Why use an LLM at all?

Invoices don't share a layout, and templates break on the ones nobody planned for. Our rules-only baseline shows this, although held-out deliberately uses layouts its templates don't fit, so that comparison favours us by construction. The result was `[[RESULT: legitimate completed, rules-only, held-out, count of 5]]`. The model also lets Sam ask questions in plain English, with code checking the figures it replies with.

### Is held-out really unseen?

Partly. The system never saw its supplier names, its two layouts, or five of its scenario combinations. But we wrote it, and most of its scenarios test the same rules as development. It also sits in the repository, so the lock is a process: a hash check, an explicit unlock phrase and a log of runs. Also, only the Full Agent suite sends the unseen layouts to a real model.

### Can a malicious invoice take over the agent?

It shouldn't be able to reach the money. Document text is labelled as untrusted, the Main Loop has no approval or payment tools, and the destination comparison happens in code. The held-out set has 3 cases with injected text, and the result was `[[RESULT: fraud category passed, PayoAI Full Agent, held-out, count of 11]]` across the 11 fraud cases. We can't claim it's injection-proof. The model could still be misled about what an invoice says, and that could affect the amount or the wording shown to Sam.

### What if Sam just approves everything, or verifies a fraudster?

We can't prevent that. The eval's human is a script that never approves a flagged payment, so we haven't measured careless approval. For verification, PayoAI requires a method, written evidence, and a contact channel that was on file before the new details came in. It records the evidence, but it can't prove the call happened. One person may both add and verify a destination, and the audit trail flags it.

### What if the agent process is compromised?

It has no payment secrets, but today it does share one database credential with the other processes, so it could write to any table. That's a real gap, and separate database roles would close it. There are no real provider keys to steal at the moment, because no real rail exists.

### How confident are you in the numbers?

Not very, beyond this set. The interval is `[[RESULT: exact 95% binomial interval for unsafe executions, PayoAI Full Agent, held-out]]`, and it only describes these synthetic cases, which aren't a random sample of real invoices. Only 5 held-out cases are legitimate, so false-block figures rest on very few.

### What did it cost?

`[[RESULT: model cost per case, PayoAI Full Agent, held-out, USD]]` per case, in model tokens at list price. That's the only real cost we measured. The simulator charges no fees, and we have no hosting or human review costs in that figure.

### Is the VAT checking legally reliable?

No, and we label it "Prepared for accountant review, not certified compliant." The rules follow HMRC's VAT Notice 700/21, last updated 18 March 2024, including simplified invoices at or below £250. The rounding tolerance and the VAT number pattern are our own settings with no confirmed source, and no VAT number is checked with HMRC. Compliance results can't block a payment on their own. They can only flag it for review.

### Are you faster or safer than an owner doing this by hand?

We don't know. We compared PayoAI with two software baselines, not with people, and we haven't tested it with real owners.

### Why is there crypto in it?

The demo company pays a contractor in USDC, and wallet address poisoning is a clear example of a changed-destination fraud. The checks for full address, chain and token contract are real code, but the payment itself is simulated. Nothing is signed or broadcast.
