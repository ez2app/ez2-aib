// SPDX-License-Identifier: Apache-2.0
//! `site scan` as decisions (E10 category 9: 9.1 / 9.2 / 9.9; f1fae's map 4.8, gap #1). The
//! host shell runs a few **read-only** commands inside one site's container and hands what
//! they printed to this module, which says `ok`, `finding` or `unknown` per check. No I/O.
//!
//! Checks:
//! * `core_checksums` — the site software's own files against the official checksums.
//! * `plugin_checksums` — every plugin against the official checksums. A plugin with no
//!   official checksums (a paid plugin) is **unknown**, never ok.
//! * `uploads_files` — anything under the uploads folder that a web server could run
//!   (`.php`, `.phtml`, `.phar`, … also as an inner extension: `x.php.jpg`) and any file
//!   whose name starts with a dot.
//! * `recent_php` — PHP files changed in the last [`RECENT_DAYS`] days, by content time or
//!   by status-change time (a content time set back by hand still shows as a change).
//! * `administrators` — the site's administrators against the first administrators of the
//!   opening record; one more than expected is a finding for a person to confirm.
//!
//! Rules that hold for every check:
//! * **Unknown is not ok.** Not run, cut short, no exit code, an exit code that does not
//!   fit, a line that does not parse: unknown. A finding outranks unknown: the result of a
//!   scan is its worst check ([`Outcome::worst`]).
//! * **The output is parsed, not only its exit code**: a success line without exit code 0,
//!   or an exit code 0 without a success line, is unknown.
//! * **Nothing about the host leaves**: a path is shown relative to the site's folder, and
//!   a path outside it (or with `..`) is shown as [`OUTSIDE`]. The report carries the site
//!   code and the domain only — no address, no container number, no provider.
//!
//! The text being judged comes from inside the customer's container: a site that is already
//! taken over can lie to the commands run there. A clean scan is evidence, not proof.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::{json, Value};

/// How bad one check (or a whole scan) is. Ordered: `Ok < Unknown < Finding`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Outcome {
    Ok,
    Unknown,
    Finding,
}

impl Outcome {
    pub fn word(self) -> &'static str {
        match self {
            Outcome::Ok => "ok",
            Outcome::Unknown => "unknown",
            Outcome::Finding => "finding",
        }
    }

    /// The worse of the two.
    pub fn worst(self, other: Outcome) -> Outcome {
        if other > self {
            return other;
        }
        self
    }

    /// The command's exit code: 0 ok, 1 finding, 3 unknown (as `site verify`).
    pub fn exit_code(self) -> u8 {
        match self {
            Outcome::Ok => 0,
            Outcome::Finding => 1,
            Outcome::Unknown => 3,
        }
    }
}

/// How many days back a PHP file counts as recently changed.
pub const RECENT_DAYS: u32 = 7;
/// At most this many names per check are listed; the count says how many there were.
pub const LIST_MAX: usize = 50;
/// What a path outside the site's folder is shown as.
pub const OUTSIDE: &str = "(outside the site)";
/// Extensions a web server may run, or that have no business under uploads.
pub const EXEC_EXT: &[&str] = &[
    "php", "php3", "php4", "php5", "php7", "php8", "pht", "phtml", "phar", "phps", "shtml",
    "cgi", "pl", "py", "sh",
];
/// Extensions of PHP files.
pub const PHP_EXT: &[&str] = &[
    "php", "php3", "php4", "php5", "php7", "php8", "pht", "phtml", "phar", "phps",
];

/// The marker the wrappers print after the command, with its exit code.
const MARK: &str = "\nez2-scan-rc=";
/// Runs `"$@"` with its standard error on standard output (the checksum commands warn on
/// standard error), then the marker. Always exits 0: the exit code is in the text.
pub const WRAP_MERGED: &str = "\"$@\" 2>&1; printf '\\nez2-scan-rc=%s\\n' \"$?\"";
/// The same with standard error dropped (lists whose every line is a name).
pub const WRAP_QUIET: &str = "\"$@\" 2>/dev/null; printf '\\nez2-scan-rc=%s\\n' \"$?\"";

/// What one command printed, and whether it was cut at the reader's cap (then there is no
/// exit code, and the last name may be cut too).
#[derive(Debug, Clone, Copy)]
pub struct Seen<'a> {
    pub text: &'a str,
    pub cut: bool,
}

