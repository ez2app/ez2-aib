//! N-webonly 第 4 件, the half behind [`crate::receipt_run::ReceiptRunner`] (b4297 11-20;
//! DESIGN-g1-receipt-origacct-b4297-1120 r3, cut with 7ebca 21:35). Slice R1: what is decided
//! before anything leaves this machine, as pure functions. Nothing here runs git, opens a
//! connection or raises a window; the runner that does (slices R3, R4) calls these.
//!
//! - [`SWITCH`]: default off. Without `AIBD_RECEIPT_RUN=1` the runner is never set and every
//!   request stays `no_host` ([`crate::receipt_run::NoRunner`]).
//! - [`pick`]: the machine. Only one of the pinned list ([`crate::trusted_hosts`]) whose `os`
//!   is `mac`: the test machine runs the tree under `sandbox-exec`, and there is no such
//!   thing on Windows yet (design r3 §8: Windows results are GitHub CI's).
//! - [`brings`]: what the head would run at build time that its base does not, read from the
//!   changed paths and the two `Cargo.lock`s. A head that touches which toolchain or cargo
//!   settings are used, or takes a crate from anywhere but crates.io, is `tree_refused` here,
//!   before a card is raised; the gate on the machine checks the same again with its own
//!   copy of the rule ([`aib_receipt_gate::sandbox`]), because this side is a display to it.
//! - [`owner_ask`]: the owner's fingerprint for one run, bound to {machine, bundle sha256,
//!   run id, knife table sha256} (design r3 §2) and to what [`brings`] found, so a ticket
//!   approves this very bundle going to this very machine. Each run asks; there is no
//!   "same seat, same day" pass (5d33a PREREAD-g1origacct L1).
//! - [`run_words`] / [`read_words`] / [`cancel_words`]: the only three lines aibd says to a
//!   machine. Each is parsed back with the gate's own reader before it is returned, so a
//!   line this side would send is one the gate takes.
//! - [`answer`]: the gate's first line, as one of the fixed reason codes a page may be told.
//!
//! First version: the whole workspace, no knives (`KNIVES-NOT-RUN`; knives are CI's). A
//! narrow request is refused `receipt_not_allowed`: it can never be green
//! ([`crate::receipt_check`]) and would spend a fingerprint and a machine for nothing.

use crate::trusted_hosts::TrustedHost;
use aib_receipt_gate::sandbox::{lock_ok, path_refused};
use serde_json::json;
use std::collections::BTreeSet;

/// `AIBD_RECEIPT_RUN=1` sets the runner at start-up; anything else leaves `no_host`.
pub const SWITCH: &str = "AIBD_RECEIPT_RUN";
/// The owner_ok operation of one run.
pub const OP: &str = "receipt.run";
/// SHA-256 of no bytes: the knife table of a run that sends none.
pub const NO_KNIVES_SHA256: &str =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
/// At most this many names of one kind go into a card or a page.
pub const NAMED_MAX: usize = 12;

pub fn switch_on(v: Option<String>) -> bool {
    v.as_deref() == Some("1")
}

/// The machine a receipt goes to: the first of the pinned list that is a Mac.
pub fn pick(hosts: &[TrustedHost]) -> Option<&TrustedHost> {
    hosts.iter().find(|h| h.os == "mac")
}

/// What a head would run at build time that its base does not.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct Brings {
    /// `build.rs` files added or changed (they run at build, as the machine's own account).
    pub build_scripts: Vec<String>,
    /// `Cargo.toml` files added or changed.
    pub manifests: Vec<String>,
    /// `name version` of packages `Cargo.lock` lists that the base's did not (their build
    /// scripts and proc-macros run too).
    pub new_packages: Vec<String>,
    /// Files added under a `tests` or `src` folder.
    pub new_code_files: usize,
    /// Every path the head adds, changes or removes.
    pub files: usize,
}

impl Brings {
    /// Whether the card says so in red: something new runs at build time.
    pub fn runs_new_code_at_build(&self) -> bool {
        !self.build_scripts.is_empty() || !self.new_packages.is_empty()
    }
}

fn base_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// `name version` of every package of a `Cargo.lock`.
pub fn packages(lock: &str) -> BTreeSet<String> {
    let value = |line: &str, key: &str| -> Option<String> {
        let v = line
            .strip_prefix(key)?
            .trim_start()
            .strip_prefix('=')?
            .trim();
        Some(v.strip_prefix('"')?.strip_suffix('"')?.to_owned())
    };
    let mut out = BTreeSet::new();
    let mut name: Option<String> = None;
    for line in lock.lines().map(str::trim) {
        if line == "[[package]]" {
            name = None;
        } else if let Some(n) = value(line, "name") {
            name = Some(n);
        } else if let Some(v) = value(line, "version") {
            // the version line of a package follows its name; any other is not one
            if let Some(n) = name.take() {
                out.insert(format!("{n} {v}"));
            }
        }
    }
    out
}

/// What `changed` (git's `(status letter, path)` of the head against its base: `A`, `M`,
/// `D`, …; a rename read as a removal and an addition) and the two lock files bring.
/// `tree_refused`: a changed path the gate refuses, or a lock that names a source other
/// than crates.io.
pub fn brings(
    changed: &[(String, String)],
    base_lock: &str,
    head_lock: &str,
) -> Result<Brings, &'static str> {
    if changed.iter().any(|(_, p)| path_refused(p)) || !lock_ok(head_lock) {
        return Err("tree_refused");
    }
    let kept = |(status, _): &&(String, String)| status != "D";
    let named = |file: &str| -> Vec<String> {
        changed
            .iter()
            .filter(kept)
            .filter(|(_, p)| base_name(p) == file)
            .map(|(_, p)| p.clone())
            .collect()
    };
    let had = packages(base_lock);
    let new_code_files = changed
        .iter()
        .filter(|(status, p)| {
            status == "A"
                && p.ends_with(".rs")
                && p.split('/').any(|part| part == "tests" || part == "src")
        })
        .count();
    Ok(Brings {
        build_scripts: named("build.rs"),
        manifests: named("Cargo.toml"),
        new_packages: packages(head_lock).difference(&had).cloned().collect(),
        new_code_files,
        files: changed
            .iter()
            .map(|(_, p)| p)
            .collect::<BTreeSet<_>>()
            .len(),
    })
}

/// How much of a machine's name the window shows (the whole name is in the binding).
pub const HOST_SHOWN: usize = 16;

/// How much of a run id the window shows (the whole id is in the binding).
pub const RUN_ID_SHOWN: usize = 12;

/// The few words the fingerprint window shows of one run, all of them inside the 80
/// characters it keeps ([`crate::owner_ok::RESOURCE_MAX`]): a longer name or a larger count
/// is cut here, never by the window. aibd's words first — the red warning, the machine's
/// pinned name, the counts — and the run id, a word the sending seat chose, last and cut
/// (ebab8 11-25 14:01, SCAN-owner-window-words 3: a long run id pushed the warning and the
/// machine into the part the window cuts). No path or package name a seat chose.
pub fn card_words(host: &str, run_id: &str, b: &Brings) -> String {
    let red = if b.runs_new_code_at_build() {
        "會跑新程式"
    } else {
        ""
    };
    let host: String = host.chars().take(HOST_SHOWN).collect();
    let to = format!("收據到 {host}");
    let counts = format!(
        "build.rs {}·新套件 {}·新檔 {}",
        b.build_scripts.len().min(999),
        b.new_packages.len().min(999),
        b.new_code_files.min(9999),
    );
    crate::owner_ok::fixed_then_cut(&[red, &to, &counts], run_id, RUN_ID_SHOWN)
}

/// What one run is, for the owner's fingerprint: everything [`owner_ask`] binds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sent<'a> {
    /// The machine's pinned name.
    pub host: &'a str,
    pub run_id: &'a str,
    pub head: &'a str,
    /// The base the bundle is made against.
    pub base: &'a str,
    pub bundle_sha256: &'a str,
    pub knives_sha256: &'a str,
}

