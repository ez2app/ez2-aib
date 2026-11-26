//! Pairing confirmed by the owner's fingerprint (DESIGN-pair-touchid-7b81e-1119 r3; 5d33a
//! CHECK-pair-touchid-5d33a-1119 「可以寫碼」; BOUNDARY-webonly §3): a page credential is
//! confirmed only when aibd itself has the owner sign with a key that asks for the fingerprint.
//! A same-account AI can type the code, tick the box and answer y; it cannot press the finger.
//!
//! - The key: a Secure Enclave signing key made by `aibd ctl pair-key init` with the owner there
//!   (aib-sys `presence_macos`, ACL biometry-current-set). Its blob and public key sit in
//!   `<data>/pair-key/` as `key.blob` / `key.pub`, owned by this account and written by nobody
//!   else.
//! - Checked at every start ([`check`]), all three or pairing is refused (never the y / tick):
//!   the device reports the stored public key; the key refuses to sign without the owner
//!   (`probe`, the registry's item 6 trick: a key an AI swapped in without the ACL signs
//!   silently ⇒ `pair_key_weak`); the files are this account's and not writable by others.
//! - Asking ([`KeyPresence::ask`]): sign the binding (pair id, short code, who it goes to, the
//!   page's nonce digest) with the reason [`reason`] composed here, then verify the signature
//!   with the stored public key. Only then is the pairing confirmed.
//! - The window says what and who (5d33a §6-3): the short code, built-in page / your folder's
//!   page, who asked (the terminal's `aibd ctl web`, or the page re-confirming an action), and
//!   「本機鑰(未登記)」 (§6-1: Presence level until the T135-b registry, then HardwareSigned).
//!   Nothing in it comes from a request.
//! - Default: off (`AIBD_PAIRING=touchid` turns it on) until 0eed7 has pressed it on a real Mac
//!   with aibd under launchd (5d33a L1).

use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Length of an uncompressed P-256 public key (0x04 ‖ x ‖ y).
pub const PUBLIC_KEY_LEN: usize = 65;

/// Who asked for the window (5d33a §6-3), set by aibd from the call path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Who {
    /// The one line, `aibd ctl web`.
    Terminal,
    /// The page asking again, for a pairing carried over an update (BOUNDARY r3 §3). A yes lifts
    /// the whole page to this run's full rights until its own end, so the window says that, and
    /// the action only as what started it (5d33a REVIEW-touchid-8ec571dc L1): `act` from aibd's
    /// own list ([`crate::pairing::RECONFIRM_ACTS`]), `until` composed by aibd from the pairing's
    /// end. Nothing the page chose (no seat) reaches the window.
    Reconfirm { act: &'static str, until: String },
}

/// What the fingerprint window says. Only aibd's own values: the short code it made, where the
/// credential goes, who asked.
pub fn reason(who: &Who, short_code: &str, user_page: bool) -> String {
    let to = if user_page {
        "你資料夾的頁(只能打給 safe 席)"
    } else {
        "內建頁"
    };
    match who {
        Who::Terminal => format!(
            "aibd 配對網頁:短碼 {short_code};給 {to};由 終端機 aibd ctl web 叫出。本機鑰(未登記)"
        ),
        // a carried pairing has no short code (it was made before the update)
        Who::Reconfirm { act, until } => format!(
            "aibd 網頁重新確認:升級這頁到本輪完整權限,到 {until};起因:{act};換版前延續的配對,給 {to}。本機鑰(未登記)"
        ),
    }
}

/// The plain sentence on top of a pairing window (Andy 11-21 01:44: 「最上方要用真人容易了解的
/// 簡易描述，下方再接原來的內容」): what a yes does. Only aibd's own values.
pub fn plain(who: &Who, user_page: bool) -> String {
    let to = if user_page {
        "你資料夾的頁"
    } else {
        "內建頁"
    };
    match who {
        Who::Terminal => format!("讓這個瀏覽器分頁配對成 AIB 的{to}"),
        Who::Reconfirm { act, until } => {
            format!("讓這一頁繼續用完整權限到 {until}(起因:{act})")
        }
    }
}

/// Who the pairing is for, the window's second line (ebab8 11-23 05:47: the pairing window is the
/// same four lines as an approval's, [`crate::owner_ok::window_text`]). Only aibd's own values.
pub fn object(who: &Who, user_page: bool) -> String {
    let to = if user_page {
        "你資料夾的頁(只能打給 safe 席)"
    } else {
        "內建頁"
    };
    match who {
        Who::Terminal => format!("這個瀏覽器分頁 → {to}"),
        Who::Reconfirm { .. } => format!("這一頁 → {to}"),
    }
}

/// The whole pairing window, four lines as an approval's (ebab8 11-23 05:47; ORDER-touchhud 2):
/// what a yes does ([`plain`], the same words the countdown and the page card show), for whom
/// ([`object`]), the code the countdown beside it shows and until when, then [`reason`] as it
/// always said (Andy 11-21 01:42 every window counts down; 519d0 05:49). The words are only
/// shown; the key signs [`binding`], not these.
pub fn window(who: &Who, short_code: &str, user_page: bool, until: &str, code: &str) -> String {
    format!(
        "{}\n對象:{}\n對照碼 {code} · {until},過了這張就作廢\n{}",
        plain(who, user_page),
        object(who, user_page),
        reason(who, short_code, user_page)
    )
}

