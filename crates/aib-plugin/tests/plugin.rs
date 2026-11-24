//! The plugin area: what is read as a plugin, when one is run, and that each "no" has its own
//! word. The last test drives the built `aib-plugin`; the plugin it installs is a copy of
//! that same executable, so there is a real program to run on every system.
#![allow(clippy::unwrap_used)]

use aib_plugin::{
    exe_path, find, home, parse, parse_switches, read_switches, runnable, sha256_hex, switched_on,
    switches_text, Manifest, No,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

fn file(sum: Option<&str>) -> Value {
    let mut v = json!({
        "schema": "aib-plugin/v1", "name": "demo", "version": "1.2.3", "summary": "a demo",
        "license": "Apache-2.0", "paid": false, "default": "on",
        "commands": [{"name": "demo", "bin": "bin/demo"}, {"name": "demo-two", "bin": "bin/two"}],
        "something_newer": {"left": "alone"}
    });
    if let Some(s) = sum {
        v["sha256"] = json!({"bin/demo": s});
    }
    v
}

fn manifest(sum: Option<&str>) -> Manifest {
    parse(&file(sum).to_string(), "demo").unwrap()
}

#[test]
fn a_plugin_file_is_read_whole_under_its_own_folders_name_or_not_at_all() {
    let m = manifest(None);
    assert_eq!(
        (m.name.as_str(), m.version.as_str(), m.paid, m.default_on),
        ("demo", "1.2.3", false, true)
    );
    assert_eq!(m.commands.len(), 2);
    assert!(m.sha256.is_empty());
    // the folder it was found in is not its name: it is not that plugin
    assert!(parse(&file(None).to_string(), "other")
        .unwrap_err()
        .contains("folder"));
    let with = |f: &str, v: Value| {
        let mut j = file(None);
        j[f] = v;
        parse(&j.to_string(), "demo")
    };
    assert!(with("schema", json!("aib-plugin/v2"))
        .unwrap_err()
        .contains("schema"));
    assert!(with("paid", json!("no")).is_err());
    assert!(with("default", json!("yes")).is_err());
    assert!(with("commands", json!([])).is_err());
    // an executable outside the plugin's folder
    for bin in [
        "../other/bin/x",
        "/usr/bin/env",
        "bin/../../x",
        "C:/x",
        "bin\\x",
        "",
    ] {
        let r = with("commands", json!([{"name": "demo", "bin": bin}]));
        assert!(r.is_err(), "{bin:?}");
    }
    assert!(with("commands", json!([{"name": "Demo", "bin": "bin/d"}])).is_err());
    let twice = json!([{"name": "demo", "bin": "bin/a"}, {"name": "demo", "bin": "bin/b"}]);
    assert!(with("commands", twice).unwrap_err().contains("twice"));
    assert!(with("sha256", json!({"bin/demo": "abc"})).is_err());
    assert!(with("sha256", json!({"../x": "a".repeat(64)})).is_err());
    assert!(
        with("sha256", json!({"bin/demo": "A".repeat(64)})).is_err(),
        "upper case is not the sum's form"
    );
    for missing in [
        "schema", "name", "version", "summary", "paid", "default", "commands",
    ] {
        let mut j = file(None);
        j.as_object_mut().unwrap().remove(missing);
        assert!(parse(&j.to_string(), "demo").is_err(), "{missing}");
    }
    assert!(parse("[]", "demo").is_err() && parse("nope", "demo").is_err());
    // a key twice: the later one must not quietly win ("paid": true then false would run free)
    let text = file(None).to_string();
    let twice = text.replacen("\"paid\":false", "\"paid\":true,\"paid\":false", 1);
    assert_ne!(twice, text);
    assert!(parse(&twice, "demo").unwrap_err().contains("twice"));
}

#[test]
fn the_switch_is_what_the_user_set_else_the_plugins_default() {
    let on = manifest(None);
    let mut off = on.clone();
    off.default_on = false;
    let none = BTreeMap::new();
    assert!(switched_on(&on, &none) && !switched_on(&off, &none));
    let set = parse_switches(r#"{"demo": "off"}"#).unwrap();
    assert!(!switched_on(&on, &set));
    let set = parse_switches(r#"{"demo": "on", "another": "off"}"#).unwrap();
    assert!(switched_on(&off, &set));
    assert_eq!(parse_switches(&switches_text(&set)).unwrap(), set);
    // a word that is not on / off, a name that is no name, not an object
    assert!(parse_switches(r#"{"demo": true}"#).is_err());
    assert!(parse_switches(r#"{"demo": "enabled"}"#).is_err());
    assert!(parse_switches(r#"{"../demo": "on"}"#).is_err());
    assert!(parse_switches("[]").is_err());
    // off, then on, for one name in one file: an error, not "on"
    let both = r#"{"demo": "off", "demo": "on"}"#;
    assert!(parse_switches(both).unwrap_err().contains("twice"));
}

fn install(dir: &Path, m: &Value, exe: &[u8]) -> PathBuf {
    let folder = dir.join("demo");
    std::fs::create_dir_all(folder.join("bin")).unwrap();
    std::fs::write(folder.join("aib-plugin.json"), m.to_string()).unwrap();
    std::fs::write(folder.join("bin/demo"), exe).unwrap();
    folder
}

#[test]
fn a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word() {
    let d = tempfile::tempdir().unwrap();
    let sum = sha256_hex(b"the installed file");
    let folder = install(d.path(), &file(Some(&sum)), b"the installed file");
    let none: Result<BTreeMap<String, bool>, String> = Ok(BTreeMap::new());
    let m = manifest(Some(&sum));
    // (the path comes back with its links followed)
    assert_eq!(
        runnable(&m, &folder, &none, "demo", false),
        Ok(folder.join("bin/demo").canonicalize().unwrap())
    );

    let no = |m: &Manifest, sw: &Result<BTreeMap<String, bool>, String>, c: &str| {
        runnable(m, &folder, sw, c, false).unwrap_err()
    };
    // switched off by the user
    let off = Ok(parse_switches(r#"{"demo": "off"}"#).unwrap());
    assert_eq!(no(&m, &off, "demo"), No::Off);
    // the switches file cannot be read: nothing is taken as on, whatever the default
    let unread = Err("not JSON".to_string());
    assert_eq!(no(&m, &unread, "demo").word(), "switches_unread");
    // a paid plugin: the licence is not read here, and not known is not allowed
    let mut paid = m.clone();
    paid.paid = true;
    assert_eq!(no(&paid, &none, "demo"), No::PaidNotChecked);
    assert_eq!(no(&m, &none, "nope"), No::NoSuchCommand("nope".into()));
    // a command the file names without a sum
    assert_eq!(no(&m, &none, "demo-two"), No::NoSum("bin/two".into()));
    // installed without sums at all
    assert_eq!(
        no(&manifest(None), &none, "demo"),
        No::NoSum("bin/demo".into())
    );
    // the file on disk is not the one that was installed
    std::fs::write(folder.join("bin/demo"), b"something else").unwrap();
    let n = no(&m, &none, "demo");
    assert_eq!(n.word(), "sum_differs");
    assert!(n.text().contains(&sum[..12]), "{}", n.text());
    std::fs::remove_file(folder.join("bin/demo")).unwrap();
    assert_eq!(no(&m, &none, "demo").word(), "missing");
    // a folder where the executable should be: not a plain file, and it is not read
    std::fs::create_dir(folder.join("bin/demo")).unwrap();
    assert_eq!(no(&m, &none, "demo").word(), "not_a_file");
    std::fs::remove_dir(folder.join("bin/demo")).unwrap();
    // off is said before anything about the file is looked at
    assert_eq!(no(&m, &off, "nope"), No::Off);
    // Windows: .exe is added to the name, whatever ending the name has
    let bin = folder.join("bin");
    assert_eq!(exe_path(&folder, "bin/demo", true), bin.join("demo.exe"));
    assert_eq!(
        exe_path(&folder, "bin/tool.v1", true),
        bin.join("tool.v1.exe")
    );
    assert_eq!(exe_path(&folder, "bin/tool.v1", false), bin.join("tool.v1"));
}

#[test]
fn every_folder_is_listed_and_an_unreadable_one_says_why() {
    let d = tempfile::tempdir().unwrap();
    assert_eq!(find(&d.path().join("no-such-folder")).unwrap(), vec![]);
    install(d.path(), &file(None), b"x");
    std::fs::create_dir_all(d.path().join("broken")).unwrap();
    std::fs::write(d.path().join("broken/aib-plugin.json"), "{").unwrap();
    std::fs::create_dir_all(d.path().join("empty")).unwrap();
    std::fs::write(d.path().join("a-file-not-a-plugin"), "").unwrap();
    let f = find(d.path()).unwrap();
    let names: Vec<&str> = f.iter().map(|x| x.folder.as_str()).collect();
    assert_eq!(names, vec!["broken", "demo", "empty"]);
    assert!(f[0].manifest.as_ref().unwrap_err().contains("not JSON"));
    assert!(f[1].manifest.is_ok());
    assert!(f[2]
        .manifest
        .as_ref()
        .unwrap_err()
        .contains("aib-plugin.json"));
    // the user's switches: no file is none set; a file that is there and broken is an error
    assert_eq!(
        read_switches(&d.path().join("none.json")).unwrap(),
        BTreeMap::new()
    );
    std::fs::write(d.path().join("sw.json"), "{").unwrap();
    assert!(read_switches(&d.path().join("sw.json")).is_err());
    assert_eq!(
        home(Some(" "), Some(Path::new("/h"))),
        Some(PathBuf::from("/h/.ez2-aib"))
    );
    assert_eq!(
        home(Some("/x"), Some(Path::new("/h"))),
        Some(PathBuf::from("/x"))
    );
    assert_eq!(home(None, None), None);
}

#[cfg(unix)]
#[test]
fn an_executable_a_link_leads_out_of_the_folder_is_not_run_whatever_its_sum() {
    let d = tempfile::tempdir().unwrap();
    let outside = d.path().join("elsewhere");
    std::fs::write(&outside, b"a program somewhere else").unwrap();
    let sum = sha256_hex(b"a program somewhere else");
    let folder = install(&d.path().join("plugins"), &file(Some(&sum)), b"x");
    std::fs::remove_file(folder.join("bin/demo")).unwrap();
    std::os::unix::fs::symlink(&outside, folder.join("bin/demo")).unwrap();
    let none: Result<BTreeMap<String, bool>, String> = Ok(BTreeMap::new());
    // the sum is the linked file's own, and it is refused all the same
    let n = runnable(&manifest(Some(&sum)), &folder, &none, "demo", false).unwrap_err();
    assert_eq!(n.word(), "outside", "{n:?}");
    // a link to a file inside the folder is the folder's own file
    std::fs::remove_file(folder.join("bin/demo")).unwrap();
    std::fs::write(folder.join("bin/real"), b"a program somewhere else").unwrap();
    std::os::unix::fs::symlink(folder.join("bin/real"), folder.join("bin/demo")).unwrap();
    assert!(runnable(&manifest(Some(&sum)), &folder, &none, "demo", false).is_ok());
}

#[test]
fn a_switch_is_set_whole_and_a_name_that_is_no_plugin_is_said() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("state/aib-plugin/switches.json");
    aib_plugin::set_switch(&f, "demo", false).unwrap();
    aib_plugin::set_switch(&f, "other", true).unwrap();
    // the second set kept the first
    let set = read_switches(&f).unwrap();
    assert_eq!(
        (set.get("demo"), set.get("other")),
        (Some(&false), Some(&true))
    );
    // no half-written copy is left beside the file (the lock file stays, empty)
    let mut left: Vec<String> = std::fs::read_dir(f.parent().unwrap())
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    left.sort();
    assert_eq!(
        left,
        vec!["switches.json".to_string(), "switches.lock".to_string()]
    );
    // while another holds the lock nothing is changed, and it says so
    let lock = f.parent().unwrap().join("switches.lock");
    let held = aib_ledger::take_lock(&lock, std::time::Duration::from_secs(2)).unwrap();
    assert!(aib_plugin::set_switch(&f, "demo", true)
        .unwrap_err()
        .contains("nothing is changed"));
    assert_eq!(read_switches(&f).unwrap().get("demo"), Some(&false));
    drop(held);
    aib_plugin::set_switch(&f, "demo", true).unwrap();
    assert_eq!(read_switches(&f).unwrap().get("demo"), Some(&true));
    // a broken file is not written over
    std::fs::write(&f, "{").unwrap();
    assert!(aib_plugin::set_switch(&f, "demo", true).is_err());
    assert_eq!(std::fs::read_to_string(&f).unwrap(), "{");
    // a switch for a name that is no installed plugin switches nothing, and is said
    install(d.path(), &file(None), b"x");
    let found = find(d.path()).unwrap();
    let set = parse_switches(r#"{"dmeo": "off", "demo": "on"}"#).unwrap();
    let stray = vec!["dmeo".to_string()];
    assert_eq!(aib_plugin::stray_switches(&set, &found), stray);
    // a folder of that name with no readable plugin file is not an installed plugin either
    std::fs::create_dir_all(d.path().join("dmeo")).unwrap();
    let found = find(d.path()).unwrap();
    assert_eq!(aib_plugin::stray_switches(&set, &found), stray);
    // a switches file that is a link to nothing is a setting that cannot be read
    #[cfg(unix)]
    {
        let link = d.path().join("dangling.json");
        std::os::unix::fs::symlink(d.path().join("gone.json"), &link).unwrap();
        assert!(read_switches(&link).is_err());
    }
}

fn tool(home: &Path, args: &[&str]) -> (i32, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_aib-plugin"))
        .args(args)
        .env("EZ2_AIB_HOME", home)
        .output()
        .unwrap();
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    );
    (o.status.code().unwrap_or(-1), text)
}

#[test]
fn the_tool_lists_switches_and_runs_a_plugin_that_is_the_file_it_was_installed_as() {
    let d = tempfile::tempdir().unwrap();
    let home = d.path();
    assert!(tool(home, &["list"]).1.contains("no plugin under"));
    // the plugin's executable is a copy of this very tool
    let exe = std::fs::read(env!("CARGO_BIN_EXE_aib-plugin")).unwrap();
    let folder = home.join("plugins/demo");
    std::fs::create_dir_all(folder.join("bin")).unwrap();
    let bin = exe_path(&folder, "bin/demo", cfg!(windows));
    std::fs::write(&bin, &exe).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    // installed without a sum: listed, and not run
    std::fs::write(folder.join("aib-plugin.json"), file(None).to_string()).unwrap();
    let (rc, out) = tool(home, &["list", "--json"]);
    assert_eq!(rc, 0, "{out}");
    let v: Value = serde_json::from_str(out.trim()).unwrap();
    assert_eq!(
        (
            v["plugins"][0]["name"].as_str(),
            v["plugins"][0]["runs"].as_bool()
        ),
        (Some("demo"), Some(false))
    );
    assert_eq!(v["plugins"][0]["why_not"], "no_sum");
    let (rc, out) = tool(home, &["run", "demo", "--", "list"]);
    assert_eq!(rc, 3, "{out}");
    assert!(out.contains("not run: no_sum"), "{out}");
    // with the sum of the file that is there: it runs, with its arguments, and its exit code
    // is the tool's (the copy is asked to list: 0; asked nonsense: 2)
    std::fs::write(
        folder.join("aib-plugin.json"),
        file(Some(&sha256_hex(&exe))).to_string(),
    )
    .unwrap();
    let (rc, out) = tool(home, &["run", "demo", "--", "list"]);
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("demo") && out.contains("runs"),
        "the copy listed the same area: {out}"
    );
    assert_eq!(tool(home, &["run", "demo", "demo", "--", "nonsense"]).0, 2);
    // switched off: not run; on again: run
    assert_eq!(tool(home, &["off", "demo"]), (0, "demo off\n".to_string()));
    let (rc, out) = tool(home, &["run", "demo", "--", "list"]);
    assert_eq!(rc, 3, "{out}");
    assert!(out.contains("not run: off"), "{out}");
    assert!(tool(home, &["list"]).1.contains("off"));
    assert_eq!(tool(home, &["on", "demo"]).0, 0);
    assert_eq!(tool(home, &["run", "demo", "--", "list"]).0, 0);
    // the file is changed after it was installed: not run
    let mut other = exe.clone();
    other.extend_from_slice(b"changed");
    std::fs::write(&bin, other).unwrap();
    let (rc, out) = tool(home, &["run", "demo", "--", "list"]);
    assert_eq!(rc, 3, "{out}");
    assert!(out.contains("not run: sum_differs"), "{out}");
    // a plugin that is not there; a switch for one that is not there; a broken switches file
    assert_eq!(tool(home, &["run", "ghost", "--", "x"]).0, 3);
    assert_eq!(tool(home, &["on", "ghost"]).0, 2);
    std::fs::write(home.join("state/aib-plugin/switches.json"), "{").unwrap();
    let (rc, out) = tool(home, &["run", "demo", "--", "list"]);
    assert_eq!(rc, 3, "{out}");
    assert!(out.contains("switches_unread"), "{out}");
    assert_eq!(
        tool(home, &["off", "demo"]).0,
        2,
        "a broken switches file is not written over"
    );
    assert_eq!(tool(home, &["frobnicate"]).0, 2);
}