/// The fingerprint one run asks. `by` is the run's own name for the ticket (`receipt:<run
/// id>`): the runner asks, no page resends.
pub fn owner_ask(s: &Sent<'_>, b: &Brings, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "host": s.host, "run_id": s.run_id, "head": s.head, "base": s.base,
        "bundle_sha256": s.bundle_sha256, "knives_sha256": s.knives_sha256,
        "build_scripts": b.build_scripts, "manifests": b.manifests,
        "new_packages": b.new_packages, "new_code_files": b.new_code_files, "files": b.files,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: card_words(s.host, s.run_id, b),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// What a page shows of a run that waits for its owner: counts, and the first names of each
/// kind (they are paths and package names of the seat's own tree, shown as text).
pub fn page_card(host: &str, b: &Brings) -> serde_json::Value {
    let first = |v: &[String]| -> Vec<String> { v.iter().take(NAMED_MAX).cloned().collect() };
    json!({
        "host": crate::receipt_run::nickname(host),
        "red": b.runs_new_code_at_build(),
        "build_scripts": first(&b.build_scripts), "build_scripts_n": b.build_scripts.len(),
        "manifests_n": b.manifests.len(),
        "new_packages": first(&b.new_packages), "new_packages_n": b.new_packages.len(),
        "new_code_files": b.new_code_files, "files": b.files,
    })
}

fn checked(line: String) -> Result<String, &'static str> {
    aib_receipt_gate::parse(Some(&line)).map(|_| line)
}

/// The line that starts a run: the whole workspace, no knives.
pub fn run_words(
    run_id: &str,
    head: &str,
    bundle_len: u64,
    bundle_sha256: &str,
) -> Result<String, &'static str> {
    checked(format!(
        "run {run_id} {head} ws {bundle_len} {bundle_sha256} 0 {NO_KNIVES_SHA256}"
    ))
}

/// The line that asks for a run's `.out`.
pub fn read_words(run_id: &str) -> Result<String, &'static str> {
    checked(format!("fetch {run_id}"))
}

/// The line that withdraws a run.
pub fn cancel_words(run_id: &str) -> Result<String, &'static str> {
    checked(format!("cancel {run_id}"))
}

/// The gate's answer to `verb`, from the first line it printed: `Ok` for `OK <verb>`, else
/// the reason as a page may be told it (one of [`crate::receipt_run::REFUSALS`], or
/// `refused` for a code this side does not know). A line that is neither, or no line (the
/// connection ended), is `lost`.
pub fn answer(stdout: &[u8], verb: &str) -> Result<(), &'static str> {
    let first = stdout.split(|b| *b == b'\n').next().unwrap_or_default();
    let Ok(first) = std::str::from_utf8(first) else {
        return Err("lost");
    };
    let first = first.trim_end_matches('\r');
    if first.strip_prefix("OK ") == Some(verb) {
        return Ok(());
    }
    Err(match first.strip_prefix("ERR ") {
        Some("receipt_tool_changed") => "receipt_tool_changed",
        Some("host_changed" | "gate_writable") => "host_changed",
        Some("disk_low") => "disk_low",
        Some("toolchain_changed" | "no_toolchain") => "toolchain_changed",
        Some("busy" | "no_bash" | "not_ready" | "no_sandbox" | "only_ws_no_knives") => "no_host",
        Some(
            "tree_refused" | "bundle_refused" | "bundle_not_head" | "sha256_mismatch"
            | "short_input",
        ) => "tree_refused",
        // a reason this side does not know is not passed off as the tree's fault
        Some(_) => "refused",
        None => "lost",
    })
}

/// What a `fetch` brought back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Fetched {
    /// The run's sealed `.out`.
    Out(Vec<u8>),
    /// It is still under way: ask again later.
    NotYet,
    /// It ended with no `.out`, and why (one of [`crate::receipt_run::REFUSALS`]).
    Ended(&'static str),
}

/// The gate's answer to `fetch`: the bytes after `OK fetch` are the `.out`; `not_done` is
/// not an ending; a run the machine no longer knows (`lost_run`, `no_such_run`) is `lost`.
/// No answer at all (the connection failed, the read timed out) is [`Fetched::NotYet`]: one
/// missed read does not end a run, the caller counts them.
pub fn fetched(stdout: &[u8]) -> Fetched {
    const OK: &[u8] = b"OK fetch\n";
    if let Some(out) = stdout.strip_prefix(OK) {
        return Fetched::Out(out.to_vec());
    }
    if stdout.is_empty() {
        return Fetched::NotYet;
    }
    let first = stdout.split(|b| *b == b'\n').next().unwrap_or_default();
    match first {
        b"ERR not_done" => Fetched::NotYet,
        b"ERR lost_run" | b"ERR no_such_run" => Fetched::Ended("lost"),
        _ => Fetched::Ended(answer(stdout, "fetch").err().unwrap_or("lost")),
    }
}

// ---------------------------------------------------------------------------------------
// Slice R4b: the runner. Everything that leaves this machine goes through [`Link`]; what the
// owner says comes through [`Owner`]; what aibd knows comes through [`Facts`]. Each has a
// real side ([`Link`]: aibd's own key and ssh, R3; [`TouchOwner`]; [`AibdFacts`]) and a
// double in the tests below.

/// How aibd says one line to a pinned machine and hears its answer: the delegate key's ssh
/// (R3). A machine that cannot be reached, or an answer that did not come in `secs`, is no
/// bytes at all.
pub trait Link: Send + Sync {
    /// Whether anything can be reached at all (the key exists, ssh is there). `false`: a
    /// request is refused `no_host` before a bundle is made or a card is raised.
    fn ready(&self) -> bool;
    /// Says `line` to `host`, the file `stdin` (the bundle) on its standard input, and
    /// returns what it printed.
    fn say(
        &self,
        host: &TrustedHost,
        line: &str,
        stdin: Option<&std::path::Path>,
        secs: u64,
    ) -> Vec<u8>;
}

/// A link that reaches nothing (so nothing is asked either): what a runner has in tests of
/// the rest, and what aibd's had before [`crate::receipt_link::SshLink`].
pub struct NoLink;

impl Link for NoLink {
    fn ready(&self) -> bool {
        false
    }
    fn say(&self, _: &TrustedHost, _: &str, _: Option<&std::path::Path>, _: u64) -> Vec<u8> {
        Vec::new()
    }
}

/// The owner's answer to one run's card. Blocking: it returns when the owner answered or
/// the card ended. `true` only for a fingerprint given for exactly this ask.
pub trait Owner: Send + Sync {
    fn approves(&self, ask: &crate::owner_ok::Ask) -> bool;
}

/// The owner asked through [`crate::owner_ok`]: a ticket, the fingerprint window, the
/// ticket re-read each second until it is answered, then used up against this very ask.
pub struct TouchOwner;

impl Owner for TouchOwner {
    fn approves(&self, ask: &crate::owner_ok::Ask) -> bool {
        use crate::owner_ok::{Asked, State};
        let Asked::Ticket(ticket) = crate::owner_ok::ask(ask) else {
            return false;
        };
        let op = crate::timers::RECEIPT_OWNER_WAIT.begin(None);
        loop {
            match crate::owner_ok::state(&ticket, &ask.by) {
                State::Pending => {
                    if !op.tick() {
                        return false;
                    }
                }
                State::Approved { .. } => return crate::owner_ok::approved_for(&ticket, ask),
                _ => return false,
            }
        }
    }
}

/// A seat's copy as the runner needs it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatCopy {
    /// The seat's copy of a registered repo.
    pub tree: std::path::PathBuf,
    /// The registered repo it is a copy of.
    pub repo: std::path::PathBuf,
    /// aibd's empty hooks folder (its parent is git's home).
    pub hooks: std::path::PathBuf,
}

