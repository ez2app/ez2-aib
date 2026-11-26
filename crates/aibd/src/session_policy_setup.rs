//! W-10 (IFACE-w10-session-policy-c2f0b-1125 r5; 3e53b PRE-READ, f163c r4 / r5, fc7f9 #894):
//! the page sets up which AI programs this computer may open, without a terminal.
//!
//! - `GET /api/v1/session-policy`: the policy file's state (`loaded` / `missing` / `invalid`),
//!   the programs in force now, what a fresh look finds for `claude` / `codex` and of what
//!   kind, whether a policy could be written, and the file's path. Read again every time.
//! - act `session_policy_init`: write a policy with the native programs found, after a fresh
//!   owner check each time (the operation has no hold, a ticket is used once), then read it
//!   back and put it in force without a restart.
//!
//! The request names nothing (r1): the body is `type`, and `ticket` on the resend (r4); the
//! names are the fixed [`PROVIDERS`], the folders are aibd's own `PATH` plus the native
//! installers' `~/.local/bin`. What is written is what the owner approved: the file's text is
//! the ask's parameters, so a look that finds something else at the resend is not approved.
//!
//! A file already there, readable or not, is never replaced (r3: `policy_exists`); the page
//! says to remove it first. This is the same-user file `aibd ctl policy init` writes and
//! [`crate::allowlist`] documents as not a same-user boundary: the owner check stops the AI on
//! the page from pressing the button, not a local program from editing the file.

use crate::allowlist::{self, is_interpreter, kind_of, Kind, SessionPolicy};
use crate::api::AppState;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// The page act.
pub const KIND: &str = "session_policy_init";
/// The owner-check operation (one act, one check: no hold in [`crate::grants`]).
pub const OP: &str = "session_policy.init";
/// The only programs looked for; the request cannot name another.
pub const PROVIDERS: [&str; 2] = ["claude", "codex"];

/// The name shown for a provider (the page's own table says the same).
pub fn brand(name: &str) -> Option<&'static str> {
    match name {
        "claude" => Some("Claude"),
        "codex" => Some("Codex"),
        _ => None,
    }
}

/// The window's top words for a policy text: the fixed names of its programs, in order (`None` when
/// one is not a fixed provider, so the window says only the operation's name).
pub fn brands_of(v: &Value) -> Option<String> {
    let names = v["programs"]
        .as_array()?
        .iter()
        .map(|p| p["name"].as_str().and_then(brand))
        .collect::<Option<Vec<_>>>()?;
    (!names.is_empty()).then(|| names.join("、"))
}

/// Whether the page may write a policy on this computer: Windows (W-10). On macOS
/// `aibd ctl policy init` also checks the code signature, which this act does not, so the page
/// only reads there. Tests that set [`test_search_dirs`] write on any platform.
pub fn writes_here() -> bool {
    cfg!(windows)
        || TEST_DIRS
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_some()
}

/// What a look found for one provider (IFACE r5 §1a `kind`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LookKind {
    Native,
    /// An npm install: a `.cmd` / `.bat` / `.ps1` launcher, or a file that runs through node.
    Npm,
    Script,
    Interpreter,
    NotFound,
}

