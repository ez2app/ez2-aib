//! N1 (Windows only, PREREAD-shared-folder-noindex-a9136-1121, 5d33a 0/0/2/2): mark a folder
//! "not content indexed" so Windows Search does not read seat trees, receipts and handoffs.
//! `GetFileAttributesW` / `SetFileAttributesW` (`fileapi.h`, public). On other platforms
//! [`mark`] does nothing; macOS uses a `.noindex` name and `.metadata_never_index` instead.

use std::io;
use std::path::Path;

/// Add `FILE_ATTRIBUTE_NOT_CONTENT_INDEXED` to `dir`, keeping its other attributes.
#[cfg(windows)]
pub fn mark(dir: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileAttributesW, SetFileAttributesW, FILE_ATTRIBUTE_NOT_CONTENT_INDEXED,
        INVALID_FILE_ATTRIBUTES,
    };
    let wide: Vec<u16> = dir.as_os_str().encode_wide().chain(Some(0)).collect();
    // SAFETY: `wide` is a NUL-terminated UTF-16 path that lives for the whole call.
    let now = unsafe { GetFileAttributesW(wide.as_ptr()) };
    if now == INVALID_FILE_ATTRIBUTES {
        return Err(io::Error::last_os_error());
    }
    if now & FILE_ATTRIBUTE_NOT_CONTENT_INDEXED != 0 {
        return Ok(());
    }
    // SAFETY: as above; the attributes are the folder's own plus one documented flag.
    let ok = unsafe { SetFileAttributesW(wide.as_ptr(), now | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED) };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

/// Nothing to do outside Windows.
#[cfg(not(windows))]
pub fn mark(_dir: &Path) -> io::Result<()> {
    Ok(())
}

/// Whether `dir` carries the flag (Windows); `false` elsewhere.
#[cfg(windows)]
pub fn marked(dir: &Path) -> bool {
    use std::os::windows::fs::MetadataExt;
    use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_NOT_CONTENT_INDEXED;
    std::fs::metadata(dir)
        .is_ok_and(|m| m.file_attributes() & FILE_ATTRIBUTE_NOT_CONTENT_INDEXED != 0)
}

/// Whether `dir` carries the flag (Windows); `false` elsewhere.
#[cfg(not(windows))]
pub fn marked(_dir: &Path) -> bool {
    false
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    /// NI5: a new folder ends up marked, and marking twice is fine.
    #[test]
    fn a_folder_is_marked_not_indexed() {
        let d = std::env::temp_dir().join(format!("aib-noindex-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        assert!(!marked(&d));
        mark(&d).unwrap();
        mark(&d).unwrap();
        let got = marked(&d);
        let _ = std::fs::remove_dir(&d);
        assert!(got, "reason: windows_indexed");
    }
}
