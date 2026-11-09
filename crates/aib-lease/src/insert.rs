// SPDX-License-Identifier: Apache-2.0
//! D4: one short, light run next to a long holder (DECISION-test-classify v1.1 item 5,
//! DECISION-d4-insert-043dd-1110).
//!
//! The insert slot is `<lease>.insert.json`. It is read and written only under the lease's own
//! lock (`<lease>.lock`, the one host_lease.py takes too), so "is the holder exclusive, is anyone
//! starving in the queue, is the slot free" and "take the slot" are one critical section, and an
//! exclusive `acquire` in this crate sees a running insert and waits for it.
//!
//! **Off by default.** The 1.x `aib lease acquire` and test_entry's small batches do not know
//! the slot: until every lease entry on a machine is this crate, an insert could overlap an
//! exclusive holder that took the lease through 1.x. A machine opts in with the file
//! `<lease>.insert-enabled`. That file only lets a measurement be less clean; it opens no gate.
//!
//! The slot is taken only when all of these hold:
//! * the machine opted in;
//! * a lease is held, by a holder that is alive and **not** exclusive (no holder ⇒ take the
//!   lease itself);
//! * nobody in the queue has waited [`STARVE_S`] or more (the long waiter gets the next turn);
//! * the slot is free (its holder dead, or the record gone).
//!
//! The run that inserts brings its own hard timeout ([`HARD_MIN_S`]..=[`HARD_MAX_S`]); a record
//! whose pid is still alive keeps the slot even past it, because a shell that has not finished
//! stopping its tree has not finished.

use super::*;

pub const INSERT_SCHEMA: &str = "aib-host-lease-insert/v1";
/// v1.1: a long waiter of 30 min or more keeps the next turn.
pub const STARVE_S: i64 = 30 * 60;
/// v1.1: hard timeout max(p90 × 2, 5 min) …
pub const HARD_MIN_S: i64 = 5 * 60;
/// … at most 15 min.
pub const HARD_MAX_S: i64 = 15 * 60;

impl Paths {
    pub fn insert(&self) -> PathBuf {
        self.with_suffix(".insert.json")
    }
    pub fn insert_enabled(&self) -> PathBuf {
        self.with_suffix(".insert-enabled")
    }
}

/// `Ok(None)` = no insert record; `Err` = unreadable (fail closed: counts as taken).
pub fn read_insert(paths: &Paths) -> Result<Option<Rec>, String> {
    let p = paths.insert();
    let text = match fs::read_to_string(&p) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("unreadable {}: {e}", p.display())),
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) if s(&m, "schema") == Some(INSERT_SCHEMA) => Ok(Some(m)),
        _ => Err(format!("bad insert record {}", p.display())),
    }
}

/// Is this insert record still running? Only a dead (or reused) pid frees it.
pub fn insert_live(r: &Rec, p: &dyn Probe) -> bool {
    !matches!(holder_alive(r, p), Live::Dead | Live::Reused)
}

/// The insert record that blocks an exclusive acquire, if any (unreadable counts).
fn running_insert(paths: &Paths, p: &dyn Probe) -> Result<Option<Rec>, String> {
    match read_insert(paths)? {
        Some(r) if insert_live(&r, p) => Ok(Some(r)),
        _ => Ok(None),
    }
}

/// For [`acquire`]: an exclusive acquire must wait for a running insert (Codex D4 #3 / #4).
pub(crate) fn exclusive_blocked(paths: &Paths, p: &dyn Probe) -> Option<Outcome> {
    match running_insert(paths, p) {
        Ok(None) => None,
        Ok(Some(r)) => Some(done(
            RC_HELD,
            json!({"result": "insert_running", "insert": {"owner": s_or(&r, "owner"),
                   "what": s_or(&r, "what"), "run_id": s_or(&r, "run_id"),
                   "deadline": s_or(&r, "deadline_at")}}),
        )),
        Err(e) => Some(unreadable(e)),
    }
}

#[derive(Debug, Clone, Default)]
pub struct InsertArgs {
    pub holder: Holder,
    pub hard_timeout_s: i64,
}

