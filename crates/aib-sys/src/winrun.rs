//! The signed-in user's "run at sign-in" entry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`,
//! `winreg.h`, public): the Windows half of "aibd starts by itself" (T97, IFACE-T97-1m r2 left it
//! out; PLAN-win-customer-today-c2f0b-1126 rows 2 and 4), plus the one question the setup asks
//! about its own token (is it elevated?). No PowerShell, reg.exe or Task Scheduler.
//!
//! COND-win-installer-3e53b-1126 r1 (pre-read of `Win32_System_Registry`):
//! - only `RegSetKeyValueW`, `RegGetValueW` (read back) and `RegDeleteKeyValueW`, all here;
//! - the root is always the current user's and the key is always [`RUN_KEY`]: no function takes
//!   a root or a key, so nothing here can touch another account or the machine;
//! - only `REG_SZ` is written and only `REG_SZ` is read back (`RRF_RT_REG_SZ`);
//! - a source guard (the tests below) keeps the machine-wide root and the other registry calls
//!   out of every crate, and the current-user root out of every file but this one.
//!
//! PRE-READ-aib-cmd-windows-3e53b-1127 (`aib web` on Windows): the one other value is the
//! user's own `Path` under [`ENV_KEY`] ([`user_path_read`] / [`user_path_write`]): read as it
//! is (`RRF_NOEXPAND`, so `%USERPROFILE%` and the like stay unexpanded), written back with the
//! type it had (`REG_SZ` or `REG_EXPAND_SZ`), compared again just before writing, read back
//! after and put back as it was when it does not read back. What goes into it is worked out by
//! aibd (`aib_cmd::path_with` / `path_without`); nothing here adds or removes a folder.
//!
//! Same account as the rest of aibd: the Run value is the user's own and any program of theirs
//! can change it. It is a convenience, not a defence (the defence is pairing / Windows Hello).

/// The key every value here lives under (below the current user's root).
pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// AIB's own entry in Settings > Apps (below the current user's root).
pub const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AIB";

/// The only values written under [`UNINSTALL_KEY`].
pub const UNINSTALL_NAMES: [&str; 5] = [
    "DisplayName",
    "DisplayVersion",
    "Publisher",
    "InstallLocation",
    "UninstallString",
];

/// The current user's environment key (below the current user's root): only [`ENV_PATH_NAME`]
/// is read or written there.
pub const ENV_KEY: &str = "Environment";

/// The one value under [`ENV_KEY`] this module touches.
pub const ENV_PATH_NAME: &str = "Path";

/// The longest `Path` value written, in UTF-16 units (an environment variable's limit).
pub const PATH_MAX_UNITS: usize = 32767;

/// [`user_path_write`]: the value changed between the caller's read and the write.
pub const PATH_CHANGED: &str = "user_path_changed_meanwhile";

/// [`user_path_write`]: the value did not read back as written (what was there is put back).
pub const PATH_NOT_READ_BACK: &str = "user_path_not_read_back";

/// The registry type the user's `Path` had: written back the same.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathKind {
    Sz,
    ExpandSz,
}

/// Is `name` one of [`UNINSTALL_NAMES`]?
pub fn uninstall_name_ok(name: &str) -> bool {
    UNINSTALL_NAMES.contains(&name)
}

/// `"<path>"` for a Run value: the whole path in double quotes, nothing after it. A path that
/// itself holds a double quote, a NUL or a line break is refused (it could not be quoted back
/// unambiguously), and so is one that is not absolute (`X:\…` or `\\server\…`).
pub fn quoted(path: &str) -> Result<String, String> {
    if path.contains(['"', '\0', '\r', '\n']) {
        return Err(format!(
            "path has a quote or control character; not written: {path:?}"
        ));
    }
    let drive = matches!(path.as_bytes(), [d, b':', b'\\', _, ..] if d.is_ascii_alphabetic());
    if !drive && !path.starts_with(r"\\") {
        return Err(format!(
            "not an absolute Windows path; not written: {path:?}"
        ));
    }
    Ok(format!("\"{path}\""))
}

