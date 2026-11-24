//! The AIB plugin area (Andy 11-23: one fixed place for plugins; a plugin is a program of its
//! own, on by default, and can be turned off; later a marketplace lists them).
//!
//! ```text
//! <AIB home>/plugins/<name>/aib-plugin.json     what the plugin is
//! <AIB home>/plugins/<name>/bin/<command>       its executable (`.exe` on Windows)
//! <AIB home>/state/aib-plugin/switches.json     {"<name>": "on" | "off"}: what the user set
//! ```
//!
//! A plugin is **run** only when all of this holds, and each "no" has its own word:
//! - its `aib-plugin.json` is read whole: `schema` is `aib-plugin/v1`, `name` equals the folder's
//!   name, the fields are of their kinds ([`parse`]);
//! - its switch is on: what the user set, else the file's `default` ([`switched_on`]);
//! - it is free (`paid: false`). A paid plugin is not run by this version: the licence is not
//!   read here yet, and "not known" is never "allowed";
//! - the command is one the file names, its executable is inside the plugin's folder, and the
//!   file's `sha256` has a sum for it that the executable on disk matches ([`runnable`]). A
//!   plugin installed without sums is listed and not run: the file on disk must be the file
//!   that was installed.
//!
//! What the sum is for, said plainly: it catches a file that was replaced by accident, left
//! over from another version, or cut short. It is not a wall against a program running as the
//! same user, which could as well replace this tool; and between the sum being checked and the
//! file being started there is a moment in which such a program could swap it. An executable
//! that a link leads out of the plugin's folder is refused whatever its sum.
//!
//! This crate reads and decides; the binary lists, switches and runs. Nothing of a plugin is
//! loaded into AIB: a plugin that breaks cannot take AIB down, and the same holds on macOS,
//! Windows and Linux.

use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub mod marketplace;

pub const SCHEMA: &str = "aib-plugin/v1";

/// JSON read with every object's keys required to be different: `{"demo": "off", "demo": "on"}`
/// is an error, not "on". A plain read keeps the last of two and says nothing, which is how a
/// switch or `paid` could quietly mean the opposite of what the file's first half says.
struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Strict;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON")
            }
            fn visit_bool<E>(self, v: bool) -> Result<Strict, E> {
                Ok(Strict(Value::from(v)))
            }
            fn visit_i64<E>(self, v: i64) -> Result<Strict, E> {
                Ok(Strict(Value::from(v)))
            }
            fn visit_u64<E>(self, v: u64) -> Result<Strict, E> {
                Ok(Strict(Value::from(v)))
            }
            fn visit_f64<E>(self, v: f64) -> Result<Strict, E> {
                Ok(Strict(Value::from(v)))
            }
            fn visit_str<E>(self, v: &str) -> Result<Strict, E> {
                Ok(Strict(Value::from(v)))
            }
            fn visit_unit<E>(self) -> Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
                let mut v = Vec::new();
                while let Some(Strict(x)) = a.next_element()? {
                    v.push(x);
                }
                Ok(Strict(Value::Array(v)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Strict, A::Error> {
                let mut m = Map::new();
                while let Some(k) = a.next_key::<String>()? {
                    let Strict(v) = a.next_value()?;
                    if m.insert(k.clone(), v).is_some() {
                        let twice = format!("the key {k:?} comes twice");
                        return Err(serde::de::Error::custom(twice));
                    }
                }
                Ok(Strict(Value::Object(m)))
            }
        }
        d.deserialize_any(V)
    }
}

/// JSON text as a value, an object's key coming twice being an error.
pub fn strict_json(text: &str) -> Result<Value, String> {
    serde_json::from_str::<Strict>(text)
        .map(|s| s.0)
        .map_err(|e| format!("not JSON: {e}"))
}

/// `<AIB home>`: `EZ2_AIB_HOME` when it says something, else `.ez2-aib` under the home folder.
pub fn home(env_home: Option<&str>, user_home: Option<&Path>) -> Option<PathBuf> {
    match env_home.map(str::trim).filter(|h| !h.is_empty()) {
        Some(h) => Some(PathBuf::from(h)),
        None => user_home.map(|u| u.join(".ez2-aib")),
    }
}

pub fn plugins_dir(home: &Path) -> PathBuf {
    home.join("plugins")
}

pub fn switches_file(home: &Path) -> PathBuf {
    home.join("state").join("aib-plugin").join("switches.json")
}

