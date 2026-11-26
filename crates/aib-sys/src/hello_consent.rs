//! B2 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): the owner check on Windows
//! is a Windows Hello gesture only, asked at the moment aibd needs the owner. No key is made
//! (the laptop's Passport provider refused aibd's P-256 key: 0x80090027), nothing is cached,
//! nothing is written: every ask shows the window again, and the answer stays inside this
//! process (`Windows.Security.Credentials.UI.UserConsentVerifier`).
//!
//! The window is tied to a window of aibd's own (ebab8 11-26 11:56; Microsoft: a desktop
//! program asks with `IUserConsentVerifierInterop::RequestVerificationForWindowAsync`): aibd runs
//! in the background and never has the foreground window, so each ask raises [`OwnerWindow`], a
//! small always-on-top window saying AIB is asking, on a thread of its own, hands its handle to
//! the interop call and closes it when Windows answers. When it cannot be made the ask goes
//! without a window (`RequestVerificationAsync`), as before.
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

/// The words on [`OwnerWindow`]: only aibd's, never a request's.
pub const OWNER_WINDOW_TITLE: &str = "AIB 正在請你確認本人 · 請看 Windows 安全性視窗";

/// A small always-on-top window of this process for the Hello window to belong to, alive on its
/// own thread (which pumps its messages) until dropped.
struct OwnerWindow {
    hwnd: isize,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// Its window procedure: quits the thread's message loop when the window is destroyed.
unsafe extern "system" fn owner_proc(
    h: windows_sys::Win32::Foundation::HWND,
    m: u32,
    w: windows_sys::Win32::Foundation::WPARAM,
    l: windows_sys::Win32::Foundation::LPARAM,
) -> windows_sys::Win32::Foundation::LRESULT {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DefWindowProcW, PostQuitMessage, WM_DESTROY,
    };
    if m == WM_DESTROY {
        // SAFETY: no arguments beyond the exit code
        unsafe { PostQuitMessage(0) };
        return 0;
    }
    // SAFETY: the arguments Windows gave this procedure, passed on unchanged
    unsafe { DefWindowProcW(h, m, w, l) }
}

impl OwnerWindow {
    /// The window, or none when it could not be made (the ask then goes without one).
    fn raise() -> Option<OwnerWindow> {
        let (tx, rx) = std::sync::mpsc::channel::<isize>();
        let thread = std::thread::Builder::new()
            .name("aib-hello-owner".into())
            .spawn(move || owner_thread(&tx))
            .ok()?;
        // named exception (ADR 0005): a bounded wait for the window's own thread to say it made
        // the window, inside the one Hello ask, as run_limited waits for the answer
        #[allow(clippy::disallowed_methods)]
        let made = rx.recv_timeout(std::time::Duration::from_secs(5));
        match made {
            Ok(h) if h != 0 => Some(OwnerWindow {
                hwnd: h,
                thread: Some(thread),
            }),
            _ => {
                let _ = thread.join();
                None
            }
        }
    }
}

