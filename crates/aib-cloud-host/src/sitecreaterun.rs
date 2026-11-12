// SPDX-License-Identifier: Apache-2.0
//! The customer's `site create <domain> [blank]` (E1): what their key's request starts on
//! the host, how it is carried out, and how they follow it.
//!
//! [`start`], under the customer's forced command: the domain must not be opening already
//! (the same answer, `already_opening`, whoever's opening it is; their own code is given
//! back only to them); the plan is made for the customer as `site create --plan --tenant`
//! makes it (read only); a plan that does not go on to the clone is answered with a fixed
//! word. Otherwise a code is chosen, the ask is written (`ASKED/<code>.json`, root only:
//! the sealed plan, the customer, the domain) and the opening is started as a one-shot
//! system unit (PLAN-customer-site-create 11.8: 「由一次性的系統單元啟動」), so it outlives the
//! ssh session. The customer gets the code at once.
//!
//! [`run`] (`site create <domain> --run <code>`, in that unit): the ask names the process
//! that carries it on (a holder like the host lock's, judged the lock's way); the plan is
//! applied under the code given (`--apply`, [`sitecreatecmd::chosen`]) and the opening
//! carried on to the end (`--continue`), both as operations the customer key approved. A
//! run that finds the opening record already there carries it on from its step, so a run
//! cut off is resumed by running it again; an opening that stopped on a failure is not
//! run again by itself (a person looks: `op status`, `--continue`).
//!
//! [`progress`] (`site <code> status` while the site is not live yet): the step done and
//! whether it stopped, from the ask and the opening record. Only fixed words go out; why it
//! stopped stays in the ask on the host.
//!
//! What is given only to a plan that runs (33e28 M1, M2): the plan's quota judged and
//! `fits` (a quota not looked at, or not told, is never taken as room), and a domain that
//! is not one of the platform's own ([`sitecreatecmd::PLATFORM_DOMAINS`]; a list not read
//! whole starts nothing). Both are checked here and again by the plan itself.
//!
//! Two asks of one domain at once (33e28 N1): both may pass [`opening_of`] before either
//! is written, and both runs start. The second to take the host lock remakes the plan
//! under it, finds the first one's opening record and stops before the clone
//! (`domain_taken`, or a plan that no longer matches): nothing of it is made, and its
//! customer reads `stopped`. A run whose unit starts after [`START_GRACE_S`] does not take
//! its ask any more (N2): the ask already reads as stopped and no longer holds the domain.
//!
//! The asks are kept, stopped and open ones too (N3): they are what makes a code given once
//! never given again ([`codes_given`]). They are small; clearing old ones joins the
//! clean-up of the records' `.bak` files when that is made.

use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use aib_cloud::op::Liveness;
use aib_cloud::sitecreate::{CreateStep, STEPS};
use serde_json::{json, Value};

use crate::opstore;
use crate::sitecreatecmd::{self, HostPaths, HostRunner};

/// The customers' asks, one per code: root only.
pub const ASKED: &str = "/var/lib/ez2-cloud/sites-asked";
/// This program on the host, as the customer keys' entries name it.
pub const HOST_BIN: &str = "/usr/local/sbin/aib-cloud-host";
/// How long an ask may wait for its run to write itself down before it reads as stopped
/// (33e28: an opening that names no holder is judged after 120 s).
pub const START_GRACE_S: u64 = 120;
/// How often, and how far apart, a run asks again for a host lock another operation holds
/// (a backup, another opening): about ten minutes, then the ask stops at `host_busy`.
pub const LOCK_TRIES: u32 = 40;
pub const LOCK_PAUSE: Duration = Duration::from_secs(15);

/// Where the asks and the openings are; tests hand in scratch directories.
pub struct Places {
    pub asked: PathBuf,
    pub creating: PathBuf,
    pub opened: PathBuf,
    pub records: PathBuf,
    pub removed: PathBuf,
    /// The platform's own domains ([`sitecreatecmd::PLATFORM_DOMAINS`]).
    pub platform: PathBuf,
    /// Only `proc_root` and `boot_id` are read: whether a run is still the process it names.
    pub lock: opstore::LockPaths,
}

impl Places {
    pub fn host() -> Result<Self, String> {
        Ok(Places {
            asked: PathBuf::from(ASKED),
            creating: PathBuf::from(sitecreatecmd::CREATING),
            opened: PathBuf::from(sitecreatecmd::OPENED),
            records: PathBuf::from(sitecreatecmd::RECORDS),
            removed: PathBuf::from(sitecreatecmd::REMOVED_RECORDS),
            platform: PathBuf::from(sitecreatecmd::PLATFORM_DOMAINS),
            lock: opstore::LockPaths::host()?,
        })
    }
}

/// The answer to a customer's `site create`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Asked {
    /// Started under this code.
    Opening(String),
    /// Refused, with a fixed word; with `already_opening`, the code of the customer's own
    /// opening of that domain (none when it is someone else's).
    Refused(&'static str, Option<String>),
    /// Could not be started. What went wrong is not the customer's to read.
    Unavailable,
}

/// What the customer's key needs of an opening. The host's is [`HostOpener`]; tests hand
/// in their own.
pub trait Opener {
    fn create(&mut self, tenant: &str, domain: &str, blank: bool) -> Asked;
    /// `None`: no ask of this customer under this code.
    fn progress(&mut self, tenant: &str, code: &str) -> Option<Value>;
}

pub struct HostOpener;

impl Opener for HostOpener {
    fn create(&mut self, tenant: &str, domain: &str, blank: bool) -> Asked {
        let Ok(places) = Places::host() else {
            return Asked::Unavailable;
        };
        let records = places.records.to_string_lossy().into_owned();
        start(
            &places,
            tenant,
            domain,
            blank,
            || sitecreatecmd::sealed_plan(domain, blank, Some(tenant.to_owned()), &records),
            opstore::random4,
            |code| launch(domain, code),
            crate::probe::unix_now(),
        )
    }

    fn progress(&mut self, tenant: &str, code: &str) -> Option<Value> {
        let places = Places::host().ok()?;
        progress(&places, tenant, code, crate::probe::unix_now())
    }
}

/// The word a plan stops at, as the customer is told it. Only these two are theirs to
/// act on; every other stop is ours ([`Asked::Unavailable`]).
fn customer_stop(word: &str) -> Option<&'static str> {
    match word {
        "domain_taken" => Some("domain_taken"),
        // E7 (#677): the plan stops here once the quota is judged in it
        "quota_exceeded" => Some("quota_exceeded"),
        _ => None,
    }
}