impl LookKind {
    pub fn word(self) -> &'static str {
        match self {
            LookKind::Native => "native",
            LookKind::Npm => "npm",
            LookKind::Script => "script",
            LookKind::Interpreter => "interpreter",
            LookKind::NotFound => "not_found",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Look {
    pub name: String,
    pub kind: LookKind,
    /// The file as found (not its real path: a version update that moves the target is
    /// followed, [`crate::allowlist::resolve`] resolves again on every spawn).
    pub path: Option<PathBuf>,
    /// Why it is not usable ("" when native).
    pub why: String,
}

const SHIMS: [&str; 3] = ["cmd", "bat", "ps1"];

/// [`look_in`] with this platform's file names.
pub fn look(name: &str, dirs: &[PathBuf]) -> Look {
    look_in(name, dirs, cfg!(windows))
}

/// The first `name` (`name.exe` when `windows`) in `dirs` that aibd's own gate would start:
/// an absolute folder, a native executable by its first bytes, not a shell / interpreter by
/// name. A launcher is never taken; it is only reported (an npm `claude.cmd` runs node, which
/// aibd refuses with `provider_install_unsupported`). Otherwise the first reason met.
pub fn look_in(name: &str, dirs: &[PathBuf], windows: bool) -> Look {
    let file = if windows {
        format!("{name}.exe")
    } else {
        name.to_owned()
    };
    let mut first: Option<Look> = None;
    for d in dirs.iter().filter(|d| d.is_absolute()) {
        let exe = d.join(&file);
        if exe.is_file() {
            match check(&exe) {
                Ok(()) => {
                    return Look {
                        name: name.to_owned(),
                        kind: LookKind::Native,
                        path: Some(exe),
                        why: String::new(),
                    }
                }
                Err((kind, why)) => {
                    if first.is_none() {
                        first = Some(Look {
                            name: name.to_owned(),
                            kind,
                            why: format!("{}: {why}", exe.display()),
                            path: Some(exe),
                        });
                    }
                }
            }
        }
        for shim in SHIMS {
            let p = d.join(format!("{name}.{shim}"));
            if first.is_none() && p.is_file() {
                first = Some(Look {
                    name: name.to_owned(),
                    kind: LookKind::Npm,
                    why: format!(
                        "{}: a .{shim} launcher (npm installs run node, which aibd refuses); install the native {name}",
                        p.display()
                    ),
                    path: Some(p),
                });
            }
        }
    }
    first.unwrap_or(Look {
        name: name.to_owned(),
        kind: LookKind::NotFound,
        path: None,
        why: "not found".into(),
    })
}

fn check(exe: &Path) -> Result<(), (LookKind, String)> {
    let real = std::fs::canonicalize(exe)
        .map_err(|e| (LookKind::NotFound, format!("cannot resolve: {e}")))?;
    if is_interpreter(&real) || is_interpreter(exe) {
        return Err((LookKind::Interpreter, "a shell or interpreter".to_string()));
    }
    let mut head = Vec::with_capacity(512);
    std::fs::File::open(&real)
        .and_then(|f| f.take(512).read_to_end(&mut head))
        .map_err(|e| (LookKind::NotFound, format!("cannot read: {e}")))?;
    match kind_of(&real, &head) {
        Kind::Native => Ok(()),
        Kind::Script => Err((
            LookKind::Script,
            "a script, not a native executable".to_string(),
        )),
        Kind::Node => Err((
            LookKind::Npm,
            "runs through node; install the native build".to_string(),
        )),
    }
}

static TEST_DIRS: Mutex<Option<Vec<PathBuf>>> = Mutex::new(None);

/// Tests only: look in these folders instead of `PATH` and `~/.local/bin`. Rust code sets it;
/// no request, file or env var can.
#[doc(hidden)]
pub fn test_search_dirs(dirs: Option<Vec<PathBuf>>) {
    *TEST_DIRS.lock().unwrap_or_else(|e| e.into_inner()) = dirs;
}

/// Where the providers are looked for: aibd's own `PATH`, then the native installers'
/// `~/.local/bin` (`%USERPROFILE%\.local\bin` on Windows), often not on `PATH`, then the
/// copies aibd itself placed under `data` ([`with_placed`]).
pub fn search_dirs(data: &Path) -> Vec<PathBuf> {
    if let Some(d) = TEST_DIRS.lock().unwrap_or_else(|e| e.into_inner()).clone() {
        return d;
    }
    with_placed(user_dirs(), data, aib_seatopen::measured::this_platform())
}

/// `dirs`, then aibd's own placed copies (L1 provider_fetch / L2, IFACE-provider-lifecycle
/// §3.4; ebab8 11-26 12:14 C): `<data>/providers/<name>/<version>/` for this platform's
/// measured rows only, never another folder under `providers/` (a version not in the table
/// is not run, Andy 11-26 02:3x). After the user's own folders: a native program the user
/// installed is the one they chose. The look still takes only a native executable there.
pub fn with_placed(mut dirs: Vec<PathBuf>, data: &Path, platform: Option<&str>) -> Vec<PathBuf> {
    let Some(platform) = platform else {
        return dirs;
    };
    if !data.is_absolute() {
        return dirs;
    }
    let placed = aib_seatopen::MEASURED
        .iter()
        .filter(|m| PROVIDERS.contains(&m.provider) && m.platform == platform)
        .filter_map(|m| {
            let d =
                crate::provider_fetch::placed_dir(data, &crate::provider_fetch::FetchRow::from(m));
            no_link_below(data, &d, m.provider).then_some(d)
        });
    dirs.extend(placed);
    dirs
}

/// 3e53b 11-26 12:16 / 12:2x: `data` itself, every folder from it down to `dir` (the four
/// levels L2's placed check reads: data, providers, provider, version) and the program in it
/// (either file name) are themselves and not links (a Windows junction counts as one); a part
/// that is missing is fine (nothing to find).
fn no_link_below(data: &Path, dir: &Path, provider: &str) -> bool {
    let Ok(rest) = dir.strip_prefix(data) else {
        return false;
    };
    let link = |p: &Path| std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink());
    if link(data) {
        return false;
    }
    let mut at = data.to_path_buf();
    for part in rest.components() {
        at.push(part);
        if link(&at) {
            return false;
        }
    }
    ![provider.to_owned(), format!("{provider}.exe")]
        .iter()
        .any(|f| link(&dir.join(f)))
}

/// c2f0b 11-26 12:27: at `session_policy_init`, before the owner is asked, a program found in
/// aibd's placed folder is exactly the measured one (L1 `verify_placed`: the whole file's
/// SHA-256 and size) or it counts as not found: the window asks the owner to approve that very
/// program. L2 checks it again at each open. `GET` (`full` false) checks only the size the
/// table names, so the page and the init agree on what is found without a full hash on every
/// read (3e53b REVIEW-polook r1 N1); only a same-size file with other bytes differs, and init
/// and L2 refuse that one.
pub fn placed_verified(
    looks: Vec<Look>,
    data: &Path,
    platform: Option<&str>,
    full: bool,
) -> Vec<Look> {
    looks
        .into_iter()
        .map(|l| {
            let Some(path) = l.path.clone().filter(|_| l.kind == LookKind::Native) else {
                return l;
            };
            let row = aib_seatopen::MEASURED
                .iter()
                .filter(|m| m.provider == l.name && Some(m.platform) == platform)
                .map(crate::provider_fetch::FetchRow::from)
                .find(|r| {
                    path.parent() == Some(crate::provider_fetch::placed_dir(data, r).as_path())
                });
            let Some(row) = row else {
                return l;
            };
            let ok = if full {
                crate::provider_fetch::verify_placed(&row, &path).is_ok()
            } else {
                std::fs::symlink_metadata(&path).is_ok_and(|m| m.len() == row.size)
            };
            if !ok {
                return Look {
                    name: l.name,
                    kind: LookKind::NotFound,
                    path: None,
                    why: format!(
                        "{}: not the measured {} {}",
                        path.display(),
                        row.provider,
                        row.version
                    ),
                };
            }
            l
        })
        .collect()
}

fn user_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).collect())
        .unwrap_or_default();
    let home = if cfg!(windows) {
        std::env::var_os("USERPROFILE")
    } else {
        std::env::var_os("HOME")
    };
    if let Some(h) = home {
        dirs.push(PathBuf::from(h).join(".local").join("bin"));
    }
    dirs
}

