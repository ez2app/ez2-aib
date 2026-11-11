// SPDX-License-Identifier: Apache-2.0
//! A reviewed shared program and the parameter file a consent request brings with it.
//!
//! The shared program is reviewed once and pinned by its sha256; each use only brings a
//! short parameter file, which the consent dialog shows in full. The rules here decide
//! what may be run that way:
//!
//! - the program lives in `<home>/.agents/skills/<skill>/tools/generic/` (a folder kept in
//!   git), has a plain name ending in `.sh` or `.py`, is a regular file, and its sha256 is
//!   the pin the request names (the pin is shown to the person, who checks it against the
//!   review; no local file says which pin is right, since a same-user AI could write it);
//! - the parameter file is `KEY=VALUE` lines, ASCII only, nothing a shell or Python could
//!   read as code: keys from a fixed shape, never a name that changes how bash or python
//!   starts, values from a small character set.
//!
//! No I/O beyond reading the two files and writing the program's copy.

use sha2::{Digest, Sha256};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

/// Largest parameter file read.
pub const PARAMS_MAX_BYTES: usize = 4096;
/// Most keys in one parameter file.
pub const PARAMS_MAX_KEYS: usize = 32;
/// Longest key (the first letter included).
pub const KEY_MAX: usize = 32;
/// Longest value.
pub const VALUE_MAX: usize = 256;
/// Largest shared program read.
pub const PROGRAM_MAX_BYTES: u64 = 1024 * 1024;

/// Names that change how bash or python starts, where it looks for programs, or which
/// state the job writes to. A parameter may never set one of these.
const RESERVED: &[&str] = &[
    "PATH",
    "HOME",
    "IFS",
    "ENV",
    "BASH_ENV",
    "BASHOPTS",
    "SHELLOPTS",
    "SHELL",
    "PS4",
    "CDPATH",
    "GLOBIGNORE",
    "PROMPT_COMMAND",
    "TMPDIR",
    "LANG",
    "TERM",
    "USER",
    "LOGNAME",
    "TZ",
    "POSIXLY_CORRECT",
    "RUBYOPT",
    "RUBYLIB",
    "NODE_OPTIONS",
    "NODE_PATH",
];
/// Prefixes with the same effect (dynamic loaders, Python start-up, locale, our own state).
const RESERVED_PREFIX: &[&str] = &[
    "LD_", "DYLD_", "PYTHON", "LC_", "BASH_", "EZ2_AIB_", "MALLOC", "GIT_", "SSH_", "PERL5",
];

/// The line in a shared program that says which parameters it takes (inside the pinned
/// bytes, so the review covers it): `# aib-consent-params: SITE CT IP`.
pub const DECLARE: &str = "# aib-consent-params:";
/// The declaration must sit in the program's first lines.
pub const DECLARE_WITHIN_LINES: usize = 10;

/// Why a program or a parameter file is refused. `line` is 1-based, 0 when not about a line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused {
    pub code: &'static str,
    pub line: usize,
}

fn refused(code: &'static str, line: usize) -> Refused {
    Refused { code, line }
}

/// Is this a key a parameter file may set?
pub fn key_ok(k: &str) -> bool {
    let b = k.as_bytes();
    if b.is_empty() || b.len() > KEY_MAX || !b[0].is_ascii_uppercase() {
        return false;
    }
    if !b
        .iter()
        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || *c == b'_')
    {
        return false;
    }
    !RESERVED.contains(&k) && !RESERVED_PREFIX.iter().any(|p| k.starts_with(p))
}

/// Is this a value a parameter file may carry? No blank, quote, `$`, backquote, `;`, `&`,
/// `|`, `<`, `>`, `*`, `?`, `~`, brackets, backslash, control or non-ASCII character.
pub fn value_ok(v: &str) -> bool {
    !v.is_empty()
        && v.len() <= VALUE_MAX
        && v.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._:/@+,=-".contains(&c))
}