/// One listed name: a path relative to the site, a plugin, or a login.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub name: String,
    pub kind: &'static str,
}

/// One check of a scan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Check {
    pub id: &'static str,
    pub outcome: Outcome,
    /// A short code; `ok` when there is nothing to say.
    pub reason: &'static str,
    /// The first [`LIST_MAX`] names.
    pub items: Vec<Item>,
    /// How many names there were in all.
    pub total: usize,
    /// The command's output was cut: there may be more than `total`.
    pub cut: bool,
}

impl Check {
    fn new(id: &'static str) -> Self {
        Check {
            id,
            outcome: Outcome::Unknown,
            reason: "",
            items: Vec::new(),
            total: 0,
            cut: false,
        }
    }

    fn push(&mut self, name: String, kind: &'static str) {
        self.total += 1;
        if self.items.len() < LIST_MAX {
            self.items.push(Item { name, kind });
        }
    }

    fn with(mut self, outcome: Outcome, reason: &'static str) -> Self {
        self.outcome = outcome;
        self.reason = reason;
        self
    }

    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id,
            "result": self.outcome.word(),
            "reason": self.reason,
            "total": self.total,
            "cut": self.cut,
            "items": self.items.iter().map(|i| json!({ "name": i.name, "kind": i.kind })).collect::<Vec<_>>(),
        })
    }
}

/// Split a wrapped command's text into what the command printed and its exit code. `None`
/// when the marker is missing or does not end in a number.
pub fn split_rc(text: &str) -> Option<(&str, i32)> {
    let at = text.rfind(MARK)?;
    let rest = &text[at + MARK.len()..];
    let rest = rest.strip_suffix('\n').unwrap_or(rest);
    if rest.is_empty() || !rest.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some((&text[..at], rest.parse().ok()?))
}

/// What the command printed and its exit code; no exit code when the text was cut.
fn body_and_rc(s: Seen<'_>) -> (&str, Option<i32>) {
    match split_rc(s.text) {
        Some((b, rc)) if !s.cut => (b, Some(rc)),
        _ => (s.text, None),
    }
}

/// `path` as shown in a report: relative to the site's folder `root`. A relative path is
/// taken as already relative to it; an absolute path elsewhere, or one with `..`, is
/// [`OUTSIDE`].
pub fn site_relative(root: &str, path: &str) -> String {
    let root = root.trim_end_matches('/');
    let rel = match path.strip_prefix(root).and_then(|r| r.strip_prefix('/')) {
        Some(r) => r,
        None if path.starts_with('/') => return OUTSIDE.to_owned(),
        None => path,
    };
    if rel.is_empty() || rel.split('/').any(|c| c == "..") {
        return OUTSIDE.to_owned();
    }
    rel.to_owned()
}

/// The extensions of a file name after its first dot, lower case: `a.PHP.jpg` → `php`, `jpg`.
fn inner_extensions(name: &str) -> impl Iterator<Item = String> + '_ {
    let base = name.rsplit('/').next().unwrap_or(name);
    base.split('.').skip(1).map(str::to_ascii_lowercase)
}

/// Why a file under uploads is listed, or `None` when it is not.
pub fn upload_kind(name: &str) -> Option<&'static str> {
    let base = name.rsplit('/').next().unwrap_or(name);
    if base.starts_with('.') {
        return Some("hidden");
    }
    if inner_extensions(name).any(|e| EXEC_EXT.contains(&e.as_str())) {
        return Some("executable");
    }
    None
}

/// `Some` when the file is a PHP file (by its last extension).
pub fn php_kind(name: &str) -> Option<&'static str> {
    let last = inner_extensions(name).last()?;
    PHP_EXT
        .contains(&last.as_str())
        .then_some("changed_recently")
}

fn find_names(args: &mut Vec<String>, exts: &[&str], inner_too: bool) {
    args.push("(".to_owned());
    for (n, e) in exts.iter().enumerate() {
        if n > 0 {
            args.push("-o".to_owned());
        }
        args.extend(["-iname".to_owned(), format!("*.{e}")]);
        if inner_too {
            args.extend(["-o".to_owned(), "-iname".to_owned(), format!("*.{e}.*")]);
        }
    }
}

