//! `aibd status | install --no-start | uninstall` (T97-1m; IFACE-T97-1m-043dd-1117.md r2,
//! DECISION-council-t97-install-043dd-1116.md).
//!
//! - macOS only in this slice; every command answers `unsupported` (rc 5) elsewhere. The code
//!   still builds and its pure parts are tested on every platform.
//! - No outside program is run here: the plist is written and read by this file, hashes come
//!   from `sha2`. Registering with launchd is T97-2.
//! - Data is never touched: install / uninstall only move the executable, the plist and the
//!   install record (`<aibd data dir>/install/record.json`). The aibd data directory is
//!   `AIBD_DATA_DIR`, else the platform default, never the AIB 1.x `~/.ez2-aib`.
//! - The "different build" refusals are against slips, not a defence: the same user can always
//!   uninstall and install again, or swap the file by hand. Replacing a build is `aibd update`
//!   (T97-3: Touch ID, later a signature).
pub mod update;

use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};

/// launchd label of the real aibd. Tests and CI pass another one (`app.ez2.aibd.test-<rid>`).
pub const DEFAULT_LABEL: &str = "app.ez2.aibd";
/// What follows the install directory in the plist PATH.
pub const PATH_TAIL: &str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
/// The record lives in this folder of the aibd data directory.
pub const RECORD_DIR: &str = "install";
pub const RECORD_FILE: &str = "record.json";
/// Left out of the data fingerprint: files that only live while aibd runs, logs, the record.
pub const FINGERPRINT_SKIP_DIRS: &[&str] = &["run", RECORD_DIR];
pub const FINGERPRINT_SKIP_SUFFIXES: &[&str] = &[".lock", ".sock", ".pid", ".log"];

pub const RC_OK: u8 = 0;
pub const RC_MISMATCH: u8 = 1;
pub const RC_USAGE: u8 = 2;
pub const RC_REFUSED: u8 = 3;
pub const RC_RUNNING: u8 = 4;
pub const RC_UNSUPPORTED: u8 = 5;

pub const USAGE: &str = "usage: aibd status [--json] [--fingerprint] [--label L] [--plist-dir D]\n       aibd install --no-start [--no-pair-key] [--dir D] [--label L] [--plist-dir D] [--env K=V]... [--unset-env K]... [--adopt] [--json]\n       aibd uninstall [--yes] [--label L] [--plist-dir D] [--json]\n       aibd start | stop [--label L] [--plist-dir D]\n       aibd update --file F [--label L] [--plist-dir D]";

/// plist ExitTimeOut: launchd sends SIGTERM, then SIGKILL after this many seconds (T97-2m §2).
pub const EXIT_TIMEOUT_S: u64 = 20;
/// Waiting for the lock to be let go: ExitTimeOut plus this (§2).
pub const STOP_GRACE_S: u64 = 5;
/// Waiting for health to answer with a new instance id (§3).
pub const START_LIMIT_S: u64 = 15;
/// One wait step (§3).
pub const STEP_MS: u64 = 250;
const NOT_LAUNCHD: &str =
    "an aibd not run by launchd holds the lock; not touched (stop it yourself)";

/// What a command prints and returns.
#[derive(Debug, Default)]
pub struct Out {
    pub code: u8,
    pub stdout: String,
    pub stderr: String,
}

impl Out {
    fn say(&mut self, line: impl AsRef<str>) {
        self.stdout.push_str(line.as_ref());
        self.stdout.push('\n');
    }
    fn fail(code: u8, msg: impl AsRef<str>) -> Out {
        Out {
            code,
            stdout: String::new(),
            stderr: format!("{}\n", msg.as_ref()),
        }
    }
}

/// What one launchctl call answered (T97-2m). `code` is None when it could not run.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LaunchdOut {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

impl LaunchdOut {
    pub fn ok(&self) -> bool {
        self.code == Some(0)
    }
}

/// The four launchd operations aibd uses (IFACE-T97-2m r1 §4). The real one lives in
/// `platform::macos_launchd` (the only place `/bin/launchctl` runs); tests hand in a
/// stand-in that records the calls.
pub trait Launchd {
    /// `gui/<uid>`.
    fn domain(&self) -> String;
    fn bootstrap(&self, plist: &Path) -> LaunchdOut;
    fn bootout(&self, label: &str) -> LaunchdOut;
    fn kickstart(&self, label: &str) -> LaunchdOut;
    fn print(&self, label: &str) -> LaunchdOut;
}

/// Where no launchd exists (not macOS): every call answers "could not run". `run` refuses
/// those platforms before any call (rc 5); this only lets the code compile there.
pub struct NoLaunchd;

impl Launchd for NoLaunchd {
    fn domain(&self) -> String {
        String::new()
    }
    fn bootstrap(&self, _: &Path) -> LaunchdOut {
        LaunchdOut::default()
    }
    fn bootout(&self, _: &str) -> LaunchdOut {
        LaunchdOut::default()
    }
    fn kickstart(&self, _: &str) -> LaunchdOut {
        LaunchdOut::default()
    }
    fn print(&self, _: &str) -> LaunchdOut {
        LaunchdOut::default()
    }
}

/// What `print` says about a label (§1b; only `state = ` and `pid = ` lines are read, L2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Loaded {
    Yes {
        state: Option<String>,
        pid: Option<u32>,
    },
    No,
    Unknown(String),
}

pub fn loaded(r: &LaunchdOut) -> Loaded {
    match r.code {
        Some(0) => {
            let mut state = None;
            let mut pid = None;
            for l in r.stdout.lines() {
                let l = l.trim();
                if let Some(v) = l.strip_prefix("state = ") {
                    state.get_or_insert_with(|| v.trim().to_string());
                } else if let Some(v) = l.strip_prefix("pid = ") {
                    if pid.is_none() {
                        pid = v.trim().parse().ok();
                    }
                }
            }
            Loaded::Yes { state, pid }
        }
        Some(113)
            if r.stderr.contains("Could not find service")
                || r.stdout.contains("Could not find service") =>
        {
            Loaded::No
        }
        _ => Loaded::Unknown(first_line(r)),
    }
}

fn first_line(r: &LaunchdOut) -> String {
    let t = if r.stderr.trim().is_empty() {
        &r.stdout
    } else {
        &r.stderr
    };
    let l = t
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && *l != "Bad request.");
    match (r.code, l) {
        (Some(c), Some(l)) => format!("rc {c}: {l}"),
        (Some(c), None) => format!("rc {c}"),
        (None, Some(l)) => l.to_string(),
        (None, None) => "launchctl did not run".into(),
    }
}

/// aibd's health answer: the instance id of the aibd now answering, if any (§3).
pub trait Health {
    fn instance(&self, data_dir: &Path) -> Option<String>;
}

/// The real one: `<data>/run/aibd.json` gives the loopback address, `GET /api/v1/health`
/// gives `instance_id`. Health is public; no token is read.
pub struct HttpHealth;

impl Health for HttpHealth {
    fn instance(&self, data_dir: &Path) -> Option<String> {
        use std::io::{Read, Write};
        let a = crate::runinfo::addr(&data_dir.join("run")).ok()?;
        let mut s =
            std::net::TcpStream::connect_timeout(&a, std::time::Duration::from_millis(500)).ok()?;
        s.set_read_timeout(Some(std::time::Duration::from_secs(2)))
            .ok()?;
        write!(
            s,
            "GET /api/v1/health HTTP/1.1\r\nHost: {a}\r\nConnection: close\r\n\r\n"
        )
        .ok()?;
        let mut b = Vec::new();
        s.take(65_536).read_to_end(&mut b).ok()?;
        parse_health(&b)
    }
}

/// `instance_id` from a raw HTTP answer; anything but a plain 200 with a JSON body is None.
pub fn parse_health(raw: &[u8]) -> Option<String> {
    let i = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = std::str::from_utf8(&raw[..i]).ok()?;
    if !head.starts_with("HTTP/1.1 200") || head.to_ascii_lowercase().contains("chunked") {
        return None;
    }
    let v: serde_json::Value = serde_json::from_slice(&raw[i + 4..]).ok()?;
    v.get("instance_id")?.as_str().map(str::to_owned)
}

/// Short-lived CLI wait, ADR 0005: `aibd install` / `start` / `stop` / `uninstall` are
/// one-shot commands, not the aibd server, so they have no registered timer. The only clock
/// wait in this file; server and timers code must not call it (5d33a IFACE-T97-2m r1 point 1).
#[allow(clippy::disallowed_methods)]
pub fn pace(d: std::time::Duration) {
    let (_keep, rx) = std::sync::mpsc::channel::<()>();
    let _ = rx.recv_timeout(d);
}

/// What the launchd-facing commands are handed, so tests use stand-ins.
pub struct Svc<'a> {
    pub ld: &'a dyn Launchd,
    pub health: &'a dyn Health,
    pub pace: &'a dyn Fn(std::time::Duration),
    /// the pairing key step ([`PairKey`])
    pub key: &'a dyn PairKey,
}

/// `aibd install`'s pairing key step (ebab8 11-26 第四包: a first install on a Mac must not
/// send the person to a terminal): with a fingerprint pairing, make the key or check the one
/// there ([`crate::touch::ensure_pair_key`]). Tests hand in a stand-in.
pub trait PairKey {
    fn ensure(&self, data: &Path) -> Result<crate::touch::PairKeyMade, String>;
}

/// This computer's pairing key: the Secure Enclave on macOS. Elsewhere there is none to make
/// ([`owner_touch_env`] refuses a fingerprint pairing there before this is reached).
pub struct HerePairKey;

impl PairKey for HerePairKey {
    fn ensure(&self, data: &Path) -> Result<crate::touch::PairKeyMade, String> {
        #[cfg(target_os = "macos")]
        {
            crate::touch::ensure_pair_key_here(data)
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = data;
            Err("a fingerprint pairing key is made on macOS only".into())
        }
    }
}

/// Everything a command reads from the process, so tests can hand it in.
#[derive(Debug, Clone)]
pub struct Host {
    pub os: &'static str,
    pub arch: &'static str,
    pub home: Option<PathBuf>,
    /// `AIBD_DATA_DIR` when set and not empty
    pub data_dir_env: Option<PathBuf>,
    pub default_data_dir: Option<PathBuf>,
    /// the running executable (`std::env::current_exe`)
    pub exe: PathBuf,
    pub version: String,
    pub now_unix: u64,
    /// the user id (None where there is none, Windows)
    pub uid: Option<u32>,
}

impl Host {
    pub fn from_process() -> Result<Host, String> {
        Ok(Host {
            os: std::env::consts::OS,
            arch: std::env::consts::ARCH,
            home: std::env::home_dir(),
            data_dir_env: std::env::var_os("AIBD_DATA_DIR")
                .filter(|v| !v.is_empty())
                .map(PathBuf::from),
            default_data_dir: crate::platform::default_data_dir(),
            exe: std::env::current_exe()
                .map_err(|e| format!("cannot find this executable: {e}"))?,
            version: crate::selfcheck::version_line(),
            now_unix: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0),
            uid: crate::platform::uid(),
        })
    }
}

