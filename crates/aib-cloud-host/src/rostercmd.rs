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
//!
//! Exit status: 0 printed; 2 the roster or the site was refused; 3 a file could not be read.

use std::fs;

use aib_cloud::roster::{self, Roster};
use aib_cloud::site::{record_tenant, valid_code};
use serde_json::Value;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";

/// The codes of every site this host has a record for. A record that cannot be read is an
/// error: a tenant id must not slip past the "is not a site code" rule because one record
/// was unreadable.
pub(crate) fn site_codes(records: &str) -> Result<Vec<String>, String> {
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
        _ => {
            eprintln!("roster needs check <file> | entry-lines <file> | ssh-lines <file> <site> | sudoers <file>…  [--records dir]");
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
