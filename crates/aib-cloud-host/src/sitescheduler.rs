// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site scheduler <code> --check | --plan | --apply [--records dir]`
//! (E10 6.2, 4542e 11-12): the scheduler of a site opened before the install put it in
//! place — the site software's own scheduler off (`DISABLE_WP_CRON`) and a timer in the
//! container that runs the due events every five minutes as the site's own user. The
//! writing is [`crate::siteidentity::scheduler_write`], the same the install and a restore
//! use; nothing here writes otherwise.
//!
//! **Three answers, and only one writes.** Every part is read before anything is decided
//! ([`observe`]): the constant, both unit files (are they plain files, what they hold),
//! what systemd has loaded for each (from where, with which drop-ins, whether it wants a
//! reload) and whether the timer is enabled and active. A part is known only from an
//! answer that says so — `config has` exiting 1, systemd's `not-found`, a file not listed
//! — never from a failure: any other failure, a timeout, more output than the cap, makes
//! the site [`State::Unknown`], and nothing is written. A unit that is a link, comes from
//! elsewhere, carries drop-ins or is masked is someone else's: [`State::Conflict`], left
//! alone. Only [`State::Needs`] reaches the writing (Codex 11-12 on the plan: the shared
//! `scheduler` writes when its first reading fails; this command must not).
//!
//! **Where nothing is run.** A quarantined container is not entered (Andy 11-10), nor one
//! that is not running; a firewall file or a container status that cannot be read stops
//! the same way. The command never starts a container.
//!
//! **`--apply`** is an operation (`site.scheduler` on `site:<code>`, approved by root on
//! the host only; a customer's key cannot): under the host lock the gate and the reading
//! are done again, a site already in place answers "no change" and leaves no journal, a
//! refused one leaves none either; a change is journaled, written, and read again with
//! [`observe`] — `verified` means the scheduler reads back as set, not that its first run
//! went well. The first run follows at once: the host has been up longer than the
//! timer's two minutes. `--check` shows how the last run ended.
//!
//! **What `--check` and `--plan` also show** (c477a M1 on the hand-written sheet): the due
//! events by hook name and count (never their arguments), the site's automatic update
//! settings, and the background queue's counts by state when it has one. These are for
//! the person who signs; they change no answer. Output is codes, names and counts, never
//! what a file holds.

use std::cell::RefCell;
use std::fs;
use std::path::PathBuf;

use aib_cloud::quarantine;
use aib_cloud::site::SiteSpec;
use serde_json::{json, Value};

use crate::opstore;
use crate::sitecreatecmd::Runner;
use crate::siteidentity::{cron_files, cron_unit, in_ct, scheduler_write, wp};

pub const COMMAND: &str = "site.scheduler";
const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const FIREWALL_DIR: &str = "/etc/pve/firewall";
const UNIT_DIR: &str = "/etc/systemd/system";
/// What systemd is asked about each unit; every one must be answered exactly once.
const UNIT_KEYS: [&str; 6] = [
    "LoadState",
    "UnitFileState",
    "ActiveState",
    "FragmentPath",
    "DropInPaths",
    "NeedDaemonReload",
];
/// The site software without the customer's plugins and theme, for every reading here.
const QUIET: [&str; 2] = ["--skip-plugins", "--skip-themes"];

// ------------------------------------------------------------------ answers

/// The exit code a runner's failure carries, when the program ran and exited: the runners
/// end such a failure with `exited exit status: <n>`. A timeout, too much output, a signal
/// or a program that could not start has none.
pub fn exit_code(err: &str) -> Option<i32> {
    let (_, n) = err.rsplit_once("exited exit status: ")?;
    if n.is_empty() || !n.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    n.parse().ok()
}

/// What the reading found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    /// Everything as the install leaves it.
    InPlace,
    /// Every part read, and these are not as they should be.
    Needs(Vec<&'static str>),
    /// A part that could not be read: nothing may be written.
    Unknown(&'static str),
    /// A part someone else put there: left alone.
    Conflict(&'static str),
}

impl State {
    pub fn word(&self) -> &'static str {
        match self {
            State::InPlace => "in_place",
            State::Needs(_) => "needs",
            State::Unknown(c) | State::Conflict(c) => c,
        }
    }
}

/// One finding of the reading.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Found {
    Needs(&'static str),
    Unknown(&'static str),
    Conflict(&'static str),
}

/// Every finding together: one unknown makes the whole unknown (nothing is written on a
/// guess), then one conflict; otherwise what needs putting right, or in place.
pub fn verdict(found: &[Found]) -> State {
    if let Some(c) = found.iter().find_map(|f| match f {
        Found::Unknown(c) => Some(*c),
        _ => None,
    }) {
        return State::Unknown(c);
    }
    if let Some(c) = found.iter().find_map(|f| match f {
        Found::Conflict(c) => Some(*c),
        _ => None,
    }) {
        return State::Conflict(c);
    }
    let needs: Vec<&'static str> = found
        .iter()
        .filter_map(|f| match f {
            Found::Needs(c) => Some(*c),
            _ => None,
        })
        .collect();
    if needs.is_empty() {
        State::InPlace
    } else {
        State::Needs(needs)
    }
}

/// `systemctl show <unit> -p …` as key → value: every key of [`UNIT_KEYS`] once, nothing
/// else; anything short of that is not an answer.
pub fn parse_unit(text: &str) -> Option<Vec<(String, String)>> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.lines().filter(|l| !l.is_empty()) {
        let (k, v) = line.split_once('=')?;
        if !UNIT_KEYS.contains(&k) || out.iter().any(|(seen, _)| seen == k) {
            return None;
        }
        out.push((k.to_owned(), v.to_owned()));
    }
    (out.len() == UNIT_KEYS.len()).then_some(out)
}

fn key<'a>(unit: &'a [(String, String)], k: &str) -> &'a str {
    unit.iter()
        .find(|(name, _)| name == k)
        .map(|(_, v)| v.as_str())
        .unwrap_or("")
}

/// `find <dir> -maxdepth 1 -name 'wpcron-<code>.*' -printf '%f %y\n'`: the type of each
/// listed name (`f` a plain file, `l` a link, …). A line that is not `<name> <type>` makes
/// the listing no answer.
pub fn parse_listing(text: &str) -> Option<Vec<(String, String)>> {
    text.lines()
        .filter(|l| !l.is_empty())
        .map(|l| {
            let (name, kind) = l.rsplit_once(' ')?;
            (!name.is_empty() && kind.len() == 1).then(|| (name.to_owned(), kind.to_owned()))
        })
        .collect()
}

