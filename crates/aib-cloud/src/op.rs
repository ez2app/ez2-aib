// SPDX-License-Identifier: Apache-2.0
//! What every writing command shares (4.4.1 §1.1 / §1.3 / §1.4): an operation id, a
//! journal that records the intent **before** anything is changed, the states an operation
//! moves through, what to do when a journal is found half way, whether an approval still
//! covers what is about to run, and when a host lock may be taken over.
//!
//! This module decides; it does not write. The host shell appends [`Entry`] lines to a
//! journal file, takes the lock file and asks these functions what the files mean.
//!
//! * An operation is one command against one target. Its journal is append-only: one JSON
//!   object per line, numbered from 1.
//! * `planned → approved → running → verified` is the good path. `running` may also end
//!   in `failed`; `failed` may go to `recovery`, which ends in `verified` (recovered and
//!   checked) or `failed` again. Nothing leaves `verified`.
//! * A journal that stops at `running` means the process died or the connection dropped
//!   **after** a change may have been made. The answer is never "run it again": verify
//!   first ([`Resume::VerifyFirst`]).
//! * An approval is bound to the plan it was given for: the same target and the same
//!   SHA-256 of the plan, unexpired. Anything else must be asked again.
//! * A lock is taken over only when its holder is **known to be gone**. A holder that is
//!   merely slow, or whose liveness cannot be established, keeps the lock.
//! * `plan_sha256` is made by the shell and must cover everything an approver was shown:
//!   the spec, the commands with their variables filled in, the **identity of the target
//!   host** and the version of this crate. A hash without the host would let an approval
//!   for `site:site2` count on a reinstalled machine with the same site code.
//! * The way back must be safe to take twice: `failed → recovery → failed → recovery` is
//!   a legal history, and a recovery can itself be interrupted.
//! * The shell syncs the `running` line to disk **before** it changes anything.
//! * Checking an approval ([`approval_covers`]) and writing the `approved` line happen
//!   under the same host lock, and a journal is kept at least until the approval it names
//!   has expired: together they are what makes a nonce single-use.
//! * [`parse_journal`] returning no entries and "cut short" means the operation never
//!   began: its first line did not reach the disk.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::{json, Value};

/// Why a journal, an entry or an id was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Not an operation id.
    Id(String),
    /// An entry that does not have the expected shape.
    Entry(String),
    /// Entries that do not form one operation's history.
    Journal(String),
}

impl core::fmt::Display for Error {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Error::Id(s) => write!(f, "operation id refused: {s:?}"),
            Error::Entry(s) => write!(f, "journal entry refused: {s}"),
            Error::Journal(s) => write!(f, "journal refused: {s}"),
        }
    }
}

impl core::error::Error for Error {}

/// `<YYYYMMDD>T<HHMMSS>Z-<8 lower hex>`: sortable by time, unique by the random half.
/// The caller makes it; this checks it, because the id becomes a file name.
pub fn valid_op_id(id: &str) -> bool {
    let b = id.as_bytes();
    b.len() == 25
        && b[8] == b'T'
        && b[15] == b'Z'
        && b[16] == b'-'
        && b[..8].iter().chain(&b[9..15]).all(u8::is_ascii_digit)
        && b[17..]
            .iter()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
}

/// Where an operation stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// The plan exists; nothing is approved or changed.
    Planned,
    /// A person (or a standing authorisation) approved this plan.
    Approved,
    /// Changes are being made.
    Running,
    /// Done and checked.
    Verified,
    /// Stopped with an error, or the check after it failed.
    Failed,
    /// The way back is being taken.
    Recovery,
}

impl State {
    /// The spelling used in journals.
    pub fn as_str(self) -> &'static str {
        match self {
            State::Planned => "planned",
            State::Approved => "approved",
            State::Running => "running",
            State::Verified => "verified",
            State::Failed => "failed",
            State::Recovery => "recovery",
        }
    }

    fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "planned" => State::Planned,
            "approved" => State::Approved,
            "running" => State::Running,
            "verified" => State::Verified,
            "failed" => State::Failed,
            "recovery" => State::Recovery,
            _ => return None,
        })
    }

    /// Whether `next` may follow `self` in a journal.
    pub fn may_become(self, next: State) -> bool {
        matches!(
            (self, next),
            (State::Planned, State::Approved)
                | (State::Approved, State::Running)
                | (State::Running, State::Verified | State::Failed)
                | (State::Failed, State::Recovery)
                | (State::Recovery, State::Verified | State::Failed)
        )
    }
}

