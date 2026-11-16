//! A receipt read as the four states (SPEC-shadow-u0-model §1.4, 1ced7 H1 / M3 / M4 / N1–N3):
//! every line is `ran`, `reused`, `skipped` or `not_run`, and a receipt is green only when
//! every line is a pass, a reuse that names its source, or a skip that is harmless where the
//! receipt is used; at least one line ran; and the run ended on its normal path.
//!
//! New lines (what receipt-run.sh / cell-steps write after piece 1):
//! - `STEP ran pass 12s <name>` / `STEP ran fail rc=101 12s <name>`
//! - `STEP reused from=<run or file> key=<key> tree=<tree> at=<time> <name>`
//! - `STEP skipped why=<word> [os=…] [event=…] [reason=…] [bites_on=a,b] <name>`
//! - `STEP not_run why=<word> <name>`
//! - `KNIFE <test>: SKIPPED why=named_cannot_bite_here reason=<r> bites_on=<a,b>`
//! - `END rc=<n> complete=true|false`
//!
//! Old receipts (`STEP rc=0 12s <name>`, `SKIP-REUSED`, `NOT-MACOS`, `KNIVES-SKIPPED`,
//! `KNIVES-NOT-RUN`, `END rc=0 <date>`) are still read, marked legacy: never a source, and
//! their reuse lines carry no key, so they are `not_run`.

use crate::skip::{SkipWhy, Use};

/// One line of a receipt, in the four states.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Line {
    Ran {
        name: String,
        pass: bool,
        rc: Option<i32>,
        secs: Option<u64>,
    },
    Reused {
        name: String,
        from: String,
        key: String,
        tree: String,
        at: String,
    },
    Skipped {
        name: String,
        why: SkipWhy,
    },
    /// `why` is a [`SkipWhy`] word, or `no_knife_table` / `reuse_without_source` / a word the
    /// reader does not know: none of them is harmless.
    NotRun {
        name: String,
        why: String,
    },
}

/// A receipt read whole.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Receipt {
    pub lines: Vec<Line>,
    /// `END rc=<n> …`; `None` = no END line (a SIGKILL never writes one).
    pub end_rc: Option<i32>,
    /// `true` only from `END … complete=true`, written on the script's normal path.
    pub complete: bool,
    /// An old receipt: read, never a source.
    pub legacy: bool,
    /// A `KILLED` / `INCOMPLETE` / `STEP-STOPPED` line was seen.
    pub killed: bool,
}

/// `k=v` words of a line, and what is left (the step name, which may have spaces).
fn fields(rest: &str, keys: &[&str]) -> (Vec<(String, String)>, String) {
    let mut kv = Vec::new();
    let mut words = rest.split(' ').peekable();
    while let Some(&w) = words.peek() {
        match w.split_once('=') {
            Some((k, v)) if keys.contains(&k) => {
                kv.push((k.to_string(), v.to_string()));
                words.next();
            }
            _ => break,
        }
    }
    (kv, words.collect::<Vec<_>>().join(" "))
}

fn get<'a>(kv: &'a [(String, String)], k: &str) -> Option<&'a str> {
    kv.iter()
        .find(|(a, _)| a == k)
        .map(|(_, v)| v.as_str())
        .filter(|v| !v.is_empty())
}

/// `12s` ⇒ 12.
fn secs(w: &str) -> Option<u64> {
    w.strip_suffix('s')?.parse().ok()
}

fn skip_why(kv: &[(String, String)]) -> Result<SkipWhy, String> {
    let why = get(kv, "why").unwrap_or("");
    Ok(match why {
        "not_this_os" => SkipWhy::NotThisOs {
            os: get(kv, "os").unwrap_or("").to_string(),
        },
        "not_this_event" => SkipWhy::NotThisEvent {
            event: get(kv, "event").unwrap_or("").to_string(),
        },
        "user_filter" => SkipWhy::UserFilter,
        "named_cannot_bite_here" => SkipWhy::NamedCannotBiteHere {
            why: get(kv, "reason").unwrap_or("").to_string(),
            bites_on: get(kv, "bites_on")
                .map(|b| b.split(',').map(str::to_string).collect())
                .unwrap_or_default(),
        },
        "upstream_failed" => SkipWhy::UpstreamFailed,
        "timeout_budget" => SkipWhy::TimeoutBudget,
        "engine_skip" => SkipWhy::EngineSkip,
        other => return Err(if other.is_empty() { "unknown" } else { other }.to_string()),
    })
}