#[cfg(windows)]
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(windows)]
fn os_err(rc: u32) -> crate::SysError {
    crate::SysError::Os(i32::try_from(rc).unwrap_or(i32::MAX))
}

/// The two keys this module writes, both below the current user's root; nothing else.
#[cfg(windows)]
#[derive(Clone, Copy)]
enum Key {
    Run,
    Uninstall,
    Environment,
}

#[cfg(windows)]
impl Key {
    fn path(self) -> &'static str {
        match self {
            Key::Run => RUN_KEY,
            Key::Uninstall => UNINSTALL_KEY,
            Key::Environment => ENV_KEY,
        }
    }
}

#[cfg(windows)]
fn set_sz(key: Key, name: &str, value: &str) -> Result<(), crate::SysError> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ};
    let key = wide(key.path());
    let name_w = wide(name);
    let data = wide(value);
    let bytes = u32::try_from(data.len() * 2).map_err(|_| crate::SysError::Unknown)?;
    // SAFETY: every pointer is a NUL-terminated UTF-16 buffer that lives for the whole call;
    // `bytes` is the size of `data` in bytes, terminator included, as REG_SZ wants. A missing
    // subkey is created by this same call (no separate key-creating call).
    let rc = unsafe {
        RegSetKeyValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name_w.as_ptr(),
            REG_SZ,
            data.as_ptr().cast(),
            bytes,
        )
    };
    if rc == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(os_err(rc))
    }
}

#[cfg(windows)]
fn get_sz(key: Key, name: &str) -> Result<Option<String>, crate::SysError> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, RRF_NOEXPAND, RRF_RT_REG_SZ,
    };
    let key = wide(key.path());
    let name_w = wide(name);
    let mut buf = vec![0u16; 2048];
    let mut bytes = u32::try_from(buf.len() * 2).map_err(|_| crate::SysError::Unknown)?;
    // SAFETY: `buf` is writable for `bytes` bytes; key and name are NUL-terminated.
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name_w.as_ptr(),
            // RRF_NOEXPAND: a REG_EXPAND_SZ value is not expanded and read as a REG_SZ (3e53b N1)
            RRF_RT_REG_SZ | RRF_NOEXPAND,
            std::ptr::null_mut(),
            buf.as_mut_ptr().cast(),
            &mut bytes,
        )
    };
    if rc == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if rc != ERROR_SUCCESS {
        return Err(os_err(rc));
    }
    let units = (usize::try_from(bytes).unwrap_or(0) / 2).min(buf.len());
    let got = buf.get(..units).unwrap_or_default();
    let end = got.iter().position(|u| *u == 0).unwrap_or(got.len());
    Ok(Some(String::from_utf16_lossy(
        got.get(..end).unwrap_or_default(),
    )))
}

#[cfg(windows)]
fn delete_if(key: Key, name: &str, expected: &str) -> Result<bool, crate::SysError> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{RegDeleteKeyValueW, HKEY_CURRENT_USER};
    if get_sz(key, name)?.as_deref() != Some(expected) {
        return Ok(false);
    }
    let key = wide(key.path());
    let name_w = wide(name);
    // SAFETY: key and name are NUL-terminated UTF-16 buffers alive for the call.
    let rc = unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, key.as_ptr(), name_w.as_ptr()) };
    if rc == ERROR_SUCCESS {
        Ok(true)
    } else if rc == ERROR_FILE_NOT_FOUND {
        Ok(false)
    } else {
        Err(os_err(rc))
    }
}

/// Write (or replace) the Run value `name` = `command` (`REG_SZ`).
#[cfg(windows)]
pub fn run_set(name: &str, command: &str) -> Result<(), crate::SysError> {
    set_sz(Key::Run, name, command)
}

/// Read the Run value `name`: `Ok(None)` when there is none. A value of another type than
/// `REG_SZ` is an error (it is not one we wrote).
#[cfg(windows)]
pub fn run_get(name: &str) -> Result<Option<String>, crate::SysError> {
    get_sz(Key::Run, name)
}

