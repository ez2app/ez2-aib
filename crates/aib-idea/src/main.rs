//! `aib-idea`: the suggestion ledger's command line (S76-1, DEV-S76 r2 §3). See lib.rs.
//!
//! ```text
//! aib-idea add --seat <5> --title <t> --now <n> --suggest <s> --benefit <b>
//!              --cost <cost, what breaks and how to undo it> --touches <none | class,...>
//!              --grade <S|A|B|C> --axis <stable|fast|simple|unseen>
//!              [--node S76] [--source <key>]          # prints "<i_xxxxxxxx> <auto|needs_human>"
//! aib-idea list [--unreviewed] [--seat X | --all] [--json]
//! aib-idea show <id>
//! aib-idea review --ok <id> [<id>...] --by andy --quote "<Andy's words + where>" [--seat X]
//! aib-idea review --no <id> [<id>...] --by andy --quote "<…>" --why "<why>" [--seat X]
//! aib-idea footer --seat X
//! ```
//!
//! rc 0 done · 2 the ledger is incomplete (a bad line, an unknown version, a broken rule, a
//! last line cut short): reads print what they can and say so, every write is refused ·
//! 3 refused by a rule (an id unknown or ambiguous, already decided, an ok for needs_human;
//! nothing written) · 4 the lock was not taken in 2 s, or a write / sync failed (never a
//! success) · 64 usage.
//!
//! Ledger `<root>/state/ideas/ideas.jsonl`, lock `<root>/state/ideas/ideas.lock` (never
//! removed), root = `EZ2_AIB_HOME` or `~/.ez2-aib` (`~` = USERPROFILE on Windows, HOME
//! elsewhere). The seat defaults to `AIB_SEAT_CODE`. `AIB_IDEA_NOW_MS` and
//! `AIB_IDEA_TZ_OFFSET_MIN` (default 480, Taipei) are for tests and other cabins.

use aib_idea::{
    add_line, check_batch, classify, clean, field_ok, footer, parse_touches, replay, review_line,
    seat_ok, short, uuid_from, Idea, Ledger, Review, MAX_LINE_BYTES,
};
use std::collections::hash_map::RandomState;
use std::fs::{self, File, OpenOptions, TryLockError};
use std::hash::{BuildHasher, Hasher};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// `aib-idea add --help` (DEV-S76 §12, Andy 11-14 11:01): what each touches word covers.
const ADD_HELP: &str = "\
aib-idea add --seat <5 碼> --title <一句> --now <現況> --suggest <建議> --benefit <好處>
             --cost <代價,含失敗影響與怎麼回復> --touches <none | 類別,...>
             --grade <S|A|B|C> --axis <stable|fast|simple|unseen> [--node S76] [--source <鍵>]

--grade(必填,§14):S = 主軸成敗或擋大錯;A = 明顯省 Andy / 全艦時間;B = 有感改善;C = 小改善。
--axis(必填,§14):stable 穩定 / fast 快速 / simple 簡化 / unseen 沒想到,選一個主方向。
清單與 footer 每條開頭寫「等級 | 方向 | 自動同意/需人同意」,S → A → B → C 排,同級照加入順序。

--touches:none 只能單獨用;其他是紅線類,任一個 ⇒ needs_human(不經這支核准)。
  money sign prod delete data external customer_text seat machine security rule release

none 要五條全成立:影響範圍明確;可回復且不需救回遺失資料;沒有對外或正式環境副作用;
不改授權 / 治理 / 安全規則;有具體驗證方式。分類不確定 ⇒ 不填 none。

例子(DEV-S76 §12):
- 算 none:只改自家 crate / 自家腳本 / 自家測試與刀、審查流程內的格式小改
  (收據多印一欄、刀清單加平台欄、監看包 bash -c、審查稿附增量)。前提仍是五條件。
- 算 rule:改全艦規則文字、同意判定本身、審查門檻、合併放行條件、任何「可以跳過某個檢查」
  的做法(免實機收據、只比 tree 碼不重跑測試)。
- 算 machine:改機器設定、安裝位置、排程、租約政策、主機上的環境變數或快取位置
  (CARGO_TARGET_DIR 指到工作區外、挑機規則)。只改 receipt-run.sh 參數不算 machine
  (算 none,但改它要走 ez2-skills PR + 審)。
- 算 external:會增加對外 API 呼叫量或改對外送出內容。

auto 只省「等 Andy 同意這項建議」;測試、審查、PR、收據、合併隊一條都不省。
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

