//! The review gate's ledger (S78 r-1, DEV-S78 §2–§5 and §7).
//!
//! A seat records each review file it wrote (`submit`), the author records the commit that
//! fixed a finding (`fix`, which only reaches `claimed`), and a later review by a seat that is
//! not the author confirms it (`submit --verifies`, which reaches `verified`). `gate` reads
//! the replayed state and says whether the PR head may go on: rc 0 pass, 3 blocked, 2 the
//! ledger is incomplete.
//!
//! This is a discipline check between seats, not a security line (DEV-S78 §1): seat codes
//! and the local ledger can be edited by anyone on this account.
//!
//! The ledger is append-only JSONL through aib-ledger (one lock, whole-line append, replay in
//! file order, an incomplete ledger refuses every write). Every line is a v 2 envelope with
//! aggregate `review_ledger` / `reviews`. Replay re-checks every rule, so a hand-edited line
//! that breaks one makes the ledger incomplete instead of being trusted.
//!
//! Not in this slice (DEV-S78 r-1 second part): dismiss, reclassify, waive, the `carried`
//! row for a merge of main with no hand resolution, and evidence confidence per finding.

use aib_ledger::envelope::{self, Aggregate};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};

/// The one aggregate of this ledger: the whole review ledger is one stream.
pub const AGG: Aggregate<'static> = Aggregate {
    kind: "review_ledger",
    id: "reviews",
};

pub const MAX_LINE_BYTES: usize = 64 * 1024;
/// A finding is kept as its first line, cut to this many characters.
pub const MAX_FINDING_CHARS: usize = 300;
/// A review file is at most this big (a review is text a person reads).
pub const MAX_FILE_BYTES: usize = 512 * 1024;

pub fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// A full commit id: 40 lower-case hex.
pub fn hex40_ok(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn hex64_ok(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The key a PR is filed under: its number, or a branch name.
pub fn pr_ok(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 200
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'/' | b'-'))
}

pub fn sha256_hex(b: &[u8]) -> String {
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

/// Control characters removed, for printing on one line.
pub fn clean(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).collect()
}

/// DEV-S78 §2. N (備註) gates like L.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    H,
    M,
    L,
    N,
}

impl Level {
    pub const ALL: [Level; 4] = [Level::H, Level::M, Level::L, Level::N];

    pub fn letter(self) -> char {
        match self {
            Level::H => 'H',
            Level::M => 'M',
            Level::L => 'L',
            Level::N => 'N',
        }
    }

    pub fn from_letter(c: char) -> Option<Level> {
        Level::ALL.into_iter().find(|l| l.letter() == c)
    }

    fn index(self) -> usize {
        self as usize
    }

    /// The word on the board.
    pub fn word(self) -> &'static str {
        match self {
            Level::H => "嚴重",
            Level::M => "中度",
            Level::L => "輕度",
            Level::N => "備註",
        }
    }

    /// Must be verified before the gate passes (DEV-S78 §4; dismiss / waive come later).
    pub fn blocks(self) -> bool {
        matches!(self, Level::H | Level::M)
    }
}

/// The fixed first line of a review file (RULES §20 / memory 11-09):
/// `結論: 通過 頭: <40 hex> H/M/L/N: a/b/c/d 範圍: <range>`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FirstLine {
    pub pass: bool,
    pub head: String,
    pub counts: [usize; 4],
    pub range: String,
}

pub fn parse_first_line(line: &str) -> Result<FirstLine, String> {
    let line = line.trim_end_matches('\r');
    let rest = line
        .strip_prefix("結論: ")
        .ok_or("the first line does not start with 「結論: 」")?;
    let (verdict, rest) = rest
        .split_once(" 頭: ")
        .ok_or("the first line has no 「 頭: 」")?;
    let verdict = verdict.trim();
    // the whole word, as aib-core firstline does: 「通過(附 2 Medium 要改)」 is not 通過;
    // 要改 = 不通過 (Andy 11-14; 1ced7 r-1 L1)
    let pass = match verdict {
        "通過" => true,
        "不通過" | "要改" => false,
        _ => return Err("結論 is not one of 通過 / 不通過 / 要改".into()),
    };
    let head = rest.get(..40).unwrap_or_default();
    if !hex40_ok(head) {
        return Err("頭 is not 40 lower-case hex".into());
    }
    let rest = rest[40..]
        .strip_prefix(" H/M/L/N: ")
        .ok_or("no 「 H/M/L/N: 」 right after the head")?;
    let (nums, range) = rest
        .split_once(" 範圍: ")
        .ok_or("the first line has no 「 範圍: 」")?;
    let parts: Vec<&str> = nums.split('/').collect();
    let mut counts = [0usize; 4];
    if parts.len() != 4 {
        return Err("H/M/L/N is not four numbers".into());
    }
    for (c, p) in counts.iter_mut().zip(&parts) {
        if p.is_empty() || p.len() > 3 || !p.bytes().all(|b| b.is_ascii_digit()) {
            return Err("H/M/L/N is not four numbers".into());
        }
        *c = p.parse().map_err(|_| "H/M/L/N is not four numbers")?;
    }
    if range.trim().is_empty() {
        return Err("範圍 is empty".into());
    }
    Ok(FirstLine {
        pass,
        head: head.to_owned(),
        counts,
        range: range.trim().to_owned(),
    })
}