/// A plugin's or a command's name: `a-z 0-9 -`, not starting or ending with `-`, 1 to 64.
pub fn name_ok(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && !s.starts_with('-')
        && !s.ends_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// A path inside the plugin's folder: `/` between plain parts, no `.` / `..`, not absolute.
pub fn inside(rel: &str) -> bool {
    !rel.is_empty()
        && !rel.contains('\\')
        && rel
            .split('/')
            .all(|p| !p.is_empty() && p != "." && p != ".." && !p.contains(':'))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub name: String,
    /// Its executable, as a path inside the plugin's folder (without `.exe`).
    pub bin: String,
}

/// One plugin's file, read whole.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub name: String,
    pub version: String,
    pub summary: String,
    pub paid: bool,
    /// The switch when the user has not set it.
    pub default_on: bool,
    pub commands: Vec<Command>,
    /// `bin` path ⇒ the sum of the executable that was installed.
    pub sha256: BTreeMap<String, String>,
}

fn text(o: &Map<String, Value>, f: &str) -> Result<String, String> {
    o.get(f)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("no \"{f}\""))
}

fn hex64(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Reads `aib-plugin.json`. `folder` is the name of the folder it was found in: a file that
/// says another name is not that plugin. A field the reader does not know is left alone, so
/// a newer plugin still lists in an older AIB; a field it knows and cannot read is an error.
pub fn parse(text_json: &str, folder: &str) -> Result<Manifest, String> {
    let v = strict_json(text_json)?;
    let o = v.as_object().ok_or("not an object")?;
    match o.get("schema").and_then(Value::as_str) {
        Some(SCHEMA) => {}
        other => return Err(format!("schema {other:?} is not {SCHEMA:?}")),
    }
    let name = text(o, "name")?;
    if !name_ok(&name) {
        return Err(format!("name {name:?} is not a-z 0-9 -"));
    }
    if name != folder {
        return Err(format!("name {name:?} is not its folder's name {folder:?}"));
    }
    let paid = o
        .get("paid")
        .and_then(Value::as_bool)
        .ok_or("\"paid\" is not true or false")?;
    let default_on = match o.get("default").and_then(Value::as_str) {
        Some("on") => true,
        Some("off") => false,
        other => return Err(format!("\"default\" {other:?} is not \"on\" or \"off\"")),
    };
    let list = o
        .get("commands")
        .and_then(Value::as_array)
        .filter(|a| !a.is_empty())
        .ok_or("no \"commands\"")?;
    let mut commands: Vec<Command> = Vec::new();
    for c in list {
        let c = c.as_object().ok_or("a command is not an object")?;
        let (cname, bin) = (text(c, "name")?, text(c, "bin")?);
        if !name_ok(&cname) {
            return Err(format!("command name {cname:?} is not a-z 0-9 -"));
        }
        if !inside(&bin) {
            return Err(format!(
                "command {cname}: bin {bin:?} is not a path inside the plugin's folder"
            ));
        }
        if commands.iter().any(|x| x.name == cname) {
            return Err(format!("command {cname} comes twice"));
        }
        commands.push(Command { name: cname, bin });
    }
    let mut sha256 = BTreeMap::new();
    if let Some(s) = o.get("sha256") {
        let m = s.as_object().ok_or("\"sha256\" is not an object")?;
        for (k, v) in m {
            let sum = v.as_str().unwrap_or("");
            if !inside(k) || !hex64(sum) {
                return Err(format!(
                    "sha256 of {k:?} is not 64 lower-case hex digits for a path inside the folder"
                ));
            }
            sha256.insert(k.clone(), sum.to_string());
        }
    }
    Ok(Manifest {
        name,
        version: text(o, "version")?,
        summary: text(o, "summary")?,
        paid,
        default_on,
        commands,
        sha256,
    })
}

/// What the user set: plugin name ⇒ on. A file that is not there is no setting; a file that is
/// there and is not `{"<name>": "on" | "off"}` is an error (a switch that cannot be read is not
/// guessed: the caller treats every plugin as off).
pub fn parse_switches(text_json: &str) -> Result<BTreeMap<String, bool>, String> {
    let v = strict_json(text_json)?;
    let o = v.as_object().ok_or("not an object")?;
    let mut out = BTreeMap::new();
    for (k, v) in o {
        let on = match v.as_str() {
            Some("on") => true,
            Some("off") => false,
            _ => return Err(format!("{k:?}: not \"on\" or \"off\"")),
        };
        if !name_ok(k) {
            return Err(format!("{k:?} is not a plugin name"));
        }
        out.insert(k.clone(), on);
    }
    Ok(out)
}

pub fn switches_text(s: &BTreeMap<String, bool>) -> String {
    let m: Map<String, Value> = s
        .iter()
        .map(|(k, on)| (k.clone(), Value::from(if *on { "on" } else { "off" })))
        .collect();
    format!("{}\n", Value::Object(m))
}

/// Is the plugin switched on: what the user set, else the plugin's own default.
pub fn switched_on(m: &Manifest, set: &BTreeMap<String, bool>) -> bool {
    set.get(&m.name).copied().unwrap_or(m.default_on)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// Why a command of a plugin is not run. Each is a fixed word for the list and the exit line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum No {
    /// The switches file is there and cannot be read: nothing is taken as on.
    SwitchesUnread(String),
    Off,
    /// A paid plugin: the licence is not read by this version.
    PaidNotChecked,
    NoSuchCommand(String),
    /// The file names no sum for this executable.
    NoSum(String),
    Missing(PathBuf),
    /// The executable is not inside the plugin's folder once links are followed.
    Outside(PathBuf),
    /// What stands at the executable's path is not a plain file (a folder, a pipe, a device).
    NotAFile(PathBuf),
    /// The executable on disk is not the one that was installed.
    SumDiffers {
        bin: String,
        want: String,
        got: String,
    },
}

impl No {
    pub fn word(&self) -> &'static str {
        match self {
            No::SwitchesUnread(_) => "switches_unread",
            No::Off => "off",
            No::PaidNotChecked => "paid_not_checked",
            No::NoSuchCommand(_) => "no_such_command",
            No::NoSum(_) => "no_sum",
            No::Missing(_) => "missing",
            No::Outside(_) => "outside",
            No::NotAFile(_) => "not_a_file",
            No::SumDiffers { .. } => "sum_differs",
        }
    }
    pub fn text(&self) -> String {
        match self {
            No::SwitchesUnread(e) => {
                format!("the switches file cannot be read ({e}): no plugin is taken as on")
            }
            No::Off => "it is switched off".into(),
            No::PaidNotChecked => {
                "it is a paid plugin and this version does not read the licence".into()
            }
            No::NoSuchCommand(c) => format!("it has no command {c}"),
            No::NoSum(b) => {
                format!("aib-plugin.json has no sha256 for {b}: install it with its sums")
            }
            No::Missing(p) => format!("{} is not there", p.display()),
            No::NotAFile(p) => format!("{} is not a plain file", p.display()),
            No::Outside(p) => format!(
                "{} is not inside the plugin's folder (a link leads out of it)",
                p.display()
            ),
            No::SumDiffers { bin, want, got } => format!(
                "{bin} is not the file that was installed (sha256 {} wanted, {} found)",
                &want[..12],
                &got[..12]
            ),
        }
    }
}

