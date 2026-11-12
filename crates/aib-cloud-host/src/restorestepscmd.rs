// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site restore-wpress <code> --plan --from <backup id> | --plan --close
//! resolved|abandoned | --apply | --continue | --resolved | --abandon <plan.json>` — restoring a customer's own WordPress backup into one of
//! their sites, step by step (E3, B3 slice 2). The order, what a failure leaves and when a
//! restore is refused are [`aib_cloud::restoresteps`]'s; this module keeps the step record
//! and runs the steps as one operation of the journal.
//!
//! * One step record per site under [`RECORDS`]: the backup, the operation, the steps
//!   done, the step that failed and whether what the failure asked for (throw the
//!   download away, take the site back) was done. It is written whole (a new file,
//!   synced, renamed over the old one) when a restore starts, after every step and after
//!   a failure, so it never says more than was done.
//! * `--plan` reads the site's record, its last restore and every other site's last
//!   restore, and says whether a new restore starts, the last one goes on (and from
//!   which step) or nothing is done (and why). It changes nothing. The plan is sealed
//!   like every other plan.
//! * `--apply` takes a plan that starts a restore, `--continue` one that goes on with
//!   one: under the host lock the plan is made again and must be the very plan given,
//!   then the steps run as that plan's operation. A failed operation blocks the site
//!   until `op recovered`, as every other operation does; a kept restore then goes on
//!   with `--plan` and `--continue`.
//! * This slice has no step that does anything yet: downloading (with the sharer gate),
//!   importing and tidying are slices 3 and 4. On this host every `--apply` and
//!   `--continue` is refused before the lock is taken, so nothing is journaled; the
//!   order, the record and the failure handling are tested with a made-up host.
//! * A restore that this code can neither finish nor go on with — one interrupted before
//!   its tidying (it holds every site of the host), or one kept for a person whose
//!   customer wants another backup — is closed by a person: `--plan --close resolved`
//!   (the site is fine as it is; the record stays and reads as over) or `--plan --close
//!   abandoned` (the record is moved under `closed/`), then `--resolved <plan.json>` or
//!   `--abandon <plan.json>`, made again under the host lock like every other plan. The
//!   failed operation that left the restore so blocks the site until `op recovered`,
//!   so that comes first.
//! * A record is read only from its own site's file: one whose `site` is another site's
//!   (a copy a person made by hand) is an error, so it is never written over the other.
//! * Before a restore starts, the download gate ([`aib_cloud::restoregate`]) is asked:
//!   who shared the backup, whether its name is this site's, its size against the room
//!   on the host, and whether the site's database has been up long enough. The plan
//!   carries the backup's name and size; the verify step checks the downloaded file
//!   against them and writes its SHA-256 into the record (B3 slice 3). Reading the share
//!   itself is not wired yet: until it is, every plan here stops at `source_unknown`.
//! * The import ([`aib_cloud::restoreimport`], B3 slice 4): the site's own disk must
//!   hold it (asked by the plan and again right before the restore point, since the
//!   download takes time); the import plugins are brought up to date and must be
//!   active, the plugin must list the backup, and the restore must end with 0 and print
//!   WP-CLI's own `Success:` and no `Error:` (the plugin's words after them follow the
//!   site's language and are not read; c477a slice 4 L1). `df` and `mysqladmin` run
//!   with `LC_ALL=C`, so their words are the ones read; the import runs with
//!   [`restoreimport::IMPORT_ENV`].
//! * Wired on the real host (B3 slice 5): the import plugins are installed from the host's
//!   own copy ([`PLUGINS_DIR`], each zip checked against its `SHA256SUMS` before any is
//!   installed, `--force --activate`, never fetched from the vendor inside the customer's
//!   container), the backup is pushed from [`DOWNLOADS_DIR`] to where the plugin finds it,
//!   the restore runs as the site's user with [`restoreimport::IMPORT_ENV`] and
//!   `--no-color`, and the accept step compares the restored site with the downloaded
//!   backup as `restore verify` does; a difference keeps the site for a person. The
//!   download, the restore point, the tidying and the roll-back are not: [`HostRestorer`]
//!   still says it is not wired, so nothing runs yet.
//! * The command is `site restore-wpress`, not `site restore`: that one brings a whole
//!   container back from the host's own archive, a different thing.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use aib_cloud::restoregate::{self, Facts, Sharer};
use aib_cloud::restoreimport::{self, SiteRoom};
use aib_cloud::restoresteps::{
    self, AfterFailure, Close, Next, Other, RestoreStep, Standing, STEPS,
};
use aib_cloud::site::SiteSpec;
use aib_cloud::sitecreate::Probe;
use serde_json::{json, Value};

use crate::opstore;

/// Where the step records are: one `<site code>.json` per site.
pub const RECORDS: &str = "/var/lib/ez2-cloud/restores";
const SITE_RECORDS: &str = "/var/lib/ez2-cloud/sites";
/// The journal's name for this command.
pub const COMMAND: &str = "site.restore-wpress";
/// The journal's name for closing a restore.
pub const CLOSE_COMMAND: &str = "site.restore-wpress-close";
/// Why nothing runs on this host yet.
pub const NOT_WIRED: &str =
    "not_wired: the restore steps are not wired on this host yet (B3 slices 3 and 4); nothing was done";

/// The backup a restore downloads: its name and size as the share said, and the
/// SHA-256 of what was downloaded once it was checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Backup {
    pub name: String,
    pub size: u64,
    pub sha256: Option<String>,
}

/// One site's last restore.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub site: String,
    pub domain: String,
    /// The backup's id at the share it comes from.
    pub source: String,
    /// The operation that wrote the record last.
    pub op: String,
    pub done: Vec<RestoreStep>,
    /// The step that failed and why, when one did.
    pub failed: Option<(RestoreStep, String)>,
    /// Whether what [`restoresteps::after_failure`] asked for was done.
    pub cleaned_up: bool,
    /// How a person closed it and the operation that did, when one did.
    pub closed: Option<(Close, String)>,
    /// The backup, when the plan named one.
    pub backup: Option<Backup>,
}

impl Record {
    pub fn to_json(&self) -> Value {
        json!({
            "site": self.site,
            "domain": self.domain,
            "source": self.source,
            "op": self.op,
            "done": self.done.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
            "failed": self.failed.as_ref().map(|(s, why)| json!({ "step": s.as_str(), "why": why })),
            "cleaned_up": self.cleaned_up,
            "closed": self.closed.as_ref().map(|(how, op)| json!({ "how": how.as_str(), "op": op })),
            "backup": self.backup.as_ref().map(|b| json!({ "name": b.name, "size": b.size, "sha256": b.sha256 })),
        })
    }

    pub fn from_json(v: &Value) -> Result<Self, String> {
        let text = |k: &str| {
            v.get(k)
                .and_then(Value::as_str)
                .map(str::to_owned)
                .ok_or_else(|| format!("step record: no {k}"))
        };
        let step = |w: &Value| {
            w.as_str()
                .and_then(RestoreStep::parse)
                .ok_or_else(|| format!("step record: {w} is not a step"))
        };
        let done = v
            .get("done")
            .and_then(Value::as_array)
            .ok_or("step record: no done")?
            .iter()
            .map(step)
            .collect::<Result<Vec<_>, _>>()?;
        let failed = match v.get("failed") {
            None | Some(Value::Null) => None,
            Some(f) => Some((
                step(f.get("step").unwrap_or(&Value::Null))?,
                f.get("why")
                    .and_then(Value::as_str)
                    .ok_or("step record: a failure without why")?
                    .to_owned(),
            )),
        };
        let closed = match v.get("closed") {
            None | Some(Value::Null) => None,
            Some(c) => Some((
                c.get("how")
                    .and_then(Value::as_str)
                    .and_then(Close::parse)
                    .ok_or("step record: closed, but not how")?,
                c.get("op")
                    .and_then(Value::as_str)
                    .ok_or("step record: closed, but by no operation")?
                    .to_owned(),
            )),
        };
        let backup = match v.get("backup") {
            None | Some(Value::Null) => None,
            Some(b) => Some(backup_from(b).ok_or("step record: a backup without name or size")?),
        };
        Ok(Record {
            site: text("site")?,
            domain: text("domain")?,
            source: text("source")?,
            op: text("op")?,
            done,
            failed,
            cleaned_up: v
                .get("cleaned_up")
                .and_then(Value::as_bool)
                .ok_or("step record: no cleaned_up")?,
            closed,
            backup,
        })
    }

    pub fn standing(&self) -> Result<Standing, String> {
        restoresteps::standing(
            &self.done,
            self.failed.as_ref().map(|(s, _)| *s),
            self.cleaned_up,
            self.closed.is_some(),
        )
        .map_err(|w| {
            format!(
                "{w}: the step record of {} is not one this code writes",
                self.site
            )
        })
    }
}

/// A backup's name and size from a plan or a record; the SHA-256 when there is one.
fn backup_from(v: &Value) -> Option<Backup> {
    Some(Backup {
        name: v.get("name")?.as_str()?.to_owned(),
        size: v.get("size")?.as_u64()?,
        sha256: v.get("sha256").and_then(Value::as_str).map(str::to_owned),
    })
}

/// What the share says of a backup.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Source {
    pub name: String,
    pub size: Option<u64>,
    pub sharer: Sharer,
}

/// What was downloaded: its length, its first bytes and its SHA-256.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fetched {
    pub size: u64,
    pub head: Vec<u8>,
    pub sha256: String,
}

/// What a restore needs from the host. The real host is [`HostRestorer`]; tests use a
/// made-up one.
pub trait RestoreHost {
    /// Whether the steps do anything on this host. Until they do, nothing is run.
    fn wired(&self) -> bool;
    /// The site's step record; `None` when the site never had a restore.
    fn read_record(&mut self, site: &str) -> Result<Option<Record>, String>;
    /// Write the site's step record whole.
    fn write_record(&mut self, rec: &Record) -> Result<(), String>;
    /// The step records of every other site. One that cannot be read is an error, not
    /// left out: it might be the restore that should refuse this one.
    fn others(&mut self, site: &str) -> Result<Vec<Record>, String>;
    /// Whether the target site is up.
    fn site_up(&mut self) -> Probe;
    /// What the share says of the backup, with the share gate's verdict on who shared
    /// it; `None` when the share could not be read.
    fn source(&mut self, source: &str) -> Result<Option<Source>, String>;
    /// Free bytes where downloads go.
    fn free_bytes(&mut self) -> Option<u64>;
    /// How long the target's database has been up.
    fn db_up_secs(&mut self) -> Option<u64>;
    /// What the download step left.
    fn downloaded(&mut self, rec: &Record) -> Result<Fetched, String>;
    /// The target site's own disk.
    fn site_room(&mut self) -> Option<SiteRoom>;
    /// Bring the import plugins up to date; what `plugin list --status=active
    /// --field=name` then prints.
    fn update_import_plugins(&mut self, rec: &Record) -> Result<String, String>;
    /// Put the downloaded backup where the plugin finds it; what `ai1wm list-backups`
    /// then prints.
    fn place_backup(&mut self, rec: &Record) -> Result<String, String>;
    /// Run the plugin's restore of the backup with [`restoreimport::IMPORT_ENV`]; what
    /// it printed. A command that ends with anything but 0 is an error, whatever it
    /// printed.
    fn import(&mut self, rec: &Record) -> Result<String, String>;
    /// The domains of the customer's other sites on this host; none when the site's
    /// record names no customer. A site record that cannot be read is an error.
    fn sibling_domains(&mut self, site: &str) -> Result<Vec<String>, String>;
    fn run_step(&mut self, step: RestoreStep, rec: &Record) -> Result<(), String>;
    /// Put the site software's scheduler back as the install leaves it
    /// ([`crate::siteidentity::scheduler`]): a backup may bring back a `wp-config.php`
    /// without `DISABLE_WP_CRON`, and the timer must still be there. `true` when anything
    /// had to be written again.
    fn put_scheduler_back(&mut self, rec: &Record) -> Result<bool, String>;
    /// Throw away what was downloaded, and the restore point's archive if one was begun:
    /// a failed restore point leaves a half-made archive behind.
    fn discard(&mut self, rec: &Record) -> Result<(), String>;
    /// Bring the site back from the restore point.
    fn roll_back(&mut self, rec: &Record) -> Result<(), String>;
    /// Move a record a person abandoned out of the site's place, kept where it can still
    /// be read; the site may then start a new restore.
    fn move_aside(&mut self, rec: &Record) -> Result<(), String>;
}

