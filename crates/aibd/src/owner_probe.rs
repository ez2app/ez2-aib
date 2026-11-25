//! `aibd ctl owner-check probe` (DESIGN-persona-propose-osverify-c2f0b-1123 r3 §8, S10-2 first
//! part; f163c REVIEW r3): what this machine's owner check would be, read without asking
//! anyone. On macOS: LocalAuthentication's `canEvaluatePolicy` for biometrics and for the
//! owner, and `biometryType`. Nothing is evaluated, no key is made, no window opens.
//!
//! It exists to measure, on every Mac (a MacBook open and closed, a Mac mini with and without
//! a Touch ID keyboard), what tells "no fingerprint hardware" from "fingerprint not usable
//! now", before S10-2 decides when the pairing key may take the login password. Until then it
//! changes nothing: the key stays biometry-current-set.
//!
//! The words in [`Class`] are a first reading of Apple's documented codes, printed beside the
//! raw numbers; the rule is fixed only after the measurements (r4).

use serde_json::{json, Value};

/// What a probe reads. [`Device::evaluate`] is there so a test can see the probe never asks
/// the owner (f163c N1); no probe path calls it.
pub trait Device {
    /// `LABiometryType`: 0 none, 1 Touch ID, 2 Face ID, 4 Optic ID.
    fn biometry_type(&self) -> isize;
    fn can_biometrics(&self) -> Result<(), isize>;
    fn can_owner(&self) -> Result<(), isize>;
    /// Asking the owner (`evaluatePolicy`): never from here.
    fn evaluate(&self) -> Result<(), isize>;
}

/// `LAErrorBiometryNotAvailable`.
pub const NOT_AVAILABLE: isize = -6;
/// `LAErrorBiometryNotEnrolled`.
pub const NOT_ENROLLED: isize = -7;
/// `LAErrorBiometryLockout`.
pub const LOCKED_OUT: isize = -8;
/// `LAErrorPasscodeNotSet`.
pub const NO_PASSCODE: isize = -5;

/// A first reading of what the device said.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// Touch ID there and a finger enrolled.
    Fingerprint,
    /// Touch ID there, no finger enrolled.
    NotEnrolled,
    /// Touch ID reported but not usable now (a closed lid, a keyboard away, locked out).
    NotNow,
    /// No biometry reported at all; the login password works.
    PasswordOnly,
    /// Neither: no owner check on this machine.
    None,
}

impl Class {
    pub fn word(self) -> &'static str {
        match self {
            Class::Fingerprint => "fingerprint",
            Class::NotEnrolled => "not_enrolled",
            Class::NotNow => "not_now",
            Class::PasswordOnly => "password_only",
            Class::None => "none",
        }
    }
}

pub fn class(ty: isize, bio: Result<(), isize>, owner: Result<(), isize>) -> Class {
    match (ty, bio, owner) {
        (_, _, Err(_)) => Class::None,
        (0, _, Ok(())) => Class::PasswordOnly,
        (_, Ok(()), Ok(())) => Class::Fingerprint,
        (_, Err(NOT_ENROLLED), Ok(())) => Class::NotEnrolled,
        (_, Err(_), Ok(())) => Class::NotNow,
    }
}

/// [`class`] of what `d` says now, asking nobody (the same reads as [`probe`]).
pub fn class_of(d: &dyn Device) -> Class {
    let bio = d.can_biometrics();
    let owner = d.can_owner();
    class(d.biometry_type(), bio, owner)
}

/// Which pairing key `aibd ctl pair-key init` makes (DESIGN-s10-2b-nofinger-c2f0b-1125 r1
/// §4a): the fingerprint key where Touch ID works; the login-password key only where the Mac
/// reports no biometry at all. Touch ID there but not usable now (a closed lid, a keyboard
/// away) makes nothing: a password key made then would outlive the reason. There is no flag
/// to force the password key: an AI types a flag as easily as the owner.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyKind {
    Finger,
    LoginPassword,
}

pub fn key_kind(c: Class) -> Result<KeyKind, &'static str> {
    match c {
        Class::Fingerprint => Ok(KeyKind::Finger),
        Class::PasswordOnly => Ok(KeyKind::LoginPassword),
        Class::NotNow => Err("owner_check_finger_not_now"),
        Class::NotEnrolled => Err("owner_check_finger_not_enrolled"),
        Class::None => Err("owner_check_none"),
    }
}

/// How the pairing key is asked to sign, by what the owner check reads at that moment
/// (DESIGN-s10-2b-nofinger r1 §7a): where Touch ID works, a window with the finger only
/// whatever the key's ACL (a key swapped for a login-password one still needs the finger);
/// Touch ID there but not usable now: nothing, never the password instead; no biometry: the
/// window the key's own ACL gives.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    FingerOnly,
    AsTheKeyAllows,
}

pub fn route(c: Class) -> Result<Route, &'static str> {
    Ok(match key_kind(c)? {
        KeyKind::Finger => Route::FingerOnly,
        KeyKind::LoginPassword => Route::AsTheKeyAllows,
    })
}

fn said(r: Result<(), isize>) -> Value {
    match r {
        Ok(()) => json!("ok"),
        Err(code) => json!(code),
    }
}