const STEP_KEYS: &[&str] = &[
    "rc", "from", "key", "tree", "at", "why", "os", "event", "reason", "bites_on",
];

/// One `STEP …` line (without `STEP `).
fn step(rest: &str) -> Option<Line> {
    let (state, rest) = rest.split_once(' ')?;
    match state {
        "ran" => {
            let (result, rest) = rest.split_once(' ')?;
            let (kv, rest) = fields(rest, &["rc"]);
            let (t, name) = rest.split_once(' ').unwrap_or((rest.as_str(), ""));
            let pass = match result {
                "pass" => true,
                "fail" => false,
                _ => return None,
            };
            Some(Line::Ran {
                name: name.to_string(),
                pass,
                rc: get(&kv, "rc").and_then(|r| r.parse().ok()),
                secs: secs(t),
            })
        }
        "reused" => {
            let (kv, name) = fields(rest, STEP_KEYS);
            // a reuse that does not name its source, key, tree and time is not a run (M4)
            match (
                get(&kv, "from"),
                get(&kv, "key"),
                get(&kv, "tree"),
                get(&kv, "at"),
            ) {
                (Some(from), Some(key), Some(tree), Some(at)) => Some(Line::Reused {
                    name,
                    from: from.to_string(),
                    key: key.to_string(),
                    tree: tree.to_string(),
                    at: at.to_string(),
                }),
                _ => Some(Line::NotRun {
                    name,
                    why: "reuse_without_source".into(),
                }),
            }
        }
        "skipped" => {
            let (kv, name) = fields(rest, STEP_KEYS);
            Some(match skip_why(&kv) {
                Ok(why) => Line::Skipped { name, why },
                Err(why) => Line::NotRun { name, why },
            })
        }
        "not_run" => {
            let (kv, name) = fields(rest, STEP_KEYS);
            Some(Line::NotRun {
                name,
                why: get(&kv, "why").unwrap_or("unknown").to_string(),
            })
        }
        _ => None,
    }
}

/// An old `STEP rc=N 12s <name>` line.
fn legacy_step(rest: &str) -> Option<Line> {
    let (rc, rest) = rest.strip_prefix("rc=")?.split_once(' ')?;
    let rc: i32 = rc.parse().ok()?;
    let (t, name) = rest.split_once(' ').unwrap_or((rest, ""));
    Some(Line::Ran {
        name: name.trim().to_string(),
        pass: rc == 0,
        rc: Some(rc),
        secs: secs(t),
    })
}

/// Read a receipt's text.
pub fn parse(text: &str) -> Receipt {
    let mut r = Receipt::default();
    for raw in text.lines() {
        let l = raw.trim_end();
        if let Some(rest) = l.strip_prefix("STEP ") {
            if let Some(line) = step(rest) {
                r.lines.push(line);
            } else if let Some(line) = legacy_step(rest) {
                r.legacy = true;
                r.lines.push(line);
            } else if !rest.starts_with("STOPPED") {
                // a STEP line nobody can read is not a pass
                r.lines.push(Line::NotRun {
                    name: rest.to_string(),
                    why: "unreadable".into(),
                });
            }
            if rest.starts_with("STOPPED") {
                r.killed = true;
            }
        } else if let Some(name) = l.strip_prefix("SKIP-REUSED ") {
            r.legacy = true;
            r.lines.push(Line::NotRun {
                name: name.to_string(),
                why: "reuse_without_source".into(),
            });
        } else if let Some(name) = l.strip_prefix("NOT-MACOS ") {
            r.legacy = true;
            r.lines.push(Line::Skipped {
                name: name.to_string(),
                why: SkipWhy::NotThisOs { os: "macos".into() },
            });
        } else if l.starts_with("KNIVES-SKIPPED") {
            r.legacy = true;
            r.lines.push(Line::NotRun {
                name: l.to_string(),
                why: SkipWhy::UpstreamFailed.word().into(),
            });
        } else if l.starts_with("KNIVES-NOT-RUN") {
            r.legacy = true;
            r.lines.push(Line::NotRun {
                name: l.to_string(),
                why: "no_knife_table".into(),
            });
        } else if let Some((test, rest)) = l
            .strip_prefix("KNIFE ")
            .and_then(|k| k.split_once(": SKIPPED "))
        {
            let (kv, _) = fields(rest, STEP_KEYS);
            r.lines.push(match skip_why(&kv) {
                Ok(why) => Line::Skipped {
                    name: test.to_string(),
                    why,
                },
                Err(why) => Line::NotRun {
                    name: test.to_string(),
                    why,
                },
            });
        } else if l.starts_with("KILLED") || l.contains("INCOMPLETE") {
            r.killed = true;
        } else if let Some(rest) = l.strip_prefix("END rc=") {
            let (rc, more) = rest.split_once(' ').unwrap_or((rest, ""));
            r.end_rc = rc.parse().ok();
            // only the normal path writes complete=true; an old END line says nothing
            r.complete = more.split(' ').any(|w| w == "complete=true");
            if !more.split(' ').any(|w| w.starts_with("complete=")) {
                r.legacy = true;
            }
        }
    }
    r
}

