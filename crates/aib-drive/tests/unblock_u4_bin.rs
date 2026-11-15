//! `aib-drive unblock-u4` end to end (DECISION-council-unblock-519d0-1115 §2 / §5, u-4):
//! it reads only files under the state root and says what it could not read.

use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

const HA: &str = "aaaaaaaa1111111111111111111111111111aaaa";
const HB: &str = "bbbbbbbb2222222222222222222222222222bbbb";

struct Root(PathBuf);

impl Root {
    fn new(tag: &str) -> Root {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let p =
            std::env::temp_dir().join(format!("aib-drive-u4-{tag}-{}-{nanos}", std::process::id()));
        for d in ["pending", "drive", "state/dm-queue/5d33a"] {
            fs::create_dir_all(p.join(d)).expect("mkdir");
        }
        Root(p)
    }
    fn put(&self, rel: &str, body: &str) {
        fs::write(self.0.join(rel), body).expect("write");
    }
    fn run(&self, args: &[&str]) -> (i32, Value) {
        let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
            .arg("unblock-u4")
            .args(args)
            .env("EZ2_AIB_STATE_ROOT", &self.0)
            .output()
            .expect("run aib-drive unblock-u4");
        let out = String::from_utf8_lossy(&o.stdout).into_owned();
        (
            o.status.code().unwrap_or(-1),
            serde_json::from_str(&out).unwrap_or(Value::String(out)),
        )
    }
}

impl Drop for Root {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn now_s() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

fn red(head: &str) -> String {
    format!(
        "== HEAD={head}\nRC test=101\ntest t_one ... FAILED\nthread 't_one' (1) panicked at crates/aibd/tests/s.rs:529:5:\nRC knives=SKIPPED\nHEAD-END {head}\n"
    )
}

fn codes(v: &Value) -> Vec<String> {
    v["rows"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|r| r["code"].as_str().map(str::to_owned))
        .collect()
}

/// Each rule from the files it reads; `--write` keeps the same rows in drive/unblock-src/u-4.json.
#[test]
fn the_three_rules_come_from_the_files_and_write_keeps_them() {
    let r = Root::new("all");
    let now = now_s();
    r.put(
        "drive/slice-sweep.json",
        &json!({"branches": [{"branch": "feat/a-1ced7-1115", "head": HA, "pr": 801}],
                "open_prs": [{"number": 802, "head": HB}]})
        .to_string(),
    );
    r.put("pending/RECEIPT-m1-a-1ced7-aaaaaaaa.out", &red(HA));
    r.put("pending/RECEIPT-m1-b-a9136-bbbbbbbb.out", &red(HB));
    r.put(
        "state/dm-queue/5d33a/q_00000001.json",
        &json!({"id": "q_00000001", "seat": "5d33a", "status": "done",
                "sent_at": now - 3600, "read_at": now - 2400,
                "msg": format!("派工: 審 a {} → 5d33a\n頭 {HA}", &HA[..8])})
        .to_string(),
    );
    r.put(
        "pending/REVIEW-b-5d33a-bbbbbbbb.md",
        &format!("結論: 通過 頭: {HB} H/M/L/N: 0/0/0/0\n"),
    );
    r.put(
        "drive/act-dryrun.jsonl",
        &format!(
            "{}\n",
            json!({"kind": "gone", "at_ms": (now - 60) * 1000, "key": "nudge:q_x:-:c477a", "why": "aib dm stage resolve"})
        ),
    );
    let (rc, v) = r.run(&["--json", "--write"]);
    assert_eq!(rc, 0, "{v}");
    assert_eq!(codes(&v), ["U6", "U11", "U8"], "{v}");
    assert_eq!(v["unreadable"], json!([]), "{v}");
    let kept: Value = serde_json::from_str(
        &fs::read_to_string(r.0.join("drive/unblock-src/u-4.json")).expect("written"),
    )
    .expect("json");
    assert_eq!(kept["rows"], v["rows"]);
    assert_eq!(kept["schema"], "aib-drive.unblock.v2");
    for row in v["rows"].as_array().expect("rows") {
        assert_eq!(row["source"], "u-4", "{row}");
        assert!(
            row["as_of_ms"].as_u64().is_some() && row.get("blocks").is_none(),
            "{row}"
        );
        let id = row["id"].as_str().expect("id");
        assert_eq!(
            id.split_once(':').map(|(c, _)| c),
            row["code"].as_str(),
            "{row}"
        );
    }
    // a second write keeps when each row was first seen
    let (_, again) = r.run(&["--json", "--write"]);
    let since = |v: &Value| {
        v["rows"]
            .as_array()
            .map(|a| a.iter().map(|r| r["since_ms"].clone()).collect::<Vec<_>>())
    };
    assert_eq!(since(&again), since(&v));
}

/// U21: no cache and no reviews are said, not taken for "no blocker"; a dispatch is not
/// judged untaken when the reviews cannot be read.
#[test]
fn what_cannot_be_read_is_listed_not_taken_for_no_blocker() {
    let r = Root::new("missing");
    let now = now_s();
    fs::remove_dir_all(r.0.join("pending")).expect("rm pending");
    r.put(
        "state/dm-queue/5d33a/q_00000001.json",
        &json!({"id": "q_00000001", "seat": "5d33a", "status": "done",
                "sent_at": now - 3600, "read_at": now - 2400,
                "msg": format!("派工: 審 a → 5d33a\n頭 {HA}")})
        .to_string(),
    );
    let (rc, v) = r.run(&["--json"]);
    assert_eq!(rc, 0, "{v}");
    assert!(codes(&v).is_empty(), "{v}");
    // IFACE v2 r3: [{name, why, covers}]; a row of a covered code that went away is unknown
    let mut covered: Vec<&str> = Vec::new();
    for u in v["unreadable"].as_array().expect("unreadable") {
        assert!(u["name"].is_string() && u["why"].is_string(), "{u}");
        let c = u["covers"].as_array().expect("covers");
        assert!(!c.is_empty(), "{u}");
        covered.extend(c.iter().filter_map(Value::as_str));
    }
    for code in ["U6", "U11"] {
        assert!(covered.contains(&code), "{code} covered: {v}");
    }
    assert!(!covered.contains(&"U8"), "act's journal was read: {v}");
    let un = v["unreadable"].to_string();
    for want in ["slice-sweep.json", "RECEIPT", "REVIEW"] {
        assert!(un.contains(want), "{want}: {v}");
    }
    assert!(!r.0.join("drive/unblock-src/u-4.json").exists());
    let (rc, _) = r.run(&["--bogus"]);
    assert_eq!(rc, 2);
}
