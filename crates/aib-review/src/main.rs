//! `aib-review`: the review gate's command line (S78 r-1, DEV-S78). See lib.rs.
//!
//! ```text
//! aib-review submit --pr <number|branch> --author <5> --file <REVIEW-….md>
//!                   [--verifies R1-M1 …] [--seat X] [--repo <dir>]
//! aib-review fix --pr X --ids R1-M1 … --commit <40 hex> [--seat X] [--repo <dir>]
//! aib-review status [--pr X] [--json]
//! aib-review gate --pr X --head <40 hex> [--receipt <TEST-….txt>] [--repo <dir>]
//! (--seat may be left out when AIB_SEAT_CODE is set; --repo defaults to the current folder)
//! ```
//!
//! rc 0 done / gate passes · 2 the ledger is incomplete (gate says so too: never a silent
//! wait) · 3 refused by a rule, or the gate is blocked (each reason printed) · 4 the lock was
//! not taken in 2 s, a read / write / sync failed, or git could not be run · 64 usage.
//!
//! Ledger `<root>/state/review/review.jsonl`, lock `<root>/state/review/review.lock` (never
//! removed), root = `EZ2_AIB_HOME` or `~/.ez2-aib` (`~` = USERPROFILE on Windows, HOME
//! elsewhere). `AIB_REVIEW_NOW_MS` is for tests.

use aib_review::{
    check_counts, clean, fix_line, gate, hex40_ok, parse_findings, parse_first_line, pr_ok,
    receipt_heads, replay, seat_ok, sha256_hex, submit_line, Ledger, Level, State, Submit,
    MAX_FILE_BYTES, MAX_LINE_BYTES,
};
use serde_json::{json, Value};
use std::collections::hash_map::RandomState;
use std::fs;
use std::hash::{BuildHasher, Hasher};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const USAGE: &str = "\
aib-review submit --pr <PR 號|分支> --author <作者席 5 碼> --file <審查稿.md> [--verifies R1-M1 …] [--seat X] [--repo <資料夾>]
aib-review fix --pr X --ids R1-M1 … --commit <40 碼> [--seat X] [--repo <資料夾>]
aib-review status [--pr X] [--json]
aib-review gate --pr X --head <40 碼> [--receipt <收據>] [--repo <資料夾>]

審查稿第一行:結論: 通過|不通過 頭: <40 碼> H/M/L/N: a/b/c/d 範圍: <範圍>
新發現一條一行:- [H1] … / - [M1] … / - [L1] … / - [N1] …(每級從 1 連號,條數要等於第一行)。
fix 只有作者能記,只到 claimed;下一輪非作者審查 submit --verifies 才 verified。
gate:rc 0 過 · 3 擋(每條理由印出)· 2 帳本不完整。這是席位間的紀律檢查,不是安全防線。
--seat 只有設了 AIB_SEAT_CODE 才能省。
";

const LOCK_WAIT: Duration = Duration::from_secs(2);

enum Fail {
    Usage(String),
    Incomplete,
    Rule(String),
    Io(String),
}

impl Fail {
    fn rc(&self) -> u8 {
        match self {
            Fail::Usage(_) => 64,
            Fail::Incomplete => 2,
            Fail::Rule(_) => 3,
            Fail::Io(_) => 4,
        }
    }
}

fn usage(s: impl Into<String>) -> Fail {
    Fail::Usage(s.into())
}

fn rule(s: impl Into<String>) -> Fail {
    Fail::Rule(s.into())
}

fn nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

fn root() -> Result<PathBuf, Fail> {
    if let Some(h) = nonempty("EZ2_AIB_HOME") {
        return Ok(PathBuf::from(h));
    }
    #[cfg(windows)]
    let home = nonempty("USERPROFILE").or_else(|| nonempty("HOME"));
    #[cfg(not(windows))]
    let home = nonempty("HOME").or_else(|| nonempty("USERPROFILE"));
    home.map(|h| PathBuf::from(h).join(".ez2-aib"))
        .ok_or_else(|| usage("no EZ2_AIB_HOME and no home folder"))
}

fn review_dir() -> Result<PathBuf, Fail> {
    Ok(root()?.join("state").join("review"))
}

