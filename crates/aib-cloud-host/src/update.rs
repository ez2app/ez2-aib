// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host self-update <version>` — the host takes its own binary from a GitHub
//! release instead of someone scp-ing a CI artifact (大綱 r2 第 26 件, 11-09).
//!
//! ```text
//! aib-cloud-host self-update <version> [--dest /usr/local/sbin/aib-cloud-host]
//!                                      [--repo ez2app/ez2-aib] [--base-url <https://… | file:///…>]
//! ```
//!
//! What it does, in order, and it stops at the first thing that is not exactly right:
//!
//! 1. `<version>` must be `X.Y.Z` or `X.Y.Z-preview.N`; the release is tag `cloud-host-v<version>`
//!    and the file is `aib-cloud-host-<version>-x86_64-unknown-linux-musl` (the only target the
//!    `cloud-host` workflow builds; the host is a Proxmox x86_64 box with no toolchain).
//! 2. If the file already installed at `--dest` has that release's SHA-256, nothing is touched
//!    (rc 0, says so).
//! 3. `curl` (argument list, `--proto =https --tlsv1.2`, 180 s) fetches the binary and the
//!    release's `SHA256SUMS` into a temp directory **next to** `--dest` (same filesystem, so the
//!    final step is one `rename`).
//! 4. `sha256sum` of the download must equal the line for that file in `SHA256SUMS`.
//! 5. The download is made executable and run with `--version`; it must print exactly
//!    `aib-cloud-host <version> linux-x86_64`.
//! 6. The installed file (if any) is renamed to `<dest>.prev`, the download renamed onto `--dest`.
//!    If that last rename fails the previous file is put back. The temp directory is removed.
//!
//! `--base-url` exists for the release workflow's verify job, which points it at a `file://`
//! directory holding the files it just built: the same code path runs end to end with no
//! network and no public release. Any other scheme than `https://` or `file://` is refused.
//! No shell strings anywhere; every child is `Command::new(prog).args([...])`.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::probe::run_capture;

pub const TARGET: &str = "x86_64-unknown-linux-musl";
const DEFAULT_DEST: &str = "/usr/local/sbin/aib-cloud-host";
const DEFAULT_REPO: &str = "ez2app/ez2-aib";
const FETCH_TIMEOUT: Duration = Duration::from_secs(180);
const RUN_TIMEOUT: Duration = Duration::from_secs(10);

/// The resolved request: everything the steps need, nothing fetched yet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub version: String,
    pub asset: String,
    pub tag: String,
    pub base_url: String,
    pub dest: PathBuf,
}

/// `X.Y.Z` or `X.Y.Z-preview.N`, digits only, no leading `v`.
pub fn valid_version(v: &str) -> bool {
    let (core, pre) = match v.split_once('-') {
        Some((c, p)) => (c, Some(p)),
        None => (v, None),
    };
    let mut parts = core.split('.');
    let three = (0..3).all(|_| parts.next().is_some_and(is_number));
    if !three || parts.next().is_some() {
        return false;
    }
    match pre {
        None => true,
        Some(p) => p
            .strip_prefix("preview.")
            .is_some_and(|n| is_number(n) && !n.starts_with('0')),
    }
}

fn is_number(s: &str) -> bool {
    !s.is_empty() && s.len() <= 9 && s.bytes().all(|b| b.is_ascii_digit())
}

/// `owner/name`, each in `[A-Za-z0-9_.-]`, nothing else (it goes into a URL path).
pub fn valid_repo(r: &str) -> bool {
    let Some((owner, name)) = r.split_once('/') else {
        return false;
    };
    let ok = |s: &str| {
        !s.is_empty()
            && s.len() <= 100
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.' || b == b'-')
    };
    ok(owner) && ok(name)
}

pub fn asset_name(version: &str) -> String {
    format!("aib-cloud-host-{version}-{TARGET}")
}

pub fn tag_name(version: &str) -> String {
    format!("cloud-host-v{version}")
}

pub fn release_base_url(repo: &str, version: &str) -> String {
    format!(
        "https://github.com/{repo}/releases/download/{}",
        tag_name(version)
    )
}

/// What a correct build of `<version>` prints for `--version` on the host.
pub fn expected_version_line(version: &str) -> String {
    format!("aib-cloud-host {version} linux-x86_64")
}

