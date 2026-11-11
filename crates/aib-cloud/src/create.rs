//! Background site creation: is the site that says "creating" still being created?
//!
//! `site create` answers "creating + site code" and the work goes on in a background
//! process (PLAN-customer-site-create r2 §8.1 #3). If that process dies — the host
//! reboots, it is killed, it crashes — the record would say "creating" for ever, and
//! because a second `create` from the same user returns the same site code, that user
//! could never open a site again and nobody would be told (33e28 review M1).
//!
//! The worker writes its [`Holder`] (pid + start time + boot id, the same triple the host
//! lock uses since #647) into the creating record before it does anything else — under the
//! host lock, after re-reading the record, and only if [`may_take_holder`] says the attempt
//! is still its own and unheld; otherwise it ends without creating anything. Whoever
//! reads the record — `status`, the next `create`, the first operation after a boot —
//! asks [`verdict`]; on [`Verdict::Interrupted`] it takes the host lock, re-reads the
//! record and marks it unfinished only if [`still_same_attempt`] says nothing changed in
//! between. Pure decision code: the caller reads `/proc`, the clock and the record.
//!
//! In the site record (PLAN-customer-site-create §11.8, 4542e): `state` is `creating`,
//! `ready` or `unfinished` (a record without it is `ready`: the sites from before);
//! `created_at` is UTC seconds, written when the code is given; `holder` has the host
//! lock's shape. Reading those fields into [`CreateState`] and a [`Holder`] is the caller's.
//!
//! `created_at` has one-second steps, so two attempts started within the same second
//! would read as one in [`may_take_holder`] and [`still_same_attempt`]. That cannot
//! happen in practice: a new attempt needs the old one marked unfinished first, which
//! takes [`START_GRACE_SECS`] at least (c477a liveness review r2 N2).

use crate::op::{lock, Holder, Liveness, Lock};

/// The creation state written in a site record.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreateState {
    /// The background worker is (or should be) creating it.
    Creating,
    /// Finished; the site can be used.
    Ready,
    /// Stopped half way; kept, counted against the plan, not usable (r2 §8.1 #4).
    Unfinished,
}

/// What the reader should conclude.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Being created: tell the user to look again later.
    Creating,
    /// The worker is gone (or never started): mark the record unfinished and tell us.
    Interrupted,
    /// Whether the worker lives could not be established: say "creating", add that the
    /// state cannot be checked right now, change nothing.
    Unknown,
    /// Finished.
    Ready,
    /// Already marked unfinished.
    Unfinished,
}

/// Seconds a "creating" record may go without a holder before the start is taken as
/// failed: the request handler writes the record under the host lock, then starts the
/// worker, whose first act is to write its holder. Only a worker that never started is
/// judged by time; once there is a holder, slow is not dead.
pub const START_GRACE_SECS: u64 = 120;

/// Decide about one site record. `created_at` is when the record was written in the
/// "creating" state; `now` is the current time (same clock); `boot_id` is the current
/// boot; `liveness` is what `/proc` says about the holder (ignored without one).
pub fn verdict(
    state: CreateState,
    holder: Option<&Holder>,
    created_at: u64,
    now: u64,
    boot_id: &str,
    liveness: Liveness,
) -> Verdict {
    match state {
        CreateState::Ready => Verdict::Ready,
        CreateState::Unfinished => Verdict::Unfinished,
        CreateState::Creating => match holder {
            None if now.saturating_sub(created_at) > START_GRACE_SECS => Verdict::Interrupted,
            None => Verdict::Creating,
            // same rules as the host lock: another boot, or that pid + start time is gone
            Some(_) => match lock(holder, boot_id, liveness) {
                Lock::TakeOver => Verdict::Interrupted,
                Lock::Busy if liveness == Liveness::Unknown => Verdict::Unknown,
                Lock::Busy | Lock::Take => Verdict::Creating,
            },
        },
    }
}

/// After taking the host lock and re-reading the record: may it now be marked
/// unfinished? Only if it is still "creating" with the very holder (or the same missing
/// holder and the same `created_at`) that the verdict was about. Otherwise the worker
/// finished in between, another reader already marked it, or a newer attempt started —
/// and overwriting would destroy that.
pub fn still_same_attempt(
    judged: (Option<&Holder>, u64),
    now_state: CreateState,
    now_record: (Option<&Holder>, u64),
) -> bool {
    now_state == CreateState::Creating && judged.0 == now_record.0 && judged.1 == now_record.1
}

