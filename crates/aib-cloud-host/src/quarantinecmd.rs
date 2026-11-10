// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site quarantine | unquarantine | quarantine-archive <code> --plan` —
//! the read-only half (4.4.1 §1.3 / §12). Reads the site record, the CT's firewall file,
//! the edge configuration and the quarantine record if one is on disk, asks
//! `aib_cloud::quarantine` for the plan and prints it. **Nothing is changed**: without
//! `--plan` the command refuses, because applying a plan needs the operation journal and
//! the host lock (`aib_cloud::op`), which are not wired to the host yet.
//!
//! The plan is printed as what would happen to this one site: the edge configuration is
//! shown as this site's new block, not as the whole file. It carries the operation id an
//! apply will bring back and is sealed ([`opstore::seal`]): `plan_sha256` covers the plan,
//! the SHA-256 of this host's machine id and the crate version. No readable machine id ⇒
//! no plan.

use std::fs;
use std::path::Path;

use aib_cloud::quarantine::{self, Action, Quarantine, Record};
use aib_cloud::site::{spec_from_record_strict, valid_code, SiteSpec};
use serde_json::{json, Value};

use crate::opstore;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const QUARANTINE_RECORDS: &str = "/var/lib/ez2-cloud/quarantine";
const PAGE: &str = "/srv/errors/quarantined.html";

/// Which plan is asked for.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Quarantine,
    Unquarantine,
    Archive,
}

/// A file that may legitimately be absent: `Ok(None)` when it is not there, an error when
/// it is there and cannot be read (that is not "absent").
fn read_optional(path: &str) -> Result<Option<String>, String> {
    match fs::read_to_string(path) {
        Ok(t) => Ok(Some(t)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{path}: {e}")),
    }
}

/// One action as a line of the printed plan. File contents are reduced to what concerns
/// this site.
pub fn action_json(spec: &SiteSpec, action: &Action) -> Value {
    match action {
        Action::SaveRecord(r) => json!({
            "do": "save_record", "site": r.site, "ct": r.ct, "op": r.op, "reason": r.reason,
            "keeps_firewall_bytes": r.prev_firewall.len(),
            "keeps_edge_block_bytes": r.prev_caddy_block.len(),
        }),
        Action::WriteFirewall { ct, text } => json!({
            "do": "write_firewall", "ct": ct,
            "quarantine": quarantine::is_quarantined(text), "text": text,
        }),
        Action::ApplyFirewall => json!({ "do": "apply_firewall" }),
        Action::DropConnections { ip } => json!({ "do": "drop_connections", "ip": ip }),
        // a block that cannot be taken out is shown as an error, not as a silent null (#625 seg. 4 N1)
        Action::WriteCaddyfile { text } => match quarantine::caddy_block_text(text, &spec.code) {
            Ok(block) => json!({ "do": "write_edge_block", "block": block }),
            Err(e) => json!({ "do": "write_edge_block", "block": null, "error": e.to_string() }),
        },
        Action::Verify { quarantined } => json!({ "do": "verify", "quarantined": quarantined }),
        Action::ArchiveRecord => json!({ "do": "archive_record" }),
    }
}

fn load_spec(code: &str, records: &str) -> Result<SiteSpec, String> {
    if !valid_code(code) {
        return Err(format!("site code {code:?} is not valid"));
    }
    let path = format!("{records}/{code}.json");
    let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
    let rec: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    // strict: a quarantine restores a firewall file from the record, so the record must
    // say which one
    let spec = spec_from_record_strict(&rec).map_err(|e| format!("{path}: {e}"))?;
    if spec.code != code {
        return Err(format!("{path}: record is for site {:?}", spec.code));
    }
    Ok(spec)
}

fn load_record(code: &str, dir: &str) -> Result<Option<Record>, String> {
    let path = format!("{dir}/{code}.json");
    let Some(text) = read_optional(&path)? else {
        return Ok(None);
    };
    let v: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    Record::from_json(&v)
        .map(Some)
        .map_err(|e| format!("{path}: {e}"))
}

/// Refuse a command that would put a quarantined site back on the network or overwrite
/// its quarantine (`site resume` / `suspend`, `site restore`): the decision is
/// `quarantine::guard`, the facts are the CT's firewall file and the quarantine record.
/// A record that cannot be read refuses too — it is not "no record" (DESIGN-eu02-quarantine
/// rule 4; 33e28 deep review 11-10: the guard had no caller on the host).
pub fn refuse_if_quarantined(
    code: &str,
    ct_fw_path: &str,
    quarantine_dir: &str,
    what: quarantine::Reopens,
) -> Result<(), String> {
    let ct_fw = read_optional(ct_fw_path)?.unwrap_or_default();
    let record = load_record(code, quarantine_dir)?;
    quarantine::guard(code, &ct_fw, record.as_ref(), what).map_err(|e| e.to_string())
}

/// [`refuse_if_quarantined`] with this host's paths.
pub fn refuse_if_quarantined_here(
    code: &str,
    ct: u32,
    what: quarantine::Reopens,
) -> Result<(), String> {
    refuse_if_quarantined(
        code,
        &format!("/etc/pve/firewall/{ct}.fw"),
        QUARANTINE_RECORDS,
        what,
    )
}

/// What was read from the host for one site.
#[derive(Clone, Copy)]
pub struct Seen<'a> {
    /// The CT's firewall file; empty when there is none.
    pub ct_fw: &'a str,
    /// The edge configuration.
    pub caddyfile: &'a str,
    /// The site's quarantine record, if one is on disk.
    pub record: Option<&'a Record>,
    /// Last journal state of the operation that record names, if a journal was found.
    pub quarantine_op_state: Option<aib_cloud::op::State>,
}

