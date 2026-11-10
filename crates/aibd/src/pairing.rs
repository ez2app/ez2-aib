//! Pairing and page credentials (ADR 0006 §7), manual-entry variant for W1.
//!
//! `aib web` (start-up token) asks for a pairing code, the person types it into the
//! page, the page redeems it with a form POST and gets a page credential back. The
//! credential starts **unconfirmed**: it may only read its own pairing status, revoke
//! itself and read health. It is unlocked only when the person has compared a 6-digit
//! short code shown on two channels (the terminal and the page) and `aib web` sends the
//! confirmation with the start-up token. A countdown running out never confirms.
//!
//! Boundary (stated, not hidden): an AI running under the same OS account can read the
//! start-up token file, so it can do everything `aib web` does, confirmation included.
//! Pairing keeps out other OS accounts and pages on other local ports; it does not stop a
//! same-account AI.
//!
//! Trial values (ADR 0006 leaves them to the PoC, PLAN-web-workbench §11): the code lives
//! [`CODE_TTL`]; a redeemed credential must be confirmed within [`CONFIRM_WINDOW`] or it is
//! revoked; a confirmed credential lives [`CREDENTIAL_TTL`]; at most [`MAX_FAILURES`]
//! failures per [`FAILURE_WINDOW`] per aibd, then every redeem / confirm answers
//! rate-limited.
//!
//! Off by default ([`ConfirmChannel::Off`]): the only confirmation built so far is
//! `aib web` asking y/N in the terminal, which an AI on the same account can answer (it
//! can read the token file and call confirm itself). ADR 0006 §7 calls relying on that a
//! risk acceptance that needs Andy's explicit consent, so it is opt-in
//! (`AIBD_PAIRING=terminal-unverified`) and never described as authentication. The
//! confirm call is shaped so a later channel can replace it: aibd itself showing a
//! Touch ID prompt with the short code and verifying the result, not trusting a client.
//!
//! Everything is in memory: an aibd restart drops every code and credential. Codes and
//! credentials are kept only as SHA-256 digests. Expiry is computed on each access from a
//! monotonic clock (no timer, ADR 0005), so a wall-clock jump never unlocks anything.

use sha2::{Digest, Sha256};
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use subtle::ConstantTimeEq;

/// How long a pairing code can be redeemed (trial value; a person reads and types it).
pub const CODE_TTL: Duration = Duration::from_secs(120);
/// How long a redeemed credential waits for the short-code confirmation (trial value).
pub const CONFIRM_WINDOW: Duration = Duration::from_secs(120);
/// Lifetime of a page credential, counted from redemption (trial value).
pub const CREDENTIAL_TTL: Duration = Duration::from_secs(12 * 3600);
/// Failure counting window for the rate limit.
pub const FAILURE_WINDOW: Duration = Duration::from_secs(60);
/// Failures (bad code, reuse, wrong short code) per window before everything is refused.
pub const MAX_FAILURES: usize = 10;
/// Pairing records kept at once (each is kept long enough to recognise a late re-send).
pub const MAX_PAIRS: usize = 256;
/// Live page credentials (awaiting or confirmed) at once. The credential lookup scans
/// all of them in constant time, so its cost grows with this number (a9136 11-10).
pub const MAX_CREDENTIALS: usize = 16;
/// Characters in a pairing code (Crockford base32, 5 bits each: 50 bits).
pub const CODE_LEN: usize = 10;

const CROCKFORD: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";
/// A record is kept until the credential it may have issued is surely gone, so a second
/// redemption at any point while a credential could live is still seen as reuse.
const KEEP: Duration = Duration::from_secs(120 + 120 + 12 * 3600);

type Hash = [u8; 32];

/// Monotonic clock; tests pass a fake one to move time.
pub type Clock = Arc<dyn Fn() -> Instant + Send + Sync>;

/// What a pairing (and the credential it issued) is now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PairState {
    /// Code issued, not redeemed yet.
    Issued,
    /// Redeemed; the credential waits for the short-code confirmation.
    AwaitingConfirmation,
    /// Confirmed; the credential has full access until it expires.
    Confirmed,
    /// Revoked (by reuse, wrong short code, cancel, revoke, or the confirmation window).
    Revoked,
    /// The code or the confirmed credential ran out of time.
    Expired,
}

