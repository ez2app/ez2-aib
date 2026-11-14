// SPDX-License-Identifier: Apache-2.0
//! Local mode (建議表 #12, DECISION-pushscan-12): the same check run on the Mac before a push
//! or before a PR's text is sent, so a host word is stopped before it reaches the public repo.
//!
//! The denylist here is strict: the private file is the main list (same format as the CI
//! secret); `hosts.json` server names and ssh targets only add to it. A line it does not
//! understand is an error (exit 3), never skipped. `word:` entries match on ASCII
//! letter / digit boundaries (a short code next to Chinese, `_`, `-` or `.` still hits; inside
//! a longer ASCII word it does not); every other entry is a substring, as in CI.
//! Errors name the line number only, never the entry.

use crate::{AddedFile, Denylist};
use std::collections::HashSet;

/// A merge commit's own lines (7b81e, review of 86ea06b9 M2): `diffs` holds the commit's diff
/// against each of its parents, first parent first. A line counts only when it is added
/// against every parent: a line one parent already had came in with that parent (main merged
/// into a branch brings main's lines, already public and already checked by CI), not with this
/// commit. All diffs end at the same commit, so a line is matched by its path and new line
/// number; a file (its path is checked too) only when every diff has it. One diff (an
/// ordinary commit) is returned as it is.
pub fn added_against_all(mut diffs: Vec<Vec<AddedFile>>) -> Vec<AddedFile> {
    if diffs.len() <= 1 {
        return diffs.pop().unwrap_or_default();
    }
    let first = diffs.remove(0);
    let paths: Vec<HashSet<&str>> = diffs
        .iter()
        .map(|d| d.iter().map(|f| f.path.as_str()).collect())
        .collect();
    let lines: Vec<HashSet<(&str, u32)>> = diffs
        .iter()
        .map(|d| {
            d.iter()
                .flat_map(|f| f.lines.iter().map(move |(n, _)| (f.path.as_str(), *n)))
                .collect()
        })
        .collect();
    first
        .iter()
        .filter(|f| paths.iter().all(|p| p.contains(f.path.as_str())))
        .map(|f| AddedFile {
            path: f.path.clone(),
            lines: f
                .lines
                .iter()
                .filter(|(n, _)| lines.iter().all(|o| o.contains(&(f.path.as_str(), *n))))
                .cloned()
                .collect(),
        })
        .collect()
}

/// One `<local ref> <local sha> <remote ref> <remote sha>` line a pre-push hook reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PushRef {
    pub local_sha: String,
    pub remote_sha: String,
}

impl PushRef {
    /// The push deletes the remote ref: nothing is sent.
    pub fn is_delete(&self) -> bool {
        is_zero(&self.local_sha)
    }

    /// The remote ref does not exist yet (a new branch).
    pub fn is_new(&self) -> bool {
        is_zero(&self.remote_sha)
    }
}

fn is_zero(sha: &str) -> bool {
    !sha.is_empty() && sha.bytes().all(|b| b == b'0')
}

fn is_sha(s: &str) -> bool {
    (s.len() == 40 || s.len() == 64) && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Parse a pre-push hook's standard input. Any malformed line is an error: a push that cannot
/// be read is not checked, and not checked is not clean.
pub fn parse_pre_push(stdin: &str) -> Result<Vec<PushRef>, String> {
    let mut out = Vec::new();
    for (k, line) in stdin.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() != 4 || !is_sha(f[1]) || !is_sha(f[3]) {
            return Err(format!(
                "pre-push input line {} is not '<ref> <sha> <ref> <sha>'",
                k + 1
            ));
        }
        out.push(PushRef {
            local_sha: f[1].to_ascii_lowercase(),
            remote_sha: f[3].to_ascii_lowercase(),
        });
    }
    Ok(out)
}

/// Parse the private denylist file strictly. `word:` / `sub:` are understood; `re:` and
/// `net:` (from the host-word exporter) are not supported yet and are refused, as is an empty
/// value. The error names the line number only.
pub fn parse_strict(text: &str) -> Result<Denylist, String> {
    let mut d = Denylist::default();
    for (k, raw) in text.lines().enumerate() {
        let l = raw.trim();
        if l.is_empty() || l.starts_with('#') {
            continue;
        }
        let bad = || format!("denylist line {}: not understood (entry not shown)", k + 1);
        if let Some(v) = l.strip_prefix("word:") {
            let v = v.trim();
            if v.is_empty() || !v.bytes().all(|b| b.is_ascii_alphanumeric()) {
                return Err(bad());
            }
            d.push_word(v);
        } else if let Some(v) = l.strip_prefix("sub:") {
            if v.trim().is_empty() {
                return Err(bad());
            }
            d.push_sub(v.trim());
        } else if l.starts_with("re:") || l.starts_with("net:") {
            return Err(bad());
        } else {
            d.push_sub(l);
        }
    }
    Ok(d)
}

/// Entries `hosts.json` adds: each server's name and the host part of its ssh target.
/// ASCII letter / digit names become `word:` entries; anything else (an address, a dotted
/// name) a substring. A file that is not the expected shape is an error.
pub fn hosts_entries(json: &str) -> Result<Vec<String>, String> {
    let v: serde_json::Value =
        serde_json::from_str(json).map_err(|_| "hosts.json is not JSON".to_string())?;
    let servers = v
        .get("servers")
        .and_then(|s| s.as_array())
        .ok_or("hosts.json has no servers list")?;
    let mut out = Vec::new();
    for s in servers {
        let name = s.get("name").and_then(|n| n.as_str());
        let target = s
            .get("ssh")
            .and_then(|n| n.as_str())
            .map(|t| t.rsplit('@').next().unwrap_or(t));
        for v in [name, target].into_iter().flatten() {
            let v = v.trim();
            if v.len() < 3 {
                continue; // too short to be a host word; would hit everything
            }
            if v.bytes().all(|b| b.is_ascii_alphanumeric()) {
                out.push(format!("word:{v}"));
            } else {
                out.push(format!("sub:{v}"));
            }
        }
    }
    Ok(out)
}

