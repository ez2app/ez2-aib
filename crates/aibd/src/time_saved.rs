//! The page's "time saved" boxes (省時雙系統, F2): `POST /api/v1/act {"type":"time_saved"}`.
//!
//! The numbers are the plugin's, not aibd's: aibd finds `aib-skipstop` in the plugin area
//! (`<AIB home>/plugins/aib-skipstop`, [`aib_plugin`]), and only when the plugin is switched on
//! and its executable is the one its `aib-plugin.json` names (sha256) runs
//! `aib-skipstop saved --json --utc-offset <minutes>`, with nothing on its stdin, in its own
//! process group, ended at [`crate::timers::TIME_SAVED_WAIT`].
//!
//! What the page gets is built here from the answer's own numbers and sanitized `recent` event
//! summaries ([`shape`]); raw unvalidated text is never passed through. An answer that is not format
//! `v: 1`, or lacks a number, is `bad_answer`, not a board of zeros: a zero on this board is a
//! claim ("nothing was saved").
//!
//! The answer always has `state`:
//! - `ok`: `saved` holds the five boxes;
//! - `not_installed`, `off`, `not_runnable` (with `why`, a fixed word of [`aib_plugin::No`]),
//!   `unreadable` (the plugin's file), `no_answer` (did not end well in time), `bad_answer`.
//!
//! Read only: it writes nothing, and the one thing the caller chooses is the clock's offset.
//! Any confirmed page may ask (the numbers are not a secret of the repo, and nothing of the
//! caller's is run). One read runs at a time, and an answer is kept for [`KEEP`]: a page that
//! asks again and again gets the kept answer, not a new process each time.

use std::path::{Path, PathBuf};
use std::process::Command;

use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

pub const KIND: &str = "time_saved";
/// The plugin, and its command of the same name.
pub const PLUGIN: &str = "aib-skipstop";
/// The format of `saved --json` this version reads.
pub const FORMAT_V: u64 = 1;
/// The boxes, in the order the page shows them.
pub const BOXES: [&str; 5] = ["today", "week", "month", "year", "all"];

/// The clock the days start on, as minutes east of UTC: what the page says, when it is a whole
/// number a clock can have (UTC-12:00 to UTC+14:00); anything else is UTC.
pub fn offset_min(body: &Value) -> i64 {
    body.get("utc_offset_min")
        .and_then(Value::as_i64)
        .filter(|m| (-720..=840).contains(m))
        .unwrap_or(0)
}

/// The plugin's executable, or the state the page is told and why.
pub fn exe(home: &Path, windows: bool) -> Result<PathBuf, (&'static str, &'static str)> {
    let folder = aib_plugin::plugins_dir(home).join(PLUGIN);
    if !folder.is_dir() {
        return Err(("not_installed", ""));
    }
    let manifest = std::fs::read_to_string(folder.join("aib-plugin.json"))
        .map_err(|_| ("unreadable", "no_file"))
        .and_then(|t| {
            aib_plugin::parse(&t, PLUGIN).map_err(|_| ("unreadable", "not_understood"))
        })?;
    let switches = aib_plugin::read_switches(&aib_plugin::switches_file(home));
    aib_plugin::runnable(&manifest, &folder, &switches, PLUGIN, windows).map_err(|no| match no {
        aib_plugin::No::Off => ("off", ""),
        other => ("not_runnable", other.word()),
    })
}

fn num(o: &Map<String, Value>, k: &str) -> Result<u64, String> {
    o.get(k)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("no number {k}"))
}

/// One sum of the answer (`total`, `skip` or `stop`), every number there.
fn sum(b: &Map<String, Value>, k: &str) -> Result<Value, String> {
    let o = b
        .get(k)
        .and_then(Value::as_object)
        .ok_or_else(|| format!("no {k}"))?;
    Ok(json!({
        "confirmed_ms": num(o, "confirmed_ms")?,
        "estimated_ms": num(o, "estimated_ms")?,
        "penalty_ms": num(o, "penalty_ms")?,
        "count": num(o, "count")?,
    }))
}

fn recent_text(io: &Map<String, Value>, k: &str) -> Result<String, String> {
    let raw = io
        .get(k)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("recent item bad {k}"))?;
    let cleaned = crate::dm_import::clean(raw);
    let trimmed: String = cleaned.trim().chars().take(240).collect();
    if trimmed.is_empty() {
        return Err(format!("recent item empty {k}"));
    }
    Ok(trimmed)
}

