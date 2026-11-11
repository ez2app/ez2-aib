// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host roster check | entry-lines | ssh-lines | sudoers` — read one customer's roster
//! (`aib_cloud::roster`) against this host's site records and print the `authorized_keys`
//! lines it stands for. **Nothing is changed**: putting the lines where sshd reads them is
//! a separate, approved step.
//!
//! ```text
//! aib-cloud-host roster check <file> [--records dir]
//! aib-cloud-host roster entry-lines <file> [--records dir]
//! aib-cloud-host roster ssh-lines <file> <site> [--records dir]
//! aib-cloud-host roster sudoers <file>… [--records dir]
//! aib-cloud-host roster share-check <file> --rosters <dir> [--owner <address>]… [--sharer <address>] [--records dir]
//! ```
//!
//! * `check` reads the roster strictly and says how many members and keys it holds.
//! * `entry-lines` prints the entry account's lines. Each runs the customer command
//!   through `sudo -n`: the entry account has no rights of its own, and the one thing it
//!   may run as root is that command (see `aib_cloud::roster::ENTRY_COMMAND`).
//! * `ssh-lines` prints the site user's lines for one site — and only for a site whose
//!   record names this roster's tenant outright. Any other site, or one that does not
//!   exist, is refused with the same word.
//! * `sudoers` prints the entry account's sudoers file from **every** roster on the host
//!   (`aib_cloud::roster::sudoers_file`): one rule per forced command, arguments written
//!   out, no `*`. It goes to `aib_cloud::roster::SUDOERS_PATH` through `visudo -cf` and
//!   `install -m 0440`, an approved step like the lines; `site verify` H14 reads it back.
//!   One roster refused refuses the whole file: a file missing a tenant's rules would lock
//!   that tenant out without saying so.
//! * `share-check` asks whether a backup shared with our drive account is this tenant's
//!   (`aib_cloud::share::judge`, plan §14.7 #11): the owners the drive names (`--owner`, as
//!   many as it names, none when it names none) and the sharer (`--sharer`, **always**
//!   when the drive names one), against the roster's `emails` — and against every other
//!   tenant's: `--rosters` is the directory of every roster on the host (each `*.json`
//!   in it one roster; the tenant's own file may be among them). An address another
//!   tenant also registers refuses the backup; a roster there that cannot be read leaves
//!   it unknown (c477a M1). A restore from a shared backup goes on only on `allowed`. The
//!   addresses come from the drive, read by the caller; nothing here reaches the drive.
//!
//! Exit status: 0 printed (`share-check`: allowed); 2 the words, the roster or the site
//! were refused; 3 a file could not be read; 4 (`share-check`) cannot tell whose the
//! backup is; 5 (`share-check`) the backup is not this tenant's. 4 and 5 both stop a
//! restore; they are two codes so a caller can tell "ask again" from "no" (c477a N3).

use std::fs;

use aib_cloud::roster::{self, Roster};
use aib_cloud::site::{record_tenant, valid_code};
use serde_json::Value;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";

/// The codes of every site this host has a record for. A record that cannot be read is an
/// error: a tenant id must not slip past the "is not a site code" rule because one record
/// was unreadable.
fn site_codes(records: &str) -> Result<Vec<String>, String> {
    let mut out = Vec::new();
    for entry in fs::read_dir(records).map_err(|e| format!("{records}: {e}"))? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rec: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let code = rec
            .get("site")
            .and_then(Value::as_str)
            .filter(|c| valid_code(c))
            .ok_or_else(|| format!("{}: no site code", path.display()))?;
        out.push(code.to_owned());
    }
    out.sort();
    Ok(out)
}

/// Read and check a roster file against the host's site codes.
fn load(file: &str, records: &str) -> Result<Result<Roster, roster::Bad>, String> {
    let codes = site_codes(records)?;
    let codes: Vec<&str> = codes.iter().map(String::as_str).collect();
    let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
    Ok(roster::parse(&text, &codes))
}

