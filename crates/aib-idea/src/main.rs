//! `aib-idea`: the suggestion ledger's command line (S76). See the crate docs in lib.rs.
//!
//! ```text
//! aib-idea add --seat <5> --title <t> --now <n> --suggest <s> --benefit <b> --cost <c>
//!              [--touches <class,...>] [--node <S76>]          # prints "<id> <verdict>"
//! aib-idea list [--status auto|needs_human|ok|no] [--unreviewed] [--seat X] [--since 24h] [--json]
//! aib-idea show <id>
//! aib-idea review --ok <id...> --by andy --quote "<Andy's words>"
//! aib-idea review --ok-all-unreviewed --seat X [--max 10] --by andy --quote "<Andy's words>"
//! aib-idea review --no <id> --by andy --quote "<Andy's words>" --why "<why>"
//! aib-idea footer [--seat X] [--max 10]
//! ```
//!
//! rc 0 done; 2 the ledger has lines that could not be read (listed on stderr, the command
//! still ran on the rest); 64 usage (nothing written).
//!
//! The ledger: `~/.ez2-aib/state/ideas/ideas.jsonl` (Windows `%USERPROFILE%\.ez2-aib\state\
//! ideas\ideas.jsonl`), or `AIB_IDEA_LEDGER`. Append-only; every write holds an exclusive
//! lock on `<ledger>.lock` (flock on Unix, LockFileEx on Windows, both through `File::lock`).
//! `AIB_IDEA_NOW` (unix seconds) and `AIB_IDEA_TZ_OFFSET_MIN` (default 480, Taipei) are for
//! tests and other cabins.

use aib_idea::{
    add_line, classify, footer, id_ok, ok_all_ids, parse_touches, replay, review_line, seat_ok,
    Idea, Ledger, Review, Status,
};
use std::collections::hash_map::RandomState;
use std::fs::{self, File, OpenOptions};
use std::hash::{BuildHasher, Hasher};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

/// A text field longer than this is refused (one ledger line stays readable).
const MAX_FIELD: usize = 2000;
const DEFAULT_MAX: usize = 10;

struct Usage(String);

fn usage(s: impl Into<String>) -> Usage {
    Usage(s.into())
}

fn home() -> Option<PathBuf> {
    #[cfg(windows)]
    let h = std::env::var_os("USERPROFILE");
    #[cfg(not(windows))]
    let h = std::env::var_os("HOME");
    h.filter(|v| !v.is_empty()).map(PathBuf::from)
}

fn ledger_path() -> Result<PathBuf, Usage> {
    if let Some(p) = std::env::var_os("AIB_IDEA_LEDGER").filter(|v| !v.is_empty()) {
        return Ok(PathBuf::from(p));
    }
    let h = home()
        .ok_or_else(|| usage("no home folder (HOME / USERPROFILE) and no AIB_IDEA_LEDGER"))?;
    Ok(h.join(".ez2-aib")
        .join("state")
        .join("ideas")
        .join("ideas.jsonl"))
}

fn now() -> i64 {
    if let Some(n) = std::env::var("AIB_IDEA_NOW")
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
    {
        return n;
    }
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
}

fn tz_offset_min() -> i32 {
    std::env::var("AIB_IDEA_TZ_OFFSET_MIN")
        .ok()
        .and_then(|v| v.parse::<i32>().ok())
        .filter(|m| (-720..=840).contains(m))
        .unwrap_or(480)
}

fn read_ledger(path: &Path) -> std::io::Result<Ledger> {
    match fs::read(path) {
        Ok(b) => Ok(replay(&String::from_utf8_lossy(&b))),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Ledger::default()),
        Err(e) => Err(e),
    }
}

