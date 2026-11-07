-- AIB 2.0 migration 0001: event log (ADR 0003: the events table is the outbox).
CREATE TABLE events (
    global_seq      INTEGER PRIMARY KEY,           -- one cursor across all aggregates
    event_id        TEXT    NOT NULL UNIQUE,
    aggregate_type  TEXT    NOT NULL,
    aggregate_id    TEXT    NOT NULL,
    sequence        INTEGER NOT NULL CHECK (sequence >= 1),
    type            TEXT    NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    correlation_id  TEXT,
    occurred_at     TEXT    NOT NULL,              -- UTC RFC 3339
    payload         TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    UNIQUE (aggregate_id, sequence)
);
CREATE INDEX events_aggregate ON events (aggregate_id, sequence);

CREATE TABLE event_consumers (
    name            TEXT    PRIMARY KEY,
    last_global_seq INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);
