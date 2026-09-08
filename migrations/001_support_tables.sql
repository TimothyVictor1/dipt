-- DIPT support table: the scheduler's fetch log.
--
-- This sits outside the original six-table schema. It is created separately
-- because the application role (dipt_user) does not hold CREATE on schema
-- public. Run this once as the database owner:
--
--     psql -U postgres -d dipt -f migrations/001_support_tables.sql
--
-- It is idempotent: re-running it makes no further changes.
--
-- NOTE: admin-edited agent prompts do NOT need a database table. They live in
-- config/agent_prompts/<agent>.txt (see dipt/prompt_store.py) and are editable
-- from the dashboard Settings page with no migration and no DB privileges.

BEGIN;

-- One row per pipeline run. The scheduler reads the most recent successful
-- run's started_at so each run only fetches papers newer than the last.
CREATE TABLE IF NOT EXISTS fetch_log (
    id           SERIAL PRIMARY KEY,
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP,
    papers_saved INTEGER NOT NULL DEFAULT 0,
    status       VARCHAR(16) NOT NULL DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS fetch_log_status_started_idx
    ON fetch_log (status, started_at DESC);

-- Let the application role read and write it.
GRANT SELECT, INSERT, UPDATE, DELETE ON fetch_log TO dipt_user;
GRANT USAGE, SELECT ON SEQUENCE fetch_log_id_seq TO dipt_user;

COMMIT;
