//! AP26 W-4 (PLAN-ap26-windows-first-c2f0b-1125 §2 W-4, §3 W sheet steps 5–6;
//! PRE-READ-w4-winsys-features-c2f0b-1125, 3e53b 0/0/2/2): owner-present measurements on the
//! Windows laptop, run by `aib-wincheck` from CI-built test binaries with `--ignored
//! --nocapture`. Each prints one `W4-M1` / `W4-M2` JSON line; nothing is written elsewhere.
//!
//! - M1: the system's secure CredUI window (the comparison: the product does not use CredUI,
//!   PLAN §1 待定 1). While it is up, take a screenshot and send synthetic keys from this
//!   desktop.
//! - M2: a window on a desktop of our own (what W-5 would use), excluded from capture. Same
//!   screenshot and synthetic keys from the user's desktop, and once more from a thread that
//!   opened our desktop by name (a same-account program that knows the name).
//!
//! What the owner types is only compared with the fixed test word `test1234`: the result is
//! `exact`, `injected` (our synthetic keys arrived) or `other` — never the text or its length
//! (3e53b condition 3). Buffers are zeroed before they are freed.
#![cfg(windows)]
#![allow(clippy::unwrap_used, clippy::print_stdout, clippy::disallowed_methods)] // owner-present runs

use std::ffi::c_void;
use std::ptr::{null, null_mut};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{GENERIC_ALL, HWND};
use windows_sys::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
    ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CAPTUREBLT, DIB_RGB_COLORS,
    SRCCOPY,
};
use windows_sys::Win32::Security::Credentials::{
    CredUIPromptForWindowsCredentialsW, CredUnPackAuthenticationBufferW, CREDUIWIN_GENERIC,
    CREDUIWIN_SECURE_PROMPT, CREDUI_INFOW,
};
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::StationsAndDesktops::{
    CloseDesktop, CreateDesktopW, GetThreadDesktop, OpenDesktopW, SetThreadDesktop, SwitchDesktop,
    HDESK,
};
use windows_sys::Win32::System::Threading::GetCurrentThreadId;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, SetFocus, INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE,
    VK_RETURN,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetSystemMetrics,
    GetWindowTextW, PeekMessageW, RegisterClassW, SetWindowDisplayAffinity, TranslateMessage,
    ES_AUTOHSCROLL, ES_PASSWORD, MSG, PM_REMOVE, SM_CXSCREEN, SM_CYSCREEN, WDA_EXCLUDEFROMCAPTURE,
    WM_KEYDOWN, WNDCLASSW, WS_BORDER, WS_CHILD, WS_OVERLAPPEDWINDOW, WS_VISIBLE,
};

const TEST_WORD: &str = "test1234";

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Wait without a periodic timer: a receive that nobody answers (measurement code only).
fn pause(ms: u64) {
    let (_keep, rx) = std::sync::mpsc::channel::<()>();
    let _ = rx.recv_timeout(Duration::from_millis(ms));
}

/// What the owner typed, as one word; the text itself is zeroed.
fn judge(mut typed: Vec<u16>) -> &'static str {
    let s = String::from_utf16_lossy(&typed);
    let word = if s == TEST_WORD {
        "exact"
    } else if s.contains("zz") || s.contains("yy") {
        "injected"
    } else {
        "other"
    };
    let mut b = s.into_bytes();
    for x in b.iter_mut() {
        // SAFETY: a live byte of our own buffer
        unsafe { std::ptr::write_volatile(x, 0) };
    }
    for x in typed.iter_mut() {
        // SAFETY: a live u16 of our own buffer
        unsafe { std::ptr::write_volatile(x, 0) };
    }
    word
}