/// One unit's findings, from what systemd said about it, whether and how it is listed in
/// the unit folder, and what the file holds (read only when it is a plain file).
pub fn judge_unit(
    unit: &str,
    is_timer: bool,
    shown: Option<&str>,
    listed: Option<&str>,
    held: Option<Result<&str, ()>>,
    want: &str,
) -> Vec<Found> {
    let Some(shown) = shown.and_then(parse_unit) else {
        return vec![Found::Unknown("cron_unit_unread")];
    };
    let mut out = Vec::new();
    let path = format!("{UNIT_DIR}/{unit}");
    match key(&shown, "LoadState") {
        "loaded" => {
            if key(&shown, "FragmentPath") != path {
                out.push(Found::Conflict("cron_unit_elsewhere"));
            }
            if !key(&shown, "DropInPaths").is_empty() {
                out.push(Found::Conflict("cron_unit_dropin"));
            }
        }
        "not-found" => out.push(Found::Needs("cron_unit_not_loaded")),
        "masked" => out.push(Found::Conflict("cron_unit_masked")),
        _ => out.push(Found::Unknown("cron_unit_load_state")),
    }
    match listed {
        None => out.push(Found::Needs("cron_unit_missing")),
        Some("f") => match held {
            Some(Ok(text)) if text == want => {}
            Some(Ok(_)) => out.push(Found::Needs("cron_unit_differs")),
            Some(Err(())) | None => out.push(Found::Unknown("cron_unit_unread")),
        },
        Some(_) => out.push(Found::Conflict("cron_unit_not_file")),
    }
    match key(&shown, "NeedDaemonReload") {
        "no" => {}
        "yes" => out.push(Found::Needs("cron_unit_reload")),
        _ => out.push(Found::Unknown("cron_unit_unread")),
    }
    if is_timer {
        if key(&shown, "UnitFileState") != "enabled" {
            out.push(Found::Needs("cron_timer_disabled"));
        }
        if key(&shown, "ActiveState") != "active" {
            out.push(Found::Needs("cron_timer_inactive"));
        }
    }
    out
}

/// The site software's own scheduler: `config has` exiting 0 then `config get` reading
/// `1` / `true` is off as it should be; `config has` exiting 1 is a constant not there.
pub fn judge_constant(has: Result<(), Option<i32>>, got: Option<Result<&str, ()>>) -> Vec<Found> {
    match (has, got) {
        (Err(Some(1)), _) => vec![Found::Needs("cron_not_set")],
        (Err(_), _) => vec![Found::Unknown("cron_constant_unread")],
        (Ok(()), Some(Ok(v))) if matches!(v.trim(), "1" | "true") => Vec::new(),
        (Ok(()), Some(Ok(_))) => vec![Found::Needs("cron_not_set")],
        (Ok(()), _) => vec![Found::Unknown("cron_constant_unread")],
    }
}

/// Read every part inside the container and judge it ([`verdict`]). Reads only.
pub fn observe(runner: &mut impl Runner, spec: &SiteSpec) -> (State, Vec<Found>) {
    let mut found = Vec::new();
    let has = runner
        .run(&wp(spec, &[QUIET[0], QUIET[1], "config", "has", "DISABLE_WP_CRON"]))
        .map(|_| ())
        .map_err(|e| exit_code(&e));
    let got = if has.is_ok() {
        Some(
            runner
                .run(&wp(spec, &[QUIET[0], QUIET[1], "config", "get", "DISABLE_WP_CRON"]))
                .map_err(|_| ()),
        )
    } else {
        None
    };
    found.extend(judge_constant(has, got.as_ref().map(|r| r.as_deref().map_err(|_| ()))));
    let pattern = format!("{}.*", cron_unit(&spec.code));
    let listing = runner
        .run(&in_ct(
            spec.ct,
            &["find", UNIT_DIR, "-maxdepth", "1", "-name", &pattern, "-printf", "%f %y\\n"],
        ))
        .ok()
        .and_then(|t| parse_listing(&t));
    for (file, want) in cron_files(&spec.code) {
        let unit = file.rsplit('/').next().unwrap_or("").to_owned();
        let is_timer = unit.ends_with(".timer");
        let shown = runner
            .run(&in_ct(
                spec.ct,
                &[
                    "systemctl", "show", &unit, "-p", "LoadState", "-p", "UnitFileState", "-p",
                    "ActiveState", "-p", "FragmentPath", "-p", "DropInPaths", "-p",
                    "NeedDaemonReload",
                ],
            ))
            .ok();
        let Some(listing) = listing.as_ref() else {
            found.push(Found::Unknown("cron_unit_unread"));
            continue;
        };
        let listed = listing
            .iter()
            .find(|(name, _)| *name == unit)
            .map(|(_, kind)| kind.as_str());
        let held = if listed == Some("f") {
            Some(runner.run(&in_ct(spec.ct, &["cat", &file])).map_err(|_| ()))
        } else {
            None
        };
        found.extend(judge_unit(
            &unit,
            is_timer,
            shown.as_deref(),
            listed,
            held.as_ref().map(|r| r.as_deref().map_err(|_| ())),
            &want,
        ));
    }
    (verdict(&found), found)
}

// ------------------------------------------------------------------ where nothing is run

/// Whether the container may be entered: its firewall file says it is not quarantined and
/// its status says running. What cannot be read stops it too.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    Run,
    Skip(&'static str),
}

pub fn gate(firewall: Option<&str>, status: Option<&str>) -> Gate {
    let Some(fw) = firewall else {
        return Gate::Skip("firewall_unread");
    };
    if quarantine::is_quarantined(fw) {
        return Gate::Skip("skipped_quarantined");
    }
    match status.map(str::trim) {
        Some("status: running") => Gate::Run,
        Some("status: stopped") => Gate::Skip("ct_not_running"),
        _ => Gate::Skip("ct_status_unread"),
    }
}

/// Where a site's record and its container's firewall file are read from.
pub struct Places {
    pub records: PathBuf,
    pub firewall: PathBuf,
}

impl Places {
    pub fn host() -> Self {
        Places {
            records: PathBuf::from(RECORDS),
            firewall: PathBuf::from(FIREWALL_DIR),
        }
    }
}

fn gate_of(runner: &mut impl Runner, places: &Places, spec: &SiteSpec) -> Gate {
    let fw = fs::read_to_string(places.firewall.join(format!("{}.fw", spec.ct))).ok();
    let ct = spec.ct.to_string();
    let status = runner.run(&["pct".to_owned(), "status".to_owned(), ct]).ok();
    gate(fw.as_deref(), status.as_deref())
}

