//! 指紋批次簽, the pure part (DESIGN-ownerbatch-impl-08de5-1123 r0, f163c REVIEW r0 0/0/3/1;
//! what to do: DESIGN-owner-request-2d297-1122 r4 §2b–§2d). Nothing here is wired yet: which
//! operations may share one window, the list's hash, the window's numbered lines, how many fit,
//! and the duplicates a page sent twice. The collector, the window and the replay come next.
//!
//! - One window, one group (r4 §2c): 搬家 (register a repo, open a seat with it, trust, move
//!   messages), 送回倉, 推分支 / 開 PR. Anything else is not batched in this version.
//! - seat_tick / drive_switch keep the key's signature of their own ticket, which a batch's
//!   child ticket has not: refused `signed_alone` until B4 (f163c r0: only stricter).
//! - The hash, the window's numbers and the page's list are in one order (f163c N1).

use crate::owner_ok::{self, Ask};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// The page act that opens a batch.
pub const KIND: &str = "owner_batch";
/// At most this many in one window.
pub const MAX_ITEMS: usize = 8;
/// The characters the system window shows of the whole list.
pub const WINDOW_CHARS: usize = 900;
/// One item's line longer than this is never batched (r4 §2c: it goes alone).
pub const LINE_CHARS: usize = 160;

/// Which operations share a window (r4 §2c's first groups).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Group {
    /// 搬家: a repo registered, a seat opened with it, trusted, its messages moved.
    Move,
    /// 送回倉 (several seats).
    SendBack,
    /// 推分支 / 開 PR (several seats).
    Push,
}

impl Group {
    pub fn code(self) -> &'static str {
        match self {
            Self::Move => "move",
            Self::SendBack => "send_back",
            Self::Push => "push",
        }
    }
}

/// The operations whose approval is kept as the key's signature of their own ticket.
pub const SIGNED: [&str; 3] = [
    crate::seat_tick::OP_SET,
    crate::seat_tick::OP_RESUME,
    crate::drive_switch::OP,
];

/// The group of an operation, or why it is not batched (`signed_alone` / `not_batchable`).
pub fn group_of(op: &str) -> Result<Group, &'static str> {
    if SIGNED.contains(&op) {
        return Err("signed_alone");
    }
    match op {
        crate::repos::OP_REGISTER
        | crate::repos::OP_OPEN
        | crate::seat_trust::OP
        | crate::seat_move::OP => Ok(Group::Move),
        crate::send_back::OP_SEND_BACK => Ok(Group::SendBack),
        crate::repo_push::OP | crate::pr_open::OP => Ok(Group::Push),
        _ => Err("not_batchable"),
    }
}

/// Why a list is not one batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BatchError {
    Empty,
    /// More than fit in one window: at most `max`.
    TooBig {
        max: usize,
    },
    /// Item `i` cannot share this window: `reason`.
    Mixed {
        i: usize,
        reason: &'static str,
    },
}

impl BatchError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Empty => "batch_empty",
            Self::TooBig { .. } => "batch_too_big",
            Self::Mixed { .. } => "batch_mixed",
        }
    }
}

/// The window's lines, numbered from 1 in the list's order: the plain words and the object.
pub fn window_lines(asks: &[Ask]) -> Vec<String> {
    asks.iter()
        .enumerate()
        .map(|(i, a)| {
            format!(
                "{}. {}({})",
                i + 1,
                owner_ok::top_words(a),
                owner_ok::object_shown(&a.resource)
            )
        })
        .collect()
}

/// How many of `lines`, from the first, fit one window.
pub fn fitting(lines: &[String]) -> usize {
    let mut used = 0;
    let mut n = 0;
    for l in lines.iter().take(MAX_ITEMS) {
        used += l.chars().count() + 1;
        if used > WINDOW_CHARS {
            break;
        }
        n += 1;
    }
    n
}