/// One new finding of a review file: a line `- [M1] …` (outside fenced code blocks). Lines
/// that only talk about an earlier round's finding (`- **M1 已修**`) are not new findings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub level: Level,
    pub n: usize,
    pub text: String,
}

pub fn parse_findings(body: &str) -> Result<Vec<Finding>, String> {
    let mut out: Vec<Finding> = Vec::new();
    let mut fenced = false;
    for line in body.lines().skip(1) {
        let t = line.trim_start();
        if t.starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if fenced {
            continue;
        }
        let Some(rest) = t.strip_prefix("- [") else {
            continue;
        };
        let mut chars = rest.chars();
        let Some(level) = chars.next().and_then(Level::from_letter) else {
            continue;
        };
        let rest = chars.as_str();
        let Some((num, text)) = rest.split_once(']') else {
            continue;
        };
        if num.is_empty() || num.len() > 3 || !num.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let n: usize = num.parse().map_err(|_| "bad finding number")?;
        if out.iter().any(|f| f.level == level && f.n == n) {
            return Err(format!("[{}{n}] is written twice", level.letter()));
        }
        let text: String = clean(text.trim()).chars().take(MAX_FINDING_CHARS).collect();
        if text.is_empty() {
            return Err(format!("[{}{n}] has no text", level.letter()));
        }
        out.push(Finding { level, n, text });
    }
    if fenced {
        return Err("a code block is not closed".into());
    }
    Ok(out)
}

/// The first line's numbers must equal the findings, numbered 1..n per level (DEV-S78 §2).
pub fn check_counts(first: &FirstLine, findings: &[Finding]) -> Result<(), String> {
    let mut have = [0usize; 4];
    for f in findings {
        have[f.level.index()] += 1;
    }
    if have != first.counts {
        let show = |c: [usize; 4]| format!("{}/{}/{}/{}", c[0], c[1], c[2], c[3]);
        return Err(format!(
            "H/M/L/N says {} but the findings are {}",
            show(first.counts),
            show(have)
        ));
    }
    for l in Level::ALL {
        let mut ns: Vec<usize> = findings
            .iter()
            .filter(|f| f.level == l)
            .map(|f| f.n)
            .collect();
        ns.sort_unstable();
        if ns.iter().enumerate().any(|(i, n)| *n != i + 1) {
            return Err(format!("the {} findings are not numbered 1..n", l.letter()));
        }
    }
    if first.pass && first.counts[Level::H.index()] > 0 {
        return Err("結論 通過 with a 嚴重 finding".into());
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Open,
    /// The author says this commit fixed it (DEV-S78 §3: only claimed).
    Claimed {
        commit: String,
    },
    /// A later review on `head` by `seat` (not the author) confirmed the fix.
    Verified {
        seat: String,
        head: String,
    },
}

impl State {
    pub fn word(&self) -> &'static str {
        match self {
            State::Open => "open",
            State::Claimed { .. } => "claimed",
            State::Verified { .. } => "verified",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FindingRec {
    /// `R<round>-<level><n>`
    pub id: String,
    pub level: Level,
    pub text: String,
    pub round: u32,
    pub state: State,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewRec {
    pub round: u32,
    pub head: String,
    pub seat: String,
    pub pass: bool,
    pub range: String,
    pub file: String,
    pub file_sha256: String,
    pub at_ms: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pr {
    /// The seat that wrote the PR. Every review must be by another seat.
    pub author: String,
    pub reviews: Vec<ReviewRec>,
    pub findings: Vec<FindingRec>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Ledger {
    pub prs: BTreeMap<String, Pr>,
    pub errors: Vec<String>,
    event_ids: HashSet<String>,
    events: u64,
}

fn s<'a>(o: &'a Map<String, Value>, k: &str) -> Result<&'a str, String> {
    o.get(k)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("bad {k}"))
}

fn strs(o: &Map<String, Value>, k: &str) -> Result<Vec<String>, String> {
    o.get(k)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("bad {k}"))?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_owned)
                .ok_or_else(|| format!("bad {k}"))
        })
        .collect()
}

