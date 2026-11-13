// SPDX-License-Identifier: Apache-2.0
//! What a consent request may look like (B2 T136-1, PLAN-t136-consent-rust-c477a-1113).
//!
//! Port of the frozen 1.x `aib/src/cockpit/consent.py`: the limits (:76–106), the
//! allow-list [`classify`] (:251–303) with the same refusal words, [`display_cmd`] (:310,
//! Python `shlex.quote`), [`clean_summary`] (:304), [`lint_acks`] / [`seat`] (:314–330) and
//! the request file readers and writer (:212–250). Nothing here asks the person, runs
//! anything or writes outside the path it is given.
//!
//! Characters the person cannot see are judged by the shared table in
//! [`aib_approve::text::is_bad_char`] (Unicode categories Cc / Cf / Zl / Zp / Co / Cn / Cs,
//! any space but U+0020, plus the 1.x extra list; table pinned to Unicode 16.0).
//!
//! Known differences from 1.x, all stricter and none reachable from the CLI:
//! - Python `re.match(r"^…$")` also accepts one trailing `\n`; the name checks here do not.
//! - Python `json.loads` takes `NaN` / `Infinity`; [`read_json`] refuses them (serde_json).
//! - [`write_atomic`] writes keys sorted (serde_json map), 1.x in insertion order; both
//!   are one-space-indented UTF-8 JSON and every reader parses rather than compares bytes.

use serde_json::{Map, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

/// A request not decided within this long is `expired` (1.x `EXPIRE_S`).
pub const EXPIRE_S: u64 = 30 * 60;
/// Hard limit for one approved run (1.x `RUN_TIMEOUT_S`).
pub const RUN_TIMEOUT_S: u64 = 20 * 60;
/// An inbox file younger than this is left for the next pass (1.x `FRESH_S`).
pub const FRESH_S: u64 = 2;
/// Largest request file read (1.x `MAX_REQ_BYTES`).
pub const MAX_REQ_BYTES: u64 = 16 * 1024;
/// Most words in a request's argv (1.x `MAX_ARGV`).
pub const MAX_ARGV: usize = 40;
/// Longest shown command, in code points (1.x `MAX_CMD_LEN`).
pub const MAX_CMD_LEN: usize = 1500;
/// Largest SIGN script read (1.x `MAX_SIGN_BYTES`).
pub const MAX_SIGN_BYTES: u64 = 256 * 1024;
/// Longest one-line summary, in code points (1.x `SUMMARY_MAX`).
pub const SUMMARY_MAX: usize = 200;

/// `aib <sub>` commands a seat may ask for (1.x `AIB_SUBCOMMANDS`; `update` is not one).
pub const AIB_SUBCOMMANDS: &[&str] = &["dm-queue", "lang", "now", "plugins", "theme"];
/// `claude plugin <sub>` commands a seat may ask for (1.x `CLAUDE_PLUGIN_SUB`).
pub const CLAUDE_PLUGIN_SUB: &[&str] = &["disable", "enable", "install", "list", "uninstall", "update"];

/// What kind of job an accepted argv is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// `aib <allowed sub> …`
    Aib,
    /// `claude plugin <allowed sub> …`, no flags
    ClaudePlugin,
    /// `bash ~/.ez2-aib/pending/SIGN-….sh`, strong only
    SignScript,
    /// `generic <program> --params <file>`, strong only (checked further by `params`)
    SharedProgram,
}

impl Kind {
    /// The 1.x word for this kind.
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Aib => "aib",
            Kind::ClaudePlugin => "claude_plugin",
            Kind::SignScript => "sign_script",
            Kind::SharedProgram => "shared_program",
        }
    }
}

/// True when `text` holds a character the person could not see in the window.
pub fn has_bad_char(text: &str) -> bool {
    text.chars().any(aib_approve::text::is_bad_char)
}

fn all_in(s: &str, ok: impl Fn(u8) -> bool) -> bool {
    s.bytes().all(ok)
}

fn word_byte(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_' || c == b'-'
}

fn name_byte(c: u8) -> bool {
    word_byte(c) || c == b'.'
}