fn now_ms() -> i64 {
    if let Some(n) = nonempty("AIB_REVIEW_NOW_MS").and_then(|v| v.parse::<i64>().ok()) {
        return n;
    }
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

static SALT: AtomicU64 = AtomicU64::new(0);

/// 128 random-enough bits as 32 hex; uniqueness is checked against the ledger under the lock.
fn new_event_id(taken: impl Fn(&str) -> bool) -> String {
    loop {
        let mut out = String::new();
        for _ in 0..2 {
            let mut h = RandomState::new().build_hasher();
            h.write_u32(std::process::id());
            h.write_u128(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_or(0, |d| d.as_nanos()),
            );
            h.write_u64(SALT.fetch_add(1, Ordering::Relaxed));
            out.push_str(&format!("{:016x}", h.finish()));
        }
        if !taken(&out) {
            return out;
        }
    }
}

fn read_ledger(path: &Path) -> Result<Ledger, Fail> {
    match aib_ledger::read(path) {
        Ok(aib_ledger::Read::Text(t)) => Ok(replay(&t)),
        Ok(aib_ledger::Read::NotUtf8) => Ok(Ledger {
            errors: vec!["not UTF-8".into()],
            ..Ledger::default()
        }),
        Ok(aib_ledger::Read::Missing) => Ok(Ledger::default()),
        Err(e) => Err(Fail::Io(format!("read: {e}"))),
    }
}

fn report(errors: &[String]) {
    for e in errors {
        eprintln!("aib-review: ledger {e}");
    }
    if !errors.is_empty() {
        eprintln!(
            "aib-review: the ledger is incomplete (不完整): writes are refused and the gate does not pass until it is fixed"
        );
    }
}

/// Lock → replay → `decide` → append one line → sync_all → unlock (aib-ledger's one write path).
fn write_locked(
    decide: impl FnOnce(&Ledger) -> Result<(Option<String>, String), Fail>,
) -> Result<String, Fail> {
    let dir = review_dir()?;
    fs::create_dir_all(&dir).map_err(|e| Fail::Io(format!("mkdir: {e}")))?;
    aib_ledger::write_if_complete(
        &dir.join("review.lock"),
        &dir.join("review.jsonl"),
        LOCK_WAIT,
        MAX_LINE_BYTES,
        |text| {
            let l = replay(text);
            let problems = l.errors.clone();
            (l, problems)
        },
        decide,
    )
    .map_err(|e| match e {
        aib_ledger::Refused::Lock(s) | aib_ledger::Refused::Read(s) => Fail::Io(s),
        aib_ledger::Refused::Incomplete(errors) => {
            report(&errors);
            Fail::Incomplete
        }
        aib_ledger::Refused::Rule(f) => f,
        aib_ledger::Refused::Append(aib_ledger::Append::TooLong(n)) => {
            rule(format!("the line is over {n} bytes"))
        }
        aib_ledger::Refused::Append(aib_ledger::Append::Io(s)) => Fail::Io(s),
    })
}

/// `--name value…`, bare `--flag`. No words outside flags.
struct Args {
    pairs: Vec<(String, Vec<String>)>,
}

impl Args {
    fn parse(raw: &[String], bools: &[&str], known: &[&str]) -> Result<Args, Fail> {
        let mut pairs: Vec<(String, Vec<String>)> = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let name = raw[i]
                .strip_prefix("--")
                .ok_or_else(|| usage(format!("unexpected word {}", raw[i])))?;
            if !known.contains(&name) {
                return Err(usage(format!("unknown flag --{name}")));
            }
            if pairs.iter().any(|(k, _)| k == name) {
                return Err(usage(format!("--{name} given twice")));
            }
            let mut vals = Vec::new();
            if !bools.contains(&name) {
                while i + 1 < raw.len() && !raw[i + 1].starts_with("--") {
                    i += 1;
                    vals.push(raw[i].clone());
                }
            }
            pairs.push((name.to_string(), vals));
            i += 1;
        }
        Ok(Args { pairs })
    }
    fn has(&self, k: &str) -> bool {
        self.pairs.iter().any(|(x, _)| x == k)
    }
    fn many(&self, k: &str) -> Option<&[String]> {
        self.pairs
            .iter()
            .find(|(x, _)| x == k)
            .map(|(_, v)| v.as_slice())
    }
    fn one(&self, k: &str) -> Result<Option<String>, Fail> {
        match self.many(k) {
            None => Ok(None),
            Some([v]) if !v.is_empty() => Ok(Some(v.clone())),
            Some(_) => Err(usage(format!("--{k} takes one value"))),
        }
    }
    fn need(&self, k: &str) -> Result<String, Fail> {
        self.one(k)?
            .ok_or_else(|| usage(format!("--{k} is required")))
    }
    fn pr(&self) -> Result<String, Fail> {
        let p = self.need("pr")?;
        if !pr_ok(&p) {
            return Err(usage(format!(
                "--pr {}: a number or a branch name",
                clean(&p)
            )));
        }
        Ok(p)
    }
    fn seat_of(&self, k: &str, env: Option<&str>) -> Result<String, Fail> {
        let s = match (self.one(k)?, env) {
            (Some(s), _) => s,
            (None, Some(e)) => {
                nonempty(e).ok_or_else(|| usage(format!("--{k} is required (or {e})")))?
            }
            (None, None) => return Err(usage(format!("--{k} is required"))),
        };
        if !seat_ok(&s) {
            return Err(usage(format!("--{k} {}: 5 lower-case hex", clean(&s))));
        }
        Ok(s)
    }
    fn head(&self, k: &str) -> Result<String, Fail> {
        let h = self.need(k)?;
        if !hex40_ok(&h) {
            return Err(usage(format!("--{k}: 40 lower-case hex")));
        }
        Ok(h)
    }
    fn repo(&self) -> Result<PathBuf, Fail> {
        Ok(PathBuf::from(
            self.one("repo")?.unwrap_or_else(|| ".".to_owned()),
        ))
    }
}