/// How many lines [`window`] has, as [`crate::owner_ok::WINDOW_LINES`].
pub const WINDOW_LINES: usize = 4;

/// When a pairing that has `left` to live ends, for the window: local `HH:MM` from `date(1)`
/// (the workspace forbids `unsafe`, so no localtime_r; as aib-seatops nowline), or
/// 「還剩 N 小時 M 分」 when `date` cannot say. Only aibd's own clock goes in.
pub fn until_text(left: std::time::Duration) -> String {
    let rel = || {
        let m = left.as_secs() / 60;
        format!("還剩 {} 小時 {} 分", m / 60, m % 60)
    };
    let Ok(now) = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) else {
        return rel();
    };
    let at = (now + left).as_secs();
    // BSD date takes -r EPOCH; GNU takes -d @EPOCH (its -r names a file)
    let args: [String; 2] = if cfg!(target_os = "macos") {
        ["-r".into(), at.to_string()]
    } else {
        ["-d".into(), format!("@{at}")]
    };
    let out = std::process::Command::new("date")
        .args(args)
        .arg("+%H:%M")
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let t = String::from_utf8_lossy(&o.stdout).trim().to_string();
            let ok = t.len() == 5
                && t.as_bytes()[2] == b':'
                && t.bytes()
                    .enumerate()
                    .all(|(i, b)| i == 2 || b.is_ascii_digit());
            if ok {
                t
            } else {
                rel()
            }
        }
        _ => rel(),
    }
}

/// Is `t` a clock's `HH:MM:SS`?
pub fn clock_ok(t: &str) -> bool {
    t.len() == 8
        && t.bytes().enumerate().all(|(i, b)| {
            if i == 2 || i == 5 {
                b == b':'
            } else {
                b.is_ascii_digit()
            }
        })
}

/// This machine's local `HH:MM:SS` at the unix second `at`, from `date(1)` as [`until_text`];
/// none when `date` cannot say (the window then says the seconds instead).
pub fn clock_text(at: u64) -> Option<String> {
    let args: [String; 2] = if cfg!(target_os = "macos") {
        ["-r".into(), at.to_string()]
    } else {
        ["-d".into(), format!("@{at}")]
    };
    let out = std::process::Command::new("date")
        .args(args)
        .arg("+%H:%M:%S")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let t = String::from_utf8_lossy(&out.stdout).trim().to_string();
    clock_ok(&t).then_some(t)
}

/// The bytes the key signs for one pairing: a version tag, then each part on its own line.
pub fn binding(pair_id: &str, short_code: &str, user_page: bool, nonce_hex: &str) -> Vec<u8> {
    format!("aibd/pair-touch/v1\n{pair_id}\n{short_code}\n{user_page}\n{nonce_hex}\n").into_bytes()
}

/// The owner check aibd asks for; `Ok` only once a signature by the owner's key checked out.
pub trait Presence: Send + Sync {
    fn ask(&self, reason: &str, msg: &[u8]) -> Result<(), &'static str>;
    /// [`Presence::ask`] whose window closes by itself when `limit` passes with no answer:
    /// then [`TIMEOUT`], which is not a no (0eed7 VERIFY-ownertouch-andy M1: a window left
    /// open held the one-window gate for 27 minutes). A presence without a window of its own
    /// answers as `ask` does.
    fn ask_until(
        &self,
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<(), &'static str> {
        let _ = limit;
        self.ask(reason, msg)
    }
    /// [`Presence::ask_until`] that also leaves in `keep` the signature it checked, for a
    /// caller that stores it and checks it again later with [`Presence::verify`] (seat_tick:
    /// DESIGN-drive-in-aibd r3). A presence with no key keeps nothing.
    fn ask_until_keep(
        &self,
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
        keep: &mut Vec<u8>,
    ) -> Result<(), &'static str> {
        keep.clear();
        self.ask_until(reason, msg, limit)
    }
    /// Whether `sig` is the owner's key's signature of `msg`: the key checked at start, never
    /// one read from anywhere else. A presence with no key: never.
    fn verify(&self, msg: &[u8], sig: &[u8]) -> bool {
        let _ = (msg, sig);
        false
    }
    /// Whether a yes comes with the key's signature, which an "always" stores and checks
    /// again: a presence that only checks the person (no key, a password) does not, and then
    /// "always" is never offered (c2f0b REVIEW-netallow-incr L-a).
    fn signs(&self) -> bool {
        false // a presence with no key signs nothing
    }
}

/// The window closed by itself: nobody answered within its limit. Not a refusal.
pub const TIMEOUT: &str = "pair_touch_timeout";

/// Whether a window's end starts the cool-down: only a no does; a yes and a window nobody
/// answered ([`TIMEOUT`]) do not.
pub fn ends_quietly(got: &Result<(), &'static str>) -> bool {
    matches!(got, Ok(()) | Err(TIMEOUT))
}

pub type SharedPresence = Arc<dyn Presence>;