/// One journal line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// Operation id.
    pub op: String,
    /// 1, 2, 3 … within the operation.
    pub seq: u32,
    /// Seconds since the epoch, as the host saw it.
    pub at: u64,
    /// The state entered.
    pub state: State,
    /// The command, e.g. `site.quarantine`.
    pub command: String,
    /// What it acts on, e.g. `site:site2`. One operation, one target.
    pub target: String,
    /// SHA-256 of the plan, 64 lower hex. The same in every entry of an operation.
    pub plan_sha256: String,
    /// A short code for why (`ok`, `verify_red`, `timeout`, …); never raw output.
    pub code: String,
    /// The nonce of the approval this operation runs under: empty in `planned`, set in
    /// `approved` and the same in every entry after it. It ties the journal to the
    /// approver's receipt and lets an interrupted operation resume under the approval it
    /// already used.
    pub approval: String,
}

fn valid_sha256(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn valid_token(s: &str, extra: &[u8]) -> bool {
    !s.is_empty()
        && s.len() <= 80
        && s.bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || extra.contains(&c))
}

impl Entry {
    /// Check the fields a shell fills in. Entries read back from a file go through
    /// [`Entry::from_json`], which calls this.
    pub fn validate(&self) -> Result<(), Error> {
        if !valid_op_id(&self.op) {
            return Err(Error::Id(self.op.clone()));
        }
        let bad = |what: &str| Err(Error::Entry(what.to_owned()));
        if self.seq == 0 {
            return bad("seq starts at 1");
        }
        if !valid_token(&self.command, b"._-") {
            return bad("command");
        }
        if !valid_token(&self.target, b"._-:") {
            return bad("target");
        }
        if !valid_sha256(&self.plan_sha256) {
            return bad("plan_sha256 is 64 lower hex");
        }
        if !valid_token(&self.code, b"_") {
            return bad("code");
        }
        if (self.state == State::Planned) != self.approval.is_empty() {
            return bad("approval is empty in planned and set afterwards");
        }
        if !self.approval.is_empty() && !valid_token(&self.approval, b"_-") {
            return bad("approval");
        }
        Ok(())
    }

    /// The line to append (without the newline).
    pub fn to_json(&self) -> Value {
        json!({
            "op": self.op, "seq": self.seq, "at": self.at, "state": self.state.as_str(),
            "command": self.command, "target": self.target,
            "plan_sha256": self.plan_sha256, "code": self.code, "approval": self.approval,
        })
    }

    /// Read one line back. Unknown keys are refused: a journal is evidence.
    pub fn from_json(v: &Value) -> Result<Self, Error> {
        let obj = v
            .as_object()
            .ok_or_else(|| Error::Entry("not an object".to_owned()))?;
        const KEYS: &[&str] = &[
            "op",
            "seq",
            "at",
            "state",
            "command",
            "target",
            "plan_sha256",
            "code",
            "approval",
        ];
        if obj.len() != KEYS.len() || !obj.keys().all(|k| KEYS.contains(&k.as_str())) {
            return Err(Error::Entry("keys".to_owned()));
        }
        let text = |k: &str| {
            obj.get(k)
                .and_then(Value::as_str)
                .map(ToOwned::to_owned)
                .ok_or_else(|| Error::Entry(k.to_owned()))
        };
        let number = |k: &str| {
            obj.get(k)
                .and_then(Value::as_u64)
                .ok_or_else(|| Error::Entry(k.to_owned()))
        };
        let entry = Entry {
            op: text("op")?,
            seq: u32::try_from(number("seq")?).map_err(|_| Error::Entry("seq".to_owned()))?,
            at: number("at")?,
            state: State::parse(&text("state")?).ok_or_else(|| Error::Entry("state".to_owned()))?,
            command: text("command")?,
            target: text("target")?,
            plan_sha256: text("plan_sha256")?,
            code: text("code")?,
            approval: text("approval")?,
        };
        entry.validate()?;
        Ok(entry)
    }
}