/// The keys a program declares it takes, from its first [`DECLARE_WITHIN_LINES`] lines.
/// None: no declaration, more than one, or a declared key that is not a key a parameter
/// file may set.
pub fn declared_keys(program: &[u8]) -> Option<Vec<String>> {
    let text = std::str::from_utf8(program).ok()?;
    let mut found: Option<Vec<String>> = None;
    for line in text.lines().take(DECLARE_WITHIN_LINES) {
        let Some(rest) = line.strip_prefix(DECLARE) else {
            continue;
        };
        if found.is_some() {
            return None;
        }
        let keys: Vec<String> = rest.split_ascii_whitespace().map(str::to_owned).collect();
        if keys.is_empty() || keys.len() > PARAMS_MAX_KEYS || !keys.iter().all(|k| key_ok(k)) {
            return None;
        }
        found = Some(keys);
    }
    found
}

/// Every key in `params` is one the program declares.
pub fn params_declared(params: &[(String, String)], declared: &[String]) -> Result<(), Refused> {
    match params.iter().position(|(k, _)| !declared.contains(k)) {
        Some(i) => Err(refused("params_key_undeclared", i + 1)),
        None => Ok(()),
    }
}

/// The review of a shared program: its first line says it passed (RULES §1 shape) and the
/// file names the pin. Returns that first line, which the dialog shows. A review proves
/// nothing by itself (a same-user AI can write one); it puts the reviewer's verdict next
/// to the pin in front of the person.
pub fn review_first_line(review: &str, pin: &str) -> Result<String, Refused> {
    let first = review.lines().next().unwrap_or("");
    if !first.starts_with("結論: 通過 ") || first.len() > 400 || first.chars().any(char::is_control)
    {
        return Err(refused("review_not_passed", 1));
    }
    if !review.contains(pin) {
        return Err(refused("review_without_pin", 0));
    }
    Ok(first.to_owned())
}

/// Read a parameter file's text. Blank lines and lines starting with `#` are skipped; every
/// other line is `KEY=VALUE` with nothing around it. Keys may not repeat.
pub fn parse_params(text: &str) -> Result<Vec<(String, String)>, Refused> {
    if text.len() > PARAMS_MAX_BYTES {
        return Err(refused("params_too_big", 0));
    }
    let mut out: Vec<(String, String)> = Vec::new();
    for (i, line) in text.split('\n').enumerate() {
        let n = i + 1;
        if !line.is_ascii() {
            return Err(refused("params_not_ascii", n));
        }
        if line.bytes().any(|c| c.is_ascii_control()) {
            return Err(refused("params_control_char", n));
        }
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            return Err(refused("params_no_equals", n));
        };
        if !key_ok(k) {
            return Err(refused("params_key", n));
        }
        if !value_ok(v) {
            return Err(refused("params_value", n));
        }
        if out.iter().any(|(old, _)| old == k) {
            return Err(refused("params_key_twice", n));
        }
        if out.len() == PARAMS_MAX_KEYS {
            return Err(refused("params_too_many", n));
        }
        out.push((k.to_owned(), v.to_owned()));
    }
    if out.is_empty() {
        return Err(refused("params_empty", 0));
    }
    Ok(out)
}

/// The interpreter a shared program runs under, from its name.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    Bash,
    Python,
}

impl Lang {
    /// The argv that runs a copy at `path` (python with `-I`: no user site, no PYTHON*,
    /// no script folder on the import path).
    pub fn argv(self, path: &Path) -> Vec<String> {
        let p = path.to_string_lossy().into_owned();
        match self {
            Lang::Bash => vec!["/bin/bash".into(), p],
            Lang::Python => vec!["/usr/bin/python3".into(), "-I".into(), p],
        }
    }
}

