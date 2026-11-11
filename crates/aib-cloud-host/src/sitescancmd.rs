// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site scan <domain> [--records dir] [--opened dir]` — a **read-only** scan
//! of one site (E10 category 9; f1fae's map 4.8, gap #1): the official checksums of the
//! site software and of every plugin, runnable and dot files under uploads, PHP files
//! changed in the last [`aib_cloud::scan::RECENT_DAYS`] days, and the administrators
//! against the opening record's first administrators. Judging is
//! [`aib_cloud::scan`]; this file only finds the site and runs the commands.
//!
//! **Nothing is changed.** No cleaning, no quarantine, no file written on the host or in
//! the container. Every command goes through [`read_only`] before it runs: the site
//! software only as `core verify-checksums`, `plugin verify-checksums` or `user list`, as
//! the site's own user with plugins and themes not loaded; `find` only with `-print0`.
//!
//! The site is found by its domain in the site records; the expected administrators come
//! from the opening record (`sites-opened/<code>.json`). A site opened by hand has none:
//! its administrators are unknown, not ok. The report carries the site code and the
//! domain, never the container number or an address; exit status 0 ok, 1 finding,
//! 3 unknown.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use aib_cloud::scan::{self, Check, Seen, WRAP_MERGED, WRAP_QUIET};
use aib_cloud::site::{spec_from_record, SiteSpec};
use serde_json::Value;

use crate::sitecreatecmd::{domain_arg, OPENED};
use crate::siteidentity::{in_ct, wp};

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
/// What one command may print before it is cut: the container is the customer's.
const OUT_MAX: usize = 256 * 1024;
/// The plugin check fetches every plugin's checksums from the vendor of the site software.
const SECONDS: u64 = 300;

/// Runs one command: its standard output and whether it was cut at [`OUT_MAX`].
pub trait Exec {
    fn run(&mut self, argv: &[String]) -> Result<(String, bool), String>;
}

/// The real host.
pub struct HostExec;

impl Exec for HostExec {
    fn run(&mut self, argv: &[String]) -> Result<(String, bool), String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args).env("LC_ALL", "C");
        crate::probe::run_capture_bounded(&mut cmd, Duration::from_secs(SECONDS), OUT_MAX)
    }
}

/// The site's folder inside its container.
pub fn site_root(code: &str) -> String {
    format!("/var/www/{code}")
}

/// What the site software may be asked: its first words after `--path=…`.
const WP_ALLOWED: &[&[&str]] = &[
    &["core", "verify-checksums"],
    &["plugin", "verify-checksums"],
    &["user", "list"],
];
/// `find` actions other than `-print0`.
const FIND_FORBIDDEN: &[&str] = &[
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls",
    "-prune",
];

/// `true` only for the commands this scan is made of: `pct exec <ct> -- sh -c <wrapper> sh`
/// and then either the site software as the site's own user on a reading subcommand, or a
/// `find` that only prints.
pub fn read_only(argv: &[String]) -> bool {
    let a: Vec<&str> = argv.iter().map(String::as_str).collect();
    let ["pct", "exec", ct, "--", "sh", "-c", wrap, "sh", inner @ ..] = a.as_slice() else {
        return false;
    };
    if ct.is_empty()
        || !ct.bytes().all(|b| b.is_ascii_digit())
        || ![WRAP_MERGED, WRAP_QUIET].contains(wrap)
    {
        return false;
    }
    match inner {
        ["find", rest @ ..] => {
            rest.last() == Some(&"-print0") && !rest.iter().any(|x| FIND_FORBIDDEN.contains(x))
        }
        ["sudo", "-u", user, "-H", "/usr/local/bin/wp", path, rest @ ..] => {
            let words: Vec<&str> = rest
                .iter()
                .copied()
                .filter(|w| !w.starts_with("--"))
                .collect();
            **path == format!("--path={}", site_root(user))
                && rest.contains(&"--skip-plugins")
                && rest.contains(&"--skip-themes")
                && WP_ALLOWED.contains(&words.as_slice())
        }
        _ => false,
    }
}

