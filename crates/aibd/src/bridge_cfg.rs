//! N-web-dmbridge switches (519d0 11-19 02:08: a preview aibd's environment is cleared to a short
//! list, and 5d33a's boundary does not take an environment variable as an "on"): one file in
//! aibd's own data folder, `<data>/bridge.json`, read when used.
//!
//! ```json
//! {"dm_mirror": true, "import_state": "/Users/x/.ez2-aib/state",
//!  "legacy_cabin": "20261113-035938-dab9", "person_name": "Andy"}
//! ```
//!
//! - `dm_mirror`: the 1.x ledger mirrored into the message library ([`crate::dm_import::start`],
//!   read once at start).
//! - `import_state`: the ledger's `state` folder (a preview aibd has its own HOME); else
//!   `AIBD_IMPORT_STATE` (a path, not a switch, as a9136's import reads it), else the 1.x rule.
//! - `legacy_cabin`: the page may write letters to this cabin's old-style seats
//!   ([`crate::legacy_send`]); absent ⇒ off.
//! - `person_name`: the name in those letters' sender label; absent ⇒ the 1.x default.
//! - `hub_seat`: the person's mailbox in the 1.x ledger (a9136 11-19 02:12): a five-character
//!   code, not a seat's, that the page's letters come from and old-style seats answer to with
//!   `aib dm <cabin>/<hub_seat>`; its letters pair as `hub`. Absent ⇒ letters come from `hub` and
//!   a seat answers by closing the letter with its answer as `--why`.
//!
//! A missing, unreadable or malformed file is every switch off. Like every local file this is no
//! defence against an AI on the same account (GUIDE-same-user-ai-threat-model): it decides what
//! aibd does, it proves nothing.

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const FILE: &str = "bridge.json";

/// The largest file read.
const MAX: u64 = 16 * 1024;

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Bridge {
    pub dm_mirror: bool,
    pub import_state: Option<PathBuf>,
    pub legacy_cabin: Option<String>,
    pub person_name: Option<String>,
    pub hub_seat: Option<String>,
}

/// A hub code: five of `[0-9a-z]` (the 1.x SEAT_RE takes it as an address).
pub fn hub_ok(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

/// The hub code as it may be used: never one of the cabin's live seats (5d33a REVIEW-dmbridge
/// 5e778fb6 L1: `hub_seat` set to `519d0` put that seat's letters in the person's thread and the
/// page's letters in its name), so a live seat's code counts as not set.
pub fn hub_of(hub: Option<String>, live: &[String]) -> Option<String> {
    hub.filter(|h| !live.iter().any(|s| s == h))
}

/// [`hub_of`] for the file now, with the live seats of its `legacy_cabin` (no cabin: no hub).
pub fn hub_now() -> Option<String> {
    let cfg = now();
    let cabin = cfg.legacy_cabin.clone()?;
    let home = queue_root(&cfg, |k| std::env::var(k).ok())?
        .parent()?
        .parent()?
        .to_path_buf();
    let live = crate::legacy_send::cabin_seats(&home.join("instances"), &cabin);
    hub_of(cfg.hub_seat, &live)
}

fn s_of(v: &Value, k: &str) -> Option<String> {
    v.get(k)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
}

/// `<data>/bridge.json` (every switch off when it is missing or bad).
pub fn load(data_dir: &Path) -> Bridge {
    let p = data_dir.join(FILE);
    let ok = std::fs::metadata(&p).is_ok_and(|m| m.is_file() && m.len() <= MAX);
    let Some(v) = ok
        .then(|| std::fs::read(&p).ok())
        .flatten()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .filter(Value::is_object)
    else {
        return Bridge::default();
    };
    Bridge {
        dm_mirror: v.get("dm_mirror").and_then(Value::as_bool) == Some(true),
        import_state: s_of(&v, "import_state").map(PathBuf::from),
        legacy_cabin: s_of(&v, "legacy_cabin"),
        person_name: s_of(&v, "person_name"),
        hub_seat: s_of(&v, "hub_seat").filter(|h| hub_ok(h)),
    }
}

static DIR: OnceLock<PathBuf> = OnceLock::new();

/// aibd's data folder, set once at start.
pub fn set_dir(data_dir: &Path) {
    let _ = DIR.set(data_dir.to_path_buf());
}

/// The switches now (off before [`set_dir`]).
pub fn now() -> Bridge {
    DIR.get().map(|d| load(d)).unwrap_or_default()
}

/// The ledger's `dm-queue` folder: the file's `import_state`, else `AIBD_IMPORT_STATE`, else the
/// 1.x rule ([`crate::dm_import::queue_root`]).
pub fn queue_root(cfg: &Bridge, env: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    // a9136 e3739f76: the file's folder only when it is one (as the import reads it)
    match cfg.import_state.as_ref().filter(|st| st.is_dir()) {
        Some(st) => Some(st.join("dm-queue")),
        None => crate::dm_import::queue_root(env),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 5d33a REVIEW-dmbridge 5e778fb6 L1: a live seat's code is not the hub.
    #[test]
    fn a_live_seats_code_is_not_the_hub() {
        let live = ["519d0".to_owned(), "7ebca".to_owned()];
        assert_eq!(
            hub_of(Some("519d0".into()), &live),
            None,
            "reason: live_seat_as_hub"
        );
        assert_eq!(
            hub_of(Some("h0b01".into()), &live).as_deref(),
            Some("h0b01")
        );
        assert_eq!(hub_of(None, &live), None);
    }

    #[test]
    fn a_missing_or_bad_file_is_every_switch_off() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(load(d.path()), Bridge::default());
        std::fs::write(d.path().join(FILE), "{nope").unwrap();
        assert_eq!(load(d.path()), Bridge::default(), "reason: bad_file_on");
        std::fs::write(d.path().join(FILE), r#"{"dm_mirror": "yes"}"#).unwrap();
        assert!(!load(d.path()).dm_mirror, "reason: string_is_on");
        std::fs::write(
            d.path().join(FILE),
            r#"{"dm_mirror": true, "import_state": "/s/state", "legacy_cabin": "c1", "person_name": " Andy ", "hub_seat": "h0b01"}"#,
        )
        .unwrap();
        let b = load(d.path());
        assert_eq!(
            b,
            Bridge {
                dm_mirror: true,
                import_state: Some(PathBuf::from("/s/state")),
                legacy_cabin: Some("c1".into()),
                person_name: Some("Andy".into()),
                hub_seat: Some("h0b01".into()),
            }
        );
        let q = queue_root(&b, |k| {
            (k == "AIBD_IMPORT_STATE").then(|| "/env/state".into())
        });
        assert_eq!(
            q,
            Some(Path::new("/env/state").join("dm-queue")),
            "reason: missing_folder_used"
        );
        let st = d.path().join("state");
        std::fs::create_dir_all(&st).unwrap();
        let real = Bridge {
            import_state: Some(st.clone()),
            ..Bridge::default()
        };
        assert_eq!(queue_root(&real, |_| None), Some(st.join("dm-queue")));
        let q = queue_root(&Bridge::default(), |k| {
            (k == "AIBD_IMPORT_STATE").then(|| "/env/state".into())
        });
        assert_eq!(q, Some(Path::new("/env/state").join("dm-queue")));
        std::fs::write(d.path().join(FILE), r#"{"hub_seat": "../x1"}"#).unwrap();
        assert_eq!(load(d.path()).hub_seat, None, "reason: bad_hub_code");
    }
}
