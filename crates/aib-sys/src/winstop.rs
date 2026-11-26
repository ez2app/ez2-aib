//! U2 (PLAN-win-upgrade-path-90856-1126; 3e53b 11-26 11:2x): the Windows setup ends the one
//! running copy of the installed `aibd.exe` before it swaps the program. aibd on Windows has no
//! console to stop it with (it only hears Ctrl-C), so `TerminateProcess` is the only way; a
//! same-account program can already end its own processes, so this is no new power.
//!
//! 3e53b's conditions, all here:
//! - each candidate is opened once (`PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION |
//!   SYNCHRONIZE`) and its identity is read **on that handle**: image path (canonical, equal to
//!   the installed `aibd.exe`, which must not be a link), user SID and session (its token),
//!   creation time; the same handle is the one ended. No pid is opened again, no `taskkill`.
//! - exactly one match is ended; none or more than one ⇒ nothing is ended ([`pick`]).
//! - the process name from the snapshot is only a pre-filter (fewer handles opened), never part
//!   of the identity.
//!
//! [`pick`] is plain Rust and tested on every platform; the Windows calls are in [`stop_one`].

use std::path::{Path, PathBuf};

/// What a running process is, read through its own handle.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seen {
    /// Canonical image path (`QueryFullProcessImageNameW`, then `canonicalize`).
    pub path: PathBuf,
    /// The token's user SID, as bytes.
    pub sid: Vec<u8>,
    /// The token's session id.
    pub session: u32,
    /// Creation time (FILETIME 100 ns), for the report.
    pub created: u64,
}

/// Which of the seen processes is the one to end.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pick {
    /// Exactly one matches: its index.
    One(usize),
    /// None matches.
    None,
    /// This many match: end none of them.
    Many(usize),
}

/// The one process whose path, SID and session are all the wanted ones; none or more than one
/// ⇒ not [`Pick::One`].
pub fn pick(seen: &[Seen], path: &Path, sid: &[u8], session: u32) -> Pick {
    let hits: Vec<usize> = seen
        .iter()
        .enumerate()
        .filter(|(_, s)| s.path == path && s.sid == sid && s.session == session)
        .map(|(i, _)| i)
        .collect();
    match hits.as_slice() {
        [i] => Pick::One(*i),
        [] => Pick::None,
        many => Pick::Many(many.len()),
    }
}

/// End the one running copy of `exe` (this user, this session; never this process), then wait
/// up to `wait` for it to be gone. `Ok((pid, created))` of the one ended. Errors are lines for
/// the setup: `stop_link`, `stop_none`, `stop_many`, `stop_failed`, `stop_still_running`.
#[cfg(windows)]
pub fn stop_one(exe: &Path, wait: std::time::Duration) -> Result<(u32, u64), String> {
    use crate::job::Process;
    let meta = std::fs::symlink_metadata(exe).map_err(|e| format!("stop_failed: {e}"))?;
    if meta.file_type().is_symlink() {
        return Err(format!("stop_link: {} is a link", exe.display()));
    }
    let want = std::fs::canonicalize(exe).map_err(|e| format!("stop_failed: {e}"))?;
    let name = exe
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let me = std::process::id();
    let (my_sid, my_session) = win::own_identity().map_err(|e| format!("stop_failed: {e}"))?;
    let mut opened: Vec<Process> = Vec::new();
    let mut seen: Vec<Seen> = Vec::new();
    for (pid, exe_name) in win::snapshot().map_err(|e| format!("stop_failed: {e}"))? {
        if pid == me || exe_name.to_lowercase() != name {
            continue;
        }
        let Ok(p) = Process::open_terminable(pid) else {
            continue;
        };
        let Ok(s) = win::seen(&p) else {
            continue;
        };
        opened.push(p);
        seen.push(s);
    }
    let i = match pick(&seen, &want, &my_sid, my_session) {
        Pick::One(i) => i,
        Pick::None => {
            return Err(format!(
                "stop_none: no {} of this user in this session could be opened",
                want.display()
            ))
        }
        Pick::Many(n) => {
            return Err(format!(
                "stop_many: {n} copies of {} run as this user in this session; none ended",
                want.display()
            ))
        }
    };
    let p = &opened[i];
    p.terminate(1).map_err(|e| format!("stop_failed: {e}"))?;
    match p.wait(wait) {
        Ok(true) => Ok((p.pid(), seen[i].created)),
        Ok(false) => Err(format!(
            "stop_still_running: pid {} after {wait:?}",
            p.pid()
        )),
        Err(e) => Err(format!("stop_failed: {e}")),
    }
}

#[cfg(windows)]
mod win {
    use super::Seen;
    use crate::job::Process;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Security::{
        GetLengthSid, GetTokenInformation, TokenSessionId, TokenUser, TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, OpenProcessToken, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    };

    /// Every (pid, exe name) in a process snapshot.
    pub fn snapshot() -> Result<Vec<(u32, String)>, String> {
        // SAFETY: flags and pid 0 (all processes); the handle is closed below.
        let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
        if snap == INVALID_HANDLE_VALUE {
            return Err("process snapshot".into());
        }
        let mut out = Vec::new();
        // SAFETY: plain-data struct; all-zero is valid and dwSize is set before use.
        let mut e: PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        e.dwSize = u32::try_from(std::mem::size_of::<PROCESSENTRY32W>()).unwrap_or(0);
        // SAFETY: `snap` is a live snapshot handle; `e` is writable with dwSize set.
        let mut ok = unsafe { Process32FirstW(snap, &mut e) };
        while ok != 0 {
            let len = e
                .szExeFile
                .iter()
                .position(|&c| c == 0)
                .unwrap_or(e.szExeFile.len());
            out.push((
                e.th32ProcessID,
                String::from_utf16_lossy(&e.szExeFile[..len]),
            ));
            // SAFETY: same handle and struct as above.
            ok = unsafe { Process32NextW(snap, &mut e) };
        }
        // SAFETY: `snap` came from CreateToolhelp32Snapshot and is closed once.
        unsafe { CloseHandle(snap) };
        Ok(out)
    }