/// `inner` inside the site's container, under `wrap`.
fn wrapped(spec: &SiteSpec, wrap: &str, inner: &[String]) -> Vec<String> {
    let mut parts: Vec<&str> = vec!["sh", "-c", wrap, "sh"];
    parts.extend(inner.iter().map(String::as_str));
    in_ct(spec.ct, &parts)
}

/// The site software as the site's own user, without the `pct exec` around it.
fn wp_inner(spec: &SiteSpec, rest: &[&str]) -> Vec<String> {
    wp(spec, rest).split_off(4)
}

/// The five commands of a scan, in order.
pub fn commands(spec: &SiteSpec) -> [Vec<String>; 5] {
    let root = site_root(&spec.code);
    let quiet = ["--skip-plugins", "--skip-themes"];
    let with = |words: &[&str]| -> Vec<String> {
        let mut w: Vec<&str> = words.to_vec();
        w.extend(quiet);
        wp_inner(spec, &w)
    };
    [
        wrapped(spec, WRAP_MERGED, &with(&["core", "verify-checksums"])),
        wrapped(
            spec,
            WRAP_MERGED,
            &with(&["plugin", "verify-checksums", "--all", "--format=json"]),
        ),
        wrapped(spec, WRAP_QUIET, &scan::uploads_find_args(&root)),
        wrapped(spec, WRAP_QUIET, &scan::recent_find_args(&root)),
        wrapped(
            spec,
            WRAP_QUIET,
            &with(&[
                "user",
                "list",
                "--role=administrator",
                "--fields=user_login",
                "--format=json",
            ]),
        ),
    ]
}

/// Run one command if it only reads. A command that would not only read is an error (the
/// scan stops, nothing ran); one that fails is `None` (that check is unknown).
pub fn look(exec: &mut impl Exec, argv: &[String]) -> Result<Option<(String, bool)>, String> {
    if !read_only(argv) {
        return Err("a scan command that does not only read was refused".to_owned());
    }
    Ok(exec.run(argv).ok())
}

fn as_seen(r: &Option<(String, bool)>) -> Option<Seen<'_>> {
    r.as_ref().map(|(text, cut)| Seen { text, cut: *cut })
}

/// Scan one site: the report and its exit code.
pub fn scan_site(
    exec: &mut impl Exec,
    spec: &SiteSpec,
    domain: &str,
    expected: Option<&[String]>,
) -> Result<(Value, u8), String> {
    let root = site_root(&spec.code);
    let [core, plugins, uploads, recent, admins] = commands(spec);
    let core = look(exec, &core)?;
    let plugins = look(exec, &plugins)?;
    let uploads = look(exec, &uploads)?;
    let recent = look(exec, &recent)?;
    let admins = look(exec, &admins)?;
    let checks: Vec<Check> = vec![
        scan::core_checksums(&root, as_seen(&core)),
        scan::plugin_checksums(&root, as_seen(&plugins)),
        scan::uploads_files(&root, as_seen(&uploads)),
        scan::recent_php(&root, as_seen(&recent)),
        scan::administrators(as_seen(&admins), expected),
    ];
    Ok((
        scan::report(&spec.code, domain, &checks),
        scan::overall(&checks).exit_code(),
    ))
}

/// The one site whose record names `domain`. None, more than one, or a record that names
/// it and cannot be read: refused.
pub fn site_for_domain(records: &[Value], domain: &str) -> Result<SiteSpec, String> {
    let mut hits: Vec<SiteSpec> = Vec::new();
    for rec in records {
        let names = rec.get("hosts").and_then(Value::as_str).unwrap_or("");
        if !names.split(',').any(|h| h == domain) {
            continue;
        }
        let spec = spec_from_record(rec).map_err(|e| format!("the record naming {domain}: {e}"))?;
        hits.push(spec);
    }
    match hits.len() {
        1 => Ok(hits.remove(0)),
        0 => Err(format!("no site record names {domain}")),
        n => Err(format!("{n} site records name {domain}")),
    }
}

