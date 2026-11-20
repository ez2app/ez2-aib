//! aib-receipt-gate: the only program aibd's delegate key runs on a test machine
//! (DESIGN-receipt-gate-a9136-1120 r2; f1fae PLAN-trusted-delegate-hosts-token r1 §1 `restrict,command=`).
//!
//! Slice 1: reading the command. sshd hands the client's words in `SSH_ORIGINAL_COMMAND`; the
//! gate splits them on single spaces only (no quotes, escapes or shell), checks each word's
//! shape and returns one [`Request`]. Anything else is refused with a short code. Nothing here
//! touches a file, git or a process: receiving, checking and starting come in slices 2 and 3.

pub mod plan;
pub mod recv;
pub mod run;
pub mod sandbox;

/// Largest bundle the gate takes (bytes).
pub const MAX_BUNDLE: u64 = 300 * 1024 * 1024;
/// Largest knife table the gate takes (bytes); 0 = no knives.
pub const MAX_KNIVES: u64 = 256 * 1024;
/// Longest command line read.
const MAX_LINE: usize = 512;

/// One request the gate serves.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Request {
    Run {
        run_id: String,
        head: String,
        krate: String,
        bundle_len: u64,
        bundle_sha256: String,
        knives_len: u64,
        knives_sha256: String,
    },
    Status {
        run_id: String,
    },
    Fetch {
        run_id: String,
    },
    Cancel {
        run_id: String,
    },
}

fn hex(s: &str, n: usize) -> bool {
    s.len() == n
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `web-<5 of the seat>-<9 hex of the head>`, and `-2` … `-99` when that head is asked again
/// (the shape aibd's `receipt_run::run_id_ok` takes; design r3 §1).
pub fn run_id_ok(s: &str) -> bool {
    head9_of(s).is_some()
}

/// The nine head characters a run id names; `None` for any other shape.
pub fn head9_of(s: &str) -> Option<&str> {
    let mut p = s.split('-');
    let (web, seat, h9, again, end) = (p.next(), p.next(), p.next(), p.next(), p.next());
    let seat_ok = seat.is_some_and(|x| {
        x.len() == 5
            && x.bytes()
                .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
    });
    let again_ok = again.is_none_or(|n| {
        !n.starts_with('0') && n.parse::<u8>().is_ok_and(|n| (2..=99).contains(&n))
    });
    if web != Some("web") || !seat_ok || !again_ok || end.is_some() {
        return None;
    }
    h9.filter(|h| hex(h, 9))
}

/// A workspace crate name or `ws`.
pub fn crate_ok(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=40).contains(&b.len())
        && b[0].is_ascii_lowercase()
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-' || *c == b'_')
}

fn len_ok(s: &str, max: u64) -> Option<u64> {
    if s.is_empty() || s.len() > 10 || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    if s.len() > 1 && s.starts_with('0') {
        return None;
    }
    s.parse::<u64>().ok().filter(|n| *n <= max)
}

/// The words of `line`: printable ASCII split on single spaces. A tab, newline, a doubled or
/// edge space, a quote, a shell character or any non-ASCII byte refuses the line (5d33a
/// PREREAD-receiptgate Q1): every field is one plain word, so nothing needs quoting.
pub fn words(line: &str) -> Result<Vec<&str>, &'static str> {
    if line.is_empty() {
        return Err("no_command");
    }
    if line.len() > MAX_LINE {
        return Err("line_too_long");
    }
    if !line
        .bytes()
        .all(|b| b == b' ' || (b.is_ascii_graphic() && !b"\"'`\\$;&|<>()*?[]{}~#!".contains(&b)))
    {
        return Err("bad_words");
    }
    let w: Vec<&str> = line.split(' ').collect();
    if w.iter().any(|x| x.is_empty()) {
        return Err("bad_words");
    }
    Ok(w)
}