/// The plan: what is done next with the site's restore, from what the host holds now.
pub fn plan_doc(
    spec: &SiteSpec,
    source: &str,
    host: &mut impl RestoreHost,
) -> Result<Value, String> {
    let domain = spec.primary().to_owned();
    let last = host.read_record(&spec.code)?;
    let standing = last.as_ref().map(Record::standing).transpose()?;
    let others = host.others(&spec.code)?;
    let mut other_standings = Vec::new();
    for o in &others {
        other_standings.push((o, o.standing()?));
    }
    let other_list: Vec<Other<'_>> = other_standings
        .iter()
        .map(|(o, s)| Other {
            site: &o.site,
            domain: &o.domain,
            standing: *s,
        })
        .collect();
    let mut doc = json!({
        "plan": "site-restore-wpress",
        "site": spec.code,
        "domain": domain,
        "source": source,
        "last": standing.map(standing_word),
        "next": "stop",
        "from": Value::Null,
        "stop": Value::Null,
        "backup": Value::Null,
    });
    let stop = |doc: &mut Value, why: &str| doc["stop"] = json!(why);
    if let Err(why) = restoresteps::ready_to_go_on(host.site_up()) {
        stop(&mut doc, why);
        return Ok(doc);
    }
    match restoresteps::next(standing) {
        Next::Start => match restoresteps::admit(&spec.code, &domain, &other_list) {
            Ok(()) => match download_gate(&spec.code, source, &domain, host)? {
                Ok(backup) => {
                    doc["backup"] = backup;
                    doc["next"] = json!("start");
                    doc["from"] = json!(RestoreStep::Download.as_str());
                }
                Err(refused) => stop(&mut doc, refused),
            },
            Err(why) => stop(&mut doc, why),
        },
        Next::GoOn(step) => match &last {
            Some(rec) if rec.source != source => stop(&mut doc, "source_differs"),
            _ => {
                doc["next"] = json!("go_on");
                doc["from"] = json!(step.as_str());
            }
        },
        Next::Stop(why) => stop(&mut doc, why),
    }
    Ok(doc)
}

/// The download gate, from what the host says now: the backup's name and size for the
/// plan, or the gate's fixed word.
fn download_gate(
    site: &str,
    source: &str,
    domain: &str,
    host: &mut impl RestoreHost,
) -> Result<Result<Value, &'static str>, String> {
    let Some(src) = host.source(source)? else {
        return Ok(Err("source_unknown"));
    };
    let facts = Facts {
        sharer: src.sharer,
        name: &src.name,
        domain,
        size: src.size,
        free: host.free_bytes(),
        db_up_secs: host.db_up_secs(),
    };
    if let Err(refused) = restoregate::admit_download(&facts) {
        return Ok(Err(refused));
    }
    // c477a (slice 3 r2 N1): the name's second level lets a backup of the customer's other
    // site through; one whose name is exactly that other site is refused here
    let siblings = host.sibling_domains(site)?;
    let siblings: Vec<&str> = siblings.iter().map(String::as_str).collect();
    if restoregate::names_another_site(&src.name, &siblings) {
        return Ok(Err("backup_of_another_site"));
    }
    let size = src.size.unwrap_or_default();
    Ok(
        restoreimport::site_room(size, host.site_room(), RESTORE_POINT_IN_SITE)
            .map(|()| json!({ "name": src.name, "size": size })),
    )
}

/// Whether the restore point is kept in the site's own disk. Until the restore point step
/// is wired and its place known, it is counted there.
pub const RESTORE_POINT_IN_SITE: bool = true;

/// Before the restore point: the site's disk must still hold the import, now that the
/// download is done.
fn before_restore_point(host: &mut impl RestoreHost, rec: &Record) -> Result<(), String> {
    let size = rec
        .backup
        .as_ref()
        .map(|b| b.size)
        .ok_or("the step record names no backup")?;
    restoreimport::site_room(size, host.site_room(), RESTORE_POINT_IN_SITE).map_err(str::to_owned)
}

/// The import step: plugins up to date and active, the backup listed, the restore ended
/// with 0 and said WP-CLI's `Success:` (in whatever language the site is).
fn import_backup(host: &mut impl RestoreHost, rec: &Record) -> Result<(), String> {
    let name = rec
        .backup
        .as_ref()
        .map(|b| b.name.clone())
        .ok_or("the step record names no backup")?;
    let active = host.update_import_plugins(rec)?;
    restoreimport::plugins_ready(&active).map_err(str::to_owned)?;
    let listing = host.place_backup(rec)?;
    restoreimport::backup_listed(&listing, &name).map_err(str::to_owned)?;
    let said = host.import(rec)?;
    restoreimport::import_finished(&said).map_err(str::to_owned)
}

/// The verify step: what was downloaded must be what the plan named; its SHA-256 goes
/// into the record.
fn verify_download(host: &mut impl RestoreHost, rec: &mut Record) -> Result<(), String> {
    let planned = rec
        .backup
        .as_ref()
        .map(|b| b.size)
        .ok_or("the step record names no backup")?;
    let got = host.downloaded(rec)?;
    restoregate::check_download(planned, got.size, &got.head, &got.sha256)
        .map_err(str::to_owned)?;
    if let Some(b) = rec.backup.as_mut() {
        b.sha256 = Some(got.sha256);
    }
    Ok(())
}

fn standing_word(s: Standing) -> String {
    match s {
        Standing::Finished => "finished".to_owned(),
        Standing::Discarded => "discarded".to_owned(),
        Standing::RolledBack => "rolled_back".to_owned(),
        Standing::Kept(step) => format!("kept:{}", step.as_str()),
        Standing::Interrupted(step) => format!("interrupted:{}", step.as_str()),
        Standing::Resolved => "resolved".to_owned(),
    }
}

/// Which plans `--apply` and `--continue` take.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Apply,
    Continue,
}

impl Mode {
    fn next_word(self) -> &'static str {
        match self {
            Mode::Apply => "start",
            Mode::Continue => "go_on",
        }
    }
}

/// The plan as this host makes it now, under the lock, checked against the plan given:
/// same hash, same document, and the kind of plan the mode takes. Returns the step to
/// start from.
pub fn recheck(
    spec: &SiteSpec,
    given: &Value,
    mode: Mode,
    fingerprint: &str,
    host: &mut impl RestoreHost,
) -> Result<(String, RestoreStep), String> {
    let op = given
        .get("op")
        .and_then(Value::as_str)
        .ok_or("the plan has no op")?
        .to_owned();
    let source = given
        .get("source")
        .and_then(Value::as_str)
        .ok_or("the plan has no source")?;
    let mut doc = plan_doc(spec, source, host)?;
    crate::quarantinecmd::seal_with_op(&mut doc, &op, fingerprint, env!("CARGO_PKG_VERSION"))?;
    if doc != *given {
        return Err(
            "plan_changed: this host is not as the plan saw it (or the plan was edited); make a new plan"
                .to_owned(),
        );
    }
    if doc["next"] != mode.next_word() {
        return Err(format!(
            "this plan says {} {}: {} takes a plan that says {}",
            doc["next"].as_str().unwrap_or_default(),
            doc["stop"].as_str().unwrap_or_default(),
            if mode == Mode::Apply {
                "--apply"
            } else {
                "--continue"
            },
            mode.next_word()
        ));
    }
    let from = doc["from"]
        .as_str()
        .and_then(RestoreStep::parse)
        .ok_or("the plan names no step to start from")?;
    Ok((op, from))
}

/// Run the steps from `from` on, writing the record after each, and on a failure do what
/// [`restoresteps::after_failure`] says and write that down too.
pub fn carry_out(
    host: &mut impl RestoreHost,
    mut rec: Record,
    from: RestoreStep,
) -> Result<Value, String> {
    let start = STEPS
        .iter()
        .position(|s| *s == from)
        .ok_or("no such step")?;
    rec.failed = None;
    rec.cleaned_up = false;
    host.write_record(&rec)?;
    for &step in &STEPS[start..] {
        let ran = match step {
            RestoreStep::Verify => verify_download(host, &mut rec),
            RestoreStep::RestorePoint => {
                before_restore_point(host, &rec).and_then(|()| host.run_step(step, &rec))
            }
            RestoreStep::Import => import_backup(host, &rec),
            // the scheduler first: a failure from here on keeps the site for a person, and
            // site verify S14 turns red on a site left without it (7b81e, wp-cron r2 N1)
            RestoreStep::Tidy => host
                .put_scheduler_back(&rec)
                .and_then(|_| host.run_step(step, &rec)),
            _ => host.run_step(step, &rec),
        };
        if let Err(e) = ran {
            rec.failed = Some((step, e.clone()));
            host.write_record(&rec)?;
            let after = restoresteps::after_failure(step);
            let undone = match after {
                AfterFailure::Discard => {
                    host.discard(&rec).map(|()| "the download was thrown away")
                }
                AfterFailure::RollBack => host
                    .roll_back(&rec)
                    .map(|()| "the site was taken back from the restore point"),
                AfterFailure::Keep => {
                    Ok("the site is kept as it is for a person; it may go on from this step")
                }
            };
            return match undone {
                Ok(said) => {
                    if after != AfterFailure::Keep {
                        rec.cleaned_up = true;
                        host.write_record(&rec)?;
                    }
                    Err(format!("{} failed: {e}; {said}", step.as_str()))
                }
                Err(u) => Err(format!(
                    "{} failed: {e}; and cleaning up after it failed too: {u}; a person looks",
                    step.as_str()
                )),
            };
        }
        rec.done.push(step);
        host.write_record(&rec).map_err(|e| {
            format!(
                "{} was done but the step record was not written: {e}",
                step.as_str()
            )
        })?;
    }
    Ok(json!({
        "site": rec.site,
        "done": rec.done.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
        "state": "ready_to_switch",
    }))
}

/// `--apply` / `--continue`: under the host lock the plan is made again and must be the
/// plan given, then the steps run as its operation.
pub fn apply(
    place: &opstore::OpPlace,
    plan: &Value,
    spec: &SiteSpec,
    mode: Mode,
    host: &mut impl RestoreHost,
) -> Result<Value, String> {
    if plan.get("plan").and_then(Value::as_str) != Some("site-restore-wpress")
        || plan.get("site").and_then(Value::as_str) != Some(spec.code.as_str())
    {
        return Err("not a site-restore-wpress plan for this site".to_owned());
    }
    if !host.wired() {
        return Err(NOT_WIRED.to_owned());
    }
    let op = plan
        .get("op")
        .and_then(Value::as_str)
        .ok_or("the plan has no op")?
        .to_owned();
    let host = std::cell::RefCell::new(host);
    let done = opstore::run_op_prepared_as(
        place,
        &op,
        COMMAND,
        &format!("site:{}", spec.code),
        opstore::BY_LOCAL_ROOT,
        || {
            let mut h = host.borrow_mut();
            let (op, from) = recheck(spec, plan, mode, &place.fingerprint, &mut **h)?;
            let rec = match (mode, h.read_record(&spec.code)?) {
                (Mode::Continue, Some(mut rec)) => {
                    rec.op = op;
                    rec
                }
                (Mode::Continue, None) => return Err("no restore to go on with".to_owned()),
                (Mode::Apply, _) => Record {
                    site: spec.code.clone(),
                    domain: spec.primary().to_owned(),
                    source: plan["source"].as_str().unwrap_or_default().to_owned(),
                    op,
                    done: Vec::new(),
                    failed: None,
                    cleaned_up: false,
                    closed: None,
                    backup: plan.get("backup").and_then(backup_from),
                },
            };
            Ok(Some((opstore::unsealed(plan), (rec, from))))
        },
        |_op, (rec, from): (Record, RestoreStep)| {
            let mut h = host.borrow_mut();
            carry_out(&mut **h, rec, from)
        },
    )?;
    done.ok_or_else(|| "nothing to do".to_owned())
}