/// Whether `site`'s record names `tenant` outright.
fn site_is_of(site: &str, tenant: &str, records: &str) -> bool {
    valid_code(site)
        && fs::read_to_string(format!("{records}/{site}.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .is_some_and(|rec| {
                rec.get("site").and_then(Value::as_str) == Some(site)
                    && record_tenant(&rec) == Some(tenant)
            })
}

/// What to print for `words` (the arguments after `roster`), or the exit status that says why not.
pub fn answer(words: &[&str], records: &str) -> Result<Vec<String>, u8> {
    let (action, file, site) = match words {
        ["check", file] => ("check", *file, None),
        ["entry-lines", file] => ("entry-lines", *file, None),
        ["ssh-lines", file, site] => ("ssh-lines", *file, Some(*site)),
        ["sudoers", files @ ..] if !files.is_empty() => return sudoers(files, records),
        ["share-check", file, rest @ ..] => return share_check(file, rest, records),
        _ => {
            eprintln!("roster needs check <file> | entry-lines <file> | ssh-lines <file> <site> | sudoers <file>… | share-check <file> [--owner a]… [--sharer a]  [--records dir]");
            return Err(2);
        }
    };
    let roster = match load(file, records) {
        Err(why) => {
            eprintln!("could not read: {why}");
            return Err(3);
        }
        Ok(Err(bad)) => {
            eprintln!("refused: {}", bad.as_str());
            return Err(2);
        }
        Ok(Ok(r)) => r,
    };
    match (action, site) {
        ("entry-lines", _) => Ok(roster::entry_lines(&roster)),
        ("ssh-lines", Some(site)) => {
            if !site_is_of(site, &roster.tenant_id, records) {
                eprintln!("refused: tenant_mismatch");
                return Err(2);
            }
            Ok(roster::ssh_lines(&roster, site))
        }
        _ => {
            let keys: usize = roster.members.iter().map(|m| m.keys.len()).sum();
            Ok(vec![format!(
                "ok tenant_id={} members={} keys={keys}",
                roster.tenant_id,
                roster.members.len()
            )])
        }
    }
}

/// Every address the rosters in `dir` register for a tenant other than `tenant`. `None`
/// when the directory, or any roster in it, cannot be read or is refused: an address of
/// another tenant must not slip through because its roster was unreadable.
fn other_addresses(dir: &str, tenant: &str, records: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    for entry in fs::read_dir(dir).ok()? {
        let path = entry.ok()?.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let r = load(path.to_str()?, records).ok()?.ok()?;
        if r.tenant_id != tenant {
            out.extend(r.emails);
        }
    }
    Some(out)
}

/// `roster share-check <file> [--owner a]… [--sharer a]`: whether a shared backup is the
/// roster's tenant's. Any other word, a flag without its value or a second `--sharer` is
/// a usage error (2): an owner must not be dropped because of a typo.
fn share_check(file: &str, rest: &[&str], records: &str) -> Result<Vec<String>, u8> {
    use aib_cloud::share::{judge, Gate, Why};
    let mut owners: Vec<&str> = Vec::new();
    let mut sharer: Option<&str> = None;
    let mut dir: Option<&str> = None;
    let mut i = 0;
    while i < rest.len() {
        match (rest[i], rest.get(i + 1)) {
            ("--owner", Some(a)) => owners.push(*a),
            ("--sharer", Some(a)) if sharer.is_none() => sharer = Some(*a),
            ("--rosters", Some(d)) if dir.is_none() => dir = Some(*d),
            _ => {
                eprintln!("share-check needs <file> --rosters <dir> [--owner <address>]… [--sharer <address>]");
                return Err(2);
            }
        }
        i += 2;
    }
    let Some(dir) = dir else {
        eprintln!("share-check needs --rosters <dir>: every tenant's roster is read");
        return Err(2);
    };
    let roster = match load(file, records) {
        Err(why) => {
            eprintln!("could not read: {why}");
            return Err(3);
        }
        Ok(Err(bad)) => {
            eprintln!("refused: {}", bad.as_str());
            return Err(2);
        }
        Ok(Ok(r)) => r,
    };
    let gate = match other_addresses(dir, &roster.tenant_id, records) {
        Some(others) => judge(&owners, sharer, &roster.emails, &others),
        None => Gate::Unknown(Why::OtherRostersUnreadable),
    };
    let (word, why, code) = match gate {
        Gate::Allowed => return Ok(vec![format!("allowed tenant_id={}", roster.tenant_id)]),
        Gate::Refused(why) => ("refused", why, 5),
        Gate::Unknown(why) => ("unknown", why, 4),
    };
    eprintln!("{word}: {}", why.as_str());
    Err(code)
}

