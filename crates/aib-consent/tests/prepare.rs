// SPDX-License-Identifier: Apache-2.0
//! B2 T136-3a: the checks before anyone is asked, with the 1.x result / why words
//! (consent.py `decide` :709–:790).
#![cfg(unix)]

use aib_consent::params::sha256_hex;
use aib_consent::prepare::{plain, prepare, ta_clean, Scratch, REASON_CMD_MAX};
use aib_consent::request::Kind;
use serde_json::{json, Map, Value};
use std::fs;
use std::path::{Path, PathBuf};

const NOW: f64 = 1_794_500_000.0;

struct Home(PathBuf);

impl Home {
    fn new(tag: &str) -> Home {
        let h = std::env::temp_dir().join(format!("aib-consent-prep-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&h);
        fs::create_dir_all(h.join(".ez2-aib/pending")).expect("pending");
        fs::create_dir_all(h.join(".agents/skills/ops/tools/generic")).expect("generic");
        Home(h)
    }
    fn p(&self, rel: &str) -> PathBuf {
        self.0.join(rel)
    }
}

impl Drop for Home {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn req(v: Value) -> Map<String, Value> {
    match v {
        Value::Object(m) => m,
        _ => Map::new(),
    }
}

fn aib_req(t: Value) -> Map<String, Value> {
    req(
        json!({"v": 1, "id": "r1", "t": t, "argv": ["aib", "now", "--clear"],
               "summary": "清狀態", "seat": "c477a"}),
    )
}

fn stopped(r: Result<aib_consent::prepare::Ready, aib_consent::prepare::Stop>) -> (String, Value) {
    match r {
        Ok(_) => panic!("expected a stop"),
        Err(s) => (s.result.to_owned(), Value::Object(s.detail)),
    }
}

#[test]
fn t_must_be_an_integer_within_the_window() {
    let h = Home::new("t");
    let t = NOW as i64;
    assert!(prepare(&aib_req(json!(t)), &h.0, NOW).is_ok());
    assert!(prepare(&aib_req(json!(t - 1800)), &h.0, NOW).is_ok());
    assert!(prepare(&aib_req(json!(t + 60)), &h.0, NOW).is_ok());
    for bad in [
        json!(t - 1801),
        json!(t + 61),
        json!(NOW),
        json!(true),
        json!(t.to_string()),
        Value::Null,
        json!(u64::MAX),
    ] {
        assert_eq!(
            stopped(prepare(&aib_req(bad.clone()), &h.0, NOW)),
            ("expired".into(), json!({})),
            "{bad}"
        );
    }
    // half a second over the 30 minutes: 1.x compares against a float clock
    assert!(prepare(&aib_req(json!(t - 1800)), &h.0, NOW + 0.5).is_err());
}

#[test]
fn the_allow_list_word_comes_back() {
    let h = Home::new("cls");
    let mut r = aib_req(json!(NOW as i64));
    r.insert("argv".into(), json!(["rm", "-rf", "/"]));
    assert_eq!(
        stopped(prepare(&r, &h.0, NOW)),
        ("not_allowed".into(), json!({"why": "not_allowed"}))
    );
    r.insert("argv".into(), json!(["aib", "now", "--allow-unsigned"]));
    assert_eq!(
        stopped(prepare(&r, &h.0, NOW)),
        ("not_allowed".into(), json!({"why": "aib_allow_unsigned"}))
    );
}

#[test]
fn the_summary_is_cleaned_like_1x_str_or_empty() {
    let h = Home::new("sum");
    let t = NOW as i64;
    for empty in [
        Value::Null,
        json!(""),
        json!(false),
        json!(0),
        json!(0.0),
        json!([]),
        json!({}),
        json!("\u{200b}\t"),
    ] {
        let mut r = aib_req(json!(t));
        r.insert("summary".into(), empty.clone());
        assert_eq!(
            stopped(prepare(&r, &h.0, NOW)),
            ("invalid".into(), json!({"why": "summary_missing"})),
            "{empty}"
        );
    }
    for (given, shown) in [
        (json!(true), "True"),
        (json!(7), "7"),
        (json!(" 刪\n站 "), "刪 站"),
    ] {
        let mut r = aib_req(json!(t));
        r.insert("summary".into(), given);
        let ok = prepare(&r, &h.0, NOW).expect("ready");
        assert_eq!(ok.view["summary"], json!(shown));
    }
}

#[test]
fn the_view_has_the_seat_and_strong_and_a_long_command_turns_strong() {
    let h = Home::new("view");
    let mut r = aib_req(json!(NOW as i64));
    r.insert("seat".into(), json!("NOT A SEAT"));
    let ok = prepare(&r, &h.0, NOW).expect("ready");
    assert_eq!(ok.kind, Kind::Aib);
    assert_eq!(ok.view["seat"], Value::Null);
    assert_eq!(ok.view["strong"], json!(false));
    assert!(!ok.strong);
    let long = "x".repeat(REASON_CMD_MAX);
    r.insert("argv".into(), json!(["aib", "now", long]));
    let ok = prepare(&r, &h.0, NOW).expect("ready");
    assert!(ok.strong);
    assert_eq!(ok.view["strong"], json!(true));
}

const CLEAN_SIGN: &str =
    "#!/bin/bash\n# 可丟環境: ct-900 收據: receipts/r1.json\nset -euo pipefail\necho hi\n";

fn sign_req(h: &Home, name: &str, body: &str) -> Map<String, Value> {
    let p = h.p(&format!(".ez2-aib/pending/{name}"));
    fs::write(&p, body).expect("sign");
    req(
        json!({"v": 1, "id": "s1", "t": NOW as i64, "argv": ["bash", p], "strong": true,
               "summary": "換主機設定"}),
    )
}

// knife: the copy is what runs, not the path (consent.py :755 "同意後原檔被改也跑不到改過的")
#[test]
fn a_sign_script_is_copied_checked_and_linted_from_the_copy() {
    let h = Home::new("sign");
    let r = sign_req(&h, "SIGN-a.sh", CLEAN_SIGN);
    let ok = prepare(&r, &h.0, NOW).expect("ready");
    assert_eq!(ok.kind, Kind::SignScript);
    let sha = sha256_hex(CLEAN_SIGN.as_bytes());
    assert_eq!(ok.view["sign"]["sha256"], json!(sha));
    assert!(ok.view["sign"]["head"]
        .as_str()
        .is_some_and(|s| s.starts_with("#!/bin/bash\n# 可丟環境")));
    assert_eq!(
        ok.lint_rec["lint"]["rehearsal"],
        json!({"env": "ct-900", "receipt": "receipts/r1.json"})
    );
    let (copy, csha) = ok.sign_copy.clone().expect("copy");
    assert_eq!(csha, sha);
    assert_eq!(copy.file_name().and_then(|n| n.to_str()), Some("SIGN-a.sh"));
    fs::write(h.p(".ez2-aib/pending/SIGN-a.sh"), "#!/bin/bash\nrm -rf /\n").expect("edit");
    assert_eq!(fs::read_to_string(&copy).expect("copy"), CLEAN_SIGN);
    {
        use std::os::unix::fs::PermissionsExt;
        let dir = copy.parent().expect("dir");
        let m = fs::metadata(dir).expect("meta").permissions().mode() & 0o777;
        assert_eq!(m, 0o700);
    }
    let dir = copy.parent().expect("dir").to_path_buf();
    drop(ok);
    assert!(!dir.exists(), "the scratch folder goes with the Ready");
}

#[test]
fn a_sign_script_with_findings_is_not_allowed_and_acks_are_checked() {
    let h = Home::new("lint");
    let r = sign_req(&h, "SIGN-b.sh", "#!/bin/bash\necho hi\n");
    let (res, d) = stopped(prepare(&r, &h.0, NOW));
    assert_eq!(res, "not_allowed");
    assert_eq!(d["why"], json!("sign_lint"));
    assert_eq!(d["sha256"], json!(sha256_hex(b"#!/bin/bash\necho hi\n")));
    assert!(d["lint"]["findings"]
        .as_array()
        .is_some_and(|f| f.iter().any(|x| x["rule"] == json!("R1"))));
    let mut r = sign_req(&h, "SIGN-c.sh", CLEAN_SIGN);
    r.insert("lint_ack".into(), json!("R1"));
    assert_eq!(
        stopped(prepare(&r, &h.0, NOW)),
        ("invalid".into(), json!({"why": "lint_ack_code"}))
    );
}

#[test]
fn a_sign_copy_that_is_not_utf8_fails_like_the_1x_command() {
    let h = Home::new("utf8");
    let p = h.p(".ez2-aib/pending/SIGN-d.sh");
    fs::write(&p, b"#!/bin/bash\n\xff\n").expect("write");
    let r = req(
        json!({"v": 1, "t": NOW as i64, "argv": ["bash", p], "strong": true,
                       "summary": "x"}),
    );
    let (res, d) = stopped(prepare(&r, &h.0, NOW));
    assert_eq!(res, "failed");
    assert_eq!(d["why"], json!("signlint_rc:2"));
    assert_eq!(
        d["lint"],
        json!({"findings": [], "findings_more": 0, "acks": []})
    );
}

fn shared(h: &Home) -> (PathBuf, PathBuf, String) {
    let prog = h.p(".agents/skills/ops/tools/generic/site-remove.sh");
    let body = b"#!/bin/bash\n# aib-consent-params: SITE\necho $SITE\n";
    fs::write(&prog, body).expect("prog");
    let pfile = h.p("params.env");
    fs::write(&pfile, "SITE=s-1\n").expect("params");
    (prog, pfile, sha256_hex(body))
}

fn shared_req(prog: &Path, pfile: &Path, pin: &str, review: &Path) -> Map<String, Value> {
    req(
        json!({"v": 1, "t": NOW as i64, "argv": ["generic", prog, "--params", pfile],
               "strong": true, "summary": "刪站", "pin": pin, "review": review}),
    )
}

#[test]
fn a_shared_program_is_staged_through_the_library() {
    let h = Home::new("gen");
    let (prog, pfile, pin) = shared(&h);
    let review = h.p("REVIEW.md");
    fs::write(&review, format!("結論: 通過 頭: {pin} H/M/L/N: 0/0/0/0\n")).expect("review");
    let ok = prepare(&shared_req(&prog, &pfile, &pin, &review), &h.0, NOW).expect("ready");
    assert_eq!(ok.kind, Kind::SharedProgram);
    let st = ok.staged.clone().expect("staged");
    assert_eq!(st["program_sha"], json!(pin));
    assert_eq!(st["params_sha"], json!(sha256_hex(b"SITE=s-1\n")));
    let dir = ok.scratch.as_ref().expect("scratch").path().to_path_buf();
    assert_eq!(
        Path::new(st["program_copy"].as_str().expect("copy")).parent(),
        Some(dir.as_path())
    );
    assert_eq!(ok.lint_rec["program"]["keys"], json!(["SITE"]));
    assert_eq!(ok.lint_rec["program"]["sha256"], json!(pin));
    assert_eq!(ok.view["program"]["ok"], json!(true));
}

#[test]
fn shared_program_refusals_keep_the_1x_words() {
    let h = Home::new("genr");
    let (prog, pfile, pin) = shared(&h);
    let review = h.p("REVIEW.md");
    fs::write(&review, "結論: 不通過\n").expect("review");
    for (p, rv) in [
        ("A".repeat(64), review.clone()),
        (pin.clone(), PathBuf::from("REVIEW.md")),
        (format!("{pin}\n"), review.clone()),
    ] {
        assert_eq!(
            stopped(prepare(&shared_req(&prog, &pfile, &p, &rv), &h.0, NOW)),
            ("invalid".into(), json!({"why": "generic_pin_or_review"}))
        );
    }
    assert_eq!(
        stopped(prepare(
            &shared_req(&prog, &pfile, &pin, &review),
            &h.0,
            NOW
        )),
        (
            "not_allowed".into(),
            json!({"why": "review_not_passed", "line": 1})
        )
    );
    assert_eq!(
        stopped(prepare(
            &shared_req(&prog, &pfile, &"0".repeat(64), &review),
            &h.0,
            NOW
        )),
        (
            "not_allowed".into(),
            json!({"why": "program_pin_mismatch", "line": 0})
        )
    );
}

#[test]
fn shown_text_is_cleaned_like_ta_clean() {
    assert_eq!(ta_clean(" a\u{202e}b\tc\u{2028} ", 80), "ab c");
    assert_eq!(ta_clean("abcdef", 4), "abc…");
    assert_eq!(plain("x\u{200b}y", 10), "xy");
}

#[test]
fn a_scratch_folder_is_0700_and_removed_unless_kept() {
    use std::os::unix::fs::PermissionsExt;
    let s = Scratch::new("aib-consent-test-").expect("scratch");
    let p = s.path().to_path_buf();
    assert_eq!(
        fs::metadata(&p).expect("meta").permissions().mode() & 0o777,
        0o700
    );
    drop(s);
    assert!(!p.exists());
    let s = Scratch::new("aib-consent-test-").expect("scratch");
    let kept = s.keep();
    assert!(kept.exists());
    let _ = fs::remove_dir_all(kept);
}
