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
//! Weak pairing (a20, 11-17/18): the terminal channel is read from `AIBD_PAIRING` on every
//! platform, and its credentials say `method: terminal_y`. When Windows Hello / Touch ID lands, the aibd
//! update that brings it restarts aibd (dropping every credential, below) and must no longer
//! read `terminal-unverified`, so no weak credential outlives it. **Except the carry-over
//! below**: a credential confirmed with `terminal_y` is carried across an update like any
//! other, so the Hello update's carry-in must refuse it (the carry file records how it was
//! confirmed; 043dd 1a562172 / 8b7d7415, 5d33a PREREAD-batch1 M1, card T39) — until then a weak
//! page survives one update and still reaches safe seats only.
//!
//! Everything is in memory: an aibd restart drops every code and credential, except the one
//! carry-over across an update ([`crate::pair_carry`], S89-pair-carry): a confirmed credential's
//! digest is written at a clean stop when the executable on disk changed, read and deleted at
//! the next start, and lives there [`CARRY_USE_BY`] unless the page uses it. Codes and
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
/// 519d0 11-18 20:36 (Andy hit it in the preview: a double submit revoked his good credential):
/// the same code sent again with the same page nonce this soon after it was redeemed is the
/// same page clicking twice, answered "already" without a credential, a revoke or a failure.
pub const RESUBMIT_WINDOW: Duration = Duration::from_secs(30);
/// SPEC §19f (Andy 11-18 20:5x「配對碼不對或已過期 輸入配對碼 要倒數」): `aibd ctl web` swaps an
/// expired code for a new one at most this many times while it waits.
pub const MAX_ROTATIONS: u8 = 5;
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
/// A preview pass lasts this long at most (the tab opens right after the click).
pub const PASS_TTL: Duration = Duration::from_secs(120);
/// Passes a preview keeps waiting at once.
pub const MAX_PASSES: usize = 8;
/// A credential carried over an update must be used this soon after the new aibd starts, or it
/// is gone (DECISION-council-pair-persist §1: 10 minutes).
pub const CARRY_USE_BY: Duration = Duration::from_secs(600);
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
    /// aibd has the owner sign with a fingerprint key ([`crate::touch`]); neither the y nor
    /// the box on the page confirms (DESIGN-pair-touchid r3, 5d33a CHECK-pair-touchid).
    TouchId,
}

impl ConfirmChannel {
    /// From `AIBD_PAIRING`: `terminal-unverified` turns the weak pairing on, on every platform
    /// (a20; 519d0 r3b 11-18 02:18: macOS keeps it, marked weak, until Touch ID (T39) lands, so
    /// AIB 2.0 can be paired on a Mac at all); anything else is off.
    pub fn from_env_value(v: Option<&str>) -> ConfirmChannel {
        match v {
            Some("terminal-unverified") => ConfirmChannel::TerminalUnverified,
            Some("touchid") => ConfirmChannel::TouchId,
            _ => ConfirmChannel::Off,
        }
    }

    /// What this aibd pairs with (Andy 11-26 03:1x, PLAN-win-customer-today-c2f0b-1126): with
    /// `AIBD_PAIRING` unset, a Windows aibd pairs through Windows Hello, so a customer starts it
    /// with no `set`; a value given still decides (`off`, `0`, anything unknown: off). Elsewhere
    /// unset stays off.
    pub fn from_env_here(v: Option<&str>, windows: bool) -> ConfirmChannel {
        match v {
            None if windows => ConfirmChannel::TouchId,
            _ => ConfirmChannel::from_env_value(v),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ConfirmChannel::Off => "off",
            ConfirmChannel::TerminalUnverified => "terminal-unverified",
            ConfirmChannel::TouchId => "touchid",
        }
    }

    /// How a credential on this channel was confirmed, for the record and the page (a20):
    /// `terminal_y` = someone answered y in a terminal of this account, **not** the person
    /// proven present (a same-account AI can answer too).
    pub fn method(self) -> Option<&'static str> {
        match self {
            ConfirmChannel::Off => None,
            ConfirmChannel::TerminalUnverified => Some("terminal_y"),
            // Touch ID proves the person (7b81e c984a2c19): not the weak terminal_y marking;
            // the page reads the channel's own `touch` flag (integration on package 4)
            ConfirmChannel::TouchId => None,
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
    /// The same page sent the same code again ([`RESUBMIT_WINDOW`], same nonce): nothing new
    /// is issued and nothing is revoked; the page keeps the credential it already has.
    Already,
    /// SPEC §19f: the code was right but its time is up (said apart from a wrong code).
    Expired,
}

/// Returned to `aib web` once (the code is never stored in clear).
pub struct NewCode {
    pub pair_id: String,
    /// `XXXXX-XXXXX`
    pub code: String,
    pub expires_in: u64,
    /// SPEC §19f: when it runs out, Unix milliseconds (the page counts down to it).
    pub expires_at_ms: u64,
}

fn unix_ms_in(d: Duration) -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |n| {
            u64::try_from((n + d).as_millis()).unwrap_or(u64::MAX)
        })
}

impl std::fmt::Debug for NewCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NewCode")
            .field("pair_id", &self.pair_id)
            .field("code", &"<redacted>")
            .field("expires_in", &self.expires_in)
            .finish()
    }
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
    /// The credential went to a user folder page (it asked with to=user): the y/N prompt says
    /// so (S107-userhtml option 2).
    pub user_page: bool,
    /// Carried over an update (S89-pair-carry); the page and `aibd ctl pair` can tell.
    pub carried: bool,
}

/// A page credential presented on a request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PageAccess {
    pub digest: Hash,
    pub confirmed: bool,
    /// Redeemed while a user folder page served `/` (S107-userhtml, 519d0 03:5x §3): kept on
    /// the credential, so deleting the page afterwards does not lift what it may do.
    pub user_page: bool,
    /// Carried over an update (S89-pair-carry): it types to safe seats only, and nothing a
    /// person must answer in the moment (a dgrs seat's input, an approval) takes it
    /// (DECISION-council-pair-persist §2).
    pub carried: bool,
}

/// A confirmed credential as written for the carry-over: its digest, never the credential.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CarriedCred {
    pub digest: Hash,
    pub user_page: bool,
    /// Seconds of its lifetime left when it was written.
    pub left: u64,
}

/// A carried credential's clock: it ends with its own lifetime, and earlier if the page does
/// not use it within [`CARRY_USE_BY`].
#[derive(Debug, Clone, Copy)]
struct Carry {
    ends: Instant,
    use_by: Instant,
    used: bool,
    /// The owner pressed the finger for it in this run ([`Pairing::reconfirm`], BOUNDARY r3
    /// §3): it then works as one paired in this run, to the same end, and is still never
    /// carried again.
    reconfirmed: bool,
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
    /// See [`PageAccess::user_page`].
    user_page: bool,
    /// Carried over an update; never carried again.
    carry: Option<Carry>,
    /// Made by `aibd ctl web` asking for the terminal too (SPEC §19b-3): once confirmed, the
    /// terminal takes its own credential once ([`Pairing::take_terminal`]).
    terminal: bool,
    /// The terminal's pairing, once made: revoking this one revokes it too.
    linked: Option<String>,
    /// The digest of the nonce the redeeming page sent (kept in its memory only, never in the
    /// URL): the same page sending the same code again is not a reuse ([`RESUBMIT_WINDOW`]).
    page_nonce: Option<Hash>,
    /// SPEC §19f: how many expired codes came before this one in one `ctl web` wait.
    rotation: u8,
    /// SPEC §19f: the code that replaced this expired one, `(pair id, code)`, in memory only;
    /// handed to whoever shows this code ([`Pairing::next_code`]), never written anywhere.
    next: Option<(String, String)>,
}

impl Pair {
    fn state(&self, now: Instant) -> PairState {
        let r = self.redeemed.unwrap_or(self.issued);
        if let (Some(c), Stored::Confirmed) = (self.carry, self.stored) {
            return if now >= c.ends || (!c.used && now >= c.use_by) {
                PairState::Expired
            } else {
                PairState::Confirmed
            };
        }
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
            PairState::Confirmed => match self.carry {
                Some(c) if !c.used => left(c.ends.min(c.use_by)),
                Some(c) => left(c.ends),
                None => left(r + CREDENTIAL_TTL),
            },
            PairState::Revoked | PairState::Expired => 0,
        };
        PairView {
            state,
            expires_in,
            short_code: (state == PairState::AwaitingConfirmation).then(|| self.short_code.clone()),
            user_page: self.user_page,
            carried: self.carry.is_some(),
        }
    }
}