/// Take the insert slot (see the module notes for when).
pub fn insert_acquire(paths: &Paths, p: &dyn Probe, now: f64, a: &InsertArgs) -> Outcome {
    let local = p.hostname();
    let h = &a.holder;
    if let Err(e) = h.check(&local) {
        return done(RC_USAGE, json!({"result": "usage", "error": e}));
    }
    if h.remote(&local) || h.pid.is_none() {
        return done(
            RC_USAGE,
            json!({"result": "usage", "error": "an insert runs on this machine: --pid of its shell, no --host"}),
        );
    }
    if !(HARD_MIN_S..=HARD_MAX_S).contains(&a.hard_timeout_s) {
        return done(
            RC_USAGE,
            json!({"result": "usage", "error": format!("--hard-timeout-s must be {HARD_MIN_S}..={HARD_MAX_S}")}),
        );
    }
    let _m = match mutex(paths) {
        Ok(m) => m,
        Err(e) => return unreadable(e),
    };
    let deny = |why: &str, extra: Value| {
        log_event(
            paths,
            p,
            now,
            "insert_denied",
            json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "why": why}),
        );
        let mut out = json!({"result": why});
        if let (Value::Object(o), Value::Object(x)) = (&mut out, extra) {
            o.extend(x);
        }
        done(RC_HELD, out)
    };
    if !paths.insert_enabled().exists() {
        return deny("insert_disabled", json!({}));
    }
    let cur = match read_lease(paths) {
        Ok(c) => c,
        Err(e) => return unreadable(e),
    };
    let Some(cur) = cur.filter(|c| !is_expired(c, now, p)) else {
        return deny("no_long_holder", json!({}));
    };
    if cur
        .get("exclusive")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return deny("holder_exclusive", json!({"holder": holder_view(&cur)}));
    }
    if let Some(e) = queue_live(paths, now, p)
        .into_iter()
        .find(|e| waited(e, now) >= STARVE_S)
    {
        return deny(
            "long_waiter",
            json!({"waiter": s_or(&e, "run_id"), "waited_s": waited(&e, now)}),
        );
    }
    match read_insert(paths) {
        Err(e) => return unreadable(e),
        Ok(Some(r)) if insert_live(&r, p) && s(&r, "run_id") != Some(h.run_id.as_str()) => {
            return deny(
                "insert_taken",
                json!({"insert": s_or(&r, "run_id"), "deadline": s_or(&r, "deadline_at")}),
            );
        }
        Ok(_) => {}
    }
    let deadline = now as i64 + a.hard_timeout_s;
    let mut r = Rec::new();
    r.insert("schema".into(), json!(INSERT_SCHEMA));
    r.insert("owner".into(), json!(h.owner));
    r.insert("what".into(), json!(h.what));
    r.insert("run_id".into(), json!(h.run_id));
    h.stamp(&mut r, &local, now, p);
    r.insert("started_epoch".into(), json!(now as i64));
    r.insert("hard_timeout_s".into(), json!(a.hard_timeout_s));
    r.insert("deadline_epoch".into(), json!(deadline));
    r.insert("deadline_at".into(), json!(p.iso(deadline)));
    r.insert("main_run_id".into(), json!(s(&cur, "run_id")));
    if let Err(e) = write_json(&paths.insert(), &Value::Object(r)) {
        return unreadable(e);
    }
    log_event(
        paths,
        p,
        now,
        "insert",
        json!({"owner": h.owner, "what": h.what, "run_id": h.run_id,
               "main_run_id": s(&cur, "run_id"), "hard_timeout_s": a.hard_timeout_s}),
    );
    done(
        RC_OK,
        json!({"result": "inserted", "run_id": h.run_id, "main_run_id": s(&cur, "run_id"),
               "deadline": p.iso(deadline)}),
    )
}

/// Give the slot back; only the run that holds it can.
pub fn insert_release(paths: &Paths, p: &dyn Probe, now: f64, run_id: &str) -> Outcome {
    let _m = match mutex(paths) {
        Ok(m) => m,
        Err(e) => return unreadable(e),
    };
    match read_insert(paths) {
        Err(e) => unreadable(e),
        Ok(Some(r)) if s(&r, "run_id") == Some(run_id) => {
            if let Err(e) = fs::remove_file(paths.insert()) {
                return unreadable(format!("{}: {e}", paths.insert().display()));
            }
            log_event(
                paths,
                p,
                now,
                "insert_release",
                json!({"run_id": run_id, "held_s": now as i64 - r.get("started_epoch").and_then(Value::as_i64).unwrap_or(now as i64)}),
            );
            done(RC_OK, json!({"result": "released", "run_id": run_id}))
        }
        Ok(r) => done(
            RC_HELD,
            json!({"result": "not_holder", "insert": r.as_ref().map(|r| s_or(r, "run_id"))}),
        ),
    }
}