/// The file aibd reads its policy from: `AIBD_SESSION_POLICY`, else the data folder's
/// [`allowlist::DEFAULT_POLICY_FILE`] (the same rule as [`crate::config`]).
pub fn policy_path(data: &Path) -> PathBuf {
    match std::env::var_os("AIBD_SESSION_POLICY") {
        Some(p) => PathBuf::from(p),
        None => data.join(allowlist::DEFAULT_POLICY_FILE),
    }
}

/// `missing` only when nothing is there (a link to nowhere is there); `invalid` when it
/// cannot be looked at or does not load.
pub fn file_state(p: &Path) -> &'static str {
    match std::fs::symlink_metadata(p) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => "missing",
        Err(_) => "invalid",
        Ok(_) => match allowlist::from_file(p) {
            Ok(_) => "loaded",
            Err(_) => "invalid",
        },
    }
}

/// Names that carry a key or a credential (Andy 11-26 02:0x via ebab8: a seat uses the login the
/// provider already has on this computer, and a policy the page writes passes no key). `aibd ctl
/// policy init` still lists them; a person who wants one adds it to the file by hand.
pub const KEY_ENV: [&str; 6] = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
];

fn is_key(name: &str) -> bool {
    KEY_ENV.iter().any(|k| k.eq_ignore_ascii_case(name))
}

/// The policy file's text for what `looks` found native (paths that are not UTF-8 are left
/// out), with the [`allowlist::PREFILL_ENV`] names that are set, but no [`KEY_ENV`]; `None` when no native one.
/// Checked with aibd's own parser: what is written is what aibd will load.
pub fn policy_text(looks: &[Look], is_set: impl Fn(&str) -> bool) -> Option<String> {
    let programs: Vec<Value> = looks
        .iter()
        .filter(|l| l.kind == LookKind::Native)
        .filter_map(|l| {
            let p = l.path.as_ref()?.to_str()?;
            Some(json!({"name": l.name, "path": p}))
        })
        .collect();
    if programs.is_empty() {
        return None;
    }
    let env = allowlist::prefill_env(|k| !is_key(k) && is_set(k));
    let text = json!({"programs": programs, "env": env}).to_string();
    allowlist::parse(&text).ok()?;
    Some(text)
}

/// Why nothing was written.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InitRefused {
    NoNative,
    Exists,
    WriteFailed,
}

