-- Additive proposal for the existing LeaseStore database, not a replacement DB.
-- Apply under the migration lock with foreign_keys=ON; existing tables retained.
-- Existing lease timestamps remain seconds. New *_ms fields are milliseconds.
-- Immutable refs below are digests verified by the object store, not file paths.
CREATE TABLE IF NOT EXISTS df_schema_migrations (
    version INTEGER PRIMARY KEY,
    sql_sha256 TEXT NOT NULL CHECK(length(sql_sha256)=64),
    applied_at_ms INTEGER NOT NULL CHECK(applied_at_ms>=0)
);

CREATE TABLE IF NOT EXISTS df_work (
    work_key TEXT PRIMARY KEY CHECK(length(work_key)=64),
    project TEXT NOT NULL,
    proposal_sha256 TEXT NOT NULL CHECK(length(proposal_sha256)=64),
    programme_sha256 TEXT NOT NULL CHECK(length(programme_sha256)=64),
    created_sequence INTEGER NOT NULL CHECK(created_sequence>=0),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 4),
    UNIQUE(work_key,project)
);

CREATE TABLE IF NOT EXISTS df_attempts (
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL REFERENCES df_work(work_key),
    project TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number>=1),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    state TEXT NOT NULL CHECK(state IN (
      'ready','budget_pending','reserved','dispatching','dispatched','running',
      'succeeded','failed','cancelled','uncertain','stale')),
    version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
    lease_id TEXT REFERENCES leases(lease_id),
    budget_receipt_sha256 TEXT,
    envelope_sha256 TEXT,
    result_sha256 TEXT,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>=created_at_ms),
    FOREIGN KEY(work_key,project) REFERENCES df_work(work_key,project),
    UNIQUE(work_key, attempt_number),
    CHECK(state NOT IN ('reserved','dispatching','dispatched','running') OR
      (lease_id IS NOT NULL AND budget_receipt_sha256 IS NOT NULL AND envelope_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS df_one_active_attempt_per_project
ON df_attempts(project)
WHERE state IN ('budget_pending','reserved','dispatching','dispatched','running','uncertain');

CREATE TABLE IF NOT EXISTS df_outbox (
    outbox_id TEXT PRIMARY KEY,
    attempt_id TEXT REFERENCES df_attempts(attempt_id),
    project TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('budget_reserve','project_event','dispatch','cancel','wake')),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    state TEXT NOT NULL CHECK(state IN ('pending','sending','delivered','uncertain','refused')),
    version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
    receipt_sha256 TEXT,
    next_observation_at_ms INTEGER,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
    CHECK(state!='delivered' OR receipt_sha256 IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS df_executor_registrations (
    registration_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES df_attempts(attempt_id),
    repository_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_attempt INTEGER NOT NULL CHECK(run_attempt>=1),
    oidc_token_sha256 TEXT NOT NULL UNIQUE CHECK(length(oidc_token_sha256)=64),
    envelope_sha256 TEXT NOT NULL CHECK(length(envelope_sha256)=64),
    credential_sha256 TEXT NOT NULL UNIQUE CHECK(length(credential_sha256)=64),
    expires_at_ms INTEGER NOT NULL,
    last_heartbeat_at_ms INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    UNIQUE(repository_id,run_id,run_attempt)
);

CREATE TABLE IF NOT EXISTS df_wakeups (
    project TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    processed_at_ms INTEGER,
    PRIMARY KEY(project,source_event_id)
);

CREATE TABLE IF NOT EXISTS df_provider_calls (
    call_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES df_attempts(attempt_id),
    invocation_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
    allowance_receipt_sha256 TEXT NOT NULL,
    grant_id TEXT REFERENCES grants(grant_id),
    state TEXT NOT NULL CHECK(state IN ('reserved','started','observed','not_started','uncertain')),
    reserved_microusd INTEGER NOT NULL CHECK(reserved_microusd>=0),
    billed_microusd INTEGER CHECK(billed_microusd>=0),
    estimated_microusd INTEGER CHECK(estimated_microusd>=0),
    result_sha256 TEXT,
    unknown_reason TEXT,
    CHECK(state!='started' OR grant_id IS NOT NULL),
    CHECK(state!='uncertain' OR unknown_reason IS NOT NULL)
);
-- This table is a durable call index/audit link to the existing budget/meter.
-- Summing it never creates an allowance or refunds an ExecutionBudget reservation.