/// Read an operation's journal: every entry valid, numbered 1.. without gaps, all about
/// the same operation, command, target and plan, starting at `planned`, each state
/// allowed after the one before. Returns the last entry. Time going backwards is not an
/// error (clocks get corrected); the numbering is what orders a journal.
pub fn replay(entries: &[Entry]) -> Result<&Entry, Error> {
    let bad = |what: &str| Err(Error::Journal(what.to_owned()));
    let Some(first) = entries.first() else {
        return bad("empty");
    };
    if first.state != State::Planned {
        return bad("does not start at planned");
    }
    let mut prev: Option<&Entry> = None;
    for (i, e) in entries.iter().enumerate() {
        e.validate()?;
        if u32::try_from(i + 1).ok() != Some(e.seq) {
            return bad("seq is not 1, 2, 3 …");
        }
        if e.op != first.op
            || e.command != first.command
            || e.target != first.target
            || e.plan_sha256 != first.plan_sha256
        {
            return bad("entries of different operations or plans");
        }
        if let Some(p) = prev {
            if !p.state.may_become(e.state) {
                return bad("state does not follow");
            }
            // one operation runs under one approval, from `approved` to the end
            if p.state != State::Planned && p.approval != e.approval {
                return bad("approval changes");
            }
        }
        prev = Some(e);
    }
    Ok(prev.unwrap_or(first))
}

/// What to do with an operation found in a given state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Resume {
    /// Not approved yet: ask.
    NeedsApproval,
    /// Approved and nothing changed yet: check the approval still holds, then run.
    Run,
    /// It was running when the journal stops: something may have been changed. Verify
    /// before anything else; never run the steps again on the journal's word.
    VerifyFirst,
    /// It failed: take the way back, or leave it to a person.
    Recover,
    /// The way back was being taken when the journal stops: verify, then decide.
    VerifyRecovery,
    /// Done.
    Nothing,
}

/// See [`Resume`].
pub fn resume(state: State) -> Resume {
    match state {
        State::Planned => Resume::NeedsApproval,
        State::Approved => Resume::Run,
        State::Running => Resume::VerifyFirst,
        State::Failed => Resume::Recover,
        State::Recovery => Resume::VerifyRecovery,
        State::Verified => Resume::Nothing,
    }
}

/// An approval, as the approver's tool recorded it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Approval {
    /// The target it was given for.
    pub target: String,
    /// SHA-256 of the plan it was given for.
    pub plan_sha256: String,
    /// Seconds since the epoch after which it no longer counts.
    pub expires: u64,
    /// One-time value; the caller keeps the used ones.
    pub nonce: String,
}

/// Why an approval does not cover what is about to run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    /// Given for another target.
    Target,
    /// Given for another plan (the plan, a variable or a precondition changed since).
    Plan,
    /// Too old.
    Expired,
    /// Already used once.
    Replayed,
}

/// Whether `approval` covers operation `op` running the plan `plan_sha256` against
/// `target` at `now`. `used_by` names the operation that already spent a nonce, if any:
/// an approval is good for one operation, and stays good for **that** operation — one
/// that was interrupted after `approved` resumes under the approval its journal records,
/// while the same approval offered to another operation is a replay. The shell records
/// "this nonce belongs to this op" by writing the `approved` line, which carries both.
pub fn approval_covers(
    approval: &Approval,
    op: &str,
    target: &str,
    plan_sha256: &str,
    now: u64,
    used_by: impl Fn(&str) -> Option<String>,
) -> Result<(), Refusal> {
    if approval.target != target {
        Err(Refusal::Target)
    } else if !valid_sha256(plan_sha256) || approval.plan_sha256 != plan_sha256 {
        Err(Refusal::Plan)
    } else if now >= approval.expires {
        Err(Refusal::Expired)
    } else if used_by(&approval.nonce).is_some_and(|other| other != op) {
        Err(Refusal::Replayed)
    } else {
        Ok(())
    }
}

