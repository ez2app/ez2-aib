//! M1 security gate for `POST /api/v1/sessions`: who may be started.
//!
//! Rulings (a63ab 11-09 19:08 / 19:12 / 19:14 / 19:15, Andy 19:1x; ADR 0003 "Update
//! 2026-11-09"):
//! - an allow-list replaces "any absolute path, a few env names denied";
//! - each entry is a name + a path + the expected signer. M1 records the signer and does
//!   not check it (M2 does);
//! - every spawn resolves the real path again (no cache), compares real paths, and runs
//!   the real path;
//! - shells, interpreters and launchers can never be listed or started;
//! - an npm (node) install of a provider is refused with `provider_install_unsupported`.
//!
//! What this is: a guard against mistakes and against an API caller passing an arbitrary
//! path. What it is not: a defence against a same-user process. That process can edit the
//! policy file, or copy `sh` under another name and list it (known gap I4). The defences
//! for that are the M2 signer check, M4 Touch ID / Windows Hello approval, and the
//! Phase 5 installer keeping the list where the user cannot write.
//!
//! The policy is read once at start from the file named by `AIBD_SESSION_POLICY`, or,
//! when that is unset, from [`DEFAULT_POLICY_FILE`] in aibd's data folder (T80; the
//! file `aibd ctl policy init` writes):
//!
//! ```json
//! {"programs": [{"name": "claude", "path": "/abs/claude", "signer": "Q6L2SF6YDW"}],
//!  "env": ["MY_VAR"]}
//! ```
//!
//! Neither set nor present means nothing may be started. An unreadable or invalid file
//! stops aibd from starting.

use aib_core::ErrorCode;
use serde::Deserialize;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::Instant;

