-- AIB 2.0 migration 0002: seats (spec 4.1, 4.2, 5) and idempotency keys (spec 8).
-- A seat row is the current state; every change also writes an event in the
-- same transaction (the events table is the outbox, ADR 0003).
CREATE TABLE seats (
    seat_id     TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    state       TEXT    NOT NULL,                  -- aib_core::SeatState, SCREAMING_SNAKE_CASE
    revision    INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at  TEXT    NOT NULL,                  -- UTC RFC 3339
    updated_at  TEXT    NOT NULL
);
CREATE INDEX seats_created ON seats (created_at, seat_id);

-- One row per (route, key): a retried write returns the first answer, a
-- different request under the same key is refused.
CREATE TABLE idempotency_keys (
    scope       TEXT    NOT NULL,                  -- e.g. 'POST /api/v1/seats'
    key         TEXT    NOT NULL,
    request     TEXT    NOT NULL,                  -- canonical request body
    status      INTEGER NOT NULL,
    response    TEXT    NOT NULL CHECK (json_valid(response)),
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (scope, key)
);