impl PairState {
    pub fn as_str(self) -> &'static str {
        match self {
            PairState::Issued => "issued",
            PairState::AwaitingConfirmation => "awaiting_confirmation",
            PairState::Confirmed => "confirmed",
            PairState::Revoked => "revoked",
            PairState::Expired => "expired",
        }
    }
}

/// Which confirmation channel unlocks a credential.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ConfirmChannel {
    /// Pairing is off: no code is issued (the default).
    #[default]
    Off,
    /// `aib web` asks y/N in the terminal and calls confirm with the start-up token.
    /// UNVERIFIED as an independent channel: a same-account AI can do the same.
    TerminalUnverified,
}

impl ConfirmChannel {
    /// From `AIBD_PAIRING`: `terminal-unverified` turns pairing on; anything else is off.
    pub fn from_env_value(v: Option<&str>) -> ConfirmChannel {
        match v {
            Some("terminal-unverified") => ConfirmChannel::TerminalUnverified,
            _ => ConfirmChannel::Off,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ConfirmChannel::Off => "off",
            ConfirmChannel::TerminalUnverified => "terminal-unverified",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PairError {
    /// Pairing is off on this aibd (`AIBD_PAIRING`).
    Off,
    /// Unknown, expired, malformed or wrong code / short code (one answer for all).
    Failed,
    /// The code was redeemed before; every credential from it is now revoked.
    Reused,
    /// Too many failures in the window.
    RateLimited,
    /// Too many pairing records; try again later.
    Full,
    /// No pairing with that id.
    NotFound,
}

/// Returned to `aib web` once (the code is never stored in clear).
#[derive(Debug)]
pub struct NewCode {
    pub pair_id: String,
    /// `XXXXX-XXXXX`
    pub code: String,
    pub expires_in: u64,
}

/// Returned to the page once (the credential is never stored in clear).
pub struct Redeemed {
    pub pair_id: String,
    pub credential: String,
}

impl std::fmt::Debug for Redeemed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Redeemed")
            .field("pair_id", &self.pair_id)
            .field("credential", &"<redacted>")
            .finish()
    }
}

/// A view of one pairing, for `GET /pair/status` and `GET /pair/codes/{id}`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PairView {
    pub state: PairState,
    /// Seconds left in the current state (0 when it is final).
    pub expires_in: u64,
    /// Only while the credential awaits confirmation.
    pub short_code: Option<String>,
}

/// A page credential presented on a request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PageAccess {
    pub digest: Hash,
    pub confirmed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stored {
    Issued,
    Awaiting,
    Confirmed,
    Revoked,
}

struct Pair {
    code_hash: Hash,
    issued: Instant,
    stored: Stored,
    cred: Option<Hash>,
    redeemed: Option<Instant>,
    short_code: String,
}

impl Pair {
    fn state(&self, now: Instant) -> PairState {
        let r = self.redeemed.unwrap_or(self.issued);
        match self.stored {
            Stored::Issued if now >= self.issued + CODE_TTL => PairState::Expired,
            Stored::Issued => PairState::Issued,
            Stored::Awaiting if now >= r + CONFIRM_WINDOW => PairState::Revoked,
            Stored::Awaiting => PairState::AwaitingConfirmation,
            Stored::Confirmed if now >= r + CREDENTIAL_TTL => PairState::Expired,
            Stored::Confirmed => PairState::Confirmed,
            Stored::Revoked => PairState::Revoked,
        }
    }

    fn view(&self, now: Instant) -> PairView {
        let state = self.state(now);
        let r = self.redeemed.unwrap_or(self.issued);
        let left = |end: Instant| end.saturating_duration_since(now).as_secs();
        let expires_in = match state {
            PairState::Issued => left(self.issued + CODE_TTL),
            PairState::AwaitingConfirmation => left(r + CONFIRM_WINDOW),
            PairState::Confirmed => left(r + CREDENTIAL_TTL),
            PairState::Revoked | PairState::Expired => 0,
        };
        PairView {
            state,
            expires_in,
            short_code: (state == PairState::AwaitingConfirmation).then(|| self.short_code.clone()),
        }
    }
}

