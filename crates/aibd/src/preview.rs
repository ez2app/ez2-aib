//! 試用預覽版 (SPEC-workspace-multiseat §20; Andy 11-18 20:3x 「把這個 可以同意 測試預覽版 的
//! 功能也做出一個同意框 … 之後才可以啟用在非正式區」; DESIGN-preview-a9136-1118 rev 2, 5d33a
//! CHECK-preview r1): aibd starts and stops preview builds of itself, each in its own folder,
//! port and HOME, after the person agreed on the built-in page to that exact file.
//!
//! §20b (Andy 11-18 23:2x 「預覽版 可能會有非常多種的開發,所以不能寫死」; DECISION-andy-519d0-1118
//! -2320 補段): there is no one preview. `<data>/previews.json` is the list (預覽清單): each one
//! a name, what it is for (card), who added it, its head, its station (能看 / 已定版 / 能用中 /
//! 已進正式), what in it can be seen but not used, and whether it was taken down. Adding one is
//! `preview_add`; `preview_retire` takes one down (its folder stays); `preview_fix` writes the
//! person's words that fixed it (定版). Start / stop / the agreement name one preview by id.
//! 能看 stage: with an empty list the page is shown two sample rows (`sample: true`, no build).
//!
//! - Each preview's build is `<data>/previews/<id>/aibd` (`aibd.exe`) with a one-line `VERSION`
//!   next to it, put there by the quick-fix line. The agreement is bound to the file's sha256: the page
//!   sends back the hash it showed, a changed file is `409 preview_changed`. The file is copied
//!   to `<data>/preview/run/` and the copy hashed again before it runs; that guards against a
//!   half-written file, not against a same-account program (5d33a L1: that one can run anything).
//! - Only the built-in page a person paired, not a carried pairing or a user folder page
//!   (`workspace::gives_words`), may start it; any page may stop it.
//! - It runs with `AIBD_DATA_DIR=<data>/previews/<id>/data`, `HOME=<data>/previews/<id>/home` (5d33a M1:
//!   what aibd and its seats find from HOME stays there too), `AIBD_BIND=127.0.0.1:0`,
//!   `AIBD_PREVIEW=1`, the session policy copied in, nothing else of this aibd's (no inherited
//!   descriptors either, 5d33a L2: std opens everything close-on-exec). It does not read this
//!   aibd's seats, threads, tree or pairing.
//! - Every start writes `preview.consented` (which credential, version, sha256), then
//!   `preview.started`; `preview.stopped` / `preview.exited` when it ends; each with the id.
//!   Several may run at once, each on its own port.
use crate::api::AppState;
use crate::guard::Caller;
use crate::pairing::PageAccess;
use aib_store::NewEvent;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

/// The page's acts.
pub const START: &str = "preview_start";
pub const STOP: &str = "preview_stop";
pub const ADD: &str = "preview_add";
pub const RETIRE: &str = "preview_retire";
pub const FIX: &str = "preview_fix";
/// §23b: the preview's page says someone clicked / typed / scrolled (background reads do not).
pub const ACTIVE: &str = "preview_active";
/// §23 (043dd 7a750ca6): 上預覽 on an item, and 更新 inside a preview; 能看: answered only.
pub const UP: &str = "preview_up";
pub const UPDATE: &str = "preview_update";
/// Andy 11-19 08:5x: 打開 on a running preview from the paired built-in page; it opens paired.
pub const OPEN: &str = "preview_open";
/// §23b defaults (Andy 23:4x, adjustable in the list's `update`): no one has used the page
/// this long ⇒ it may update by itself; a 「有新版」 not pressed this long ⇒ it shows louder.
pub const IDLE_SECS: u64 = 180;
pub const NAG_SECS: u64 = 1800;
/// Set on the preview build's own aibd: `/api/v1/health` and `/api/v1/version` say
/// `preview: true` (the page shows 「預覽 · 非正式區」), and it starts no preview of its own.
pub const ENV_PREVIEW: &str = "AIBD_PREVIEW";
/// The list's name of the preview a preview build is (043dd page: 頂上標示帶預覽名稱).
pub const ENV_PREVIEW_NAME: &str = "AIBD_PREVIEW_NAME";
/// The list's id of the preview a preview build is (043dd: its page sends `preview_active`).
pub const ENV_PREVIEW_ID: &str = "AIBD_PREVIEW_ID";
/// The sha256 of the build a preview build was started from (its 「有新版」 is counted from it).
pub const ENV_PREVIEW_SHA: &str = "AIBD_PREVIEW_SHA256";
/// `<data>/previews/<id>/<VERSIONS>`: one line per build put there (§23 版本紀錄, N-pv-up P1).
pub const VERSIONS: &str = "versions.jsonl";
/// At most this many lines of the record are read (the newest).
pub const VERSIONS_READ: usize = 1000;
/// `<data>/previews/<id>/<SWAPS>`: one line per time a build was put in front of the viewer
/// (§23d, Andy 11-19 00:3x 「這次 自動換 是換什麼?誰換?」).
pub const SWAPS: &str = "swaps.jsonl";
/// How many swaps health / GET previews give the 載入彈窗.
pub const SWAPS_SHOWN: usize = 20;
/// `<data>/<DIR>/<id>/`: one preview's build, its VERSION, its copies, its data and HOME.
pub const DIR: &str = "previews";
/// `<data>/<REGISTRY>`: the list.
pub const REGISTRY: &str = "previews.json";
/// Where a preview is (DECISION-andy-519d0-1118-2320 §三個階段).
pub const STATIONS: [&str; 4] = ["能看", "已定版", "能用中", "已進正式"];
/// The longest name / note kept.
pub const TEXT_MAX: usize = 200;
pub const VERSION_FILE: &str = "VERSION";
/// The longest VERSION line kept.
pub const VERSION_MAX: usize = 200;
/// A preview build larger than this is not read.
pub const BIN_MAX_BYTES: u64 = 512 * 1024 * 1024;
/// The fields a start may carry.
pub const START_FIELDS: [&str; 7] = [
    "type",
    "id",
    "preview_id",
    "version",
    "sha256",
    "consent",
    "idempotency_key",
];

/// One support point in a version: which node, at which head.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct NodeHead {
    pub id: String,
    pub head: String,
}

/// One change a build brings (§23d): a sentence, its author seat, its card.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Change {
    pub text: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub card: String,
}

/// VERSION lines 4 on: `一句 | 作者席 | 卡號` (the author and card may be left out).
fn changes_of(text: &str) -> Vec<Change> {
    text.lines()
        .skip(3)
        .take(50)
        .map(|l| {
            let mut p = l.split('|').map(|x| {
                x.trim()
                    .chars()
                    .filter(|c| !c.is_control())
                    .take(VERSION_MAX)
                    .collect::<String>()
            });
            Change {
                text: p.next().unwrap_or_default(),
                author: p.next().unwrap_or_default(),
                card: p.next().unwrap_or_default(),
            }
        })
        .filter(|c| !c.text.is_empty())
        .collect()
}

/// One time a build was put in front of the viewer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Swap {
    pub at: String,
    /// Who: `auto` (a fix swapped in by itself) or the page / seat that asked.
    pub by: String,
    /// `auto` / `manual` / `start`.
    pub how: String,
    #[serde(default)]
    pub from: Option<String>,
    pub to: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub changes: Vec<Change>,
}

/// The newest `n` swaps of `dir`, newest first.
pub fn read_swaps(dir: &Path, n: usize) -> Vec<Swap> {
    let text = std::fs::read_to_string(dir.join(SWAPS)).unwrap_or_default();
    let mut out: Vec<Swap> = text
        .lines()
        .rev()
        .filter_map(|l| serde_json::from_str::<Swap>(l).ok())
        .take(n)
        .collect();
    out.truncate(n);
    out
}

fn append_swap(dir: &Path, s: &Swap) {
    use std::io::Write;
    let Ok(line) = serde_json::to_string(s) else {
        return;
    };
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(SWAPS))
    {
        let _ = writeln!(f, "{line}");
    }
}

fn swap_of(found: &Found, by: &str, how: &str, from: Option<String>) -> Swap {
    Swap {
        at: crate::sessions::now_rfc3339(),
        by: by.into(),
        how: how.into(),
        from,
        to: found.version.clone(),
        summary: found.summary.clone(),
        changes: found.changes.clone(),
    }
}

/// One line of a preview's version record.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Version {
    pub n: u64,
    /// The merged head the build came from.
    #[serde(default)]
    pub head: String,
    #[serde(default)]
    pub nodes: Vec<NodeHead>,
    /// The build's VERSION line and hash (`ready` only).
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub sha256: String,
    pub at: String,
    /// `ready`, or why not: `conflict` / `build_failed` (§23 4.).
    pub state: String,
    #[serde(default)]
    pub summary: String,
    /// `fix` / `feature` (see [`Found::kind`]).
    #[serde(default)]
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// The newest `VERSIONS_READ` lines of `dir`'s record; lines that do not read are skipped.
pub fn read_versions(dir: &Path) -> Vec<Version> {
    let text = std::fs::read_to_string(dir.join(VERSIONS)).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    let from = lines.len().saturating_sub(VERSIONS_READ);
    lines[from..]
        .iter()
        .filter_map(|l| serde_json::from_str::<Version>(l).ok())
        .collect()
}

/// Add `v` to `dir`'s record as the next number; the number given back.
pub fn append_version(dir: &Path, mut v: Version) -> std::io::Result<u64> {
    use std::io::Write;
    v.n = read_versions(dir).last().map_or(1, |l| l.n + 1);
    let line = serde_json::to_string(&v)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(VERSIONS))?;
    writeln!(f, "{line}")?;
    Ok(v.n)
}