/// The key operations, so tests use a fake device.
pub trait KeyDevice: Send + Sync {
    /// `Ok` only when the key refuses to sign without the owner.
    fn probe(&self, blob: &[u8]) -> Result<(), &'static str>;
    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str>;
    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str>;
    /// [`KeyDevice::sign`] that gives up with [`TIMEOUT`] after `limit`; a device without a
    /// window signs as `sign` does.
    fn sign_until(
        &self,
        blob: &[u8],
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<Vec<u8>, &'static str> {
        let _ = limit;
        self.sign(blob, reason, msg)
    }
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool;
}

/// The key as stored.
#[derive(Clone)]
pub struct KeyFiles {
    pub blob: Vec<u8>,
    pub public: [u8; PUBLIC_KEY_LEN],
}

impl std::fmt::Debug for KeyFiles {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("KeyFiles(<redacted>)")
    }
}

/// `<data>/pair-key`.
pub fn key_dir(data: &Path) -> PathBuf {
    data.join("pair-key")
}

/// A key file this account owns and nobody else may write (5d33a §6-1 third check).
fn own_file(p: &Path) -> Result<Vec<u8>, &'static str> {
    let m = std::fs::symlink_metadata(p).map_err(|_| "pair_key_missing")?;
    if !m.file_type().is_file() {
        return Err("pair_key_not_own");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if m.uid() != nix::unistd::getuid().as_raw() || m.mode() & 0o022 != 0 {
            return Err("pair_key_not_own");
        }
    }
    std::fs::read(p).map_err(|_| "pair_key_missing")
}

/// Reads `key.blob` and `key.pub` from `dir`.
pub fn load(dir: &Path) -> Result<KeyFiles, &'static str> {
    let blob = own_file(&dir.join("key.blob"))?;
    let public: [u8; PUBLIC_KEY_LEN] = own_file(&dir.join("key.pub"))?
        .try_into()
        .map_err(|_| "pair_key_mismatch")?;
    if blob.is_empty() {
        return Err("pair_key_missing");
    }
    Ok(KeyFiles { blob, public })
}

/// The start-up check: the device reports the stored public key, and the key asks for the owner.
pub fn check(dev: &dyn KeyDevice, k: &KeyFiles) -> Result<(), &'static str> {
    if dev.public_key(&k.blob)? != k.public {
        return Err("pair_key_mismatch");
    }
    dev.probe(&k.blob).map_err(|_| "pair_key_weak")
}

/// [`Presence`] by a checked key.
pub struct KeyPresence {
    dev: Box<dyn KeyDevice>,
    key: KeyFiles,
}

impl KeyPresence {
    /// Only after [`check`] passed.
    pub fn new(dev: Box<dyn KeyDevice>, key: KeyFiles) -> Result<KeyPresence, &'static str> {
        check(dev.as_ref(), &key)?;
        Ok(KeyPresence { dev, key })
    }
}

impl Presence for KeyPresence {
    fn ask(&self, reason: &str, msg: &[u8]) -> Result<(), &'static str> {
        let sig = self.dev.sign(&self.key.blob, reason, msg)?;
        if !self.dev.verify(&self.key.public, msg, &sig) {
            return Err("pair_touch_bad_signature");
        }
        Ok(())
    }
    fn ask_until(
        &self,
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<(), &'static str> {
        self.ask_until_keep(reason, msg, limit, &mut Vec::new())
    }
    fn ask_until_keep(
        &self,
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
        keep: &mut Vec<u8>,
    ) -> Result<(), &'static str> {
        keep.clear();
        let sig = self.dev.sign_until(&self.key.blob, reason, msg, limit)?;
        let checked = self.dev.verify(&self.key.public, msg, &sig);
        if !checked {
            return Err("pair_touch_bad_signature");
        }
        *keep = sig;
        Ok(())
    }
    fn verify(&self, msg: &[u8], sig: &[u8]) -> bool {
        let by_the_checked_key = self.dev.verify(&self.key.public, msg, sig);
        !sig.is_empty() && by_the_checked_key
    }
    fn signs(&self) -> bool {
        true // ask_until_keep keeps the signature of every yes
    }
}

/// Sign the way the owner check reads now ([`crate::owner_probe::route`], S10-2b-1): the
/// finger-only window, the key's own window, or nothing. Read again at every signature, so a
/// lid closed after the start is a refusal and never the password.
pub fn routed<T>(
    now: crate::owner_probe::Class,
    finger_only: impl FnOnce() -> Result<T, &'static str>,
    as_the_key_allows: impl FnOnce() -> Result<T, &'static str>,
) -> Result<T, &'static str> {
    use crate::owner_probe::Route;
    match crate::owner_probe::route(now)? {
        Route::FingerOnly => finger_only(),
        Route::AsTheKeyAllows => as_the_key_allows(),
    }
}

/// This Mac's Secure Enclave.
#[cfg(target_os = "macos")]
pub struct MacSe;

#[cfg(target_os = "macos")]
fn se_code(e: aib_sys::presence_macos::SeError) -> &'static str {
    match e {
        aib_sys::presence_macos::SeError::Timeout => TIMEOUT,
        aib_sys::presence_macos::SeError::KeyMismatch => KEY_MISMATCH,
        other => other.code(),
    }
}