/// One allowed program.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Program {
    /// A label (`claude`, `codex`, a test fixture): 1-64 of `A-Z a-z 0-9 . _ -`.
    pub name: String,
    /// Absolute, no `..`. Usually the stable link (`~/.local/bin/claude`); its real path
    /// is resolved again on every spawn, so a version update that moves the target is
    /// followed.
    pub path: PathBuf,
    /// The expected signer (macOS Team ID / Windows Authenticode subject). M1 keeps it,
    /// M2 checks it. `None` only for unsigned test fixtures.
    #[serde(default)]
    pub signer: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SessionPolicy {
    pub programs: Vec<Program>,
    /// Env names a request may set. Names on the fixed deny list (`AIBD_*`, `DYLD_*`,
    /// `LD_*`, `PATH`) are refused when the file is read: no list can allow them.
    pub env: Vec<String>,
    /// Test fixtures only: let the in-process tests that need `/bin/sh` through the
    /// interpreter rule. Neither the file nor any env var can set it; it is `false`
    /// unless Rust code builds the struct that way.
    pub interpreters_ok: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyFile {
    #[serde(default)]
    programs: Vec<Program>,
    #[serde(default)]
    env: Vec<String>,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum PolicyError {
    #[error("AIBD_SESSION_POLICY must be an absolute path")]
    NotAbsolute,
    #[error("AIBD_SESSION_POLICY could not be read: {0}")]
    Unreadable(String),
    #[error("AIBD_SESSION_POLICY is not valid: {0}")]
    Invalid(String),
    #[error("AIBD_SESSION_POLICY program {0}: {1}")]
    BadProgram(String, &'static str),
    #[error("AIBD_SESSION_POLICY env name is not allowed: {0}")]
    BadEnv(String),
}

/// Why a request was refused. Messages name the setting, never a value from the request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refused {
    Program,
    Env,
    ProviderInstall,
}

impl Refused {
    pub fn code(self) -> ErrorCode {
        match self {
            Refused::Program => ErrorCode::ProgramNotAllowed,
            Refused::Env => ErrorCode::EnvNotAllowed,
            Refused::ProviderInstall => ErrorCode::ProviderInstallUnsupported,
        }
    }

    pub fn message(self) -> &'static str {
        match self {
            Refused::Program => "argv[0] is not a program allowed by AIBD_SESSION_POLICY",
            Refused::Env => "env key is not one of the names in AIBD_SESSION_POLICY",
            Refused::ProviderInstall => {
                "this provider install runs through node; install the standalone signed build"
            }
        }
    }
}

/// Rejections of the same token and code within this window are folded into one record.
pub const REJECT_WINDOW: std::time::Duration = std::time::Duration::from_secs(60);

/// Folds repeated rejections (a63ab 19:23: one record per token + code per minute, with a
/// count, so a loop of bad requests cannot flood the event table or the log).
///
/// The first rejection of a key opens a window and is written. Later ones inside the
/// window are only counted. When the window closes, the count is written by the flush
/// ([`RejectLog::close`], armed once per window, `session.reject_flush` timer), by the
/// next rejection of that key (whichever comes first), or at shutdown
/// ([`RejectLog::drain`]). So the total is never lost, except in a crash during the
/// window (1ced7 #593 M1).
#[derive(Debug, Default)]
pub struct RejectLog {
    open: std::sync::Mutex<Windows>,
}

/// Open windows by (token_id, code).
type Windows = std::collections::HashMap<(String, &'static str), Window>;

#[derive(Debug)]
struct Window {
    start: Instant,
    folded: u64,
    /// The program of the last folded rejection (already made safe by the caller).
    program: String,
}

/// What [`RejectLog::note`] asks the caller to do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Note {
    /// Write a record now; `folded_before` = rejections folded since the last record.
    Write { folded_before: u64 },
    /// Counted only. `arm` = this window's start when this is its first folded
    /// rejection: arm one flush for the window's end.
    Folded { arm: Option<Instant> },
}

impl RejectLog {
    fn map(&self) -> std::sync::MutexGuard<'_, Windows> {
        self.open.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `now` is the clock (injected: tests pass their own instants). A clock that goes
    /// back never opens a new window.
    pub fn note(&self, now: Instant, token_id: &str, code: &'static str, program: &str) -> Note {
        let mut m = self.map();
        let key = (token_id.to_owned(), code);
        match m.get_mut(&key) {
            Some(w) if now.saturating_duration_since(w.start) < REJECT_WINDOW => {
                w.folded += 1;
                program.clone_into(&mut w.program);
                Note::Folded {
                    arm: (w.folded == 1).then_some(w.start),
                }
            }
            Some(w) => {
                let folded_before = w.folded;
                *w = Window {
                    start: now,
                    folded: 0,
                    program: program.to_owned(),
                };
                Note::Write { folded_before }
            }
            None => {
                m.insert(
                    key,
                    Window {
                        start: now,
                        folded: 0,
                        program: program.to_owned(),
                    },
                );
                Note::Write { folded_before: 0 }
            }
        }
    }

    /// The window that opened at `start` has closed: end it and hand back what it folded
    /// (`None` when nothing was folded, or a later rejection already wrote the count and
    /// opened a new window).
    pub fn close(
        &self,
        token_id: &str,
        code: &'static str,
        start: Instant,
    ) -> Option<(u64, String)> {
        let mut m = self.map();
        let key = (token_id.to_owned(), code);
        if m.get(&key).map(|w| w.start) != Some(start) {
            return None;
        }
        let w = m.remove(&key)?;
        (w.folded > 0).then_some((w.folded, w.program))
    }

    /// Shutdown: every window that still holds a count, as (token_id, code, folded, program).
    pub fn drain(&self) -> Vec<(String, &'static str, u64, String)> {
        let mut m = self.map();
        m.drain()
            .filter(|(_, w)| w.folded > 0)
            .map(|((t, c), w)| (t, c, w.folded, w.program))
            .collect()
    }
}

/// A program name as the audit record and the log may show it: at most 64 characters,
/// control characters and invisible format characters escaped (`\u{a}`, `\u{202e}`), so
/// a crafted `argv[0]` cannot forge a log line or show reversed text (1ced7 #593 L3, L1).
/// An ellipsis marks a cut.
pub fn audit_name(s: &str) -> String {
    let mut out = String::new();
    for (n, c) in s.chars().enumerate() {
        if n == 64 {
            out.push('…');
            break;
        }
        if c.is_control() || is_invisible_format(c) {
            out.push_str(&c.escape_unicode().to_string());
        } else {
            out.push(c);
        }
    }
    out
}

/// Characters that are not `is_control()` but still move or hide text in a terminal or a
/// log viewer (1ced7 #593 L1): zero-width and direction marks (U+200B–U+200F), line and
/// paragraph separators and direction embeddings / overrides (U+2028–U+202E), word
/// joiner and direction isolates (U+2060–U+2069), and the byte order mark (U+FEFF).
fn is_invisible_format(c: char) -> bool {
    matches!(
        c,
        '\u{200b}'..='\u{200f}' | '\u{2028}'..='\u{202e}' | '\u{2060}'..='\u{2069}' | '\u{feff}'
    )
}

/// Shells, interpreters and launchers (springboards: they run any program or code they are
/// given, so listing one would open everything; 1ced7 M, a63ab 19:23). Compared with the
/// file name's leading ASCII letters, lower-cased: `bash-5.2` ⇒ `bash`,
/// `python3.12-intel64` ⇒ `python`, `pwsh-preview` ⇒ `pwsh`, `rundll32.exe` ⇒ `rundll`,
/// `PING.EXE` ⇒ `ping` (not listed).
const INTERPRETERS: &[&str] = &[
    // shells
    "sh",
    "bash",
    "zsh",
    "dash",
    "ash",
    "ksh",
    "mksh",
    "pdksh",
    "oksh",
    "yash",
    "csh",
    "tcsh",
    "fish",
    "nu",
    "elvish",
    "xonsh",
    "busybox",
    "toybox",
    // Windows
    "cmd",
    "command",
    "powershell",
    "pwsh",
    "wsl",
    "wscript",
    "cscript",
    "mshta",
    "rundll",
    "regsvr",
    "msiexec",
    "conhost",
    "wt",
    "forfiles",
    "msbuild",
    "installutil",
    // interpreters
    "python",
    "pythonw",
    "py",
    "pypy",
    "ipython",
    "node",
    "nodejs",
    "deno",
    "bun",
    "ruby",
    "irb",
    "jruby",
    "perl",
    "php",
    "lua",
    "luajit",
    "tclsh",
    "wish",
    "expect",
    "osascript",
    "swift",
    "awk",
    "gawk",
    "mawk",
    "nawk",
    "java",
    "jshell",
    "jrunscript",
    "rscript",
    "julia",
    "guile",
    "racket",
    "sbcl",
    "erl",
    "escript",
    "elixir",
    "iex",
    "dotnet",
    "groovy",
    "scala",
    "kotlin",
    // launchers
    "env",
    "xargs",
    "nice",
    "nohup",
    "timeout",
    "sudo",
    "doas",
    "su",
    "script",
    "launchctl",
    "open",
    "nsenter",
    "chroot",
    "setsid",
    "stdbuf",
    "unbuffer",
    "flock",
    "watch",
    "time",
    "ionice",
    "taskset",
    "caffeinate",
    "arch",
    "find",
    // more springboards (1ced7 #593 L2): each can run a command it is given
    "ssh",
    "tmux",
    "screen",
    "make",
    "git",
    "vi",
    "vim",
    "nvim",
    "emacs",
    "less",
    "more",
    "man",
    "sed",
    "tar",
    "rsync",
    "npx",
    "npm",
    "pnpm",
    "yarn",
    "uv",
    "uvx",
    "pip",
    "pipx",
    "xcrun",
    "sandbox",
    "dtrace",
    "systemd",
    "runas",
    "schtasks",
    "at",
    "strace",
    "ltrace",
    "gdb",
    "lldb",
    "valgrind",
    "parallel",
];

/// The file name names a shell, interpreter or launcher. A name that is not UTF-8 counts
/// as one (refuse what cannot be read).
pub fn is_interpreter(p: &Path) -> bool {
    let Some(n) = p.file_name() else {
        return false;
    };
    let Some(n) = n.to_str() else {
        return true;
    };
    let stem: String = n
        .chars()
        .map(|c| c.to_ascii_lowercase())
        .take_while(char::is_ascii_lowercase)
        .collect();
    INTERPRETERS.contains(&stem.as_str())
}

/// What kind of file the real path is, from its extension and first bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Native,
    /// Run by an interpreter (`#!`, `.cmd`, `.ps1` …).
    Script,
    /// Run by node: an npm install (`.js`, or a `#!` / shim that names node).
    Node,
}

