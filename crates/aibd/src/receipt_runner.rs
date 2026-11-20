//! N-webonly 第 4 件, the half behind [`crate::receipt_run::ReceiptRunner`] (b4297 11-20;
//! DESIGN-g1-receipt-origacct-b4297-1120 r3, cut with 7ebca 21:35). Slice R1: what is decided
//! before anything leaves this machine, as pure functions. Nothing here runs git, opens a
//! connection or raises a window; the runner that does (slices R3, R4) calls these.
//!
//! - [`SWITCH`]: default off. Without `AIBD_RECEIPT_RUN=1` the runner is never set and every
//!   request stays `no_host` ([`crate::receipt_run::NoRunner`]).
//! - [`pick`]: the machine. Only one of the pinned list ([`crate::trusted_hosts`]) whose `os`
//!   is `mac`: the test machine runs the tree under `sandbox-exec`, and there is no such
//!   thing on Windows yet (design r3 §8: Windows results are GitHub CI's).
//! - [`brings`]: what the head would run at build time that its base does not, read from the
//!   changed paths and the two `Cargo.lock`s. A head that touches which toolchain or cargo
//!   settings are used, or takes a crate from anywhere but crates.io, is `tree_refused` here,
//!   before a card is raised; the gate on the machine checks the same again with its own
//!   copy of the rule ([`aib_receipt_gate::sandbox`]), because this side is a display to it.
//! - [`owner_ask`]: the owner's fingerprint for one run, bound to {machine, bundle sha256,
//!   run id, knife table sha256} (design r3 §2) and to what [`brings`] found, so a ticket
//!   approves this very bundle going to this very machine. Each run asks; there is no
//!   "same seat, same day" pass (5d33a PREREAD-g1origacct L1).
//! - [`run_words`] / [`read_words`] / [`cancel_words`]: the only three lines aibd says to a
//!   machine. Each is parsed back with the gate's own reader before it is returned, so a
//!   line this side would send is one the gate takes.
//! - [`answer`]: the gate's first line, as one of the fixed reason codes a page may be told.
//!
//! First version: the whole workspace, no knives (`KNIVES-NOT-RUN`; knives are CI's). A
//! narrow request is refused `receipt_not_allowed`: it can never be green
//! ([`crate::receipt_check`]) and would spend a fingerprint and a machine for nothing.

use crate::trusted_hosts::TrustedHost;
use aib_receipt_gate::sandbox::{lock_ok, path_refused};
use serde_json::json;
use std::collections::BTreeSet;

/// `AIBD_RECEIPT_RUN=1` sets the runner at start-up; anything else leaves `no_host`.
pub const SWITCH: &str = "AIBD_RECEIPT_RUN";
/// The owner_ok operation of one run.
pub const OP: &str = "receipt.run";
/// SHA-256 of no bytes: the knife table of a run that sends none.
pub const NO_KNIVES_SHA256: &str =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
/// At most this many names of one kind go into a card or a page.
pub const NAMED_MAX: usize = 12;

pub fn switch_on(v: Option<String>) -> bool {
    v.as_deref() == Some("1")
}

/// The machine a receipt goes to: the first of the pinned list that is a Mac.
pub fn pick(hosts: &[TrustedHost]) -> Option<&TrustedHost> {
    hosts.iter().find(|h| h.os == "mac")
}

/// What a head would run at build time that its base does not.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct Brings {
    /// `build.rs` files added or changed (they run at build, as the machine's own account).
    pub build_scripts: Vec<String>,
    /// `Cargo.toml` files added or changed.
    pub manifests: Vec<String>,
    /// `name version` of packages `Cargo.lock` lists that the base's did not (their build
    /// scripts and proc-macros run too).
    pub new_packages: Vec<String>,
    /// Files added under a `tests` or `src` folder.
    pub new_code_files: usize,
    /// Every path the head adds, changes or removes.
    pub files: usize,
}

impl Brings {
    /// Whether the card says so in red: something new runs at build time.
    pub fn runs_new_code_at_build(&self) -> bool {
        !self.build_scripts.is_empty() || !self.new_packages.is_empty()
    }
}