/// The fingerprint was given but the pairing key wanted another window: it is not the
/// fingerprint key this Mac should have (S10-2b-1 §8-3). Refused, no second window.
pub const KEY_MISMATCH: &str = "owner_check_key_mismatch";

/// Whether this aibd has read "fingerprint" since it started (3e53b REVIEW-s102b L1). Kept in
/// aibd's memory only: a file would be one more thing an AI of the same account writes.
/// macOS only: Touch ID is read there alone, so elsewhere nothing would set it.
#[cfg(target_os = "macos")]
static SEEN_FINGER: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// What the owner check reads now, remembering a fingerprint seen since aibd started: once
/// Touch ID was there, a later "no biometry" (a Touch ID keyboard taken away may read so) is
/// "not usable now", so a pairing key swapped for a password one never gets the password
/// window while this aibd runs (3e53b REVIEW-s102b L1).
pub fn remembered(
    now: crate::owner_probe::Class,
    seen_finger: &std::sync::atomic::AtomicBool,
) -> crate::owner_probe::Class {
    use crate::owner_probe::Class;
    use std::sync::atomic::Ordering;
    if now == Class::Fingerprint {
        seen_finger.store(true, Ordering::SeqCst);
    }
    let once_had_touch_id = seen_finger.load(Ordering::SeqCst);
    if now == Class::PasswordOnly && once_had_touch_id {
        return Class::NotNow;
    }
    now
}

#[cfg(target_os = "macos")]
fn class_now() -> crate::owner_probe::Class {
    remembered(
        crate::owner_probe::class_of(&crate::owner_probe::Mac::read()),
        &SEEN_FINGER,
    )
}

/// A window with no limit of its own ([`KeyDevice::sign`]) still closes after this on the
/// finger-only path, which only has the limited call.
#[cfg(target_os = "macos")]
const FINGER_ONLY_LONGEST: std::time::Duration = std::time::Duration::from_secs(15 * 60);

#[cfg(target_os = "macos")]
impl KeyDevice for MacSe {
    fn probe(&self, blob: &[u8]) -> Result<(), &'static str> {
        aib_sys::presence_macos::probe(blob).map_err(|e| e.code())
    }
    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
        aib_sys::presence_macos::public_key(blob).map_err(|e| e.code())
    }
    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
        routed(
            class_now(),
            || {
                aib_sys::presence_macos::sign_until_finger_only(
                    blob,
                    reason,
                    msg,
                    FINGER_ONLY_LONGEST,
                )
                .map_err(se_code)
            },
            || aib_sys::presence_macos::sign(blob, reason, msg).map_err(|e| e.code()),
        )
    }
    fn sign_until(
        &self,
        blob: &[u8],
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<Vec<u8>, &'static str> {
        routed(
            class_now(),
            || {
                aib_sys::presence_macos::sign_until_finger_only(blob, reason, msg, limit)
                    .map_err(se_code)
            },
            || aib_sys::presence_macos::sign_until(blob, reason, msg, limit).map_err(se_code),
        )
    }
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
        aib_sys::presence_macos::verify(public, msg, sig) == Ok(true)
    }
}

/// This Windows machine's Hello key (Microsoft Passport key storage provider, aib-sys
/// `hello_ncrypt`). S10-3b-1/2: **unverified on a real Hello machine** until the W sheet
/// (PLAN-ap26-windows-first-c2f0b-1125 §3); only [`KeyDevice::verify`] runs in CI. Every window
/// has a limit (a call without one gets [`WIN_HELLO_LONGEST`]); a window still up refuses the
/// next one.
#[cfg(windows)]
pub struct WinHello;

/// The longest a Hello window from [`KeyDevice::sign`] waits (same as the Mac's finger-only
/// window).
#[cfg(windows)]
const WIN_HELLO_LONGEST: std::time::Duration = std::time::Duration::from_secs(15 * 60);

/// A Hello error as the 1.x word; aibd's own limit is [`TIMEOUT`].
#[cfg(windows)]
fn hello_code(e: aib_sys::hello::HelloError) -> &'static str {
    match e {
        aib_sys::hello::HelloError::TimedOut => TIMEOUT,
        other => other.code(),
    }
}

#[cfg(windows)]
impl WinHello {
    /// S10-3b-2 (3e53b READ-ap26-r2 L): the key is in a TPM. A software Hello key (a PIN on a
    /// machine without a TPM) is not; W-2 reads this as the device's `hardware_bound`.
    pub fn hardware_bound(blob: &[u8]) -> bool {
        aib_sys::hello_ncrypt::hardware_backed(blob)
    }
}

/// B2 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): the owner check on Windows
/// is a Windows Hello gesture asked at that moment, no key (the Passport provider refused
/// aibd's key on the laptop, 0x80090027). Every ask shows the window; nothing is cached or
/// written; a yes is used by the same call that asked. No signature, so [`Presence::signs`]
/// is false and an "always" is never offered (3e53b B2 condition).
#[cfg(windows)]
pub struct WinConsent;