/// The request in `SSH_ORIGINAL_COMMAND` (`None` = an interactive login, refused).
pub fn parse(command: Option<&str>) -> Result<Request, &'static str> {
    let w = words(command.ok_or("no_command")?)?;
    let one = |w: &[&str]| -> Result<String, &'static str> {
        match w {
            [id] if run_id_ok(id) => Ok((*id).to_owned()),
            [_] => Err("bad_run_id"),
            _ => Err("bad_arity"),
        }
    };
    match (w[0], &w[1..]) {
        ("run", [id, head, krate, bl, bs, kl, ks]) => {
            if !run_id_ok(id) {
                return Err("bad_run_id");
            }
            if !hex(head, 40) {
                return Err("bad_head");
            }
            // the run id names the head it tests
            if head9_of(id) != head.get(..9) {
                return Err("run_id_not_head");
            }
            if !crate_ok(krate) {
                return Err("bad_crate");
            }
            let bundle_len = len_ok(bl, MAX_BUNDLE)
                .filter(|n| *n > 0)
                .ok_or("bad_bundle_len")?;
            let knives_len = len_ok(kl, MAX_KNIVES).ok_or("bad_knives_len")?;
            if !hex(bs, 64) || !hex(ks, 64) {
                return Err("bad_sha256");
            }
            if *krate == "ws" && knives_len > 0 {
                // knives fit one crate (receipt-run: ws + knives is rc 64)
                return Err("knives_need_one_crate");
            }
            Ok(Request::Run {
                run_id: (*id).to_owned(),
                head: (*head).to_owned(),
                krate: (*krate).to_owned(),
                bundle_len,
                bundle_sha256: (*bs).to_owned(),
                knives_len,
                knives_sha256: (*ks).to_owned(),
            })
        }
        ("run", _) => Err("bad_arity"),
        ("status", rest) => Ok(Request::Status { run_id: one(rest)? }),
        ("fetch", rest) => Ok(Request::Fetch { run_id: one(rest)? }),
        ("cancel", rest) => Ok(Request::Cancel { run_id: one(rest)? }),
        _ => Err("verb_unknown"),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const HEAD: &str = "0123456789abcdef0123456789abcdef01234567";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn run(krate: &str, bl: &str, kl: &str) -> String {
        format!("run web-a9136-012345678 {HEAD} {krate} {bl} {SHA} {kl} {SHA}")
    }

    #[test]
    fn only_four_verbs_of_plain_words_are_read() {
        assert_eq!(parse(None), Err("no_command"), "reason: login_taken");
        assert_eq!(parse(Some("")), Err("no_command"));
        assert_eq!(
            parse(Some("sh")),
            Err("verb_unknown"),
            "reason: shell_taken"
        );
        assert_eq!(
            parse(Some("scp -t .")),
            Err("verb_unknown"),
            "reason: scp_taken"
        );
        for bad in [
            "status  web-a9136-012345678",
            " status web-a9136-012345678",
            "status web-a9136-012345678 ",
            "status\tweb-a9136-012345678",
            "status web-a9136-012345678\nid",
            "status web-a9136-012345678;id",
            "status 'web-a9136-012345678'",
            "status $(id)",
            "status web-a9136-01234567８",
        ] {
            assert_eq!(parse(Some(bad)), Err("bad_words"), "reason: taken {bad:?}");
        }
        assert_eq!(parse(Some(&"x".repeat(MAX_LINE + 1))), Err("line_too_long"));
        assert_eq!(
            parse(Some("status web-a9136-012345678")),
            Ok(Request::Status {
                run_id: "web-a9136-012345678".into()
            })
        );
        assert!(matches!(
            parse(Some("fetch web-a9136-012345678")),
            Ok(Request::Fetch { .. })
        ));
        assert!(matches!(
            parse(Some("cancel web-a9136-012345678")),
            Ok(Request::Cancel { .. })
        ));
        assert_eq!(
            parse(Some("status web-a9136-012345678 x")),
            Err("bad_arity")
        );
        assert_eq!(parse(Some("status ../x")), Err("bad_run_id"));
        for other in [
            "status a9136-g1-012345678",
            "status web-A9136-012345678",
            "status web-a913-012345678",
            "status web-a9136-01234567",
            "status web-a9136-012345678-1",
            "status web-a9136-012345678-02",
            "status web-a9136-012345678-100",
            "status web-a9136-012345678-2-3",
        ] {
            assert_eq!(
                parse(Some(other)),
                Err("bad_run_id"),
                "reason: taken {other:?}"
            );
        }
        assert!(parse(Some("status web-a9136-012345678-2")).is_ok());
        assert!(parse(Some("fetch web-zz0q9-012345678-99")).is_ok());
    }

    #[test]
    fn a_run_names_its_head_and_its_sizes() {
        let r = parse(Some(&run("aibd", "1000", "0"))).unwrap();
        assert_eq!(
            r,
            Request::Run {
                run_id: "web-a9136-012345678".into(),
                head: HEAD.into(),
                krate: "aibd".into(),
                bundle_len: 1000,
                bundle_sha256: SHA.into(),
                knives_len: 0,
                knives_sha256: SHA.into(),
            }
        );
        assert!(parse(Some(&run("ws", "1000", "0"))).is_ok());
        // the run id ends with another head
        let other = run("aibd", "1", "0").replace("web-a9136-012345678", "web-a9136-987654321");
        assert_eq!(
            parse(Some(&other)),
            Err("run_id_not_head"),
            "reason: head_mismatch_taken"
        );
        // asked again: the same head, a number after it
        let again = run("aibd", "1", "0").replace("web-a9136-012345678", "web-a9136-012345678-2");
        assert!(parse(Some(&again)).is_ok());
        let again = run("aibd", "1", "0").replace("web-a9136-012345678", "web-a9136-987654321-2");
        assert_eq!(parse(Some(&again)), Err("run_id_not_head"));
        let short = run("aibd", "1", "0").replace(HEAD, &HEAD[..39]);
        assert_eq!(parse(Some(&short)), Err("bad_head"));
        assert_eq!(parse(Some(&run("Aibd", "1", "0"))), Err("bad_crate"));
        assert_eq!(parse(Some(&run("aibd", "0", "0"))), Err("bad_bundle_len"));
        assert_eq!(parse(Some(&run("aibd", "01", "0"))), Err("bad_bundle_len"));
        assert_eq!(
            parse(Some(&run("aibd", &(MAX_BUNDLE + 1).to_string(), "0"))),
            Err("bad_bundle_len"),
            "reason: big_bundle_taken"
        );
        assert_eq!(
            parse(Some(&run("aibd", "1", &(MAX_KNIVES + 1).to_string()))),
            Err("bad_knives_len"),
            "reason: big_knives_taken"
        );
        assert_eq!(
            parse(Some(&run("aibd", "1", "0").replace(SHA, &SHA[..63]))),
            Err("bad_sha256")
        );
        assert_eq!(
            parse(Some(&run("ws", "1", "10"))),
            Err("knives_need_one_crate")
        );
        assert_eq!(parse(Some("run web-a9136-012345678")), Err("bad_arity"));
    }
}
