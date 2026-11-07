# Gemini 4 Argon as the agent's brain

Status: accepted (2026-11-07)

## Context

The UCL AgentVerse 2026 builder guide provides Gemini API credits and recommends Gemini 4 Argon as the central reasoning and verification model. The team must use Google's models. Fewer models also means fewer variables to test.

## Decision

| LLM step | Model | Thinking level |
|---|---|---|
| Main Loop | `gemini-4-argon` | `medium` |
| Extract Fields | `gemini-4-argon` | `medium` |
| Investigate | `gemini-4-argon` | `high` |
| Explain | `gemini-3.8-flash` | `low` |
| Purchase Research | `gemini-4-argon` | `medium` |
| Auto Mode learner | `gemini-3.5-flash-lite` | `minimal` |
| LLM judge | `gemini-4-argon` | `high` |

- Models are called through the Gemini API's Interactions API, with an AI Studio key and `google-genai` 2.3.0 or later, not Vertex AI.
- The Main Loop's conversation is stored by the API using `previous_interaction_id`. Every input and output is still saved as a Trace Step.
- Each Node's model and thinking level sit in versioned config and are recorded on its Trace Step.
- Investigate runs in the background with a progress status.
- Extract Fields is compared at `medium` and `low` on about 20 development cases before its setting is locked.

## Alternatives rejected

- **Gemini 3.8 Flash as the default:** cheaper and faster, but Argon is the guide's recommended brain and these are the hardest reasoning steps.
- **Vertex AI:** the guide directs teams to AI Studio keys, and the Vertex page we read lacked model IDs, prices and feature tables.
- **Claude on Vertex AI:** ruled out because the team must use Google's models.

## Consequences

- Argon has no free tier, so the build depends on event credits. Its price doubles on 1 December 2026, and Gemini 3.8 Flash's doubles on 1 January 2027.
- Sampling settings such as `temperature` are ignored on Argon, so outputs can't be forced to repeat. The LLM judge's variance is measured by repeating a sample.
- Our documentation check didn't establish how safety blocks are reported, or what happens if stored history is edited. These need testing early, and any unclear result counts as a failed step.

## Note added after the build (2026-11-07)

The model table above matches `src/payoai/agent/config.py`, which adds two smaller steps: category suggestion and draft request, both on `gemini-3.8-flash` at `low`.

Three items in this ADR differ from the build or can't be confirmed from it:

- **Investigate doesn't run in the background.** It runs inside its Node. Code gathers the related records, and the model gets them in one call, with no tool loop. See `investigate` in `src/payoai/routines/nodes/decision.py`.
- **The Extract Fields comparison isn't recorded.** `extract_fields` is set to `medium`, but nothing in the repository records a comparison at `medium` and `low`.
- **The judge's variance isn't measured by the code.** `evals/runner.py` can score a sample with `--judge-sample`, but it doesn't repeat a sample to measure variance.

`src/payoai/agent/llm.py` treats any status other than `completed`, empty output or invalid JSON as a failed step. This holds whichever way safety blocks turn out to be reported.
