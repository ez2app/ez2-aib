//! A13 (DESIGN-a13-model-list-08de5-1123 r1; f163c REVIEW-a13 r1 0/0/4/2; Andy 11-23 04:4x
//! 「選AI的模組，應該是下拉式的，帶出真實目前的可選ai」): the models the built-in page may offer
//! per brand, on each `capabilities.providers[]` row of `GET /api/v1/version`. Shown only: an
//! open still checks the model (`aib_seatopen::model_shape`).
//!
//! Each brand only from its own source:
//! - codex: the list codex fetched from its account itself, `<codex home>/models_cache.json`
//!   (the codex home: `CODEX_HOME`, else the seats' HOME `.codex`, as `workspace::login_now`);
//!   `visibility: "list"` only. A file outside aibd's folder that a seat can write, so it is
//!   data, not fact (GUIDE same-user threat): opened without following a link and without
//!   waiting (a FIFO answers at once), looked at once open (a plain file), read at most
//!   [`MAX_BYTES`], at most [`MAX_MODELS`] ids of at most [`MAX_ID`] bytes that pass
//!   `model_shape`, labels cut to [`MAX_LABEL`] with no control characters (f163c L1–L3). The
//!   `.codex` folder itself being a link is not refused: what leaks is ids that pass the shape.
//! - claude: its CLI lists no models and keeps no quota on disk; aibd's own aliases
//!   ([`CLAUDE_ALIASES`], one place), said as such ([`ALIAS_WORDS`]).
//!
//! The brand's default model first; a model a seat here used in the last 30 days says `used`.
//! A missing, broken, too large or odd codex file is not an error: the default alone
//! (`models_source: "default_only"`).

use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};

/// claude's model aliases (its CLI picks the newest of each); a new one is added here only.
pub const CLAUDE_ALIASES: &[&str] = &["fable", "opus", "sonnet", "haiku"];
/// What the page says after an alias (ebab8 05:09 point 1).
pub const ALIAS_WORDS: &str = "aibd 內建別名,不是向帳號查到的";
/// codex's own list, in its home.
pub const CODEX_CACHE: &str = "models_cache.json";
pub const MAX_BYTES: u64 = 1_048_576;
pub const MAX_MODELS: usize = 64;
pub const MAX_ID: usize = 128;
pub const MAX_LABEL: usize = 80;

/// What codex's file gave: `(id, label)` in its order, and when codex fetched it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Codex {
    pub models: Vec<(String, String)>,
    pub fetched_at: Option<String>,
}

#[cfg(unix)]
fn open_plain(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_NONBLOCK)
        .open(p)
}

#[cfg(windows)]
fn open_plain(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::windows::fs::OpenOptionsExt;
    // FILE_FLAG_OPEN_REPARSE_POINT: a link is opened as itself, then refused below
    const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(p)
}

/// Once open: a plain file, not a link (Windows: no reparse point).
fn plain(m: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const REPARSE_POINT: u32 = 0x400;
        if m.file_attributes() & REPARSE_POINT != 0 {
            return false;
        }
    }
    m.is_file() && !m.file_type().is_symlink()
}

/// A label as the page may show it: no control characters, at most [`MAX_LABEL`] characters.
pub fn label_of(s: &str) -> String {
    s.chars()
        .filter(|c| !c.is_control())
        .take(MAX_LABEL)
        .collect()
}

fn id_ok(id: &str) -> bool {
    id.len() <= MAX_ID && aib_seatopen::model_shape(id)
}

