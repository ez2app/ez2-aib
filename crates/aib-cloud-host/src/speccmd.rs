// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host spec lint <file>` and `spec plan <file> [--from <step>] [--json]`
//! (4.4.1 §24-4): read a provisioning spec, refuse it with every reason at once or print
//! the ordered plan. Reads one file and prints; nothing is run and no host is contacted.
//!
//! This is the reader `aib_cloud::provision` leaves two jobs to: an object with the same
//! key twice is refused here (a parsed value no longer shows it — the second would
//! silently win), and the file must be UTF-8 JSON of a sane size.

use std::collections::BTreeSet;
use std::fs;
use std::io::Read;

use aib_cloud::provision::{self, Command, Disruption, Kind, Level, Recovery, Step};
use serde_json::Value;

/// Largest spec file read, in bytes. A spec is a few hundred steps of text.
const MAX_BYTES: u64 = 2 * 1024 * 1024;

/// The first key that appears twice in one object of `text`, if any. `text` must already
/// be valid JSON (this walks tokens, it does not validate).
pub fn duplicate_key(text: &str) -> Option<String> {
    // one entry per open container: Some(keys seen, a key is expected next) for an
    // object, None for an array
    let mut stack: Vec<Option<(BTreeSet<String>, bool)>> = Vec::new();
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        match c {
            '{' => stack.push(Some((BTreeSet::new(), true))),
            '[' => stack.push(None),
            '}' | ']' => {
                stack.pop();
            }
            ',' => {
                if let Some(Some((_, expect_key))) = stack.last_mut() {
                    *expect_key = true;
                }
            }
            '"' => {
                let mut raw = String::new();
                while let Some(d) = chars.next() {
                    match d {
                        '"' => break,
                        '\\' => {
                            raw.push(d);
                            if let Some(e) = chars.next() {
                                raw.push(e);
                            }
                        }
                        _ => raw.push(d),
                    }
                }
                if let Some(Some((keys, expect_key))) = stack.last_mut() {
                    if *expect_key {
                        *expect_key = false;
                        // compare what the key means, not how it is escaped
                        let key =
                            serde_json::from_str::<String>(&format!("\"{raw}\"")).unwrap_or(raw);
                        if !keys.insert(key.clone()) {
                            return Some(key);
                        }
                    }
                }
            }
            _ => {}
        }
    }
    None
}

/// Read and parse a spec file: size, UTF-8, JSON, no duplicate keys.
pub fn read_spec(path: &str) -> Result<Value, String> {
    let size = fs::metadata(path)
        .map_err(|e| format!("{path}: {e}"))?
        .len();
    if size > MAX_BYTES {
        return Err(format!(
            "{path}: {size} bytes is larger than a spec ({MAX_BYTES})"
        ));
    }
    // read through a limit as well: the file may have grown since the size was taken
    let mut text = String::new();
    fs::File::open(path)
        .map_err(|e| format!("{path}: {e}"))?
        .take(MAX_BYTES + 1)
        .read_to_string(&mut text)
        .map_err(|e| format!("{path}: {e}"))?;
    if text.len() as u64 > MAX_BYTES {
        return Err(format!("{path}: larger than a spec ({MAX_BYTES} bytes)"));
    }
    let value: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    if let Some(key) = duplicate_key(&text) {
        return Err(format!("{path}: key {key:?} appears twice in one object"));
    }
    Ok(value)
}

/// Text as it is safe to show to whoever approves a plan: control characters are written
/// out as `\u{..}` instead of being sent to the terminal. `provision::lint` refuses them
/// already; this is the second lock on the same door, for a plan that reached here some
/// other way.
pub fn printable(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if c.is_control() {
            out.push_str(&format!("\\u{{{:x}}}", u32::from(c)));
        } else {
            out.push(c);
        }
    }
    out
}

fn command_text(c: &Command) -> String {
    match c {
        Command::Argv(args) => args
            .iter()
            .map(|a| match a {
                provision::Arg::Text(t) => format!("{t:?}"),
                provision::Arg::Secret(n) => format!("<secret:{n}>"),
            })
            .collect::<Vec<_>>()
            .join(" "),
        Command::Shell { sh, why } => format!(
            "sh: {}   (shell because: {})",
            printable(sh),
            printable(why)
        ),
    }
}

