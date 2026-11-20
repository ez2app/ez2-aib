//! N-webonly 第 4 件 (7ebca 11-20, 519d0 21:33; DESIGN-g1-receipt-origacct-b4297-1120 r3 §5):
//! aibd reads a receipt's `.out` itself, key by key, the way a seat is told to read one by hand
//! (memory of 11-17 / 11-20: a receipt was called green on its last lines while `restored` was
//! 101). [`check`] is a pure function of the file's bytes: the page shows green or red and why.
//!
//! What a green receipt has (receipt-run.sh as of ez2-skills 98b56725e; the shapes in the tests
//! are those of a real one, RECEIPT-…-7ebca-mail-6f547e599.out):
//! - its last line is `RECEIPT-SHA256 <sha256 of every byte before that line>` (the seal);
//! - one `HEAD <sha>` and every `HEAD-END <sha>` are the head that was asked for;
//! - `RC <step>=0` for every step in [`STEPS`] (and [`KNIFE_STEPS`] when knives were sent), and
//!   no `RC <step>=` of those with another value anywhere;
//! - one `TESTS-TOTAL` with `failed=0` and at least one test passed;
//! - with knives: `KNIVES-EXPECTED` = the number aibd sent = `BITES-COUNT`, `MISSES-COUNT 0`,
//!   `KNIVES-RC 0`, `KNIVES-OK` (b4297 11-20: whether one knife bit is receipt-run's own judgement, written in
//!   those three keys; it is not judged again here from the `RC knife_…` lines);
//! - no line that makes a receipt red or not a full one ([`RED_LINES`]).
//!
//! A key that is there twice with two values is red: lines a test prints into the file cannot
//! add a green value beside the real one. This is not a defence against the machine that wrote
//! the file (the seal is a checksum, not a signature; whoever can write the file can write a
//! green one): the page's green is a display, the merge gate reads CI (design r3 §5).
//!
//! Only fixed words leave this module ([`Verdict::reasons`] are codes, the numbers are numbers):
//! never a line of the receipt.

use sha2::{Digest, Sha256};

/// The steps every full receipt runs.
pub const STEPS: [&str; 5] = ["fmt", "anchors", "clippy", "winclippy", "test"];
/// The steps a receipt with knives also runs.
pub const KNIFE_STEPS: [&str; 2] = ["knifecompile", "restored"];
/// A line starting with one of these (then a space or the line's end) makes the receipt red.
pub const RED_LINES: [&str; 6] = [
    "KNIVES-SKIPPED",
    "RECEIPT-RED",
    "RECEIPT-NARROW",
    "RECEIPT-INVALID",
    "KNIVES-INCOMPLETE",
    "COMPILE-ONLY",
];
const SEAL: &str = "RECEIPT-SHA256 ";

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Tests {
    pub suites: u64,
    pub passed: u64,
    pub failed: u64,
    pub ignored: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Knives {
    pub expected: u64,
    pub bites: u64,
    pub misses: u64,
}

/// What aibd read in a receipt. `green` only with no reason.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Verdict {
    pub green: bool,
    /// Why not, as codes (`seal_missing`, `rc_nonzero:test`, …), in the order they are checked.
    pub reasons: Vec<String>,
    /// The receipt says it ran only some tests (never green).
    pub narrow: bool,
    pub tests: Option<Tests>,
    pub knives: Option<Knives>,
}