/// The executable of `bin` on this system: `.exe` is added on Windows (added, not put in
/// place of an ending the name has: `bin/tool.v1` is `tool.v1.exe`).
pub fn exe_path(folder: &Path, bin: &str, windows: bool) -> PathBuf {
    let mut p = folder.to_path_buf();
    for part in bin.split('/') {
        p.push(part);
    }
    if windows {
        let mut name = p.file_name().map(|n| n.to_os_string()).unwrap_or_default();
        name.push(".exe");
        p.set_file_name(name);
    }
    p
}

/// May `command` of this plugin be run? Ok = the executable to run. The checks are asked in
/// the order of the module text; the executable is read and summed last.
pub fn runnable(
    m: &Manifest,
    folder: &Path,
    switches: &Result<BTreeMap<String, bool>, String>,
    command: &str,
    windows: bool,
) -> Result<PathBuf, No> {
    let set = switches
        .as_ref()
        .map_err(|e| No::SwitchesUnread(e.clone()))?;
    if !switched_on(m, set) {
        return Err(No::Off);
    }
    if m.paid {
        return Err(No::PaidNotChecked);
    }
    let c = m
        .commands
        .iter()
        .find(|c| c.name == command)
        .ok_or_else(|| No::NoSuchCommand(command.to_string()))?;
    let want = m
        .sha256
        .get(&c.bin)
        .ok_or_else(|| No::NoSum(c.bin.clone()))?;
    let exe = exe_path(folder, &c.bin, windows);
    // with every link followed, the file is still inside the plugin's folder
    let (Ok(real), Ok(root)) = (exe.canonicalize(), folder.canonicalize()) else {
        return Err(No::Missing(exe));
    };
    if !real.starts_with(&root) {
        return Err(No::Outside(real));
    }
    // a plain file only: reading a pipe or a device could wait for ever
    if !real.metadata().is_ok_and(|m| m.is_file()) {
        return Err(No::NotAFile(real));
    }
    let bytes = std::fs::read(&real).map_err(|_| No::Missing(exe.clone()))?;
    let got = sha256_hex(&bytes);
    if &got != want {
        return Err(No::SumDiffers {
            bin: c.bin.clone(),
            want: want.clone(),
            got,
        });
    }
    Ok(real)
}