/// The `find` that lists [`upload_kind`] files under the site's uploads, NUL-separated.
/// Read only: no action but `-print0`.
pub fn uploads_find_args(root: &str) -> Vec<String> {
    let mut a: Vec<String> = ["find", &format!("{root}/wp-content/uploads"), "-xdev"]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
    a.extend(
        ["(", "-type", "f", "-o", "-type", "l", ")"]
            .iter()
            .map(|s| (*s).to_owned()),
    );
    find_names(&mut a, EXEC_EXT, true);
    a.extend(["-o", "-name", ".*", ")", "-print0"].iter().map(|s| (*s).to_owned()));
    a
}

/// The `find` that lists PHP files in the site changed in the last [`RECENT_DAYS`] days, by
/// content time or by status-change time, NUL-separated.
pub fn recent_find_args(root: &str) -> Vec<String> {
    let days = format!("-{RECENT_DAYS}");
    let mut a: Vec<String> = ["find", root, "-xdev", "-type", "f"]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
    find_names(&mut a, PHP_EXT, false);
    a.push(")".to_owned());
    a.extend(["(".to_owned(), "-mtime".to_owned(), days.clone()]);
    a.extend(["-o".to_owned(), "-ctime".to_owned(), days]);
    a.extend([")".to_owned(), "-print0".to_owned()]);
    a
}

// ------------------------------------------------------------------ checksums

const CORE_MISMATCH: &str = "Warning: File doesn't verify against checksum: ";
const CORE_EXTRA: &str = "Warning: File should not exist: ";
const CORE_MISSING: &str = "Warning: File doesn't exist: ";
const CORE_OK: &str = "Success: WordPress installation verifies against checksums.";

fn says_trouble(line: &str) -> bool {
    line.starts_with("Warning:") || line.starts_with("Error:")
}

/// `wp core verify-checksums`, wrapped with [`WRAP_MERGED`].
pub fn core_checksums(root: &str, seen: Option<Seen<'_>>) -> Check {
    let mut c = Check::new("core_checksums");
    let Some(s) = seen else {
        return c.with(Outcome::Unknown, "not_run");
    };
    c.cut = s.cut;
    let (body, rc) = body_and_rc(s);
    let mut success = false;
    let mut other = false;
    for line in body.lines().map(str::trim_end) {
        if let Some(p) = line.strip_prefix(CORE_MISMATCH) {
            c.push(site_relative(root, p), "checksum_mismatch");
        } else if let Some(p) = line.strip_prefix(CORE_EXTRA) {
            c.push(site_relative(root, p), "not_in_release");
        } else if let Some(p) = line.strip_prefix(CORE_MISSING) {
            c.push(site_relative(root, p), "missing");
        } else if line == CORE_OK {
            success = true;
        } else if says_trouble(line) {
            other = true;
        }
    }
    if c.total > 0 {
        return c.with(Outcome::Finding, "files_differ");
    }
    if rc.is_none() {
        return c.with(Outcome::Unknown, "no_exit_code");
    }
    if other || !success {
        return c.with(Outcome::Unknown, "unrecognized");
    }
    if rc != Some(0) {
        return c.with(Outcome::Unknown, "exit_code");
    }
    c.with(Outcome::Ok, "ok")
}

const PLUGIN_SKIP_HEAD: &str = "Warning: Could not retrieve the checksums for version ";
const PLUGIN_SKIP_MID: &str = " of plugin ";
const PLUGIN_SKIP_TAIL: &str = ", skipping.";
const PLUGIN_NOVERSION_HEAD: &str = "Warning: Could not retrieve the version for plugin ";

/// The number before ` skipped)` in wp-cli's closing line; 0 when there is none.
fn skipped_count(line: &str) -> Option<usize> {
    let Some(at) = line.find(" skipped)") else {
        return Some(0);
    };
    let head = &line[..at];
    let start = head
        .rfind(|ch: char| !ch.is_ascii_digit())
        .map_or(0, |i| i + 1);
    head[start..].parse().ok()
}

fn plugin_message_kind(message: &str) -> &'static str {
    match message {
        "Checksum does not match" => "checksum_mismatch",
        "File was added" => "not_in_release",
        "File is missing" => "missing",
        _ => "differs",
    }
}