/// A plain program name: letters, digits, `.`, `_`, `-`, not starting with `.` or `-`,
/// ending in `.sh` or `.py`.
pub fn program_lang(name: &str) -> Option<Lang> {
    let b = name.as_bytes();
    if b.is_empty()
        || b.len() > 128
        || b[0] == b'.'
        || b[0] == b'-'
        || !b
            .iter()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(c))
    {
        return None;
    }
    if name.len() > 3 && name.ends_with(".sh") {
        Some(Lang::Bash)
    } else if name.len() > 3 && name.ends_with(".py") {
        Some(Lang::Python)
    } else {
        None
    }
}

/// Is `real` (already resolved, no links left) `<home>/.agents/skills/<skill>/tools/generic/<name>`?
pub fn program_place_ok(home: &Path, real: &Path) -> bool {
    let Ok(skills) = home.join(".agents").join("skills").canonicalize() else {
        return false;
    };
    let Ok(rest) = real.strip_prefix(&skills) else {
        return false;
    };
    let parts: Vec<_> = rest.components().collect();
    parts.len() == 4
        && parts[1].as_os_str() == "tools"
        && parts[2].as_os_str() == "generic"
        && parts[0]
            .as_os_str()
            .to_str()
            .is_some_and(|s| !s.starts_with('.'))
}