/// How far the build `current` (its sha256) is behind the record (§23b).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Behind {
    /// Ready versions after the current one.
    pub behind: usize,
    /// The newest ready version, when it is not the current one.
    pub latest: Option<Version>,
    /// When the first of them was put there (the 「有新版」 starts then).
    pub since: Option<String>,
}

/// `current` = the running build's hash. A build the record does not know (put there by hand)
/// is behind every ready version unless it is the newest one's.
pub fn behind(versions: &[Version], current: Option<&str>) -> Behind {
    let Some(cur) = current else {
        return Behind::default();
    };
    let ready: Vec<&Version> = versions.iter().filter(|v| v.state == "ready").collect();
    let after: Vec<&Version> = match ready.iter().rposition(|v| v.sha256 == cur) {
        Some(i) => ready[i + 1..].to_vec(),
        None if ready.last().is_some_and(|l| l.sha256 != cur) => ready.clone(),
        None => Vec::new(),
    };
    Behind {
        behind: after.len(),
        latest: after.last().map(|v| (*v).clone()),
        since: after.first().map(|v| v.at.clone()),
    }
}

/// In a preview build: `{version, behind, nag_secs, since}` when its list has a newer ready
/// build than the one it runs (043dd a9405ae7 頂上提示條); `None` otherwise. Blocking.
pub fn preview_update() -> Option<Value> {
    if !is_preview() {
        return None;
    }
    let cur = std::env::var(ENV_PREVIEW_SHA).ok()?;
    // its data folder is <data>/previews/<id>/data: the record is one up, the list three up
    let in_data = crate::workspace::data_file(VERSIONS)?;
    let dir = in_data.parent()?.parent()?.to_path_buf();
    let b = behind(&read_versions(&dir), Some(&cur));
    let latest = b.latest?;
    let rule = dir
        .parent()
        .and_then(Path::parent)
        .map(|d| update_rule(&d.join(REGISTRY)))
        .unwrap_or_default();
    Some(
        json!({"version": latest.version, "behind": b.behind, "nag_secs": rule.nag_secs,
        "since": b.since, "summary": latest.summary,
        "kind": if latest.kind == "feature" { "feature" } else { "fix" }}),
    )
}

/// One row of the list.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Entry {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub what: String,
    #[serde(default)]
    pub card: String,
    #[serde(default)]
    pub by: String,
    #[serde(default)]
    pub head: String,
    #[serde(default)]
    pub station: String,
    /// What can be seen in it but not used yet.
    #[serde(default)]
    pub not_usable: Vec<String>,
    #[serde(default)]
    pub retired: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fixed: Option<Fixed>,
    #[serde(default)]
    pub added_at: String,
    /// §23b: the last click / key / scroll on this preview's page.
    #[serde(default)]
    pub last_active_at: String,
}

/// §23b: when an update waits for the viewer (seconds; adjustable).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateRule {
    #[serde(default = "idle_default")]
    pub idle_secs: u64,
    #[serde(default = "nag_default")]
    pub nag_secs: u64,
}

fn idle_default() -> u64 {
    IDLE_SECS
}
fn nag_default() -> u64 {
    NAG_SECS
}

impl Default for UpdateRule {
    fn default() -> Self {
        Self {
            idle_secs: IDLE_SECS,
            nag_secs: NAG_SECS,
        }
    }
}

/// The person fixed it (定版): their words and the head they meant.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Fixed {
    pub head: String,
    pub words: String,
    pub at: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct Registry {
    #[serde(default)]
    previews: Vec<Entry>,
    #[serde(default)]
    update: UpdateRule,
}

/// The list's update rule (defaults when there is none).
pub fn update_rule(reg: &Path) -> UpdateRule {
    std::fs::read_to_string(reg)
        .ok()
        .and_then(|t| serde_json::from_str::<Registry>(&t).ok())
        .map(|r| r.update)
        .unwrap_or_default()
}

/// An id aibd gave (`pv_` + lowercase hex / `_`): never a path.
pub fn valid_id(s: &str) -> bool {
    s.strip_prefix("pv_").is_some_and(|r| {
        !r.is_empty()
            && r.len() <= 40
            && r.bytes()
                .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase() || b == b'_')
    })
}

/// The list in `reg`; empty when there is none or it does not read.
pub fn load(reg: &Path) -> Vec<Entry> {
    std::fs::read_to_string(reg)
        .ok()
        .and_then(|t| serde_json::from_str::<Registry>(&t).ok())
        .map(|r| r.previews)
        .unwrap_or_default()
}

pub(crate) fn save(reg: &Path, previews: Vec<Entry>) -> std::io::Result<()> {
    let update = update_rule(reg);
    let text = serde_json::to_string_pretty(&Registry { previews, update })
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let tmp = reg.with_extension("json.tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, reg)
}

/// One writer of the list at a time.
pub(crate) static REG: Mutex<()> = Mutex::new(());

/// 能看: what the page shows while the list is empty (no build, cannot start).
pub fn samples() -> Vec<Entry> {
    [
        (
            "pv_sample_list",
            "預覽清單(樣本)",
            "每個預覽一列、各自開關與同意",
        ),
        (
            "pv_sample_second",
            "第二個預覽(樣本)",
            "同時可以有很多個預覽",
        ),
    ]
    .into_iter()
    .map(|(id, name, what)| Entry {
        id: id.into(),
        name: name.into(),
        what: what.into(),
        station: STATIONS[0].into(),
        not_usable: vec!["樣本，沒有執行檔".into()],
        ..Default::default()
    })
    .collect()
}

/// Text from the page: no control characters, at most `TEXT_MAX`.
pub(crate) fn clean_text(v: Option<&Value>) -> String {
    clean(v)
}

fn clean(v: Option<&Value>) -> String {
    v.and_then(Value::as_str)
        .unwrap_or("")
        .chars()
        .filter(|c| !c.is_control())
        .take(TEXT_MAX)
        .collect::<String>()
        .trim()
        .to_owned()
}

/// This aibd is a preview build run by another aibd.
pub fn is_preview() -> bool {
    std::env::var(ENV_PREVIEW).as_deref() == Ok("1")
}

/// §20 (5d33a ecf54f91d M1, PREREAD-integws2 M2): what a preview build refuses to run, so it
/// never installs over, starts, stops or updates the real aibd. `status` is only a read.
pub fn refuses_service_cmd(cmd: &str) -> bool {
    matches!(cmd, "install" | "uninstall" | "start" | "stop" | "update")
}

/// The name this preview build was started under; `None` outside a preview.
pub fn preview_name() -> Option<String> {
    is_preview()
        .then(|| std::env::var(ENV_PREVIEW_NAME).ok())
        .flatten()
        .map(|n| {
            n.chars()
                .filter(|c| !c.is_control())
                .take(TEXT_MAX)
                .collect()
        })
}

/// The list id this preview build was started under; `None` outside a preview.
pub fn preview_id() -> Option<String> {
    is_preview()
        .then(|| std::env::var(ENV_PREVIEW_ID).ok())
        .flatten()
        .filter(|i| valid_id(i))
}

/// The preview a body names: `id`, or `preview_id` (043dd 518ffd48 page).
pub fn id_of(body: &Value) -> &str {
    body.get("id")
        .or_else(|| body.get("preview_id"))
        .and_then(Value::as_str)
        .unwrap_or("")
}

pub fn bin_name() -> &'static str {
    if cfg!(windows) {
        "aibd.exe"
    } else {
        "aibd"
    }
}

/// What `<data>/preview/` holds now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    pub version: String,
    pub sha256: String,
    pub bin: PathBuf,
    /// VERSION's second line: what this build changed, one line (043dd 9793c435 「已更新」).
    pub summary: String,
    /// VERSION's third line: `fix` (default; swapped in by itself, §23c) or `feature` (waits
    /// for 「按一下更新」).
    pub kind: String,
    /// VERSION's lines 4 on (§23d).
    pub changes: Vec<Change>,
}

