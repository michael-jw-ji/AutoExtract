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