/// `roster sudoers <file>…`: the sudoers file of every roster named, or the status that says why not.
fn sudoers(files: &[&str], records: &str) -> Result<Vec<String>, u8> {
    let mut rosters = Vec::new();
    for file in files {
        match load(file, records) {
            Err(why) => {
                eprintln!("could not read: {why}");
                return Err(3);
            }
            Ok(Err(bad)) => {
                eprintln!("refused: {}", bad.as_str());
                return Err(2);
            }
            Ok(Ok(r)) => rosters.push(r),
        }
    }
    match roster::sudoers_file(&rosters) {
        Ok(text) => Ok(text.lines().map(str::to_owned).collect()),
        Err(bad) => {
            eprintln!("refused: {}", bad.as_str());
            Err(2)
        }
    }
}

/// `roster …`: see the module text.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let mut records = RECORDS.to_owned();
    let mut words: Vec<&str> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            // `share-check`'s own flags go through with their value; `answer` checks them
            "--owner" | "--sharer" | "--rosters" => {
                words.push(a.as_str());
                if let Some(v) = it.next() {
                    words.push(v.as_str());
                }
            }
            flag if flag.starts_with("--") => return Err(format!("unknown flag {flag}")),
            word => words.push(word),
        }
    }
    match answer(&words, &records) {
        Ok(lines) => {
            for l in lines {
                println!("{l}");
            }
            Ok(0)
        }
        Err(code) => Ok(code),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use serde_json::json;

    fn key(n: u8) -> String {
        let fill = char::from(b'A' + n);
        format!(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{}",
            std::iter::repeat_n(fill, 43).collect::<String>()
        )
    }

    /// A records directory with site2 and site3 of `t-aaa`, site4 of `t-bbb`, site5 of nobody,
    /// and a roster for `tenant`.
    fn world(name: &str, tenant: &str) -> (std::path::PathBuf, String) {
        let dir = std::env::temp_dir().join(format!("aib-roster-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let records = dir.join("sites");
        fs::create_dir_all(&records).unwrap();
        for (site, owner) in [
            ("site2", Some("t-aaa")),
            ("site3", Some("t-aaa")),
            ("site4", Some("t-bbb")),
            ("site5", None),
        ] {
            let mut rec = json!({ "site": site, "ct": 112 });
            if let Some(o) = owner {
                rec["tenant_id"] = json!(o);
            }
            fs::write(records.join(format!("{site}.json")), rec.to_string()).unwrap();
        }
        let roster = json!({ "tenant_id": tenant, "members": [
            { "identity_id": "m-01", "roles": ["owner"], "scope": "tenant", "keys": [
                { "public_key": key(0), "use": "entry", "added": "2026-11-11T00:00:00Z" },
                { "public_key": key(1), "use": "ssh", "added": "2026-11-11T00:00:00Z" }] }] });
        let file = dir.join("roster.json");
        fs::write(&file, roster.to_string()).unwrap();
        (dir, file.to_str().unwrap().to_owned())
    }

    #[test]
    fn lines_only_for_the_rosters_own_sites() {
        let (dir, file) = world("own", "t-aaa");
        let records = dir.join("sites");
        let r = records.to_str().unwrap();
        assert_eq!(
            answer(&["check", &file], r).unwrap(),
            ["ok tenant_id=t-aaa members=1 keys=2"]
        );
        let entry = answer(&["entry-lines", &file], r).unwrap();
        assert_eq!(entry.len(), 1);
        assert!(entry[0].starts_with(
            "command=\"sudo -n /usr/local/sbin/aib-cloud-host customer --tenant t-aaa --roles owner\",restrict ssh-ed25519 "
        ));
        assert_eq!(
            answer(&["ssh-lines", &file, "site3"], r).unwrap(),
            [format!("restrict,pty {} m-01", key(1))]
        );
        // another tenant's site, a site of nobody, a site that does not exist, not a code:
        // one refusal, and no line
        for site in ["site4", "site5", "site9", "../site3"] {
            assert_eq!(answer(&["ssh-lines", &file, site], r), Err(2), "{site}");
        }
        // wrong words, a roster that is not there
        assert_eq!(answer(&["ssh-lines", &file], r), Err(2));
        assert_eq!(answer(&["check", "/nonexistent/roster.json"], r), Err(3));
        assert_eq!(answer(&["check", &file], "/nonexistent/sites"), Err(3));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_shared_backup_passes_only_from_the_tenants_own_address() {
        let (dir, file) = world("share", "t-aaa");
        let f = file.as_str();
        let r = dir.join("sites");
        let r = r.to_str().unwrap();
        // every roster of the host: this tenant's own file and t-bbb's
        let all = dir.join("rosters");
        fs::create_dir_all(&all).unwrap();
        let d = all.to_str().unwrap();
        let sc = |extra: &[&str]| {
            let mut w = vec!["share-check", f, "--rosters", d];
            w.extend_from_slice(extra);
            answer(&w, r)
        };
        // no address registered yet: cannot tell, whoever the owner is
        assert_eq!(sc(&["--owner", "owner@example.com"]), Err(4));
        let mut v: Value = serde_json::from_str(&fs::read_to_string(&file).unwrap()).unwrap();
        v["emails"] = json!(["owner@example.com", "staff@example.com"]);
        fs::write(&file, v.to_string()).unwrap();
        fs::write(all.join("t-aaa.json"), v.to_string()).unwrap();
        let mut other = v;
        other["tenant_id"] = json!("t-bbb");
        other["emails"] = json!(["other@example.org"]);
        fs::write(all.join("t-bbb.json"), other.to_string()).unwrap();
        assert_eq!(
            sc(&["--owner", "Owner@Example.com"]).unwrap(),
            ["allowed tenant_id=t-aaa"]
        );
        assert_eq!(
            sc(&[
                "--owner",
                "owner@example.com",
                "--sharer",
                "owner@example.com"
            ])
            .unwrap(),
            ["allowed tenant_id=t-aaa"]
        );
        // someone else's file, someone else sharing it, two owners: not this tenant's
        for extra in [
            &["--owner", "other@example.org"][..],
            &[
                "--owner",
                "owner@example.com",
                "--sharer",
                "other@example.org",
            ][..],
            &[
                "--owner",
                "owner@example.com",
                "--owner",
                "other@example.org",
            ][..],
        ] {
            assert_eq!(sc(extra), Err(5), "{extra:?}");
        }
        // the drive named no owner: cannot tell
        assert_eq!(sc(&[]), Err(4));
        // a flag without its value, an unknown word, a second sharer, no --rosters:
        // usage, never allowed
        for extra in [
            &["--owner"][..],
            &["owner@example.com"][..],
            &[
                "--sharer",
                "owner@example.com",
                "--sharer",
                "owner@example.com",
            ][..],
            &["--rosters", d][..],
        ] {
            assert_eq!(sc(extra), Err(2), "{extra:?}");
        }
        assert_eq!(
            answer(&["share-check", f, "--owner", "owner@example.com"], r),
            Err(2)
        );
        assert_eq!(
            answer(
                &[
                    "share-check",
                    "/nonexistent/roster.json",
                    "--rosters",
                    d,
                    "--owner",
                    "owner@example.com"
                ],
                r
            ),
            Err(3)
        );
        // through the command line: the flags reach the check with their values
        let args = |owner: &str| -> Vec<String> {
            [
                "share-check",
                f,
                "--rosters",
                d,
                "--owner",
                owner,
                "--records",
                r,
            ]
            .iter()
            .map(|a| (*a).to_owned())
            .collect()
        };
        assert_eq!(cmd(&args("owner@example.com")), Ok(0));
        assert_eq!(cmd(&args("other@example.org")), Ok(5));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_address_another_tenant_also_registers_stops_the_restore() {
        let (dir, file) = world("share2", "t-aaa");
        let f = file.as_str();
        let r = dir.join("sites");
        let r = r.to_str().unwrap();
        let all = dir.join("rosters");
        fs::create_dir_all(&all).unwrap();
        let d = all.to_str().unwrap();
        let mut v: Value = serde_json::from_str(&fs::read_to_string(&file).unwrap()).unwrap();
        v["emails"] = json!(["owner@example.com", "staff@example.com"]);
        fs::write(&file, v.to_string()).unwrap();
        // t-bbb registers staff@ too (c477a M1): a backup of staff@ is not t-aaa's to restore
        let mut other = v;
        other["tenant_id"] = json!("t-bbb");
        other["emails"] = json!(["STAFF@example.com"]);
        fs::write(all.join("t-bbb.json"), other.to_string()).unwrap();
        let check = |extra: &[&str]| {
            let mut w = vec!["share-check", f, "--rosters", d];
            w.extend_from_slice(extra);
            answer(&w, r)
        };
        assert_eq!(check(&["--owner", "staff@example.com"]), Err(5));
        assert_eq!(
            check(&[
                "--owner",
                "owner@example.com",
                "--sharer",
                "staff@example.com"
            ]),
            Err(5)
        );
        assert!(check(&["--owner", "owner@example.com"]).is_ok());
        // a roster there that cannot be read, or that is refused: cannot tell
        fs::write(all.join("broken.json"), "{").unwrap();
        assert_eq!(check(&["--owner", "owner@example.com"]), Err(4));
        fs::write(
            all.join("broken.json"),
            json!({"tenant_id": "t-ccc", "x": 1}).to_string(),
        )
        .unwrap();
        assert_eq!(check(&["--owner", "owner@example.com"]), Err(4));
        fs::remove_file(all.join("broken.json")).unwrap();
        assert!(check(&["--owner", "owner@example.com"]).is_ok());
        // a directory that is not there: cannot tell
        let gone = dir.join("no-such-dir");
        let gone = gone.to_str().unwrap();
        assert_eq!(
            answer(
                &[
                    "share-check",
                    f,
                    "--rosters",
                    gone,
                    "--owner",
                    "owner@example.com"
                ],
                r
            ),
            Err(4)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_sudoers_file_comes_from_every_roster_or_none() {
        let (dir, file) = world("sudo", "t-aaa");
        let r = dir.join("sites");
        let r = r.to_str().unwrap();
        let other = dir.join("other.json");
        let text = fs::read_to_string(&file).unwrap().replace("t-aaa", "t-bbb");
        fs::write(&other, text).unwrap();
        let other = other.to_str().unwrap();
        let lines = answer(&["sudoers", &file, other], r).unwrap();
        assert_eq!(lines[1], roster::SUDOERS_DEFAULTS);
        assert_eq!(
            &lines[2..],
            [
                "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer --tenant t-aaa --roles owner",
                "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer --tenant t-bbb --roles owner",
            ]
        );
        // one roster unreadable, refused, or the same tenant twice: no file at all
        assert_eq!(
            answer(&["sudoers", &file, "/nonexistent/roster.json"], r),
            Err(3)
        );
        assert_eq!(answer(&["sudoers", &file, &file], r), Err(2));
        let clash = dir.join("clash.json");
        fs::write(
            &clash,
            fs::read_to_string(&file).unwrap().replace("t-aaa", "site5"),
        )
        .unwrap();
        assert_eq!(
            answer(&["sudoers", &file, clash.to_str().unwrap()], r),
            Err(2)
        );
        assert_eq!(answer(&["sudoers"], r), Err(2));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_roster_whose_tenant_is_a_site_code_gives_no_lines() {
        let (dir, file) = world("clash", "site5");
        let records = dir.join("sites");
        let r = records.to_str().unwrap();
        for words in [
            vec!["check", file.as_str()],
            vec!["entry-lines", file.as_str()],
            vec!["ssh-lines", file.as_str(), "site5"],
        ] {
            assert_eq!(answer(&words, r), Err(2), "{words:?}");
        }
        let _ = fs::remove_dir_all(&dir);
    }
}
