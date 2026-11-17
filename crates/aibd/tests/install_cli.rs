//! T97-1m: `aibd install --no-start` / `status` / `uninstall` with the real binary, every
//! place moved into a temporary folder (`--dir`, `--plist-dir`, `AIBD_DATA_DIR`, a test
//! label), so the real aibd and its plist are never touched (IFACE-T97-1m-043dd-1117.md §9).
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

struct T {
    _d: tempfile::TempDir,
    root: PathBuf,
}

impl T {
    fn new() -> T {
        let d = tempfile::tempdir().expect("tempdir");
        let root = d.path().to_path_buf();
        for s in ["bin", "agents", "data"] {
            std::fs::create_dir_all(root.join(s)).expect("mkdir");
        }
        T { _d: d, root }
    }
    fn p(&self, s: &str) -> PathBuf {
        self.root.join(s)
    }
    fn label(&self) -> String {
        format!("app.ez2.aibd.test-{}", std::process::id())
    }
    fn plist(&self) -> PathBuf {
        self.p("agents").join(format!("{}.plist", self.label()))
    }
    fn run(&self, args: &[&str]) -> Output {
        self.run_as(&self.label(), args)
    }
    fn run_as(&self, label: &str, args: &[&str]) -> Output {
        let label = label.to_string();
        let agents = self.p("agents");
        let mut a: Vec<&str> = args.to_vec();
        a.extend([
            "--label",
            &label,
            "--plist-dir",
            agents.to_str().expect("utf8"),
        ]);
        Command::new(env!("CARGO_BIN_EXE_aibd"))
            .args(&a)
            .env("AIBD_DATA_DIR", self.p("data"))
            .output()
            .expect("run aibd")
    }
    fn install(&self, extra: &[&str]) -> Output {
        let bin = self.p("bin");
        let mut a = vec![
            "install",
            "--no-start",
            "--dir",
            bin.to_str().expect("utf8"),
        ];
        a.extend_from_slice(extra);
        self.run(&a)
    }
    fn data_print(&self) -> String {
        let o = self.run(&["status", "--fingerprint"]);
        assert!(o.status.success(), "{o:?}");
        String::from_utf8_lossy(&o.stdout).into_owned()
    }
}

fn text(o: &Output) -> String {
    format!(
        "rc={:?}\n{}{}",
        o.status.code(),
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    )
}

fn plist_text(p: &Path) -> String {
    std::fs::read_to_string(p).expect("plist")
}