/// One screenshot of the user's desktop: whether the copy worked, and the share of
/// near-black pixels (a secure or other desktop reads black or stale).
fn shot() -> (bool, f64) {
    // SAFETY: GDI calls on handles made here and released here, a buffer sized for the bitmap
    unsafe {
        let (w, h) = (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
        if w <= 0 || h <= 0 {
            return (false, -1.0);
        }
        let screen = GetDC(null_mut());
        if screen.is_null() {
            return (false, -1.0);
        }
        let mem = CreateCompatibleDC(screen);
        let bmp = CreateCompatibleBitmap(screen, w, h);
        let old = SelectObject(mem, bmp);
        let copied = BitBlt(mem, 0, 0, w, h, screen, 0, 0, SRCCOPY | CAPTUREBLT) != 0;
        SelectObject(mem, old);
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = w;
        info.bmiHeader.biHeight = -h;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        let mut px = vec![0u8; w as usize * h as usize * 4];
        let lines = GetDIBits(
            mem,
            bmp,
            0,
            h as u32,
            px.as_mut_ptr().cast(),
            &mut info,
            DIB_RGB_COLORS,
        );
        DeleteObject(bmp);
        DeleteDC(mem);
        ReleaseDC(null_mut(), screen);
        let dark = px
            .chunks_exact(4)
            .filter(|p| u32::from(p[0]) + u32::from(p[1]) + u32::from(p[2]) < 30)
            .count();
        (copied && lines > 0, dark as f64 / (w as f64 * h as f64))
    }
}

/// Synthetic key presses of `text` from this thread's desktop; how many events Windows took.
fn send(text: &str) -> u32 {
    let mut v = Vec::new();
    for u in text.encode_utf16() {
        for up in [0, KEYEVENTF_KEYUP] {
            // SAFETY: INPUT is plain data; all-zero is a valid value
            let mut i: INPUT = unsafe { std::mem::zeroed() };
            i.r#type = INPUT_KEYBOARD;
            i.Anonymous.ki = KEYBDINPUT {
                wVk: 0,
                wScan: u,
                dwFlags: KEYEVENTF_UNICODE | up,
                time: 0,
                dwExtraInfo: 0,
            };
            v.push(i);
        }
    }
    // SAFETY: a live array of v.len() INPUTs, its element size given
    unsafe {
        SendInput(
            v.len() as u32,
            v.as_ptr(),
            std::mem::size_of::<INPUT>() as i32,
        )
    }
}

/// The CredUI buffer's password, judged; the buffer is zeroed, then freed.
fn unpack_and_free(buf: *mut c_void, len: u32) -> &'static str {
    let (mut user, mut dom, mut pw) = ([0u16; 256], [0u16; 256], [0u16; 256]);
    let (mut ul, mut dl, mut pl) = (256u32, 256u32, 256u32);
    // SAFETY: the buffer CredUI gave with its size; three 256-wide outputs, their sizes given
    let ok = unsafe {
        CredUnPackAuthenticationBufferW(
            0,
            buf,
            len,
            user.as_mut_ptr(),
            &mut ul,
            dom.as_mut_ptr(),
            &mut dl,
            pw.as_mut_ptr(),
            &mut pl,
        )
    } != 0;
    let n = (pl as usize).min(256);
    let typed: Vec<u16> = pw[..n].iter().copied().take_while(|&c| c != 0).collect();
    let word = if ok { judge(typed) } else { "unpack_failed" };
    for x in pw.iter_mut().chain(user.iter_mut()).chain(dom.iter_mut()) {
        // SAFETY: a live u16 of our own array
        unsafe { std::ptr::write_volatile(x, 0) };
    }
    for i in 0..len as usize {
        // SAFETY: inside the buffer CredUI gave (len bytes)
        unsafe { std::ptr::write_volatile(buf.cast::<u8>().add(i), 0) };
    }
    // SAFETY: CredUI's buffer, freed once
    unsafe { CoTaskMemFree(buf) };
    word
}

/// W sheet step 5 · M1: the system's secure credential window.
#[test]
#[ignore = "W sheet: owner at the Windows laptop"]
fn w4_m1_credui_secure_prompt() {
    let before = shot();
    let (tx, rx) = std::sync::mpsc::channel();
    let watcher = std::thread::spawn(move || {
        pause(4000);
        let during = shot();
        let sent = send("zz");
        let _ = tx.send((during, sent));
    });
    let msg = wide("M1 量測:請打 test1234 後按確定。這是測試字，不要打你的 Windows 密碼。");
    let cap = wide("AIB 量測 M1");
    let info = CREDUI_INFOW {
        cbSize: std::mem::size_of::<CREDUI_INFOW>() as u32,
        hwndParent: null_mut(),
        pszMessageText: msg.as_ptr(),
        pszCaptionText: cap.as_ptr(),
        hbmBanner: null_mut(),
    };
    let (mut pkg, mut out, mut out_len, mut save) = (0u32, null_mut::<c_void>(), 0u32, 0);
    let started = Instant::now();
    // SAFETY: a filled CREDUI_INFOW with live strings; out-pointers to our locals
    let rc = unsafe {
        CredUIPromptForWindowsCredentialsW(
            &info,
            0,
            &mut pkg,
            null(),
            0,
            &mut out,
            &mut out_len,
            &mut save,
            CREDUIWIN_GENERIC | CREDUIWIN_SECURE_PROMPT,
        )
    };
    let secs = started.elapsed().as_secs();
    let ((during_ok, during_dark), sent) = rx
        .recv_timeout(Duration::from_secs(10))
        .unwrap_or(((false, -1.0), 0));
    let _ = watcher.join();
    let typed = if rc == 0 && !out.is_null() {
        unpack_and_free(out, out_len)
    } else {
        "none"
    };
    println!(
        "W4-M1 {{\"rc\":{rc},\"secs\":{secs},\"shot_before_ok\":{},\"dark_before\":{:.3},\"shot_during_ok\":{during_ok},\"dark_during\":{during_dark:.3},\"keys_sent\":{sent},\"typed\":\"{typed}\"}}",
        before.0, before.1
    );
}

/// Switches back to the user's desktop and closes ours, however the window ends.
struct Back {
    user: HDESK,
    ours: HDESK,
}