/// One step of a plan, for a person: what it is, what it runs, what it destroys, how the
/// connection is affected, how it is checked and the way back.
pub fn step_text(n: usize, s: &Step, assumed: bool) -> String {
    let mut out = String::new();
    let level = match s.level {
        Level::L0 => "L0 read",
        Level::L1 => "L1 write",
        Level::L2 => "L2 APPROVAL",
    };
    let head = if assumed {
        "assumed done — verify first".to_owned()
    } else {
        format!("{n:>3}")
    };
    out.push_str(&format!(
        "{head}  {}  [{level}]  {}\n",
        printable(&s.id),
        printable(&s.title)
    ));
    if assumed {
        for v in &s.verify {
            out.push_str(&format!("       verify  {}\n", command_text(&v.run)));
        }
        return out;
    }
    if let Some(c) = &s.consent {
        out.push_str(&format!("       approver confirms: {}\n", printable(c)));
    }
    if let Some(d) = &s.destroys {
        out.push_str(&format!("       DESTROYS: {}\n", printable(d)));
    }
    match s.disruption {
        Disruption::None => {}
        Disruption::Network {
            reconnect_timeout_s,
            auto_revert_s,
        } => out.push_str(&format!(
            "       cuts the network: wait up to {reconnect_timeout_s}s; the host reverts by itself after {auto_revert_s}s\n"
        )),
        Disruption::Reboot {
            reconnect_timeout_s,
        } => out.push_str(&format!(
            "       reboots: wait up to {reconnect_timeout_s}s\n"
        )),
    }
    if s.detach {
        out.push_str("       runs detached on the host and is polled\n");
    }
    if s.kind == Kind::Manual {
        for i in &s.instructions {
            out.push_str(&format!("       a person: {}\n", printable(i)));
        }
    }
    for c in &s.apply {
        out.push_str(&format!("       run     {}\n", command_text(c)));
    }
    for v in &s.verify {
        let probe = if v.active { " (active probe)" } else { "" };
        out.push_str(&format!("       verify  {}{probe}\n", command_text(&v.run)));
    }
    match &s.recovery {
        Recovery::None => {}
        Recovery::Rollback(run) => {
            for c in run {
                out.push_str(&format!("       undo    {}\n", command_text(c)));
            }
        }
        Recovery::Rescue(path) => out.push_str(&format!(
            "       no rollback — rescue system and a person: {}\n",
            printable(path)
        )),
        Recovery::Unsupported(path) => {
            out.push_str(&format!(
                "       no rollback — recovery path: {}\n",
                printable(path)
            ));
        }
    }
    out
}