impl InitRefused {
    pub fn code(self) -> &'static str {
        match self {
            InitRefused::NoNative => "policy_no_native",
            InitRefused::Exists => "policy_exists",
            InitRefused::WriteFailed => "policy_write_failed",
        }
    }
    fn status(self) -> StatusCode {
        match self {
            InitRefused::WriteFailed => StatusCode::INTERNAL_SERVER_ERROR,
            _ => StatusCode::CONFLICT,
        }
    }
    fn message(self) -> &'static str {
        match self {
            InitRefused::NoNative => "no native claude / codex executable found; nothing written",
            InitRefused::Exists => "a session policy file is already there; it is never replaced",
            InitRefused::WriteFailed => {
                "the policy could not be written and read back; nothing changed"
            }
        }
    }
}

static SEQ: AtomicU64 = AtomicU64::new(0);

/// Write `contents` to `path` only if nothing is there: a fresh temporary file beside it, then
/// a hard link (fails when `path` exists), then the temporary file removed. The folder is
/// created private when missing; on Windows the file takes that folder's owner-only ACL, on
/// POSIX it is 0600. `AlreadyExists` means something was there.
pub fn write_new(path: &Path, contents: &[u8]) -> std::io::Result<()> {
    write_new_with(path, contents, |p| std::fs::remove_file(p))
}

/// [`write_new`] with the temporary file's removal handed in (tests make it fail). Once the
/// hard link is made the policy is written: a temporary file that cannot be removed (on
/// Windows an antivirus often holds a file just made) is logged and left, never a failure,
/// or the page would say nothing was written while the file is there and the next try
/// answers `policy_exists` (f163c REVIEW-w10-c2f0b-f163c-42d83daf L1).
pub(crate) fn write_new_with(
    path: &Path,
    contents: &[u8],
    remove: impl Fn(&Path) -> std::io::Result<()>,
) -> std::io::Result<()> {
    use std::fs::OpenOptions;
    use std::io::Write;
    #[cfg(unix)]
    use std::os::unix::fs::OpenOptionsExt;
    let bad = |m: &str| std::io::Error::new(std::io::ErrorKind::InvalidInput, m.to_owned());
    let parent = path.parent().ok_or_else(|| bad("policy has no parent"))?;
    if !parent.exists() {
        crate::platform::create_private_dir(parent)?;
    }
    let file_name = path
        .file_name()
        .ok_or_else(|| bad("policy has no file name"))?
        .to_string_lossy();
    let temporary = parent.join(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(&temporary)?;
    let written = file
        .write_all(contents)
        .and_then(|_| file.sync_all())
        .and_then(|_| std::fs::hard_link(&temporary, path));
    drop(file);
    let removed = remove(&temporary);
    written?;
    if let Err(e) = removed {
        tracing::warn!(error = %e, file = %temporary.display(), "policy written; its temporary file is left");
    }
    Ok(())
}

/// Write the policy and read it back the way aibd reads it at start. Never replaces a file
/// (r3); a file written but not loadable is removed again (3e53b condition 3).
pub fn write_and_load(path: &Path, text: &str) -> Result<SessionPolicy, InitRefused> {
    if !path.is_absolute() || allowlist::parse(text).is_err() {
        return Err(InitRefused::WriteFailed);
    }
    match std::fs::symlink_metadata(path) {
        Ok(_) => return Err(InitRefused::Exists),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err(InitRefused::WriteFailed),
    }
    match write_new(path, text.as_bytes()) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => return Err(InitRefused::Exists),
        Err(_) => return Err(InitRefused::WriteFailed),
    }
    loaded_as_written(path, text)
}

/// 3e53b L1 (REVIEW-w10 adbb97c6): what is put in force is the text the owner approved, never
/// the file read back (a same-user program could replace it between the link and the read).
/// The file is read back only to check it is that text and that aibd would load it at start
/// (owner, mode); otherwise it is removed and nothing changes.
pub fn loaded_as_written(path: &Path, text: &str) -> Result<SessionPolicy, InitRefused> {
    let same = std::fs::read(path).is_ok_and(|b| b == text.as_bytes());
    match (same, allowlist::from_file(path), allowlist::parse(text)) {
        (true, Ok(_), Ok(p)) => Ok(p),
        _ => {
            let _ = std::fs::remove_file(path);
            Err(InitRefused::WriteFailed)
        }
    }
}

/// The window's object line: the paths to be written, in order.
fn object_words(text: &str) -> String {
    let v: Value = serde_json::from_str(text).unwrap_or(Value::Null);
    v["programs"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|p| p["path"].as_str())
                .collect::<Vec<_>>()
                .join("、")
        })
        .unwrap_or_default()
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"code": code, "reason": code, "message": message,
                    "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

fn refused(r: InitRefused) -> Response {
    refuse(r.status(), r.code(), r.message())
}

