# PayoAI

**An AI agent that runs accounts payable for UK small businesses.** It handles bills, invoices and contractor payments, and the owner just approves.

Built for the UCL AgentVerse 2026 Finance track.

**Live demo: [payoai.ecomnis.com](https://payoai.ecomnis.com)**

> For safety, the demo server has no Google Gemini API key of its own. When you chat, the agent says no key is configured and offers a field to paste yours (get one from [Google AI Studio](https://aistudio.google.com/apikey)). The key works only from your browser and isn't saved anywhere on the server: no file, database or log. The agent holds it in memory just to run your requests, and forgets it when you sign out, after 12 hours, or when the server restarts. You can also [run it yourself](#run-it-yourself) with your own key.

PayoAI runs on synthetic data and a simulated payment provider. **It has never moved real money, and it uses no real records, real account details or real logins.**

## The problem

Our users are small business owners with no finance team. They need three things:

- **Payments.** Every bill and contractor paid to the right person, for the right amount, once.
- **Compliance.** A record for every payment that HMRC will accept, ready for the accountant.
- **Cash flow.** Running short of cash is one of the most common reasons small firms fail.

Where it goes wrong: paying a fraudster, paying twice, and running out of cash.

## What the agent does

You ask in chat: "Pay this bill." "Pay Alex £400." "Can we afford it?"

| Payments | Compliance | Cash flow |
|---|---|---|
| Processes every bill and invoice | Checks invoices against UK VAT rules | Forecasts the next 7 days |
| Pays only verified contractors | Lists what's missing for your accountant | Protects a cash reserve |
| Bank transfers and crypto (USDC) | Records each payment and chases missing receipts | Warns you before a shortfall |
| Blocks duplicates and changed bank details | Keeps a full audit trail | Pays the bills due soonest first |
| Sends only after your approval | | |

It also handles verified contacts, VAT reports, expenses and receipts, recurring payments and batch payments.

**You approve every payment. The agent does the rest.**

## Three protections

**1. Compliance: two questions, kept apart.**
- *Is it safe to pay?* Safety rules can block a payment: changed bank details or wallet, an exact duplicate bill, or the wrong chain or token.
- *Is it right for the books?* UK VAT rules flag a bill for review: missing VAT invoice details, line and total arithmetic, and full vs simplified invoices (up to £250).

The ruleset is `uk-invoice-1.1`, based on HMRC VAT Notice 700/21. Its output is prepared for accountant review and isn't certified compliant.

**2. Fraud and scams.** A crypto payment can't be pulled back, so the full wallet address must match the verified one exactly. A lookalike address with the same first and last characters is blocked (address poisoning). The chain and token contract must match too, since a matching symbol isn't enough, and so must the rail (bank or wallet). Changed bank details get the same check. If an invoice has a changed IBAN, no payment is prepared, so there's nothing to approve. Hidden text telling the AI to skip the checks changes nothing, because the checks are code.

**3. Cash.** A 7-day forecast per currency covers bills due, payments in flight and approvals. A bill that would break the cash reserve is flagged with the shortfall. Batches share out cash by due date, soonest first. A stale balance never reports "all fine".

## How the agent is built

- **Main Loop.** It chats and answers fast, with read-only tools: look up records, extract a field, show it.
- **Routines.** Anything that needs control runs as a Routine, a fixed tree of code steps and LLM steps.

The agent works out what you want. The Routine decides what's allowed. The model has no tool to approve or pay.

What makes it an agent:

- **Talks freely.** It answers any question about your payables from live records.
- **Plans in sequence.** For a message with several bills, it writes a plan and runs one Routine at a time.
- **Cards in the chat.** You start, pick an option, fill a missing field or approve, right in the conversation.
- **Searches the web.** This runs in an isolated Routine with no business data and no write tools.
- **Full traceability.** Every agent turn and Routine run is saved step by step: model calls, code steps, untrusted inputs, time and cost. You can see which step went wrong, LLM or code, and test one step alone or the whole run with the real model.
- **Tested.** 40 held-out cases against LLM-only and rules-only builds.

### How the verification loop works

The model's output is checked by code at each point where it could matter, and a failed check stops or downgrades the step. It is never accepted on trust.

1. **Extraction.** The model must quote the document for each key field. Code confirms that each quote is in the document and contains the value. A failure gives `NEEDS_REVIEW`.
2. **Decision.** Code Nodes run the checks and combine them, and the worst result wins: `BLOCK`, `NEEDS_REVIEW`, `NEEDS_INFORMATION` or `ELIGIBLE_FOR_APPROVAL`. The model doesn't take part, and Memory isn't an input.
3. **Findings.** Investigate may only add suggestions. A finding that cites no real record is dropped.
4. **Replies.** Before a chat reply is final, code checks that its figures appear in this Turn's tool results, and that words like "paid" match the record's status. While the reply streams, it is shown greyed out as an unchecked draft, and the checked reply replaces it. A failed reply is regenerated once. A second failure shows the raw records instead.
5. **Approval.** The Approver's tap carries the payment hash shown on screen. One database statement accepts it only if the approval is still pending, the hash matches, it hasn't expired, the person is still an Approver and payments aren't paused.
6. **Execution.** Before sending, code recomputes the hash and re-checks authority, the destination and cash. The attempt is saved before anything is sent, and a timeout becomes `UNKNOWN` and is never resent.
7. **Reconciliation.** The status comes from asking the provider directly. Code compares the amount, asset, recipient and reference with what was approved, and a mismatch goes to manual review.

### Models

These are the exact identifiers in `src/payoai/agent/config.py`, called through the Gemini Interactions API.

| Step | Model | Thinking level |
|---|---|---|
| Main Loop | `gemini-3.8-flash` | `low` |
| Extract Fields | `gemini-3.8-flash` | `medium` |
| Investigate | `gemini-3.8-flash` | `high` |
| Purchase Research | `gemini-3.8-flash` | `medium` |
| LLM judge (evaluation only) | `gemini-4-argon` | `high` |
| Explain, category suggestion, draft request | `gemini-3.8-flash` | `low` |
| Auto Mode learner | `gemini-3.5-flash-lite` | `minimal` |

## Run it yourself

The agent is ready to run. You only need to set it up locally with your own Gemini API key.

### 1. Install the requirements

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- Node 20 or later
- Postgres (local or hosted)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)

```bash
git clone https://github.com/nikolasss4/payoai-uclhack.git
cd payoai-uclhack
uv sync
createdb payoai          # or create a database any other way
```

### 2. Create the env files

Each process loads only the secrets it needs, from its own file in the repo root. These files are git-ignored, so never commit them. `.env.example` lists every name.

**`.env.shared`** (every process)

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/payoai
PROVIDER_MODE=simulator
```

**`.env.agent`** (the agent worker)

```bash
GEMINI_API_KEY=your-gemini-api-key
```

You can also leave this out and paste a key in the app. When there's no key, the chat says so and shows a field for one. A pasted key isn't stored anywhere, and it works only from the browser that pasted it. Each browser sends a random secret with its requests, and the agent worker keeps the key in memory under a hash of that secret, ahead of `GEMINI_API_KEY`. Jobs started without a browser (the scheduler, webhooks) only use `GEMINI_API_KEY`. The key is forgotten on sign-out, after 12 hours, or when the worker restarts.

**`.env.api`** (the API)

```bash
SESSION_SECRET=any-long-random-string
WEBHOOK_SECRET_SIMULATOR=another-long-random-string
```

**`.env.money`** (the money worker) can be left out. Its names are for payment rails that aren't built yet (Airwallex, Wise, Circle), and every payment goes through the simulator.

You can generate the secrets with `openssl rand -hex 32`.

### 3. Seed and start

```bash
uv run python -m payoai.seed   # applies migrations and creates the synthetic demo company
scripts/dev.sh                 # builds the frontend if needed, then starts the API, three workers and the test shop
```

Open http://127.0.0.1:8743 and sign in as one of the seeded users. The test shop runs on port 8793. To use other ports, set `API_PORT` and `MERCHANT_PORT`, for example `API_PORT=9100 scripts/dev.sh`. Logs are in `data/logs/`.

For frontend work, run `npm run dev` in `frontend/` (port 5173, with `/api` proxied to `API_PORT`). The Developer Console is at `/dev.html`.

To run the benchmark (see `deck/DATA.md` and `docs/evaluation.md`):

```bash
PYTHONPATH="$PWD:$PWD/src" uv run python -m evals.runner --suite routines --split dev
```

## Payment rails

| Rail | Status |
|---|---|
| Simulator | Built, and the only rail in use. Every result is labelled `SIMULATED`. |
| Airwallex | Adapter not built |
| Wise | Adapter not built |
| Circle and testnet USDC | Adapter not built. Crypto payments are simulated, and nothing is signed or broadcast. |

## Limits

- All test data is synthetic and was written by the same team that wrote the agent. See `deck/DATA.md`.
- The benchmark only runs Pay Bill. Other Routines are covered by unit tests, which use a scripted model.
- The simulator charges no fees, so the only real cost measured is model usage.
- The UK ruleset covers domestic VAT invoices and suppliers not registered for VAT. It hasn't been reviewed by an accountant, and VAT numbers aren't checked with HMRC.
- PayoAI records that a destination was verified, but it can't prove a call-back really happened.
- Sign-in is a demo with seeded users and no password, and all processes share one database credential. This isn't production security.
- It gives no legal, tax or accounting advice. Its records are prepared for accountant review.

## More

- `docs/PLAN.md`: the plan, and in section 15 where the build differs from it
- `docs/routines.md`: every Routine, Node by Node, with its status
- `docs/traces.md` and `docs/evaluation.md`: traces and the benchmark
- `docs/adr/`: decisions that are hard to reverse
- `deck/`: judging deck, pitch script and data notes