    /// The identity of `p`, read on its own handle.
    pub fn seen(p: &Process) -> Result<Seen, String> {
        let h = p.raw();
        let mut buf = vec![0u16; 32768];
        let mut len = u32::try_from(buf.len()).unwrap_or(0);
        // SAFETY: `h` is open with PROCESS_QUERY_LIMITED_INFORMATION; `buf` holds `len` u16s.
        let ok = unsafe {
            QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, buf.as_mut_ptr(), &mut len)
        };
        if ok == 0 {
            return Err("image path".into());
        }
        let raw = String::from_utf16_lossy(&buf[..len as usize]);
        let path = std::fs::canonicalize(&raw).map_err(|e| format!("image path: {e}"))?;
        let (sid, session) = token_identity(h)?;
        let created = p.created_100ns().map_err(|e| format!("created: {e}"))?;
        Ok(Seen {
            path,
            sid,
            session,
            created,
        })
    }

    /// This process's user SID and session.
    pub fn own_identity() -> Result<(Vec<u8>, u32), String> {
        // SAFETY: no arguments; a pseudo handle that needs no closing.
        token_identity(unsafe { GetCurrentProcess() })
    }

    /// User SID bytes and session id from the token of the process behind `h`.
    fn token_identity(h: HANDLE) -> Result<(Vec<u8>, u32), String> {
        let mut token: HANDLE = std::ptr::null_mut();
        // SAFETY: `h` is a process handle with limited query rights; `token` is an out pointer.
        if unsafe { OpenProcessToken(h, TOKEN_QUERY, &mut token) } == 0 {
            return Err("token".into());
        }
        let r = read_token(token);
        // SAFETY: `token` came from OpenProcessToken and is closed once.
        unsafe { CloseHandle(token) };
        r
    }

    fn read_token(token: HANDLE) -> Result<(Vec<u8>, u32), String> {
        let mut need = 0u32;
        // SAFETY: size query: null buffer of length 0, `need` receives the size.
        unsafe { GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut need) };
        if need == 0 {
            return Err("token user size".into());
        }
        let mut buf = vec![0u64; (need as usize).div_ceil(8)];
        // SAFETY: `buf` is at least `need` bytes and 8-aligned for TOKEN_USER.
        let ok = unsafe {
            GetTokenInformation(token, TokenUser, buf.as_mut_ptr().cast(), need, &mut need)
        };
        if ok == 0 {
            return Err("token user".into());
        }
        // SAFETY: the call above wrote a TOKEN_USER at the start of `buf`.
        let tu = unsafe { &*(buf.as_ptr() as *const TOKEN_USER) };
        let sid = tu.User.Sid;
        // SAFETY: `sid` points into `buf`, a valid SID the token call wrote.
        let n = unsafe { GetLengthSid(sid) } as usize;
        // SAFETY: the SID is `n` bytes long, inside `buf`, which outlives this read.
        let sid = unsafe { std::slice::from_raw_parts(sid as *const u8, n) }.to_vec();
        let mut session = 0u32;
        let mut got = 0u32;
        // SAFETY: TokenSessionId writes one u32 into `session`.
        let ok = unsafe {
            GetTokenInformation(
                token,
                TokenSessionId,
                std::ptr::addr_of_mut!(session).cast(),
                4,
                &mut got,
            )
        };
        if ok == 0 {
            return Err("token session".into());
        }
        Ok((sid, session))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(path: &str, sid: u8, session: u32) -> Seen {
        Seen {
            path: PathBuf::from(path),
            sid: vec![1, 5, sid],
            session,
            created: 7,
        }
    }
    const EXE: &str = "C:/Users/A/AppData/Local/AIB/aibd.exe";

    /// U2: only a process whose path, user and session all match is picked.
    #[test]
    fn only_the_installed_copy_of_this_user_and_session_is_picked() {
        let seen = [
            s("C:/Other/aibd.exe", 1, 2),
            s(EXE, 9, 2),
            s(EXE, 1, 3),
            s(EXE, 1, 2),
        ];
        assert_eq!(pick(&seen, Path::new(EXE), &[1, 5, 1], 2), Pick::One(3));
    }

    /// 3e53b knife 1: a copy at another path is never picked.
    #[test]
    fn a_copy_at_another_path_is_not_picked() {
        let seen = [s("C:/Other/aibd.exe", 1, 2)];
        assert_eq!(pick(&seen, Path::new(EXE), &[1, 5, 1], 2), Pick::None);
    }

    /// 3e53b knife 2: another user's copy is never picked.
    #[test]
    fn another_users_copy_is_not_picked() {
        let seen = [s(EXE, 9, 2)];
        assert_eq!(pick(&seen, Path::new(EXE), &[1, 5, 1], 2), Pick::None);
    }

    /// A copy in another session (another sign-in of the same user) is never picked.
    #[test]
    fn a_copy_in_another_session_is_not_picked() {
        let seen = [s(EXE, 1, 3)];
        assert_eq!(pick(&seen, Path::new(EXE), &[1, 5, 1], 2), Pick::None);
    }

    /// 3e53b knife 3: two matching copies ⇒ none is ended.
    #[test]
    fn two_matching_copies_end_none() {
        let seen = [s(EXE, 1, 2), s(EXE, 1, 2)];
        assert_eq!(pick(&seen, Path::new(EXE), &[1, 5, 1], 2), Pick::Many(2));
    }
}