fn read_json(path: &Path) -> Option<Value> {
    serde_json::from_str(&fs::read_to_string(path).ok()?).ok()
}

/// Every ask, by its file. A directory that is not there holds none; one that cannot be
/// read, or an ask that cannot, is `None`: an ask unread might be an opening.
fn asks(dir: &Path) -> Option<Vec<Value>> {
    sitecreatecmd::read_records(dir)
}

/// The state of an ask now: `open`, `stopped`, or `opening` while its run is waited for
/// ([`START_GRACE_S`]) or still the process it names.
fn ask_state(places: &Places, ask: &Value, now: u64) -> &'static str {
    match ask.get("state").and_then(Value::as_str) {
        Some("open") => "open",
        Some("waiting") => {
            let at = ask.get("asked_at").and_then(Value::as_u64).unwrap_or(0);
            if now.saturating_sub(at) < START_GRACE_S {
                "opening"
            } else {
                "stopped"
            }
        }
        Some("running") => {
            let holder = ask
                .get("holder")
                .and_then(|h| opstore::holder_from_text(&h.to_string()));
            match holder.map(|h| opstore::holder_liveness(&places.lock, &h)) {
                Some(Liveness::Gone) | None => "stopped",
                Some(_) => "opening",
            }
        }
        _ => "stopped",
    }
}

/// The customer's ask under `code` as an abandon sees it ([`crate::siteabandon`]): `None`
/// when there is none, else its state now (`opening` while its run is waited for or still
/// going). `Err` when it is there and cannot be read: an ask unread might be an opening.
pub(crate) fn ask_now(places: &Places, code: &str, now: u64) -> Result<Option<&'static str>, ()> {
    let path = places.asked.join(format!("{code}.json"));
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(()),
    };
    let ask: Value = serde_json::from_str(&text).map_err(|_| ())?;
    if ask.get("site").and_then(Value::as_str) != Some(code) {
        return Err(());
    }
    Ok(Some(ask_state(places, &ask, now)))
}

/// The ask of an abandoned opening, stopped with `why: abandoned` (c477a abandon L1). It is
/// kept, not removed: the code it holds stays given ([`codes_given`]). No ask is nothing to
/// do; one that cannot be read is an error.
pub(crate) fn stop_ask_abandoned(places: &Places, code: &str, now: u64) -> Result<(), String> {
    let path = places.asked.join(format!("{code}.json"));
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    let mut ask: Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let obj = ask
        .as_object_mut()
        .ok_or_else(|| format!("{}: not an ask", path.display()))?;
    obj.insert("state".to_owned(), json!("stopped"));
    obj.insert("why".to_owned(), json!("abandoned"));
    obj.insert("at".to_owned(), json!(now));
    // kept, not removed: the code stays given
    rewrite_ask(&places.asked, code, &ask)
}

fn first_host(rec: &Value) -> Option<&str> {
    rec.get("hosts")?.as_str()?.split(',').next().map(str::trim)
}

/// The opening of `domain` (or its `www` twin) under way: an ask still going, or an
/// opening record. `Err` when either could not be read.
fn opening_of(
    places: &Places,
    domain: &str,
    now: u64,
) -> Result<Option<(String, Option<String>)>, ()> {
    let names = sitecreatecmd::same_names(domain);
    let named = |d: Option<&str>| d.is_some_and(|d| names.iter().any(|n| n == d));
    let code_and_tenant = |r: &Value| {
        (
            r.get("site")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            aib_cloud::site::record_tenant(r).map(str::to_owned),
        )
    };
    for ask in asks(&places.asked).ok_or(())? {
        if named(ask.get("domain").and_then(Value::as_str))
            && ask_state(places, &ask, now) == "opening"
        {
            return Ok(Some(code_and_tenant(&ask)));
        }
    }
    let creating = sitecreatecmd::read_records(&places.creating).ok_or(())?;
    Ok(creating
        .iter()
        .find(|r| named(first_host(r)))
        .map(code_and_tenant))
}

/// Every code given: live, removed and opening sites, and every ask.
fn codes_given(places: &Places) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let abandoned = crate::siteabandon::abandoned_dir(&places.creating);
    for dir in [
        &places.records,
        &places.removed,
        &places.creating,
        &abandoned,
        &places.asked,
    ] {
        for r in sitecreatecmd::read_records(dir)? {
            if let Some(code) = r.get("site").and_then(Value::as_str) {
                out.push(code.to_owned());
            }
        }
    }
    Some(out)
}

fn private_dir(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o700))
        .map_err(|e| format!("{}: {e}", dir.display()))
}

