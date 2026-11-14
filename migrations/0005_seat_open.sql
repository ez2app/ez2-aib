-- AIB 2.0 migration 0005: what a seat was opened as (B4-1b-1, a9136 11-14,
-- IFACE-b4-1b-seat-open-a9136-1114.md). Add-only: 0004 is not touched, its
-- `role` column stays and is no longer written (Andy 11-14: a persona, not a
-- role). The persona's name is not stored here: it is read from the persona
-- cards when shown, so a rename never leaves a stale name (7b81e 11-14).
ALTER TABLE seat_meta ADD COLUMN mode TEXT CHECK (mode IS NULL OR mode IN ('dgrs', 'safe'));
ALTER TABLE seat_meta ADD COLUMN axis_id TEXT CHECK (axis_id IS NULL OR length(axis_id) BETWEEN 1 AND 64);
ALTER TABLE seat_meta ADD COLUMN cabin TEXT CHECK (cabin IS NULL OR length(cabin) BETWEEN 1 AND 64);