/// What the runner is told about this machine and what it tells back.
pub trait Facts: Send + Sync {
    /// The pinned machines ([`crate::trusted_hosts`]).
    fn hosts(&self) -> Vec<TrustedHost>;
    /// Whether the seat is trusted to ask now ([`crate::trusted_seats`]).
    fn trusted(&self, seat_id: &str) -> bool;
    /// The seat's copy and the head it is on, read as plain files.
    fn copy_of(&self, seat_id: &str) -> Option<(SeatCopy, String)>;
    /// A folder of aibd's own for bundles and scratch repositories.
    fn work_dir(&self) -> std::path::PathBuf;
    /// The owner answered the card of `run_id` ([`crate::receipt_run::owner_answered`]).
    /// `false`: the run no longer waits for it (withdrawn).
    fn owner_answered(&self, run_id: &str, ok: bool) -> bool;
    /// The run ended with no `.out` ([`crate::receipt_run::runner_lost`]).
    fn ended(&self, run_id: &str, why: &'static str);
}

/// A read that brought no answer this many times in a row ends the run `lost` (at the
/// 30-second pass: twenty minutes of a machine that does not answer).
pub const MISSED_READS: u32 = 40;
/// How long the line that sends the bundle may take.
pub const SEND_SECS: u64 = 900;
/// How long a read or a cancel may take (5d33a PREREAD N5: a read answers within 20 s).
pub const READ_SECS: u64 = 20;

#[derive(Debug, Clone)]
struct Known {
    host: TrustedHost,
    /// The machine answered `OK run`: before that nothing is asked of it for this run.
    sent: bool,
    /// Reads in a row that brought no answer.
    missed: u32,
    /// Withdrawn while it was being prepared or sent.
    withdrawn: bool,
    /// A seat's own request whose bundle is made and judged and whose card is on a page:
    /// nothing asks the owner for a fingerprint until [`Runner::open_card`].
    held: Option<std::sync::Arc<Held>>,
}

/// A run that waits for the owner to open its card on a page.
#[derive(Debug)]
struct Held {
    ask: crate::receipt_run::Ask,
    copy_host: TrustedHost,
    bundle: std::path::PathBuf,
    made: crate::receipt_bundle::Made,
}

/// The runner behind [`crate::receipt_run::ReceiptRunner`] ([`Started`]).
pub struct Runner {
    facts: std::sync::Arc<dyn Facts>,
    link: std::sync::Arc<dyn Link>,
    owner: std::sync::Arc<dyn Owner>,
    /// The runs this aibd is preparing or has sent.
    runs: std::sync::Mutex<std::collections::HashMap<String, Known>>,
}

impl Runner {
    pub fn new(
        facts: std::sync::Arc<dyn Facts>,
        link: std::sync::Arc<dyn Link>,
        owner: std::sync::Arc<dyn Owner>,
    ) -> std::sync::Arc<Self> {
        std::sync::Arc::new(Self {
            facts,
            link,
            owner,
            runs: std::sync::Mutex::new(std::collections::HashMap::new()),
        })
    }

    fn runs(&self) -> std::sync::MutexGuard<'_, std::collections::HashMap<String, Known>> {
        self.runs.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn withdrawn(&self, run_id: &str) -> bool {
        self.runs().get(run_id).is_none_or(|k| k.withdrawn)
    }

    /// What is checked before a request is taken: quick, nothing leaves the machine. The
    /// run is known from here on.
    pub fn check(
        &self,
        ask: &crate::receipt_run::Ask,
    ) -> Result<(TrustedHost, SeatCopy), &'static str> {
        if !self.link.ready() {
            return Err("no_host");
        }
        // first version: the whole workspace; a narrow receipt is never green
        if ask.narrow || !ask.tests.is_empty() {
            return Err("receipt_not_allowed");
        }
        let host = pick(&self.facts.hosts()).cloned().ok_or("no_host")?;
        if !self.facts.trusted(&ask.seat_id) {
            return Err("receipt_not_allowed");
        }
        let (copy, head) = self
            .facts
            .copy_of(&ask.seat_id)
            .ok_or("receipt_not_allowed")?;
        if head != ask.head {
            return Err("head_not_seat_head");
        }
        self.runs().insert(
            ask.run_id.clone(),
            Known {
                host: host.clone(),
                sent: false,
                missed: 0,
                withdrawn: false,
                held: None,
            },
        );
        Ok((host, copy))
    }

    /// One run from the bundle to the machine (on a thread of its own in aibd): the bundle
    /// is made and judged, the owner is asked for exactly that bundle on exactly that
    /// machine, and only a yes sends it. Every way it stops is told through [`Facts`].
    /// A seat's own request (`by_seat`) stops once its bundle is made: no window comes up
    /// for something the owner did not ask for; [`Runner::open_card`] goes on from there.
    pub fn carry(&self, ask: &crate::receipt_run::Ask, host: &TrustedHost, copy: &SeatCopy) {
        let run_id = ask.run_id.as_str();
        let dir = self.facts.work_dir();
        let bundle = dir.join(format!("{run_id}.bundle"));
        let scratch = dir.join(format!("{run_id}.scratch"));
        let made = match crate::receipt_bundle::make(
            &copy.repo,
            &copy.tree,
            &ask.head,
            &copy.hooks,
            &scratch,
            &bundle,
        ) {
            Ok(m) => m,
            Err(why) => {
                // no card was raised: the run ends with the reason
                self.runs().remove(run_id);
                self.facts.ended(run_id, why);
                return;
            }
        };
        if ask.by_seat {
            let held = std::sync::Arc::new(Held {
                ask: ask.clone(),
                copy_host: host.clone(),
                bundle: bundle.clone(),
                made,
            });
            let kept = {
                let mut runs = self.runs();
                match runs.get_mut(run_id) {
                    Some(k) if !k.withdrawn => {
                        k.held = Some(held);
                        true
                    }
                    _ => false,
                }
            };
            if !kept {
                // withdrawn while the bundle was made
                self.runs().remove(run_id);
                let _ = std::fs::remove_file(&bundle);
            }
            return;
        }
        self.ask_and_send(ask, host, &bundle, &made);
    }

    /// The card of a seat's own run that waits on a page ([`page_card`]), while it waits.
    pub fn held_card(&self, run_id: &str) -> Option<serde_json::Value> {
        let held = self.runs().get(run_id).and_then(|k| k.held.clone())?;
        Some(page_card(&held.copy_host.name, &held.made.brings))
    }

    /// The owner opened the card of a seat's own run on a page: the fingerprint window comes
    /// up now (on a thread of its own) and a yes sends the bundle that was made. `false`
    /// when no such run waits (it is opened once).
    pub fn open_card(self: &std::sync::Arc<Self>, run_id: &str) -> bool {
        let Some(held) = self.take_card(run_id) else {
            return false;
        };
        let (runner, again) = (self.clone(), held.clone());
        let spawned = std::thread::Builder::new()
            .name("aibd-receipt-card".into())
            .spawn(move || runner.send_held(&again));
        if spawned.is_err() {
            // still waiting: the card can be opened again
            if let Some(k) = self.runs().get_mut(run_id) {
                k.held = Some(held);
            }
            return false;
        }
        true
    }

    /// The waiting run's bundle, taken once (a second opening finds none).
    fn take_card(&self, run_id: &str) -> Option<std::sync::Arc<Held>> {
        let mut runs = self.runs();
        runs.get_mut(run_id)
            .filter(|k| !k.withdrawn)
            .and_then(|k| k.held.take())
    }

    fn send_held(&self, held: &Held) {
        self.ask_and_send(&held.ask, &held.copy_host, &held.bundle, &held.made);
    }

    /// Asks the owner for exactly this bundle on exactly this machine and sends it on a yes.
    fn ask_and_send(
        &self,
        ask: &crate::receipt_run::Ask,
        host: &TrustedHost,
        bundle: &std::path::Path,
        made: &crate::receipt_bundle::Made,
    ) {
        let run_id = ask.run_id.as_str();
        let sent = Sent {
            host: &host.name,
            run_id,
            head: &ask.head,
            base: &made.base,
            bundle_sha256: &made.sha256,
            knives_sha256: NO_KNIVES_SHA256,
        };
        let card = owner_ask(&sent, &made.brings, format!("receipt:{run_id}"));
        let yes = !self.withdrawn(run_id) && self.owner.approves(&card);
        // the answer is written first: a run withdrawn meanwhile is no longer waiting
        let still = self.facts.owner_answered(run_id, yes);
        if !yes || !still || self.withdrawn(run_id) {
            self.runs().remove(run_id);
            let _ = std::fs::remove_file(bundle);
            return;
        }
        let said = run_words(run_id, &ask.head, made.len, &made.sha256)
            .map_err(|_| "refused")
            .map(|line| self.link.say(host, &line, Some(bundle), SEND_SECS))
            .and_then(|out| answer(&out, "run"));
        let _ = std::fs::remove_file(bundle);
        // withdrawn while the bundle was on its way: the machine may have started it
        let gone = self.withdrawn(run_id);
        if gone {
            if let Ok(line) = cancel_words(run_id) {
                let _ = self.link.say(host, &line, None, READ_SECS);
            }
            self.runs().remove(run_id);
            return;
        }
        match said {
            Ok(()) => {
                if let Some(k) = self.runs().get_mut(run_id) {
                    k.sent = true;
                }
            }
            Err(why) => {
                self.runs().remove(run_id);
                self.facts.ended(run_id, why);
            }
        }
    }