impl Ledger {
    pub fn complete(&self) -> bool {
        self.errors.is_empty()
    }

    /// The sequence the next line must carry.
    pub fn next_sequence(&self) -> u64 {
        self.events + 1
    }

    pub fn has_event(&self, id: &str) -> bool {
        self.event_ids.contains(id)
    }

    pub fn finding(&self, pr: &str, id: &str) -> Option<&FindingRec> {
        self.prs.get(pr)?.findings.iter().find(|f| f.id == id)
    }

    /// Apply one line, re-checking every rule. On error nothing changes.
    pub fn apply(&mut self, o: &Map<String, Value>) -> Result<(), String> {
        let head = envelope::read(o, AGG, self.next_sequence())?;
        if head.v != envelope::V_WRITE {
            // this ledger was born after S97: it has no v 1 lines
            return Err("not a v 2 line".into());
        }
        if self.event_ids.contains(&head.event_id) {
            return Err("event_id twice".into());
        }
        match head.ev.as_str() {
            "submit" => self.apply_submit(o, head.at_ms)?,
            "fix" => self.apply_fix(o)?,
            _ => return Err("unknown ev".into()),
        }
        self.event_ids.insert(head.event_id);
        self.events += 1;
        Ok(())
    }

    fn apply_submit(&mut self, o: &Map<String, Value>, at_ms: i64) -> Result<(), String> {
        let pr = s(o, "pr")?;
        let seat = s(o, "seat")?;
        let author = s(o, "author")?;
        let head = s(o, "head")?;
        let sha = s(o, "file_sha256")?;
        let range = s(o, "range")?;
        let file = s(o, "file")?;
        let pass = o.get("pass").and_then(Value::as_bool).ok_or("bad pass")?;
        if !pr_ok(pr) || !seat_ok(seat) || !seat_ok(author) || !hex40_ok(head) || !hex64_ok(sha) {
            return Err("bad pr / seat / author / head / file_sha256".into());
        }
        if seat == author {
            return Err("the author cannot review its own PR".into());
        }
        let round = o.get("round").and_then(Value::as_u64).ok_or("bad round")?;
        let entry = self.prs.get(pr);
        if let Some(p) = entry {
            if p.author != author {
                return Err(format!("the author of {pr} is {}", p.author));
            }
            if p.reviews.iter().any(|r| r.file_sha256 == sha) {
                return Err("this review file is already in the ledger".into());
            }
        }
        let want = entry.map_or(0, |p| p.reviews.len()) + 1;
        if round != want as u64 {
            return Err(format!("round is not {want}"));
        }
        let round = want as u32;
        let mut findings = Vec::new();
        let mut counts = [0usize; 4];
        for f in o
            .get("findings")
            .and_then(Value::as_array)
            .ok_or("bad findings")?
        {
            let f = f.as_object().ok_or("bad findings")?;
            let id = s(f, "id")?;
            let text = s(f, "text")?;
            let level = id
                .strip_prefix(&format!("R{round}-"))
                .and_then(|r| r.chars().next())
                .and_then(Level::from_letter)
                .ok_or_else(|| format!("finding {id} is not R{round}-<H|M|L|N><n>"))?;
            counts[level.index()] += 1;
            if id != format!("R{round}-{}{}", level.letter(), counts[level.index()]) {
                return Err(format!("finding {id} is out of order"));
            }
            if text.is_empty() || text.chars().count() > MAX_FINDING_CHARS {
                return Err(format!("finding {id}: bad text"));
            }
            findings.push(FindingRec {
                id: id.to_owned(),
                level,
                text: text.to_owned(),
                round,
                state: State::Open,
            });
        }
        if pass && counts[Level::H.index()] > 0 {
            return Err("pass with a 嚴重 finding".into());
        }
        // a later review confirms claimed fixes; the claimed commit must be an ancestor of
        // this review's head (checked with git before writing, recorded here)
        let verifies = strs(o, "verifies")?;
        let mut seen = HashSet::new();
        for id in &verifies {
            if !seen.insert(id) {
                return Err(format!("verifies {id} twice"));
            }
            match entry.and_then(|p| p.findings.iter().find(|f| &f.id == id)) {
                Some(FindingRec {
                    state: State::Claimed { .. },
                    ..
                }) => {}
                Some(f) => return Err(format!("verifies {id}, which is {}", f.state.word())),
                None => return Err(format!("verifies {id}, which is not a finding of {pr}")),
            }
        }
        let p = self.prs.entry(pr.to_owned()).or_insert_with(|| Pr {
            author: author.to_owned(),
            reviews: Vec::new(),
            findings: Vec::new(),
        });
        for f in &mut p.findings {
            if verifies.contains(&f.id) {
                f.state = State::Verified {
                    seat: seat.to_owned(),
                    head: head.to_owned(),
                };
            }
        }
        p.findings.extend(findings);
        p.reviews.push(ReviewRec {
            round,
            head: head.to_owned(),
            seat: seat.to_owned(),
            pass,
            range: range.to_owned(),
            file: file.to_owned(),
            file_sha256: sha.to_owned(),
            at_ms,
        });
        Ok(())
    }

