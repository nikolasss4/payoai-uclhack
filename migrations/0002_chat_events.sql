-- What the user sees during a Turn or Routine Run: status lines, questions and cards.
-- Events carry IDs, and cards are rendered from live records, so a stale event can't mislead.
CREATE TABLE chat_events (
  id bigserial PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  chat_id text NOT NULL REFERENCES chats,
  turn_id text REFERENCES turns,
  run_id text REFERENCES routine_runs,
  kind text NOT NULL CHECK (kind IN ('STATUS', 'QUESTION', 'APPROVAL_CARD', 'START_CARD', 'SUGGESTION', 'REPLY', 'ERROR')),
  payload jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX chat_events_chat ON chat_events (chat_id, id);
