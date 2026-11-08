//! Windows owner-only ACLs without `unsafe`: the built-in `icacls.exe` (by
//! absolute path) sets a protected DACL with a single full-control entry for
//! the current user's SID, then the result is read back and checked. Any
//! failure is an error, so aibd refuses to start instead of writing a token
//! into a folder other accounts can read (review finding, 11-07).
//!
//! Level: CI-windows (GitHub runner). An interactive check with a second
//! account is still UNVERIFIED-desktop.

use std::fs::OpenOptions;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

fn system32(exe: &str) -> PathBuf {
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    PathBuf::from(root).join("System32").join(exe)
}

/// Current user's SID and `DOMAIN\user` name, from `whoami /user /fo csv /nh`.
pub fn current_user() -> io::Result<(String, String)> {
    let o = Command::new(system32("whoami.exe"))
        .args(["/user", "/fo", "csv", "/nh"])
        .output()?;
    if !o.status.success() {
        return Err(io::Error::other("whoami /user failed"));
    }
    let line = String::from_utf8_lossy(&o.stdout);
    let fields: Vec<String> = line
        .trim()
        .split("\",\"")
        .map(|f| f.trim_matches('"').to_string())
        .collect();
    match fields.as_slice() {
        [name, sid] if sid.starts_with("S-1-") => Ok((sid.clone(), name.clone())),
        _ => Err(io::Error::other("unexpected whoami output")),
    }
}

#[doc(hidden)]
pub fn acl_entries_tool() -> PathBuf {
    system32("icacls.exe")
}

fn icacls(path: &Path, args: &[&str]) -> io::Result<String> {
    let o = Command::new(system32("icacls.exe"))
        .arg(path)
        .args(args)
        .output()?;
    if !o.status.success() {
        return Err(io::Error::other(format!(
            "icacls failed on {}",
            path.display()
        )));
    }
    Ok(String::from_utf8_lossy(&o.stdout).into_owned())
}

/// ACL entries of `path` as `icacls` prints them (one `NAME:(perms)` each).
pub fn acl_entries(path: &Path) -> io::Result<Vec<String>> {
    let out = icacls(path, &[])?;
    let shown = path.display().to_string();
    let mut entries = Vec::new();
    for (i, line) in out.lines().enumerate() {
        let l = if i == 0 {
            line.strip_prefix(shown.as_str()).unwrap_or(line)
        } else {
            line
        };
        let l = l.trim();
        if l.contains(":(") {
            entries.push(l.to_string());
        }
    }
    Ok(entries)
}

/// Ok only if the DACL has exactly one entry and it is the current user.
pub fn verify_owner_only(path: &Path) -> io::Result<()> {
    let (_sid, name) = current_user()?;
    let e = acl_entries(path)?;
    let ok = e.len() == 1
        && e[0]
            .to_ascii_lowercase()
            .starts_with(&format!("{}:", name.to_ascii_lowercase()));
    if ok {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "{} is not owner-only ({} ACL entries: {e:?})",
                path.display(),
                e.len()
            ),
        ))
    }
}

fn set_owner_only(path: &Path, inherit: bool) -> io::Result<()> {
    let (sid, _) = current_user()?;
    let grant = if inherit {
        format!("*{sid}:(OI)(CI)F")
    } else {
        format!("*{sid}:F")
    };
    icacls(path, &["/inheritance:r", "/grant:r", &grant])?;
    // A parent without inheritable ACEs makes Windows stamp the creator's
    // default DACL (self, SYSTEM, Administrators) onto the new object as
    // *explicit* entries; `/inheritance:r` cannot remove those and
    // `/grant:r` only replaces our own. Remove every other principal.
    let (_, name) = current_user()?;
    let me = format!("{}:", name.to_ascii_lowercase());
    for entry in acl_entries(path)? {
        if entry.to_ascii_lowercase().starts_with(&me) {
            continue;
        }
        if let Some((principal, _)) = entry.split_once(":(") {
            icacls(path, &["/remove", principal])?;
        }
    }
    verify_owner_only(path)
}

pub fn restrict_dir(p: &Path) -> io::Result<()> {
    set_owner_only(p, true)
}

pub fn private_file_options(_o: &mut OpenOptions) {}

/// Called on the empty temp file before any secret is written into it.
pub fn restrict_file(p: &Path) -> io::Result<()> {
    set_owner_only(p, false)
}