#[cfg(windows)]
impl Presence for WinConsent {
    fn ask(&self, reason: &str, msg: &[u8]) -> Result<(), &'static str> {
        self.ask_until(reason, msg, WIN_HELLO_LONGEST)
    }
    fn ask_until(
        &self,
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<(), &'static str> {
        let _ = msg; // what the yes is for is in `reason`, composed by aibd
        aib_sys::hello_consent::ask_until(reason, limit).map_err(hello_code)
    }
}

#[cfg(windows)]
impl KeyDevice for WinHello {
    fn probe(&self, blob: &[u8]) -> Result<(), &'static str> {
        aib_sys::hello_ncrypt::probe(blob).map_err(|e| e.code())
    }
    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
        aib_sys::hello_ncrypt::public_key(blob).map_err(|e| e.code())
    }
    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
        aib_sys::hello_ncrypt::sign_until(blob, reason, msg, WIN_HELLO_LONGEST).map_err(hello_code)
    }
    fn sign_until(
        &self,
        blob: &[u8],
        reason: &str,
        msg: &[u8],
        limit: std::time::Duration,
    ) -> Result<Vec<u8>, &'static str> {
        aib_sys::hello_ncrypt::sign_until(blob, reason, msg, limit).map_err(hello_code)
    }
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
        aib_sys::hello_ncrypt::verify(public, msg, sig)
    }
}

/// `pair-key init` on Windows (S10-3b-2; 3e53b READ-winhello-wiring W7), written for every
/// platform so its "nothing kept on failure" is tested everywhere: no key files there yet, make
/// the key (`create` gives its blob and public key; it may show Hello), write both files new
/// (never over existing ones), then the start-up [`check`]. Any failure after the key was made
/// removes both files and the key itself (`delete`), so a failed run leaves nothing behind.
pub fn make_pair_key(
    dir: &Path,
    create: impl FnOnce() -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), String>,
    dev: &dyn KeyDevice,
    delete: impl FnOnce(&[u8]),
) -> Result<(), String> {
    use std::io::Write;
    if dir.join("key.blob").exists() || dir.join("key.pub").exists() {
        return Err(format!(
            "a pairing key is already in {}; remove that folder yourself to make a new one",
            dir.display()
        ));
    }
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let (blob, public) = create()?;
    let written = (|| -> Result<(), String> {
        for (name, bytes) in [("key.blob", &blob[..]), ("key.pub", &public[..])] {
            let p = dir.join(name);
            let mut o = std::fs::OpenOptions::new();
            o.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                o.mode(0o600);
            }
            let mut f = o.open(&p).map_err(|e| format!("{}: {e}", p.display()))?;
            f.write_all(bytes)
                .map_err(|e| format!("{}: {e}", p.display()))?;
        }
        let k = load(dir).map_err(String::from)?;
        check(dev, &k).map_err(|why| format!("the new key failed the start-up check ({why})"))
    })();
    if let Err(why) = written {
        let _ = std::fs::remove_file(dir.join("key.blob"));
        let _ = std::fs::remove_file(dir.join("key.pub"));
        delete(&blob);
        return Err(format!("{why}; nothing kept"));
    }
    Ok(())
}

/// Windows (3e53b READ-winhello-wiring W5): the desktop-session gate comes before anything is
/// read, so aibd outside the signed-in user's console session (session 0, ssh, another
/// session) refuses at once — no key read, no Hello window it could never show. Then the
/// stored key and the start-up [`check`], as on macOS. Written for every platform so its order
/// is tested everywhere; only Windows calls it with [`WinHello`].
pub fn presence_after_session(
    session: Result<(), &'static str>,
    data: &Path,
    dev: Box<dyn KeyDevice>,
) -> Result<SharedPresence, &'static str> {
    session?;
    let key = load(&key_dir(data))?;
    Ok(Arc::new(KeyPresence::new(dev, key)?))
}

/// The presence aibd uses at this start: the stored key on macOS when every check passes;
/// otherwise why not (pairing is then refused, never the y / tick).
pub fn presence_here(data: &Path) -> Result<SharedPresence, &'static str> {
    #[cfg(target_os = "macos")]
    {
        let key = load(&key_dir(data))?;
        // read once at start, so a fingerprint seen now is remembered (3e53b REVIEW-s102b L1)
        let _ = class_now();
        Ok(Arc::new(KeyPresence::new(Box::new(MacSe), key)?))
    }
    // B2: a gesture at the desktop, no key; no Hello for this user ⇒ refused (B3: every owner
    // action stays closed, the page shows the set-a-PIN card)
    #[cfg(windows)]
    {
        let _ = data;
        aib_sys::hello_consent::available().map_err(|e| e.code())?;
        Ok(Arc::new(WinConsent))
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = data;
        Err("pair_touch_not_on_this_system")
    }
}

