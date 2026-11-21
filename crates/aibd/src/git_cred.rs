//! `aibd git-cred`: the one-shot credential helper a push of a registered GitHub repository runs
//! with (DESIGN-gh-token-card-7b81e-1121 r1 L1; 5d33a PREREAD-ghtokencard L1).
//!
//! The push's git is given, as descriptor [`FD`], the read end of a pipe aibd has already
//! written the credential into ([`secret`]) and closed; every other descriptor above 2 is closed
//! for it (`aib_sys::stdio_only::stdio_and`). git is told to forget every other helper and to
//! run this one ([`config`]) with the host and the repository path it may answer for, and to
//! send that path (`credential.useHttpPath`). The helper answers `get` only, only when the
//! request names https, that host and that path, and only with what it reads from [`FD`] once.
//!
//! This file has no way to the keychain or any other store: it reads nothing but its standard
//! input and the descriptor it was given. So a seat that runs `aibd git-cred` itself reads only
//! its own descriptor 3, which holds no token. The token is in no argument, no environment
//! variable and no file.
use std::path::Path;

use crate::pr_api::Token;

/// The descriptor the helper reads its credential from.
pub const FD: i32 = 3;
/// The user name GitHub takes with a token over https.
pub const USER: &str = "x-access-token";
/// The most a request or a credential may be.
const MAX: usize = 4096;

/// What aibd writes for the helper: the two lines git reads back.
pub fn secret(token: &Token) -> Vec<u8> {
    let mut b = format!("username={USER}\npassword=").into_bytes();
    b.extend_from_slice(token.expose());
    b.push(b'\n');
    b
}

/// A credential [`secret`] wrote, and nothing else.
fn secret_ok(s: &[u8]) -> bool {
    let head = format!("username={USER}\npassword=");
    s.strip_prefix(head.as_bytes())
        .and_then(|r| r.strip_suffix(b"\n"))
        .is_some_and(|t| Token::new(t.to_vec()).is_some())
}

/// `owner/name` or `owner/name.git`, as a GitHub repository's path is.
fn path_ok(p: &str) -> bool {
    let Some((owner, name)) = p.split_once('/') else {
        return false;
    };
    let word = |w: &str, extra: &[u8]| {
        (1..=100).contains(&w.len())
            && w.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || extra.contains(&b))
    };
    word(owner, b"") && word(name, b"._") && !name.starts_with('.')
}

/// The `-c` pairs a push's git gets: the helper list emptied, then this one helper for `host`
/// and `path`, and the path sent with each request. None when `exe` or the names cannot be
/// written into one shell word safely.
pub fn config(exe: &Path, host: &str, path: &str) -> Option<Vec<String>> {
    let exe = exe.to_str()?;
    let host_ok = !host.is_empty()
        && host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-');
    if exe.contains(['\'', '\n', '\0']) || !exe.starts_with('/') || !host_ok || !path_ok(path) {
        return None;
    }
    Some(vec![
        "-c".into(),
        "credential.helper=".into(),
        "-c".into(),
        format!("credential.helper=!'{exe}' git-cred --fd {FD} --host {host} --path {path}"),
        "-c".into(),
        "credential.useHttpPath=true".into(),
    ])
}

/// The helper's answer to git's `op` and `request` (`key=value` lines) for `host` / `path`,
/// with the credential it was given; none: say nothing.
pub fn answer(op: &str, request: &str, host: &str, path: &str, given: &[u8]) -> Option<Vec<u8>> {
    if op != "get" || !secret_ok(given) {
        return None;
    }
    let mut seen = [false; 3];
    for line in request.lines() {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let (i, want) = match key {
            "protocol" => (0, "https"),
            "host" => (1, host),
            "path" => (2, path),
            _ => continue,
        };
        // a second line of a key must say the same
        if value != want {
            return None;
        }
        seen[i] = true;
    }
    seen.iter().all(|s| *s).then(|| given.to_vec())
}