#[derive(Default)]
struct Inner {
    pairs: HashMap<String, Pair>,
    /// live credential digest -> pair id (a revoked or expired one is dropped here; its
    /// pair record stays, so a late re-send of the code is still seen as reuse)
    creds: HashMap<Hash, String>,
    failures: VecDeque<Instant>,
    /// Totals since start, for `/api/v1/metrics` (a lock-out by another account's wrong
    /// guesses shows up here).
    failures_total: u64,
    rate_limited_total: u64,
    /// Bumped on every revocation; the event stream (A1) closes a credential's streams
    /// when it sees the credential gone after a bump.
    revocations: u64,
}

pub struct Pairing {
    inner: Mutex<Inner>,
    clock: Clock,
    channel: ConfirmChannel,
}

impl std::fmt::Debug for Pairing {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Pairing(<state redacted>)")
    }
}

impl Default for Pairing {
    fn default() -> Self {
        Pairing::with_clock(Arc::new(Instant::now))
    }
}

fn sha(s: &[u8]) -> Hash {
    Sha256::digest(s).into()
}

fn random<const N: usize>() -> std::io::Result<[u8; N]> {
    let mut b = [0u8; N];
    getrandom::fill(&mut b).map_err(std::io::Error::other)?;
    Ok(b)
}

/// Upper-case, drop `-` and spaces, read `I`/`L` as `1` and `O` as `0`. `None` unless the
/// result is exactly [`CODE_LEN`] Crockford characters.
pub fn normalize_code(s: &str) -> Option<String> {
    if s.len() > 64 {
        return None;
    }
    let mut out = String::with_capacity(CODE_LEN);
    for c in s.chars() {
        let c = match c.to_ascii_uppercase() {
            '-' | ' ' => continue,
            'I' | 'L' => '1',
            'O' => '0',
            c => c,
        };
        if !c.is_ascii() || !CROCKFORD.contains(&(c as u8)) {
            return None;
        }
        out.push(c);
    }
    (out.len() == CODE_LEN).then_some(out)
}

fn new_code() -> std::io::Result<String> {
    let b: [u8; 7] = random()?; // 56 bits, 50 used
    let mut v = b.iter().fold(0u64, |acc, x| (acc << 8) | u64::from(*x));
    let mut s = String::with_capacity(CODE_LEN);
    for _ in 0..CODE_LEN {
        s.push(char::from(CROCKFORD[(v & 31) as usize]));
        v >>= 5;
    }
    Ok(s)
}

