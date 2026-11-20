//! A page asks the owner's fingerprint (BOUNDARY-webonly-5d33a-1119 §4,
//! IFACE-webconsent-7ebca-1119 r1, CHECK-webconsent-5d33a-1119, a63 Andy 11-20): aibd opens a
//! ticket in its own memory, raises the fingerprint window with the key pairing already uses
//! (touch.rs, the Secure Enclave), the page polls the ticket's state, and right before acting
//! aibd checks the ticket once more against what it is about to do.
//!
//! Default off: only with [`SWITCH`]`=1`, `AIBD_PAIRING=touchid` and a key that checked out at
//! start; otherwise [`ask`] and [`state`] answer [`Asked::Unavailable`] /
//! [`State::Unavailable`] and [`approved_for`] is false. A caller that gets Unavailable refuses
//! with `owner_check_unavailable` (5d33a §0); nothing falls back to a page button. Only the
//! [`WIRED`] operations raise a window; the 1.x aib-consent queue is not used.
//!
//! - The category comes from the caller's fixed table, never from the page; SeatInput and
//!   Outbound are not page asks (5d33a 01:31).
//! - The window text is built from the ticket itself (no words from the page): the resource
//!   with [`aib_approve::text::is_bad_char`] characters removed, a path keeping its last 80.
//! - The parameters are kept only as their SHA-256; the check compares that, the operation and
//!   the resource, and a ticket is used once.

use aib_approve::category::Category;
use sha2::{Digest, Sha256};

/// The longest resource shown (characters; a path keeps its end, 5d33a L1).
pub const RESOURCE_MAX: usize = 80;

/// The refusal code for a caller when no fingerprint can be asked.
pub const UNAVAILABLE: &str = "owner_check_unavailable";

