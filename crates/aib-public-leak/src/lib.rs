// SPDX-License-Identifier: Apache-2.0
//! RULES §16 (approvals-to-rust C10): the public repository never names a host.
//!
//! What a PR adds is checked: the added lines of its diff (`+` lines and the paths they are
//! in), its commit messages, and its title / body. Two rules:
//! - the denylist (`AIB_PUBLIC_DENYLIST`, one entry per line, case-insensitive substring;
//!   empty lines and lines starting with `#` are skipped). It comes from a secret, so it is
//!   absent on fork PRs: then only the IPv4 rule runs and the report says `denylist_absent`.
//! - public IPv4 addresses. Allowed: the documentation ranges (192.0.2/24, 198.51.100/24,
//!   203.0.113/24), private and shared ranges (10/8, 172.16/12, 192.168/16, 100.64/10),
//!   loopback 127/8, and ranges that cannot be a host's own public address (0/8,
//!   link-local 169.254/16, multicast 224/4, reserved 240/4).
//!
//! A finding names the place (file and line, commit and line, PR title / body line) and the
//! rule (denylist entry number, or "public IPv4"), never the matched text: the report itself
//! is public. A path that hits is shown by its position in the diff, not by name.
//! IPv6 is not checked.

/// Environment variable holding the denylist.
pub const DENYLIST_ENV: &str = "AIB_PUBLIC_DENYLIST";

/// Which rule a finding broke.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rule {
    /// Entry number in the denylist, from 1 (comment and empty lines not counted).
    Denylist(usize),
    PublicIpv4,
}

/// Where a finding is. Paths are only kept when the path itself is clean.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Place {
    /// An added line. `path` is None when the path itself hit a rule; `file` is the file's
    /// position in the diff (from 1).
    Added {
        file: usize,
        path: Option<String>,
        line: u32,
    },
    /// The path of the `file`-th file of the diff.
    Path {
        file: usize,
    },
    /// A line of a commit message (`sha` shortened to 12).
    Commit {
        sha: String,
        line: u32,
    },
    PrTitle,
    PrBody {
        line: u32,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub place: Place,
    pub rule: Rule,
}

/// The denylist, lower-cased.
#[derive(Debug, Clone, Default)]
pub struct Denylist {
    entries: Vec<String>,
}