/// Six digits, uniform (rejection sampling).
fn new_short_code() -> std::io::Result<String> {
    const LIMIT: u32 = u32::MAX - (u32::MAX % 1_000_000);
    loop {
        let n = u32::from_le_bytes(random()?);
        if n < LIMIT {
            return Ok(format!("{:06}", n % 1_000_000));
        }
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

impl Pairing {
    pub fn with_clock(clock: Clock) -> Self {
        Pairing {
            inner: Mutex::new(Inner::default()),
            clock,
            channel: ConfirmChannel::Off,
        }
    }

    pub fn with_channel(mut self, channel: ConfirmChannel) -> Self {
        self.channel = channel;
        self
    }

    pub fn channel(&self) -> ConfirmChannel {
        self.channel
    }

    /// `{channel, failures_total, rate_limited_total, pairs}` for `/api/v1/metrics`.
    pub fn metrics(&self) -> serde_json::Value {
        let inner = self.lock();
        serde_json::json!({
            "channel": self.channel.as_str(),
            "failures_total": inner.failures_total,
            "rate_limited_total": inner.rate_limited_total,
            "pairs": inner.pairs.len(),
        })
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        // a panic while holding the lock cannot leave a half-issued credential usable:
        // every write is a single assignment, so keep serving
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn purge(inner: &mut Inner, now: Instant) {
        let gone: Vec<String> = inner
            .pairs
            .iter()
            .filter(|(_, p)| now >= p.issued + KEEP)
            .map(|(id, _)| id.clone())
            .collect();
        for id in gone {
            if let Some(p) = inner.pairs.remove(&id) {
                if let Some(c) = p.cred {
                    inner.creds.remove(&c);
                }
            }
        }
        let pairs = &inner.pairs;
        inner.creds.retain(|_, id| {
            pairs.get(id).is_some_and(|p| {
                matches!(
                    p.state(now),
                    PairState::AwaitingConfirmation | PairState::Confirmed
                )
            })
        });
        while inner
            .failures
            .front()
            .is_some_and(|t| now.saturating_duration_since(*t) >= FAILURE_WINDOW)
        {
            inner.failures.pop_front();
        }
    }

    fn limited(inner: &mut Inner) -> bool {
        let hit = inner.failures.len() >= MAX_FAILURES;
        if hit {
            inner.rate_limited_total += 1;
        }
        hit
    }

    fn fail(inner: &mut Inner, now: Instant, e: PairError) -> PairError {
        inner.failures.push_back(now);
        inner.failures_total += 1;
        e
    }

    fn revoke(inner: &mut Inner, id: &str) {
        if let Some(p) = inner.pairs.get_mut(id) {
            if p.stored != Stored::Revoked {
                p.stored = Stored::Revoked;
                inner.revocations += 1;
            }
            if let Some(c) = p.cred {
                inner.creds.remove(&c);
            }
        }
    }

    /// `POST /api/v1/pair/codes` (start-up token only).
    pub fn create_code(&self) -> Result<NewCode, PairError> {
        if self.channel == ConfirmChannel::Off {
            return Err(PairError::Off);
        }
        let now = (self.clock)();
        let mut inner = self.lock();
        Self::purge(&mut inner, now);
        if inner.pairs.len() >= MAX_PAIRS {
            return Err(PairError::Full);
        }
        let code = new_code().map_err(|_| PairError::Full)?;
        let pair_id = hex(&random::<8>().map_err(|_| PairError::Full)?);
        inner.pairs.insert(
            pair_id.clone(),
            Pair {
                code_hash: sha(code.as_bytes()),
                issued: now,
                stored: Stored::Issued,
                cred: None,
                redeemed: None,
                // made at redemption, bound to that credential (ADR 0006 §7; a9136 L1)
                short_code: String::new(),
            },
        );
        Ok(NewCode {
            pair_id,
            code: format!("{}-{}", &code[..5], &code[5..]),
            expires_in: CODE_TTL.as_secs(),
        })
    }

    /// `POST /api/v1/pair/redeem`. Atomic: of any number of redemptions of one code, at
    /// most one succeeds; any second one revokes the credential the first one got.
    pub fn redeem(&self, presented: &str) -> Result<Redeemed, PairError> {
        let now = (self.clock)();
        let mut inner = self.lock();
        Self::purge(&mut inner, now);
        if Self::limited(&mut inner) {
            return Err(PairError::RateLimited);
        }
        let Some(code) = normalize_code(presented) else {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        };
        let h = sha(code.as_bytes());
        let mut found: Option<String> = None;
        for (id, p) in &inner.pairs {
            if bool::from(p.code_hash.ct_eq(&h)) {
                found = Some(id.clone());
            }
        }
        let Some(id) = found else {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        };
        let Some(p) = inner.pairs.get(&id) else {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        };
        if p.stored != Stored::Issued {
            // second use, whatever the first one's state: revoke what it got
            Self::revoke(&mut inner, &id);
            return Err(Self::fail(&mut inner, now, PairError::Reused));
        }
        if p.state(now) == PairState::Expired {
            // an expired code can never be redeemed later either
            Self::revoke(&mut inner, &id);
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        }
        if inner.creds.len() >= MAX_CREDENTIALS {
            // not a failure of the code: it stays unredeemed and can be used later
            return Err(PairError::Full);
        }
        let raw: [u8; 32] = random().map_err(|_| PairError::Failed)?;
        use base64::Engine;
        let credential = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(raw);
        let digest = sha(credential.as_bytes());
        let short_code = new_short_code().map_err(|_| PairError::Failed)?;
        if let Some(p) = inner.pairs.get_mut(&id) {
            p.short_code = short_code;
            p.stored = Stored::Awaiting;
            p.cred = Some(digest);
            p.redeemed = Some(now);
        }
        inner.creds.insert(digest, id.clone());
        Ok(Redeemed {
            pair_id: id,
            credential,
        })
    }

    /// The guard's lookup: `Some` only for a credential that is awaiting confirmation or
    /// confirmed and in time.
    pub fn access(&self, presented: &str) -> Option<PageAccess> {
        let now = (self.clock)();
        let digest = sha(presented.as_bytes());
        let mut inner = self.lock();
        Self::purge(&mut inner, now);
        // constant time over every stored digest: no early exit, no hash-table probe
        let mut found: Option<&String> = None;
        for (k, id) in &inner.creds {
            if bool::from(k.ct_eq(&digest)) {
                found = Some(id);
            }
        }
        let p = inner.pairs.get(found?)?;
        match p.state(now) {
            PairState::AwaitingConfirmation => Some(PageAccess {
                digest,
                confirmed: false,
            }),
            PairState::Confirmed => Some(PageAccess {
                digest,
                confirmed: true,
            }),
            _ => None,
        }
    }

    /// `GET /api/v1/pair/status` (the page's own credential).
    pub fn status(&self, digest: &Hash) -> Option<PairView> {
        let now = (self.clock)();
        let mut inner = self.lock();
        Self::purge(&mut inner, now);
        let id = inner.creds.get(digest)?;
        inner.pairs.get(id).map(|p| p.view(now))
    }

    /// `POST /api/v1/pair/revoke` (the page's own credential).
    pub fn revoke_credential(&self, digest: &Hash) {
        let mut inner = self.lock();
        if let Some(id) = inner.creds.get(digest).cloned() {
            Self::revoke(&mut inner, &id);
        }
    }

    /// `GET /api/v1/pair/codes/{pair_id}` (start-up token only).
    pub fn view(&self, pair_id: &str) -> Option<PairView> {
        let now = (self.clock)();
        let inner = self.lock();
        inner.pairs.get(pair_id).map(|p| p.view(now))
    }

    /// `POST /api/v1/pair/codes/{pair_id}/confirm` (start-up token only), sent by
    /// `aib web` after the person answered that the page shows the same short code.
    /// A wrong short code revokes the credential (fail closed).
    pub fn confirm(&self, pair_id: &str, short_code: &str) -> Result<(), PairError> {
        let now = (self.clock)();
        let mut inner = self.lock();
        Self::purge(&mut inner, now);
        if Self::limited(&mut inner) {
            return Err(PairError::RateLimited);
        }
        let Some(p) = inner.pairs.get(pair_id) else {
            return Err(PairError::NotFound);
        };
        if p.state(now) != PairState::AwaitingConfirmation {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        }
        let ok: bool = p
            .short_code
            .as_bytes()
            .ct_eq(short_code.trim().as_bytes())
            .into();
        if !ok {
            Self::revoke(&mut inner, pair_id);
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        }
        if let Some(p) = inner.pairs.get_mut(pair_id) {
            p.stored = Stored::Confirmed;
        }
        Ok(())
    }

    /// `DELETE /api/v1/pair/codes/{pair_id}` (start-up token only): the person said no
    /// or closed the prompt. Returns false for an unknown id.
    pub fn cancel(&self, pair_id: &str) -> bool {
        let mut inner = self.lock();
        let known = inner.pairs.contains_key(pair_id);
        Self::revoke(&mut inner, pair_id);
        known
    }

    /// Revocation generation (see [`Inner::revocations`]).
    pub fn revocations(&self) -> u64 {
        self.lock().revocations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    /// A clock the test moves by hand.
    fn fake() -> (Clock, Arc<AtomicU64>) {
        let base = Instant::now();
        let offset = Arc::new(AtomicU64::new(0));
        let o = offset.clone();
        (
            Arc::new(move || base + Duration::from_millis(o.load(Ordering::SeqCst))),
            offset,
        )
    }

    fn on() -> Pairing {
        Pairing::default().with_channel(ConfirmChannel::TerminalUnverified)
    }

    fn on_with(clock: Clock) -> Pairing {
        Pairing::with_clock(clock).with_channel(ConfirmChannel::TerminalUnverified)
    }

    #[test]
    fn off_by_default_issues_no_code() {
        assert_eq!(
            Pairing::default().create_code().unwrap_err(),
            PairError::Off
        );
        assert_eq!(ConfirmChannel::from_env_value(None), ConfirmChannel::Off);
        assert_eq!(
            ConfirmChannel::from_env_value(Some("1")),
            ConfirmChannel::Off
        );
        assert_eq!(
            ConfirmChannel::from_env_value(Some("terminal-unverified")),
            ConfirmChannel::TerminalUnverified
        );
    }

    /// Timing cannot be measured reliably in a unit test, so the rule is checked on the
    /// source: the credential lookup scans with `ct_eq` and never probes the table.
    #[test]
    fn credential_lookup_is_constant_time() {
        let src = include_str!("pairing.rs");
        let body = src
            .split("pub fn access(")
            .nth(1)
            .and_then(|s| s.split("pub fn status(").next())
            .unwrap();
        assert!(body.contains("k.ct_eq(&digest)"));
        // no table probe, no `==` on the digest, no early exit from the scan
        assert!(!body.contains("creds.get("));
        assert!(!body.contains("=="));
        assert!(!body.contains("break") && !body.contains("return"));
    }

    fn advance(o: &AtomicU64, d: Duration) {
        o.fetch_add(d.as_millis() as u64, Ordering::SeqCst);
    }

    #[test]
    fn code_shape_and_normalising() {
        let p = on();
        let c = p.create_code().unwrap();
        assert_eq!(c.code.len(), CODE_LEN + 1);
        assert_eq!(&c.code[5..6], "-");
        assert_eq!(c.expires_in, CODE_TTL.as_secs());
        assert_eq!(normalize_code("abcde-fghjk").as_deref(), Some("ABCDEFGHJK"));
        assert_eq!(normalize_code("ilo00 11111").as_deref(), Some("1100011111"));
        for bad in ["", "ABCDEFGHJ", "ABCDEFGHJKM", "ABCDEFGHJU", "ÄBCDEFGHJK"] {
            assert_eq!(normalize_code(bad), None, "{bad}");
        }
        let s = new_short_code().unwrap();
        assert_eq!(s.len(), 6);
        assert!(s.bytes().all(|b| b.is_ascii_digit()));
    }

    #[test]
    fn the_short_code_exists_only_after_redemption() {
        let p = on();
        let c = p.create_code().unwrap();
        assert_eq!(p.view(&c.pair_id).unwrap().short_code, None);
        assert!(p.lock().pairs[&c.pair_id].short_code.is_empty());
        p.redeem(&c.code).unwrap();
        assert_eq!(p.view(&c.pair_id).unwrap().short_code.unwrap().len(), 6);
    }

    #[test]
    fn happy_path_awaits_then_confirms() {
        let p = on();
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code.to_lowercase()).unwrap();
        let a = p.access(&r.credential).unwrap();
        assert!(!a.confirmed);
        let v = p.view(&c.pair_id).unwrap();
        assert_eq!(v.state, PairState::AwaitingConfirmation);
        let short = v.short_code.clone().unwrap();
        assert_eq!(p.status(&a.digest).unwrap().short_code, Some(short.clone()));
        p.confirm(&c.pair_id, &short).unwrap();
        assert!(p.access(&r.credential).unwrap().confirmed);
        assert_eq!(p.view(&c.pair_id).unwrap().short_code, None);
    }

    #[test]
    fn second_redemption_is_409_and_revokes_the_first() {
        let p = on();
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let before = p.revocations();
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Reused);
        assert!(p.access(&r.credential).is_none());
        assert_eq!(p.revocations(), before + 1);
        // also after confirmation: a late re-send still revokes
        let c2 = p.create_code().unwrap();
        let r2 = p.redeem(&c2.code).unwrap();
        let s = p.view(&c2.pair_id).unwrap().short_code.unwrap();
        p.confirm(&c2.pair_id, &s).unwrap();
        assert_eq!(p.redeem(&c2.code).unwrap_err(), PairError::Reused);
        assert!(p.access(&r2.credential).is_none());
    }

    #[test]
    fn concurrent_redemptions_succeed_at_most_once() {
        let p = Arc::new(on());
        let c = p.create_code().unwrap();
        let hs: Vec<_> = (0..8)
            .map(|_| {
                let p = p.clone();
                let code = c.code.clone();
                std::thread::spawn(move || p.redeem(&code).is_ok())
            })
            .collect();
        let wins = hs
            .into_iter()
            .map(|h| h.join().unwrap())
            .filter(|x| *x)
            .count();
        assert_eq!(wins, 1);
        // and the one that won is revoked by the losers
        assert_eq!(p.view(&c.pair_id).unwrap().state, PairState::Revoked);
    }

    #[test]
    fn expired_code_never_redeems_even_racing_the_expiry() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let c = p.create_code().unwrap();
        advance(&o, CODE_TTL - Duration::from_millis(1));
        assert_eq!(p.view(&c.pair_id).unwrap().state, PairState::Issued);
        advance(&o, Duration::from_millis(1));
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Failed);
        assert_eq!(p.view(&c.pair_id).unwrap().state, PairState::Revoked);
        assert!(p.redeem(&c.code).is_err());
    }

    #[test]
    fn confirmation_window_running_out_revokes_never_confirms() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let s = p.view(&c.pair_id).unwrap().short_code.unwrap();
        advance(&o, CONFIRM_WINDOW);
        assert!(p.access(&r.credential).is_none());
        assert_eq!(p.view(&c.pair_id).unwrap().state, PairState::Revoked);
        assert_eq!(p.confirm(&c.pair_id, &s).unwrap_err(), PairError::Failed);
        assert!(p.access(&r.credential).is_none());
    }

    #[test]
    fn wrong_short_code_revokes() {
        let p = on();
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let s = p.view(&c.pair_id).unwrap().short_code.unwrap();
        let wrong = if s == "000000" { "000001" } else { "000000" };
        assert_eq!(p.confirm(&c.pair_id, wrong).unwrap_err(), PairError::Failed);
        assert_eq!(p.confirm(&c.pair_id, &s).unwrap_err(), PairError::Failed);
        assert!(p.access(&r.credential).is_none());
    }

    #[test]
    fn confirmed_credential_expires_after_its_lifetime() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let s = p.view(&c.pair_id).unwrap().short_code.unwrap();
        p.confirm(&c.pair_id, &s).unwrap();
        advance(&o, CREDENTIAL_TTL - Duration::from_secs(1));
        assert!(p.access(&r.credential).is_some());
        advance(&o, Duration::from_secs(1));
        assert!(p.access(&r.credential).is_none());
        // the record is still there, so a late re-send of the code is still reuse
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Reused);
    }

    #[test]
    fn wrong_guesses_rate_limit_but_leave_other_pairings_alone() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        for _ in 0..MAX_FAILURES {
            assert_eq!(p.redeem("00000-00000").unwrap_err(), PairError::Failed);
        }
        let c2 = p.create_code().unwrap();
        assert_eq!(p.redeem(&c2.code).unwrap_err(), PairError::RateLimited);
        // the unrelated pairing is untouched
        assert!(p.access(&r.credential).is_some());
        advance(&o, FAILURE_WINDOW);
        assert!(p.redeem(&c2.code).is_ok());
    }

    #[test]
    fn cancel_and_self_revoke() {
        let p = on();
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        assert!(p.cancel(&c.pair_id));
        assert!(p.access(&r.credential).is_none());
        assert!(!p.cancel("nope"));
        let c2 = p.create_code().unwrap();
        let r2 = p.redeem(&c2.code).unwrap();
        let d = p.access(&r2.credential).unwrap().digest;
        p.revoke_credential(&d);
        assert!(p.access(&r2.credential).is_none());
        assert!(p.status(&d).is_none());
        assert_eq!(p.view(&c2.pair_id).unwrap().state, PairState::Revoked);
    }

    #[test]
    fn only_digests_are_kept() {
        let p = on();
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let inner = p.lock();
        let norm = normalize_code(&c.code).unwrap();
        for (id, pr) in &inner.pairs {
            assert!(!id.contains(&norm));
            assert_ne!(pr.code_hash.as_slice(), norm.as_bytes());
        }
        let keys: Vec<_> = inner.creds.keys().copied().collect();
        assert_eq!(keys, vec![sha(r.credential.as_bytes())]);
        assert!(!format!("{r:?}").contains(&r.credential));
    }

    #[test]
    fn live_credentials_are_capped_and_freed_by_revoke() {
        let p = on();
        let mut last = None;
        for _ in 0..MAX_CREDENTIALS {
            let c = p.create_code().unwrap();
            last = Some(p.redeem(&c.code).unwrap());
        }
        let c = p.create_code().unwrap();
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Full);
        let d = p.access(&last.unwrap().credential).unwrap().digest;
        p.revoke_credential(&d);
        // the refused code was not used up
        assert!(p.redeem(&c.code).is_ok());
    }

    #[test]
    fn records_are_dropped_after_keep_and_capped() {
        let (clock, o) = fake();
        let p = on_with(clock);
        for _ in 0..MAX_PAIRS {
            p.create_code().unwrap();
        }
        assert_eq!(p.create_code().unwrap_err(), PairError::Full);
        advance(&o, KEEP);
        assert!(p.create_code().is_ok());
    }
}