#[test]
fn install_status_uninstall_leave_the_data_as_it_was() {
    let t = T::new();
    if !cfg!(target_os = "macos") {
        let o = t.install(&[]);
        assert_eq!(o.status.code(), Some(5), "{}", text(&o));
        return;
    }
    std::fs::create_dir_all(t.p("data/seats")).expect("mkdir");
    std::fs::write(t.p("data/seats/s1.json"), b"{\"seat\":1}").expect("write");
    let before = t.data_print();

    let o = t.install(&[]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert!(t.p("bin/aibd").exists());
    let pl = plist_text(&t.plist());
    assert!(pl.contains("<string>Standard</string>"), "{pl}");
    assert!(
        pl.contains(&format!("<string>{}</string>", t.p("bin/aibd").display())),
        "{pl}"
    );

    let o = t.run(&["status", "--json"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(v["installed"], true);
    assert_eq!(v["exe_ok"], true);
    assert_eq!(v["plist_ok"], true);
    assert_eq!(v["record_from"], "AIBD_DATA_DIR");

    let o = t.run(&["uninstall"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert!(t.p("bin/aibd").exists(), "a preview removes nothing");

    let o = t.run(&["uninstall", "--yes"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert!(!t.p("bin/aibd").exists());
    assert!(!t.plist().exists());
    assert!(!t.p("data/install/record.json").exists());
    assert!(
        String::from_utf8_lossy(&o.stdout).contains("DATA-KEPT"),
        "{}",
        text(&o)
    );
    assert!(t.p("data/seats/s1.json").exists());
    assert_eq!(before, t.data_print(), "the data fingerprint changed");
}

#[test]
fn install_without_no_start_is_refused() {
    let t = T::new();
    let bin = t.p("bin");
    let o = t.run(&["install", "--dir", bin.to_str().expect("utf8")]);
    let want = if cfg!(target_os = "macos") { 2 } else { 5 };
    assert_eq!(o.status.code(), Some(want), "{}", text(&o));
    assert!(!t.p("bin/aibd").exists());
}

#[test]
fn a_label_that_points_elsewhere_is_refused_and_adopt_only_writes_the_record() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let t = T::new();
    std::fs::create_dir_all(t.p("other")).expect("mkdir");
    std::fs::write(t.p("other/aibd"), b"another aibd").expect("write");
    let foreign = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<plist version=\"1.0\">\n<dict>\n\t<key>Label</key>\n\t<string>{}</string>\n\t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>{}</string>\n\t</array>\n\t<key>EnvironmentVariables</key>\n\t<dict>\n\t\t<key>AIB_DRIVE</key>\n\t\t<string>/x/aib-drive</string>\n\t</dict>\n</dict>\n</plist>\n",
        t.label(),
        t.p("other/aibd").display()
    );
    std::fs::write(t.plist(), &foreign).expect("write");

    let o = t.install(&[]);
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    assert!(!t.p("bin/aibd").exists(), "nothing copied");
    assert_eq!(plist_text(&t.plist()), foreign, "plist untouched");

    let o = t.run(&["install", "--no-start", "--adopt"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert_eq!(
        plist_text(&t.plist()),
        foreign,
        "adopt leaves the plist alone"
    );
    assert!(!t.p("bin/aibd").exists());
    let rec = std::fs::read_to_string(t.p("data/install/record.json")).expect("record");
    assert!(rec.contains("\"adopted\": true"), "{rec}");

    let o = t.run(&["uninstall"]);
    assert!(
        String::from_utf8_lossy(&o.stdout).contains("adopted"),
        "{}",
        text(&o)
    );
}

#[test]
fn reinstall_keeps_old_env_keys_unless_unset() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let t = T::new();
    let o = t.install(&[
        "--env",
        "AIB_DRIVE=/x/aib-drive",
        "--env",
        "AIBD_DRIVE_WAKE=1",
    ]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));

    let o = t.install(&[]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let s = String::from_utf8_lossy(&o.stdout).into_owned();
    assert!(s.contains("KEPT-ENV AIB_DRIVE"), "{s}");
    assert!(plist_text(&t.plist()).contains("<string>/x/aib-drive</string>"));

    let o = t.install(&["--unset-env", "AIB_DRIVE"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert!(String::from_utf8_lossy(&o.stdout).contains("UNSET-ENV AIB_DRIVE"));
    let pl = plist_text(&t.plist());
    assert!(!pl.contains("AIB_DRIVE</key>"), "{pl}");
    assert!(pl.contains("AIBD_DRIVE_WAKE"), "{pl}");
}

#[test]
fn another_build_in_the_target_or_the_recorded_place_is_refused() {
    if !cfg!(target_os = "macos") {
        return;
    }
    let t = T::new();
    std::fs::write(t.p("bin/aibd"), b"not this build").expect("write");
    let o = t.install(&[]);
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    assert_eq!(
        std::fs::read(t.p("bin/aibd")).expect("read"),
        b"not this build"
    );

    // installed in bin2, that copy then swapped: moving to bin3 is refused
    let t = T::new();
    let bin2 = t.p("bin2");
    let o = t.run(&[
        "install",
        "--no-start",
        "--dir",
        bin2.to_str().expect("utf8"),
    ]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    std::fs::write(t.p("bin2/aibd"), b"swapped").expect("write");
    let rec = std::fs::read_to_string(t.p("data/install/record.json")).expect("record");
    let rec = rec.replace(
        &sha_of(&std::fs::read(env!("CARGO_BIN_EXE_aibd")).expect("read")),
        &"0".repeat(64),
    );
    std::fs::write(t.p("data/install/record.json"), rec).expect("write");
    let bin3 = t.p("bin3");
    let o = t.run(&[
        "install",
        "--no-start",
        "--dir",
        bin3.to_str().expect("utf8"),
    ]);
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    assert!(!t.p("bin3/aibd").exists());
}

fn sha_of(b: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

#[test]
fn another_label_sharing_the_data_folder_is_refused() {
    // 5d33a M1: one data folder holds one record; a test label must never act on the real one
    if !cfg!(target_os = "macos") {
        return;
    }
    let t = T::new();
    let o = t.install(&[]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let other = format!("{}-other", t.label());

    let o = t.run_as(&other, &["uninstall", "--yes"]);
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    assert!(t.p("bin/aibd").exists(), "the first label's aibd stays");
    assert!(t.plist().exists(), "the first label's plist stays");

    let bin = t.p("bin");
    let o = t.run_as(
        &other,
        &[
            "install",
            "--no-start",
            "--dir",
            bin.to_str().expect("utf8"),
        ],
    );
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    let rec = std::fs::read_to_string(t.p("data/install/record.json")).expect("record");
    assert!(
        rec.contains(&format!("\"label\": \"{}\"", t.label())),
        "{rec}"
    );

    let o = t.run_as(&other, &["status", "--json"]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(v["installed"], false, "{}", text(&o));
    assert_eq!(o.status.code(), Some(1), "{}", text(&o));
}

#[test]
fn an_adopted_install_reads_as_installed_and_uninstall_removes_only_its_two_files() {
    // 5d33a N1: this machine's real aibd goes through --adopt (T97-5m)
    if !cfg!(target_os = "macos") {
        return;
    }
    let t = T::new();
    std::fs::create_dir_all(t.p("other")).expect("mkdir");
    std::fs::write(t.p("other/aibd"), b"the real aibd").expect("write");
    std::fs::create_dir_all(t.p("data/seats")).expect("mkdir");
    std::fs::write(t.p("data/seats/s1.json"), b"{}").expect("write");
    let pl = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<plist version=\"1.0\">\n<dict>\n\t<key>Label</key>\n\t<string>{}</string>\n\t<key>ProgramArguments</key>\n\t<array>\n\t\t<string>{}</string>\n\t</array>\n</dict>\n</plist>\n",
        t.label(),
        t.p("other/aibd").display()
    );
    std::fs::write(t.plist(), &pl).expect("write");
    let o = t.run(&["install", "--no-start", "--adopt"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));

    let o = t.run(&["status", "--json"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(v["installed"], true);
    assert_eq!(v["adopted"], true);
    assert_eq!(v["exe_ok"], true);
    assert_eq!(v["plist_ok"], true);

    let o = t.run(&["uninstall", "--yes"]);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    assert!(!t.p("other/aibd").exists());
    assert!(!t.plist().exists());
    assert!(t.p("data/seats/s1.json").exists(), "data stays");
}
