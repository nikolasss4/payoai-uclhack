-- An invoice that arrives for a period the timer already claimed attaches to that Occurrence.
ALTER TABLE occurrences
  ADD COLUMN document_id text REFERENCES documents,
  ADD COLUMN extraction_id text REFERENCES extractions;

-- Notes on the approval card that aren't warnings, such as a remembered source account.
ALTER TABLE payment_intents ADD COLUMN notes jsonb NOT NULL DEFAULT '[]';

-- An Approver settled this by hand as paid. It stays labelled as manual, not externally confirmed.
ALTER TABLE payment_intents DROP CONSTRAINT payment_intents_status_check;
ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_status_check CHECK (status IN (
  'READY_FOR_APPROVAL', 'APPROVED', 'SUBMITTING', 'SUBMITTED', 'PENDING', 'CONFIRMED', 'FAILED', 'REJECTED',
  'UNKNOWN', 'NEEDS_MANUAL_REVIEW', 'MANUALLY_RESOLVED', 'CANCELLED'
));

-- A late confirmation must be recordable even when a replacement payment is already live, because
-- that is exactly the duplicate people need to see. Prepare Payment refuses a bill already paid.
DROP INDEX one_live_intent_per_obligation;
CREATE UNIQUE INDEX one_live_intent_per_obligation ON payment_intents (obligation_id)
  WHERE status NOT IN ('FAILED', 'REJECTED', 'CANCELLED', 'CONFIRMED');

-- One expense per payment, even if two reconciliations see the same confirmation.
CREATE UNIQUE INDEX one_expense_per_intent ON expense_versions (intent_id)
  WHERE version = 1 AND intent_id IS NOT NULL;

-- The amount a Mandate normally pays. Without one, the timer can only assume the maximum, which is flagged.
ALTER TABLE mandates ADD COLUMN regular_amount_minor numeric(78,0);