#[derive(Debug, Default, Clone)]
pub struct Opts {
    pub json: bool,
    pub yes: bool,
    pub no_start: bool,
    pub adopt: bool,
    /// `install --no-pair-key`: leave the pairing key alone (tests and builds without a Secure
    /// Enclave). It only keeps the state before this step existed, no key, so the fingerprint
    /// pairing stays unavailable; it never makes another kind of key.
    pub no_pair_key: bool,
    pub fingerprint: bool,
    pub dir: Option<PathBuf>,
    /// `update --file`: the new build (T97-3m)
    pub file: Option<PathBuf>,
    pub label: Option<String>,
    pub plist_dir: Option<PathBuf>,
    pub env: Vec<(String, String)>,
    pub unset_env: Vec<String>,
}

/// Parse the options of one command. Every flag is checked against the command.
pub fn parse(cmd: &str, args: &[&str]) -> Result<Opts, String> {
    let mut o = Opts::default();
    let mut i = 0;
    let allowed: &[&str] = match cmd {
        "status" => &["--json", "--fingerprint", "--label", "--plist-dir"],
        "install" => &[
            "--json",
            "--no-start",
            "--no-pair-key",
            "--adopt",
            "--dir",
            "--label",
            "--plist-dir",
            "--env",
            "--unset-env",
        ],
        "uninstall" => &["--json", "--yes", "--label", "--plist-dir"],
        "start" | "stop" => &["--label", "--plist-dir"],
        "update" => &["--file", "--label", "--plist-dir"],
        _ => return Err(format!("unknown command {cmd}")),
    };
    while i < args.len() {
        let a = args[i];
        if !allowed.contains(&a) {
            return Err(format!("{cmd}: unknown option {a}"));
        }
        let mut value = || -> Result<String, String> {
            i += 1;
            args.get(i)
                .filter(|v| !v.is_empty())
                .map(|v| v.to_string())
                .ok_or_else(|| format!("{cmd}: {a} needs a value"))
        };
        match a {
            "--json" => o.json = true,
            "--yes" => o.yes = true,
            "--no-start" => o.no_start = true,
            "--no-pair-key" => o.no_pair_key = true,
            "--adopt" => o.adopt = true,
            "--fingerprint" => o.fingerprint = true,
            "--dir" => o.dir = Some(PathBuf::from(value()?)),
            "--file" => o.file = Some(PathBuf::from(value()?)),
            "--label" => o.label = Some(value()?),
            "--plist-dir" => o.plist_dir = Some(PathBuf::from(value()?)),
            "--env" => {
                let v = value()?;
                match v.split_once('=') {
                    Some((k, val)) if env_key_ok(k) => o.env.push((k.to_string(), val.to_string())),
                    _ => return Err(format!("{cmd}: --env wants KEY=VALUE, got {v}")),
                }
            }
            "--unset-env" => {
                let k = value()?;
                if !env_key_ok(&k) {
                    return Err(format!("{cmd}: --unset-env wants a variable name, got {k}"));
                }
                o.unset_env.push(k);
            }
            _ => return Err(format!("{cmd}: unknown option {a}")),
        }
        i += 1;
    }
    if o.adopt && (o.dir.is_some() || !o.env.is_empty() || !o.unset_env.is_empty()) {
        return Err(
            "install: --adopt only writes the record; it takes no --dir, --env or --unset-env"
                .into(),
        );
    }
    Ok(o)
}

/// The pairing channel's variable, and the value the owner's fingerprint asks need.
pub const PAIRING_ENV: &str = "AIBD_PAIRING";
pub const PAIRING_TOUCHID: &str = "touchid";

/// FINDING-reporeg-ownercheck-ebab8-1123 (9918e 派工 11-23): `AIBD_OWNER_TOUCH=1` works only
/// with `AIBD_PAIRING=touchid` (owner_ok.rs, [`crate::owner_ok::SWITCH`]); with any other
/// pairing every page ask answers `owner_check_unavailable`. So an install never writes the
/// one without the other:
/// - the switch off: nothing to do (`Ok(false)`);
/// - the pairing already `touchid`: nothing to do;
/// - the pairing missing or only kept from the old plist: raised to `touchid` (`Ok(true)`;
///   touchid is the stricter channel, the weak `terminal-unverified` is never kept beside it);
/// - the pairing named in this command (`pairing_named`: `--env` / `--unset-env`) as anything
///   else: refused, the command contradicts itself;
/// - a platform other than macOS: refused, never a weaker channel. This writes the macOS
///   launchd plist only; a Windows aibd needs no switch written, since with both values
///   unset it already pairs through Windows Hello and lets the page ask the owner
///   (8ced9f3eb6, [`crate::owner_ok::switch_on_here`]).
///
/// Against slips, not a defence: the same user can write the plist by hand
/// (GUIDE-same-user-ai-threat-model); the defence is the fingerprint itself.
pub fn owner_touch_env(
    env: &mut BTreeMap<String, String>,
    pairing_named: bool,
    os: &str,
) -> Result<bool, String> {
    let switch = crate::owner_ok::SWITCH;
    if !crate::owner_ok::switch_on(env.get(switch).cloned()) {
        return Ok(false);
    }
    if os != "macos" {
        return Err(format!(
            "{switch}=1 needs the owner's fingerprint pairing, and this writes only the macOS plist (on Windows leave it unset: aibd pairs through Windows Hello by default); not writing it"
        ));
    }
    match env.get(PAIRING_ENV).map(String::as_str) {
        Some(PAIRING_TOUCHID) => Ok(false),
        other if pairing_named => Err(format!(
            "{switch}=1 needs {PAIRING_ENV}={PAIRING_TOUCHID}, but this command sets {PAIRING_ENV} to {}; the page's fingerprint asks would stay unavailable",
            other.unwrap_or("nothing")
        )),
        _ => {
            env.insert(PAIRING_ENV.into(), PAIRING_TOUCHID.into());
            Ok(true)
        }
    }
}

fn env_key_ok(k: &str) -> bool {
    !k.is_empty()
        && k.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        && !k.as_bytes()[0].is_ascii_digit()
}

/// The install record: what `aibd install` put where (definition: IFACE §2).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Record {
    pub v: u32,
    pub at: String,
    pub version: String,
    pub build_sha256: String,
    pub exe: String,
    pub label: String,
    pub plist: String,
    pub env: BTreeMap<String, String>,
    pub platform: String,
    pub arch: String,
    /// true: written by `--adopt` for an aibd this command did not copy
    #[serde(default)]
    pub adopted: bool,
}

// ---------------------------------------------------------------- plist

/// The plist this command writes.
#[derive(Debug, Clone, PartialEq)]
pub struct PlistSpec {
    pub label: String,
    pub exe: String,
    pub log: String,
    pub env: BTreeMap<String, String>,
}

pub fn xml_escape(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => o.push_str("&amp;"),
            '<' => o.push_str("&lt;"),
            '>' => o.push_str("&gt;"),
            '"' => o.push_str("&quot;"),
            '\'' => o.push_str("&apos;"),
            _ => o.push(c),
        }
    }
    o
}

/// launchd plist for aibd: ProcessType Standard (a Background job is clamped to low priority,
/// launchd-background-clamp 11-16), restarted unless it exits cleanly, one log file.
pub fn render_plist(p: &PlistSpec) -> String {
    let mut s = String::from(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\">\n<dict>\n",
    );
    let kv = |s: &mut String, k: &str, v: &str| {
        s.push_str(&format!(
            "\t<key>{}</key>\n\t<string>{}</string>\n",
            xml_escape(k),
            xml_escape(v)
        ));
    };
    s.push_str("\t<key>EnvironmentVariables</key>\n\t<dict>\n");
    for (k, v) in &p.env {
        s.push_str(&format!(
            "\t\t<key>{}</key>\n\t\t<string>{}</string>\n",
            xml_escape(k),
            xml_escape(v)
        ));
    }
    s.push_str("\t</dict>\n");
    s.push_str(&format!(
        "\t<key>ExitTimeOut</key>\n\t<integer>{EXIT_TIMEOUT_S}</integer>\n"
    ));
    s.push_str("\t<key>KeepAlive</key>\n\t<dict>\n\t\t<key>SuccessfulExit</key>\n\t\t<false/>\n\t</dict>\n");
    kv(&mut s, "Label", &p.label);
    kv(&mut s, "ProcessType", "Standard");
    s.push_str(&format!(
        "\t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>{}</string>\n\t</array>\n",
        xml_escape(&p.exe)
    ));
    s.push_str("\t<key>RunAtLoad</key>\n\t<true/>\n");
    kv(&mut s, "StandardErrorPath", &p.log);
    kv(&mut s, "StandardOutPath", &p.log);
    s.push_str("\t<key>ThrottleInterval</key>\n\t<integer>30</integer>\n</dict>\n</plist>\n");
    s
}

/// One plist value, as far as this file needs it.
#[derive(Debug, Clone, PartialEq)]
pub enum PValue {
    Str(String),
    Dict(Vec<(String, PValue)>),
    Array(Vec<PValue>),
    Bool(bool),
    Other,
}

impl PValue {
    pub fn get(&self, key: &str) -> Option<&PValue> {
        match self {
            PValue::Dict(v) => v.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            PValue::Str(s) => Some(s),
            _ => None,
        }
    }
}

/// What install / status need from an existing plist.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct PlistSeen {
    pub label: Option<String>,
    pub program0: Option<String>,
    pub process_type: Option<String>,
    pub env: BTreeMap<String, String>,
}

#[derive(Debug, PartialEq)]
enum Tok {
    Open(String),
    Close(String),
    Empty(String),
    Text(String),
}

fn unescape(s: &str) -> Result<String, String> {
    let mut o = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(i) = rest.find('&') {
        o.push_str(&rest[..i]);
        let after = &rest[i + 1..];
        let end = after.find(';').ok_or("an & without ;")?;
        let name = &after[..end];
        let c = match name {
            "amp" => '&',
            "lt" => '<',
            "gt" => '>',
            "quot" => '"',
            "apos" => '\'',
            _ if name.starts_with("#x") => u32::from_str_radix(&name[2..], 16)
                .ok()
                .and_then(char::from_u32)
                .ok_or_else(|| format!("bad character reference &{name};"))?,
            _ if name.starts_with('#') => name[1..]
                .parse::<u32>()
                .ok()
                .and_then(char::from_u32)
                .ok_or_else(|| format!("bad character reference &{name};"))?,
            _ => return Err(format!("unknown entity &{name};")),
        };
        o.push(c);
        rest = &after[end + 1..];
    }
    o.push_str(rest);
    Ok(o)
}

fn tokens(text: &str) -> Result<Vec<Tok>, String> {
    let mut v = Vec::new();
    let mut rest = text;
    while !rest.is_empty() {
        if let Some(r) = rest.strip_prefix("<!--") {
            let e = r.find("-->").ok_or("unended comment")?;
            rest = &r[e + 3..];
        } else if rest.starts_with("<?") || rest.starts_with("<!") {
            let e = rest.find('>').ok_or("unended declaration")?;
            rest = &rest[e + 1..];
        } else if let Some(r) = rest.strip_prefix('<') {
            let e = r.find('>').ok_or("unended tag")?;
            let body = r[..e].trim();
            let name_of = |b: &str| b.split_whitespace().next().unwrap_or("").to_string();
            if let Some(n) = body.strip_prefix('/') {
                v.push(Tok::Close(name_of(n)));
            } else if let Some(n) = body.strip_suffix('/') {
                v.push(Tok::Empty(name_of(n)));
            } else {
                v.push(Tok::Open(name_of(body)));
            }
            rest = &r[e + 1..];
        } else {
            let e = rest.find('<').unwrap_or(rest.len());
            let t = &rest[..e];
            if !t.trim().is_empty() {
                v.push(Tok::Text(unescape(t)?));
            }
            rest = &rest[e..];
        }
    }
    Ok(v)
}