#[cfg(test)]
pub mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::sync::Mutex;

    /// A device whose answers the test sets.
    pub struct FakeDev {
        pub public: [u8; PUBLIC_KEY_LEN],
        pub probe_ok: bool,
        pub sign_ok: bool,
        pub good_sig: bool,
    }

    impl KeyDevice for FakeDev {
        fn probe(&self, _: &[u8]) -> Result<(), &'static str> {
            if self.probe_ok {
                Ok(())
            } else {
                Err("signed without the owner")
            }
        }
        fn public_key(&self, _: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
            Ok(self.public)
        }
        fn sign(&self, _: &[u8], _: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
            if self.sign_ok {
                Ok(msg.to_vec())
            } else {
                Err("user_cancel")
            }
        }
        fn verify(&self, _: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
            self.good_sig && sig == msg
        }
    }

    /// A presence that records each window and answers yes or no.
    #[derive(Default)]
    pub struct Recorder {
        pub yes: bool,
        pub asked: Mutex<Vec<(String, Vec<u8>)>>,
    }

    impl Presence for Recorder {
        fn ask(&self, reason: &str, msg: &[u8]) -> Result<(), &'static str> {
            if let Ok(mut a) = self.asked.lock() {
                a.push((reason.to_string(), msg.to_vec()));
            }
            if self.yes {
                Ok(())
            } else {
                Err("user_cancel")
            }
        }
    }

    fn key() -> KeyFiles {
        KeyFiles {
            blob: vec![1, 2, 3],
            public: [4; PUBLIC_KEY_LEN],
        }
    }

    fn dev(probe_ok: bool, sign_ok: bool, good_sig: bool) -> FakeDev {
        FakeDev {
            public: [4; PUBLIC_KEY_LEN],
            probe_ok,
            sign_ok,
            good_sig,
        }
    }

    #[test]
    fn a_key_that_signs_without_the_owner_or_is_not_the_stored_one_is_refused() {
        assert_eq!(check(&dev(true, true, true), &key()), Ok(()));
        assert_eq!(
            check(&dev(false, true, true), &key()).unwrap_err(),
            "pair_key_weak",
            "reason: weak_key_taken"
        );
        let mut other = dev(true, true, true);
        other.public = [5; PUBLIC_KEY_LEN];
        assert_eq!(
            check(&other, &key()).unwrap_err(),
            "pair_key_mismatch",
            "reason: swapped_key_taken"
        );
        assert!(KeyPresence::new(Box::new(dev(false, true, true)), key()).is_err());
    }

    #[test]
    fn only_a_signature_that_checks_out_is_a_yes() {
        let yes = KeyPresence::new(Box::new(dev(true, true, true)), key()).unwrap();
        assert_eq!(yes.ask("r", b"m"), Ok(()));
        let cancel = KeyPresence::new(Box::new(dev(true, false, true)), key()).unwrap();
        assert!(cancel.ask("r", b"m").is_err());
        let forged = KeyPresence::new(Box::new(dev(true, true, false)), key()).unwrap();
        assert_eq!(
            forged.ask("r", b"m").unwrap_err(),
            "pair_touch_bad_signature",
            "reason: unchecked_signature"
        );
        // the key signs; a presence with no key does not (c2f0b L-a)
        assert!(yes.signs(), "reason: key_presence_said_unsigned");
        let no_key = Recorder {
            yes: true,
            asked: Mutex::new(Vec::new()),
        };
        assert!(!no_key.signs(), "reason: keyless_presence_said_signed");
    }

    #[test]
    fn the_window_says_the_code_where_it_goes_and_who_asked() {
        let r = reason(&Who::Terminal, "123456", false);
        assert_eq!(
            r,
            "aibd 配對網頁:短碼 123456;給 內建頁;由 終端機 aibd ctl web 叫出。本機鑰(未登記)"
        );
        let r = reason(
            &Who::Reconfirm {
                act: "開席",
                until: "23:59".into(),
            },
            "",
            true,
        );
        assert_eq!(
            r,
            "aibd 網頁重新確認:升級這頁到本輪完整權限,到 23:59;起因:開席;換版前延續的配對,給 你資料夾的頁(只能打給 safe 席)。本機鑰(未登記)"
        );
    }

    #[test]
    fn a_pairing_window_says_it_plainly_first_and_then_what_it_always_said() {
        let w = window(
            &Who::Terminal,
            "123456",
            false,
            "請在 01:23:44 前按",
            "123456",
        );
        assert_eq!(
            w,
            "讓這個瀏覽器分頁配對成 AIB 的內建頁\n對象:這個瀏覽器分頁 → 內建頁\n對照碼 123456 · 請在 01:23:44 前按,過了這張就作廢\naibd 配對網頁:短碼 123456;給 內建頁;由 終端機 aibd ctl web 叫出。本機鑰(未登記)"
        );
        assert_eq!(w.lines().count(), WINDOW_LINES, "reason: four_lines");
        assert_eq!(
            WINDOW_LINES,
            crate::owner_ok::WINDOW_LINES,
            "reason: same_as_approval"
        );
        let who = Who::Reconfirm {
            act: "開席",
            until: "23:59".into(),
        };
        let w = window(&who, "", true, "請在 120 秒內按", "AB12");
        let l: Vec<&str> = w.lines().collect();
        assert_eq!(l.len(), WINDOW_LINES, "reason: four_lines");
        assert_eq!(
            l[0], "讓這一頁繼續用完整權限到 23:59(起因:開席)",
            "reason: plain_first"
        );
        assert_eq!(l[0], plain(&who, true), "reason: same_words_as_countdown");
        assert_eq!(l[1], "對象:這一頁 → 你資料夾的頁(只能打給 safe 席)");
        assert_eq!(l[2], "對照碼 AB12 · 請在 120 秒內按,過了這張就作廢");
        assert_eq!(l[3], reason(&who, "", true), "reason: original_kept");
    }

    #[cfg(unix)]
    #[test]
    fn key_files_must_be_this_accounts_and_not_writable_by_others() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        assert_eq!(load(d.path()).unwrap_err(), "pair_key_missing");
        std::fs::write(d.path().join("key.blob"), [1, 2, 3]).unwrap();
        std::fs::write(d.path().join("key.pub"), [4; PUBLIC_KEY_LEN]).unwrap();
        for f in ["key.blob", "key.pub"] {
            std::fs::set_permissions(d.path().join(f), std::fs::Permissions::from_mode(0o600))
                .unwrap();
        }
        assert!(load(d.path()).is_ok());
        std::fs::set_permissions(
            d.path().join("key.pub"),
            std::fs::Permissions::from_mode(0o666),
        )
        .unwrap();
        assert_eq!(
            load(d.path()).unwrap_err(),
            "pair_key_not_own",
            "reason: others_may_write"
        );
        std::fs::set_permissions(
            d.path().join("key.pub"),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
        std::fs::write(d.path().join("key.pub"), [4; 64]).unwrap();
        assert_eq!(load(d.path()).unwrap_err(), "pair_key_mismatch");
    }

    /// S10-3b-1 (3e53b READ-winhello-wiring W5): outside the desktop session nothing is read
    /// and nothing is asked, even with good key files; inside it the key is checked as usual.
    #[cfg(unix)]
    #[test]
    fn outside_the_desktop_session_the_key_is_never_read() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let dir = key_dir(d.path());
        std::fs::create_dir(&dir).unwrap();
        std::fs::write(dir.join("key.blob"), [1, 2, 3]).unwrap();
        std::fs::write(dir.join("key.pub"), [4; PUBLIC_KEY_LEN]).unwrap();
        for f in ["key.blob", "key.pub"] {
            std::fs::set_permissions(dir.join(f), std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        let refused = presence_after_session(
            Err("unavailable"),
            d.path(),
            Box::new(dev(true, true, true)),
        );
        assert_eq!(refused.err(), Some("unavailable"), "reason: session_first");
        let ok = presence_after_session(Ok(()), d.path(), Box::new(dev(true, true, true)));
        assert!(ok.is_ok(), "reason: good_key_in_session");
        let weak = presence_after_session(Ok(()), d.path(), Box::new(dev(false, true, true)));
        assert_eq!(weak.err(), Some("pair_key_weak"), "reason: still_checked");
    }

    /// S10-3b-1: on any machine whose aibd runs outside a desktop session (CI's Windows cell,
    /// an ssh start) the Windows path refuses before reading; elsewhere, with no key files,
    /// it refuses too. Never a presence from an empty data folder.
    #[test]
    fn an_empty_data_folder_gives_no_presence_here() {
        let d = tempfile::tempdir().unwrap();
        assert!(presence_here(d.path()).is_err());
    }

    /// S10-3b-2 (3e53b READ-winhello-wiring W7): a key that fails the start-up check leaves
    /// no files and is deleted; a key that passes is kept; existing files are never replaced;
    /// a key that could not be made leaves no files.
    #[test]
    fn a_pair_key_that_fails_leaves_nothing() {
        let d = tempfile::tempdir().unwrap();
        let dir = key_dir(d.path());
        let made = || Ok((vec![1, 2, 3], [4; PUBLIC_KEY_LEN]));
        let deleted = Mutex::new(Vec::<Vec<u8>>::new());
        let weak = make_pair_key(&dir, made, &dev(false, true, true), |b| {
            deleted.lock().unwrap().push(b.to_vec())
        });
        assert!(
            weak.unwrap_err().contains("nothing kept"),
            "reason: weak_key"
        );
        assert!(!dir.join("key.blob").exists() && !dir.join("key.pub").exists());
        assert_eq!(
            deleted.lock().unwrap().as_slice(),
            &[vec![1, 2, 3]],
            "reason: key_deleted"
        );

        let none = make_pair_key(
            &dir,
            || Err("no_biometry".into()),
            &dev(true, true, true),
            |_| panic!("nothing was made, nothing to delete"),
        );
        assert_eq!(none.unwrap_err(), "no_biometry");
        assert!(!dir.join("key.blob").exists(), "reason: not_made");

        assert!(make_pair_key(&dir, made, &dev(true, true, true), |_| {}).is_ok());
        assert!(load(&dir).is_ok(), "reason: kept");
        let again = make_pair_key(&dir, made, &dev(true, true, true), |_| {
            panic!("an existing key is never touched")
        });
        assert!(
            again.unwrap_err().contains("already"),
            "reason: no_overwrite"
        );
    }

    /// N-webonly-c45: the limit reaches the device, a window nobody answered reads as
    /// [`TIMEOUT`], and only a no starts the cool-down.
    #[test]
    fn the_limit_reaches_the_device_and_a_timeout_is_not_a_no() {
        struct Dev(Mutex<Option<std::time::Duration>>);
        impl KeyDevice for Dev {
            fn probe(&self, _blob: &[u8]) -> Result<(), &'static str> {
                Ok(())
            }
            fn public_key(&self, _blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
                Ok([4; PUBLIC_KEY_LEN])
            }
            fn sign(&self, _b: &[u8], _r: &str, _m: &[u8]) -> Result<Vec<u8>, &'static str> {
                Ok(vec![1])
            }
            fn sign_until(
                &self,
                _b: &[u8],
                _r: &str,
                _m: &[u8],
                limit: std::time::Duration,
            ) -> Result<Vec<u8>, &'static str> {
                *self.0.lock().unwrap() = Some(limit);
                Err(TIMEOUT)
            }
            fn verify(&self, _p: &[u8; PUBLIC_KEY_LEN], _m: &[u8], _s: &[u8]) -> bool {
                true
            }
        }
        let key = KeyFiles {
            blob: vec![1],
            public: [4; PUBLIC_KEY_LEN],
        };
        let dev = std::sync::Arc::new(Dev(Mutex::new(None)));
        struct Shared(std::sync::Arc<Dev>);
        impl KeyDevice for Shared {
            fn probe(&self, b: &[u8]) -> Result<(), &'static str> {
                self.0.probe(b)
            }
            fn public_key(&self, b: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
                self.0.public_key(b)
            }
            fn sign(&self, b: &[u8], r: &str, m: &[u8]) -> Result<Vec<u8>, &'static str> {
                self.0.sign(b, r, m)
            }
            fn sign_until(
                &self,
                b: &[u8],
                r: &str,
                m: &[u8],
                limit: std::time::Duration,
            ) -> Result<Vec<u8>, &'static str> {
                self.0.sign_until(b, r, m, limit)
            }
            fn verify(&self, p: &[u8; PUBLIC_KEY_LEN], m: &[u8], s: &[u8]) -> bool {
                self.0.verify(p, m, s)
            }
        }
        let p = KeyPresence::new(Box::new(Shared(dev.clone())), key).unwrap();
        let limit = std::time::Duration::from_secs(7);
        assert_eq!(p.ask_until("why", b"msg", limit), Err(TIMEOUT));
        assert_eq!(
            *dev.0.lock().unwrap(),
            Some(limit),
            "reason: limit_not_given_to_the_device"
        );
        // the plain ask has no limit and still signs
        assert_eq!(p.ask("why", b"msg"), Ok(()));
        assert!(ends_quietly(&Ok(())) && ends_quietly(&Err(TIMEOUT)));
        for no in [
            "cancel",
            "auth_failed",
            "pair_touch_bad_signature",
            "timeout",
        ] {
            assert!(!ends_quietly(&Err(no)), "reason: {no}_does_not_cool");
        }
    }

    /// S10-2b-1 (DESIGN-s10-2b-nofinger r1 §7a): a Touch ID Mac signs only through the
    /// finger-only window; Touch ID there but not usable now opens no window at all (never
    /// the password); a Mac with no biometry gets the key's own window.
    #[test]
    fn a_signature_goes_the_way_the_owner_check_reads_now() {
        use crate::owner_probe::Class;
        use std::cell::Cell;
        let went = Cell::new("");
        let go = |c: Class| {
            went.set("");
            routed(
                c,
                || {
                    went.set("finger_only");
                    Ok(1)
                },
                || {
                    went.set("as_the_key_allows");
                    Ok(2)
                },
            )
        };
        assert_eq!(go(Class::Fingerprint), Ok(1));
        assert_eq!(went.get(), "finger_only");
        assert_eq!(go(Class::PasswordOnly), Ok(2));
        assert_eq!(went.get(), "as_the_key_allows");
        for (c, why) in [
            (Class::NotNow, "owner_check_finger_not_now"),
            (Class::NotEnrolled, "owner_check_finger_not_enrolled"),
            (Class::None, "owner_check_none"),
        ] {
            assert_eq!(go(c), Err(why));
            assert_eq!(went.get(), "", "reason: {why}_opened_a_window");
        }
    }

    /// 3e53b REVIEW-s102b L1: once aibd read "fingerprint", a later "no biometry" (a Touch ID
    /// keyboard taken away) is "not usable now", never the password window.
    #[test]
    fn a_fingerprint_once_seen_keeps_the_password_window_shut() {
        use crate::owner_probe::Class;
        use std::sync::atomic::AtomicBool;
        let never = AtomicBool::new(false);
        assert_eq!(remembered(Class::PasswordOnly, &never), Class::PasswordOnly);
        let seen = AtomicBool::new(false);
        assert_eq!(remembered(Class::Fingerprint, &seen), Class::Fingerprint);
        assert_eq!(
            remembered(Class::PasswordOnly, &seen),
            Class::NotNow,
            "reason: password_after_touch_id"
        );
        assert_eq!(
            routed(remembered(Class::PasswordOnly, &seen), || Ok(1), || Ok(2)),
            Err("owner_check_finger_not_now")
        );
        assert_eq!(remembered(Class::NotEnrolled, &seen), Class::NotEnrolled);
    }
}
