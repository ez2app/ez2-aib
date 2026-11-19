//! A page asks the owner's fingerprint (BOUNDARY-webonly-5d33a-1119 §4,
//! IFACE-webconsent-7ebca-1119 r1, CHECK-webconsent-5d33a-1119): aibd writes a ticket into the
//! aib-consent queue, the page polls its state, and right before acting aibd checks the ticket
//! once more against what it is about to do.
//!
//! 能看 stage: the shape, the window text, the category table and the record check; the queue is
//! not wired yet, so [`ask`] and [`state`] answer [`Asked::Unavailable`] /
//! [`State::Unavailable`] and [`approved_for`] is false. A caller that gets Unavailable refuses
//! with `owner_check_unavailable` (5d33a §0); nothing falls back to a page button.
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

/// Write a ticket into the aib-consent queue (not wired at this stage).
pub fn ask(ask: &Ask) -> Asked {
    if !page_category(ask.category) {
        return Asked::Unavailable("category_not_for_page");
    }
    Asked::Unavailable(UNAVAILABLE)
}

/// A ticket's state (not wired at this stage).
pub fn state(_ticket: &str) -> State {
    State::Unavailable(UNAVAILABLE)
}

/// Right before acting: the ticket is approved, not expired, not used, and is for this very
/// act. The queue is not wired at this stage, so no record is found and this is false.
pub fn approved_for(ticket: &str, ask: &Ask) -> bool {
    let _ = (ticket, ask);
    false
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
        assert_eq!(state("t1"), State::Unavailable(UNAVAILABLE));
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
}