/// 1.x `_ID_RE`: `^[A-Za-z0-9_-]{1,64}$`.
pub fn id_ok(s: &str) -> bool {
    (1..=64).contains(&s.len()) && all_in(s, word_byte)
}

/// 1.x `_SIGN_RE`: `^SIGN-[A-Za-z0-9._-]+\.sh$`.
pub fn sign_name_ok(s: &str) -> bool {
    match s.strip_prefix("SIGN-").and_then(|r| r.strip_suffix(".sh")) {
        Some(mid) => !mid.is_empty() && all_in(mid, name_byte),
        None => false,
    }
}

/// 1.x `_PLUGIN_REF_RE`: `^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$`.
pub fn plugin_ref_ok(s: &str) -> bool {
    match s.split_once('@') {
        Some((a, b)) => !a.is_empty() && !b.is_empty() && all_in(a, name_byte) && all_in(b, name_byte),
        None => false,
    }
}

/// 1.x `_seat`: a seat code `^[a-z0-9]{3,12}$`, else none.
pub fn seat(v: &Value) -> Option<String> {
    let s = v.as_str()?;
    ((3..=12).contains(&s.len()) && all_in(s, |c| c.is_ascii_lowercase() || c.is_ascii_digit()))
        .then(|| s.to_string())
}

/// 1.x `_acks`: the `--lint-ack` codes, sorted and without repeats. Only S1–S5 may be
/// acknowledged (U1 / R1 never): anything else gives `None` (refusal `lint_ack_code`).
pub fn lint_acks(v: &Value) -> Option<Vec<String>> {
    let items: Vec<String> = match v {
        Value::Null => return Some(Vec::new()),
        Value::String(s) if s.is_empty() => return Some(Vec::new()),
        Value::Array(a) if a.is_empty() => return Some(Vec::new()),
        Value::String(s) => s.split(',').map(str::to_string).collect(),
        // Python str(x) of a non-string item never matches S[1-5]
        Value::Array(a) => a
            .iter()
            .map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| "\u{0}".into()))
            .collect(),
        _ => return None,
    };
    let mut out: Vec<String> = items
        .iter()
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
        .collect();
    out.sort();
    out.dedup();
    out.iter()
        .all(|x| x.len() == 2 && x.starts_with('S') && (b'1'..=b'5').contains(&x.as_bytes()[1]))
        .then_some(out)
}

/// Python `shlex.quote`.
pub fn shell_quote(a: &str) -> String {
    if a.is_empty() {
        return "''".into();
    }
    let safe = a
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&c));
    if safe {
        a.to_string()
    } else {
        format!("'{}'", a.replace('\'', "'\"'\"'"))
    }
}

/// 1.x `display_cmd`: argv as one shell-quoted line.
pub fn display_cmd(argv: &[String]) -> String {
    argv.iter().map(|a| shell_quote(a)).collect::<Vec<_>>().join(" ")
}

/// 1.x `_clean_summary`: tabs and line breaks become spaces, characters the person
/// cannot see are dropped, the ends trimmed, and a line longer than [`SUMMARY_MAX`]
/// cut to `SUMMARY_MAX - 1` code points plus `…`. (A summary is the seat's own words,
/// shown as such; the command itself is refused, never cleaned.)
pub fn clean_summary(text: &str) -> String {
    let kept: String = text
        .chars()
        .map(|c| if matches!(c, '\t' | '\r' | '\n') { ' ' } else { c })
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .collect();
    // after the filter the only white space left is U+0020 (every other one is Cc / Zs / Zl / Zp)
    let t = kept.trim_matches(' ');
    if t.chars().count() <= SUMMARY_MAX {
        t.to_string()
    } else {
        let mut s: String = t.chars().take(SUMMARY_MAX - 1).collect();
        s.push('…');
        s
    }
}

/// Python `Path.expanduser`: `~` and `~/…` from `home`, `~user/…` from the user database.
/// `None` where Python raises (no such user).
fn expand_user(p: &str, home: &Path) -> Option<PathBuf> {
    let Some(rest) = p.strip_prefix('~') else {
        return Some(PathBuf::from(p));
    };
    let (user, tail) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i + 1..]),
        None => (rest, ""),
    };
    let base = if user.is_empty() {
        home.to_path_buf()
    } else {
        nix::unistd::User::from_name(user).ok()??.dir
    };
    Some(if tail.is_empty() { base } else { base.join(tail) })
}