/// What the page wants done and the owner approves.
#[derive(Debug, Clone)]
pub struct Ask {
    /// From the caller's fixed table ([`page_category`]); never the page's word.
    pub category: Category,
    /// e.g. `worktree.open`, `seat.open_dgrs`, `decision.answer`.
    pub operation: &'static str,
    /// e.g. a repo path, a seat name, a card id (cleaned and cut for the window).
    pub resource: String,
    /// The exact parameters of the act; only their SHA-256 is kept.
    pub params: Vec<u8>,
    /// `page:<pair id>`: a record, not an identity.
    pub by: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Asked {
    Ticket(String),
    Unavailable(&'static str),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Pending,
    Approved { at: u64, digest: String },
    Refused(String),
    Expired,
    Unavailable(&'static str),
}

/// A ticket as aib-consent keeps it: what [`approved_for`] compares.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub ticket: String,
    pub category: Category,
    pub operation: String,
    pub resource: String,
    pub params_sha256: String,
    pub approved: bool,
    pub expires_at: u64,
    pub used: bool,
}

/// The categories a page may ask (5d33a CHECK-webconsent): Command (open a dgrs seat, register a
/// repo, go on preview), ProjectRuling (a decision's answer, a version fixed), PageBind (pairing
/// confirmed again), WindowGrant (the dgrs switch), DeleteData, AutoUpdate. A waiting-box ticket
/// keeps its own.
pub fn page_category(c: Category) -> bool {
    matches!(
        c,
        Category::Command
            | Category::ProjectRuling
            | Category::PageBind
            | Category::WindowGrant
            | Category::DeleteData
            | Category::AutoUpdate
    )
}

/// The parameters' SHA-256, lower-case hex.
pub fn params_sha256(params: &[u8]) -> String {
    Sha256::digest(params)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The resource as the window shows it: no invisible or layout characters, its last
/// [`RESOURCE_MAX`] characters.
pub fn clean_resource(s: &str) -> String {
    let kept: Vec<char> = s
        .chars()
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .collect();
    let from = kept.len().saturating_sub(RESOURCE_MAX);
    let tail: String = kept[from..].iter().collect();
    if from > 0 {
        format!("…{tail}")
    } else {
        tail
    }
}

/// The fingerprint window's words, all from the ticket.
pub fn window_text(ask: &Ask, ticket: &str) -> String {
    let p = params_sha256(&ask.params);
    format!(
        "AIB 網頁要求:{}|對象:{}|單號 {}|參數 {}",
        ask.operation,
        clean_resource(&ask.resource),
        ticket,
        &p[..12]
    )
}

/// The operations whose caller resends with the ticket and checks it with [`approved_for`]
/// (a63, Andy 11-20): registering a repo and opening a dgrs seat on one. Every other page ask
/// stays [`UNAVAILABLE`] until its caller uses the ticket, so no window is raised for nothing.
pub const WIRED: [&str; 3] = [
    crate::repos::OP_REGISTER,
    crate::repos::OP_OPEN,
    crate::seat_trust::OP,
];

/// How long a ticket lives: the owner answers and the page resends within it.
pub const TICKET_SECS: u64 = 120;

/// The switch: the owner's fingerprint for page asks, only with `AIBD_PAIRING=touchid` and a
/// checked key (default off: every ask stays [`UNAVAILABLE`]).
pub const SWITCH: &str = "AIBD_OWNER_TOUCH";

/// [`SWITCH`] is on only when it is exactly `1`.
pub fn switch_on(v: Option<String>) -> bool {
    v.as_deref() == Some("1")
}

static OWNER: std::sync::OnceLock<std::sync::Arc<Owner>> = std::sync::OnceLock::new();

/// Called once at start when [`SWITCH`] is `1` and the fingerprint key checked out (the first
/// call wins); the windows share pairing's gate.
pub fn set_presence(p: crate::touch::SharedPresence, gate: std::sync::Arc<dyn WindowGate>) {
    let _ = OWNER.set(std::sync::Arc::new(Owner::new(p, gate, unix_now)));
}

/// The one gate every fingerprint window of this aibd passes ([`crate::pairing::Pairing`]).
pub trait WindowGate: Send + Sync {
    fn open(&self) -> Result<(), &'static str>;
    fn closed(&self, yes: bool);
}

impl WindowGate for crate::pairing::Pairing {
    fn open(&self) -> Result<(), &'static str> {
        self.window_open()
    }
    fn closed(&self, yes: bool) {
        self.window_closed(yes)
    }
}

/// Closes the window in the gate even when the window's thread panics (5d33a N1).
struct Closing {
    gate: std::sync::Arc<dyn WindowGate>,
    yes: bool,
}

impl Drop for Closing {
    fn drop(&mut self) {
        self.gate.closed(self.yes);
    }
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Opens a ticket and raises the owner's fingerprint window for it (off the caller's thread);
/// the page polls [`state`] and resends with the ticket.
pub fn ask(ask: &Ask) -> Asked {
    if !page_category(ask.category) {
        return Asked::Unavailable("category_not_for_page");
    }
    if !WIRED.contains(&ask.operation) {
        return Asked::Unavailable(UNAVAILABLE);
    }
    match OWNER.get() {
        Some(o) => o.ask(ask, |f| {
            std::thread::spawn(f);
        }),
        None => Asked::Unavailable(UNAVAILABLE),
    }
}

/// A ticket's state for the page `by` (`page:<pair>`); another page's ticket reads as expired.
pub fn state(ticket: &str, by: &str) -> State {
    match OWNER.get() {
        Some(o) => o.state(ticket, by),
        None => State::Unavailable(UNAVAILABLE),
    }
}

/// The bytes the key signs for one ticket.
pub fn binding(ticket: &str, ask: &Ask) -> Vec<u8> {
    format!(
        "aibd/owner-ok/v1\n{ticket}\n{}\n{}\n{}\n{}\n",
        ask.operation,
        ask.resource,
        params_sha256(&ask.params),
        ask.by
    )
    .into_bytes()
}

struct Slot {
    rec: Record,
    by: String,
    state: State,
}

/// The tickets of this aibd, in memory only: a restart drops them all.
pub struct Owner {
    presence: crate::touch::SharedPresence,
    slots: std::sync::Mutex<std::collections::HashMap<String, Slot>>,
    gate: std::sync::Arc<dyn WindowGate>,
    clock: fn() -> u64,
}

impl Owner {
    pub fn new(
        presence: crate::touch::SharedPresence,
        gate: std::sync::Arc<dyn WindowGate>,
        clock: fn() -> u64,
    ) -> Owner {
        Owner {
            presence,
            slots: std::sync::Mutex::default(),
            gate,
            clock,
        }
    }

    fn slots(&self) -> std::sync::MutexGuard<'_, std::collections::HashMap<String, Slot>> {
        self.slots.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `spawn` runs the window (a thread in aibd, inline in tests).
    pub fn ask(
        self: &std::sync::Arc<Self>,
        ask: &Ask,
        spawn: impl FnOnce(Box<dyn FnOnce() + Send>),
    ) -> Asked {
        let now = (self.clock)();
        let Ok(ticket) = crate::sessions::new_id("ot_") else {
            return Asked::Unavailable(UNAVAILABLE);
        };
        if let Err(why) = self.gate.open() {
            return Asked::Unavailable(why);
        }
        {
            let mut s = self.slots();
            s.retain(|_, v| now < v.rec.expires_at + 600);
            s.insert(
                ticket.clone(),
                Slot {
                    rec: Record {
                        ticket: ticket.clone(),
                        category: ask.category,
                        operation: ask.operation.to_owned(),
                        resource: ask.resource.clone(),
                        params_sha256: params_sha256(&ask.params),
                        approved: false,
                        expires_at: now + TICKET_SECS,
                        used: false,
                    },
                    by: ask.by.clone(),
                    state: State::Pending,
                },
            );
        }
        let me = self.clone();
        // made before the window's thread, so a spawn that fails still closes the gate
        // (5d33a PREREAD-ownertouch r2 N1)
        let mut closing = Closing {
            gate: self.gate.clone(),
            yes: false,
        };
        let why = window_text(ask, &ticket);
        let msg = binding(&ticket, ask);
        let t = ticket.clone();
        spawn(Box::new(move || {
            // the window lives as long as the ticket: past that it closes by itself, and a
            // window nobody answered is not a no (0eed7 VERIFY-ownertouch-andy M1)
            let got =
                me.presence
                    .ask_until(&why, &msg, std::time::Duration::from_secs(TICKET_SECS));
            closing.yes = crate::touch::ends_quietly(&got);
            drop(closing);
            me.answered(&t, got);
        }));
        Asked::Ticket(ticket)
    }

    fn answered(&self, ticket: &str, got: Result<(), &'static str>) {
        let now = (self.clock)();
        let mut s = self.slots();
        let Some(slot) = s.get_mut(ticket) else {
            return;
        };
        if slot.state != State::Pending {
            return;
        }
        slot.state = match got {
            Ok(()) if now < slot.rec.expires_at => {
                slot.rec.approved = true;
                State::Approved {
                    at: now,
                    digest: slot.rec.params_sha256.clone(),
                }
            }
            Ok(()) => State::Expired,
            Err(crate::touch::TIMEOUT) => State::Expired,
            Err(e) => State::Refused(e.to_owned()),
        };
    }

    pub fn state(&self, ticket: &str, by: &str) -> State {
        let now = (self.clock)();
        match self.slots().get(ticket) {
            Some(v) if v.by == by && !v.rec.used && now < v.rec.expires_at => v.state.clone(),
            _ => State::Expired,
        }
    }

    /// Approved, not used, not expired, the same page and the same act: used up here.
    pub fn approved_for(&self, ticket: &str, ask: &Ask) -> bool {
        let now = (self.clock)();
        let mut s = self.slots();
        match s.get_mut(ticket) {
            Some(v) if v.by == ask.by && record_matches(&v.rec, ticket, ask, now) => {
                v.rec.used = true;
                true
            }
            _ => false,
        }
    }
}

/// A ticket word as aibd hands it out: 1-64 of `A-Z a-z 0-9 - _` (anything else is never a
/// ticket, and is not echoed).
pub fn ticket_ok(t: &str) -> bool {
    (1..=64).contains(&t.len())
        && t.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// The word the page reads for a [`State`] (GAP-repo-register G2, 043dd eeaf7378e4): an
/// approval's time and digest stay inside aibd.
pub fn state_word(s: &State) -> serde_json::Value {
    match s {
        State::Pending => serde_json::json!({"state": "pending"}),
        State::Approved { .. } => serde_json::json!({"state": "approved"}),
        State::Refused(_) => serde_json::json!({"state": "denied"}),
        State::Expired => serde_json::json!({"state": "expired"}),
        State::Unavailable(why) => serde_json::json!({"state": "unavailable", "reason": why}),
    }
}

/// GET /api/v1/owner/tickets/{id}: the built-in page's own question only (a seat or a script
/// gets 403 `page_only`); a malformed ticket is 400 `ticket`. Reading changes nothing.
pub async fn ticket_state(
    axum::Extension(c): axum::Extension<crate::guard::Caller>,
    axum::extract::Path(id): axum::extract::Path<String>,
) -> axum::response::Response {
    use axum::http::StatusCode;
    use axum::response::IntoResponse;
    if !matches!(c, crate::guard::Caller::Page(_)) {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(serde_json::json!({"code": "page_only",
                "message": "a ticket's state is the page's to read"})),
        )
            .into_response();
    }
    if !ticket_ok(&id) {
        return (
            StatusCode::BAD_REQUEST,
            axum::Json(serde_json::json!({"code": "ticket",
                "message": "not a ticket aibd hands out"})),
        )
            .into_response();
    }
    let crate::guard::Caller::Page(page) = &c else {
        return StatusCode::FORBIDDEN.into_response();
    };
    let by: String = page.digest[..8]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    axum::Json(state_word(&state(&id, &format!("page:{by}")))).into_response()
}

/// Right before acting: the ticket is approved, not expired, not used, from the same page and
/// for this very act; a yes is used up. Without the switch nothing is approved.
pub fn approved_for(ticket: &str, ask: &Ask) -> bool {
    OWNER.get().is_some_and(|o| o.approved_for(ticket, ask))
}

/// [`approved_for`]'s comparison on a record already read.
pub fn record_matches(rec: &Record, ticket: &str, ask: &Ask, now: u64) -> bool {
    rec.approved
        && !rec.used
        && now < rec.expires_at
        && rec.ticket == ticket
        && rec.category == ask.category
        && page_category(rec.category)
        && rec.operation == ask.operation
        && rec.resource == ask.resource
        && rec.params_sha256 == params_sha256(&ask.params)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a() -> Ask {
        Ask {
            category: Category::Command,
            operation: "worktree.open",
            resource: "/Users/x/repo".into(),
            params: b"{\"repo\":\"/Users/x/repo\"}".to_vec(),
            by: "page:0123abcd".into(),
        }
    }

    fn rec(ask: &Ask) -> Record {
        Record {
            ticket: "t1".into(),
            category: ask.category,
            operation: ask.operation.into(),
            resource: ask.resource.clone(),
            params_sha256: params_sha256(&ask.params),
            approved: true,
            expires_at: 200,
            used: false,
        }
    }

    #[test]
    fn a_page_asks_only_its_six_categories_and_nothing_is_wired_yet() {
        for c in aib_approve::category::ALL {
            let mut x = a();
            x.category = c;
            let want = if matches!(c, Category::SeatInput | Category::Outbound) || !page_category(c)
            {
                "category_not_for_page"
            } else {
                UNAVAILABLE
            };
            assert_eq!(ask(&x), Asked::Unavailable(want), "reason: category {c:?}");
        }
        assert!(
            !page_category(Category::SeatInput),
            "reason: seat_input_asked"
        );
        assert!(!page_category(Category::Outbound), "reason: outbound_asked");
        assert_eq!(
            state("t1", "page:0123abcd"),
            State::Unavailable(UNAVAILABLE)
        );
        // G2: what the page reads; an approval's digest is not shown
        for (st, want) in [
            (State::Pending, "pending"),
            (
                State::Approved {
                    at: 1,
                    digest: "d".into(),
                },
                "approved",
            ),
            (State::Refused("no".into()), "denied"),
            (State::Expired, "expired"),
            (State::Unavailable(UNAVAILABLE), "unavailable"),
        ] {
            let v = state_word(&st);
            assert_eq!(v["state"], want, "reason: word {st:?}");
            assert!(
                v.get("digest").is_none() && v.get("at").is_none(),
                "reason: leaked {v}"
            );
        }
        assert!(ticket_ok("t-1_A"));
        for bad in ["", "a/b", "..", "t 1", &"x".repeat(65)] {
            assert!(!ticket_ok(bad), "reason: ticket_shape {bad}");
        }
        assert!(!approved_for("t1", &a()), "reason: stub_approves");
    }

    #[test]
    fn the_window_shows_the_cleaned_end_of_the_resource_and_the_params_hash() {
        let mut x = a();
        x.resource = format!("{}\u{202e}/tail-end", "d/".repeat(60));
        let r = clean_resource(&x.resource);
        assert!(!r.contains('\u{202e}'), "reason: bad_char_shown");
        assert!(r.ends_with("/tail-end"), "reason: path_head_kept {r}");
        assert_eq!(r.chars().count(), RESOURCE_MAX + 1, "reason: not_cut {r}");
        assert_eq!(clean_resource("短"), "短");
        let w = window_text(&x, "t9");
        assert!(w.starts_with("AIB 網頁要求:worktree.open|對象:…"), "{w}");
        assert!(w.contains("|單號 t9|參數 "), "{w}");
        assert!(w.ends_with(&params_sha256(&x.params)[..12]), "{w}");
    }

    #[test]
    fn a_record_approves_only_this_act_once_before_it_expires() {
        let x = a();
        assert!(record_matches(&rec(&x), "t1", &x, 100));
        type Break = (&'static str, fn(&mut Record));
        let cases: [Break; 8] = [
            ("not_approved", |r| r.approved = false),
            ("used", |r| r.used = true),
            ("expired", |r| r.expires_at = 100),
            ("other_ticket", |r| r.ticket = "t2".into()),
            ("other_category", |r| r.category = Category::DeleteData),
            ("other_operation", |r| r.operation = "seat.open_dgrs".into()),
            ("other_resource", |r| r.resource = "/Users/x/other".into()),
            ("other_params", |r| r.params_sha256 = params_sha256(b"x")),
        ];
        for (why, f) in cases {
            let mut r = rec(&x);
            f(&mut r);
            assert!(!record_matches(&r, "t1", &x, 100), "reason: {why}");
        }
        let mut s = x.clone();
        s.category = Category::SeatInput;
        assert!(
            !record_matches(&rec(&s), "t1", &s, 100),
            "reason: seat_input_record"
        );
    }

    struct Says(Result<(), &'static str>);
    impl crate::touch::Presence for Says {
        fn ask(&self, _reason: &str, msg: &[u8]) -> Result<(), &'static str> {
            assert!(
                msg.starts_with(b"aibd/owner-ok/v1\n"),
                "precondition: binding"
            );
            self.0
        }
    }

    static NOW: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1000);
    fn now() -> u64 {
        NOW.load(std::sync::atomic::Ordering::SeqCst)
    }
    fn at(t: u64) {
        NOW.store(t, std::sync::atomic::Ordering::SeqCst);
    }
    fn reg() -> Ask {
        let mut x = a();
        x.operation = crate::repos::OP_REGISTER;
        x
    }
    fn gate() -> std::sync::Arc<dyn WindowGate> {
        std::sync::Arc::new(crate::pairing::Pairing::default())
    }
    fn owner(says: Result<(), &'static str>) -> std::sync::Arc<Owner> {
        std::sync::Arc::new(Owner::new(std::sync::Arc::new(Says(says)), gate(), now))
    }
    fn inline(f: Box<dyn FnOnce() + Send>) {
        f()
    }
    fn ticket(a: Asked) -> String {
        match a {
            Asked::Ticket(t) => t,
            other => panic!("reason: no_ticket {other:?}"),
        }
    }

    #[test]
    fn the_owners_yes_approves_one_act_for_one_page_once() {
        at(1000);
        assert!(switch_on(Some("1".into())));
        for off in [
            None,
            Some("0".into()),
            Some("true".into()),
            Some(" 1".into()),
        ] {
            assert!(!switch_on(off.clone()), "reason: switch_on {off:?}");
        }
        assert!(
            WIRED.contains(&crate::repos::OP_REGISTER) && WIRED.contains(&crate::repos::OP_OPEN)
        );
        // the window is pending until the owner answers
        let o = owner(Ok(()));
        let held = std::sync::Mutex::new(None::<Box<dyn FnOnce() + Send>>);
        let t = ticket(o.ask(&reg(), |f| *held.lock().unwrap() = Some(f)));
        assert!(ticket_ok(&t), "reason: ticket_shape {t}");
        assert_eq!(o.state(&t, "page:0123abcd"), State::Pending);
        assert!(!o.approved_for(&t, &reg()), "reason: pending_approved");
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_busy"),
            "reason: two_windows"
        );
        let run = held.lock().unwrap().take().unwrap();
        run();
        assert!(matches!(
            o.state(&t, "page:0123abcd"),
            State::Approved { .. }
        ));
        // another page reads nothing and cannot use it
        assert_eq!(
            o.state(&t, "page:ffffffff"),
            State::Expired,
            "reason: other_page_reads"
        );
        let mut other = reg();
        other.by = "page:ffffffff".into();
        assert!(!o.approved_for(&t, &other), "reason: other_page_uses");
        // other params or another act: no
        let mut p = reg();
        p.params = b"{}".to_vec();
        assert!(!o.approved_for(&t, &p), "reason: other_params");
        let mut op = reg();
        op.operation = crate::repos::OP_OPEN;
        assert!(!o.approved_for(&t, &op), "reason: other_operation");
        // this act, once
        assert!(o.approved_for(&t, &reg()), "reason: yes_refused");
        assert!(!o.approved_for(&t, &reg()), "reason: used_twice");
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Expired,
            "reason: used_still_shown"
        );
        assert!(!o.approved_for("ot_nope", &reg()), "reason: unknown_ticket");

        // a yes that comes after the ticket ran out approves nothing
        at(5000);
        let o = owner(Ok(()));
        let held = std::sync::Mutex::new(None::<Box<dyn FnOnce() + Send>>);
        let t = ticket(o.ask(&reg(), |f| *held.lock().unwrap() = Some(f)));
        at(5000 + TICKET_SECS);
        held.lock().unwrap().take().unwrap()();
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Expired,
            "reason: late_yes"
        );
        assert!(!o.approved_for(&t, &reg()), "reason: late_yes_used");
        // an approved ticket runs out too
        at(6000);
        let t = ticket(o.ask(&reg(), inline));
        at(6000 + TICKET_SECS);
        assert!(!o.approved_for(&t, &reg()), "reason: expired_used");
    }

    #[test]
    fn a_no_cools_the_window_and_an_hour_holds_six() {
        // the owner windows pass pairing's own gate (5d33a L1): a no cools both kinds
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Err("presence_cancelled"))),
            pairing.clone(),
            || 10_000,
        ));
        let t = ticket(o.ask(&reg(), inline));
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Refused("presence_cancelled".into())
        );
        assert!(!o.approved_for(&t, &reg()), "reason: no_approves");
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_cooling"),
            "reason: no_cool"
        );
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: pairing_not_cooled"
        );
        // the count is over the whole aibd: pairing's own windows and the owner's together
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Ok(()))),
            pairing.clone(),
            || 20_000,
        ));
        assert_eq!(pairing.window_open(), Ok(()));
        pairing.window_closed(true);
        for i in 1..crate::pairing::RECONFIRM_SHORT_MAX {
            assert!(
                matches!(o.ask(&reg(), inline), Asked::Ticket(_)),
                "reason: window {i}"
            );
        }
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_limited"),
            "reason: limit_apart"
        );
        // a window whose thread never starts still closes in the gate (5d33a r2 N1)
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Ok(()))),
            pairing.clone(),
            || 40_000,
        ));
        let _ = ticket(o.ask(&reg(), drop));
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: unspawned_left_busy"
        );
        // a window whose thread panics still closes in the gate (5d33a N1)
        struct Boom;
        impl crate::touch::Presence for Boom {
            fn ask(&self, _r: &str, _m: &[u8]) -> Result<(), &'static str> {
                panic!("precondition: the window panics")
            }
        }
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Boom),
            pairing.clone(),
            || 30_000,
        ));
        let t = ticket(o.ask(&reg(), |f| {
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
        }));
        assert_eq!(o.state(&t, "page:0123abcd"), State::Pending);
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: panic_left_busy"
        );
        // the window's words and the signed bytes carry this ticket and these params
        let b = String::from_utf8(binding("ot_1", &reg())).unwrap();
        assert!(b.contains("\not_1\nrepo.register\n"), "{b}");
        assert!(b.contains(&params_sha256(&reg().params)), "{b}");
        assert!(b.ends_with("\npage:0123abcd\n"), "{b}");
    }

    /// 0eed7 VERIFY-ownertouch-andy M1 (N-webonly-c45): the window lives as long as the ticket
    /// and no longer; one nobody answered is expired, not refused, starts no cool-down and
    /// leaves the gate free for the next ask at once.
    #[test]
    fn a_window_nobody_answers_closes_with_the_ticket_and_is_not_a_no() {
        struct Limit(std::sync::Mutex<Option<std::time::Duration>>);
        impl crate::touch::Presence for Limit {
            fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
                Ok(())
            }
            fn ask_until(
                &self,
                _reason: &str,
                _msg: &[u8],
                limit: std::time::Duration,
            ) -> Result<(), &'static str> {
                *self.0.lock().unwrap() = Some(limit);
                Err(crate::touch::TIMEOUT)
            }
        }
        let p = std::sync::Arc::new(Limit(std::sync::Mutex::new(None)));
        let o = std::sync::Arc::new(Owner::new(p.clone(), gate(), || 30_000));
        let a = reg();
        let t = ticket(o.ask(&a, inline));
        assert_eq!(
            *p.0.lock().unwrap(),
            Some(std::time::Duration::from_secs(TICKET_SECS)),
            "reason: window_without_the_tickets_limit"
        );
        assert_eq!(
            o.state(&t, &a.by),
            State::Expired,
            "reason: timeout_read_as_a_no"
        );
        assert!(!o.approved_for(&t, &a), "reason: timeout_approved");
        // not busy, not cooling: the next window opens at once
        assert!(
            matches!(o.ask(&a, inline), Asked::Ticket(_)),
            "reason: gate_held_or_cooled_after_a_timeout"
        );
        // a no still cools (the difference a timeout must not blur)
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Err("cancel"))),
            gate(),
            || 30_000,
        ));
        let _ = ticket(o.ask(&a, inline));
        assert_eq!(
            o.ask(&a, inline),
            Asked::Unavailable("pair_touch_cooling"),
            "reason: a_no_did_not_cool"
        );
    }

    /// 5d33a PREREAD-touchclose L1: the ticket's end is the one clock; a yes that comes back
    /// after it approves nothing.
    #[test]
    fn a_yes_after_the_tickets_end_approves_nothing() {
        static LATE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(50_000);
        struct Slow;
        impl crate::touch::Presence for Slow {
            fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
                // the owner's finger lands one second after the ticket ended
                LATE.fetch_add(TICKET_SECS + 1, std::sync::atomic::Ordering::SeqCst);
                Ok(())
            }
        }
        let o = std::sync::Arc::new(Owner::new(std::sync::Arc::new(Slow), gate(), || {
            LATE.load(std::sync::atomic::Ordering::SeqCst)
        }));
        let a = reg();
        let t = ticket(o.ask(&a, inline));
        assert!(!o.approved_for(&t, &a), "reason: late_yes_approved");
        assert_eq!(
            o.state(&t, &a.by),
            State::Expired,
            "reason: late_yes_not_expired"
        );
    }
}