/// `spec lint <file>`: 0 accepted, 1 refused (every finding printed).
/// `spec plan <file> [--from <step>] [--json]`: the plan, or the findings.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let (sub, rest) = args
        .split_first()
        .ok_or("spec needs lint <file> | plan <file> [--from <step>] [--json]")?;
    let mut file: Option<&str> = None;
    let mut from: Option<&str> = None;
    let mut as_json = false;
    let mut it = rest.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--from" if sub == "plan" => {
                from = Some(it.next().ok_or("--from needs a step id")?.as_str());
            }
            "--json" if sub == "plan" => as_json = true,
            f if !f.starts_with('-') && file.is_none() => file = Some(f),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if sub != "lint" && sub != "plan" {
        return Err(format!("spec {sub}: lint or plan"));
    }
    let file = file.ok_or("needs a spec file")?;
    let value = read_spec(file)?;
    let spec = match provision::lint(&value) {
        Ok(spec) => spec,
        Err(findings) => {
            for f in &findings {
                eprintln!("{f}");
            }
            eprintln!("{file}: refused, {} finding(s)", findings.len());
            return Ok(1);
        }
    };
    if sub == "lint" {
        eprintln!("{file}: accepted, {} step(s)", spec.steps().len());
        return Ok(0);
    }
    let plan = provision::plan(&spec, from).map_err(|f| f.to_string())?;
    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&plan.to_json()).map_err(|e| e.to_string())?
        );
        return Ok(0);
    }
    println!("plan {}  ({} step(s))", plan.id(), plan.steps().len());
    for s in plan.assumed() {
        print!("{}", step_text(0, s, true));
    }
    for (i, s) in plan.steps().iter().enumerate() {
        print!("{}", step_text(i + 1, s, false));
    }
    let approvals = plan.needs_approval();
    if !approvals.is_empty() {
        println!("needs approval: {}", approvals.join(", "));
    }
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn duplicate_keys_are_found_per_object() {
        assert_eq!(duplicate_key(r#"{"a":1,"b":2}"#), None);
        assert_eq!(duplicate_key(r#"{"a":1,"a":2}"#), Some("a".to_owned()));
        // the same key in two different objects is fine
        assert_eq!(duplicate_key(r#"{"a":{"x":1},"b":{"x":2}}"#), None);
        assert_eq!(duplicate_key(r#"[{"id":"a"},{"id":"a"}]"#), None);
        // nested, after an array, with string values that look like keys
        assert_eq!(
            duplicate_key(r#"{"steps":[{"id":"a","apply":[["echo","id"]],"id":"b"}]}"#),
            Some("id".to_owned())
        );
        assert_eq!(duplicate_key(r#"{"a":"a","b":"a"}"#), None);
        // braces, commas and quotes inside strings do not confuse it
        assert_eq!(duplicate_key(r#"{"a":"},{\"a\":1","b":"x,y"}"#), None);
        // two spellings of one key
        assert_eq!(duplicate_key(r#"{"a":1,"a":2}"#), Some("a".to_owned()));
    }

    fn spec_text(extra: &str) -> String {
        format!(
            r#"{{ "version": 2, "vars": {{ "hostname": "host1" }}, "steps": [
              {{ "id": "B0", "level": "L0", "kind": "check", "title": "host is {{{{hostname}}}}",
                 "verify": [ {{ "run": ["hostname", "-f"], "expect": {{ "eq": "{{{{hostname}}}}" }} }} ] }},
              {{ "id": "B1", "level": "L2", "kind": "run", "title": "reboot", "requires": ["B0"],
                 "apply": [ ["systemctl", "reboot"], ["tailscale", "up", "--authkey", {{ "secret": "ts-key" }}] ],
                 "verify": [ {{ "run": ["uname", "-r"], "expect": {{ "contains": "pve" }} }} ],
                 "recovery": {{ "kind": "rescue", "path": "boot rescue, chroot, fix grub" }},
                 "disruption": {{ "kind": "reboot", "reconnect_timeout_s": 300 }},
                 "consent": "reboot {{{{hostname}}}}"{extra} }}
            ] }}"#
        )
    }

    fn write(name: &str, text: &str) -> String {
        let dir = std::env::temp_dir().join(format!("aib-spec-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        fs::write(&path, text).unwrap();
        path.to_string_lossy().into_owned()
    }

    #[test]
    fn a_good_spec_lints_and_plans() {
        let path = write("good.json", &spec_text(""));
        assert_eq!(cmd(&["lint".to_owned(), path.clone()]), Ok(0));
        assert_eq!(cmd(&["plan".to_owned(), path.clone()]), Ok(0));
        assert_eq!(
            cmd(&["plan".to_owned(), path.clone(), "--json".to_owned()]),
            Ok(0)
        );
        let from = [
            "plan".to_owned(),
            path.clone(),
            "--from".to_owned(),
            "B1".to_owned(),
        ];
        assert_eq!(cmd(&from), Ok(0));
        let nope = [
            "plan".to_owned(),
            path,
            "--from".to_owned(),
            "nope".to_owned(),
        ];
        assert!(cmd(&nope).is_err());
    }

    #[test]
    fn refused_specs_and_files() {
        // lint findings: rc 1, not an error
        let bad = write("bad.json", &spec_text(r#", "detatch": true"#));
        assert_eq!(cmd(&["lint".to_owned(), bad.clone()]), Ok(1));
        assert_eq!(cmd(&["plan".to_owned(), bad]), Ok(1));
        // a duplicate key never reaches lint
        let dup = write("dup.json", &spec_text(r#", "level": "L0""#));
        let err = cmd(&["lint".to_owned(), dup]).unwrap_err();
        assert!(err.contains("appears twice"), "{err}");
        let not_json = write("x.json", "version: 2\n");
        assert!(cmd(&["lint".to_owned(), not_json]).is_err());
        assert!(cmd(&["lint".to_owned(), "/nonexistent/spec.json".to_owned()]).is_err());
        // usage
        assert!(cmd(&[]).is_err());
        assert!(cmd(&["apply".to_owned(), "x".to_owned()]).is_err());
        assert!(cmd(&["lint".to_owned()]).is_err());
        assert!(cmd(&["lint".to_owned(), "a".to_owned(), "--json".to_owned()]).is_err());
    }

    /// f1fae / ebb36 (#625 seg. 5 L1): what the approver sees cannot be repainted.
    #[test]
    fn control_characters_never_reach_the_terminal() {
        assert_eq!(printable("plain 中文 ok"), "plain 中文 ok");
        assert_eq!(printable("a\u{1b}[2Kb\rc"), "a\\u{1b}[2Kb\\u{d}c");
        // a step that got past lint some other way: build it by hand
        let value: Value = serde_json::from_str(&spec_text("")).unwrap();
        let spec = provision::lint(&value).unwrap();
        let plan = provision::plan(&spec, None).unwrap();
        let mut step = plan.steps()[1].clone();
        step.destroys = Some("everything\u{1b}[1A\u{1b}[2K".to_owned());
        step.title = "two\nlines".to_owned();
        let text = step_text(1, &step, false);
        assert!(!text.contains('\u{1b}'), "{text:?}");
        assert!(text.contains("DESTROYS: everything\\u{1b}[1A"));
        assert!(text.contains("two\\u{a}lines"));
    }

    #[test]
    fn a_step_is_printed_with_what_matters() {
        let value: Value = serde_json::from_str(&spec_text("")).unwrap();
        let spec = provision::lint(&value).unwrap();
        let plan = provision::plan(&spec, Some("B1")).unwrap();
        let text = step_text(1, &plan.steps()[0], false);
        for must in [
            "B1",
            "L2 APPROVAL",
            "approver confirms: reboot host1",
            "reboots: wait up to 300s",
            r#"run     "systemctl" "reboot""#,
            "<secret:ts-key>",
            r#"verify  "uname" "-r""#,
            "no rollback — rescue system and a person: boot rescue, chroot, fix grub",
        ] {
            assert!(text.contains(must), "{must:?} not in:\n{text}");
        }
        // the skipped step it depends on is shown as something to verify, not to run
        let assumed = step_text(0, &plan.assumed()[0], true);
        assert!(assumed.contains("assumed done — verify first") && assumed.contains("B0"));
        assert!(assumed.contains(r#"verify  "hostname" "-f""#) && !assumed.contains("run "));
        assert_eq!(json!(plan.needs_approval()), json!(["B1"]));
    }
}