/// Executable formats recognised as native (1ced7 #593 L1: positive, not "anything that
/// is not a script"): Mach-O (32 / 64, both byte orders, fat), ELF, PE (`MZ`).
const NATIVE_MAGIC: &[&[u8]] = &[
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\x7fELF",
    b"MZ",
];

pub fn kind_of(real: &Path, head: &[u8]) -> Kind {
    let names_node = |b: &[u8]| {
        String::from_utf8_lossy(b)
            .to_ascii_lowercase()
            .contains("node")
    };
    let ext = real
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase);
    if matches!(ext.as_deref(), Some("js" | "mjs" | "cjs")) {
        return Kind::Node;
    }
    if head.starts_with(b"#!") {
        let line = head.split(|c| *c == b'\n').next().unwrap_or(head);
        return if names_node(line) {
            Kind::Node
        } else {
            Kind::Script
        };
    }
    if NATIVE_MAGIC.iter().any(|m| head.starts_with(m)) {
        return Kind::Native;
    }
    // anything else runs through some interpreter (a .cmd shim, a text file `execvp`
    // would hand to /bin/sh, …)
    if names_node(head) {
        Kind::Node
    } else {
        Kind::Script
    }
}

fn name_ok(n: &str) -> bool {
    (1..=64).contains(&n.len())
        && n.bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

fn plain_absolute(p: &Path) -> bool {
    p.is_absolute() && !p.components().any(|c| matches!(c, Component::ParentDir))
}

fn same_path(a: &Path, b: &Path) -> bool {
    if cfg!(windows) {
        a.to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy())
    } else {
        a == b
    }
}