/// The kernel's boot id: a UUID, `8-4-4-4-12` lower hex.
pub fn valid_boot_id(id: &str) -> bool {
    let b = id.as_bytes();
    b.len() == 36
        && b.iter().enumerate().all(|(i, c)| {
            if matches!(i, 8 | 13 | 18 | 23) {
                *c == b'-'
            } else {
                c.is_ascii_digit() || (b'a'..=b'f').contains(c)
            }
        })
}

/// Operations on `target` that are not finished, given the last entry of every journal
/// on the host: `running`, `failed` and `recovery` (an `approved` one has changed nothing
/// yet). While there is one, a new operation on the same target is refused unless it is
/// that operation's own recovery — otherwise a quarantine that failed half way could be
/// "fixed" by an unrelated command that reopens the site.
pub fn unfinished<'a>(target: &str, last_entries: &[&'a Entry]) -> Vec<&'a str> {
    last_entries
        .iter()
        .filter(|e| {
            e.target == target
                && matches!(e.state, State::Running | State::Failed | State::Recovery)
        })
        .map(|e| e.op.as_str())
        .collect()
}

/// Read a journal file. Every line must be a valid entry, with one exception: a last line
/// that has **no newline after it** and does not parse is a write that was cut short; it
/// is dropped and reported (`true`). The shell appends a whole line and then syncs, so a
/// cut-short line was never acted on. Any other damaged line refuses the journal.
pub fn parse_journal(text: &str) -> Result<(Vec<Entry>, bool), Error> {
    let complete = text.is_empty() || text.ends_with('\n');
    let lines: Vec<&str> = text.lines().collect();
    let mut entries = Vec::with_capacity(lines.len());
    for (i, line) in lines.iter().enumerate() {
        let parsed = serde_json::from_str::<Value>(line)
            .map_err(|_| Error::Entry("not JSON".to_owned()))
            .and_then(|v| Entry::from_json(&v));
        match parsed {
            Ok(e) => entries.push(e),
            Err(_) if !complete && i + 1 == lines.len() => return Ok((entries, true)),
            Err(e) => return Err(e),
        }
    }
    Ok((entries, false))
}

/// Who holds the host lock, as written in the lock file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Holder {
    /// The operation.
    pub op: String,
    /// Process id of the holder.
    pub pid: u32,
    /// When that process started, in the kernel's own unit. Together with the pid it
    /// names one process: a pid alone is reused.
    pub start_time: u64,
    /// Boot id of the host when the lock was taken.
    pub boot_id: String,
}

/// What is known about the process a lock file names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Liveness {
    /// A process with that pid and that start time exists.
    Alive,
    /// No such process, or the pid now belongs to a process started at another time.
    Gone,
    /// Could not be established.
    Unknown,
}

/// What to do about the lock.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lock {
    /// Free: take it.
    Take,
    /// Held by a process that is gone, or left over from before a reboot: take it over,
    /// and treat the holder's operation as interrupted ([`resume`]).
    TakeOver,
    /// Held, or not provably free. Wait or give up; do not write.
    Busy,
}

