// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host self-update <version>` — the host takes its own binary from a GitHub
//! release instead of someone scp-ing a CI artifact (大綱 r2 第 26 件, 11-09).
//!
//! ```text
//! aib-cloud-host self-update <version> [--dest /usr/local/sbin/aib-cloud-host]
//!                                      [--allow-downgrade] [--base-url file:///…]
//! ```
//!
//! What it does, in order, and it stops at the first thing that is not exactly right:
//!
//! 1. `<version>` must be `X.Y.Z` or `X.Y.Z-preview.N`; the release is tag `cloud-host-v<version>`
//!    and the file is `aib-cloud-host-<version>-x86_64-unknown-linux-musl` (the only target the
//!    `cloud-host` workflow builds; the host is a Proxmox x86_64 box with no toolchain).
//! 2. `<version>` older than this binary's own version is refused unless `--allow-downgrade`
//!    is given (rolling back is allowed, but never by accident; #612 c477a L1). Ordering:
//!    numeric `X.Y.Z`, and `X.Y.Z-preview.N` sorts before `X.Y.Z`.
//! 3. `curl` (argument list, `--proto =https --tlsv1.2`, 180 s) fetches the release's
//!    `SHA256SUMS` into a temp directory **next to** `--dest` (same filesystem, so the final step
//!    is one `rename`). The temp directory name carries the pid and the start time.
//! 4. If the file already installed at `--dest` has that release's SHA-256, nothing is touched
//!    (rc 0, says so).
//! 5. `curl` fetches the binary; its `sha256sum` must equal the line for that file in
//!    `SHA256SUMS`.
//! 6. The download is made executable and run with `--version`; it must print exactly
//!    `aib-cloud-host <version> linux-x86_64`.
//! 7. Swap without a window where `--dest` is missing (#612 c477a M2): the installed file (if
//!    any) is hard-linked to `<dest>.prev.new`, the download is renamed onto `--dest` in one
//!    `rename`, then the link becomes `<dest>.prev`. At every instant `--dest` is the old file or
//!    the new one; if the install rename fails, `--dest` and the old `.prev` are untouched. The
//!    temp directory is removed.
//!
//! Source (#612 c477a M1): the binary and `SHA256SUMS` come from the same place, so the checksum
//! only proves "download = what that place claims". Over the network the only place accepted is
//! this repository's own release, `https://github.com/ez2app/ez2-aib/releases/download/<tag>`;
//! there is no `--repo` and `--base-url` takes only `file:///` (the release workflow's verify job
//! points it at the files it just built: the same code path end to end, no network).
//! No shell strings anywhere; every child is `Command::new(prog).args([...])`.

use std::fs;
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
    pub allow_downgrade: bool,
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

/// `X.Y.Z[-preview.N]` as an ordered key: `(X, Y, Z, N)` with a release (`no -preview`) above
/// every preview of the same `X.Y.Z`. `None` if the shape is wrong.
pub fn version_key(v: &str) -> Option<(u64, u64, u64, u64)> {
    if !valid_version(v) {
        return None;
    }
    let (core, pre) = match v.split_once('-') {
        Some((c, p)) => (c, Some(p)),
        None => (v, None),
    };
    let mut n = core.split('.').map(|x| x.parse::<u64>().ok());
    let (a, b, c) = (n.next()??, n.next()??, n.next()??);
    let p = match pre {
        None => u64::MAX,
        Some(p) => p.strip_prefix("preview.")?.parse::<u64>().ok()?,
    };
    Some((a, b, c, p))
}