/// `wp plugin verify-checksums --all --format=json`, wrapped with [`WRAP_MERGED`].
pub fn plugin_checksums(root: &str, seen: Option<Seen<'_>>) -> Check {
    let mut c = Check::new("plugin_checksums");
    let Some(s) = seen else {
        return c.with(Outcome::Unknown, "not_run");
    };
    c.cut = s.cut;
    let (body, rc) = body_and_rc(s);
    let mut differ = 0usize;
    let mut without = 0usize;
    let mut closing: Option<(bool, Option<usize>)> = None;
    let mut bad = false;
    for line in body.lines().map(str::trim_end) {
        if line.starts_with('[') {
            let Ok(Value::Array(rows)) = serde_json::from_str::<Value>(line) else {
                bad = true;
                continue;
            };
            for row in rows {
                let (Some(p), Some(f)) = (
                    row.get("plugin_name").and_then(Value::as_str),
                    row.get("file").and_then(Value::as_str),
                ) else {
                    bad = true;
                    continue;
                };
                let kind = plugin_message_kind(
                    row.get("message").and_then(Value::as_str).unwrap_or(""),
                );
                differ += 1;
                c.push(
                    site_relative(root, &format!("wp-content/plugins/{p}/{f}")),
                    kind,
                );
            }
        } else if let Some(rest) = line.strip_prefix(PLUGIN_SKIP_HEAD) {
            let name = rest
                .split_once(PLUGIN_SKIP_MID)
                .and_then(|(_, t)| t.strip_suffix(PLUGIN_SKIP_TAIL));
            match name {
                Some(n) => {
                    without += 1;
                    c.push(
                        site_relative(root, &format!("wp-content/plugins/{n}")),
                        "no_official_checksums",
                    );
                }
                None => bad = true,
            }
        } else if let Some(rest) = line.strip_prefix(PLUGIN_NOVERSION_HEAD) {
            match rest.strip_suffix(PLUGIN_SKIP_TAIL) {
                Some(n) => {
                    without += 1;
                    c.push(
                        site_relative(root, &format!("wp-content/plugins/{n}")),
                        "no_version",
                    );
                }
                None => bad = true,
            }
        } else if let Some(rest) = line.strip_prefix("Success: ") {
            closing = Some((true, skipped_count(rest)));
        } else if let Some(rest) = line.strip_prefix("Error: ") {
            closing = Some((false, skipped_count(rest)));
        } else if says_trouble(line) {
            bad = true;
        }
    }
    if differ > 0 {
        return c.with(Outcome::Finding, "files_differ");
    }
    if rc.is_none() {
        return c.with(Outcome::Unknown, "no_exit_code");
    }
    if bad {
        return c.with(Outcome::Unknown, "unrecognized");
    }
    let Some((true, Some(skipped))) = closing else {
        return c.with(Outcome::Unknown, "not_verified");
    };
    if rc != Some(0) {
        return c.with(Outcome::Unknown, "exit_code");
    }
    if skipped != without {
        return c.with(Outcome::Unknown, "skip_count_mismatch");
    }
    if without > 0 {
        return c.with(Outcome::Unknown, "no_official_checksums");
    }
    c.with(Outcome::Ok, "ok")
}

// ------------------------------------------------------------------ file lists

/// A NUL-separated `find` list, wrapped with [`WRAP_QUIET`]: every name `kind_of` accepts
/// is a finding. A name it does not accept (or one outside the site) makes the check
/// unknown unless there are findings.
fn listed(
    id: &'static str,
    root: &str,
    seen: Option<Seen<'_>>,
    kind_of: fn(&str) -> Option<&'static str>,
) -> Check {
    let mut c = Check::new(id);
    let Some(s) = seen else {
        return c.with(Outcome::Unknown, "not_run");
    };
    c.cut = s.cut;
    let (body, rc) = body_and_rc(s);
    let mut names: Vec<&str> = body.split('\0').collect();
    if s.cut {
        // the last name may be cut in the middle
        names.pop();
    }
    let mut strange = false;
    for n in names.into_iter().filter(|n| !n.is_empty()) {
        let rel = site_relative(root, n);
        match kind_of(&rel) {
            Some(kind) => c.push(rel, kind),
            _ => strange = true,
        }
    }
    if c.total > 0 {
        return c.with(Outcome::Finding, "files_found");
    }
    if rc.is_none() {
        return c.with(Outcome::Unknown, "no_exit_code");
    }
    if strange {
        return c.with(Outcome::Unknown, "unrecognized");
    }
    if rc != Some(0) {
        return c.with(Outcome::Unknown, "exit_code");
    }
    c.with(Outcome::Ok, "ok")
}