/// One folder under the plugins folder, as found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    pub folder: String,
    /// The plugin's file read whole, or why not.
    pub manifest: Result<Manifest, String>,
}

/// Every folder under `dir`, in name order. A folder without a readable `aib-plugin.json` is
/// listed with the reason, never left out: a plugin that silently disappears from the list
/// looks like one that was never installed. No plugins folder: no plugins.
pub fn find(dir: &Path) -> Result<Vec<Found>, String> {
    let rd = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    };
    let mut out = Vec::new();
    for e in rd {
        let e = e.map_err(|e| format!("{}: {e}", dir.display()))?;
        if !e.path().is_dir() {
            continue;
        }
        let folder = e.file_name().to_string_lossy().into_owned();
        if folder.starts_with('.') {
            continue;
        }
        let manifest = std::fs::read_to_string(e.path().join("aib-plugin.json"))
            .map_err(|e| match e.kind() {
                // a folder of another kind (an older add-on has no such file)
                std::io::ErrorKind::NotFound => {
                    "no aib-plugin.json: not a plugin of this plugin area".to_string()
                }
                _ => format!("aib-plugin.json: {e}"),
            })
            .and_then(|t| parse(&t, &folder));
        out.push(Found { folder, manifest });
    }
    out.sort_by(|a, b| a.folder.cmp(&b.folder));
    Ok(out)
}

/// Sets one plugin's switch in `file`, as one step that another `on` / `off` cannot fall into
/// the middle of: a lock is taken (waited for up to two seconds), the switches are read
/// again under it, and the new file is written beside the old one and renamed over it, so a
/// crash leaves the old file or the new one, never half of one. A switches file that is there
/// and cannot be read is not written over.
pub fn set_switch(file: &Path, name: &str, on: bool) -> Result<(), String> {
    let dir = file.parent().ok_or("the switches file has no folder")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    // the ledger's lock (flock on Unix, LockFileEx on Windows), let go when `_held` goes
    let lock = dir.join("switches.lock");
    let _held = aib_ledger::take_lock(&lock, std::time::Duration::from_secs(2))
        .map_err(|e| format!("{e}; nothing is changed"))?;
    let done = (|| {
        let mut set = read_switches(file)?;
        set.insert(name.to_string(), on);
        let tmp = dir.join(format!("switches.json.{}.tmp", std::process::id()));
        std::fs::write(&tmp, switches_text(&set)).map_err(|e| format!("{}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, file).map_err(|e| format!("{}: {e}", file.display()))
    })();
    done
}

/// The names in the user's switches that are no installed plugin (a typo in a hand-edited
/// file: `{"dmeo": "off"}` turns nothing off, and should not look as if it did).
pub fn stray_switches(set: &BTreeMap<String, bool>, found: &[Found]) -> Vec<String> {
    set.keys()
        .filter(|k| {
            !found
                .iter()
                .any(|f| f.manifest.as_ref().is_ok_and(|m| &m.name == *k))
        })
        .cloned()
        .collect()
}

/// The user's switches from `file`: missing = none set.
pub fn read_switches(file: &Path) -> Result<BTreeMap<String, bool>, String> {
    match std::fs::read_to_string(file) {
        Ok(t) => parse_switches(&t).map_err(|e| format!("{}: {e}", file.display())),
        // nothing at the path at all: none set. A link whose target is gone is a setting
        // that cannot be read, not the absence of one
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && file.symlink_metadata().is_err() => {
            Ok(BTreeMap::new())
        }
        Err(e) => Err(format!("{}: {e}", file.display())),
    }
}
