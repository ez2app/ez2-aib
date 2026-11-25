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
}

#[cfg(windows)]
impl Key {
    fn path(self) -> &'static str {
        match self {
            Key::Run => RUN_KEY,
            Key::Uninstall => UNINSTALL_KEY,
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
    // subkey is created by this same call (no RegCreateKeyEx).
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
    }
}
