//! The cargo adapter of `aib test` 2.0 (S69-u2, DECISION-council-herdr-fastpath r2 §2 4):
//! cargo-nextest runs the tests, one process per test; this crate builds its command line
//! and reads its two stable outputs back into [`aib_test_core`] terms.
//!
//! - `cargo nextest list --message-format json`: every test, and whether the filter picked
//!   it. A test the filter left out is a skip with a reason, never a silent gap.
//! - JUnit (`[profile.aib.junit]`): what each picked test did.
//!
//! The two are checked against each other: a picked test with no result is not a pass
//! (nextest stopped, the binary crashed, or the list and the run disagree).

pub mod units;

use aib_test_core::exit::Outcome;
use aib_test_core::skip::SkipWhy;
use std::collections::BTreeMap;
use std::path::Path;

/// The nextest profile `aib test` runs with: no retry (a pass on the second try hides a
/// failure), no fail-fast (every result is wanted), and a JUnit file to read back.
pub const PROFILE: &str = "aib";

/// The tool config passed with `--tool-config-file aib:<file>`, so the repo's own
/// `.config/nextest.toml` still applies and only the `aib` profile is ours.
pub const TOOL_CONFIG: &str = r#"[profile.aib]
retries = 0
fail-fast = false
failure-output = "final"
success-output = "never"

[profile.aib.junit]
path = "junit.xml"
store-success-output = false
store-failure-output = true
"#;

/// Where the JUnit file lands, relative to the build folder (nextest's fixed layout).
pub fn junit_path(target_dir: &Path) -> std::path::PathBuf {
    target_dir.join("nextest").join(PROFILE).join("junit.xml")
}

/// The arguments after `cargo` for the list. Same profile as the run, so a default filter
/// put in the `aib` profile applies to both (1ced7 u2 N2).
pub fn list_args(tool_config: &Path, select: &[String]) -> Vec<String> {
    let mut v: Vec<String> = [
        "nextest",
        "list",
        "--locked",
        "--message-format",
        "json",
        "--profile",
        PROFILE,
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    v.push("--tool-config-file".into());
    v.push(format!("aib:{}", tool_config.display()));
    v.extend(select.iter().cloned());
    v
}

/// The arguments after `cargo` for the run. `select` is the package / filter part, the same
/// for the list and the run, so the two can be checked against each other.
pub fn run_args(tool_config: &Path, select: &[String]) -> Vec<String> {
    let mut v: Vec<String> = ["nextest", "run", "--locked", "--profile", PROFILE]
        .iter()
        .map(|s| s.to_string())
        .collect();
    v.push("--tool-config-file".into());
    v.push(format!("aib:{}", tool_config.display()));
    v.push("--no-fail-fast".into());
    v.extend(select.iter().cloned());
    v
}

/// One test from the list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Listed {
    /// nextest's binary id (`aib-test-core`, `aibd::sweep` …).
    pub binary: String,
    pub name: String,
    /// `None` = the filter picked it; otherwise why not.
    pub left_out: Option<LeftOut>,
}

/// Why nextest's filter left a test out (its `filter-match.reason`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LeftOut {
    /// `#[ignore]` in the code: not a unit at all, as with `cargo test`.
    Ignored,
    /// The user's name filter or `-E` expression.
    UserFilter,
    /// `--partition`: another share of the run holds it.
    Partition,
    /// The repo's `default-filter`.
    DefaultFilter,
    /// A reason this version does not know.
    Other(String),
}

impl LeftOut {
    /// The core's skip reason, or `None` when it is not a unit (`#[ignore]`).
    /// A partition or a default filter is not the user's own choice, so it is not
    /// [`SkipWhy::UserFilter`]: a receipt has to point at the run that holds the test.
    pub fn skip_why(&self) -> Option<SkipWhy> {
        match self {
            LeftOut::Ignored => None,
            LeftOut::UserFilter => Some(SkipWhy::UserFilter),
            LeftOut::Partition | LeftOut::DefaultFilter | LeftOut::Other(_) => {
                Some(SkipWhy::Unknown)
            }
        }
    }
}

fn left_out(reason: &str) -> LeftOut {
    match reason {
        "ignored" => LeftOut::Ignored,
        "string" | "expression" => LeftOut::UserFilter,
        "partition" => LeftOut::Partition,
        "default-filter" => LeftOut::DefaultFilter,
        other => LeftOut::Other(other.to_string()),
    }
}

/// The list as a whole.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct List {
    pub tests: Vec<Listed>,
    /// Binaries a `-E` expression or the default filter left out whole (suite status
    /// `skipped`, no tests listed). Which of the two it was is not in the output: the caller
    /// knows whether it passed `-E`, and says so in the ledger (1ced7 u2 M1).
    pub skipped_binaries: Vec<String>,
}