impl Denylist {
    pub fn parse(text: &str) -> Denylist {
        let entries = text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(str::to_lowercase)
            .collect();
        Denylist { entries }
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Number (from 1) of the first entry found in `text`, ignoring case.
    pub fn first_hit(&self, text: &str) -> Option<usize> {
        if self.entries.is_empty() {
            return None;
        }
        let low = text.to_lowercase();
        self.entries
            .iter()
            .position(|e| low.contains(e.as_str()))
            .map(|i| i + 1)
    }
}

/// Whether an IPv4 address may appear in public text.
pub fn allowed_ipv4(o: [u8; 4]) -> bool {
    let [a, b, c, _] = o;
    a == 0
        || a == 10
        || a == 127
        || a >= 224
        || (a == 100 && (64..=127).contains(&b))
        || (a == 169 && b == 254)
        || (a == 172 && (16..=31).contains(&b))
        || (a == 192 && b == 168)
        || (a == 192 && b == 0 && c == 2)
        || (a == 198 && b == 51 && c == 100)
        || (a == 203 && b == 0 && c == 113)
}

/// Every dotted-quad IPv4 address in `text`. A run of digits and dots that is longer than
/// four parts (a version like 1.2.3.4.5) is not an address.
pub fn ipv4_in(text: &str) -> Vec<[u8; 4]> {
    let b = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        let starts =
            b[i].is_ascii_digit() && (i == 0 || !(b[i - 1].is_ascii_digit() || b[i - 1] == b'.'));
        if starts {
            if let Some((o, end)) = quad_at(b, i) {
                out.push(o);
                i = end;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// A dotted quad starting at `i`, and the index after it.
fn quad_at(b: &[u8], mut i: usize) -> Option<([u8; 4], usize)> {
    let mut o = [0u8; 4];
    for (k, slot) in o.iter_mut().enumerate() {
        if k > 0 {
            if b.get(i) != Some(&b'.') {
                return None;
            }
            i += 1;
        }
        let start = i;
        let mut v: u32 = 0;
        while i < b.len() && b[i].is_ascii_digit() && i - start < 3 {
            v = v * 10 + u32::from(b[i] - b'0');
            i += 1;
        }
        if i == start || v > 255 {
            return None;
        }
        *slot = v as u8;
    }
    let digit_after = b.get(i).is_some_and(u8::is_ascii_digit);
    let part_after = b.get(i) == Some(&b'.') && b.get(i + 1).is_some_and(u8::is_ascii_digit);
    if digit_after || part_after {
        return None;
    }
    Some((o, i))
}

/// The rules one piece of text breaks (denylist first, then IPv4; each at most once).
pub fn check(text: &str, deny: &Denylist) -> Vec<Rule> {
    let mut out = Vec::new();
    if let Some(n) = deny.first_hit(text) {
        out.push(Rule::Denylist(n));
    }
    if ipv4_in(text).into_iter().any(|o| !allowed_ipv4(o)) {
        out.push(Rule::PublicIpv4);
    }
    out
}

/// One file of a unified diff: its new path and its added lines with their new line numbers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AddedFile {
    pub path: String,
    pub lines: Vec<(u32, String)>,
}

/// Parse `git diff -U0` output into the added lines per file. Deleted files are left out;
/// binary files keep their path and no lines.
pub fn parse_diff(diff: &str) -> Vec<AddedFile> {
    let mut files: Vec<AddedFile> = Vec::new();
    let mut in_hunk = false;
    let mut next_line: u32 = 0;
    let mut deleted = false;
    // this file's entry came from its `diff --git` line (043dd #636 L2: a new empty file or
    // a mode-only change has no `+++` line, and its path must still be checked)
    let mut from_header = false;
    for l in diff.lines() {
        if let Some(rest) = l.strip_prefix("diff --git ") {
            in_hunk = false;
            deleted = false;
            from_header = false;
            if let Some(p) = header_path(rest) {
                files.push(AddedFile {
                    path: p.to_string(),
                    lines: Vec::new(),
                });
                from_header = true;
            }
            continue;
        }
        if !in_hunk {
            if l.starts_with("deleted file mode ") || l.starts_with("+++ /dev/null") {
                if from_header && !deleted {
                    files.pop(); // a removed path adds nothing
                }
                deleted = true;
            } else if let Some(p) = l.strip_prefix("+++ b/") {
                let p = p.trim_end_matches('\t').to_string();
                match files.last_mut() {
                    Some(f) if from_header => f.path = p,
                    _ => files.push(AddedFile {
                        path: p,
                        lines: Vec::new(),
                    }),
                }
            } else if let Some(rest) = l.strip_prefix("Binary files ") {
                if let Some(p) = rest
                    .split(" and b/")
                    .nth(1)
                    .and_then(|s| s.strip_suffix(" differ"))
                {
                    if !from_header {
                        files.push(AddedFile {
                            path: p.to_string(),
                            lines: Vec::new(),
                        });
                    }
                }
            }
        }
        if let Some(h) = l.strip_prefix("@@ ") {
            in_hunk = !deleted;
            next_line = hunk_new_start(h).unwrap_or(0);
            continue;
        }
        if !in_hunk {
            continue;
        }
        if let Some(text) = l.strip_prefix('+') {
            if let Some(f) = files.last_mut() {
                f.lines.push((next_line, text.to_string()));
            }
            next_line += 1;
        } else if l.starts_with(' ') {
            next_line += 1;
        }
    }
    files
}

/// The path of `a/P b/P` (the rest of a `diff --git` line; with `--no-renames` both sides are
/// the same path, so a path holding " b/" is still split right). Quoted paths (git quotes
/// `"`, `\` and control characters even with core.quotePath=false) ⇒ None: the `+++` line is
/// then the only source, as before.
fn header_path(rest: &str) -> Option<&str> {
    let s = rest.strip_prefix("a/")?;
    // s = P + " b/" + P
    let n = s.len().checked_sub(3)?;
    if n % 2 != 0 {
        return None;
    }
    let (p, tail) = (s.get(..n / 2)?, s.get(n / 2..)?);
    (tail.strip_prefix(" b/")? == p && !p.is_empty()).then_some(p)
}

/// `-a,b +c,d @@ …` ⇒ c.
fn hunk_new_start(h: &str) -> Option<u32> {
    let plus = h.split_whitespace().find(|w| w.starts_with('+'))?;
    plus[1..].split(',').next()?.parse().ok()
}

/// Commit messages from `git log --format=%H%x00%B%x1e`.
pub fn parse_log(log: &str) -> Vec<(String, String)> {
    log.split('\x1e')
        .filter_map(|rec| {
            let rec = rec.trim_start_matches('\n');
            let (sha, body) = rec.split_once('\0')?;
            Some((sha.trim().to_string(), body.to_string()))
        })
        .filter(|(sha, _)| !sha.is_empty())
        .collect()
}

/// PR title and body from a GitHub event payload (pull_request.title / .body).
pub fn pr_text(event: &serde_json::Value) -> (Option<String>, Option<String>) {
    let pr = &event["pull_request"];
    let s = |k: &str| pr[k].as_str().map(str::to_string);
    (s("title"), s("body"))
}

/// Everything a PR adds that is checked.
#[derive(Debug, Default)]
pub struct Input {
    pub files: Vec<AddedFile>,
    pub commits: Vec<(String, String)>,
    pub title: Option<String>,
    pub body: Option<String>,
}

/// All findings, in the order: files (path, then lines), commits, title, body.
pub fn scan(input: &Input, deny: &Denylist) -> Vec<Finding> {
    let mut out = Vec::new();
    for (i, f) in input.files.iter().enumerate() {
        let file = i + 1;
        let path_rules = check(&f.path, deny);
        let path = path_rules.is_empty().then(|| f.path.clone());
        for rule in path_rules {
            out.push(Finding {
                place: Place::Path { file },
                rule,
            });
        }
        for (line, text) in &f.lines {
            for rule in check(text, deny) {
                out.push(Finding {
                    place: Place::Added {
                        file,
                        path: path.clone(),
                        line: *line,
                    },
                    rule,
                });
            }
        }
    }
    for (sha, msg) in &input.commits {
        let short: String = sha.chars().take(12).collect();
        for (k, text) in msg.lines().enumerate() {
            for rule in check(text, deny) {
                out.push(Finding {
                    place: Place::Commit {
                        sha: short.clone(),
                        line: k as u32 + 1,
                    },
                    rule,
                });
            }
        }
    }
    if let Some(t) = &input.title {
        for rule in check(t, deny) {
            out.push(Finding {
                place: Place::PrTitle,
                rule,
            });
        }
    }
    if let Some(b) = &input.body {
        for (k, text) in b.lines().enumerate() {
            for rule in check(text, deny) {
                out.push(Finding {
                    place: Place::PrBody { line: k as u32 + 1 },
                    rule,
                });
            }
        }
    }
    out
}

/// One report line. Never contains the matched text.
pub fn report_line(f: &Finding) -> String {
    let place = match &f.place {
        Place::Added {
            file,
            path: Some(p),
            line,
        } => format!("{p}:{line} (file #{file})"),
        Place::Added {
            file,
            path: None,
            line,
        } => format!("file #{file} (path withheld):{line}"),
        Place::Path { file } => format!("path of file #{file}"),
        Place::Commit { sha, line } => format!("commit {sha} message line {line}"),
        Place::PrTitle => "PR title".to_string(),
        Place::PrBody { line } => format!("PR body line {line}"),
    };
    let rule = match f.rule {
        Rule::Denylist(n) => format!("denylist entry #{n}"),
        Rule::PublicIpv4 => "public IPv4 address".to_string(),
    };
    format!("public-leak: {place}: {rule}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A public address built at run time, so no literal one is in this file (RULES §16).
    fn public() -> String {
        format!("{}.{}.{}.{}", 1, 2, 3, 4)
    }

    #[test]
    fn denylist_skips_comments_and_ignores_case() {
        let d = Denylist::parse("# hosts\n\n  ExampleHost \nexample-dc\n");
        assert_eq!(d.len(), 2);
        assert_eq!(d.first_hit("deployed on EXAMPLEHOST today"), Some(1));
        assert_eq!(d.first_hit("in Example-DC"), Some(2));
        assert_eq!(d.first_hit("nothing here"), None);
        assert!(Denylist::parse("# only a comment\n").is_empty());
        // One pattern per line, the whole line, literally: a phrase with spaces, an address,
        // regex characters taken as text.
        let d = Denylist::parse("Example Data Center\r\n198.51.100.77\na.c\n");
        assert_eq!(d.first_hit("in the example data center"), Some(1));
        assert_eq!(
            d.first_hit("example center"),
            None,
            "the phrase is not split into words"
        );
        assert_eq!(d.first_hit("ip 198.51.100.77 here"), Some(2));
        assert_eq!(d.first_hit("abc"), None, "not a regex");
        assert_eq!(d.first_hit("x a.c y"), Some(3));
    }

    #[test]
    fn documentation_and_private_ranges_pass_public_ones_do_not() {
        for ok in [
            [203, 0, 113, 9],
            [192, 0, 2, 1],
            [198, 51, 100, 7],
            [10, 1, 2, 3],
            [172, 16, 0, 1],
            [172, 31, 255, 255],
            [192, 168, 1, 1],
            [100, 64, 0, 1],
            [100, 127, 0, 1],
            [127, 0, 0, 1],
            [0, 0, 0, 0],
            [169, 254, 1, 1],
            [224, 0, 0, 1],
            [255, 255, 255, 0],
        ] {
            assert!(allowed_ipv4(ok), "{ok:?} should be allowed");
        }
        for bad in [
            [1, 2, 3, 4],
            [172, 32, 0, 1],
            [100, 128, 0, 1],
            [203, 0, 114, 1],
            [192, 0, 3, 1],
            [198, 51, 101, 1],
        ] {
            assert!(!allowed_ipv4(bad), "{bad:?} is public");
        }
    }

    #[test]
    fn addresses_are_found_but_longer_dotted_runs_are_not() {
        let p = public();
        assert_eq!(ipv4_in(&format!("ssh root@{p}:22")), vec![[1, 2, 3, 4]]);
        assert_eq!(ipv4_in(&format!("({p})")), vec![[1, 2, 3, 4]]);
        assert!(
            ipv4_in(&format!("v{p}.5")).is_empty(),
            "five parts is a version"
        );
        assert!(
            ipv4_in(&format!("0.{p}")).is_empty(),
            "five parts, counted from the front"
        );
        assert!(ipv4_in("1.2.3.256").is_empty(), "octet over 255");
        assert!(ipv4_in("1.2.3").is_empty());
        assert_eq!(ipv4_in("a 10.0.0.1 b 203.0.113.5").len(), 2);
    }

    #[test]
    fn check_reports_each_rule_once() {
        let d = Denylist::parse("examplehost");
        let p = public();
        assert_eq!(
            check(&format!("examplehost at {p} and {p}"), &d),
            vec![Rule::Denylist(1), Rule::PublicIpv4]
        );
        assert_eq!(check("documented at 203.0.113.10", &d), Vec::<Rule>::new());
    }

    #[test]
    fn only_added_lines_count_with_their_new_line_numbers() {
        let diff = "\
diff --git a/x.txt b/x.txt
index 1..2 100644
--- a/x.txt
+++ b/x.txt
@@ -3,0 +4,2 @@ context
+first added
+++second added starts with two pluses
@@ -10 +11 @@
-removed examplehost
+third
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-examplehost
";
        let f = parse_diff(diff);
        assert_eq!(f.len(), 1);
        assert_eq!(f[0].path, "x.txt");
        assert_eq!(
            f[0].lines,
            vec![
                (4, "first added".to_string()),
                (5, "++second added starts with two pluses".to_string()),
                (11, "third".to_string()),
            ]
        );
    }

    /// 043dd #636 L2: no `+++` line, the path is still checked.
    #[test]
    fn a_new_empty_file_or_a_mode_change_still_has_its_path_checked() {
        let diff = "\
diff --git a/examplehost/empty b/examplehost/empty
new file mode 100644
index 0000000..e69de29
diff --git a/tools/examplehost.sh b/tools/examplehost.sh
old mode 100644
new mode 100755
diff --git a/old/examplehost b/old/examplehost
deleted file mode 100644
index e69de29..0000000
diff --git a/x b/x
--- a/x
+++ b/x
@@ -1 +1 @@
-a
+b
";
        let f = parse_diff(diff);
        let paths: Vec<&str> = f.iter().map(|f| f.path.as_str()).collect();
        assert_eq!(paths, ["examplehost/empty", "tools/examplehost.sh", "x"]);
        let found = scan(
            &Input {
                files: f,
                ..Input::default()
            },
            &Denylist::parse("examplehost"),
        );
        assert_eq!(found.len(), 2, "{found:?}");
        assert_eq!(found[0].place, Place::Path { file: 1 });
        assert_eq!(found[1].place, Place::Path { file: 2 });
    }

    #[test]
    fn the_header_path_survives_spaces_and_a_b_slash_inside() {
        assert_eq!(header_path("a/x b/x"), Some("x"));
        assert_eq!(header_path("a/my b/file b/my b/file"), Some("my b/file"));
        assert_eq!(header_path("\"a/q\\\"x\" \"b/q\\\"x\""), None);
        assert_eq!(header_path("a/x b/y"), None);
    }

    #[test]
    fn a_removed_line_is_not_a_finding() {
        let diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-examplehost\n+clean\n";
        let input = Input {
            files: parse_diff(diff),
            ..Input::default()
        };
        assert!(scan(&input, &Denylist::parse("examplehost")).is_empty());
    }

    /// Scope: only what the PR adds. A hit already on the base (here as context lines of a
    /// diff, which is how a wider diff would carry it) must not turn the check red.
    #[test]
    fn an_existing_hit_with_nothing_new_is_clean() {
        let p = public();
        let diff = format!(
            "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,4 @@\n examplehost on the base\n see {p}\n+clean addition\n tail\n"
        );
        let input = Input {
            files: parse_diff(&diff),
            ..Input::default()
        };
        assert_eq!(
            input.files[0].lines,
            vec![(3, "clean addition".to_string())],
            "scope: a line already on the base was taken as added"
        );
        assert!(
            scan(&input, &Denylist::parse("examplehost")).is_empty(),
            "scope: a hit already on the base was reported"
        );
    }

    #[test]
    fn a_path_that_hits_is_withheld_from_every_line_of_that_file() {
        let p = public();
        let diff = format!("diff --git a/examplehost/a b/examplehost/a\n--- /dev/null\n+++ b/examplehost/a\n@@ -0,0 +1 @@\n+{p}\n");
        let input = Input {
            files: parse_diff(&diff),
            ..Input::default()
        };
        let found = scan(&input, &Denylist::parse("examplehost"));
        assert_eq!(
            found[0],
            Finding {
                place: Place::Path { file: 1 },
                rule: Rule::Denylist(1)
            }
        );
        assert_eq!(
            found[1],
            Finding {
                place: Place::Added {
                    file: 1,
                    path: None,
                    line: 1
                },
                rule: Rule::PublicIpv4
            }
        );
        for f in &found {
            assert!(
                !report_line(f).contains("examplehost"),
                "{}",
                report_line(f)
            );
        }
    }

    #[test]
    fn commits_title_and_body_are_scanned_by_line() {
        let input = Input {
            commits: parse_log(&format!(
                "{}\0subject\n\nmoved to ExampleHost\n\x1e\n",
                "a".repeat(40)
            )),
            title: Some("examplehost rollout".into()),
            body: Some(format!("line one\nsee {}\n", public())),
            ..Input::default()
        };
        let found = scan(&input, &Denylist::parse("examplehost"));
        assert_eq!(
            found.iter().map(|f| f.place.clone()).collect::<Vec<_>>(),
            vec![
                Place::Commit {
                    sha: "a".repeat(12),
                    line: 3
                },
                Place::PrTitle,
                Place::PrBody { line: 2 },
            ]
        );
        assert_eq!(found[2].rule, Rule::PublicIpv4);
    }

    #[test]
    fn the_report_never_prints_the_matched_text() {
        let p = public();
        let input = Input {
            files: vec![AddedFile {
                path: "src/a.rs".into(),
                lines: vec![(7, format!("examplehost {p}"))],
            }],
            title: Some(format!("examplehost {p}")),
            ..Input::default()
        };
        let found = scan(&input, &Denylist::parse("ExampleHost"));
        assert_eq!(found.len(), 4);
        for f in &found {
            let l = report_line(f);
            assert!(
                !l.to_lowercase().contains("examplehost"),
                "word printed: {l}"
            );
            assert!(!l.contains(&p), "address printed: {l}");
        }
        assert_eq!(
            report_line(&found[0]),
            "public-leak: src/a.rs:7 (file #1): denylist entry #1"
        );
    }

    #[test]
    fn the_pr_text_comes_from_the_event() {
        let e = serde_json::json!({"pull_request": {"title": "t", "body": null}});
        assert_eq!(pr_text(&e), (Some("t".to_string()), None));
    }
}