/// The page's boxes from what `saved --json` printed. Every number is read as a number and
/// written again; a box or a number that is not there is an error for the whole answer.
pub fn shape(text: &str) -> Result<Value, String> {
    let v = aib_plugin::strict_json(text)?;
    let o = v.as_object().ok_or("not an object")?;
    if o.get("v").and_then(Value::as_u64) != Some(FORMAT_V) {
        return Err("not format v 1".into());
    }
    let mut boxes = Map::new();
    for name in BOXES {
        let b = o
            .get(name)
            .and_then(Value::as_object)
            .ok_or_else(|| format!("no box {name}"))?;
        boxes.insert(
            name.to_string(),
            json!({
                // on the clock: the number to show as time saved
                "clock_ms": num(b, "clock_ms")?,
                // runs that reused something with no full run to compare with: saved, by a
                // time that is not known
                "clock_unknown_runs": num(b, "skip_clock_unknown_runs")?,
                // the skip system's share of clock_ms (the rest is the stops' confirmed time)
                "skip_clock_ms": num(b, "skip_clock_ms")?,
                "total": sum(b, "total")?,
                "skip": sum(b, "skip")?,
                "stop": sum(b, "stop")?,
            }),
        );
    }
    // the earliest saving, as the plugin wrote it: a time, or none yet
    let since = match o.get("since") {
        None | Some(Value::Null) => Value::Null,
        Some(Value::String(s))
            if s.len() <= 40
                && s.chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | ':' | '.' | '+')) =>
        {
            Value::String(s.clone())
        }
        Some(_) => return Err("since is not a time".into()),
    };
    let mut recent = Vec::new();
    match o.get("recent") {
        None | Some(Value::Null) => {}
        Some(arr) => {
            let items = arr.as_array().ok_or("recent is not an array")?;
            for item in items.iter().take(20) {
                let io = item.as_object().ok_or("recent item is not an object")?;
                let at = io
                    .get("at")
                    .and_then(Value::as_str)
                    .filter(|s| {
                        !s.is_empty()
                            && s.len() <= 40
                            && s.chars().all(|c| {
                                c.is_ascii_alphanumeric() || matches!(c, '-' | ':' | '.' | '+')
                            })
                    })
                    .ok_or("recent item bad at")?;
                let system = match io.get("system").and_then(Value::as_str) {
                    Some("shadow") => "shadow",
                    Some("precheck") => "precheck",
                    _ => return Err("recent item bad system".into()),
                };
                let confirmed = io
                    .get("confirmed")
                    .and_then(Value::as_bool)
                    .ok_or("recent item bad confirmed")?;
                let saved_ms = num(io, "saved_ms")?;
                let avoided = recent_text(io, "avoided")?;
                let basis = recent_text(io, "basis")?;
                recent.push(json!({
                    "at": at,
                    "system": system,
                    "confirmed": confirmed,
                    "saved_ms": saved_ms,
                    "avoided": avoided,
                    "basis": basis,
                }));
            }
        }
    }
    Ok(json!({ "v": FORMAT_V, "boxes": boxes, "since": since, "recent": recent }))
}

fn command(exe: &Path, home: &Path, offset: i64) -> Command {
    let mut c = Command::new(exe);
    c.args(["saved", "--json", "--utc-offset", &offset.to_string()])
        .current_dir(home);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // its own group: at the limit the whole group is ended
        c.process_group(0);
    }
    c
}

/// What the page is told, from the plugin area under `home`.
pub fn answer(home: &Path, offset: i64, windows: bool) -> Value {
    let exe = match exe(home, windows) {
        Ok(p) => p,
        Err((state, "")) => return json!({ "state": state }),
        Err((state, why)) => return json!({ "state": state, "why": why }),
    };
    let (rc, out) = crate::gh_view::run_timed(
        command(&exe, home, offset),
        &crate::timers::TIME_SAVED_WAIT,
        None,
    );
    if rc != Some(0) {
        return json!({ "state": "no_answer" });
    }
    match std::str::from_utf8(&out)
        .map_err(|e| e.to_string())
        .and_then(shape)
    {
        Ok(saved) => json!({ "state": "ok", "utc_offset_min": offset, "saved": saved }),
        Err(_) => json!({ "state": "bad_answer" }),
    }
}

fn home_now() -> Option<PathBuf> {
    aib_plugin::home(
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    )
}

/// How long an answer is given again to the same question.
pub const KEEP: std::time::Duration = std::time::Duration::from_secs(5);

type Last = Option<(std::time::Instant, i64, Value)>;