/// The worker's side, under the host lock, after re-reading the record and before it does
/// anything else: may it write its holder and go on? Only if the record is still
/// "creating", nobody holds it yet, and it is the attempt this worker was started for
/// (the same `created_at`). Otherwise — a reader already marked it unfinished after the
/// start grace, another worker holds it, or a newer attempt replaced it — the worker
/// must end without creating anything; going on would leave a record that says
/// "unfinished" next to a site that opens (c477a liveness review L1).
pub fn may_take_holder(
    my_created_at: u64,
    now_state: CreateState,
    now_record: (Option<&Holder>, u64),
) -> bool {
    now_state == CreateState::Creating && now_record.0.is_none() && now_record.1 == my_created_at
}

/// How long a "creating" record may read [`Verdict::Unknown`] before we are told
/// (c477a liveness review N1). Counted from `created_at`: only a record with a holder
/// can be Unknown, and a normal creation, licences included, stays well under this.
pub const UNKNOWN_ALERT_SECS: u64 = 600;

/// Should this read of a record tell us (never the customer) that whether its worker
/// lives has not been known for too long? Only Unknown counts; [`Verdict::Interrupted`]
/// has its own message. Counted from `created_at`, not from the first Unknown: Unknown
/// changes nothing in the record, so there is nowhere to keep that first time. The
/// caller sends it once per site code and `created_at` (a new attempt is a new alert).
pub fn unknown_needs_alert(v: Verdict, created_at: u64, now: u64) -> bool {
    v == Verdict::Unknown && now.saturating_sub(created_at) > UNKNOWN_ALERT_SECS
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::borrow::ToOwned;

    const BOOT: &str = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
    const OTHER_BOOT: &str = "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a";
    const T0: u64 = 1_800_000_000;

    fn holder(boot: &str) -> Holder {
        Holder {
            op: "site.create:s1a2b3c".to_owned(),
            pid: 4242,
            start_time: 987_654,
            boot_id: boot.to_owned(),
        }
    }

    #[test]
    fn finished_states_pass_through() {
        for l in [Liveness::Alive, Liveness::Gone, Liveness::Unknown] {
            let h = holder(BOOT);
            assert_eq!(
                verdict(CreateState::Ready, Some(&h), T0, T0 + 9_999, BOOT, l),
                Verdict::Ready
            );
            assert_eq!(
                verdict(CreateState::Unfinished, None, T0, T0, BOOT, l),
                Verdict::Unfinished
            );
        }
    }

    // a live worker is creating however long it takes: slow is not dead
    #[test]
    fn alive_worker_is_creating_however_slow() {
        let h = holder(BOOT);
        for now in [T0, T0 + 60, T0 + 86_400 * 7] {
            assert_eq!(
                verdict(
                    CreateState::Creating,
                    Some(&h),
                    T0,
                    now,
                    BOOT,
                    Liveness::Alive
                ),
                Verdict::Creating
            );
        }
    }

    // knife (a): another boot means every process from before it is gone
    #[test]
    fn another_boot_is_interrupted() {
        let h = holder(OTHER_BOOT);
        for l in [Liveness::Alive, Liveness::Unknown, Liveness::Gone] {
            assert_eq!(
                verdict(CreateState::Creating, Some(&h), T0, T0 + 5, BOOT, l),
                Verdict::Interrupted,
                "{l:?}"
            );
        }
    }

    // knife (b): pid gone, or reused by a process started at another time (liveness says Gone)
    #[test]
    fn gone_worker_is_interrupted() {
        let h = holder(BOOT);
        assert_eq!(
            verdict(
                CreateState::Creating,
                Some(&h),
                T0,
                T0 + 5,
                BOOT,
                Liveness::Gone
            ),
            Verdict::Interrupted
        );
    }

    // knife (d): not knowing is not dead
    #[test]
    fn unknown_changes_nothing() {
        let h = holder(BOOT);
        assert_eq!(
            verdict(
                CreateState::Creating,
                Some(&h),
                T0,
                T0 + 5,
                BOOT,
                Liveness::Unknown
            ),
            Verdict::Unknown
        );
        // an unreadable boot id proves nothing either: fall back to liveness
        let damaged = holder("not-a-boot-id");
        assert_eq!(
            verdict(
                CreateState::Creating,
                Some(&damaged),
                T0,
                T0 + 5,
                BOOT,
                Liveness::Unknown
            ),
            Verdict::Unknown
        );
        assert_eq!(
            verdict(
                CreateState::Creating,
                Some(&h),
                T0,
                T0 + 5,
                "",
                Liveness::Alive
            ),
            Verdict::Creating
        );
    }

    // a worker that never started: only then does time decide
    #[test]
    fn missing_holder_waits_out_the_start_grace() {
        for l in [Liveness::Alive, Liveness::Gone, Liveness::Unknown] {
            assert_eq!(
                verdict(
                    CreateState::Creating,
                    None,
                    T0,
                    T0 + START_GRACE_SECS,
                    BOOT,
                    l
                ),
                Verdict::Creating
            );
            assert_eq!(
                verdict(
                    CreateState::Creating,
                    None,
                    T0,
                    T0 + START_GRACE_SECS + 1,
                    BOOT,
                    l
                ),
                Verdict::Interrupted
            );
        }
        // a clock that went backwards is not "late"
        assert_eq!(
            verdict(
                CreateState::Creating,
                None,
                T0,
                T0 - 3_600,
                BOOT,
                Liveness::Gone
            ),
            Verdict::Creating
        );
    }

    // knife (c): re-read under the lock before marking; never overwrite a newer state
    #[test]
    fn mark_only_the_same_attempt() {
        let h = holder(BOOT);
        let judged = (Some(&h), T0);
        assert!(still_same_attempt(
            judged,
            CreateState::Creating,
            (Some(&h), T0)
        ));
        // the worker finished in between
        assert!(!still_same_attempt(
            judged,
            CreateState::Ready,
            (Some(&h), T0)
        ));
        // another reader marked it already
        assert!(!still_same_attempt(
            judged,
            CreateState::Unfinished,
            (Some(&h), T0)
        ));
        // a newer attempt: other holder, or same holder fields but a new record
        let newer = Holder {
            pid: 4243,
            ..holder(BOOT)
        };
        assert!(!still_same_attempt(
            judged,
            CreateState::Creating,
            (Some(&newer), T0)
        ));
        assert!(!still_same_attempt(
            judged,
            CreateState::Creating,
            (Some(&h), T0 + 1)
        ));
        // the late-starting worker wrote its holder after the verdict
        assert!(!still_same_attempt(
            (None, T0),
            CreateState::Creating,
            (Some(&h), T0)
        ));
        assert!(still_same_attempt(
            (None, T0),
            CreateState::Creating,
            (None, T0)
        ));
    }

    // knives (d) (e): a late worker writes its holder only into its own, still unheld attempt
    #[test]
    fn a_late_worker_takes_only_its_own_unheld_attempt() {
        let h = holder(BOOT);
        // on time: still creating, nobody holds it, same attempt
        assert!(may_take_holder(T0, CreateState::Creating, (None, T0)));
        // too late: a reader already marked it unfinished after the start grace
        assert!(!may_take_holder(T0, CreateState::Unfinished, (None, T0)));
        assert!(!may_take_holder(T0, CreateState::Ready, (None, T0)));
        // another worker holds it
        assert!(!may_take_holder(T0, CreateState::Creating, (Some(&h), T0)));
        // a newer attempt replaced the record this worker was started for
        assert!(!may_take_holder(T0, CreateState::Creating, (None, T0 + 1)));
    }

    // N1: Unknown for too long tells us, once the alert time has passed
    #[test]
    fn unknown_for_too_long_tells_us() {
        let v = Verdict::Unknown;
        assert!(!unknown_needs_alert(v, T0, T0 + 5));
        assert!(!unknown_needs_alert(v, T0, T0 + UNKNOWN_ALERT_SECS));
        assert!(unknown_needs_alert(v, T0, T0 + UNKNOWN_ALERT_SECS + 1));
        assert!(unknown_needs_alert(v, T0, T0 + 11 * 60));
        // a clock behind the record is not "too long"
        assert!(!unknown_needs_alert(v, T0, T0 - 60));
    }

    // N1: only Unknown alerts; Interrupted has its own message, the rest are fine
    #[test]
    fn only_unknown_alerts() {
        let late = T0 + UNKNOWN_ALERT_SECS * 10;
        for v in [
            Verdict::Creating,
            Verdict::Interrupted,
            Verdict::Ready,
            Verdict::Unfinished,
        ] {
            assert!(!unknown_needs_alert(v, T0, late), "{v:?}");
        }
    }
}
