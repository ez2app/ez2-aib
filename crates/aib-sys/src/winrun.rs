//! The signed-in user's "run at sign-in" entry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`,
//! `winreg.h`, public): the Windows half of "aibd starts by itself" (T97, IFACE-T97-1m r2 left it
//! out; PLAN-win-customer-today-c2f0b-1126 rows 2 and 4). One string value per name, in
//! **HKCU only** — never HKLM, so no administrator and no other account is touched. No
//! PowerShell, no Task Scheduler (Andy 11-15 / 11-26).
//!
//! What is written is one command line; the caller quotes the path ([`quoted`]) so a folder
//! with spaces cannot be read as another program (the unquoted-path problem). Like the rest of
//! aibd's install, this is a convenience against slips, not a defence: the same account can
//! change its own Run key at any time.

/// The key every value here lives under (below `HKEY_CURRENT_USER`).
pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// `"<path>"` for a Run value: the whole path in double quotes. A path that itself holds a
/// double quote, a NUL or a newline is refused (it could not be quoted back unambiguously).
pub fn quoted(path: &str) -> Result<String, String> {
    if path.is_empty() {
        return Err("empty path".into());
    }
    if path.contains(['"', '\0', '\r', '\n']) {
        return Err(format!(
            "path has a quote or control character; not written: {path:?}"
        ));
    }
    Ok(format!("\"{path}\""))
}

#[cfg(windows)]
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Write (or replace) the Run value `name` = `command`.
#[cfg(windows)]
pub fn set(name: &str, command: &str) -> Result<(), crate::SysError> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ};
    let key = wide(RUN_KEY);
    let name_w = wide(name);
    let data = wide(command);
    let bytes = u32::try_from(data.len() * 2).map_err(|_| crate::SysError::Unknown)?;
    // SAFETY: every pointer is a NUL-terminated UTF-16 buffer that lives for the whole call;
    // `bytes` is the size of `data` in bytes, terminator included, as REG_SZ wants.
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
        Err(crate::SysError::Os(i32::try_from(rc).unwrap_or(i32::MAX)))
    }
}

/// Read the Run value `name`: `Ok(None)` when there is none.
#[cfg(windows)]
pub fn get(name: &str) -> Result<Option<String>, crate::SysError> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_SZ};
    let key = wide(RUN_KEY);
    let name_w = wide(name);
    let mut buf = vec![0u16; 2048];
    let mut bytes = u32::try_from(buf.len() * 2).map_err(|_| crate::SysError::Unknown)?;
    // SAFETY: `buf` is writable for `bytes` bytes; key and name are NUL-terminated.
    let rc = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name_w.as_ptr(),
            RRF_RT_REG_SZ,
            std::ptr::null_mut(),
            buf.as_mut_ptr().cast(),
            &mut bytes,
        )
    };
    if rc == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if rc != ERROR_SUCCESS {
        return Err(crate::SysError::Os(i32::try_from(rc).unwrap_or(i32::MAX)));
    }
    let units = (usize::try_from(bytes).unwrap_or(0) / 2).min(buf.len());
    let got = buf.get(..units).unwrap_or_default();
    let end = got.iter().position(|u| *u == 0).unwrap_or(got.len());
    Ok(Some(String::from_utf16_lossy(
        got.get(..end).unwrap_or_default(),
    )))
}

/// Remove the Run value `name`; none there is fine.
#[cfg(windows)]
pub fn remove(name: &str) -> Result<(), crate::SysError> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{RegDeleteKeyValueW, HKEY_CURRENT_USER};
    let key = wide(RUN_KEY);
    let name_w = wide(name);
    // SAFETY: key and name are NUL-terminated UTF-16 buffers alive for the call.
    let rc = unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, key.as_ptr(), name_w.as_ptr()) };
    if rc == ERROR_SUCCESS || rc == ERROR_FILE_NOT_FOUND {
        Ok(())
    } else {
        Err(crate::SysError::Os(i32::try_from(rc).unwrap_or(i32::MAX)))
    }
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn set(_: &str, _: &str) -> Result<(), crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn get(_: &str) -> Result<Option<String>, crate::SysError> {
    Err(crate::SysError::Unsupported)
}

/// No Run key off Windows.
#[cfg(not(windows))]
pub fn remove(_: &str) -> Result<(), crate::SysError> {
    Err(crate::SysError::Unsupported)
}

#[cfg(test)]
mod tests {
    use super::*;

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
        for bad in ["", "C:\\a\"b\\aibd.exe", "C:\\a\nb", "C:\\a\0b", "C:\\a\rb"] {
            assert!(quoted(bad).is_err(), "reason: accepted {bad:?}");
        }
    }

    #[test]
    fn the_run_key_is_the_current_users() {
        assert_eq!(
            RUN_KEY, r"Software\Microsoft\Windows\CurrentVersion\Run",
            "reason: run_key"
        );
    }

    #[cfg(not(windows))]
    #[test]
    fn off_windows_there_is_no_run_key() {
        assert!(matches!(set("x", "y"), Err(crate::SysError::Unsupported)));
        assert!(matches!(get("x"), Err(crate::SysError::Unsupported)));
        assert!(matches!(remove("x"), Err(crate::SysError::Unsupported)));
    }
}