/// The answer kept for a question on the clock `offset`, when it is no older than [`KEEP`].
fn kept(last: &Last, offset: i64, now: std::time::Instant) -> Option<Value> {
    match last {
        Some((at, o, v)) if *o == offset && now.saturating_duration_since(*at) < KEEP => {
            Some(v.clone())
        }
        _ => None,
    }
}

/// One read at a time: a second asker waits for the first and is given its answer.
static LAST: std::sync::Mutex<Last> = std::sync::Mutex::new(None);

/// Drop the cached `time_saved` answer (e.g. after a plugin is switched or installed).
pub fn clear_cache() {
    *LAST
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
}

fn answer_once(home: &Path, offset: i64, windows: bool) -> Value {
    // a poisoned lock only means an earlier read panicked: what it kept is still an answer
    let mut last = LAST
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(v) = kept(&last, offset, std::time::Instant::now()) {
        return v;
    }
    let v = answer(home, offset, windows);
    *last = Some((std::time::Instant::now(), offset, v.clone()));
    v
}

/// The act: the boxes now.
pub async fn page_act(body: Value) -> Response {
    let offset = offset_min(&body);
    let got = tokio::task::spawn_blocking(move || {
        home_now().map(|h| answer_once(&h, offset, cfg!(windows)))
    })
    .await;
    match got {
        Ok(Some(v)) => Json(v).into_response(),
        // no home folder to look in: nothing is installed as far as this daemon can tell
        _ => Json(json!({ "state": "not_installed" })).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SUM: &str = r#"{"confirmed_ms":7,"estimated_ms":3,"penalty_ms":1,"count":2}"#;

    fn a_box(clock: u64, unknown: u64) -> String {
        format!(
            r#"{{"total":{SUM},"skip":{SUM},"stop":{SUM},"clock_ms":{clock},"skip_clock_ms":{clock},"skip_clock_unknown_runs":{unknown}}}"#
        )
    }

    fn said(v: u64, since: &str) -> String {
        format!(
            r#"{{"v":{v},"measure":"unit_time_sum","utc_offset_min":480,"today":{},"week":{},"month":{},"year":{},"all":{},"since":{since},"observed":{{}},"unread":0}}"#,
            a_box(1000, 0),
            a_box(2000, 1),
            a_box(3000, 1),
            a_box(4000, 2),
            a_box(5000, 2)
        )
    }

    #[test]
    fn the_boxes_are_built_from_the_answer_s_numbers_and_only_from_a_whole_answer() {
        let got = shape(&said(1, "\"2026-11-22T01:02:03.004Z\"")).expect("a whole answer");
        assert_eq!(got["v"], 1);
        assert_eq!(got["since"], "2026-11-22T01:02:03.004Z");
        assert_eq!(got["boxes"]["today"]["clock_ms"], 1000);
        assert_eq!(got["boxes"]["all"]["clock_ms"], 5000);
        assert_eq!(got["boxes"]["week"]["clock_unknown_runs"], 1);
        assert_eq!(got["boxes"]["week"]["skip_clock_ms"], 2000);
        assert_eq!(got["boxes"]["year"]["total"]["confirmed_ms"], 7);
        assert_eq!(got["boxes"]["year"]["stop"]["count"], 2);
        // five boxes, and nothing of the answer that was not asked for
        let boxes = got["boxes"].as_object().expect("boxes");
        assert_eq!(
            boxes.keys().map(String::as_str).collect::<Vec<_>>().len(),
            5
        );
        assert!(got.get("observed").is_none() && got.get("measure").is_none());
        // no saving yet: no time
        assert_eq!(
            shape(&said(1, "null")).expect("whole")["since"],
            Value::Null
        );

        // another format, a box that is not there, a number that is text or is not there,
        // a since that is no time, a key twice, no JSON: no board at all
        let whole = said(1, "null");
        for bad in [
            said(2, "null"),
            whole.replace("\"month\":", "\"moon\":"),
            whole.replacen("\"clock_ms\":1000", "\"clock_ms\":\"1000\"", 1),
            whole.replacen("\"clock_ms\":1000,", "", 1),
            whole.replacen(
                "\"skip_clock_unknown_runs\":0",
                "\"skip_clock_unknown_runs\":-1",
                1,
            ),
            whole.replacen("\"count\":2", "\"count\":2.5", 1),
            whole.replacen("\"penalty_ms\":1,", "", 1),
            whole.replacen("\"skip_clock_ms\":1000,", "", 1),
            said(1, "\"<b>now</b>\""),
            said(1, "7"),
            whole.replacen("\"v\":1,", "\"v\":1,\"v\":1,", 1),
            "[]".to_string(),
            String::new(),
        ] {
            assert!(shape(&bad).is_err(), "{bad}");
        }

        // cross-platform recent validation, sanitization, and truncation
        let mut base: Value = serde_json::from_str(&whole).expect("base json");
        assert_eq!(shape(&whole).expect("no recent")["recent"], json!([]));
        base["recent"] = Value::Null;
        assert_eq!(
            shape(&base.to_string()).expect("null recent")["recent"],
            json!([])
        );
        base["recent"] = json!([]);
        assert_eq!(
            shape(&base.to_string()).expect("empty recent")["recent"],
            json!([])
        );

        let long_text = "x".repeat(300);
        base["recent"] = json!([
            {"at": "2026-11-23T06:00:00Z", "system": "precheck", "confirmed": true, "saved_ms": 4000, "avoided": "  \x1b[31mcargo test\x1b[0m  ", "basis": long_text},
            {"at": "2026-11-23T05:59:00Z", "system": "shadow", "confirmed": false, "saved_ms": 2000, "avoided": "aib-ledger", "basis": "est"}
        ]);
        let ok = shape(&base.to_string()).expect("valid recent");
        assert_eq!(ok["recent"][0]["system"], "precheck");
        assert_eq!(ok["recent"][0]["avoided"], "cargo test");
        assert_eq!(ok["recent"][0]["basis"].as_str().unwrap().len(), 240);
        assert_eq!(ok["recent"][1]["system"], "shadow");

        // >20 items truncated to 20
        base["recent"] = Value::Array((0..25).map(|i| json!({
            "at": "2026-11-23T06:00:00Z", "system": "shadow", "confirmed": true, "saved_ms": i, "avoided": "u", "basis": "b"
        })).collect());
        assert_eq!(
            shape(&base.to_string()).expect("20 cap")["recent"]
                .as_array()
                .unwrap()
                .len(),
            20
        );

        let valid_item = json!({"at": "2026-11-23T06:00:00Z", "system": "precheck", "confirmed": true, "saved_ms": 4000, "avoided": "cargo test", "basis": "median"});
        for bad_recent in [
            json!("not_array"),
            json!([123]),
            json!([{ "at": "", "system": "precheck", "confirmed": true, "saved_ms": 1, "avoided": "a", "basis": "b" }]),
            json!([{ "at": "x".repeat(41), "system": "precheck", "confirmed": true, "saved_ms": 1, "avoided": "a", "basis": "b" }]),
            json!([{ "at": "2026 11 23", "system": "precheck", "confirmed": true, "saved_ms": 1, "avoided": "a", "basis": "b" }]),
            {
                let mut x = valid_item.clone();
                x["system"] = json!("unknown");
                json!([x])
            },
            {
                let mut x = valid_item.clone();
                x["confirmed"] = json!("yes");
                json!([x])
            },
            {
                let mut x = valid_item.clone();
                x["saved_ms"] = json!(-1);
                json!([x])
            },
            {
                let mut x = valid_item.clone();
                x["saved_ms"] = json!(1.5);
                json!([x])
            },
            {
                let mut x = valid_item.clone();
                x["avoided"] = json!(" \x1b[31m ");
                json!([x])
            },
            {
                let mut x = valid_item.clone();
                x["basis"] = json!("");
                json!([x])
            },
        ] {
            let mut b = base.clone();
            b["recent"] = bad_recent;
            assert!(
                shape(&b.to_string()).is_err(),
                "expected err for {}",
                b["recent"]
            );
        }
    }

    #[test]
    fn the_clock_is_what_the_page_says_when_a_clock_can_be_that() {
        assert_eq!(offset_min(&json!({"utc_offset_min": 480})), 480);
        assert_eq!(offset_min(&json!({"utc_offset_min": -720})), -720);
        assert_eq!(offset_min(&json!({"utc_offset_min": 840})), 840);
        for odd in [
            json!({"utc_offset_min": 841}),
            json!({"utc_offset_min": -721}),
            json!({"utc_offset_min": "480"}),
            json!({"utc_offset_min": 1.5}),
            json!({}),
        ] {
            assert_eq!(offset_min(&odd), 0, "{odd}");
        }
    }

    #[test]
    fn an_answer_is_given_again_only_to_the_same_question_and_only_while_it_is_new() {
        let at = std::time::Instant::now();
        let ok = json!({"state": "ok"});
        let last = Some((at, 480, ok.clone()));
        assert_eq!(kept(&last, 480, at), Some(ok.clone()));
        let nearly = at + KEEP - std::time::Duration::from_millis(1);
        assert_eq!(kept(&last, 480, nearly), Some(ok));
        // old; another clock; nothing kept
        assert_eq!(kept(&last, 480, at + KEEP), None);
        assert_eq!(kept(&last, 0, at), None);
        assert_eq!(kept(&None, 480, at), None);
    }

    #[cfg(unix)]
    mod with_a_plugin {
        use super::super::*;
        use std::os::unix::fs::PermissionsExt;

        struct Home(PathBuf);
        impl Drop for Home {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }

        /// A plugin area with an `aib-skipstop` that is a script printing `prints`; `sum` is
        /// what its file says of the script (None: the true sum).
        fn home(tag: &str, script: &str, sum: Option<&str>) -> Home {
            let h =
                std::env::temp_dir().join(format!("aibd-time-saved-{tag}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&h);
            let bin = h.join("plugins").join(PLUGIN).join("bin");
            std::fs::create_dir_all(&bin).expect("folders");
            let exe = bin.join(PLUGIN);
            std::fs::write(&exe, script).expect("script");
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).expect("mode");
            let true_sum = aib_plugin::sha256_hex(script.as_bytes());
            let file = json!({
                "schema": aib_plugin::SCHEMA, "name": PLUGIN, "version": "0.2.0",
                "summary": "s", "paid": false, "default": "on",
                "commands": [{"name": PLUGIN, "bin": "bin/aib-skipstop"}],
                "sha256": {"bin/aib-skipstop": sum.unwrap_or(&true_sum)},
            });
            std::fs::write(
                h.join("plugins").join(PLUGIN).join("aib-plugin.json"),
                file.to_string(),
            )
            .expect("file");
            Home(h)
        }

        fn prints(text: &str) -> String {
            format!("#!/bin/sh\ncat <<'EOF_SAVED'\n{text}\nEOF_SAVED\n")
        }

        #[test]
        fn the_plugin_s_numbers_reach_the_page_only_from_the_installed_executable() {
            let whole = super::said(1, "null");
            // installed, on, the sum is the script's: its numbers, and the clock asked for
            let h = home("ok", &prints(&whole), None);
            let a = answer(&h.0, 480, false);
            assert_eq!(
                (a["state"].as_str(), a["utc_offset_min"].as_i64()),
                (Some("ok"), Some(480)),
                "{a}"
            );
            assert_eq!(a["saved"]["boxes"]["all"]["clock_ms"], 5000);

            // the script was changed after it was installed: not run
            let h = home("sum", &prints(&whole), Some(&"0".repeat(64)));
            let a = answer(&h.0, 0, false);
            assert_eq!(
                (a["state"].as_str(), a["why"].as_str()),
                (Some("not_runnable"), Some("sum_differs")),
                "{a}"
            );
            assert!(a.get("saved").is_none());

            // switched off by the user: not run, and said so
            let h = home("off", &prints(&whole), None);
            aib_plugin::set_switch(&aib_plugin::switches_file(&h.0), PLUGIN, false)
                .expect("switch");
            assert_eq!(answer(&h.0, 0, false), json!({"state": "off"}));

            // nothing installed
            let none = Home(
                std::env::temp_dir().join(format!("aibd-time-saved-none-{}", std::process::id())),
            );
            std::fs::create_dir_all(&none.0).expect("home");
            assert_eq!(answer(&none.0, 0, false), json!({"state": "not_installed"}));

            // its file is not understood
            let h = home("file", &prints(&whole), None);
            std::fs::write(
                h.0.join("plugins").join(PLUGIN).join("aib-plugin.json"),
                "{",
            )
            .expect("file");
            assert_eq!(answer(&h.0, 0, false)["state"], "unreadable");

            // it ends badly; it prints something that is no answer: no board, never zeros
            let h = home("rc", "#!/bin/sh\nexit 7\n", None);
            assert_eq!(answer(&h.0, 0, false), json!({"state": "no_answer"}));
            let h = home("junk", &prints("{\"v\":1}"), None);
            assert_eq!(answer(&h.0, 0, false), json!({"state": "bad_answer"}));
        }

        #[test]
        fn the_program_is_asked_for_saved_json_on_the_page_s_clock() {
            // the script prints an answer only when it was asked exactly that
            let whole = super::said(1, "null");
            let script = format!(
                "#!/bin/sh\n[ \"$*\" = \"saved --json --utc-offset -300\" ] || exit 9\ncat <<'EOF_SAVED'\n{whole}\nEOF_SAVED\n"
            );
            let h = home("args", &script, None);
            assert_eq!(answer(&h.0, -300, false)["state"], "ok");
            assert_eq!(answer(&h.0, 480, false)["state"], "no_answer");
        }
    }
}
