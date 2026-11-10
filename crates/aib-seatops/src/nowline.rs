//! B10: after a close, refresh the seat's status line time (`aib now`), keeping everything else.
//!
//! `aib now` (aib/src/cockpit/seat_now.py) keeps `<state>/state/seat-now/<seat>.json` (item,
//! steps, state, started_at, at) and `<seat>.line`, the line the status bar prints, built with
//! i18n. Only `at` changes here; the line gets its time swapped only when it ends with the old
//! time exactly once, so no i18n is rebuilt in Rust. Anything unexpected leaves both files as
//! they are and says why; it never changes the outcome of the close.

use serde_json::{json, Value};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

/// seat_now `_stamp`: `MM-DD HH:MM` in local time (the TZ this process runs in, like Python's
/// `time.localtime`). Read from `date(1)` so the crate stays free of `unsafe` (workspace
/// `unsafe_code = "forbid"`; localtime_r is the only libc way). BSD / macOS take `-r EPOCH`;
/// GNU takes `-d @EPOCH` (its `-r` names a file, so the form is picked by the target).
pub fn stamp(epoch: u64) -> Result<String, String> {
    #[cfg(any(target_os = "linux", target_os = "android"))]
    let args = ["-d".to_string(), format!("@{epoch}")];
    #[cfg(not(any(target_os = "linux", target_os = "android")))]
    let args = ["-r".to_string(), epoch.to_string()];
    let out = std::process::Command::new("date")
        .args(args)
        .arg("+%m-%d %H:%M")
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("date: {e}"))?;
    let s = String::from_utf8_lossy(&out.stdout).trim_end().to_string();
    let b = s.as_bytes();
    let shape = b.len() == 11
        && b.iter().enumerate().all(|(i, c)| match i {
            2 => *c == b'-',
            5 => *c == b' ',
            8 => *c == b':',
            _ => c.is_ascii_digit(),
        });
    if out.status.success() && shape {
        Ok(s)
    } else {
        Err(format!("date gave {s:?} for {epoch}"))
    }
}

fn replace(path: &Path, payload: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().unwrap_or(Path::new("."));
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("x");
    let tmp = dir.join(format!(".{name}.tmp.{}", std::process::id()));
    let res = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)?;
        f.write_all(payload)?;
        f.sync_all()?;
        std::fs::rename(&tmp, path)
    })();
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    res
}

/// Refresh `at` of `<state>/state/seat-now/<seat>.json` to `now`, and the time on `.line`.
pub fn refresh(state: &Path, seat: &str, now: u64) -> Result<(), String> {
    let dir = state.join("state").join("seat-now");
    let jpath = dir.join(format!("{seat}.json"));
    let text = std::fs::read_to_string(&jpath).map_err(|_| "no aib now record".to_string())?;
    let mut rec: Value =
        serde_json::from_str(&text).map_err(|_| "the aib now record is not JSON".to_string())?;
    if rec.get("idle").is_some_and(|v| v == &json!(true)) {
        return Err("the seat said it is idle (aib now --clear)".into());
    }
    let old = rec
        .get("at")
        .and_then(Value::as_u64)
        .ok_or("the aib now record has no time")?;
    // both times before anything is written: if `date` fails, neither file changes
    let (old_s, new_s) = (stamp(old)?, stamp(now)?);
    // the folder lock atomic_json takes, so a concurrent `aib now` is not overwritten half-way
    let d = File::open(&dir).map_err(|e| e.to_string())?;
    // flock(LOCK_EX) on the folder, released when `d` is closed
    if d.lock().is_err() {
        return Err("could not lock the seat-now folder".into());
    }
    // re-read inside the lock: `aib now` may have just written
    let text = std::fs::read_to_string(&jpath).map_err(|e| e.to_string())?;
    rec = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    if rec.get("at").and_then(Value::as_u64) != Some(old) {
        return Err("aib now was just updated; left as it is".into());
    }
    rec["at"] = json!(now);
    let mut payload = serde_json::to_string(&rec).map_err(|e| e.to_string())?;
    payload.push('\n');
    replace(&jpath, payload.as_bytes()).map_err(|e| e.to_string())?;
    drop(d);
    let lpath = dir.join(format!("{seat}.line"));
    let line = std::fs::read_to_string(&lpath).map_err(|_| "no status line file".to_string())?;
    let body = line.trim_end_matches('\n');
    if !body.ends_with(&old_s) || body.matches(&old_s).count() != 1 {
        return Err(format!(
            "the status line does not end with its time {old_s}; left as it is"
        ));
    }
    let new_line = format!("{}{}\n", &body[..body.len() - old_s.len()], new_s);
    replace(&lpath, new_line.as_bytes()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup(rec: Value, line: &str) -> (tempfile::TempDir, std::path::PathBuf) {
        let t = tempfile::tempdir().expect("tempdir");
        let d = t.path().join("state").join("seat-now");
        std::fs::create_dir_all(&d).expect("mkdir");
        std::fs::write(d.join("1ced7.json"), rec.to_string()).expect("w");
        std::fs::write(d.join("1ced7.line"), line).expect("w");
        let p = t.path().to_path_buf();
        (t, p)
    }

    fn read(p: &Path, ext: &str) -> String {
        std::fs::read_to_string(
            p.join("state")
                .join("seat-now")
                .join(format!("1ced7.{ext}")),
        )
        .expect("r")
    }

    #[test]
    fn only_the_time_changes() {
        let old = 1_794_311_000;
        let rec = json!({"at": old, "code": "1ced7", "item": "B9 開工", "pct": 20, "started_at": 1_794_274_991,
                         "state": "reply", "steps_done": 1, "steps_total": 5});
        let line = format!(
            "📌【等回應】B9 開工 | 1/5 STEP > 進度:20% | 更新：{}\n",
            stamp(old).expect("stamp")
        );
        let (_t, p) = setup(rec.clone(), &line);
        let now = old + 3600;
        refresh(&p, "1ced7", now).expect("refresh");
        let got: Value = serde_json::from_str(&read(&p, "json")).expect("json");
        let mut want = rec;
        want["at"] = json!(now);
        assert_eq!(got, want);
        assert_eq!(
            read(&p, "line"),
            format!(
                "📌【等回應】B9 開工 | 1/5 STEP > 進度:20% | 更新：{}\n",
                stamp(now).expect("stamp")
            )
        );
    }

    #[test]
    fn anything_unexpected_is_left_alone() {
        let old = 1_794_311_000;
        // the line does not end with the record's time: json is refreshed, line untouched
        let rec = json!({"at": old, "item": "x", "state": "busy"});
        let (_t, p) = setup(rec, "📌【忙碌中】x | 更新：01-01 00:00\n");
        assert!(refresh(&p, "1ced7", old + 60).is_err());
        assert_eq!(read(&p, "line"), "📌【忙碌中】x | 更新：01-01 00:00\n");
        // idle and missing records are not touched
        let (_t, p) = setup(json!({"at": old, "idle": true}), "");
        assert!(refresh(&p, "1ced7", old + 60).is_err());
        assert_eq!(
            read(&p, "json"),
            json!({"at": old, "idle": true}).to_string()
        );
        let t = tempfile::tempdir().expect("tempdir");
        assert!(refresh(t.path(), "1ced7", old).is_err());
    }
}