fn load(places: &Places, code: &str) -> Result<SiteSpec, String> {
    crate::quarantinecmd::load_spec(code, &places.records.to_string_lossy())
}

// ------------------------------------------------------------------ for the person who signs

/// A name that may be shown: hook names, states, settings. Anything else is `other`.
fn shown_name(s: &str) -> &str {
    let ok = !s.is_empty()
        && s.len() <= 80
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-/:.".contains(&b));
    if ok {
        s
    } else {
        "other"
    }
}

/// `cron event list --fields=hook,time --format=csv`: the events due at `now` (time ≤ now)
/// counted by hook, and how long the oldest has waited. `None` when it is not that table.
pub fn due_events(csv: &str, now: u64) -> Option<(Vec<(String, u32)>, u64)> {
    let mut lines = csv.lines().filter(|l| !l.is_empty());
    if lines.next()? != "hook,time" {
        return None;
    }
    let mut by: Vec<(String, u32)> = Vec::new();
    let mut oldest = 0;
    for line in lines {
        let (hook, time) = line.rsplit_once(',')?;
        let t: u64 = time.parse().ok()?;
        if t > now {
            continue;
        }
        oldest = oldest.max(now - t);
        let name = shown_name(hook).to_owned();
        match by.iter_mut().find(|(h, _)| *h == name) {
            Some((_, n)) => *n += 1,
            None => by.push((name, 1)),
        }
    }
    by.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    Some((by, oldest))
}

/// A constant of the site's configuration as shown: its value when it is a plain word,
/// `undefined` when `config has` says it is not there, `unread` otherwise.
fn constant(runner: &mut impl Runner, spec: &SiteSpec, name: &str) -> String {
    match runner.run(&wp(spec, &[QUIET[0], QUIET[1], "config", "has", name])) {
        Err(e) if exit_code(&e) == Some(1) => return "undefined".to_owned(),
        Err(_) => return "unread".to_owned(),
        Ok(_) => {}
    }
    match runner.run(&wp(spec, &[QUIET[0], QUIET[1], "config", "get", name])) {
        Ok(v) => shown_name(v.trim()).to_owned(),
        Err(_) => "unread".to_owned(),
    }
}

/// How many entries a list option holds (`option get … --format=json`): `0` when the
/// option is not there, `unread` otherwise.
fn list_option(runner: &mut impl Runner, spec: &SiteSpec, name: &str) -> String {
    match runner.run(&wp(spec, &[QUIET[0], QUIET[1], "option", "get", name, "--format=json"])) {
        Ok(t) => match serde_json::from_str::<Value>(t.trim()) {
            Ok(Value::Array(a)) => a.len().to_string(),
            Ok(Value::Object(o)) => o.len().to_string(),
            _ => "unread".to_owned(),
        },
        Err(e) if exit_code(&e) == Some(1) => "0".to_owned(),
        Err(_) => "unread".to_owned(),
    }
}

