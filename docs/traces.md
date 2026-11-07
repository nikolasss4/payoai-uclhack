# Traces and developer access

Decisions from the design grilling. Terms are defined in `CONTEXT.md`.

## Tables

| Table | Holds |
|---|---|
| Chat | Team, user, title |
| Turn | Chat, order, user input, final reply, model and prompt version, status, time and cost |
| Routine Run | Routine and version, team, what triggered it, the Turn that started it (if any), status |
| Trace Step | Turn and/or Routine Run, order, parent step, type, name, redacted input and output, trust label, evidence IDs, time, cost, error |
| Audit Event | Append-only financial events, each linking to the Trace Step that caused it |

A Routine Run can span several Turns, and some Routine Runs have no Chat at all. A Trace Step links to a Turn, a Routine Run or both.

Step types are LLM call, Read Tool call, Routine start, Node, decision, reply check, memory write, provider call and tap. The tap type records approval taps, including losing and stale ones, and destination verification.

## Recording rules

- Each step is saved when it starts and updated when it ends, so a crash still leaves a record.
- If a step can't be saved, it doesn't proceed. Record Attempt, Record Result and Update State save the record and the state change in one database transaction. The re-check steps before them write no payment state.
- Keys, tokens and full account numbers are removed before saving, by `redact` in `src/payoai/trace/__init__.py`.
- A document's file is stored by hash. Its extracted text is also saved in `documents.text_content`, so that quotes can be checked and Read Tools can show it, labelled as untrusted.
- Audit Events and expense versions are append-only, and a database trigger rejects any update or delete.

## Reading a decision's trail

| What you want | Where it is |
|---|---|
| One payment, from source to decision, approval and outcome | `GET /api/payments/{id}/trace` |
| One bill's checks and decision, including a blocked bill with no payment | `GET /api/bills/{id}`, or the `get_bill` Read Tool |
| One run's steps | The `get_run` Read Tool |
| Everything for a synthetic eval team | `GET /api/dev/traces/{team_id}` |

## Developer Console

The Developer Console exists as API routes under `/api/dev`. No screen for it can be confirmed in the code at commit `d94d88b`.

- It requires a platform-level Developer role, separate from team roles.
- It can list runs, read one run's results and start a run of a suite or one case. Comparing results means reading two runs, because no comparison view exists.
- Traces are only readable for synthetic eval teams, and a Developer has no team membership, so can't approve a payment. Run results aren't limited by team, because they only exist for eval runs.
- Payments go to the simulator, the only rail built. The smoke set against real sandboxes doesn't exist.
- Replay shows a stored trace and calls nothing. Re-run calls the model again and makes a new record.
- The held-out set needs an exact unlock phrase, on the command line and in the API. Every run is saved in `eval_runs`, whichever way it was started.

Eval runs use the same trace tables on their own synthetic team. Results are saved in `eval_runs` and `eval_results`, and as summary files in `evals/results/`, which is git-ignored.