/// A VERSION line as kept: no control characters, at most `VERSION_MAX`.
fn line_of(text: &str, n: usize) -> String {
    text.lines()
        .nth(n)
        .unwrap_or("")
        .trim()
        .chars()
        .filter(|c| !c.is_control())
        .take(VERSION_MAX)
        .collect()
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn hash_file(p: &Path) -> Option<String> {
    let meta = std::fs::metadata(p).ok()?;
    if !meta.is_file() || meta.len() > BIN_MAX_BYTES {
        return None;
    }
    std::fs::read(p).ok().map(|b| sha256_hex(&b))
}

/// The preview build in `dir` (`<data>/preview`) with its VERSION line, hashed; `None` when
/// either is missing. The build is never run to learn its version.
pub fn find(dir: &Path) -> Option<Found> {
    let bin = dir.join(bin_name());
    let text = std::fs::read_to_string(dir.join(VERSION_FILE)).ok()?;
    let version: String = text
        .lines()
        .next()
        .unwrap_or("")
        .trim()
        .chars()
        .filter(|c| !c.is_control())
        .take(VERSION_MAX)
        .collect();
    if version.is_empty() {
        return None;
    }
    let sha256 = hash_file(&bin)?;
    let kind = match line_of(&text, 2).as_str() {
        "feature" => "feature",
        _ => "fix",
    }
    .to_owned();
    Some(Found {
        version,
        sha256,
        bin,
        summary: line_of(&text, 1),
        kind,
        changes: changes_of(&text),
    })
}

/// The environment the preview build gets: names in this list only, from this aibd's own
/// environment (no `AIBD_*` except the ones set here), with its own HOME.
/// What a preview takes from its parent as it is (519d0 11-19 08:36: a parent run under another
/// HOME left its preview without the documents folder, the import state, the old cabin, the
/// mirror and the person's name). Any other `AIBD_*` stays behind.
pub const PREVIEW_PASS_ENV: [&str; 5] = [
    "AIBD_PLAN_PENDING",
    "AIBD_IMPORT_STATE",
    "AIBD_LEGACY_CABIN",
    "AIBD_DM_MIRROR",
    "AIBD_PERSON_NAME",
];

pub fn preview_env(
    parent: impl IntoIterator<Item = (String, String)>,
    dir: &Path,
) -> Vec<(String, String)> {
    let keep = |k: &str| {
        crate::platform::session::BASE_ENV
            .iter()
            .any(|b| b.eq_ignore_ascii_case(k))
            && !matches!(k.to_ascii_uppercase().as_str(), "HOME" | "USERPROFILE")
    };
    let parent: Vec<(String, String)> = parent.into_iter().collect();
    // 5d33a CHECK-preview-seat-home (甲): the seats' providers log in with this machine's
    // real HOME; the preview aibd itself keeps its own below
    // a parent that itself runs under another HOME hands on the seat home it was given
    // (519d0 08:36: the parent at another HOME gave its seats that HOME, not Andy's login)
    let named = |name: &str| {
        parent
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.clone())
            .filter(|h| Path::new(h).is_absolute())
    };
    let real_home = named(crate::platform::session::SEAT_HOME_ENV)
        .or_else(|| named(if cfg!(windows) { "USERPROFILE" } else { "HOME" }));
    let mut out: Vec<(String, String)> = parent
        .into_iter()
        .filter(|(k, _)| keep(k) || k == "AIBD_PAIRING" || PREVIEW_PASS_ENV.contains(&k.as_str()))
        .collect();
    if let Some(h) = real_home {
        out.push((crate::platform::session::SEAT_HOME_ENV.into(), h));
    }
    let home = dir.join("home").to_string_lossy().into_owned();
    out.push(("HOME".into(), home.clone()));
    if cfg!(windows) {
        out.push(("USERPROFILE".into(), home));
    }
    out.push((
        "AIBD_DATA_DIR".into(),
        dir.join("data").to_string_lossy().into_owned(),
    ));
    out.push(("AIBD_BIND".into(), "127.0.0.1:0".into()));
    out.push((ENV_PREVIEW.into(), "1".into()));
    out
}

struct Live {
    child: Child,
    port: u16,
    sha256: String,
    version: String,
    since: String,
}

static LIVE: Mutex<BTreeMap<String, Live>> = Mutex::new(BTreeMap::new());
/// The event log, kept at the first start so aibd's own shutdown can write `preview.stopped`.
static STORE: OnceLock<Arc<aib_store::Store>> = OnceLock::new();

/// The session policy this aibd runs under, set once at start (server.rs).
static PARENT_POLICY: OnceLock<Arc<crate::config::SessionPolicy>> = OnceLock::new();

pub fn set_parent_policy(p: Arc<crate::config::SessionPolicy>) {
    let _ = PARENT_POLICY.set(p);
}

/// The persona cards file this aibd reads, set once at start (server.rs).
static PARENT_CARDS: OnceLock<Option<PathBuf>> = OnceLock::new();

pub fn set_parent_cards(p: Option<PathBuf>) {
    let _ = PARENT_CARDS.set(p);
}

/// 519d0 11-19 21:32 (0eed7 EVID-mom-preview-0eed7-493aea977): a preview read the cards under
/// its own HOME, an old hand copy or none, so today's cards were not there. It now reads the
/// file its parent reads (`AIBD_PERSONA_CARDS`): a list of names, read only by aibd (no write
/// path), the same one the parent's page offers. No cards file here (the trial mother's own
/// HOME has none, 7b81e 11-19 22:3x): nothing is set and the preview reads its own as before;
/// handing a path that is not there would leave it with no cards at all.
pub(crate) fn cards_env(parent: Option<&Path>) -> Option<(&'static str, PathBuf)> {
    parent
        .filter(|p| p.is_absolute())
        .filter(|p| p.is_file())
        .map(|p| ("AIBD_PERSONA_CARDS", p.to_path_buf()))
}

/// The parent's `brain-candidates.json` (the second brains a person may register, 能看
/// stage) goes into the preview's data folder too, owner-only through a temp file; with none
/// here, the preview's own file is left as it is (519d0 11-19 21:32).
pub(crate) fn hand_candidates(data: &Path, parent: Option<&Path>) -> std::io::Result<()> {
    let Some(text) = parent.and_then(|p| std::fs::read(p).ok()) else {
        return Ok(());
    };
    write_owner_only(data, "brain-candidates.json", &text)
}

fn write_owner_only(data: &Path, name: &str, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = data.join(format!(".{name}.tmp"));
    let _ = std::fs::remove_file(&tmp);
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut o, 0o600);
    std::io::Write::write_all(&mut o.open(&tmp)?, bytes)?;
    std::fs::rename(&tmp, data.join(name))
}

/// 519d0 11-19 21:26 (Andy could not open a seat in the parent's preview): the preview's data
/// folder gets the policy this aibd is running under, whether it came from
/// `AIBD_SESSION_POLICY` or from the data folder; before, only the data folder's file was copied,
/// so a parent started with the env var gave its preview none and every brand read
/// not_set_up. Written owner-only through a temp file. With none in effect here (the parent
/// itself opens no brand), the preview's own file is left as it is: a person may have put one
/// there by hand (519d0 did, 11-19 21:1x), and an empty file would only take it away.
pub(crate) fn hand_policy(
    data: &Path,
    policy: Option<&crate::config::SessionPolicy>,
) -> std::io::Result<()> {
    let Some(p) = policy.filter(|p| !p.programs.is_empty()) else {
        return Ok(());
    };
    write_owner_only(
        data,
        crate::allowlist::DEFAULT_POLICY_FILE,
        p.to_file_text().as_bytes(),
    )
}

fn live() -> std::sync::MutexGuard<'static, BTreeMap<String, Live>> {
    LIVE.lock().unwrap_or_else(|e| e.into_inner())
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (status, Json(json!({"code": code, "message": message}))).into_response()
}

/// `<data>/previews/<id>`, for an id aibd gave.
pub(crate) fn preview_dir(id: &str) -> Option<PathBuf> {
    valid_id(id)
        .then(|| crate::workspace::data_file(DIR).map(|d| d.join(id)))
        .flatten()
}

pub(crate) fn registry() -> Option<PathBuf> {
    crate::workspace::data_file(REGISTRY)
}

pub(crate) fn record(store: &aib_store::Store, kind: &str, payload: Value) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
    let event = NewEvent {
        event_id: id.clone(),
        aggregate_type: "preview".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    if let Err(e) = store.append(vec![event]) {
        tracing::warn!(kind, error = %e, "preview event not written");
    }
}

/// Preview `id`'s running build, after noting one that ended by itself (`preview.exited`).
fn reap(store: &aib_store::Store, id: &str) -> Option<(u16, String, String, String)> {
    let mut g = live();
    let ended = g.get_mut(id)?.child.try_wait().ok().flatten();
    if let Some(status) = ended {
        let l = g.remove(id);
        drop(g);
        record(
            store,
            "preview.exited",
            json!({"id": id, "code": status.code(), "sha256": l.map(|l| l.sha256)}),
        );
        return None;
    }
    g.get(id)
        .map(|l| (l.port, l.version.clone(), l.sha256.clone(), l.since.clone()))
}

fn url_of(port: u16) -> String {
    format!("http://127.0.0.1:{port}/")
}

/// The line a pass goes down the preview's stdin as: `pass <sha256 hex> <seconds>`. Only the
/// digest: the pass itself goes to the parent's paired page alone (GUIDE same-user AI: no
/// file, no environment, no argument carries it; the pipe is this aibd's and the child's).
pub fn pass_line(pass: &str) -> String {
    format!(
        "pass {} {}\n",
        sha256_hex(pass.as_bytes()),
        crate::pairing::PASS_TTL.as_secs()
    )
}

/// The preview's side of [`pass_line`]; anything else is `None`.
pub fn parse_pass_line(line: &str) -> Option<([u8; 32], std::time::Duration)> {
    let mut w = line.split(' ');
    let (Some("pass"), Some(h), Some(s), None) = (w.next(), w.next(), w.next(), w.next()) else {
        return None;
    };
    if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let mut d = [0u8; 32];
    for (i, b) in d.iter_mut().enumerate() {
        *b = u8::from_str_radix(h.get(2 * i..2 * i + 2)?, 16).ok()?;
    }
    Some((d, std::time::Duration::from_secs(s.parse().ok()?)))
}

/// A new pass for the running preview, its digest written down its stdin; `None` when the
/// preview has no pipe (an older start) or the write failed.
fn hand_pass(child: &mut Child) -> Option<String> {
    use std::io::Write;
    let mut raw = [0u8; 32];
    getrandom::fill(&mut raw).ok()?;
    use base64::Engine;
    let pass = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(raw);
    let w = child.stdin.as_mut()?;
    w.write_all(pass_line(&pass).as_bytes()).ok()?;
    w.flush().ok()?;
    Some(pass)
}

/// The address the page opens: the built-in page (a user folder at `/` never sees the pass),
/// the pass in the fragment (never sent to a server, gone from the bar once used).
fn url_with_pass(port: u16, pass: Option<String>) -> String {
    match pass {
        Some(p) => format!("{}__builtin/#pv={p}", url_of(port)),
        None => url_of(port),
    }
}

/// In a preview: passes the parent writes down stdin go to `pairing` until stdin ends.
pub fn take_passes(r: impl std::io::BufRead, pairing: &crate::pairing::Pairing) {
    for line in r.lines() {
        let Ok(line) = line else { break };
        if let Some((digest, ttl)) = parse_pass_line(&line) {
            pairing.add_pass(digest, ttl);
        }
    }
}