impl Drop for Back {
    fn drop(&mut self) {
        // SAFETY: two desktop handles that stay open until here
        unsafe {
            SwitchDesktop(self.user);
            CloseDesktop(self.ours);
        }
    }
}

/// The M2 window on a desktop of its own; returns (typed word, capture excluded, switched).
fn m2_window(name: &str, user: HDESK) -> (&'static str, bool, bool) {
    let n = wide(name);
    // SAFETY: Win32 calls on handles made here; strings live across each call
    unsafe {
        let ours = CreateDesktopW(n.as_ptr(), null(), null(), 0, GENERIC_ALL, null());
        if ours.is_null() {
            return ("create_failed", false, false);
        }
        let _back = Back { user, ours };
        if SetThreadDesktop(ours) == 0 {
            return ("set_thread_failed", false, false);
        }
        let class = wide("AibW4M2");
        let hinst = GetModuleHandleW(null());
        let mut wc: WNDCLASSW = std::mem::zeroed();
        wc.lpfnWndProc = Some(DefWindowProcW);
        wc.hInstance = hinst;
        wc.lpszClassName = class.as_ptr();
        RegisterClassW(&wc);
        let title = wide("AIB 量測 M2:請打 test1234 後按 Enter(這是測試字，不要打你的密碼)");
        let main: HWND = CreateWindowExW(
            0,
            class.as_ptr(),
            title.as_ptr(),
            WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            100,
            100,
            720,
            160,
            null_mut(),
            null_mut(),
            hinst,
            null(),
        );
        let edit_class = wide("EDIT");
        let edit = CreateWindowExW(
            0,
            edit_class.as_ptr(),
            null(),
            WS_CHILD | WS_VISIBLE | WS_BORDER | ES_PASSWORD as u32 | ES_AUTOHSCROLL as u32,
            20,
            40,
            660,
            30,
            main,
            null_mut(),
            hinst,
            null(),
        );
        if main.is_null() || edit.is_null() {
            return ("window_failed", false, false);
        }
        let excluded = SetWindowDisplayAffinity(main, WDA_EXCLUDEFROMCAPTURE) != 0;
        let switched = SwitchDesktop(ours) != 0;
        SetFocus(edit);
        let deadline = Instant::now() + Duration::from_secs(150);
        let mut entered = false;
        let mut msg: MSG = std::mem::zeroed();
        while Instant::now() < deadline {
            if PeekMessageW(&mut msg, null_mut(), 0, 0, PM_REMOVE) == 0 {
                pause(20);
                continue;
            }
            if msg.message == WM_KEYDOWN && msg.wParam == usize::from(VK_RETURN) {
                entered = true;
                break;
            }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        let mut buf = [0u16; 64];
        let got = GetWindowTextW(edit, buf.as_mut_ptr(), 64).max(0) as usize;
        let word = if entered {
            judge(buf[..got.min(64)].to_vec())
        } else {
            "timeout"
        };
        for x in buf.iter_mut() {
            std::ptr::write_volatile(x, 0);
        }
        DestroyWindow(main);
        (word, excluded, switched)
    }
}

/// W sheet step 6 · M2: a window on a desktop of our own, excluded from capture.
#[test]
#[ignore = "W sheet: owner at the Windows laptop"]
fn w4_m2_own_desktop_window() {
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let name = format!("aib-w4-m2-{}-{stamp}", std::process::id());
    // SAFETY: this thread's own desktop handle (not to be closed)
    let user = unsafe { GetThreadDesktop(GetCurrentThreadId()) } as usize;
    let before = shot();
    let n2 = name.clone();
    let ui = std::thread::spawn(move || m2_window(&n2, user as HDESK));
    pause(4000);
    let during = shot();
    let sent_here = send("zz");
    let n3 = name.clone();
    let named = std::thread::spawn(move || {
        let w = wide(&n3);
        // SAFETY: a desktop opened by name and set on this short-lived thread only
        unsafe {
            let d = OpenDesktopW(w.as_ptr(), 0, 0, GENERIC_ALL);
            if d.is_null() {
                return (false, false, 0);
            }
            let set = SetThreadDesktop(d) != 0;
            let sent = if set { send("yy") } else { 0 };
            (true, set, sent)
        }
    })
    .join()
    .unwrap_or((false, false, 0));
    let (typed, excluded, switched) = ui.join().unwrap_or(("thread_failed", false, false));
    println!(
        "W4-M2 {{\"switched\":{switched},\"capture_excluded\":{excluded},\"shot_before_ok\":{},\"dark_before\":{:.3},\"shot_during_ok\":{},\"dark_during\":{:.3},\"keys_sent_from_user_desktop\":{sent_here},\"named_open\":{},\"named_set\":{},\"keys_sent_by_name\":{},\"typed\":\"{typed}\"}}",
        before.0, before.1, during.0, during.1, named.0, named.1, named.2
    );
}