/// The plan to close the site's last restore as `how`: what the record says now, and
/// whether it may be closed at all.
pub fn close_doc(
    spec: &SiteSpec,
    how: Close,
    host: &mut impl RestoreHost,
) -> Result<Value, String> {
    let last = host.read_record(&spec.code)?;
    let standing = last.as_ref().map(Record::standing).transpose()?;
    let mut doc = json!({
        "plan": "site-restore-wpress-close",
        "site": spec.code,
        "domain": spec.primary(),
        "close": how.as_str(),
        "last": standing.map(standing_word),
        "source": last.as_ref().map(|r| r.source.clone()),
        "record_op": last.as_ref().map(|r| r.op.clone()),
        "next": "close",
        "stop": Value::Null,
    });
    if let Err(why) = restoresteps::may_close(standing) {
        doc["next"] = json!("stop");
        doc["stop"] = json!(why);
    }
    Ok(doc)
}

/// The close plan as this host makes it now, under the lock, checked against the plan
/// given, and against what the person asked for: `--resolved` takes only a plan that
/// closes as resolved, `--abandon` only one that abandons. Returns the plan's operation.
pub fn recheck_close(
    spec: &SiteSpec,
    given: &Value,
    how: Close,
    fingerprint: &str,
    host: &mut impl RestoreHost,
) -> Result<String, String> {
    let op = given
        .get("op")
        .and_then(Value::as_str)
        .ok_or("the plan has no op")?
        .to_owned();
    let said = given
        .get("close")
        .and_then(Value::as_str)
        .and_then(Close::parse)
        .ok_or("the plan does not say how to close")?;
    let mut doc = close_doc(spec, said, host)?;
    crate::quarantinecmd::seal_with_op(&mut doc, &op, fingerprint, env!("CARGO_PKG_VERSION"))?;
    if *given != doc {
        return Err(
            "plan_changed: the restore is not as the plan saw it (or the plan was edited); make a new plan"
                .to_owned(),
        );
    }
    if said != how {
        return Err(format!(
            "this plan closes as {}: {} takes a plan that closes as {}",
            said.as_str(),
            close_flag(how),
            how.as_str()
        ));
    }
    if doc["next"] != "close" {
        return Err(format!(
            "this plan says stop {}: there is nothing to close",
            doc["stop"].as_str().unwrap_or_default()
        ));
    }
    Ok(op)
}

fn close_flag(how: Close) -> &'static str {
    match how {
        Close::Resolved => "--resolved",
        Close::Abandoned => "--abandon",
    }
}

/// `--resolved` / `--abandon`: under the host lock the close plan is made again and must
/// be the plan given, then the record is marked closed (resolved) or moved aside
/// (abandoned).
pub fn apply_close(
    place: &opstore::OpPlace,
    plan: &Value,
    spec: &SiteSpec,
    how: Close,
    host: &mut impl RestoreHost,
) -> Result<Value, String> {
    if plan.get("plan").and_then(Value::as_str) != Some("site-restore-wpress-close")
        || plan.get("site").and_then(Value::as_str) != Some(spec.code.as_str())
    {
        return Err("not a site-restore-wpress close plan for this site".to_owned());
    }
    let op = plan
        .get("op")
        .and_then(Value::as_str)
        .ok_or("the plan has no op")?
        .to_owned();
    let host = std::cell::RefCell::new(host);
    let done = opstore::run_op_prepared_as(
        place,
        &op,
        CLOSE_COMMAND,
        &format!("site:{}", spec.code),
        opstore::BY_LOCAL_ROOT,
        || {
            let mut h = host.borrow_mut();
            let op = recheck_close(spec, plan, how, &place.fingerprint, &mut **h)?;
            let rec = h.read_record(&spec.code)?.ok_or("no restore to close")?;
            Ok(Some((opstore::unsealed(plan), (rec, op))))
        },
        |_op, (mut rec, op): (Record, String)| {
            let mut h = host.borrow_mut();
            rec.closed = Some((how, op.clone()));
            match how {
                Close::Resolved => h.write_record(&rec)?,
                Close::Abandoned => h.move_aside(&rec)?,
            }
            Ok(json!({ "site": rec.site, "closed": how.as_str(), "op": op }))
        },
    )?;
    done.ok_or_else(|| "nothing to do".to_owned())
}

/// The file system downloads go to.
const DOWNLOADS_ON: &str = "/var/lib/ez2-cloud";

/// The free bytes `df -Pk` says: the fourth column of its one data line, in KiB.
fn df_avail_bytes(out: &str) -> Option<u64> {
    df_kib(out).and_then(|(_, avail)| kib_bytes(avail))
}

/// The used and free KiB of `df -Pk`'s one data line (its third and fourth columns).
fn df_kib(out: &str) -> Option<(u64, u64)> {
    let mut lines = out.lines().filter(|l| !l.trim().is_empty());
    if !lines.next()?.starts_with("Filesystem") {
        return None;
    }
    let line = lines.next()?;
    if lines.next().is_some() {
        return None;
    }
    let mut columns = line.split_whitespace().skip(2);
    let used = columns.next()?.parse().ok()?;
    let avail = columns.next()?.parse().ok()?;
    Some((used, avail))
}

fn kib_bytes(kib: u64) -> Option<u64> {
    kib.checked_mul(1024)
}

/// The site's disk from `df -Pk /` in its container.
fn df_room(out: &str) -> Option<SiteRoom> {
    let (used, avail) = df_kib(out)?;
    Some(SiteRoom {
        free: kib_bytes(avail)?,
        used: kib_bytes(used)?,
    })
}

/// The seconds `mysqladmin status` says the server has been up.
fn db_uptime(out: &str) -> Option<u64> {
    let rest = out.trim_start().strip_prefix("Uptime:")?;
    rest.split_whitespace().next()?.parse().ok()
}

/// Where the import plugins are kept on the host (root only, B3 slice 5, Andy 11-12 14:5x):
/// one zip per plugin of [`restoreimport::IMPORT_PLUGINS`], `<plugin>.zip`, and a
/// `SHA256SUMS` naming each. Put there and kept up to date by a host order; the import
/// installs from it and never reaches the plugin's vendor from the customer's container.
pub const PLUGINS_DIR: &str = "/var/lib/ez2-cloud/wp-plugins";
/// Where a site's downloaded backups are kept: `<this>/<site code>/<backup name>`.
pub const DOWNLOADS_DIR: &str = "/var/lib/ez2-cloud/restores-dl";
/// Administrators the restore adds on purpose, which the accept step allows. The tidying
/// step adds none yet (it is not wired); when it does, it writes how many into the record.
pub const ACCEPT_USERS_EXTRA: u64 = 0;

/// The real host. Its steps are not all wired yet: [`RestoreHost::wired`] is false.
pub struct HostRestorer {
    pub records: PathBuf,
    /// The site records: `<site code>.json` each.
    pub sites: PathBuf,
    pub ct: u32,
    /// The site's code: its user and its folder in the container.
    pub code: String,
    /// [`PLUGINS_DIR`].
    pub plugins: PathBuf,
    /// This site's downloads: [`DOWNLOADS_DIR`]`/<code>`.
    pub downloads: PathBuf,
    /// How a command is run. Tests put a made-up one here.
    pub run: Box<dyn crate::sitecreatecmd::Runner>,
}

/// The real runner of the restore's commands: what a command prints is read up to
/// [`crate::sitecreatecmd::CT_OUT_MAX`] (more is a failure; it comes from inside the
/// customer's container), anything but exit 0 is an error, and each kind of command has
/// its time.
pub struct RestoreRunner;

impl RestoreRunner {
    fn seconds(argv: &[String]) -> u64 {
        let has = |w: &str| argv.iter().any(|a| a == w);
        if has("ai1wm") && has("restore") {
            4 * 3600
        } else if argv.get(1).is_some_and(|a| a == "push") || has("sha256sum") {
            3600
        } else {
            600
        }
    }
}

impl crate::sitecreatecmd::Runner for RestoreRunner {
    fn run(&mut self, argv: &[String]) -> Result<String, String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args);
        match crate::probe::run_capture_bounded(
            &mut cmd,
            Duration::from_secs(Self::seconds(argv)),
            crate::sitecreatecmd::CT_OUT_MAX,
        )? {
            (out, false) => Ok(out),
            (_, true) => Err(format!("{program}: said more than is read")),
        }
    }
}

/// `wp` as the site's user inside its container, in the C locale, without colours (a
/// colour code before `Success:` would hide it; c477a slice 4 N1).
fn wp_argv(ct: u32, code: &str, args: &[&str]) -> Vec<String> {
    let mut v: Vec<String> = vec![
        "pct".into(),
        "exec".into(),
        ct.to_string(),
        "--".into(),
        "sudo".into(),
        "-u".into(),
        code.to_owned(),
        "env".into(),
    ];
    v.extend(restoreimport::IMPORT_ENV.iter().map(|e| (*e).to_owned()));
    v.push("wp".into());
    v.push(format!("--path=/var/www/{code}"));
    v.push("--no-color".into());
    v.extend(args.iter().map(|a| (*a).to_owned()));
    v
}

/// `pct push` of a host file into the container, owned by the site's user.
fn push_argv(ct: u32, from: &Path, to: &str, code: &str, perms: &str) -> Vec<String> {
    vec![
        "pct".into(),
        "push".into(),
        ct.to_string(),
        from.to_string_lossy().into_owned(),
        to.to_owned(),
        "--user".into(),
        code.to_owned(),
        "--group".into(),
        code.to_owned(),
        "--perms".into(),
        perms.to_owned(),
    ]
}

/// The SHA-256 a `SHA256SUMS` file gives `name` (`<hex>  <name>` or `<hex> *<name>`).
fn sum_of<'a>(sums: &'a str, name: &str) -> Option<&'a str> {
    sums.lines().find_map(|l| {
        let (hex, file) = l.trim_end().split_once(char::is_whitespace)?;
        let file = file.trim_start();
        let file = file.strip_prefix('*').unwrap_or(file);
        (file == name && hex.len() == 64 && hex.bytes().all(|c| c.is_ascii_hexdigit()))
            .then_some(hex)
    })
}

/// A backup's name as a file name: no folder in it, not hidden.
fn plain_name(name: &str) -> Result<&str, String> {
    if name.is_empty() || name.starts_with('.') || name.contains('/') || name.contains('\0') {
        return Err(format!("the backup's name {name:?} is not a file name"));
    }
    Ok(name)
}

