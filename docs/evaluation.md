# Evaluation

Decisions from the design grilling. Terms are defined in `CONTEXT.md`.

## Suites

| Suite | What it tests | How it runs | Judge |
|---|---|---|---|
| Full Agent | The whole product's performance on the eval dataset, from a user's message or document to the final state | The full system, through the Main Loop | Deterministic for safety and final state, LLM judge for answer quality |
| Routines | Given a document and inputs, is the final state right? | Calls Routines directly, with no Main Loop | Deterministic |
| Main Loop | Did it start the right Routine with the right inputs, answer truthfully and resist injection? | Natural-language messages through the system | Deterministic for Routine and inputs, LLM judge for answer quality |
| Faults | Are timeouts, bad webhooks, crashes and stale balances handled with no duplicates? | Fault simulator around the adapters | Deterministic |

## Baselines

| System | What it is | What it shows |
|---|---|---|
| Rules-only | Routines with template extraction, no LLM Nodes and no Main Loop | What the LLM adds in completion and false blocks |
| LLM-only | The Main Loop with direct write tools, no Routines and no checks. Simulator only. | What the Routines add in safety |
| PayoAI | The full system | The combination |
| Variations | Investigate on/off, Memory on/off | Investigate's value, and proof that memory doesn't affect decisions |

## Rules

- The full benchmark runs on the simulator. A smoke set of 10-20 cases runs against real sandboxes and testnet. Results are reported separately.
- The held-out set uses unseen supplier names, layouts and fault combinations. It is locked, and a hash of its files is committed before the first run. It runs once for the reported number. After any fix and re-run, both results are shown.
- Code is authoritative for safety: final states, payment count per obligation, recipient, amount, rules fired, extraction accuracy, approval and hash integrity, and the provider ledger.
- The LLM judge scores Explain and Investigate output, whether Main Loop answers are faithful and honest about uncertainty, and research relevance. It cannot override code. It uses a fixed rubric and model version, and a person checks a sample to report the disagreement rate.
- The target is about 150 strong cases: 90 development, 20 validation and 40 held-out, weighted toward risky categories. More can be generated from templates if time allows.
- Report counts such as "0 of 40" rather than implying a precise rate.
- Deterministic checks for the Routines and Faults suites run in CI from the start.
