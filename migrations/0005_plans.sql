-- Plans. The Main Loop makes one for a request that needs several Routine runs, and code runs
-- its items one at a time, one job each. The model never drives the steps after making the plan.
CREATE TABLE plans (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  chat_id text NOT NULL REFERENCES chats,
  turn_id text NOT NULL REFERENCES turns,
  user_id text NOT NULL REFERENCES users,
  title text NOT NULL,
  -- RUNNING until no item is left to start. Items that wait on a person keep going after that.
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'FINISHED', 'STOPPED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_running_plan_per_chat ON plans (chat_id) WHERE status = 'RUNNING';

CREATE TABLE plan_items (
  id text PRIMARY KEY,
  plan_id text NOT NULL REFERENCES plans,
  team_id text NOT NULL REFERENCES teams,
  position integer NOT NULL,
  description text NOT NULL,
  routine text NOT NULL,
  -- Validated when the plan is made, and used exactly as stored.
  inputs jsonb NOT NULL,
  depends_on integer[] NOT NULL DEFAULT '{}',
  -- STARTING is claimed but not started yet, so a retried job can tell. Once STARTED, the run
  -- or the start card is the source of truth for what the item shows.
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'STARTING', 'STARTED', 'FAILED', 'BLOCKED', 'CANCELLED')),
  note text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (plan_id, position)
);

-- A plan item starts at most one run and one start card, even when its job is retried.
ALTER TABLE routine_runs ADD COLUMN plan_item_id text REFERENCES plan_items;
CREATE UNIQUE INDEX routine_runs_plan_item ON routine_runs (plan_item_id) WHERE plan_item_id IS NOT NULL;
ALTER TABLE start_confirmations ADD COLUMN plan_item_id text REFERENCES plan_items;
CREATE UNIQUE INDEX start_confirmations_plan_item ON start_confirmations (plan_item_id) WHERE plan_item_id IS NOT NULL;

ALTER TABLE chat_events DROP CONSTRAINT chat_events_kind_check;
ALTER TABLE chat_events ADD CONSTRAINT chat_events_kind_check
  CHECK (kind IN ('STATUS', 'QUESTION', 'APPROVAL_CARD', 'START_CARD', 'SUGGESTION', 'REPLY', 'ERROR', 'THINKING', 'REPLY_DRAFT', 'PLAN'));