/// 1.x `classify`: which kind of job `argv` is, or the refusal word. `home` is the
/// person's home (the SIGN folder is `<home>/.ez2-aib/pending`).
pub fn classify(argv: &Value, strong: bool, home: &Path) -> Result<Kind, &'static str> {
    let Some(arr) = argv.as_array().filter(|a| !a.is_empty() && a.len() <= MAX_ARGV) else {
        return Err("argv_shape");
    };
    let mut words = Vec::with_capacity(arr.len());
    for a in arr {
        match a.as_str() {
            Some(s) if !s.is_empty() && !has_bad_char(s) => words.push(s.to_string()),
            _ => return Err("argv_chars"),
        }
    }
    if display_cmd(&words).chars().count() > MAX_CMD_LEN {
        return Err("argv_too_long");
    }
    let w: Vec<&str> = words.iter().map(String::as_str).collect();
    match w[0] {
        "aib" => {
            if w.len() < 2 || !AIB_SUBCOMMANDS.contains(&w[1]) {
                return Err("aib_subcommand");
            }
            if w.iter()
                .any(|a| *a == "--allow-unsigned" || a.starts_with("--allow-unsigned="))
            {
                return Err("aib_allow_unsigned");
            }
            Ok(Kind::Aib)
        }
        "claude" => {
            if w.len() < 3 || w[1] != "plugin" || !CLAUDE_PLUGIN_SUB.contains(&w[2]) {
                return Err("claude_subcommand");
            }
            // -y / --accept-command / --marketplace can each bring in another command: no flags at all
            if w[3..].iter().any(|a| a.starts_with('-')) {
                return Err("claude_flag");
            }
            if w[2] == "install" && (w.len() < 4 || !plugin_ref_ok(w[3])) {
                return Err("claude_install_ref");
            }
            Ok(Kind::ClaudePlugin)
        }
        "bash" => {
            if w.len() != 2 {
                return Err("sign_shape");
            }
            let real = expand_user(w[1], home).and_then(|p| fs::canonicalize(p).ok());
            let base = fs::canonicalize(home.join(".ez2-aib").join("pending")).ok();
            let (Some(real), Some(base)) = (real, base) else {
                return Err("sign_missing");
            };
            let name_ok = real
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(sign_name_ok);
            if real.parent() != Some(base.as_path()) || !name_ok || !real.is_file() {
                return Err("sign_path");
            }
            if !strong {
                return Err("sign_needs_strong");
            }
            Ok(Kind::SignScript)
        }
        "generic" => {
            // ["generic", <program>, "--params", <file>]: place and hash are checked by `params`
            if w.len() != 4 || w[2] != "--params" {
                return Err("generic_shape");
            }
            for a in [w[1], w[3]] {
                match expand_user(a, home) {
                    Some(p) if p.is_absolute() && p.is_file() => {}
                    _ => return Err("generic_missing"),
                }
            }
            if !strong {
                return Err("generic_needs_strong");
            }
            Ok(Kind::SharedProgram)
        }
        _ => Err("not_allowed"),
    }
}

/// 1.x `_read_regular`: the file's bytes when it is a regular file of at most `limit`
/// bytes. A link is not followed and a named pipe does not block (`O_NOFOLLOW |
/// O_NONBLOCK`); anything else gives `None`.
pub fn read_regular(path: &Path, limit: u64) -> Option<Vec<u8>> {
    let f = OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_NONBLOCK)
        .open(path)
        .ok()?;
    if !f.metadata().ok()?.file_type().is_file() {
        return None;
    }
    let mut raw = Vec::new();
    f.take(limit + 1).read_to_end(&mut raw).ok()?;
    (raw.len() as u64 <= limit).then_some(raw)
}

