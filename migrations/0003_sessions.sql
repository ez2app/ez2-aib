-- AIB 2.0 migration 0003: sessions (spec 4.1 / 4.2 / 5). The current row of
-- each session; every change writes its event in the same transaction.
-- argv and env are not stored (secrets, spec 9.4): only argv[0]'s file name
-- and the argument count, as in the events.
CREATE TABLE sessions (
    session_id   TEXT    PRIMARY KEY,
    seat_id      TEXT    REFERENCES seats (seat_id),   -- NULL: started through the preview.2 route
    pid          INTEGER NOT NULL,
    argv0        TEXT,
    argc         INTEGER NOT NULL,
    state        TEXT    NOT NULL,                     -- running | exited | killed | stop_unconfirmed | interrupted
    holds_slot   INTEGER NOT NULL CHECK (holds_slot IN (0, 1)),
    started_at   TEXT    NOT NULL,
    ended_at     TEXT,
    ended_by     TEXT,                                 -- root_exit | api | shutdown | aibd_restart
    exit_code    INTEGER,
    signal       TEXT,
    stop_status  TEXT,
    stop_reason  TEXT,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX sessions_seat ON sessions (seat_id, started_at);
