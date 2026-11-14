// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host restore expect <file.wpress>` — what a customer's All-in-One WP Migration
//! backup says the restored site must hold, read **before** the import (the counts taken
//! after it would be compared with themselves). Read-only: one file is read, nothing is
//! written, no network, no site is touched. Exit status: 0 the expectation is reliable, 3 it
//! is not (the reasons say why; the caller must stop, not guess), 2 usage.
//!
//! `aib-cloud-host restore verify <code> <file.wpress> [--users-extra N] [--records dir]` —
//! after the import, the same counts taken from the restored site's database by its own
//! WordPress (`wp eval` of one fixed piece of PHP with `--skip-plugins --skip-themes`, run as
//! the site's user inside its container; the site code is checked and taken from the site's
//! record, nothing typed reaches the command or the SQL), compared by
//! `aib_cloud::wpress::compare`. `--users-extra` is how many administrators the restore
//! added on purpose. Plugins and themes in the backup and on the site are listed side by side
//! and never judged. Read-only. Exit status: 0 match, 1 differ, 3 cannot tell, 2 usage.
//!
//! This is **not** an independent check: `--skip-plugins` does not stop must-use plugins,
//! and `wp-config.php` is the customer's too, so the answer is only as trustworthy as the
//! customer's container. What it guards against is a restore that silently lost rows; a
//! site that prints anything unexpected (a warning, an extra line) is "cannot tell", and
//! each answer is read up to [`WP_OUT_MAX`] bytes — more than that is "cannot tell" too.
//! `wp plugin list` also names must-use plugins and drop-ins (`object-cache`, our own
//! loader), which the backup's `plugins/` folder does not hold: they show as
//! `unexpected_on_site`, listed, not judged.
//!
//! Uploads: the backup's files under `uploads/` are counted from its headers (nothing is
//! extracted); the site's are counted inside its container as the site's user by one fixed
//! `find` + `awk` ([`UPLOADS_SH`]) that prints three short lines, never a file name. A
//! different number of files is a difference; bytes are listed only (cache plugins write
//! extra thumbnail sizes); the folders plugins fill by themselves after a restore
//! (`wpress::GENERATED_UPLOADS`) are counted apart and listed only. No answer, an answer
//! over the cap, or one out of form is "cannot tell". Fewer files than the backup is a
//! difference; more is listed (`extra_on_site`), not judged — the premise: the All-in-One
//! WP Migration import adds the backup's files and deletes none, so files already in the
//! container before the restore stay (ebb36 L2, the same rule as plugins). A backup exported
//! with "do not export media library" holds no uploads at all: it expects 0, so any site
//! matches and its files show as extra — such a backup tells nothing about uploads (the
//! export's options are not read from `package.json`; ebb36 N2). `verdict` is the whole answer (a
//! difference anywhere wins, then anything untold); `posts_verdict` is the posts' and
//! users' own.
//!
//! Prints one JSON document. The rules live in `aib_cloud::wpress`; this is the reading half.

use std::collections::BTreeSet;
use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};
use std::process::Command;
use std::time::Duration;

use aib_cloud::wpress::{self, Header, Part, Policy, SqlScan, Verdict, HEADER_LEN};
use serde_json::{json, Map, Value};

/// Largest root `package.json` read.
const PACKAGE_CAP: u64 = 1024 * 1024;

/// What one pass over the archive found.
#[derive(Debug, Default)]
pub struct Read1 {
    /// Files in the archive.
    pub entries: u64,
    /// Size of the root `database.sql`.
    pub database_bytes: Option<u64>,
    /// The root `package.json`, read.
    pub package: Option<wpress::Package>,
    /// The counts, or why they cannot be told.
    pub expect: Option<Result<wpress::Expect, String>>,
    /// Why the archive itself cannot be trusted.
    pub reasons: Vec<String>,
    /// Plugins in the archive, named as `wp plugin list` names them.
    pub plugins: BTreeSet<String>,
    /// Themes in the archive.
    pub themes: BTreeSet<String>,
    /// Files under `uploads/`, counted from the headers (nothing is extracted).
    pub uploads: wpress::Uploads,
}