/// 1.x `_read_json`: a request file as a JSON object ([`MAX_REQ_BYTES`] by default).
pub fn read_json(path: &Path, limit: u64) -> Option<Map<String, Value>> {
    let raw = read_regular(path, limit)?;
    let text = std::str::from_utf8(&raw).ok()?;
    match serde_json::from_str::<Value>(text).ok()? {
        Value::Object(m) => Some(m),
        _ => None,
    }
}

fn token_hex4() -> std::io::Result<String> {
    let mut b = [0u8; 4];
    File::open("/dev/urandom")?.read_exact(&mut b)?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

/// 1.x `_write_atomic`: write `data` as JSON to a hidden temp file next to `path`
/// (`.<name>.<8 hex>.tmp`, mode 0600), then rename it over `path`.
pub fn write_atomic(path: &Path, data: &Value) -> std::io::Result<()> {
    let dir = path
        .parent()
        .ok_or_else(|| std::io::Error::other("no parent"))?;
    fs::create_dir_all(dir)?;
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| std::io::Error::other("bad name"))?;
    let tmp = dir.join(format!(".{name}.{}.tmp", token_hex4()?));
    let mut out = Vec::new();
    let fmt = serde_json::ser::PrettyFormatter::with_indent(b" ");
    let mut ser = serde_json::Serializer::with_formatter(&mut out, fmt);
    serde::Serialize::serialize(data, &mut ser).map_err(std::io::Error::other)?;
    let res = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)?;
        f.write_all(&out)?;
        f.sync_all()?;
        fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600))?;
        fs::rename(&tmp, path)
    })();
    if res.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    res
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::os::unix::fs::symlink;

    struct Td(PathBuf);
    impl Drop for Td {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn td(tag: &str) -> Td {
        let p = std::env::temp_dir().join(format!("aib-consent-req-{tag}-{}-{}", std::process::id(), token_hex4().unwrap()));
        fs::create_dir_all(&p).unwrap();
        Td(fs::canonicalize(&p).unwrap())
    }
    fn home_with_sign(t: &Td, name: &str) -> PathBuf {
        let pend = t.0.join(".ez2-aib").join("pending");
        fs::create_dir_all(&pend).unwrap();
        let p = pend.join(name);
        fs::write(&p, "echo hi\n").unwrap();
        p
    }
    fn c(v: Value, strong: bool, home: &Path) -> Result<&'static str, &'static str> {
        classify(&v, strong, home).map(Kind::as_str)
    }

    // test-consent-popup-abb89-1025.py Whitelist.test_c1_whitelist
    #[test]
    fn allowed_shapes() {
        let t = td("ok");
        let s = home_with_sign(&t, "SIGN-x-0.1.0-abb89-1025.sh");
        let h = &t.0;
        assert_eq!(c(json!(["aib", "now", "--seat", "abb89", "x", "1/2"]), false, h), Ok("aib"));
        assert_eq!(c(json!(["aib", "dm-queue", "list"]), false, h), Ok("aib"));
        assert_eq!(c(json!(["claude", "plugin", "list"]), false, h), Ok("claude_plugin"));
        assert_eq!(c(json!(["claude", "plugin", "install", "aib-boards@ez2"]), false, h), Ok("claude_plugin"));
        assert_eq!(c(json!(["bash", s.to_str().unwrap()]), true, h), Ok("sign_script"));
        assert_eq!(c(json!(["aib", "now", "中文 進度 ✓"]), false, h), Ok("aib"));
    }

    #[test]
    fn refused_shapes_with_their_words() {
        let t = td("bad");
        let s = home_with_sign(&t, "SIGN-x-0.1.0-abb89-1025.sh");
        let n = home_with_sign(&t, "notsign.sh");
        let outside = t.0.join("SIGN-evil-0.1.0.sh");
        fs::write(&outside, "x").unwrap();
        let h = &t.0;
        let s = s.to_str().unwrap();
        let cases: Vec<(Value, bool, &str)> = vec![
            (json!(["aib", "consent", "ask"]), true, "aib_subcommand"),
            (json!(["aib"]), true, "aib_subcommand"),
            (json!(["aib", "--x"]), true, "aib_subcommand"),
            (json!(["aib", "test", "x"]), true, "aib_subcommand"),
            (json!(["aib", "update"]), true, "aib_subcommand"),
            (json!(["aib", "update", "--allow-unsigned=1"]), true, "aib_subcommand"),
            (json!(["aib", "plugins", "install", "--allow-unsigned", "x"]), true, "aib_allow_unsigned"),
            (json!(["aib", "plugins", "install", "--allow-unsigned=yes"]), true, "aib_allow_unsigned"),
            (json!(["claude", "plugin", "install", "x@m", "-y"]), true, "claude_flag"),
            (json!(["claude", "plugin", "install", "x@m", "--marketplace", "/tmp/evil"]), true, "claude_flag"),
            (json!(["claude", "plugin", "update", "x", "--accept-command", "sh"]), true, "claude_flag"),
            (json!(["claude", "--dangerously-skip-permissions"]), true, "claude_subcommand"),
            (json!(["claude", "-p", "x"]), true, "claude_subcommand"),
            (json!(["claude", "plugin", "list", "--dangerously-skip-permissions"]), true, "claude_flag"),
            (json!(["claude", "plugin", "install", "./local-dir"]), true, "claude_install_ref"),
            (json!(["claude", "plugin", "install"]), true, "claude_install_ref"),
            (json!(["claude", "plugin", "marketplace", "add", "x"]), true, "claude_subcommand"),
            (json!(["bash", s]), false, "sign_needs_strong"),
            (json!(["bash", outside.to_str().unwrap()]), true, "sign_path"),
            (json!(["bash", n.to_str().unwrap()]), true, "sign_path"),
            (json!(["bash", "-c", "rm -rf /"]), true, "sign_shape"),
            (json!(["bash", "~nosuchuser_zz9/SIGN-a.sh"]), true, "sign_missing"),
            (json!(["bash", "/nonexistent/SIGN-a.sh"]), true, "sign_missing"),
            (json!(["sh", "-c", "x"]), true, "not_allowed"),
            (json!(["rm", "-rf", "/"]), true, "not_allowed"),
            (json!(["aib", "now\u{0}x"]), true, "argv_chars"),
            (json!(["aib", ""]), true, "argv_chars"),
            (json!(["aib", 1]), true, "argv_chars"),
            (json!([]), true, "argv_shape"),
            (json!("aib now"), true, "argv_shape"),
            (Value::Array(vec![json!("aib"); MAX_ARGV + 1]), true, "argv_shape"),
            (json!(["aib", "now", "x".repeat(1600)]), true, "argv_too_long"),
        ];
        for (argv, strong, want) in cases {
            assert_eq!(c(argv.clone(), strong, h), Err(want), "{argv}");
        }
    }

    #[test]
    fn a_sign_reached_through_a_link_counts_where_it_really_is() {
        let t = td("lnk");
        let s = home_with_sign(&t, "SIGN-real-1.sh");
        let l = t.0.join("SIGN-link-1.sh");
        symlink(&s, &l).unwrap();
        // link outside pending pointing in: resolved path is inside ⇒ accepted (1.x resolve(strict=True))
        assert_eq!(c(json!(["bash", l.to_str().unwrap()]), true, &t.0), Ok("sign_script"));
        // link inside pending pointing out: refused
        let out = t.0.join("SIGN-out-1.sh");
        fs::write(&out, "x").unwrap();
        let l2 = t.0.join(".ez2-aib/pending/SIGN-in-1.sh");
        symlink(&out, &l2).unwrap();
        assert_eq!(c(json!(["bash", l2.to_str().unwrap()]), true, &t.0), Err("sign_path"));
        // "~/" is the given home
        assert_eq!(
            c(json!(["bash", "~/.ez2-aib/pending/SIGN-real-1.sh"]), true, &t.0),
            Ok("sign_script")
        );
    }

    // test-consent-generic-ebb36-1111.py G1Classify.test_g1_shape
    #[test]
    fn generic_shapes() {
        let t = td("gen");
        let prog = t.0.join("site-remove.sh");
        let params = t.0.join("params.env");
        fs::write(&prog, "x").unwrap();
        fs::write(&params, "SITE=s1\n").unwrap();
        let (p, q) = (prog.to_str().unwrap(), params.to_str().unwrap());
        let h = &t.0;
        assert_eq!(c(json!(["generic", p, "--params", q]), true, h), Ok("shared_program"));
        assert_eq!(c(json!(["generic", p, "--params", q]), false, h), Err("generic_needs_strong"));
        assert_eq!(c(json!(["generic", p, "--params"]), true, h), Err("generic_shape"));
        assert_eq!(c(json!(["generic", p, "--env", q]), true, h), Err("generic_shape"));
        assert_eq!(c(json!(["generic", "site-remove.sh", "--params", q]), true, h), Err("generic_missing"));
        let nope = t.0.join("nope");
        assert_eq!(c(json!(["generic", p, "--params", nope.to_str().unwrap()]), true, h), Err("generic_missing"));
        assert_eq!(c(json!(["generic", p, "--params", "~nosuchuser_zz9/x"]), true, h), Err("generic_missing"));
    }

    // test-consent-popup-abb89-1025.py Whitelist.test_c11_bad_chars
    #[test]
    fn invisible_characters_are_refused_not_cleaned() {
        let t = td("chr");
        for s in [
            "a\nb", "a\rb", "a\u{1b}b", "a\u{200b}b", "a\u{202e}b", "a\u{2066}b", "a\u{feff}b", "a\u{85}b",
            "a\u{2028}b", "a\u{e0041}b", "a\u{180e}b", "a\u{34f}b", "a\u{3164}b", "a\u{115f}b", "a\u{ffa0}b",
            "a\u{2800}b", "a\u{fe0f}b", "a\u{e0100}b", "a\u{206a}b", "a\u{fff9}b", "a\u{600}b", "a\u{1bca0}b",
            "a\u{1d173}b", "a\u{e000}b", "a\u{378}b", "a\u{a0}b", "a\u{3000}b", "a\u{2009}b",
        ] {
            assert_eq!(c(json!(["aib", "now", s]), true, &t.0), Err("argv_chars"), "{s:?}");
        }
    }

    #[test]
    fn shell_quote_matches_python_shlex() {
        assert_eq!(shell_quote(""), "''");
        assert_eq!(shell_quote("abc-_.,:=+%@/1"), "abc-_.,:=+%@/1");
        assert_eq!(shell_quote("a b"), "'a b'");
        assert_eq!(shell_quote("it's"), "'it'\"'\"'s'");
        assert_eq!(shell_quote("中文"), "'中文'");
        assert_eq!(shell_quote("$x"), "'$x'");
        let argv: Vec<String> = ["aib", "now", "a b", "1/2"].iter().map(|s| s.to_string()).collect();
        assert_eq!(display_cmd(&argv), "aib now 'a b' 1/2");
    }

    // test-consent-popup-abb89-1025.py :456
    #[test]
    fn summary_is_cleaned_to_one_line() {
        assert_eq!(clean_summary("進\u{202e}度\u{e0100}\n下一步"), "進度 下一步");
        assert_eq!(clean_summary("  \t x \r\n"), "x");
        assert_eq!(clean_summary(""), "");
        let long = "字".repeat(SUMMARY_MAX + 5);
        let got = clean_summary(&long);
        assert_eq!(got.chars().count(), SUMMARY_MAX);
        assert!(got.ends_with('…'));
        assert_eq!(clean_summary(&"字".repeat(SUMMARY_MAX)).chars().count(), SUMMARY_MAX);
    }

    #[test]
    fn lint_ack_codes() {
        assert_eq!(lint_acks(&Value::Null), Some(vec![]));
        assert_eq!(lint_acks(&json!("")), Some(vec![]));
        assert_eq!(lint_acks(&json!([])), Some(vec![]));
        assert_eq!(lint_acks(&json!("S4,S2, S2")), Some(vec!["S2".into(), "S4".into()]));
        assert_eq!(lint_acks(&json!(["S1", "S5"])), Some(vec!["S1".into(), "S5".into()]));
        assert_eq!(lint_acks(&json!("S2,U1")), None);
        assert_eq!(lint_acks(&json!("R1")), None);
        assert_eq!(lint_acks(&json!("S6")), None);
        assert_eq!(lint_acks(&json!([2])), None);
        assert_eq!(lint_acks(&json!(3)), None);
        assert_eq!(lint_acks(&json!(false)), None);
    }

    #[test]
    fn names_and_seats() {
        assert!(id_ok("1794575050-e37a6120") && id_ok(&"a".repeat(64)));
        assert!(!id_ok("") && !id_ok(&"a".repeat(65)) && !id_ok("a.b") && !id_ok("a/b") && !id_ok("a\n"));
        assert!(sign_name_ok("SIGN-mini-runner-acl-c477a-1113.sh"));
        assert!(!sign_name_ok("SIGN-.sh") && !sign_name_ok("SIGN-a b.sh") && !sign_name_ok("sign-a.sh") && !sign_name_ok("SIGN-a.sh\n"));
        assert!(plugin_ref_ok("aib-boards@ez2") && !plugin_ref_ok("x@") && !plugin_ref_ok("@m") && !plugin_ref_ok("./x"));
        assert_eq!(seat(&json!("c477a")), Some("c477a".into()));
        assert_eq!(seat(&json!("C477A")), None);
        assert_eq!(seat(&json!("ab")), None);
        assert_eq!(seat(&json!(5)), None);
    }

    #[test]
    fn reading_refuses_links_pipes_and_big_files() {
        let t = td("rd");
        let ok = t.0.join("a.json");
        fs::write(&ok, br#"{"v":1,"argv":["aib","now"]}"#).unwrap();
        assert_eq!(read_json(&ok, MAX_REQ_BYTES).unwrap()["v"], json!(1));
        let lnk = t.0.join("l.json");
        symlink(&ok, &lnk).unwrap();
        assert!(read_regular(&lnk, MAX_REQ_BYTES).is_none(), "a link must not be followed");
        let fifo = t.0.join("p.json");
        nix::unistd::mkfifo(&fifo, nix::sys::stat::Mode::from_bits_truncate(0o600)).unwrap();
        assert!(read_regular(&fifo, MAX_REQ_BYTES).is_none(), "a pipe must not block or be read");
        let big = t.0.join("b.json");
        fs::write(&big, vec![b' '; MAX_REQ_BYTES as usize + 1]).unwrap();
        assert!(read_regular(&big, MAX_REQ_BYTES).is_none());
        let edge = t.0.join("e.json");
        fs::write(&edge, vec![b' '; MAX_REQ_BYTES as usize]).unwrap();
        assert_eq!(read_regular(&edge, MAX_REQ_BYTES).unwrap().len(), MAX_REQ_BYTES as usize);
        let arr = t.0.join("arr.json");
        fs::write(&arr, b"[1]").unwrap();
        assert!(read_json(&arr, MAX_REQ_BYTES).is_none());
        let bad = t.0.join("bad.json");
        fs::write(&bad, b"\xff{}").unwrap();
        assert!(read_json(&bad, MAX_REQ_BYTES).is_none());
        assert!(read_regular(&t.0.join("missing"), MAX_REQ_BYTES).is_none());
        assert!(read_regular(&t.0, MAX_REQ_BYTES).is_none(), "a directory is not a request");
    }

    #[test]
    fn atomic_write_leaves_one_0600_file() {
        let t = td("wr");
        let p = t.0.join("inbox").join("1-abcd.json");
        let v = json!({"v": 1, "summary": "中文", "argv": ["aib", "now"]});
        write_atomic(&p, &v).unwrap();
        let m = fs::metadata(&p).unwrap();
        assert_eq!(m.permissions().mode() & 0o777, 0o600);
        assert_eq!(read_json(&p, MAX_REQ_BYTES).unwrap(), v.as_object().unwrap().clone());
        let names: Vec<_> = fs::read_dir(t.0.join("inbox")).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(names.len(), 1, "no temp file left: {names:?}");
        let text = fs::read_to_string(&p).unwrap();
        assert!(text.contains("\"summary\": \"中文\""), "UTF-8, not escaped: {text}");
        write_atomic(&p, &json!({"v": 2})).unwrap();
        assert_eq!(read_json(&p, MAX_REQ_BYTES).unwrap()["v"], json!(2));
    }
}