/// The one line `aibd ctl owner-check probe` prints.
pub fn probe(d: &dyn Device) -> Value {
    let bio = d.can_biometrics();
    let owner = d.can_owner();
    // biometryType is set only after a canEvaluatePolicy (Apple): read last
    let ty = d.biometry_type();
    json!({
        "biometry_type": ty,
        "biometrics": said(bio),
        "owner": said(owner),
        "class": class(ty, bio, owner).word(),
        "class_is_final": false,
    })
}

/// This Mac's LocalAuthentication.
#[cfg(target_os = "macos")]
pub struct Mac {
    state: aib_sys::presence_macos::OwnerCheckState,
}

#[cfg(target_os = "macos")]
impl Mac {
    pub fn read() -> Mac {
        Mac {
            state: aib_sys::presence_macos::owner_check_state(),
        }
    }
}

#[cfg(target_os = "macos")]
impl Device for Mac {
    fn biometry_type(&self) -> isize {
        self.state.biometry_type
    }
    fn can_biometrics(&self) -> Result<(), isize> {
        self.state.biometrics
    }
    fn can_owner(&self) -> Result<(), isize> {
        self.state.owner
    }
    fn evaluate(&self) -> Result<(), isize> {
        // a probe never asks the owner; there is no way to it from here
        Err(-1004)
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Fake {
        ty: isize,
        bio: Result<(), isize>,
        owner: Result<(), isize>,
        asked: AtomicUsize,
    }

    impl Device for Fake {
        fn biometry_type(&self) -> isize {
            self.ty
        }
        fn can_biometrics(&self) -> Result<(), isize> {
            self.bio
        }
        fn can_owner(&self) -> Result<(), isize> {
            self.owner
        }
        fn evaluate(&self) -> Result<(), isize> {
            self.asked.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }

    fn fake(ty: isize, bio: Result<(), isize>, owner: Result<(), isize>) -> Fake {
        Fake {
            ty,
            bio,
            owner,
            asked: AtomicUsize::new(0),
        }
    }

    /// f163c N1: the probe never asks the owner.
    #[test]
    fn a_probe_never_asks_the_owner() {
        for f in [
            fake(1, Ok(()), Ok(())),
            fake(0, Err(NOT_AVAILABLE), Ok(())),
            fake(1, Err(NOT_ENROLLED), Ok(())),
            fake(0, Err(NOT_AVAILABLE), Err(NO_PASSCODE)),
        ] {
            let v = probe(&f);
            let c = class_of(&f);
            assert_eq!(f.asked.load(Ordering::SeqCst), 0, "reason: asked {v} {c:?}");
        }
    }

    #[test]
    fn the_line_says_the_raw_numbers_and_a_first_reading() {
        let v = probe(&fake(1, Err(NOT_AVAILABLE), Ok(())));
        assert_eq!(v["biometry_type"], 1);
        assert_eq!(v["biometrics"], NOT_AVAILABLE);
        assert_eq!(v["owner"], "ok");
        assert_eq!(v["class"], "not_now");
        assert_eq!(v["class_is_final"], false);
    }

    #[test]
    fn first_readings() {
        assert_eq!(class(1, Ok(()), Ok(())), Class::Fingerprint);
        assert_eq!(class(1, Err(NOT_ENROLLED), Ok(())), Class::NotEnrolled);
        assert_eq!(class(1, Err(NOT_AVAILABLE), Ok(())), Class::NotNow);
        assert_eq!(class(1, Err(LOCKED_OUT), Ok(())), Class::NotNow);
        assert_eq!(class(0, Err(NOT_AVAILABLE), Ok(())), Class::PasswordOnly);
        assert_eq!(class(0, Err(NOT_AVAILABLE), Err(NO_PASSCODE)), Class::None);
        assert_eq!(class(1, Ok(()), Err(NO_PASSCODE)), Class::None);
    }

    /// S10-2b-1 §4a: the login-password key only where no biometry is reported; nothing
    /// while Touch ID is there but not usable, not enrolled, or no owner check at all.
    #[test]
    fn the_password_key_only_where_there_is_no_touch_id() {
        assert_eq!(key_kind(Class::Fingerprint), Ok(KeyKind::Finger));
        assert_eq!(key_kind(Class::PasswordOnly), Ok(KeyKind::LoginPassword));
        assert_eq!(key_kind(Class::NotNow), Err("owner_check_finger_not_now"));
        assert_eq!(
            key_kind(Class::NotEnrolled),
            Err("owner_check_finger_not_enrolled")
        );
        assert_eq!(key_kind(Class::None), Err("owner_check_none"));
        assert_eq!(
            key_kind(class_of(&fake(1, Err(NOT_AVAILABLE), Ok(())))),
            Err("owner_check_finger_not_now")
        );
    }

    /// S10-2b-1 §7a: a Touch ID Mac signs through the finger-only window; Touch ID not usable
    /// now is a refusal, never the password; no biometry: the key's own window.
    #[test]
    fn a_touch_id_mac_signs_with_the_finger_only() {
        assert_eq!(route(Class::Fingerprint), Ok(Route::FingerOnly));
        assert_eq!(route(Class::PasswordOnly), Ok(Route::AsTheKeyAllows));
        assert_eq!(route(Class::NotNow), Err("owner_check_finger_not_now"));
        assert_eq!(
            route(Class::NotEnrolled),
            Err("owner_check_finger_not_enrolled")
        );
        assert_eq!(route(Class::None), Err("owner_check_none"));
    }
}
