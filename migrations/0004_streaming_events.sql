-- THINKING shows what the Main Loop is doing while a Turn runs. REPLY_DRAFT carries the reply
-- as it streams. A draft hasn't passed the Reply Check yet, and only REPLY is the final answer.
ALTER TABLE chat_events DROP CONSTRAINT chat_events_kind_check;
ALTER TABLE chat_events ADD CONSTRAINT chat_events_kind_check
  CHECK (kind IN ('STATUS', 'QUESTION', 'APPROVAL_CARD', 'START_CARD', 'SUGGESTION', 'REPLY', 'ERROR', 'THINKING', 'REPLY_DRAFT'));