/// `GET /api/v1/session-policy` (r5 §1a + r3 `policy_path`): no window, read on every call.
pub async fn state(State(st): State<AppState>) -> Response {
    let data = crate::registry::data_of(&st);
    let path = policy_path(&data);
    let looked = tokio::task::spawn_blocking(move || {
        let dirs = search_dirs(&data);
        let found: Vec<Look> = PROVIDERS.iter().map(|n| look(n, &dirs)).collect();
        let found = placed_verified(found, &data, aib_seatopen::measured::this_platform(), false);
        (file_state(&path), found, path)
    })
    .await;
    let Ok((policy, found, path)) = looked else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "look failed");
    };
    let programs: Vec<String> = st
        .policy()
        .programs
        .iter()
        .map(|p| p.name.clone())
        .collect();
    let rows: Vec<Value> = found
        .iter()
        .map(|l| {
            json!({"name": l.name, "kind": l.kind.word(),
                   "path": l.path.as_ref().map(|p| p.to_string_lossy().into_owned())})
        })
        .collect();
    (
        StatusCode::OK,
        Json(json!({
            "policy": policy,
            "programs": programs,
            "found": rows,
            "can_write": writes_here() && found.iter().any(|l| l.kind == LookKind::Native),
            "policy_path": path.to_string_lossy(),
        })),
    )
        .into_response()
}

