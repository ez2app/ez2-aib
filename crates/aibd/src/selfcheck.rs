//! `aibd selfcheck`: what this build can do on this machine, said plainly.
//!
//! Every row is one capability with one status. Nothing is reported as fine
//! that was not checked here, and nothing that only exists as library code is
//! reported as working (a63ab PLAN-rust-preview, 2026-11-08):
//!
//! - process management: library present, **not wired** (no endpoint starts
//!   processes in this version);
//! - Windows Job Object: checked on GitHub's Windows runner only, never on a
//!   real machine ⇒ `unverified`;
//! - macOS lineage (M1): even when its start-up check passes it only adds
//!   candidates, so it is `unverified`, never "clean" (DECISION-23).
//!
//! The exit code is 1 only when a row `failed`; `unverified`, `unsupported`
//! and `not_wired` are honest answers, not failures.

use crate::config::Config;
use crate::lineage::Lineage;

/// One capability's answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    Ok,
    /// Checked here and it failed; the tag is stable (for scripts / metrics).
    Failed(String),
    /// Present, but not proven on this machine (or not provable as clean).
    Unverified,
    /// Does not exist on this platform.
    Unsupported,
    /// Library code exists, nothing in this build uses it yet.
    NotWired,
}

impl Status {
    /// `ok` | `failed:<tag>` | `unverified` | `unsupported` | `not_wired`.
    pub fn label(&self) -> String {
        match self {
            Status::Ok => "ok".into(),
            Status::Failed(tag) => format!("failed:{tag}"),
            Status::Unverified => "unverified".into(),
            Status::Unsupported => "unsupported".into(),
            Status::NotWired => "not_wired".into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub name: &'static str,
    pub status: Status,
    pub detail: String,
    /// Extra facts as key / value (e.g. `wait_mode`), flattened into the JSON.
    pub notes: Vec<(&'static str, String)>,
}

impl Row {
    fn new(name: &'static str, status: Status, detail: impl Into<String>) -> Self {
        Row {
            name,
            status,
            detail: detail.into(),
            notes: Vec::new(),
        }
    }
}

/// `aibd 0.2.0-preview.1 macos-aarch64`
pub fn version_line() -> String {
    format!(
        "aibd {} {}-{}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    )
}

/// All rows, from what was looked up (injected so tests never touch the real
/// environment or start a child).
pub fn rows(config: Result<Config, String>, lineage: &Lineage, os: &str) -> Vec<Row> {
    let mut out = vec![Row::new("version", Status::Ok, version_line())];
    out.push(match config {
        Ok(c) => Row::new(
            "config",
            Status::Ok,
            format!(
                "bind {} (loopback only), data dir {}",
                c.bind,
                c.data_dir.display()
            ),
        ),
        Err(e) => Row::new("config", Status::Failed("config".into()), e),
    });
    out.push(lineage_row(lineage, os));
    out.push(Row::new(
        "process_management",
        Status::NotWired,
        "process tree and group stop are library code; no endpoint starts processes in this version",
    ));
    out.push(if os == "windows" {
        Row::new(
            "windows_job",
            Status::Unverified,
            "Job Object code checked on GitHub's Windows runner only, not on a real machine; not wired",
        )
    } else {
        Row::new("windows_job", Status::Unsupported, "Windows only")
    });
    out
}

fn lineage_row(lineage: &Lineage, os: &str) -> Row {
    if os != "macos" {
        return Row::new("lineage_m1", Status::Unsupported, "macOS only");
    }
    let sc = lineage.selfcheck_status();
    if lineage.enabled() {
        Row::new(
            "lineage_m1",
            Status::Unverified,
            "start-up check passed; lineage only adds candidates and is never reported clean (DECISION-23)",
        )
    } else {
        let tag = sc.strip_prefix("failed:").unwrap_or(sc);
        Row::new(
            "lineage_m1",
            Status::Failed(tag.to_owned()),
            "start-up check failed: lineage is off until restart",
        )
    }
}

/// 1 when any row failed, else 0.
pub fn exit_code(rows: &[Row]) -> u8 {
    u8::from(rows.iter().any(|r| matches!(r.status, Status::Failed(_))))
}

/// One line per row: `name  status  detail [k=v ...]`, then a summary line.
pub fn render_text(rows: &[Row]) -> String {
    let mut s = String::new();
    for r in rows {
        s.push_str(&format!(
            "{:<20} {:<12} {}",
            r.name,
            r.status.label(),
            r.detail
        ));
        for (k, v) in &r.notes {
            s.push_str(&format!(" {k}={v}"));
        }
        s.push('\n');
    }
    let count = |f: fn(&Status) -> bool| rows.iter().filter(|r| f(&r.status)).count();
    s.push_str(&format!(
        "summary: {} ok, {} failed, {} unverified, {} not wired, {} unsupported{}\n",
        count(|s| *s == Status::Ok),
        count(|s| matches!(s, Status::Failed(_))),
        count(|s| *s == Status::Unverified),
        count(|s| *s == Status::NotWired),
        count(|s| *s == Status::Unsupported),
        if count(|s| *s != Status::Ok && *s != Status::Unsupported) > 0 {
            " (not a clean bill: read the rows above)"
        } else {
            ""
        },
    ));
    s
}

/// `[{name, status, detail, ...notes}]`
pub fn render_json(rows: &[Row]) -> serde_json::Value {
    serde_json::Value::Array(
        rows.iter()
            .map(|r| {
                let mut m = serde_json::Map::new();
                m.insert("name".into(), r.name.into());
                m.insert("status".into(), r.status.label().into());
                m.insert("detail".into(), r.detail.clone().into());
                for (k, v) in &r.notes {
                    m.insert((*k).into(), v.clone().into());
                }
                serde_json::Value::Object(m)
            })
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn cfg() -> Result<Config, String> {
        Config::from_lookup(|_| None, Some(PathBuf::from("/tmp/aibd-x"))).map_err(|e| e.to_string())
    }

    fn status_of(rows: &[Row], name: &str) -> String {
        rows.iter()
            .find(|r| r.name == name)
            .map(|r| r.status.label())
            .unwrap_or_default()
    }

    #[test]
    fn macos_lineage_that_passed_is_unverified_never_ok() {
        let l = Lineage::from_selfcheck(
            std::sync::Arc::new(aib_sys::fake::FakeProcessInfo::default()),
            Ok(()),
        );
        let rows = rows(cfg(), &l, "macos");
        assert_eq!(status_of(&rows, "lineage_m1"), "unverified");
        assert_eq!(exit_code(&rows), 0);
        assert!(render_text(&rows).contains("not a clean bill"));
    }

    #[test]
    fn a_failed_lineage_check_is_failed_with_its_tag_and_exit_1() {
        let l = Lineage::disabled("failed:child_ppid");
        let rows = rows(cfg(), &l, "macos");
        assert_eq!(status_of(&rows, "lineage_m1"), "failed:child_ppid");
        assert_eq!(exit_code(&rows), 1);
    }

    #[test]
    fn process_management_is_never_reported_as_working() {
        for os in ["macos", "windows", "linux"] {
            let rows = rows(cfg(), &Lineage::disabled("unsupported"), os);
            assert_eq!(status_of(&rows, "process_management"), "not_wired", "{os}");
        }
    }

    #[test]
    fn windows_job_is_unverified_on_windows_and_unsupported_elsewhere() {
        let l = Lineage::disabled("unsupported");
        assert_eq!(
            status_of(&rows(cfg(), &l, "windows"), "windows_job"),
            "unverified"
        );
        assert_eq!(
            status_of(&rows(cfg(), &l, "windows"), "lineage_m1"),
            "unsupported"
        );
        assert_eq!(
            status_of(&rows(cfg(), &l, "macos"), "windows_job"),
            "unsupported"
        );
    }

    #[test]
    fn a_bad_config_fails_the_check() {
        let bad = Config::from_lookup(
            |k| (k == "AIBD_BIND").then(|| "0.0.0.0:1".to_string()),
            Some(PathBuf::from("/tmp/x")),
        )
        .map_err(|e| e.to_string());
        let rows = rows(bad, &Lineage::disabled("unsupported"), "linux");
        assert_eq!(status_of(&rows, "config"), "failed:config");
        assert_eq!(exit_code(&rows), 1);
    }

    #[test]
    fn json_has_one_object_per_row_with_notes_flattened() {
        let mut rows = rows(cfg(), &Lineage::disabled("unsupported"), "linux");
        rows[0].notes.push(("wait_mode", "poll".into()));
        let j = render_json(&rows);
        let a = j.as_array().expect("array");
        assert_eq!(a.len(), rows.len());
        assert_eq!(a[0]["name"], "version");
        assert_eq!(a[0]["status"], "ok");
        assert_eq!(a[0]["wait_mode"], "poll");
        assert!(a[0]["detail"].as_str().unwrap_or("").starts_with("aibd "));
    }
}