/// The line this binary prints for `--version` (`aib-cloud-host <version> <os>-<arch>`).
pub fn own_version_line() -> String {
    format!(
        "aib-cloud-host {} {}-{}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    )
}

/// The SHA-256 (lowercase hex) that `SHA256SUMS` gives for `asset`. Accepts the two formats
/// `sha256sum` writes (`<hex>  <name>` and `<hex> *<name>`); exactly one line may name the
/// asset, and its hash must be 64 hex digits.
pub fn expected_sum(sums: &str, asset: &str) -> Result<String, String> {
    let mut found: Option<String> = None;
    for raw in sums.lines() {
        let line = raw.trim_end_matches('\r');
        let Some((hex, rest)) = line.split_once(' ') else {
            continue;
        };
        let name = rest.trim_start_matches(' ').trim_start_matches('*');
        if name != asset {
            continue;
        }
        if !is_sha256_hex(hex) {
            return Err(format!("SHA256SUMS: hash for {asset} is not 64 hex digits"));
        }
        if found.is_some() {
            return Err(format!("SHA256SUMS names {asset} more than once"));
        }
        found = Some(hex.to_ascii_lowercase());
    }
    found.ok_or_else(|| format!("SHA256SUMS has no line for {asset}"))
}

fn is_sha256_hex(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// The hash out of `sha256sum <file>` output (`<hex>  <path>`).
pub fn parse_sha256sum_output(out: &str) -> Option<String> {
    let first = out.lines().next()?;
    let hex = first.split(' ').next()?;
    is_sha256_hex(hex).then(|| hex.to_ascii_lowercase())
}

pub fn parse_args(args: &[String]) -> Result<Plan, String> {
    let mut it = args.iter();
    let version = it
        .next()
        .ok_or("self-update needs <version> (e.g. 0.2.0-preview.2)")?
        .clone();
    if !valid_version(&version) {
        return Err(format!(
            "version {version:?} must be X.Y.Z or X.Y.Z-preview.N (no leading v)"
        ));
    }
    let mut dest = PathBuf::from(DEFAULT_DEST);
    let mut repo = DEFAULT_REPO.to_owned();
    let mut base_url: Option<String> = None;
    while let Some(a) = it.next() {
        match a.as_str() {
            "--dest" => dest = PathBuf::from(it.next().ok_or("--dest needs a path")?),
            "--repo" => repo = it.next().ok_or("--repo needs owner/name")?.clone(),
            "--base-url" => base_url = Some(it.next().ok_or("--base-url needs a URL")?.clone()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !valid_repo(&repo) {
        return Err(format!("--repo {repo:?} must be owner/name"));
    }
    if !dest.is_absolute() {
        return Err(format!(
            "--dest {} must be an absolute path",
            dest.display()
        ));
    }
    let base_url = match base_url {
        None => release_base_url(&repo, &version),
        Some(u) if u.starts_with("https://") || u.starts_with("file:///") => {
            u.trim_end_matches('/').to_owned()
        }
        Some(u) => {
            return Err(format!(
                "--base-url {u:?} must start with https:// or file:///"
            ))
        }
    };
    Ok(Plan {
        asset: asset_name(&version),
        tag: tag_name(&version),
        version,
        base_url,
        dest,
    })
}

// ---------------------------------------------------------------- the steps

fn fetch(url: &str, to: &Path) -> Result<(), String> {
    let proto = if url.starts_with("file://") {
        "=file"
    } else {
        "=https"
    };
    let mut cmd = Command::new("curl");
    cmd.args([
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--proto",
        proto,
        "--tlsv1.2",
        "--max-time",
        "180",
        "--output",
    ])
    .arg(to)
    .arg(url);
    run_capture(&mut cmd, FETCH_TIMEOUT)
        .map(|_| ())
        .map_err(|e| format!("fetch {url}: {e}"))
}

fn sha256_of(path: &Path) -> Result<String, String> {
    let mut cmd = Command::new("sha256sum");
    cmd.arg("--").arg(path);
    let out = run_capture(&mut cmd, RUN_TIMEOUT)?;
    parse_sha256sum_output(&out)
        .ok_or_else(|| format!("sha256sum {}: unreadable output {out:?}", path.display()))
}

fn version_line_of(bin: &Path) -> Result<String, String> {
    let mut cmd = Command::new(bin);
    cmd.arg("--version");
    let out = run_capture(&mut cmd, RUN_TIMEOUT)?;
    Ok(out.trim().to_owned())
}

/// Remove the temp directory; a failure here is reported but never turns a success into an error.
fn cleanup(dir: &Path) {
    if let Err(e) = fs::remove_dir_all(dir) {
        eprintln!(
            "aib-cloud-host self-update: could not remove {}: {e}",
            dir.display()
        );
    }
}

pub fn main(args: &[String]) -> Result<u8, String> {
    let plan = parse_args(args)?;
    let dest_dir = plan
        .dest
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or_else(|| format!("--dest {} has no parent directory", plan.dest.display()))?;
    if !dest_dir.is_dir() {
        return Err(format!("{} is not a directory", dest_dir.display()));
    }

    // 3. fetch the sums first: a wrong version / missing release is found before the big file.
    let tmp = dest_dir.join(format!(".aib-cloud-host-update.{}", std::process::id()));
    fs::create_dir(&tmp).map_err(|e| format!("mkdir {}: {e}", tmp.display()))?;
    let result = install(&plan, &tmp);
    cleanup(&tmp);
    result
}

fn install(plan: &Plan, tmp: &Path) -> Result<u8, String> {
    let sums_path = tmp.join("SHA256SUMS");
    fetch(&format!("{}/SHA256SUMS", plan.base_url), &sums_path)?;
    let sums = fs::read_to_string(&sums_path).map_err(|e| format!("read SHA256SUMS: {e}"))?;
    let want = expected_sum(&sums, &plan.asset)?;

    // 2. already there?
    if plan.dest.is_file() && sha256_of(&plan.dest)? == want {
        println!(
            "aib-cloud-host self-update: {} already is {} ({}); nothing changed",
            plan.dest.display(),
            plan.tag,
            &want[..12]
        );
        return Ok(0);
    }

    let new_bin = tmp.join(&plan.asset);
    fetch(&format!("{}/{}", plan.base_url, plan.asset), &new_bin)?;

    // 4. checksum
    let got = sha256_of(&new_bin)?;
    if got != want {
        return Err(format!(
            "{}: SHA-256 {got} but SHA256SUMS says {want}; not installed",
            plan.asset
        ));
    }

    // 5. it runs and is the version it claims
    fs::set_permissions(&new_bin, fs::Permissions::from_mode(0o755))
        .map_err(|e| format!("chmod {}: {e}", new_bin.display()))?;
    let line = version_line_of(&new_bin)?;
    let expect = expected_version_line(&plan.version);
    if line != expect {
        return Err(format!(
            "{} --version printed {line:?}, want {expect:?}; not installed",
            plan.asset
        ));
    }

    // 6. swap
    let old_line = if plan.dest.exists() {
        Some(version_line_of(&plan.dest).unwrap_or_else(|e| format!("(unreadable: {e})")))
    } else {
        None
    };
    let prev = PathBuf::from(format!("{}.prev", plan.dest.display()));
    if old_line.is_some() {
        fs::rename(&plan.dest, &prev)
            .map_err(|e| format!("move {} aside: {e}", plan.dest.display()))?;
    }
    if let Err(e) = fs::rename(&new_bin, &plan.dest) {
        let restored = if old_line.is_some() {
            match fs::rename(&prev, &plan.dest) {
                Ok(()) => "previous file put back".to_owned(),
                Err(r) => format!("and putting the previous file back failed too: {r}"),
            }
        } else {
            "nothing was installed before".to_owned()
        };
        return Err(format!("install {}: {e}; {restored}", plan.dest.display()));
    }
    println!(
        "aib-cloud-host self-update: {} ← {} ({}; sha256 {}…); was {}{}",
        plan.dest.display(),
        plan.tag,
        line,
        &got[..12],
        old_line.as_deref().unwrap_or("not installed"),
        if old_line.is_some() {
            format!("; previous kept at {}", prev.display())
        } else {
            String::new()
        }
    );
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| (*x).to_owned()).collect()
    }

    #[test]
    fn u1_version_shapes() {
        for ok in ["0.2.0", "0.2.0-preview.2", "10.0.1-preview.12", "1.2.3"] {
            assert!(valid_version(ok), "{ok}");
        }
        for bad in [
            "",
            "v0.2.0",
            "0.2",
            "0.2.0.1",
            "0.2.0-preview",
            "0.2.0-preview.",
            "0.2.0-preview.0",
            "0.2.0-preview.02",
            "0.2.0-rc.1",
            "0.2.0-preview.2 ",
            "0.2.0/../x",
            "a.b.c",
        ] {
            assert!(!valid_version(bad), "{bad:?}");
        }
    }

    #[test]
    fn u2_names_and_urls() {
        assert_eq!(
            asset_name("0.2.0-preview.2"),
            "aib-cloud-host-0.2.0-preview.2-x86_64-unknown-linux-musl"
        );
        assert_eq!(tag_name("0.2.0-preview.2"), "cloud-host-v0.2.0-preview.2");
        assert_eq!(
            release_base_url("ez2app/ez2-aib", "0.2.0-preview.2"),
            "https://github.com/ez2app/ez2-aib/releases/download/cloud-host-v0.2.0-preview.2"
        );
        assert_eq!(
            expected_version_line("0.2.0-preview.2"),
            "aib-cloud-host 0.2.0-preview.2 linux-x86_64"
        );
        // the line this very build prints has the same shape (os-arch differs off the host)
        let mine = own_version_line();
        assert!(mine.starts_with(&format!("aib-cloud-host {} ", env!("CARGO_PKG_VERSION"))));
        assert_eq!(mine.split(' ').count(), 3);
    }

    #[test]
    fn u3_repo_shapes() {
        assert!(valid_repo("ez2app/ez2-aib"));
        assert!(valid_repo("a.b/c_d-e"));
        for bad in [
            "", "ez2app", "/x", "x/", "a/b/c", "a b/c", "a/b?x=1", "../b",
        ] {
            assert!(!valid_repo(bad), "{bad:?}");
        }
    }

    #[test]
    fn u4_sums_parsing() {
        let hex = "a".repeat(64);
        let sums = format!(
            "{hex}  aib-cloud-host-0.2.0-preview.2-x86_64-unknown-linux-musl\n{}  other\n",
            "b".repeat(64)
        );
        let asset = "aib-cloud-host-0.2.0-preview.2-x86_64-unknown-linux-musl";
        assert_eq!(expected_sum(&sums, asset).unwrap(), hex);
        // binary-mode marker, CRLF, upper-case hex all accepted
        let star = format!("{} *{asset}\r\n", "AB".repeat(32));
        assert_eq!(expected_sum(&star, asset).unwrap(), "ab".repeat(32));
        // missing, short hash, duplicate, and a name that only shares a prefix
        assert!(expected_sum(
            &sums,
            "aib-cloud-host-0.2.0-preview.3-x86_64-unknown-linux-musl"
        )
        .is_err());
        assert!(expected_sum(&format!("abc  {asset}\n"), asset).is_err());
        assert!(expected_sum(&format!("{hex}  {asset}\n{hex}  {asset}\n"), asset).is_err());
        assert!(expected_sum(&format!("{hex}  {asset}.sig\n"), asset).is_err());
        assert!(expected_sum("", asset).is_err());
    }

    #[test]
    fn u5_sha256sum_output() {
        let hex = "0123456789abcdef".repeat(4);
        assert_eq!(
            parse_sha256sum_output(&format!("{hex}  /usr/local/sbin/aib-cloud-host\n")).unwrap(),
            hex
        );
        assert_eq!(
            parse_sha256sum_output(&format!("{}  x\n", hex.to_uppercase())).unwrap(),
            hex
        );
        assert!(parse_sha256sum_output("").is_none());
        assert!(parse_sha256sum_output("sha256sum: x: No such file\n").is_none());
        assert!(parse_sha256sum_output(&format!("{}  x\n", &hex[..63])).is_none());
    }

    #[test]
    fn u6_args_defaults_and_overrides() {
        let p = parse_args(&s(&["0.2.0-preview.2"])).unwrap();
        assert_eq!(p.dest, PathBuf::from("/usr/local/sbin/aib-cloud-host"));
        assert_eq!(p.tag, "cloud-host-v0.2.0-preview.2");
        assert_eq!(
            p.base_url,
            "https://github.com/ez2app/ez2-aib/releases/download/cloud-host-v0.2.0-preview.2"
        );
        let p = parse_args(&s(&[
            "0.2.0",
            "--dest",
            "/tmp/x/aib-cloud-host",
            "--repo",
            "me/fork",
            "--base-url",
            "file:///tmp/dist/",
        ]))
        .unwrap();
        assert_eq!(p.dest, PathBuf::from("/tmp/x/aib-cloud-host"));
        assert_eq!(p.base_url, "file:///tmp/dist"); // trailing slash dropped
        let p = parse_args(&s(&["0.2.0", "--repo", "me/fork"])).unwrap();
        assert_eq!(
            p.base_url,
            "https://github.com/me/fork/releases/download/cloud-host-v0.2.0"
        );
    }

    #[test]
    fn u7_args_refused() {
        for bad in [
            vec![],
            vec!["v0.2.0"],
            vec!["0.2.0", "--dest"],
            vec!["0.2.0", "--dest", "relative/path"],
            vec!["0.2.0", "--repo", "nope"],
            vec!["0.2.0", "--base-url", "http://github.com/x"],
            vec!["0.2.0", "--base-url", "ftp://x"],
            vec!["0.2.0", "--base-url", "file:/tmp/x"],
            vec!["0.2.0", "--what"],
            vec!["0.2.0", "extra"],
        ] {
            assert!(parse_args(&s(&bad)).is_err(), "{bad:?}");
        }
    }
}