/// Reads an archive from any seekable source.
pub fn read_archive<R: Read + Seek>(mut r: R) -> Read1 {
    let mut out = Read1::default();
    let mut block = vec![0u8; HEADER_LEN];
    let mut ended = false;
    loop {
        let got = match fill(&mut r, &mut block) {
            Ok(n) => n,
            Err(e) => {
                out.reasons
                    .push(format!("the archive could not be read: {e}"));
                return out;
            }
        };
        if got == 0 {
            break;
        }
        let entry = match wpress::parse_header(&block[..got]) {
            Ok(Header::End) => {
                ended = true;
                break;
            }
            Ok(Header::Entry(e)) => e,
            Err(e) => {
                out.reasons.push(e);
                return out;
            }
        };
        out.entries += 1;
        out.uploads.add(&entry);
        match wpress::part_of(&entry) {
            Some((Part::Plugin, n)) => {
                out.plugins.insert(n);
            }
            Some((Part::Theme, n)) => {
                out.themes.insert(n);
            }
            None => {}
        }
        let root = entry.at_root();
        let res = if root && entry.name == "database.sql" {
            if out.database_bytes.is_some() {
                out.reasons
                    .push("the archive holds two root database.sql files".to_owned());
                return out;
            }
            out.database_bytes = Some(entry.size);
            let mut scan = SqlScan::new();
            let r2 = stream(&mut r, entry.size, |c| scan.feed(c));
            out.expect = Some(scan.finish());
            r2
        } else if root && entry.name == "package.json" {
            if entry.size > PACKAGE_CAP {
                out.reasons
                    .push("the root package.json is larger than 1 MiB".to_owned());
                return out;
            }
            let mut text = Vec::new();
            let r2 = stream(&mut r, entry.size, |c| text.extend_from_slice(c));
            out.package = std::str::from_utf8(&text).ok().and_then(wpress::package);
            r2
        } else if root && entry.name == "multisite.json" {
            out.reasons
                .push("the backup is of a multisite network: not restored this way".to_owned());
            return out;
        } else {
            skip(&mut r, entry.size)
        };
        if let Err(e) = res {
            out.reasons.push(e);
            return out;
        }
    }
    if !ended {
        out.reasons
            .push("the archive ends without its end block (it was cut)".to_owned());
    }
    if out.entries == 0 {
        out.reasons.push("the archive holds no files".to_owned());
    }
    if out.database_bytes.is_none() {
        out.reasons
            .push("the archive has no database.sql at its root".to_owned());
    }
    out
}