/// [`take_passes`] on this process's stdin, in its own thread (a preview only).
pub fn read_passes(pairing: Arc<crate::pairing::Pairing>) {
    let _ = std::thread::Builder::new()
        .name("preview-pass".into())
        .spawn(move || take_passes(std::io::stdin().lock(), &pairing));
}

/// One row as the page reads it.
fn row(
    e: &Entry,
    found: Option<Found>,
    running: Option<(u16, String, String, String)>,
    versions: &[Version],
    swaps: &[Swap],
    sample: bool,
) -> Value {
    let b = behind(versions, running.as_ref().map(|r| r.2.as_str()));
    let mut out = json!({
        "id": e.id, "name": e.name, "what": e.what, "card": e.card, "by": e.by,
        "head": e.head, "station": e.station, "stage": e.station, "not_usable": e.not_usable,
        "retired": e.retired, "fixed": e.fixed, "added_at": e.added_at, "sample": sample,
        // §23b: the page decides 自動更新 / 「有新版」 from these (the version record, P1)
        "last_active_at": (!e.last_active_at.is_empty()).then_some(&e.last_active_at),
        "newer": b.behind > 0, "behind": b.behind, "newer_since": b.since,
        "latest_version": b.latest.as_ref().map(|v| v.version.clone()),
        "versions": versions.iter().rev().take(10).collect::<Vec<_>>(),
        "swaps": swaps,
        "history": history_of(swaps),
        "available": found.is_some() && !is_preview() && !e.retired,
        "version": found.as_ref().map(|f| f.version.clone()),
        "sha256": found.as_ref().map(|f| f.sha256.clone()),
        "kind": found.as_ref().map(|f| f.kind.clone()),
        "summary": found.as_ref().map(|f| f.summary.clone()),
        "running": running.is_some(),
    });
    if let Some((port, version, sha256, since)) = running {
        out["url"] = json!(url_of(port));
        out["since"] = json!(since);
        out["running_version"] = json!(version);
        out["running_sha256"] = json!(sha256);
    }
    out
}

/// `GET /api/v1/previews` (pages only): the list, each with its build and whether it runs.
pub async fn status(State(st): State<AppState>, Extension(c): Extension<Caller>) -> Response {
    if !matches!(c, Caller::Page(_)) {
        return refuse(
            StatusCode::FORBIDDEN,
            "page_only",
            "the previews are the page's",
        );
    }
    let Some(reg) = registry() else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "no_data_dir",
            "no data folder",
        );
    };
    let store = st.store.clone();
    let rule = {
        let reg = reg.clone();
        tokio::task::spawn_blocking(move || update_rule(&reg))
            .await
            .unwrap_or_default()
    };
    let got = tokio::task::spawn_blocking(move || {
        let list = load(&reg);
        let sample = list.is_empty();
        let list = if sample { samples() } else { list };
        list.iter()
            .map(|e| {
                let dir = (!sample).then(|| preview_dir(&e.id)).flatten();
                let found = dir.as_deref().and_then(find);
                let versions = dir.as_deref().map(read_versions).unwrap_or_default();
                let swaps = dir
                    .as_deref()
                    .map(|d| read_swaps(d, SWAPS_SHOWN))
                    .unwrap_or_default();
                row(e, found, reap(&store, &e.id), &versions, &swaps, sample)
            })
            .collect::<Vec<Value>>()
    })
    .await;
    let Ok(rows) = got else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "previews not read",
        );
    };
    // §23c: a running preview whose folder holds a newer build is swapped now, in the
    // background (no 按 and no 閒置 needed); the page sees it on its next read
    for r in &rows {
        let newer_file = r["running"] == true
            && r["sample"] != true
            && r["sha256"].is_string()
            && r["sha256"] != r["running_sha256"]
            // a feature waits for 「按一下更新」 (043dd 9793c435)
            && r["kind"] != "feature";
        if newer_file && !is_preview() {
            let (store, id) = (st.store.clone(), r["id"].as_str().unwrap_or("").to_owned());
            drop(tokio::task::spawn_blocking(move || {
                swap_blocking(&store, &id, "auto")
            }));
        }
    }
    Json(
        json!({"previews": rows, "in_preview": is_preview(), "stations": STATIONS,
        "update": rule}),
    )
    .into_response()
}

/// Which page this is, for the events.
fn page_kind(page: &PageAccess, words: bool) -> &'static str {
    if page.carried {
        "carried"
    } else if !words {
        "user_page"
    } else {
        "built_in"
    }
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// Why a start body is refused, before anything is looked at.
pub fn start_problem(body: &Value) -> Option<(StatusCode, &'static str, &'static str)> {
    let Some(o) = body.as_object() else {
        return Some((
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be an object",
        ));
    };
    if o.keys().any(|k| !START_FIELDS.contains(&k.as_str())) {
        return Some((
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "preview_start takes id, version, sha256, consent",
        ));
    }
    if !valid_id(id_of(body)) {
        return Some((
            StatusCode::BAD_REQUEST,
            "id_bad",
            "name the preview by the id the list gave",
        ));
    }
    if body.get("consent") != Some(&json!(true)) {
        return Some((
            StatusCode::BAD_REQUEST,
            "consent_required",
            "agree in the window first",
        ));
    }
    if !body
        .get("sha256")
        .and_then(Value::as_str)
        .is_some_and(is_hex64)
    {
        return Some((
            StatusCode::BAD_REQUEST,
            "sha256_bad",
            "send back the sha256 the page showed",
        ));
    }
    None
}

/// Copy the build to `run/` and hash the copy; the copy, when it is still `sha256`.
///
/// §23c (Andy 11-19 00:2x 「對於 預覽版 修要好自動換」): every build of a preview runs from the
/// same path, `run/aibd`, so a swap is a new file at the path the running one started from —
/// what pair_carry takes as an update, and the page's confirmed pairing goes over to the new
/// build. The copy is made next to it and moved in place (a running file is never written).
fn stage(dir: &Path, found: &Found, sha256: &str) -> Result<PathBuf, &'static str> {
    let run = dir.join("run");
    std::fs::create_dir_all(&run).map_err(|_| "preview_not_started")?;
    let place = run.join(bin_name());
    let copy = run.join(format!("{}.next", bin_name()));
    let _ = std::fs::remove_file(&copy);
    std::fs::copy(&found.bin, &copy).map_err(|_| "preview_not_started")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&copy, std::fs::Permissions::from_mode(0o700))
            .map_err(|_| "preview_not_started")?;
    }
    if hash_file(&copy).as_deref() != Some(sha256) {
        let _ = std::fs::remove_file(&copy);
        return Err("preview_changed");
    }
    // Windows does not replace a running file but lets it be renamed away
    if cfg!(windows) && place.exists() {
        let _ = std::fs::rename(
            &place,
            run.join(format!("old-{}-{}", now_nanos(), bin_name())),
        );
    }
    std::fs::rename(&copy, &place).map_err(|_| "preview_not_started")?;
    Ok(place)
}

fn now_nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

/// The port the preview build wrote in its `run/aibd.json`, waiting at most the timer's life.
fn wait_port(dir: &Path, child: &mut Child) -> Option<u16> {
    let info = dir.join("data").join("run").join("aibd.json");
    let op = crate::timers::PREVIEW_PORT_WAIT.begin(None);
    loop {
        if let Ok(text) = std::fs::read_to_string(&info) {
            let port = serde_json::from_str::<Value>(&text)
                .ok()
                .and_then(|v| v["port"].as_u64())
                .and_then(|p| u16::try_from(p).ok());
            if port.is_some() {
                return port;
            }
        }
        if !matches!(child.try_wait(), Ok(None)) || !op.tick() {
            return None;
        }
    }
}

fn spawn(
    dir: &Path,
    bin: &Path,
    id: &str,
    name: &str,
    sha256: &str,
    port: Option<u16>,
) -> std::io::Result<Child> {
    let data = dir.join("data");
    std::fs::create_dir_all(data.join("run"))?;
    std::fs::create_dir_all(dir.join("home"))?;
    // an old port must not be read as this start's
    let _ = std::fs::remove_file(data.join("run").join("aibd.json"));
    // the seats it opens follow the same policy (a copy: it cannot change this one)
    hand_policy(&data, PARENT_POLICY.get().map(Arc::as_ref))?;
    hand_candidates(
        &data,
        crate::workspace::data_file("brain-candidates.json").as_deref(),
    )?;
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("aibd.log"))?;
    let mut cmd = Command::new(bin);
    cmd.env_clear()
        .envs(preview_env(std::env::vars(), dir))
        .env(ENV_PREVIEW_NAME, name)
        .env(ENV_PREVIEW_ID, id)
        .env(ENV_PREVIEW_SHA, sha256)
        // a swap keeps the port, so the open page stays on its origin (§23c)
        .env("AIBD_BIND", format!("127.0.0.1:{}", port.unwrap_or(0)))
        .current_dir(dir)
        // the parent hands its passes down here (Andy 11-19 08:5x)
        .stdin(Stdio::piped())
        .stdout(log.try_clone()?)
        .stderr(log);
    if let Some((k, v)) = cards_env(PARENT_CARDS.get().and_then(Option::as_deref)) {
        cmd.env(k, v);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // its own group: a signal to this aibd's group does not reach it
        cmd.process_group(0);
    }
    // nothing this aibd holds open but the three it hands over (519d0 11-20: #867 macOS
    // cell, fds 131 140 143 146 reached the preview)
    #[cfg(target_os = "macos")]
    aib_sys::stdio_only::stdio_only(&mut cmd);
    cmd.spawn()
}