/// codex's file, read: `visibility: "list"` entries whose `slug` is a model name.
pub fn parse_codex(bytes: &[u8]) -> Option<Codex> {
    let v: Value = serde_json::from_slice(bytes).ok()?;
    let models = v["models"]
        .as_array()?
        .iter()
        .filter(|m| m["visibility"] == "list")
        .filter_map(|m| {
            let id = m["slug"].as_str().filter(|s| id_ok(s))?;
            let label = m["display_name"]
                .as_str()
                .map_or_else(|| id.to_owned(), label_of);
            Some((id.to_owned(), label))
        })
        .take(MAX_MODELS)
        .collect();
    let fetched_at = v["fetched_at"]
        .as_str()
        .filter(|s| s.len() <= 40 && s.bytes().all(|b| b.is_ascii_graphic()))
        .map(str::to_owned);
    Some(Codex { models, fetched_at })
}

/// When the file was last written, and its size: a read is kept while both stay.
type Stamp = (Option<std::time::SystemTime>, u64);

/// The file's bytes, or nothing (see the module note).
fn read_capped(p: &Path) -> Option<(Vec<u8>, Stamp)> {
    let f = open_plain(p).ok()?;
    let m = f.metadata().ok()?;
    if !plain(&m) {
        return None;
    }
    let mut b = Vec::new();
    f.take(MAX_BYTES + 1).read_to_end(&mut b).ok()?;
    if b.len() as u64 > MAX_BYTES {
        return None;
    }
    Some((b, (m.modified().ok(), m.len())))
}

type Seen = (PathBuf, Stamp, Option<Codex>);
/// The last read, kept while the file's time and size are the same (f163c N1).
static LAST: std::sync::Mutex<Option<Seen>> = std::sync::Mutex::new(None);

/// codex's list in `codex_home` (`None`: no usable list).
pub fn read_codex(codex_home: &Path) -> Option<Codex> {
    let p = codex_home.join(CODEX_CACHE);
    let key = std::fs::symlink_metadata(&p)
        .ok()
        .map(|m| (m.modified().ok(), m.len()));
    let mut last = LAST
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let (Some(k), Some((lp, lk, c))) = (key, last.as_ref()) {
        if *lp == p && *lk == k {
            return c.clone();
        }
    }
    let got = read_capped(&p);
    let c = got.as_ref().and_then(|(b, _)| parse_codex(b));
    *last = got.map(|(_, k)| (p, k, c.clone()));
    c
}

/// The codex home seats use (`CODEX_HOME`, else the seats' HOME `.codex`).
pub fn codex_home() -> Option<PathBuf> {
    std::env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| crate::workspace::seat_login_home().map(|h| h.join(".codex")))
}

/// One brand's list: `(models, models_source)`. `used`: models seats here ran on lately.
pub fn models_of(
    brand: &str,
    default: Option<&str>,
    used: &[String],
    codex: Option<&Codex>,
) -> (Vec<Value>, &'static str) {
    let (found, source): (Vec<(String, String, &str)>, &str) = match brand {
        "claude" => (
            CLAUDE_ALIASES
                .iter()
                .map(|a| ((*a).to_owned(), format!("{a}({ALIAS_WORDS})"), "alias"))
                .collect(),
            "alias",
        ),
        "codex" => match codex {
            Some(c) if !c.models.is_empty() => (
                c.models
                    .iter()
                    .map(|(i, l)| (i.clone(), l.clone(), "codex_cache"))
                    .collect(),
                "codex_cache",
            ),
            _ => (Vec::new(), "default_only"),
        },
        _ => (Vec::new(), "default_only"),
    };
    let mut out: Vec<Value> = Vec::new();
    if let Some(d) = default.filter(|d| id_ok(d)) {
        let mut row = match found.iter().find(|(i, _, _)| i == d) {
            Some((i, l, s)) => json!({"id": i, "label": l, "source": s}),
            None => json!({"id": d, "label": d, "source": "default"}),
        };
        row["default"] = json!(true);
        out.push(row);
    }
    for (i, l, s) in &found {
        if default == Some(i.as_str()) {
            continue;
        }
        out.push(json!({"id": i, "label": l, "source": s}));
    }
    for r in &mut out {
        if r["id"]
            .as_str()
            .is_some_and(|i| used.iter().any(|u| u == i))
        {
            r["used"] = json!(true);
        }
    }
    (out, source)
}