/// Build the plan document from what was read. Separate from [`cmd`] so it can be tested
/// without a host.
pub fn plan_doc(
    mode: Mode,
    spec: &SiteSpec,
    op: &str,
    reason: &str,
    seen: &Seen<'_>,
) -> Result<Value, String> {
    let Seen {
        ct_fw,
        caddyfile,
        record,
        quarantine_op_state,
    } = *seen;
    let (what, actions, already) = match mode {
        Mode::Quarantine => {
            match quarantine::quarantine_plan(spec, op, reason, PAGE, ct_fw, caddyfile, record)
                .map_err(|e| e.to_string())?
            {
                Quarantine::Do(a) => ("quarantine", a, false),
                Quarantine::Already(a) => ("quarantine", a, true),
            }
        }
        Mode::Unquarantine => {
            let record = record.ok_or("no quarantine record on disk for this site")?;
            let a = quarantine::unquarantine_plan(spec, record, ct_fw, caddyfile)
                .map_err(|e| e.to_string())?;
            ("unquarantine", a, false)
        }
        Mode::Archive => {
            let record = record.ok_or("no quarantine record on disk for this site")?;
            let a =
                quarantine::archive_stale_plan(spec, record, quarantine_op_state, ct_fw, caddyfile)
                    .map_err(|e| e.to_string())?;
            ("quarantine-archive", a, false)
        }
    };
    Ok(json!({
        "plan": what,
        "site": spec.code,
        "ct": spec.ct,
        "already_quarantined": already,
        // which incident the record on disk belongs to, for whoever approves a way out
        "record_op": record.map(|r| r.op.as_str()),
        "record_reason": record.map(|r| r.reason.as_str()),
        // quarantine is the pre-authorised emergency action; the ways out need a person
        "level": if mode == Mode::Quarantine { "L1" } else { "L2" },
        "on_failure": if mode == Mode::Quarantine {
            "stop where it failed; everything done so far stays; no timed revert"
        } else {
            "stop where it failed; the record stays on disk"
        },
        "steps": actions.iter().map(|a| action_json(spec, a)).collect::<Vec<_>>(),
    }))
}

/// Last journal state of the quarantine operation `record` names (B1 b; #625 seg. 2 M1).
/// `None` when there is no record or no journal for it — an archive plan is then refused.
/// A journal that is there but does not replay is an error, not "none": it is evidence.
pub fn record_op_state(
    ops_dir: &Path,
    record: Option<&Record>,
) -> Result<Option<aib_cloud::op::State>, String> {
    let Some(r) = record else {
        return Ok(None);
    };
    let (entries, _) = opstore::read_journal(&opstore::journal_path(ops_dir, &r.op)?)?;
    if entries.is_empty() {
        return Ok(None);
    }
    let last = aib_cloud::op::replay(&entries).map_err(|e| format!("journal of {}: {e}", r.op))?;
    Ok(Some(last.state))
}