    /// The run's `.out` when the machine has it. A run this aibd is still preparing is not
    /// asked for; one it does not know (aibd started again) is asked at the machine a new
    /// run would get, and a machine that never heard of it ends it `lost`.
    pub fn read(&self, run_id: &str) -> Option<Vec<u8>> {
        let known = self.runs().get(run_id).cloned();
        if known.as_ref().is_some_and(|k| !k.sent) {
            return None;
        }
        let host = match known {
            Some(k) => k.host,
            None => pick(&self.facts.hosts()).cloned()?,
        };
        let line = read_words(run_id).ok()?;
        let out = self.link.say(&host, &line, None, READ_SECS);
        if out.is_empty() {
            // no answer: one missed read does not end a run, many in a row do
            let missed = {
                let mut runs = self.runs();
                let k = runs.entry(run_id.to_owned()).or_insert(Known {
                    host,
                    sent: true,
                    missed: 0,
                    withdrawn: false,
                    held: None,
                });
                k.missed += 1;
                k.missed
            };
            if missed >= MISSED_READS {
                self.runs().remove(run_id);
                self.facts.ended(run_id, "lost");
            }
            return None;
        }
        match fetched(&out) {
            Fetched::Out(bytes) => {
                self.runs().remove(run_id);
                Some(bytes)
            }
            Fetched::NotYet => {
                if let Some(k) = self.runs().get_mut(run_id) {
                    k.missed = 0;
                }
                None
            }
            Fetched::Ended(why) => {
                self.runs().remove(run_id);
                self.facts.ended(run_id, why);
                None
            }
        }
    }

    /// Withdraws a run: one still being prepared is stopped before it is sent (the thread
    /// that carries it sees the mark), one that was sent is cancelled at its machine.
    pub fn cancel(&self, run_id: &str) {
        let known = {
            let mut runs = self.runs();
            let k = runs.get_mut(run_id);
            k.map(|k| {
                k.withdrawn = true;
                k.clone()
            })
        };
        let host = match &known {
            Some(k) if !k.sent => {
                if let Some(held) = &k.held {
                    // no thread carries it: its bundle and its entry go here
                    let _ = std::fs::remove_file(&held.bundle);
                    self.runs().remove(run_id);
                }
                return;
            }
            Some(k) => Some(k.host.clone()),
            None => pick(&self.facts.hosts()).cloned(),
        };
        if let (Some(host), Ok(line)) = (host, cancel_words(run_id)) {
            let _ = self.link.say(&host, &line, None, READ_SECS);
        }
        self.runs().remove(run_id);
    }
}

/// [`Runner`] as aibd's runner. `start` answers at once (the card is coming, or why not)
/// and the run goes on on a thread of its own.
pub struct Started(pub std::sync::Arc<Runner>);

impl crate::receipt_run::ReceiptRunner for Started {
    fn start(&self, ask: &crate::receipt_run::Ask) -> crate::receipt_run::Start {
        use crate::receipt_run::Start;
        let (host, copy) = match self.0.check(ask) {
            Ok(x) => x,
            Err(why) => return Start::Refused(why),
        };
        let (runner, ask2, name) = (self.0.clone(), ask.clone(), host.name.clone());
        let spawned = std::thread::Builder::new()
            .name("aibd-receipt-run".into())
            .spawn(move || runner.carry(&ask2, &host, &copy));
        if spawned.is_err() {
            self.0.runs().remove(&ask.run_id);
            return Start::Refused("no_host");
        }
        Start::NeedsOwner {
            host: name,
            knives_sent: 0,
        }
    }

    fn read(&self, run_id: &str) -> Option<Vec<u8>> {
        self.0.read(run_id)
    }

    fn cancel(&self, run_id: &str) {
        self.0.cancel(run_id);
    }

    /// The tests the gate's sandbox always skips and the digest the gate prints for them:
    /// the gate's own list, aibd holds no second copy.
    fn skips(&self) -> Option<(Vec<String>, String)> {
        Some((
            aib_receipt_gate::sandbox::SKIPPED
                .iter()
                .map(|(name, _)| (*name).to_owned())
                .collect(),
            aib_receipt_gate::sandbox::skipped_sha256(),
        ))
    }
}

/// The runner [`set_at_start`] set, for the two entries a page's act reaches.
static AT_START: std::sync::OnceLock<std::sync::Arc<Runner>> = std::sync::OnceLock::new();

/// The owner opened the card of a seat's own run (`run_id`) on a page: see
/// [`Runner::open_card`]. `false` with the switch off.
pub fn open_card(run_id: &str) -> bool {
    AT_START.get().is_some_and(|r| r.open_card(run_id))
}

/// What a page shows on that card: see [`Runner::held_card`].
pub fn held_card(run_id: &str) -> Option<serde_json::Value> {
    AT_START.get().and_then(|r| r.held_card(run_id))
}

/// [`Facts`] read from aibd itself.
pub struct AibdFacts {
    pub st: crate::api::AppState,
}

impl AibdFacts {
    fn data(&self) -> std::path::PathBuf {
        crate::registry::data_of(&self.st)
    }
}

impl Facts for AibdFacts {
    fn hosts(&self) -> Vec<TrustedHost> {
        let p = self
            .data()
            .join(crate::trusted_hosts::DIR)
            .join(crate::trusted_hosts::FILE);
        crate::trusted_hosts::load(&p)
            .map(|(hosts, _)| hosts)
            .unwrap_or_default()
    }

    fn trusted(&self, seat_id: &str) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        crate::trusted_seats::holds(crate::trusted_seats::until(&self.data(), seat_id), now)
    }

    fn copy_of(&self, seat_id: &str) -> Option<(SeatCopy, String)> {
        let meta = self.st.store.seat_meta(seat_id).ok()??;
        let tree = std::path::PathBuf::from(&meta.cwd);
        // the repo's folder is the registered one, never a path read beside the copy
        let (_, author) = crate::seat_worktree::base_of(&tree)?;
        let name = author.strip_prefix(crate::repos::REPO_AUTHOR_PREFIX)?;
        let repo = crate::repos::find(&self.data(), name)?;
        let (_, head) = crate::seat_worktree::clone_head(&tree)?;
        let hooks = crate::seats::seat_git_hooks(self.st.sessions.private_dir());
        std::fs::create_dir_all(&hooks).ok()?;
        Some((
            SeatCopy {
                tree,
                repo: std::path::PathBuf::from(&repo.path),
                hooks,
            },
            head,
        ))
    }

    fn work_dir(&self) -> std::path::PathBuf {
        self.data().join("receipts")
    }

    fn owner_answered(&self, run_id: &str, ok: bool) -> bool {
        crate::receipt_run::owner_answered(&self.st, run_id, ok)
    }

    fn ended(&self, run_id: &str, why: &'static str) {
        let _ = crate::receipt_run::runner_lost(&self.st, run_id, why);
    }
}

