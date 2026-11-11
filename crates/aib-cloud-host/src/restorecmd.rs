// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host restore expect <file.wpress>` — what a customer's All-in-One WP Migration
//! backup says the restored site must hold, read **before** the import (the counts taken
//! after it would be compared with themselves). Read-only: one file is read, nothing is
//! written, no network, no site is touched. The rules live in `aib_cloud::wpress`; this is
//! the reading half.
//!
//! Prints one JSON document. Exit status: 0 the expectation is reliable, 3 it is not (the
//! reasons say why; the caller must stop, not guess), 2 usage.

use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};

use aib_cloud::wpress::{self, Header, SqlScan, HEADER_LEN};
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
    doc.insert(
        "not_a_criterion".into(),
        json!("active_plugins, stylesheet and template are emptied by the export"),
    );
    let reliable = reasons.is_empty() && matches!(read.expect, Some(Ok(_)));
    doc.insert("reliable".into(), json!(reliable));
    doc.insert("reasons".into(), json!(reasons));
    (Value::Object(doc), reliable)
}

/// `restore expect <file>`.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let [sub, path] = args else {
        return Err("restore needs expect <file.wpress>".to_owned());
    };
    if sub != "expect" {
        return Err("restore needs expect <file.wpress>".to_owned());
    }
    let read = match File::open(path) {
        Ok(f) => read_archive(BufReader::new(f)),
        Err(e) => Read1 {
            reasons: vec![format!("the backup could not be opened: {e}")],
            ..Read1::default()
        },
    };
    let (doc, reliable) = document(&read);
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(if reliable { 0 } else { 3 })
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
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

    fn good() -> Vec<u8> {
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
}