fn io(what: &'static str) -> impl Fn(std::io::Error) -> Fail {
    move |e| Fail::Io(format!("{what}: {e}"))
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

fn ideas_dir() -> Result<PathBuf, Fail> {
    Ok(root()?.join("state").join("ideas"))
}

fn now_ms() -> i64 {
    if let Some(n) = nonempty("AIB_IDEA_NOW_MS").and_then(|v| v.parse::<i64>().ok()) {
        return n;
    }
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

fn tz_offset_min() -> i32 {
    nonempty("AIB_IDEA_TZ_OFFSET_MIN")
        .and_then(|v| v.parse::<i32>().ok())
        .filter(|m| (-720..=840).contains(m))
        .unwrap_or(480)
}

static SALT: AtomicU64 = AtomicU64::new(0);

/// 128 bits from the std randomly seeded hasher, the process id, the time and a counter.
/// Uniqueness is checked against the ledger under the lock, not assumed.
fn random_u128() -> u128 {
    let mut out = 0u128;
    for _ in 0..2 {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        h.write_u128(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        h.write_u64(SALT.fetch_add(1, Ordering::Relaxed));
        out = out << 64 | u128::from(h.finish());
    }
    out
}

fn new_uuid(taken: impl Fn(&str) -> bool) -> String {
    loop {
        let u = uuid_from(random_u128());
        if !taken(&u) {
            return u;
        }
    }
}

fn read_ledger(path: &Path) -> Result<Ledger, Fail> {
    match fs::read(path) {
        Ok(b) => match String::from_utf8(b) {
            Ok(t) => Ok(replay(&t)),
            Err(_) => Ok(Ledger {
                errors: vec!["not UTF-8".into()],
                ..Ledger::default()
            }),
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Ledger::default()),
        Err(e) => Err(Fail::Io(format!("read: {e}"))),
    }
}

/// ADR 0005 named exception: a short wait inside a one-shot command, bounded by LOCK_WAIT.
fn pause() {
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(Duration::from_millis(10));
}

/// The exclusive lock on the fixed `ideas.lock` (`File::try_lock`: flock on Unix, LockFileEx
/// on Windows), waited for at most LOCK_WAIT. Released when the file is dropped.
fn take_lock(dir: &Path) -> Result<File, Fail> {
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join("ideas.lock"))
        .map_err(io("lock open"))?;
    let until = Instant::now() + LOCK_WAIT;
    loop {
        match f.try_lock() {
            Ok(()) => return Ok(f),
            Err(TryLockError::WouldBlock) => {}
            Err(TryLockError::Error(e)) => return Err(Fail::Io(format!("lock: {e}"))),
        }
        if Instant::now() >= until {
            return Err(Fail::Io("lock: not taken in 2 s".into()));
        }
        pause();
    }
}

/// Lock → replay → `decide` → append one line → sync_all → unlock. An incomplete ledger
/// refuses every write; `decide` returns the line to write (or none) and what to print.
fn write_locked(
    decide: impl FnOnce(&Ledger) -> Result<(Option<String>, String), Fail>,
) -> Result<String, Fail> {
    let dir = ideas_dir()?;
    fs::create_dir_all(&dir).map_err(io("mkdir"))?;
    let lock = take_lock(&dir)?;
    let path = dir.join("ideas.jsonl");
    let ledger = read_ledger(&path)?;
    if !ledger.complete() {
        report(&ledger);
        return Err(Fail::Incomplete);
    }
    let (line, out) = decide(&ledger)?;
    if let Some(line) = line {
        if line.len() + 1 > MAX_LINE_BYTES {
            return Err(usage(format!("the line is over {MAX_LINE_BYTES} bytes")));
        }
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(io("open"))?;
        f.write_all(format!("{line}\n").as_bytes())
            .map_err(io("write"))?;
        f.sync_all().map_err(io("sync"))?;
    }
    drop(lock);
    Ok(out)
}

fn report(l: &Ledger) {
    for e in &l.errors {
        eprintln!("aib-idea: ledger {e}");
    }
    if !l.complete() {
        eprintln!(
            "aib-idea: the ledger is incomplete (不完整): writes are refused until it is fixed"
        );
    }
}

/// `--name value…`, bare `--flag`, and words before the first flag.
struct Args {
    words: Vec<String>,
    pairs: Vec<(String, Vec<String>)>,
}

impl Args {
    fn parse(raw: &[String], bools: &[&str], known: &[&str]) -> Result<Args, Fail> {
        let mut a = Args {
            words: Vec::new(),
            pairs: Vec::new(),
        };
        let mut i = 0;
        while i < raw.len() {
            if let Some(name) = raw[i].strip_prefix("--") {
                if !known.contains(&name) {
                    return Err(usage(format!("unknown flag --{name}")));
                }
                if a.pairs.iter().any(|(k, _)| k == name) {
                    return Err(usage(format!("--{name} given twice")));
                }
                let mut vals = Vec::new();
                if !bools.contains(&name) {
                    while i + 1 < raw.len() && !raw[i + 1].starts_with("--") {
                        i += 1;
                        vals.push(raw[i].clone());
                    }
                }
                a.pairs.push((name.to_string(), vals));
            } else if a.pairs.is_empty() {
                a.words.push(raw[i].clone());
            } else {
                return Err(usage(format!("unexpected word {}", raw[i])));
            }
            i += 1;
        }
        Ok(a)
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
    /// One text value: non-empty, at most 500 characters.
    fn one(&self, k: &str) -> Result<Option<String>, Fail> {
        match self.many(k) {
            None => Ok(None),
            Some([v]) if field_ok(v) => Ok(Some(v.clone())),
            Some([_]) => Err(usage(format!("--{k}: empty or over 500 characters"))),
            Some(_) => Err(usage(format!("--{k} takes one value"))),
        }
    }
    fn need(&self, k: &str) -> Result<String, Fail> {
        self.one(k)?
            .ok_or_else(|| usage(format!("--{k} is required")))
    }
    /// --seat, else AIB_SEAT_CODE; 5 lower-case hex.
    fn seat(&self) -> Result<Option<String>, Fail> {
        let s = match self.one("seat")? {
            Some(s) => Some(s),
            None => nonempty("AIB_SEAT_CODE"),
        };
        match s {
            Some(s) if !seat_ok(&s) => Err(usage(format!("seat {s}: 5 lower-case hex"))),
            s => Ok(s),
        }
    }
    fn need_seat(&self) -> Result<String, Fail> {
        self.seat()?
            .ok_or_else(|| usage("--seat is required (or AIB_SEAT_CODE)"))
    }
}

fn cmd_add(raw: &[String]) -> Result<u8, Fail> {
    if raw.iter().any(|w| w == "--help" || w == "-h") {
        print!("{ADD_HELP}");
        return Ok(0);
    }
    let a = Args::parse(
        raw,
        &[],
        &[
            "seat", "title", "now", "suggest", "benefit", "cost", "touches", "grade", "axis",
            "node", "source",
        ],
    )?;
    if !a.words.is_empty() {
        return Err(usage("add takes flags only"));
    }
    let touches = parse_touches(&a.need("touches")?).map_err(Fail::Usage)?;
    let grade = a.need("grade")?;
    if !aib_idea::grade_ok(&grade) {
        return Err(usage("--grade is S, A, B or C"));
    }
    let axis = a.need("axis")?;
    if !aib_idea::axis_ok(&axis) {
        return Err(usage("--axis is stable, fast, simple or unseen"));
    }
    let mut idea = Idea {
        id: String::new(),
        at_ms: now_ms(),
        seat: a.need_seat()?,
        title: a.need("title")?,
        now: a.need("now")?,
        suggest: a.need("suggest")?,
        benefit: a.need("benefit")?,
        cost: a.need("cost")?,
        class: classify(&touches),
        touches,
        grade,
        axis,
        node: a.one("node")?,
        source: a.one("source")?,
        review: None,
    };
    let out = write_locked(|l| {
        // the same --source again: the first one stands, nothing is added
        if let Some(s) = idea.source.as_deref() {
            if let Some(old) = l.by_source(s) {
                return Ok((None, format!("{} {}", short(&old.id), old.class.code())));
            }
        }
        idea.id = new_uuid(|u| l.get(u).is_some());
        let event = new_uuid(|u| l.event_ids.iter().any(|e| e == u));
        let out = format!("{} {}", short(&idea.id), idea.class.code());
        Ok((Some(add_line(&event, &idea)), out))
    })?;
    println!("{out}");
    Ok(0)
}

fn cmd_list(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(
        raw,
        &["unreviewed", "all", "json"],
        &["unreviewed", "seat", "all", "json"],
    )?;
    if !a.words.is_empty() {
        return Err(usage("list takes flags only"));
    }
    let seat = if a.has("all") {
        if a.has("seat") {
            return Err(usage("--seat or --all, not both"));
        }
        None
    } else {
        Some(a.need_seat()?)
    };
    let l = read_ledger(&ideas_dir()?.join("ideas.jsonl"))?;
    let mut picked: Vec<&Idea> = l
        .ideas
        .iter()
        .filter(|i| !a.has("unreviewed") || i.review.is_none())
        .filter(|i| seat.as_deref().is_none_or(|s| i.seat == s))
        .collect();
    aib_idea::by_grade(&mut picked);
    if a.has("json") {
        let v = serde_json::json!({
            "schema": "aib-idea.list.v1",
            "complete": l.complete(),
            "ideas": picked.iter().map(|i| i.to_json()).collect::<Vec<_>>(),
            "errors": l.errors,
        });
        println!("{v}");
    } else {
        if !l.complete() {
            println!(
                "(帳本不完整 — incomplete: {} line(s) unread)",
                l.errors.len()
            );
        }
        let (now, tz) = (now_ms(), tz_offset_min());
        for i in picked {
            let node = i
                .node
                .as_deref()
                .map(|n| format!("{} ", clean(n)))
                .unwrap_or_default();
            println!(
                "{} | {} {} {} {} {node}{} [{}]",
                i.label(),
                short(&i.id),
                i.status(),
                i.seat,
                aib_idea::clock(i.at_ms, now, tz),
                clean(&i.title),
                i.touches.join(",")
            );
        }
    }
    report(&l);
    Ok(if l.complete() { 0 } else { 2 })
}

fn resolve_one<'a>(l: &'a Ledger, word: &str) -> Result<&'a Idea, Fail> {
    match l.resolve(word).as_slice() {
        [i] => Ok(*i),
        [] => Err(Fail::Rule(format!("no idea {word}"))),
        _ => Err(Fail::Rule(format!(
            "{word} matches more than one idea; give more of the id"
        ))),
    }
}

fn cmd_show(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &[], &[])?;
    let [word] = a.words.as_slice() else {
        return Err(usage("show <id>"));
    };
    let l = read_ledger(&ideas_dir()?.join("ideas.jsonl"))?;
    let i = resolve_one(&l, word)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&i.to_json()).unwrap_or_default()
    );
    report(&l);
    Ok(if l.complete() { 0 } else { 2 })
}