/// `aibd git-cred --fd 3 --host <host> --path <path> <op>`, as git runs it.
pub fn main(args: &[&str]) -> std::process::ExitCode {
    // git ignores a helper that says nothing; an error is the same nothing
    let ["--fd", fd, "--host", host, "--path", path, op] = args else {
        return std::process::ExitCode::SUCCESS;
    };
    if *fd != FD.to_string() || *op != "get" {
        return std::process::ExitCode::SUCCESS;
    }
    let mut request = String::new();
    let _ = std::io::Read::read_to_string(
        &mut std::io::Read::take(std::io::stdin(), u64::try_from(MAX).unwrap_or(0)),
        &mut request,
    );
    let given = given();
    if let Some(out) = answer(op, &request, host, path, &given) {
        let _ = std::io::Write::write_all(&mut std::io::stdout(), &out);
    }
    std::process::ExitCode::SUCCESS
}

#[cfg(target_os = "macos")]
fn given() -> Vec<u8> {
    aib_sys::stdio_only::read_given(FD, MAX).unwrap_or_default()
}

/// Elsewhere nothing is handed over yet (DESIGN-gh-token-card L1: Windows passes one handle,
/// written when its owner fingerprint is wired).
#[cfg(not(target_os = "macos"))]
fn given() -> Vec<u8> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tok() -> Token {
        Token::new(b"github_pat_TEST_7b81e".to_vec()).unwrap()
    }

    #[test]
    fn the_helper_answers_get_for_its_own_host_and_path_only() {
        let given = secret(&tok());
        assert_eq!(
            given,
            b"username=x-access-token\npassword=github_pat_TEST_7b81e\n"
        );
        let req = "protocol=https\nhost=github.com\npath=ez2app/demo.git\n";
        let (h, p) = ("github.com", "ez2app/demo.git");
        assert_eq!(answer("get", req, h, p, &given), Some(given.clone()));
        for (op, r) in [
            ("store", req),
            ("erase", req),
            (
                "get",
                "protocol=http\nhost=github.com\npath=ez2app/demo.git\n",
            ),
            (
                "get",
                "protocol=https\nhost=evil.example\npath=ez2app/demo.git\n",
            ),
            (
                "get",
                "protocol=https\nhost=github.com\npath=ez2app/other.git\n",
            ),
            ("get", "protocol=https\nhost=github.com\n"),
            (
                "get",
                "protocol=https\nhost=github.com\nhost=evil.example\npath=ez2app/demo.git\n",
            ),
        ] {
            assert_eq!(
                answer(op, r, h, p, &given),
                None,
                "reason: answered {op} {r:?}"
            );
        }
        // only what secret() wrote: no third line, no other user
        for bad in [
            &b""[..],
            b"username=x-access-token\npassword=a b\n",
            b"username=x-access-token\npassword=t\nhost=evil\n",
            b"username=someone\npassword=t\n",
        ] {
            assert_eq!(
                answer("get", req, h, p, bad),
                None,
                "reason: passed {bad:?}"
            );
        }
    }

    #[test]
    fn git_forgets_every_other_helper_and_the_token_is_in_no_argument() {
        let c = config(
            Path::new("/Applications/AIB/aibd"),
            "github.com",
            "ez2app/demo.git",
        )
        .unwrap();
        assert_eq!(
            c[0..2],
            ["-c", "credential.helper="],
            "reason: list_not_cleared"
        );
        assert_eq!(
            c[3],
            "credential.helper=!'/Applications/AIB/aibd' git-cred --fd 3 --host github.com --path ez2app/demo.git"
        );
        assert_eq!(c[5], "credential.useHttpPath=true", "reason: path_not_sent");
        for (exe, host, path) in [
            ("aibd", "github.com", "o/r"),
            ("/a'b/aibd", "github.com", "o/r"),
            ("/aibd", "git hub", "o/r"),
            ("/aibd", "github.com", "o/r;x"),
            ("/aibd", "github.com", "o/../r"),
            ("/aibd", "github.com", "o"),
        ] {
            assert_eq!(
                config(Path::new(exe), host, path),
                None,
                "reason: {exe} {host} {path}"
            );
        }
    }

    /// 5d33a L1: this file reads no store; a seat running `aibd git-cred` gets nothing.
    #[test]
    fn the_helper_has_no_way_to_a_store() {
        let src = include_str!("git_cred.rs");
        let code = src.split("#[cfg(test)]").next().unwrap();
        for word in [
            "Keychain",
            "Secrets",
            "presence_macos",
            "password(",
            "KEEPER",
            "gh_token",
        ] {
            assert!(!code.contains(word), "reason: store_reachable {word}");
        }
    }
}