/// Remove the Run value `name` only when it reads exactly `expected`: `Ok(true)` removed,
/// `Ok(false)` there was none or it was somebody else's (left as it is).
#[cfg(windows)]
pub fn run_delete_if(name: &str, expected: &str) -> Result<bool, crate::SysError> {
    delete_if(Key::Run, name, expected)
}

/// Write one value of AIB's entry in Settings > Apps ([`UNINSTALL_KEY`]); only the names in
/// [`UNINSTALL_NAMES`] (COND r0 §4-1).
#[cfg(windows)]
pub fn uninstall_set(name: &str, value: &str) -> Result<(), crate::SysError> {
    if !uninstall_name_ok(name) {
        return Err(crate::SysError::Unsupported);
    }
    set_sz(Key::Uninstall, name, value)
}

/// Read one value of AIB's Settings > Apps entry.
#[cfg(windows)]
pub fn uninstall_get(name: &str) -> Result<Option<String>, crate::SysError> {
    get_sz(Key::Uninstall, name)
}

/// Remove one value of AIB's Settings > Apps entry when it reads exactly `expected`. The key
/// itself stays (empty, so Settings no longer lists it): deleting keys is not in this module.
#[cfg(windows)]
pub fn uninstall_delete_if(name: &str, expected: &str) -> Result<bool, crate::SysError> {
    if !uninstall_name_ok(name) {
        return Err(crate::SysError::Unsupported);
    }
    delete_if(Key::Uninstall, name, expected)
}

/// The user's `Path` as stored (not expanded) and its type; `None` when there is none. Another
/// type, a value too long or one that is not valid UTF-16 is an error: nothing is written then.
#[cfg(windows)]
pub fn user_path_read() -> Result<Option<(String, PathKind)>, String> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, REG_EXPAND_SZ, REG_SZ, RRF_NOEXPAND, RRF_RT_REG_EXPAND_SZ,
        RRF_RT_REG_SZ,
    };
    let (key, name) = (wide(Key::Environment.path()), wide(ENV_PATH_NAME));
    // the limit, its terminator and one more unit: a value that fills it all is too long
    let mut buf = vec![0u16; PATH_MAX_UNITS + 2];
    let mut bytes = u32::try_from(buf.len() * 2).map_err(|_| "user PATH buffer".to_string())?;
    let mut kind = 0u32;
    // SAFETY: `buf` is writable for `bytes` bytes, `kind` is a valid out pointer, key and name
    // are NUL-terminated. RRF_NOEXPAND (3e53b PRE-READ §2-2): a REG_EXPAND_SZ value comes back
    // as stored, so writing it back does not freeze the customer's %VARIABLES%.
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name.as_ptr(),
            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND,
            &mut kind,
            buf.as_mut_ptr().cast(),
            &mut bytes,
        )
    };
    if rc == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if rc != ERROR_SUCCESS {
        return Err(format!(
            "could not read the user PATH ({}); not changed",
            os_err(rc)
        ));
    }
    let kind = match kind {
        REG_SZ => PathKind::Sz,
        REG_EXPAND_SZ => PathKind::ExpandSz,
        _ => return Err("the user PATH is not a text value; not changed".into()),
    };
    let units = (usize::try_from(bytes).unwrap_or(0) / 2).min(buf.len());
    let got = buf.get(..units).unwrap_or_default();
    let end = got.iter().position(|u| *u == 0).unwrap_or(got.len());
    if end > PATH_MAX_UNITS {
        return Err("the user PATH is longer than Windows allows; not changed".into());
    }
    // strict, not lossy: a lossy read would change the customer's other folders when written back
    let v = String::from_utf16(got.get(..end).unwrap_or_default())
        .map_err(|_| "the user PATH is not valid text; not changed".to_string())?;
    Ok(Some((v, kind)))
}