/// Stop the running build: TERM and its own graceful stop (unix), then kill. `by` goes to the
/// event. Blocking.
pub fn stop_blocking(store: &aib_store::Store, id: &str, by: &str) -> bool {
    let Some(mut l) = live().remove(id) else {
        return false;
    };
    #[cfg(unix)]
    {
        if let Ok(pid) = i32::try_from(l.child.id()) {
            let _ = nix::sys::signal::kill(
                nix::unistd::Pid::from_raw(pid),
                nix::sys::signal::Signal::SIGTERM,
            );
            let op = crate::timers::PREVIEW_STOP_WAIT.begin(None);
            while matches!(l.child.try_wait(), Ok(None)) && op.tick() {}
        }
    }
    let ended_by_itself = matches!(l.child.try_wait(), Ok(Some(_)));
    if !ended_by_itself {
        let _ = l.child.kill();
    }
    let _ = l.child.wait();
    record(
        store,
        "preview.stopped",
        json!({"id": id, "by": by, "sha256": l.sha256, "killed": !ended_by_itself}),
    );
    true
}

/// aibd is ending: every preview goes with it.
pub fn shutdown() {
    if let Some(store) = STORE.get() {
        let ids: Vec<String> = live().keys().cloned().collect();
        for id in ids {
            stop_blocking(store, &id, "aibd");
        }
    }
}

/// `POST /api/v1/act` `{"type":"preview_start","version","sha256","consent":true}`.
pub async fn start(st: AppState, page: PageAccess, body: Value) -> Response {
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    if !words || page.carried {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_needs_built_in_page",
            "only the built-in page a person paired turns the preview on",
        );
    }
    if is_preview() {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_in_preview",
            "this is the preview",
        );
    }
    if let Some((status, code, message)) = start_problem(&body) {
        return refuse(status, code, message);
    }
    let id = id_of(&body).to_owned();
    let (Some(dir), Some(reg)) = (preview_dir(&id), registry()) else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "no_data_dir",
            "no data folder",
        );
    };
    let want = body["sha256"].as_str().unwrap_or("").to_owned();
    let _ = STORE.set(st.store.clone());
    let store = st.store.clone();
    let pairing: String = page.digest[..6]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let kind = page_kind(&page, words);
    let done =
        tokio::task::spawn_blocking(move || -> Result<String, (StatusCode, &'static str)> {
            let listed = load(&reg).into_iter().find(|e| e.id == id);
            let name = match listed {
                None => return Err((StatusCode::NOT_FOUND, "preview_not_listed")),
                Some(e) if e.retired => return Err((StatusCode::CONFLICT, "preview_retired")),
                Some(e) => e.name,
            };
            if let Some((port, _, sha, _)) = reap(&store, &id) {
                if sha == want {
                    let pass = live().get_mut(&id).and_then(|l| hand_pass(&mut l.child));
                    return Ok(url_with_pass(port, pass));
                }
                return Err((StatusCode::CONFLICT, "preview_running_other"));
            }
            let found = find(&dir).ok_or((StatusCode::NOT_FOUND, "preview_unavailable"))?;
            if found.sha256 != want {
                return Err((StatusCode::CONFLICT, "preview_changed"));
            }
            let copy = stage(&dir, &found, &want).map_err(|c| (StatusCode::CONFLICT, c))?;
            record(
                &store,
                "preview.consented",
                json!({"id": id, "pairing": pairing, "page": kind, "version": found.version,
                "sha256": want, "sha12": &want[..12]}),
            );
            let started = Instant::now();
            note_version(&dir, &found);
            append_swap(
                &dir,
                &swap_of(&found, &format!("page:{pairing}"), "start", None),
            );
            let mut child = spawn(&dir, &copy, &id, &name, &want, None)
                .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "preview_not_started"))?;
            let Some(port) = wait_port(&dir, &mut child) else {
                let _ = child.kill();
                let _ = child.wait();
                record(
                    &store,
                    "preview.start_failed",
                    json!({"id": id, "sha256": want}),
                );
                return Err((StatusCode::SERVICE_UNAVAILABLE, "preview_not_started"));
            };
            let pass = hand_pass(&mut child);
            record(
                &store,
                "preview.started",
                json!({"id": id, "sha256": want, "port": port, "pid": child.id(),
                "ms": u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)}),
            );
            live().insert(
                id.clone(),
                Live {
                    child,
                    port,
                    sha256: want,
                    version: found.version,
                    since: crate::sessions::now_rfc3339(),
                },
            );
            Ok(url_with_pass(port, pass))
        })
        .await;
    match done {
        Ok(Ok(url)) => Json(json!({"url": url})).into_response(),
        Ok(Err((status, code))) => refuse(status, code, "the preview did not start"),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "preview start failed",
        ),
    }
}

/// A build the record does not know yet (put there by hand, or by the quick-fix line) gets a
/// line, so 有新版 / 已更新 can be told from the record.
fn note_version(dir: &Path, found: &Found) {
    let known = read_versions(dir)
        .iter()
        .any(|v| v.state == "ready" && v.sha256 == found.sha256);
    if known {
        return;
    }
    let head = found
        .version
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_owned();
    let _ = append_version(
        dir,
        Version {
            head,
            version: found.version.clone(),
            sha256: found.sha256.clone(),
            at: crate::sessions::now_rfc3339(),
            state: "ready".into(),
            summary: found.summary.clone(),
            kind: found.kind.clone(),
            ..Default::default()
        },
    );
}

/// `POST /api/v1/act` `{"type":"preview_open","id"}` (Andy 11-19 08:5x): 打開 on a running
/// preview hands a new pass, so the tab opens paired. Only the built-in page a person paired
/// (as [`start`]); `preview_not_running` when it is not up.
pub async fn open(st: AppState, page: PageAccess, body: Value) -> Response {
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    // the person's own pairing on the built-in page, not one carried over a version swap
    let paired_here = words && !page.carried;
    if !paired_here {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_needs_built_in_page",
            "only the built-in page a person paired opens the preview paired",
        );
    }
    if is_preview() {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_in_preview",
            "this is the preview",
        );
    }
    let id = id_of(&body).to_owned();
    let store = st.store.clone();
    let pairing: String = page.digest[..6]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let done = tokio::task::spawn_blocking(move || {
        let (port, ..) = reap(&store, &id)?;
        let pass = live().get_mut(&id).and_then(|l| hand_pass(&mut l.child));
        record(
            &store,
            "preview.opened",
            json!({"id": id, "pairing": pairing, "paired": pass.is_some()}),
        );
        Some(url_with_pass(port, pass))
    })
    .await;
    match done {
        Ok(Some(url)) => Json(json!({"url": url})).into_response(),
        Ok(None) => refuse(
            StatusCode::CONFLICT,
            "preview_not_running",
            "the preview is not running",
        ),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the preview did not open",
        ),
    }
}

/// Start the staged build and wait for its port; `port` first, then any port.
fn launch(
    dir: &Path,
    copy: &Path,
    id: &str,
    name: &str,
    sha256: &str,
    port: Option<u16>,
) -> Option<(Child, u16)> {
    let mut child = spawn(dir, copy, id, name, sha256, port).ok()?;
    if let Some(p) = wait_port(dir, &mut child) {
        return Some((child, p));
    }
    let _ = child.kill();
    let _ = child.wait();
    port.and_then(|_| launch(dir, copy, id, name, sha256, None))
}

/// Previews being swapped now (one swap per preview at a time).
static SWAPPING: Mutex<std::collections::BTreeSet<String>> =
    Mutex::new(std::collections::BTreeSet::new());

/// §23c: put the newest build of running preview `id` in place of the one running, on the same
/// port; its data, HOME, seats and threads stay (same folders) and the page's confirmed
/// pairing goes over once (pair_carry). Blocking.
pub fn swap_blocking(store: &aib_store::Store, id: &str, by: &str) -> Result<Value, &'static str> {
    if !SWAPPING
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(id.to_owned())
    {
        return Err("preview_swapping");
    }
    let out = swap_once(store, id, by);
    SWAPPING
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(id);
    out
}

fn swap_once(store: &aib_store::Store, id: &str, by: &str) -> Result<Value, &'static str> {
    let dir = preview_dir(id).ok_or("no_data_dir")?;
    let (port, old_version, old_sha) = reap(store, id)
        .map(|r| (r.0, r.1, r.2))
        .ok_or("preview_not_running")?;
    let found = find(&dir).ok_or("preview_unavailable")?;
    if found.sha256 == old_sha {
        return Ok(json!({"id": id, "updated": false}));
    }
    let name = registry()
        .map(|r| load(&r))
        .unwrap_or_default()
        .into_iter()
        .find(|e| e.id == id)
        .filter(|e| !e.retired)
        .map(|e| e.name)
        .ok_or("preview_not_listed")?;
    note_version(&dir, &found);
    let copy = stage(&dir, &found, &found.sha256)?;
    stop_blocking(store, id, by);
    let started = Instant::now();
    let Some((child, now_port)) = launch(&dir, &copy, id, &name, &found.sha256, Some(port)) else {
        record(
            store,
            "preview.swap_failed",
            json!({"id": id, "from": old_sha, "to": found.sha256}),
        );
        return Err("preview_not_started");
    };
    let how = if by == "auto" { "auto" } else { "manual" };
    append_swap(&dir, &swap_of(&found, by, how, Some(old_version)));
    record(
        store,
        "preview.swapped",
        json!({"id": id, "by": by, "from": old_sha, "to": found.sha256, "port": now_port,
            "port_kept": now_port == port, "pid": child.id(),
            "ms": u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)}),
    );
    live().insert(
        id.to_owned(),
        Live {
            child,
            port: now_port,
            sha256: found.sha256.clone(),
            version: found.version.clone(),
            since: crate::sessions::now_rfc3339(),
        },
    );
    Ok(json!({"id": id, "updated": true, "url": url_of(now_port),
        "port_kept": now_port == port, "version": found.version}))
}