/// The list as one batch: its group, or the first reason it is not.
pub fn check(asks: &[Ask]) -> Result<Group, BatchError> {
    let first = asks.first().ok_or(BatchError::Empty)?;
    let group = group_of(first.operation).map_err(|reason| BatchError::Mixed { i: 0, reason })?;
    for (i, a) in asks.iter().enumerate().skip(1) {
        match group_of(a.operation) {
            Err(reason) => return Err(BatchError::Mixed { i, reason }),
            Ok(g) if g != group => return Err(BatchError::Mixed { i, reason: "group" }),
            Ok(_) => {}
        }
    }
    let lines = window_lines(asks);
    if let Some(i) = lines.iter().position(|l| l.chars().count() > LINE_CHARS) {
        return Err(BatchError::Mixed {
            i,
            reason: "too_long",
        });
    }
    let max = fitting(&lines);
    if asks.len() > max {
        return Err(BatchError::TooBig { max });
    }
    Ok(group)
}

/// The list's hash (r4 §2c): every item's operation, resource and params hash in order, and
/// how many there are. The order is in it: the same items in another order are another list.
pub fn batch_sha(asks: &[Ask]) -> String {
    let mut h = Sha256::new();
    h.update(format!("aibd/owner-batch/v1\n{}\n", asks.len()));
    for (i, a) in asks.iter().enumerate() {
        h.update(format!(
            "{i}\n{}\n{}\n{}\n",
            a.operation,
            a.resource,
            owner_ok::params_sha256(&a.params)
        ));
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// The bytes the key signs for one batch ticket.
pub fn binding(ticket: &str, batch_sha: &str, by: &str) -> Vec<u8> {
    format!("aibd/owner-batch/v1\n{ticket}\n{batch_sha}\n{by}\n").into_bytes()
}

/// An item's identity for de-duplication: its act type and body without `idem_key` /
/// `ticket` (two sends of one act are one item).
pub fn item_sha(kind: &str, body: &Value) -> String {
    let mut b = body.clone();
    if let Some(o) = b.as_object_mut() {
        o.remove("idem_key");
        o.remove("ticket");
    }
    owner_ok::params_sha256(format!("{kind}\n{b}").as_bytes())
}

/// The page's items with the later copies of one item dropped (r4 N2): the indexes kept, in
/// order, and those merged into an earlier one.
pub fn dedup(items: &[(String, Value)]) -> (Vec<usize>, Vec<usize>) {
    let mut seen = std::collections::BTreeSet::new();
    let (mut kept, mut merged) = (Vec::new(), Vec::new());
    for (i, (kind, body)) in items.iter().enumerate() {
        if seen.insert(item_sha(kind, body)) {
            kept.push(i);
        } else {
            merged.push(i);
        }
    }
    (kept, merged)
}

/// What one item's collection gave: exactly one fingerprint ask, or why it cannot be batched
/// (none = it needs no fingerprint; more than one = not one approval).
pub fn one(asks: Vec<Ask>) -> Result<Ask, &'static str> {
    let mut it = asks.into_iter();
    match (it.next(), it.next()) {
        (Some(a), None) => Ok(a),
        (None, _) => Err("no_touch"),
        (Some(_), Some(_)) => Err("many_asks"),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use aib_approve::category::Category;
    use serde_json::json;

    fn ask(op: &'static str, resource: &str, params: Value) -> Ask {
        Ask {
            category: Category::Command,
            operation: op,
            resource: resource.to_owned(),
            params: serde_json::to_vec(&params).unwrap(),
            by: "page:p1".into(),
        }
    }

    fn push(n: usize) -> Vec<Ask> {
        (0..n)
            .map(|i| {
                ask(
                    crate::repo_push::OP,
                    &format!("ez2-aib · web/s{i}"),
                    json!({"repo": "ez2-aib", "branch": format!("web/s{i}")}),
                )
            })
            .collect()
    }

    /// B1-1: one group per window; a signed one and an unlisted one are refused by index.
    #[test]
    fn one_window_holds_one_group_and_no_signed_act() {
        assert_eq!(check(&push(2)), Ok(Group::Push));
        let mut mixed = push(2);
        mixed.push(ask(crate::send_back::OP_SEND_BACK, "s", json!({})));
        assert_eq!(
            check(&mixed),
            Err(BatchError::Mixed {
                i: 2,
                reason: "group"
            })
        );
        let mut signed = push(1);
        signed.push(ask(crate::seat_tick::OP_SET, "s", json!({})));
        assert_eq!(
            check(&signed),
            Err(BatchError::Mixed {
                i: 1,
                reason: "signed_alone"
            }),
            "reason: signed_batched"
        );
        assert_eq!(group_of(crate::drive_switch::OP), Err("signed_alone"));
        assert_eq!(group_of(crate::gh_token::OP), Err("not_batchable"));
        assert_eq!(group_of(crate::repos::OP_REGISTER), Ok(Group::Move));
        assert_eq!(check(&[]), Err(BatchError::Empty));
    }

    /// B1-2: the list's hash holds the order and the count; the window numbers in that order.
    #[test]
    fn the_hash_and_the_window_keep_the_order() {
        let a = push(3);
        let mut b = a.clone();
        b.swap(0, 2);
        assert_ne!(batch_sha(&a), batch_sha(&b), "reason: order_not_in_hash");
        assert_ne!(
            batch_sha(&a[..2]),
            batch_sha(&a),
            "reason: count_not_in_hash"
        );
        let mut c = a.clone();
        c[1].params = b"{}".to_vec();
        assert_ne!(batch_sha(&a), batch_sha(&c), "reason: params_not_in_hash");
        let lines = window_lines(&b);
        assert!(
            lines[0].starts_with("1. ") && lines[0].contains("web/s2"),
            "{lines:?}"
        );
        assert!(
            lines[2].starts_with("3. ") && lines[2].contains("web/s0"),
            "{lines:?}"
        );
        assert_eq!(lines.len(), 3, "reason: an_item_not_in_the_window");
    }

    /// B1-3: more than a window holds is refused with how many fit; a line too long goes alone.
    #[test]
    fn what_does_not_fit_one_window_is_refused() {
        assert_eq!(
            check(&push(MAX_ITEMS + 1)),
            Err(BatchError::TooBig { max: MAX_ITEMS })
        );
        let long = ask(
            crate::repos::OP_REGISTER,
            &"r".repeat(80),
            json!({"name": "n".repeat(100)}),
        );
        let mut v = vec![ask(crate::repos::OP_REGISTER, "x", json!({"name": "x"}))];
        v.push(long);
        assert_eq!(
            check(&v),
            Err(BatchError::Mixed {
                i: 1,
                reason: "too_long"
            })
        );
        let wide: Vec<String> = (0..6).map(|_| "字".repeat(200)).collect();
        assert_eq!(fitting(&wide), 4, "reason: window_overfilled");
    }

    /// B1-4: the same act sent twice (another idem_key) is one item; the first one stays.
    #[test]
    fn the_same_act_twice_is_one_item() {
        let items = vec![
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "1"}),
            ),
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "c", "idem_key": "2"}),
            ),
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "3"}),
            ),
            (
                "pr_open".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "4"}),
            ),
        ];
        assert_eq!(
            dedup(&items),
            (vec![0, 1, 3], vec![2]),
            "reason: duplicate_kept"
        );
    }

    /// B1-5: an item joins a batch only when collecting it asked exactly one fingerprint.
    #[test]
    fn an_item_is_batched_only_with_one_ask() {
        assert_eq!(one(vec![]).map(|_| ()), Err("no_touch"));
        assert_eq!(one(push(2)).map(|_| ()), Err("many_asks"));
        assert_eq!(one(push(1)).unwrap().resource, "ez2-aib · web/s0");
        let b = binding("ot_1", &batch_sha(&push(1)), "page:p1");
        assert!(String::from_utf8(b)
            .unwrap()
            .starts_with("aibd/owner-batch/v1\not_1\n"));
    }
}