fn base_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// `name version` of every package of a `Cargo.lock`.
pub fn packages(lock: &str) -> BTreeSet<String> {
    let value = |line: &str, key: &str| -> Option<String> {
        let v = line
            .strip_prefix(key)?
            .trim_start()
            .strip_prefix('=')?
            .trim();
        Some(v.strip_prefix('"')?.strip_suffix('"')?.to_owned())
    };
    let mut out = BTreeSet::new();
    let mut name: Option<String> = None;
    for line in lock.lines().map(str::trim) {
        if line == "[[package]]" {
            name = None;
        } else if let Some(n) = value(line, "name") {
            name = Some(n);
        } else if let Some(v) = value(line, "version") {
            // the version line of a package follows its name; any other is not one
            if let Some(n) = name.take() {
                out.insert(format!("{n} {v}"));
            }
        }
    }
    out
}

/// What `changed` (git's `(status letter, path)` of the head against its base: `A`, `M`,
/// `D`, …; a rename read as a removal and an addition) and the two lock files bring.
/// `tree_refused`: a changed path the gate refuses, or a lock that names a source other
/// than crates.io.
pub fn brings(
    changed: &[(String, String)],
    base_lock: &str,
    head_lock: &str,
) -> Result<Brings, &'static str> {
    if changed.iter().any(|(_, p)| path_refused(p)) || !lock_ok(head_lock) {
        return Err("tree_refused");
    }
    let kept = |(status, _): &&(String, String)| status != "D";
    let named = |file: &str| -> Vec<String> {
        changed
            .iter()
            .filter(kept)
            .filter(|(_, p)| base_name(p) == file)
            .map(|(_, p)| p.clone())
            .collect()
    };
    let had = packages(base_lock);
    let new_code_files = changed
        .iter()
        .filter(|(status, p)| {
            status == "A"
                && p.ends_with(".rs")
                && p.split('/').any(|part| part == "tests" || part == "src")
        })
        .count();
    Ok(Brings {
        build_scripts: named("build.rs"),
        manifests: named("Cargo.toml"),
        new_packages: packages(head_lock).difference(&had).cloned().collect(),
        new_code_files,
        files: changed
            .iter()
            .map(|(_, p)| p)
            .collect::<BTreeSet<_>>()
            .len(),
    })
}

/// How much of a machine's name the window shows (the whole name is in the binding).
pub const HOST_SHOWN: usize = 16;

/// The few words the fingerprint window shows of one run, all of them inside the 80
/// characters it keeps ([`crate::owner_ok::RESOURCE_MAX`]): a longer name or a larger count
/// is cut here, never the front of the line. Only aibd's words, the run id and the machine's
/// pinned name; no path or package name a seat chose.
pub fn card_words(host: &str, run_id: &str, b: &Brings) -> String {
    let red = if b.runs_new_code_at_build() {
        "會跑新程式:"
    } else {
        ""
    };
    let host: String = host.chars().take(HOST_SHOWN).collect();
    format!(
        "收據 {run_id} 到 {host}·{red}build.rs {}·新套件 {}·新檔 {}",
        b.build_scripts.len().min(999),
        b.new_packages.len().min(999),
        b.new_code_files.min(9999),
    )
}

/// What one run is, for the owner's fingerprint: everything [`owner_ask`] binds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sent<'a> {
    /// The machine's pinned name.
    pub host: &'a str,
    pub run_id: &'a str,
    pub head: &'a str,
    /// The base the bundle is made against.
    pub base: &'a str,
    pub bundle_sha256: &'a str,
    pub knives_sha256: &'a str,
}