/// [`uploads_find_args`]' output.
pub fn uploads_files(root: &str, seen: Option<Seen<'_>>) -> Check {
    listed("uploads_files", root, seen, upload_kind)
}

/// [`recent_find_args`]' output.
pub fn recent_php(root: &str, seen: Option<Seen<'_>>) -> Check {
    listed("recent_php", root, seen, php_kind)
}

// ------------------------------------------------------------------ administrators

/// `wp user list --role=administrator --fields=user_login --format=json`, wrapped with
/// [`WRAP_QUIET`], against the first administrators of the opening record (`None` when
/// there is no readable opening record).
pub fn administrators(seen: Option<Seen<'_>>, expected: Option<&[String]>) -> Check {
    let mut c = Check::new("administrators");
    let Some(s) = seen else {
        return c.with(Outcome::Unknown, "not_run");
    };
    c.cut = s.cut;
    let (body, Some(rc)) = body_and_rc(s) else {
        return c.with(Outcome::Unknown, "no_exit_code");
    };
    if rc != 0 {
        return c.with(Outcome::Unknown, "exit_code");
    }
    let rows = body
        .lines()
        .map(str::trim)
        .find(|l| l.starts_with('['))
        .and_then(|l| serde_json::from_str::<Value>(l).ok());
    let Some(Value::Array(rows)) = rows else {
        return c.with(Outcome::Unknown, "unrecognized");
    };
    let mut logins: Vec<String> = Vec::new();
    for row in rows {
        let Some(l) = row.get("user_login").and_then(Value::as_str) else {
            return c.with(Outcome::Unknown, "unrecognized");
        };
        logins.push(l.to_owned());
    }
    if logins.is_empty() {
        return c.with(Outcome::Unknown, "none_listed");
    }
    let Some(expected) = expected else {
        for l in logins {
            c.push(l, "not_compared");
        }
        return c.with(Outcome::Unknown, "no_opening_record");
    };
    let known = |l: &str| expected.iter().any(|e| e.eq_ignore_ascii_case(l));
    let extra: Vec<String> = logins.iter().filter(|l| !known(l)).cloned().collect();
    for l in &extra {
        c.push(l.clone(), "needs_confirmation");
    }
    // a first administrator no longer there is shown, but is not what this check is for
    for e in expected {
        if !logins.iter().any(|l| l.eq_ignore_ascii_case(e)) {
            c.push(e.clone(), "expected_absent");
        }
    }
    if !extra.is_empty() {
        return c.with(Outcome::Finding, "unexpected_administrators");
    }
    c.with(Outcome::Ok, "ok")
}

// ------------------------------------------------------------------ the scan

/// The scan's result: its worst check. No check at all is unknown.
pub fn overall(checks: &[Check]) -> Outcome {
    if checks.is_empty() {
        return Outcome::Unknown;
    }
    checks
        .iter()
        .fold(Outcome::Ok, |w, c| w.worst(c.outcome))
}

