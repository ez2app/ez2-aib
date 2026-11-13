-- AIB 2.0 migration 0004: what reviving a seat needs (A11 S37) and who was
-- last seen alive (A11 T46). DESIGN-A11-revive-rust-2240e-1113.md §1 / §4.
--
-- seat_meta: one row per seat (1:1), written in the same transaction as the
-- seat, so a seat that exists can always be revived. Kept out of `seats` on
-- purpose: Seat is the API contract, and cwd / resume id must not appear in
-- the seat list. resume_id is NULL until the CLI reports it (pending); an
-- empty string never stands for a value.
CREATE TABLE seat_meta (
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
    updated_at   TEXT    NOT NULL                     -- UTC RFC 3339
);
CREATE INDEX seat_meta_resume ON seat_meta (resume_id) WHERE resume_id IS NOT NULL;

-- cabin_alive: last time each cabin was seen running, and whether this
-- outage was already reported. A live round clears alerted_at; a cabin
-- closed on purpose loses its row. Times are Unix seconds (threshold math).
CREATE TABLE cabin_alive (
    cabin       TEXT    NOT NULL PRIMARY KEY CHECK (length(cabin) >= 1),
    last_seen   INTEGER NOT NULL,
    seats_json  TEXT    NOT NULL CHECK (json_valid(seats_json) AND json_type(seats_json) = 'array'),
    alerted_at  INTEGER
);
