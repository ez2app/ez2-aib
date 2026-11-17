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

pub const USAGE: &str = "usage: aibd status [--json] [--fingerprint] [--label L] [--plist-dir D]\n       aibd install --no-start [--dir D] [--label L] [--plist-dir D] [--env K=V]... [--unset-env K]... [--adopt] [--json]\n       aibd uninstall [--yes] [--label L] [--plist-dir D] [--json]";

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
        })
    }
}

#[derive(Debug, Default, Clone)]
pub struct Opts {
    pub json: bool,
    pub yes: bool,
    pub no_start: bool,
    pub adopt: bool,
    pub fingerprint: bool,
    pub dir: Option<PathBuf>,
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
            "--adopt",
            "--dir",
            "--label",
            "--plist-dir",
            "--env",
            "--unset-env",
        ],
        "uninstall" => &["--json", "--yes", "--label", "--plist-dir"],
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
            "--adopt" => o.adopt = true,
            "--fingerprint" => o.fingerprint = true,
            "--dir" => o.dir = Some(PathBuf::from(value()?)),
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

fn unsupported(h: &Host, cmd: &str) -> Option<Out> {
    (h.os != "macos").then(|| {
        Out::fail(
            RC_UNSUPPORTED,
            format!("aibd {cmd}: unsupported: T97 Windows half (HKCU Run) not in this slice"),
        )
    })
}

// ---------------------------------------------------------------- commands

pub fn run(cmd: &str, args: &[&str], h: &Host) -> Out {
    let o = match parse(cmd, args) {
        Ok(o) => o,
        Err(e) => return Out::fail(RC_USAGE, format!("aibd {e}\n{USAGE}")),
    };
    if let Some(u) = unsupported(h, cmd) {
        return u;
    }
    let r = match cmd {
        "status" => status(h, &o),
        "install" => install(h, &o),
        _ => uninstall(h, &o),
    };
    r.unwrap_or_else(|e| Out::fail(RC_REFUSED, format!("aibd {cmd}: {e}")))
}

fn status(h: &Host, o: &Opts) -> Result<Out, String> {
    let pl = places(h, o)?;
    let plist = read_plist(&pl.plist).ok().flatten();
    let (rec, data, from) = find_record(h, plist.as_ref())?;
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
    let mut mismatch: Vec<String> = Vec::new();
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
    let bytes: u64 = data
        .as_deref()
        .and_then(|d| fingerprint(d).ok())
        .map(|v| v.iter().map(|p| p.size).sum())
        .unwrap_or(0);
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
                "running": run,
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

fn install(h: &Host, o: &Opts) -> Result<Out, String> {
    if !o.no_start {
        return Ok(Out::fail(
            RC_USAGE,
            "aibd install: pass --no-start (registering and starting is T97-2)",
        ));
    }
    let pl = places(h, o)?;
    let existing = read_plist(&pl.plist)?;
    let (old_rec, _, _) = find_record(h, existing.as_ref())?;
    let mut out = Out::default();

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

    // environment: the old plist's keys stay unless unset (5d33a L2); --env wins
    let mut env: BTreeMap<String, String> = BTreeMap::new();
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
            env.insert(k.clone(), v.clone());
        }
    }
    for (k, v) in &o.env {
        env.insert(k.clone(), v.clone());
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
        if !same_path(&r.plist, &pl.plist.display().to_string()) && Path::new(&r.plist).exists() {
            std::fs::remove_file(&r.plist).map_err(|e| format!("remove old {}: {e}", r.plist))?;
            out.say(format!("REMOVED-OLD {}", r.plist));
        }
    }
    out.say("NOT-STARTED (T97-2 registers and starts aibd)");
    Ok(out)
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

fn uninstall(h: &Host, o: &Opts) -> Result<Out, String> {
    let pl = places(h, o)?;
    let plist = read_plist(&pl.plist).ok().flatten();
    let (rec, data, _) = find_record(h, plist.as_ref())?;
    let mut out = Out::default();
    let (Some(rec), Some(data)) = (rec, data) else {
        out.say("NOT-INSTALLED (no install record; nothing removed)");
        return Ok(out);
    };
    if running(&data) {
        return Ok(Out::fail(
            RC_RUNNING,
            "aibd uninstall: aibd is running; stop it first (T97-2 adds the stop)",
        ));
    }
    let files = fingerprint(&data).map(|v| v.len()).unwrap_or(0);
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
        };
        for c in ["status", "install", "uninstall"] {
            let args: &[&str] = if c == "install" { &["--no-start"] } else { &[] };
            assert_eq!(run(c, args, &h).code, RC_UNSUPPORTED, "{c}");
        }
    }
}
