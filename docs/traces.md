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

Step types are LLM call, Read Tool call, Routine start, Node, decision, reply check, memory write and provider call.

## Recording rules

- Each step is saved when it starts and updated when it ends, so a crash still leaves a record.
- If a step can't be saved, it doesn't proceed. On the money path, the record and the state change are saved in one database transaction.
- Keys, tokens and full account numbers are removed before saving. Documents are stored by reference and hash.

## Developer Console

- It requires a platform-level Developer role, separate from team roles.
- It can browse traces, run one case, a suite or everything, and compare results.
- It only accesses dedicated synthetic eval teams. A Developer can't see a real business's chats or approve its payments.
- Payments go to the simulator by default, and the smoke set is an explicit choice.
- Replay shows a stored trace and calls nothing. Re-run calls the model again and makes a new record.
- The held-out set is locked behind an explicit action. Every run is logged and shown in the results.

Eval runs use the same tables, with a link to their eval case.
