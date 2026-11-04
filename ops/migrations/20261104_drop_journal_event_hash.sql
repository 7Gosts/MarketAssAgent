BEGIN;

DROP INDEX IF EXISTS uq_journal_events_event_id;
ALTER TABLE journal_events DROP COLUMN IF EXISTS event_id;

COMMIT;