/// Run `f` with the ledger read under the exclusive lock; `f` returns the lines to append.
fn with_lock<T>(
    path: &Path,
    f: impl FnOnce(&Ledger) -> Result<(Vec<String>, T), Usage>,
) -> Result<(Ledger, T), Failure> {
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        fs::create_dir_all(dir).map_err(Failure::io("mkdir"))?;
    }
    let mut lock_name = path.as_os_str().to_owned();
    lock_name.push(".lock");
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(PathBuf::from(lock_name))
        .map_err(Failure::io("lock open"))?;
    lock.lock().map_err(Failure::io("lock"))?;
    let ledger = read_ledger(path).map_err(Failure::io("read"))?;
    let (lines, out) = f(&ledger).map_err(Failure::Usage)?;
    if !lines.is_empty() {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(path)
            .map_err(Failure::io("open"))?;
        // a line cut short by a crash must not swallow the next one
        let mut text = String::new();
        let len = file.metadata().map_err(Failure::io("stat"))?.len();
        if len > 0 {
            let mut last = [0u8; 1];
            file.seek(SeekFrom::Start(len - 1))
                .and_then(|_| file.read_exact(&mut last))
                .map_err(Failure::io("read"))?;
            if last[0] != b'\n' {
                text.push('\n');
            }
        }
        for l in &lines {
            text.push_str(l);
            text.push('\n');
        }
        file.write_all(text.as_bytes())
            .and_then(|_| file.sync_data())
            .map_err(Failure::io("write"))?;
    }
    drop(lock);
    Ok((ledger, out))
}

enum Failure {
    Usage(Usage),
    Io(String),
}

impl Failure {
    fn io(what: &'static str) -> impl Fn(std::io::Error) -> Failure {
        move |e| Failure::Io(format!("{what}: {e}"))
    }
}

/// Flags of one command: `--name value`, `--flag`, and words before the first flag.
struct Args {
    words: Vec<String>,
    pairs: Vec<(String, Vec<String>)>,
}

fn parse_args(raw: &[String], bools: &[&str]) -> Args {
    let mut a = Args {
        words: Vec::new(),
        pairs: Vec::new(),
    };
    let mut i = 0;
    while i < raw.len() {
        let w = &raw[i];
        if let Some(name) = w.strip_prefix("--") {
            let mut vals = Vec::new();
            if !bools.contains(&name) {
                while i + 1 < raw.len() && !raw[i + 1].starts_with("--") {
                    i += 1;
                    vals.push(raw[i].clone());
                }
            }
            a.pairs.push((name.to_string(), vals));
        } else {
            a.words.push(w.clone());
        }
        i += 1;
    }
    a
}