/// The fingerprint one run asks. `by` is the run's own name for the ticket (`receipt:<run
/// id>`): the runner asks, no page resends.
pub fn owner_ask(s: &Sent<'_>, b: &Brings, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "host": s.host, "run_id": s.run_id, "head": s.head, "base": s.base,
        "bundle_sha256": s.bundle_sha256, "knives_sha256": s.knives_sha256,
        "build_scripts": b.build_scripts, "manifests": b.manifests,
        "new_packages": b.new_packages, "new_code_files": b.new_code_files, "files": b.files,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: card_words(s.host, s.run_id, b),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// What a page shows of a run that waits for its owner: counts, and the first names of each
/// kind (they are paths and package names of the seat's own tree, shown as text).
pub fn page_card(host: &str, b: &Brings) -> serde_json::Value {
    let first = |v: &[String]| -> Vec<String> { v.iter().take(NAMED_MAX).cloned().collect() };
    json!({
        "host": crate::receipt_run::nickname(host),
        "red": b.runs_new_code_at_build(),
        "build_scripts": first(&b.build_scripts), "build_scripts_n": b.build_scripts.len(),
        "manifests_n": b.manifests.len(),
        "new_packages": first(&b.new_packages), "new_packages_n": b.new_packages.len(),
        "new_code_files": b.new_code_files, "files": b.files,
    })
}

fn checked(line: String) -> Result<String, &'static str> {
    aib_receipt_gate::parse(Some(&line)).map(|_| line)
}

/// The line that starts a run: the whole workspace, no knives.
pub fn run_words(
    run_id: &str,
    head: &str,
    bundle_len: u64,
    bundle_sha256: &str,
) -> Result<String, &'static str> {
    checked(format!(
        "run {run_id} {head} ws {bundle_len} {bundle_sha256} 0 {NO_KNIVES_SHA256}"
    ))
}

/// The line that asks for a run's `.out`.
pub fn read_words(run_id: &str) -> Result<String, &'static str> {
    checked(format!("fetch {run_id}"))
}

/// The line that withdraws a run.
pub fn cancel_words(run_id: &str) -> Result<String, &'static str> {
    checked(format!("cancel {run_id}"))
}