/// A table prefix that may stand in a query.
fn valid_prefix(p: &str) -> bool {
    !p.is_empty() && p.len() <= 32 && p.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

/// The background queue's counts by state (`none` when the site has no such table).
fn queue(runner: &mut impl Runner, spec: &SiteSpec) -> String {
    let Ok(prefix) = runner.run(&wp(spec, &[QUIET[0], QUIET[1], "db", "prefix"])) else {
        return "unread".to_owned();
    };
    let prefix = prefix.trim();
    if !valid_prefix(prefix) {
        return "unread".to_owned();
    }
    let table = format!("{prefix}actionscheduler_actions");
    let tables = format!("SHOW TABLES LIKE '{table}'");
    match runner.run(&wp(
        spec,
        &[QUIET[0], QUIET[1], "db", "query", &tables, "--skip-column-names"],
    )) {
        Ok(t) if t.trim().is_empty() => return "none".to_owned(),
        Ok(t) if t.trim() == table => {}
        _ => return "unread".to_owned(),
    }
    let count = format!("SELECT status, COUNT(*) FROM {table} GROUP BY status");
    let Ok(rows) = runner.run(&wp(
        spec,
        &[QUIET[0], QUIET[1], "db", "query", &count, "--skip-column-names"],
    )) else {
        return "unread".to_owned();
    };
    let mut out = Vec::new();
    for row in rows.lines().filter(|l| !l.is_empty()) {
        let Some((state, n)) = row.split_once('\t') else {
            return "unread".to_owned();
        };
        if !n.bytes().all(|b| b.is_ascii_digit()) {
            return "unread".to_owned();
        }
        out.push(format!("{}={n}", shown_name(state)));
    }
    if out.is_empty() {
        "empty".to_owned()
    } else {
        out.join(",")
    }
}

/// How the scheduler's last run ended, from systemd: `Result` and the exit status.
fn last_run(runner: &mut impl Runner, spec: &SiteSpec) -> String {
    let service = format!("{}.service", cron_unit(&spec.code));
    match runner.run(&in_ct(
        spec.ct,
        &["systemctl", "show", &service, "-p", "Result", "-p", "ExecMainStatus"],
    )) {
        Ok(t) => {
            let mut parts = Vec::new();
            for l in t.lines().filter(|l| !l.is_empty()) {
                let Some((k, v)) = l.split_once('=') else {
                    return "unread".to_owned();
                };
                parts.push(format!("{}={}", shown_name(k), shown_name(v)));
            }
            parts.join(",")
        }
        Err(_) => "unread".to_owned(),
    }
}

/// What the person who signs must see first among the due events (4542e 11-12, c477a M1):
/// how many may change the site's version (the automatic update and the check that
/// starts it), send mail, or publish scheduled posts.
pub fn marked(by: &[(String, u32)]) -> (u32, u32, u32) {
    let mut out = (0, 0, 0);
    for (hook, n) in by {
        let h = hook.to_ascii_lowercase();
        if h == "wp_maybe_auto_update" || h == "wp_version_check" {
            out.0 += n;
        }
        if h.contains("mail") {
            out.1 += n;
        }
        if h == "publish_future_post" {
            out.2 += n;
        }
    }
    out
}

/// The lines `--check` and `--plan` show after the answer.
fn details(runner: &mut impl Runner, spec: &SiteSpec, now: u64) -> Vec<String> {
    let mut lines = Vec::new();
    let events = runner.run(&wp(
        spec,
        &[QUIET[0], QUIET[1], "cron", "event", "list", "--fields=hook,time", "--format=csv"],
    ));
    match events.ok().as_deref().and_then(|t| due_events(t, now)) {
        Some((by, oldest)) => {
            let total: u32 = by.iter().map(|(_, n)| n).sum();
            let hooks: Vec<String> = by.iter().map(|(h, n)| format!("{h}={n}")).collect();
            lines.push(format!(
                "due {total} oldest_waiting_s={oldest} hooks: {}",
                hooks.join(" ")
            ));
            let (update, mail, publish) = marked(&by);
            lines.push(format!(
                "marked update={update} mail={mail} publish={publish}"
            ));
        }
        None => lines.push("due unread".to_owned()),
    }
    lines.push(format!(
        "auto_update core={} disabled={} plugins={} themes={}",
        constant(runner, spec, "WP_AUTO_UPDATE_CORE"),
        constant(runner, spec, "AUTOMATIC_UPDATER_DISABLED"),
        list_option(runner, spec, "auto_update_plugins"),
        list_option(runner, spec, "auto_update_themes"),
    ));
    lines.push(format!("queue {}", queue(runner, spec)));
    lines.push(format!("last_run {}", last_run(runner, spec)));
    lines
}

// ------------------------------------------------------------------ the three modes

/// The writing steps, as `--plan` shows them and the journal's plan carries them.
fn steps(spec: &SiteSpec) -> Vec<String> {
    let unit = cron_unit(&spec.code);
    vec![
        "config set DISABLE_WP_CRON true --raw".to_owned(),
        format!("write {UNIT_DIR}/{unit}.service"),
        format!("write {UNIT_DIR}/{unit}.timer"),
        "systemctl daemon-reload".to_owned(),
        format!("systemctl enable --now {unit}.timer"),
    ]
}

fn plan_of(spec: &SiteSpec, needs: &[&'static str]) -> Value {
    json!({
        "command": COMMAND,
        "site": spec.code,
        "ct": spec.ct,
        "needs": needs,
        "steps": steps(spec),
    })
}

/// `--check`: 0 in place, 1 something to put right, 3 not looked at or not known.
pub fn check(runner: &mut impl Runner, places: &Places, code: &str, now: u64) -> Result<(u8, Vec<String>), String> {
    let spec = load(places, code)?;
    if let Gate::Skip(why) = gate_of(runner, places, &spec) {
        return Ok((3, vec![format!("{code} {why}")]));
    }
    let (state, found) = observe(runner, &spec);
    let rc = match state {
        State::InPlace => 0,
        State::Needs(_) => 1,
        State::Unknown(_) | State::Conflict(_) => 3,
    };
    let codes: Vec<&str> = found
        .iter()
        .map(|f| match f {
            Found::Needs(c) | Found::Unknown(c) | Found::Conflict(c) => *c,
        })
        .collect();
    let mut lines = vec![format!(
        "{code} {} {}",
        state.word(),
        if codes.is_empty() { "-".to_owned() } else { codes.join(",") }
    )];
    lines.extend(details(runner, &spec, now));
    Ok((rc, lines))
}

/// `--plan`: what `--apply` would do now. 0 with a plan or "no change", 3 when it would
/// refuse.
pub fn plan(runner: &mut impl Runner, places: &Places, code: &str, now: u64) -> Result<(u8, Vec<String>), String> {
    let spec = load(places, code)?;
    if let Gate::Skip(why) = gate_of(runner, places, &spec) {
        return Ok((3, vec![format!("{code} {why}")]));
    }
    let (rc, head) = match observe(runner, &spec).0 {
        State::InPlace => (0, format!("{code} no_change")),
        State::Needs(needs) => (0, plan_of(&spec, &needs).to_string()),
        other => (3, format!("{code} refused {}", other.word())),
    };
    let mut lines = vec![head];
    lines.extend(details(runner, &spec, now));
    Ok((rc, lines))
}

/// What `--apply` did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Applied {
    Changed,
    NoChange,
    Skipped(&'static str),
}

/// `--apply` as an operation on `site:<code>` ([`opstore::run_op_prepared`]): the gate and
/// the reading under the lock; in place or refused ends there without a journal; a change
/// is [`scheduler_write`], then [`observe`] must say in place.
pub fn apply<R: Runner>(
    place: &opstore::OpPlace,
    places: &Places,
    code: &str,
    approval: &str,
    runner: &mut R,
    now: u64,
) -> Result<Applied, String> {
    let spec = load(places, code)?;
    let target = format!("site:{}", spec.code);
    // what ended the operation before any change: read after it returns
    let ended: RefCell<Option<Applied>> = RefCell::new(None);
    let (end, site) = (&ended, &spec);
    let done = opstore::run_op_prepared(
        place,
        COMMAND,
        &target,
        approval,
        now,
        // the runner goes through the prepared input: one closure holds it at a time
        move || {
            if let Gate::Skip(why) = gate_of(runner, places, site) {
                *end.borrow_mut() = Some(Applied::Skipped(why));
                return Ok(None);
            }
            match observe(runner, site).0 {
                State::InPlace => {
                    *end.borrow_mut() = Some(Applied::NoChange);
                    Ok(None)
                }
                State::Needs(needs) => Ok(Some((plan_of(site, &needs), runner))),
                State::Unknown(c) | State::Conflict(c) => {
                    *end.borrow_mut() = Some(Applied::Skipped(c));
                    Ok(None)
                }
            }
        },
        |_, runner: &mut R| {
            scheduler_write(runner, site)?;
            match observe(runner, site).0 {
                State::InPlace => Ok(()),
                other => Err(format!("cron_not_in_place_after: {}", other.word())),
            }
        },
    )?;
    let ended = ended.into_inner();
    match (done, ended) {
        (Some(()), _) => Ok(Applied::Changed),
        (None, Some(a)) => Ok(a),
        (None, None) => Err("scheduler: nothing was decided".to_owned()),
    }
}

// ------------------------------------------------------------------ command line

#[derive(Debug, PartialEq, Eq)]
enum Mode {
    Check,
    Plan,
    Apply,
}

/// `<code>` and exactly one mode; `--records <dir>` may come once. Anything else is a
/// usage error.
fn parse(args: &[String]) -> Result<(String, Mode, Places), String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with('-'))
        .ok_or("needs <code>")?
        .clone();
    if !aib_cloud::site::valid_code(&code) {
        return Err(format!("{code:?} is not a site code"));
    }
    let mut mode = None;
    let mut places = Places::host();
    let mut records = false;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        let m = match a.as_str() {
            "--check" => Mode::Check,
            "--plan" => Mode::Plan,
            "--apply" => Mode::Apply,
            "--records" if !records => {
                records = true;
                places.records = PathBuf::from(it.next().ok_or("--records needs a folder")?);
                continue;
            }
            other => return Err(format!("unknown argument {other:?}")),
        };
        if mode.replace(m).is_some() {
            return Err("one of --check, --plan, --apply".to_owned());
        }
    }
    let mode = mode.ok_or("one of --check, --plan, --apply")?;
    Ok((code, mode, places))
}