/// `act preview_update {preview_id}`: swap now (the built-in page; inside a preview the swap
/// is automatic, so it answers only).
pub async fn update(st: AppState, page: PageAccess, body: Value) -> Response {
    if is_preview() {
        return Json(json!({"state": "auto", "done": false})).into_response();
    }
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    if page.carried || !words {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_needs_built_in_page",
            "only the built-in page a person paired updates a preview",
        );
    }
    let id = id_of(&body).to_owned();
    if !valid_id(&id) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "id_bad",
            "name the preview by its id",
        );
    }
    let store = st.store.clone();
    let by = format!(
        "page:{}",
        page.digest[..6]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    match tokio::task::spawn_blocking(move || swap_blocking(&store, &id, &by)).await {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(code)) => refuse(StatusCode::CONFLICT, code, "the preview was not updated"),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "preview update failed",
        ),
    }
}

/// In a preview build: what the last swap brought (`{version, at, from_version, changes}`),
/// for the 「已更新」 line on top (§23c); `None` when the record does not know this build.
pub fn preview_updated() -> Option<Value> {
    if !is_preview() {
        return None;
    }
    let cur = std::env::var(ENV_PREVIEW_SHA).ok()?;
    let in_data = crate::workspace::data_file(VERSIONS)?;
    let dir = in_data.parent()?.parent()?.to_path_buf();
    let vs = read_versions(&dir);
    let ready: Vec<&Version> = vs.iter().filter(|v| v.state == "ready").collect();
    let i = ready.iter().rposition(|v| v.sha256 == cur)?;
    let prev = i.checked_sub(1).map(|j| ready[j]);
    Some(
        json!({"version": ready[i].version, "at": ready[i].at, "summary": ready[i].summary,
        "from_version": prev.map(|p| p.version.clone()),
        "changes": prev.map(|p| changes(p, ready[i])).unwrap_or_default()}),
    )
}

/// In a preview build: the version it runs and what it changed (043dd 9793c435: the page
/// reloads itself when `preview_version` moves). Blocking.
pub fn preview_version() -> (Option<String>, Option<String>) {
    let Some(v) = preview_updated() else {
        return (None, None);
    };
    let s = |k: &str| v[k].as_str().filter(|t| !t.is_empty()).map(str::to_owned);
    (s("version"), s("summary"))
}

/// In a preview build: the newest swaps of its preview, for the 載入彈窗's 「這次更新」 (§23d).
pub fn preview_swaps() -> Vec<Swap> {
    if !is_preview() {
        return Vec::new();
    }
    crate::workspace::data_file(SWAPS)
        .and_then(|p| p.parent()?.parent().map(Path::to_path_buf))
        .map(|d| read_swaps(&d, SWAPS_SHOWN))
        .unwrap_or_default()
}

/// A swap in 043dd 53f2cc5e's shape (`preview_history`): `by` is `auto` or `Andy` (a paired
/// built-in page is the person), `how` auto / manual (a first start reads as manual), and each
/// change's author as `seat`.
pub fn history_of(swaps: &[Swap]) -> Vec<Value> {
    swaps
        .iter()
        .map(|w| {
            let by = if w.by == "auto" {
                "auto"
            } else if w.by.starts_with("page:") {
                "Andy"
            } else {
                w.by.as_str()
            };
            let changes: Vec<Value> = w
                .changes
                .iter()
                .map(|c| json!({"text": c.text, "seat": c.author, "card": c.card}))
                .collect();
            json!({"from": w.from, "to": w.to, "at": w.at, "by": by,
                "how": if w.how == "auto" { "auto" } else { "manual" },
                "first": w.how == "start", "summary": w.summary, "changes": changes})
        })
        .collect()
}

/// The nodes whose head is new or moved between two versions.
pub fn changes(prev: &Version, cur: &Version) -> Vec<String> {
    cur.nodes
        .iter()
        .filter(|n| !prev.nodes.iter().any(|p| p.id == n.id && p.head == n.head))
        .map(|n| n.id.clone())
        .collect()
}

/// `POST /api/v1/act` `{"type":"preview_stop","id"}`: any page (stopping only makes it safer;
/// the event says which kind of page did).
pub async fn stop(st: AppState, page: PageAccess, body: Value) -> Response {
    let id = id_of(&body).to_owned();
    if !valid_id(&id) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "id_bad",
            "name the preview by its id",
        );
    }
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    let by = page_kind(&page, words);
    let store = st.store.clone();
    let stopped = tokio::task::spawn_blocking(move || stop_blocking(&store, &id, by)).await;
    Json(json!({"stopped": true, "was_running": stopped.unwrap_or(false)})).into_response()
}

/// What `preview_add` / `preview_retire` / `preview_fix` do to the list, or why not. `now` and
/// `new_id` are given so a test can call it.
pub fn edit_list(
    list: &mut Vec<Entry>,
    kind: &str,
    body: &Value,
    by: &str,
    now: &str,
    new_id: &str,
) -> Result<Value, (StatusCode, &'static str)> {
    match kind {
        ADD => {
            let name = clean(body.get("name"));
            if name.is_empty() {
                return Err((StatusCode::BAD_REQUEST, "name_required"));
            }
            if list.iter().any(|e| !e.retired && e.name == name) {
                return Err((StatusCode::CONFLICT, "name_taken"));
            }
            let not_usable = body
                .get("not_usable")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .map(|v| clean(Some(v)))
                        .filter(|t| !t.is_empty())
                        .take(50)
                        .collect()
                })
                .unwrap_or_default();
            list.push(Entry {
                id: new_id.into(),
                name,
                what: clean(body.get("what")),
                card: clean(body.get("card")),
                by: by.into(),
                head: clean(body.get("head")),
                station: STATIONS[0].into(),
                not_usable,
                added_at: now.into(),
                ..Default::default()
            });
            Ok(json!({"id": new_id}))
        }
        ACTIVE => {
            let id = id_of(body);
            let e = list
                .iter_mut()
                .find(|e| e.id == id)
                .ok_or((StatusCode::NOT_FOUND, "preview_not_listed"))?;
            e.last_active_at = now.into();
            Ok(json!({"id": id, "last_active_at": now}))
        }
        RETIRE | FIX => {
            let id = id_of(body);
            let e = list
                .iter_mut()
                .find(|e| e.id == id)
                .ok_or((StatusCode::NOT_FOUND, "preview_not_listed"))?;
            if e.retired {
                return Err((StatusCode::CONFLICT, "preview_retired"));
            }
            if kind == RETIRE {
                e.retired = true;
                return Ok(json!({"id": id, "retired": true}));
            }
            let words = clean(body.get("words"));
            if words.is_empty() {
                return Err((StatusCode::BAD_REQUEST, "words_required"));
            }
            let head = match clean(body.get("head")) {
                h if h.is_empty() => e.head.clone(),
                h => h,
            };
            e.station = STATIONS[1].into();
            e.fixed = Some(Fixed {
                head,
                words,
                at: now.into(),
            });
            Ok(json!({"id": id, "station": e.station}))
        }
        _ => Err((StatusCode::BAD_REQUEST, "invalid_request")),
    }
}

/// `POST /api/v1/act` `preview_add {name, what?, card?, head?, not_usable?}`,
/// `preview_retire {id}`, `preview_fix {id, words, head?}`: the built-in page a person paired.
pub async fn edit(st: AppState, page: PageAccess, body: Value) -> Response {
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    if page.carried || !words {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_needs_built_in_page",
            "only the built-in page a person paired changes the list",
        );
    }
    if is_preview() {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_in_preview",
            "this is a preview",
        );
    }
    let (Some(reg), Some(root)) = (registry(), crate::workspace::data_file(DIR)) else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "no_data_dir",
            "no data folder",
        );
    };
    let Ok(new_id) = crate::sessions::new_id("pv_") else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no id");
    };
    let kind = body["type"].as_str().unwrap_or("").to_owned();
    let by: String = format!(
        "page:{}",
        page.digest[..6]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    let store = st.store.clone();
    let done = tokio::task::spawn_blocking(move || {
        let _one = REG.lock().unwrap_or_else(|e| e.into_inner());
        let mut list = load(&reg);
        let now = crate::sessions::now_rfc3339();
        let out = edit_list(&mut list, &kind, &body, &by, &now, &new_id)?;
        let id = out["id"].as_str().unwrap_or_default().to_owned();
        if kind == ADD {
            std::fs::create_dir_all(root.join(&id))
                .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "no_data_dir"))?;
        }
        save(&reg, list).map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "list_not_written"))?;
        if kind == RETIRE {
            stop_blocking(&store, &id, "retired");
        }
        let event = match kind.as_str() {
            ADD => Some("preview.added"),
            RETIRE => Some("preview.retired"),
            FIX => Some("preview.fixed"),
            // a click is not worth an event
            _ => None,
        };
        if let Some(event) = event {
            record(&store, event, json!({"id": id, "by": by}));
        }
        Ok(out)
    })
    .await;
    match done {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err((status, code))) => refuse(status, code, "the list did not change"),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "preview list failed",
        ),
    }
}

#[cfg(test)]
mod tests {
    /// 5d33a PREREAD-integws2 M2: update replaces the installed aibd too.
    #[test]
    fn a_preview_refuses_every_command_that_changes_the_real_aibd() {
        for c in ["install", "uninstall", "start", "stop", "update"] {
            assert!(super::refuses_service_cmd(c), "reason: preview_may_{c}");
        }
        assert!(
            !super::refuses_service_cmd("status"),
            "reason: status_refused"
        );
    }