/// The printed report. Site code and domain only: nothing about the host.
pub fn report(site: &str, domain: &str, checks: &[Check]) -> Value {
    json!({
        "scan": "site",
        "site": site,
        "domain": domain,
        "result": overall(checks).word(),
        "changes_nothing": true,
        "recent_days": RECENT_DAYS,
        "checks": checks.iter().map(Check::to_json).collect::<Vec<_>>(),
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::string::ToString;

    const ROOT: &str = "/var/www/s-abc123";

    fn wrapped(body: &str, rc: i32) -> String {
        format!("{body}\nez2-scan-rc={rc}\n")
    }

    fn seen(text: &str) -> Option<Seen<'_>> {
        Some(Seen { text, cut: false })
    }

    #[test]
    fn the_scan_is_its_worst_check() {
        assert_eq!(Outcome::Ok.worst(Outcome::Unknown), Outcome::Unknown);
        assert_eq!(Outcome::Unknown.worst(Outcome::Finding), Outcome::Finding);
        assert_eq!(Outcome::Finding.worst(Outcome::Ok), Outcome::Finding);
        let ok = core_checksums(ROOT, seen(&wrapped(CORE_OK, 0)));
        let unk = core_checksums(ROOT, None);
        assert_eq!(overall(&[ok.clone(), unk.clone()]), Outcome::Unknown);
        assert_eq!(overall(&[ok.clone()]), Outcome::Ok);
        assert_eq!(overall(&[]), Outcome::Unknown);
        assert_eq!(Outcome::Finding.exit_code(), 1);
        assert_eq!(Outcome::Unknown.exit_code(), 3);
    }

    #[test]
    fn the_exit_code_is_read_from_the_last_marker() {
        assert_eq!(split_rc("a\nb\nez2-scan-rc=0\n"), Some(("a\nb", 0)));
        assert_eq!(split_rc("\nez2-scan-rc=1"), Some(("", 1)));
        assert_eq!(split_rc("x\nez2-scan-rc=0\ny\nez2-scan-rc=2\n"), Some(("x\nez2-scan-rc=0\ny", 2)));
        assert_eq!(split_rc("no marker"), None);
        assert_eq!(split_rc("\nez2-scan-rc=\n"), None);
        assert_eq!(split_rc("\nez2-scan-rc=0 forged\n"), None);
        assert_eq!(split_rc("\nez2-scan-rc=+0\n"), None);
        assert_eq!(split_rc("\nez2-scan-rc=-1\n"), None);
        assert!(WRAP_MERGED.contains("ez2-scan-rc=") && WRAP_QUIET.contains("ez2-scan-rc="));
        assert!(WRAP_MERGED.contains("2>&1") && WRAP_QUIET.contains("2>/dev/null"));
    }

    #[test]
    fn a_path_outside_the_site_is_not_shown() {
        assert_eq!(site_relative(ROOT, "/var/www/s-abc123/wp-content/uploads/x.php"), "wp-content/uploads/x.php");
        assert_eq!(site_relative(ROOT, "wp-includes/version.php"), "wp-includes/version.php");
        assert_eq!(site_relative(ROOT, "/etc/pve/lxc/112.conf"), OUTSIDE);
        assert_eq!(site_relative(ROOT, "/var/www/s-abc1234/x.php"), OUTSIDE);
        assert_eq!(site_relative(ROOT, "wp-content/../../../etc/passwd"), OUTSIDE);
        assert_eq!(site_relative(ROOT, "/var/www/s-abc123/"), OUTSIDE);
    }

    #[test]
    fn a_core_file_that_differs_is_a_finding() {
        let out = wrapped(
            "Warning: File doesn't verify against checksum: wp-includes/version.php\nWarning: File should not exist: wp-admin/x.php\nWarning: File doesn't exist: wp-login.php\nError: WordPress installation doesn't verify against checksums.",
            1,
        );
        let c = core_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Finding, "files_differ"));
        let names: Vec<(&str, &str)> = c.items.iter().map(|i| (i.name.as_str(), i.kind)).collect();
        assert_eq!(
            names,
            [
                ("wp-includes/version.php", "checksum_mismatch"),
                ("wp-admin/x.php", "not_in_release"),
                ("wp-login.php", "missing")
            ]
        );
    }

    #[test]
    fn a_core_success_needs_exit_zero_and_its_line() {
        assert_eq!(core_checksums(ROOT, seen(&wrapped(CORE_OK, 0))).outcome, Outcome::Ok);
        let c = core_checksums(ROOT, seen(&wrapped(CORE_OK, 1)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "exit_code"));
        let c = core_checksums(ROOT, seen(&wrapped("", 0)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "unrecognized"));
        let c = core_checksums(ROOT, seen(CORE_OK));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_exit_code"));
        let c = core_checksums(ROOT, Some(Seen { text: &wrapped(CORE_OK, 0), cut: true }));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_exit_code"));
        let c = core_checksums(ROOT, None);
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "not_run"));
    }

    #[test]
    fn a_core_warning_nobody_knows_is_unknown() {
        let out = wrapped(
            &format!("Warning: Couldn't get checksums from WordPress.org.\n{CORE_OK}"),
            0,
        );
        let c = core_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "unrecognized"));
        // noise that is not a wp-cli warning is not judged
        let out = wrapped(&format!("PHP Deprecated:  something\n{CORE_OK}"), 0);
        assert_eq!(core_checksums(ROOT, seen(&out)).outcome, Outcome::Ok);
    }

    #[test]
    fn all_plugins_verified_is_ok() {
        let out = wrapped("Success: Verified 3 of 3 plugins.", 0);
        let c = plugin_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Ok, "ok"));
    }

    #[test]
    fn a_paid_plugin_without_checksums_is_unknown_never_ok() {
        let out = wrapped(
            "Warning: Could not retrieve the checksums for version 7.1 of plugin flying-press, skipping.\nSuccess: Verified 2 of 3 plugins (1 skipped).",
            0,
        );
        let c = plugin_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_official_checksums"));
        assert_eq!(
            c.items,
            [Item { name: "wp-content/plugins/flying-press".to_string(), kind: "no_official_checksums" }]
        );
    }

    #[test]
    fn a_skip_count_that_does_not_match_the_warnings_is_unknown() {
        let out = wrapped(
            "Warning: Could not retrieve the checksums for version 7.1 of plugin flying-press, skipping.\nSuccess: Verified 1 of 3 plugins (2 skipped).",
            0,
        );
        let c = plugin_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "skip_count_mismatch"));
        assert_eq!(skipped_count("Only verified 1 of 4 plugins (1 failed, 12 skipped)."), Some(12));
        assert_eq!(skipped_count("Verified 3 of 3 plugins."), Some(0));
    }

    #[test]
    fn a_plugin_file_that_differs_is_a_finding() {
        let out = wrapped(
            "[{\"plugin_name\":\"akismet\",\"file\":\"akismet.php\",\"message\":\"Checksum does not match\"},{\"plugin_name\":\"akismet\",\"file\":\"x.php\",\"message\":\"File was added\"}]\nError: No plugins verified (1 failed).",
            1,
        );
        let c = plugin_checksums(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason), (Outcome::Finding, "files_differ"));
        assert_eq!(c.items[0].name, "wp-content/plugins/akismet/akismet.php");
        assert_eq!(c.items[1].kind, "not_in_release");
    }

    #[test]
    fn a_plugin_run_without_a_success_line_is_unknown() {
        let c = plugin_checksums(ROOT, seen(&wrapped("", 0)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "not_verified"));
        let c = plugin_checksums(ROOT, seen(&wrapped("Success: Verified 3 of 3 plugins.", 255)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "exit_code"));
        let c = plugin_checksums(ROOT, seen(&wrapped("[not json\nSuccess: Verified 3 of 3 plugins.", 0)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "unrecognized"));
        let c = plugin_checksums(ROOT, seen("Success: Verified 3 of 3 plugins."));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_exit_code"));
    }

    #[test]
    fn a_runnable_file_under_uploads_is_listed() {
        assert_eq!(upload_kind("wp-content/uploads/2026/11/a.php"), Some("executable"));
        assert_eq!(upload_kind("wp-content/uploads/a.PHTML"), Some("executable"));
        assert_eq!(upload_kind("wp-content/uploads/a.phar"), Some("executable"));
        assert_eq!(upload_kind("wp-content/uploads/photo.jpg"), None);
        assert_eq!(upload_kind("wp-content/uploads/photo"), None);
    }

    #[test]
    fn an_inner_extension_counts() {
        assert_eq!(upload_kind("wp-content/uploads/shell.php.jpg"), Some("executable"));
    }

    #[test]
    fn a_dot_file_under_uploads_is_listed() {
        assert_eq!(upload_kind("wp-content/uploads/.htaccess"), Some("hidden"));
        assert_eq!(upload_kind("wp-content/uploads/2026/.x.jpg"), Some("hidden"));
    }

    #[test]
    fn uploads_findings_and_their_limits() {
        let out = wrapped(
            "/var/www/s-abc123/wp-content/uploads/a.php\0/var/www/s-abc123/wp-content/uploads/.user.ini\0",
            0,
        );
        let c = uploads_files(ROOT, seen(&out));
        assert_eq!((c.outcome, c.reason, c.total), (Outcome::Finding, "files_found", 2));
        assert_eq!(c.items[1], Item { name: "wp-content/uploads/.user.ini".to_string(), kind: "hidden" });
        assert_eq!(uploads_files(ROOT, seen(&wrapped("", 0))).outcome, Outcome::Ok);
        let c = uploads_files(ROOT, seen(&wrapped("", 1)));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "exit_code"));
        let c = uploads_files(ROOT, seen(""));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_exit_code"));
        // a name outside the site is never printed
        let c = uploads_files(ROOT, seen(&wrapped("/etc/pve/x.php\0", 0)));
        assert_eq!((c.outcome, c.reason, c.total), (Outcome::Unknown, "unrecognized", 0));
        // cut: the names before the cut count, the cut one does not
        let many: String = (0..80).map(|n| format!("{ROOT}/wp-content/uploads/{n}.php\0")).collect();
        let c = uploads_files(ROOT, Some(Seen { text: &format!("{many}{ROOT}/wp-con"), cut: true }));
        assert_eq!((c.outcome, c.total, c.items.len(), c.cut), (Outcome::Finding, 80, LIST_MAX, true));
    }

    #[test]
    fn the_find_commands_only_list() {
        for args in [uploads_find_args(ROOT), recent_find_args(ROOT)] {
            assert_eq!(args.first().map(String::as_str), Some("find"));
            assert_eq!(args.last().map(String::as_str), Some("-print0"));
            for bad in ["-delete", "-exec", "-execdir", "-ok", "-fprint", "-fls"] {
                assert!(!args.iter().any(|a| a == bad), "{bad}");
            }
            let open = args.iter().filter(|a| *a == "(").count();
            let close = args.iter().filter(|a| *a == ")").count();
            assert_eq!(open, close);
        }
        let u = uploads_find_args(ROOT);
        assert_eq!(u[1], "/var/www/s-abc123/wp-content/uploads");
        assert!(u.iter().any(|a| a == "*.php.*") && u.iter().any(|a| a == ".*"));
    }

    #[test]
    fn a_change_time_set_back_still_counts() {
        let r = recent_find_args(ROOT);
        assert!(r.windows(2).any(|w| w[0] == "-ctime" && w[1] == "-7"));
        assert!(r.windows(2).any(|w| w[0] == "-mtime" && w[1] == "-7"));
    }

    #[test]
    fn a_recent_php_file_is_listed() {
        let out = wrapped(&format!("{ROOT}/wp-content/plugins/x/y.php\0"), 0);
        let c = recent_php(ROOT, seen(&out));
        assert_eq!((c.outcome, c.items[0].kind), (Outcome::Finding, "changed_recently"));
        assert_eq!(php_kind("a.php.jpg"), None);
        assert_eq!(php_kind("a.PHP"), Some("changed_recently"));
    }

    fn admins_out(logins: &[&str]) -> String {
        let rows: Vec<Value> = logins.iter().map(|l| json!({ "user_login": l })).collect();
        wrapped(&Value::Array(rows).to_string(), 0)
    }

    #[test]
    fn an_administrator_nobody_opened_needs_a_person() {
        let expected = ["ops".to_string(), "andy".to_string()];
        let out = admins_out(&["ops", "Andy", "wp_backup"]);
        let c = administrators(seen(&out), Some(&expected));
        assert_eq!((c.outcome, c.reason), (Outcome::Finding, "unexpected_administrators"));
        assert_eq!(c.items, [Item { name: "wp_backup".to_string(), kind: "needs_confirmation" }]);
        let c = administrators(seen(&admins_out(&["ops", "andy"])), Some(&expected));
        assert_eq!((c.outcome, c.total), (Outcome::Ok, 0));
        let c = administrators(seen(&admins_out(&["ops"])), Some(&expected));
        assert_eq!((c.outcome, c.items[0].kind), (Outcome::Ok, "expected_absent"));
    }

    #[test]
    fn without_an_opening_record_administrators_are_unknown() {
        let c = administrators(seen(&admins_out(&["ops"])), None);
        assert_eq!(c.outcome, Outcome::Unknown);
        assert_eq!(c.reason, "no_opening_record");
        assert_eq!(c.items[0].kind, "not_compared");
        let c = administrators(seen(&wrapped("Error: nope", 1)), Some(&[]));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "exit_code"));
        let c = administrators(Some(Seen { text: &admins_out(&["ops"]), cut: true }), Some(&[]));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "no_exit_code"));
        let c = administrators(seen(&admins_out(&[])), Some(&[]));
        assert_eq!((c.outcome, c.reason), (Outcome::Unknown, "none_listed"));
    }

    #[test]
    fn the_report_carries_the_site_and_nothing_of_the_host() {
        let out = wrapped(&format!("{ROOT}/wp-content/uploads/a.php\0/srv/host/112/x.php\0"), 0);
        let checks = [uploads_files(ROOT, seen(&out))];
        let text = report("s-abc123", "example.test", &checks).to_string();
        assert!(text.contains("\"result\":\"finding\""), "{text}");
        assert!(!text.contains("/var/www") && !text.contains("112") && !text.contains("/srv"), "{text}");
    }
}