    fn apply_fix(&mut self, o: &Map<String, Value>) -> Result<(), String> {
        let pr = s(o, "pr")?;
        let seat = s(o, "seat")?;
        let commit = s(o, "commit")?;
        if !hex40_ok(commit) {
            return Err("bad commit".into());
        }
        let ids = strs(o, "ids")?;
        let p = self
            .prs
            .get_mut(pr)
            .ok_or_else(|| format!("{pr} has no review"))?;
        if p.author != seat {
            return Err(format!("only the author ({}) records a fix", p.author));
        }
        if ids.is_empty() {
            return Err("no ids".into());
        }
        for id in &ids {
            match p.findings.iter().find(|f| &f.id == id) {
                Some(FindingRec {
                    state: State::Verified { .. },
                    ..
                }) => return Err(format!("{id} is already verified")),
                Some(_) => {}
                None => return Err(format!("{id} is not a finding of {pr}")),
            }
        }
        for f in &mut p.findings {
            if ids.contains(&f.id) {
                f.state = State::Claimed {
                    commit: commit.to_owned(),
                };
            }
        }
        Ok(())
    }
}

pub fn replay(text: &str) -> Ledger {
    let mut l = Ledger::default();
    let errors = aib_ledger::replay(text, MAX_LINE_BYTES, |o| l.apply(o));
    l.errors = errors;
    l
}

/// What `submit` records.
#[derive(Debug, Clone)]
pub struct Submit {
    pub pr: String,
    pub seat: String,
    pub author: String,
    pub first: FirstLine,
    pub findings: Vec<Finding>,
    pub file: String,
    pub file_sha256: String,
    pub verifies: Vec<String>,
}

fn finish(l: &Ledger, o: Map<String, Value>) -> Result<String, String> {
    // the same rules as replay: write only what replay will accept
    l.clone().apply(&o)?;
    Ok(Value::Object(o).to_string())
}

/// The line for `submit`, or why it is refused.
pub fn submit_line(l: &Ledger, event_id: &str, at_ms: i64, sub: &Submit) -> Result<String, String> {
    let round = l.prs.get(&sub.pr).map_or(0, |p| p.reviews.len()) + 1;
    let mut o = envelope::write("submit", event_id, at_ms, AGG, l.next_sequence());
    o.insert("pr".into(), sub.pr.clone().into());
    o.insert("round".into(), round.into());
    o.insert("seat".into(), sub.seat.clone().into());
    o.insert("author".into(), sub.author.clone().into());
    o.insert("head".into(), sub.first.head.clone().into());
    o.insert("pass".into(), sub.first.pass.into());
    o.insert("range".into(), sub.first.range.clone().into());
    o.insert("file".into(), sub.file.clone().into());
    o.insert("file_sha256".into(), sub.file_sha256.clone().into());
    let findings: Vec<Value> = sub
        .findings
        .iter()
        .map(|f| {
            json!({
                "id": format!("R{round}-{}{}", f.level.letter(), f.n),
                "text": f.text,
            })
        })
        .collect();
    // ordered H1.. then M1.. as replay expects
    let mut findings = findings;
    findings.sort_by_key(|v| {
        let id = v["id"].as_str().unwrap_or_default().to_owned();
        let rest = id.split_once('-').map_or("", |(_, r)| r).to_owned();
        let level = rest
            .chars()
            .next()
            .and_then(Level::from_letter)
            .unwrap_or(Level::N);
        let n: usize = rest.get(1..).and_then(|x| x.parse().ok()).unwrap_or(0);
        (level, n)
    });
    o.insert("findings".into(), Value::Array(findings));
    o.insert("verifies".into(), json!(sub.verifies));
    finish(l, o)
}

/// The line for `fix`, or why it is refused.
pub fn fix_line(
    l: &Ledger,
    event_id: &str,
    at_ms: i64,
    pr: &str,
    ids: &[String],
    commit: &str,
    seat: &str,
) -> Result<String, String> {
    let mut o = envelope::write("fix", event_id, at_ms, AGG, l.next_sequence());
    o.insert("pr".into(), pr.into());
    o.insert("ids".into(), json!(ids));
    o.insert("commit".into(), commit.into());
    o.insert("seat".into(), seat.into());
    finish(l, o)
}

