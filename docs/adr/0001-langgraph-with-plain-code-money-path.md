# LangGraph for Routines, plain code for the money path

Status: accepted (2026-11-07)

## Context

PayoAI needs durable pauses, reusable Nodes and branching for Routines with LLM steps. It also promises zero duplicate payments. As we understand it, LangGraph restarts a paused Node from its beginning on resume, which is harmless for a check but dangerous for a step that sends money. The integration spike should confirm this behaviour.

## Decision

- The Main Loop and Routines with LLM steps run on LangGraph, checkpointed in Postgres.
- Execute Payment and Reconcile Payment run as plain code with a state table and a Postgres-backed worker queue.
- Postgres is the single source of truth for payment state. LangGraph checkpoints only track progress through a Routine.
- The backend is Python, and the frontend can be Next.js calling a Python API.

An attempt is saved before anything is sent. After a crash, an attempt with no result becomes `UNKNOWN` and goes to Reconcile Payment. It is never re-sent.

During an LLM outage, approvals, execution, reconciliation, Pause All Payments and code checks keep working. LLM steps pause or degrade rather than assume a pass.

## Alternatives rejected

- **A small custom engine:** full control, but we would rebuild checkpoints, pauses and tracing during a hackathon.
- **LangGraph for everything:** simpler at first, but the no-duplicates guarantee would depend on a framework's resume behaviour.

## Consequences

Two execution models must be understood. Approval taps and payment execution are handled outside the graph, so a Routine hands over to the money path rather than running it.

## Note added after the build (2026-11-07)

The decision above stands for Routines and the money path, with one change: **the Main Loop is a plain Python loop, not a LangGraph graph.** Gemini's Interactions API stores the conversation, using `previous_interaction_id`, so the loop has no conversation state for a checkpoint to hold. See `src/payoai/agent/main_loop.py`. Routines still run on LangGraph with the Postgres checkpointer (`src/payoai/routines/engine.py`), and Execute Payment and Reconcile Payment are plain code as decided.

Some Routines turned out simpler than a graph needs to be. Prepare Batch, Purchase Checkout and several others are one-Node graphs. Verify Destination is plain code behind an API route, and the second half of Purchase Checkout runs as plain code in `money-worker`. `docs/routines.md` gives each Routine's status.

A Routine's graph now ends when the payment is prepared, rather than pausing at Await Approval. The approval is a database record, and the tap updates `routine_runs.status` directly, without resuming a graph. The only pauses left are questions in chat, which live in their own Node with no side effects. That makes resume restarting a Node harmless, so the design doesn't depend on the behaviour this ADR asked the spike to confirm.