/// What a pairing carried over an update may be re-confirmed for (BOUNDARY r3 §3): the
/// action key the page sends and the words the fingerprint window shows. A key not here is
/// refused; nothing from the request reaches the window but a checked seat code.
pub const RECONFIRM_ACTS: &[(&str, &str)] = &[
    ("seat_open", "開席"),
    ("seat_restart", "重開席"),
    ("type_dgrs", "對 dgrs 席送字"),
    ("assign", "改指派"),
    ("slot_answer", "回答事件卡"),
    ("node_write", "改進度樹"),
];
/// 5d33a CHECK-pair-touchid §6-2: per carried pairing, at most this many windows in
/// [`RECONFIRM_SHORT`] and [`RECONFIRM_LONG_MAX`] in an hour.
pub const RECONFIRM_SHORT_MAX: usize = 3;
pub const RECONFIRM_SHORT: Duration = Duration::from_secs(600);
pub const RECONFIRM_LONG_MAX: usize = 6;
const RECONFIRM_LONG: Duration = Duration::from_secs(3600);

/// The wait before `asks` (when windows were shown, oldest first) holds fewer than
/// [`RECONFIRM_SHORT_MAX`] in the last [`RECONFIRM_SHORT`] and fewer than [`RECONFIRM_LONG_MAX`]
/// in the last [`RECONFIRM_LONG`]: the window that has to drop out of each count, the later one.
fn limit_wait(asks: &VecDeque<Instant>, now: Instant) -> Option<Duration> {
    let long: Vec<Instant> = asks
        .iter()
        .copied()
        .filter(|t| now < *t + RECONFIRM_LONG)
        .collect();
    let short: Vec<Instant> = long
        .iter()
        .copied()
        .filter(|t| now < *t + RECONFIRM_SHORT)
        .collect();
    let mut ends = Vec::new();
    if short.len() >= RECONFIRM_SHORT_MAX {
        ends.push(short[short.len() - RECONFIRM_SHORT_MAX] + RECONFIRM_SHORT);
    }
    if long.len() >= RECONFIRM_LONG_MAX {
        ends.push(long[long.len() - RECONFIRM_LONG_MAX] + RECONFIRM_LONG);
    }
    ends.into_iter().max().map(|end| end - now)
}
/// After a window ended in a no, none for this long (5d33a REVIEW-touchid-8ec571dc M1).
pub const TOUCH_COOL: Duration = Duration::from_secs(30);
/// How long a fingerprint window of pairing stays open with no answer (the same as an owner
/// ticket's life, [`crate::owner_ok::TICKET_SECS`]): then it closes and the one-window gate
/// is free again.
pub const TOUCH_WINDOW: Duration = Duration::from_secs(crate::owner_ok::TICKET_SECS);

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
    /// Wrong / late confirmations; a bucket of its own (043dd A4 M1).
    confirm_failures_total: u64,
    /// Bumped on every revocation; the event stream (A1) closes a credential's streams
    /// when it sees the credential gone after a bump.
    revocations: u64,
    /// pair id -> when its re-confirm windows were shown (the last hour; 5d33a §6-2).
    reconfirm_asks: HashMap<String, VecDeque<Instant>>,
    /// A fingerprint window is open now ([`Pairing::touch_gate`]).
    touch_busy: bool,
    /// No window before this (a no was answered; [`TOUCH_COOL`]).
    touch_cool_until: Option<Instant>,
    /// When a redeem's windows were shown (the last hour, the whole aibd).
    touch_asks: VecDeque<Instant>,
    /// Preview passes the parent aibd handed this preview down its stdin (digest, until):
    /// one each, for the page its paired page opened ([`Pairing::admit_pass`]).
    passes: VecDeque<(Hash, Instant)>,
}

pub struct Pairing {
    inner: Mutex<Inner>,
    clock: Clock,
    channel: ConfirmChannel,
    journal: std::sync::OnceLock<Journal>,
    /// The owner check for [`ConfirmChannel::TouchId`]; unset ⇒ every such pairing is refused.
    presence: std::sync::OnceLock<crate::touch::SharedPresence>,
}