fn value(t: &[Tok], i: &mut usize) -> Result<PValue, String> {
    let tok = t.get(*i).ok_or("plist ends early")?;
    *i += 1;
    match tok {
        Tok::Empty(n) if n == "true" => Ok(PValue::Bool(true)),
        Tok::Empty(n) if n == "false" => Ok(PValue::Bool(false)),
        Tok::Empty(n) if n == "string" => Ok(PValue::Str(String::new())),
        Tok::Empty(n) if n == "dict" => Ok(PValue::Dict(Vec::new())),
        Tok::Empty(n) if n == "array" => Ok(PValue::Array(Vec::new())),
        Tok::Empty(_) => Ok(PValue::Other),
        Tok::Open(n) if n == "dict" => {
            let mut v = Vec::new();
            loop {
                match t.get(*i) {
                    Some(Tok::Close(c)) if c == "dict" => {
                        *i += 1;
                        return Ok(PValue::Dict(v));
                    }
                    Some(Tok::Open(k)) if k == "key" => {
                        *i += 1;
                        let key = match t.get(*i) {
                            Some(Tok::Text(s)) => {
                                *i += 1;
                                s.clone()
                            }
                            _ => String::new(),
                        };
                        match t.get(*i) {
                            Some(Tok::Close(c)) if c == "key" => *i += 1,
                            _ => return Err("a <key> is not closed".into()),
                        }
                        let val = value(t, i)?;
                        v.push((key, val));
                    }
                    _ => return Err("a <dict> holds something other than <key> pairs".into()),
                }
            }
        }
        Tok::Open(n) if n == "array" => {
            let mut v = Vec::new();
            loop {
                if let Some(Tok::Close(c)) = t.get(*i) {
                    if c == "array" {
                        *i += 1;
                        return Ok(PValue::Array(v));
                    }
                }
                v.push(value(t, i)?);
            }
        }
        Tok::Open(n) => {
            let n = n.clone();
            let mut text = String::new();
            if let Some(Tok::Text(s)) = t.get(*i) {
                text = s.clone();
                *i += 1;
            }
            match t.get(*i) {
                Some(Tok::Close(c)) if *c == n => *i += 1,
                _ => return Err(format!("<{n}> is not closed")),
            }
            Ok(if n == "string" {
                PValue::Str(text)
            } else {
                PValue::Other
            })
        }
        _ => Err("unexpected text or closing tag".into()),
    }
}

/// Read an XML plist. A binary plist or anything else is an error (the caller refuses).
pub fn parse_plist(text: &str) -> Result<PValue, String> {
    let t = tokens(text)?;
    let mut i = match t
        .iter()
        .position(|x| matches!(x, Tok::Open(n) if n == "plist"))
    {
        Some(p) => p + 1,
        None => return Err("not an XML plist".into()),
    };
    value(&t, &mut i)
}

pub fn seen(p: &PValue) -> PlistSeen {
    let mut s = PlistSeen {
        label: p.get("Label").and_then(PValue::as_str).map(str::to_owned),
        process_type: p
            .get("ProcessType")
            .and_then(PValue::as_str)
            .map(str::to_owned),
        ..Default::default()
    };
    if let Some(PValue::Array(a)) = p.get("ProgramArguments") {
        s.program0 = a.first().and_then(PValue::as_str).map(str::to_owned);
    }
    if let Some(PValue::Dict(d)) = p.get("EnvironmentVariables") {
        for (k, v) in d {
            if let Some(v) = v.as_str() {
                s.env.insert(k.clone(), v.to_string());
            }
        }
    }
    s
}

/// Two paths written as text name the same file, whichever separator each uses.
pub fn same_path(a: &str, b: &str) -> bool {
    let norm = |s: &str| s.replace('\\', "/").trim_end_matches('/').to_string();
    norm(a) == norm(b)
}

// ---------------------------------------------------------------- files

pub fn sha256_file(p: &Path) -> std::io::Result<String> {
    let mut f = std::fs::File::open(p)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex(&h.finalize()))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// RFC 3339 UTC from Unix seconds (no clock crate: civil-from-days).
pub fn rfc3339(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem / 60) % 60,
        rem % 60
    )
}

/// One file of the data fingerprint.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Print {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

fn skipped(rel: &str, is_dir: bool) -> bool {
    if is_dir {
        return FINGERPRINT_SKIP_DIRS.contains(&rel);
    }
    FINGERPRINT_SKIP_SUFFIXES.iter().any(|s| rel.ends_with(s))
}