/// Sets aibd's runner when [`SWITCH`] is on, with the delegate key's ssh as its link
/// ([`crate::receipt_link`]). The key pair is made here the first time (off this thread).
/// While there is no key, or no machine has its line, every request is still refused or
/// ends `lost`: nothing runs anywhere that the owner did not set up.
pub fn set_at_start(st: &crate::api::AppState, switch: Option<String>) -> bool {
    if !switch_on(switch) {
        return false;
    }
    let data = crate::registry::data_of(st);
    let (ssh, keygen) = if cfg!(windows) {
        (
            "C:\\Windows\\System32\\OpenSSH\\ssh.exe",
            "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe",
        )
    } else {
        ("/usr/bin/ssh", "/usr/bin/ssh-keygen")
    };
    let for_key = data.clone();
    // only the key's id is ever written to a log, never the key
    let _ = std::thread::Builder::new()
        .name("aibd-receipt-key".into())
        .spawn(move || {
            match crate::receipt_link::ensure_key(&for_key, std::path::Path::new(keygen)) {
                Ok(line) => tracing::warn!(
                    key_id = %crate::receipt_link::key_id(&line),
                    "the receipt delegate key is there"
                ),
                Err(why) => tracing::warn!(why, "no receipt delegate key: requests stay refused"),
            }
        });
    let runner = Runner::new(
        std::sync::Arc::new(AibdFacts { st: st.clone() }),
        std::sync::Arc::new(crate::receipt_link::SshLink::new(
            data,
            std::path::PathBuf::from(ssh),
        )),
        std::sync::Arc::new(TouchOwner),
    );
    let _ = AT_START.set(runner.clone());
    crate::receipt_run::set_runner(std::sync::Arc::new(Started(runner)));
    true
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const HEAD: &str = "0123456789abcdef0123456789abcdef01234567";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const IO: &str = "registry+https://github.com/rust-lang/crates.io-index";

    fn host(name: &str, os: &str) -> TrustedHost {
        TrustedHost {
            name: name.into(),
            hostname: "192.0.2.10".into(),
            port: 22,
            user: "owner".into(),
            hostkey: "ssh-ed25519 AAAA".into(),
            os: os.into(),
        }
    }

    fn lock(packages: &[(&str, &str)]) -> String {
        let mut s = String::from("version = 4\n");
        for (n, v) in packages {
            s.push_str(&format!(
                "\n[[package]]\nname = \"{n}\"\nversion = \"{v}\"\nsource = \"{IO}\"\nchecksum = \"x\"\n"
            ));
        }
        s.push_str("\n[[package]]\nname = \"aibd\"\nversion = \"0.2.0\"\ndependencies = [\n \"sha2\",\n]\n");
        s
    }

    fn ch(list: &[(&str, &str)]) -> Vec<(String, String)> {
        list.iter()
            .map(|(s, p)| ((*s).to_owned(), (*p).to_owned()))
            .collect()
    }

    #[test]
    fn the_switch_is_off_until_it_is_one() {
        assert!(switch_on(Some("1".into())));
        for off in [
            None,
            Some(String::new()),
            Some("0".into()),
            Some("true".into()),
        ] {
            assert!(!switch_on(off.clone()), "reason: on for {off:?}");
        }
    }

    #[test]
    fn a_receipt_goes_to_a_pinned_mac_or_nowhere() {
        let hosts = [
            host("win", "windows"),
            host("lin", "mac"),
            host("mini", "mac"),
        ];
        assert_eq!(pick(&hosts).map(|h| h.name.as_str()), Some("lin"));
        assert!(
            pick(&[host("win", "windows"), host("box", "linux")]).is_none(),
            "reason: no_sandbox_machine_picked"
        );
        assert!(pick(&[]).is_none());
    }

    #[test]
    fn a_lock_lists_its_packages_by_name_and_version() {
        let l = lock(&[("sha2", "0.10.9"), ("serde", "1.0.0")]);
        let p: Vec<String> = packages(&l).into_iter().collect();
        assert_eq!(p, ["aibd 0.2.0", "serde 1.0.0", "sha2 0.10.9"]);
        // a dependency list's words are not packages
        assert!(!p.iter().any(|x| x.starts_with("sha2,")));
        assert!(packages("").is_empty());
    }

    #[test]
    fn what_a_head_brings_is_read_from_its_changes_and_its_lock() {
        let base = lock(&[("sha2", "0.10.9")]);
        let head = lock(&[
            ("sha2", "0.10.9"),
            ("evil-macro", "0.0.1"),
            ("serde", "1.0.1"),
        ]);
        let changed = ch(&[
            ("M", "crates/aibd/src/lib.rs"),
            ("A", "crates/aibd/src/new.rs"),
            ("A", "crates/aibd/tests/new_test.rs"),
            ("A", "crates/aibd/tests/fixtures/x.json"),
            ("A", "crates/aibd/build.rs"),
            ("M", "crates/aib-store/build.rs"),
            ("D", "crates/old/build.rs"),
            ("M", "Cargo.toml"),
            ("D", "crates/old/Cargo.toml"),
            ("M", "Cargo.lock"),
            ("A", "docs/build.rs.md"),
        ]);
        let b = brings(&changed, &base, &head).unwrap();
        assert_eq!(
            b.build_scripts,
            ["crates/aibd/build.rs", "crates/aib-store/build.rs"],
            "reason: build_script_missed"
        );
        assert_eq!(b.manifests, ["Cargo.toml"]);
        assert_eq!(
            b.new_packages,
            ["evil-macro 0.0.1", "serde 1.0.1"],
            "reason: new_package_missed"
        );
        assert_eq!(b.new_code_files, 2);
        assert_eq!(b.files, 11);
        assert!(b.runs_new_code_at_build());
        // nothing new at build time
        let quiet = brings(&ch(&[("M", "crates/aibd/src/lib.rs")]), &base, &base).unwrap();
        assert!(!quiet.runs_new_code_at_build());
        assert_eq!(
            quiet,
            Brings {
                files: 1,
                ..Brings::default()
            }
        );
        // a version that moved is a new package
        let moved = brings(&[], &base, &lock(&[("sha2", "0.10.10")])).unwrap();
        assert_eq!(moved.new_packages, ["sha2 0.10.10"]);
    }

    #[test]
    fn a_head_that_picks_its_toolchain_or_its_sources_is_refused_before_a_card() {
        let l = lock(&[("sha2", "0.10.9")]);
        for p in [
            "rust-toolchain.toml",
            ".cargo/config.toml",
            "crates/aibd/.cargo/config",
            ".Cargo/config.toml",
        ] {
            for status in ["A", "M", "D"] {
                assert_eq!(
                    brings(&ch(&[("M", "src/lib.rs"), (status, p)]), &l, &l),
                    Err("tree_refused"),
                    "reason: taken {status} {p}"
                );
            }
        }
        let git = l.replace(IO, "git+https://example.invalid/x/y#abc");
        assert_eq!(
            brings(&ch(&[("M", "Cargo.lock")]), &l, &git),
            Err("tree_refused"),
            "reason: git_source_taken"
        );
        // the base's lock is not judged: only what would be fetched for the head
        assert!(brings(&ch(&[("M", "Cargo.lock")]), &git, &l).is_ok());
    }

    fn sent() -> Sent<'static> {
        Sent {
            host: "lin",
            run_id: "web-b4297-012345678",
            head: HEAD,
            base: "89abcdef0123456789abcdef0123456789abcdef",
            bundle_sha256: SHA,
            knives_sha256: NO_KNIVES_SHA256,
        }
    }

    #[test]
    fn the_fingerprint_is_for_this_bundle_on_this_machine() {
        let b = Brings {
            files: 3,
            ..Brings::default()
        };
        let ask = owner_ask(&sent(), &b, "receipt:web-b4297-012345678".into());
        assert_eq!(ask.operation, OP);
        assert_eq!(ask.by, "receipt:web-b4297-012345678");
        let same = owner_ask(&sent(), &b, "receipt:web-b4297-012345678".into());
        assert_eq!(ask.params, same.params);
        let other_sha = "b".repeat(64);
        let changes: [Sent<'_>; 6] = [
            Sent {
                host: "mini",
                ..sent()
            },
            Sent {
                run_id: "web-b4297-012345678-2",
                ..sent()
            },
            Sent {
                head: "1123456789abcdef0123456789abcdef01234567",
                ..sent()
            },
            Sent {
                base: HEAD,
                ..sent()
            },
            Sent {
                bundle_sha256: &other_sha,
                ..sent()
            },
            Sent {
                knives_sha256: &other_sha,
                ..sent()
            },
        ];
        for (i, s) in changes.iter().enumerate() {
            assert_ne!(
                owner_ask(s, &b, ask.by.clone()).params,
                ask.params,
                "reason: field {i} not bound"
            );
        }
        // what the head brings is bound too: a card read as quiet does not pass a loud bundle
        let loud = Brings {
            new_packages: vec!["evil-macro 0.0.1".into()],
            ..b.clone()
        };
        assert_ne!(owner_ask(&sent(), &loud, ask.by.clone()).params, ask.params);
        let script = Brings {
            build_scripts: vec!["build.rs".into()],
            ..b.clone()
        };
        assert_ne!(
            owner_ask(&sent(), &script, ask.by.clone()).params,
            ask.params
        );
    }

    #[test]
    fn the_window_says_when_new_code_runs_at_build() {
        let quiet = Brings {
            files: 3,
            new_code_files: 1,
            ..Brings::default()
        };
        let w = card_words("lin", "web-b4297-012345678", &quiet);
        assert_eq!(w, "收據到 lin · build.rs 0·新套件 0·新檔 1 · web-b4297-01…");
        assert!(w.chars().count() <= crate::owner_ok::RESOURCE_MAX);
        let loud = Brings {
            build_scripts: vec!["a/build.rs".into(), "b/build.rs".into()],
            new_packages: vec!["x 1.0.0".into()],
            ..quiet.clone()
        };
        let w = card_words("lin", "web-b4297-012345678-99", &loud);
        assert!(
            w.starts_with("會跑新程式 · 收據到 lin · build.rs 2·新套件 1"),
            "reason: quiet_card"
        );
        // the longest name, run id and counts still fit the window whole: nothing is cut by
        // the window, and the warning and the machine come first
        let most = Brings {
            build_scripts: vec!["b".into(); 2000],
            new_packages: vec!["p".into(); 2000],
            new_code_files: 123_456,
            ..Brings::default()
        };
        let long = card_words(&"m".repeat(32), "web-b4297-012345678-99", &most);
        assert!(
            long.chars().count() <= crate::owner_ok::RESOURCE_MAX,
            "reason: window_cuts_the_front ({long})"
        );
        assert!(long.starts_with("會跑新程式 · 收據到 mmmmmmmmmmmmmmmm · build.rs 999"));
        assert_eq!(crate::owner_ok::clean_resource(&long), long);
        // no name a seat chose is in the window
        assert!(!w.contains("a/build.rs") && !w.contains("x 1.0.0"));
        let page = page_card("lin", &loud);
        assert_eq!(page["red"], true);
        assert_eq!(page["build_scripts_n"], 2);
        assert_eq!(page["new_packages"][0], "x 1.0.0");
        let many = Brings {
            new_packages: (0..40).map(|i| format!("p{i} 1.0.0")).collect(),
            ..Brings::default()
        };
        let page = page_card("lin", &many);
        assert_eq!(
            page["new_packages"].as_array().map(Vec::len),
            Some(NAMED_MAX)
        );
        assert_eq!(page["new_packages_n"], 40);
        // a new package alone is red: its build script and proc-macros run
        assert_eq!(page["red"], true, "reason: new_package_not_red");
        assert!(card_words("lin", "web-b4297-012345678", &many).starts_with("會跑新程式 · "));
    }

    /// ebab8 11-25 14:01 (SCAN-owner-window-words 3): the run id is the sending seat's word.
    /// However long, or written to look like aibd's words (another machine, a quiet count), it
    /// comes after the warning, the machine and the counts, cut, and the window shows it all.
    #[test]
    fn a_run_id_never_stands_before_the_warning_or_the_machine() {
        let loud = Brings {
            build_scripts: vec!["a/build.rs".into()],
            ..Brings::default()
        };
        let fixed = "會跑新程式 · 收據到 lin · build.rs 1·新套件 0·新檔 0 · ";
        let long = format!("web-{}", "9".repeat(60));
        let fake = "到 andymini·build.rs 0·新套件 0";
        for id in [long.as_str(), fake] {
            let w = card_words("lin", id, &loud);
            assert!(
                w.chars().count() <= crate::owner_ok::RESOURCE_MAX,
                "reason: window_cuts {w}"
            );
            let shown = crate::owner_ok::object_shown(&w);
            crate::owner_ok::fixed_first(&shown, fixed, &["andymini", "build.rs 0"]).unwrap();
            assert!(w.ends_with('…'), "reason: run_id_not_cut {w}");
        }
    }

    #[test]
    fn a_line_to_a_machine_is_one_the_gate_reads() {
        let run = run_words("web-b4297-012345678", HEAD, 1000, SHA).unwrap();
        assert_eq!(
            run,
            format!("run web-b4297-012345678 {HEAD} ws 1000 {SHA} 0 {NO_KNIVES_SHA256}")
        );
        assert_eq!(
            read_words("web-b4297-012345678-2").unwrap(),
            "fetch web-b4297-012345678-2"
        );
        assert_eq!(
            cancel_words("web-b4297-012345678").unwrap(),
            "cancel web-b4297-012345678"
        );
        // a word that would be another command never becomes a line
        for bad in [
            "web-b4297-012345678;id",
            "web-b4297-012345678 x",
            "$(id)",
            "",
            "../x",
        ] {
            assert!(read_words(bad).is_err(), "reason: sent {bad:?}");
            assert!(cancel_words(bad).is_err(), "reason: sent {bad:?}");
            assert!(
                run_words(bad, HEAD, 1, SHA).is_err(),
                "reason: sent {bad:?}"
            );
        }
        assert_eq!(
            run_words("web-b4297-987654321", HEAD, 1, SHA),
            Err("run_id_not_head"),
            "reason: other_head_sent"
        );
        assert!(run_words("web-b4297-012345678", HEAD, 0, SHA).is_err());
        assert!(run_words("web-b4297-012345678", HEAD, 1, "short").is_err());
        // the digest of no knives is the digest of no bytes
        use sha2::{Digest, Sha256};
        let none: String = Sha256::digest(b"")
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(none, NO_KNIVES_SHA256);
    }

    // ---- slice R4b: the runner, with a machine, an owner and aibd's facts that are doubles

    use crate::receipt_bundle::fixture::{fix, Fix};
    use std::collections::VecDeque;
    use std::path::Path;
    use std::sync::{Arc, Mutex};

    struct FakeLink {
        ready: bool,
        answers: Mutex<VecDeque<Vec<u8>>>,
        /// (machine, line, sha256 of what was on standard input)
        said: Mutex<Vec<(String, String, Option<String>)>>,
    }

    impl Link for FakeLink {
        fn ready(&self) -> bool {
            self.ready
        }
        fn say(&self, host: &TrustedHost, line: &str, stdin: Option<&Path>, _: u64) -> Vec<u8> {
            use sha2::{Digest, Sha256};
            let sha = stdin.map(|p| {
                Sha256::digest(std::fs::read(p).unwrap())
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>()
            });
            self.said
                .lock()
                .unwrap()
                .push((host.name.clone(), line.to_owned(), sha));
            self.answers.lock().unwrap().pop_front().unwrap_or_default()
        }
    }

    struct FakeOwner {
        yes: bool,
        asked: Mutex<Vec<crate::owner_ok::Ask>>,
    }

    impl Owner for FakeOwner {
        fn approves(&self, ask: &crate::owner_ok::Ask) -> bool {
            self.asked.lock().unwrap().push(ask.clone());
            self.yes
        }
    }

    struct FakeFacts {
        hosts: Vec<TrustedHost>,
        trusted: bool,
        copy: Option<(SeatCopy, String)>,
        dir: std::path::PathBuf,
        told: Mutex<Vec<String>>,
    }

    impl Facts for FakeFacts {
        fn hosts(&self) -> Vec<TrustedHost> {
            self.hosts.clone()
        }
        fn trusted(&self, _: &str) -> bool {
            self.trusted
        }
        fn copy_of(&self, _: &str) -> Option<(SeatCopy, String)> {
            self.copy.clone()
        }
        fn work_dir(&self) -> std::path::PathBuf {
            self.dir.clone()
        }
        fn owner_answered(&self, run_id: &str, ok: bool) -> bool {
            self.told
                .lock()
                .unwrap()
                .push(format!("owner {run_id} {ok}"));
            true
        }
        fn ended(&self, run_id: &str, why: &'static str) {
            self.told
                .lock()
                .unwrap()
                .push(format!("ended {run_id} {why}"));
        }
    }

    struct World {
        f: Fix,
        head: String,
        facts: Arc<FakeFacts>,
        link: Arc<FakeLink>,
        owner: Arc<FakeOwner>,
        runner: Arc<Runner>,
    }

    impl World {
        fn ask(&self) -> crate::receipt_run::Ask {
            crate::receipt_run::Ask {
                run_id: format!("web-b4297-{}", &self.head[..9]),
                seat_id: "seat-b4297".into(),
                head: self.head.clone(),
                narrow: false,
                tests: Vec::new(),
                by_seat: false,
            }
        }
        fn told(&self) -> Vec<String> {
            self.facts.told.lock().unwrap().clone()
        }
        fn said(&self) -> Vec<(String, String, Option<String>)> {
            self.link.said.lock().unwrap().clone()
        }
        fn asked(&self) -> usize {
            self.owner.asked.lock().unwrap().len()
        }
        fn nothing_left(&self) -> bool {
            match std::fs::read_dir(self.facts.work_dir()) {
                Ok(mut d) => d.next().is_none(),
                Err(_) => true,
            }
        }
    }

    /// A seat's copy with one commit (`file`) on the repo's main, a machine that will give
    /// `answers` in order and an owner who says `yes`.
    fn world(file: &str, yes: bool, answers: &[&[u8]]) -> World {
        let f = fix();
        let head = f.commit(file, "fn x() {}\n");
        let facts = Arc::new(FakeFacts {
            hosts: vec![host("win", "windows"), host("lin", "mac")],
            trusted: true,
            copy: Some((
                SeatCopy {
                    tree: f.tree.clone(),
                    repo: f.repo.clone(),
                    hooks: f.hooks.clone(),
                },
                head.clone(),
            )),
            dir: f.data.path().join("receipts"),
            told: Mutex::new(Vec::new()),
        });
        let link = Arc::new(FakeLink {
            ready: true,
            answers: Mutex::new(answers.iter().map(|a| a.to_vec()).collect()),
            said: Mutex::new(Vec::new()),
        });
        let owner = Arc::new(FakeOwner {
            yes,
            asked: Mutex::new(Vec::new()),
        });
        let runner = Runner::new(facts.clone(), link.clone(), owner.clone());
        World {
            f,
            head,
            facts,
            link,
            owner,
            runner,
        }
    }

    fn with(w: &World, change: impl FnOnce(&mut FakeFacts), ready: bool) -> Arc<Runner> {
        let mut facts = FakeFacts {
            hosts: w.facts.hosts.clone(),
            trusted: w.facts.trusted,
            copy: w.facts.copy.clone(),
            dir: w.facts.dir.clone(),
            told: Mutex::new(Vec::new()),
        };
        change(&mut facts);
        let link = Arc::new(FakeLink {
            ready,
            answers: Mutex::new(VecDeque::new()),
            said: Mutex::new(Vec::new()),
        });
        Runner::new(Arc::new(facts), link, w.owner.clone())
    }

    #[test]
    fn a_request_is_refused_before_anything_leaves_or_is_asked() {
        let w = world("src/new.rs", true, &[]);
        let ask = w.ask();
        assert_eq!(
            with(&w, |_| {}, false).check(&ask).err(),
            Some("no_host"),
            "reason: no_link_asked"
        );
        let narrow = crate::receipt_run::Ask {
            narrow: true,
            tests: vec!["aibd:usage".into()],
            ..ask.clone()
        };
        assert_eq!(w.runner.check(&narrow).err(), Some("receipt_not_allowed"));
        let no_mac = with(&w, |f| f.hosts = vec![host("win", "windows")], true);
        assert_eq!(
            no_mac.check(&ask).err(),
            Some("no_host"),
            "reason: windows_picked"
        );
        let untrusted = with(&w, |f| f.trusted = false, true);
        assert_eq!(
            untrusted.check(&ask).err(),
            Some("receipt_not_allowed"),
            "reason: untrusted_seat_ran"
        );
        let no_copy = with(&w, |f| f.copy = None, true);
        assert_eq!(no_copy.check(&ask).err(), Some("receipt_not_allowed"));
        let other = crate::receipt_run::Ask {
            head: "0123456789abcdef0123456789abcdef01234567".into(),
            ..ask.clone()
        };
        assert_eq!(
            w.runner.check(&other).err(),
            Some("head_not_seat_head"),
            "reason: other_head_ran"
        );
        let (h, copy) = w.runner.check(&ask).unwrap();
        assert_eq!(h.name, "lin");
        assert_eq!(copy.tree, w.f.tree);
        // checking asked nobody and said nothing
        assert_eq!(w.asked(), 0);
        assert!(w.said().is_empty());
    }

    #[test]
    fn a_yes_sends_exactly_the_bundle_the_owner_was_shown() {
        let w = world(
            "src/new.rs",
            true,
            &[
                b"OK run\nSTARTED x\n",
                b"ERR not_done\n",
                b"OK fetch\nRC fmt=0\n",
            ],
        );
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        // being prepared: the machine is not asked for it
        assert_eq!(w.runner.read(&ask.run_id), None);
        assert!(w.said().is_empty(), "reason: asked_before_sent");
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(w.told(), [format!("owner {} true", ask.run_id)]);
        let said = w.said();
        assert_eq!(said.len(), 1);
        let (machine, line, on_stdin) = &said[0];
        assert_eq!(machine, "lin");
        let words: Vec<&str> = line.split(' ').collect();
        assert_eq!(
            &words[..4],
            ["run", ask.run_id.as_str(), w.head.as_str(), "ws"]
        );
        // what went over is what the line names and what the fingerprint was for
        let shown = w.owner.asked.lock().unwrap()[0].clone();
        let params: serde_json::Value = serde_json::from_slice(&shown.params).unwrap();
        assert_eq!(
            on_stdin.as_deref(),
            Some(words[5]),
            "reason: other_bytes_sent"
        );
        assert_eq!(
            params["bundle_sha256"], words[5],
            "reason: other_bundle_approved"
        );
        assert_eq!(params["host"], "lin");
        assert_eq!(params["run_id"], ask.run_id.as_str());
        assert_eq!(params["base"], w.f.main.as_str());
        assert_eq!(shown.by, format!("receipt:{}", ask.run_id));
        assert!(w.nothing_left(), "reason: bundle_left");
        // then it is read: not done is no ending, the .out comes whole
        assert_eq!(w.runner.read(&ask.run_id), None);
        assert_eq!(w.told().len(), 1, "reason: under_way_ended");
        assert_eq!(w.runner.read(&ask.run_id), Some(b"RC fmt=0\n".to_vec()));
        assert_eq!(w.said()[2].1, format!("fetch {}", ask.run_id));
    }

    #[test]
    fn a_seats_own_request_waits_for_its_card_to_be_opened() {
        let w = world("src/new.rs", true, &[b"OK run\nSTARTED x\n"]);
        let ask = crate::receipt_run::Ask {
            by_seat: true,
            ..w.ask()
        };
        let (h, copy) = w.runner.check(&ask).unwrap();
        assert_eq!(w.runner.held_card(&ask.run_id), None);
        w.runner.carry(&ask, &h, &copy);
        // the bundle is made and judged, and nobody was asked for a fingerprint
        assert_eq!(w.asked(), 0, "reason: window_for_a_seats_request");
        assert!(w.said().is_empty() && w.told().is_empty());
        assert!(!w.nothing_left(), "reason: held_bundle_gone");
        let card = w.runner.held_card(&ask.run_id).unwrap();
        assert_eq!(card["host"], crate::receipt_run::nickname("lin"));
        assert_eq!(w.runner.read(&ask.run_id), None);
        assert!(w.said().is_empty(), "reason: asked_before_sent");
        // opened once: the owner is asked for that bundle, a yes sends it
        assert!(!w.runner.open_card("web-b4297-000000000"));
        let held = w.runner.take_card(&ask.run_id).unwrap();
        assert!(
            !w.runner.open_card(&ask.run_id),
            "reason: card_opened_twice"
        );
        w.runner.send_held(&held);
        assert_eq!(w.asked(), 1);
        assert_eq!(w.told(), [format!("owner {} true", ask.run_id)]);
        let said = w.said();
        assert_eq!(said.len(), 1, "reason: held_bundle_not_sent");
        let shown = w.owner.asked.lock().unwrap()[0].clone();
        let params: serde_json::Value = serde_json::from_slice(&shown.params).unwrap();
        let words: Vec<&str> = said[0].1.split(' ').collect();
        assert_eq!(said[0].2.as_deref(), Some(words[5]));
        assert_eq!(params["bundle_sha256"], words[5]);
        assert!(w.nothing_left(), "reason: bundle_left");
        assert_eq!(w.runner.held_card(&ask.run_id), None);
    }

    #[test]
    fn a_seats_own_request_withdrawn_on_its_card_asks_nobody() {
        let w = world("src/new.rs", true, &[]);
        let ask = crate::receipt_run::Ask {
            by_seat: true,
            ..w.ask()
        };
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        assert!(!w.nothing_left());
        w.runner.cancel(&ask.run_id);
        assert!(w.nothing_left(), "reason: withdrawn_bundle_left");
        assert!(
            !w.runner.open_card(&ask.run_id),
            "reason: withdrawn_card_opened"
        );
        assert_eq!(w.asked(), 0);
        assert!(w.said().is_empty() && w.told().is_empty());
    }

    #[test]
    fn the_runner_hands_over_the_gates_own_skips() {
        use crate::receipt_run::ReceiptRunner;
        let w = world("src/new.rs", true, &[]);
        let (names, sha) = Started(w.runner.clone()).skips().unwrap();
        assert_eq!(names.len(), aib_receipt_gate::sandbox::SKIPPED.len());
        assert_eq!(names[0], aib_receipt_gate::sandbox::SKIPPED[0].0);
        assert_eq!(sha, aib_receipt_gate::sandbox::skipped_sha256());
    }

    #[test]
    fn a_no_sends_nothing() {
        let w = world("src/new.rs", false, &[]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(w.asked(), 1);
        assert!(w.said().is_empty(), "reason: sent_without_a_yes");
        assert_eq!(w.told(), [format!("owner {} false", ask.run_id)]);
        assert!(w.nothing_left(), "reason: bundle_left");
    }

    #[test]
    fn a_head_the_gate_would_refuse_never_reaches_the_owner() {
        let w = world(".cargo/config.toml", true, &[]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(w.asked(), 0, "reason: card_for_a_refused_tree");
        assert!(w.said().is_empty());
        assert_eq!(w.told(), [format!("ended {} tree_refused", ask.run_id)]);
        assert!(w.nothing_left());
    }

    #[test]
    fn a_machines_refusal_ends_the_run_with_its_reason() {
        let w = world("src/new.rs", true, &[b"ERR busy\n"]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(
            w.told(),
            [
                format!("owner {} true", ask.run_id),
                format!("ended {} no_host", ask.run_id)
            ]
        );
        // a machine that did not answer the send at all: lost
        let w = world("src/new.rs", true, &[]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(w.told()[1], format!("ended {} lost", ask.run_id));
    }

    #[test]
    fn a_run_withdrawn_while_it_is_prepared_is_not_sent() {
        let w = world("src/new.rs", true, &[]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.cancel(&ask.run_id);
        // nothing was sent yet: nothing to cancel at the machine
        assert!(w.said().is_empty());
        w.runner.carry(&ask, &h, &copy);
        assert_eq!(w.asked(), 0, "reason: card_for_a_withdrawn_run");
        assert!(w.said().is_empty(), "reason: withdrawn_run_sent");
        assert!(w.nothing_left());
    }

    #[test]
    fn a_silent_machine_ends_a_run_late_and_one_that_never_heard_of_it_at_once() {
        // an aibd that started again does not know the run: the machine is asked
        let w = world("src/new.rs", true, &[]);
        let id = w.ask().run_id;
        for _ in 1..MISSED_READS {
            assert_eq!(w.runner.read(&id), None);
        }
        assert!(w.told().is_empty(), "reason: one_missed_read_ended");
        assert_eq!(w.runner.read(&id), None);
        assert_eq!(w.told(), [format!("ended {id} lost")]);
        assert_eq!(w.said()[0].1, format!("fetch {id}"));
        let w = world("src/new.rs", true, &[b"ERR no_such_run\n"]);
        let id = w.ask().run_id;
        assert_eq!(w.runner.read(&id), None);
        assert_eq!(w.told(), [format!("ended {id} lost")]);
        // a withdrawal of a run that was sent is said to its machine
        let w = world("src/new.rs", true, &[b"OK run\n", b"OK cancel\n"]);
        let ask = w.ask();
        let (h, copy) = w.runner.check(&ask).unwrap();
        w.runner.carry(&ask, &h, &copy);
        w.runner.cancel(&ask.run_id);
        assert_eq!(w.said()[1].1, format!("cancel {}", ask.run_id));
    }

    #[test]
    fn a_reason_this_side_does_not_know_is_not_the_trees_fault() {
        for line in ["ERR anything_else", "ERR fetch_failed", "ERR config_bad"] {
            let got = answer(format!("{line}\n").as_bytes(), "run");
            assert_eq!(got, Err("refused"), "reason: {line} blamed_on_the_tree");
        }
        assert_eq!(crate::receipt_run::said("refused"), "refused");
        // and through a fetch
        assert_eq!(fetched(b"ERR out_unreadable\n"), Fetched::Ended("refused"));
    }

    #[test]
    fn a_fetch_brings_the_out_or_says_how_the_run_stands() {
        assert_eq!(
            fetched(b"OK fetch\nRC fmt=0\nRECEIPT-SHA256 x\n"),
            Fetched::Out(b"RC fmt=0\nRECEIPT-SHA256 x\n".to_vec())
        );
        assert_eq!(fetched(b"OK fetch\n"), Fetched::Out(Vec::new()));
        assert_eq!(
            fetched(b"ERR not_done\n"),
            Fetched::NotYet,
            "reason: under_way_ended"
        );
        assert_eq!(
            fetched(b""),
            Fetched::NotYet,
            "reason: one_missed_read_ended"
        );
        assert_eq!(fetched(b"ERR lost_run\n"), Fetched::Ended("lost"));
        assert_eq!(fetched(b"ERR no_such_run\n"), Fetched::Ended("lost"));
        assert_eq!(
            fetched(b"ERR tree_refused\n"),
            Fetched::Ended("tree_refused")
        );
        assert_eq!(fetched(b"ERR disk_low\n"), Fetched::Ended("disk_low"));
        // lines of a receipt that only look like an answer are the receipt
        assert_eq!(
            fetched(b"OK fetch\nERR not_done\n"),
            Fetched::Out(b"ERR not_done\n".to_vec())
        );
        // another verb's OK, or anything else, is not an .out
        assert_eq!(
            fetched(b"OK status\nstate done\n"),
            Fetched::Ended("lost"),
            "reason: other_ok_read"
        );
        assert_eq!(fetched(b"OK fetch"), Fetched::Ended("lost"));
        assert_eq!(fetched(b"Welcome\n"), Fetched::Ended("lost"));
    }

    #[test]
    fn a_gates_answer_is_ok_or_a_fixed_reason() {
        assert_eq!(
            answer(b"OK run\nSTARTED web-b4297-012345678\n", "run"),
            Ok(())
        );
        assert_eq!(answer(b"OK fetch\r\n", "fetch"), Ok(()));
        // another verb's OK is not this one's
        assert_eq!(
            answer(b"OK status\n", "run"),
            Err("lost"),
            "reason: other_ok_taken"
        );
        assert_eq!(answer(b"OK runner\n", "run"), Err("lost"));
        assert_eq!(answer(b"", "run"), Err("lost"));
        assert_eq!(answer(b"\xff\xfe\n", "run"), Err("lost"));
        assert_eq!(answer(b"Welcome to a shell\n", "run"), Err("lost"));
        for (line, why) in [
            ("ERR receipt_tool_changed", "receipt_tool_changed"),
            ("ERR gate_writable", "host_changed"),
            ("ERR disk_low", "disk_low"),
            ("ERR no_toolchain", "toolchain_changed"),
            ("ERR busy", "no_host"),
            ("ERR not_ready", "no_host"),
            ("ERR tree_refused", "tree_refused"),
            ("ERR sha256_mismatch", "tree_refused"),
            ("ERR bundle_not_head", "tree_refused"),
        ] {
            let got = answer(format!("{line}\nmore\n").as_bytes(), "run");
            assert_eq!(got, Err(why), "reason: {line}");
            assert!(
                crate::receipt_run::REFUSALS.contains(&why),
                "reason: {why} is not a word a page is told"
            );
        }
    }
}