fn cmd_review(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &[], &["ok", "no", "by", "quote", "why", "seat"])?;
    if !a.words.is_empty() || a.has("ok") == a.has("no") {
        return Err(usage("review --ok <id...> | --no <id...>"));
    }
    let ok = a.has("ok");
    let words: Vec<String> = a
        .many(if ok { "ok" } else { "no" })
        .map(<[String]>::to_vec)
        .unwrap_or_default();
    if words.is_empty() {
        return Err(usage("give at least one id"));
    }
    if a.need("by")? != "andy" {
        return Err(usage("--by andy: only Andy reviews today"));
    }
    let why = a.one("why")?;
    if !ok && why.is_none() {
        return Err(usage("--no needs --why"));
    }
    if ok && why.is_some() {
        return Err(usage("--why goes with --no"));
    }
    let r = Review {
        ok,
        by: "andy".into(),
        quote: a.need("quote")?,
        why,
        at_ms: now_ms(),
        seat: a.need_seat()?,
    };
    let out = write_locked(|l| {
        let mut ids = Vec::new();
        for w in &words {
            ids.push(resolve_one(l, w)?.id.clone());
        }
        check_batch(l, &ids, ok).map_err(Fail::Rule)?;
        let event = new_uuid(|u| l.event_ids.iter().any(|e| e == u));
        let verdict = if ok { "ok" } else { "no" };
        let out = ids
            .iter()
            .map(|id| format!("{} {verdict}", short(id)))
            .collect::<Vec<_>>()
            .join("\n");
        Ok((Some(review_line(&event, &ids, &r)), out))
    })?;
    println!("{out}");
    Ok(0)
}