/// Decide about the host lock. `boot_id` is the current boot. However long a holder has
/// been running is not an input: slow is not dead.
pub fn lock(holder: Option<&Holder>, boot_id: &str, liveness: Liveness) -> Lock {
    match holder {
        None => Lock::Take,
        // every process from before the reboot is gone — but only two well-formed ids
        // that differ say "another boot". An id that could not be read, or a damaged lock
        // file, differs from everything and proves nothing: fall through to liveness.
        Some(h) if valid_boot_id(&h.boot_id) && valid_boot_id(boot_id) && h.boot_id != boot_id => {
            Lock::TakeOver
        }
        Some(_) => match liveness {
            Liveness::Gone => Lock::TakeOver,
            Liveness::Alive | Liveness::Unknown => Lock::Busy,
        },
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::string::ToString;

    const OP: &str = "20261110T014500Z-0a1b2c3d";
    const SHA: &str = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

    fn entry(seq: u32, state: State) -> Entry {
        Entry {
            op: OP.to_owned(),
            seq,
            at: 1_800_000_000 + u64::from(seq),
            state,
            command: "site.quarantine".to_owned(),
            target: "site:site2".to_owned(),
            plan_sha256: SHA.to_owned(),
            code: "ok".to_owned(),
            approval: if state == State::Planned {
                String::new()
            } else {
                "n1".to_owned()
            },
        }
    }

    fn journal(states: &[State]) -> Vec<Entry> {
        states
            .iter()
            .zip(1..)
            .map(|(s, seq)| entry(seq, *s))
            .collect()
    }

    #[test]
    fn op_ids() {
        assert!(valid_op_id(OP));
        for bad in [
            "",
            "20261110T014500Z-0a1b2c3",
            "20261110T014500Z-0a1b2c3dd",
            "20261110T014500Z-0A1B2C3D",
            "20261110T014500Z_0a1b2c3d",
            "2026111xT014500Z-0a1b2c3d",
            "20261110 014500Z-0a1b2c3d",
            "../../../etc/passwd-0a1b2c3d",
        ] {
            assert!(!valid_op_id(bad), "{bad:?}");
        }
    }

    #[test]
    fn states_follow_one_another() {
        use State::{Approved, Failed, Planned, Recovery, Running, Verified};
        let all = [Planned, Approved, Running, Verified, Failed, Recovery];
        let allowed = [
            (Planned, Approved),
            (Approved, Running),
            (Running, Verified),
            (Running, Failed),
            (Failed, Recovery),
            (Recovery, Verified),
            (Recovery, Failed),
        ];
        for a in all {
            for b in all {
                assert_eq!(a.may_become(b), allowed.contains(&(a, b)), "{a:?} -> {b:?}");
            }
        }
        for s in all {
            assert_eq!(State::parse(s.as_str()), Some(s));
        }
        assert_eq!(State::parse("done"), None);
    }

    #[test]
    fn entry_round_trip_and_strict_reading() {
        let e = entry(1, State::Planned);
        assert_eq!(Entry::from_json(&e.to_json()).unwrap(), e);
        let mut v = e.to_json();
        v["extra"] = json!(1);
        assert!(Entry::from_json(&v).is_err());
        let mut v = e.to_json();
        v.as_object_mut().unwrap().remove("code");
        assert!(Entry::from_json(&v).is_err());
        for (key, value) in [
            ("op", json!("x")),
            ("seq", json!(0)),
            ("state", json!("done")),
            ("command", json!("Site Quarantine")),
            ("target", json!("site:../x")),
            ("plan_sha256", json!("abc")),
            ("code", json!("stderr: permission denied")),
        ] {
            let mut v = e.to_json();
            v[key] = value;
            assert!(Entry::from_json(&v).is_err(), "{key}");
        }
    }

    #[test]
    fn replay_good_paths() {
        use State::{Approved, Failed, Planned, Recovery, Running, Verified};
        let j = journal(&[Planned, Approved, Running, Verified]);
        assert_eq!(replay(&j).unwrap().state, Verified);
        let j = journal(&[Planned, Approved, Running, Failed, Recovery, Verified]);
        assert_eq!(replay(&j).unwrap().state, Verified);
        let j = journal(&[Planned, Approved, Running]);
        assert_eq!(replay(&j).unwrap().state, Running);
        let j = journal(&[Planned]);
        assert_eq!(replay(&j).unwrap().seq, 1);
    }

    #[test]
    fn replay_refuses_what_is_not_one_history() {
        use State::{Approved, Planned, Running, Verified};
        assert!(replay(&[]).is_err());
        // running without approval
        assert!(replay(&journal(&[Planned, Running])).is_err());
        // does not start at planned
        assert!(replay(&journal(&[Approved, Running])).is_err());
        // nothing follows verified
        assert!(replay(&journal(&[Planned, Approved, Running, Verified, Running])).is_err());
        let good = journal(&[Planned, Approved, Running]);
        // a gap, or a line removed
        let mut j = good.clone();
        j.remove(1);
        assert!(replay(&j).is_err());
        // another plan slipped in after approval
        let mut j = good.clone();
        j[2].plan_sha256 = SHA.replace('9', "8");
        assert!(replay(&j).is_err());
        // another target, another operation
        let mut j = good.clone();
        j[2].target = "site:site3".to_owned();
        assert!(replay(&j).is_err());
        let mut j = good.clone();
        j[1].op = "20261110T014501Z-0a1b2c3d".to_owned();
        assert!(replay(&j).is_err());
        // time going backwards is tolerated
        let mut j = good;
        j[2].at = 1;
        assert!(replay(&j).is_ok());
    }

    #[test]
    fn an_interrupted_run_is_verified_not_repeated() {
        assert_eq!(resume(State::Planned), Resume::NeedsApproval);
        assert_eq!(resume(State::Approved), Resume::Run);
        assert_eq!(resume(State::Running), Resume::VerifyFirst);
        assert_eq!(resume(State::Failed), Resume::Recover);
        assert_eq!(resume(State::Recovery), Resume::VerifyRecovery);
        assert_eq!(resume(State::Verified), Resume::Nothing);
    }

    #[test]
    fn an_approval_covers_one_plan_for_one_operation() {
        let a = Approval {
            target: "site:site2".to_owned(),
            plan_sha256: SHA.to_owned(),
            expires: 2000,
            nonce: "n1".to_owned(),
        };
        let t = "site:site2";
        let fresh = |_: &str| None;
        assert_eq!(approval_covers(&a, OP, t, SHA, 1000, fresh), Ok(()));
        assert_eq!(
            approval_covers(&a, OP, "site:site3", SHA, 1000, fresh),
            Err(Refusal::Target)
        );
        let other = SHA.replace('9', "8");
        assert_eq!(
            approval_covers(&a, OP, t, &other, 1000, fresh),
            Err(Refusal::Plan)
        );
        assert_eq!(
            approval_covers(&a, OP, t, "", 1000, fresh),
            Err(Refusal::Plan)
        );
        assert_eq!(
            approval_covers(&a, OP, t, SHA, 2000, fresh),
            Err(Refusal::Expired)
        );
        // spent by this operation (its journal says approved under n1, then it died):
        // resuming is not a replay
        let mine = |n: &str| (n == "n1").then(|| OP.to_owned());
        assert_eq!(approval_covers(&a, OP, t, SHA, 1000, mine), Ok(()));
        // the same approval offered to another operation is
        let another = "20261110T014501Z-0a1b2c3d";
        assert_eq!(
            approval_covers(&a, another, t, SHA, 1000, mine),
            Err(Refusal::Replayed)
        );
        // an approval whose own hash is not a hash covers nothing, even "equal" to itself
        let empty = Approval {
            plan_sha256: String::new(),
            ..a
        };
        assert_eq!(
            approval_covers(&empty, OP, t, "", 1000, fresh),
            Err(Refusal::Plan)
        );
    }

    #[test]
    fn slow_is_not_dead() {
        const BOOT_A: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";
        const BOOT_B: &str = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
        assert!(valid_boot_id(BOOT_A) && valid_boot_id(BOOT_B));
        for bad in [
            "",
            "boot-a",
            "0F8FAD5B-D9CB-469F-A165-70867728950E",
            &BOOT_A[..35],
        ] {
            assert!(!valid_boot_id(bad), "{bad:?}");
        }
        let h = Holder {
            op: OP.to_owned(),
            pid: 4242,
            start_time: 99,
            boot_id: BOOT_A.to_owned(),
        };
        assert_eq!(lock(None, BOOT_A, Liveness::Unknown), Lock::Take);
        assert_eq!(lock(Some(&h), BOOT_A, Liveness::Alive), Lock::Busy);
        assert_eq!(lock(Some(&h), BOOT_A, Liveness::Unknown), Lock::Busy);
        assert_eq!(lock(Some(&h), BOOT_A, Liveness::Gone), Lock::TakeOver);
        // after a reboot the holder cannot be alive, whatever a pid lookup says
        assert_eq!(lock(Some(&h), BOOT_B, Liveness::Alive), Lock::TakeOver);
        // a boot id that could not be read, or a damaged lock file, proves no reboot
        // (review of #623 segment 4, 1ced7 M1)
        assert_eq!(lock(Some(&h), "", Liveness::Alive), Lock::Busy);
        assert_eq!(lock(Some(&h), "", Liveness::Unknown), Lock::Busy);
        let damaged = Holder {
            boot_id: "garbage".to_owned(),
            ..h
        };
        assert_eq!(lock(Some(&damaged), BOOT_A, Liveness::Alive), Lock::Busy);
        assert_eq!(lock(Some(&damaged), BOOT_A, Liveness::Unknown), Lock::Busy);
        assert_eq!(lock(Some(&damaged), BOOT_A, Liveness::Gone), Lock::TakeOver);
    }

    /// Review of #623 segment 4 (ebb36 M1): the journal says which approval an operation
    /// runs under, and it does not change half way.
    #[test]
    fn the_journal_names_the_approval() {
        use State::{Approved, Planned, Running};
        let j = journal(&[Planned, Approved, Running]);
        assert_eq!((j[0].approval.as_str(), j[1].approval.as_str()), ("", "n1"));
        assert!(replay(&j).is_ok());
        let mut swapped = j.clone();
        swapped[2].approval = "n2".to_owned();
        assert!(replay(&swapped).is_err());
        let mut unnamed = j.clone();
        unnamed[1].approval = String::new();
        assert!(replay(&unnamed).is_err());
        let mut early = j;
        early[0].approval = "n1".to_owned();
        assert!(replay(&early).is_err());
    }

    /// ebb36 L1: an operation that did not finish blocks a new one on the same target.
    #[test]
    fn unfinished_operations_on_a_target() {
        use State::{Approved, Failed, Planned, Recovery, Running, Verified};
        let other_op = "20261110T020000Z-ffffffff";
        for (state, open) in [
            (Planned, false),
            (Approved, false),
            (Running, true),
            (Failed, true),
            (Recovery, true),
            (Verified, false),
        ] {
            let mut e = entry(1, state);
            e.op = other_op.to_owned();
            let want: Vec<&str> = if open {
                alloc::vec![other_op]
            } else {
                Vec::new()
            };
            assert_eq!(unfinished("site:site2", &[&e]), want, "{state:?}");
            assert!(unfinished("site:site3", &[&e]).is_empty());
        }
    }

    /// ebb36 L2: only a last line cut short is tolerated.
    #[test]
    fn a_journal_file_with_a_torn_last_line() {
        use State::{Approved, Planned, Running};
        let j = journal(&[Planned, Approved, Running]);
        let line = |e: &Entry| e.to_json().to_string();
        let whole = alloc::format!("{}\n{}\n{}\n", line(&j[0]), line(&j[1]), line(&j[2]));
        assert_eq!(parse_journal(&whole).unwrap(), (j.clone(), false));
        assert_eq!(parse_journal("").unwrap(), (Vec::new(), false));
        // the third line was being written when the host died
        let torn = alloc::format!("{}\n{}\n{}", line(&j[0]), line(&j[1]), &line(&j[2])[..40]);
        let (read, cut) = parse_journal(&torn).unwrap();
        assert!(cut);
        assert_eq!(read, j[..2]);
        assert_eq!(resume(replay(&read).unwrap().state), Resume::Run);
        // the same damage followed by a newline, or in the middle, is not a torn write
        assert!(parse_journal(&alloc::format!("{torn}\n")).is_err());
        let middle = alloc::format!("{}\n{{\"op\":\n{}\n", line(&j[0]), line(&j[2]));
        assert!(parse_journal(&middle).is_err());
        // a complete last line without a newline is simply read
        let no_newline = whole.trim_end().to_owned();
        assert_eq!(parse_journal(&no_newline).unwrap(), (j, false));
    }
}
