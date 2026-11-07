# PayoAI

Domain glossary for PayoAI, an AI finance operations agent for small businesses.

## Agent

**Main Loop**: The free-form conversational agent. It can answer any question using Read Tools and can start Routines, but it cannot change anything itself.
_Avoid_: orchestrator, chatbot

**Read Tool**: A capability the Main Loop uses to look up information, such as expenses or counterparties. It never changes anything.
_Avoid_: query, skill

**Chip**: A money fact in a Main Loop reply, showing a record's live values. It is rendered from the record itself, not written by the model.
_Avoid_: badge, tag

**Routine**: A predictable, fixed sequence of steps for one scenario. Routines are the only way anything gets changed or executed.
_Avoid_: workflow, chain, pipeline

**Node**: One reusable step inside a Routine, such as a duplicate check or a crypto destination check. Routines that need the same step share the same Node.
_Avoid_: stage, task

**Routine Run**: One recorded execution of a Routine, registered as soon as it starts, with its inputs and what triggered it. Registering a run commits no money; a run started by mistake only leaves a cancellable draft.
_Avoid_: job, task

**Start Confirmation**: A user's tap that allows a Routine Run to start. Each Routine's configuration says whether it needs one. It is not approval to pay, and it does not verify anything.
_Avoid_: verification, verify required

**Waiting Run**: A Routine Run paused until the user answers a question in chat.
_Avoid_: interrupted task, unfinished task

**Memory**: A saved preference or piece of business context that can influence suggestions but never a decision. It gives no authority.
_Avoid_: policy, rule, knowledge

**Auto Mode**: A setting that lets Memory be learned without a confirmation tap.
_Avoid_: autonomous mode, autopilot

**Chat**: One conversation between a user and PayoAI within a team.
_Avoid_: thread, session

**Turn**: One user input and everything the agent does in response, until it replies. Only a user input creates a Turn.
_Avoid_: run, message, request

**Trace Step**: One recorded action, such as an LLM call, a Read Tool call or a Node, with its input and output. Not every step involves an LLM.
_Avoid_: span, log entry

**Developer Console**: Where a Developer browses traces and runs eval cases, using synthetic data only.
_Avoid_: admin panel, dashboard

## Payments

**Superseded Bill**: An unpaid bill that has been replaced by a corrected one. It can no longer be paid, but stays in the history, linked to its replacement.
_Avoid_: deleted bill, removed submission, edited bill

**Verified Destination**: A bank account or wallet belonging to a counterparty that has passed independent verification. It is the only place a payment can be sent. Details found in chat, an invoice or transaction history are never verified on their own.
_Avoid_: beneficiary, payee details, saved address

**Template**: Saved payment details for convenience. It gives no authority to pay.
_Avoid_: preset, saved payment

**Automation Rule**: When something should happen, such as the 1st of each month or when a matching invoice arrives.
_Avoid_: schedule, trigger

**Mandate**: The limits on what may be prepared automatically, such as recipient, destination, maximum amount and end date. It never replaces approval.
_Avoid_: authorisation, standing order, policy

**Occurrence**: One period's payment under a Mandate. Each Mandate has at most one per period.
_Avoid_: instance, recurrence

## Relationships

- The **Main Loop** can call **Read Tools** directly.
- All changes and operations go through a **Routine**, whether the **Main Loop** asked for it or not.
- The **Main Loop** starts **Routines** one at a time and picks up **Waiting Runs** itself, offering to continue them.
- The **Main Loop** cannot skip a **Start Confirmation** that a **Routine** requires.
- The **Main Loop** names who to pay and how much, but can never supply bank or wallet details. A **Routine** finds the **Verified Destination** itself.
- A new recipient is added and verified by a separate **Routine**, which cannot lead straight to a payment.