/// `git -C <repo> <args>`: Some(true) rc 0, Some(false) rc 1, Err otherwise.
fn git_yes(repo: &Path, args: &[&str]) -> Result<bool, Fail> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| Fail::Io(format!("git: {e}")))?;
    match out.status.code() {
        Some(0) => Ok(true),
        Some(1) => Ok(false),
        c => Err(Fail::Io(format!(
            "git {}: rc {c:?} {}",
            args.join(" "),
            clean(String::from_utf8_lossy(&out.stderr).trim())
        ))),
    }
}

fn commit_exists(repo: &Path, c: &str) -> Result<bool, Fail> {
    // cat-file -e answers 128 for a missing object as well as for "not a repository", so
    // ask rev-parse whether this is a repository first
    if !git_yes(repo, &["rev-parse", "--git-dir"])? {
        return Err(Fail::Io(format!(
            "{} is not a git repository",
            repo.display()
        )));
    }
    let probe = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["cat-file", "-e", &format!("{c}^{{commit}}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| Fail::Io(format!("git: {e}")))?;
    Ok(probe.success())
}

fn cmd_submit(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(
        raw,
        &[],
        &["pr", "author", "file", "verifies", "seat", "repo"],
    )?;
    let pr = a.pr()?;
    let seat = a.seat_of("seat", Some("AIB_SEAT_CODE"))?;
    let author = a.seat_of("author", None)?;
    let file = PathBuf::from(a.need("file")?);
    let repo = a.repo()?;
    let verifies: Vec<String> = a
        .many("verifies")
        .map(<[String]>::to_vec)
        .unwrap_or_default();
    if a.has("verifies") && verifies.is_empty() {
        return Err(usage("--verifies takes one or more finding ids"));
    }
    let bytes = fs::read(&file).map_err(|e| Fail::Io(format!("read {}: {e}", file.display())))?;
    if bytes.len() > MAX_FILE_BYTES {
        return Err(rule(format!(
            "the review file is over {MAX_FILE_BYTES} bytes"
        )));
    }
    let text = String::from_utf8(bytes).map_err(|_| rule("the review file is not UTF-8"))?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let first = parse_first_line(text.lines().next().unwrap_or_default()).map_err(rule)?;
    let findings = parse_findings(text).map_err(rule)?;
    check_counts(&first, &findings).map_err(rule)?;
    if !commit_exists(&repo, &first.head)? {
        return Err(rule(format!(
            "頭 {} is not a commit in {}",
            &first.head[..9],
            repo.display()
        )));
    }
    // each fix being confirmed must be in the reviewed head (DEV-S78 §3)
    let dir = review_dir()?;
    let before = read_ledger(&dir.join("review.jsonl"))?;
    if !before.complete() {
        report(&before.errors);
        return Err(Fail::Incomplete);
    }
    let mut checked = Vec::new();
    for id in &verifies {
        let commit = match before.finding(&pr, id).map(|f| &f.state) {
            Some(State::Claimed { commit }) => commit.clone(),
            Some(s) => return Err(rule(format!("verifies {id}, which is {}", s.word()))),
            None => {
                return Err(rule(format!(
                    "verifies {id}, which is not a finding of {pr}"
                )))
            }
        };
        if !git_yes(
            &repo,
            &["merge-base", "--is-ancestor", &commit, &first.head],
        )? {
            return Err(rule(format!(
                "{id}: the fix {} is not in 頭 {}",
                &commit[..9],
                &first.head[..9]
            )));
        }
        checked.push((id.clone(), commit));
    }
    let sub = Submit {
        pr: pr.clone(),
        seat,
        author,
        file: file
            .file_name()
            .map(|n| clean(&n.to_string_lossy()))
            .unwrap_or_default(),
        file_sha256: sha256_hex(text.as_bytes()),
        first,
        findings,
        verifies,
    };
    let out = write_locked(|l| {
        for (id, commit) in &checked {
            if !matches!(l.finding(&pr, id).map(|f| &f.state), Some(State::Claimed { commit: c }) if c == commit)
            {
                return Err(rule(format!(
                    "{id} changed while it was being checked; run again"
                )));
            }
        }
        let event_id = new_event_id(|e| l.has_event(e));
        let line = submit_line(l, &event_id, now_ms(), &sub).map_err(rule)?;
        let round = l.prs.get(&pr).map_or(0, |p| p.reviews.len()) + 1;
        let c = sub.first.counts;
        Ok((
            Some(line),
            format!(
                "R{round} {pr} {} H/M/L/N {}/{}/{}/{}{}",
                &sub.first.head[..9],
                c[0],
                c[1],
                c[2],
                c[3],
                if sub.verifies.is_empty() {
                    String::new()
                } else {
                    format!(" verified {}", sub.verifies.join(","))
                }
            ),
        ))
    })?;
    println!("{out}");
    Ok(0)
}

fn cmd_fix(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &[], &["pr", "ids", "commit", "seat", "repo"])?;
    let pr = a.pr()?;
    let seat = a.seat_of("seat", Some("AIB_SEAT_CODE"))?;
    let commit = a.head("commit")?;
    let ids: Vec<String> = a.many("ids").map(<[String]>::to_vec).unwrap_or_default();
    if ids.is_empty() {
        return Err(usage("--ids takes one or more finding ids"));
    }
    let repo = a.repo()?;
    if !commit_exists(&repo, &commit)? {
        return Err(rule(format!(
            "{} is not a commit in {}",
            &commit[..9],
            repo.display()
        )));
    }
    let out = write_locked(|l| {
        let event_id = new_event_id(|e| l.has_event(e));
        let line = fix_line(l, &event_id, now_ms(), &pr, &ids, &commit, &seat).map_err(rule)?;
        Ok((
            Some(line),
            format!(
                "claimed {} by {} (only claimed: the next review verifies)",
                ids.join(","),
                &commit[..9]
            ),
        ))
    })?;
    println!("{out}");
    Ok(0)
}

fn state_json(s: &State) -> Value {
    match s {
        State::Open => json!({"state": "open"}),
        State::Claimed { commit } => json!({"state": "claimed", "commit": commit}),
        State::Verified { seat, head } => json!({"state": "verified", "by": seat, "head": head}),
    }
}

fn cmd_status(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &["json"], &["pr", "json"])?;
    let only = if a.has("pr") { Some(a.pr()?) } else { None };
    let l = read_ledger(&review_dir()?.join("review.jsonl"))?;
    let prs: Vec<(&String, &aib_review::Pr)> = l
        .prs
        .iter()
        .filter(|(k, _)| only.as_ref().is_none_or(|o| o == *k))
        .collect();
    if a.has("json") {
        let list: Vec<Value> = prs
            .iter()
            .map(|(k, p)| {
                json!({
                    "pr": k,
                    "author": p.author,
                    "reviews": p.reviews.iter().map(|r| json!({
                        "round": r.round, "head": r.head, "seat": r.seat, "pass": r.pass,
                        "range": r.range, "file": r.file, "file_sha256": r.file_sha256,
                    })).collect::<Vec<_>>(),
                    "findings": p.findings.iter().map(|f| {
                        let mut v = state_json(&f.state);
                        v["id"] = f.id.clone().into();
                        v["level"] = f.level.letter().to_string().into();
                        v["text"] = f.text.clone().into();
                        v
                    }).collect::<Vec<_>>(),
                })
            })
            .collect();
        println!(
            "{}",
            json!({"complete": l.complete(), "errors": l.errors, "prs": list})
        );
    } else {
        for (k, p) in &prs {
            let r = p.reviews.last();
            println!(
                "{k} · 作者 {} · {} 輪 · 最新 R{} {} by {}",
                p.author,
                p.reviews.len(),
                r.map_or(0, |r| r.round),
                r.map_or("-", |r| &r.head[..9]),
                r.map_or("-", |r| r.seat.as_str())
            );
            for f in &p.findings {
                if f.level == Level::N && !matches!(f.state, State::Open) {
                    continue;
                }
                println!(
                    "  {} {} {}: {}",
                    f.id,
                    f.level.word(),
                    f.state.word(),
                    f.text
                );
            }
        }
        if prs.is_empty() {
            println!("沒有審查紀錄");
        }
    }
    report(&l.errors);
    Ok(if l.complete() { 0 } else { 2 })
}