impl Drop for OwnerWindow {
    fn drop(&mut self) {
        use windows_sys::Win32::UI::WindowsAndMessaging::{PostMessageW, WM_CLOSE};
        // SAFETY: our own window's handle; a WM_CLOSE posted to its thread's queue
        unsafe { PostMessageW(self.hwnd as _, WM_CLOSE, 0, 0) };
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// [`OwnerWindow`]'s thread: make the window, send its handle (0 when it failed), pump its
/// messages until it is destroyed.
fn owner_thread(tx: &std::sync::mpsc::Sender<isize>) {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DispatchMessageW, GetMessageW, GetSystemMetrics, RegisterClassW,
        SetForegroundWindow, TranslateMessage, MSG, SM_CXSCREEN, WNDCLASSW, WS_CAPTION,
        WS_EX_TOPMOST, WS_POPUP, WS_VISIBLE,
    };
    let wide = |s: &str| {
        s.encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>()
    };
    let class = wide("AibHelloOwner");
    let title = wide(OWNER_WINDOW_TITLE);
    // SAFETY: no module name: this program's own handle
    let hinst = unsafe { GetModuleHandleW(null()) };
    // SAFETY: WNDCLASSW is plain data; all-zero is a valid value
    let mut wc: WNDCLASSW = unsafe { std::mem::zeroed() };
    wc.lpfnWndProc = Some(owner_proc);
    wc.hInstance = hinst;
    wc.lpszClassName = class.as_ptr();
    // SAFETY: a class structure and name that live across the call (a second register of the
    // same class fails harmlessly: the first one stays)
    unsafe { RegisterClassW(&wc) };
    // SAFETY: no arguments
    let cx = unsafe { GetSystemMetrics(SM_CXSCREEN) };
    let (w, h) = (560, 60);
    // SAFETY: the class registered above and strings that live across the call
    let hwnd = unsafe {
        CreateWindowExW(
            WS_EX_TOPMOST,
            class.as_ptr(),
            title.as_ptr(),
            WS_POPUP | WS_CAPTION | WS_VISIBLE,
            ((cx - w) / 2).max(0),
            40,
            w,
            h,
            null_mut(),
            null_mut(),
            hinst,
            null(),
        )
    };
    let _ = tx.send(hwnd as isize);
    if hwnd.is_null() {
        return;
    }
    // SAFETY: our own window; Windows may refuse a background process the foreground
    unsafe { SetForegroundWindow(hwnd) };
    // SAFETY: MSG is plain data; all-zero is a valid value
    let mut msg: MSG = unsafe { std::mem::zeroed() };
    // SAFETY: (loop) this thread's own queue and a live MSG; ends at WM_QUIT (0) or an error (-1)
    while unsafe { GetMessageW(&mut msg, null_mut(), 0, 0) } > 0 {
        // SAFETY: a message just read from this thread's queue
        unsafe { TranslateMessage(&msg) };
        // SAFETY: the same message
        unsafe { DispatchMessageW(&msg) };
    }
}

/// Ask the owner now; blocks until Windows Hello answers. `Ok` only for a verified gesture.
pub fn ask(reason: &str) -> Result<(), HelloError> {
    if reason.is_empty() || reason.contains('\0') {
        return Err(HelloError::BadArg("prompt text"));
    }
    crate::hello::interactive_session()?;
    let msg = HSTRING::from(reason);
    // lives until Windows answers: the Hello window belongs to it
    let owner = OwnerWindow::raise();
    // both arms are the same IAsyncOperation<UserConsentVerificationResult> (the interop
    // call takes its type from the first arm)
    let op = match &owner {
        None => UserConsentVerifier::RequestVerificationAsync(&msg).map_err(|e| os(&e))?,
        Some(o) => {
            let interop: IUserConsentVerifierInterop =
                windows::core::factory::<UserConsentVerifier, IUserConsentVerifierInterop>()
                    .map_err(|e| os(&e))?;
            // SAFETY: our own live window's handle and a live HSTRING
            unsafe { interop.RequestVerificationForWindowAsync(HWND(o.hwnd as _), &msg) }
                .map_err(|e| os(&e))?
        }
    };
    let r = op.join().map_err(|e| os(&e))?;
    drop(owner);
    consent_outcome(r.0)
}

/// [`ask`] that gives up with [`HelloError::TimedOut`] after `limit` (the window may stay up
/// until Windows closes it; a second ask meanwhile is [`HelloError::StillOpen`]).
pub fn ask_until(reason: &str, limit: std::time::Duration) -> Result<(), HelloError> {
    let r = reason.to_string();
    run_limited(&WINDOW_OPEN, limit, move || ask(&r))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    /// ebab8 11-26 11:56 (ForWindow): the Hello window gets a window of aibd's own to belong
    /// to, and that window is gone once the ask is over (no window left behind, the thread
    /// ended). Makes and closes a plain window only; no Hello window is raised.
    #[test]
    fn the_hello_window_gets_an_own_window_that_closes_after() {
        use windows_sys::Win32::UI::WindowsAndMessaging::{GetWindowThreadProcessId, IsWindow};
        let o = OwnerWindow::raise().expect("reason: no_owner_window");
        let h = o.hwnd;
        // SAFETY: a handle just made by this process (IsWindow only reads)
        let alive = unsafe { IsWindow(h as _) };
        assert_ne!(alive, 0, "reason: owner_window_not_alive");
        let mut pid = 0u32;
        // SAFETY: the same handle and a live u32
        unsafe { GetWindowThreadProcessId(h as _, &mut pid) };
        assert_eq!(pid, std::process::id(), "reason: owner_window_not_ours");
        drop(o);
        // SAFETY: the handle, now closed (IsWindow only reads)
        let still = unsafe { IsWindow(h as _) };
        assert_eq!(still, 0, "reason: owner_window_left_open");
        assert!(!OWNER_WINDOW_TITLE.is_empty());
    }
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