fn cmd_footer(raw: &[String]) -> Result<u8, Fail> {
    let a = Args::parse(raw, &[], &["seat"])?;
    if !a.words.is_empty() {
        return Err(usage("footer --seat X"));
    }
    let seat = a.need_seat()?;
    let l = read_ledger(&ideas_dir()?.join("ideas.jsonl"))?;
    print!("{}", footer(&l, &seat, now_ms(), tz_offset_min()));
    report(&l);
    Ok(if l.complete() { 0 } else { 2 })
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let r = match raw.split_first() {
        Some((c, rest)) if c == "add" => cmd_add(rest),
        Some((c, rest)) if c == "list" => cmd_list(rest),
        Some((c, rest)) if c == "show" => cmd_show(rest),
        Some((c, rest)) if c == "review" => cmd_review(rest),
        Some((c, rest)) if c == "footer" => cmd_footer(rest),
        _ => Err(usage("aib-idea add | list | show | review | footer")),
    };
    match r {
        Ok(rc) => ExitCode::from(rc),
        Err(f) => {
            match &f {
                Fail::Usage(e) => eprintln!("aib-idea: usage: {e}"),
                Fail::Incomplete => {}
                Fail::Rule(e) => eprintln!("aib-idea: refused: {e} (nothing written)"),
                Fail::Io(e) => eprintln!("aib-idea: {e} (not written)"),
            }
            ExitCode::from(f.rc())
        }
    }
}