/// Adds `default_model`, `models`, `models_source` (codex: `models_fetched_at`) to every
/// provider row of a version body.
pub fn add_to(
    v: &mut Value,
    defaults: &BTreeMap<String, String>,
    used: &BTreeMap<String, Vec<String>>,
    codex: Option<&Codex>,
) {
    let Some(rows) = v["capabilities"]["providers"].as_array_mut() else {
        return;
    };
    for r in rows.iter_mut() {
        let Some(brand) = r["id"].as_str().map(str::to_owned) else {
            continue;
        };
        let d = defaults.get(&brand).map(String::as_str);
        let none = Vec::new();
        let (models, source) = models_of(&brand, d, used.get(&brand).unwrap_or(&none), codex);
        r["default_model"] = json!(d);
        r["models"] = Value::Array(models);
        r["models_source"] = json!(source);
        if brand == "codex" && source == "codex_cache" {
            r["models_fetched_at"] = json!(codex.and_then(|c| c.fetched_at.clone()));
        }
    }
}

/// Models seats here ran on since `since` (UTC RFC 3339, as aibd writes `updated_at`: the same
/// shape sorts as text), per brand.
pub fn used_lately(metas: &[aib_store::SeatMeta], since: &str) -> BTreeMap<String, Vec<String>> {
    let mut out: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for m in metas {
        let recent = m.updated_at.as_str() >= since;
        if let Some(model) = m.model.as_deref().filter(|x| recent && id_ok(x)) {
            let l = out.entry(m.provider.clone()).or_default();
            if !l.iter().any(|x| x == model) {
                l.push(model.to_owned());
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// The real file's shape (11-23, codex 0.167.0), cut down.
    const CACHE: &str = r#"{"fetched_at": "2026-11-22T19:05:37.425139Z", "etag": "x",
      "client_version": "0.167.0", "identity": "acct",
      "models": [
        {"slug": "gpt-6.2-sol", "display_name": "GPT-6.2 Sol", "visibility": "list", "priority": 1},
        {"slug": "gpt-reserve", "display_name": "Reserve", "visibility": "hide", "priority": 2},
        {"slug": "bad slug", "display_name": "x", "visibility": "list"},
        {"slug": "gpt-6-luna", "display_name": "Luna\u0007 with a very long name that goes on and on and on and on and on and on and on", "visibility": "list"}
      ]}"#;

    fn ids(v: &[Value]) -> Vec<&str> {
        v.iter().map(|r| r["id"].as_str().unwrap()).collect()
    }

    /// A13-1: codex's own listed models, hidden and odd ones left out, labels cut.
    #[test]
    fn codex_lists_its_own_shown_models_only() {
        let c = parse_codex(CACHE.as_bytes()).unwrap();
        let got: Vec<&str> = c.models.iter().map(|(i, _)| i.as_str()).collect();
        assert_eq!(
            got,
            ["gpt-6.2-sol", "gpt-6-luna"],
            "reason: hidden_or_bad_listed"
        );
        let luna = &c.models[1].1;
        assert!(
            luna.chars().count() <= MAX_LABEL && !luna.contains('\u{7}'),
            "{luna}"
        );
        assert_eq!(c.fetched_at.as_deref(), Some("2026-11-22T19:05:37.425139Z"));
        let long = format!(
            r#"{{"models": [{{"slug": "{}", "visibility": "list"}}]}}"#,
            "a".repeat(MAX_ID + 1)
        );
        assert!(
            parse_codex(long.as_bytes()).unwrap().models.is_empty(),
            "reason: long_id_listed"
        );
    }

    /// A13-2: the default first; claude says its list is aibd's aliases; used is marked.
    #[test]
    fn the_default_comes_first_and_aliases_say_so() {
        let (m, s) = models_of("claude", Some("sonnet"), &["opus".into()], None);
        assert_eq!(s, "alias");
        assert_eq!(
            ids(&m),
            ["sonnet", "fable", "opus", "haiku"],
            "reason: default_not_first"
        );
        assert_eq!(m[0]["default"], true);
        assert!(m
            .iter()
            .all(|r| r["source"] == "alias" && r["label"].as_str().unwrap().contains(ALIAS_WORDS)));
        assert_eq!(m[2]["used"], true);
        let c = parse_codex(CACHE.as_bytes()).unwrap();
        let (m, s) = models_of("codex", Some("gpt-6-luna"), &[], Some(&c));
        assert_eq!(
            (ids(&m), s),
            (vec!["gpt-6-luna", "gpt-6.2-sol"], "codex_cache")
        );
    }

    /// A13-3 (ebab8 05:09 point 2): no list, a broken one, or an empty one ⇒ the default alone.
    #[test]
    fn no_codex_list_is_the_default_alone() {
        for c in [None, parse_codex(b"{\"models\": []}").as_ref().cloned()] {
            let (m, s) = models_of("codex", Some("gpt-6-sol"), &[], c.as_ref());
            assert_eq!(
                (ids(&m), s),
                (vec!["gpt-6-sol"], "default_only"),
                "reason: not_default_only"
            );
        }
        assert!(parse_codex(b"{not json").is_none());
        let (m, _) = models_of("codex", None, &[], None);
        assert!(m.is_empty());
    }

    /// A13-4 (f163c L1 L2): a link, a FIFO and a file over the cap give nothing, at once.
    #[cfg(unix)]
    #[test]
    fn a_link_a_fifo_or_a_big_file_gives_nothing() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real.json");
        std::fs::write(&real, CACHE).unwrap();
        assert!(read_capped(&real).is_some());
        let link = d.path().join("link");
        std::fs::create_dir(&link).unwrap();
        std::os::unix::fs::symlink(&real, link.join(CODEX_CACHE)).unwrap();
        assert!(read_codex(&link).is_none(), "reason: link_followed");
        let fifo = d.path().join("fifo");
        std::fs::create_dir(&fifo).unwrap();
        nix::unistd::mkfifo(&fifo.join(CODEX_CACHE), nix::sys::stat::Mode::S_IRWXU).unwrap();
        let t = std::time::Instant::now();
        assert!(read_codex(&fifo).is_none());
        assert!(
            t.elapsed() < std::time::Duration::from_secs(1),
            "reason: fifo_waited"
        );
        let big = d.path().join("big");
        std::fs::create_dir(&big).unwrap();
        let mut b = CACHE.as_bytes().to_vec();
        b.resize(usize::try_from(MAX_BYTES).unwrap() + 1, b' ');
        std::fs::write(big.join(CODEX_CACHE), b).unwrap();
        assert!(read_codex(&big).is_none(), "reason: over_cap_read");
    }

    /// A13-5: the version rows get the three fields; the old ones stay.
    #[test]
    fn every_provider_row_gets_its_models() {
        let mut v = json!({"capabilities": {"providers": [
            {"id": "claude", "open": true, "modes": ["safe"]},
            {"id": "codex", "open": false, "modes": ["safe"], "reason": "not_logged_in"}]}});
        let d: BTreeMap<String, String> = [("claude".into(), "opus".into())].into();
        let c = parse_codex(CACHE.as_bytes()).unwrap();
        add_to(&mut v, &d, &BTreeMap::new(), Some(&c));
        let r = &v["capabilities"]["providers"];
        assert_eq!(r[0]["default_model"], "opus");
        assert_eq!(r[0]["models"][0]["id"], "opus");
        assert_eq!(r[1]["models_source"], "codex_cache");
        assert_eq!(r[1]["models_fetched_at"], "2026-11-22T19:05:37.425139Z");
        assert_eq!(r[1]["reason"], "not_logged_in");
        assert_eq!(r[1]["default_model"], Value::Null);
    }
}