impl Args {
    fn check(&self, known: &[&str]) -> Result<(), Usage> {
        for (k, _) in &self.pairs {
            if !known.contains(&k.as_str()) {
                return Err(usage(format!("unknown flag --{k}")));
            }
            if self.pairs.iter().filter(|(x, _)| x == k).count() > 1 {
                return Err(usage(format!("--{k} given twice")));
            }
        }
        Ok(())
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
    /// One value, non-empty after trimming, at most MAX_FIELD characters.
    fn one(&self, k: &str) -> Result<Option<String>, Usage> {
        match self.many(k) {
            None => Ok(None),
            Some([v]) if !v.trim().is_empty() && v.chars().count() <= MAX_FIELD => {
                Ok(Some(v.clone()))
            }
            Some([_]) => Err(usage(format!(
                "--{k}: empty or over {MAX_FIELD} characters"
            ))),
            Some(_) => Err(usage(format!("--{k} takes one value"))),
        }
    }
    fn need(&self, k: &str) -> Result<String, Usage> {
        self.one(k)?
            .ok_or_else(|| usage(format!("--{k} is required")))
    }
    fn seat(&self) -> Result<Option<String>, Usage> {
        match self.one("seat")? {
            Some(s) if !seat_ok(&s) => Err(usage(format!("--seat {s}: 5 lower-case hex"))),
            s => Ok(s),
        }
    }
    fn max(&self) -> Result<usize, Usage> {
        match self.one("max")? {
            None => Ok(DEFAULT_MAX),
            Some(m) => m
                .parse::<usize>()
                .ok()
                .filter(|n| (1..=200).contains(n))
                .ok_or_else(|| usage("--max: 1..200")),
        }
    }
}

fn new_id(l: &Ledger) -> String {
    loop {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        h.write_u128(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        let id = format!("i_{:08x}", h.finish() as u32);
        if l.get(&id).is_none() {
            return id;
        }
    }
}

fn since_secs(s: &str) -> Option<i64> {
    let unit = s.chars().last()?;
    let n: i64 = s[..s.len() - unit.len_utf8()]
        .parse()
        .ok()
        .filter(|n| *n > 0)?;
    let k = match unit {
        'm' => 60,
        'h' => 3600,
        'd' => 86_400,
        _ => return None,
    };
    n.checked_mul(k)
}

fn report_errors(l: &Ledger) -> u8 {
    for e in &l.errors {
        eprintln!("aib-idea: ledger {e}");
    }
    if l.errors.is_empty() {
        0
    } else {
        2
    }
}

fn cmd_add(raw: &[String], path: &Path) -> Result<u8, Failure> {
    let a = parse_args(raw, &[]);
    let known = [
        "seat", "title", "now", "suggest", "benefit", "cost", "touches", "node",
    ];
    a.check(&known).map_err(Failure::Usage)?;
    if !a.words.is_empty() {
        return Err(Failure::Usage(usage("add takes flags only")));
    }
    let seat = a
        .seat()
        .and_then(|s| s.ok_or_else(|| usage("--seat is required")))
        .map_err(Failure::Usage)?;
    let touches = match a.one("touches").map_err(Failure::Usage)? {
        None => None,
        Some(t) => Some(parse_touches(&t).map_err(|e| Failure::Usage(usage(e)))?),
    };
    let first = classify(touches.as_deref());
    let mut idea = Idea {
        id: String::new(),
        at: now(),
        seat,
        title: a.need("title").map_err(Failure::Usage)?,
        now: a.need("now").map_err(Failure::Usage)?,
        suggest: a.need("suggest").map_err(Failure::Usage)?,
        benefit: a.need("benefit").map_err(Failure::Usage)?,
        cost: a.need("cost").map_err(Failure::Usage)?,
        touches,
        first,
        node: a.one("node").map_err(Failure::Usage)?,
        reviews: Vec::new(),
    };
    let (l, id) = with_lock(path, |l| {
        idea.id = new_id(l);
        Ok((vec![add_line(&idea)], idea.id.clone()))
    })?;
    println!("{id} {}", first.code());
    Ok(report_errors(&l))
}

fn cmd_list(raw: &[String], path: &Path) -> Result<u8, Failure> {
    let a = parse_args(raw, &["unreviewed", "json"]);
    a.check(&["status", "unreviewed", "seat", "since", "json"])
        .map_err(Failure::Usage)?;
    let status = match a.one("status").map_err(Failure::Usage)? {
        None => None,
        Some(s) => Some(Status::parse(&s).ok_or_else(|| {
            Failure::Usage(usage(format!("--status {s}: auto|needs_human|ok|no")))
        })?),
    };
    let seat = a.seat().map_err(Failure::Usage)?;
    let since = match a.one("since").map_err(Failure::Usage)? {
        None => None,
        Some(s) => Some(
            since_secs(&s)
                .ok_or_else(|| Failure::Usage(usage(format!("--since {s}: like 30m, 24h, 7d"))))?,
        ),
    };
    let l = read_ledger(path).map_err(Failure::io("read"))?;
    let t = now();
    let picked: Vec<&Idea> = l
        .ideas
        .iter()
        .filter(|i| status.is_none_or(|s| i.status() == s))
        .filter(|i| !a.has("unreviewed") || i.unreviewed())
        .filter(|i| seat.as_deref().is_none_or(|s| i.seat == s))
        .filter(|i| since.is_none_or(|d| i.at >= t.saturating_sub(d)))
        .collect();
    if a.has("json") {
        let v = serde_json::json!({
            "schema": "aib-idea.list.v1",
            "ideas": picked.iter().map(|i| i.to_json()).collect::<Vec<_>>(),
            "errors": l.errors,
        });
        println!("{v}");
    } else {
        for i in &picked {
            let node = i
                .node
                .as_deref()
                .map(|s| format!("{s} "))
                .unwrap_or_default();
            println!(
                "{} {} {} {} {node}{}",
                i.id,
                i.status().code(),
                i.seat,
                aib_idea::clock(i.at, t, tz_offset_min()),
                i.title
            );
        }
    }
    Ok(report_errors(&l))
}

fn cmd_show(raw: &[String], path: &Path) -> Result<u8, Failure> {
    let a = parse_args(raw, &[]);
    a.check(&[]).map_err(Failure::Usage)?;
    let [id] = a.words.as_slice() else {
        return Err(Failure::Usage(usage("show <id>")));
    };
    let l = read_ledger(path).map_err(Failure::io("read"))?;
    let i = l
        .get(id)
        .ok_or_else(|| Failure::Usage(usage(format!("no idea {id}"))))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&i.to_json()).unwrap_or_default()
    );
    Ok(report_errors(&l))
}