/// Why a receipt is not green; empty = green. `used` picks the skip white list (a receipt
/// is [`Use::Evidence`]); `bite_shown` answers, for a knife named as unable to bite here,
/// whether a bite on one of the named machines can be shown (1ced7 N3).
pub fn not_green(r: &Receipt, used: Use, bite_shown: impl Fn(&[String]) -> bool) -> Vec<String> {
    let mut why = Vec::new();
    match r.end_rc {
        None => why.push("no END line (the run did not finish)".to_string()),
        Some(0) => {}
        Some(n) => why.push(format!("END rc={n}")),
    }
    if !r.complete {
        why.push("not complete=true".into());
    }
    if r.killed {
        why.push("killed / incomplete".into());
    }
    if !r.lines.iter().any(|l| matches!(l, Line::Ran { .. })) {
        why.push("nothing ran".into());
    }
    for l in &r.lines {
        match l {
            Line::Ran { pass: true, .. } | Line::Reused { .. } => {}
            Line::Ran { name, rc, .. } => why.push(format!("{name}: failed (rc {rc:?})")),
            Line::Skipped { name, why: w } => {
                if !w.harmless(used, &bite_shown) {
                    why.push(format!("{name}: skipped why={}", w.word()));
                }
            }
            Line::NotRun { name, why: w } => why.push(format!("{name}: not_run why={w}")),
        }
    }
    why
}