/// Reads `cargo nextest list --message-format json` (shape checked against nextest 0.9.152
/// output). A suite status other than `listed` / `skipped` is an error.
pub fn parse_list(json: &str) -> Result<List, String> {
    let v: serde_json::Value =
        serde_json::from_str(json).map_err(|e| format!("nextest list is not JSON: {e}"))?;
    let suites = v
        .get("rust-suites")
        .and_then(|s| s.as_object())
        .ok_or("nextest list has no rust-suites")?;
    let mut out = List::default();
    for (id, suite) in suites {
        let status = suite.get("status").and_then(|s| s.as_str()).unwrap_or("");
        match status {
            "listed" => {}
            "skipped" => {
                out.skipped_binaries.push(id.clone());
                continue;
            }
            _ => return Err(format!("suite {id}: unknown status {status:?}")),
        }
        let cases = suite
            .get("testcases")
            .and_then(|c| c.as_object())
            .ok_or_else(|| format!("suite {id} has no testcases"))?;
        for (name, case) in cases {
            let fm = case
                .get("filter-match")
                .ok_or_else(|| format!("{id} {name}: no filter-match"))?;
            let left = match fm.get("status").and_then(|s| s.as_str()) {
                Some("matches") => None,
                Some("mismatch") => Some(left_out(
                    fm.get("reason").and_then(|r| r.as_str()).unwrap_or(""),
                )),
                other => return Err(format!("{id} {name}: filter-match status {other:?}")),
            };
            out.tests.push(Listed {
                binary: id.clone(),
                name: name.clone(),
                left_out: left,
            });
        }
    }
    Ok(out)
}

/// What one test did, from the JUnit file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CaseStatus {
    Passed,
    Failed,
    /// Failed, then passed on a retry: not a pass (the profile has no retry, so this only
    /// shows up when someone's config adds one).
    Flaky,
    Skipped,
}

/// One `<testcase>`: nextest writes the binary id as `classname` and the test as `name`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Case {
    pub binary: String,
    pub name: String,
    pub status: CaseStatus,
}

fn unescape(s: &str) -> String {
    s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

fn attr(tag: &str, name: &str) -> Option<String> {
    let key = format!(" {name}=\"");
    let at = tag.find(&key)? + key.len();
    let end = tag[at..].find('"')? + at;
    Some(unescape(&tag[at..end]))
}

/// Reads nextest's JUnit file. Only `<testcase>` and the child tags that say how it ended
/// are read; output text inside them is not parsed.
pub fn parse_junit(xml: &str) -> Result<Vec<Case>, String> {
    let mut out = Vec::new();
    let mut rest = xml;
    while let Some(at) = rest.find("<testcase") {
        let after = &rest[at..];
        // `<testcase` must be the whole tag name, not `<testcases`
        let next = after[9..].chars().next();
        if !matches!(next, Some(' ' | '>' | '/' | '\n' | '\t' | '\r')) {
            rest = &after[9..];
            continue;
        }
        let close = after.find('>').ok_or("a <testcase> tag is not closed")?;
        let tag = &after[..close];
        let binary = attr(tag, "classname").ok_or("a <testcase> has no classname")?;
        let name = attr(tag, "name").ok_or("a <testcase> has no name")?;
        let (body, used) = if tag.ends_with('/') {
            ("", close + 1)
        } else {
            let end = after
                .find("</testcase>")
                .ok_or_else(|| format!("<testcase {name}> has no end tag"))?;
            (&after[close + 1..end], end + "</testcase>".len())
        };
        let status = if body.contains("<failure") || body.contains("<error") {
            CaseStatus::Failed
        } else if body.contains("<flakyFailure") || body.contains("<flakyError") {
            CaseStatus::Flaky
        } else if body.contains("<skipped") {
            CaseStatus::Skipped
        } else {
            CaseStatus::Passed
        };
        out.push(Case {
            binary,
            name,
            status,
        });
        rest = &after[used..];
    }
    Ok(out)
}

/// The whole cargo unit, from the list and the run together.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reading {
    /// The unit's outcome for [`aib_test_core::exit::exit_for`].
    pub outcome: Outcome,
    /// Tests the filter left out, each with its skip reason (`#[ignore]` not counted).
    pub skipped: Vec<(String, SkipWhy)>,
    /// Picked tests with no result in the JUnit file.
    pub missing: Vec<String>,
    /// Results for tests the list did not pick (the list and the run disagree).
    pub unexpected: Vec<String>,
}

