// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site restore-wpress <code> --from <backup id> --plan | --apply <plan.json>
//! | --continue <plan.json>` — restoring a customer's own WordPress backup into one of
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
//! * The command is `site restore-wpress`, not `site restore`: that one brings a whole
//!   container back from the host's own archive, a different thing.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use aib_cloud::restoresteps::{self, AfterFailure, Next, Other, RestoreStep, Standing, STEPS};
use aib_cloud::site::SiteSpec;
use aib_cloud::sitecreate::Probe;
use serde_json::{json, Value};

use crate::opstore;

/// Where the step records are: one `<site code>.json` per site.
pub const RECORDS: &str = "/var/lib/ez2-cloud/restores";
const SITE_RECORDS: &str = "/var/lib/ez2-cloud/sites";
/// The journal's name for this command.
pub const COMMAND: &str = "site.restore-wpress";
/// Why nothing runs on this host yet.
pub const NOT_WIRED: &str =
    "not_wired: the restore steps are not wired on this host yet (B3 slices 3 and 4); nothing was done";

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
        })
    }

    pub fn standing(&self) -> Result<Standing, String> {
        restoresteps::standing(
            &self.done,
            self.failed.as_ref().map(|(s, _)| *s),
            self.cleaned_up,
        )
        .map_err(|w| {
            format!(
                "{w}: the step record of {} is not one this code writes",
                self.site
            )
        })
    }
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
    fn run_step(&mut self, step: RestoreStep, rec: &Record) -> Result<(), String>;
    /// Throw away what was downloaded.
    fn discard(&mut self, rec: &Record) -> Result<(), String>;
    /// Bring the site back from the restore point.
    fn roll_back(&mut self, rec: &Record) -> Result<(), String>;
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
    });
    let stop = |doc: &mut Value, why: &str| doc["stop"] = json!(why);
    if let Err(why) = restoresteps::ready_to_go_on(host.site_up()) {
        stop(&mut doc, why);
        return Ok(doc);
    }
    match restoresteps::next(standing) {
        Next::Start => match restoresteps::admit(&spec.code, &domain, &other_list) {
            Ok(()) => {
                doc["next"] = json!("start");
                doc["from"] = json!(RestoreStep::Download.as_str());
            }
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

fn standing_word(s: Standing) -> String {
    match s {
        Standing::Finished => "finished".to_owned(),
        Standing::Discarded => "discarded".to_owned(),
        Standing::RolledBack => "rolled_back".to_owned(),
        Standing::Kept(step) => format!("kept:{}", step.as_str()),
        Standing::Interrupted(step) => format!("interrupted:{}", step.as_str()),
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
            doc["next"],
            doc["stop"],
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
        if let Err(e) = host.run_step(step, &rec) {
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

/// The real host. Its steps are not wired yet: [`RestoreHost::wired`] is false.
pub struct HostRestorer {
    pub records: PathBuf,
    pub ct: u32,
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

fn read_record_file(path: &Path) -> Result<Option<Record>, String> {
    match fs::read_to_string(path) {
        Ok(text) => {
            let v: Value =
                serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
            Record::from_json(&v).map(Some)
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
        read_record_file(&self.records.join(format!("{site}.json")))
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
            if name.starts_with('.') || !name.ends_with(".json") || name == own {
                continue;
            }
            match read_record_file(&path)? {
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
    fn run_step(&mut self, _step: RestoreStep, _rec: &Record) -> Result<(), String> {
        Err(NOT_WIRED.to_owned())
    }
    fn discard(&mut self, _rec: &Record) -> Result<(), String> {
        Err(NOT_WIRED.to_owned())
    }
    fn roll_back(&mut self, _rec: &Record) -> Result<(), String> {
        Err(NOT_WIRED.to_owned())
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut source: Option<String> = None;
    let mut plan_only = false;
    let mut file: Option<(Mode, String)> = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--from" => source = Some(it.next().ok_or("--from needs the backup's id")?.clone()),
            "--apply" | "--continue" if file.is_some() => {
                return Err("one of --apply or --continue".to_owned())
            }
            "--apply" => {
                file = Some((
                    Mode::Apply,
                    it.next().ok_or("--apply needs a plan file")?.clone(),
                ))
            }
            "--continue" => {
                file = Some((
                    Mode::Continue,
                    it.next().ok_or("--continue needs a plan file")?.clone(),
                ));
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let spec = crate::quarantinecmd::load_spec(&code, SITE_RECORDS)?;
    let mut host = HostRestorer {
        records: PathBuf::from(RECORDS),
        ct: spec.ct,
    };
    match (plan_only, file) {
        (true, None) => {
            let source = source.ok_or("--plan needs --from <the backup's id>")?;
            if !restoresteps::valid_source_id(&source) {
                return Err("--from is not a shared file's id".to_owned());
            }
            let mut doc = plan_doc(&spec, &source, &mut host)?;
            let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
            let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
            crate::quarantinecmd::seal_with_op(
                &mut doc,
                &op,
                &fingerprint,
                env!("CARGO_PKG_VERSION"),
            )?;
            println!(
                "{}",
                serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
            );
            Ok(0)
        }
        (false, Some((mode, path))) => {
            if source.is_some() {
                return Err("--from belongs to --plan: the plan given says it".to_owned());
            }
            let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
            let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
            let place = opstore::OpPlace::host()?;
            let done = apply(&place, &plan, &spec, mode, &mut host)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&done).map_err(|e| e.to_string())?
            );
            Ok(0)
        }
        (true, Some(_)) => Err("--plan, or --apply / --continue <plan.json>, not both".to_owned()),
        (false, None) => Err(
            "needs --plan --from <id>, --apply <plan.json> or --continue <plan.json>".to_owned(),
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
        }
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
        let mut host = HostRestorer {
            records: d.join("restores"),
            ct: 119,
        };
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
        let mut host = HostRestorer { records, ct: 119 };
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
}