/// `Err` when `wanted` is older than `current` and no `--allow-downgrade`.
pub fn check_downgrade(wanted: &str, current: &str, allow: bool) -> Result<(), String> {
    let (Some(w), Some(c)) = (version_key(wanted), version_key(current)) else {
        return Err(format!(
            "cannot compare versions {wanted:?} and {current:?}"
        ));
    };
    if w < c && !allow {
        return Err(format!(
            "{wanted} is older than this aib-cloud-host ({current}); refusing to downgrade \
             (pass --allow-downgrade to roll back on purpose)"
        ));
    }
    Ok(())
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

/// What `aib-cloud-host build-info` prints, and what `aib-release host-build` finds in the file
/// (DEV 4.6 P1): the commit this binary was built from (40 hex, or `unknown`) and whether the
/// sources that went into it had uncommitted changes (`build.rs`). One literal, so the bytes
/// sit together in the file. `--version` stays as it was: `self-update` compares it exactly.
pub const BUILD_INFO: &str = concat!(
    "aib-build-info version=",
    env!("CARGO_PKG_VERSION"),
    " commit=",
    env!("AIB_CLOUD_HOST_COMMIT"),
    " dirty=",
    env!("AIB_CLOUD_HOST_DIRTY"),
    ";"
);

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
    let mut base_url: Option<String> = None;
    let mut allow_downgrade = false;
    while let Some(a) = it.next() {
        match a.as_str() {
            "--dest" => dest = PathBuf::from(it.next().ok_or("--dest needs a path")?),
            "--base-url" => base_url = Some(it.next().ok_or("--base-url needs a URL")?.clone()),
            "--allow-downgrade" => allow_downgrade = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !dest.is_absolute() {
        return Err(format!(
            "--dest {} must be an absolute path",
            dest.display()
        ));
    }
    let base_url = match base_url {
        None => release_base_url(DEFAULT_REPO, &version),
        Some(u) if u.starts_with("file:///") => u.trim_end_matches('/').to_owned(),
        Some(u) => {
            return Err(format!(
                "--base-url {u:?} must start with file:/// (over the network only this \
                 repository's own GitHub release is used)"
            ))
        }
    };
    Ok(Plan {
        asset: asset_name(&version),
        tag: tag_name(&version),
        version,
        base_url,
        dest,
        allow_downgrade,
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

    // 2. no accidental downgrade
    check_downgrade(
        &plan.version,
        env!("CARGO_PKG_VERSION"),
        plan.allow_downgrade,
    )?;

    // 3. fetch the sums first: a wrong version / missing release is found before the big file.
    //    pid + start time: a temp dir left by an earlier killed run cannot collide (c477a N2).
    let started = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tmp = dest_dir.join(format!(
        ".aib-cloud-host-update.{}.{started}",
        std::process::id()
    ));
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

    // 4. already there?
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

    // 5. checksum
    let got = sha256_of(&new_bin)?;
    if got != want {
        return Err(format!(
            "{}: SHA-256 {got} but SHA256SUMS says {want}; not installed",
            plan.asset
        ));
    }

    // 6. it runs and is the version it claims
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&new_bin, fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("chmod {}: {e}", new_bin.display()))?;
    }
    let line = version_line_of(&new_bin)?;
    let expect = expected_version_line(&plan.version);
    if line != expect {
        return Err(format!(
            "{} --version printed {line:?}, want {expect:?}; not installed",
            plan.asset
        ));
    }

    // 7. swap
    let old_line = if plan.dest.exists() {
        Some(version_line_of(&plan.dest).unwrap_or_else(|e| format!("(unreadable: {e})")))
    } else {
        None
    };
    let prev = prev_path(&plan.dest);
    swap_in(&new_bin, &plan.dest, &prev)?;
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

/// `<dest>.prev`
pub fn prev_path(dest: &Path) -> PathBuf {
    PathBuf::from(format!("{}.prev", dest.display()))
}

/// Put `new` at `dest` with no moment where `dest` is missing (#612 c477a M2).
///
/// 1. If `dest` exists, hard-link it to `<prev>.new` (a leftover `<prev>.new` is removed first).
/// 2. `rename(new, dest)` — one atomic step on the same filesystem; `dest` is the old file before
///    it and the new file after it.
/// 3. `rename(<prev>.new, prev)`.
///
/// If step 1 or 2 fails, `dest` and an existing `prev` are untouched (the half-made link is
/// removed). If only step 3 fails the new file is installed and the old one is still reachable
/// at `<prev>.new`; that is reported, not undone.
pub fn swap_in(new: &Path, dest: &Path, prev: &Path) -> Result<(), String> {
    let link = PathBuf::from(format!("{}.new", prev.display()));
    let had_old = dest.exists();
    if had_old {
        match fs::remove_file(&link) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("remove leftover {}: {e}", link.display())),
        }
        fs::hard_link(dest, &link).map_err(|e| {
            format!(
                "keep {} as {}: {e}; not installed",
                dest.display(),
                link.display()
            )
        })?;
    }
    if let Err(e) = fs::rename(new, dest) {
        if had_old {
            let _ = fs::remove_file(&link);
        }
        return Err(format!(
            "install {}: {e}; the installed file was not changed",
            dest.display()
        ));
    }
    if had_old {
        fs::rename(&link, prev).map_err(|e| {
            format!(
                "installed {}, but keeping the previous file as {} failed: {e} (it is at {})",
                dest.display(),
                prev.display(),
                link.display()
            )
        })?;
    }
    Ok(())
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
        assert!(!p.allow_downgrade);
        let p = parse_args(&s(&[
            "0.2.0",
            "--dest",
            "/tmp/x/aib-cloud-host",
            "--base-url",
            "file:///tmp/dist/",
            "--allow-downgrade",
        ]))
        .unwrap();
        assert_eq!(p.dest, PathBuf::from("/tmp/x/aib-cloud-host"));
        assert_eq!(p.base_url, "file:///tmp/dist"); // trailing slash dropped
        assert!(p.allow_downgrade);
    }

    #[test]
    fn u7_args_refused() {
        for bad in [
            vec![],
            vec!["v0.2.0"],
            vec!["0.2.0", "--dest"],
            vec!["0.2.0", "--dest", "relative/path"],
            // #612 c477a M1: no other source over the network — not https, not another repo
            vec!["0.2.0", "--base-url", "https://example.com/x"],
            vec![
                "0.2.0",
                "--base-url",
                "https://github.com/ez2app/ez2-aib/releases/download/cloud-host-v0.2.0",
            ],
            vec!["0.2.0", "--repo", "me/fork"],
            vec!["0.2.0", "--base-url", "http://github.com/x"],
            vec!["0.2.0", "--base-url", "ftp://x"],
            vec!["0.2.0", "--base-url", "file:/tmp/x"],
            vec!["0.2.0", "--what"],
            vec!["0.2.0", "extra"],
        ] {
            assert!(parse_args(&s(&bad)).is_err(), "{bad:?}");
        }
    }

    /// DEV 4.6 P1: the build says which commit it came from, in the one shape
    /// `aib-release host-build` reads (its own tests hold the reader to the same shape).
    #[test]
    fn build_info_names_the_commit_in_one_fixed_shape() {
        let rest = BUILD_INFO
            .strip_prefix("aib-build-info ")
            .and_then(|r| r.strip_suffix(';'))
            .unwrap();
        let parts: Vec<&str> = rest.split(' ').collect();
        assert_eq!(parts.len(), 3, "{BUILD_INFO}");
        assert_eq!(parts[0], format!("version={}", env!("CARGO_PKG_VERSION")));
        let commit = parts[1].strip_prefix("commit=").unwrap();
        assert!(
            commit == "unknown"
                || (commit.len() == 40
                    && commit
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))),
            "{commit}"
        );
        assert!(matches!(parts[2], "dirty=0" | "dirty=1"), "{}", parts[2]);
        assert_eq!(BUILD_INFO.matches(';').count(), 1);
    }

    #[test]
    fn u8_downgrade_refused_unless_asked() {
        // ordering: numeric, and a preview sorts before its release
        assert!(version_key("0.2.0-preview.4") < version_key("0.2.0"));
        assert!(version_key("0.2.0-preview.4") < version_key("0.2.0-preview.10"));
        assert!(version_key("0.9.9") < version_key("0.10.0"));
        assert!(version_key("1.0.0") > version_key("0.99.99"));
        assert_eq!(version_key("v0.2.0"), None);
        // #612 c477a L1
        assert!(check_downgrade("0.1.9", "0.2.0-preview.4", false).is_err());
        assert!(check_downgrade("0.2.0-preview.3", "0.2.0-preview.4", false).is_err());
        assert!(check_downgrade("0.2.0-preview.3", "0.2.0-preview.4", true).is_ok());
        assert!(check_downgrade("0.2.0-preview.4", "0.2.0-preview.4", false).is_ok());
        assert!(check_downgrade("0.2.0", "0.2.0-preview.4", false).is_ok());
        assert!(check_downgrade("0.2.0", "garbage", false).is_err());
    }

    #[test]
    fn u9_swap_keeps_prev_and_never_leaves_dest_missing() {
        let dir = std::env::temp_dir().join(format!(
            "aib-cloud-host-u9-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&dir).unwrap();
        let (dest, prev, new) = (dir.join("bin"), dir.join("bin.prev"), dir.join("new"));
        // fresh install: no .prev
        fs::write(&new, b"v1").unwrap();
        swap_in(&new, &dest, &prev).unwrap();
        assert_eq!(fs::read(&dest).unwrap(), b"v1");
        assert!(!prev.exists());
        // over an existing different file: new in place, old kept as .prev, stale .prev replaced
        fs::write(&prev, b"stale").unwrap();
        fs::write(&new, b"v2").unwrap();
        swap_in(&new, &dest, &prev).unwrap();
        assert_eq!(fs::read(&dest).unwrap(), b"v2");
        assert_eq!(fs::read(&prev).unwrap(), b"v1");
        assert!(!new.exists());
        // rename fails (source missing): dest is still the old file, .prev still the one before,
        // no half-made link left
        swap_in(&dir.join("missing"), &dest, &prev).unwrap_err();
        assert_eq!(fs::read(&dest).unwrap(), b"v2");
        assert_eq!(fs::read(&prev).unwrap(), b"v1");
        assert!(!dir.join("bin.prev.new").exists());
        fs::remove_dir_all(&dir).unwrap();
    }
}
