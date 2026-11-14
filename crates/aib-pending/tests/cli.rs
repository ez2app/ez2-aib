//! `aib-pending` 指令列(S81 p-1b):index 寫 INDEX.json、鎖、list / show / patterns。

use std::fs::{self, OpenOptions};
use std::path::Path;
use std::process::{Command, Output};

fn run(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aib-pending"))
        .args(args)
        .arg("--dir")
        .arg(dir)
        .env_remove("EZ2_AIB_HOME")
        .output()
        .expect("test setup")
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn seed(dir: &Path) {
    fs::write(
        dir.join("PLAN-a-b433b-1114.md"),
        "---\nstatus: 定稿\nid: d_aaaaaa\nnode: S81\nowner: b433b\n---\n# a\n",
    )
    .expect("test setup");
    fs::write(
        dir.join("PLAN-b-b433b-1115.md"),
        "---\nstatus: 草稿\nsupersedes: PLAN-a-b433b-1114.md\nnode: S81\nowner: 519d0\n---\n",
    )
    .expect("test setup");
    fs::write(
        dir.join("REVIEW-x-1ced7-abc.md"),
        "結論: 通過\n看了 PLAN-a-b433b-1114.md 與 d_aaaaaa\n",
    )
    .expect("test setup");
    fs::write(
        dir.join("EXCLUDE-pending-519d0-1114.md"),
        "- SIGN-*(Andy 指紋、CONSENT-*\n- tools-a63ab/(工具)\n",
    )
    .expect("test setup");
    fs::create_dir(dir.join("tools-a63ab")).expect("test setup");
    fs::write(
        dir.join("tools-a63ab").join("PLAN-inside.md"),
        "PLAN-a-b433b-1114.md",
    )
    .expect("test setup");
}

#[test]
fn index_writes_rows_refs_chain_and_patterns_and_skips_folders_and_its_own_files() {
    let t = tempfile::tempdir().unwrap();
    seed(t.path());
    let o = run(t.path(), &["index"]);
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    assert!(out(&o).contains("changes null"), "{}", out(&o));
    let v: serde_json::Value =
        serde_json::from_slice(&fs::read(t.path().join("INDEX.json")).unwrap()).unwrap();
    assert!(
        !t.path().join("INDEX.json.tmp").exists(),
        "tmp renamed away"
    );
    let rows = v["rows"].as_array().unwrap();
    let names: Vec<&str> = rows.iter().map(|r| r["name"].as_str().unwrap()).collect();
    assert_eq!(
        names,
        vec![
            "EXCLUDE-pending-519d0-1114.md",
            "PLAN-a-b433b-1114.md",
            "PLAN-b-b433b-1115.md",
            "REVIEW-x-1ced7-abc.md"
        ],
        "no subfolder file, no INDEX.json / INDEX.lock"
    );
    let a = &rows[1];
    assert_eq!(
        a["refs"], 2,
        "REVIEW-x once (by name and id) + PLAN-b's supersedes; the subfolder is not scanned"
    );
    assert_eq!(
        a["superseded_by"],
        serde_json::json!(["PLAN-b-b433b-1115.md"])
    );
    assert_eq!(a["id"], "d_aaaaaa");
    assert_eq!(v["dirs_not_scanned"], 1);
    assert_eq!(v["partial"], false);
    assert_eq!(v["changes"], serde_json::Value::Null);
    assert_eq!(
        v["exclude_patterns"],
        serde_json::json!(["SIGN-*", "CONSENT-*", "tools-a63ab/"])
    );

    // 第二次:changes 對上一份
    fs::write(
        t.path().join("PLAN-b-b433b-1115.md"),
        "---\nstatus: 定稿\nsupersedes: PLAN-a-b433b-1114.md\n---\n",
    )
    .unwrap();
    fs::remove_file(t.path().join("REVIEW-x-1ced7-abc.md")).unwrap();
    let o = run(t.path(), &["index"]);
    assert!(o.status.success());
    assert!(out(&o).contains("changes +0 -1 狀態變 1"), "{}", out(&o));
    let v: serde_json::Value =
        serde_json::from_slice(&fs::read(t.path().join("INDEX.json")).unwrap()).unwrap();
    assert_eq!(
        v["changes"]["status_names"],
        serde_json::json!([{"name": "PLAN-b-b433b-1115.md", "from": "草稿", "to": "定稿"}])
    );
    assert_eq!(
        v["changes"]["removed_names"],
        serde_json::json!(["REVIEW-x-1ced7-abc.md"])
    );
}

#[test]
fn a_held_lock_refuses_a_second_index_and_writes_nothing() {
    let t = tempfile::tempdir().unwrap();
    seed(t.path());
    let held = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(t.path().join("INDEX.lock"))
        .unwrap();
    held.try_lock().unwrap();
    let o = run(t.path(), &["index"]);
    assert_eq!(o.status.code(), Some(4));
    assert!(!t.path().join("INDEX.json").exists());
    drop(held);
    assert!(run(t.path(), &["index"]).status.success());
}

#[test]
fn list_filters_and_show_prints_the_chain_and_where_it_is_read() {
    let t = tempfile::tempdir().unwrap();
    seed(t.path());
    let o = run(t.path(), &["list", "--node", "S81", "--seat", "519d0"]);
    assert!(o.status.success());
    let s = out(&o);
    assert!(
        s.starts_with("PLAN-b-b433b-1115.md\tevolving/PLAN\t草稿\tS81\t519d0\n"),
        "{s}"
    );
    assert!(s.contains("# 1 筆"), "{s}");
    let o = run(t.path(), &["list", "--kind", "review", "--json"]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).unwrap();
    assert_eq!(v["rows"].as_array().unwrap().len(), 1);
    assert!(!t.path().join("INDEX.json").exists(), "list writes nothing");

    let o = run(t.path(), &["show", "d_aaaaaa"]);
    assert!(o.status.success());
    let s = out(&o);
    assert!(s.starts_with("PLAN-a-b433b-1114.md\n"), "{s}");
    assert!(s.contains("取代鏈 舊→ - · 新→ PLAN-b-b433b-1115.md"), "{s}");
    assert!(
        s.contains("被引用 2 處:\n  PLAN-b-b433b-1115.md:3\n  REVIEW-x-1ced7-abc.md:2\n"),
        "{s}"
    );
    assert!(s.contains("只掃了 pending"), "{s}");
    assert_eq!(run(t.path(), &["show", "NOPE.md"]).status.code(), Some(1));
}

#[test]
fn patterns_prints_the_table_and_an_unreadable_table_is_rc_3() {
    let t = tempfile::tempdir().unwrap();
    seed(t.path());
    let o = run(t.path(), &["patterns"]);
    assert!(o.status.success());
    assert!(
        out(&o).starts_with("SIGN-*\nCONSENT-*\ntools-a63ab/\n# 3 條"),
        "{}",
        out(&o)
    );
    fs::write(t.path().join("EXCLUDE-pending-519d0-1114.md"), "# 空\n").unwrap();
    assert_eq!(run(t.path(), &["patterns"]).status.code(), Some(3));
    fs::remove_file(t.path().join("EXCLUDE-pending-519d0-1114.md")).unwrap();
    assert_eq!(run(t.path(), &["patterns"]).status.code(), Some(3));
    assert_eq!(
        run(&t.path().join("missing"), &["list"]).status.code(),
        Some(3)
    );
    assert_eq!(run(t.path(), &["bogus"]).status.code(), Some(64));
}

#[cfg(unix)]
#[test]
fn a_symlink_is_not_followed() {
    let t = tempfile::tempdir().unwrap();
    seed(t.path());
    std::os::unix::fs::symlink(
        t.path().join("PLAN-a-b433b-1114.md"),
        t.path().join("PLAN-link.md"),
    )
    .unwrap();
    let o = run(t.path(), &["list"]);
    assert!(!out(&o).contains("PLAN-link.md"), "{}", out(&o));
}