/// Checks the run against the list. `met` only when every picked test has a result, none
/// failed or was flaky, nothing ran that was not picked, and at least one test ran (the
/// core's exit code also refuses zero, 1ced7 u2 N1: both say so).
pub fn read(listed: &[Listed], cases: &[Case]) -> Reading {
    let key = |b: &str, n: &str| format!("{b} {n}");
    let mut picked: BTreeMap<String, bool> = BTreeMap::new();
    let mut skipped = Vec::new();
    for l in listed {
        match &l.left_out {
            None => {
                picked.insert(key(&l.binary, &l.name), false);
            }
            Some(lo) => {
                if let Some(w) = lo.skip_why() {
                    skipped.push((key(&l.binary, &l.name), w));
                }
            }
        }
    }
    let mut bad = false;
    let mut ran = 0u32;
    let mut unexpected = Vec::new();
    for c in cases {
        let k = key(&c.binary, &c.name);
        match picked.get_mut(&k) {
            Some(seen) => *seen = true,
            None => {
                unexpected.push(k);
                continue;
            }
        }
        match c.status {
            CaseStatus::Passed => ran += 1,
            CaseStatus::Failed | CaseStatus::Flaky => {
                ran += 1;
                bad = true;
            }
            CaseStatus::Skipped => bad = true,
        }
    }
    let missing: Vec<String> = picked
        .into_iter()
        .filter(|(_, seen)| !seen)
        .map(|(k, _)| k)
        .collect();
    let met = !bad && missing.is_empty() && unexpected.is_empty() && ran > 0;
    Reading {
        outcome: Outcome::Ran {
            met,
            tests_ran: Some(ran),
        },
        skipped,
        missing,
        unexpected,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // shapes copied from cargo-nextest 0.9.152 output on a test machine (paths shortened,
    // other fields kept): list --message-format json, and the JUnit file of profile aib
    const LIST: &str = r#"{"rust-build-meta":{"target-directory":"/r/target","build-directory":"/r/target","base-output-directories":["debug"]},"test-count":4,
 "rust-suites":{
  "aib-accept":{"package-name":"aib-accept","binary-id":"aib-accept","binary-name":"aib_accept",
   "package-id":"path+file:///r/crates/aib-accept#0.2.0-preview.7","kind":"lib",
   "binary-path":"/r/target/debug/deps/aib_accept-f09f","build-platform":"target","cwd":"/r/crates/aib-accept",
   "status":"skipped","testcases":{}},
  "aib-test-core":{"package-name":"aib-test-core","binary-id":"aib-test-core","binary-name":"aib_test_core",
   "package-id":"path+file:///r/crates/aib-test-core#0.2.0-preview.7","kind":"lib",
   "binary-path":"/r/target/debug/deps/aib_test_core-1","build-platform":"target","cwd":"/r/crates/aib-test-core",
   "status":"listed","testcases":{
     "key::tests::a":{"kind":"test","ignored":false,"filter-match":{"status":"matches"}},
     "key::tests::b":{"kind":"test","ignored":false,"filter-match":{"status":"matches"}},
     "key::tests::slow":{"kind":"test","ignored":true,"filter-match":{"status":"mismatch","reason":"ignored"}},
     "skip::tests::c":{"kind":"test","ignored":false,"filter-match":{"status":"mismatch","reason":"expression"}}}}}}"#;

    const JUNIT: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="nextest-run" tests="2" skipped="0" failures="0" errors="0" uuid="64c882e0" timestamp="2026-11-17T02:37:07.175+08:00" time="0.006">
    <testsuite name="aib-test-core" tests="2" skipped="0" errors="0" failures="0">
        <testcase name="key::tests::a" classname="aib-test-core" timestamp="2026-11-17T02:37:07.175+08:00" time="0.005"/>
        <testcase name="key::tests::b" classname="aib-test-core" timestamp="2026-11-17T02:37:07.175+08:00" time="0.005"/>
    </testsuite>
</testsuites>
"#;

    /// The second case written open, with a body, the way a failure is written.
    fn with_body(body: &str) -> String {
        JUNIT.replacen(
            r#"time="0.005"/>
    </testsuite>"#,
            &format!("time=\"0.005\">\n{body}\n</testcase>\n    </testsuite>"),
            1,
        )
    }

    #[test]
    fn the_list_says_which_tests_were_picked_and_why_the_rest_were_not() {
        let all = parse_list(LIST).unwrap();
        let l = &all.tests;
        assert_eq!(l.len(), 4);
        assert_eq!(all.skipped_binaries, vec!["aib-accept".to_string()]);
        let by = |n: &str| l.iter().find(|x| x.name == n).unwrap().left_out.clone();
        assert_eq!(by("key::tests::a"), None);
        assert_eq!(by("key::tests::slow"), Some(LeftOut::Ignored));
        assert_eq!(by("skip::tests::c"), Some(LeftOut::UserFilter));
    }

    #[test]
    fn a_binary_left_out_whole_is_named_and_an_unknown_status_is_an_error() {
        let odd = LIST.replace(r#""status":"listed""#, r#""status":"broken""#);
        assert!(parse_list(&odd).unwrap_err().contains("unknown status"));
        assert!(parse_list("not json").is_err());
        let skipped = parse_list(LIST).unwrap().skipped_binaries;
        assert_eq!(skipped, vec!["aib-accept".to_string()]);
    }

    #[test]
    fn every_picked_test_passing_is_met_and_ignored_tests_are_not_skips() {
        let r = read(
            &parse_list(LIST).unwrap().tests,
            &parse_junit(JUNIT).unwrap(),
        );
        assert_eq!(
            r.outcome,
            Outcome::Ran {
                met: true,
                tests_ran: Some(2)
            }
        );
        assert_eq!(
            r.skipped,
            vec![(
                "aib-test-core skip::tests::c".to_string(),
                SkipWhy::UserFilter
            )]
        );
        assert!(r.missing.is_empty() && r.unexpected.is_empty());
    }

    #[test]
    fn a_failure_or_a_flaky_pass_is_not_met() {
        let l = parse_list(LIST).unwrap().tests;
        let fail = with_body(r#"<failure type="test failure">boom</failure>"#);
        let r = read(&l, &parse_junit(&fail).unwrap());
        assert!(matches!(r.outcome, Outcome::Ran { met: false, .. }));
        let flaky = with_body(r#"<flakyFailure type="test failure">x</flakyFailure>"#);
        let c = parse_junit(&flaky).unwrap();
        assert_eq!(c[1].status, CaseStatus::Flaky);
        assert_eq!(parse_junit(&fail).unwrap()[1].status, CaseStatus::Failed);
        assert!(matches!(
            read(&l, &c).outcome,
            Outcome::Ran { met: false, .. }
        ));
    }

    #[test]
    fn a_picked_test_with_no_result_is_not_a_pass() {
        let l = parse_list(LIST).unwrap().tests;
        let one = parse_junit(JUNIT).unwrap()[..1].to_vec();
        let r = read(&l, &one);
        assert!(matches!(r.outcome, Outcome::Ran { met: false, .. }));
        assert_eq!(r.missing, vec!["aib-test-core key::tests::b".to_string()]);
    }

    #[test]
    fn a_result_the_list_did_not_pick_means_they_disagree() {
        let l = parse_list(LIST).unwrap().tests;
        let extra = JUNIT.replace(
            "</testsuite>",
            "<testcase name=\"skip::tests::c\" classname=\"aib-test-core\" time=\"0\"/>\n</testsuite>",
        );
        let r = read(&l, &parse_junit(&extra).unwrap());
        assert!(matches!(r.outcome, Outcome::Ran { met: false, .. }));
        assert_eq!(
            r.unexpected,
            vec!["aib-test-core skip::tests::c".to_string()]
        );
    }

    #[test]
    fn a_self_closing_case_passes_and_names_are_unescaped() {
        let x = r#"<testsuites><testcase name="t::a&lt;u8&gt;" classname="b&amp;c" time="0"/></testsuites>"#;
        let c = parse_junit(x).unwrap();
        assert_eq!(
            c,
            vec![Case {
                binary: "b&c".into(),
                name: "t::a<u8>".into(),
                status: CaseStatus::Passed
            }]
        );
        assert!(parse_junit("<testsuites></testsuites>").unwrap().is_empty());
    }

    #[test]
    fn nothing_picked_and_nothing_run_is_not_met() {
        let r = read(&[], &[]);
        assert_eq!(
            r.outcome,
            Outcome::Ran {
                met: false,
                tests_ran: Some(0)
            }
        );
    }

    #[test]
    fn a_partition_or_default_filter_is_not_the_users_choice() {
        assert_eq!(LeftOut::Partition.skip_why(), Some(SkipWhy::Unknown));
        assert_eq!(LeftOut::DefaultFilter.skip_why(), Some(SkipWhy::Unknown));
        assert_eq!(LeftOut::Ignored.skip_why(), None);
    }

    #[test]
    fn the_run_uses_the_aib_profile_and_the_list_the_same_selection() {
        let cfg = Path::new("/tmp/aib-nextest.toml");
        let sel = vec!["-p".to_string(), "aib-test-core".to_string()];
        let r = run_args(cfg, &sel);
        assert!(r.windows(2).any(|w| w == ["--profile", "aib"]));
        assert!(r.contains(&"--no-fail-fast".to_string()));
        assert!(r.ends_with(&sel));
        let l = list_args(cfg, &sel);
        assert!(l.ends_with(&sel));
        assert!(l.windows(2).any(|w| w == ["--profile", "aib"]));
        assert!(TOOL_CONFIG.contains(concat!("retries", " = 0")));
        assert_eq!(
            junit_path(Path::new("target")),
            Path::new("target/nextest/aib/junit.xml")
        );
    }
}