/// The heads a test receipt names (`HEAD=`, `== HEAD=`, `HEAD `, `HEAD-END `), after its seal
/// (the last line `RECEIPT-SHA256 <sha256 of every byte before that line>`, receipt-run.sh,
/// Andy 11-14) is checked. The seal catches a half-written or broken receipt, not a forger.
pub fn receipt_heads(text: &str) -> Result<Vec<String>, String> {
    let body = text.strip_suffix('\n').unwrap_or(text);
    let (before, last) = match body.rfind('\n') {
        Some(i) => (&text[..=i], &body[i + 1..]),
        None => ("", body),
    };
    let seal = last
        .strip_prefix("RECEIPT-SHA256 ")
        .ok_or("the last line is not RECEIPT-SHA256 (cut short or not a receipt)")?;
    if !hex64_ok(seal) {
        return Err("RECEIPT-SHA256 is not 64 hex".into());
    }
    if sha256_hex(before.as_bytes()) != seal {
        return Err("RECEIPT-SHA256 does not match the receipt".into());
    }
    let mut heads = Vec::new();
    let mut end = false;
    for line in before.lines() {
        let line = line.trim_end_matches('\r');
        let h = if let Some(h) = line.strip_prefix("HEAD-END ") {
            end = true;
            h
        } else if let Some(h) = line
            .strip_prefix("== HEAD=")
            .or_else(|| line.strip_prefix("HEAD="))
            .or_else(|| line.strip_prefix("HEAD "))
        {
            h
        } else {
            continue;
        };
        let h = h.trim();
        if !hex40_ok(h) {
            return Err(format!("a HEAD line is not 40 hex: {}", clean(h)));
        }
        heads.push(h.to_owned());
    }
    if heads.is_empty() {
        return Err("the receipt names no HEAD".into());
    }
    if !end {
        return Err("the receipt has no HEAD-END line (it did not finish)".into());
    }
    Ok(heads)
}

/// Why `pr` at `head` may not go on (empty = pass). `receipt` is what [`receipt_heads`] said,
/// when a receipt was given.
///
/// A verified finding is cleared only for a head that contains the head it was verified on
/// (`contains(verified_head, head)`, git `merge-base --is-ancestor` in the command), so an
/// older head without the fix does not pass. Errors from `contains` are returned as they are.
pub fn gate(
    l: &Ledger,
    pr: &str,
    head: &str,
    receipt: Option<&Result<Vec<String>, String>>,
    mut contains: impl FnMut(&str, &str) -> Result<bool, String>,
) -> Result<Vec<String>, String> {
    let mut why = Vec::new();
    match l.prs.get(pr) {
        None => why.push(format!("{pr} has no review")),
        Some(p) => {
            // the newest review on this head decides: a 不通過 there blocks even with no
            // 嚴重 / 中度 open (1ced7 r-1 M1; the merge rule is 通過 with H/M 0)
            match p.reviews.iter().rev().find(|r| r.head == head) {
                None => {
                    let newest = p.reviews.last().map_or("-", |r| &r.head[..9]);
                    why.push(format!(
                        "no seat review on head {} (the newest review is on {newest})",
                        &head[..9]
                    ));
                }
                Some(r) if !r.pass => why.push(format!(
                    "the newest review on head {} (round {} by {}) is 不通過",
                    &head[..9],
                    r.round,
                    r.seat
                )),
                Some(_) => {}
            }
            for f in p.findings.iter().filter(|f| f.level.blocks()) {
                let state = match &f.state {
                    State::Verified { head: v, .. } if v == head || contains(v, head)? => continue,
                    State::Verified { head: v, .. } => {
                        format!("verified on {}, which this head does not contain", &v[..9])
                    }
                    s => s.word().to_owned(),
                };
                why.push(format!(
                    "{} {} is {state}: {}",
                    f.id,
                    f.level.word(),
                    f.text
                ));
            }
        }
    }
    match receipt {
        None => {}
        Some(Err(e)) => why.push(format!("receipt: {e}")),
        Some(Ok(heads)) => {
            if let Some(h) = heads.iter().find(|h| h.as_str() != head) {
                why.push(format!("receipt: HEAD {} is not {}", &h[..9], &head[..9]));
            }
        }
    }
    Ok(why)
}

#[cfg(test)]
mod tests {
    use super::*;

    const H40: &str = "1b0d079b58bf33909f6b8302e82b543d40043581";
    const H40B: &str = "e798c1ca29582db812420f633bb0e6961634fc95";

    fn first(s: &str) -> FirstLine {
        parse_first_line(s).expect("first line")
    }