/// Env names compare like the OS does: Windows without case, Unix exactly.
fn env_name_eq(a: &str, b: &str) -> bool {
    if cfg!(windows) {
        a.eq_ignore_ascii_case(b)
    } else {
        a == b
    }
}

/// Check the policy file's text. Interpreters are refused here by the listed path's name
/// and, when it resolves now, by the real path's name (I1); every spawn checks again.
pub fn parse(text: &str) -> Result<SessionPolicy, PolicyError> {
    let f: PolicyFile =
        serde_json::from_str(text).map_err(|e| PolicyError::Invalid(e.to_string()))?;
    let mut programs: Vec<Program> = Vec::new();
    for p in f.programs {
        let shown = p.path.display().to_string();
        if !name_ok(&p.name) {
            return Err(PolicyError::BadProgram(
                p.name,
                "name must be 1-64 of A-Z a-z 0-9 . _ -",
            ));
        }
        if !plain_absolute(&p.path) {
            return Err(PolicyError::BadProgram(
                shown,
                "path must be absolute without `..`",
            ));
        }
        if let Some(s) = &p.signer {
            if s.trim().is_empty() || s.len() > 256 || s.chars().any(char::is_control) {
                return Err(PolicyError::BadProgram(
                    shown,
                    "signer must be 1-256 printable characters",
                ));
            }
        }
        let real_is_interp = std::fs::canonicalize(&p.path)
            .map(|r| is_interpreter(&r))
            .unwrap_or(false);
        if is_interpreter(&p.path) || real_is_interp {
            return Err(PolicyError::BadProgram(
                shown,
                "shells, interpreters and launchers cannot be listed",
            ));
        }
        if programs
            .iter()
            .any(|q| q.name == p.name || same_path(&q.path, &p.path))
        {
            return Err(PolicyError::BadProgram(shown, "listed twice"));
        }
        programs.push(p);
    }
    let mut env: Vec<String> = Vec::new();
    for k in f.env {
        if !crate::sessions::env_key_ok(&k) || crate::sessions::env_key_denied(&k) {
            return Err(PolicyError::BadEnv(k));
        }
        if !env.iter().any(|e| e.eq_ignore_ascii_case(&k)) {
            env.push(k);
        }
    }
    Ok(SessionPolicy {
        programs,
        env,
        interpreters_ok: false,
    })
}

/// Read and check the file `AIBD_SESSION_POLICY` names.
/// The policy file aibd reads from its data folder when `AIBD_SESSION_POLICY` is unset.
pub const DEFAULT_POLICY_FILE: &str = "session-policy.json";

pub fn from_path(path: &str) -> Result<SessionPolicy, PolicyError> {
    from_file(Path::new(path))
}

/// [`from_path`] for a path that may not be UTF-8 (the data folder's default file).
pub fn from_file(p: &Path) -> Result<SessionPolicy, PolicyError> {
    if !p.is_absolute() {
        return Err(PolicyError::NotAbsolute);
    }
    let text =
        std::fs::read_to_string(p).map_err(|e| PolicyError::Unreadable(e.kind().to_string()))?;
    parse(&text)
}

/// Every env name of the request is on the list.
pub fn env_allowed<'a>(
    p: &SessionPolicy,
    mut names: impl Iterator<Item = &'a str>,
) -> Result<(), Refused> {
    if names.any(|k| !p.env.iter().any(|a| env_name_eq(a, k))) {
        return Err(Refused::Env);
    }
    Ok(())
}

/// The path to run for `argv0`, resolved now (no cache). `argv0` must be absolute without
/// `..`, its real path must equal the real path of a listed program, and (unless
/// `interpreters_ok`) neither name may be an interpreter and the real file must be a
/// native executable. A file swapped between this check and exec is not caught (P8).
pub fn resolve(argv0: &str, p: &SessionPolicy) -> Result<PathBuf, Refused> {
    let a = Path::new(argv0);
    if !plain_absolute(a) {
        return Err(Refused::Program);
    }
    let real = std::fs::canonicalize(a).map_err(|_| Refused::Program)?;
    let listed = p.programs.iter().any(|e| {
        std::fs::canonicalize(&e.path)
            .map(|r| same_path(&r, &real))
            .unwrap_or(false)
    });
    if !listed {
        return Err(Refused::Program);
    }
    if !p.interpreters_ok {
        if is_interpreter(&real) || is_interpreter(a) {
            return Err(Refused::Program);
        }
        let mut head = Vec::with_capacity(512);
        std::fs::File::open(&real)
            .and_then(|f| f.take(512).read_to_end(&mut head))
            .map_err(|_| Refused::Program)?;
        match kind_of(&real, &head) {
            Kind::Native => {}
            Kind::Script => return Err(Refused::Program),
            Kind::Node => return Err(Refused::ProviderInstall),
        }
    }
    Ok(exec_path(real))
}

