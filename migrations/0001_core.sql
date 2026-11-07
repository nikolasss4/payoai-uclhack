-- PayoAI core schema. Runs with search_path set to the target schema.
-- Money is an integer in the asset's smallest unit, never a float.

-- Access
CREATE TABLE users (
  id text PRIMARY KEY,
  name text NOT NULL,
  email text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE teams (
  id text PRIMARY KEY,
  name text NOT NULL,
  jurisdiction text NOT NULL DEFAULT 'GB',
  is_eval boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memberships (
  team_id text NOT NULL REFERENCES teams,
  user_id text NOT NULL REFERENCES users,
  role text NOT NULL CHECK (role IN ('VIEWER', 'OPERATOR', 'APPROVER')),
  PRIMARY KEY (team_id, user_id)
);
CREATE TABLE platform_roles (
  user_id text NOT NULL REFERENCES users,
  role text NOT NULL CHECK (role IN ('DEVELOPER')),
  PRIMARY KEY (user_id, role)
);
CREATE TABLE team_controls (
  team_id text PRIMARY KEY REFERENCES teams,
  payments_paused boolean NOT NULL DEFAULT false,
  paused_by text REFERENCES users,
  paused_at timestamptz,
  approval_ttl_hours integer NOT NULL DEFAULT 24,
  auto_mode boolean NOT NULL DEFAULT false
);

-- Conversation
CREATE TABLE chats (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  user_id text NOT NULL REFERENCES users,
  title text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE turns (
  id text PRIMARY KEY,
  chat_id text NOT NULL REFERENCES chats,
  team_id text NOT NULL REFERENCES teams,
  seq integer NOT NULL,
  user_input text NOT NULL,
  final_reply text,
  model text,
  prompt_version text,
  interaction_id text,
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
  cost_micros bigint NOT NULL DEFAULT 0,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  UNIQUE (chat_id, seq)
);
CREATE TABLE task_list_items (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  chat_id text NOT NULL REFERENCES chats,
  position integer NOT NULL,
  description text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'DONE', 'CANCELLED')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Routines
CREATE TABLE routine_runs (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  routine text NOT NULL,
  routine_version text NOT NULL DEFAULT '1',
  chat_id text REFERENCES chats,
  started_turn_id text REFERENCES turns,
  trigger_type text NOT NULL CHECK (trigger_type IN ('CHAT', 'CONFIRMATION', 'TAP', 'TIMER', 'WEBHOOK', 'SYSTEM', 'EVAL')),
  triggered_by text,
  inputs jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL CHECK (status IN ('RUNNING', 'WAITING_ANSWER', 'AWAITING_APPROVAL', 'AWAITING_DOCUMENT', 'COMPLETED', 'FAILED', 'CANCELLED')),
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
-- One step running per Chat.
CREATE UNIQUE INDEX one_running_step_per_chat ON routine_runs (chat_id) WHERE status = 'RUNNING' AND chat_id IS NOT NULL;
CREATE INDEX routine_runs_waiting ON routine_runs (team_id, status);

CREATE TABLE run_questions (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text NOT NULL REFERENCES routine_runs,
  field text NOT NULL,
  prompt text NOT NULL,
  options jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ANSWERED', 'EXPIRED')),
  answer jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE start_confirmations (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  chat_id text REFERENCES chats,
  turn_id text REFERENCES turns,
  routine text NOT NULL,
  inputs jsonb NOT NULL,
  requested_by text NOT NULL REFERENCES users,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CONFIRMED', 'DECLINED', 'EXPIRED')),
  run_id text REFERENCES routine_runs,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Trace
CREATE TABLE trace_steps (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  turn_id text REFERENCES turns,
  run_id text REFERENCES routine_runs,
  parent_id text REFERENCES trace_steps,
  step_type text NOT NULL CHECK (step_type IN ('LLM_CALL', 'READ_TOOL', 'ROUTINE_START', 'NODE', 'DECISION', 'REPLY_CHECK', 'MEMORY_WRITE', 'PROVIDER_CALL', 'TAP')),
  name text NOT NULL,
  input jsonb,
  output jsonb,
  trust_label text NOT NULL DEFAULT 'TRUSTED' CHECK (trust_label IN ('TRUSTED', 'UNTRUSTED')),
  evidence_ids jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
  error text,
  model text,
  thinking_level text,
  input_tokens integer,
  output_tokens integer,
  cost_micros bigint,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  ended_at timestamptz,
  CHECK (turn_id IS NOT NULL OR run_id IS NOT NULL)
);
CREATE INDEX trace_steps_run ON trace_steps (run_id, started_at);
CREATE INDEX trace_steps_turn ON trace_steps (turn_id, started_at);

CREATE TABLE audit_events (
  id bigserial PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  actor_type text NOT NULL CHECK (actor_type IN ('USER', 'AGENT', 'SYSTEM', 'PROVIDER')),
  actor_id text,
  event_type text NOT NULL,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}',
  trace_step_id text REFERENCES trace_steps,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX audit_events_entity ON audit_events (team_id, entity_type, entity_id);

-- Contacts
CREATE TABLE counterparties (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  display_name text NOT NULL,
  legal_name text,
  entity_type text NOT NULL DEFAULT 'business' CHECK (entity_type IN ('individual', 'business', 'government', 'internal')),
  classifications jsonb NOT NULL DEFAULT '[]',
  vat_number text,
  usual_amount_min_minor numeric(78,0),
  usual_amount_max_minor numeric(78,0),
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX counterparties_team ON counterparties (team_id, display_name);
CREATE TABLE contact_channels (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  counterparty_id text NOT NULL REFERENCES counterparties,
  kind text NOT NULL CHECK (kind IN ('phone', 'email', 'contract')),
  value text NOT NULL,
  added_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE payment_destinations (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  counterparty_id text NOT NULL REFERENCES counterparties,
  rail text NOT NULL CHECK (rail IN ('FIAT_BANK', 'CRYPTO')),
  asset text NOT NULL,
  masked_display text NOT NULL,
  details jsonb NOT NULL,
  chain_id integer,
  token_contract text,
  status text NOT NULL DEFAULT 'UNVERIFIED' CHECK (status IN ('UNVERIFIED', 'VERIFIED', 'REVOKED')),
  source text NOT NULL,
  added_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);
CREATE INDEX payment_destinations_cp ON payment_destinations (team_id, counterparty_id, status);
CREATE TABLE destination_verifications (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  destination_id text NOT NULL REFERENCES payment_destinations,
  method text NOT NULL CHECK (method IN ('CALL_BACK', 'SIGNED_CONTRACT', 'PROVIDER_CHECK', 'CRYPTO_WALLET', 'NEW_RELATIONSHIP')),
  channel_id text REFERENCES contact_channels,
  evidence text NOT NULL,
  verified_by text NOT NULL REFERENCES users,
  same_person_added boolean NOT NULL DEFAULT false,
  verified_at timestamptz NOT NULL DEFAULT now()
);

-- Bills
CREATE TABLE documents (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  filename text NOT NULL,
  media_type text NOT NULL,
  sha256 text NOT NULL,
  storage_path text NOT NULL,
  text_content text,
  uploaded_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_hash ON documents (team_id, sha256);
CREATE TABLE extractions (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  document_id text NOT NULL REFERENCES documents,
  fields jsonb NOT NULL,
  failed_quotes jsonb NOT NULL DEFAULT '[]',
  model text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE obligations (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  counterparty_id text REFERENCES counterparties,
  source_type text NOT NULL CHECK (source_type IN ('INVOICE', 'REQUEST', 'OCCURRENCE', 'PURCHASE')),
  document_id text REFERENCES documents,
  extraction_id text REFERENCES extractions,
  description text,
  amount_minor numeric(78,0),
  asset text,
  due_date date,
  invoice_reference text,
  period text,
  status text NOT NULL CHECK (status IN ('REGISTERED', 'NEEDS_INFORMATION', 'NEEDS_REVIEW', 'VERIFIED', 'BLOCKED', 'PAYMENT_PREPARED', 'PAYMENT_IN_PROGRESS', 'PAID', 'FAILED', 'OUTCOME_UNKNOWN', 'SUPERSEDED', 'CANCELLED')),
  superseded_by text REFERENCES obligations,
  mandate_id text,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX obligations_team ON obligations (team_id, status, due_date);
-- One live bill per supplier and invoice number. Superseded, cancelled and blocked bills don't count.
CREATE UNIQUE INDEX one_live_invoice ON obligations (team_id, counterparty_id, invoice_reference)
  WHERE invoice_reference IS NOT NULL AND status NOT IN ('SUPERSEDED', 'CANCELLED', 'BLOCKED');
CREATE TABLE field_corrections (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  obligation_id text NOT NULL REFERENCES obligations,
  field text NOT NULL,
  old_value jsonb,
  new_value jsonb,
  is_money_field boolean NOT NULL,
  corrected_by text NOT NULL REFERENCES users,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Checks
CREATE TABLE check_results (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  obligation_id text REFERENCES obligations,
  check_name text NOT NULL,
  rule_id text NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('PASS', 'NOT_APPLICABLE', 'ELIGIBLE_FOR_APPROVAL', 'NEEDS_INFORMATION', 'NEEDS_REVIEW', 'BLOCK')),
  reason text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}',
  policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX check_results_obligation ON check_results (team_id, obligation_id);
CREATE TABLE decisions (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  obligation_id text REFERENCES obligations,
  decision text NOT NULL CHECK (decision IN ('ELIGIBLE_FOR_APPROVAL', 'NEEDS_INFORMATION', 'NEEDS_REVIEW', 'BLOCK')),
  reasons jsonb NOT NULL,
  explanation text,
  policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE investigations (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  decision_id text REFERENCES decisions,
  findings jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Cash
CREATE TABLE financial_accounts (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  name text NOT NULL,
  provider text NOT NULL,
  rail text NOT NULL CHECK (rail IN ('FIAT_BANK', 'CRYPTO')),
  asset text NOT NULL,
  chain_id integer,
  token_contract text,
  external_id text NOT NULL,
  reserve_minor numeric(78,0) NOT NULL DEFAULT 0,
  available_excludes_in_flight boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'ACTIVE'
);
CREATE TABLE balance_snapshots (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  account_id text NOT NULL REFERENCES financial_accounts,
  confirmed_minor numeric(78,0) NOT NULL,
  available_minor numeric(78,0) NOT NULL,
  as_of timestamptz NOT NULL,
  source text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX balance_snapshots_latest ON balance_snapshots (account_id, as_of DESC);
CREATE TABLE cash_analyses (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE alerts (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  account_id text NOT NULL REFERENCES financial_accounts,
  shortfall_date date NOT NULL,
  shortfall_minor numeric(78,0) NOT NULL,
  driving_obligation_ids jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (team_id, account_id, shortfall_date)
);

-- Payments
CREATE TABLE payment_intents (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  obligation_id text NOT NULL REFERENCES obligations,
  counterparty_id text NOT NULL REFERENCES counterparties,
  destination_id text NOT NULL REFERENCES payment_destinations,
  source_account_id text NOT NULL REFERENCES financial_accounts,
  run_id text REFERENCES routine_runs,
  rail text NOT NULL,
  provider text NOT NULL,
  amount_minor numeric(78,0) NOT NULL,
  asset text NOT NULL,
  payment_reference text NOT NULL,
  max_fee_minor numeric(78,0) NOT NULL DEFAULT 0,
  chain_id integer,
  token_contract text,
  canonical jsonb NOT NULL,
  payload_hash text NOT NULL,
  idempotency_key text NOT NULL,
  warnings jsonb NOT NULL DEFAULT '[]',
  requires_review boolean NOT NULL DEFAULT false,
  status text NOT NULL CHECK (status IN ('READY_FOR_APPROVAL', 'APPROVED', 'SUBMITTING', 'SUBMITTED', 'PENDING', 'CONFIRMED', 'FAILED', 'REJECTED', 'UNKNOWN', 'NEEDS_MANUAL_REVIEW', 'CANCELLED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (team_id, idempotency_key)
);
CREATE INDEX payment_intents_team ON payment_intents (team_id, status);
-- An obligation has one payment that can still move money.
CREATE UNIQUE INDEX one_live_intent_per_obligation ON payment_intents (obligation_id)
  WHERE status NOT IN ('FAILED', 'REJECTED', 'CANCELLED');

CREATE TABLE approvals (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  intent_id text NOT NULL REFERENCES payment_intents,
  payload_hash text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
  decided_by text REFERENCES users,
  decided_at timestamptz,
  reason text,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_open_approval ON approvals (intent_id) WHERE status IN ('PENDING', 'APPROVED');

CREATE TABLE payment_attempts (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  intent_id text NOT NULL REFERENCES payment_intents,
  approval_id text NOT NULL REFERENCES approvals,
  provider text NOT NULL,
  idempotency_key text NOT NULL,
  external_id text,
  signed_tx text,
  tx_hash text,
  state text NOT NULL CHECK (state IN ('RECORDED', 'SUBMITTED', 'REJECTED', 'UNKNOWN', 'PENDING', 'CONFIRMED', 'FAILED', 'NEEDS_MANUAL_REVIEW', 'MANUALLY_RESOLVED')),
  evidence_label text NOT NULL DEFAULT 'SIMULATED' CHECK (evidence_label IN ('SIMULATED', 'SANDBOX_OBSERVED', 'TESTNET_OBSERVED')),
  poll_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (team_id, provider, idempotency_key)
);
-- One active attempt per payment.
CREATE UNIQUE INDEX one_active_attempt ON payment_attempts (intent_id) WHERE state NOT IN ('REJECTED', 'FAILED');

CREATE TABLE external_events (
  id text PRIMARY KEY,
  team_id text REFERENCES teams,
  attempt_id text REFERENCES payment_attempts,
  provider text NOT NULL,
  provider_event_id text,
  source text NOT NULL CHECK (source IN ('WEBHOOK', 'QUERY', 'SUBMIT')),
  signature_valid boolean,
  observed_status text,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_event_id)
);

-- Recurring
CREATE TABLE templates (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  name text NOT NULL,
  counterparty_id text NOT NULL REFERENCES counterparties,
  destination_id text REFERENCES payment_destinations,
  amount_minor numeric(78,0),
  asset text NOT NULL,
  purpose text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mandates (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  counterparty_id text NOT NULL REFERENCES counterparties,
  allowed_destination_ids jsonb NOT NULL,
  max_amount_minor numeric(78,0) NOT NULL,
  asset text NOT NULL,
  required_evidence jsonb NOT NULL DEFAULT '[]',
  valid_until date NOT NULL,
  version integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'REVOKED', 'SUPERSEDED')),
  drafted_by text NOT NULL REFERENCES users,
  activated_by text REFERENCES users,
  purpose text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE automation_rules (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  mandate_id text NOT NULL REFERENCES mandates,
  frequency text NOT NULL CHECK (frequency IN ('WEEKLY', 'MONTHLY')),
  day_of_period integer NOT NULL,
  business_day_rule boolean NOT NULL DEFAULT true,
  next_due date,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE occurrences (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  mandate_id text NOT NULL REFERENCES mandates,
  period text NOT NULL,
  obligation_id text REFERENCES obligations,
  claimed_by text NOT NULL CHECK (claimed_by IN ('TIMER', 'INVOICE')),
  run_id text REFERENCES routine_runs,
  is_late boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (mandate_id, period)
);

-- Accounting
CREATE TABLE expense_versions (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  expense_id text NOT NULL,
  version integer NOT NULL,
  obligation_id text REFERENCES obligations,
  intent_id text REFERENCES payment_intents,
  counterparty_id text REFERENCES counterparties,
  business_purpose text,
  category text,
  category_is_suggestion boolean NOT NULL DEFAULT true,
  accounting_period text,
  asset text NOT NULL,
  gross_minor numeric(78,0),
  net_minor numeric(78,0),
  tax_minor numeric(78,0),
  gbp_value_minor numeric(78,0),
  rate_source text,
  status text NOT NULL CHECK (status IN ('DRAFT', 'SUPPORTED', 'REVIEWED', 'ACCOUNTED', 'MISSING_EVIDENCE')),
  reason text,
  actor text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (expense_id, version)
);
CREATE TABLE journal_proposals (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  expense_id text NOT NULL,
  kind text NOT NULL DEFAULT 'INITIAL' CHECK (kind IN ('INITIAL', 'ADJUSTMENT')),
  lines jsonb NOT NULL,
  reviewed boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE exceptions (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  expense_id text NOT NULL,
  kind text NOT NULL,
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
  details jsonb NOT NULL DEFAULT '{}',
  last_reminded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- One open exception per expense.
CREATE UNIQUE INDEX one_open_exception ON exceptions (expense_id, kind) WHERE status = 'OPEN';

-- Purchases
CREATE TABLE research_reports (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  constraints jsonb NOT NULL,
  candidates jsonb NOT NULL,
  coverage jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE research_sources (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  report_id text REFERENCES research_reports,
  run_id text REFERENCES routine_runs,
  url text NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  sha256 text NOT NULL,
  content text NOT NULL
);
CREATE TABLE purchase_summaries (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  run_id text REFERENCES routine_runs,
  merchant text NOT NULL,
  summary jsonb NOT NULL,
  payload_hash text NOT NULL,
  order_number text,
  order_status text NOT NULL DEFAULT 'DRAFT',
  payment_status text NOT NULL DEFAULT 'NONE',
  delivery_status text NOT NULL DEFAULT 'NONE',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Memory
CREATE TABLE memories (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  user_id text REFERENCES users,
  type text NOT NULL CHECK (type IN ('merchant_preference', 'category_hint', 'source_account_hint', 'purchase_constraint', 'reply_style', 'business_context')),
  subject text,
  value jsonb NOT NULL,
  origin text NOT NULL CHECK (origin IN ('confirmed', 'stated', 'observed')),
  source_ids jsonb NOT NULL DEFAULT '[]',
  evidence_count integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'DELETED')),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memories_lookup ON memories (team_id, type, subject) WHERE status = 'ACTIVE';

-- Infrastructure
CREATE TABLE jobs (
  id text PRIMARY KEY,
  kind text NOT NULL,
  team_id text NOT NULL REFERENCES teams,
  payload jsonb NOT NULL DEFAULT '{}',
  run_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'CLAIMED', 'DONE', 'FAILED')),
  locked_by text,
  locked_at timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  dedupe_key text UNIQUE,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready ON jobs (kind, run_at) WHERE status = 'QUEUED';
CREATE TABLE notifications (
  id text PRIMARY KEY,
  team_id text NOT NULL REFERENCES teams,
  user_id text REFERENCES users,
  kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',
  dedupe_key text UNIQUE,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE eval_runs (
  id text PRIMARY KEY,
  suite text NOT NULL,
  system text NOT NULL,
  split text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  summary jsonb,
  started_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE eval_results (
  id text PRIMARY KEY,
  eval_run_id text NOT NULL REFERENCES eval_runs,
  case_id text NOT NULL,
  team_id text REFERENCES teams,
  passed boolean NOT NULL,
  details jsonb NOT NULL,
  cost_micros bigint,
  latency_ms integer,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Simulator's own ledger: stands in for a provider's records, outside PayoAI's state.
CREATE TABLE sim_transfers (
  external_id text PRIMARY KEY,
  provider text NOT NULL,
  idempotency_key text NOT NULL,
  account_external_id text NOT NULL,
  amount_minor numeric(78,0) NOT NULL,
  asset text NOT NULL,
  destination_ref text NOT NULL,
  reference text NOT NULL,
  status text NOT NULL,
  polls integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (provider, idempotency_key)
);
CREATE TABLE sim_balances (
  account_external_id text PRIMARY KEY,
  available_minor numeric(78,0) NOT NULL,
  stale_since timestamptz
);
CREATE TABLE sim_faults (
  reference text PRIMARY KEY,
  fault jsonb NOT NULL
);

-- Audit and expense history can't be rewritten.
CREATE FUNCTION reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION reject_change();
CREATE TRIGGER expense_versions_append_only BEFORE UPDATE OR DELETE ON expense_versions
  FOR EACH ROW EXECUTE FUNCTION reject_change();

-- An approval's payment hash can't change.
CREATE FUNCTION reject_hash_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.payload_hash <> OLD.payload_hash OR NEW.intent_id <> OLD.intent_id THEN
    RAISE EXCEPTION 'an approval cannot be rebound to another payment';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER approvals_hash_fixed BEFORE UPDATE ON approvals
  FOR EACH ROW EXECUTE FUNCTION reject_hash_change();