/// The gate's answer to `verb`, from the first line it printed: `Ok` for `OK <verb>`, else
/// the reason as a page may be told it (one of [`crate::receipt_run::REFUSALS`]). A line
/// that is neither, or no line (the connection ended), is `lost`.
pub fn answer(stdout: &[u8], verb: &str) -> Result<(), &'static str> {
    let first = stdout.split(|b| *b == b'\n').next().unwrap_or_default();
    let Ok(first) = std::str::from_utf8(first) else {
        return Err("lost");
    };
    let first = first.trim_end_matches('\r');
    if first.strip_prefix("OK ") == Some(verb) {
        return Ok(());
    }
    Err(match first.strip_prefix("ERR ") {
        Some("receipt_tool_changed") => "receipt_tool_changed",
        Some("host_changed" | "gate_writable") => "host_changed",
        Some("disk_low") => "disk_low",
        Some("toolchain_changed" | "no_toolchain") => "toolchain_changed",
        Some("busy" | "no_bash" | "not_ready" | "no_sandbox") => "no_host",
        Some(_) => "tree_refused",
        None => "lost",
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const HEAD: &str = "0123456789abcdef0123456789abcdef01234567";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const IO: &str = "registry+https://github.com/rust-lang/crates.io-index";

    fn host(name: &str, os: &str) -> TrustedHost {
        TrustedHost {
            name: name.into(),
            hostname: "192.0.2.10".into(),
            port: 22,
            user: "owner".into(),
            hostkey: "ssh-ed25519 AAAA".into(),
            os: os.into(),
        }
    }

    fn lock(packages: &[(&str, &str)]) -> String {
        let mut s = String::from("version = 4\n");
        for (n, v) in packages {
            s.push_str(&format!(
                "\n[[package]]\nname = \"{n}\"\nversion = \"{v}\"\nsource = \"{IO}\"\nchecksum = \"x\"\n"
            ));
        }
        s.push_str("\n[[package]]\nname = \"aibd\"\nversion = \"0.2.0\"\ndependencies = [\n \"sha2\",\n]\n");
        s
    }

    fn ch(list: &[(&str, &str)]) -> Vec<(String, String)> {
        list.iter()
            .map(|(s, p)| ((*s).to_owned(), (*p).to_owned()))
            .collect()
    }

    #[test]
    fn the_switch_is_off_until_it_is_one() {
        assert!(switch_on(Some("1".into())));
        for off in [
            None,
            Some(String::new()),
            Some("0".into()),
            Some("true".into()),
        ] {
            assert!(!switch_on(off.clone()), "reason: on for {off:?}");
        }
    }

    #[test]
    fn a_receipt_goes_to_a_pinned_mac_or_nowhere() {
        let hosts = [
            host("win", "windows"),
            host("lin", "mac"),
            host("mini", "mac"),
        ];
        assert_eq!(pick(&hosts).map(|h| h.name.as_str()), Some("lin"));
        assert!(
            pick(&[host("win", "windows"), host("box", "linux")]).is_none(),
            "reason: no_sandbox_machine_picked"
        );
        assert!(pick(&[]).is_none());
    }

    #[test]
    fn a_lock_lists_its_packages_by_name_and_version() {
        let l = lock(&[("sha2", "0.10.9"), ("serde", "1.0.0")]);
        let p: Vec<String> = packages(&l).into_iter().collect();
        assert_eq!(p, ["aibd 0.2.0", "serde 1.0.0", "sha2 0.10.9"]);
        // a dependency list's words are not packages
        assert!(!p.iter().any(|x| x.starts_with("sha2,")));
        assert!(packages("").is_empty());
    }

    #[test]
    fn what_a_head_brings_is_read_from_its_changes_and_its_lock() {
        let base = lock(&[("sha2", "0.10.9")]);
        let head = lock(&[
            ("sha2", "0.10.9"),
            ("evil-macro", "0.0.1"),
            ("serde", "1.0.1"),
        ]);
        let changed = ch(&[
            ("M", "crates/aibd/src/lib.rs"),
            ("A", "crates/aibd/src/new.rs"),
            ("A", "crates/aibd/tests/new_test.rs"),
            ("A", "crates/aibd/tests/fixtures/x.json"),
            ("A", "crates/aibd/build.rs"),
            ("M", "crates/aib-store/build.rs"),
            ("D", "crates/old/build.rs"),
            ("M", "Cargo.toml"),
            ("D", "crates/old/Cargo.toml"),
            ("M", "Cargo.lock"),
            ("A", "docs/build.rs.md"),
        ]);
        let b = brings(&changed, &base, &head).unwrap();
        assert_eq!(
            b.build_scripts,
            ["crates/aibd/build.rs", "crates/aib-store/build.rs"],
            "reason: build_script_missed"
        );
        assert_eq!(b.manifests, ["Cargo.toml"]);
        assert_eq!(
            b.new_packages,
            ["evil-macro 0.0.1", "serde 1.0.1"],
            "reason: new_package_missed"
        );
        assert_eq!(b.new_code_files, 2);
        assert_eq!(b.files, 11);
        assert!(b.runs_new_code_at_build());
        // nothing new at build time
        let quiet = brings(&ch(&[("M", "crates/aibd/src/lib.rs")]), &base, &base).unwrap();
        assert!(!quiet.runs_new_code_at_build());
        assert_eq!(
            quiet,
            Brings {
                files: 1,
                ..Brings::default()
            }
        );
        // a version that moved is a new package
        let moved = brings(&[], &base, &lock(&[("sha2", "0.10.10")])).unwrap();
        assert_eq!(moved.new_packages, ["sha2 0.10.10"]);
    }

    #[test]
    fn a_head_that_picks_its_toolchain_or_its_sources_is_refused_before_a_card() {
        let l = lock(&[("sha2", "0.10.9")]);
        for p in [
            "rust-toolchain.toml",
            ".cargo/config.toml",
            "crates/aibd/.cargo/config",
            ".Cargo/config.toml",
        ] {
            for status in ["A", "M", "D"] {
                assert_eq!(
                    brings(&ch(&[("M", "src/lib.rs"), (status, p)]), &l, &l),
                    Err("tree_refused"),
                    "reason: taken {status} {p}"
                );
            }
        }
        let git = l.replace(IO, "git+https://example.invalid/x/y#abc");
        assert_eq!(
            brings(&ch(&[("M", "Cargo.lock")]), &l, &git),
            Err("tree_refused"),
            "reason: git_source_taken"
        );
        // the base's lock is not judged: only what would be fetched for the head
        assert!(brings(&ch(&[("M", "Cargo.lock")]), &git, &l).is_ok());
    }

    fn sent() -> Sent<'static> {
        Sent {
            host: "lin",
            run_id: "web-b4297-012345678",
            head: HEAD,
            base: "89abcdef0123456789abcdef0123456789abcdef",
            bundle_sha256: SHA,
            knives_sha256: NO_KNIVES_SHA256,
        }
    }

    #[test]
    fn the_fingerprint_is_for_this_bundle_on_this_machine() {
        let b = Brings {
            files: 3,
            ..Brings::default()
        };
        let ask = owner_ask(&sent(), &b, "receipt:web-b4297-012345678".into());
        assert_eq!(ask.operation, OP);
        assert_eq!(ask.by, "receipt:web-b4297-012345678");
        let same = owner_ask(&sent(), &b, "receipt:web-b4297-012345678".into());
        assert_eq!(ask.params, same.params);
        let other_sha = "b".repeat(64);
        let changes: [Sent<'_>; 6] = [
            Sent {
                host: "mini",
                ..sent()
            },
            Sent {
                run_id: "web-b4297-012345678-2",
                ..sent()
            },
            Sent {
                head: "1123456789abcdef0123456789abcdef01234567",
                ..sent()
            },
            Sent {
                base: HEAD,
                ..sent()
            },
            Sent {
                bundle_sha256: &other_sha,
                ..sent()
            },
            Sent {
                knives_sha256: &other_sha,
                ..sent()
            },
        ];
        for (i, s) in changes.iter().enumerate() {
            assert_ne!(
                owner_ask(s, &b, ask.by.clone()).params,
                ask.params,
                "reason: field {i} not bound"
            );
        }
        // what the head brings is bound too: a card read as quiet does not pass a loud bundle
        let loud = Brings {
            new_packages: vec!["evil-macro 0.0.1".into()],
            ..b.clone()
        };
        assert_ne!(owner_ask(&sent(), &loud, ask.by.clone()).params, ask.params);
        let script = Brings {
            build_scripts: vec!["build.rs".into()],
            ..b.clone()
        };
        assert_ne!(
            owner_ask(&sent(), &script, ask.by.clone()).params,
            ask.params
        );
    }

    #[test]
    fn the_window_says_when_new_code_runs_at_build() {
        let quiet = Brings {
            files: 3,
            new_code_files: 1,
            ..Brings::default()
        };
        let w = card_words("lin", "web-b4297-012345678", &quiet);
        assert_eq!(
            w,
            "收據 web-b4297-012345678 到 lin·build.rs 0·新套件 0·新檔 1"
        );
        assert!(w.chars().count() <= crate::owner_ok::RESOURCE_MAX);
        let loud = Brings {
            build_scripts: vec!["a/build.rs".into(), "b/build.rs".into()],
            new_packages: vec!["x 1.0.0".into()],
            ..quiet.clone()
        };
        let w = card_words("lin", "web-b4297-012345678-99", &loud);
        assert!(
            w.contains("會跑新程式:build.rs 2·新套件 1"),
            "reason: quiet_card"
        );
        // the longest name and the largest counts still fit the window whole: its front
        // (the run id) is never what gets cut
        let most = Brings {
            build_scripts: vec!["b".into(); 2000],
            new_packages: vec!["p".into(); 2000],
            new_code_files: 123_456,
            ..Brings::default()
        };
        let long = card_words(&"m".repeat(32), "web-b4297-012345678-99", &most);
        assert!(
            long.chars().count() <= crate::owner_ok::RESOURCE_MAX,
            "reason: window_cuts_the_front ({long})"
        );
        assert!(long.starts_with("收據 web-b4297-012345678-99 到 mmmmmmmmmmmmmmmm·會跑新程式:"));
        assert_eq!(crate::owner_ok::clean_resource(&long), long);
        // no name a seat chose is in the window
        assert!(!w.contains("a/build.rs") && !w.contains("x 1.0.0"));
        let page = page_card("lin", &loud);
        assert_eq!(page["red"], true);
        assert_eq!(page["build_scripts_n"], 2);
        assert_eq!(page["new_packages"][0], "x 1.0.0");
        let many = Brings {
            new_packages: (0..40).map(|i| format!("p{i} 1.0.0")).collect(),
            ..Brings::default()
        };
        let page = page_card("lin", &many);
        assert_eq!(
            page["new_packages"].as_array().map(Vec::len),
            Some(NAMED_MAX)
        );
        assert_eq!(page["new_packages_n"], 40);
        // a new package alone is red: its build script and proc-macros run
        assert_eq!(page["red"], true, "reason: new_package_not_red");
        assert!(card_words("lin", "web-b4297-012345678", &many).contains("會跑新程式:"));
    }

    #[test]
    fn a_line_to_a_machine_is_one_the_gate_reads() {
        let run = run_words("web-b4297-012345678", HEAD, 1000, SHA).unwrap();
        assert_eq!(
            run,
            format!("run web-b4297-012345678 {HEAD} ws 1000 {SHA} 0 {NO_KNIVES_SHA256}")
        );
        assert_eq!(
            read_words("web-b4297-012345678-2").unwrap(),
            "fetch web-b4297-012345678-2"
        );
        assert_eq!(
            cancel_words("web-b4297-012345678").unwrap(),
            "cancel web-b4297-012345678"
        );
        // a word that would be another command never becomes a line
        for bad in [
            "web-b4297-012345678;id",
            "web-b4297-012345678 x",
            "$(id)",
            "",
            "../x",
        ] {
            assert!(read_words(bad).is_err(), "reason: sent {bad:?}");
            assert!(cancel_words(bad).is_err(), "reason: sent {bad:?}");
            assert!(
                run_words(bad, HEAD, 1, SHA).is_err(),
                "reason: sent {bad:?}"
            );
        }
        assert_eq!(
            run_words("web-b4297-987654321", HEAD, 1, SHA),
            Err("run_id_not_head"),
            "reason: other_head_sent"
        );
        assert!(run_words("web-b4297-012345678", HEAD, 0, SHA).is_err());
        assert!(run_words("web-b4297-012345678", HEAD, 1, "short").is_err());
        // the digest of no knives is the digest of no bytes
        use sha2::{Digest, Sha256};
        let none: String = Sha256::digest(b"")
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(none, NO_KNIVES_SHA256);
    }

    #[test]
    fn a_gates_answer_is_ok_or_a_fixed_reason() {
        assert_eq!(
            answer(b"OK run\nSTARTED web-b4297-012345678\n", "run"),
            Ok(())
        );
        assert_eq!(answer(b"OK fetch\r\n", "fetch"), Ok(()));
        // another verb's OK is not this one's
        assert_eq!(
            answer(b"OK status\n", "run"),
            Err("lost"),
            "reason: other_ok_taken"
        );
        assert_eq!(answer(b"OK runner\n", "run"), Err("lost"));
        assert_eq!(answer(b"", "run"), Err("lost"));
        assert_eq!(answer(b"\xff\xfe\n", "run"), Err("lost"));
        assert_eq!(answer(b"Welcome to a shell\n", "run"), Err("lost"));
        for (line, why) in [
            ("ERR receipt_tool_changed", "receipt_tool_changed"),
            ("ERR gate_writable", "host_changed"),
            ("ERR disk_low", "disk_low"),
            ("ERR no_toolchain", "toolchain_changed"),
            ("ERR busy", "no_host"),
            ("ERR not_ready", "no_host"),
            ("ERR tree_refused", "tree_refused"),
            ("ERR sha256_mismatch", "tree_refused"),
            ("ERR anything_else", "tree_refused"),
        ] {
            let got = answer(format!("{line}\nmore\n").as_bytes(), "run");
            assert_eq!(got, Err(why), "reason: {line}");
            assert!(
                crate::receipt_run::REFUSALS.contains(&why),
                "reason: {why} is not a word a page is told"
            );
        }
    }
}
