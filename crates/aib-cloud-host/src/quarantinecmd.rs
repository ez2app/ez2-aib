// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site quarantine | unquarantine | quarantine-archive <code> --plan` —
//! the read-only half (4.4.1 §1.3 / §12). Reads the site record, the CT's firewall file,
//! the edge configuration and the quarantine record if one is on disk, asks
//! `aib_cloud::quarantine` for the plan and prints it. **Nothing is changed**: without
//! `--plan` the command refuses, because applying a plan needs the operation journal and
//! the host lock (`aib_cloud::op`), which are not wired to the host yet.
//!
//! The plan is printed as what would happen to this one site: the edge configuration is
//! shown as this site's new block, not as the whole file.

use std::fs;
use std::io::Read;

use aib_cloud::quarantine::{self, Action, Quarantine, Record};
use aib_cloud::site::{spec_from_record_strict, valid_code, SiteSpec};
use serde_json::{json, Value};

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

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian (Howard Hinnant's
/// `civil_from_days`).
fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// An operation id for `now` (seconds since the epoch, UTC) and four random bytes.
pub fn op_id(now: u64, random: [u8; 4]) -> String {
    let secs = i64::try_from(now).unwrap_or(0);
    let (y, m, d) = civil(secs.div_euclid(86_400));
    let t = secs.rem_euclid(86_400);
    format!(
        "{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z-{:02x}{:02x}{:02x}{:02x}",
        t / 3600,
        t % 3600 / 60,
        t % 60,
        random[0],
        random[1],
        random[2],
        random[3]
    )
}

fn random4() -> Result<[u8; 4], String> {
    let mut b = [0u8; 4];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("/dev/urandom: {e}"))?;
    Ok(b)
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
        Action::WriteCaddyfile { text } => json!({
            "do": "write_edge_block",
            "block": quarantine::caddy_block_text(text, &spec.code).ok(),
        }),
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

/// Build the plan document from what was read. Separate from [`cmd`] so it can be tested
/// without a host.
pub fn plan_doc(
    mode: Mode,
    spec: &SiteSpec,
    op: &str,
    reason: &str,
    ct_fw: &str,
    caddyfile: &str,
    record: Option<&Record>,
    quarantine_op_state: Option<aib_cloud::op::State>,
) -> Result<Value, String> {
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
    let op = op_id(crate::probe::unix_now(), random4()?);
    let doc = plan_doc(
        mode,
        &spec,
        &op,
        &reason,
        &ct_fw,
        &caddyfile,
        record.as_ref(),
        // the operation journal is not wired to the host yet: without it an archive plan
        // is refused (it cannot tell an interrupted quarantine from a finished way out)
        None,
    )?;
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
    fn op_ids_are_what_the_journal_accepts() {
        assert_eq!(op_id(0, [0, 0, 0, 0]), "19700101T000000Z-00000000");
        // 2026-11-10 01:45:00 UTC
        assert_eq!(
            op_id(1_794_275_100, [0x0a, 0x1b, 0x2c, 0x3d]),
            "20261110T014500Z-0a1b2c3d"
        );
        // a leap day and the last second of a year
        assert_eq!(op_id(1_709_164_800, [255; 4]), "20240229T000000Z-ffffffff");
        assert_eq!(op_id(1_735_689_599, [1; 4]), "20241231T235959Z-01010101");
        for now in [0, 1_794_275_100, 1_709_164_800, 4_102_444_799] {
            assert!(aib_cloud::op::valid_op_id(&op_id(now, [7; 4])), "{now}");
        }
    }

    #[test]
    fn a_quarantine_plan_shows_this_site_only() {
        let s = spec();
        let fw = desired_firewall("ez2-site-v2", None);
        let file = format!(
            "{}\n# site:other\nother.example.test {{\n\treverse_proxy 10.77.0.20:80\n}}\n",
            caddy_block_upstream(&s, "10.77.0.12")
        );
        let doc = plan_doc(
            Mode::Quarantine,
            &s,
            "20261110T014500Z-0a1b2c3d",
            "abuse_report",
            &fw,
            &file,
            None,
            None,
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
        for mode in [Mode::Unquarantine, Mode::Archive] {
            let err = plan_doc(mode, &s, "x", "", &fw, &file, None, None).unwrap_err();
            assert!(err.contains("no quarantine record"), "{err}");
        }
        // a record whose host is fully restored: archive is the plan, unquarantine is not
        let record = Record {
            site: "site2".to_owned(),
            ct: 112,
            op: "20261110T014500Z-0a1b2c3d".to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: fw.clone(),
            prev_caddy_block: file.clone(),
        };
        let done = Some(aib_cloud::op::State::Verified);
        let doc = plan_doc(Mode::Archive, &s, "x", "", &fw, &file, Some(&record), done).unwrap();
        assert_eq!(doc["record_op"], "20261110T014500Z-0a1b2c3d");
        assert_eq!(doc["record_reason"], "operator");
        // without the journal, or with an unfinished quarantine, there is no archive plan
        let running = Some(aib_cloud::op::State::Running);
        assert!(plan_doc(Mode::Archive, &s, "x", "", &fw, &file, Some(&record), None).is_err());
        assert!(plan_doc(
            Mode::Archive,
            &s,
            "x",
            "",
            &fw,
            &file,
            Some(&record),
            running
        )
        .is_err());
        assert_eq!(doc["level"], "L2");
        assert_eq!(doc["steps"][1]["do"], "archive_record");
        assert!(plan_doc(
            Mode::Unquarantine,
            &s,
            "x",
            "",
            &fw,
            &file,
            Some(&record),
            done
        )
        .is_err());
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