/// act `session_policy_init` (r2 / r4): the caller checked it is the built-in page.
pub async fn init(st: &AppState, body: Value, by: String) -> Response {
    // r4: `type`, and the ticket on the resend; anything else is not this act
    let Some(o) = body.as_object() else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "policy_bad_request",
            "body must be an object",
        );
    };
    if o.keys().any(|k| k != "type" && k != "ticket") {
        return refuse(
            StatusCode::BAD_REQUEST,
            "policy_bad_request",
            "only type, and ticket on the resend",
        );
    }
    let ticket = match o.get("ticket") {
        None => None,
        Some(Value::String(t)) => Some(t.clone()),
        Some(_) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "policy_bad_request",
                "ticket is a string",
            )
        }
    };
    if !writes_here() {
        return refuse(
            StatusCode::CONFLICT,
            InitRefused::NoNative.code(),
            "this computer's policy is written by `aibd ctl policy init` (it checks the signature)",
        );
    }
    let data = crate::registry::data_of(st);
    let path = policy_path(&data);
    let p2 = path.clone();
    let looked = tokio::task::spawn_blocking(move || {
        let exists = !matches!(std::fs::symlink_metadata(&p2),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound);
        let dirs = search_dirs(&data);
        let found: Vec<Look> = PROVIDERS.iter().map(|n| look(n, &dirs)).collect();
        let found = placed_verified(found, &data, aib_seatopen::measured::this_platform(), true);
        (
            exists,
            policy_text(&found, |k| std::env::var_os(k).is_some()),
        )
    })
    .await;
    let Ok((exists, text)) = looked else {
        return refused(InitRefused::WriteFailed);
    };
    if exists {
        return refused(InitRefused::Exists);
    }
    let Some(text) = text else {
        return refused(InitRefused::NoNative);
    };
    // the owner approves this very text (its sha is the parameters'); a look at the resend
    // that finds something else is another ask
    let ask = crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: object_words(&text),
        params: text.clone().into_bytes(),
        by,
    };
    match ticket {
        None => return crate::repos::asked(&ask),
        Some(t) => {
            if !crate::owner_ok::approved_for(&t, &ask) {
                return crate::repos::not_approved();
            }
        }
    }
    let written = tokio::task::spawn_blocking(move || write_and_load(&path, &text)).await;
    match written {
        Ok(Ok(policy)) => {
            let policy = Arc::new(policy);
            st.sessions.set_policy(policy.clone());
            crate::preview::replace_parent_policy(policy.clone());
            let programs: Vec<String> = policy.programs.iter().map(|p| p.name.clone()).collect();
            (
                StatusCode::OK,
                Json(json!({"done": true, "policy": "loaded", "programs": programs})),
            )
                .into_response()
        }
        Ok(Err(r)) => refused(r),
        Err(_) => refused(InitRefused::WriteFailed),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn file(d: &Path, name: &str, body: &[u8]) {
        std::fs::write(d.join(name), body).unwrap();
    }

    /// WIN4-3 (moved from ctl.rs with the look): a launcher, a node script named `.exe`, a text
    /// file are never taken; a PE is.
    #[test]
    fn a_look_takes_only_a_native_executable() {
        let d = tempfile::tempdir().unwrap();
        let dirs = vec![d.path().to_path_buf()];
        assert_eq!(look_in("claude", &dirs, true).kind, LookKind::NotFound);
        file(d.path(), "claude.cmd", b"@\"%~dp0\\node.exe\" cli.js %*");
        let l = look_in("claude", &dirs, true);
        assert_eq!(l.kind, LookKind::Npm, "{l:?}");
        assert!(l.why.contains("claude.cmd"), "{l:?}");
        file(d.path(), "claude.exe", b"#!/usr/bin/env node\n");
        let l = look_in("claude", &dirs, true);
        assert_eq!(l.kind, LookKind::Npm, "reason: node_script_taken {l:?}");
        assert!(l.why.contains("node"), "{l:?}");
        file(d.path(), "claude.exe", b"echo hi\r\n");
        assert_eq!(look_in("claude", &dirs, true).kind, LookKind::Script);
        file(d.path(), "claude.exe", b"MZ\x90\x00");
        let l = look_in("claude", &dirs, true);
        assert_eq!(l.kind, LookKind::Native, "{l:?}");
        assert_eq!(l.path, Some(d.path().join("claude.exe")));
    }

    /// ebab8 11-26 12:14 C: the copy L1 / L2 placed is found after the user's own folders,
    /// only in a measured version's folder of this platform.
    #[test]
    fn aibds_placed_copy_is_looked_for_after_the_users_folders_and_only_a_measured_one() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data");
        let user = d.path().join("user");
        std::fs::create_dir_all(&user).unwrap();
        assert_eq!(
            with_placed(vec![user.clone()], &data, Some("win32-x64")),
            vec![
                user.clone(),
                data.join("providers").join("claude").join("2.1.326")
            ],
            "reason: placed_dirs"
        );
        assert_eq!(
            with_placed(vec![user.clone()], &data, None),
            vec![user.clone()],
            "reason: no_platform"
        );
        let placed = data.join("providers").join("claude").join("2.1.326");
        let other = data.join("providers").join("claude").join("9.9.9");
        std::fs::create_dir_all(&placed).unwrap();
        std::fs::create_dir_all(&other).unwrap();
        file(&other, "claude.exe", b"MZ\x90\x00");
        let dirs = with_placed(vec![user.clone()], &data, Some("win32-x64"));
        assert_eq!(
            look_in("claude", &dirs, true).kind,
            LookKind::NotFound,
            "reason: unmeasured_version_taken"
        );
        file(&placed, "claude.exe", b"MZ\x90\x00");
        let l = look_in("claude", &dirs, true);
        assert_eq!(
            l.path,
            Some(placed.join("claude.exe")),
            "reason: placed_not_found {l:?}"
        );
        file(&user, "claude.exe", b"MZ\x90\x00");
        let l = look_in("claude", &dirs, true);
        assert_eq!(
            l.path,
            Some(user.join("claude.exe")),
            "reason: user_not_first {l:?}"
        );
    }

    /// 3e53b 11-26 12:16: a relative data folder adds nothing.
    #[test]
    fn a_relative_data_folder_adds_no_placed_folder() {
        let user = vec![PathBuf::from("/u")];
        assert_eq!(
            with_placed(user.clone(), Path::new("data"), Some("win32-x64")),
            user,
            "reason: relative_placed"
        );
    }

    /// 3e53b 11-26 12:16: a placed folder (any level below data) or its program that is a
    /// link is never looked in.
    #[cfg(unix)]
    #[test]
    fn a_placed_folder_or_program_that_is_a_link_is_not_looked_in() {
        use std::os::unix::fs::symlink;
        let d = tempfile::tempdir().unwrap();
        let elsewhere = d.path().join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        file(&elsewhere, "claude.exe", b"MZ\x90\x00");
        let only = |data: &Path| with_placed(Vec::new(), data, Some("win32-x64"));
        // the version folder is a link
        let a = d.path().join("a");
        std::fs::create_dir_all(a.join("providers").join("claude")).unwrap();
        symlink(
            &elsewhere,
            a.join("providers").join("claude").join("2.1.326"),
        )
        .unwrap();
        assert_eq!(only(&a), Vec::<PathBuf>::new(), "reason: link_folder");
        // a folder on the way (providers) is a link
        let b = d.path().join("b");
        let real = d.path().join("real-providers");
        std::fs::create_dir_all(real.join("claude").join("2.1.326")).unwrap();
        std::fs::create_dir_all(&b).unwrap();
        symlink(&real, b.join("providers")).unwrap();
        assert_eq!(only(&b), Vec::<PathBuf>::new(), "reason: link_on_the_way");
        // the program is a link
        let c = d.path().join("c");
        let v = c.join("providers").join("claude").join("2.1.326");
        std::fs::create_dir_all(&v).unwrap();
        symlink(elsewhere.join("claude.exe"), v.join("claude.exe")).unwrap();
        assert_eq!(only(&c), Vec::<PathBuf>::new(), "reason: link_program");
        std::fs::remove_file(v.join("claude.exe")).unwrap();
        file(&v, "claude.exe", b"MZ\x90\x00");
        assert_eq!(only(&c), vec![v.clone()], "reason: plain_placed");
        // data itself is a link
        let e = d.path().join("e");
        symlink(&c, &e).unwrap();
        assert_eq!(only(&e), Vec::<PathBuf>::new(), "reason: link_data");
    }

    /// 3e53b 11-26 12:2x: what the policy names is the placed path as written,
    /// `<data>/providers/<provider>/<version>/<program>` (not a resolved or other name), so
    /// L2's placed check at the open compares the same text.
    #[test]
    fn the_policy_names_the_placed_program_by_its_placed_path() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data");
        let m = aib_seatopen::measured_for("claude", "win32-x64").unwrap();
        let placed =
            crate::provider_fetch::placed_dir(&data, &crate::provider_fetch::FetchRow::from(m));
        std::fs::create_dir_all(&placed).unwrap();
        file(&placed, "claude.exe", b"MZ\x90\x00");
        let dirs = with_placed(Vec::new(), &data, Some("win32-x64"));
        let text = policy_text(&[look_in("claude", &dirs, true)], |_| false).unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            v["programs"][0]["path"].as_str(),
            placed.join("claude.exe").to_str(),
            "reason: placed_path_literal"
        );
    }

    /// c2f0b 11-26 12:27: at init a placed program that is not the measured file is not
    /// found (the owner is never asked about it); a program in the user's own folder is left
    /// as the look found it.
    #[test]
    fn at_init_a_placed_program_must_be_the_measured_file() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data");
        let m = aib_seatopen::measured_for("claude", "win32-x64").unwrap();
        let placed =
            crate::provider_fetch::placed_dir(&data, &crate::provider_fetch::FetchRow::from(m));
        std::fs::create_dir_all(&placed).unwrap();
        file(&placed, "claude.exe", b"MZ\x90\x00");
        let dirs = with_placed(Vec::new(), &data, Some("win32-x64"));
        let found = vec![look_in("claude", &dirs, true)];
        assert_eq!(found[0].kind, LookKind::Native);
        let v = placed_verified(found.clone(), &data, Some("win32-x64"), false);
        assert_eq!(v[0].kind, LookKind::NotFound, "reason: placed_size {v:?}");
        let v = placed_verified(found, &data, Some("win32-x64"), true);
        assert_eq!(
            v[0].kind,
            LookKind::NotFound,
            "reason: placed_unverified {v:?}"
        );
        assert_eq!(v[0].path, None, "{v:?}");
        assert!(
            v[0].why.contains("not the measured claude 2.1.326"),
            "{v:?}"
        );
        assert_eq!(
            policy_text(&v, |_| false),
            None,
            "reason: placed_unverified"
        );
        let user = d.path().join("user");
        std::fs::create_dir_all(&user).unwrap();
        file(&user, "claude.exe", b"MZ\x90\x00");
        let found = vec![look_in("claude", std::slice::from_ref(&user), true)];
        let v = placed_verified(found, &data, Some("win32-x64"), true);
        assert_eq!(
            v[0].path,
            Some(user.join("claude.exe")),
            "reason: user_checked {v:?}"
        );
    }

    #[test]
    fn a_relative_folder_is_never_looked_in() {
        let d = tempfile::tempdir().unwrap();
        file(d.path(), "claude", b"\x7fELF");
        let rel = vec![PathBuf::from("relative-dir")];
        assert_eq!(look_in("claude", &rel, false).kind, LookKind::NotFound);
        let abs = vec![d.path().to_path_buf()];
        assert_eq!(look_in("claude", &abs, false).kind, LookKind::Native);
    }

    #[test]
    fn only_native_programs_are_written_and_none_writes_nothing() {
        let native = Look {
            name: "claude".into(),
            kind: LookKind::Native,
            path: Some(PathBuf::from(if cfg!(windows) {
                "C:\\Users\\x\\.local\\bin\\claude.exe"
            } else {
                "/home/x/.local/bin/claude"
            })),
            why: String::new(),
        };
        let npm = Look {
            name: "codex".into(),
            kind: LookKind::Npm,
            path: Some(PathBuf::from(if cfg!(windows) {
                "C:\\npm\\codex.cmd"
            } else {
                "/npm/codex"
            })),
            why: "npm".into(),
        };
        let t = policy_text(&[native.clone(), npm.clone()], |_| false).unwrap();
        let p = allowlist::parse(&t).unwrap();
        assert_eq!(p.programs.len(), 1, "reason: npm_written {t}");
        assert_eq!(p.programs[0].name, "claude");
        assert_eq!(
            policy_text(&[npm], |_| false),
            None,
            "reason: nothing_native_written"
        );
        assert_eq!(object_words(&t), native.path.unwrap().to_str().unwrap());
    }

    #[test]
    fn a_file_already_there_is_never_replaced_and_a_bad_one_is_removed() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("session-policy.json");
        let good = format!(
            r#"{{"programs":[{{"name":"claude","path":"{}"}}],"env":[]}}"#,
            d.path()
                .join("claude")
                .to_str()
                .unwrap()
                .replace('\\', "\\\\")
        );
        std::fs::write(&p, b"not json").unwrap();
        assert_eq!(write_and_load(&p, &good), Err(InitRefused::Exists));
        assert_eq!(
            std::fs::read(&p).unwrap(),
            b"not json",
            "reason: overwritten"
        );
        std::fs::remove_file(&p).unwrap();
        let loaded = write_and_load(&p, &good).unwrap();
        assert_eq!(loaded.programs[0].name, "claude");
        assert_eq!(file_state(&p), "loaded");
        assert_eq!(write_and_load(&p, &good), Err(InitRefused::Exists));
        let left: Vec<_> = std::fs::read_dir(d.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name())
            .collect();
        assert_eq!(left.len(), 1, "reason: temporary_left {left:?}");
        assert_eq!(
            write_and_load(&d.path().join("other.json"), "{}x"),
            Err(InitRefused::WriteFailed)
        );
        assert!(!d.path().join("other.json").exists());
    }

    /// Andy 11-26 02:0x: the page writes no key name, even when aibd has the variable set; the
    /// names that are not keys (a proxy) are kept.
    #[test]
    fn a_policy_the_page_writes_names_no_key() {
        let l = Look {
            name: "claude".into(),
            kind: LookKind::Native,
            path: Some(std::env::temp_dir().join("claude")),
            why: String::new(),
        };
        let t = policy_text(&[l], |_| true).unwrap();
        let p = allowlist::parse(&t).unwrap();
        for k in KEY_ENV {
            assert!(
                !p.env.iter().any(|e| e.eq_ignore_ascii_case(k)),
                "reason: key_name_written {k} {t}"
            );
        }
        assert!(
            p.env.iter().any(|e| e == "HTTPS_PROXY"),
            "reason: proxy_dropped {t}"
        );
    }

    /// 3e53b L1: a file swapped between the write and the read back is not put in force (nor
    /// kept); the approved text is what aibd uses.
    #[test]
    fn only_the_approved_text_is_put_in_force() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("session-policy.json");
        let one = |name: &str| {
            format!(
                r#"{{"programs":[{{"name":"{name}","path":"{}"}}],"env":[]}}"#,
                d.path().join(name).to_str().unwrap().replace('\\', "\\\\")
            )
        };
        std::fs::write(&p, one("codex")).unwrap();
        assert_eq!(
            loaded_as_written(&p, &one("claude")),
            Err(InitRefused::WriteFailed),
            "reason: swapped_file_in_force"
        );
        assert!(!p.exists(), "reason: swapped_file_kept");
        std::fs::write(&p, one("claude")).unwrap();
        let got = loaded_as_written(&p, &one("claude")).unwrap();
        assert_eq!(got.programs[0].name, "claude");
    }

    /// r3 at the lowest layer: even with the checks above it gone, the write itself never
    /// replaces a file, and leaves no temporary file behind.
    #[test]
    fn write_new_never_replaces_a_file() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("session-policy.json");
        std::fs::write(&p, b"theirs").unwrap();
        let e = write_new(&p, b"ours").unwrap_err();
        assert_eq!(e.kind(), std::io::ErrorKind::AlreadyExists, "{e}");
        assert_eq!(std::fs::read(&p).unwrap(), b"theirs", "reason: replaced");
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 1);
        let q = d.path().join("sub").join("new.json");
        write_new(&q, b"ours").unwrap();
        assert_eq!(std::fs::read(&q).unwrap(), b"ours");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&q).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "reason: policy_readable_by_others");
        }
    }

    #[test]
    fn a_temporary_file_held_by_someone_else_is_no_failure() {
        // f163c L1: on Windows an antivirus often holds the file just made
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("session-policy.json");
        let held = |_: &Path| Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied));
        write_new_with(&p, b"ours", held).expect("reason: written_policy_reported_failed");
        assert_eq!(std::fs::read(&p).unwrap(), b"ours");
        // the link already there still refuses, whatever the removal does
        let e = write_new_with(&p, b"again", held).unwrap_err();
        assert_eq!(e.kind(), std::io::ErrorKind::AlreadyExists, "{e}");
        assert_eq!(std::fs::read(&p).unwrap(), b"ours", "reason: replaced");
    }

    #[test]
    fn the_state_tells_missing_from_invalid() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("session-policy.json");
        assert_eq!(file_state(&p), "missing");
        std::fs::write(&p, b"{").unwrap();
        assert_eq!(file_state(&p), "invalid");
    }
}