#[cfg(windows)]
fn user_path_set(value: &str, kind: PathKind) -> Result<(), String> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegSetKeyValueW, HKEY_CURRENT_USER, REG_EXPAND_SZ, REG_SZ,
    };
    let (key, name) = (wide(Key::Environment.path()), wide(ENV_PATH_NAME));
    let data = wide(value);
    let bytes = u32::try_from(data.len() * 2).map_err(|_| "user PATH too long".to_string())?;
    let ty = match kind {
        PathKind::Sz => REG_SZ,
        PathKind::ExpandSz => REG_EXPAND_SZ,
    };
    // SAFETY: every pointer is a NUL-terminated UTF-16 buffer alive for the call; `bytes` is
    // the size of `data` in bytes, terminator included, as REG_SZ / REG_EXPAND_SZ want.
    let rc = unsafe {
        RegSetKeyValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name.as_ptr(),
            ty,
            data.as_ptr().cast(),
            bytes,
        )
    };
    if rc == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(format!("could not write the user PATH ({})", os_err(rc)))
    }
}

/// The user's `Path` taken away (it had none before AIB wrote one that did not read back).
#[cfg(windows)]
fn user_path_delete() -> Result<(), String> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{RegDeleteKeyValueW, HKEY_CURRENT_USER};
    let (key, name) = (wide(Key::Environment.path()), wide(ENV_PATH_NAME));
    // SAFETY: key and name are NUL-terminated UTF-16 buffers alive for the call.
    let rc = unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, key.as_ptr(), name.as_ptr()) };
    if rc == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(format!("could not remove the user PATH ({})", os_err(rc)))
    }
}

/// Write the user's `Path` = `new` with type `kind`, only when it still reads `before` (value
/// and type; `None` = there was none) — else [`PATH_CHANGED`], nothing written. Read back
/// after: anything but `new` with `kind` puts `before` back and is [`PATH_NOT_READ_BACK`].
#[cfg(windows)]
pub fn user_path_write(
    new: &str,
    kind: PathKind,
    before: Option<(&str, PathKind)>,
) -> Result<(), String> {
    write_checked(
        &user_path_read,
        &user_path_set,
        &user_path_delete,
        new,
        kind,
        before,
    )
}

/// The steps of [`user_path_write`] over a given read / write / remove, so both platforms test
/// them (f163c REVIEW-op8 L1). "Put back" is said only when a read shows it put back: the old
/// value and type written again, or — there was none — the value taken away only while it is
/// still exactly what this wrote.
pub fn write_checked(
    read: &dyn Fn() -> Result<Option<(String, PathKind)>, String>,
    set: &dyn Fn(&str, PathKind) -> Result<(), String>,
    remove: &dyn Fn() -> Result<(), String>,
    new: &str,
    kind: PathKind,
    before: Option<(&str, PathKind)>,
) -> Result<(), String> {
    if new.contains('\0') || new.encode_utf16().count() > PATH_MAX_UNITS {
        return Err("the new user PATH would be too long; not changed".into());
    }
    let reads = |want: Option<(&str, PathKind)>| {
        read().is_ok_and(|now| now.as_ref().map(|(v, k)| (v.as_str(), *k)) == want)
    };
    if !reads(before) {
        return Err(PATH_CHANGED.into());
    }
    set(new, kind)?;
    if reads(Some((new, kind))) {
        return Ok(());
    }
    let restored = match before {
        Some((v, k)) => set(v, k).is_ok() && reads(Some((v, k))),
        None => reads(None) || (reads(Some((new, kind))) && remove().is_ok() && reads(None)),
    };
    Err(format!(
        "{PATH_NOT_READ_BACK}: the user PATH did not read back as written; {}",
        if restored {
            "put back as it was"
        } else {
            "could not put it back"
        }
    ))
}

/// Tell open programs the environment changed (`WM_SETTINGCHANGE` "Environment", 5 s, not to
/// a hung window). Only new windows see the new PATH either way, so a timeout is not a failure.
#[cfg(windows)]
pub fn env_changed() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SendMessageTimeoutW, HWND_BROADCAST, SMTO_ABORTIFHUNG, WM_SETTINGCHANGE,
    };
    let what = wide(ENV_KEY);
    let mut result = 0usize;
    // SAFETY: `what` is a NUL-terminated UTF-16 string alive for the call, the form
    // WM_SETTINGCHANGE's lParam takes; `result` is a valid out pointer.
    let _ = unsafe {
        SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            what.as_ptr() as isize,
            SMTO_ABORTIFHUNG,
            5000,
            &mut result,
        )
    };
}