fn is_sha(s: &str, len: usize) -> bool {
    s.len() == len
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The bytes the seal covers and the digest the last line names, when the file ends with a
/// seal line.
fn sealed(bytes: &[u8]) -> Option<(&[u8], &str)> {
    let body_and_seal = bytes.strip_suffix(b"\n")?;
    let at = body_and_seal
        .iter()
        .rposition(|b| *b == b'\n')
        .map_or(0, |i| i + 1);
    let (body, last) = body_and_seal.split_at(at);
    let named = std::str::from_utf8(last).ok()?.strip_prefix(SEAL)?;
    is_sha(named, 64).then_some((body, named))
}

fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// `body` with its seal line: what receipt-run.sh's `seal` leaves (a runner's double and the
/// tests write receipts with it).
pub fn seal(body: &[u8]) -> Vec<u8> {
    let mut out = body.to_vec();
    out.extend_from_slice(format!("{SEAL}{}\n", digest(body)).as_bytes());
    out
}

/// The values of every line `<key> <value>` (the value is the line's rest).
fn values<'a>(lines: &[&'a str], key: &str) -> Vec<&'a str> {
    lines
        .iter()
        .filter_map(|l| l.strip_prefix(key)?.strip_prefix(' '))
        .collect()
}

/// `name=<n>` in a `TESTS-TOTAL` line.
fn field(line: &str, name: &str) -> Option<u64> {
    line.split(' ')
        .find_map(|w| w.strip_prefix(name)?.strip_prefix('='))?
        .parse()
        .ok()
}

/// The one value all the lines of a key agree on, as a number.
fn one_number(lines: &[&str], key: &str) -> Result<u64, &'static str> {
    let v = values(lines, key);
    let first = v.first().ok_or("missing")?;
    if v.iter().any(|x| x != first) {
        return Err("differs");
    }
    first.parse().map_err(|_| "not_a_number")
}