/// Every file under the aibd data directory (relative path with `/`, size, sha256), sorted;
/// the skip list is [`FINGERPRINT_SKIP_DIRS`] and [`FINGERPRINT_SKIP_SUFFIXES`]. A symbolic
/// link is listed by its target text, never followed. A missing directory is an empty list.
pub fn fingerprint(root: &Path) -> std::io::Result<Vec<Print>> {
    let mut out = Vec::new();
    if !root.exists() {
        return Ok(out);
    }
    let mut stack = vec![(root.to_path_buf(), String::new())];
    while let Some((dir, rel)) = stack.pop() {
        for e in std::fs::read_dir(&dir)? {
            let e = e?;
            let name = e.file_name().to_string_lossy().into_owned();
            let r = if rel.is_empty() {
                name
            } else {
                format!("{rel}/{name}")
            };
            let md = std::fs::symlink_metadata(e.path())?;
            if md.file_type().is_symlink() {
                let t = std::fs::read_link(e.path())?;
                let text = t.to_string_lossy().into_owned();
                out.push(Print {
                    path: r,
                    size: text.len() as u64,
                    sha256: format!("link:{}", hex(&Sha256::digest(text.as_bytes()))),
                });
            } else if md.is_dir() {
                if !skipped(&r, true) {
                    stack.push((e.path(), r));
                }
            } else if !skipped(&r, false) {
                out.push(Print {
                    size: md.len(),
                    sha256: sha256_file(&e.path())?,
                    path: r,
                });
            }
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(out)
}

/// aibd is running when another process holds `<data>/run/aibd.lock` (the server's single
/// instance lock). The file is never created here.
pub fn running(data_dir: &Path) -> bool {
    let p = data_dir.join("run").join("aibd.lock");
    let Ok(f) = std::fs::OpenOptions::new().write(true).open(&p) else {
        return false;
    };
    matches!(f.try_lock(), Err(std::fs::TryLockError::WouldBlock))
}

fn write_atomic(p: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = p.with_extension(format!("tmp-{}", std::process::id()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, p).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

#[cfg(unix)]
fn make_executable(p: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755))
}
#[cfg(not(unix))]
fn make_executable(_: &Path) -> std::io::Result<()> {
    Ok(())
}

// ---------------------------------------------------------------- places

struct Places {
    label: String,
    plist: PathBuf,
    dir: Option<PathBuf>,
    log: Option<PathBuf>,
}

fn places(h: &Host, o: &Opts) -> Result<Places, String> {
    let home = h.home.clone();
    let need_home = || {
        home.clone()
            .ok_or("no home directory: pass --dir and --plist-dir")
    };
    let label = o.label.clone().unwrap_or_else(|| DEFAULT_LABEL.to_string());
    if label.contains('/') || label.contains('\\') || label.starts_with('.') {
        return Err(format!("bad --label {label}"));
    }
    let plist_dir = match &o.plist_dir {
        Some(d) => d.clone(),
        None => need_home()?.join("Library").join("LaunchAgents"),
    };
    let dir = match &o.dir {
        Some(d) => Some(d.clone()),
        None => home.as_ref().map(|h| h.join(".local").join("bin")),
    };
    Ok(Places {
        plist: plist_dir.join(format!("{label}.plist")),
        label,
        dir,
        log: home.map(|h| h.join("Library").join("Logs").join("ez2-aibd.log")),
    })
}

fn read_plist(p: &Path) -> Result<Option<PlistSeen>, String> {
    match std::fs::read_to_string(p) {
        Ok(t) => parse_plist(&t)
            .map(|v| Some(seen(&v)))
            .map_err(|e| format!("cannot read {}: {e}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("cannot read {}: {e}", p.display())),
    }
}

fn record_path(data: &Path) -> PathBuf {
    data.join(RECORD_DIR).join(RECORD_FILE)
}

fn read_record(data: &Path) -> Result<Option<Record>, String> {
    let p = record_path(data);
    match std::fs::read(&p) {
        Ok(b) => serde_json::from_slice(&b)
            .map(Some)
            .map_err(|e| format!("cannot read {}: {e}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("cannot read {}: {e}", p.display())),
    }
}

/// Where the record is: `AIBD_DATA_DIR`, else the platform default, else the `AIBD_DATA_DIR`
/// the plist hands aibd (5d33a L1: `status` from a terminal without the variable).
fn find_record(
    h: &Host,
    plist: Option<&PlistSeen>,
) -> Result<(Option<Record>, Option<PathBuf>, &'static str), String> {
    let mut tries: Vec<(PathBuf, &'static str)> = Vec::new();
    if let Some(d) = &h.data_dir_env {
        tries.push((d.clone(), "AIBD_DATA_DIR"));
    }
    if let Some(d) = &h.default_data_dir {
        tries.push((d.clone(), "default"));
    }
    if let Some(d) = plist
        .and_then(|p| p.env.get("AIBD_DATA_DIR"))
        .filter(|d| !d.is_empty())
    {
        tries.push((PathBuf::from(d), "plist"));
    }
    for (d, from) in &tries {
        if let Some(r) = read_record(d)? {
            return Ok((Some(r), Some(d.clone()), from));
        }
    }
    Ok((None, tries.first().map(|t| t.0.clone()), "none"))
}

/// A record answers for one label and one plist path. One data folder holds one record, so a
/// command run with another `--label` / `--plist-dir` must not act on it (5d33a M1: a test label
/// without `AIBD_DATA_DIR` would otherwise remove the real aibd).
fn not_ours(r: &Record, pl: &Places) -> Option<String> {
    if r.label == pl.label && same_path(&r.plist, &pl.plist.display().to_string()) {
        return None;
    }
    Some(format!(
        "the install record here is for label {} (plist {}), not {} ({}); use the same --label / --plist-dir, or another AIBD_DATA_DIR",
        r.label,
        r.plist,
        pl.label,
        pl.plist.display()
    ))
}

/// Files and bytes under the data folder, with the fingerprint's skip list; sizes from
/// metadata, nothing hashed (5d33a L1).
fn data_size(root: &Path) -> (usize, u64) {
    let (mut files, mut bytes) = (0usize, 0u64);
    let mut stack = vec![(root.to_path_buf(), String::new())];
    while let Some((dir, rel)) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            let r = if rel.is_empty() {
                name
            } else {
                format!("{rel}/{name}")
            };
            let Ok(md) = std::fs::symlink_metadata(e.path()) else {
                continue;
            };
            if md.is_dir() {
                if !skipped(&r, true) {
                    stack.push((e.path(), r));
                }
            } else if !skipped(&r, false) {
                files += 1;
                bytes += md.len();
            }
        }
    }
    (files, bytes)
}

fn unsupported(h: &Host, cmd: &str) -> Option<Out> {
    (h.os != "macos").then(|| {
        Out::fail(
            RC_UNSUPPORTED,
            format!("aibd {cmd}: unsupported: T97 Windows half (HKCU Run) not in this slice"),
        )
    })
}

// ---------------------------------------------------------------- commands

pub fn run(cmd: &str, args: &[&str], h: &Host, svc: &Svc) -> Out {
    let o = match parse(cmd, args) {
        Ok(o) => o,
        Err(e) => return Out::fail(RC_USAGE, format!("aibd {e}\n{USAGE}")),
    };
    if let Some(u) = unsupported(h, cmd) {
        return u;
    }
    let r = match cmd {
        "status" => status(h, &o, svc),
        "install" => install(h, &o, svc),
        "start" => start(h, &o, svc),
        "stop" => stop(h, &o, svc),
        "update" => Err("update runs through install::update::run_update".into()),
        _ => uninstall(h, &o, svc),
    };
    r.unwrap_or_else(|e| Out::fail(RC_REFUSED, format!("aibd {cmd}: {e}")))
}

// ---------------------------------------------------------------- launchd steps (§1b)

/// uid 0 has no gui domain; aibd is a per-user service (5d33a N1).
fn not_root(h: &Host) -> Result<(), String> {
    if h.uid == Some(0) {
        return Err(
            "do not run this with sudo: aibd is a per-user launchd service (gui/<uid>)".into(),
        );
    }
    Ok(())
}

/// Wait until nobody holds `run/aibd.lock`; false when it is still held at the limit.
fn wait_unlocked(data: &Path, svc: &Svc) -> bool {
    let steps = (EXIT_TIMEOUT_S + STOP_GRACE_S) * 1000 / STEP_MS;
    for _ in 0..steps {
        if !running(data) {
            return true;
        }
        (svc.pace)(std::time::Duration::from_millis(STEP_MS));
    }
    !running(data)
}

/// Wait for health to answer with an instance id other than `old` (5d33a codex-O9).
fn wait_new_instance(data: &Path, old: Option<&str>, svc: &Svc) -> Option<String> {
    let steps = START_LIMIT_S * 1000 / STEP_MS;
    for _ in 0..=steps {
        if let Some(id) = svc.health.instance(data) {
            if Some(id.as_str()) != old {
                return Some(id);
            }
        }
        (svc.pace)(std::time::Duration::from_millis(STEP_MS));
    }
    None
}

/// Take the label out of launchd and wait for aibd to let go of its lock.
/// `strict`: an unreadable `print` stops the command (install / start / stop); uninstall
/// goes on when no aibd holds the lock.
fn unload(label: &str, data: &Path, svc: &Svc, out: &mut Out, strict: bool) -> Result<(), Out> {
    match loaded(&svc.ld.print(label)) {
        Loaded::No => {}
        Loaded::Unknown(why) if strict || running(data) => {
            return Err(Out::fail(
                RC_REFUSED,
                format!("launchctl print {label}: {why}"),
            ));
        }
        Loaded::Unknown(why) => out.say(format!(
            "LAUNCHD-UNKNOWN {why} (no aibd holds the lock; going on; if it is still listed: launchctl bootout {}/{label})",
            svc.ld.domain()
        )),
        Loaded::Yes { .. } => {
            let r = svc.ld.bootout(label);
            match r.code {
                Some(0) => out.say(format!("BOOTOUT {}/{label}", svc.ld.domain())),
                // Boot-out failed: 3: No such process — already out
                Some(3) => out.say(format!("ALREADY-OUT {label}")),
                _ => {
                    return Err(Out::fail(
                        RC_REFUSED,
                        format!("launchctl bootout {label}: {}", first_line(&r)),
                    ))
                }
            }
            if !wait_unlocked(data, svc) {
                return Err(Out::fail(
                    RC_RUNNING,
                    format!(
                        "aibd still holds its lock {}s after bootout; launchd is still stopping it (not killed here)",
                        EXIT_TIMEOUT_S + STOP_GRACE_S
                    ),
                ));
            }
        }
    }
    if running(data) {
        return Err(Out::fail(RC_RUNNING, NOT_LAUNCHD));
    }
    Ok(())
}

/// Bootstrap the plist (RunAtLoad starts it; no kickstart after, 5d33a L1) and wait for a
/// new instance id.
fn register(plist: &Path, data: &Path, svc: &Svc, out: &mut Out) -> Result<(), Out> {
    let old = svc.health.instance(data);
    let r = svc.ld.bootstrap(plist);
    if !r.ok() {
        return Err(Out::fail(
            RC_REFUSED,
            format!(
                "launchctl bootstrap {} {}: {}",
                svc.ld.domain(),
                plist.display(),
                first_line(&r)
            ),
        ));
    }
    out.say(format!("BOOTSTRAP {} {}", svc.ld.domain(), plist.display()));
    up(data, old.as_deref(), svc, out)
}

fn up(data: &Path, old: Option<&str>, svc: &Svc, out: &mut Out) -> Result<(), Out> {
    match wait_new_instance(data, old, svc) {
        Some(id) => {
            out.say(format!("STARTED instance {id}"));
            Ok(())
        }
        None => Err(Out::fail(
            RC_REFUSED,
            format!("aibd did not answer health with a new instance within {START_LIMIT_S}s"),
        )),
    }
}

/// The record this label owns, with its data folder; refused when the record is another
/// label's (5d33a T97-1m M1).
fn owned(h: &Host, o: &Opts) -> Result<(Record, PathBuf, Places), String> {
    let pl = places(h, o)?;
    let plist = read_plist(&pl.plist).ok().flatten();
    let (rec, data, _) = find_record(h, plist.as_ref())?;
    let (Some(rec), Some(data)) = (rec, data) else {
        return Err("not installed (no install record): aibd install first".into());
    };
    not_ours(&rec, &pl).map_or(Ok(()), Err)?;
    Ok((rec, data, pl))
}

fn start(h: &Host, o: &Opts, svc: &Svc) -> Result<Out, String> {
    not_root(h)?;
    let (rec, data, _) = owned(h, o)?;
    let mut out = Out::default();
    // T97-3m (5d33a r1 L3): an update left half-done is finished or undone by this CLI first
    update::recover(&rec, &data, svc, &mut out)?;
    let state = loaded(&svc.ld.print(&rec.label));
    if let Loaded::Yes { pid: Some(p), .. } = &state {
        out.say(format!("ALREADY-RUNNING pid {p}"));
        return Ok(out);
    }
    // 5d33a T97-2m M1: an aibd launchd does not run holds the lock; a launchd one would exit 3
    // and KeepAlive would restart it forever
    if running(&data) {
        return Ok(Out::fail(RC_RUNNING, NOT_LAUNCHD));
    }
    let res = match state {
        Loaded::Yes { pid: Some(_), .. } => Ok(()),
        Loaded::Yes { .. } => {
            let old = svc.health.instance(&data);
            let r = svc.ld.kickstart(&rec.label);
            if r.ok() {
                out.say(format!("KICKSTART {}/{}", svc.ld.domain(), rec.label));
                up(&data, old.as_deref(), svc, &mut out)
            } else {
                Err(Out::fail(
                    RC_REFUSED,
                    format!("launchctl kickstart {}: {}", rec.label, first_line(&r)),
                ))
            }
        }
        Loaded::No => register(Path::new(&rec.plist), &data, svc, &mut out),
        Loaded::Unknown(why) => Err(Out::fail(
            RC_REFUSED,
            format!("launchctl print {}: {why}", rec.label),
        )),
    };
    Ok(keep_lines(out, res))
}

/// The error with the lines already printed kept (5d33a T97-2m N1).
fn keep_lines(out: Out, res: Result<(), Out>) -> Out {
    match res {
        Ok(()) => out,
        Err(e) => Out {
            code: e.code,
            stdout: out.stdout,
            stderr: e.stderr,
        },
    }
}

fn stop(h: &Host, o: &Opts, svc: &Svc) -> Result<Out, String> {
    not_root(h)?;
    let (rec, data, _) = owned(h, o)?;
    let mut out = Out::default();
    Ok(match unload(&rec.label, &data, svc, &mut out, true) {
        Ok(()) => {
            out.say("STOPPED");
            out
        }
        Err(e) => e,
    })
}

fn status(h: &Host, o: &Opts, svc: &Svc) -> Result<Out, String> {
    let pl = places(h, o)?;
    let plist = read_plist(&pl.plist).ok().flatten();
    let (rec, data, from) = find_record(h, plist.as_ref())?;
    let mut mismatch: Vec<String> = Vec::new();
    let rec = match rec {
        Some(r) => match not_ours(&r, &pl) {
            None => Some(r),
            Some(why) => {
                mismatch.push(why);
                None
            }
        },
        None => None,
    };
    let mut out = Out::default();
    if o.fingerprint {
        let Some(data) = data else {
            return Err("no aibd data directory (set AIBD_DATA_DIR)".into());
        };
        let fp = fingerprint(&data).map_err(|e| format!("fingerprint {}: {e}", data.display()))?;
        if o.json {
            out.say(
                serde_json::json!({"data_dir": data.display().to_string(),
                    "skip_dirs": FINGERPRINT_SKIP_DIRS, "skip_suffixes": FINGERPRINT_SKIP_SUFFIXES, "files": fp})
                .to_string(),
            );
        } else {
            out.say(format!(
                "FINGERPRINT {} skip dirs={} suffixes={}",
                data.display(),
                FINGERPRINT_SKIP_DIRS.join(","),
                FINGERPRINT_SKIP_SUFFIXES.join(",")
            ));
            for p in fp {
                out.say(format!("{}  {}  {}", p.sha256, p.size, p.path));
            }
        }
        return Ok(out);
    }
    let (mut exe_ok, mut plist_ok) = (false, false);
    if let Some(r) = &rec {
        exe_ok = sha256_file(Path::new(&r.exe)).ok().as_deref() == Some(r.build_sha256.as_str());
        if !exe_ok {
            mismatch.push(format!(
                "exe {} is missing or not the recorded build",
                r.exe
            ));
        }
        let rp = read_plist(Path::new(&r.plist)).ok().flatten();
        plist_ok = rp.as_ref().is_some_and(|p| {
            p.program0.as_deref().is_some_and(|x| same_path(x, &r.exe))
                && p.label.as_deref() == Some(r.label.as_str())
        });
        if !plist_ok {
            mismatch.push(format!(
                "plist {} is missing or does not point at {}",
                r.plist, r.exe
            ));
        }
    }
    let run = data.as_deref().is_some_and(running);
    let (ld_state, ld_pid) = match loaded(&svc.ld.print(&pl.label)) {
        Loaded::Yes { state, pid } => (state.unwrap_or_else(|| "unknown".into()), pid),
        Loaded::No => ("not-loaded".to_string(), None),
        Loaded::Unknown(_) => ("unknown".to_string(), None),
    };
    let bytes: u64 = data.as_deref().map(|d| data_size(d).1).unwrap_or(0);
    let code = if mismatch.is_empty() {
        RC_OK
    } else {
        RC_MISMATCH
    };
    if o.json {
        out.say(
            serde_json::json!({
                "installed": rec.is_some(), "record_from": from,
                "adopted": rec.as_ref().is_some_and(|r| r.adopted),
                "exe": rec.as_ref().map(|r| r.exe.clone()), "exe_ok": exe_ok,
                "plist": rec.as_ref().map(|r| r.plist.clone()), "plist_ok": plist_ok,
                "running": run, "launchd": ld_state, "launchd_pid": ld_pid,
                "data_dir": data.as_ref().map(|d| d.display().to_string()), "data_bytes": bytes,
                "mismatch": mismatch,
            })
            .to_string(),
        );
    } else {
        out.say(format!(
            "installed {}",
            if rec.is_some() { "yes" } else { "no" }
        ));
        out.say(format!("record_from {from}"));
        if let Some(r) = &rec {
            out.say(format!(
                "exe {} {}",
                r.exe,
                if exe_ok { "ok" } else { "MISMATCH" }
            ));
            out.say(format!(
                "plist {} {}",
                r.plist,
                if plist_ok { "ok" } else { "MISMATCH" }
            ));
            if r.adopted {
                out.say("adopted (recorded by --adopt; not copied by aibd install)");
            }
        }
        out.say(format!("running {}", if run { "yes" } else { "no" }));
        match ld_pid {
            Some(p) => out.say(format!("launchd {ld_state} pid {p}")),
            None => out.say(format!("launchd {ld_state}")),
        }
        if let Some(d) = &data {
            out.say(format!("data_dir {} {bytes} bytes", d.display()));
        }
        for m in &mismatch {
            out.say(format!(
                "\u{1b}[31mMISMATCH {m}\u{1b}[0m (a reminder, not a defence)"
            ));
        }
    }
    out.code = code;
    Ok(out)
}

fn install(h: &Host, o: &Opts, svc: &Svc) -> Result<Out, String> {
    let stopped = std::cell::Cell::new(false);
    install_steps(h, o, svc, &stopped).map_err(|e| {
        if stopped.get() {
            // 5d33a T97-2m L1: say that aibd is no longer running
            format!(
                "{e}; aibd was stopped: fix the error and run aibd install again (or aibd start)"
            )
        } else {
            e
        }
    })
}

fn install_steps(
    h: &Host,
    o: &Opts,
    svc: &Svc,
    stopped: &std::cell::Cell<bool>,
) -> Result<Out, String> {
    if o.adopt && !o.no_start {
        return Ok(Out::fail(
            RC_USAGE,
            "aibd install --adopt only writes the record: aibd install --no-start --adopt, then aibd start",
        ));
    }
    if !o.no_start {
        not_root(h)?;
    }
    let pl = places(h, o)?;
    let existing = read_plist(&pl.plist)?;
    let (old_rec, _, _) = find_record(h, existing.as_ref())?;
    if let Some(why) = old_rec.as_ref().and_then(|r| not_ours(r, &pl)) {
        return Err(why);
    }
    let mut out = Out::default();
    // environment: the old plist's keys stay unless unset (5d33a L2); --env wins. Worked out
    // before anything is stopped or copied, so a refused combination changes nothing.
    let mut env: BTreeMap<String, String> = BTreeMap::new();
    if !o.adopt {
        let given: Vec<&str> = o.env.iter().map(|(k, _)| k.as_str()).collect();
        if let Some(ex) = &existing {
            for (k, v) in &ex.env {
                if k == "PATH" {
                    continue;
                }
                if o.unset_env.iter().any(|u| u == k) {
                    out.say(format!("UNSET-ENV {k}"));
                    continue;
                }
                if !given.contains(&k.as_str()) {
                    out.say(format!("KEPT-ENV {k}"));
                }
                env.insert(k.to_owned(), v.to_owned());
            }
        }
        for (k, v) in &o.env {
            env.insert(k.clone(), v.clone());
        }
        let named = given.contains(&PAIRING_ENV) || o.unset_env.iter().any(|u| u == PAIRING_ENV);
        if owner_touch_env(&mut env, named, h.os)? {
            out.say(format!(
                "SET-ENV {PAIRING_ENV}={PAIRING_TOUCHID} ({}=1 needs it)",
                crate::owner_ok::SWITCH
            ));
        }
    }
    // ebab8 11-26 第四包: a fingerprint pairing gets its key here, made or checked, before
    // anything is stopped, copied or written, so a failure changes nothing (3e53b r1 a–c)
    let wants_key = !o.adopt && env.get(PAIRING_ENV).map(String::as_str) == Some(PAIRING_TOUCHID);
    if wants_key && o.no_pair_key {
        out.say("PAIR-KEY skipped (--no-pair-key): the fingerprint pairing stays unavailable until aibd install runs without it");
    } else if wants_key {
        let data = data_dir_for(h, &env)?;
        match svc.key.ensure(&data) {
            Ok(crate::touch::PairKeyMade::Checked) => {
                out.say("PAIR-KEY checked (the key there passed aibd's start-up check)")
            }
            Ok(crate::touch::PairKeyMade::Made(k)) => out.say(format!(
                "PAIR-KEY made ({})",
                match k {
                    crate::owner_probe::KeyKind::Finger => "it asks for your fingerprint",
                    crate::owner_probe::KeyKind::LoginPassword =>
                        "this Mac has no Touch ID, so it asks for your login password",
                }
            )),
            Err(why) => return Err(format!("pairing key: {why}")),
        }
    }
    // 5d33a T97-2m r1 M1: an aibd already in launchd is taken out (and waited for) before
    // the plist is rewritten; otherwise bootstrap answers 5 and the new plist is not used
    if !o.no_start {
        if let Some(data) = h
            .data_dir_env
            .clone()
            .or_else(|| old_rec.as_ref().and_then(|r| data_dir_for(h, &r.env).ok()))
            .or_else(|| h.default_data_dir.clone())
        {
            if let Err(e) = unload(&pl.label, &data, svc, &mut out, true) {
                return Ok(e);
            }
            stopped.set(out.stdout.contains("BOOTOUT "));
        }
    }

    if o.adopt {
        let Some(ex) = existing else {
            return Err(format!(
                "nothing to adopt: {} does not exist",
                pl.plist.display()
            ));
        };
        let exe = ex
            .program0
            .clone()
            .ok_or_else(|| format!("{} has no ProgramArguments", pl.plist.display()))?;
        let sha = sha256_file(Path::new(&exe)).map_err(|e| format!("{exe}: {e}"))?;
        let rec = Record {
            v: 1,
            at: rfc3339(h.now_unix),
            version: String::new(),
            build_sha256: sha,
            exe,
            label: pl.label.clone(),
            plist: pl.plist.display().to_string(),
            env: ex.env.clone(),
            platform: h.os.to_string(),
            arch: h.arch.to_string(),
            adopted: true,
        };
        let data = data_dir_for(h, &rec.env)?;
        write_record(&data, &rec)?;
        out.say(format!(
            "ADOPTED {} (record only; executable and plist untouched)",
            rec.exe
        ));
        // adopt never writes the plist, so a mismatched pair is only reported here
        if owner_touch_env(&mut rec.env.clone(), true, h.os).is_err() {
            out.say(format!(
                "WARN-ENV {}=1 without {PAIRING_ENV}={PAIRING_TOUCHID}: the page's fingerprint asks stay unavailable; aibd install --no-start --env {PAIRING_ENV}={PAIRING_TOUCHID}",
                crate::owner_ok::SWITCH
            ));
        }
        out.say(format!("RECORD {}", record_path(&data).display()));
        return Ok(out);
    }

    let dir = pl.dir.clone().ok_or("no home directory: pass --dir")?;
    let target = dir.join(exe_name());
    let target_s = target.display().to_string();
    // 5d33a M1: the label already points somewhere this command did not put there
    if let Some(ex) = &existing {
        let points = ex.program0.clone().unwrap_or_default();
        let ours = old_rec.as_ref().is_some_and(|r| same_path(&r.exe, &points));
        if !same_path(&points, &target_s) && !ours {
            return Err(format!(
                "{} already points at {points} (not installed by aibd install); to take it over: aibd install --no-start --adopt",
                pl.plist.display()
            ));
        }
    }
    let me = sha256_file(&h.exe).map_err(|e| format!("{}: {e}", h.exe.display()))?;
    // a different build is aibd update (T97-3); this refusal is against slips only (5d33a M2)
    if let Some(r) = &old_rec {
        if !same_path(&r.exe, &target_s) && r.build_sha256 != me {
            return Err(format!(
                "recorded install {} is another build; replacing a build is aibd update (T97-3)",
                r.exe
            ));
        }
    }
    let is_self = std::fs::canonicalize(&target).ok() == std::fs::canonicalize(&h.exe).ok()
        && target.exists();
    if target.exists() && !is_self {
        let there = sha256_file(&target).map_err(|e| format!("{target_s}: {e}"))?;
        if there != me {
            return Err(format!(
                "{target_s} is another build; replacing a build is aibd update (T97-3)"
            ));
        }
        out.say(format!("SAME {target_s}"));
    } else if !is_self {
        std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        let tmp = dir.join(format!(".{}.tmp-{}", exe_name(), std::process::id()));
        std::fs::copy(&h.exe, &tmp).map_err(|e| format!("copy to {}: {e}", tmp.display()))?;
        let copied = sha256_file(&tmp).map_err(|e| e.to_string())?;
        if copied != me {
            let _ = std::fs::remove_file(&tmp);
            return Err("the copy does not hash like this executable".into());
        }
        make_executable(&tmp).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &target).map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            format!("rename into {target_s}: {e}")
        })?;
        out.say(format!("COPIED {target_s}"));
    }

    env.entry("PATH".into())
        .or_insert_with(|| format!("{}:{PATH_TAIL}", dir.display()));
    let log = pl
        .log
        .clone()
        .unwrap_or_else(|| dir.join("ez2-aibd.log"))
        .display()
        .to_string();
    let spec = PlistSpec {
        label: pl.label.clone(),
        exe: target_s.clone(),
        log,
        env: env.clone(),
    };
    if let Some(d) = pl.plist.parent() {
        std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    write_atomic(&pl.plist, render_plist(&spec).as_bytes())
        .map_err(|e| format!("write {}: {e}", pl.plist.display()))?;
    out.say(format!("PLIST {}", pl.plist.display()));

    let rec = Record {
        v: 1,
        at: rfc3339(h.now_unix),
        version: h.version.clone(),
        build_sha256: me,
        exe: target_s.clone(),
        label: pl.label.clone(),
        plist: pl.plist.display().to_string(),
        env,
        platform: h.os.to_string(),
        arch: h.arch.to_string(),
        adopted: false,
    };
    let data = data_dir_for(h, &rec.env)?;
    write_record(&data, &rec)?;
    out.say(format!("RECORD {}", record_path(&data).display()));

    // moved with --dir: take away the old copy when it is still the recorded build
    if let Some(r) = &old_rec {
        if !same_path(&r.exe, &target_s) && !r.adopted {
            let old = Path::new(&r.exe);
            if sha256_file(old).ok().as_deref() == Some(r.build_sha256.as_str()) {
                std::fs::remove_file(old).map_err(|e| format!("remove old {}: {e}", r.exe))?;
                out.say(format!("REMOVED-OLD {}", r.exe));
            }
        }
    }
    if o.no_start {
        out.say("NOT-STARTED (--no-start)");
        return Ok(out);
    }
    let res = register(&pl.plist, &data, svc, &mut out);
    Ok(keep_lines(out, res))
}

fn exe_name() -> &'static str {
    if cfg!(windows) {
        "aibd.exe"
    } else {
        "aibd"
    }
}

fn data_dir_for(h: &Host, env: &BTreeMap<String, String>) -> Result<PathBuf, String> {
    h.data_dir_env
        .clone()
        .or_else(|| {
            env.get("AIBD_DATA_DIR")
                .filter(|d| !d.is_empty())
                .map(PathBuf::from)
        })
        .or_else(|| h.default_data_dir.clone())
        .ok_or_else(|| "no aibd data directory (set AIBD_DATA_DIR)".into())
}

fn write_record(data: &Path, r: &Record) -> Result<(), String> {
    let dir = data.join(RECORD_DIR);
    crate::platform::create_private_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let body = serde_json::to_vec_pretty(r).map_err(|e| e.to_string())?;
    crate::platform::write_private_file(&record_path(data), &body)
        .map_err(|e| format!("write {}: {e}", record_path(data).display()))
}

fn uninstall(h: &Host, o: &Opts, svc: &Svc) -> Result<Out, String> {
    not_root(h)?;
    let pl = places(h, o)?;
    let plist = read_plist(&pl.plist).ok().flatten();
    let (rec, data, _) = find_record(h, plist.as_ref())?;
    let mut out = Out::default();
    let (Some(rec), Some(data)) = (rec, data) else {
        out.say("NOT-INSTALLED (no install record; nothing removed)");
        return Ok(out);
    };
    if let Some(why) = not_ours(&rec, &pl) {
        return Err(format!("{why}; nothing removed"));
    }
    let files = data_size(&data).0;
    if !o.yes {
        let how = if rec.adopted {
            " (adopted: recorded by --adopt, not copied by aibd install)"
        } else {
            ""
        };
        out.say(format!("WOULD-REMOVE {}", rec.plist));
        out.say(format!("WOULD-REMOVE {}{how}", rec.exe));
        out.say(format!("WOULD-KEEP {} {files} files", data.display()));
        out.say("pass --yes to remove");
        return Ok(out);
    }
    if let Ok(s) = sha256_file(Path::new(&rec.exe)) {
        if s != rec.build_sha256 {
            return Err(format!(
                "{} is not the recorded build; nothing removed",
                rec.exe
            ));
        }
    }
    // 5d33a N2 (T97-1m r1): out of launchd first, then the files (adopted the same)
    if let Err(e) = unload(&rec.label, &data, svc, &mut out, false) {
        return Ok(e);
    }
    for p in [&rec.plist, &rec.exe] {
        match std::fs::remove_file(p) {
            Ok(()) => out.say(format!("REMOVED {p}")),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => out.say(format!("GONE {p}")),
            Err(e) => return Err(format!("remove {p}: {e}")),
        }
    }
    std::fs::remove_file(record_path(&data)).map_err(|e| format!("remove record: {e}"))?;
    let _ = std::fs::remove_dir(data.join(RECORD_DIR));
    out.say(format!("DATA-KEPT {} {files}", data.display()));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> PlistSpec {
        let mut env = BTreeMap::new();
        env.insert("AIB_DRIVE".into(), "/x/aib-drive".into());
        env.insert("PATH".into(), format!("/h/.local/bin:{PATH_TAIL}"));
        PlistSpec {
            label: "app.ez2.aibd.test-a&b".into(),
            exe: "/h/.local/bin/aibd".into(),
            log: "/h/Library/Logs/ez2-aibd.log".into(),
            env,
        }
    }

    #[test]
    fn the_plist_reads_back_with_process_type_standard_and_every_env_key() {
        let s = spec();
        let text = render_plist(&s);
        assert!(text.contains("app.ez2.aibd.test-a&amp;b"), "{text}");
        let v = parse_plist(&text).unwrap();
        let seen = seen(&v);
        assert_eq!(seen.label.as_deref(), Some("app.ez2.aibd.test-a&b"));
        assert_eq!(seen.program0.as_deref(), Some("/h/.local/bin/aibd"));
        assert_eq!(seen.process_type.as_deref(), Some("Standard"));
        assert_eq!(seen.env, s.env);
        assert_eq!(v.get("RunAtLoad"), Some(&PValue::Bool(true)));
    }

    #[test]
    fn a_plist_written_by_hand_with_tabs_and_comments_is_read() {
        let t = "<?xml version=\"1.0\"?>\n<!DOCTYPE plist>\n<plist version=\"1.0\">\n<dict>\n<!-- c -->\n\t<key>Label</key>\n\t<string>app.ez2.aibd</string>\n\t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>/Users/u/.ez2-aib/bin/aibd</string>\n\t</array>\n\t<key>EnvironmentVariables</key>\n\t<dict>\n\t\t<key>AIBD_DRIVE_WAKE</key>\n\t\t<string>1</string>\n\t</dict>\n\t<key>ThrottleInterval</key>\n\t<integer>30</integer>\n</dict>\n</plist>\n";
        let s = seen(&parse_plist(t).unwrap());
        assert_eq!(s.program0.as_deref(), Some("/Users/u/.ez2-aib/bin/aibd"));
        assert_eq!(s.env.get("AIBD_DRIVE_WAKE").map(String::as_str), Some("1"));
        assert!(parse_plist("bplist00\u{0}\u{1}").is_err());
    }

    #[test]
    fn paths_compare_with_either_separator() {
        assert!(same_path(
            "C:\\Users\\u\\AppData\\Local\\aibd\\aibd.exe",
            "C:/Users/u/AppData/Local/aibd/aibd.exe"
        ));
        assert!(same_path("/h/.local/bin/aibd", "/h/.local/bin/aibd/"));
        assert!(!same_path("/h/.local/bin/aibd", "/h/.ez2-aib/bin/aibd"));
    }

    #[test]
    fn options_are_checked_per_command() {
        assert!(parse("status", &["--yes"]).is_err());
        assert!(parse("install", &["--env", "1A=x"]).is_err());
        assert!(parse("install", &["--adopt", "--dir", "/x"]).is_err());
        let o = parse(
            "install",
            &["--no-start", "--env", "A_B=c=d", "--unset-env", "X"],
        )
        .unwrap();
        assert_eq!(o.env, vec![("A_B".to_string(), "c=d".to_string())]);
        assert_eq!(o.unset_env, vec!["X".to_string()]);
    }

    #[test]
    fn rfc3339_from_unix_seconds() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(1_795_390_200), "2026-11-22T23:30:00Z");
    }

    #[test]
    fn the_fingerprint_skips_runtime_files_and_the_record() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("run")).unwrap();
        std::fs::create_dir_all(d.path().join("install")).unwrap();
        std::fs::create_dir_all(d.path().join("seats")).unwrap();
        std::fs::write(d.path().join("run/aibd.lock"), b"").unwrap();
        std::fs::write(d.path().join("install/record.json"), b"{}").unwrap();
        std::fs::write(d.path().join("aibd.log"), b"x").unwrap();
        std::fs::write(d.path().join("seats/a.json"), b"abc").unwrap();
        let fp = fingerprint(d.path()).unwrap();
        assert_eq!(fp.len(), 1, "{fp:?}");
        assert_eq!(fp[0].path, "seats/a.json");
        assert_eq!(fp[0].size, 3);
    }

    #[test]
    fn every_command_is_unsupported_off_macos() {
        let h = Host {
            os: "windows",
            arch: "x86_64",
            home: Some(PathBuf::from("C:\\Users\\u")),
            data_dir_env: None,
            default_data_dir: None,
            exe: PathBuf::from("C:\\x\\aibd.exe"),
            version: "aibd 0 windows-x86_64".into(),
            now_unix: 0,
            uid: None,
        };
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        let svc = f.svc(&hl);
        for c in ["status", "install", "uninstall", "start", "stop"] {
            let args: &[&str] = if c == "install" { &["--no-start"] } else { &[] };
            assert_eq!(run(c, args, &h, &svc).code, RC_UNSUPPORTED, "{c}");
        }
        assert!(f.calls.borrow().is_empty());
    }

    // ------------------------------------------------ T97-2m launchd stand-ins
    // Fixtures are this Mac's real launchctl answers (11-17 18:2x, a probe label of 043dd's,
    // removed after): IFACE-T97-2m r2 §1b.
    use std::cell::RefCell;
    use std::collections::VecDeque;

    pub(super) fn lo(code: i32, stdout: &str, stderr: &str) -> LaunchdOut {
        LaunchdOut {
            code: Some(code),
            stdout: stdout.into(),
            stderr: stderr.into(),
        }
    }
    pub(super) fn print_not_running() -> LaunchdOut {
        lo(
            0,
            "gui/501/app.ez2.aibd.unit = {\n\tactive count = 0\n\tstate = not running\n}\n",
            "",
        )
    }
    pub(super) fn print_running(pid: u32) -> LaunchdOut {
        lo(0, &format!("gui/501/app.ez2.aibd.unit = {{\n\tstate = running\n\tprogram = /x/aibd\n\tpid = {pid}\n}}\n"), "")
    }
    pub(super) fn print_missing() -> LaunchdOut {
        lo(113, "", "Bad request.\nCould not find service \"app.ez2.aibd.unit\" in domain for user gui: 501\n")
    }

    #[derive(Default)]
    pub(super) struct FakeLd {
        pub(super) calls: RefCell<Vec<String>>,
        pub(super) prints: RefCell<VecDeque<LaunchdOut>>,
        bootstrap: RefCell<Option<LaunchdOut>>,
        bootout: RefCell<Option<LaunchdOut>>,
        /// at bootout: did this file still exist
        watch: RefCell<Option<(PathBuf, bool)>>,
        /// the pairing key step: the data folders it was handed, and its answer (made a
        /// fingerprint key when unset)
        pub(super) key_asked: RefCell<Vec<PathBuf>>,
        pub(super) key_answer: RefCell<Option<Result<crate::touch::PairKeyMade, String>>>,
    }

    impl PairKey for FakeLd {
        fn ensure(&self, data: &Path) -> Result<crate::touch::PairKeyMade, String> {
            self.key_asked.borrow_mut().push(data.to_path_buf());
            self.key_answer
                .borrow()
                .clone()
                .unwrap_or(Ok(crate::touch::PairKeyMade::Made(
                    crate::owner_probe::KeyKind::Finger,
                )))
        }
    }

    impl FakeLd {
        pub(super) fn svc<'a>(&'a self, h: &'a FakeHealth) -> Svc<'a> {
            Svc {
                ld: self,
                health: h,
                pace: &|_| {},
                key: self,
            }
        }
        pub(super) fn calls(&self) -> Vec<String> {
            self.calls.borrow().clone()
        }
    }

    impl Launchd for FakeLd {
        fn domain(&self) -> String {
            "gui/501".into()
        }
        fn bootstrap(&self, plist: &Path) -> LaunchdOut {
            self.calls
                .borrow_mut()
                .push(format!("bootstrap gui/501 {}", plist.display()));
            self.bootstrap
                .borrow()
                .clone()
                .unwrap_or_else(|| lo(0, "", ""))
        }
        fn bootout(&self, label: &str) -> LaunchdOut {
            self.calls
                .borrow_mut()
                .push(format!("bootout gui/501/{label}"));
            if let Some((p, seen)) = self.watch.borrow_mut().as_mut() {
                *seen = p.exists();
            }
            self.bootout
                .borrow()
                .clone()
                .unwrap_or_else(|| lo(0, "", ""))
        }
        fn kickstart(&self, label: &str) -> LaunchdOut {
            self.calls
                .borrow_mut()
                .push(format!("kickstart gui/501/{label}"));
            lo(0, "", "")
        }
        fn print(&self, label: &str) -> LaunchdOut {
            self.calls
                .borrow_mut()
                .push(format!("print gui/501/{label}"));
            self.prints
                .borrow_mut()
                .pop_front()
                .unwrap_or_else(print_missing)
        }
    }

    /// Answers the queued ids in turn, then the last one forever.
    #[derive(Default)]
    pub(super) struct FakeHealth {
        ids: RefCell<VecDeque<Option<String>>>,
        last: RefCell<Option<String>>,
    }

    impl FakeHealth {
        pub(super) fn answering(ids: &[Option<&str>]) -> FakeHealth {
            FakeHealth {
                ids: RefCell::new(ids.iter().map(|i| i.map(str::to_owned)).collect()),
                last: RefCell::new(None),
            }
        }
    }

    impl Health for FakeHealth {
        fn instance(&self, _: &Path) -> Option<String> {
            if let Some(i) = self.ids.borrow_mut().pop_front() {
                *self.last.borrow_mut() = i.clone();
                return i;
            }
            self.last.borrow().clone()
        }
    }

    pub(super) struct Mac {
        d: tempfile::TempDir,
        pub(super) h: Host,
    }

    impl Mac {
        pub(super) fn new() -> Mac {
            let d = tempfile::tempdir().unwrap();
            std::fs::create_dir_all(d.path().join("src")).unwrap();
            std::fs::write(d.path().join("src/aibd"), b"this build").unwrap();
            let h = Host {
                os: "macos",
                arch: "aarch64",
                home: Some(d.path().to_path_buf()),
                data_dir_env: Some(d.path().join("data")),
                default_data_dir: None,
                exe: d.path().join("src/aibd"),
                version: "aibd 0 macos-aarch64".into(),
                now_unix: 0,
                uid: Some(501),
            };
            Mac { d, h }
        }
        /// `rel` with `/` between parts, joined part by part (a Windows path then has one
        /// separator kind); `bin/aibd…` is the installed file, `aibd.exe` on Windows.
        pub(super) fn p(&self, rel: &str) -> PathBuf {
            let rel = match rel.strip_prefix("bin/aibd") {
                Some(rest) => format!("bin/{}{rest}", exe_name()),
                None => rel.to_string(),
            };
            rel.split('/')
                .fold(self.d.path().to_path_buf(), |p, c| p.join(c))
        }
        pub(super) fn args(&self, extra: &[&str]) -> Vec<String> {
            let mut v: Vec<String> = extra.iter().map(|s| s.to_string()).collect();
            for (k, val) in [
                ("--label", "app.ez2.aibd.unit".to_string()),
                ("--plist-dir", self.p("la").display().to_string()),
            ] {
                v.push(k.into());
                v.push(val);
            }
            v
        }
        pub(super) fn run(&self, cmd: &str, extra: &[&str], svc: &Svc) -> Out {
            let mut a = self.args(extra);
            if cmd == "install" {
                a.push("--dir".into());
                a.push(self.p("bin").display().to_string());
            }
            let a: Vec<&str> = a.iter().map(String::as_str).collect();
            run(cmd, &a, &self.h, svc)
        }
        pub(super) fn plist(&self) -> PathBuf {
            self.p("la/app.ez2.aibd.unit.plist")
        }
    }

    #[test]
    fn install_takes_a_loaded_label_out_then_bootstraps_without_kickstart() {
        // 5d33a r2 N2: the "not running" fixture, both steps present
        let m = Mac::new();
        let f = FakeLd::default();
        f.prints.borrow_mut().push_back(print_not_running());
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("aaaa"), Some("bbbb")]);
        let o = m.run("install", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert_eq!(
            f.calls(),
            vec![
                "print gui/501/app.ez2.aibd.unit".to_string(),
                "bootout gui/501/app.ez2.aibd.unit".to_string(),
                format!("bootstrap gui/501 {}", m.plist().display()),
            ]
        );
        assert!(o.stdout.contains("STARTED instance bbbb"), "{}", o.stdout);
        let text = std::fs::read_to_string(m.plist()).unwrap();
        assert!(
            text.contains("<key>ExitTimeOut</key>\n\t<integer>20</integer>"),
            "{text}"
        );
    }

    #[test]
    fn the_old_instance_answering_is_not_started() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa")]);
        let o = m.run("install", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(
            o.stderr
                .contains("did not answer health with a new instance"),
            "{o:?}"
        );
    }

    #[test]
    fn bootstrap_failing_is_refused_with_launchctl_words() {
        let m = Mac::new();
        let f = FakeLd::default();
        *f.bootstrap.borrow_mut() = Some(lo(5, "", "Bootstrap failed: 5: Input/output error\n"));
        let hl = FakeHealth::default();
        let o = m.run("install", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("rc 5: Bootstrap failed: 5"), "{o:?}");
    }

    #[test]
    fn root_is_refused_before_any_launchctl() {
        let mut m = Mac::new();
        m.h.uid = Some(0);
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        for c in ["install", "start", "stop", "uninstall"] {
            let o = m.run(c, &[], &f.svc(&hl));
            assert_eq!(o.code, RC_REFUSED, "{c} {o:?}");
            assert!(o.stderr.contains("sudo"), "{c} {o:?}");
        }
        assert!(f.calls().is_empty(), "{:?}", f.calls());
    }

    #[test]
    fn uninstall_boots_out_before_removing_and_no_such_process_is_already_out() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        assert_eq!(m.run("install", &["--no-start"], &f.svc(&hl)).code, RC_OK);
        assert!(
            f.calls().is_empty(),
            "--no-start calls nothing: {:?}",
            f.calls()
        );
        f.prints.borrow_mut().push_back(print_not_running());
        *f.bootout.borrow_mut() = Some(lo(3, "", "Boot-out failed: 3: No such process\n"));
        *f.watch.borrow_mut() = Some((m.plist(), false));
        let o = m.run("uninstall", &["--yes"], &f.svc(&hl));
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert_eq!(
            f.watch.borrow().as_ref().map(|w| w.1),
            Some(true),
            "plist was there at bootout"
        );
        assert!(o.stdout.contains("ALREADY-OUT"), "{}", o.stdout);
        assert!(!m.plist().exists());
        assert!(!m.p("bin/aibd").exists());
    }

    #[test]
    fn start_does_nothing_when_running_and_kickstarts_without_k_when_loaded_but_stopped() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[None, Some("cccc")]);
        assert_eq!(m.run("install", &["--no-start"], &f.svc(&hl)).code, RC_OK);
        f.prints.borrow_mut().push_back(print_running(29981));
        let o = m.run("start", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(
            o.stdout.contains("ALREADY-RUNNING pid 29981"),
            "{}",
            o.stdout
        );
        f.prints.borrow_mut().push_back(print_not_running());
        let o = m.run("start", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert_eq!(
            f.calls(),
            vec![
                "print gui/501/app.ez2.aibd.unit",
                "print gui/501/app.ez2.aibd.unit",
                "kickstart gui/501/app.ez2.aibd.unit"
            ]
        );
    }

    #[test]
    fn stop_gives_up_at_the_limit_while_the_lock_is_held() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        assert_eq!(m.run("install", &["--no-start"], &f.svc(&hl)).code, RC_OK);
        std::fs::create_dir_all(m.p("data/run")).unwrap();
        let lock = std::fs::File::create(m.p("data/run/aibd.lock")).unwrap();
        lock.try_lock().unwrap();
        f.prints.borrow_mut().push_back(print_running(1));
        let n = RefCell::new(0u64);
        let svc = Svc {
            ld: &f,
            health: &hl,
            pace: &|_| *n.borrow_mut() += 1,
            key: &f,
        };
        let o = m.run("stop", &[], &svc);
        assert_eq!(o.code, RC_RUNNING, "{o:?}");
        assert_eq!(
            *n.borrow(),
            (EXIT_TIMEOUT_S + STOP_GRACE_S) * 1000 / STEP_MS
        );
        // not launchd's: print says missing, the lock is still held
        let o = m.run("stop", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_RUNNING, "{o:?}");
        assert!(o.stderr.contains("not run by launchd"), "{o:?}");
    }

    #[test]
    fn start_refuses_while_an_aibd_launchd_does_not_run_holds_the_lock() {
        // 5d33a T97-2m M1: no bootstrap / kickstart, or launchd restarts an exit-3 aibd forever
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        assert_eq!(m.run("install", &["--no-start"], &f.svc(&hl)).code, RC_OK);
        std::fs::create_dir_all(m.p("data/run")).unwrap();
        let lock = std::fs::File::create(m.p("data/run/aibd.lock")).unwrap();
        lock.try_lock().unwrap();
        for p in [print_missing(), print_not_running()] {
            f.prints.borrow_mut().push_back(p);
            let o = m.run("start", &[], &f.svc(&hl));
            assert_eq!(o.code, RC_RUNNING, "{o:?}");
            assert!(o.stderr.contains("not run by launchd"), "{o:?}");
        }
        let c = f.calls();
        assert!(c.iter().all(|x| x.starts_with("print ")), "{c:?}");
    }

    #[test]
    fn install_says_aibd_was_stopped_when_a_later_step_fails() {
        // 5d33a T97-2m L1
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        f.prints.borrow_mut().push_back(print_not_running());
        // another build in the target: refused after the bootout
        std::fs::create_dir_all(m.p("bin")).unwrap();
        std::fs::write(m.p("bin/aibd"), b"another build").unwrap();
        let o = m.run("install", &[], &f.svc(&hl));
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("aibd was stopped"), "{o:?}");
        assert!(f.calls().iter().any(|c| c.starts_with("bootout ")));
    }

    #[test]
    fn status_reads_only_state_and_pid_and_unknown_does_not_change_the_code() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::default();
        assert_eq!(m.run("install", &["--no-start"], &f.svc(&hl)).code, RC_OK);
        f.prints.borrow_mut().push_back(print_running(29981));
        let o = m.run("status", &["--json"], &f.svc(&hl));
        let v: serde_json::Value = serde_json::from_str(o.stdout.trim()).unwrap();
        assert_eq!(v["launchd"], "running");
        assert_eq!(v["launchd_pid"], 29981);
        let code = o.code;
        f.prints
            .borrow_mut()
            .push_back(lo(64, "", "Usage: launchctl print <service-target>\n"));
        let o = m.run("status", &["--json"], &f.svc(&hl));
        let v: serde_json::Value = serde_json::from_str(o.stdout.trim()).unwrap();
        assert_eq!(v["launchd"], "unknown");
        assert_eq!(o.code, code);
    }

    #[test]
    fn health_is_read_from_a_plain_200_only() {
        let ok = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 40\r\n\r\n{\"status\":\"ok\",\"instance_id\":\"abab\"}";
        assert_eq!(parse_health(ok).as_deref(), Some("abab"));
        assert_eq!(
            parse_health(b"HTTP/1.1 503 x\r\n\r\n{\"instance_id\":\"abab\"}"),
            None
        );
        assert_eq!(
            parse_health(b"HTTP/1.1 200 OK\r\n\r\n{\"instance_id\":null}"),
            None
        );
    }

    /// 5d33a IFACE-T97-2m r1 point 1: the ADR 0005 exception is for the one-shot CLI only;
    /// only main.rs (which hands it to these commands) may name it.
    #[test]
    fn pace_is_named_only_by_the_cli() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut stack = vec![src.clone()];
        let mut bad = Vec::new();
        while let Some(d) = stack.pop() {
            for e in std::fs::read_dir(&d).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    stack.push(p);
                    continue;
                }
                let rel = p
                    .strip_prefix(&src)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/");
                if rel == "main.rs" || rel == "install.rs" || !rel.ends_with(".rs") {
                    continue;
                }
                if std::fs::read_to_string(&p)
                    .unwrap()
                    .contains("install::pace")
                {
                    bad.push(rel);
                }
            }
        }
        assert!(bad.is_empty(), "{bad:?}");
    }

    fn env_of(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// FINDING-reporeg-ownercheck-ebab8-1123: the switch never without the touchid pairing.
    #[test]
    fn owner_touch_needs_the_touchid_pairing_or_is_refused() {
        let sw = crate::owner_ok::SWITCH;
        // switch off (or not exactly 1): untouched
        for v in [None, Some("0"), Some("true")] {
            let mut e = env_of(&[(PAIRING_ENV, "terminal-unverified")]);
            if let Some(v) = v {
                e.insert(sw.into(), v.into());
            }
            let before = e.clone();
            assert_eq!(owner_touch_env(&mut e, true, "macos"), Ok(false), "{v:?}");
            assert_eq!(e, before);
        }
        // already touchid
        let mut e = env_of(&[(sw, "1"), (PAIRING_ENV, PAIRING_TOUCHID)]);
        assert_eq!(owner_touch_env(&mut e, true, "macos"), Ok(false));
        // kept from the old plist / missing, not named this time: raised to touchid
        for kept in [Some("terminal-unverified"), Some("off"), None] {
            let mut e = env_of(&[(sw, "1")]);
            if let Some(k) = kept {
                e.insert(PAIRING_ENV.into(), k.into());
            }
            assert_eq!(
                owner_touch_env(&mut e, false, "macos"),
                Ok(true),
                "{kept:?}"
            );
            assert_eq!(
                e[PAIRING_ENV], PAIRING_TOUCHID,
                "reason: pairing_not_raised"
            );
        }
        // named this time as something else, or unset by name: refused, nothing changed
        for named in [Some("terminal-unverified"), None] {
            let mut e = env_of(&[(sw, "1")]);
            if let Some(n) = named {
                e.insert(PAIRING_ENV.into(), n.into());
            }
            let before = e.clone();
            let r = owner_touch_env(&mut e, true, "macos");
            assert!(r.is_err(), "reason: contradiction_allowed {named:?}");
            assert!(r.unwrap_err().contains(PAIRING_TOUCHID));
            assert_eq!(e, before);
        }
        // no fingerprint pairing on this platform yet: refused, never a weaker channel
        for os in ["windows", "linux"] {
            let mut e = env_of(&[(sw, "1"), (PAIRING_ENV, PAIRING_TOUCHID)]);
            let r = owner_touch_env(&mut e, false, os);
            assert!(r.is_err(), "reason: off_mac_allowed {os}");
            assert!(r.unwrap_err().contains("Windows Hello"));
        }
    }

    fn plist_env(m: &Mac) -> BTreeMap<String, String> {
        seen(&parse_plist(&std::fs::read_to_string(m.plist()).unwrap()).unwrap()).env
    }

    #[test]
    fn install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys() {
        let sw = crate::owner_ok::SWITCH;
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[]);
        let svc = f.svc(&hl);
        let ot = format!("{sw}=1");
        // the weak pairing named next to the switch: refused before anything is written
        let o = m.run(
            "install",
            &[
                "--no-start",
                "--env",
                &ot,
                "--env",
                "AIBD_PAIRING=terminal-unverified",
            ],
            &svc,
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(!m.plist().exists(), "reason: plist_written_on_refusal");
        assert!(!m.p("bin/aibd").exists(), "reason: copied_on_refusal");
        // the incident's plist: weak pairing and a session limit
        let o = m.run(
            "install",
            &[
                "--no-start",
                "--env",
                "AIBD_PAIRING=terminal-unverified",
                "--env",
                "AIBD_MAX_SESSIONS=8",
            ],
            &svc,
        );
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(!o.stdout.contains("PAIR-KEY"), "{}", o.stdout);
        assert!(
            f.key_asked.borrow().is_empty(),
            "reason: key_for_weak_pairing"
        );
        // the switch added later: the kept weak pairing is raised, the limit stays
        let o = m.run("install", &["--no-start", "--env", &ot], &svc);
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(
            o.stdout.contains("SET-ENV AIBD_PAIRING=touchid"),
            "{}",
            o.stdout
        );
        // ebab8 11-26 第四包: the key is made here, no terminal step left
        assert!(
            o.stdout
                .contains("PAIR-KEY made (it asks for your fingerprint)"),
            "{}",
            o.stdout
        );
        assert!(!o.stdout.contains("NEXT"), "{}", o.stdout);
        assert_eq!(f.key_asked.borrow().as_slice(), &[m.p("data")]);
        let e = plist_env(&m);
        assert_eq!(e.get(sw).map(String::as_str), Some("1"));
        assert_eq!(e[PAIRING_ENV], PAIRING_TOUCHID, "reason: weak_pairing_kept");
        assert_eq!(e["AIBD_MAX_SESSIONS"], "8");
        // a plain reinstall keeps all three, says nothing new
        let o = m.run("install", &["--no-start"], &svc);
        assert_eq!(o.code, RC_OK, "{o:?}");
        for k in [sw, PAIRING_ENV, "AIBD_MAX_SESSIONS"] {
            assert!(
                o.stdout.contains(&format!("KEPT-ENV {k}")),
                "{k}: {}",
                o.stdout
            );
        }
        assert!(!o.stdout.contains("SET-ENV"), "{}", o.stdout);
        assert_eq!(plist_env(&m), e, "reason: reinstall_dropped_keys");
        // unsetting the pairing by name while the switch stays: refused, plist as it was
        let o = m.run("install", &["--no-start", "--unset-env", PAIRING_ENV], &svc);
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert_eq!(plist_env(&m), e);
        // a key there that passes the check: said so, nothing else
        *f.key_answer.borrow_mut() = Some(Ok(crate::touch::PairKeyMade::Checked));
        let o = m.run("install", &["--no-start"], &svc);
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(o.stdout.contains("PAIR-KEY checked"), "{}", o.stdout);
        assert!(!o.stdout.contains("NEXT"), "{}", o.stdout);
        assert!(f.calls().is_empty(), "{:?}", f.calls());
    }

    /// ebab8 11-26 第四包 / 3e53b PLAN-mac-firstrun r1 b: a pairing key that cannot be made now
    /// (Touch ID not usable) or one there that fails aibd's start-up check stops the install
    /// before anything is copied or written, and says why.
    #[test]
    fn a_pairing_key_that_fails_installs_nothing() {
        let sw = crate::owner_ok::SWITCH;
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[]);
        let svc = f.svc(&hl);
        *f.key_answer.borrow_mut() = Some(Err(
            "pair_key_weak: the pairing key in x did not pass".into()
        ));
        let ot = format!("{sw}=1");
        let o = m.run("install", &["--no-start", "--env", &ot], &svc);
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("pairing key: pair_key_weak"), "{o:?}");
        assert!(!o.stdout.contains("PAIR-KEY"), "{o:?}");
        assert!(!m.plist().exists(), "reason: plist_written_on_key_failure");
        assert!(!m.p("bin/aibd").exists(), "reason: copied_on_key_failure");
        assert_eq!(f.key_asked.borrow().len(), 1);
        assert!(f.calls().is_empty(), "{:?}", f.calls());
        // --no-pair-key leaves the key alone (no key made, the pairing stays unavailable)
        let o = m.run(
            "install",
            &["--no-start", "--no-pair-key", "--env", &ot],
            &svc,
        );
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(
            o.stdout.contains("PAIR-KEY skipped (--no-pair-key)"),
            "{o:?}"
        );
        assert_eq!(
            f.key_asked.borrow().len(),
            1,
            "reason: key_step_run_with_no_pair_key"
        );
    }

    #[test]
    fn a_refused_pairing_stops_nothing_that_runs() {
        // without --no-start the check still comes before launchd is touched
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[]);
        let ot = format!("{}=1", crate::owner_ok::SWITCH);
        let o = m.run(
            "install",
            &["--env", &ot, "--env", "AIBD_PAIRING=terminal-unverified"],
            &f.svc(&hl),
        );
        assert_ne!(o.code, RC_OK, "{o:?}");
        assert!(
            f.calls().is_empty(),
            "reason: stopped_before_refusal {:?}",
            f.calls()
        );
        assert!(!o.stderr.contains("aibd was stopped"), "{}", o.stderr);
    }

    #[test]
    fn adopt_reports_a_switch_without_touchid() {
        let sw = crate::owner_ok::SWITCH;
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[]);
        let pl = PlistSpec {
            label: "app.ez2.aibd.unit".into(),
            exe: m.p("src/aibd").display().to_string(),
            log: m.p("log").display().to_string(),
            env: env_of(&[
                (sw, "1"),
                (PAIRING_ENV, "terminal-unverified"),
                ("AIBD_DATA_DIR", &m.p("data").display().to_string()),
            ]),
        };
        std::fs::create_dir_all(m.plist().parent().unwrap()).unwrap();
        std::fs::write(m.plist(), render_plist(&pl)).unwrap();
        let before = std::fs::read(m.plist()).unwrap();
        // --adopt takes no --dir, which Mac::run adds for install
        let a = m.args(&["--no-start", "--adopt"]);
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        let o = run("install", &a, &m.h, &f.svc(&hl));
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(o.stdout.contains("WARN-ENV"), "{}", o.stdout);
        assert_eq!(std::fs::read(m.plist()).unwrap(), before);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn the_real_domain_is_gui_and_the_user_id() {
        let d = crate::platform::macos_launchd::Real.domain();
        assert_eq!(d, format!("gui/{}", nix::unistd::getuid().as_raw()));
    }
}