/// Reads up to `buf.len()` bytes; fewer only at the end of the source.
fn fill<R: Read>(r: &mut R, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut n = 0;
    while n < buf.len() {
        match r.read(&mut buf[n..]) {
            Ok(0) => break,
            Ok(k) => n += k,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(n)
}

/// Hands `size` bytes of content to `f` in chunks; the content must all be there.
fn stream<R: Read>(r: &mut R, size: u64, mut f: impl FnMut(&[u8])) -> Result<(), String> {
    let mut left = size;
    let mut buf = vec![0u8; 64 * 1024];
    while left > 0 {
        let want = usize::try_from(left.min(buf.len() as u64)).unwrap_or(buf.len());
        let got =
            fill(r, &mut buf[..want]).map_err(|e| format!("the archive could not be read: {e}"))?;
        if got < want {
            return Err(
                "a file in the archive is shorter than its header says (it was cut)".to_owned(),
            );
        }
        f(&buf[..got]);
        left -= got as u64;
    }
    Ok(())
}

/// Moves past `size` bytes of content; the content must all be there.
fn skip<R: Read + Seek>(r: &mut R, size: u64) -> Result<(), String> {
    let here = r
        .stream_position()
        .map_err(|e| format!("the archive could not be read: {e}"))?;
    let end = r
        .seek(SeekFrom::End(0))
        .map_err(|e| format!("the archive could not be read: {e}"))?;
    let to = here
        .checked_add(size)
        .ok_or_else(|| "a file size in the archive is out of range".to_owned())?;
    if to > end {
        return Err(
            "a file in the archive is shorter than its header says (it was cut)".to_owned(),
        );
    }
    r.seek(SeekFrom::Start(to))
        .map_err(|e| format!("the archive could not be read: {e}"))?;
    Ok(())
}

/// Whether the archive's expectation can be used (what [`document`] calls reliable).
pub fn reliable(read: &Read1) -> bool {
    read.reasons.is_empty() && matches!(read.expect, Some(Ok(_)))
}

/// The document `restore expect` prints, and whether it is reliable.
pub fn document(read: &Read1) -> (Value, bool) {
    let mut reasons = read.reasons.clone();
    let mut doc = Map::new();
    doc.insert("entries".into(), json!(read.entries));
    doc.insert("database_sql_bytes".into(), json!(read.database_bytes));
    let p = read.package.clone().unwrap_or_default();
    doc.insert("site_url".into(), json!(p.site_url));
    doc.insert("home_url".into(), json!(p.home_url));
    doc.insert("wp_version".into(), json!(p.wp_version));
    match &read.expect {
        Some(Ok(e)) => {
            let mut by: Map<String, Value> = Map::new();
            let mut totals: Map<String, Value> = Map::new();
            for ((t, s), n) in &e.posts {
                let slot = by.entry(t.clone()).or_insert_with(|| json!({}));
                if let Some(o) = slot.as_object_mut() {
                    o.insert(s.clone(), json!(n));
                }
                let tot = totals.entry(t.clone()).or_insert_with(|| json!(0));
                *tot = json!(tot.as_u64().unwrap_or(0) + n);
            }
            doc.insert("table_prefix".into(), json!(e.prefix));
            doc.insert("posts_by_type_status".into(), Value::Object(by));
            doc.insert("post_type_totals".into(), Value::Object(totals));
            doc.insert("users".into(), json!(e.users));
        }
        Some(Err(why)) => reasons.push(why.clone()),
        None => {}
    }
    doc.insert("uploads".into(), uploads_doc(&read.uploads));
    doc.insert(
        "not_a_criterion".into(),
        json!("active_plugins, stylesheet and template are emptied by the export"),
    );
    let reliable = reasons.is_empty() && reliable(read);
    doc.insert("reliable".into(), json!(reliable));
    doc.insert("reasons".into(), json!(reasons));
    (Value::Object(doc), reliable)
}

/// The PHP the restored site's own WordPress runs to count itself. Fixed: nothing from
/// outside is put into it. The tables are named by WordPress (`$wpdb`), so the prefix is
/// whatever the site's configuration says.
pub const COUNT_PHP: &str = r#"global $wpdb;
$r = $wpdb->get_results("SELECT post_type, post_status, COUNT(*) FROM {$wpdb->posts} GROUP BY post_type, post_status", ARRAY_N);
if ($wpdb->last_error !== "" || !is_array($r)) { echo "ERR\n"; return; }
foreach ($r as $x) { echo "P\t" . $x[0] . "\t" . $x[1] . "\t" . $x[2] . "\n"; }
$u = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->users}");
if ($u === null || $wpdb->last_error !== "") { echo "ERR\n"; return; }
echo "U\t" . $u . "\n";
echo "END\n";"#;

/// What was read from the restored site; `None` is "the command did not answer".
#[derive(Debug, Default)]
pub struct SiteOut {
    /// Output of [`COUNT_PHP`].
    pub counts: Option<String>,
    /// `wp plugin list --field=name`.
    pub plugins: Option<String>,
    /// `wp theme list --field=name`.
    pub themes: Option<String>,
    /// Output of [`UPLOADS_SH`].
    pub uploads: Option<String>,
}

fn uploads_doc(u: &wpress::Uploads) -> Value {
    json!({
        "files": u.kept.files,
        "bytes": u.kept.bytes,
        "generated_files": u.generated.files,
        "generated_bytes": u.generated.bytes,
    })
}

/// Counts the restored site's uploads inside its container, as the site's user: one pass
/// of `find`, each file printed as its class and size only (never its name, so a name with
/// a new line in it cannot shift a count), summed by `awk`; three lines come back whatever
/// the number of files. `$1` is the uploads folder, the rest are `find` tests naming
/// [`wpress::GENERATED_UPLOADS`]. A folder `find` cannot read is "cannot tell" (exit 3), an
/// absent folder is no uploads.
pub const UPLOADS_SH: &str = r#"set -f
d=$1; shift
[ -d "$d" ] || { printf 'F\t0\t0\nG\t0\t0\nEND\n'; exit 0; }
cd -- "$d" || exit 3
{ find . -type f \( \( "$@" \) -printf 'G %s\n' -o -printf 'F %s\n' \) || echo X; } |
awk '$1 == "X" { bad = 1; next } { n[$1]++; b[$1] += $2 }
END { if (bad) exit 3; printf "F\t%d\t%.0f\nG\t%d\t%.0f\nEND\n", n["F"], b["F"], n["G"], b["G"] }'"#;

/// One command run inside the customer's container: the whole argv, `None` when it does
/// not answer cleanly. Injected so tests never run `pct`.
pub type Runner<'a> = dyn FnMut(&[String]) -> Option<String> + 'a;

fn in_ct(ct: u32, code: &str, tail: &[&str]) -> Vec<String> {
    let mut v: Vec<String> = ["pct", "exec", &ct.to_string(), "--", "sudo", "-u", code]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
    v.extend(tail.iter().map(|s| (*s).to_owned()));
    v
}

fn wp_argv(ct: u32, code: &str, args: &[&str]) -> Vec<String> {
    let path = format!("--path=/var/www/{code}");
    let mut tail = vec!["wp", path.as_str(), "--skip-plugins", "--skip-themes"];
    tail.extend_from_slice(args);
    in_ct(ct, code, &tail)
}

/// The argv that counts the site's uploads (`code` is checked and taken from the record).
pub fn uploads_argv(ct: u32, code: &str) -> Vec<String> {
    let dir = format!("/var/www/{code}/wp-content/uploads");
    let globs: Vec<String> = wpress::GENERATED_UPLOADS
        .iter()
        .map(|g| format!("./{g}/*"))
        .collect();
    let mut tail = vec!["sh", "-c", UPLOADS_SH, "sh", dir.as_str()];
    for (i, g) in globs.iter().enumerate() {
        if i > 0 {
            tail.push("-o");
        }
        tail.push("-path");
        tail.push(g.as_str());
    }
    in_ct(ct, code, &tail)
}

/// Asks the restored site everything `restore verify` compares, through `run`.
pub fn ask_site(run: &mut Runner<'_>, ct: u32, code: &str) -> SiteOut {
    SiteOut {
        counts: run(&wp_argv(ct, code, &["eval", COUNT_PHP])),
        plugins: run(&wp_argv(ct, code, &["plugin", "list", "--field=name"])),
        themes: run(&wp_argv(ct, code, &["theme", "list", "--field=name"])),
        uploads: run(&uploads_argv(ct, code)),
    }
}

/// The uploads section of `restore verify`, and its verdict: `Some(true)` differ,
/// `Some(false)` match, `None` cannot tell.
fn uploads_section(expect: &wpress::Uploads, site: Option<&str>) -> (Value, Option<bool>) {
    let mut doc = Map::new();
    doc.insert("expected".into(), uploads_doc(expect));
    doc.insert(
        "judged".into(),
        json!("fewer files outside the generated folders than the backup is a difference; more are listed only (the import deletes nothing already on the site); bytes are listed only"),
    );
    doc.insert("generated_folders".into(), json!(wpress::GENERATED_UPLOADS));
    doc.insert(
        "generated_why".into(),
        json!("plugins write these by themselves once the restored site serves pages (CSS, caches, logs): listed, not judged"),
    );
    let differ = match site.map(wpress::uploads_from_site) {
        None => {
            doc.insert("verdict".into(), json!("unknown"));
            doc.insert(
                "reason".into(),
                json!("the site's uploads could not be counted (no answer, over the cap, or too slow)"),
            );
            None
        }
        Some(Err(e)) => {
            doc.insert("verdict".into(), json!("unknown"));
            doc.insert("reason".into(), json!(e));
            None
        }
        Some(Ok(actual)) => {
            doc.insert("actual".into(), uploads_doc(&actual));
            doc.insert(
                "missing_from_site".into(),
                json!(expect.kept.files.saturating_sub(actual.kept.files)),
            );
            doc.insert(
                "extra_on_site".into(),
                json!(actual.kept.files.saturating_sub(expect.kept.files)),
            );
            let same = wpress::uploads_match(expect, &actual);
            doc.insert(
                "verdict".into(),
                json!(if same { "match" } else { "differ" }),
            );
            Some(!same)
        }
    };
    (Value::Object(doc), differ)
}

fn diffs(list: &[wpress::Diff]) -> Value {
    json!(list
        .iter()
        .map(|d| json!({ "kind": d.kind, "status": d.status, "expected": d.expected, "actual": d.actual }))
        .collect::<Vec<_>>())
}

fn listed(backup: &BTreeSet<String>, site: Option<&str>) -> Value {
    let Some(out) = site else {
        return json!({ "unknown": "the site's list could not be read" });
    };
    match wpress::names(out) {
        Ok(set) => {
            let d = wpress::set_diff(backup, &set);
            json!({
                "in_backup": backup.len(),
                "on_site": set.len(),
                "missing_from_site": d.missing_from_site,
                "unexpected_on_site": d.unexpected_on_site,
                "judged": false,
            })
        }
        Err(e) => json!({ "unknown": e }),
    }
}

/// The document `restore verify` prints, and its exit status.
pub fn verify_doc(code: &str, read: &Read1, site: &SiteOut, users_extra: u64) -> (Value, u8) {
    let (expect_doc, reliable) = document(read);
    let mut doc = Map::new();
    doc.insert("site".into(), json!(code));
    doc.insert("users_extra_allowed".into(), json!(users_extra));
    doc.insert("expect".into(), expect_doc);
    let verdict = match (&read.expect, reliable) {
        (Some(Ok(expect)), true) => match site.counts.as_deref() {
            None => Verdict::Unknown("the site's counts could not be read".to_owned()),
            Some(out) => match wpress::actual_from_wp(out) {
                Ok(actual) => {
                    let mut posts: Map<String, Value> = Map::new();
                    for ((t, st), n) in &actual.posts {
                        let slot = posts.entry(t.clone()).or_insert_with(|| json!({}));
                        if let Some(o) = slot.as_object_mut() {
                            o.insert(st.clone(), json!(n));
                        }
                    }
                    doc.insert(
                        "actual".into(),
                        json!({ "posts_by_type_status": posts, "users": actual.users }),
                    );
                    wpress::compare(expect, &actual, &Policy::standard(users_extra))
                }
                Err(e) => Verdict::Unknown(e),
            },
        },
        _ => Verdict::Unknown("the backup's expectation is not reliable".to_owned()),
    };
    let rc = match &verdict {
        Verdict::Match { advisory } => {
            doc.insert("verdict".into(), json!("match"));
            doc.insert("advisory".into(), diffs(advisory));
            0
        }
        Verdict::Differ { strict, advisory } => {
            doc.insert("verdict".into(), json!("differ"));
            doc.insert("strict".into(), diffs(strict));
            doc.insert("advisory".into(), diffs(advisory));
            1
        }
        Verdict::Unknown(why) => {
            doc.insert("verdict".into(), json!("unknown"));
            doc.insert("reason".into(), json!(why));
            3
        }
    };
    doc.insert(
        "plugins".into(),
        listed(&read.plugins, site.plugins.as_deref()),
    );
    doc.insert(
        "themes".into(),
        listed(&read.themes, site.themes.as_deref()),
    );
    // the backup is not trusted: its uploads are not compared either
    let uploads = if reliable {
        let (up, differ) = uploads_section(&read.uploads, site.uploads.as_deref());
        doc.insert("uploads".into(), up);
        differ
    } else {
        doc.insert(
            "uploads".into(),
            json!({ "verdict": "unknown", "reason": "the backup's expectation is not reliable" }),
        );
        None
    };
    // a difference found anywhere is a difference; otherwise anything untold is untold
    let rc = match (rc, uploads) {
        (1, _) | (_, Some(true)) => 1,
        (0, Some(false)) => 0,
        _ => 3,
    };
    let posts_verdict = doc.get("verdict").cloned().unwrap_or(Value::Null);
    doc.insert("posts_verdict".into(), posts_verdict);
    doc.insert(
        "verdict".into(),
        json!(match rc {
            0 => "match",
            1 => "differ",
            _ => "unknown",
        }),
    );
    (Value::Object(doc), rc)
}

fn open_archive(path: &str) -> Read1 {
    match File::open(path) {
        Ok(f) => read_archive(BufReader::new(f)),
        Err(e) => Read1 {
            reasons: vec![format!("the backup could not be opened: {e}")],
            ..Read1::default()
        },
    }
}

/// How much of each answer from inside the customer's container is read (33e28 #631 seg. 5
/// M1: a program there must not be able to fill this host's memory). Standard error is not
/// read at all.
pub const WP_OUT_MAX: usize = 64 * 1024;

/// Runs a command whose output is not trusted to end; `None` when it fails, times out, or
/// has more to say than [`WP_OUT_MAX`].
fn bounded(cmd: &mut Command) -> Option<String> {
    match crate::probe::run_capture_bounded(cmd, Duration::from_secs(120), WP_OUT_MAX) {
        Ok((out, false)) => Some(out),
        Ok((_, true)) | Err(_) => None,
    }
}

/// The real runner: the argv as one command, read through [`bounded`].
fn run_real(argv: &[String]) -> Option<String> {
    let (prog, rest) = argv.split_first()?;
    let mut cmd = Command::new(prog);
    cmd.args(rest);
    bounded(&mut cmd)
}

fn cmd_verify(args: &[String]) -> Result<u8, String> {
    const USAGE: &str =
        "restore verify needs <site code> <file.wpress> [--users-extra N] [--records dir]";
    let (Some(code), Some(path)) = (args.first(), args.get(1)) else {
        return Err(USAGE.to_owned());
    };
    if code.starts_with("--") || path.starts_with("--") {
        return Err(USAGE.to_owned());
    }
    let mut users_extra = 0u64;
    let mut records = "/var/lib/ez2-cloud/sites".to_owned();
    let mut it = args[2..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--users-extra" => {
                users_extra = it
                    .next()
                    .and_then(|n| n.parse().ok())
                    .ok_or("--users-extra needs a number")?;
            }
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    // checks the code's form and reads the container number from the site's own record
    let spec = crate::quarantinecmd::load_spec(code, &records)?;
    let read = open_archive(path);
    let site = if reliable(&read) {
        ask_site(&mut run_real, spec.ct, &spec.code)
    } else {
        // the backup cannot be trusted: the site is not asked anything
        SiteOut::default()
    };
    let (doc, rc) = verify_doc(&spec.code, &read, &site, users_extra);
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(rc)
}

/// `restore expect <file>` | `restore verify <code> <file> …`.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    match args.first().map(String::as_str) {
        Some("verify") => cmd_verify(&args[1..]),
        Some("expect") => {
            let [_, path] = args else {
                return Err("restore expect needs <file.wpress>".to_owned());
            };
            let (doc, reliable) = document(&open_archive(path));
            println!(
                "{}",
                serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
            );
            Ok(if reliable { 0 } else { 3 })
        }
        _ => Err("restore needs expect <file.wpress> | verify <site code> <file.wpress> [--users-extra N]".to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
pub(crate) mod tests {
    use super::*;
    use std::io::Cursor;

    fn header(name: &str, size: usize, folder: &str) -> Vec<u8> {
        let mut h = vec![0u8; HEADER_LEN];
        h[..name.len()].copy_from_slice(name.as_bytes());
        let s = size.to_string();
        h[255..255 + s.len()].copy_from_slice(s.as_bytes());
        h[269..279].copy_from_slice(b"1793000000");
        h[281..281 + folder.len()].copy_from_slice(folder.as_bytes());
        h
    }

    const SQL: &str = "CREATE TABLE `SERVMASK_PREFIX_posts` (\n  `ID` bigint,\n  `post_date` datetime,\n  `post_status` varchar(20),\n  `post_parent` bigint,\n  `post_type` varchar(20)\n);\nINSERT INTO `SERVMASK_PREFIX_posts` VALUES (1,'2026-01-01','publish',0,'post'),(2,'2026-01-01','publish',0,'page');\nCREATE TABLE `SERVMASK_PREFIX_users` (\n  `ID` bigint\n);\nINSERT INTO `SERVMASK_PREFIX_users` VALUES (1);\n";
    const PKG: &str = r#"{"SiteURL":"https://example.test","HomeURL":"https://example.test","WordPress":{"Version":"6.8.1"}}"#;

    fn archive(files: &[(&str, &str, &[u8])], end: bool) -> Vec<u8> {
        let mut out = Vec::new();
        for (name, folder, body) in files {
            out.extend(header(name, body.len(), folder));
            out.extend_from_slice(body);
        }
        if end {
            out.extend(vec![0u8; HEADER_LEN]);
        }
        out
    }

    pub(crate) fn good() -> Vec<u8> {
        archive(
            &[
                ("package.json", "", PKG.as_bytes()),
                ("database.sql", "plugins/x", b"junk that is not counted"),
                ("a.php", "plugins/x", b"<?php"),
                ("database.sql", "", SQL.as_bytes()),
            ],
            true,
        )
    }

    #[test]
    fn a_good_archive_gives_a_reliable_expectation() {
        let r = read_archive(Cursor::new(good()));
        let (doc, ok) = document(&r);
        assert!(ok, "{doc}");
        assert_eq!(doc["entries"], 4);
        assert_eq!(doc["users"], 1);
        assert_eq!(doc["posts_by_type_status"]["post"]["publish"], 1);
        assert_eq!(doc["post_type_totals"]["page"], 1);
        assert_eq!(doc["table_prefix"], "SERVMASK_PREFIX_");
        assert_eq!(doc["site_url"], "https://example.test");
        assert_eq!(doc["wp_version"], "6.8.1");
    }

    #[test]
    fn only_the_root_database_counts() {
        let a = archive(&[("database.sql", "plugins/x", SQL.as_bytes())], true);
        let (doc, ok) = document(&read_archive(Cursor::new(a)));
        assert!(!ok);
        assert!(doc["reasons"]
            .to_string()
            .contains("no database.sql at its root"));
        let a = archive(
            &[
                ("database.sql", "", SQL.as_bytes()),
                ("database.sql", ".", SQL.as_bytes()),
            ],
            true,
        );
        assert!(!document(&read_archive(Cursor::new(a))).1);
    }

    #[test]
    fn an_archive_without_its_end_block_is_not_reliable() {
        let mut a = good();
        a.truncate(a.len() - HEADER_LEN);
        let (doc, ok) = document(&read_archive(Cursor::new(a)));
        assert!(!ok);
        assert!(doc["reasons"].to_string().contains("end block"));
    }

    #[test]
    fn a_cut_archive_is_not_reliable() {
        let a = good();
        for cut in [10, HEADER_LEN + 3, a.len() - HEADER_LEN - 5] {
            let (_, ok) = document(&read_archive(Cursor::new(a[..cut].to_vec())));
            assert!(!ok, "cut at {cut}");
        }
        // a skipped file shorter than its header says
        let mut b = archive(&[("a.php", "plugins/x", b"<?php")], false);
        b[255..256].copy_from_slice(b"9");
        assert!(!document(&read_archive(Cursor::new(b))).1);
    }

    #[test]
    fn multisite_and_bad_paths_are_not_reliable() {
        let a = archive(
            &[
                ("multisite.json", "", b"{}"),
                ("database.sql", "", SQL.as_bytes()),
            ],
            true,
        );
        assert!(!document(&read_archive(Cursor::new(a))).1);
        let a = archive(
            &[("x", "../up", b"1"), ("database.sql", "", SQL.as_bytes())],
            true,
        );
        assert!(!document(&read_archive(Cursor::new(a))).1);
    }

    #[test]
    fn the_command_reads_one_file_and_says_reliable_or_not() {
        let dir = std::env::temp_dir().join(format!("aib-wpx-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("b.wpress");
        std::fs::write(&p, good()).unwrap();
        let ps = p.to_string_lossy().to_string();
        assert_eq!(cmd(&["expect".into(), ps.clone()]).unwrap(), 0);
        let mut cut = good();
        cut.truncate(cut.len() - HEADER_LEN);
        std::fs::write(&p, cut).unwrap();
        assert_eq!(cmd(&["expect".into(), ps]).unwrap(), 3);
        assert_eq!(
            cmd(&[
                "expect".into(),
                dir.join("none").to_string_lossy().to_string()
            ])
            .unwrap(),
            3
        );
        assert!(cmd(&["check".into(), "x".into()]).is_err());
        assert!(cmd(&["expect".into()]).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn site(counts: &str) -> SiteOut {
        SiteOut {
            counts: Some(counts.to_owned()),
            plugins: Some("x\nakismet\n".to_owned()),
            themes: Some("twentytwentyfive\n".to_owned()),
            uploads: Some(NO_UPLOADS.to_owned()),
        }
    }

    pub(crate) const NO_UPLOADS: &str = "F\t0\t0\nG\t0\t0\nEND\n";

    /// [`good`] with three uploaded files, one of them in a generated folder.
    fn with_uploads() -> Vec<u8> {
        archive(
            &[
                ("package.json", "", PKG.as_bytes()),
                ("a.jpg", "uploads/2026/01", b"0123456789"),
                ("a-150x150.jpg", "uploads/2026/01", b"01234"),
                ("post-1.css", "uploads/elementor/css", b"css"),
                ("database.sql", "", SQL.as_bytes()),
            ],
            true,
        )
    }

    fn with(counts: &str, uploads: Option<&str>) -> SiteOut {
        SiteOut {
            uploads: uploads.map(str::to_owned),
            ..site(counts)
        }
    }

    #[test]
    fn verify_judges_the_number_of_uploads_not_their_bytes() {
        let r = read_archive(Cursor::new(with_uploads()));
        let (e, ok) = document(&r);
        assert!(ok, "{e}");
        assert_eq!(e["uploads"]["files"], 2);
        assert_eq!(e["uploads"]["bytes"], 15);
        assert_eq!(e["uploads"]["generated_files"], 1);
        // same number, other bytes, other generated files: a match, the numbers listed
        let (doc, rc) = verify_doc(
            "s1",
            &r,
            &with(SAME, Some("F\t2\t99999\nG\t30\t7\nEND\n")),
            0,
        );
        assert_eq!(rc, 0, "{doc}");
        assert_eq!(doc["verdict"], "match");
        assert_eq!(doc["uploads"]["verdict"], "match");
        assert_eq!(doc["uploads"]["actual"]["bytes"], 99999);
        assert_eq!(doc["uploads"]["expected"]["generated_files"], 1);
        assert!(doc["uploads"]["generated_why"].is_string());
        // one file lost, or one more: a difference, even with the posts matching
        // one file lost: a difference, even with the posts matching
        let lost = "F\t1\t15\nG\t1\t3\nEND\n";
        let (doc, rc) = verify_doc("s1", &r, &with(SAME, Some(lost)), 0);
        assert_eq!(rc, 1, "{doc}");
        assert_eq!(doc["verdict"], "differ");
        assert_eq!(doc["posts_verdict"], "match");
        assert_eq!(doc["uploads"]["verdict"], "differ");
        assert_eq!(doc["uploads"]["missing_from_site"], 1);
        // one more than the backup (already on the site before the import): listed only
        let more = "F\t3\t15\nG\t1\t3\nEND\n";
        let (doc, rc) = verify_doc("s1", &r, &with(SAME, Some(more)), 0);
        assert_eq!(rc, 0, "{doc}");
        assert_eq!(doc["uploads"]["verdict"], "match");
        assert_eq!(doc["uploads"]["extra_on_site"], 1);
        assert_eq!(doc["uploads"]["missing_from_site"], 0);
    }

    #[test]
    fn uploads_that_cannot_be_counted_leave_the_restore_untold() {
        let r = read_archive(Cursor::new(with_uploads()));
        // no answer (over the cap, too slow, find failed), or an answer out of form
        for up in [None, Some(""), Some("F\t2\t15\nG\t1\t3\n"), Some("2\n")] {
            let (doc, rc) = verify_doc("s1", &r, &with(SAME, up), 0);
            assert_eq!(rc, 3, "{up:?} {doc}");
            assert_eq!(doc["verdict"], "unknown");
            assert_eq!(doc["posts_verdict"], "match");
            assert_eq!(doc["uploads"]["verdict"], "unknown");
        }
        // a difference in the posts is still a difference
        let fewer = "P\tpost\tpublish\t1\nU\t1\nEND\n";
        assert_eq!(verify_doc("s1", &r, &with(fewer, None), 0).1, 1);
        // a backup that is not trusted has its uploads not compared at all
        let mut cut = with_uploads();
        cut.truncate(cut.len() - HEADER_LEN);
        let r = read_archive(Cursor::new(cut));
        let (doc, rc) = verify_doc("s1", &r, &with(SAME, Some("F\t2\t15\nG\t1\t3\nEND\n")), 0);
        assert_eq!(rc, 3);
        assert!(doc["uploads"].get("actual").is_none(), "{doc}");
    }

    #[test]
    fn the_site_is_asked_through_the_runner_as_its_own_user_and_never_lists_files() {
        let mut seen: Vec<Vec<String>> = Vec::new();
        let mut fake = |argv: &[String]| -> Option<String> {
            seen.push(argv.to_vec());
            if argv.iter().any(|a| a == "sh") {
                Some(NO_UPLOADS.to_owned())
            } else {
                None
            }
        };
        let out = ask_site(&mut fake, 107, "s-ab12cd");
        assert_eq!(out.uploads.as_deref(), Some(NO_UPLOADS));
        assert!(out.counts.is_none());
        assert_eq!(seen.len(), 4);
        for argv in &seen {
            assert_eq!(
                argv[..7],
                ["pct", "exec", "107", "--", "sudo", "-u", "s-ab12cd"]
            );
        }
        let up = &seen[3];
        assert_eq!(
            up[7..12],
            [
                "sh",
                "-c",
                UPLOADS_SH,
                "sh",
                "/var/www/s-ab12cd/wp-content/uploads"
            ]
        );
        assert!(
            up.windows(2).any(|w| w == ["-path", "./elementor/css/*"]),
            "{up:?}"
        );
        assert_eq!(
            up.iter().filter(|a| *a == "-path").count(),
            wpress::GENERATED_UPLOADS.len()
        );
        // the answer is counts only: no file name is printed, and it is read with the cap
        assert!(
            !UPLOADS_SH.contains("%p")
                && !UPLOADS_SH.contains("%P")
                && !UPLOADS_SH.contains("-print ")
        );
        assert!(UPLOADS_SH.contains("-type f"));
        assert!(seen[0].contains(&"--skip-plugins".to_owned()));
    }

    /// The script itself, where GNU `find` is (the host's containers are Debian).
    #[cfg(target_os = "linux")]
    #[test]
    fn the_uploads_script_counts_files_and_bytes_only() {
        let dir = std::env::temp_dir().join(format!("aib-up-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("2026/01")).unwrap();
        std::fs::create_dir_all(dir.join("elementor/css")).unwrap();
        std::fs::write(dir.join("2026/01/a.jpg"), b"0123456789").unwrap();
        std::fs::write(dir.join("2026/01/new\nline.jpg"), b"01234").unwrap();
        std::fs::write(dir.join("elementor/css/p.css"), b"css").unwrap();
        let mut argv = uploads_argv(1, "x");
        argv.drain(..7);
        argv[4] = dir.to_string_lossy().into_owned();
        assert_eq!(run_real(&argv).as_deref(), Some("F\t2\t15\nG\t1\t3\nEND\n"));
        argv[4] = dir.join("absent").to_string_lossy().into_owned();
        assert_eq!(run_real(&argv).as_deref(), Some(NO_UPLOADS));
        // a folder find cannot read: no answer, never a short count (ebb36 L1); root reads it
        let root = Command::new("id")
            .arg("-u")
            .output()
            .is_ok_and(|o| o.stdout.trim_ascii() == b"0");
        if !root {
            use std::os::unix::fs::PermissionsExt;
            let locked = dir.join("2026/locked");
            std::fs::create_dir_all(&locked).unwrap();
            std::fs::write(locked.join("b.jpg"), b"x").unwrap();
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
            argv[4] = dir.to_string_lossy().into_owned();
            let got = run_real(&argv);
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(got, None);
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    pub(crate) const SAME: &str = "P\tpost\tpublish\t1\nP\tpage\tpublish\t1\nU\t1\nEND\n";

    #[test]
    fn verify_matches_when_the_site_holds_what_the_backup_said() {
        let r = read_archive(Cursor::new(good()));
        let (doc, rc) = verify_doc("s1", &r, &site(SAME), 0);
        assert_eq!(rc, 0, "{doc}");
        assert_eq!(doc["verdict"], "match");
        assert_eq!(doc["actual"]["users"], 1);
        assert_eq!(doc["plugins"]["judged"], false);
        assert_eq!(doc["plugins"]["in_backup"], 1);
        assert_eq!(doc["themes"]["in_backup"], 0);
        assert_eq!(doc["plugins"]["unexpected_on_site"][0], "akismet");
        assert_eq!(doc["themes"]["unexpected_on_site"][0], "twentytwentyfive");
        assert_eq!(
            doc["plugins"]["missing_from_site"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
        // revisions are listed, not judged
        let more = "P\tpost\tpublish\t1\nP\tpage\tpublish\t1\nP\trevision\tinherit\t4\nU\t1\nEND\n";
        let (doc, rc) = verify_doc("s1", &r, &site(more), 0);
        assert_eq!(rc, 0, "{doc}");
        assert_eq!(doc["advisory"][0]["kind"], "revision");
    }

    #[test]
    fn verify_differs_when_posts_or_users_are_missing() {
        let r = read_archive(Cursor::new(good()));
        let fewer = "P\tpost\tpublish\t1\nU\t1\nEND\n";
        let (doc, rc) = verify_doc("s1", &r, &site(fewer), 0);
        assert_eq!(rc, 1, "{doc}");
        assert_eq!(doc["verdict"], "differ");
        assert_eq!(doc["strict"][0]["kind"], "page");
        // an administrator the restore added on purpose
        let two = "P\tpost\tpublish\t1\nP\tpage\tpublish\t1\nU\t2\nEND\n";
        assert_eq!(verify_doc("s1", &r, &site(two), 0).1, 1);
        assert_eq!(verify_doc("s1", &r, &site(two), 1).1, 0);
    }

    #[test]
    fn verify_cannot_tell_from_a_bad_backup_or_a_silent_site() {
        let r = read_archive(Cursor::new(good()));
        let (doc, rc) = verify_doc("s1", &r, &SiteOut::default(), 0);
        assert_eq!(rc, 3, "{doc}");
        assert_eq!(doc["verdict"], "unknown");
        assert!(doc["plugins"]["unknown"].is_string());
        for bad in [
            "",
            "ERR\n",
            "P\tpost\tpublish\t1\nU\t1\n",
            "P\tpost\tpublish\tmany\nU\t1\nEND\n",
        ] {
            assert_eq!(verify_doc("s1", &r, &site(bad), 0).1, 3, "{bad:?}");
        }
        let mut cut = good();
        cut.truncate(cut.len() - HEADER_LEN);
        let r = read_archive(Cursor::new(cut));
        let (doc, rc) = verify_doc("s1", &r, &site(SAME), 0);
        assert_eq!(rc, 3);
        assert!(doc["reason"].to_string().contains("not reliable"));
    }

    #[test]
    fn verify_is_refused_before_anything_runs_when_the_words_are_wrong() {
        let a = |v: &[&str]| v.iter().map(|x| (*x).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&a(&["verify"])).is_err());
        assert!(cmd(&a(&["verify", "s1"])).is_err());
        assert!(cmd(&a(&["verify", "--users-extra", "1"])).is_err());
        assert!(cmd(&a(&["verify", "s1", "b.wpress", "--users-extra", "x"])).is_err());
        assert!(cmd(&a(&["verify", "s1", "b.wpress", "--other"])).is_err());
        let e = cmd(&a(&[
            "verify",
            "../x",
            "b.wpress",
            "--records",
            "/nonexistent-aib",
        ]))
        .unwrap_err();
        assert!(e.contains("not valid"), "{e}");
        assert!(cmd(&a(&[
            "verify",
            "s1",
            "b.wpress",
            "--records",
            "/nonexistent-aib"
        ]))
        .is_err());
    }

    #[test]
    fn an_answer_longer_than_the_cap_is_no_answer() {
        // `yes` never stops: read up to the cap, then the group is killed and nothing is used
        assert_eq!(bounded(Command::new("yes").arg("P")), None);
        let mut big = Command::new("head");
        big.args(["-c", &(WP_OUT_MAX + 1).to_string(), "/dev/zero"]);
        assert_eq!(bounded(&mut big), None);
        let mut fits = Command::new("head");
        fits.args(["-c", &WP_OUT_MAX.to_string(), "/dev/zero"]);
        assert_eq!(bounded(&mut fits).map(|s| s.len()), Some(WP_OUT_MAX));
        assert_eq!(bounded(&mut Command::new("false")), None);
    }

    #[test]
    fn the_counting_php_names_no_table_itself() {
        assert!(COUNT_PHP.contains("{$wpdb->posts}"));
        assert!(COUNT_PHP.contains("{$wpdb->users}"));
        assert!(!COUNT_PHP.contains("wp_"));
        assert!(COUNT_PHP.trim_end().ends_with("echo \"END\\n\";"));
    }
}