/// Is this process running elevated ("Run as administrator")? The setup refuses then: it would
/// write the wrong user's folder and Run key (COND r0 §1-4).
#[cfg(windows)]
pub fn elevated() -> Result<bool, crate::SysError> {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::Security::{
        GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    let mut token: HANDLE = std::ptr::null_mut();
    // SAFETY: no arguments; returns this process's pseudo handle, which needs no closing.
    let me = unsafe { GetCurrentProcess() };
    // SAFETY: `me` is this process's pseudo handle; `token` is a valid out pointer.
    let ok = unsafe { OpenProcessToken(me, TOKEN_QUERY, &mut token) };
    if ok == 0 {
        return Err(crate::SysError::Unknown);
    }
    let mut e = TOKEN_ELEVATION { TokenIsElevated: 0 };
    let mut got = 0u32;
    let size = u32::try_from(std::mem::size_of::<TOKEN_ELEVATION>()).unwrap_or(4);
    // SAFETY: `token` was opened with TOKEN_QUERY above; `e` is writable for `size` bytes.
    let ok = unsafe {
        GetTokenInformation(
            token,
            TokenElevation,
            std::ptr::addr_of_mut!(e).cast(),
            size,
            &mut got,
        )
    };
    // SAFETY: `token` is the handle OpenProcessToken gave and is closed once.
    unsafe { CloseHandle(token) };
    if ok == 0 {
        return Err(crate::SysError::Unknown);
    }
    Ok(e.TokenIsElevated != 0)
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn run_set(_: &str, _: &str) -> Result<(), crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn run_get(_: &str) -> Result<Option<String>, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn run_delete_if(_: &str, _: &str) -> Result<bool, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Settings > Apps entry off Windows.
#[cfg(not(windows))]
pub fn uninstall_set(_: &str, _: &str) -> Result<(), crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Settings > Apps entry off Windows.
#[cfg(not(windows))]
pub fn uninstall_get(_: &str) -> Result<Option<String>, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Settings > Apps entry off Windows.
#[cfg(not(windows))]
pub fn uninstall_delete_if(_: &str, _: &str) -> Result<bool, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// Not asked off Windows.
/// No user PATH off Windows.
#[cfg(not(windows))]
pub fn user_path_read() -> Result<Option<(String, PathKind)>, String> {
    Err(crate::SysError::Unsupported.to_string())
}

#[cfg(not(windows))]
pub fn user_path_write(_: &str, _: PathKind, _: Option<(&str, PathKind)>) -> Result<(), String> {
    Err(crate::SysError::Unsupported.to_string())
}

#[cfg(not(windows))]
pub fn env_changed() {}

#[cfg(not(windows))]
pub fn elevated() -> Result<bool, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    #[test]
    fn a_run_command_is_the_whole_path_in_quotes() {
        assert_eq!(
            quoted(r"C:\Users\A B\AppData\Local\AIB\aibd.exe").as_deref(),
            Ok(r#""C:\Users\A B\AppData\Local\AIB\aibd.exe""#),
            "reason: unquoted"
        );
    }

    #[test]
    fn a_path_that_cannot_be_quoted_is_refused() {
        for bad in [
            "",
            "C:\\a\"b\\aibd.exe",
            "C:\\a\nb",
            "C:\\a\0b",
            "C:\\a\rb",
            r"AIB\aibd.exe",
            "%LOCALAPPDATA%\\AIB\\aibd.exe",
        ] {
            assert!(quoted(bad).is_err(), "reason: accepted {bad:?}");
        }
    }

    // the source guard (COND r1, pre-read condition 3): the names are spelled in two halves so
    // this file does not name what it looks for
    fn banned() -> Vec<String> {
        [
            ("HKEY_LOCAL", "_MACHINE"),
            ("RegCreate", "KeyEx"),
            ("RegSet", "ValueEx"),
            ("RegOpen", "KeyEx"),
        ]
        .iter()
        .map(|(a, b)| format!("{a}{b}"))
        .collect()
    }

    fn user_root() -> String {
        format!("{}{}", "HKEY_CURRENT", "_USER")
    }

    fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                if p.file_name().is_some_and(|n| n == "target") {
                    continue;
                }
                rs_files(&p, out);
            } else if p.extension().is_some_and(|x| x == "rs") {
                out.push(p);
            }
        }
    }

    fn is_winrun(p: &Path) -> bool {
        p.ends_with(Path::new("aib-sys").join("src").join("winrun.rs"))
    }

    /// `"<file>: <name>"` for every banned registry name in any `.rs` under `root`, and for the
    /// current-user root anywhere but winrun.rs.
    fn registry_names(root: &Path) -> Vec<String> {
        let mut files = Vec::new();
        rs_files(root, &mut files);
        let mut hits = Vec::new();
        for f in files {
            let Ok(text) = std::fs::read_to_string(&f) else {
                continue;
            };
            for n in banned() {
                if text.contains(n.as_str()) {
                    hits.push(format!("{}: {n}", f.display()));
                }
            }
            if !is_winrun(&f) && text.contains(user_root().as_str()) {
                hits.push(format!("{}: {}", f.display(), user_root()));
            }
        }
        hits
    }

    #[test]
    fn no_crate_names_the_machine_root_or_other_registry_calls_and_only_winrun_the_user_root() {
        let ws = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let mut hits = registry_names(&ws.join("crates"));
        hits.extend(registry_names(&ws.join("xtask")));
        assert_eq!(hits, Vec::<String>::new(), "reason: registry_guard");
    }

    #[test]
    fn the_registry_guard_sees_a_planted_name() {
        let root = std::env::temp_dir().join(format!("aib-sys-regguard-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let deep = root.join("x").join("src");
        let own = root.join("aib-sys").join("src");
        std::fs::create_dir_all(&deep).unwrap_or_default();
        std::fs::create_dir_all(&own).unwrap_or_default();
        std::fs::write(own.join("winrun.rs"), user_root()).unwrap_or_default();
        let clean = registry_names(&root);
        std::fs::write(deep.join("a.rs"), format!("// {}", banned()[0])).unwrap_or_default();
        std::fs::write(deep.join("b.rs"), format!("// {}", user_root())).unwrap_or_default();
        let hits = registry_names(&root);
        let _ = std::fs::remove_dir_all(&root);
        assert!(clean.is_empty(), "reason: winrun_allowed {clean:?}");
        assert_eq!(hits.len(), 2, "reason: planted_found {hits:?}");
    }

    #[test]
    fn only_the_listed_settings_entry_values_are_written() {
        for ok in UNINSTALL_NAMES {
            assert!(uninstall_name_ok(ok), "reason: refused {ok}");
        }
        for no in ["", "QuietUninstallString", "NoRemove", "displayname", "Run"] {
            assert!(!uninstall_name_ok(no), "reason: took {no}");
        }
        assert!(UNINSTALL_KEY.ends_with(r"\Uninstall\AIB"), "reason: key");
    }

    /// PRE-READ-aib-cmd-windows §4: under the environment key only the `Path` value, and the
    /// key's name is said once (in `Key::path`); every other line naming the key names the value.
    #[test]
    fn only_the_path_value_of_the_environment_key_is_touched() {
        assert_eq!(ENV_KEY, "Environment", "reason: env_key");
        assert_eq!(ENV_PATH_NAME, "Path", "reason: env_name");
        let src = include_str!("winrun.rs");
        let key = format!("Key::{}", "Environment");
        let arm = format!("{key} => {}", "ENV_KEY");
        let uses: Vec<&str> = src
            .lines()
            .filter(|l| l.contains(key.as_str()) && !l.contains(arm.as_str()))
            .collect();
        assert!(!uses.is_empty(), "reason: no_use_found");
        for l in uses {
            assert!(l.contains("ENV_PATH_NAME"), "reason: other_env_value {l}");
        }
        assert_eq!(
            src.matches(arm.as_str()).count(),
            1,
            "reason: env_key_twice"
        );
        // read as stored, written back with its own type (PRE-READ §2-2; knives P1s / P2s)
        let noexpand = format!("RRF_RT_REG_EXPAND_SZ | {},", "RRF_NOEXPAND");
        assert_eq!(
            src.matches(noexpand.as_str()).count(),
            1,
            "reason: path_read_expanded"
        );
        let kept = format!("PathKind::ExpandSz => {},", "REG_EXPAND_SZ");
        assert_eq!(
            src.matches(kept.as_str()).count(),
            1,
            "reason: path_type_not_kept"
        );
    }

    /// A stand-in registry value: what `read` sees, the writes it took, a write that does not
    /// stick (`mangle`), one written in between by somebody else (`theirs`).
    struct Reg {
        now: std::cell::RefCell<Option<(String, PathKind)>>,
        mangle: std::cell::Cell<usize>,
        theirs: Option<(String, PathKind)>,
        removed: std::cell::Cell<usize>,
        reads: std::cell::Cell<usize>,
        fail_read: usize,
    }

    impl Reg {
        fn new(now: Option<(&str, PathKind)>) -> Reg {
            Reg {
                now: std::cell::RefCell::new(now.map(|(v, k)| (v.to_string(), k))),
                mangle: std::cell::Cell::new(0),
                theirs: None,
                removed: std::cell::Cell::new(0),
                reads: std::cell::Cell::new(0),
                fail_read: 0,
            }
        }
        fn write(
            &self,
            new: &str,
            kind: PathKind,
            before: Option<(&str, PathKind)>,
        ) -> Result<(), String> {
            let read = || {
                self.reads.set(self.reads.get() + 1);
                if self.reads.get() == self.fail_read {
                    return Err("busy".to_string());
                }
                Ok(self.now.borrow().clone())
            };
            let set = |v: &str, k: PathKind| {
                let n = self.mangle.get();
                *self.now.borrow_mut() = if n > 0 {
                    self.mangle.set(n - 1);
                    self.theirs.clone().or_else(|| Some((format!("{v}x"), k)))
                } else {
                    Some((v.to_string(), k))
                };
                Ok::<(), String>(())
            };
            let remove = || {
                self.removed.set(self.removed.get() + 1);
                *self.now.borrow_mut() = None;
                Ok::<(), String>(())
            };
            write_checked(&read, &set, &remove, new, kind, before)
        }
    }

    #[test]
    fn a_path_write_is_compared_before_read_back_after_and_put_back_only_when_it_reads_so() {
        use PathKind::{ExpandSz, Sz};
        // written, type kept
        let r = Reg::new(Some(("a", Sz)));
        assert_eq!(r.write("a;b", Sz, Some(("a", Sz))), Ok(()));
        assert_eq!(*r.now.borrow(), Some(("a;b".into(), Sz)));
        // changed meanwhile (value or type): nothing written
        let r = Reg::new(Some(("a", ExpandSz)));
        assert_eq!(
            r.write("a;b", Sz, Some(("a", Sz))),
            Err(PATH_CHANGED.into())
        );
        assert_eq!(
            *r.now.borrow(),
            Some(("a".into(), ExpandSz)),
            "reason: wrote_over_change"
        );
        // did not read back: the old value and type back, said only when it reads so
        let r = Reg::new(Some(("a", ExpandSz)));
        r.mangle.set(1);
        let e = r.write("a;b", ExpandSz, Some(("a", ExpandSz))).unwrap_err();
        assert!(
            e.starts_with(PATH_NOT_READ_BACK) && e.contains("put back as it was"),
            "{e}"
        );
        assert_eq!(*r.now.borrow(), Some(("a".into(), ExpandSz)));
        let r = Reg::new(Some(("a", Sz)));
        r.mangle.set(2);
        let e = r.write("a;b", Sz, Some(("a", Sz))).unwrap_err();
        assert!(
            e.contains("could not put it back"),
            "reason: said_put_back {e}"
        );
        // none before, the new one did not stick: taken away only while it is still ours
        let r = Reg::new(None);
        r.mangle.set(1);
        let e = r.write("b", ExpandSz, None).unwrap_err();
        assert!(
            e.contains("could not put it back"),
            "reason: removed_not_ours {e}"
        );
        assert_eq!(r.removed.get(), 0, "reason: removed_not_ours");
        // none before, the read back failed once but the value is ours: taken away, and said
        let r = Reg {
            fail_read: 2,
            ..Reg::new(None)
        };
        let e = r.write("b", ExpandSz, None).unwrap_err();
        assert!(e.contains("put back as it was"), "{e}");
        assert_eq!(
            (r.removed.get(), r.now.borrow().clone()),
            (1, None),
            "reason: not_removed"
        );
        // somebody else's value there in between: not removed
        let r = Reg {
            theirs: Some(("c".into(), Sz)),
            ..Reg::new(None)
        };
        r.mangle.set(1);
        assert!(r.write("b", ExpandSz, None).is_err());
        assert_eq!(r.removed.get(), 0, "reason: removed_theirs");
        assert_eq!(*r.now.borrow(), Some(("c".into(), Sz)));
        assert!(
            r.write(&"x".repeat(PATH_MAX_UNITS + 1), Sz, None).is_err(),
            "reason: too_long"
        );
    }

    #[cfg(not(windows))]
    #[test]
    fn off_windows_there_is_no_run_key() {
        assert!(matches!(
            run_set("x", "y"),
            Err(crate::SysError::Unsupported)
        ));
        assert!(matches!(run_get("x"), Err(crate::SysError::Unsupported)));
        assert!(matches!(
            run_delete_if("x", "y"),
            Err(crate::SysError::Unsupported)
        ));
        assert!(matches!(elevated(), Err(crate::SysError::Unsupported)));
        assert!(user_path_read().is_err());
        assert!(user_path_write("x", PathKind::Sz, None).is_err());
    }
}

/// Is this process alone on its console, i.e. the window was opened for it (the AIB shortcut,
/// a double-click) and closes when it ends? `GetConsoleProcessList` (ebab8 11-26 12:39: a
/// failure must not vanish with the window). Off Windows, or on any error: false.
#[cfg(windows)]
pub fn own_console() -> bool {
    use windows_sys::Win32::System::Console::GetConsoleProcessList;
    let mut ids = [0u32; 4];
    // SAFETY: `ids` is writable for 4 u32s; the call writes at most that many.
    let n = unsafe { GetConsoleProcessList(ids.as_mut_ptr(), 4) };
    n == 1
}

/// No console window of our own off Windows.
#[cfg(not(windows))]
pub fn own_console() -> bool {
    false
}

/// This user's display language as one of the page's four: `en`, `ja`, `ko`, `zh-TW`
/// (`GetUserDefaultUILanguage`). Off Windows: `en`.
#[cfg(windows)]
pub fn ui_lang() -> &'static str {
    // SAFETY: no arguments, returns a LANGID.
    lang_of(unsafe { windows_sys::Win32::Globalization::GetUserDefaultUILanguage() })
}

/// `en` off Windows (the shortcut's window is Windows only).
#[cfg(not(windows))]
pub fn ui_lang() -> &'static str {
    "en"
}

/// A Windows LANGID as one of `en`, `ja`, `ko`, `zh-TW`: any Chinese reads the page's
/// Traditional Chinese, anything else English.
pub fn lang_of(langid: u16) -> &'static str {
    match langid & 0x3ff {
        0x04 => "zh-TW",
        0x11 => "ja",
        0x12 => "ko",
        _ => "en",
    }
}

#[cfg(test)]
mod lang_tests {
    use super::lang_of;

    #[test]
    fn windows_languages_map_to_the_four_page_languages() {
        for (id, want) in [
            (0x0404, "zh-TW"),
            (0x0C04, "zh-TW"),
            (0x0804, "zh-TW"),
            (0x0411, "ja"),
            (0x0412, "ko"),
            (0x0409, "en"),
            (0x0407, "en"),
        ] {
            assert_eq!(lang_of(id), want, "reason: {id:#06x}");
        }
    }
}
