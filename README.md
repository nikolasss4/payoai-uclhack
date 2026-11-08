# PayoAI

PayoAI is a finance agent for the UCL AgentVerse 2026 Finance track. It runs on synthetic data and a simulated payment provider. **It has never moved real money, and it uses no real records, real account details or real logins.**

## The user and the decision

The user is a UK small business owner with no finance team. The decision is: "Is it safe to pay this bill now, and can we afford it?"

The failures that hurt this person are paying a fraudster's changed bank details, paying the same bill twice, and missing a payment because cash ran short.

## What it does

The owner uploads a bill or asks a question in chat. A model reads the invoice and answers questions, and fixed code does everything that decides or moves money.

- Code compares the invoice with the supplier's Verified Destination, past bills, UK invoice rules and the current cash position. The worst result wins: `BLOCK`, `NEEDS_REVIEW`, `NEEDS_INFORMATION` or `ELIGIBLE_FOR_APPROVAL`.
- A blocked bill, or one missing essential information, gets no payment. Any other payment waits for a person with the Approver role, who approves one exact payment through its hash.
- Bills are only ever paid to a destination a person has verified. Details on an invoice or in chat are never used as a payee.
- Every step is saved as a Trace Step, and financial events go to an append-only audit table.

## Models

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

## How the verification loop works

The model's output is checked by code at each point where it could matter, and a failed check stops or downgrades the step. It is never accepted on trust.

1. **Extraction.** The model must quote the document for each key field. Code confirms that each quote is in the document and contains the value. A failure gives `NEEDS_REVIEW`.
2. **Decision.** Code Nodes run the checks and combine them. The model doesn't take part, and Memory isn't an input.
3. **Findings.** Investigate may only add suggestions. A finding that cites no real record is dropped.
4. **Replies.** Before a chat reply is final, code checks that its figures appear in this Turn's tool results, and that words like "paid" match the record's status. While the reply streams, it is shown greyed out as an unchecked draft, with no Chips, and the checked reply replaces it. A draft that fails the check is withdrawn. A failed reply is regenerated once. A second failure shows the raw records instead. The thinking line above it shows the Main Loop's own steps, and the model's thought summaries when it gives any. Those are labelled as thinking and aren't checked.
5. **Approval.** The Approver's tap carries the payment hash shown on screen. One database statement accepts it only if the approval is still pending, the hash matches, it hasn't expired, the person is still an Approver and payments aren't paused.
6. **Execution.** Before sending, code recomputes the hash and re-checks authority, the destination and cash. The attempt is saved before anything is sent, and a timeout becomes `UNKNOWN` and is never resent.
7. **Reconciliation.** The status comes from asking the provider directly. Code compares the amount, asset, recipient and reference with what was approved, and a mismatch goes to manual review.

Explain's wording isn't checked by code. In evaluation, an LLM judge scores a sample of it, and that judge can't override a code result.

## Run it

You need Python 3.12, `uv`, Node 20 or later, a Postgres database and a Gemini API key. Copy the names from `.env.example` into these git-ignored files, and never commit values.

| File | Used by | Names |
|---|---|---|
| `.env.shared` | Every process | `DATABASE_URL`, `PROVIDER_MODE` |
| `.env.agent` | `agent-worker` | `GEMINI_API_KEY` |
| `.env.money` | `money-worker` | `AIRWALLEX_API_KEY`, `WISE_API_TOKEN`, `CIRCLE_API_KEY`, `TESTNET_SIGNER_KEY`, `CHAIN_RPC_URL`, `TEST_CARD_TOKEN` |
| `.env.api` | `api` | `SESSION_SECRET`, `WEBHOOK_SECRET_SIMULATOR` |

The provider names in `.env.money` are for rails that aren't built, so they can stay empty.

```bash
uv sync
uv run python -m payoai.seed   # applies migrations and creates the synthetic demo company
scripts/dev.sh                 # builds the frontend if needed, then starts the API, three workers and the test shop
```

The app is then at http://127.0.0.1:8000. It's a React app in `frontend/`, built into `web/dist` and served by the API. For frontend work, run `npm run dev` in `frontend/` (port 5173, with `/api` proxied to port 8000). The Developer Console is still the plain page at `/dev.html`. To run the benchmark, see `deck/DATA.md` and `docs/evaluation.md`:

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
