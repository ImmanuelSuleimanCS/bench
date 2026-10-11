-- common 0002: nothing is ever deleted, and notes are never edited, even by hand in sqlite3.
-- ON DELETE RESTRICT (0001) only protects a row that another row points at; these triggers protect every row.
-- Archive and restore are the whole lifecycle. A future permanent delete would drop a trigger in its own migration.

CREATE TRIGGER tasks_never_deleted BEFORE DELETE ON tasks
BEGIN
  SELECT RAISE(ABORT, 'Bench never deletes tasks; archive instead');
END;

CREATE TRIGGER projects_never_deleted BEFORE DELETE ON projects
BEGIN
  SELECT RAISE(ABORT, 'Bench never deletes projects; archive instead');
END;

CREATE TRIGGER notes_never_deleted BEFORE DELETE ON task_notes
BEGIN
  SELECT RAISE(ABORT, 'Bench never deletes notes');
END;

CREATE TRIGGER notes_never_edited BEFORE UPDATE ON task_notes
BEGIN
  SELECT RAISE(ABORT, 'notes are append only');
END;