fn cmd_gate(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &[], &["pr", "head", "receipt", "repo"])?;
    let pr = a.pr()?;
    let head = a.head("head")?;
    let l = read_ledger(&review_dir()?.join("review.jsonl"))?;
    if !l.complete() {
        report(&l.errors);
        println!("gate: unknown {pr} {} (ledger incomplete)", &head[..9]);
        return Ok(2);
    }
    let receipt = match a.one("receipt")? {
        None => None,
        Some(p) => Some(match fs::read(&p) {
            Ok(b) => String::from_utf8(b)
                .map_err(|_| "not UTF-8".to_owned())
                .and_then(|t| receipt_heads(&t)),
            Err(e) => Err(format!("read {p}: {e}")),
        }),
    };
    let repo = a.repo()?;
    let why = gate(&l, &pr, &head, receipt.as_ref(), |v, h| {
        git_yes(&repo, &["merge-base", "--is-ancestor", v, h]).map_err(|f| match f {
            Fail::Io(s) | Fail::Rule(s) | Fail::Usage(s) => s,
            Fail::Incomplete => "incomplete".into(),
        })
    })
    .map_err(Fail::Io)?;
    if why.is_empty() {
        println!(
            "gate: pass {pr} {}{}",
            &head[..9],
            if receipt.is_some() {
                " (review head = receipt head)"
            } else {
                ""
            }
        );
        return Ok(0);
    }
    println!("gate: blocked {pr} {}", &head[..9]);
    for w in &why {
        println!("  - {w}");
    }
    Ok(3)
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let r = match raw.first().map(String::as_str) {
        Some("--help" | "-h" | "help") => {
            print!("{USAGE}");
            Ok(0)
        }
        Some("submit") => cmd_submit(&raw[1..]),
        Some("fix") => cmd_fix(&raw[1..]),
        Some("status") => cmd_status(&raw[1..]),
        Some("gate") => cmd_gate(&raw[1..]),
        _ => Err(usage("a command: submit / fix / status / gate (--help)")),
    };
    match r {
        Ok(rc) => ExitCode::from(rc),
        Err(f) => {
            match &f {
                Fail::Usage(s) => eprintln!("aib-review: {s}\n\n{USAGE}"),
                Fail::Incomplete => {}
                Fail::Rule(s) => eprintln!("aib-review: refused: {s}"),
                Fail::Io(s) => eprintln!("aib-review: {s}"),
            }
            ExitCode::from(f.rc())
        }
    }
}