fn cmd_review(raw: &[String], path: &Path) -> Result<u8, Failure> {
    let a = parse_args(raw, &["ok-all-unreviewed"]);
    a.check(&[
        "ok",
        "no",
        "ok-all-unreviewed",
        "seat",
        "max",
        "by",
        "quote",
        "why",
    ])
    .map_err(Failure::Usage)?;
    let picks = [a.has("ok"), a.has("no"), a.has("ok-all-unreviewed")]
        .iter()
        .filter(|x| **x)
        .count();
    if picks != 1 || !a.words.is_empty() {
        return Err(Failure::Usage(usage(
            "review takes one of --ok <id...> | --no <id> | --ok-all-unreviewed",
        )));
    }
    let by = a.need("by").map_err(Failure::Usage)?;
    if by != "andy" {
        return Err(Failure::Usage(usage("--by andy: only Andy reviews")));
    }
    let quote = a.need("quote").map_err(Failure::Usage)?;
    let why = a.one("why").map_err(Failure::Usage)?;
    let ok = !a.has("no");
    if !ok && why.is_none() {
        return Err(Failure::Usage(usage("--no needs --why")));
    }
    let given: Vec<String> = a
        .many("ok")
        .or(a.many("no"))
        .map(<[String]>::to_vec)
        .unwrap_or_default();
    if a.has("no") && given.len() != 1 {
        return Err(Failure::Usage(usage("--no takes one id")));
    }
    if (a.has("ok") || a.has("no")) && given.is_empty() {
        return Err(Failure::Usage(usage("--ok needs at least one id")));
    }
    let seat = a.seat().map_err(Failure::Usage)?;
    let max = a.max().map_err(Failure::Usage)?;
    if a.has("ok-all-unreviewed") && seat.is_none() {
        return Err(Failure::Usage(usage(
            "--ok-all-unreviewed needs --seat (the seat whose footer Andy answered)",
        )));
    }
    let r = Review {
        ok,
        by,
        quote,
        why,
        at: now(),
    };
    let (l, ids) = with_lock(path, |l| {
        let ids = if a.has("ok-all-unreviewed") {
            ok_all_ids(l, seat.as_deref().unwrap_or_default(), max)
        } else {
            for id in &given {
                if !id_ok(id) || l.get(id).is_none() {
                    return Err(usage(format!("no idea {id}")));
                }
            }
            given.clone()
        };
        Ok((ids.iter().map(|id| review_line(id, &r)).collect(), ids))
    })?;
    if ids.is_empty() {
        println!("nothing to agree");
    }
    for id in &ids {
        println!("{id} {}", if ok { "ok" } else { "no" });
    }
    Ok(report_errors(&l))
}

fn cmd_footer(raw: &[String], path: &Path) -> Result<u8, Failure> {
    let a = parse_args(raw, &[]);
    a.check(&["seat", "max"]).map_err(Failure::Usage)?;
    let seat = a.seat().map_err(Failure::Usage)?;
    let max = a.max().map_err(Failure::Usage)?;
    let l = read_ledger(path).map_err(Failure::io("read"))?;
    // the same list ok-all-unreviewed takes (footer_items): what Andy saw is what one 「同意」 covers
    print!(
        "{}",
        footer(&l, seat.as_deref(), max, now(), tz_offset_min())
    );
    Ok(report_errors(&l))
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let Some((cmd, rest)) = raw.split_first() else {
        eprintln!("usage: aib-idea add|list|show|review|footer …");
        return ExitCode::from(64);
    };
    let path = match ledger_path() {
        Ok(p) => p,
        Err(Usage(e)) => {
            eprintln!("aib-idea: {e}");
            return ExitCode::from(64);
        }
    };
    let r = match cmd.as_str() {
        "add" => cmd_add(rest, &path),
        "list" => cmd_list(rest, &path),
        "show" => cmd_show(rest, &path),
        "review" => cmd_review(rest, &path),
        "footer" => cmd_footer(rest, &path),
        _ => Err(Failure::Usage(usage(format!("unknown command {cmd}")))),
    };
    match r {
        Ok(rc) => ExitCode::from(rc),
        Err(Failure::Usage(Usage(e))) => {
            eprintln!("aib-idea: {e}");
            ExitCode::from(64)
        }
        Err(Failure::Io(e)) => {
            eprintln!("aib-idea: {e}");
            ExitCode::from(1)
        }
    }
}