/// Put the operation id into the plan and seal it: `plan_sha256` covers the plan with its
/// id, the host and the crate version.
pub fn seal_with_op(
    doc: &mut Value,
    op: &str,
    fingerprint: &str,
    version: &str,
) -> Result<(), String> {
    let obj = doc.as_object_mut().ok_or("a plan is a JSON object")?;
    obj.insert("op".to_owned(), json!(op));
    opstore::seal(doc, fingerprint, version)
}

pub fn cmd(args: &[String], mode: Mode) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = RECORDS.to_owned();
    let mut qdir = QUARANTINE_RECORDS.to_owned();
    let mut reason: Option<String> = None;
    let mut plan_only = false;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--quarantine-records" => {
                qdir = it.next().ok_or("--quarantine-records needs a dir")?.clone();
            }
            "--reason" => reason = Some(it.next().ok_or("--reason needs a short code")?.clone()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !plan_only {
        return Err(
            "only --plan is available: applying needs the operation journal and the host lock, which are not wired yet"
                .to_owned(),
        );
    }
    let reason = match (mode, reason) {
        (Mode::Quarantine, Some(r)) => r,
        (Mode::Quarantine, None) => {
            return Err(
                "quarantine needs --reason <code> (e.g. abuse_report, outbound_spam, operator)"
                    .to_owned(),
            )
        }
        (_, Some(_)) => return Err("--reason belongs to quarantine".to_owned()),
        (_, None) => String::new(),
    };
    let spec = load_spec(&code, &records)?;
    let ct_fw = read_optional(&format!("/etc/pve/firewall/{}.fw", spec.ct))?.unwrap_or_default();
    let caddyfile = crate::probe::edge_caddyfile()?;
    let record = load_record(&code, &qdir)?;
    let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
    let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
    // only an archive plan needs the journal of the quarantine the record names (#625
    // seg. 2 M1); the other plans must not be refused over a journal they do not use
    let quarantine_op_state = if mode == Mode::Archive {
        record_op_state(Path::new(opstore::OPS_DIR), record.as_ref())?
    } else {
        None
    };
    let seen = Seen {
        ct_fw: &ct_fw,
        caddyfile: &caddyfile,
        record: record.as_ref(),
        quarantine_op_state,
    };
    let mut doc = plan_doc(mode, &spec, &op, &reason, &seen)?;
    // the id an apply brings back, and the seal an approval is bound to (B1 a)
    seal_with_op(&mut doc, &op, &fingerprint, env!("CARGO_PKG_VERSION"))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::site::{caddy_block_upstream, desired_firewall, Limits};

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.test"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
        .with_fw_group("ez2-site-v2")
        .unwrap()
    }

    #[test]
    fn a_quarantine_plan_shows_this_site_only() {
        let s = spec();
        let fw = desired_firewall("ez2-site-v2", None);
        let file = format!(
            "{}\n# site:other\nother.example.test {{\n\treverse_proxy 10.77.0.20:80\n}}\n",
            caddy_block_upstream(&s, "10.77.0.12")
        );
        let seen = Seen {
            ct_fw: &fw,
            caddyfile: &file,
            record: None,
            quarantine_op_state: None,
        };
        let doc = plan_doc(
            Mode::Quarantine,
            &s,
            "20261110T014500Z-0a1b2c3d",
            "abuse_report",
            &seen,
        )
        .unwrap();
        assert_eq!(doc["plan"], "quarantine");
        assert_eq!(doc["level"], "L1");
        assert_eq!(doc["already_quarantined"], false);
        let kinds: Vec<&str> = doc["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["do"].as_str().unwrap())
            .collect();
        assert_eq!(
            kinds,
            [
                "save_record",
                "write_firewall",
                "apply_firewall",
                "drop_connections",
                "write_edge_block",
                "drop_connections",
                "verify"
            ]
        );
        assert_eq!(doc["steps"][1]["quarantine"], true);
        assert_eq!(doc["steps"][3]["ip"], "10.77.0.12");
        // the other site's block is not in what is printed
        let text = doc.to_string();
        assert!(text.contains("ez2:quarantined") && !text.contains("other.example.test"));
    }

    #[test]
    fn the_ways_out_need_a_record_and_are_l2() {
        let s = spec();
        let fw = desired_firewall("ez2-site-v2", None);
        let file = caddy_block_upstream(&s, "10.77.0.12");
        // a record whose host is fully restored: archive is the plan, unquarantine is not
        let record = Record {
            site: "site2".to_owned(),
            ct: 112,
            op: "20261110T014500Z-0a1b2c3d".to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: fw.clone(),
            prev_caddy_block: file.clone(),
        };
        let seen = |record, quarantine_op_state| Seen {
            ct_fw: &fw,
            caddyfile: &file,
            record,
            quarantine_op_state,
        };
        for mode in [Mode::Unquarantine, Mode::Archive] {
            let err = plan_doc(mode, &s, "x", "", &seen(None, None)).unwrap_err();
            assert!(err.contains("no quarantine record"), "{err}");
        }
        let done = Some(aib_cloud::op::State::Verified);
        let running = Some(aib_cloud::op::State::Running);
        let doc = plan_doc(Mode::Archive, &s, "x", "", &seen(Some(&record), done)).unwrap();
        assert_eq!(doc["level"], "L2");
        assert_eq!(doc["steps"][1]["do"], "archive_record");
        assert_eq!(doc["record_op"], "20261110T014500Z-0a1b2c3d");
        assert_eq!(doc["record_reason"], "operator");
        // without the journal, or with an unfinished quarantine, there is no archive plan
        assert!(plan_doc(Mode::Archive, &s, "x", "", &seen(Some(&record), None)).is_err());
        assert!(plan_doc(Mode::Archive, &s, "x", "", &seen(Some(&record), running)).is_err());
        assert!(plan_doc(Mode::Unquarantine, &s, "x", "", &seen(Some(&record), done)).is_err());
    }

    /// B1 (a): the printed plan carries its operation id, and its hash is over the plan
    /// with that id, this host and this version.
    #[cfg(target_os = "linux")]
    #[test]
    fn a_printed_plan_is_sealed_with_its_op() {
        let s = spec();
        let fw = desired_firewall("ez2-site-v2", None);
        let file = caddy_block_upstream(&s, "10.77.0.12");
        let op = "20261110T014500Z-0a1b2c3d";
        let seen = Seen {
            ct_fw: &fw,
            caddyfile: &file,
            record: None,
            quarantine_op_state: None,
        };
        let mut doc = plan_doc(Mode::Quarantine, &s, op, "operator", &seen).unwrap();
        seal_with_op(&mut doc, op, "f1", "0.2.0").unwrap();
        assert_eq!(doc["op"], op);
        let sha = doc["plan_sha256"].as_str().unwrap().to_owned();
        let again = opstore::sha256_hex(
            opstore::plan_hash_input(&opstore::unsealed(&doc), "f1", "0.2.0").as_bytes(),
        )
        .unwrap();
        assert_eq!(sha, again);
        let mut other = plan_doc(Mode::Quarantine, &s, op, "operator", &seen).unwrap();
        seal_with_op(&mut other, op, "f2", "0.2.0").unwrap();
        assert_ne!(other["plan_sha256"], doc["plan_sha256"], "another host");
    }

    /// #625 seg. 2 M1 wired to the journal (B1): the archive plan sees the state of the
    /// quarantine the record names — none, unfinished or verified.
    #[test]
    fn the_archive_plan_reads_the_quarantine_journal() {
        use aib_cloud::op::{Entry, State};
        let dir = std::env::temp_dir().join(format!(
            "aib-qcmd-journal-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let op = "20261110T014500Z-0a1b2c3d";
        let record = Record {
            site: "site2".to_owned(),
            ct: 112,
            op: op.to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: String::new(),
            prev_caddy_block: String::new(),
        };
        assert_eq!(record_op_state(&dir, None).unwrap(), None);
        assert_eq!(
            record_op_state(&dir, Some(&record)).unwrap(),
            None,
            "no journal"
        );
        let line = |seq, state, approval: &str| Entry {
            op: op.to_owned(),
            seq,
            at: 1_794_300_000,
            state,
            command: "site.quarantine".to_owned(),
            target: "site:site2".to_owned(),
            plan_sha256: "a".repeat(64),
            code: "ok".to_owned(),
            approval: approval.to_owned(),
        };
        opstore::append_entry(&dir, &line(1, State::Planned, "")).unwrap();
        opstore::append_entry(&dir, &line(2, State::Approved, "n1")).unwrap();
        opstore::append_entry(&dir, &line(3, State::Running, "n1")).unwrap();
        assert_eq!(
            record_op_state(&dir, Some(&record)).unwrap(),
            Some(State::Running)
        );
        opstore::append_entry(&dir, &line(4, State::Verified, "n1")).unwrap();
        assert_eq!(
            record_op_state(&dir, Some(&record)).unwrap(),
            Some(State::Verified)
        );
        // a journal that does not replay is not read as "no journal"
        fs::write(
            opstore::journal_path(&dir, op).unwrap(),
            format!("{}\n", line(2, State::Approved, "n1").to_json()),
        )
        .unwrap();
        assert!(record_op_state(&dir, Some(&record)).is_err());
    }

    /// The guard has a caller on the host: a record on disk or the quarantine firewall
    /// refuses; a damaged record refuses; a plain site passes.
    #[test]
    fn a_quarantined_site_is_not_reopened() {
        use quarantine::Reopens;
        let dir = std::env::temp_dir().join(format!(
            "aib-qcmd-guard-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("q")).unwrap();
        let fw = dir.join("112.fw");
        let (fwp, qd) = (fw.to_str().unwrap(), dir.join("q"));
        let qd = qd.to_str().unwrap();
        fs::write(&fw, desired_firewall("ez2-site-v2", None)).unwrap();
        assert!(refuse_if_quarantined("site2", fwp, qd, Reopens::Resume).is_ok());
        // no firewall file at all is not "quarantined" either
        assert!(
            refuse_if_quarantined("site2", &format!("{fwp}.none"), qd, Reopens::Resume).is_ok()
        );
        // a record on disk
        let record = Record {
            site: "site2".to_owned(),
            ct: 112,
            op: "20261110T014500Z-0a1b2c3d".to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: String::new(),
            // a record must name the block it replaced (#625: empty is refused as unreadable)
            prev_caddy_block: "site2.example.test {\n    reverse_proxy 10.77.0.12:80\n}\n"
                .to_owned(),
        };
        fs::write(
            dir.join("q").join("site2.json"),
            record.to_json().to_string(),
        )
        .unwrap();
        for what in [Reopens::Resume, Reopens::Restore] {
            let err = refuse_if_quarantined("site2", fwp, qd, what).unwrap_err();
            assert!(err.contains("quarantined"), "{err}");
        }
        // a record that cannot be read is not "no record"
        fs::write(dir.join("q").join("site2.json"), "{broken").unwrap();
        assert!(refuse_if_quarantined("site2", fwp, qd, Reopens::Resume).is_err());
        // the quarantine firewall without a record
        fs::remove_file(dir.join("q").join("site2.json")).unwrap();
        fs::write(&fw, quarantine::firewall()).unwrap();
        assert!(refuse_if_quarantined("site2", fwp, qd, Reopens::Resume).is_err());
    }

    #[test]
    fn nothing_but_a_plan() {
        let args = ["site2".to_owned()];
        let err = cmd(&args, Mode::Quarantine).unwrap_err();
        assert!(err.contains("only --plan"), "{err}");
        let args = ["site2".to_owned(), "--plan".to_owned()];
        let err = cmd(&args, Mode::Quarantine).unwrap_err();
        assert!(err.contains("--reason"), "{err}");
        let args = [
            "site2".to_owned(),
            "--plan".to_owned(),
            "--reason".to_owned(),
            "x".to_owned(),
        ];
        let err = cmd(&args, Mode::Unquarantine).unwrap_err();
        assert!(err.contains("belongs to quarantine"), "{err}");
    }
}
