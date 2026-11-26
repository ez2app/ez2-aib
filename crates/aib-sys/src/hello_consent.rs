//! B2 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): the owner check on Windows
//! is a Windows Hello gesture only, asked at the moment aibd needs the owner. No key is made
//! (the laptop's Passport provider refused aibd's P-256 key: 0x80090027), nothing is cached,
//! nothing is written: every ask shows the window again, and the answer stays inside this
//! process (`Windows.Security.Credentials.UI.UserConsentVerifier`).
//!
//! The window is tied to this process's foreground window when it has one
//! (`IUserConsentVerifierInterop::RequestVerificationForWindowAsync`), else asked without one.
//! The outcome words are [`crate::hello::consent_outcome`]'s, tested on every platform.

use crate::hello::{consent_available, consent_outcome, run_limited, HelloError};
use windows::core::HSTRING;
use windows::Security::Credentials::UI::{UserConsentVerifier, UserConsentVerifierAvailability};
use windows::Win32::Foundation::HWND;
use windows::Win32::System::WinRT::IUserConsentVerifierInterop;

/// One Hello window at a time (as the key path's).
static WINDOW_OPEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn os(e: &windows::core::Error) -> HelloError {
    HelloError::Os(e.code().0 as u32)
}

/// Whether Windows Hello (PIN, face, fingerprint) is set up for this user here; no window.
pub fn available() -> Result<(), HelloError> {
    crate::hello::interactive_session()?;
    let a: UserConsentVerifierAvailability = UserConsentVerifier::CheckAvailabilityAsync()
        .and_then(|op| op.join())
        .map_err(|e| os(&e))?;
    consent_available(a.0)
}

/// The foreground window when it is this process's own; null otherwise.
fn own_window() -> HWND {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId,
    };
    // SAFETY: no arguments; returns a handle or null
    let w = unsafe { GetForegroundWindow() };
    if w.is_null() {
        return HWND(std::ptr::null_mut());
    }
    let mut pid = 0u32;
    // SAFETY: a window handle just read and a live u32 for its process id
    unsafe { GetWindowThreadProcessId(w, &mut pid) };
    if pid == std::process::id() {
        HWND(w)
    } else {
        HWND(std::ptr::null_mut())
    }
}

/// Ask the owner now; blocks until Windows Hello answers. `Ok` only for a verified gesture.
pub fn ask(reason: &str) -> Result<(), HelloError> {
    if reason.is_empty() || reason.contains('\0') {
        return Err(HelloError::BadArg("prompt text"));
    }
    crate::hello::interactive_session()?;
    let msg = HSTRING::from(reason);
    let w = own_window();
    // both arms are the same IAsyncOperation<UserConsentVerificationResult> (the interop
    // call takes its type from the first arm)
    let op = if w.0.is_null() {
        UserConsentVerifier::RequestVerificationAsync(&msg).map_err(|e| os(&e))?
    } else {
        let interop: IUserConsentVerifierInterop =
            windows::core::factory::<UserConsentVerifier, IUserConsentVerifierInterop>()
                .map_err(|e| os(&e))?;
        // SAFETY: our own window handle and a live HSTRING
        unsafe { interop.RequestVerificationForWindowAsync(w, &msg) }.map_err(|e| os(&e))?
    };
    let r = op.join().map_err(|e| os(&e))?;
    consent_outcome(r.0)
}

/// [`ask`] that gives up with [`HelloError::TimedOut`] after `limit` (the window may stay up
/// until Windows closes it; a second ask meanwhile is [`HelloError::StillOpen`]).
pub fn ask_until(reason: &str, limit: std::time::Duration) -> Result<(), HelloError> {
    let r = reason.to_string();
    run_limited(&WINDOW_OPEN, limit, move || ask(&r))
}

/// W0 (owner at the laptop): `aib_sys-*.exe --ignored --exact hello_consent::w0::w0_c1_ask_once
/// --nocapture`. Prints availability, then asks once: answer it, then run again and cancel.
#[cfg(test)]
mod w0 {
    #![allow(clippy::unwrap_used, clippy::print_stdout)] // owner-present runs: print what happened
    use super::*;

    #[test]
    #[ignore = "W0: owner at the Windows laptop"]
    fn w0_c1_ask_once() {
        println!("W0-C1 available: {:?}", available());
        let r = ask_until(
            "W0-C1 測試:AIB 要確認是你本人(這行字有沒有出現?)",
            std::time::Duration::from_secs(120),
        );
        println!(
            "W0-C1 ask: {r:?} code={}",
            r.as_ref().err().map_or("ok", |e| e.code())
        );
    }
}