/// `site scheduler <code> --check | --plan | --apply [--records dir]`.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    // a mistyped argument must never fall through to a mode (c477a L1 on the hand-written
    // sheet: `--dryrun` became a real run): every usage error is 64, nothing read or run
    let (code, mode, places) = match parse(args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("aib-cloud-host site scheduler: {e}");
            eprintln!("usage: site scheduler <code> --check | --plan | --apply [--records dir]");
            return Ok(64);
        }
    };
    let now = crate::probe::unix_now();
    let mut runner = crate::sitecreatecmd::HostRunner;
    let (rc, lines) = match mode {
        Mode::Check => check(&mut runner, &places, &code, now)?,
        Mode::Plan => plan(&mut runner, &places, &code, now)?,
        Mode::Apply => {
            let place = opstore::OpPlace::host()?;
            match apply(&place, &places, &code, opstore::BY_LOCAL_ROOT, &mut runner, now)? {
                Applied::Changed => (0, vec![format!("{code} changed")]),
                Applied::NoChange => (0, vec![format!("{code} no_change")]),
                Applied::Skipped(why) => (3, vec![format!("{code} skipped {why}")]),
            }
        }
    };
    for l in lines {
        println!("{l}");
    }
    Ok(rc)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::path::Path;

    const CODE: &str = "s-sched1";
    const CT: u32 = 131;
    const NOW: u64 = 1_800_000_000;

    fn spec() -> SiteSpec {
        SiteSpec::new(
            CODE,
            CT,
            31,
            &["sched.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    fn exited(n: i32) -> String {
        format!("\"pct\" exited exit status: {n}")
    }

    fn unit_text(load: &str, file: &str, active: &str, frag: &str, dropin: &str, reload: &str) -> String {
        format!(
            "LoadState={load}\nUnitFileState={file}\nActiveState={active}\nFragmentPath={frag}\nDropInPaths={dropin}\nNeedDaemonReload={reload}\n"
        )
    }

    /// A site container as the commands see it. Writing commands change it the way the
    /// real ones would; `fail` makes every command whose line holds it fail with no exit
    /// code (a timeout), `exit` makes them exit with a code.
    struct Ct {
        calls: Vec<String>,
        status: &'static str,
        /// `config has DISABLE_WP_CRON`: `Ok` there, `Err(n)` exits n
        has: Result<(), i32>,
        value: String,
        /// unit file name → (find type, content)
        files: BTreeMap<String, (String, String)>,
        loaded: bool,
        enabled: bool,
        active: bool,
        dropin: bool,
        fail: Option<&'static str>,
        exit: Option<(&'static str, i32)>,
        /// writes that do not take (a unit file written but read back different)
        sticky: bool,
    }

    impl Ct {
        fn fresh() -> Self {
            Ct {
                calls: Vec::new(),
                status: "status: running\n",
                has: Err(1),
                value: String::new(),
                files: BTreeMap::new(),
                loaded: false,
                enabled: false,
                active: false,
                dropin: false,
                fail: None,
                exit: None,
                sticky: false,
            }
        }
        fn in_place() -> Self {
            let mut c = Ct::fresh();
            c.has = Ok(());
            c.value = "1".to_owned();
            for (file, text) in cron_files(CODE) {
                let name = file.rsplit('/').next().unwrap().to_owned();
                c.files.insert(name, ("f".to_owned(), text));
            }
            c.loaded = true;
            c.enabled = true;
            c.active = true;
            c
        }
        fn writes(&self) -> Vec<&String> {
            self.calls
                .iter()
                .filter(|l| {
                    l.contains(" tee ")
                        || l.contains("config set")
                        || l.contains("daemon-reload")
                        || l.contains("enable --now")
                })
                .collect()
        }
        fn entered(&self) -> usize {
            self.calls.iter().filter(|l| l.starts_with("pct exec")).count()
        }
        fn show(&self, unit: &str) -> String {
            let timer = unit.ends_with(".timer");
            if !self.loaded {
                return unit_text("not-found", "", "inactive", "", "", "no");
            }
            unit_text(
                "loaded",
                if timer && self.enabled { "enabled" } else if timer { "disabled" } else { "static" },
                if timer && self.active { "active" } else { "inactive" },
                &format!("{UNIT_DIR}/{unit}"),
                if self.dropin { "/etc/systemd/system/x.d/override.conf" } else { "" },
                "no",
            )
        }
    }

    impl Runner for Ct {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            if self.fail.is_some_and(|f| line.contains(f)) {
                return Err("\"pct\": timeout".to_owned());
            }
            if let Some((f, n)) = self.exit {
                if line.contains(f) {
                    return Err(exited(n));
                }
            }
            if line.starts_with("pct status") {
                return Ok(self.status.to_owned());
            }
            if line.contains("config has DISABLE_WP_CRON") {
                return self.has.map(|()| String::new()).map_err(exited);
            }
            if line.contains("config get DISABLE_WP_CRON") {
                return Ok(format!("{}\n", self.value));
            }
            if line.contains("config set DISABLE_WP_CRON true --raw") {
                self.has = Ok(());
                self.value = "1".to_owned();
                return Ok(String::new());
            }
            if line.contains("config has ") {
                return Err(exited(1));
            }
            if line.contains(" find ") {
                return Ok(self
                    .files
                    .iter()
                    .map(|(n, (k, _))| format!("{n} {k}\n"))
                    .collect());
            }
            if line.contains("systemctl show") && line.contains("-p Result") {
                return Ok("Result=success\nExecMainStatus=0\n".to_owned());
            }
            if line.contains("systemctl show") {
                let unit = argv[argv.iter().position(|a| a == "show").unwrap() + 1].clone();
                return Ok(self.show(&unit));
            }
            if let [.., cat, file] = argv {
                if cat == "cat" {
                    let name = file.rsplit('/').next().unwrap();
                    return self
                        .files
                        .get(name)
                        .map(|(_, t)| t.clone())
                        .ok_or_else(|| exited(1));
                }
            }
            // the install's read back asks these (`siteidentity::scheduler_in_place`)
            if line.contains("systemctl is-enabled") {
                return if self.enabled { Ok("enabled\n".to_owned()) } else { Err(exited(1)) };
            }
            if line.contains("systemctl is-active") {
                return if self.active { Ok("active\n".to_owned()) } else { Err(exited(3)) };
            }
            if line.contains("daemon-reload") {
                self.loaded = !self.files.is_empty();
                return Ok(String::new());
            }
            if line.contains("enable --now") {
                self.enabled = true;
                self.active = true;
                return Ok(String::new());
            }
            if line.contains("cron event list") {
                return Ok(format!("hook,time\nwp_version_check,{}\naction_scheduler_run_queue,{}\nlater,{}\n", NOW - 100, NOW - 50, NOW + 10));
            }
            if line.contains("option get") {
                return Err(exited(1));
            }
            if line.contains("db prefix") {
                return Ok("wp_\n".to_owned());
            }
            if line.contains("SHOW TABLES") {
                return Ok(String::new());
            }
            Ok(String::new())
        }

        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            if self.fail.is_some_and(|f| line.contains(f)) {
                return Err("\"pct\": timeout".to_owned());
            }
            let [.., tee, file] = argv else {
                return Err("not a tee".to_owned());
            };
            assert_eq!(tee, "tee");
            let name = file.rsplit('/').next().unwrap().to_owned();
            let text = if self.sticky {
                "something else".to_owned()
            } else {
                String::from_utf8_lossy(input).into_owned()
            };
            self.files.insert(name, ("f".to_owned(), text));
            Ok(String::new())
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aib-sched-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn places(d: &Path, quarantined: bool) -> Places {
        let records = d.join("records");
        let firewall = d.join("firewall");
        fs::create_dir_all(&records).unwrap();
        fs::create_dir_all(&firewall).unwrap();
        let rec = aib_cloud::site::record(&spec(), "2026-11-12T00:00:00Z");
        fs::write(records.join(format!("{CODE}.json")), rec.to_string()).unwrap();
        fs::write(
            firewall.join(format!("{CT}.fw")),
            quarantine::expected_firewall(&spec(), quarantined),
        )
        .unwrap();
        Places { records, firewall }
    }

    fn place(d: &Path) -> opstore::OpPlace {
        fs::write(d.join("boot_id"), "0f0e0d0c-0b0a-4908-8706-050403020100\n").unwrap();
        let me = d.join("proc").join(std::process::id().to_string());
        fs::create_dir_all(&me).unwrap();
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            me.join("stat"),
            format!("{} (aib-cloud-host) {filler} 4242 0 0\n", std::process::id()),
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

    fn journal_states(pl: &opstore::OpPlace) -> Vec<String> {
        let mut out = Vec::new();
        for e in fs::read_dir(&pl.ops_dir).unwrap() {
            let text = fs::read_to_string(e.unwrap().path()).unwrap();
            for l in text.lines() {
                let v: Value = serde_json::from_str(l).unwrap();
                out.push(v["state"].as_str().unwrap().to_owned());
            }
        }
        out
    }

    // ---------------------------------------------------------------- the pure rules

    #[test]
    fn an_exit_code_only_from_a_program_that_exited() {
        assert_eq!(exit_code(&exited(1)), Some(1));
        assert_eq!(exit_code(&exited(37)), Some(37));
        assert_eq!(exit_code("\"pct\": timeout"), None);
        assert_eq!(exit_code("\"pct\": more than 10 bytes of output"), None);
        assert_eq!(exit_code("\"pct\" exited signal: 9 (SIGKILL)"), None);
        assert_eq!(exit_code("\"pct\" exited exit status: 1x"), None);
        assert_eq!(exit_code("\"pct\" exited exit status: "), None);
    }

    #[test]
    fn unknown_wins_over_conflict_and_conflict_over_needs() {
        let n = Found::Needs("cron_not_set");
        let c = Found::Conflict("cron_unit_dropin");
        let u = Found::Unknown("cron_unit_unread");
        assert_eq!(verdict(&[n, c, u]), State::Unknown("cron_unit_unread"));
        assert_eq!(verdict(&[n, c]), State::Conflict("cron_unit_dropin"));
        assert_eq!(verdict(&[n]), State::Needs(vec!["cron_not_set"]));
        assert_eq!(verdict(&[]), State::InPlace);
    }

    #[test]
    fn the_constant_is_known_missing_only_from_exit_1() {
        assert_eq!(judge_constant(Err(Some(1)), None), [Found::Needs("cron_not_set")]);
        assert_eq!(judge_constant(Err(Some(255)), None), [Found::Unknown("cron_constant_unread")]);
        assert_eq!(judge_constant(Err(None), None), [Found::Unknown("cron_constant_unread")]);
        assert!(judge_constant(Ok(()), Some(Ok("1\n"))).is_empty());
        assert!(judge_constant(Ok(()), Some(Ok("true"))).is_empty());
        assert_eq!(judge_constant(Ok(()), Some(Ok("false"))), [Found::Needs("cron_not_set")]);
        assert_eq!(judge_constant(Ok(()), Some(Err(()))), [Found::Unknown("cron_constant_unread")]);
        assert_eq!(judge_constant(Ok(()), None), [Found::Unknown("cron_constant_unread")]);
    }

    #[test]
    fn a_unit_answer_has_every_key_once_and_nothing_else() {
        let ok = unit_text("loaded", "enabled", "active", "/x", "", "no");
        assert!(parse_unit(&ok).is_some());
        assert!(parse_unit(&ok.replace("NeedDaemonReload=no\n", "")).is_none());
        assert!(parse_unit(&format!("{ok}ActiveState=active\n")).is_none());
        assert!(parse_unit(&format!("{ok}Other=1\n")).is_none());
        assert!(parse_unit("garbage").is_none());
    }

    fn timer() -> String {
        format!("{}.timer", cron_unit(CODE))
    }
    fn want() -> String {
        cron_files(CODE)[1].1.clone()
    }
    fn shown(load: &str, file: &str, active: &str, dropin: &str, reload: &str) -> String {
        unit_text(load, file, active, &format!("{UNIT_DIR}/{}", timer()), dropin, reload)
    }

    #[test]
    fn a_timer_in_place_has_no_finding() {
        let s = shown("loaded", "enabled", "active", "", "no");
        assert!(judge_unit(&timer(), true, Some(&s), Some("f"), Some(Ok(&want())), &want()).is_empty());
    }

    #[test]
    fn a_unit_someone_else_put_there_is_a_conflict() {
        let w = want();
        let t = timer();
        let dropin = shown("loaded", "enabled", "active", "/etc/systemd/system/x.d/a.conf", "no");
        assert!(judge_unit(&t, true, Some(&dropin), Some("f"), Some(Ok(&w)), &w)
            .contains(&Found::Conflict("cron_unit_dropin")));
        let elsewhere = unit_text("loaded", "enabled", "active", "/lib/systemd/system/x", "", "no");
        assert!(judge_unit(&t, true, Some(&elsewhere), Some("f"), Some(Ok(&w)), &w)
            .contains(&Found::Conflict("cron_unit_elsewhere")));
        let masked = shown("masked", "masked", "inactive", "", "no");
        assert!(judge_unit(&t, true, Some(&masked), Some("f"), Some(Ok(&w)), &w)
            .contains(&Found::Conflict("cron_unit_masked")));
        let ok = shown("loaded", "enabled", "active", "", "no");
        assert!(judge_unit(&t, true, Some(&ok), Some("l"), None, &w)
            .contains(&Found::Conflict("cron_unit_not_file")));
    }

    #[test]
    fn a_unit_not_read_is_unknown_never_in_place() {
        let w = want();
        let t = timer();
        assert_eq!(judge_unit(&t, true, None, Some("f"), Some(Ok(&w)), &w), [Found::Unknown("cron_unit_unread")]);
        let ok = shown("loaded", "enabled", "active", "", "no");
        assert!(judge_unit(&t, true, Some(&ok), Some("f"), Some(Err(())), &w)
            .contains(&Found::Unknown("cron_unit_unread")));
        let odd = shown("bad-setting", "enabled", "active", "", "no");
        assert!(judge_unit(&t, true, Some(&odd), Some("f"), Some(Ok(&w)), &w)
            .contains(&Found::Unknown("cron_unit_load_state")));
    }

    #[test]
    fn what_needs_putting_right() {
        let w = want();
        let t = timer();
        let none = unit_text("not-found", "", "inactive", "", "", "no");
        let f = judge_unit(&t, true, Some(&none), None, None, &w);
        assert!(f.contains(&Found::Needs("cron_unit_missing")));
        assert!(f.contains(&Found::Needs("cron_unit_not_loaded")));
        assert!(f.contains(&Found::Needs("cron_timer_disabled")));
        assert!(f.contains(&Found::Needs("cron_timer_inactive")));
        let reload = shown("loaded", "enabled", "active", "", "yes");
        assert_eq!(
            judge_unit(&t, true, Some(&reload), Some("f"), Some(Ok("old")), &w),
            [Found::Needs("cron_unit_differs"), Found::Needs("cron_unit_reload")]
        );
        // a oneshot service is inactive between runs: not a finding
        let svc = unit_text("loaded", "static", "inactive", &format!("{UNIT_DIR}/x.service"), "", "no");
        assert!(judge_unit("x.service", false, Some(&svc), Some("f"), Some(Ok("a")), "a").is_empty());
    }

    #[test]
    fn the_gate_enters_only_a_running_container_not_quarantined() {
        let normal = quarantine::expected_firewall(&spec(), false);
        let cut = quarantine::expected_firewall(&spec(), true);
        assert_eq!(gate(Some(&normal), Some("status: running\n")), Gate::Run);
        assert_eq!(gate(Some(&cut), Some("status: running\n")), Gate::Skip("skipped_quarantined"));
        assert_eq!(gate(None, Some("status: running\n")), Gate::Skip("firewall_unread"));
        assert_eq!(gate(Some(&normal), Some("status: stopped\n")), Gate::Skip("ct_not_running"));
        assert_eq!(gate(Some(&normal), None), Gate::Skip("ct_status_unread"));
        assert_eq!(gate(Some(&normal), Some("status: paused")), Gate::Skip("ct_status_unread"));
    }

    #[test]
    fn due_events_by_hook_with_their_arguments_never_shown() {
        let csv = format!(
            "hook,time\na_hook,{}\na_hook,{}\nb_hook,{}\nnot yet,{}\nbad hook;rm,{}\n",
            NOW - 10,
            NOW - 500,
            NOW,
            NOW + 1,
            NOW - 1
        );
        let (by, oldest) = due_events(&csv, NOW).unwrap();
        assert_eq!(oldest, 500);
        assert_eq!(by, [("a_hook".to_owned(), 2), ("b_hook".to_owned(), 1), ("other".to_owned(), 1)]);
        assert!(due_events("time\n1\n", NOW).is_none());
        assert!(due_events("hook,time\na,soon\n", NOW).is_none());
    }

    // ---------------------------------------------------------------- reading a site

    #[test]
    fn a_fresh_site_needs_everything_and_nothing_is_written_by_reading() {
        let mut ct = Ct::fresh();
        let (state, _) = observe(&mut ct, &spec());
        let State::Needs(needs) = state else { panic!("{state:?}") };
        assert!(needs.contains(&"cron_not_set"));
        assert!(needs.contains(&"cron_unit_missing"));
        assert!(ct.writes().is_empty(), "{:?}", ct.calls);
        // every reading of the site software skips the customer's plugins and theme
        assert!(ct
            .calls
            .iter()
            .filter(|l| l.contains("/usr/local/bin/wp"))
            .all(|l| l.contains("--skip-plugins --skip-themes")));
    }

    #[test]
    fn a_site_in_place_reads_in_place() {
        let mut ct = Ct::in_place();
        assert_eq!(observe(&mut ct, &spec()).0, State::InPlace);
    }

    #[test]
    fn a_reading_that_fails_without_an_answer_is_unknown() {
        let mut ct = Ct::fresh();
        ct.has = Err(255);
        assert_eq!(observe(&mut ct, &spec()).0, State::Unknown("cron_constant_unread"));
        let mut ct = Ct::fresh();
        ct.fail = Some(" find ");
        assert_eq!(observe(&mut ct, &spec()).0, State::Unknown("cron_unit_unread"));
    }

    // ---------------------------------------------------------------- the modes

    #[test]
    fn check_answers_0_1_3() {
        let d = scratch("check");
        let p = places(&d, false);
        assert_eq!(check(&mut Ct::in_place(), &p, CODE, NOW).unwrap().0, 0);
        let (rc, lines) = check(&mut Ct::fresh(), &p, CODE, NOW).unwrap();
        assert_eq!(rc, 1);
        assert!(lines[0].starts_with(&format!("{CODE} needs cron_not_set")), "{lines:?}");
        assert!(lines.iter().any(|l| l.starts_with("due 2 oldest_waiting_s=100")), "{lines:?}");
        assert!(lines.iter().any(|l| l == "auto_update core=undefined disabled=undefined plugins=0 themes=0"), "{lines:?}");
        assert!(lines.iter().any(|l| l == "queue none"), "{lines:?}");
        let mut ct = Ct::fresh();
        ct.has = Err(2);
        assert_eq!(check(&mut ct, &p, CODE, NOW).unwrap().0, 3);
    }

    #[test]
    fn check_and_plan_never_enter_a_quarantined_or_stopped_container() {
        let d = scratch("gate");
        for (quarantined, status) in [(true, "status: running\n"), (false, "status: stopped\n")] {
            let p = places(&d, quarantined);
            let mut ct = Ct::fresh();
            ct.status = status;
            assert_eq!(check(&mut ct, &p, CODE, NOW).unwrap().0, 3);
            assert_eq!(plan(&mut ct, &p, CODE, NOW).unwrap().0, 3);
            assert_eq!(ct.entered(), 0, "{:?}", ct.calls);
        }
    }

    #[test]
    fn plan_shows_the_five_steps_and_writes_nothing() {
        let d = scratch("plan");
        let p = places(&d, false);
        let mut ct = Ct::fresh();
        let (rc, lines) = plan(&mut ct, &p, CODE, NOW).unwrap();
        assert_eq!(rc, 0);
        let v: Value = serde_json::from_str(&lines[0]).unwrap();
        assert_eq!(v["command"], COMMAND);
        assert_eq!(v["steps"].as_array().unwrap().len(), 5);
        assert!(ct.writes().is_empty(), "{:?}", ct.calls);
        let mut ct = Ct::in_place();
        assert_eq!(plan(&mut ct, &p, CODE, NOW).unwrap().1[0], format!("{CODE} no_change"));
    }

    #[test]
    fn apply_writes_reads_back_and_a_second_run_changes_nothing() {
        let d = scratch("apply");
        let p = places(&d, false);
        let pl = place(&d);
        let mut ct = Ct::fresh();
        assert_eq!(apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW), Ok(Applied::Changed));
        assert_eq!(journal_states(&pl), ["planned", "approved", "running", "verified"]);
        assert!(!ct.writes().is_empty());
        let before = ct.calls.len();
        assert_eq!(apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW), Ok(Applied::NoChange));
        let again: Vec<&String> = ct.calls[before..]
            .iter()
            .filter(|l| l.contains(" tee ") || l.contains("config set") || l.contains("daemon-reload") || l.contains("enable --now"))
            .collect();
        assert!(again.is_empty(), "{again:?}");
        // no change leaves no new journal
        assert_eq!(journal_states(&pl).len(), 4);
    }

    #[test]
    fn apply_writes_nothing_where_it_does_not_know() {
        let d = scratch("unknown");
        let p = places(&d, false);
        let pl = place(&d);
        let mut ct = Ct::fresh();
        ct.has = Err(255);
        assert_eq!(
            apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW),
            Ok(Applied::Skipped("cron_constant_unread"))
        );
        assert!(ct.writes().is_empty(), "{:?}", ct.calls);
        let mut ct = Ct::fresh();
        ct.files.insert(timer(), ("l".to_owned(), String::new()));
        assert_eq!(
            apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW),
            Ok(Applied::Skipped("cron_unit_not_file"))
        );
        assert!(ct.writes().is_empty(), "{:?}", ct.calls);
        assert!(journal_states(&pl).is_empty());
    }

    #[test]
    fn apply_never_enters_a_quarantined_or_stopped_container() {
        let d = scratch("apply-gate");
        let pl = place(&d);
        for (quarantined, status, why) in [
            (true, "status: running\n", "skipped_quarantined"),
            (false, "status: stopped\n", "ct_not_running"),
        ] {
            let p = places(&d, quarantined);
            let mut ct = Ct::fresh();
            ct.status = status;
            assert_eq!(apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW), Ok(Applied::Skipped(why)));
            assert_eq!(ct.entered(), 0, "{:?}", ct.calls);
        }
    }

    #[test]
    fn apply_fails_and_journals_it_when_the_writing_does_not_read_back() {
        let d = scratch("sticky");
        let p = places(&d, false);
        let pl = place(&d);
        let mut ct = Ct::fresh();
        ct.sticky = true;
        let err = apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW).unwrap_err();
        assert!(err.starts_with("cron_unit_differs"), "{err}");
        assert_eq!(journal_states(&pl).last().map(String::as_str), Some("failed"));
        // a failed operation holds the site back until a person closes it
        let mut ct = Ct::in_place();
        assert!(apply(&pl, &p, CODE, opstore::BY_LOCAL_ROOT, &mut ct, NOW).is_err());
    }

    #[test]
    fn version_mail_and_publishing_are_marked() {
        let by = [
            ("wp_version_check".to_owned(), 1),
            ("wp_maybe_auto_update".to_owned(), 1),
            ("fluentmail_do_daily_scheduled_tasks".to_owned(), 2),
            ("publish_future_post".to_owned(), 3),
            ("wp_update_plugins".to_owned(), 1),
        ];
        assert_eq!(marked(&by), (2, 2, 3));
        assert_eq!(marked(&[]), (0, 0, 0));
    }

    #[test]
    fn a_usage_error_is_64_and_never_a_mode() {
        let a = |v: &[&str]| v.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        for bad in [
            &[][..],
            &["--apply"][..],
            &[CODE][..],
            &[CODE, "--dryrun"][..],
            &[CODE, "--check", "--apply"][..],
            &[CODE, "--apply", "-n"][..],
            &[CODE, "--records", "a", "--records", "b", "--check"][..],
            &["../x", "--check"][..],
        ] {
            assert!(parse(&a(bad)).is_err(), "{bad:?}");
            assert_eq!(cmd(&a(bad)), Ok(64), "{bad:?}");
        }
        assert_eq!(parse(&a(&[CODE, "--plan"])).unwrap().1, Mode::Plan);
        assert_eq!(parse(&a(&[CODE, "--records", "/r", "--apply"])).unwrap().1, Mode::Apply);
    }

    #[test]
    fn a_customer_key_cannot_approve_it() {
        let d = scratch("customer");
        let p = places(&d, false);
        let pl = place(&d);
        let mut ct = Ct::fresh();
        assert!(apply(&pl, &p, CODE, opstore::BY_CUSTOMER_KEY, &mut ct, NOW).is_err());
        assert!(ct.calls.is_empty(), "{:?}", ct.calls);
        assert!(!opstore::CUSTOMER_COMMANDS.contains(&COMMAND));
    }
}