fn write_whole(path: &Path, text: &str) -> Result<(), String> {
    use std::io::Write as _;
    use std::os::unix::fs::OpenOptionsExt as _;
    let name = path
        .file_name()
        .ok_or("no file name")?
        .to_string_lossy()
        .into_owned();
    let tmp = path.with_file_name(format!(".{name}.tmp-{}", std::process::id()));
    let _ = fs::remove_file(&tmp);
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .and_then(|()| fs::rename(&tmp, path))
        .map_err(|e| format!("{}: {e}", path.display()))?;
    if let Some(dir) = path.parent() {
        fs::File::open(dir)
            .and_then(|d| d.sync_all())
            .map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    Ok(())
}

/// The record in `path`, which is `expected`'s file: a record of another site there is an
/// error, or going on with it would write over that site's record.
fn read_record_file(path: &Path, expected: &str) -> Result<Option<Record>, String> {
    match fs::read_to_string(path) {
        Ok(text) => {
            let v: Value =
                serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
            let rec = Record::from_json(&v)?;
            if rec.site != expected {
                return Err(format!(
                    "{}: the record is of {}, not of {expected}; a person looks",
                    path.display(),
                    rec.site
                ));
            }
            Ok(Some(rec))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

impl RestoreHost for HostRestorer {
    fn wired(&self) -> bool {
        false
    }
    fn read_record(&mut self, site: &str) -> Result<Option<Record>, String> {
        read_record_file(&self.records.join(format!("{site}.json")), site)
    }
    fn write_record(&mut self, rec: &Record) -> Result<(), String> {
        fs::create_dir_all(&self.records)
            .map_err(|e| format!("{}: {e}", self.records.display()))?;
        let text = serde_json::to_string_pretty(&rec.to_json()).map_err(|e| e.to_string())?;
        write_whole(
            &self.records.join(format!("{}.json", rec.site)),
            &(text + "\n"),
        )
    }
    fn others(&mut self, site: &str) -> Result<Vec<Record>, String> {
        let entries = match fs::read_dir(&self.records) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(e) => return Err(format!("{}: {e}", self.records.display())),
        };
        let own = format!("{site}.json");
        let mut out = Vec::new();
        for entry in entries {
            let path = entry.map_err(|e| e.to_string())?.path();
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let Some(expected) = name.strip_suffix(".json") else {
                continue;
            };
            if name.starts_with('.') || name == own {
                continue;
            }
            match read_record_file(&path, expected)? {
                Some(rec) => out.push(rec),
                // listed, then removed by a person before it was read: nothing to hold
                None => continue,
            }
        }
        Ok(out)
    }
    fn site_up(&mut self) -> Probe {
        let mut cmd = Command::new("pct");
        cmd.args(["status", &self.ct.to_string()]);
        match crate::probe::run_capture(&mut cmd, Duration::from_secs(20)) {
            Ok(out) if out.trim() == "status: running" => Probe::Done,
            Ok(out) if out.trim() == "status: stopped" => Probe::NotDone,
            _ => Probe::Unknown,
        }
    }
    fn source(&mut self, _source: &str) -> Result<Option<Source>, String> {
        // reading the share (and asking the share gate) is wired with the download
        Ok(None)
    }
    fn free_bytes(&mut self) -> Option<u64> {
        let mut cmd = Command::new("df");
        cmd.env("LC_ALL", "C").args(["-Pk", DOWNLOADS_ON]);
        crate::probe::run_capture(&mut cmd, Duration::from_secs(20))
            .ok()
            .and_then(|out| df_avail_bytes(&out))
    }
    fn db_up_secs(&mut self) -> Option<u64> {
        let mut cmd = Command::new("pct");
        cmd.args([
            "exec",
            &self.ct.to_string(),
            "--",
            "env",
            "LC_ALL=C",
            "mysqladmin",
            "status",
        ]);
        crate::probe::run_capture(&mut cmd, Duration::from_secs(30))
            .ok()
            .and_then(|out| db_uptime(&out))
    }
    fn downloaded(&mut self, _rec: &Record) -> Result<Fetched, String> {
        Err(NOT_WIRED.to_owned())
    }
    fn site_room(&mut self) -> Option<SiteRoom> {
        let mut cmd = Command::new("pct");
        cmd.args([
            "exec",
            &self.ct.to_string(),
            "--",
            "env",
            "LC_ALL=C",
            "df",
            "-Pk",
            "/",
        ]);
        crate::probe::run_capture(&mut cmd, Duration::from_secs(20))
            .ok()
            .and_then(|out| df_room(&out))
    }
    fn update_import_plugins(&mut self, _rec: &Record) -> Result<String, String> {
        // every zip is checked against SHA256SUMS before any is installed
        let sums_file = self.plugins.join("SHA256SUMS");
        let sums = fs::read_to_string(&sums_file)
            .map_err(|e| format!("import_plugin_source: {}: {e}", sums_file.display()))?;
        let mut zips = Vec::new();
        for plugin in restoreimport::IMPORT_PLUGINS {
            let name = format!("{plugin}.zip");
            let want = sum_of(&sums, &name)
                .ok_or_else(|| format!("import_plugin_source: {name} is not in SHA256SUMS"))?;
            let path = self.plugins.join(&name);
            let said = self.run.run(&[
                "sha256sum".to_owned(),
                "--".to_owned(),
                path.to_string_lossy().into_owned(),
            ])?;
            let got = said.split_whitespace().next().unwrap_or_default();
            if !got.eq_ignore_ascii_case(want) {
                return Err(format!(
                    "import_plugin_sha_mismatch: {name} is not the one SHA256SUMS names; nothing was installed"
                ));
            }
            zips.push((name, path));
        }
        for (name, path) in &zips {
            let inside = format!("/tmp/ez2-{name}");
            self.run
                .run(&push_argv(self.ct, path, &inside, &self.code, "0600"))?;
            let installed = self.run.run(&wp_argv(
                self.ct,
                &self.code,
                &["plugin", "install", &inside, "--force", "--activate"],
            ));
            // the zip goes whether the install worked or not
            let removed = self.run.run(&[
                "pct".to_owned(),
                "exec".to_owned(),
                self.ct.to_string(),
                "--".to_owned(),
                "rm".to_owned(),
                "-f".to_owned(),
                "--".to_owned(),
                inside.clone(),
            ]);
            installed?;
            removed?;
        }
        self.run.run(&wp_argv(
            self.ct,
            &self.code,
            &["plugin", "list", "--status=active", "--field=name"],
        ))
    }
    fn place_backup(&mut self, rec: &Record) -> Result<String, String> {
        let name = rec
            .backup
            .as_ref()
            .map(|b| b.name.clone())
            .ok_or("the step record names no backup")?;
        let name = plain_name(&name)?;
        let to = format!("/var/www/{}/wp-content/ai1wm-backups/{name}", self.code);
        self.run.run(&push_argv(
            self.ct,
            &self.downloads.join(name),
            &to,
            &self.code,
            "0640",
        ))?;
        self.run
            .run(&wp_argv(self.ct, &self.code, &["ai1wm", "list-backups"]))
    }
    fn import(&mut self, rec: &Record) -> Result<String, String> {
        let name = rec
            .backup
            .as_ref()
            .map(|b| b.name.clone())
            .ok_or("the step record names no backup")?;
        let name = plain_name(&name)?;
        let restore_argv = wp_argv(self.ct, &self.code, &["ai1wm", "restore", name, "--yes"]);
        // anything but exit 0 is an error, whatever it printed (c477a slice 4 L1)
        self.run.run(&restore_argv)
    }
    fn sibling_domains(&mut self, site: &str) -> Result<Vec<String>, String> {
        let read = |path: &Path| -> Result<Value, String> {
            let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
        };
        let own = read(&self.sites.join(format!("{site}.json")))?;
        let Some(tenant) = aib_cloud::site::record_tenant(&own) else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        let entries =
            fs::read_dir(&self.sites).map_err(|e| format!("{}: {e}", self.sites.display()))?;
        for entry in entries {
            let path = entry.map_err(|e| e.to_string())?.path();
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            if name.starts_with('.') || !name.ends_with(".json") || name == format!("{site}.json") {
                continue;
            }
            let rec = read(&path)?;
            if aib_cloud::site::record_tenant(&rec) == Some(tenant) {
                let spec = aib_cloud::site::spec_from_record(&rec)
                    .map_err(|e| format!("{}: {e}", path.display()))?;
                out.extend(spec.hosts);
            }
        }
        out.sort();
        Ok(out)
    }
    fn run_step(&mut self, step: RestoreStep, rec: &Record) -> Result<(), String> {
        match step {
            RestoreStep::Accept => self.accept(rec),
            _ => Err(NOT_WIRED.to_owned()),
        }
    }
    fn put_scheduler_back(&mut self, rec: &Record) -> Result<bool, String> {
        let spec = crate::quarantinecmd::load_spec(&rec.site, &self.sites.to_string_lossy())?;
        crate::siteidentity::scheduler(&mut crate::sitecreatecmd::HostRunner, &spec)
    }
    fn discard(&mut self, _rec: &Record) -> Result<(), String> {
        Err(NOT_WIRED.to_owned())
    }
    fn roll_back(&mut self, _rec: &Record) -> Result<(), String> {
        Err(NOT_WIRED.to_owned())
    }
    fn move_aside(&mut self, rec: &Record) -> Result<(), String> {
        let (_, op) = rec
            .closed
            .as_ref()
            .ok_or("only a closed record is moved aside")?;
        let aside = self.records.join("closed");
        fs::create_dir_all(&aside).map_err(|e| format!("{}: {e}", aside.display()))?;
        let text = serde_json::to_string_pretty(&rec.to_json()).map_err(|e| e.to_string())?;
        write_whole(
            &aside.join(format!("{}-{op}.json", rec.site)),
            &(text + "\n"),
        )?;
        let active = self.records.join(format!("{}.json", rec.site));
        fs::remove_file(&active).map_err(|e| format!("{}: {e}", active.display()))?;
        fs::File::open(&self.records)
            .and_then(|d| d.sync_all())
            .map_err(|e| format!("{}: {e}", self.records.display()))
    }
}

impl HostRestorer {
    /// The accept step: the restored site against what the downloaded backup says it
    /// holds (`restore verify`'s reading and rules). A match goes on; a difference, or
    /// anything that cannot be told, is a failure, and a failure here keeps the site as it
    /// is for a person ([`restoresteps::after_failure`]): nothing is taken back
    /// automatically after the tidying (Andy 11-12 14:5x).
    fn accept(&mut self, rec: &Record) -> Result<(), String> {
        let name = rec
            .backup
            .as_ref()
            .map(|b| b.name.clone())
            .ok_or("the step record names no backup")?;
        let path = self.downloads.join(plain_name(&name)?);
        let file = fs::File::open(&path)
            .map_err(|e| format!("accept_unknown: {}: {e}", path.display()))?;
        let read = crate::restorecmd::read_archive(std::io::BufReader::new(file));
        let run = &mut self.run;
        let site = crate::restorecmd::ask_site(
            &mut |argv: &[String]| run.run(argv).ok(),
            self.ct,
            &self.code,
        );
        let (doc, rc) = crate::restorecmd::verify_doc(&self.code, &read, &site, ACCEPT_USERS_EXTRA);
        match rc {
            0 => Ok(()),
            1 => Err(format!("accept_differ: {}", doc["strict"])),
            _ => Err(format!("accept_unknown: {}", doc["reason"])),
        }
    }
}

/// What a plan file given to the command is taken for.
#[derive(Debug, Clone, Copy)]
enum Given {
    Steps(Mode),
    Close(Close),
}

fn print(v: &Value) -> Result<u8, String> {
    println!(
        "{}",
        serde_json::to_string_pretty(v).map_err(|e| e.to_string())?
    );
    Ok(0)
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut source: Option<String> = None;
    let mut close: Option<Close> = None;
    let mut plan_only = false;
    let mut file: Option<(Given, String)> = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        let given = match a.as_str() {
            "--plan" => {
                plan_only = true;
                continue;
            }
            "--from" => {
                source = Some(it.next().ok_or("--from needs the backup's id")?.clone());
                continue;
            }
            "--close" => {
                let w = it.next().ok_or("--close needs resolved or abandoned")?;
                close = Some(Close::parse(w).ok_or("--close is resolved or abandoned")?);
                continue;
            }
            "--apply" => Given::Steps(Mode::Apply),
            "--continue" => Given::Steps(Mode::Continue),
            "--resolved" => Given::Close(Close::Resolved),
            "--abandon" => Given::Close(Close::Abandoned),
            other => return Err(format!("unknown argument {other}")),
        };
        if file.is_some() {
            return Err("one of --apply, --continue, --resolved or --abandon".to_owned());
        }
        file = Some((
            given,
            it.next().ok_or(format!("{a} needs a plan file"))?.clone(),
        ));
    }
    let spec = crate::quarantinecmd::load_spec(&code, SITE_RECORDS)?;
    let mut host = HostRestorer {
        records: PathBuf::from(RECORDS),
        sites: PathBuf::from(SITE_RECORDS),
        ct: spec.ct,
        code: spec.code.clone(),
        plugins: PathBuf::from(PLUGINS_DIR),
        downloads: Path::new(DOWNLOADS_DIR).join(&spec.code),
        run: Box::new(RestoreRunner),
    };
    match (plan_only, file) {
        (true, None) => {
            let mut doc = match (source, close) {
                (Some(source), None) => {
                    if !restoresteps::valid_source_id(&source) {
                        return Err("--from is not a shared file's id".to_owned());
                    }
                    plan_doc(&spec, &source, &mut host)?
                }
                (None, Some(how)) => close_doc(&spec, how, &mut host)?,
                _ => return Err("--plan needs --from <the backup's id> or --close resolved|abandoned".to_owned()),
            };
            let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
            let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
            crate::quarantinecmd::seal_with_op(
                &mut doc,
                &op,
                &fingerprint,
                env!("CARGO_PKG_VERSION"),
            )?;
            print(&doc)
        }
        (false, Some((given, path))) => {
            if source.is_some() || close.is_some() {
                return Err("--from and --close belong to --plan: the plan given says them".to_owned());
            }
            let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
            let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
            let place = opstore::OpPlace::host()?;
            print(&match given {
                Given::Steps(mode) => apply(&place, &plan, &spec, mode, &mut host)?,
                Given::Close(how) => apply_close(&place, &plan, &spec, how, &mut host)?,
            })
        }
        (true, Some(_)) => Err("--plan, or a plan file to carry out, not both".to_owned()),
        (false, None) => Err(
            "needs --plan --from <id>, --plan --close resolved|abandoned, --apply, --continue, --resolved or --abandon <plan.json>"
                .to_owned(),
        ),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::op::State;
    use aib_cloud::site::spec_from_record;
    use std::collections::BTreeMap;

    const OP: &str = "20261111T130000Z-ab12cd34";
    const SOURCE: &str = "1AbCdEfGhIjKlMnOpQrStUv";

    fn spec() -> SiteSpec {
        spec_from_record(&json!({
            "site": "site9", "ct": 119, "ip": "10.77.0.19", "hosts": "www.example.com",
            "wp": 1, "locale": "en_US", "created": "2026-01-01T00:00:00Z", "template": 900,
            "limits": { "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 512 }
        }))
        .unwrap()
    }

    fn scratch(name: &str) -> PathBuf {
        let d =
            std::env::temp_dir().join(format!("aib-restoresteps-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn place(name: &str) -> opstore::OpPlace {
        let d = scratch(name);
        fs::write(d.join("boot_id"), "0f0e0d0c-0b0a-4908-8706-050403020100\n").unwrap();
        let me = d.join("proc").join(std::process::id().to_string());
        fs::create_dir_all(&me).unwrap();
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            me.join("stat"),
            format!(
                "{} (aib-cloud-host) {filler} 4242 0 0\n",
                std::process::id()
            ),
        )
        .unwrap();
        fs::create_dir_all(d.join("ops")).unwrap();
        opstore::OpPlace {
            ops_dir: d.join("ops"),
            lock: opstore::LockPaths {
                lock: d.join("host.lock"),
                proc_root: d.join("proc"),
                boot_id: d.join("boot_id"),
            },
            fingerprint: "f".repeat(64),
        }
    }

    /// A made-up host: records in memory, a step that fails on demand, every call noted.
    struct Fake {
        wired: bool,
        records: BTreeMap<String, Record>,
        up: Probe,
        fail_at: Option<RestoreStep>,
        undo_fails: bool,
        broken_other: bool,
        calls: Vec<String>,
        /// How many steps the stored record said were done when each step began.
        seen_done: Vec<usize>,
        /// Records moved aside.
        aside: Vec<Record>,
        source: Option<Source>,
        free: Option<u64>,
        db_up: Option<u64>,
        /// What is wrong with the download, when something is: "size" or "head".
        bad_download: Option<&'static str>,
        room: Option<SiteRoom>,
        /// What the plugin list prints after the update.
        active: String,
        /// What the backup listing prints; `None` lists the record's backup.
        listing: Option<String>,
        /// What the restore prints.
        said: String,
        /// The site's disk once the download is done, when it changed meanwhile.
        room_later: Option<SiteRoom>,
        siblings: Vec<String>,
        /// Putting the scheduler back fails.
        scheduler_fails: bool,
    }

    const NAME: &str = "example-com-20261101-101010-ab12cd.wpress";
    const SIZE: u64 = 2 * 1024 * 1024 * 1024;
    const SHA: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    fn wpress_head() -> Vec<u8> {
        let mut h = vec![0u8; restoregate::HEADER_LEN];
        h[..12].copy_from_slice(b"package.json");
        h[255..258].copy_from_slice(b"512");
        h[269..279].copy_from_slice(b"1730000000");
        h
    }

    fn fake() -> Fake {
        Fake {
            wired: true,
            records: BTreeMap::new(),
            up: Probe::Done,
            fail_at: None,
            undo_fails: false,
            broken_other: false,
            calls: Vec::new(),
            seen_done: Vec::new(),
            aside: Vec::new(),
            source: Some(Source {
                name: NAME.to_owned(),
                size: Some(SIZE),
                sharer: Sharer::Allowed,
            }),
            free: Some(4 * SIZE + restoregate::SPARE_BYTES),
            db_up: Some(3600),
            bad_download: None,
            room: Some(SiteRoom {
                free: 3 * SIZE + 4 * restoreimport::SITE_SPARE_BYTES,
                used: SIZE,
            }),
            active: restoreimport::IMPORT_PLUGINS.join("\n"),
            listing: None,
            said: format!("Restoring\n{}\n", restoreimport::RESTORE_COMPLETE),
            room_later: None,
            siblings: Vec::new(),
            scheduler_fails: false,
        }
    }

    impl RestoreHost for Fake {
        fn wired(&self) -> bool {
            self.wired
        }
        fn read_record(&mut self, site: &str) -> Result<Option<Record>, String> {
            Ok(self.records.get(site).cloned())
        }
        fn write_record(&mut self, rec: &Record) -> Result<(), String> {
            self.records.insert(rec.site.clone(), rec.clone());
            Ok(())
        }
        fn others(&mut self, site: &str) -> Result<Vec<Record>, String> {
            if self.broken_other {
                return Err("other.json: expected value".to_owned());
            }
            Ok(self
                .records
                .values()
                .filter(|r| r.site != site)
                .cloned()
                .collect())
        }
        fn site_up(&mut self) -> Probe {
            self.up
        }
        fn run_step(&mut self, step: RestoreStep, rec: &Record) -> Result<(), String> {
            self.calls.push(step.as_str().to_owned());
            let stored = self
                .records
                .get(&rec.site)
                .map_or(usize::MAX, |r| r.done.len());
            self.seen_done.push(stored);
            if self.fail_at == Some(step) {
                return Err("made-up failure".to_owned());
            }
            Ok(())
        }
        fn put_scheduler_back(&mut self, _rec: &Record) -> Result<bool, String> {
            self.calls.push("scheduler".to_owned());
            if self.scheduler_fails {
                return Err("cron_not_running: made-up".to_owned());
            }
            Ok(true)
        }
        fn discard(&mut self, _rec: &Record) -> Result<(), String> {
            self.calls.push("discard".to_owned());
            if self.undo_fails {
                return Err("made-up".to_owned());
            }
            Ok(())
        }
        fn roll_back(&mut self, _rec: &Record) -> Result<(), String> {
            self.calls.push("roll_back".to_owned());
            if self.undo_fails {
                return Err("made-up".to_owned());
            }
            Ok(())
        }
        fn move_aside(&mut self, rec: &Record) -> Result<(), String> {
            self.records.remove(&rec.site);
            self.aside.push(rec.clone());
            Ok(())
        }
        fn source(&mut self, _source: &str) -> Result<Option<Source>, String> {
            Ok(self.source.clone())
        }
        fn free_bytes(&mut self) -> Option<u64> {
            self.free
        }
        fn db_up_secs(&mut self) -> Option<u64> {
            self.db_up
        }
        fn downloaded(&mut self, rec: &Record) -> Result<Fetched, String> {
            self.calls.push("verify".to_owned());
            if let Some(later) = self.room_later.take() {
                self.room = Some(later);
            }
            let stored = self
                .records
                .get(&rec.site)
                .map_or(usize::MAX, |r| r.done.len());
            self.seen_done.push(stored);
            if self.fail_at == Some(RestoreStep::Verify) {
                return Err("made-up failure".to_owned());
            }
            let mut got = Fetched {
                size: SIZE,
                head: wpress_head(),
                sha256: SHA.to_owned(),
            };
            match self.bad_download {
                Some("size") => got.size -= 1,
                Some("head") => got.head[..4].copy_from_slice(b"PK\x03\x04"),
                _ => {}
            }
            Ok(got)
        }
        fn site_room(&mut self) -> Option<SiteRoom> {
            self.room
        }
        fn update_import_plugins(&mut self, _rec: &Record) -> Result<String, String> {
            self.calls.push("update_plugins".to_owned());
            Ok(self.active.clone())
        }
        fn place_backup(&mut self, rec: &Record) -> Result<String, String> {
            self.calls.push("place".to_owned());
            Ok(self.listing.clone().unwrap_or_else(|| {
                format!(
                    "| {} | today |",
                    rec.backup.as_ref().map_or("", |b| b.name.as_str())
                )
            }))
        }
        fn sibling_domains(&mut self, _site: &str) -> Result<Vec<String>, String> {
            Ok(self.siblings.clone())
        }
        fn import(&mut self, rec: &Record) -> Result<String, String> {
            self.calls.push("import".to_owned());
            let stored = self
                .records
                .get(&rec.site)
                .map_or(usize::MAX, |r| r.done.len());
            self.seen_done.push(stored);
            if self.fail_at == Some(RestoreStep::Import) {
                return Err("made-up failure".to_owned());
            }
            Ok(self.said.clone())
        }
    }

    fn sealed(place: &opstore::OpPlace, host: &mut Fake) -> Value {
        let mut doc = plan_doc(&spec(), SOURCE, host).unwrap();
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &place.fingerprint,
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        doc
    }

    fn states(place: &opstore::OpPlace) -> Vec<State> {
        match opstore::read_journal(&opstore::journal_path(&place.ops_dir, OP).unwrap()) {
            Ok((entries, _)) => entries.iter().map(|e| e.state).collect(),
            Err(_) => Vec::new(),
        }
    }

    fn record(
        site: &str,
        domain: &str,
        done: &[RestoreStep],
        failed: Option<RestoreStep>,
        cleaned_up: bool,
    ) -> Record {
        Record {
            site: site.to_owned(),
            domain: domain.to_owned(),
            source: SOURCE.to_owned(),
            op: "20261111T100000Z-00000000".to_owned(),
            done: done.to_vec(),
            failed: failed.map(|s| (s, "earlier".to_owned())),
            cleaned_up,
            closed: None,
            backup: None,
        }
    }

    fn sealed_close(place: &opstore::OpPlace, how: Close, host: &mut Fake) -> Value {
        let mut doc = close_doc(&spec(), how, host).unwrap();
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &place.fingerprint,
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        doc
    }

    fn kept() -> Record {
        record(
            "site9",
            "www.example.com",
            &STEPS[..5],
            Some(RestoreStep::Accept),
            false,
        )
    }

    fn interrupted() -> Record {
        record(
            "site9",
            "www.example.com",
            &STEPS[..3],
            Some(RestoreStep::Import),
            false,
        )
    }

    fn mine(h: &Fake) -> &Record {
        h.records.get("site9").unwrap()
    }

    #[test]
    fn a_record_reads_back_as_it_was_written() {
        let r = record(
            "site9",
            "www.example.com",
            &STEPS[..4],
            Some(RestoreStep::Tidy),
            false,
        );
        assert_eq!(Record::from_json(&r.to_json()), Ok(r));
        let d = scratch("record");
        let mut host = host_at(d.join("restores"), d.join("sites"), 119);
        let r = record("site9", "www.example.com", &STEPS[..2], None, false);
        host.write_record(&r).unwrap();
        assert_eq!(host.read_record("site9"), Ok(Some(r)));
        assert_eq!(host.read_record("site8"), Ok(None));
        let names: Vec<_> = fs::read_dir(d.join("restores"))
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names, ["site9.json"], "nothing but the record is left");
    }

    #[test]
    fn a_restore_runs_every_step_and_writes_each_down() {
        let p = place("all");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        assert_eq!(plan["next"], "start");
        let done = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap();
        assert_eq!(done["state"], "ready_to_switch");
        assert_eq!(mine(&h).done, STEPS);
        assert_eq!(
            states(&p),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Verified
            ]
        );
    }

    #[test]
    fn every_step_is_written_down_as_it_is_done() {
        let p = place("tidy");
        let mut h = fake();
        h.fail_at = Some(RestoreStep::Tidy);
        let plan = sealed(&p, &mut h);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("tidy failed"), "{err}");
        // each step began with the steps before it already in the stored record
        assert_eq!(h.seen_done, [0, 1, 2, 3, 4]);
        assert_eq!(mine(&h).done, STEPS[..4]);
        assert_eq!(
            mine(&h).failed.as_ref().map(|f| f.0),
            Some(RestoreStep::Tidy)
        );
        assert_eq!(mine(&h).standing(), Ok(Standing::Kept(RestoreStep::Tidy)));
    }

    #[test]
    fn a_failure_from_the_tidying_on_keeps_the_site() {
        let p = place("keep");
        let mut h = fake();
        h.fail_at = Some(RestoreStep::Accept);
        let plan = sealed(&p, &mut h);
        apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(
            !h.calls.iter().any(|c| c == "discard" || c == "roll_back"),
            "{:?}",
            h.calls
        );
        assert_eq!(states(&p).last(), Some(&State::Failed));
    }

    #[test]
    fn a_failed_import_takes_the_site_back() {
        let p = place("import");
        let mut h = fake();
        h.fail_at = Some(RestoreStep::Import);
        let plan = sealed(&p, &mut h);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("taken back"), "{err}");
        assert_eq!(h.calls.last().map(String::as_str), Some("roll_back"));
        assert_eq!(mine(&h).standing(), Ok(Standing::RolledBack));
    }

    #[test]
    fn a_failure_before_the_import_throws_the_download_away() {
        let p = place("verify");
        let mut h = fake();
        h.fail_at = Some(RestoreStep::Verify);
        let plan = sealed(&p, &mut h);
        apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert_eq!(h.calls, ["download", "verify", "discard"]);
        assert_eq!(mine(&h).standing(), Ok(Standing::Discarded));
    }

    #[test]
    fn a_clean_up_that_fails_leaves_an_interruption() {
        let p = place("undo");
        let mut h = fake();
        h.fail_at = Some(RestoreStep::Import);
        h.undo_fails = true;
        let plan = sealed(&p, &mut h);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("a person looks"), "{err}");
        assert_eq!(
            mine(&h).standing(),
            Ok(Standing::Interrupted(RestoreStep::Import))
        );
    }

    #[test]
    fn a_restore_goes_on_from_the_step_that_failed() {
        let p = place("goon");
        let mut h = fake();
        h.records.insert(
            "site9".to_owned(),
            record(
                "site9",
                "www.example.com",
                &STEPS[..5],
                Some(RestoreStep::Accept),
                false,
            ),
        );
        let plan = sealed(&p, &mut h);
        assert_eq!(
            (plan["next"].as_str(), plan["from"].as_str()),
            (Some("go_on"), Some("accept"))
        );
        apply(&p, &plan, &spec(), Mode::Continue, &mut h).unwrap();
        assert_eq!(h.calls, ["accept", "ready_to_switch"]);
        assert_eq!(mine(&h).done, STEPS);
        assert_eq!(mine(&h).op, OP);
    }

    #[test]
    fn apply_takes_a_start_plan_and_continue_a_go_on_plan() {
        let p = place("modes");
        let mut h = fake();
        let start = sealed(&p, &mut h);
        let err = apply(&p, &start, &spec(), Mode::Continue, &mut h).unwrap_err();
        assert!(err.contains("--continue takes"), "{err}");
        assert!(h.calls.is_empty());
        h.records.insert(
            "site9".to_owned(),
            record(
                "site9",
                "www.example.com",
                &STEPS[..5],
                Some(RestoreStep::Accept),
                false,
            ),
        );
        let go_on = sealed(&p, &mut h);
        let err = apply(&p, &go_on, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("--apply takes"), "{err}");
        assert!(h.calls.is_empty());
        assert!(states(&p).is_empty(), "nothing journaled");
    }

    #[test]
    fn a_plan_that_no_longer_matches_is_refused() {
        let p = place("changed");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        // another site's restore of the same domain is left half way after the plan
        h.records.insert(
            "site8".to_owned(),
            record(
                "site8",
                "example.com",
                &STEPS[..4],
                Some(RestoreStep::Tidy),
                false,
            ),
        );
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("plan_changed"), "{err}");
        assert!(h.calls.is_empty());
        assert!(states(&p).is_empty());
    }

    #[test]
    fn another_sites_restore_of_the_domain_stops_the_plan() {
        let mut h = fake();
        h.records.insert(
            "site8".to_owned(),
            record(
                "site8",
                "EXAMPLE.com.",
                &STEPS[..4],
                Some(RestoreStep::Tidy),
                false,
            ),
        );
        let doc = plan_doc(&spec(), SOURCE, &mut h).unwrap();
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["stop"], "restore_in_progress_for_domain");
    }

    #[test]
    fn a_damaged_record_of_another_site_refuses_every_restore() {
        let mut h = fake();
        h.broken_other = true;
        assert!(plan_doc(&spec(), SOURCE, &mut h).is_err());
        let d = scratch("damaged");
        let records = d.join("restores");
        fs::create_dir_all(&records).unwrap();
        fs::write(records.join("site8.json"), "{not json").unwrap();
        let mut host = host_at(records, PathBuf::new(), 119);
        assert!(host.others("site9").is_err());
    }

    #[test]
    fn nothing_runs_until_the_steps_are_wired() {
        let p = place("unwired");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        h.wired = false;
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.starts_with("not_wired"), "{err}");
        assert!(h.calls.is_empty());
        assert!(states(&p).is_empty(), "nothing journaled");
    }

    #[test]
    fn a_target_that_is_down_takes_no_restore() {
        let mut h = fake();
        h.up = Probe::Unknown;
        let doc = plan_doc(&spec(), SOURCE, &mut h).unwrap();
        assert_eq!(
            (doc["next"].as_str(), doc["stop"].as_str()),
            (Some("stop"), Some("target_site_unknown"))
        );
    }

    #[test]
    fn a_kept_restore_goes_on_only_with_its_own_backup() {
        let mut h = fake();
        let mut r = record(
            "site9",
            "www.example.com",
            &STEPS[..5],
            Some(RestoreStep::Accept),
            false,
        );
        r.source = "1ZzZzZzZzZzZzZzZzZzZz".to_owned();
        h.records.insert("site9".to_owned(), r);
        let doc = plan_doc(&spec(), SOURCE, &mut h).unwrap();
        assert_eq!(doc["stop"], "source_differs");
    }

    #[test]
    fn a_record_in_another_sites_file_is_refused() {
        let d = scratch("misfiled");
        let records = d.join("restores");
        fs::create_dir_all(&records).unwrap();
        let r = record("site9", "www.example.com", &STEPS[..2], None, false);
        fs::write(records.join("site8.json"), r.to_json().to_string()).unwrap();
        let mut host = host_at(records, PathBuf::new(), 119);
        let err = host.read_record("site8").unwrap_err();
        assert!(err.contains("is of site9, not of site8"), "{err}");
        assert!(host.others("site7").is_err());
    }

    #[test]
    fn a_kept_restore_a_person_resolved_lets_a_new_one_start() {
        let p = place("resolve");
        let mut h = fake();
        h.records.insert("site9".to_owned(), kept());
        assert_eq!(
            plan_doc(&spec(), "1ZzZzZzZzZzZzZzZzZzZz", &mut h).unwrap()["stop"],
            "source_differs"
        );
        let plan = sealed_close(&p, Close::Resolved, &mut h);
        assert_eq!(
            (plan["next"].as_str(), plan["last"].as_str()),
            (Some("close"), Some("kept:accept"))
        );
        let done = apply_close(&p, &plan, &spec(), Close::Resolved, &mut h).unwrap();
        assert_eq!(done["closed"], "resolved");
        assert_eq!(mine(&h).closed, Some((Close::Resolved, OP.to_owned())));
        assert_eq!(mine(&h).standing(), Ok(Standing::Resolved));
        let doc = plan_doc(&spec(), "1ZzZzZzZzZzZzZzZzZzZz", &mut h).unwrap();
        assert_eq!(doc["next"], "start");
        assert_eq!(states(&p).last(), Some(&State::Verified));
        assert!(h.calls.is_empty(), "nothing is run on the site");
    }

    #[test]
    fn an_abandoned_restore_no_longer_holds_the_host() {
        let p = place("abandon");
        let mut h = fake();
        h.records.insert("site9".to_owned(), interrupted());
        let mut other = interrupted();
        other.site = "site8".to_owned();
        other.domain = "other.example".to_owned();
        assert!(
            restoresteps::admit(
                "site8",
                "other.example",
                &[Other {
                    site: "site9",
                    domain: "www.example.com",
                    standing: interrupted().standing().unwrap(),
                }]
            )
            .is_err(),
            "before: the interruption holds the host"
        );
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "interrupted_before_tidy"
        );
        let plan = sealed_close(&p, Close::Abandoned, &mut h);
        apply_close(&p, &plan, &spec(), Close::Abandoned, &mut h).unwrap();
        assert!(
            h.read_record("site9").unwrap().is_none(),
            "the record left its place"
        );
        assert_eq!(
            h.aside.iter().map(|r| r.closed.clone()).collect::<Vec<_>>(),
            [Some((Close::Abandoned, OP.to_owned()))]
        );
        h.records.insert("site8".to_owned(), other);
        assert!(
            h.others("site8").unwrap().is_empty(),
            "nothing holds the host now"
        );
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "restore_unfinished_on_host"
        );
        h.records.remove("site8");
        assert_eq!(plan_doc(&spec(), SOURCE, &mut h).unwrap()["next"], "start");
    }

    #[test]
    fn an_abandoned_record_is_kept_aside_on_the_host() {
        let d = scratch("aside");
        let mut host = host_at(d.join("restores"), d.join("sites"), 119);
        let mut r = interrupted();
        host.write_record(&r).unwrap();
        r.closed = Some((Close::Abandoned, OP.to_owned()));
        host.move_aside(&r).unwrap();
        assert_eq!(host.read_record("site9"), Ok(None));
        let kept = d
            .join("restores")
            .join("closed")
            .join(format!("site9-{OP}.json"));
        let back: Value = serde_json::from_str(&fs::read_to_string(kept).unwrap()).unwrap();
        assert_eq!(Record::from_json(&back), Ok(r));
        assert_eq!(
            host.others("site8"),
            Ok(Vec::new()),
            "the closed folder is no record"
        );
    }

    #[test]
    fn only_a_restore_that_is_not_over_is_closed_on_the_host() {
        let p = place("nothing");
        let mut h = fake();
        let none = sealed_close(&p, Close::Resolved, &mut h);
        assert_eq!(
            (none["next"].as_str(), none["stop"].as_str()),
            (Some("stop"), Some("nothing_to_close"))
        );
        h.records.insert(
            "site9".to_owned(),
            record("site9", "www.example.com", &STEPS, None, false),
        );
        let plan = sealed_close(&p, Close::Resolved, &mut h);
        assert_eq!(plan["stop"], "nothing_to_close");
        let err = apply_close(&p, &plan, &spec(), Close::Resolved, &mut h).unwrap_err();
        assert!(err.contains("nothing to close"), "{err}");
        assert_eq!(mine(&h).closed, None);
        assert!(states(&p).is_empty(), "nothing journaled");
    }

    #[test]
    fn a_close_plan_that_no_longer_matches_is_refused() {
        let p = place("closechanged");
        let mut h = fake();
        h.records.insert("site9".to_owned(), kept());
        let plan = sealed_close(&p, Close::Resolved, &mut h);
        // the restore went on (and stopped again) after the plan was made
        let mut later = kept();
        later.op = "20261111T120000Z-11111111".to_owned();
        h.records.insert("site9".to_owned(), later);
        let err = apply_close(&p, &plan, &spec(), Close::Resolved, &mut h).unwrap_err();
        assert!(err.contains("plan_changed"), "{err}");
        assert_eq!(mine(&h).closed, None);
        assert!(states(&p).is_empty());
    }

    #[test]
    fn resolved_takes_only_a_plan_that_resolves() {
        let p = place("closehow");
        let mut h = fake();
        h.records.insert("site9".to_owned(), kept());
        let abandon = sealed_close(&p, Close::Abandoned, &mut h);
        let err = apply_close(&p, &abandon, &spec(), Close::Resolved, &mut h).unwrap_err();
        assert!(err.contains("--resolved takes"), "{err}");
        let resolve = sealed_close(&p, Close::Resolved, &mut h);
        let err = apply_close(&p, &resolve, &spec(), Close::Abandoned, &mut h).unwrap_err();
        assert!(err.contains("--abandon takes"), "{err}");
        assert_eq!(mine(&h).closed, None);
        assert!(h.aside.is_empty());
        assert!(states(&p).is_empty());
    }

    #[test]
    fn the_download_gate_stops_the_plan() {
        let mut h = fake();
        let doc = plan_doc(&spec(), SOURCE, &mut h).unwrap();
        assert_eq!(doc["next"], "start");
        assert_eq!(doc["backup"], json!({ "name": NAME, "size": SIZE }));
        h.source = None;
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "source_unknown"
        );
        h.source = Some(Source {
            name: NAME.to_owned(),
            size: Some(SIZE),
            sharer: Sharer::Refused("share_address_not_on_roster"),
        });
        let doc = plan_doc(&spec(), SOURCE, &mut h).unwrap();
        assert_eq!(
            (
                doc["next"].as_str(),
                doc["stop"].as_str(),
                doc["backup"].is_null()
            ),
            (Some("stop"), Some("share_address_not_on_roster"), true)
        );
        h = fake();
        h.free = Some(1);
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "disk_too_small"
        );
        h = fake();
        h.db_up = Some(10);
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "database_restarted_lately"
        );
    }

    #[test]
    fn a_backup_changed_at_the_share_after_the_plan_is_refused() {
        let p = place("sharechanged");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        h.source.as_mut().unwrap().size = Some(SIZE + 1);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("plan_changed"), "{err}");
        assert!(h.calls.is_empty());
    }

    #[test]
    fn the_downloads_hash_is_written_down() {
        let p = place("hash");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap();
        assert_eq!(
            mine(&h).backup,
            Some(Backup {
                name: NAME.to_owned(),
                size: SIZE,
                sha256: Some(SHA.to_owned()),
            })
        );
        let r = mine(&h).clone();
        assert_eq!(Record::from_json(&r.to_json()), Ok(r));
    }

    #[test]
    fn a_download_that_is_not_what_the_share_said_is_thrown_away() {
        for (bad, word) in [("size", "download_size_differs"), ("head", "not_a_wpress")] {
            let p = place(&format!("bad{bad}"));
            let mut h = fake();
            h.bad_download = Some(bad);
            let plan = sealed(&p, &mut h);
            let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
            assert!(err.contains(word), "{err}");
            assert_eq!(h.calls, ["download", "verify", "discard"], "{bad}");
            assert_eq!(mine(&h).standing(), Ok(Standing::Discarded));
            assert_eq!(mine(&h).backup.as_ref().unwrap().sha256, None, "{bad}");
        }
    }

    #[test]
    fn the_hosts_free_room_and_database_uptime_are_read() {
        let df = "Filesystem     1024-blocks      Used Available Capacity Mounted on\n/dev/md2        937276536 109847516 779745496      13% /\n";
        assert_eq!(df_avail_bytes(df), Some(779_745_496 * 1024));
        assert_eq!(
            df_avail_bytes("Filesystem 1024-blocks Used Available\n"),
            None
        );
        assert_eq!(df_avail_bytes(&format!("{df}/dev/md3 1 1 1 1% /x\n")), None);
        assert_eq!(
            df_avail_bytes("/dev/md2 937276536 109847516 779745496 13% /\n"),
            None
        );
        assert_eq!(
            db_uptime("Uptime: 86412  Threads: 1  Questions: 25  Slow queries: 0\n"),
            Some(86412)
        );
        assert_eq!(
            db_uptime("mysqladmin: connect to server at 'localhost' failed\n"),
            None
        );
        assert_eq!(db_uptime("Uptime: soon"), None);
    }

    #[test]
    fn a_site_disk_too_small_for_the_import_stops_the_plan() {
        let mut h = fake();
        h.room = Some(SiteRoom {
            free: 3 * SIZE,
            used: SIZE,
        });
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "site_disk_too_small"
        );
        h.room = None;
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "site_disk_unknown"
        );
    }

    #[test]
    fn the_site_disk_is_asked_again_before_the_restore_point() {
        let p = place("roomlater");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        // the site grew while the backup was downloading
        h.room_later = Some(SiteRoom {
            free: 3 * SIZE,
            used: SIZE,
        });
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(
            err.contains("restore_point failed: site_disk_too_small"),
            "{err}"
        );
        assert_eq!(h.calls, ["download", "verify", "discard"]);
        assert_eq!(mine(&h).standing(), Ok(Standing::Discarded));
    }

    #[test]
    fn an_import_runs_on_plugins_up_to_date_with_the_backup_listed() {
        let p = place("import-ok");
        let mut h = fake();
        let plan = sealed(&p, &mut h);
        apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap();
        assert_eq!(
            h.calls,
            [
                "download",
                "verify",
                "restore_point",
                "update_plugins",
                "place",
                "import",
                "scheduler",
                "tidy",
                "accept",
                "ready_to_switch"
            ]
        );
    }

    #[test]
    fn a_restore_puts_the_scheduler_back_before_tidying_and_keeps_the_site_if_it_cannot() {
        let p = place("scheduler");
        let mut h = fake();
        h.scheduler_fails = true;
        let plan = sealed(&p, &mut h);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("tidy failed"), "{err}");
        // the import stays: no tidying, no throwing away, no taking back
        assert_eq!(h.calls.last().map(String::as_str), Some("scheduler"));
        assert!(
            !h.calls
                .iter()
                .any(|c| c == "tidy" || c == "discard" || c == "roll_back"),
            "{:?}",
            h.calls
        );
        assert_eq!(mine(&h).done, STEPS[..4]);
        assert_eq!(mine(&h).standing(), Ok(Standing::Kept(RestoreStep::Tidy)));
    }

    #[test]
    fn an_import_without_its_plugins_or_its_listing_is_taken_back_unrun() {
        for (what, word) in [
            ("plugins", "import_plugin_missing"),
            ("listing", "backup_not_visible"),
        ] {
            let p = place(&format!("import-{what}"));
            let mut h = fake();
            match what {
                "plugins" => h.active = restoreimport::IMPORT_PLUGINS[0].to_owned(),
                _ => h.listing = Some("No backups found.".to_owned()),
            }
            let plan = sealed(&p, &mut h);
            let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
            assert!(err.contains(word), "{err}");
            assert!(
                !h.calls.iter().any(|c| c == "import"),
                "{what}: {:?}",
                h.calls
            );
            assert_eq!(h.calls.last().map(String::as_str), Some("roll_back"));
            assert_eq!(mine(&h).standing(), Ok(Standing::RolledBack));
        }
    }

    #[test]
    fn an_import_on_a_site_in_another_language_goes_on() {
        // c477a slice 4 L1: a zh_TW site prints the plugin's words in zh_TW
        let p = place("import-zh");
        let mut h = fake();
        h.said = "正在還原備份…\nSuccess: 還原完成。\n".to_owned();
        let plan = sealed(&p, &mut h);
        apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap();
        assert_eq!(mine(&h).done, STEPS);
        assert!(!h.calls.iter().any(|c| c == "roll_back"), "{:?}", h.calls);
    }

    #[test]
    fn an_import_the_plugin_did_not_call_complete_is_taken_back() {
        let p = place("import-said");
        let mut h = fake();
        h.said = "Restoring\nError: could not open the archive\n".to_owned();
        let plan = sealed(&p, &mut h);
        let err = apply(&p, &plan, &spec(), Mode::Apply, &mut h).unwrap_err();
        assert!(err.contains("import_not_confirmed"), "{err}");
        assert_eq!(h.calls.last().map(String::as_str), Some("roll_back"));
        assert_eq!(mine(&h).done, STEPS[..3]);
    }

    #[test]
    fn the_sites_disk_is_read_from_df_in_its_container() {
        let df = "Filesystem     1024-blocks    Used Available Capacity Mounted on\n/dev/mapper/pve-vm--119--disk--0 16337788 4194304 11288380 28% /\n";
        assert_eq!(
            df_room(df),
            Some(SiteRoom {
                free: 11_288_380 * 1024,
                used: 4_194_304 * 1024,
            })
        );
        assert_eq!(
            df_room("Dateisystem 1K-Blöcke Benutzt Verfügbar\n/dev/x 1 2 3 1% /\n"),
            None
        );
    }

    #[test]
    fn a_backup_of_another_site_of_the_customer_is_refused() {
        let mut h = fake();
        h.source.as_mut().unwrap().name = "example-shop-20261101-101010-ab12cd.wpress".to_owned();
        // the second level lets it through for www.example.com ...
        assert_eq!(plan_doc(&spec(), SOURCE, &mut h).unwrap()["next"], "start");
        // ... unless it is the name of the customer's other site
        h.siblings = vec!["www.example.shop".to_owned()];
        assert_eq!(
            plan_doc(&spec(), SOURCE, &mut h).unwrap()["stop"],
            "backup_of_another_site"
        );
    }

    #[test]
    fn the_customers_other_sites_are_read_from_the_site_records() {
        let d = scratch("siblings");
        let sites = d.join("sites");
        fs::create_dir_all(&sites).unwrap();
        let rec = |code: &str, ct: u32, hosts: &str, tenant: Option<&str>| {
            let mut v = json!({
                "site": code, "ct": ct, "ip": format!("10.77.0.{}", ct - 100), "hosts": hosts,
                "wp": 1, "locale": "en_US", "created": "2026-01-01T00:00:00Z", "template": 900,
                "limits": { "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 512 }
            });
            if let Some(t) = tenant {
                v["tenant_id"] = json!(t);
            }
            fs::write(sites.join(format!("{code}.json")), v.to_string()).unwrap();
        };
        rec("site9", 119, "www.example.com", Some("t1"));
        rec("site8", 118, "example.shop,shop.example.net", Some("t1"));
        rec("site7", 117, "other.shop", Some("t2"));
        rec("site6", 116, "nobody.shop", None);
        let mut host = host_at(d.join("restores"), sites.clone(), 119);
        assert_eq!(
            host.sibling_domains("site9"),
            Ok(vec![
                "example.shop".to_owned(),
                "shop.example.net".to_owned()
            ])
        );
        assert_eq!(
            host.sibling_domains("site6"),
            Ok(Vec::new()),
            "no customer, no siblings"
        );
        fs::write(sites.join("site5.json"), "{not json").unwrap();
        assert!(host.sibling_domains("site9").is_err());
    }

    // ------------------------------------------------------------ B3 slice 5: the wiring

    type Asked = std::rc::Rc<std::cell::RefCell<Vec<String>>>;

    /// A host whose commands are answered by `answer` from the command line, each one kept.
    type Answer = Box<dyn FnMut(&str) -> Result<String, String>>;

    struct Script {
        asked: Asked,
        answer: Answer,
    }

    impl crate::sitecreatecmd::Runner for Script {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.asked.borrow_mut().push(line.clone());
            (self.answer)(&line)
        }
    }

    fn scripted(
        answer: impl FnMut(&str) -> Result<String, String> + 'static,
    ) -> (Box<dyn crate::sitecreatecmd::Runner>, Asked) {
        let asked = Asked::default();
        (
            Box::new(Script {
                asked: asked.clone(),
                answer: Box::new(answer),
            }),
            asked,
        )
    }

    fn host_at(records: PathBuf, sites: PathBuf, ct: u32) -> HostRestorer {
        HostRestorer {
            records,
            sites,
            ct,
            code: "site9".to_owned(),
            plugins: PathBuf::new(),
            downloads: PathBuf::new(),
            run: scripted(|_| Err("no command in this test".to_owned())).0,
        }
    }

    const ZIP_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const ZIP_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const ACTIVE: &str =
        "akismet\nall-in-one-wp-migration\nall-in-one-wp-migration-unlimited-extension\n";

    /// A host with the plugin zips summed and a downloaded backup, answering as a site
    /// whose language is zh_TW does.
    fn wired_host(
        name: &str,
        answer: impl FnMut(&str) -> Result<String, String> + 'static,
    ) -> (HostRestorer, Asked, PathBuf) {
        let d = scratch(name);
        let plugins = d.join("wp-plugins");
        let downloads = d.join("restores-dl").join("site9");
        fs::create_dir_all(&plugins).unwrap();
        fs::create_dir_all(&downloads).unwrap();
        fs::write(
            plugins.join("SHA256SUMS"),
            format!("{ZIP_A}  all-in-one-wp-migration.zip\n{ZIP_B} *all-in-one-wp-migration-unlimited-extension.zip\n"),
        )
        .unwrap();
        fs::write(downloads.join(NAME), crate::restorecmd::tests::good()).unwrap();
        let (run, asked) = scripted(answer);
        let host = HostRestorer {
            records: d.join("restores"),
            sites: d.join("sites"),
            ct: 119,
            code: "site9".to_owned(),
            plugins,
            downloads,
            run,
        };
        (host, asked, d)
    }

    fn with_backup(done: &[RestoreStep]) -> Record {
        Record {
            backup: Some(Backup {
                name: NAME.to_owned(),
                size: 4096,
                sha256: Some(SHA.to_owned()),
            }),
            ..record("site9", "www.example.com", done, None, false)
        }
    }

    /// What a zh_TW site answers: the plugin's words are Chinese, WP-CLI's prefixes are not.
    fn zh_tw(line: &str) -> Result<String, String> {
        Ok(if line.starts_with("sha256sum ") {
            let hex = if line.ends_with("unlimited-extension.zip") {
                ZIP_B
            } else {
                ZIP_A
            };
            format!("{hex}  {}\n", line.rsplit(' ').next().unwrap())
        } else if line.contains(" plugin install ") {
            "正在安裝外掛…\nSuccess: Installed 1 of 1 plugins.\n".to_owned()
        } else if line.contains(" plugin list --status=active ") {
            ACTIVE.to_owned()
        } else if line.ends_with(" ai1wm list-backups") {
            format!("+------+\n| {NAME} | 2026-11-01 | 4 KB |\n+------+\n")
        } else if line.contains(" ai1wm restore ") {
            "正在還原備份…\nSuccess: 還原完成。\n".to_owned()
        } else {
            String::new()
        })
    }

    #[test]
    fn the_import_runs_these_commands_word_for_word() {
        let (mut host, asked, d) = wired_host("wire", zh_tw);
        let rec = with_backup(&STEPS[..3]);
        // a zh_TW site: WP-CLI's Success: decides, the plugin's words are not read
        assert_eq!(import_backup(&mut host, &rec), Ok(()));
        let p = d.join("wp-plugins");
        let dl = d.join("restores-dl").join("site9");
        let wp = "pct exec 119 -- sudo -u site9 env LC_ALL=C LANG=C LANGUAGE=C wp --path=/var/www/site9 --no-color";
        let mut want = vec![
            format!(
                "sha256sum -- {}",
                p.join("all-in-one-wp-migration.zip").display()
            ),
            format!(
                "sha256sum -- {}",
                p.join("all-in-one-wp-migration-unlimited-extension.zip")
                    .display()
            ),
        ];
        for plugin in restoreimport::IMPORT_PLUGINS {
            let inside = format!("/tmp/ez2-{plugin}.zip");
            want.push(format!(
                "pct push 119 {} {inside} --user site9 --group site9 --perms 0600",
                p.join(format!("{plugin}.zip")).display()
            ));
            want.push(format!("{wp} plugin install {inside} --force --activate"));
            want.push(format!("pct exec 119 -- rm -f -- {inside}"));
        }
        want.push(format!("{wp} plugin list --status=active --field=name"));
        want.push(format!(
            "pct push 119 {} /var/www/site9/wp-content/ai1wm-backups/{NAME} --user site9 --group site9 --perms 0640",
            dl.join(NAME).display()
        ));
        want.push(format!("{wp} ai1wm list-backups"));
        want.push(format!("{wp} ai1wm restore {NAME} --yes"));
        assert_eq!(*asked.borrow(), want);
        // nothing of the vendor's is reached from the container
        assert!(asked.borrow().iter().all(|l| !l.contains("http")));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_plugin_zip_not_as_summed_is_not_installed() {
        let (mut host, asked, d) = wired_host("wiresha", |line| {
            if line.starts_with("sha256sum ") && line.ends_with("unlimited-extension.zip") {
                Ok(format!("{ZIP_A}  x\n"))
            } else {
                zh_tw(line)
            }
        });
        let rec = with_backup(&STEPS[..3]);
        let err = host.update_import_plugins(&rec).unwrap_err();
        assert!(err.starts_with("import_plugin_sha_mismatch"), "{err}");
        assert!(
            asked.borrow().iter().all(|l| l.starts_with("sha256sum ")),
            "{:?}",
            asked.borrow()
        );
        // a zip SHA256SUMS does not name is not installed either
        fs::write(
            d.join("wp-plugins").join("SHA256SUMS"),
            format!("{ZIP_A}  all-in-one-wp-migration.zip\n"),
        )
        .unwrap();
        let err = host.update_import_plugins(&rec).unwrap_err();
        assert!(err.starts_with("import_plugin_source"), "{err}");
        assert!(asked.borrow().iter().all(|l| !l.starts_with("pct ")));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn an_import_that_does_not_end_with_0_fails_and_is_rolled_back() {
        // it printed Success: and still ended with 1: an error
        let (mut host, _asked, d) = wired_host("wirerc", |line| {
            if line.contains(" ai1wm restore ") {
                Err("\"pct\" exited exit status: 1".to_owned())
            } else {
                zh_tw(line)
            }
        });
        let rec = with_backup(&STEPS[..3]);
        assert!(host.import(&rec).is_err());
        assert!(import_backup(&mut host, &rec).is_err());
        assert_eq!(
            restoresteps::after_failure(RestoreStep::Import),
            AfterFailure::RollBack
        );
        // carried out: the failure is written down and the roll-back is asked for
        let err = carry_out(&mut host, rec, RestoreStep::Import).unwrap_err();
        assert!(err.starts_with("import failed:"), "{err}");
        assert!(err.contains("cleaning up after it failed too"), "{err}");
        let kept = host.read_record("site9").unwrap().unwrap();
        assert_eq!(kept.failed.map(|(s, _)| s), Some(RestoreStep::Import));
        let _ = fs::remove_dir_all(&d);
    }

    /// The restored site's answers to `restore verify`'s questions, with these counts.
    fn site_says(counts: &'static str) -> impl FnMut(&str) -> Result<String, String> {
        move |line| {
            Ok(if line.contains(" eval ") {
                counts.to_owned()
            } else if line.contains(" plugin list --field=name") {
                "x\nakismet\n".to_owned()
            } else if line.contains(" theme list --field=name") {
                "twentytwentyfive\n".to_owned()
            } else if line.contains(" sh -c ") {
                crate::restorecmd::tests::NO_UPLOADS.to_owned()
            } else {
                return Err(format!("not asked in this test: {line}"));
            })
        }
    }

    #[test]
    fn an_accept_that_differs_keeps_the_site() {
        // the site holds what the backup says: accepted
        let (mut host, _asked, d) =
            wired_host("acceptok", site_says(crate::restorecmd::tests::SAME));
        let rec = with_backup(&STEPS[..5]);
        assert_eq!(host.run_step(RestoreStep::Accept, &rec), Ok(()));
        let _ = fs::remove_dir_all(&d);
        // a page lost: a difference, the site kept as it is for a person
        let lost = "P\tpost\tpublish\t1\nU\t1\nEND\n";
        let (mut host, asked, d) = wired_host("acceptdiffer", site_says(lost));
        let err = host.run_step(RestoreStep::Accept, &rec).unwrap_err();
        assert!(err.starts_with("accept_differ"), "{err}");
        assert_eq!(
            restoresteps::after_failure(RestoreStep::Accept),
            AfterFailure::Keep
        );
        let err = carry_out(&mut host, rec.clone(), RestoreStep::Accept).unwrap_err();
        assert!(err.contains("kept as it is for a person"), "{err}");
        let kept = host.read_record("site9").unwrap().unwrap();
        assert_eq!(kept.failed.map(|(s, _)| s), Some(RestoreStep::Accept));
        assert!(!kept.cleaned_up);
        // nothing was taken back, and the site was only read
        assert!(asked
            .borrow()
            .iter()
            .all(|l| l.starts_with("pct exec 119 -- sudo -u site9 ")));
        // an answer that cannot be read is not a pass either
        let (mut host, _asked, d2) = wired_host("acceptunknown", site_says("garbage\n"));
        let err = host.run_step(RestoreStep::Accept, &rec).unwrap_err();
        assert!(err.starts_with("accept_unknown"), "{err}");
        let _ = fs::remove_dir_all(&d);
        let _ = fs::remove_dir_all(&d2);
    }
}
