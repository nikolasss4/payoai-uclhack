# Evaluation

Decisions from the design grilling, updated to match `evals/` at commit `d94d88b`. Terms are defined in `CONTEXT.md`. How the data was made is in `deck/DATA.md`.

## Suites

The runner is `evals/runner.py`. It has three suites, and every one of them runs the Pay Bill Routine.

| Suite | What it tests | How it runs | Model | Judge |
|---|---|---|---|---|
| `full_agent` | The whole path, from a user's message and a document to the final state | Through the Main Loop, then the Start Confirmation is tapped by the script | Real Gemini calls | Deterministic, plus an optional sampled LLM judge for Explain's wording |
| `routines` | Whether the checks reach the right decision, on non-fault cases | Calls Pay Bill directly | **Replaced.** A fake returns the correct fields from the case. | Deterministic |
| `faults` | Timeouts, server errors, pending states and mismatches, with no duplicates | Calls Pay Bill directly, with faults in the simulator | **Replaced**, as above | Deterministic |

This means the `routines` and `faults` suites test code, not the model. Only `full_agent` tests extraction, the Main Loop and real cost.

The planned **Main Loop suite isn't in the runner**. `tests/test_main_loop.py` covers the Main Loop with a scripted model. It checks that bank details are refused, that Start Confirmations hold, that injected text changes nothing and that the reply check works. The real model's choice of Routine is only exercised by `full_agent`, using one fixed message.

## Baselines

| System | What it is | What it shows | How to read it |
|---|---|---|---|
| Rules-only (`--system rules_only`) | Pay Bill with regular-expression extraction for the `labelled` layout, and no other LLM steps | What the model adds in completion and false blocks | Its templates only fit one layout, and the held-out split doesn't use that layout, so it is expected to stop on held-out. This favours PayoAI by construction. |
| LLM-only (`--system llm_only`) | The model with a directory, history, balance and a direct `send_payment` tool, told to be careful. Simulator only. | What the Routines add in safety | It gives no typed decision, so a case it refuses to pay is recorded as `NO_PAYMENT` and fails the decision check. Compare unsafe counts, not pass rates. |
| PayoAI (`--system payoai`) | The full system | The combination | Compare with rules-only using `full_agent`, because the `routines` suite gives PayoAI perfect extraction. |

The planned Investigate on/off and Memory on/off variations aren't in the runner. `tests/test_routines.py` has one test, using a scripted model, that decisions are identical with Memory on and off. Investigate on/off isn't implemented.

## Rules

- The full benchmark runs on the simulator. The planned smoke set against real sandboxes and testnet doesn't exist, because no real rail is built.
- The held-out split uses unseen supplier names and layouts, and includes five scenarios that the development split never shows. It needs `--unlock-heldout` with an exact phrase. `load_cases` refuses to run if a file's hash differs from `evals/cases/MANIFEST.json`.
- The held-out set is meant to run once for the reported number. After any fix and re-run, both results must be shown. The manifest was committed in `d94d88b`, together with the runner. The repository can't show on its own that no earlier run happened, so the run log in `eval_runs` is the evidence.
- Code is authoritative for safety. It checks final state, payment count against the simulator's ledger, recipient, rules fired, and that every attempt has a recorded human approval. Extraction accuracy is also code-scored, on `full_agent` only.
- The LLM judge scores Explain's wording against recorded facts, using a fixed rubric (`semantic-1`) and model. It can't override code. It is only used with `--judge-sample`. The planned human check of a sample and the disagreement rate aren't in the code, so they must be done by hand or left out.
- Report counts in the form "N of 40" rather than implying a precise rate. Reported counts describe this synthetic set, which isn't a random sample of real invoices.
- `tests/` holds deterministic tests that need a database. No CI is configured in the repository, so they run by hand.

## What the scripted human does

`human_approves` in the runner approves a payment only if the decision is `ELIGIBLE_FOR_APPROVAL`, and leaves anything flagged for review. So:

- For `BLOCK` cases, "no money moved" tests the code, because no payment can be prepared.
- For `NEEDS_REVIEW` cases, "no money moved" is partly due to this scripted human. The code does prepare a payment, marked as needing review. The benchmark tests that the case is flagged correctly, and not what a real person would do next.
- It doesn't measure a person who approves carelessly.

## What the benchmark doesn't cover

- Any Routine other than Pay Bill.
- PDFs and images. Every case is a plain-text document.
- Real provider behaviour, fees or settlement. The simulator charges no fee.
- Process separation. The runner calls the money path in its own process.
- Real invoices, real suppliers or real users.

## Recorded for each run

`summarise` in the runner reports these keys, which the deck's placeholders map to: `cases`, `passed`, `errors`, `unsafe_executions`, `wrong_recipient`, `duplicate_or_unauthorised`, `paid_when_it_should_not`, `legitimate_cases`, `legitimate_completed`, `false_blocks_or_escalations`, `outcome_truthful`, `by_category`, `extraction_fields_right`, `extraction_fields_total`, `semantic_sampled`, `semantic_faithful_mean`, `semantic_uncertainty_mean`, `latency_ms_median`, `latency_ms_p95`, `cost_usd_total`, `cost_usd_per_case` and `failures`.

Cost is token usage at the list prices in `src/payoai/agent/config.py`, so a suite with the model replaced reports zero.