    use super::*;

    /// 519d0 11-19 21:32 (0eed7 EVID-mom-preview-0eed7-493aea977): the preview reads the cards
    /// file its parent reads, and gets the parent's brain candidates owner-only; with none at the
    /// parent, the preview's own candidates stay.
    #[test]
    fn a_preview_reads_its_parents_cards_and_gets_its_brain_candidates() {
        let d = tempfile::tempdir().unwrap();
        // the cards: the file the parent reads, by path (read only, never copied)
        let cards = d.path().join("cards.json");
        std::fs::write(&cards, "{}").unwrap();
        assert_eq!(
            cards_env(Some(&cards)),
            Some(("AIBD_PERSONA_CARDS", cards.clone())),
            "reason: cards_not_handed"
        );
        assert_eq!(cards_env(None), None);
        assert_eq!(cards_env(Some(Path::new("rel/cards.json"))), None);
        // a relative path that is a file (cargo runs tests in the package root) is still refused
        assert!(
            Path::new("Cargo.toml").is_file(),
            "precondition: test cwd is the package root"
        );
        assert_eq!(
            cards_env(Some(Path::new("Cargo.toml"))),
            None,
            "reason: relative_cards_handed"
        );
        // a parent whose cards file is not there hands nothing (the preview keeps its own)
        assert_eq!(
            cards_env(Some(&d.path().join("no-cards.json"))),
            None,
            "reason: missing_cards_handed"
        );
        // the brain candidates: copied owner-only; none at the parent leaves the preview's own
        let src = d.path().join("parent-candidates.json");
        std::fs::write(&src, r#"[{"route_key":"k1","title":"t1"}]"#).unwrap();
        let data = d.path().join("data");
        std::fs::create_dir_all(&data).unwrap();
        hand_candidates(&data, Some(&src)).unwrap();
        let f = data.join("brain-candidates.json");
        assert_eq!(
            std::fs::read_to_string(&f).unwrap(),
            r#"[{"route_key":"k1","title":"t1"}]"#,
            "reason: candidates_not_handed"
        );
        hand_candidates(&data, Some(&d.path().join("none.json"))).unwrap();
        hand_candidates(&data, None).unwrap();
        assert!(f.exists(), "reason: preview_candidates_wiped");
    }

    /// 519d0 11-19 21:26: the preview gets the policy this aibd runs under, owner-only, read back
    /// as the same programs and env names; with none here, the preview's own file stays.
    #[test]
    fn a_preview_gets_the_policy_its_parent_runs_under() {
        let d = tempfile::tempdir().unwrap();
        let policy = crate::config::SessionPolicy {
            programs: vec![crate::allowlist::Program {
                name: "claude".into(),
                // an absolute path on either system (allowlist::parse refuses a relative one;
                // `/opt/…` has no drive on Windows)
                path: if cfg!(windows) {
                    r"C:\ez2-test\claude.exe".into()
                } else {
                    "/opt/ez2-test/claude".into()
                },
                signer: Some("TEAMID1234".into()),
            }],
            env: vec!["EZ2_TEST_NAME".into()],
            interpreters_ok: false,
        };
        hand_policy(d.path(), Some(&policy)).unwrap();
        let f = d.path().join(crate::allowlist::DEFAULT_POLICY_FILE);
        let back = crate::allowlist::parse(&std::fs::read_to_string(&f).unwrap()).unwrap();
        assert_eq!(back, policy, "reason: policy_not_handed");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&f).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "reason: policy_not_owner_only");
        }
        let kept = std::fs::read_to_string(&f).unwrap();
        hand_policy(d.path(), Some(&crate::config::SessionPolicy::default())).unwrap();
        assert_eq!(
            std::fs::read_to_string(&f).unwrap(),
            kept,
            "reason: empty_policy_overwrote"
        );
        hand_policy(d.path(), None).unwrap();
        assert_eq!(std::fs::read_to_string(&f).unwrap(), kept);
    }

    /// Andy 11-19 08:5x: what goes down the preview's stdin is the pass's digest and its life,
    /// never the pass; the preview takes only well-formed lines, and the page it lets in once.
    #[test]
    fn a_pass_goes_down_the_pipe_as_its_digest_and_lets_one_page_in() {
        let pass = "Zz09_-".repeat(7) + "a";
        assert_eq!(pass.len(), 43);
        let line = pass_line(&pass);
        assert!(!line.contains(&pass), "reason: pass_in_pipe {line}");
        let (d, ttl) = parse_pass_line(line.trim_end()).unwrap();
        assert_eq!(d.as_slice(), Sha256::digest(pass.as_bytes()).as_slice());
        assert_eq!(ttl, crate::pairing::PASS_TTL);
        for bad in [
            "pass",
            "pass 00 120",
            &format!("pass {} x", "0".repeat(64)),
            &format!("pass {} 120 more", "0".repeat(64)),
            &format!("pass {} 120", "g".repeat(64)),
        ] {
            assert!(
                parse_pass_line(bad).is_none(),
                "reason: bad_line_taken {bad}"
            );
        }
        let p = crate::pairing::Pairing::default()
            .with_channel(crate::pairing::ConfirmChannel::TouchId);
        let input = format!("junk\n{line}");
        take_passes(std::io::Cursor::new(input), &p);
        assert!(p.admit_pass(&pass).is_ok(), "reason: pass_not_taken");
        assert!(p.admit_pass(&pass).is_err(), "reason: pass_reused");
        assert_eq!(
            url_with_pass(45678, Some(pass.clone())),
            format!("http://127.0.0.1:45678/__builtin/#pv={pass}"),
            "reason: pass_not_to_built_in_page"
        );
        assert_eq!(url_with_pass(45678, None), "http://127.0.0.1:45678/");
    }

    #[test]
    fn the_preview_build_is_found_by_its_version_line_and_hash() {
        let d = tempfile::tempdir().unwrap();
        assert!(find(d.path()).is_none(), "reason: found_without_build");
        std::fs::write(d.path().join(bin_name()), b"build-1").unwrap();
        assert!(find(d.path()).is_none(), "reason: found_without_version");
        std::fs::write(
            d.path().join(VERSION_FILE),
            "0.2.0-preview.8 3f2a91c0\nmore\n",
        )
        .unwrap();
        let f = find(d.path()).unwrap();
        assert_eq!(f.version, "0.2.0-preview.8 3f2a91c0");
        assert_eq!(f.sha256, sha256_hex(b"build-1"));
        std::fs::write(d.path().join(bin_name()), b"build-2").unwrap();
        assert_ne!(
            find(d.path()).unwrap().sha256,
            f.sha256,
            "reason: hash_not_of_file"
        );
    }

    /// 519d0 11-19 08:36: a parent itself run under another HOME (a preview aibd started by
    /// hand) hands on the seat home it was given, not its own HOME; other AIBD_* stay behind.
    #[test]
    fn a_parent_with_a_seat_home_hands_that_on_not_its_own_home() {
        // preview_env takes only an absolute home: on Windows that needs a drive
        let abs = |p: &str| {
            if cfg!(windows) {
                format!("C:{p}")
            } else {
                p.to_owned()
            }
        };
        let dir = Path::new("/tmp/pv");
        let parent = [
            ("PATH", "/usr/bin".to_owned()),
            ("HOME", abs("/tmp/preview-home")),
            ("USERPROFILE", abs("/tmp/preview-home")),
            ("AIBD_SEAT_HOME", abs("/Users/real")),
            ("AIBD_SEAT_INPUT_DGRS", "1".to_owned()),
        ]
        .map(|(k, v)| (k.to_owned(), v));
        let env = preview_env(parent, dir);
        let seat: Vec<&str> = env
            .iter()
            .filter(|(k, _)| k == "AIBD_SEAT_HOME")
            .map(|(_, v)| v.as_str())
            .collect();
        assert_eq!(seat, [abs("/Users/real")], "reason: parent_home_handed");
        assert!(
            !env.iter().any(|(k, _)| k == "AIBD_SEAT_INPUT_DGRS"),
            "reason: other_aibd_carried"
        );
    }

    /// 5d33a M1: its own HOME and data; nothing of this aibd's but the base names.
    #[test]
    fn the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting() {
        let dir = Path::new("/p/preview");
        let parent = [
            ("PATH", "/usr/bin"),
            ("HOME", "/Users/real"),
            ("AIBD_DATA_DIR", "/real/data"),
            ("AIBD_SEAT_INPUT_DGRS", "1"),
            ("SSH_AUTH_SOCK", "/tmp/agent"),
            ("AIBD_PAIRING", "terminal-unverified"),
            ("AIBD_PLAN_PENDING", "/Users/real/.ez2-aib/pending"),
            ("AIBD_IMPORT_STATE", "/Users/real/.ez2-aib/state"),
            ("AIBD_LEGACY_CABIN", "/Users/real/cabin"),
            ("AIBD_DM_MIRROR", "1"),
            ("AIBD_PERSON_NAME", "Andy"),
        ]
        .map(|(k, v)| (k.to_owned(), v.to_owned()));
        let env = preview_env(parent, dir);
        let get = |k: &str| {
            env.iter()
                .filter(|(n, _)| n == k)
                .map(|(_, v)| v.as_str())
                .collect::<Vec<_>>()
        };
        let (home, data) = (dir.join("home"), dir.join("data"));
        assert_eq!(get("HOME"), [home.to_string_lossy()], "reason: home_shared");
        assert_eq!(
            get("AIBD_DATA_DIR"),
            [data.to_string_lossy()],
            "reason: data_shared"
        );
        assert_eq!(get("AIBD_BIND"), ["127.0.0.1:0"]);
        assert_eq!(get(ENV_PREVIEW), ["1"]);
        assert_eq!(get("PATH"), ["/usr/bin"]);
        assert!(
            get("AIBD_SEAT_INPUT_DGRS").is_empty(),
            "reason: dgrs_switch_carried"
        );
        assert!(get("SSH_AUTH_SOCK").is_empty());
        assert_eq!(get("AIBD_PAIRING"), ["terminal-unverified"]);
        // 519d0 11-19 08:36: these five go over as they are
        assert_eq!(
            get("AIBD_PLAN_PENDING"),
            ["/Users/real/.ez2-aib/pending"],
            "reason: pass_env_dropped"
        );
        for k in &PREVIEW_PASS_ENV[1..] {
            assert_eq!(get(k).len(), 1, "reason: pass_env_dropped {k}");
        }
        // 5d33a CHECK-preview-seat-home (甲): the real HOME goes over for the seats only
        if !cfg!(windows) {
            assert_eq!(
                get("AIBD_SEAT_HOME"),
                ["/Users/real"],
                "reason: seat_home_not_handed"
            );
        }
    }

    /// 5d33a PREREAD N1: the copy that runs is hashed again; a build changed since it was found
    /// is not run.
    #[test]
    fn the_copy_runs_only_when_it_is_still_the_file_agreed_to() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join(bin_name()), b"build-1").unwrap();
        std::fs::write(d.path().join(VERSION_FILE), "v1\n").unwrap();
        let found = find(d.path()).unwrap();
        let copy = stage(d.path(), &found, &found.sha256).unwrap();
        assert_eq!(hash_file(&copy).as_deref(), Some(found.sha256.as_str()));
        // the build changes after the page showed it
        std::fs::write(d.path().join(bin_name()), b"build-2").unwrap();
        assert_eq!(
            stage(d.path(), &found, &found.sha256),
            Err("preview_changed"),
            "reason: changed_copy_runs"
        );
    }

    #[test]
    fn a_start_needs_the_consent_and_the_hash_and_nothing_else() {
        let sha = "a".repeat(64);
        let ok =
            json!({"type": START, "id": "pv_1a", "version": "v", "sha256": sha, "consent": true});
        assert!(start_problem(&ok).is_none());
        let code = |v: Value| start_problem(&v).map(|p| p.1);
        let mut v = ok.clone();
        v["consent"] = json!(false);
        assert_eq!(code(v), Some("consent_required"));
        let mut v = ok.clone();
        v["consent"] = json!("true");
        assert_eq!(code(v), Some("consent_required"));
        let mut v = ok.clone();
        v["sha256"] = json!("A".repeat(64));
        assert_eq!(code(v), Some("sha256_bad"));
        let mut v = ok.clone();
        v.as_object_mut().unwrap().remove("sha256");
        assert_eq!(code(v), Some("sha256_bad"), "reason: version_alone");
        let mut v = ok.clone();
        v["id"] = json!("../x");
        assert_eq!(code(v), Some("id_bad"), "reason: id_is_a_path");
        let mut v = ok;
        v["path"] = json!("/x");
        assert_eq!(code(v), Some("unknown_field"));
    }

    /// §23c: every build runs from the same path (the pairing goes over with pair_carry).
    #[test]
    fn every_build_of_a_preview_runs_from_the_same_path() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join(bin_name()), b"build-1").unwrap();
        std::fs::write(d.path().join(VERSION_FILE), "aaaaaaa1 t\n").unwrap();
        let f1 = find(d.path()).unwrap();
        assert_eq!((f1.kind.as_str(), f1.summary.as_str()), ("fix", ""));
        std::fs::write(d.path().join(VERSION_FILE), "aaaaaaa1 t\n加清單\nfeature\n").unwrap();
        let ff = find(d.path()).unwrap();
        assert!(ff.changes.is_empty());
        std::fs::write(
            d.path().join(VERSION_FILE),
            "v\n摘要\nfix\n修時間軸 | 043dd | N-ws-card\n只有一句\n\n",
        )
        .unwrap();
        let fc = find(d.path()).unwrap();
        assert_eq!(fc.changes.len(), 2, "reason: change_lines_lost");
        assert_eq!(
            (fc.changes[0].author.as_str(), fc.changes[0].card.as_str()),
            ("043dd", "N-ws-card")
        );
        append_swap(d.path(), &swap_of(&fc, "auto", "auto", Some("v0".into())));
        append_swap(
            d.path(),
            &swap_of(&f1, "page:ab", "manual", Some("v".into())),
        );
        let h = history_of(&read_swaps(d.path(), 5));
        assert_eq!(
            (h[0]["by"].as_str(), h[1]["by"].as_str()),
            (Some("Andy"), Some("auto"))
        );
        assert_eq!(
            h[1]["changes"][0]["seat"], "043dd",
            "reason: change_seat_lost"
        );
        let sw = read_swaps(d.path(), 1);
        assert_eq!(
            (sw.len(), sw[0].how.as_str()),
            (1, "manual"),
            "reason: swaps_not_newest_first"
        );
        assert_eq!(
            (ff.kind.as_str(), ff.summary.as_str()),
            ("feature", "加清單")
        );
        std::fs::write(d.path().join(VERSION_FILE), "aaaaaaa1 t\n").unwrap();
        let p1 = stage(d.path(), &f1, &f1.sha256).unwrap();
        std::fs::write(d.path().join(bin_name()), b"build-2").unwrap();
        let f2 = find(d.path()).unwrap();
        let p2 = stage(d.path(), &f2, &f2.sha256).unwrap();
        assert_eq!(p1, p2, "reason: path_per_build");
        assert_eq!(hash_file(&p2), Some(f2.sha256.clone()));
        let nh = |id: &str, head: &str| NodeHead {
            id: id.into(),
            head: head.into(),
        };
        let a = Version {
            nodes: vec![nh("F", "1"), nh("G", "1")],
            ..Default::default()
        };
        let b = Version {
            nodes: vec![nh("F", "1"), nh("G", "2"), nh("H", "1")],
            ..Default::default()
        };
        assert_eq!(changes(&a, &b), ["G", "H"], "reason: changes_wrong");
    }

    /// §23 P1: the record counts what is newer than the running build; a hand-put build that
    /// is not the newest is behind all of them.
    #[test]
    fn a_running_build_is_behind_the_ready_versions_after_it() {
        let d = tempfile::tempdir().unwrap();
        let v = |sha: &str, state: &str, at: &str| Version {
            version: format!("v-{sha}"),
            sha256: sha.into(),
            at: at.into(),
            state: state.into(),
            ..Default::default()
        };
        assert_eq!(append_version(d.path(), v("a", "ready", "t1")).unwrap(), 1);
        assert_eq!(
            append_version(d.path(), v("b", "build_failed", "t2")).unwrap(),
            2
        );
        assert_eq!(append_version(d.path(), v("c", "ready", "t3")).unwrap(), 3);
        assert_eq!(append_version(d.path(), v("d", "ready", "t4")).unwrap(), 4);
        let vs = read_versions(d.path());
        assert_eq!(vs.len(), 4);
        let b = behind(&vs, Some("a"));
        assert_eq!(b.behind, 2, "reason: failed_counted_or_ready_missed");
        assert_eq!(b.since.as_deref(), Some("t3"));
        assert_eq!(b.latest.map(|l| l.version).as_deref(), Some("v-d"));
        assert_eq!(behind(&vs, Some("d")).behind, 0, "reason: newest_is_behind");
        assert_eq!(behind(&vs, None).behind, 0, "reason: stopped_is_behind");
        assert_eq!(behind(&vs, Some("by-hand")).behind, 3);
        std::fs::write(d.path().join(VERSIONS), "not json\n").unwrap();
        assert!(read_versions(d.path()).is_empty());
    }

    /// §20b: many previews, each its own row; taken down keeps the row; 定版 keeps the words.
    #[test]
    fn the_list_adds_retires_and_fixes_one_preview_at_a_time() {
        let mut l = Vec::new();
        let add = |l: &mut Vec<Entry>, name: &str, id: &str| {
            edit_list(
                l,
                ADD,
                &json!({"name": name, "card": "N-ws-x"}),
                "page:ab",
                "t1",
                id,
            )
        };
        assert!(add(&mut l, "清單", "pv_1").is_ok());
        assert!(add(&mut l, "第二個", "pv_2").is_ok());
        assert_eq!(l.len(), 2, "reason: one_preview_only");
        assert_eq!(
            add(&mut l, "清單", "pv_3").err().map(|e| e.1),
            Some("name_taken")
        );
        assert_eq!(l[0].station, "能看");
        let r = edit_list(
            &mut l,
            FIX,
            &json!({"id": "pv_2", "words": "定"}),
            "p",
            "t2",
            "",
        );
        assert!(r.is_ok(), "{r:?}");
        assert_eq!(l[1].station, "已定版");
        assert_eq!(l[1].fixed.as_ref().map(|f| f.words.as_str()), Some("定"));
        assert_eq!(l[0].station, "能看", "reason: fix_hits_other");
        assert!(edit_list(&mut l, RETIRE, &json!({"id": "pv_1"}), "p", "t3", "").is_ok());
        assert!(l[0].retired && l.len() == 2, "reason: retire_drops_row");
        assert_eq!(
            edit_list(
                &mut l,
                FIX,
                &json!({"id": "pv_1", "words": "定"}),
                "p",
                "t",
                ""
            )
            .err()
            .map(|e| e.1),
            Some("preview_retired")
        );
        assert!(valid_id("pv_0a1b") && !valid_id("pv_") && !valid_id("pv_../a"));
        assert_eq!(samples().len(), 2, "reason: samples_not_many");
    }
}