/// Where a pairing's outcome is written (aibd's event journal; set once at start). SPEC §19d:
/// the tab the command was typed in may close, so the outcome is kept here too.
pub type Journal = Arc<dyn Fn(&'static str, serde_json::Value) + Send + Sync>;

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
            journal: std::sync::OnceLock::new(),
            presence: std::sync::OnceLock::new(),
        }
    }

    pub fn with_channel(mut self, channel: ConfirmChannel) -> Self {
        self.channel = channel;
        self
    }

    pub fn channel(&self) -> ConfirmChannel {
        self.channel
    }

    /// Sets the owner check for [`ConfirmChannel::TouchId`] (the first call wins).
    pub fn set_presence(&self, p: crate::touch::SharedPresence) {
        let _ = self.presence.set(p);
    }

    /// [`ConfirmChannel::TouchId`]: ask the owner for the pairing `pair_id` that awaits
    /// confirmation, with the window [`crate::touch::reason`] composes from aibd's own values;
    /// a yes confirms it, anything else (no key, cancel, a bad signature, too late) revokes it.
    /// Blocking (the fingerprint window): call it off the async threads.
    pub fn ask_touch(&self, pair_id: &str, who: &crate::touch::Who) -> Result<(), &'static str> {
        let Some(presence) = self.presence.get() else {
            self.cancel(pair_id);
            return Err("pair_key_missing");
        };
        let got = {
            let mut inner = self.lock();
            let now = (self.clock)();
            Self::purge(&mut inner, now);
            let found = match inner.pairs.get(pair_id) {
                Some(p) if p.state(now) == PairState::AwaitingConfirmation => {
                    (p.short_code.clone(), p.user_page, p.page_nonce)
                }
                _ => return Err("pair_touch_not_awaiting"),
            };
            // a redeem's window counts against the whole aibd (5d33a M1)
            Self::touch_gate(&mut inner, now, true).map(|()| found)
        };
        let (short, user_page, nonce) = match got {
            Ok(v) => v,
            Err(e) => {
                self.cancel(pair_id);
                return Err(e);
            }
        };
        let nonce_hex = nonce.map(|n| hex(&n)).unwrap_or_default();
        let msg = crate::touch::binding(pair_id, &short, user_page, &nonce_hex);
        // plain words, until when and the short code on top, the countdown beside (Andy 11-21)
        let until = crate::owner_ok::unix_now() + TOUCH_WINDOW.as_secs();
        let why = crate::touch::window(
            who,
            &short,
            user_page,
            &crate::owner_ok::until_words(until),
            &short,
        );
        // the window closes by itself after TOUCH_WINDOW; unanswered is not a no (no cool-down)
        let asked = {
            let _hud =
                crate::touch_hud::Hud::show(&crate::touch::plain(who, user_page), until, &short);
            presence.ask_until(&why, &msg, TOUCH_WINDOW)
        };
        self.touch_done(crate::touch::ends_quietly(&asked));
        if let Err(e) = asked {
            self.cancel(pair_id);
            return Err(e);
        }
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        match inner.pairs.get_mut(pair_id) {
            Some(p) if p.state(now) == PairState::AwaitingConfirmation => {
                p.stored = Stored::Confirmed;
                Ok(())
            }
            _ => Err("pair_touch_late"),
        }
    }

    /// 5d33a REVIEW-touchid-8ec571dc M1 (DESIGN r3 §3, against a same-account AI raising window
    /// after window until a hand presses out of habit): one fingerprint window at a time
    /// (`pair_touch_busy`), none for [`TOUCH_COOL`] after one ended in a no
    /// (`pair_touch_cooling`), and, for `global`, the same 3 in 10 minutes / 6 an hour as
    /// re-confirm, counted over the whole aibd (`pair_touch_limited`). Marks a window open.
    fn touch_gate(inner: &mut Inner, now: Instant, global: bool) -> Result<(), &'static str> {
        if inner.touch_busy {
            return Err("pair_touch_busy");
        }
        if inner.touch_cool_until.is_some_and(|t| now < t) {
            return Err("pair_touch_cooling");
        }
        if global {
            let asks = &mut inner.touch_asks;
            while asks.front().is_some_and(|t| now >= *t + RECONFIRM_LONG) {
                asks.pop_front();
            }
            let short = asks.iter().filter(|t| now < **t + RECONFIRM_SHORT).count();
            let full = short >= RECONFIRM_SHORT_MAX || asks.len() >= RECONFIRM_LONG_MAX;
            if full {
                return Err("pair_touch_limited");
            }
            asks.push_back(now);
        }
        inner.touch_busy = true;
        Ok(())
    }

    /// How long until [`Self::touch_gate`] lets a window past the 3 in 10 minutes / 6 an hour
    /// again; none when it would now (0eed7 pv7 L: the page says when, not "cannot be asked").
    pub fn touch_wait(&self) -> Option<Duration> {
        let inner = self.lock();
        limit_wait(&inner.touch_asks, (self.clock)())
    }

    /// The window [`Self::touch_gate`] let open has closed; a no starts the cool-down.
    fn touch_done(&self, yes: bool) {
        let mut inner = self.lock();
        let now = (self.clock)();
        inner.touch_busy = false;
        if !yes {
            inner.touch_cool_until = Some(now + TOUCH_COOL);
        }
    }

    /// An owner window outside pairing (owner_ok, a63) goes through the same gate as pairing's
    /// own: one window at a time, the cool-down after a no, and the 3 in 10 minutes / 6 an hour
    /// over the whole aibd (5d33a PREREAD-ownertouch L1: no alternating two kinds of window).
    pub fn window_open(&self) -> Result<(), &'static str> {
        let mut inner = self.lock();
        let now = (self.clock)();
        let owner_counted = true;
        Self::touch_gate(&mut inner, now, owner_counted)
    }

    /// The window [`Self::window_open`] let open has closed; a no starts the cool-down.
    pub fn window_closed(&self, yes: bool) {
        self.touch_done(yes)
    }

    /// Whether a carried pairing can be re-confirmed here: the fingerprint channel with its key.
    /// The page reads this off a refusal (`reconfirm`) before it offers the window (519d0
    /// 01:41: probe, then fall back to "pair again").
    pub fn can_reconfirm(&self) -> bool {
        self.channel == ConfirmChannel::TouchId && self.presence.get().is_some()
    }

    /// `POST /api/v1/pair/reconfirm` (BOUNDARY r3 §3): the owner's finger for a pairing carried
    /// over an update, started by the action `act` (a key of [`RECONFIRM_ACTS`]). A yes makes it
    /// work as one paired in this run until its own end (the window says so, with that end); a
    /// no leaves it as it was. Blocking (the fingerprint window): call it off the async threads.
    pub fn reconfirm(&self, digest: &Hash, act: &str) -> Result<(), &'static str> {
        let Some(&(_, words)) = RECONFIRM_ACTS.iter().find(|(k, _)| *k == act) else {
            return Err("reconfirm_act_unknown");
        };
        if !self.can_reconfirm() {
            return Err("pair_reconfirm_off");
        }
        let (pair_id, user_page, left) = {
            let mut inner = self.lock();
            let now = (self.clock)();
            Self::purge(&mut inner, now);
            let Some(id) = inner.creds.get(digest).cloned() else {
                return Err("pair_not_carried");
            };
            match inner.pairs.get(&id) {
                Some(p)
                    if p.state(now) == PairState::Confirmed
                        && p.carry.is_some_and(|c| !c.reconfirmed) => {}
                _ => return Err("pair_not_carried"),
            }
            let (user_page, left) = match inner.pairs.get(&id) {
                Some(p) => (
                    p.user_page,
                    p.carry
                        .map(|c| c.ends.saturating_duration_since(now))
                        .unwrap_or_default(),
                ),
                None => return Err("pair_not_carried"),
            };
            let asks = inner.reconfirm_asks.entry(id.clone()).or_default();
            while asks.front().is_some_and(|t| now >= *t + RECONFIRM_LONG) {
                asks.pop_front();
            }
            let short = asks.iter().filter(|t| now < **t + RECONFIRM_SHORT).count();
            if short >= RECONFIRM_SHORT_MAX || asks.len() >= RECONFIRM_LONG_MAX {
                return Err("pair_reconfirm_limited");
            }
            // one window at a time and the cool-down hold here too (5d33a M1)
            Self::touch_gate(&mut inner, now, false)?;
            if let Some(asks) = inner.reconfirm_asks.get_mut(&id) {
                asks.push_back(now);
            }
            (id, user_page, left)
        };
        let who = crate::touch::Who::Reconfirm {
            act: words,
            until: crate::touch::until_text(left),
        };
        // no short code here: four characters of the pairing's id stand in (owner_ok::pair_code)
        let until = crate::owner_ok::unix_now() + TOUCH_WINDOW.as_secs();
        let code = crate::owner_ok::pair_code(&pair_id);
        let shown = crate::touch::window(
            &who,
            "",
            user_page,
            &crate::owner_ok::until_words(until),
            &code,
        );
        let mut msg = crate::touch::binding(&pair_id, "", user_page, "");
        msg.extend_from_slice(format!("reconfirm\n{act}\n").as_bytes());
        let Some(presence) = self.presence.get() else {
            self.touch_done(false);
            return Err("pair_key_missing");
        };
        // the window closes by itself after TOUCH_WINDOW; unanswered is not a no (no cool-down)
        let asked = {
            let _hud =
                crate::touch_hud::Hud::show(&crate::touch::plain(&who, user_page), until, &code);
            presence.ask_until(&shown, &msg, TOUCH_WINDOW)
        };
        self.touch_done(crate::touch::ends_quietly(&asked));
        asked?;
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        match inner.pairs.get_mut(&pair_id) {
            Some(p) if p.state(now) == PairState::Confirmed => match p.carry.as_mut() {
                Some(c) => {
                    c.reconfirmed = true;
                    Ok(())
                }
                None => Err("pair_not_carried"),
            },
            _ => Err("pair_touch_late"),
        }
    }

    /// Sets where outcomes are written (the first call wins).
    pub fn set_journal(&self, j: Journal) {
        let _ = self.journal.set(j);
    }

    /// Writes one outcome (blocking: call it off the async threads). Never a code or a
    /// credential: only the step's outcome and the pair id.
    pub fn journal(&self, kind: &'static str, payload: serde_json::Value) {
        if let Some(j) = self.journal.get() {
            j(kind, payload);
        }
    }

    /// `{channel, failures_total, rate_limited_total, pairs}` for `/api/v1/metrics`.
    pub fn metrics(&self) -> serde_json::Value {
        let inner = self.lock();
        serde_json::json!({
            "channel": self.channel.as_str(),
            "failures_total": inner.failures_total,
            "rate_limited_total": inner.rate_limited_total,
            "confirm_failures_total": inner.confirm_failures_total,
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
            let linked = p.linked.take();
            if let Some(c) = p.cred {
                inner.creds.remove(&c);
            }
            // the terminal's credential stands on this one's code: it goes with it
            if let Some(t) = linked {
                Self::revoke(inner, &t);
            }
        }
    }

    /// `POST /api/v1/pair/codes` (start-up token only).
    pub fn create_code(&self) -> Result<NewCode, PairError> {
        self.create_code_for(false)
    }

    /// [`Self::create_code`]; `terminal`: the terminal that asked is paired in the same step
    /// (SPEC §19b-3).
    pub fn create_code_for(&self, terminal: bool) -> Result<NewCode, PairError> {
        self.create_code_next(terminal, None)
    }

    /// [`Self::create_code_for`]; `replaces` (SPEC §19f): the terminal's own code that ran out
    /// unused, swapped at most [`MAX_ROTATIONS`] times; whoever shows the old code may fetch
    /// the new one ([`Self::next_code`]).
    pub fn create_code_next(
        &self,
        terminal: bool,
        replaces: Option<&str>,
    ) -> Result<NewCode, PairError> {
        if self.channel == ConfirmChannel::Off {
            return Err(PairError::Off);
        }
        let mut inner = self.lock();
        let now = (self.clock)(); // after the lock: waiting for it never uses an old now
        Self::purge(&mut inner, now);
        if inner.pairs.len() >= MAX_PAIRS {
            return Err(PairError::Full);
        }
        let rotation = match replaces {
            None => 0,
            Some(old) => match inner.pairs.get(old) {
                Some(o)
                    if terminal
                        && o.terminal
                        && o.stored == Stored::Issued
                        && o.next.is_none()
                        && o.rotation < MAX_ROTATIONS
                        && o.state(now) == PairState::Expired =>
                {
                    o.rotation + 1
                }
                _ => return Err(PairError::NotFound),
            },
        };
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
                user_page: false,
                carry: None,
                terminal,
                linked: None,
                page_nonce: None,
                rotation,
                next: None,
            },
        );
        let shown = format!("{}-{}", &code[..5], &code[5..]);
        if let Some(o) = replaces.and_then(|old| inner.pairs.get_mut(old)) {
            o.next = Some((pair_id.clone(), shown.clone()));
        }
        Ok(NewCode {
            pair_id,
            code: shown,
            expires_in: CODE_TTL.as_secs(),
            expires_at_ms: unix_ms_in(CODE_TTL),
        })
    }

    /// `POST /api/v1/pair/next` (SPEC §19f): the page shows the code it has. Still live: that
    /// code with its end. Swapped by `ctl web` after it ran out: the live one that replaced it
    /// (following up to [`MAX_ROTATIONS`] swaps). `Ok(None)`: known, ran out, no new one yet.
    /// A code nobody made counts as a failure, as in redeem.
    pub fn next_code(&self, presented: &str) -> Result<Option<NewCode>, PairError> {
        if self.channel == ConfirmChannel::Off {
            return Err(PairError::Off);
        }
        let mut inner = self.lock();
        let now = (self.clock)();
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
        let Some(mut id) = found else {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        };
        // still live and unused: the same code, with its end (the page counts down to it)
        if let Some(p) = inner.pairs.get(&id) {
            if p.stored == Stored::Issued && p.state(now) == PairState::Issued {
                let left = (p.issued + CODE_TTL).saturating_duration_since(now);
                return Ok(Some(NewCode {
                    pair_id: id,
                    code: format!("{}-{}", &code[..5], &code[5..]),
                    expires_in: left.as_secs(),
                    expires_at_ms: unix_ms_in(left),
                }));
            }
        }
        for _ in 0..=MAX_ROTATIONS {
            let Some((next_id, next_code)) = inner.pairs.get(&id).and_then(|p| p.next.clone())
            else {
                return Ok(None);
            };
            let Some(n) = inner.pairs.get(&next_id) else {
                return Ok(None);
            };
            if n.stored == Stored::Issued && n.state(now) == PairState::Issued {
                let left = (n.issued + CODE_TTL).saturating_duration_since(now);
                return Ok(Some(NewCode {
                    pair_id: next_id,
                    code: next_code,
                    expires_in: left.as_secs(),
                    expires_at_ms: unix_ms_in(left),
                }));
            }
            id = next_id;
        }
        Ok(None)
    }

    /// `POST /api/v1/pair/redeem`. Atomic: of any number of redemptions of one code, at
    /// most one succeeds; any second one revokes the credential the first one got.
    pub fn redeem(&self, presented: &str) -> Result<Redeemed, PairError> {
        self.redeem_marked(presented, false)
    }

    /// [`Self::redeem`], marking the credential as handed to a user folder page when one
    /// serves `/` at this moment (`user_page`).
    pub fn redeem_marked(&self, presented: &str, user_page: bool) -> Result<Redeemed, PairError> {
        self.redeem_with(presented, user_page, false, None)
    }

    /// [`Self::redeem_marked`]; `confirm_in_page` (the box under the code, SPEC §19b-2): typing
    /// the code is the confirmation, no y at the terminal. Never for a user folder page.
    pub fn redeem_with(
        &self,
        presented: &str,
        user_page: bool,
        confirm_in_page: bool,
        nonce: Option<&str>,
    ) -> Result<Redeemed, PairError> {
        if self.channel == ConfirmChannel::Off {
            // off means off: no matching, no failure counted (043dd A4 L2)
            return Err(PairError::Off);
        }
        // TouchId: only the fingerprint confirms (ask_touch), never the box on the page
        let confirm_in_page = confirm_in_page && self.channel != ConfirmChannel::TouchId;
        let mut inner = self.lock();
        let now = (self.clock)(); // after the lock: waiting for it never uses an old now
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
        if p.stored == Stored::Revoked && p.cred.is_none() {
            // cancelled or expired before anyone redeemed it: a dead code, not a reuse
            // (SPEC §19f: past its time it says so; cancelled stays the plain failure)
            let e = if now >= p.issued + CODE_TTL {
                PairError::Expired
            } else {
                PairError::Failed
            };
            return Err(Self::fail(&mut inner, now, e));
        }
        let nonce_hash = nonce.map(|n| sha(n.as_bytes()));
        if p.stored != Stored::Issued
            && p.stored != Stored::Revoked
            && p.redeemed.is_some_and(|r| now < r + RESUBMIT_WINDOW)
            && matches!((p.page_nonce, nonce_hash), (Some(a), Some(b)) if bool::from(a.ct_eq(&b)))
        {
            // the same page sending the same code again (a double click, Enter and a click)
            return Err(PairError::Already);
        }
        if p.stored != Stored::Issued {
            // second use, whatever the first one's state: revoke what it got
            Self::revoke(&mut inner, &id);
            return Err(Self::fail(&mut inner, now, PairError::Reused));
        }
        if p.state(now) == PairState::Expired {
            // an expired code can never be redeemed later either
            Self::revoke(&mut inner, &id);
            return Err(Self::fail(&mut inner, now, PairError::Expired));
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
            p.stored = if confirm_in_page && !user_page {
                Stored::Confirmed
            } else {
                Stored::Awaiting
            };
            p.cred = Some(digest);
            p.redeemed = Some(now);
            p.user_page = user_page;
            p.page_nonce = nonce_hash;
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
        self.access_digest(sha(presented.as_bytes()))
    }

    /// [`Pairing::access`] by the credential's digest: 指紋批次簽 replays each item with the
    /// pairing as it is now (f163c REVIEW-ownerbatch-impl r0 L3), not as it was collected.
    pub fn access_digest(&self, digest: Hash) -> Option<PageAccess> {
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        // constant time over every stored digest: no early exit, no hash-table probe
        let mut found: Option<&String> = None;
        for (k, id) in &inner.creds {
            if bool::from(k.ct_eq(&digest)) {
                found = Some(id);
            }
        }
        let id = found?.clone();
        let p = inner.pairs.get_mut(&id)?;
        let carried = p.carry.is_some_and(|c| !c.reconfirmed);
        match p.state(now) {
            PairState::AwaitingConfirmation => Some(PageAccess {
                digest,
                confirmed: false,
                user_page: p.user_page,
                carried,
            }),
            PairState::Confirmed => {
                // a carried credential, once used, lives out its own lifetime
                if let Some(c) = p.carry.as_mut() {
                    c.used = true;
                }
                Some(PageAccess {
                    digest,
                    confirmed: true,
                    user_page: p.user_page,
                    carried,
                })
            }
            _ => None,
        }
    }

    /// `GET /api/v1/pair/status` (the page's own credential).
    pub fn status(&self, digest: &Hash) -> Option<PairView> {
        let mut inner = self.lock();
        let now = (self.clock)(); // after the lock: waiting for it never uses an old now
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
        let inner = self.lock();
        let now = (self.clock)();
        inner.pairs.get(pair_id).map(|p| p.view(now))
    }

    /// `POST /api/v1/pair/codes/{pair_id}/confirm` (start-up token only), sent by
    /// `aib web` after the person answered that the page shows the same short code.
    /// A wrong short code revokes the credential (fail closed).
    pub fn confirm(&self, pair_id: &str, short_code: &str) -> Result<(), PairError> {
        let mut inner = self.lock();
        let now = (self.clock)(); // after the lock: waiting for it never uses an old now
        Self::purge(&mut inner, now);
        // confirm has no shared rate limit: anyone can fill the redeem bucket (any web page),
        // and confirm needs the start-up token; a wrong short code revokes (043dd A4 M1)
        if self.channel == ConfirmChannel::TouchId {
            // the terminal's y is not a confirmation here: only the fingerprint is (ask_touch)
            inner.confirm_failures_total += 1;
            return Err(PairError::Failed);
        }
        let Some(p) = inner.pairs.get(pair_id) else {
            return Err(PairError::NotFound);
        };
        if p.state(now) != PairState::AwaitingConfirmation {
            inner.confirm_failures_total += 1;
            return Err(PairError::Failed);
        }
        let ok: bool = p
            .short_code
            .as_bytes()
            .ct_eq(short_code.trim().as_bytes())
            .into();
        if !ok {
            Self::revoke(&mut inner, pair_id);
            inner.confirm_failures_total += 1;
            return Err(PairError::Failed);
        }
        if let Some(p) = inner.pairs.get_mut(pair_id) {
            p.stored = Stored::Confirmed;
        }
        Ok(())
    }

    /// The terminal's own credential for a pairing made with `terminal` (SPEC §19b-3), once:
    /// only while that pairing is confirmed, and never twice (`Ok(None)` after the first).
    /// It is a pairing of its own, confirmed, linked so revoking the page's revokes it.
    pub fn take_terminal(&self, pair_id: &str) -> Result<Option<String>, PairError> {
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        let Some(p) = inner.pairs.get(pair_id) else {
            return Err(PairError::NotFound);
        };
        if !p.terminal || p.linked.is_some() || p.state(now) != PairState::Confirmed {
            return Ok(None);
        }
        if inner.creds.len() >= MAX_CREDENTIALS || inner.pairs.len() >= MAX_PAIRS {
            return Err(PairError::Full);
        }
        let raw: [u8; 32] = random().map_err(|_| PairError::Failed)?;
        use base64::Engine;
        let credential = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(raw);
        let digest = sha(credential.as_bytes());
        let id = hex(&random::<8>().map_err(|_| PairError::Failed)?);
        let dead = sha(&random::<32>().map_err(|_| PairError::Failed)?);
        inner.pairs.insert(
            id.clone(),
            Pair {
                // no code leads here: a random digest nothing hashes to
                code_hash: dead,
                issued: now,
                stored: Stored::Confirmed,
                cred: Some(digest),
                redeemed: Some(now),
                short_code: String::new(),
                user_page: false,
                carry: None,
                terminal: false,
                linked: None,
                page_nonce: None,
                rotation: 0,
                next: None,
            },
        );
        inner.creds.insert(digest, id.clone());
        if let Some(p) = inner.pairs.get_mut(pair_id) {
            p.linked = Some(id);
        }
        Ok(Some(credential))
    }

    /// A pass the parent aibd handed down (only its digest; the pass itself went to the
    /// parent's paired page and from there into the opened tab's address fragment). Lasts
    /// `ttl`, at most [`PASS_TTL`]; the oldest goes past [`MAX_PASSES`].
    pub fn add_pass(&self, digest: [u8; 32], ttl: Duration) {
        let mut inner = self.lock();
        let now = (self.clock)();
        inner.passes.retain(|(_, until)| now < *until);
        while inner.passes.len() >= MAX_PASSES {
            inner.passes.pop_front();
        }
        inner.passes.push_back((digest, now + ttl.min(PASS_TTL)));
    }

    /// `POST /api/v1/pair/preview` (Andy 11-19 08:5x: the preview opens already paired): the
    /// page its parent's paired page opened shows the pass from its address; a live one is
    /// used up and buys a confirmed credential, as a terminal's own does. No code, no key and
    /// no finger here: the person proved it on the parent, whose paired page alone got the
    /// pass. A wrong or used pass counts as a failed code; pairing off refuses it too.
    pub fn admit_pass(&self, presented: &str) -> Result<String, PairError> {
        if self.channel == ConfirmChannel::Off {
            return Err(PairError::Off);
        }
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        if Self::limited(&mut inner) {
            return Err(PairError::RateLimited);
        }
        inner.passes.retain(|(_, until)| now < *until);
        let h = sha(presented.trim().as_bytes());
        let Some(at) = inner
            .passes
            .iter()
            .position(|(d, _)| bool::from(d.ct_eq(&h)))
        else {
            return Err(Self::fail(&mut inner, now, PairError::Failed));
        };
        if inner.creds.len() >= MAX_CREDENTIALS || inner.pairs.len() >= MAX_PAIRS {
            return Err(PairError::Full);
        }
        // used up before anything else: a second showing finds nothing
        inner.passes.remove(at);
        let raw: [u8; 32] = random().map_err(|_| PairError::Failed)?;
        use base64::Engine;
        let credential = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(raw);
        let digest = sha(credential.as_bytes());
        let id = hex(&random::<8>().map_err(|_| PairError::Failed)?);
        let dead = sha(&random::<32>().map_err(|_| PairError::Failed)?);
        inner.pairs.insert(
            id.clone(),
            Pair {
                // no code leads here: a random digest nothing hashes to
                code_hash: dead,
                issued: now,
                stored: Stored::Confirmed,
                cred: Some(digest),
                redeemed: Some(now),
                short_code: String::new(),
                user_page: false,
                carry: None,
                terminal: false,
                linked: None,
                page_nonce: None,
                rotation: 0,
                next: None,
            },
        );
        inner.creds.insert(digest, id);
        Ok(credential)
    }

    /// `DELETE /api/v1/pair/codes/{pair_id}` (start-up token only): the person said no
    /// or closed the prompt. Returns false for an unknown id.
    pub fn cancel(&self, pair_id: &str) -> bool {
        let mut inner = self.lock();
        let known = inner.pairs.contains_key(pair_id);
        Self::revoke(&mut inner, pair_id);
        known
    }

    /// What a clean stop writes for the carry-over (S89-pair-carry): every credential that is
    /// confirmed and in time now, and was not itself carried over (once only, 5d33a F2).
    /// A revoked, expired or still unconfirmed one is not written.
    pub fn carry_out(&self) -> Vec<CarriedCred> {
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        let mut out = Vec::new();
        for (digest, id) in &inner.creds {
            let Some(p) = inner.pairs.get(id) else {
                continue;
            };
            if p.carry.is_some() || p.state(now) != PairState::Confirmed {
                continue;
            }
            let left = p.view(now).expires_in;
            if left > 0 {
                out.push(CarriedCred {
                    digest: *digest,
                    user_page: p.user_page,
                    left,
                });
            }
        }
        out
    }

    /// The new aibd takes what the old one wrote: each credential confirmed, with its own
    /// lifetime left (never more than [`CREDENTIAL_TTL`]), its user-page mark (5d33a F3) and a
    /// [`CARRY_USE_BY`] deadline to be used. Off means off. Returns how many it took.
    pub fn carry_in(&self, list: &[CarriedCred]) -> usize {
        if self.channel == ConfirmChannel::Off {
            return 0;
        }
        let mut inner = self.lock();
        let now = (self.clock)();
        Self::purge(&mut inner, now);
        let mut n = 0;
        for c in list {
            if c.left == 0
                || inner.creds.len() >= MAX_CREDENTIALS
                || inner.pairs.len() >= MAX_PAIRS
                || inner.creds.contains_key(&c.digest)
            {
                continue;
            }
            let (Ok(id), Ok(dead)) = (random::<8>(), random::<32>()) else {
                continue;
            };
            let id = hex(&id);
            let left = Duration::from_secs(c.left).min(CREDENTIAL_TTL);
            inner.pairs.insert(
                id.clone(),
                Pair {
                    // no code leads here: a random digest nothing hashes to
                    code_hash: dead,
                    issued: now,
                    stored: Stored::Confirmed,
                    cred: Some(c.digest),
                    redeemed: Some(now),
                    short_code: String::new(),
                    user_page: c.user_page,
                    carry: Some(Carry {
                        ends: now + left,
                        use_by: now + CARRY_USE_BY,
                        used: false,
                        reconfirmed: false,
                    }),
                    terminal: false,
                    linked: None,
                    page_nonce: None,
                    rotation: 0,
                    next: None,
                },
            );
            inner.creds.insert(c.digest, id);
            n += 1;
        }
        n
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

    fn touch_with(yes: Option<bool>) -> (Pairing, Arc<crate::touch::tests::Recorder>) {
        let p = Pairing::default().with_channel(ConfirmChannel::TouchId);
        let rec = Arc::new(crate::touch::tests::Recorder {
            yes: yes.unwrap_or(false),
            ..Default::default()
        });
        if yes.is_some() {
            p.set_presence(rec.clone());
        }
        (p, rec)
    }

    /// DESIGN-pair-touchid r3 / 5d33a CHECK-pair-touchid: with the TouchId channel neither the
    /// box on the page nor the terminal's y confirms; only aibd's own fingerprint window does,
    /// and the window says only aibd's values (L2).
    #[test]
    fn touchid_confirms_only_by_the_owners_fingerprint() {
        use crate::touch::{reason, Who};
        let (p, rec) = touch_with(Some(true));
        let c = p.create_code().unwrap();
        let r = p
            .redeem_with(&c.code, false, true, Some("nonce-from-the-page"))
            .unwrap();
        let v = p.view(&r.pair_id).unwrap();
        assert_eq!(
            v.state,
            PairState::AwaitingConfirmation,
            "reason: box_confirmed"
        );
        let short = v.short_code.unwrap();
        assert!(
            p.confirm(&r.pair_id, &short).is_err(),
            "reason: terminal_y_confirmed"
        );
        assert_eq!(p.ask_touch(&r.pair_id, &Who::Terminal), Ok(()));
        assert_eq!(p.view(&r.pair_id).unwrap().state, PairState::Confirmed);
        assert!(p.access(&r.credential).unwrap().confirmed);
        let asked = rec.asked.lock().unwrap();
        assert_eq!(asked.len(), 1);
        // plain words on top, then the window's old words as they were (Andy 11-21 01:44)
        // four lines now (ebab8 11-23 05:47); the old words are the last
        let lines: Vec<&str> = asked[0].0.lines().collect();
        assert_eq!(
            lines.len(),
            crate::touch::WINDOW_LINES,
            "reason: window_words"
        );
        // the first line is exactly aibd's question, nothing from the request before it
        assert_eq!(
            lines[0],
            crate::touch::ask_first(&Who::Terminal),
            "reason: window_first_line"
        );
        let (top, rest) = (lines[2], lines[3]);
        assert!(
            top.starts_with(&crate::touch::plain(&Who::Terminal, false)),
            "reason: window_words {top}"
        );
        assert_eq!(
            rest,
            reason(&Who::Terminal, &short, false),
            "reason: window_words"
        );
        assert!(
            !asked[0].0.contains("nonce"),
            "reason: request_text_in_window"
        );
        // once confirmed it is not asked again
        assert_eq!(
            p.ask_touch(&r.pair_id, &Who::Terminal),
            Err("pair_touch_not_awaiting")
        );
    }

    #[test]
    fn no_key_or_a_cancelled_fingerprint_revokes_the_pairing() {
        use crate::touch::Who;
        for yes in [None, Some(false)] {
            let (p, _) = touch_with(yes);
            let c = p.create_code().unwrap();
            let r = p.redeem_with(&c.code, false, false, None).unwrap();
            let e = p.ask_touch(&r.pair_id, &Who::Terminal).unwrap_err();
            assert_eq!(
                e,
                if yes.is_none() {
                    "pair_key_missing"
                } else {
                    "user_cancel"
                }
            );
            assert_eq!(
                p.view(&r.pair_id).unwrap().state,
                PairState::Revoked,
                "reason: kept_after_no"
            );
            assert!(p.access(&r.credential).is_none());
        }
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
        // a20 r3b: the weak terminal pairing is read on every platform (macOS too)
        assert_eq!(
            ConfirmChannel::from_env_value(Some("terminal-unverified")),
            ConfirmChannel::TerminalUnverified
        );
        assert_eq!(
            ConfirmChannel::TerminalUnverified.method(),
            Some("terminal_y")
        );
        assert_eq!(ConfirmChannel::Off.method(), None);
    }

    #[test]
    fn a_windows_aibd_pairs_through_hello_unless_told_otherwise() {
        // Andy 11-26 03:1x: a customer starts aibd.exe with no `set`
        assert_eq!(
            ConfirmChannel::from_env_here(None, true),
            ConfirmChannel::TouchId,
            "reason: windows_unset_not_hello"
        );
        assert_eq!(
            ConfirmChannel::from_env_here(None, false),
            ConfirmChannel::Off,
            "reason: other_platform_unset_turned_on"
        );
        for (v, want) in [
            ("off", ConfirmChannel::Off),
            ("0", ConfirmChannel::Off),
            ("", ConfirmChannel::Off),
            ("terminal-unverified", ConfirmChannel::TerminalUnverified),
            ("touchid", ConfirmChannel::TouchId),
        ] {
            for windows in [true, false] {
                assert_eq!(
                    ConfirmChannel::from_env_here(Some(v), windows),
                    want,
                    "reason: given_value_not_decisive {v:?} {windows}"
                );
            }
        }
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

    /// SPEC §19b-2: the box makes typing the code the confirmation, never for a user folder
    /// page; the terminal's credential comes once and ends with the page's.
    #[test]
    fn the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it() {
        let p = on();
        let n = p.create_code_for(true).unwrap();
        assert_eq!(
            p.take_terminal(&n.pair_id).unwrap(),
            None,
            "not before it is confirmed"
        );
        let r = p.redeem_with(&n.code, false, true, None).unwrap();
        assert!(
            p.access(&r.credential).unwrap().confirmed,
            "reason: box_ignored"
        );
        let t = p.take_terminal(&n.pair_id).unwrap().unwrap();
        assert!(p.access(&t).unwrap().confirmed);
        assert_eq!(
            p.take_terminal(&n.pair_id).unwrap(),
            None,
            "reason: terminal_twice"
        );
        p.revoke_credential(&sha(r.credential.as_bytes()));
        assert!(p.access(&t).is_none(), "reason: terminal_outlives_page");
        // a user folder page: the box does not count
        let n = p.create_code().unwrap();
        let r = p.redeem_with(&n.code, true, true, None).unwrap();
        assert!(
            !p.access(&r.credential).unwrap().confirmed,
            "reason: user_page_box"
        );
        // a code made without the terminal gives none
        let n = p.create_code().unwrap();
        p.redeem_with(&n.code, false, true, None).unwrap();
        assert_eq!(p.take_terminal(&n.pair_id).unwrap(), None);
    }

    fn advance(o: &AtomicU64, d: Duration) {
        o.fetch_add(d.as_millis() as u64, Ordering::SeqCst);
    }

    /// Andy 11-19 08:5x (519d0 dispatch): a pass the parent handed down pairs the preview's
    /// page once and in time, confirmed, with no code, no key and no finger (the preview's
    /// own folder has no pair-key); a used, late or wrong one fails; pairing off refuses it.
    #[test]
    fn a_preview_pass_pairs_once_in_time_without_a_key() {
        let (clock, o) = fake();
        let p = Pairing::with_clock(clock).with_channel(ConfirmChannel::TouchId);
        let pass = "A".repeat(43);
        p.add_pass(sha(pass.as_bytes()), Duration::from_secs(3600));
        let cred = p.admit_pass(&pass).unwrap();
        assert!(
            p.access(&cred).unwrap().confirmed,
            "reason: pass_not_confirmed"
        );
        assert_eq!(
            p.admit_pass(&pass).unwrap_err(),
            PairError::Failed,
            "reason: pass_reused"
        );
        // at most PASS_TTL, whatever the line said
        let late = "B".repeat(43);
        p.add_pass(sha(late.as_bytes()), Duration::from_secs(3600));
        advance(&o, PASS_TTL);
        assert_eq!(
            p.admit_pass(&late).unwrap_err(),
            PairError::Failed,
            "reason: pass_late"
        );
        assert!(p.admit_pass(&"C".repeat(43)).is_err());
        let off = Pairing::default();
        off.add_pass(sha(pass.as_bytes()), PASS_TTL);
        assert_eq!(off.admit_pass(&pass).unwrap_err(), PairError::Off);
        // only the newest MAX_PASSES wait
        let q = on();
        for i in 0..=MAX_PASSES {
            q.add_pass(sha(format!("{i:043}").as_bytes()), PASS_TTL);
        }
        assert!(
            q.admit_pass(&format!("{:043}", 0)).is_err(),
            "reason: passes_unbounded"
        );
        assert!(q.admit_pass(&format!("{MAX_PASSES:043}")).is_ok());
    }

    #[test]
    fn code_shape_and_normalising() {
        let p = on();
        let c = p.create_code().unwrap();
        assert_eq!(c.code.len(), CODE_LEN + 1);
        assert_eq!(&c.code[5..6], "-");
        assert_eq!(c.expires_in, CODE_TTL.as_secs());
        assert!(!format!("{c:?}").contains(&c.code[..5]));
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
    fn a_dead_code_is_a_failure_not_a_reuse() {
        let p = on();
        let c = p.create_code().unwrap();
        assert!(p.cancel(&c.pair_id));
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Failed);
    }

    #[test]
    fn redeem_is_off_when_pairing_is_off() {
        let p = Pairing::default();
        assert_eq!(p.redeem("00000-00000").unwrap_err(), PairError::Off);
        assert_eq!(p.metrics()["failures_total"], 0);
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

    /// A page paired and confirmed on `p`: (credential, pair id).
    fn confirmed(p: &Pairing) -> (String, String) {
        let c = p.create_code().unwrap();
        let r = p.redeem(&c.code).unwrap();
        let short = p.view(&c.pair_id).unwrap().short_code.unwrap();
        p.confirm(&c.pair_id, &short).unwrap();
        (r.credential, c.pair_id)
    }

    /// PC1 (S89-pair-carry): only a confirmed credential in time is written; a revoked, an
    /// unconfirmed and an expired one are not (DECISION §4: revoke, then restart ⇒ 401).
    #[test]
    fn carry_out_takes_confirmed_credentials_only() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let (live, _) = confirmed(&p);
        let (gone, gone_id) = confirmed(&p);
        p.cancel(&gone_id);
        let c = p.create_code().unwrap();
        let _waiting = p.redeem(&c.code).unwrap();
        let out = p.carry_out();
        assert_eq!(out.len(), 1, "{out:?}");
        assert_eq!(out[0].digest, sha(live.as_bytes()));
        assert!(out[0].digest != sha(gone.as_bytes()));
        assert_eq!(out[0].left, CREDENTIAL_TTL.as_secs());
        advance(&o, CREDENTIAL_TTL);
        assert!(p.carry_out().is_empty(), "an expired one is not carried");
    }

    /// PC2: a carried credential works as confirmed, keeps its user-page mark, says it was
    /// carried, and is never written again (once only, 5d33a F2 / F3).
    #[test]
    fn a_carried_credential_is_confirmed_marked_and_not_carried_again() {
        let old = on();
        let (cred, _) = confirmed(&old);
        let mut out = old.carry_out();
        out[0].user_page = true;
        let new = on();
        assert_eq!(new.carry_in(&out), 1);
        let a = new.access(&cred).unwrap();
        assert!(a.confirmed && a.carried && a.user_page);
        assert!(new.status(&a.digest).unwrap().carried);
        assert!(new.carry_out().is_empty(), "carried once, never twice");
        // a credential nobody carried is still unknown to the new aibd
        let (other, _) = confirmed(&old);
        assert!(new.access(&other).is_none());
    }

    /// A carried pairing in a TouchId aibd (with the owner's key answering `yes`, or none).
    fn carried_touch(
        yes: Option<bool>,
        clock: Option<Clock>,
    ) -> (Pairing, String, Arc<crate::touch::tests::Recorder>) {
        let old = on();
        let (cred, _) = confirmed(&old);
        let out = old.carry_out();
        let new = match clock {
            Some(c) => Pairing::with_clock(c),
            None => Pairing::default(),
        }
        .with_channel(ConfirmChannel::TouchId);
        let rec = Arc::new(crate::touch::tests::Recorder {
            yes: yes.unwrap_or(false),
            ..Default::default()
        });
        if yes.is_some() {
            new.set_presence(rec.clone());
        }
        assert_eq!(new.carry_in(&out), 1);
        (new, cred, rec)
    }

    /// BOUNDARY r3 §3 / 5d33a CHECK-pair-touchid §6-2, §6-3: a carried pairing re-confirmed
    /// by the owner's finger works as one paired in this run; the window says the whole page goes
    /// up, to when, and aibd's words for the action that started it; it is never carried again; a no changes
    /// nothing; an unknown action, a pairing of this run or no key gets no window.
    #[test]
    fn a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs() {
        let (p, cred, rec) = carried_touch(Some(true), None);
        let a = p.access(&cred).unwrap();
        assert!(a.carried);
        assert!(p.can_reconfirm());
        assert_eq!(
            p.reconfirm(&a.digest, "rm -rf"),
            Err("reconfirm_act_unknown"),
            "reason: act_not_listed"
        );
        assert_eq!(p.reconfirm(&a.digest, "seat_open"), Ok(()));
        let asked = rec.asked.lock().unwrap().clone();
        assert_eq!(asked.len(), 1, "reason: act_not_listed");
        // 5d33a L1: it says the whole page goes up to this run's rights, to when, and why
        // four lines as an approval's (ebab8 11-23 05:47): the plain words first, the old ones last
        let lines: Vec<&str> = asked[0].0.lines().collect();
        assert_eq!(
            lines.len(),
            crate::touch::WINDOW_LINES,
            "reason: window_four_lines"
        );
        let (top, w) = (lines[2], lines[3]);
        assert!(
            top.starts_with("讓這一頁繼續用完整權限到 ") && top.contains("(起因:開席)"),
            "reason: window_top {top}"
        );
        assert!(
            w.starts_with("aibd 網頁重新確認:升級這頁到本輪完整權限,到 "),
            "reason: window_hides_full_upgrade {w}"
        );
        assert!(
            w.ends_with(&format!(
                ";起因:開席;換版前延續的配對,給 內建頁;由瀏覽器頁要求。{}",
                crate::touch::OWNER_CHECK_WORD
            )),
            "{w}"
        );
        let b = p.access(&cred).unwrap();
        assert!(b.confirmed && !b.carried, "reason: still_weak_after_yes");
        assert!(
            p.status(&b.digest).unwrap().carried,
            "the view still says it came over"
        );
        assert!(p.carry_out().is_empty(), "reason: carried_twice");
        // once this run's, there is nothing to re-confirm
        assert_eq!(p.reconfirm(&b.digest, "seat_open"), Err("pair_not_carried"));
    }

    #[test]
    fn a_no_or_no_key_leaves_a_carried_pairing_weak() {
        for yes in [None, Some(false)] {
            let (p, cred, _) = carried_touch(yes, None);
            let d = p.access(&cred).unwrap().digest;
            let e = p.reconfirm(&d, "assign").unwrap_err();
            assert_eq!(
                e,
                if yes.is_none() {
                    "pair_reconfirm_off"
                } else {
                    "user_cancel"
                }
            );
            let a = p.access(&cred).unwrap();
            assert!(a.confirmed && a.carried, "reason: still_weak_after_no");
        }
        // not the fingerprint channel ⇒ no window either
        let old = on();
        let (cred, _) = confirmed(&old);
        let new = on();
        new.carry_in(&old.carry_out());
        let d = new.access(&cred).unwrap().digest;
        assert!(!new.can_reconfirm());
        assert_eq!(new.reconfirm(&d, "assign"), Err("pair_reconfirm_off"));
    }

    /// 5d33a §6-2: per carried pairing at most 3 windows in 10 minutes and 6 in an hour.
    #[test]
    fn reconfirm_windows_are_limited_per_carried_pairing() {
        let (clock, o) = fake();
        let (p, cred, rec) = carried_touch(Some(false), Some(clock));
        let d = p.access(&cred).unwrap().digest;
        for _ in 0..RECONFIRM_SHORT_MAX {
            assert_eq!(p.reconfirm(&d, "assign"), Err("user_cancel"));
            // past the cool-down a no starts (5d33a M1), still inside ten minutes
            advance(&o, TOUCH_COOL);
        }
        assert_eq!(
            p.reconfirm(&d, "assign"),
            Err("pair_reconfirm_limited"),
            "reason: short_limit"
        );
        advance(&o, RECONFIRM_SHORT);
        for _ in 0..(RECONFIRM_LONG_MAX - RECONFIRM_SHORT_MAX) {
            assert_eq!(p.reconfirm(&d, "assign"), Err("user_cancel"));
            // ten minutes apart: the short count stays under its limit, the hour fills
            advance(&o, RECONFIRM_SHORT);
        }
        assert_eq!(
            p.reconfirm(&d, "assign"),
            Err("pair_reconfirm_limited"),
            "reason: long_limit"
        );
        assert_eq!(rec.asked.lock().unwrap().len(), RECONFIRM_LONG_MAX);
    }

    /// A redeemed code awaiting the finger, in a TouchId aibd with clock `clock`.
    fn awaiting(p: &Pairing) -> String {
        let c = p.create_code().unwrap();
        p.redeem_with(&c.code, false, false, None).unwrap().pair_id
    }

    /// 5d33a REVIEW-touchid-8ec571dc M1 (DESIGN r3 §3): one window at a time; none for 30 s
    /// after a no; a redeem's windows at most 3 in 10 minutes and 6 in an hour over the whole
    /// aibd. A pairing held back is revoked, never left waiting for a later window.
    #[test]
    fn fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited() {
        use crate::touch::Who;
        let (clock, o) = fake();
        let p = Pairing::with_clock(clock).with_channel(ConfirmChannel::TouchId);
        let no = Arc::new(crate::touch::tests::Recorder::default());
        p.set_presence(no.clone());
        // a window open now ⇒ a second redeem gets none
        p.lock().touch_busy = true;
        let id = awaiting(&p);
        assert_eq!(
            p.ask_touch(&id, &Who::Terminal),
            Err("pair_touch_busy"),
            "reason: two_windows"
        );
        assert_eq!(p.view(&id).unwrap().state, PairState::Revoked);
        assert!(no.asked.lock().unwrap().is_empty(), "reason: two_windows");
        p.lock().touch_busy = false;
        // a no ⇒ nothing for TOUCH_COOL
        let id = awaiting(&p);
        assert_eq!(p.ask_touch(&id, &Who::Terminal), Err("user_cancel"));
        assert!(!p.lock().touch_busy, "the window closed");
        let id = awaiting(&p);
        assert_eq!(
            p.ask_touch(&id, &Who::Terminal),
            Err("pair_touch_cooling"),
            "reason: no_cool_down"
        );
        assert_eq!(no.asked.lock().unwrap().len(), 1, "reason: no_cool_down");
        // the window says it plainly first, until when and the short code, then what it said
        let said = no.asked.lock().unwrap()[0].0.clone();
        let lines: Vec<&str> = said.lines().collect();
        assert_eq!(
            lines.len(),
            crate::touch::WINDOW_LINES,
            "reason: window_four_lines"
        );
        let (top, rest) = (lines[2], lines[3]);
        assert!(
            top.starts_with("讓這個瀏覽器分頁配對成 AIB 的")
                && lines[1].ends_with(",過了這張就作廢"),
            "reason: window_top {top}"
        );
        let code: String = rest
            .split_once("短碼 ")
            .map(|(_, r)| r.chars().take_while(char::is_ascii_digit).collect())
            .unwrap_or_default();
        assert!(
            !code.is_empty() && lines[1].starts_with(&format!("你的頁面也要顯示 {code},")),
            "reason: window_code {said}"
        );
        advance(&o, TOUCH_COOL);
        // the whole aibd: 3 in ten minutes (the no above was the first)
        for _ in 0..2 {
            let id = awaiting(&p);
            assert_eq!(p.ask_touch(&id, &Who::Terminal), Err("user_cancel"));
            advance(&o, TOUCH_COOL);
        }
        let id = awaiting(&p);
        assert_eq!(
            p.ask_touch(&id, &Who::Terminal),
            Err("pair_touch_limited"),
            "reason: global_short_limit"
        );
        // the three were shown at 0, TOUCH_COOL and 2 × TOUCH_COOL: the first drops out first
        assert_eq!(
            p.touch_wait(),
            Some(RECONFIRM_SHORT - TOUCH_COOL * 3),
            "reason: short_wait"
        );
        // ten minutes on, three more fit in the hour, then none
        advance(&o, RECONFIRM_SHORT);
        for _ in 0..3 {
            let id = awaiting(&p);
            assert_eq!(p.ask_touch(&id, &Who::Terminal), Err("user_cancel"));
            advance(&o, RECONFIRM_SHORT);
        }
        let id = awaiting(&p);
        assert_eq!(
            p.ask_touch(&id, &Who::Terminal),
            Err("pair_touch_limited"),
            "reason: global_long_limit"
        );
        // six in the hour and none in the last ten minutes: the first of the six leaves the hour
        assert_eq!(
            p.touch_wait(),
            Some(RECONFIRM_LONG - TOUCH_COOL * 3 - RECONFIRM_SHORT * 4),
            "reason: long_wait"
        );
        assert_eq!(no.asked.lock().unwrap().len(), RECONFIRM_LONG_MAX);
    }

    /// PC3: unused for CARRY_USE_BY after the start ⇒ gone (DECISION §4: past 10 minutes 401);
    /// used in time ⇒ it lives out its own lifetime, never longer.
    #[test]
    fn a_carried_credential_must_be_used_within_the_window() {
        let old = on();
        let (cred, _) = confirmed(&old);
        let out = old.carry_out();
        let (clock, o) = fake();
        let idle = on_with(clock);
        idle.carry_in(&out);
        advance(&o, CARRY_USE_BY);
        assert!(idle.access(&cred).is_none(), "unused past the window");
        let (clock, o) = fake();
        let used = on_with(clock);
        used.carry_in(&out);
        advance(&o, CARRY_USE_BY - Duration::from_secs(1));
        assert!(used.access(&cred).is_some());
        advance(&o, Duration::from_secs(2));
        assert!(used.access(&cred).is_some(), "used in time, still alive");
        advance(&o, CREDENTIAL_TTL);
        assert!(used.access(&cred).is_none(), "never past its own lifetime");
    }

    /// PC4: pairing off takes nothing; a carried credential can be revoked like any other.
    #[test]
    fn carry_in_is_off_when_pairing_is_off_and_revoke_ends_it() {
        let old = on();
        let (cred, _) = confirmed(&old);
        let out = old.carry_out();
        assert_eq!(Pairing::default().carry_in(&out), 0);
        let new = on();
        new.carry_in(&out);
        let a = new.access(&cred).unwrap();
        new.revoke_credential(&a.digest);
        assert!(new.access(&cred).is_none());
    }

    /// 519d0 20:36: the same page (same nonce) sending the same code again within
    /// RESUBMIT_WINDOW keeps its credential; another nonce, no nonce or later is still a reuse.
    #[test]
    fn the_same_page_sending_the_code_twice_keeps_its_credential() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let n = "page-nonce-0123456789abcdef";
        let c = p.create_code().unwrap();
        let r = p.redeem_with(&c.code, false, true, Some(n)).unwrap();
        let before = p.revocations();
        assert_eq!(
            p.redeem_with(&c.code, false, true, Some(n)).unwrap_err(),
            PairError::Already
        );
        assert!(
            p.access(&r.credential).is_some(),
            "reason: double_click_revoked"
        );
        assert_eq!(p.revocations(), before);
        // another page's nonce: a reuse, as before
        assert_eq!(
            p.redeem_with(&c.code, false, true, Some("another-page-nonce-000000"))
                .unwrap_err(),
            PairError::Reused
        );
        assert!(
            p.access(&r.credential).is_none(),
            "reason: other_nonce_kept"
        );
        // no nonce, and past the window: a reuse
        let c2 = p.create_code().unwrap();
        let r2 = p.redeem_with(&c2.code, false, true, Some(n)).unwrap();
        assert_eq!(
            p.redeem_with(&c2.code, false, true, None).unwrap_err(),
            PairError::Reused
        );
        assert!(p.access(&r2.credential).is_none());
        let c3 = p.create_code().unwrap();
        let r3 = p.redeem_with(&c3.code, false, true, Some(n)).unwrap();
        advance(&o, RESUBMIT_WINDOW + Duration::from_secs(1));
        assert_eq!(
            p.redeem_with(&c3.code, false, true, Some(n)).unwrap_err(),
            PairError::Reused
        );
        assert!(
            p.access(&r3.credential).is_none(),
            "reason: late_resend_kept"
        );
        // a first redeem without a nonce never matches a later one with a nonce
        let c4 = p.create_code().unwrap();
        let r4 = p.redeem_with(&c4.code, false, true, None).unwrap();
        assert_eq!(
            p.redeem_with(&c4.code, false, true, Some(n)).unwrap_err(),
            PairError::Reused
        );
        assert!(p.access(&r4.credential).is_none());
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

    /// SPEC §19f: ctl web swaps its own expired, unused code at most MAX_ROTATIONS times; the
    /// page that shows the old code gets the new one (through every swap), nobody else does,
    /// and the three failures are told apart.
    #[test]
    fn an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let first = p.create_code_for(true).unwrap();
        assert!(first.expires_at_ms > 0);
        // not yet expired: no swap
        assert_eq!(
            p.create_code_next(true, Some(&first.pair_id)).unwrap_err(),
            PairError::NotFound,
            "reason: live_code_swapped"
        );
        // still live: the same code and its end
        let same = p.next_code(&first.code).unwrap().unwrap();
        assert_eq!(same.code, first.code, "reason: live_code_not_given_back");
        assert_eq!(same.expires_in, CODE_TTL.as_secs());
        advance(&o, CODE_TTL);
        assert_eq!(p.redeem(&first.code).unwrap_err(), PairError::Expired);
        // a code redeemed (here: revoked as expired) is not swapped; a fresh one that ran out is
        assert!(p.create_code_next(true, Some(&first.pair_id)).is_err());
        let c = p.create_code_for(true).unwrap();
        advance(&o, CODE_TTL);
        // ran out, not swapped yet: nothing new
        assert!(p.next_code(&c.code).unwrap().is_none());
        let mut old_id = c.pair_id.clone();
        for i in 1..=MAX_ROTATIONS {
            let n = p.create_code_next(true, Some(&old_id)).unwrap();
            // the page with the first code follows every swap to the live one
            let got = p.next_code(&c.code).unwrap().unwrap();
            assert_eq!(got.code, n.code, "reason: page_not_given_new_code {i}");
            assert!(got.expires_in <= CODE_TTL.as_secs());
            // a second swap of the same old code: no
            assert!(p.create_code_next(true, Some(&old_id)).is_err());
            advance(&o, CODE_TTL);
            old_id = n.pair_id;
        }
        assert_eq!(
            p.create_code_next(true, Some(&old_id)).unwrap_err(),
            PairError::NotFound,
            "reason: more_than_max_rotations"
        );
        // a page code (not the terminal's) is never swapped
        let page_code = p.create_code_for(false).unwrap();
        advance(&o, CODE_TTL);
        assert!(p.create_code_next(true, Some(&page_code.pair_id)).is_err());
        // a code nobody made: a failure, as in redeem
        assert_eq!(p.next_code("00000-00000").unwrap_err(), PairError::Failed);
        assert_eq!(p.redeem("00000-00000").unwrap_err(), PairError::Failed);
    }

    #[test]
    fn expired_code_never_redeems_even_racing_the_expiry() {
        let (clock, o) = fake();
        let p = on_with(clock);
        let c = p.create_code().unwrap();
        advance(&o, CODE_TTL - Duration::from_millis(1));
        assert_eq!(p.view(&c.pair_id).unwrap().state, PairState::Issued);
        advance(&o, Duration::from_millis(1));
        // SPEC §19f: said apart from a wrong code
        assert_eq!(p.redeem(&c.code).unwrap_err(), PairError::Expired);
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