/// Windows `canonicalize` answers `\\?\C:\…`; run `C:\…` (the verbatim prefix is not
/// needed for a drive path and not every API takes it). UNC paths stay as they are.
fn exec_path(p: PathBuf) -> PathBuf {
    if cfg!(windows) {
        if let Some(rest) = p.to_str().and_then(|s| s.strip_prefix(r"\\?\")) {
            if rest.as_bytes().get(1) == Some(&b':') {
                return PathBuf::from(rest);
            }
        }
    }
    p
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prog(name: &str, path: &Path) -> Program {
        Program {
            name: name.into(),
            path: path.to_path_buf(),
            signer: None,
        }
    }

    #[test]
    fn interpreter_names() {
        for n in [
            "/bin/sh",
            "/usr/bin/bash",
            "/usr/bin/python3.12",
            "/usr/bin/python3",
            "/usr/local/bin/node",
            "/usr/bin/env",
            "/usr/bin/osascript",
            "/x/PWSH.EXE",
            "/x/rundll32.exe",
            "/usr/bin/perl5.34",
            "/usr/local/bin/bash-5.2",
            "/usr/bin/python3.12-intel64",
            "/x/pwsh-preview",
            "/usr/bin/xargs",
            "/usr/bin/arch",
            "/usr/bin/caffeinate",
            "/usr/bin/awk",
            "/x/msiexec.exe",
            "/x/regsvr32.exe",
            "/usr/bin/ksh93",
            "/usr/bin/sandbox-exec",
            "/bin/systemd-run",
            "/usr/bin/git",
            "/usr/bin/vim",
            "/usr/local/bin/npx",
        ] {
            assert!(is_interpreter(Path::new(n)), "{n}");
        }
        for n in [
            "/bin/sleep",
            "/x/claude",
            "/x/codex",
            "/bin/true",
            "/x/ping.exe",
            "/x/sha256sum",
            "/x/envsubst",
            "/x/openssl",
            "/x/7z",
        ] {
            assert!(!is_interpreter(Path::new(n)), "{n}");
        }
    }

    #[test]
    fn rejections_fold_per_token_and_code_per_minute() {
        let log = RejectLog::default();
        let t0 = Instant::now();
        let at = |s: u64| t0 + std::time::Duration::from_secs(s);
        let p = "program_not_allowed";
        let w0 = Note::Write { folded_before: 0 };
        assert_eq!(log.note(at(0), "startup", p, "a"), w0);
        assert_eq!(
            log.note(at(1), "startup", p, "b"),
            Note::Folded { arm: Some(at(0)) }
        );
        assert_eq!(
            log.note(at(59), "startup", p, "c"),
            Note::Folded { arm: None }
        );
        // another code or another token is another key
        assert_eq!(log.note(at(2), "startup", "env_not_allowed", "a"), w0);
        assert_eq!(log.note(at(2), "pair_1", p, "a"), w0);
        // the flush at the window's end writes the count and the last program
        assert_eq!(log.close("startup", p, at(0)), Some((2, "c".to_string())));
        assert_eq!(log.close("startup", p, at(0)), None);
        // nothing folded: nothing to write
        assert_eq!(log.close("startup", "env_not_allowed", at(2)), None);
        // a rejection after a late flush: the rejection writes the count itself …
        assert_eq!(log.note(at(100), "startup", p, "a"), w0);
        assert_eq!(
            log.note(at(101), "startup", p, "a"),
            Note::Folded { arm: Some(at(100)) }
        );
        assert_eq!(
            log.note(at(160), "startup", p, "a"),
            Note::Write { folded_before: 1 }
        );
        // … and the late flush of the old window then finds nothing
        assert_eq!(log.close("startup", p, at(100)), None);
        // a clock that goes back never opens a new window
        assert_eq!(
            log.note(at(150), "startup", p, "a"),
            Note::Folded { arm: Some(at(160)) }
        );
        // shutdown hands back every window that still holds a count
        let mut left = log.drain();
        left.sort();
        assert_eq!(left, [("startup".to_string(), p, 1, "a".to_string())]);
        assert!(log.drain().is_empty());
    }

    #[test]
    fn audit_names_are_short_and_escaped() {
        assert_eq!(audit_name("claude"), "claude");
        assert_eq!(audit_name("a\nb\u{1b}"), "a\\u{a}b\\u{1b}");
        // 1ced7 #593 L1: format characters that are not control characters
        for (c, esc) in [
            ('\u{200b}', "\\u{200b}"),
            ('\u{200f}', "\\u{200f}"),
            ('\u{2028}', "\\u{2028}"),
            ('\u{2029}', "\\u{2029}"),
            ('\u{202e}', "\\u{202e}"),
            ('\u{2066}', "\\u{2066}"),
            ('\u{2069}', "\\u{2069}"),
            ('\u{feff}', "\\u{feff}"),
        ] {
            assert_eq!(audit_name(&format!("a{c}b")), format!("a{esc}b"), "{esc}");
        }
        // neighbours that are plain text stay as they are
        assert_eq!(audit_name("a\u{2027}b\u{2030}c"), "a\u{2027}b\u{2030}c");
        assert_eq!(audit_name("cl\u{e9}ude"), "cl\u{e9}ude");
        let long = "x".repeat(100);
        let got = audit_name(&long);
        assert_eq!(got.chars().count(), 65);
        assert!(got.ends_with('…'));
    }

    #[test]
    fn kinds_from_extension_and_first_line() {
        let k = |p: &str, h: &[u8]| kind_of(Path::new(p), h);
        assert_eq!(k("/x/cli.js", b""), Kind::Node);
        assert_eq!(
            k("/x/claude", b"#!/usr/bin/env node\nrequire()"),
            Kind::Node
        );
        assert_eq!(
            k("/x/claude.cmd", b"@\"%~dp0\\node.exe\" cli.js"),
            Kind::Node
        );
        assert_eq!(k("/x/run", b"#!/bin/sh\necho node"), Kind::Script);
        assert_eq!(k("/x/run.ps1", b"Write-Host hi"), Kind::Script);
        assert_eq!(k("/x/claude", b"\xcf\xfa\xed\xfe node"), Kind::Native);
        assert_eq!(k("/x/claude", b"\x7fELF\x02"), Kind::Native);
        assert_eq!(k("/x/ping.exe", b"MZ\x90\x00"), Kind::Native);
        assert_eq!(k("/x/fat", b"\xca\xfe\xba\xbe"), Kind::Native);
        // L1: not recognised ⇒ not native (an empty or plain text file would go to sh)
        assert_eq!(k("/x/claude", b""), Kind::Script);
        assert_eq!(k("/x/claude", b"echo hi\n"), Kind::Script);
    }

    #[test]
    fn env_names_are_listed_case_like_the_os() {
        let p = SessionPolicy {
            env: vec!["MY_VAR".into()],
            ..Default::default()
        };
        assert_eq!(env_allowed(&p, ["MY_VAR"].into_iter()), Ok(()));
        assert_eq!(env_allowed(&p, std::iter::empty()), Ok(()));
        assert_eq!(env_allowed(&p, ["OTHER"].into_iter()), Err(Refused::Env));
        let lower = env_allowed(&p, ["my_var"].into_iter());
        if cfg!(windows) {
            assert_eq!(lower, Ok(()));
        } else {
            assert_eq!(lower, Err(Refused::Env));
        }
    }

    #[test]
    fn empty_policy_allows_nothing() {
        let exe = std::env::current_exe().unwrap();
        let exe = exe.to_str().unwrap();
        assert_eq!(
            resolve(exe, &SessionPolicy::default()),
            Err(Refused::Program)
        );
    }

    #[test]
    fn refusals_name_the_setting_with_their_codes() {
        assert_eq!(Refused::Program.code().http_status(), 403);
        assert_eq!(Refused::Env.code().as_str(), "env_not_allowed");
        assert_eq!(
            Refused::ProviderInstall.code().as_str(),
            "provider_install_unsupported"
        );
        for r in [Refused::Program, Refused::Env] {
            assert!(r.message().contains("AIBD_SESSION_POLICY"));
        }
    }

    // Unix paths in the fixtures (`/a` is not absolute on Windows).
    #[cfg(unix)]
    #[test]
    fn the_file_is_checked_field_by_field() {
        let ok = parse(
            r#"{"programs":[{"name":"fx","path":"/opt/fx/bin/fx","signer":"Q6L2SF6YDW"}],
                "env":[" MY_VAR", "MY_VAR"]}"#,
        );
        // " MY_VAR" is not a valid name: no trimming inside JSON
        assert_eq!(ok, Err(PolicyError::BadEnv(" MY_VAR".into())));
        let ok = parse(
            r#"{"programs":[{"name":"fx","path":"/opt/fx/bin/fx","signer":"Q6L2SF6YDW"}],
                "env":["MY_VAR","my_var","LANG"]}"#,
        )
        .unwrap();
        assert_eq!(ok.programs.len(), 1);
        assert_eq!(ok.programs[0].signer.as_deref(), Some("Q6L2SF6YDW"));
        assert_eq!(ok.env, ["MY_VAR", "LANG"]);
        assert!(!ok.interpreters_ok);
        assert_eq!(parse("{}").unwrap(), SessionPolicy::default());
        let bad = |t: &str| parse(t).unwrap_err();
        assert!(matches!(bad(r#"{"x":1}"#), PolicyError::Invalid(_)));
        // a63ab 20:0x: the file can never turn the interpreter rule off
        for t in [
            r#"{"interpreters_ok":true}"#,
            r#"{"programs":[],"interpreters_ok":true}"#,
            r#"{"interpreters_ok":false}"#,
        ] {
            assert!(matches!(bad(t), PolicyError::Invalid(_)), "{t}");
        }
        assert!(matches!(
            bad(r#"{"programs":[{"name":"a","path":"/a","extra":1}]}"#),
            PolicyError::Invalid(_)
        ));
        for (t, why) in [
            (r#"{"programs":[{"name":"","path":"/a"}]}"#, "name"),
            (r#"{"programs":[{"name":"a b","path":"/a"}]}"#, "name"),
            (r#"{"programs":[{"name":"a","path":"a"}]}"#, "path"),
            (r#"{"programs":[{"name":"a","path":"/x/../a"}]}"#, "path"),
            (
                r#"{"programs":[{"name":"a","path":"/a","signer":" "}]}"#,
                "signer",
            ),
            (
                r#"{"programs":[{"name":"a","path":"/x/sh"}]}"#,
                "interpreters",
            ),
            (
                r#"{"programs":[{"name":"a","path":"/usr/bin/env"}]}"#,
                "interpreters",
            ),
            (
                r#"{"programs":[{"name":"a","path":"/x/python3"}]}"#,
                "interpreters",
            ),
            (
                r#"{"programs":[{"name":"a","path":"/a"},{"name":"a","path":"/b"}]}"#,
                "twice",
            ),
        ] {
            match parse(t) {
                Err(PolicyError::BadProgram(_, m)) => assert!(m.contains(why), "{t}: {m}"),
                other => panic!("{t}: {other:?}"),
            }
        }
        for k in [
            "PATH",
            "path",
            "AIBD_TOKEN",
            "DYLD_INSERT_LIBRARIES",
            "LD_PRELOAD",
            "1X",
            "A-B",
        ] {
            let t = format!(r#"{{"env":["{k}"]}}"#);
            assert_eq!(parse(&t), Err(PolicyError::BadEnv(k.into())), "{k}");
        }
    }

    #[test]
    fn from_path_needs_an_absolute_readable_file() {
        assert_eq!(from_path("policy.json"), Err(PolicyError::NotAbsolute));
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("policy.json");
        assert!(matches!(
            from_path(f.to_str().unwrap()),
            Err(PolicyError::Unreadable(_))
        ));
        std::fs::write(&f, r#"{"env":["MY_VAR"]}"#).unwrap();
        assert_eq!(from_path(f.to_str().unwrap()).unwrap().env, ["MY_VAR"]);
    }

    /// I5 (CI-windows; UNVERIFIED-desktop): cmd / PowerShell are refused even when listed,
    /// in any case; a native program compares without case.
    #[cfg(windows)]
    #[test]
    fn windows_shells_are_refused_and_case_is_ignored() {
        let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
        let sys = |e: &str| PathBuf::from(format!(r"{root}\System32\{e}"));
        let p = SessionPolicy {
            programs: vec![
                prog("cmd", &sys("cmd.exe")),
                prog("ps", &sys(r"WindowsPowerShell\v1.0\powershell.exe")),
                prog("ping", &sys("PING.EXE")),
            ],
            ..Default::default()
        };
        for e in [
            "cmd.exe",
            "CMD.EXE",
            r"WindowsPowerShell\v1.0\powershell.exe",
        ] {
            let a = sys(e);
            assert_eq!(
                resolve(a.to_str().unwrap(), &p),
                Err(Refused::Program),
                "{e}"
            );
        }
        let lower = sys("ping.exe");
        let got = resolve(&lower.to_string_lossy().to_lowercase(), &p).unwrap();
        assert!(
            !got.to_string_lossy().starts_with(r"\\?\"),
            "{}",
            got.display()
        );
        assert!(got
            .to_string_lossy()
            .to_lowercase()
            .ends_with(r"\system32\ping.exe"));
    }

    #[cfg(unix)]
    mod unix {
        use super::*;
        use std::os::unix::fs::{symlink, PermissionsExt};

        fn real(p: &str) -> PathBuf {
            std::fs::canonicalize(p).unwrap()
        }

        fn s(p: &Path) -> &str {
            p.to_str().unwrap()
        }

        fn write_exec(p: &Path, body: &[u8]) {
            std::fs::write(p, body).unwrap();
            std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755)).unwrap();
        }

        #[test]
        fn a_listed_program_runs_by_its_real_path() {
            let d = tempfile::tempdir().unwrap();
            let link = d.path().join("fx");
            symlink("/bin/sleep", &link).unwrap();
            let p = SessionPolicy {
                programs: vec![prog("fx", &link)],
                ..Default::default()
            };
            assert_eq!(resolve(s(&link), &p), Ok(real("/bin/sleep")));
            // P5: another name for the same real file runs the same file
            assert_eq!(resolve("/bin/sleep", &p), Ok(real("/bin/sleep")));
            assert_eq!(resolve("/usr/bin/true", &p), Err(Refused::Program));
            // P2: `..` is refused before anything is resolved
            let dotdot = format!(
                "{}/../{}/fx",
                s(d.path()),
                d.path().file_name().unwrap().to_str().unwrap()
            );
            assert_eq!(resolve(&dotdot, &p), Err(Refused::Program));
            assert_eq!(resolve("sleep", &p), Err(Refused::Program));
        }

        #[test]
        fn every_spawn_resolves_again_n1_n2() {
            let d = tempfile::tempdir().unwrap();
            let link = d.path().join("current");
            symlink("/bin/sleep", &link).unwrap();
            let p = SessionPolicy {
                programs: vec![prog("fx", &link)],
                ..Default::default()
            };
            assert_eq!(resolve(s(&link), &p), Ok(real("/bin/sleep")));
            std::fs::remove_file(&link).unwrap();
            symlink("/usr/bin/true", &link).unwrap();
            assert_eq!(resolve(s(&link), &p), Ok(real("/usr/bin/true")));
            std::fs::remove_file(&link).unwrap();
            symlink("/bin/sh", &link).unwrap();
            assert_eq!(resolve(s(&link), &p), Err(Refused::Program));
        }

        #[test]
        fn a_listed_link_to_a_shell_is_refused_i2() {
            let d = tempfile::tempdir().unwrap();
            let link = d.path().join("fixture-ok");
            symlink("/bin/sh", &link).unwrap();
            // the file check refuses it at start…
            let t = format!(r#"{{"programs":[{{"name":"fx","path":"{}"}}]}}"#, s(&link));
            assert!(matches!(parse(&t), Err(PolicyError::BadProgram(_, _))));
            // …and a policy built in code is refused at spawn
            let p = SessionPolicy {
                programs: vec![prog("fx", &link)],
                ..Default::default()
            };
            assert_eq!(resolve(s(&link), &p), Err(Refused::Program));
            let ok = SessionPolicy {
                interpreters_ok: true,
                ..p
            };
            assert_eq!(resolve(s(&link), &ok), Ok(real("/bin/sh")));
        }

        #[test]
        fn npm_installs_and_scripts_are_refused_n3() {
            let d = tempfile::tempdir().unwrap();
            let js = d.path().join("cli.js");
            write_exec(&js, b"#!/usr/bin/env node\nconsole.log(1)\n");
            let claude = d.path().join("claude");
            symlink(&js, &claude).unwrap();
            let shim = d.path().join("codex");
            write_exec(&shim, b"#!/usr/bin/env node\n");
            let script = d.path().join("run");
            write_exec(&script, b"#!/bin/sh\nexit 0\n");
            let p = SessionPolicy {
                programs: vec![
                    prog("claude", &claude),
                    prog("codex", &shim),
                    prog("run", &script),
                ],
                ..Default::default()
            };
            assert_eq!(resolve(s(&claude), &p), Err(Refused::ProviderInstall));
            assert_eq!(resolve(s(&shim), &p), Err(Refused::ProviderInstall));
            assert_eq!(resolve(s(&script), &p), Err(Refused::Program));
        }

        /// I4, expected-gap (a63ab 19:14): a renamed copy of `sh` passes M1. When the M2
        /// signer check lands this must turn into a refusal: change the assertion then.
        #[test]
        fn known_gap_i4_a_renamed_copy_of_sh_is_allowed() {
            let d = tempfile::tempdir().unwrap();
            let copy = d.path().join("fixture-ok");
            std::fs::copy("/bin/sh", &copy).unwrap();
            let p = SessionPolicy {
                programs: vec![prog("fx", &copy)],
                ..Default::default()
            };
            assert_eq!(resolve(s(&copy), &p), Ok(real(s(&copy))));
        }
    }
}
