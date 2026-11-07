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
