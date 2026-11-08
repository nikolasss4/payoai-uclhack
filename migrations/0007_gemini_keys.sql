-- A Gemini API key pasted in the app. The agent worker uses it for the team's LLM calls,
-- ahead of GEMINI_API_KEY in .env.agent. The API writes it and never sends it back.
CREATE TABLE IF NOT EXISTS gemini_keys (
  team_id text PRIMARY KEY REFERENCES teams,
  api_key text NOT NULL,
  updated_by text NOT NULL REFERENCES users,
  updated_at timestamptz NOT NULL DEFAULT now()
);