/// Is `pin` 64 lowercase hex digits?
pub fn pin_ok(pin: &str) -> bool {
    pin.len() == 64
        && pin
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// A checked program: its resolved path, language, bytes and sha256.
#[derive(Debug, Clone)]
pub struct Program {
    pub real: PathBuf,
    pub lang: Lang,
    pub bytes: Vec<u8>,
    pub sha256: String,
}

/// Read and check the shared program against its pin.
pub fn check_program(home: &Path, path: &Path, pin: &str) -> Result<Program, Refused> {
    if !pin_ok(pin) {
        return Err(refused("pin_shape", 0));
    }
    // the path the request names may not be a link itself (c477a Q3); the resolved path must
    // still sit in the allowed folder
    match fs::symlink_metadata(path) {
        Ok(m) if m.file_type().is_symlink() => return Err(refused("program_is_link", 0)),
        Ok(_) => {}
        Err(_) => return Err(refused("program_missing", 0)),
    }
    let real = path
        .canonicalize()
        .map_err(|_| refused("program_missing", 0))?;
    let lang = real
        .file_name()
        .and_then(|n| n.to_str())
        .and_then(program_lang)
        .ok_or(refused("program_name", 0))?;
    if !program_place_ok(home, &real) {
        return Err(refused("program_place", 0));
    }
    let meta = fs::metadata(&real).map_err(|_| refused("program_missing", 0))?;
    if !meta.is_file() {
        return Err(refused("program_not_file", 0));
    }
    let mut bytes = Vec::new();
    fs::File::open(&real)
        .and_then(|f| f.take(PROGRAM_MAX_BYTES + 1).read_to_end(&mut bytes))
        .map_err(|_| refused("program_unreadable", 0))?;
    if bytes.len() as u64 > PROGRAM_MAX_BYTES {
        return Err(refused("program_too_big", 0));
    }
    let sha256 = sha256_hex(&bytes);
    if sha256 != pin {
        return Err(refused("program_pin_mismatch", 0));
    }
    Ok(Program {
        real,
        lang,
        bytes,
        sha256,
    })
}

/// Read a parameter file (at most [`PARAMS_MAX_BYTES`] + 1 bytes are read) and parse it.
/// Returns the raw text too: the dialog and the record show it as it is.
pub fn read_params(path: &Path) -> Result<(String, Vec<(String, String)>), Refused> {
    let meta = fs::symlink_metadata(path).map_err(|_| refused("params_missing", 0))?;
    if !meta.is_file() {
        return Err(refused("params_not_file", 0));
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .and_then(|f| f.take(PARAMS_MAX_BYTES as u64 + 1).read_to_end(&mut bytes))
        .map_err(|_| refused("params_unreadable", 0))?;
    if bytes.len() > PARAMS_MAX_BYTES {
        return Err(refused("params_too_big", 0));
    }
    let text = String::from_utf8(bytes).map_err(|_| refused("params_not_ascii", 0))?;
    let parsed = parse_params(&text)?;
    Ok((text, parsed))
}

/// Write `bytes` into `dir` (which the caller made 0700) as a new 0600 file named `name`,
/// read it back and check its sha256 once more. Returns the copy's path. The dialog, the
/// record and the run all use this copy, never the original again.
pub fn stage_bytes(dir: &Path, name: &str, bytes: &[u8]) -> Result<PathBuf, Refused> {
    use std::io::Write;
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let meta = fs::symlink_metadata(dir).map_err(|_| refused("stage_dir", 0))?;
    if !meta.is_dir() || meta.permissions().mode() & 0o077 != 0 {
        return Err(refused("stage_dir", 0));
    }
    if name.is_empty() || name.starts_with('.') || name.contains('/') {
        return Err(refused("stage_name", 0));
    }
    let copy = dir.join(name);
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&copy)
        .map_err(|_| refused("stage_write", 0))?;
    f.write_all(bytes)
        .and_then(|()| f.sync_all())
        .map_err(|_| refused("stage_write", 0))?;
    drop(f);
    let back = fs::read(&copy).map_err(|_| refused("stage_write", 0))?;
    if sha256_hex(&back) != sha256_hex(bytes) {
        return Err(refused("stage_hash_changed", 0));
    }
    Ok(copy)
}

/// Read a staged copy back and check it is still the bytes with this sha256.
pub fn read_staged(path: &Path, sha256: &str, limit: u64) -> Result<Vec<u8>, Refused> {
    let meta = fs::symlink_metadata(path).map_err(|_| refused("staged_missing", 0))?;
    if !meta.is_file() || meta.len() > limit {
        return Err(refused("staged_missing", 0));
    }
    let bytes = fs::read(path).map_err(|_| refused("staged_missing", 0))?;
    if sha256_hex(&bytes) != sha256 {
        return Err(refused("staged_hash_changed", 0));
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_plain_parameter_file_reads() {
        let t = "# site remove\nSITE=s-abc123\nCT=104\nIP=10.77.0.4\nHOST=shop.example.test\nMODE=dry\n";
        let p = parse_params(t).expect("parses");
        assert_eq!(p.len(), 5);
        assert_eq!(p[0], ("SITE".into(), "s-abc123".into()));
        assert_eq!(p[4], ("MODE".into(), "dry".into()));
    }

    // knife: the reserved names are refused
    #[test]
    fn names_that_change_how_bash_or_python_start_are_refused() {
        for k in [
            "PATH",
            "BASH_ENV",
            "ENV",
            "IFS",
            "SHELLOPTS",
            "PS4",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "LC_ALL",
            "BASH_FUNC_X",
            "EZ2_AIB_STATE_ROOT",
            "HOME",
            "TMPDIR",
            "GIT_SSH",
            "SSH_AUTH_SOCK",
            "RUBYOPT",
            "PERL5OPT",
            "NODE_OPTIONS",
            "POSIXLY_CORRECT",
        ] {
            assert!(!key_ok(k), "{k}");
            assert_eq!(
                parse_params(&format!("{k}=x\n")),
                Err(refused("params_key", 1)),
                "{k}"
            );
        }
        assert!(key_ok("SITE"));
        assert!(key_ok("ARCHIVE_TO"));
    }

    #[test]
    fn keys_have_one_shape() {
        for k in [
            "",
            "site",
            "1SITE",
            "_SITE",
            "SI-TE",
            "SI TE",
            &"A".repeat(33),
        ] {
            assert!(!key_ok(k), "{k:?}");
        }
        assert!(key_ok(&"A".repeat(32)));
    }

    // knife: the value character set
    #[test]
    fn values_carry_nothing_a_shell_could_read() {
        for v in [
            "", "a b", "a'b", "a\"b", "$x", "`id`", "a;b", "a&b", "a|b", "a<b", "a>b", "a*", "a?",
            "~", "a(b)", "a[b]", "a{b}", "a\\b", "a!b", "a#b", "a%b",
        ] {
            assert!(!value_ok(v), "{v:?}");
        }
        for v in [
            "s-abc",
            "10.77.0.4",
            "shop.example.test",
            "a@b",
            "x/y:z",
            "k=v",
            "1,2",
            "+x",
        ] {
            assert!(value_ok(v), "{v:?}");
        }
        assert!(!value_ok(&"a".repeat(257)));
        assert!(value_ok(&"a".repeat(256)));
    }

    #[test]
    fn lines_are_exactly_key_equals_value() {
        assert_eq!(parse_params("SITE = x\n"), Err(refused("params_key", 1)));
        assert_eq!(parse_params("SITE=x \n"), Err(refused("params_value", 1)));
        assert_eq!(
            parse_params(" # x\nSITE=x\n"),
            Err(refused("params_no_equals", 1))
        );
        assert_eq!(parse_params("SITE\n"), Err(refused("params_no_equals", 1)));
        assert_eq!(
            parse_params("SITE=x\r\n"),
            Err(refused("params_control_char", 1))
        );
        assert_eq!(
            parse_params("SITE=x\tY\n"),
            Err(refused("params_control_char", 1))
        );
        assert_eq!(
            parse_params("SITE=caf\u{e9}\n"),
            Err(refused("params_not_ascii", 1))
        );
        assert_eq!(
            parse_params("A=1\nA=2\n"),
            Err(refused("params_key_twice", 2))
        );
        assert_eq!(parse_params("# only\n\n"), Err(refused("params_empty", 0)));
    }

    #[test]
    fn size_and_count_have_limits() {
        let many: String = (0..33).map(|i| format!("K{i}=v\n")).collect();
        assert_eq!(parse_params(&many), Err(refused("params_too_many", 33)));
        let big = format!("A={}\n", "a".repeat(200)).repeat(21);
        assert_eq!(parse_params(&big), Err(refused("params_too_big", 0)));
    }

    #[test]
    fn program_names_and_languages() {
        assert_eq!(program_lang("site-remove.sh"), Some(Lang::Bash));
        assert_eq!(program_lang("dns-rm.py"), Some(Lang::Python));
        for n in [
            ".sh",
            "a.bash",
            ".hidden.sh",
            "-x.sh",
            "a b.sh",
            "a$.sh",
            "x.sh.txt",
            "x",
        ] {
            assert_eq!(program_lang(n), None, "{n}");
        }
        assert_eq!(
            Lang::Python.argv(Path::new("/t/x.py")),
            vec!["/usr/bin/python3", "-I", "/t/x.py"]
        );
    }

    // knife: keys outside the program's declaration are refused
    #[test]
    fn only_the_keys_the_program_declares() {
        let prog = b"#!/bin/bash\n# aib-consent-params: SITE CT\nset -eu\n";
        let decl = declared_keys(prog).expect("declared");
        assert_eq!(decl, vec!["SITE", "CT"]);
        let ok = parse_params("SITE=s-1\nCT=104\n").expect("parses");
        assert_eq!(params_declared(&ok, &decl), Ok(()));
        let extra = parse_params("SITE=s-1\nGITHUB_TOKEN_FILE=x\n").expect("parses");
        assert_eq!(
            params_declared(&extra, &decl),
            Err(refused("params_key_undeclared", 2))
        );
    }

    #[test]
    fn a_declaration_is_one_line_near_the_top_with_allowed_keys() {
        assert_eq!(declared_keys(b"#!/bin/bash\nset -eu\n"), None);
        let two = b"# aib-consent-params: A\n# aib-consent-params: B\n";
        assert_eq!(declared_keys(two), None);
        assert_eq!(declared_keys(b"# aib-consent-params: SITE PATH\n"), None);
        assert_eq!(declared_keys(b"# aib-consent-params:\n"), None);
        let late: String = "x\n".repeat(10) + "# aib-consent-params: SITE\n";
        assert_eq!(declared_keys(late.as_bytes()), None);
    }

    #[test]
    fn a_review_has_passed_and_names_the_pin() {
        let pin = "a".repeat(64);
        let good = format!("結論: 通過 頭: x H/M/L/N: 0/0/0/0 範圍: 全文\nsha256 {pin}\n");
        assert!(review_first_line(&good, &pin).is_ok());
        let failed = format!("結論: 不通過 頭: x\n{pin}\n");
        assert_eq!(
            review_first_line(&failed, &pin),
            Err(refused("review_not_passed", 1))
        );
        let title = format!("# 審查\n結論: 通過 頭: x\n{pin}\n");
        assert_eq!(
            review_first_line(&title, &pin),
            Err(refused("review_not_passed", 1))
        );
        let other = "結論: 通過 頭: x\n".to_owned() + &"b".repeat(64);
        assert_eq!(
            review_first_line(&other, &pin),
            Err(refused("review_without_pin", 0))
        );
    }

    fn shared_home(tag: &str) -> PathBuf {
        let home = std::env::temp_dir().join(format!("aib-consent-params-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&home);
        fs::create_dir_all(home.join(".agents/skills/ops/tools/generic")).expect("dirs");
        home
    }

    // knife: the pin is compared; knife: the place is checked
    #[test]
    fn a_program_must_match_its_pin_and_sit_in_the_shared_folder() {
        let home = shared_home("pin");
        let prog = home.join(".agents/skills/ops/tools/generic/site-remove.sh");
        let body = b"#!/bin/bash\n# aib-consent-params: SITE\necho $SITE\n";
        fs::write(&prog, body).expect("write");
        let pin = sha256_hex(body);
        let p = check_program(&home, &prog, &pin).expect("pinned program");
        assert_eq!(p.lang, Lang::Bash);
        assert_eq!(p.sha256, pin);
        assert_eq!(
            check_program(&home, &prog, &"0".repeat(64)).err(),
            Some(refused("program_pin_mismatch", 0))
        );
        let outside = home.join("site-remove.sh");
        fs::write(&outside, body).expect("write");
        assert_eq!(
            check_program(&home, &outside, &pin).err(),
            Some(refused("program_place", 0))
        );
        let link = home.join(".agents/skills/ops/tools/generic/link.sh");
        std::os::unix::fs::symlink(&prog, &link).expect("link");
        assert_eq!(
            check_program(&home, &link, &pin).err(),
            Some(refused("program_is_link", 0))
        );
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn a_staged_copy_is_checked_again() {
        use std::os::unix::fs::PermissionsExt;
        let dir = shared_home("stage").join("stage");
        fs::create_dir(&dir).expect("dir");
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).expect("chmod");
        let c = stage_bytes(&dir, "params.env", b"SITE=s-1\n").expect("staged");
        let sha = sha256_hex(b"SITE=s-1\n");
        assert_eq!(read_staged(&c, &sha, 4096).expect("same"), b"SITE=s-1\n");
        fs::write(&c, b"SITE=s-2\n").expect("tamper");
        assert_eq!(read_staged(&c, &sha, 4096).err(), Some(refused("staged_hash_changed", 0)));
        assert_eq!(stage_bytes(&dir, "params.env", b"x").err(), Some(refused("stage_write", 0)));
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).expect("chmod");
        assert_eq!(stage_bytes(&dir, "other", b"x").err(), Some(refused("stage_dir", 0)));
    }

    #[test]
    fn pins_are_64_lowercase_hex() {
        assert!(pin_ok(&"a".repeat(64)));
        assert!(!pin_ok(&"A".repeat(64)));
        assert!(!pin_ok(&"a".repeat(63)));
        assert!(!pin_ok(&"g".repeat(64)));
    }
}