    #[test]
    fn the_first_line_is_read_in_the_one_fixed_shape() {
        let f = first(&format!(
            "結論: 通過 頭: {H40} H/M/L/N: 0/0/2/3 範圍: 6ddcdf57..1b0d079b"
        ));
        assert!(f.pass);
        assert_eq!(f.counts, [0, 0, 2, 3]);
        assert_eq!(f.head, H40);
        let f = first(&format!(
            "結論: 不通過 頭: {H40} H/M/L/N: 0/1/2/2 範圍: 全文"
        ));
        assert!(!f.pass);
        let f = first(&format!("結論: 要改 頭: {H40} H/M/L/N: 0/1/0/0 範圍: 全文"));
        assert!(!f.pass, "要改 = 不通過");
        for bad in [
            format!("結論: 通過(附 2 Medium 要改) 頭: {H40} H/M/L/N: 0/2/0/0 範圍: 全文"),
            format!("結論: 通過啦 頭: {H40} H/M/L/N: 0/0/0/0 範圍: 全文"),
            format!("結論:通過 頭: {H40} H/M/L/N: 0/0/0/0 範圍: 全文"),
            format!("結論: 可以 頭: {H40} H/M/L/N: 0/0/0/0 範圍: 全文"),
            format!("結論: 通過 頭: {} H/M/L/N: 0/0/0/0 範圍: 全文", &H40[..39]),
            format!("結論: 通過 頭: {H40} H/M/L: 0/0/0 範圍: 全文"),
            format!("結論: 通過 頭: {H40} H/M/L/N: 0/0/x/0 範圍: 全文"),
            format!("結論: 通過 頭: {H40} H/M/L/N: 0/0/0/0 範圍: "),
            format!(
                "結論: 通過 頭: {} H/M/L/N: 0/0/0/0 範圍: 全文",
                H40.to_uppercase()
            ),
        ] {
            assert!(parse_first_line(&bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn new_findings_are_the_bracket_lines_and_must_match_the_numbers() {
        let text = format!(
            "結論: 不通過 頭: {H40} H/M/L/N: 0/1/1/1 範圍: 全文\n\
             ## 已確認\n- **M1 已修**:上一輪的,不算新發現\n\
             - [M1] the lock is not released\n  - [L9] nested lines count too? no: indented ok\n\
             ```\n- [H1] an example inside a code block\n```\n- [N1] a note\n"
        );
        let f = parse_findings(&text).expect("findings");
        let ids: Vec<String> = f.iter().map(|x| format!("{:?}{}", x.level, x.n)).collect();
        assert_eq!(ids, vec!["M1", "L9", "N1"]);
        // L9 is not L1: numbers must be 1..n
        let fl = first(text.lines().next().unwrap_or_default());
        assert_eq!(
            check_counts(&fl, &f).unwrap_err(),
            "the L findings are not numbered 1..n"
        );
        let fl2 = first(&format!(
            "結論: 不通過 頭: {H40} H/M/L/N: 0/2/1/1 範圍: 全文"
        ));
        assert!(check_counts(&fl2, &f)
            .unwrap_err()
            .starts_with("H/M/L/N says 0/2/1/1 but the findings are 0/1/1/1"));
    }

    #[test]
    fn a_pass_with_a_severe_finding_is_refused() {
        let fl = first(&format!("結論: 通過 頭: {H40} H/M/L/N: 1/0/0/0 範圍: 全文"));
        let f = vec![Finding {
            level: Level::H,
            n: 1,
            text: "x".into(),
        }];
        assert_eq!(
            check_counts(&fl, &f).unwrap_err(),
            "結論 通過 with a 嚴重 finding"
        );
    }

    fn sub(l: &Ledger, seat: &str, head: &str, ms: [usize; 4], verifies: &[&str]) -> String {
        sub_as(l, seat, head, ms, verifies, ms[0] == 0 && ms[1] == 0)
    }

    fn sub_as(
        l: &Ledger,
        seat: &str,
        head: &str,
        ms: [usize; 4],
        verifies: &[&str],
        pass: bool,
    ) -> String {
        let mut findings = Vec::new();
        for lv in Level::ALL {
            for n in 1..=ms[lv.index()] {
                findings.push(Finding {
                    level: lv,
                    n,
                    text: format!("{} {n}", lv.word()),
                });
            }
        }
        let s = Submit {
            pr: "757".into(),
            seat: seat.into(),
            author: "043dd".into(),
            first: FirstLine {
                pass,
                head: head.into(),
                counts: ms,
                range: "全文".into(),
            },
            findings,
            file: "REVIEW.md".into(),
            file_sha256: sha256_hex(format!("{seat}{head}{}", l.next_sequence()).as_bytes()),
            verifies: verifies.iter().map(|x| (*x).to_owned()).collect(),
        };
        submit_line(l, &format!("e{}", l.next_sequence()), 1_800_000_000_000, &s).expect("submit")
    }

    /// The gate with a history where H40 → H40B → 333…: a head contains every earlier one.
    fn g(l: &Ledger, pr: &str, head: &str, r: Option<&Result<Vec<String>, String>>) -> Vec<String> {
        let order = |h: &str| [H40, H40B].iter().position(|x| *x == h).unwrap_or(2);
        gate(l, pr, head, r, |v, h| Ok(order(v) <= order(h))).expect("gate")
    }

    fn push(text: &mut String, line: &str) -> Ledger {
        text.push_str(line);
        text.push('\n');
        let l = replay(text);
        assert!(l.complete(), "{:?}", l.errors);
        l
    }

    #[test]
    fn a_medium_blocks_until_fixed_and_verified_by_another_seat() {
        let mut t = String::new();
        let l = push(
            &mut t,
            &sub(&Ledger::default(), "1ced7", H40, [0, 1, 1, 0], &[]),
        );
        let why = g(&l, "757", H40, None);
        assert_eq!(why.len(), 2, "{why:?}");
        assert!(why[0].ends_with("is 不通過"), "{why:?}");
        assert!(why[1].starts_with("R1-M1 中度 is open"), "{why:?}");
        // the author's fix only claims
        let line = fix_line(&l, "e2", 1, "757", &["R1-M1".into()], H40B, "043dd").expect("fix");
        let l = push(&mut t, &line);
        assert!(g(&l, "757", H40, None)[1].starts_with("R1-M1 中度 is claimed"));
        // the next round on the new head confirms it
        let l = push(&mut t, &sub(&l, "1ced7", H40B, [0, 0, 0, 0], &["R1-M1"]));
        assert_eq!(g(&l, "757", H40B, None), Vec::<String>::new());
        // the older head does not contain the fix: verified there does not clear it
        assert_eq!(
            g(&l, "757", H40, None),
            vec![
                format!(
                    "the newest review on head {} (round 1 by 1ced7) is 不通過",
                    &H40[..9]
                ),
                format!(
                    "R1-M1 中度 is verified on {}, which this head does not contain: 中度 1",
                    &H40B[..9]
                )
            ]
        );
        // a later head that contains the verified one is cleared (and needs its own review)
        let later = "3".repeat(40);
        assert_eq!(
            g(&l, "757", &later, None),
            vec![format!(
                "no seat review on head {} (the newest review is on {})",
                &later[..9],
                &H40B[..9]
            )]
        );
        // a git failure is not a pass
        assert_eq!(
            gate(&l, "757", &later, None, |_, _| Err("git: rc 128".into())).unwrap_err(),
            "git: rc 128"
        );
        // a light finding never blocks
        assert_eq!(
            l.finding("757", "R1-L1").map(|f| f.state.word()),
            Some("open")
        );
    }

    #[test]
    fn the_author_cannot_review_or_verify_and_only_the_author_claims() {
        let l = Ledger::default();
        let mut s = Submit {
            pr: "757".into(),
            seat: "043dd".into(),
            author: "043dd".into(),
            first: first(&format!("結論: 通過 頭: {H40} H/M/L/N: 0/0/0/0 範圍: 全文")),
            findings: vec![],
            file: "R.md".into(),
            file_sha256: sha256_hex(b"x"),
            verifies: vec![],
        };
        assert_eq!(
            submit_line(&l, "e1", 1, &s).unwrap_err(),
            "the author cannot review its own PR"
        );
        let mut t = String::new();
        let l = push(&mut t, &sub(&l, "1ced7", H40, [0, 1, 0, 0], &[]));
        assert_eq!(
            fix_line(&l, "e2", 1, "757", &["R1-M1".into()], H40B, "1ced7").unwrap_err(),
            "only the author (043dd) records a fix"
        );
        // verifying an open finding (no fix claimed) is refused
        s.seat = "a9136".into();
        s.file_sha256 = sha256_hex(b"y");
        s.verifies = vec!["R1-M1".into()];
        assert_eq!(
            submit_line(&l, "e3", 1, &s).unwrap_err(),
            "verifies R1-M1, which is open"
        );
        // the same review file twice
        s.verifies = vec![];
        s.file_sha256 = l.prs["757"].reviews[0].file_sha256.clone();
        assert_eq!(
            submit_line(&l, "e4", 1, &s).unwrap_err(),
            "this review file is already in the ledger"
        );
        // a second PR author for the same PR
        s.file_sha256 = sha256_hex(b"z");
        s.author = "ebb36".into();
        assert_eq!(
            submit_line(&l, "e5", 1, &s).unwrap_err(),
            "the author of 757 is 043dd"
        );
    }

    #[test]
    fn a_hand_edited_line_that_breaks_a_rule_makes_the_ledger_incomplete() {
        let line = sub(&Ledger::default(), "1ced7", H40, [0, 1, 0, 0], &[]);
        let bad = line.replace("\"seat\":\"1ced7\"", "\"seat\":\"043dd\"");
        assert_ne!(bad, line);
        let l = replay(&format!("{bad}\n"));
        assert_eq!(
            l.errors,
            vec!["line 1: the author cannot review its own PR"]
        );
        let flip = line.replace("\"R1-M1\"", "\"R1-M2\"");
        let l = replay(&format!("{flip}\n"));
        assert_eq!(l.errors, vec!["line 1: finding R1-M2 is out of order"]);
        let l = replay(&format!("{line}\n{line}\n"));
        assert_eq!(l.errors.len(), 1, "{:?}", l.errors);
        assert!(l.errors[0].starts_with("line 2: sequence is not 2"));
    }

    fn sealed(body: &str) -> String {
        format!("{body}RECEIPT-SHA256 {}\n", sha256_hex(body.as_bytes()))
    }

    #[test]
    fn a_receipt_must_be_sealed_and_finished_and_on_the_same_head() {
        let ok = sealed(&format!("x\nHEAD={H40}\nRC test=0\nHEAD-END {H40}\nEND\n"));
        assert_eq!(receipt_heads(&ok).expect("heads"), vec![H40, H40]);
        let shared = sealed(&format!("== HEAD={H40}\nHEAD-END {H40}\nHEAD {H40}\n"));
        assert_eq!(receipt_heads(&shared).expect("heads").len(), 3);
        // one byte changed after sealing
        let broken = ok.replace("RC test=0", "RC test=1");
        assert_eq!(
            receipt_heads(&broken).unwrap_err(),
            "RECEIPT-SHA256 does not match the receipt"
        );
        // cut before the seal
        assert!(receipt_heads(&format!("HEAD={H40}\nHEAD-END {H40}\n"))
            .unwrap_err()
            .starts_with("the last line is not RECEIPT-SHA256"));
        // never finished
        assert_eq!(
            receipt_heads(&sealed(&format!("HEAD={H40}\n"))).unwrap_err(),
            "the receipt has no HEAD-END line (it did not finish)"
        );
        // gate: head moved during the run
        let moved = sealed(&format!("HEAD={H40}\nHEAD-END {H40B}\n"));
        let mut t = String::new();
        let l = push(
            &mut t,
            &sub(&Ledger::default(), "1ced7", H40, [0, 0, 0, 0], &[]),
        );
        let r = receipt_heads(&moved);
        let why = g(&l, "757", H40, Some(&r));
        assert_eq!(
            why,
            vec![format!("receipt: HEAD {} is not {}", &H40B[..9], &H40[..9])]
        );
        let r = receipt_heads(&ok);
        assert!(g(&l, "757", H40, Some(&r)).is_empty());
    }

    #[test]
    fn no_review_on_this_head_blocks() {
        let mut t = String::new();
        let l = push(
            &mut t,
            &sub(&Ledger::default(), "1ced7", H40, [0, 0, 0, 0], &[]),
        );
        assert_eq!(
            g(&l, "757", H40B, None),
            vec![format!(
                "no seat review on head {} (the newest review is on {})",
                &H40B[..9],
                &H40[..9]
            )]
        );
        assert_eq!(g(&l, "758", H40, None), vec!["758 has no review"]);
    }

    /// 1ced7 r-1 M1: the newest review on the head must be 通過, even with no 嚴重 / 中度
    /// open; a later 通過 on the same head clears it.
    #[test]
    fn a_fail_on_this_head_blocks_until_a_later_pass_on_it() {
        let mut t = String::new();
        let l = push(
            &mut t,
            &sub_as(&Ledger::default(), "1ced7", H40, [0, 0, 2, 0], &[], false),
        );
        assert_eq!(
            g(&l, "757", H40, None),
            vec![format!(
                "the newest review on head {} (round 1 by 1ced7) is 不通過",
                &H40[..9]
            )]
        );
        let l = push(&mut t, &sub(&l, "7b81e", H40, [0, 0, 0, 0], &[]));
        assert!(
            g(&l, "757", H40, None).is_empty(),
            "a later 通過 on the head"
        );
        // a 不通過 on another head does not block this one
        let l = push(&mut t, &sub_as(&l, "1ced7", H40B, [0, 0, 1, 0], &[], false));
        assert!(g(&l, "757", H40, None).is_empty());
        assert_eq!(g(&l, "757", H40B, None).len(), 1);
    }
}
