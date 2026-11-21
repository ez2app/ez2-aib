-- AIB 2.0 migration 0006: a seat may be opened as `work` (W1 N-ws-live-in, a9136 11-19;
-- 0eed7 a2cd0ccc4 H: 0005's CHECK let only dgrs / safe in, so a work seat could not be
-- written). SQLite cannot change a CHECK in place: seat_meta is rebuilt with the same
-- columns, the same rows and the same index; 0004 / 0005 stay as shipped.
CREATE TABLE seat_meta_0006 (
    seat_id      TEXT    NOT NULL PRIMARY KEY REFERENCES seats (seat_id),
    provider     TEXT    NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    role         TEXT,
    cwd          TEXT    NOT NULL CHECK (length(cwd) >= 1),
    model        TEXT,
    model_pinned INTEGER NOT NULL CHECK (model_pinned IN (0, 1)),
    resume_id    TEXT    CHECK (resume_id IS NULL OR length(resume_id) >= 1),
    project_uid  TEXT,
    persona_uid  TEXT,
    brain_route  TEXT,
    updated_at   TEXT    NOT NULL,                    -- UTC RFC 3339
    mode         TEXT    CHECK (mode IS NULL OR mode IN ('dgrs', 'safe', 'work')),
    axis_id      TEXT    CHECK (axis_id IS NULL OR length(axis_id) BETWEEN 1 AND 64),
    cabin        TEXT    CHECK (cabin IS NULL OR length(cabin) BETWEEN 1 AND 64)
);
INSERT INTO seat_meta_0006 (seat_id, provider, role, cwd, model, model_pinned, resume_id,
                            project_uid, persona_uid, brain_route, updated_at, mode, axis_id, cabin)
    SELECT seat_id, provider, role, cwd, model, model_pinned, resume_id,
           project_uid, persona_uid, brain_route, updated_at, mode, axis_id, cabin
    FROM seat_meta;
DROP INDEX seat_meta_resume;
DROP TABLE seat_meta;
ALTER TABLE seat_meta_0006 RENAME TO seat_meta;
CREATE INDEX seat_meta_resume ON seat_meta (resume_id) WHERE resume_id IS NOT NULL;