fn load_records(dir: &str) -> Result<Vec<Value>, String> {
    let mut out = Vec::new();
    for entry in fs::read_dir(dir).map_err(|e| format!("{dir}: {e}"))? {
        let path = entry.map_err(|e| format!("{dir}: {e}"))?.path();
        if path.extension().and_then(|x| x.to_str()) != Some("json") {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            out.push(v);
        }
    }
    Ok(out)
}

/// The first administrators of the opening record; `None` when there is no readable one.
pub fn expected_admins(opened: &Path, code: &str) -> Option<Vec<String>> {
    let text = fs::read_to_string(opened.join(format!("{code}.json"))).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    v.get("admins")?
        .as_array()?
        .iter()
        .map(|l| l.as_str().map(str::to_owned))
        .collect()
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let domain = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <domain>")?;
    let domain = domain_arg(domain)?;
    let mut records = RECORDS.to_owned();
    let mut opened = OPENED.to_owned();
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--opened" => opened = it.next().ok_or("--opened needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let spec = site_for_domain(&load_records(&records)?, &domain)?;
    let expected = expected_admins(Path::new(&opened), &spec.code);
    let (doc, code) = scan_site(&mut HostExec, &spec, &domain, expected.as_deref())?;
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(code)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::site::Limits;
    use serde_json::json;

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-abc123",
            112,
            12,
            &["shop.example.test"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
    }

    struct Fake<F: FnMut(&[String]) -> Result<(String, bool), String>> {
        seen: Vec<Vec<String>>,
        answer: F,
    }

    impl<F: FnMut(&[String]) -> Result<(String, bool), String>> Exec for Fake<F> {
        fn run(&mut self, argv: &[String]) -> Result<(String, bool), String> {
            self.seen.push(argv.to_vec());
            (self.answer)(argv)
        }
    }

    fn rc0(body: &str) -> Result<(String, bool), String> {
        Ok((format!("{body}\nez2-scan-rc=0\n"), false))
    }

    fn clean(argv: &[String]) -> Result<(String, bool), String> {
        let a = argv.join(" ");
        if a.contains("core verify-checksums") {
            rc0("Success: WordPress installation verifies against checksums.")
        } else if a.contains("plugin verify-checksums") {
            rc0("Success: Verified 2 of 2 plugins.")
        } else if a.contains("user list") {
            rc0("[{\"user_login\":\"ops\"}]")
        } else {
            rc0("")
        }
    }

    #[test]
    fn every_scan_command_only_reads() {
        let s = spec();
        for c in commands(&s) {
            assert!(read_only(&c), "{c:?}");
            assert_eq!(&c[..4], ["pct", "exec", "112", "--"]);
        }
        // the site software runs as the site's own user, on its own folder
        let core = &commands(&s)[0];
        assert_eq!(
            &core[8..14],
            [
                "sudo",
                "-u",
                "s-abc123",
                "-H",
                "/usr/local/bin/wp",
                "--path=/var/www/s-abc123"
            ]
        );
    }

    #[test]
    fn a_command_that_would_change_something_is_refused_before_it_runs() {
        let s = spec();
        let mut bad = commands(&s)[2].clone();
        let at = bad.len() - 1;
        bad.insert(at, "-delete".to_owned());
        let mut wp_bad = commands(&s)[4].clone();
        let at = wp_bad.iter().position(|w| w == "list").unwrap();
        wp_bad[at] = "delete".to_owned();
        let mut other_user = commands(&s)[0].clone();
        other_user[10] = "root".to_owned();
        let mut no_skip_plugins = commands(&s)[1].clone();
        no_skip_plugins.retain(|w| w != "--skip-plugins");
        let mut no_skip_themes = commands(&s)[1].clone();
        no_skip_themes.retain(|w| w != "--skip-themes");
        for argv in [bad, wp_bad, other_user, no_skip_plugins, no_skip_themes] {
            let mut f = Fake {
                seen: Vec::new(),
                answer: clean,
            };
            assert!(look(&mut f, &argv).is_err(), "{argv:?}");
            assert!(f.seen.is_empty());
        }
    }

    #[test]
    fn a_clean_site_is_ok_and_the_report_says_nothing_of_the_host() {
        let s = spec();
        let mut f = Fake {
            seen: Vec::new(),
            answer: clean,
        };
        let expected = ["ops".to_owned()];
        let (doc, code) = scan_site(&mut f, &s, "shop.example.test", Some(&expected)).unwrap();
        assert_eq!((doc["result"].as_str(), code), (Some("ok"), 0));
        assert_eq!(f.seen.len(), 5);
        let text = doc.to_string();
        assert!(
            !text.contains("112") && !text.contains("10.77.") && !text.contains("/var/www"),
            "{text}"
        );
    }

    #[test]
    fn a_command_that_fails_makes_its_check_unknown() {
        let s = spec();
        let mut f = Fake {
            seen: Vec::new(),
            answer: |argv: &[String]| {
                if argv.join(" ").contains("plugin verify-checksums") {
                    Err("\"pct\" exited 255 on ct 112".to_owned())
                } else {
                    clean(argv)
                }
            },
        };
        let (doc, code) =
            scan_site(&mut f, &s, "shop.example.test", Some(&["ops".to_owned()])).unwrap();
        assert_eq!((doc["result"].as_str(), code), (Some("unknown"), 3));
        assert_eq!(doc["checks"][1]["reason"], "not_run");
        assert!(!doc.to_string().contains("112"));
    }

    #[test]
    fn a_planted_file_is_a_finding() {
        let s = spec();
        let mut f = Fake {
            seen: Vec::new(),
            answer: |argv: &[String]| {
                if argv
                    .iter()
                    .any(|a| a == "/var/www/s-abc123/wp-content/uploads")
                {
                    rc0("/var/www/s-abc123/wp-content/uploads/2026/11/x.php\0")
                } else {
                    clean(argv)
                }
            },
        };
        let (doc, code) = scan_site(&mut f, &s, "shop.example.test", None).unwrap();
        assert_eq!((doc["result"].as_str(), code), (Some("finding"), 1));
        assert_eq!(
            doc["checks"][2]["items"][0]["name"],
            "wp-content/uploads/2026/11/x.php"
        );
        // no opening record: the administrators are unknown, the finding still wins
        assert_eq!(doc["checks"][4]["result"], "unknown");
    }

    fn rec(code: &str, ct: u32, hosts: &str) -> Value {
        json!({ "site": code, "ct": ct, "ip": format!("10.77.0.{}", ct - 100), "hosts": hosts })
    }

    #[test]
    fn the_site_is_the_one_record_naming_the_domain() {
        let records = [
            rec("s-aaa111", 111, "a.example.test,www.a.example.test"),
            rec("s-bbb222", 112, "b.example.test"),
        ];
        assert_eq!(
            site_for_domain(&records, "www.a.example.test")
                .unwrap()
                .code,
            "s-aaa111"
        );
        assert!(site_for_domain(&records, "example.test").is_err());
    }

    #[test]
    fn two_records_naming_one_domain_are_refused() {
        let records = [
            rec("s-aaa111", 111, "a.example.test"),
            rec("s-bbb222", 112, "a.example.test"),
        ];
        assert!(site_for_domain(&records, "a.example.test").is_err());
        // a record that names the domain and cannot be read is not passed over
        let broken = [
            json!({ "site": "s-aaa111", "hosts": "a.example.test" }),
            rec("s-bbb222", 112, "a.example.test"),
        ];
        assert!(site_for_domain(&broken, "a.example.test").is_err());
    }

    #[test]
    fn the_expected_administrators_come_from_the_opening_record() {
        let dir = std::env::temp_dir().join(format!("aib-scan-opened-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("s-abc123.json"), r#"{"admins":["ops","ops2"]}"#).unwrap();
        fs::write(dir.join("s-bad000.json"), r#"{"admins":["ops",3]}"#).unwrap();
        assert_eq!(
            expected_admins(&dir, "s-abc123"),
            Some(vec!["ops".to_owned(), "ops2".to_owned()])
        );
        assert_eq!(expected_admins(&dir, "s-bad000"), None);
        assert_eq!(expected_admins(&dir, "s-none00"), None);
        fs::remove_dir_all(&dir).unwrap();
    }
}
