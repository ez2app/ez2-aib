//! S89-talk-1 seat credential (DECISION-council-seat-talk-519d0-1118 §我裁的 3): when aibd
//! starts a seat's provider it makes one random token for that seat and puts it in the
//! provider's environment as [`ENV`]. The provider (and the `aibd mcp` tools it starts) shows
//! it as `Authorization: Bearer …` to list the seats of its cabin and send to one of them;
//! nothing else ([`crate::guard`]). The token dies when the seat's session ends, and a new
//! start gives a new one.
//!
//! Not a defence against the same account: the environment of a process is readable by that
//! account. What it bounds is what a seat can ask aibd for, and who a message is from (the
//! sender is the seat the token was made for, never a field the seat writes).
//!
//! Only the SHA-256 of a token is kept; a token is never logged or journaled.

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard};

/// The env name the seat's provider gets its token in. Seat-owned: a request can never set
/// it ([`crate::allowlist::seat_owned`]).
pub const ENV: &str = "AIB_SEAT_TOKEN";

/// Who a seat token belongs to: the key the guard puts in the request (the token's digest,
/// so [`crate::guard::Caller`] stays `Copy` and no secret travels further).
pub type Key = [u8; 32];

#[derive(Default)]
pub struct SeatTokens {
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    by_key: HashMap<Key, String>,
    by_seat: HashMap<String, Key>,
}

fn key_of(token: &str) -> Key {
    Sha256::digest(token.as_bytes()).into()
}

impl SeatTokens {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A new token for `seat_id`; the seat's earlier token (if any) stops working.
    pub fn issue(&self, seat_id: &str) -> std::io::Result<String> {
        let t = crate::token::Token::generate()?;
        let token = t.expose().to_owned();
        let key = key_of(&token);
        let mut g = self.lock();
        if let Some(old) = g.by_seat.insert(seat_id.to_owned(), key) {
            g.by_key.remove(&old);
        }
        g.by_key.insert(key, seat_id.to_owned());
        Ok(token)
    }

    /// The key of `presented` when it is a live seat token.
    pub fn key(&self, presented: &str) -> Option<Key> {
        let key = key_of(presented);
        self.lock().by_key.contains_key(&key).then_some(key)
    }

    /// The seat a live key belongs to (`None` once the seat stopped).
    pub fn seat(&self, key: &Key) -> Option<String> {
        self.lock().by_key.get(key).cloned()
    }

    /// The seat's session ended: its token stops working.
    pub fn revoke(&self, seat_id: &str) {
        let mut g = self.lock();
        if let Some(k) = g.by_seat.remove(seat_id) {
            g.by_key.remove(&k);
        }
    }
}

static TOKENS: std::sync::LazyLock<SeatTokens> = std::sync::LazyLock::new(SeatTokens::default);

/// This aibd's seat tokens (one process, one table; gone on restart like the seats' sessions).
pub fn tokens() -> &'static SeatTokens {
    &TOKENS
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A token names its seat; a new start replaces it; a stop ends it.
    #[test]
    fn a_seat_token_names_its_seat_until_replaced_or_revoked() {
        let t = SeatTokens::default();
        let a = t.issue("seat_a").unwrap();
        let k = t.key(&a).expect("issued");
        assert_eq!(t.seat(&k).as_deref(), Some("seat_a"));
        assert!(t.key("not-a-token").is_none());
        let a2 = t.issue("seat_a").unwrap();
        assert!(t.key(&a).is_none(), "reason: old_token_kept");
        let k2 = t.key(&a2).unwrap();
        t.revoke("seat_a");
        assert!(t.key(&a2).is_none(), "reason: revoked_token_kept");
        assert!(t.seat(&k2).is_none());
    }
}