/// A new ask: the file must not be there yet (two asks never share a code).
fn write_new_ask(dir: &Path, code: &str, ask: &Value) -> Result<(), String> {
    private_dir(dir)?;
    let path = dir.join(format!("{code}.json"));
    let text = serde_json::to_string_pretty(ask).map_err(|e| e.to_string())? + "\n";
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// An ask written again whole: a temporary file, root only, then renamed.
fn rewrite_ask(dir: &Path, code: &str, ask: &Value) -> Result<(), String> {
    let path = dir.join(format!("{code}.json"));
    let tmp = dir.join(format!(".{code}.json.tmp"));
    let text = serde_json::to_string_pretty(ask).map_err(|e| e.to_string())? + "\n";
    let _ = fs::remove_file(&tmp);
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .and_then(|()| fs::rename(&tmp, &path))
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// The customer's `site create`. `plan` makes the plan (`--plan --tenant`), `launch` starts
/// the run for a code. Nothing is written before the plan says the clone may run.
#[allow(clippy::too_many_arguments)]
pub fn start(
    places: &Places,
    tenant: &str,
    domain: &str,
    blank: bool,
    plan: impl FnOnce() -> Result<Value, String>,
    mut random: impl FnMut() -> Result<[u8; 4], String>,
    launch: impl FnOnce(&str) -> Result<(), String>,
    now: u64,
) -> Asked {
    // the platform's own domains first: whether one is opening is nobody's to learn
    let Some(ours) = fs::read_to_string(&places.platform)
        .ok()
        .as_deref()
        .and_then(aib_cloud::sitecreate::platform_domains)
    else {
        return Asked::Unavailable;
    };
    if aib_cloud::sitecreate::is_platform(domain, &ours) {
        return Asked::Refused("domain_taken", None);
    }
    match opening_of(places, domain, now) {
        Err(()) => return Asked::Unavailable,
        Ok(Some((code, owner))) => {
            // whose opening it is is not told to anyone else
            let own = (owner.as_deref() == Some(tenant)).then_some(code);
            return Asked::Refused("already_opening", own);
        }
        Ok(None) => {}
    }
    let Ok(plan) = plan() else {
        return Asked::Unavailable;
    };
    if plan["quota"]["tenant_id"].as_str() != Some(tenant)
        || plan.get("blank").and_then(Value::as_bool) != Some(blank)
    {
        return Asked::Unavailable;
    }
    if plan["next"] != "run" || plan["next_step"] != CreateStep::Clone.as_str() {
        let stop = plan.get("stop").and_then(Value::as_str).unwrap_or("");
        return match customer_stop(stop) {
            Some(word) => Asked::Refused(word, None),
            None => Asked::Unavailable,
        };
    }
    // only a quota judged and found to have room starts an opening (33e28 M1)
    if plan["quota"]["verdict"] != "fits" {
        return Asked::Unavailable;
    }
    let Some(taken) = codes_given(places) else {
        return Asked::Unavailable;
    };
    let Ok(code) = sitecreatecmd::new_code(&taken, &mut random) else {
        return Asked::Unavailable;
    };
    let mut ask = json!({
        "site": code,
        "domain": domain,
        "blank": blank,
        "tenant_id": tenant,
        "plan": plan,
        "state": "waiting",
        "asked_at": now,
    });
    if write_new_ask(&places.asked, &code, &ask).is_err() {
        return Asked::Unavailable;
    }
    if let Err(e) = launch(&code) {
        ask["state"] = json!("stopped");
        ask["why"] = json!("launch_failed");
        ask["detail"] = json!(e);
        let _ = rewrite_ask(&places.asked, &code, &ask);
        return Asked::Unavailable;
    }
    Asked::Opening(code)
}

/// The step after `done` in the order of opening (the clone when nothing is done).
fn step_after(done: Option<&str>) -> Option<&'static str> {
    match done {
        None => STEPS.first().map(|s| s.as_str()),
        Some(d) => {
            let i = STEPS.iter().position(|s| s.as_str() == d)?;
            STEPS.get(i + 1).map(|s| s.as_str())
        }
    }
}

/// What a customer sees of their opening under `code`: `None` unless there is an ask of
/// theirs under it. `state` is `opening`, `open` or `stopped`; `step_done` is the last step
/// done (none before the clone); `stopped_at` the step it stopped at, when it stopped and
/// that is known.
pub fn progress(places: &Places, tenant: &str, code: &str, now: u64) -> Option<Value> {
    if !aib_cloud::site::valid_code(code) {
        return None;
    }
    let ask = read_json(&places.asked.join(format!("{code}.json")))?;
    if ask.get("site").and_then(Value::as_str) != Some(code)
        || aib_cloud::site::record_tenant(&ask) != Some(tenant)
    {
        return None;
    }
    let mut state = ask_state(places, &ask, now);
    let opening = read_json(&places.creating.join(format!("{code}.json")));
    let opened = places.opened.join(format!("{code}.json")).exists();
    let step_done: Option<String> = if opened {
        Some(CreateStep::Health.as_str().to_owned())
    } else if let Some(rec) = &opening {
        rec.get("step").and_then(Value::as_str).map(str::to_owned)
    } else {
        ask.get("step_done")
            .and_then(Value::as_str)
            .map(str::to_owned)
    };
    // a step kept for a person has stopped, whatever the ask says yet
    let kept = opening
        .as_ref()
        .and_then(|r| r.get("kept")?.get("step")?.as_str().map(str::to_owned));
    if kept.is_some() && state == "opening" {
        state = "stopped";
    }
    // an opening that got to the end is open, whatever its ask still says: a person's
    // `--continue` after a stop ends it without writing the ask again (E1 audit #5)
    if opened {
        state = "open";
    }
    let stopped_at = (state == "stopped").then(|| {
        kept.or_else(|| {
            ask.get("stopped_at")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .or_else(|| step_after(step_done.as_deref()).map(str::to_owned))
    });
    Some(json!({
        "site": code,
        "state": state,
        "step_done": step_done,
        "stopped_at": stopped_at.flatten(),
    }))
}

/// The steps a run takes; the host's are [`HostSteps`], tests hand in their own.
pub trait Steps {
    /// `--apply` of the plan under `code`.
    fn apply(&mut self, plan: &Value, domain: &str, code: &str) -> Result<(), String>;
    /// `--continue` to the end.
    fn carry_on(&mut self, domain: &str, code: &str) -> Result<Option<Value>, String>;
}

pub struct HostSteps {
    pub place: opstore::OpPlace,
    pub paths: HostPaths,
}

impl Steps for HostSteps {
    fn apply(&mut self, plan: &Value, domain: &str, code: &str) -> Result<(), String> {
        let records = self.paths.records.to_string_lossy().into_owned();
        let tenant = sitecreatecmd::plan_tenant(plan);
        sitecreatecmd::apply_by(
            &self.place,
            plan,
            domain,
            &self.paths.creating,
            || sitecreatecmd::see(domain, &records, tenant),
            sitecreatecmd::chosen(code),
            opstore::BY_CUSTOMER_KEY,
            &mut HostRunner,
        )
        .map(|_| ())
    }

    fn carry_on(&mut self, domain: &str, code: &str) -> Result<Option<Value>, String> {
        sitecreatecmd::continue_apply_by(
            &self.place,
            domain,
            code,
            &self.paths,
            &mut HostRunner,
            CreateStep::Health,
            opstore::BY_CUSTOMER_KEY,
        )
    }
}

/// The fixed word an error starts with (`clone_failed: …`), or `failed`.
fn word_of(e: &str) -> String {
    match e.split_once(':') {
        Some((w, _)) if !w.is_empty() && w.bytes().all(|b| b.is_ascii_lowercase() || b == b'_') => {
            w.to_owned()
        }
        _ => "failed".to_owned(),
    }
}

/// `f` again while the host lock is another operation's, up to [`LOCK_TRIES`] times.
fn while_locked<T>(
    mut f: impl FnMut() -> Result<T, String>,
    pause: &mut impl FnMut(),
) -> Result<T, String> {
    let mut tries = 1;
    loop {
        match f() {
            Err(e) if e.starts_with("host is locked by operation") => {
                if tries >= LOCK_TRIES {
                    return Err(format!("host_busy: {e}"));
                }
                tries += 1;
                pause();
            }
            other => return other,
        }
    }
}

/// Carry the customer's opening under `code` on, as process `pid`. Returns the state it
/// ended in (`open` or `stopped`); `Err` when this run may not carry it on at all.
pub fn run(
    places: &Places,
    code: &str,
    domain: &str,
    pid: u32,
    steps: &mut impl Steps,
    pause: &mut impl FnMut(),
    now: impl Fn() -> u64,
) -> Result<&'static str, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("{code:?} is not a site code"));
    }
    let path = places.asked.join(format!("{code}.json"));
    let mut ask = read_json(&path).ok_or_else(|| format!("{}: no ask", path.display()))?;
    if ask.get("site").and_then(Value::as_str) != Some(code)
        || ask.get("domain").and_then(Value::as_str) != Some(domain)
    {
        return Err(format!(
            "{}: not the ask of {code} for {domain}",
            path.display()
        ));
    }
    match (
        ask.get("state").and_then(Value::as_str),
        ask_state(places, &ask, now()),
    ) {
        (Some("open"), _) => return Ok("open"),
        // the unit started after the ask stopped waiting for it: the domain is no longer
        // held by this ask, so it is not taken up again (33e28 N2)
        (Some("waiting"), "stopped") => {
            ask["state"] = json!("stopped");
            ask["why"] = json!("start_late");
            ask["at"] = json!(now());
            rewrite_ask(&places.asked, code, &ask)?;
            return Err(format!(
                "{code}: the run started after its ask stopped waiting"
            ));
        }
        // started and still going: another run has it
        (Some("running"), "opening") => {
            return Err(format!("{code}: another run carries this opening on"))
        }
        // waiting, or a run that is gone: this run takes it
        (Some("waiting" | "running"), _) => {}
        _ => {
            return Err(format!(
                "{code}: the opening stopped; a person looks (op status, --continue)"
            ))
        }
    }
    let plan = ask.get("plan").cloned().ok_or("the ask has no plan")?;
    let op = plan
        .get("op")
        .and_then(Value::as_str)
        .ok_or("the plan has no op")?;
    let holder = opstore::holder_of(&places.lock, op, pid)?;
    ask["state"] = json!("running");
    ask["holder"] = opstore::holder_json(&holder);
    ask["at"] = json!(now());
    rewrite_ask(&places.asked, code, &ask)?;

    let opening_path = places.creating.join(format!("{code}.json"));
    let opened_path = places.opened.join(format!("{code}.json"));
    let stop = |ask: &mut Value, at: Option<String>, e: &str| -> Result<&'static str, String> {
        let done =
            read_json(&opening_path).and_then(|r| r.get("step")?.as_str().map(str::to_owned));
        ask["state"] = json!("stopped");
        ask["step_done"] = json!(done);
        ask["stopped_at"] = json!(at.or_else(|| step_after(done.as_deref()).map(str::to_owned)));
        ask["why"] = json!(word_of(e));
        ask["detail"] = json!(e);
        ask["at"] = json!(now());
        rewrite_ask(&places.asked, code, ask)?;
        Ok("stopped")
    };
    if !opening_path.exists() && !opened_path.exists() {
        if let Err(e) = while_locked(|| steps.apply(&plan, domain, code), &mut *pause) {
            return stop(&mut ask, Some(CreateStep::Clone.as_str().to_owned()), &e);
        }
    }
    let end = if opened_path.exists() {
        Ok(None)
    } else {
        while_locked(|| steps.carry_on(domain, code), &mut *pause)
    };
    match end {
        Ok(Some(rec)) if rec.get("held").is_some_and(Value::is_string) => {
            let why = format!("held: {}", rec["held"].as_str().unwrap_or(""));
            stop(&mut ask, Some(CreateStep::Start.as_str().to_owned()), &why)
        }
        Ok(_) if opened_path.exists() => {
            ask["state"] = json!("open");
            ask["step_done"] = json!(CreateStep::Health.as_str());
            ask["at"] = json!(now());
            rewrite_ask(&places.asked, code, &ask)?;
            Ok("open")
        }
        Ok(_) => stop(
            &mut ask,
            None,
            "not_finished: the opening did not reach the end",
        ),
        Err(e) => {
            let kept = read_json(&opening_path)
                .and_then(|r| r.get("kept")?.get("step")?.as_str().map(str::to_owned));
            stop(&mut ask, kept, &e)
        }
    }
}