/// Green: nothing in [`not_green`].
pub fn is_green(r: &Receipt, used: Use, bite_shown: impl Fn(&[String]) -> bool) -> bool {
    not_green(r, used, bite_shown).is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_bite(_: &[String]) -> bool {
        false
    }

    fn green(text: &str) -> Vec<String> {
        not_green(&parse(text), Use::Evidence, no_bite)
    }

    const OK: &str = "STEP ran pass 3s Toolchain\nSTEP ran pass 120s aib-drive knives (macOS)\nEND rc=0 complete=true\n";

    #[test]
    fn every_line_passing_and_a_normal_end_is_green() {
        assert_eq!(green(OK), Vec::<String>::new());
        let r = parse(OK);
        assert!(!r.legacy);
        assert_eq!(
            r.lines[1],
            Line::Ran {
                name: "aib-drive knives (macOS)".into(),
                pass: true,
                rc: None,
                secs: Some(120)
            }
        );
    }

    #[test]
    fn a_failed_upstream_step_leaves_the_knives_not_run() {
        // 1ced7 反例 1 (receipt-run 122, c1fb5 b0238060): clippy red ⇒ no knife ran
        let t = "STEP ran fail rc=101 40s clippy\nSTEP not_run why=upstream_failed knives\nEND rc=1 complete=true\n";
        let w = green(t);
        assert!(
            w.iter().any(|x| x.contains("not_run why=upstream_failed")),
            "{w:?}"
        );
        let old = "STEP rc=0 3s fmt\nKNIVES-SKIPPED (clippy or test failed, so no knife ran)\nEND rc=0 2026-11-16 10:00:00\n";
        let w = green(old);
        assert!(w.iter().any(|x| x.contains("upstream_failed")), "{w:?}");
    }

    #[test]
    fn a_receipt_with_no_end_line_is_not_complete() {
        // 1ced7 反例 2: a SIGKILL writes no END line, and the trap does not run
        let w = green("STEP ran pass 3s Toolchain\n");
        assert!(w.iter().any(|x| x.contains("no END line")), "{w:?}");
        // the trap's END line says complete=false
        let w = green("STEP ran pass 3s Toolchain\nKILLED by TERM (run not complete)\nEND rc=1 complete=false\n");
        assert!(w.iter().any(|x| x.contains("not complete=true")), "{w:?}");
        assert!(w.iter().any(|x| x.contains("killed")), "{w:?}");
    }

    #[test]
    fn a_reuse_without_its_source_is_not_run() {
        // 1ced7 反例 3 / M4
        let full = "STEP ran pass 3s Toolchain\nSTEP reused from=43845696225/2 key=k1 tree=t1 at=2026-11-16T22:55:44Z knife anchors\nEND rc=0 complete=true\n";
        assert_eq!(green(full), Vec::<String>::new());
        for drop in [
            "from=43845696225/2 ",
            "key=k1 ",
            "tree=t1 ",
            "at=2026-11-16T22:55:44Z ",
        ] {
            let w = green(&full.replace(drop, ""));
            assert!(
                w.iter().any(|x| x.contains("reuse_without_source")),
                "{drop}: {w:?}"
            );
        }
        // the old SKIP-REUSED line names no source at all
        let w = green(
            "STEP rc=0 3s Toolchain\nSKIP-REUSED Core purity\nEND rc=0 2026-11-16 22:50:05\n",
        );
        assert!(
            w.iter()
                .any(|x| x.contains("Core purity: not_run why=reuse_without_source")),
            "{w:?}"
        );
    }

    #[test]
    fn an_old_receipt_is_read_and_marked_legacy() {
        // 1ced7 反例 4
        let r = parse("NOT-MACOS Windows version\nSTEP rc=0 522s  aib-drive act knives (macOS)\nCELL-RED 0\nEND rc=0 2026-11-16 22:50:05\n");
        assert!(r.legacy);
        assert!(!r.complete);
        assert_eq!(
            r.lines[1],
            Line::Ran {
                name: "aib-drive act knives (macOS)".into(),
                pass: true,
                rc: Some(0),
                secs: Some(522)
            }
        );
        assert!(matches!(
            r.lines[0],
            Line::Skipped {
                why: SkipWhy::NotThisOs { .. },
                ..
            }
        ));
    }

    #[test]
    fn a_user_filter_skip_is_not_green_in_a_receipt() {
        // 1ced7 N2: a receipt needs a real run for every unit; the user's own run does not
        let t = "STEP ran pass 3s Toolchain\nSTEP skipped why=user_filter knives\nEND rc=0 complete=true\n";
        assert!(!green(t).is_empty());
        assert!(is_green(&parse(t), Use::Local, no_bite));
        let os = "STEP ran pass 3s Toolchain\nSTEP skipped why=not_this_os os=windows win knives\nEND rc=0 complete=true\n";
        assert_eq!(green(os), Vec::<String>::new());
    }

    #[test]
    fn a_knife_that_cannot_bite_here_needs_a_bite_shown_elsewhere() {
        // 1ced7 N3: the L2 knife on a macOS where sysinfo reads a cleared environment right
        let t = "STEP ran pass 300s rust knives\nKNIFE a_cleared_environment_reads_as_empty_not_as_apple_strings: SKIPPED why=named_cannot_bite_here reason=sysinfo_reads_right bites_on=macos-26\nEND rc=0 complete=true\n";
        assert!(!green(t).is_empty());
        let shown = |on: &[String]| on == ["macos-26".to_string()];
        assert!(is_green(&parse(t), Use::Evidence, shown));
        // no machine named ⇒ never harmless
        let bare = t.replace(" bites_on=macos-26", "");
        assert!(!is_green(&parse(&bare), Use::Evidence, |_| true));
    }

    #[test]
    fn an_unknown_reason_or_an_unreadable_step_is_not_run() {
        for t in [
            "STEP ran pass 3s a\nSTEP skipped why=because b\nEND rc=0 complete=true\n",
            "STEP ran pass 3s a\nSTEP skipped b\nEND rc=0 complete=true\n",
            "STEP ran pass 3s a\nSTEP maybe b\nEND rc=0 complete=true\n",
            "STEP ran pass 3s a\nKNIVES-NOT-RUN (no knife table given)\nEND rc=0 complete=true\n",
        ] {
            assert!(!green(t).is_empty(), "{t}");
        }
    }

    #[test]
    fn nothing_ran_is_not_green() {
        let t = "STEP skipped why=not_this_os os=macos a\nEND rc=0 complete=true\n";
        assert!(green(t).iter().any(|x| x == "nothing ran"));
    }
}