/// Reads a receipt. `head` is the 40-hex head aibd asked for; `knives_sent` how many knives it
/// sent with it (0: none, the knife keys are not asked for).
pub fn check(bytes: &[u8], head: &str, knives_sent: u64) -> Verdict {
    let mut reasons: Vec<String> = Vec::new();
    let mut no = |code: &str| reasons.push(code.to_owned());

    match sealed(bytes) {
        None => no("seal_missing"),
        Some((body, named)) if digest(body) != named => no("seal_mismatch"),
        Some(_) => {}
    }
    let text = String::from_utf8_lossy(bytes);
    let lines: Vec<&str> = text.lines().collect();

    if !is_sha(head, 40) {
        no("head_asked_not_a_sha");
    }
    let heads = values(&lines, "HEAD");
    match heads.as_slice() {
        [] => no("head_missing"),
        hs if hs.iter().any(|h| *h != head) => no("head_other"),
        _ => {}
    }
    let ends = values(&lines, "HEAD-END");
    match ends.as_slice() {
        [] => no("head_end_missing"),
        es if es.iter().any(|h| *h != head) => no("head_moved"),
        _ => {}
    }

    let narrow = lines
        .iter()
        .any(|l| *l == "RECEIPT-NARROW" || l.starts_with("RECEIPT-NARROW "));
    for red in RED_LINES {
        if lines.iter().any(|l| {
            l.strip_prefix(red)
                .is_some_and(|r| r.is_empty() || r.starts_with(' '))
        }) {
            no(&format!("red_line:{red}"));
        }
    }

    let with_knives = knives_sent > 0;
    let knife_steps: &[&str] = if with_knives { &KNIFE_STEPS } else { &[] };
    for step in STEPS.iter().chain(knife_steps) {
        let key = format!("RC {step}=");
        let got: Vec<&str> = lines
            .iter()
            .filter_map(|l| l.strip_prefix(key.as_str()))
            .collect();
        if got.is_empty() {
            no(&format!("rc_missing:{step}"));
        } else if got.iter().any(|v| *v != "0") {
            no(&format!("rc_nonzero:{step}"));
        }
    }

    let totals = values(&lines, "TESTS-TOTAL");
    let tests = match totals.as_slice() {
        [one] => {
            let f = |n| field(one, n);
            match (f("suites"), f("passed"), f("failed"), f("ignored")) {
                (Some(suites), Some(passed), Some(failed), Some(ignored)) => Some(Tests {
                    suites,
                    passed,
                    failed,
                    ignored,
                }),
                _ => None,
            }
        }
        _ => None,
    };
    match (totals.len(), tests) {
        (0, _) => no("tests_total_missing"),
        (1, None) => no("tests_total_unread"),
        (1, Some(t)) if t.failed != 0 => no("tests_failed"),
        (1, Some(t)) if t.passed == 0 => no("tests_none_passed"),
        (1, Some(_)) => {}
        _ => no("tests_total_twice"),
    }

    let mut knives = None;
    if with_knives {
        let mut num = |key: &str, code: &str| match one_number(&lines, key) {
            Ok(n) => Some(n),
            Err(why) => {
                no(&format!("{code}_{why}"));
                None
            }
        };
        let expected = num("KNIVES-EXPECTED", "knives_expected");
        let bites = num("BITES-COUNT", "bites");
        let misses = num("MISSES-COUNT", "misses");
        // receipt-run's own summary of the knife part (5d33a N2): 0 only when none missed
        if num("KNIVES-RC", "knives_rc").is_some_and(|rc| rc != 0) {
            no("knives_rc_nonzero");
        }
        if let (Some(expected), Some(bites), Some(misses)) = (expected, bites, misses) {
            knives = Some(Knives {
                expected,
                bites,
                misses,
            });
            if expected != knives_sent {
                no("knives_expected_not_sent");
            }
            if bites != expected {
                no("knives_not_all_bit");
            }
            if misses != 0 {
                no("knives_missed");
            }
        }
        if !lines.contains(&"KNIVES-OK") {
            no("knives_ok_missing");
        }
    }

    Verdict {
        green: reasons.is_empty(),
        reasons,
        narrow,
        tests,
        knives,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const H: &str = "6f547e599826ea87ecaa770771fefcd188c59cd4";
    const OTHER: &str = "0ac59766cc9a9dec994f81b43eeb0f227eb31cb3";

    /// A receipt in the shape receipt-run.sh writes (RECEIPT-…-7ebca-mail-6f547e599.out, the
    /// lines between the keys left out), with two knives.
    fn body() -> String {
        format!(
            "START pid 91682\n\
             == HEAD={H}\n\
             RC fmt=0\nSTEP rc=0 3s fmt\n\
             RC anchors=0\n\
             RC knifecompile=0\n\
             RC clippy=0\n\
             RC winclippy=0\n\
             STEP rc=0 1099s test\n\
             RC test=0\n\
             TESTS-TOTAL suites=312 passed=4386 failed=0 ignored=24\n\
             KNIVES-EXPECTED 2\n\
             test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 598 filtered out\n\
             RC knife_MN1=101 (want non-0)\n\
             RC knife_SS-T1=101 (want non-0)\n\
             RC restored=0\n\
             TREE-CLEAN\n\
             HEAD-END {H}\n\
             HEAD {H}\n\
             BRANCH feat/x\n\
             FMT-OK\nANCHORS-OK\nCLIPPY-OK\nWINCLIPPY-OK\nTEST-OK\n\
             KNIVES-RC 0\n\
             BITES-COUNT 2\n\
             MISSES-COUNT 0\n\
             TREE-CLEAN\n\
             HEAD-END {H}\n\
             KNIVES-OK\n\
             END x\n"
        )
    }

    fn seal(body: &str) -> Vec<u8> {
        super::seal(body.as_bytes())
    }

    fn reasons_of(body: &str, knives: u64) -> Vec<String> {
        check(&seal(body), H, knives).reasons
    }

    #[test]
    fn a_whole_receipt_for_the_head_is_green() {
        let v = check(&seal(&body()), H, 2);
        assert_eq!(v.reasons, Vec::<String>::new(), "reason: green_refused");
        assert!(v.green && !v.narrow);
        assert_eq!(
            v.tests,
            Some(Tests {
                suites: 312,
                passed: 4386,
                failed: 0,
                ignored: 24
            })
        );
        assert_eq!(
            v.knives,
            Some(Knives {
                expected: 2,
                bites: 2,
                misses: 0
            })
        );
        // no knives sent: the knife keys are not asked for
        let plain: String = body()
            .lines()
            .filter(|l| {
                ![
                    "KNIVES",
                    "BITES",
                    "MISSES",
                    "RC knife",
                    "RC restored",
                    "RC knifecompile",
                ]
                .iter()
                .any(|k| l.starts_with(k))
            })
            .map(|l| format!("{l}\n"))
            .collect();
        let v = check(&seal(&plain), H, 0);
        assert!(v.green, "reason: plain_refused {:?}", v.reasons);
        assert_eq!(v.knives, None);
    }

    #[test]
    fn the_seal_is_the_digest_of_what_is_before_its_line() {
        let good = seal(&body());
        // one byte of the body changed after sealing
        let mut edited = good.clone();
        edited[0] = b'X';
        assert_eq!(check(&edited, H, 2).reasons, ["seal_mismatch"]);
        // no seal line at all (a run that was killed)
        assert_eq!(check(body().as_bytes(), H, 2).reasons, ["seal_missing"]);
        // a seal that is not the last line
        let mut more = good.clone();
        more.extend_from_slice(b"KNIVES-OK\n");
        assert_eq!(check(&more, H, 2).reasons, ["seal_missing"]);
        // not ended by a newline, or not a digest
        assert_eq!(
            check(&good[..good.len() - 1], H, 2).reasons,
            ["seal_missing"]
        );
        let short = format!("{}{SEAL}abc\n", body());
        assert_eq!(check(short.as_bytes(), H, 2).reasons, ["seal_missing"]);
        assert_eq!(check(b"", H, 0).reasons[0], "seal_missing");
    }

    #[test]
    fn a_receipt_counts_only_for_the_head_asked_for() {
        assert_eq!(
            check(&seal(&body()), OTHER, 2).reasons,
            ["head_other", "head_moved"]
        );
        // the tree moved while it ran: the last HEAD-END is another head
        let moved = body().replacen(
            &format!("HEAD-END {H}\nKNIVES-OK"),
            &format!("HEAD-END {OTHER}\nKNIVES-OK"),
            1,
        );
        assert_eq!(reasons_of(&moved, 2), ["head_moved"]);
        let no_end = body().replace(&format!("HEAD-END {H}\n"), "");
        assert_eq!(reasons_of(&no_end, 2), ["head_end_missing"]);
        let no_head = body().replace(&format!("\nHEAD {H}\n"), "\n");
        assert_eq!(reasons_of(&no_head, 2), ["head_missing"]);
        assert_eq!(
            check(&seal(&body()), "6f547e5998", 2).reasons[0],
            "head_asked_not_a_sha"
        );
    }

    #[test]
    fn every_step_is_there_and_zero() {
        // the 11-20 mistake: everything green at the end, restored 101
        let r = body().replace("RC restored=0", "RC restored=101");
        assert_eq!(reasons_of(&r, 2), ["rc_nonzero:restored"]);
        for step in STEPS.iter().chain(&KNIFE_STEPS) {
            let gone = body().replace(&format!("RC {step}=0\n"), "");
            assert_eq!(
                reasons_of(&gone, 2),
                [format!("rc_missing:{step}")],
                "reason: missing_step_passed"
            );
            let red = body().replace(&format!("RC {step}=0\n"), &format!("RC {step}=1\n"));
            assert_eq!(reasons_of(&red, 2), [format!("rc_nonzero:{step}")]);
        }
        // a line printed into the file beside the real one does not make it green
        let both = body().replace("RC test=0\n", "RC test=101\nRC test=0\n");
        assert_eq!(
            reasons_of(&both, 2),
            ["rc_nonzero:test"],
            "reason: second_value_wins"
        );
    }

    #[test]
    fn the_tests_total_says_none_failed() {
        let failed = body().replace("failed=0", "failed=2");
        assert_eq!(reasons_of(&failed, 2), ["tests_failed"]);
        let none = body().replace("passed=4386", "passed=0");
        assert_eq!(reasons_of(&none, 2), ["tests_none_passed"]);
        let gone = body().replace(
            "TESTS-TOTAL suites=312 passed=4386 failed=0 ignored=24\n",
            "",
        );
        assert_eq!(reasons_of(&gone, 2), ["tests_total_missing"]);
        let unread = body().replace("failed=0", "failed=x");
        assert_eq!(reasons_of(&unread, 2), ["tests_total_unread"]);
        let twice = body().replace(
            "KNIVES-EXPECTED 2\n",
            "TESTS-TOTAL suites=1 passed=1 failed=0 ignored=0\nKNIVES-EXPECTED 2\n",
        );
        assert_eq!(reasons_of(&twice, 2), ["tests_total_twice"]);
    }

    #[test]
    fn every_knife_sent_bit() {
        // aibd sent three, the receipt ran two
        assert_eq!(reasons_of(&body(), 3), ["knives_expected_not_sent"]);
        let missed = body()
            .replace("BITES-COUNT 2", "BITES-COUNT 1")
            .replace("MISSES-COUNT 0", "MISSES-COUNT 1")
            .replace("KNIVES-OK\n", "");
        assert_eq!(
            reasons_of(&missed, 2),
            ["knives_not_all_bit", "knives_missed", "knives_ok_missing"]
        );
        let no_ok = body().replace("KNIVES-OK\n", "");
        assert_eq!(reasons_of(&no_ok, 2), ["knives_ok_missing"]);
        let no_count = body().replace("MISSES-COUNT 0\n", "");
        assert_eq!(reasons_of(&no_count, 2), ["misses_missing"]);
        let two = body().replace("BITES-COUNT 2\n", "BITES-COUNT 2\nBITES-COUNT 1\n");
        assert_eq!(reasons_of(&two, 2), ["bites_differs"]);
        let rc = body().replace("KNIVES-RC 0", "KNIVES-RC 1");
        assert_eq!(reasons_of(&rc, 2), ["knives_rc_nonzero"]);
        let no_rc = body().replace("KNIVES-RC 0\n", "");
        assert_eq!(reasons_of(&no_rc, 2), ["knives_rc_missing"]);
        let word = body().replace("KNIVES-EXPECTED 2", "KNIVES-EXPECTED two");
        assert_eq!(reasons_of(&word, 2), ["knives_expected_not_a_number"]);
    }

    #[test]
    fn a_red_or_narrow_line_is_never_green() {
        for red in RED_LINES {
            let r = body().replace("TREE-CLEAN\nHEAD-END", &format!("{red} why\nHEAD-END"));
            assert_eq!(
                reasons_of(&r, 2),
                [format!("red_line:{red}")],
                "reason: red_line_passed"
            );
        }
        let narrow = body().replace("RC fmt=0", "RECEIPT-NARROW tests=aibd:usage\nRC fmt=0");
        let narrow = narrow.replace("RC winclippy=0", "RC winclippy=skipped-narrow");
        let v = check(&seal(&narrow), H, 2);
        assert!(v.narrow && !v.green, "reason: narrow_green");
        assert_eq!(
            v.reasons,
            ["red_line:RECEIPT-NARROW", "rc_nonzero:winclippy"]
        );
        // the word inside another line is not the key
        let inside = body().replace("BRANCH feat/x", "BRANCH feat/RECEIPT-RED x");
        assert_eq!(reasons_of(&inside, 2), Vec::<String>::new());
        let longer = body().replace("BRANCH feat/x", "RECEIPT-REDUCED x");
        assert_eq!(reasons_of(&longer, 2), Vec::<String>::new());
    }
}
