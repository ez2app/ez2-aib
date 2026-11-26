//! "AIB" shortcuts on the desktop and in the Start menu (ebab8 11-26 12:20, Andy「要最快讓用戶一鍵
//! 可用」): after the setup the customer had no way back to the workbench but typing
//! `aibd.exe ctl web` into cmd. The shortcut runs exactly that.
//!
//! Windows only: `SHGetKnownFolderPath` (Desktop / Programs, so a desktop moved to OneDrive is
//! still found), `IShellLinkW` + `IPersistFile` to write and read a `.lnk`. A shortcut is a file
//! the same account can write anyway: it is no new power, and nothing reads it but the shell.
//! Which shortcut is ours (and may be replaced or removed) is decided by the setup from
//! [`read`]; this module never deletes.

use std::path::{Path, PathBuf};

/// The desktop and the Start menu's Programs folder of this user.
#[cfg(windows)]
pub fn folders() -> Result<Vec<PathBuf>, String> {
    use windows::Win32::UI::Shell::{FOLDERID_Desktop, FOLDERID_Programs};
    Ok(vec![
        known(&FOLDERID_Desktop).map_err(|e| format!("desktop folder: {e}"))?,
        known(&FOLDERID_Programs).map_err(|e| format!("Start menu folder: {e}"))?,
    ])
}

/// Write the shortcut `link` to `exe` with `args`, the icon taken from `exe`.
#[cfg(windows)]
pub fn make(link: &Path, exe: &Path, args: &str, description: &str) -> Result<(), String> {
    use windows::core::{Interface, HSTRING};
    use windows::Win32::System::Com::IPersistFile;
    let _com = Com::init();
    let l = shell_link()?;
    let dir = exe.parent().unwrap_or(exe);
    // SAFETY: `l` is a live IShellLinkW; every string is an owned HSTRING that outlives the call.
    unsafe {
        l.SetPath(&HSTRING::from(exe))
            .map_err(|e| format!("path: {e}"))?;
        l.SetArguments(&HSTRING::from(args))
            .map_err(|e| format!("arguments: {e}"))?;
        l.SetWorkingDirectory(&HSTRING::from(dir))
            .map_err(|e| format!("folder: {e}"))?;
        l.SetIconLocation(&HSTRING::from(exe), 0)
            .map_err(|e| format!("icon: {e}"))?;
        l.SetDescription(&HSTRING::from(description))
            .map_err(|e| format!("description: {e}"))?;
    }
    let f: IPersistFile = l.cast().map_err(|e| format!("save: {e}"))?;
    // SAFETY: `f` is the same object's IPersistFile; the file name is an owned HSTRING.
    unsafe { f.Save(&HSTRING::from(link), true) }.map_err(|e| format!("save: {e}"))
}

/// The target and arguments written in the shortcut `link` (as written, not expanded).
#[cfg(windows)]
pub fn read(link: &Path) -> Result<(PathBuf, String), String> {
    use windows::core::{Interface, HSTRING};
    use windows::Win32::System::Com::{IPersistFile, STGM_READ};
    let _com = Com::init();
    let l = shell_link()?;
    let f: IPersistFile = l.cast().map_err(|e| format!("load: {e}"))?;
    // SAFETY: `f` is a live IPersistFile; the file name is an owned HSTRING.
    unsafe { f.Load(&HSTRING::from(link), STGM_READ) }.map_err(|e| format!("load: {e}"))?;
    let mut path = vec![0u16; 32768];
    let mut args = vec![0u16; 32768];
    // SAFETY: both buffers are writable for their whole length; no find data is asked for.
    // 4 = SLGP_RAWPATH: the path as written, environment variables not expanded.
    unsafe {
        l.GetPath(&mut path, std::ptr::null_mut(), 4)
            .map_err(|e| format!("target: {e}"))?;
        l.GetArguments(&mut args)
            .map_err(|e| format!("arguments: {e}"))?;
    }
    Ok((PathBuf::from(wide(&path)), wide(&args)))
}

/// No shortcuts off Windows.
#[cfg(not(windows))]
pub fn folders() -> Result<Vec<PathBuf>, String> {
    Err("not_windows".into())
}

/// No shortcuts off Windows.
#[cfg(not(windows))]
pub fn make(_: &Path, _: &Path, _: &str, _: &str) -> Result<(), String> {
    Err("not_windows".into())
}

/// No shortcuts off Windows.
#[cfg(not(windows))]
pub fn read(_: &Path) -> Result<(PathBuf, String), String> {
    Err("not_windows".into())
}

/// The text up to the first NUL.
#[cfg(windows)]
fn wide(b: &[u16]) -> String {
    let n = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    String::from_utf16_lossy(&b[..n])
}

#[cfg(windows)]
fn shell_link() -> Result<windows::Win32::UI::Shell::IShellLinkW, String> {
    use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_INPROC_SERVER};
    use windows::Win32::UI::Shell::ShellLink;
    // SAFETY: the shell's own in-process ShellLink class; COM is initialised by the caller.
    unsafe { CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER) }
        .map_err(|e| format!("shell link: {e}"))
}

#[cfg(windows)]
fn known(id: &windows::core::GUID) -> Result<PathBuf, String> {
    use windows::Win32::System::Com::CoTaskMemFree;
    use windows::Win32::UI::Shell::{SHGetKnownFolderPath, KF_FLAG_DEFAULT};
    // SAFETY: `id` is a known-folder GUID; the returned string is freed once below.
    let p =
        unsafe { SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, None) }.map_err(|e| e.to_string())?;
    // SAFETY: `p` is the NUL-terminated string the call returned.
    let s = unsafe { p.to_string() };
    // SAFETY: `p` was allocated by the shell with CoTaskMemAlloc and is freed exactly once.
    unsafe { CoTaskMemFree(Some(p.0 as *const core::ffi::c_void)) };
    s.map(PathBuf::from).map_err(|e| e.to_string())
}

/// COM for this thread while it lives; uninitialised only when this call initialised it.
#[cfg(windows)]
struct Com(bool);

#[cfg(windows)]
impl Com {
    fn init() -> Com {
        use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};
        // SAFETY: plain call; S_OK / S_FALSE are paired with CoUninitialize in Drop, a thread
        // already in another apartment (RPC_E_CHANGED_MODE) is used as it is.
        Com(unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) }.is_ok())
    }
}

#[cfg(windows)]
impl Drop for Com {
    fn drop(&mut self) {
        if self.0 {
            // SAFETY: pairs the successful CoInitializeEx above on this same thread.
            unsafe { windows::Win32::System::Com::CoUninitialize() };
        }
    }
}
