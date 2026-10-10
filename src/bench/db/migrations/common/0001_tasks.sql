-- common 0001: projects, tasks, and notes for every area.
-- The migration runner owns the schema_version table and the transaction; do not add BEGIN or COMMIT.
-- Version 0 has no delete action. ON DELETE RESTRICT makes an accidental delete fail instead of cascading.

CREATE TABLE projects (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'done', 'archived')),
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE tasks (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
  title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  urgency     INTEGER NOT NULL CHECK (urgency BETWEEN 1 AND 3),
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'archived')),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  done_at     TEXT,
  archived_at TEXT
);
CREATE INDEX tasks_by_status ON tasks (status, urgency DESC, updated_at DESC);

CREATE TABLE task_notes (
  id         INTEGER PRIMARY KEY,
  task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  body       TEXT NOT NULL CHECK (length(body) >= 1)
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
