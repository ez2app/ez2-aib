//! The Windows system directory (`GetSystemDirectoryW`, `sysinfoapi.h`, public), for the system's
//! own programs by absolute path (aibd's host_ssh: `<system dir>\OpenSSH\ssh.exe`; DESIGN-hostmode
//! r3 §3-4, 3e53b DEEP2 T1). Read from the OS, never from `%SystemRoot%` / `PATH`: both are
//! environment a same-account process sets. On other platforms there is none.

use std::path::PathBuf;

/// The system directory, or none when it cannot be read (the caller refuses).
#[cfg(windows)]
pub fn system_dir() -> Option<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
    let mut buf = [0u16; 512];
    let cap = u32::try_from(buf.len()).ok()?;
    // SAFETY: `buf` is writable for `cap` UTF-16 units for the whole call.
    let n = unsafe { GetSystemDirectoryW(buf.as_mut_ptr(), cap) };
    let n = usize::try_from(n).ok()?;
    // 0 = failed; n >= len = the buffer was too small and nothing usable was written
    if n == 0 || n >= buf.len() {
        return None;
    }
    let path = PathBuf::from(std::ffi::OsString::from_wide(buf.get(..n)?));
    path.is_absolute().then_some(path)
}

/// No system directory off Windows.
#[cfg(not(windows))]
pub fn system_dir() -> Option<PathBuf> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_system_directory_is_read_from_the_os_only() {
        let d = system_dir();
        if cfg!(windows) {
            let d = d.expect("reason: system_dir_unreadable");
            assert!(d.is_absolute(), "reason: relative {d:?}");
            assert!(
                d.to_string_lossy()
                    .to_ascii_lowercase()
                    .ends_with("system32"),
                "reason: not_system32 {d:?}"
            );
        } else {
            assert_eq!(d, None, "reason: system_dir_off_windows");
        }
    }
}