/// The one-shot unit that runs `--run <code>`: its own unit, so the opening outlives the
/// customer's ssh session; collected when it ends.
pub fn unit_argv(domain: &str, code: &str) -> Vec<String> {
    [
        "systemd-run",
        &format!("--unit=ez2-site-create-{code}"),
        "--collect",
        "--quiet",
        "--",
        HOST_BIN,
        "site",
        "create",
        domain,
        "--run",
        code,
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect()
}

fn launch(domain: &str, code: &str) -> Result<(), String> {
    let argv = unit_argv(domain, code);
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..]);
    crate::probe::run_capture(&mut cmd, Duration::from_secs(30)).map(|_| ())
}

/// Wait [`LOCK_PAUSE`] through a child, as `probe` pauses between its checks (ADR 0005: this
/// command has no timer registry).
fn pause() {
    if let Ok(mut child) = Command::new("sleep")
        .arg(LOCK_PAUSE.as_secs().to_string())
        .spawn()
    {
        let _ = crate::probe::wait_with_timeout(&mut child, LOCK_PAUSE + Duration::from_secs(5));
    }
}

/// `site create <domain> --run <code>` on this host (in the unit [`start`] launched).
/// Exit 0 when the site is open, 1 when the opening stopped.
pub fn run_cmd(domain: &str, code: &str) -> Result<u8, String> {
    let places = Places::host()?;
    let mut steps = HostSteps {
        place: opstore::OpPlace::host()?,
        paths: HostPaths::host(),
    };
    let state = run(
        &places,
        code,
        domain,
        std::process::id(),
        &mut steps,
        &mut pause,
        crate::probe::unix_now,
    )?;
    println!("{}", json!({ "site": code, "state": state }));
    Ok(u8::from(state != "open"))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const T: &str = "t-acme1";
    const NOW: u64 = 1_794_400_000;

    fn scratch(name: &str) -> Places {
        let d = std::env::temp_dir().join(format!("aib-createrun-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        for sub in ["asked", "creating", "opened", "records", "removed", "proc"] {
            fs::create_dir_all(d.join(sub)).unwrap();
        }
        fs::write(d.join("boot_id"), "0f0e0d0c-0b0a-4908-8706-050403020100\n").unwrap();
        fs::write(d.join("platform-domains"), "# ours\nours.example\n").unwrap();
        Places {
            asked: d.join("asked"),
            creating: d.join("creating"),
            opened: d.join("opened"),
            records: d.join("records"),
            removed: d.join("removed"),
            platform: d.join("platform-domains"),
            lock: opstore::LockPaths {
                lock: d.join("host.lock"),
                proc_root: d.join("proc"),
                boot_id: d.join("boot_id"),
            },
        }
    }

    /// An abandoned opening's code is never given again, though nothing holds it.
    #[test]
    fn an_abandoned_openings_code_stays_given() {
        let places = scratch("abandoned");
        let gone = crate::siteabandon::abandoned_dir(&places.creating);
        sitecreatecmd::write_record(&gone, "s-gone01", &json!({"site": "s-gone01"})).unwrap();
        let taken = codes_given(&places).unwrap();
        assert!(taken.iter().any(|c| c == "s-gone01"));
        let _ = fs::remove_dir_all(places.asked.parent().unwrap());
    }

    /// An abandoned opening's ask reads stopped, why abandoned, and is kept: its code stays
    /// given.
    #[test]
    fn an_abandoned_ask_is_stopped_kept_and_its_code_given() {
        let p = scratch("askgone");
        let ask = json!({"site": "s-gone02", "domain": "gone.example.test", "tenant_id": "t-aaa",
            "state": "running", "holder": null, "asked_at": NOW});
        write_new_ask(&p.asked, "s-gone02", &ask).unwrap();
        assert_eq!(ask_now(&p, "s-gone02", NOW), Ok(Some("stopped")));
        stop_ask_abandoned(&p, "s-gone02", NOW + 5).unwrap();
        let kept = read_json(&p.asked.join("s-gone02.json")).unwrap();
        assert_eq!(kept["state"], "stopped");
        assert_eq!(kept["why"], "abandoned");
        assert_eq!(kept["domain"], "gone.example.test");
        assert!(codes_given(&p).unwrap().iter().any(|c| c == "s-gone02"));
        assert_eq!(
            progress(&p, "t-aaa", "s-gone02", NOW + 6).unwrap()["state"],
            "stopped"
        );
        // no ask: nothing to stop, nothing to tell
        stop_ask_abandoned(&p, "s-none01", NOW).unwrap();
        assert_eq!(ask_now(&p, "s-none01", NOW), Ok(None));
        let _ = fs::remove_dir_all(p.asked.parent().unwrap());
    }

    /// An ask waited for is an opening; one that cannot be read is not told.
    #[test]
    fn an_ask_is_seen_opening_or_not_told() {
        let p = scratch("asknow");
        let ask = json!({"site": "s-wait01", "domain": "wait.example.test", "state": "waiting", "asked_at": NOW});
        write_new_ask(&p.asked, "s-wait01", &ask).unwrap();
        assert_eq!(ask_now(&p, "s-wait01", NOW + 1), Ok(Some("opening")));
        assert_eq!(
            ask_now(&p, "s-wait01", NOW + START_GRACE_S + 1),
            Ok(Some("stopped"))
        );
        fs::write(p.asked.join("s-bad001.json"), "{").unwrap();
        assert_eq!(ask_now(&p, "s-bad001", NOW), Err(()));
        let _ = fs::remove_dir_all(p.asked.parent().unwrap());
    }

    /// A process `pid` started at `start` in the scratch `/proc`.
    fn alive(p: &Places, pid: u32, start: u64) {
        let me = p.lock.proc_root.join(pid.to_string());
        fs::create_dir_all(&me).unwrap();
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            me.join("stat"),
            format!("{pid} (aib-cloud-host) {filler} {start} 0 0\n"),
        )
        .unwrap();
    }

    fn plan_for(tenant: &str, blank: bool) -> Value {
        json!({
            "plan": "site-create", "domain": "www.new.example.test", "blank": blank,
            "quota": { "tenant_id": tenant, "verdict": "fits" },
            "next": "run", "next_step": "clone", "stop": null,
            "op": "20261111T120000Z-ab12cd34",
        })
    }

    fn stopped_plan(word: &str) -> Value {
        let mut p = plan_for(T, false);
        p["next"] = json!("stop");
        p["stop"] = json!(word);
        p
    }

    fn seq(start: u8) -> impl FnMut() -> Result<[u8; 4], String> {
        let mut n = start;
        move || {
            n = n.wrapping_add(1);
            Ok([n, 7, 9, 11])
        }
    }

    fn ask_on(p: &Places, code: &str) -> Value {
        read_json(&p.asked.join(format!("{code}.json"))).unwrap()
    }

    fn started(p: &Places, tenant: &str, domain: &str, launched: &mut Vec<String>) -> Asked {
        start(
            p,
            tenant,
            domain,
            false,
            || Ok(plan_for(tenant, false)),
            seq(0),
            |code| {
                launched.push(code.to_owned());
                Ok(())
            },
            NOW,
        )
    }

    /// The customer gets a code at once; the ask holds the plan for the run and is root's.
    #[test]
    fn a_plan_that_runs_gives_a_code_and_starts_the_run() {
        let p = scratch("start");
        let mut launched = Vec::new();
        let Asked::Opening(code) = started(&p, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        assert!(aib_cloud::site::valid_code(&code) && code.starts_with("s-"));
        assert_eq!(launched, [code.as_str()]);
        let ask = ask_on(&p, &code);
        assert_eq!(ask["state"], "waiting");
        assert_eq!(ask["tenant_id"], T);
        assert_eq!(ask["plan"]["op"], "20261111T120000Z-ab12cd34");
        let mode = fs::metadata(p.asked.join(format!("{code}.json")))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
        assert_eq!(
            fs::metadata(&p.asked).unwrap().permissions().mode() & 0o777,
            0o700
        );
        // and the customer follows it under that code
        let seen = progress(&p, T, &code, NOW + 5).unwrap();
        assert_eq!(seen["state"], "opening");
        assert_eq!(seen["step_done"], Value::Null);
        assert_eq!(seen["stopped_at"], Value::Null);
    }

    /// The same domain (or its www twin) while it is opening: refused, and the code is
    /// given back only to the customer whose opening it is.
    #[test]
    fn the_same_domain_while_it_is_opening_is_refused() {
        let p = scratch("twice");
        let mut launched = Vec::new();
        let Asked::Opening(code) = started(&p, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        assert_eq!(
            started(&p, T, "new.example.test", &mut launched),
            Asked::Refused("already_opening", Some(code.clone()))
        );
        assert_eq!(
            started(&p, "t-other", "www.new.example.test", &mut launched),
            Asked::Refused("already_opening", None)
        );
        assert_eq!(launched.len(), 1);
        // an opening record of the domain holds it too
        let q = scratch("twice-rec");
        fs::write(
            q.creating.join("s-abc123.json"),
            json!({ "site": "s-abc123", "hosts": "www.new.example.test", "tenant_id": T })
                .to_string(),
        )
        .unwrap();
        assert_eq!(
            started(&q, T, "www.new.example.test", &mut launched),
            Asked::Refused("already_opening", Some("s-abc123".to_owned()))
        );
        // an ask whose run never wrote itself down no longer holds the domain
        let r = scratch("twice-stale");
        let Asked::Opening(_) = started(&r, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        let again = start(
            &r,
            T,
            "www.new.example.test",
            false,
            || Ok(plan_for(T, false)),
            seq(50),
            |_| Ok(()),
            NOW + START_GRACE_S,
        );
        assert!(matches!(again, Asked::Opening(_)), "{again:?}");
    }

    /// A plan that does not go on to the clone: the customer's two words as they are, any
    /// other stop of ours is one neutral answer; nothing is written, nothing launched.
    #[test]
    fn a_plan_that_stops_is_answered_with_a_fixed_word() {
        let p = scratch("stops");
        for (word, answer) in [
            ("domain_taken", Asked::Refused("domain_taken", None)),
            ("quota_exceeded", Asked::Refused("quota_exceeded", None)),
            ("quota_unknown", Asked::Unavailable),
            ("unfinished_operation", Asked::Unavailable),
            ("numbers_unknown", Asked::Unavailable),
        ] {
            let got = start(
                &p,
                T,
                "www.new.example.test",
                false,
                || Ok(stopped_plan(word)),
                seq(0),
                |_| panic!("launched"),
                NOW,
            );
            assert_eq!(got, answer, "{word}");
        }
        // a plan for another customer, or not as asked, is never run
        for plan in [plan_for("t-other", false), plan_for(T, true)] {
            let got = start(
                &p,
                T,
                "www.new.example.test",
                false,
                || Ok(plan.clone()),
                seq(0),
                |_| panic!("launched"),
                NOW,
            );
            assert_eq!(got, Asked::Unavailable);
        }
        let got = start(
            &p,
            T,
            "www.new.example.test",
            false,
            || Err("no".to_owned()),
            seq(0),
            |_| panic!("launched"),
            NOW,
        );
        assert_eq!(got, Asked::Unavailable);
        assert_eq!(fs::read_dir(&p.asked).unwrap().count(), 0);
    }

    /// A plan that would run while its quota was not judged, or not told, starts nothing
    /// (33e28 M1): no code, no ask, no unit.
    #[test]
    fn a_quota_not_judged_starts_nothing() {
        let p = scratch("quota");
        for quota in [
            json!({ "tenant_id": T, "verdict": "quota_unknown" }),
            json!({ "tenant_id": T, "verdict": "not checked yet" }),
            json!({ "tenant_id": T }),
            json!({ "tenant_id": T, "verdict": null }),
        ] {
            let mut plan = plan_for(T, false);
            plan["quota"] = quota.clone();
            let got = start(
                &p,
                T,
                "www.new.example.test",
                false,
                || Ok(plan),
                seq(0),
                |_| panic!("launched"),
                NOW,
            );
            assert_eq!(got, Asked::Unavailable, "{quota}");
        }
        assert_eq!(fs::read_dir(&p.asked).unwrap().count(), 0);
    }

    /// The platform's own domains are refused like a name in use, before any plan; a
    /// list that is not there, or not read whole, starts nothing (33e28 M2).
    #[test]
    fn the_platform_s_own_domains_are_not_started() {
        let p = scratch("platform");
        for name in ["ours.example", "www.ours.example", "shop.ours.example"] {
            let got = start(
                &p,
                T,
                name,
                false,
                || panic!("planned"),
                seq(0),
                |_| panic!("launched"),
                NOW,
            );
            assert_eq!(got, Asked::Refused("domain_taken", None), "{name}");
        }
        for text in [None, Some("ours.example\nnot a domain\n"), Some("# none\n")] {
            match text {
                Some(t) => fs::write(&p.platform, t).unwrap(),
                None => {
                    let _ = fs::remove_file(&p.platform);
                }
            }
            let got = start(
                &p,
                T,
                "www.new.example.test",
                false,
                || panic!("planned"),
                seq(0),
                |_| panic!("launched"),
                NOW,
            );
            assert_eq!(got, Asked::Unavailable, "{text:?}");
        }
        assert_eq!(fs::read_dir(&p.asked).unwrap().count(), 0);
    }

    /// A unit that could not be started: the customer is not given a code to follow, and
    /// the ask says why for a person.
    #[test]
    fn a_run_that_could_not_be_launched_is_not_an_opening() {
        let p = scratch("nolaunch");
        let got = start(
            &p,
            T,
            "www.new.example.test",
            false,
            || Ok(plan_for(T, false)),
            seq(0),
            |_| Err("systemd-run: failed".to_owned()),
            NOW,
        );
        assert_eq!(got, Asked::Unavailable);
        let only = fs::read_dir(&p.asked)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let ask = read_json(&only).unwrap();
        assert_eq!(ask["state"], "stopped");
        assert_eq!(ask["why"], "launch_failed");
    }

    /// A code in use anywhere (live, removed, opening, asked) is never given again.
    #[test]
    fn a_code_given_before_is_not_given_again() {
        let p = scratch("codes");
        let first = sitecreatecmd::code_from([1, 7, 9, 11]);
        fs::write(
            p.removed.join("x.json"),
            json!({ "site": first }).to_string(),
        )
        .unwrap();
        let mut launched = Vec::new();
        let Asked::Opening(code) = started(&p, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        assert_ne!(code, first);
    }

    /// Someone else's code, a code never asked, or not a code: nothing to show.
    #[test]
    fn progress_is_the_customer_s_own_only() {
        let p = scratch("mine");
        let mut launched = Vec::new();
        let Asked::Opening(code) = started(&p, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        assert!(progress(&p, "t-other", &code, NOW).is_none());
        assert!(progress(&p, T, "s-zzzzzz", NOW).is_none());
        assert!(progress(&p, T, "../x", NOW).is_none());
    }

    struct FakeSteps<'a> {
        p: &'a Places,
        apply: Vec<Result<(), String>>,
        carry: Vec<Result<Option<Value>, String>>,
        calls: Vec<&'static str>,
        /// what the apply leaves: an opening record at the clone
        record: Value,
        /// what carrying on leaves: the opened record, or the opening at this step
        reach: Option<&'static str>,
    }

    impl Steps for FakeSteps<'_> {
        fn apply(&mut self, _plan: &Value, _domain: &str, code: &str) -> Result<(), String> {
            self.calls.push("apply");
            let r = self.apply.remove(0);
            if r.is_ok() {
                let mut rec = self.record.clone();
                rec["site"] = json!(code);
                fs::write(
                    self.p.creating.join(format!("{code}.json")),
                    rec.to_string(),
                )
                .unwrap();
            }
            r
        }
        fn carry_on(&mut self, _domain: &str, code: &str) -> Result<Option<Value>, String> {
            self.calls.push("carry_on");
            let r = self.carry.remove(0);
            let opening = self.p.creating.join(format!("{code}.json"));
            match self.reach {
                Some("health") => {
                    fs::rename(&opening, self.p.opened.join(format!("{code}.json"))).unwrap();
                }
                Some(step) => {
                    let mut rec: Value = read_json(&opening).unwrap();
                    rec["step"] = json!(step);
                    fs::write(&opening, rec.to_string()).unwrap();
                }
                None => {}
            }
            r
        }
    }

    fn fake<'a>(p: &'a Places) -> FakeSteps<'a> {
        FakeSteps {
            p,
            apply: vec![Ok(())],
            carry: vec![Ok(Some(json!({ "step": "health" })))],
            calls: Vec::new(),
            record: json!({ "hosts": "www.new.example.test", "step": "clone", "tenant_id": T }),
            reach: Some("health"),
        }
    }

    const PID: u32 = 4321;

    fn asked(p: &Places) -> String {
        let mut launched = Vec::new();
        let Asked::Opening(code) = started(p, T, "www.new.example.test", &mut launched) else {
            panic!("not started")
        };
        alive(p, PID, 777);
        code
    }

    fn ran(p: &Places, code: &str, steps: &mut FakeSteps<'_>) -> Result<&'static str, String> {
        run(
            p,
            code,
            "www.new.example.test",
            PID,
            steps,
            &mut || {},
            || NOW + 10,
        )
    }

    /// The whole way: apply, then carry on, then open; the ask names the run while it goes.
    #[test]
    fn a_run_applies_then_carries_on_to_the_end() {
        let p = scratch("run-ok");
        let code = asked(&p);
        let mut steps = fake(&p);
        assert_eq!(ran(&p, &code, &mut steps), Ok("open"));
        assert_eq!(steps.calls, ["apply", "carry_on"]);
        let ask = ask_on(&p, &code);
        assert_eq!(ask["state"], "open");
        assert_eq!(ask["holder"]["pid"], PID);
        assert_eq!(ask["holder"]["start_time"], 777);
        assert_eq!(progress(&p, T, &code, NOW + 20).unwrap()["state"], "open");
        // run again: nothing more is done
        let mut again = fake(&p);
        assert_eq!(ran(&p, &code, &mut again), Ok("open"));
        assert!(again.calls.is_empty());
    }

    /// A run cut off after the clone: run again, it carries on from the opening record and
    /// does not clone a second time.
    #[test]
    fn a_run_cut_off_resumes_from_the_opening_record() {
        let p = scratch("run-resume");
        let code = asked(&p);
        // the first run cloned and was gone (its process is not in /proc any more)
        let mut rec = fake(&p).record;
        rec["site"] = json!(code);
        fs::write(p.creating.join(format!("{code}.json")), rec.to_string()).unwrap();
        let mut ask = ask_on(&p, &code);
        ask["state"] = json!("running");
        ask["holder"] = json!({ "op": "x", "pid": 99, "start_time": 1, "boot_id": "0f0e0d0c-0b0a-4908-8706-050403020100" });
        rewrite_ask(&p.asked, &code, &ask).unwrap();
        assert_eq!(progress(&p, T, &code, NOW).unwrap()["state"], "stopped");
        let mut steps = fake(&p);
        assert_eq!(ran(&p, &code, &mut steps), Ok("open"));
        assert_eq!(steps.calls, ["carry_on"]);
    }

    /// A run that is still going is not run twice.
    #[test]
    fn a_run_still_going_is_not_run_twice() {
        let p = scratch("run-twice");
        let code = asked(&p);
        let mut ask = ask_on(&p, &code);
        ask["state"] = json!("running");
        ask["holder"] = json!({ "op": "x", "pid": PID, "start_time": 777, "boot_id": "0f0e0d0c-0b0a-4908-8706-050403020100" });
        rewrite_ask(&p.asked, &code, &ask).unwrap();
        let mut steps = fake(&p);
        assert!(ran(&p, &code, &mut steps)
            .unwrap_err()
            .contains("another run"));
        assert!(steps.calls.is_empty());
        assert_eq!(progress(&p, T, &code, NOW).unwrap()["state"], "opening");
    }

    /// A failure stops the opening where it was: the customer sees the step, not why; the
    /// ask keeps why for a person, and a stopped opening is not run again by itself.
    #[test]
    fn a_failure_stops_where_it_was_and_only_the_step_is_shown() {
        let p = scratch("run-fail");
        let code = asked(&p);
        let mut steps = fake(&p);
        steps.carry = vec![Err("install_failed: the installer said no".to_owned())];
        steps.reach = Some("start");
        assert_eq!(ran(&p, &code, &mut steps), Ok("stopped"));
        let seen = progress(&p, T, &code, NOW + 20).unwrap();
        assert_eq!(seen["state"], "stopped");
        assert_eq!(seen["step_done"], "start");
        assert_eq!(seen["stopped_at"], "install");
        assert!(seen.get("why").is_none() && !seen.to_string().contains("installer"));
        let ask = ask_on(&p, &code);
        assert_eq!(ask["why"], "install_failed");
        let mut again = fake(&p);
        assert!(ran(&p, &code, &mut again).is_err());
        assert!(again.calls.is_empty());
    }

    /// Stopped on a failure, then carried on to the end by a person (`--continue`, which
    /// does not write the ask): the customer sees it open, not stopped (E1 audit #5).
    #[test]
    fn a_stop_carried_on_by_a_person_reads_as_open() {
        let p = scratch("run-continued");
        let code = asked(&p);
        let mut steps = fake(&p);
        steps.carry = vec![Err("license_unknown: the key is not taken".to_owned())];
        steps.reach = Some("identity");
        assert_eq!(ran(&p, &code, &mut steps), Ok("stopped"));
        assert_eq!(ask_on(&p, &code)["state"], "stopped");
        // the person's --continue: the opening record becomes the opened one
        fs::rename(
            p.creating.join(format!("{code}.json")),
            p.opened.join(format!("{code}.json")),
        )
        .unwrap();
        let seen = progress(&p, T, &code, NOW + 20).unwrap();
        assert_eq!(seen["state"], "open");
        assert_eq!(seen["step_done"], "health");
        assert_eq!(seen["stopped_at"], Value::Null);
        // an ask that reads as anything else (failed, a word unknown) is open too
        let mut ask = ask_on(&p, &code);
        ask["state"] = json!("failed");
        rewrite_ask(&p.asked, &code, &ask).unwrap();
        assert_eq!(progress(&p, T, &code, NOW + 20).unwrap()["state"], "open");
    }

    /// Stopped and not carried on yet: still stopped, at the step it stopped at.
    #[test]
    fn a_stop_not_carried_on_yet_still_reads_as_stopped() {
        let p = scratch("run-not-continued");
        let code = asked(&p);
        let mut steps = fake(&p);
        steps.carry = vec![Err("license_unknown: the key is not taken".to_owned())];
        steps.reach = Some("identity");
        assert_eq!(ran(&p, &code, &mut steps), Ok("stopped"));
        let seen = progress(&p, T, &code, NOW + 20).unwrap();
        assert_eq!(seen["state"], "stopped");
        assert_eq!(seen["step_done"], "identity");
        assert_eq!(seen["stopped_at"], "licenses");
    }

    /// A step kept for a person shows where it stopped, from the opening record.
    #[test]
    fn a_step_kept_for_a_person_is_where_it_stopped() {
        let p = scratch("run-kept");
        let code = asked(&p);
        let mut rec = fake(&p).record;
        rec["site"] = json!(code);
        rec["step"] = json!("identity");
        rec["kept"] = json!({ "step": "licenses", "why": "x" });
        fs::write(p.creating.join(format!("{code}.json")), rec.to_string()).unwrap();
        let seen = progress(&p, T, &code, NOW + 5).unwrap();
        assert_eq!(seen["state"], "stopped");
        assert_eq!(seen["stopped_at"], "licenses");
        assert_eq!(seen["step_done"], "identity");
    }

    /// A failed clone stops at the clone; a held start stops at the start.
    #[test]
    fn a_failed_clone_and_a_held_start_say_their_step() {
        let p = scratch("run-clone");
        let code = asked(&p);
        let mut steps = fake(&p);
        steps.apply = vec![Err(
            "plan_changed: this host is not as the plan saw it".to_owned()
        )];
        assert_eq!(ran(&p, &code, &mut steps), Ok("stopped"));
        assert_eq!(steps.calls, ["apply"]);
        let seen = progress(&p, T, &code, NOW + 20).unwrap();
        assert_eq!(seen["stopped_at"], "clone");
        assert_eq!(ask_on(&p, &code)["why"], "plan_changed");

        let q = scratch("run-held");
        let code = asked(&q);
        let mut steps = fake(&q);
        steps.carry = vec![Ok(Some(
            json!({ "step": "firewall", "held": "mac_not_local" }),
        ))];
        steps.reach = Some("firewall");
        assert_eq!(ran(&q, &code, &mut steps), Ok("stopped"));
        let seen = progress(&q, T, &code, NOW + 20).unwrap();
        assert_eq!(seen["stopped_at"], "start");
        assert_eq!(seen["step_done"], "firewall");
    }

    /// A unit that starts after its ask stopped waiting does not take the ask up: the
    /// domain was let go of when the ask read as stopped (33e28 N2).
    #[test]
    fn a_run_started_too_late_does_not_take_its_ask() {
        let p = scratch("run-late");
        let code = asked(&p);
        let mut steps = fake(&p);
        let got = run(
            &p,
            &code,
            "www.new.example.test",
            PID,
            &mut steps,
            &mut || {},
            || NOW + START_GRACE_S,
        );
        assert!(got.is_err(), "{got:?}");
        assert!(steps.calls.is_empty());
        let ask = ask_on(&p, &code);
        assert_eq!(ask["state"], "stopped");
        assert_eq!(ask["why"], "start_late");
        assert_eq!(ask["holder"], Value::Null);
        // and it stays a stop for a person: running it again does nothing either
        assert!(ran(&p, &code, &mut steps).is_err());
        assert!(steps.calls.is_empty());
    }

    /// The host lock held by another operation: asked again, then given up as host_busy.
    #[test]
    fn a_busy_host_lock_is_waited_for_then_given_up() {
        let p = scratch("run-busy");
        let code = asked(&p);
        let mut steps = fake(&p);
        let busy = || {
            Err(
                "host is locked by operation 20261111T000000Z-00000000 (pid 9); not writing"
                    .to_owned(),
            )
        };
        steps.apply = vec![busy(), busy(), Ok(())];
        let mut pauses = 0;
        let got = run(
            &p,
            &code,
            "www.new.example.test",
            PID,
            &mut steps,
            &mut || pauses += 1,
            || NOW,
        );
        assert_eq!(got, Ok("open"));
        assert_eq!(pauses, 2);

        let q = scratch("run-busy2");
        let code = asked(&q);
        let mut steps = fake(&q);
        steps.apply = (0..LOCK_TRIES).map(|_| busy()).collect();
        assert_eq!(ran(&q, &code, &mut steps), Ok("stopped"));
        assert_eq!(steps.calls.len(), LOCK_TRIES as usize);
        assert_eq!(ask_on(&q, &code)["why"], "host_busy");
    }

    /// The run is for the ask's own domain only.
    #[test]
    fn a_run_for_another_domain_is_refused() {
        let p = scratch("run-other");
        let code = asked(&p);
        let mut steps = fake(&p);
        let got = run(
            &p,
            &code,
            "other.example.test",
            PID,
            &mut steps,
            &mut || {},
            || NOW,
        );
        assert!(got.is_err());
        assert!(steps.calls.is_empty());
    }

    #[test]
    fn the_unit_runs_this_program_for_the_code_alone() {
        assert_eq!(
            unit_argv("www.new.example.test", "s-abc123"),
            [
                "systemd-run",
                "--unit=ez2-site-create-s-abc123",
                "--collect",
                "--quiet",
                "--",
                HOST_BIN,
                "site",
                "create",
                "www.new.example.test",
                "--run",
                "s-abc123"
            ]
        );
        assert_eq!(word_of("clone_failed: x"), "clone_failed");
        assert_eq!(word_of("/var/x: denied"), "failed");
    }
}
