-- Every stage of the loop appends a row. The dashboard is pure reads.
-- If a live component dies mid-demo, the audit trail still tells the story.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id      TEXT UNIQUE NOT NULL,
    text        TEXT NOT NULL,
    gold_json   TEXT,
    split       TEXT NOT NULL CHECK (split IN ('live', 'holdout')),
    messiness   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_split ON documents(split);

CREATE TABLE IF NOT EXISTS model_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT UNIQUE NOT NULL,
    base_model       TEXT NOT NULL,
    adapter_ref      TEXT,
    status           TEXT NOT NULL CHECK (status IN ('candidate','promoted','rejected')),
    -- 'baseten' serves traffic; 'local' is the laptop-GPU evidence track.
    -- Never gate one against the other -- different base models.
    track            TEXT NOT NULL DEFAULT 'baseten',
    parent_id        INTEGER REFERENCES model_versions(id),
    notes            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    promoted_at      TEXT
);

CREATE TABLE IF NOT EXISTS extractions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id           INTEGER NOT NULL REFERENCES documents(id),
    model_version_id INTEGER REFERENCES model_versions(id),
    raw_output       TEXT NOT NULL,
    parsed_json      TEXT,
    valid            INTEGER NOT NULL,
    signature        TEXT,
    error_count      INTEGER NOT NULL DEFAULT 0,
    latency_ms       INTEGER,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_extractions_valid ON extractions(valid);
CREATE INDEX IF NOT EXISTS idx_extractions_sig ON extractions(signature);

CREATE TABLE IF NOT EXISTS failures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id INTEGER NOT NULL REFERENCES extractions(id),
    doc_id        INTEGER NOT NULL REFERENCES documents(id),
    signature     TEXT NOT NULL,
    error_count   INTEGER NOT NULL,
    errors_json   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new','repaired','unrepairable','consumed')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_failures_status ON failures(status);
CREATE INDEX IF NOT EXISTS idx_failures_sig ON failures(signature);

CREATE TABLE IF NOT EXISTS repairs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    failure_id    INTEGER NOT NULL REFERENCES failures(id),
    doc_id        INTEGER NOT NULL REFERENCES documents(id),
    method        TEXT NOT NULL CHECK (method IN ('mechanical','distill')),
    repaired_json TEXT NOT NULL,
    verified      INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_repairs_verified ON repairs(verified);

CREATE TABLE IF NOT EXISTS training_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id INTEGER REFERENCES model_versions(id),
    baseten_job_id   TEXT,
    dataset_path     TEXT,
    dataset_size     INTEGER,
    repair_count     INTEGER,
    replay_count     INTEGER,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','succeeded','failed')),
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS evals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id INTEGER NOT NULL REFERENCES model_versions(id),
    eval_set_hash    TEXT NOT NULL,
    valid_rate       REAL NOT NULL,
    field_f1         REAL NOT NULL,
    -- Which metric field_f1 actually holds: 'field_f1' (micro-averaged, the
    -- Baseten scorer) or 'mean_f1' (per-document, the local scorer).
    metric           TEXT NOT NULL DEFAULT 'field_f1',
    n_docs           INTEGER NOT NULL,
    per_field_json   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS promotions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL REFERENCES model_versions(id),
    incumbent_id  INTEGER REFERENCES model_versions(id),
    decision      TEXT NOT NULL CHECK (decision IN ('promoted','rejected')),
    margin        REAL NOT NULL,
    required      REAL NOT NULL,
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Single-row table holding the frozen holdout fingerprint.
CREATE TABLE IF NOT EXISTS eval_freeze (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    set_hash   TEXT NOT NULL,
    n_docs     INTEGER NOT NULL,
    doc_ids    TEXT NOT NULL,
    frozen_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