/// Whether `word` (lower case, ASCII letters / digits) is in `low` (lower case) with no ASCII
/// letter or digit right before or after it.
pub fn has_word(low: &str, word: &str) -> bool {
    let b = low.as_bytes();
    let mut from = 0;
    while let Some(i) = low[from..].find(word) {
        let s = from + i;
        let e = s + word.len();
        let before = s == 0 || !b[s - 1].is_ascii_alphanumeric();
        let after = e == b.len() || !b[e].is_ascii_alphanumeric();
        if before && after {
            return true;
        }
        from = s + 1;
        while !low.is_char_boundary(from) {
            from += 1;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    const Z: &str = "0000000000000000000000000000000000000000";
    const A: &str = "1111111111111111111111111111111111111111";

    #[test]
    fn pre_push_lines_are_read_and_bad_ones_refused() {
        let r = parse_pre_push(&format!("refs/heads/x {A} refs/heads/x {Z}\n\n")).unwrap();
        assert_eq!(r.len(), 1);
        assert!(r[0].is_new() && !r[0].is_delete());
        let r = parse_pre_push(&format!("(delete) {Z} refs/heads/x {A}\n")).unwrap();
        assert!(r[0].is_delete());
        assert!(parse_pre_push("refs/heads/x abc refs/heads/x def\n").is_err());
        assert!(parse_pre_push(&format!("refs/heads/x {A}\n")).is_err());
    }

    #[test]
    fn short_codes_match_on_ascii_word_boundaries() {
        for hit in [
            "zq01", "到zq01", "zq01機", "x_zq01", "zq01-a", "a.zq01.b", "(zq01)",
        ] {
            assert!(has_word(hit, "zq01"), "{hit}");
        }
        for miss in ["zq010", "szq01x", "azq01", "zq01b"] {
            assert!(!has_word(miss, "zq01"), "{miss}");
        }
        assert!(
            has_word("szq01 zq01", "zq01"),
            "a later occurrence still counts"
        );
        assert!(has_word("中文中zq01", "zq01"), "after multi-byte text");
    }

    #[test]
    fn strict_parse_understands_word_and_sub_and_refuses_the_rest() {
        let d = parse_strict("# c\nword:ZQ01\nsub:Example Host\nplain\n").unwrap();
        assert_eq!(d.len(), 3);
        assert_eq!(d.first_hit("go to Zq01 now"), Some(1));
        assert_eq!(d.first_hit("go to zq010 now"), None);
        assert_eq!(d.first_hit("an EXAMPLE HOST here"), Some(2));
        assert_eq!(d.first_hit("explainer"), Some(3), "sub-string, as in CI");
        for bad in ["re:a.b", "net:10.0.0.0/8", "word:", "word:a-b", "sub: "] {
            let e = parse_strict(&format!("ok\n{bad}\n")).unwrap_err();
            assert!(e.contains("line 2"), "{e}");
            for v in ["a.b", "10.0.0.0", "a-b"] {
                assert!(!e.contains(v), "entry printed: {e}");
            }
        }
    }

    fn af(path: &str, lines: &[(u32, &str)]) -> AddedFile {
        AddedFile {
            path: path.into(),
            lines: lines.iter().map(|(n, t)| (*n, t.to_string())).collect(),
        }
    }

    #[test]
    fn a_merge_keeps_only_lines_new_against_every_parent() {
        // vs the branch: main's line 2 and the hand-written line 5 are new;
        // vs main: the branch's line 3 and the hand-written line 5 are new
        let vs_branch = vec![af("a", &[(2, "from main"), (5, "by hand")])];
        let vs_main = vec![af("a", &[(3, "from branch"), (5, "by hand")])];
        assert_eq!(
            added_against_all(vec![vs_branch, vs_main]),
            vec![af("a", &[(5, "by hand")])]
        );
        // a file only one side changed comes with that side: not even its path is this commit's
        let vs_branch = vec![af("main-only", &[(1, "x")])];
        assert!(added_against_all(vec![vs_branch, vec![]]).is_empty());
        // a file new against both parents keeps its path, even with no added line (binary)
        let both = vec![af("new.bin", &[])];
        assert_eq!(
            added_against_all(vec![both.clone(), both.clone()]),
            vec![af("new.bin", &[])]
        );
        // an ordinary commit (one parent) is kept whole, empty / binary files too
        let one = vec![af("b", &[(1, "y"), (2, "z")]), af("c.bin", &[])];
        assert_eq!(added_against_all(vec![one.clone()]), one);
        assert!(added_against_all(vec![]).is_empty());
    }

    #[test]
    fn hosts_json_adds_names_and_ssh_hosts() {
        let j = r#"{"servers":[{"name":"srvone","ssh":"root@srv.example.invalid"},{"name":"ab","ssh":"box9"}]}"#;
        assert_eq!(
            hosts_entries(j).unwrap(),
            vec!["word:srvone", "sub:srv.example.invalid", "word:box9"]
        );
        assert!(hosts_entries("[]").is_err());
        assert!(hosts_entries("nope").is_err());
    }
}
