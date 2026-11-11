// SPDX-License-Identifier: Apache-2.0
//! Restoring a site from a customer's All-in-One WP Migration backup (`.wpress`): what the
//! backup itself says the site should hold, taken **before** the import, and the rule that
//! compares it with what the restored site holds afterwards. Counting the restored site and
//! comparing it with itself always matches; the numbers have to come from the backup.
//!
//! The backup is untrusted input (a customer hands it over): every header field is checked,
//! a path that climbs out or starts at `/` is refused, a header cut short is refused, and an
//! archive that ends without its end block is refused. Nothing here guesses: when the table
//! prefix cannot be told from the dump, or the counts are not there, the answer is
//! [`Verdict::Unknown`] and the caller stops.
//!
//! Layout of a `.wpress` archive: one 4377-byte header before every file — name (255 bytes),
//! size (14, decimal), modification time (12, decimal), path prefix (4088), then a tail field
//! (8) that is not part of the path — followed by the file's bytes; the archive ends with a
//! header of zeros. The site's database is `database.sql` at the root (path prefix empty or
//! `.`); a plugin may carry its own `.sql`, so only the root one counts. The table prefix is
//! written as a placeholder in the dump; it is found from the `posts` table (the only one
//! whose columns include `post_type`, `post_status`, `post_date` and `post_parent`) and every
//! other core table is then named exactly, never by its ending (a plugin table called
//! `…_maspik_options` once stood in for the real options table).
//!
//! Not a criterion, ever: `active_plugins`, `stylesheet` and `template` — the export empties
//! them. Only advisory: post types and statuses WordPress prunes or expires on its own
//! (revisions, auto-drafts, the customizer's changesets, oEmbed caches, the trash).
//!
//! No I/O: the caller reads the archive and feeds this module headers and chunks.

use alloc::collections::BTreeMap;
use alloc::string::{String, ToString};
use alloc::vec::Vec;

/// Bytes in one entry header.
pub const HEADER_LEN: usize = 4377;
const NAME_END: usize = 255;
const SIZE_END: usize = 269;
const MTIME_END: usize = 281;
const PATH_END: usize = 4369;

/// Longest SQL statement head kept while looking for its kind (a `CREATE TABLE` is far
/// shorter); a longer one makes the dump unreadable for this purpose.
pub const HEAD_CAP: usize = 256 * 1024;

/// Longest post type / status value believed (WordPress columns are 20 characters).
pub const VALUE_CAP: usize = 64;

/// One file in the archive.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// File name, no path.
    pub name: String,
    /// Folder inside the archive, `""` at the root, no leading or trailing `/`.
    pub folder: String,
    /// Bytes of content that follow the header.
    pub size: u64,
}

impl Entry {
    /// At the root of the archive.
    pub fn at_root(&self) -> bool {
        self.folder.is_empty()
    }
}

/// What one header block is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Header {
    /// The end-of-archive block (all zeros).
    End,
    /// A file follows.
    Entry(Entry),
}

/// Reads one header block. `block` is what was read at a header position; fewer than
/// [`HEADER_LEN`] bytes means the archive was cut.
pub fn parse_header(block: &[u8]) -> Result<Header, String> {
    if block.len() != HEADER_LEN {
        return Err(format!(
            "a header is cut short ({} of {HEADER_LEN} bytes)",
            block.len()
        ));
    }
    if block.iter().all(|b| *b == 0) {
        return Ok(Header::End);
    }
    let name = field(&block[..NAME_END], "name")?;
    let size = field(&block[NAME_END..SIZE_END], "size")?;
    let mtime = field(&block[SIZE_END..MTIME_END], "time")?;
    let folder = field(&block[MTIME_END..PATH_END], "path")?;
    if size.is_empty() || !size.bytes().all(|b| b.is_ascii_digit()) {
        return Err("a header's size is not a number".to_string());
    }
    let size: u64 = size
        .parse()
        .map_err(|_| "a header's size is out of range".to_string())?;
    if !mtime.bytes().all(|b| b.is_ascii_digit()) {
        return Err("a header's time is not a number".to_string());
    }
    if name.is_empty() || name == "." || name == ".." || name.contains(['/', '\\']) {
        return Err("a header names no file, or a path instead of a file".to_string());
    }
    let folder = clean_folder(&folder)?;
    Ok(Header::Entry(Entry { name, folder, size }))
}

/// The text of a NUL-padded header field; anything after the first NUL must be NUL too,
/// and the text must be UTF-8 without control characters.
fn field(raw: &[u8], what: &str) -> Result<String, String> {
    let end = raw.iter().position(|b| *b == 0).unwrap_or(raw.len());
    if raw[end..].iter().any(|b| *b != 0) {
        return Err(format!("a header's {what} has bytes after its end"));
    }
    let text =
        core::str::from_utf8(&raw[..end]).map_err(|_| format!("a header's {what} is not UTF-8"))?;
    if text.chars().any(char::is_control) {
        return Err(format!("a header's {what} holds a control character"));
    }
    Ok(text.to_string())
}

/// A folder inside the archive, checked: relative, no `..`, no backslash.
fn clean_folder(path: &str) -> Result<String, String> {
    if path.starts_with('/') || path.contains('\\') {
        return Err("a header's path is absolute or uses backslashes".to_string());
    }
    let mut out: Vec<&str> = Vec::new();
    for seg in path.split('/') {
        if seg.is_empty() || seg == "." {
            continue;
        }
        if seg == ".." {
            return Err("a header's path climbs out of the archive".to_string());
        }
        out.push(seg);
    }
    Ok(out.join("/"))
}

/// What the archive's `package.json` says about the source site (all optional; the counts
/// do not depend on it).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Package {
    /// `SiteURL`.
    pub site_url: Option<String>,
    /// `HomeURL`.
    pub home_url: Option<String>,
    /// `WordPress.Version`.
    pub wp_version: Option<String>,
}

/// Reads the root `package.json`; `None` when it is not a JSON object.
pub fn package(text: &str) -> Option<Package> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    let o = v.as_object()?;
    let s = |x: Option<&serde_json::Value>| x.and_then(|x| x.as_str()).map(ToString::to_string);
    Some(Package {
        site_url: s(o.get("SiteURL")),
        home_url: s(o.get("HomeURL")),
        wp_version: s(o.get("WordPress").and_then(|w| w.get("Version"))),
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    /// Collecting the head of a statement.
    Head,
    /// Inside the `VALUES` of an `INSERT`.
    Rows,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Quote {
    None,
    /// Inside a string opened by this byte (`'` or `"`).
    Str(u8),
    /// Inside a back-quoted name.
    Name,
    LineComment,
    BlockComment,
}

/// Counts what a dump inserts, fed in chunks of any size. Strings, back-quoted names and
/// comments are followed byte by byte, so a `),(` or a `;` inside a value is text.
#[derive(Debug)]
pub struct SqlScan {
    mode: Mode,
    q: Quote,
    esc: bool,
    /// A string just closed; a quote right after it reopens it (`''` is one quote).
    closed: Option<u8>,
    prev: u8,
    head: Vec<u8>,
    /// Columns of each table, from `CREATE TABLE` or an `INSERT` column list.
    columns: BTreeMap<String, Vec<String>>,
    /// Rows inserted into each table.
    rows: BTreeMap<String, u64>,
    /// Rows inserted into a table whose columns were not known yet.
    blind: BTreeMap<String, u64>,
    /// Per table with `post_type` and `post_status` columns: (type, status) → rows.
    tally: BTreeMap<String, BTreeMap<(String, String), u64>>,
    // the INSERT being read
    table: String,
    want: [Option<usize>; 2],
    depth: u32,
    col: usize,
    cur: [Vec<u8>; 2],
    over: bool,
    broken: Option<String>,
}

impl Default for SqlScan {
    fn default() -> Self {
        Self::new()
    }
}

impl SqlScan {
    /// A scanner at the start of a dump.
    pub fn new() -> Self {
        SqlScan {
            mode: Mode::Head,
            q: Quote::None,
            esc: false,
            closed: None,
            prev: 0,
            head: Vec::new(),
            columns: BTreeMap::new(),
            rows: BTreeMap::new(),
            blind: BTreeMap::new(),
            tally: BTreeMap::new(),
            table: String::new(),
            want: [None, None],
            depth: 0,
            col: 0,
            cur: [Vec::new(), Vec::new()],
            over: false,
            broken: None,
        }
    }

    fn fail(&mut self, why: String) {
        if self.broken.is_none() {
            self.broken = Some(why);
        }
    }

    /// Feeds the next bytes of the dump.
    pub fn feed(&mut self, chunk: &[u8]) {
        for &b in chunk {
            if self.broken.is_some() {
                return;
            }
            self.byte(b);
        }
    }

    fn keep(&mut self, b: u8) {
        match self.mode {
            Mode::Head => {
                if self.head.len() >= HEAD_CAP {
                    self.fail("a statement is longer than this scanner reads".to_string());
                    return;
                }
                self.head.push(b);
            }
            Mode::Rows => {
                if self.depth == 1 {
                    for (i, w) in self.want.iter().enumerate() {
                        if *w == Some(self.col) {
                            if self.cur[i].len() >= VALUE_CAP {
                                self.over = true;
                            } else {
                                self.cur[i].push(b);
                            }
                        }
                    }
                }
            }
        }
    }

    fn byte(&mut self, b: u8) {
        if let Some(q) = self.closed.take() {
            if b == q {
                // a doubled quote: one quote character, the string goes on
                self.q = Quote::Str(q);
                self.keep_text(q);
                return;
            }
        }
        match self.q {
            Quote::Str(q) => {
                if self.esc {
                    self.esc = false;
                    let c = match b {
                        b'n' => b'\n',
                        b'r' => b'\r',
                        b't' => b'\t',
                        b'0' => 0,
                        b'Z' => 0x1a,
                        x => x,
                    };
                    self.keep_text(c);
                } else if b == b'\\' {
                    self.esc = true;
                } else if b == q {
                    self.q = Quote::None;
                    self.closed = Some(q);
                    if self.mode == Mode::Head {
                        self.keep(b);
                    }
                } else {
                    self.keep_text(b);
                }
                return;
            }
            Quote::Name => {
                self.keep(b);
                if b == b'`' {
                    self.q = Quote::None;
                }
                return;
            }
            Quote::LineComment => {
                if b == b'\n' {
                    self.q = Quote::None;
                    self.keep(b' ');
                }
                return;
            }
            Quote::BlockComment => {
                if self.prev == b'*' && b == b'/' {
                    self.q = Quote::None;
                    self.prev = 0;
                    self.keep(b' ');
                } else {
                    self.prev = b;
                }
                return;
            }
            Quote::None => {}
        }
        match self.mode {
            Mode::Head => self.head_byte(b),
            Mode::Rows => self.row_byte(b),
        }
    }

    /// A byte of string content: kept as is in a head (the quotes stay around it), kept
    /// unescaped in a wanted row value.
    fn keep_text(&mut self, b: u8) {
        self.keep(b);
    }

    fn head_byte(&mut self, b: u8) {
        let last = self.head.last().copied();
        match b {
            b'\'' | b'"' => {
                self.q = Quote::Str(b);
                self.keep(b);
            }
            b'`' => {
                self.q = Quote::Name;
                self.keep(b);
            }
            b'#' => self.q = Quote::LineComment,
            b'-' if last == Some(b'-') => {
                self.head.pop();
                self.q = Quote::LineComment;
            }
            b'*' if last == Some(b'/') => {
                self.head.pop();
                self.q = Quote::BlockComment;
                self.prev = 0;
            }
            b';' => {
                self.statement_done();
            }
            b'(' if self.values_start() => {
                self.begin_insert();
            }
            _ => self.keep(b),
        }
    }

    /// The head reads `INSERT … VALUES` and the next `(` opens the first row.
    fn values_start(&self) -> bool {
        let h = trim(&self.head);
        starts_ci(h, b"INSERT") && ends_ci(h, b"VALUES")
    }

    fn begin_insert(&mut self) {
        let head = core::mem::take(&mut self.head);
        let h = trim(&head);
        let Some((table, rest)) = first_name(h) else {
            self.fail("an INSERT names no back-quoted table".to_string());
            return;
        };
        let body = &rest[..rest.len().saturating_sub(b"VALUES".len())];
        // a column list on the INSERT names this statement's order, whatever CREATE said
        let mut listed: Option<Vec<String>> = None;
        if let (Some(open), Some(close)) = (
            body.iter().position(|c| *c == b'('),
            body.iter().rposition(|c| *c == b')'),
        ) {
            if open < close {
                let cols: Vec<String> = body[open + 1..close]
                    .split(|c| *c == b',')
                    .map(|c| {
                        String::from_utf8_lossy(trim(c))
                            .trim_matches('`')
                            .to_string()
                    })
                    .collect();
                self.columns
                    .entry(table.clone())
                    .or_insert_with(|| cols.clone());
                listed = Some(cols);
            }
        }
        let cols = listed.as_ref().or_else(|| self.columns.get(&table));
        self.want = match cols {
            Some(c) => [
                c.iter().position(|x| x == "post_type"),
                c.iter().position(|x| x == "post_status"),
            ],
            None => [None, None],
        };
        self.table = table;
        self.mode = Mode::Rows;
        self.depth = 0;
        self.row_open();
    }

    fn row_open(&mut self) {
        self.depth = 1;
        self.col = 0;
        self.cur = [Vec::new(), Vec::new()];
        self.over = false;
    }

    fn row_byte(&mut self, b: u8) {
        match b {
            b'\'' | b'"' => self.q = Quote::Str(b),
            b'`' => self.q = Quote::Name,
            b'(' => {
                if self.depth == 0 {
                    self.row_open();
                } else {
                    self.depth += 1;
                    self.keep(b);
                }
            }
            b')' => {
                if self.depth == 0 {
                    self.fail("a row closes that was never opened".to_string());
                } else if self.depth == 1 {
                    self.depth = 0;
                    self.row_done();
                } else {
                    self.depth -= 1;
                    self.keep(b);
                }
            }
            b',' if self.depth == 1 => self.col += 1,
            b',' if self.depth == 0 => {}
            b';' if self.depth == 0 => {
                self.mode = Mode::Head;
                self.head.clear();
            }
            b' ' | b'\t' | b'\r' | b'\n' => {
                if self.depth > 0 {
                    self.keep(b);
                }
            }
            _ if self.depth == 0 => {
                self.fail("text between the rows of an INSERT that is not a row".to_string());
            }
            _ => self.keep(b),
        }
    }

    fn row_done(&mut self) {
        *self.rows.entry(self.table.clone()).or_insert(0) += 1;
        match self.want {
            [Some(_), Some(_)] => {
                if self.over {
                    self.fail(format!(
                        "a post type or status in {} is longer than {VALUE_CAP} bytes",
                        self.table
                    ));
                    return;
                }
                let t = String::from_utf8_lossy(trim(&self.cur[0])).to_string();
                let s = String::from_utf8_lossy(trim(&self.cur[1])).to_string();
                *self
                    .tally
                    .entry(self.table.clone())
                    .or_default()
                    .entry((t, s))
                    .or_insert(0) += 1;
            }
            _ if !self.columns.contains_key(&self.table) => {
                *self.blind.entry(self.table.clone()).or_insert(0) += 1;
            }
            _ => {}
        }
    }

    fn statement_done(&mut self) {
        let head = core::mem::take(&mut self.head);
        let h = trim(&head);
        if starts_ci(h, b"CREATE TABLE") {
            if let Some((table, rest)) = first_name(h) {
                let cols = create_columns(rest);
                self.columns.insert(table, cols);
            }
        } else if starts_ci(h, b"INSERT") {
            self.fail("an INSERT without VALUES rows".to_string());
        }
    }

    /// Finishes the dump: the counts the restored site must show, or why they cannot be
    /// told.
    pub fn finish(self) -> Result<Expect, String> {
        if let Some(why) = self.broken {
            return Err(why);
        }
        if self.mode == Mode::Rows || self.q != Quote::None || !trim(&self.head).is_empty() {
            return Err("the dump ends inside a statement".to_string());
        }
        let posts: Vec<&String> = self
            .columns
            .iter()
            .filter(|(t, c)| {
                t.ends_with("posts")
                    && ["post_type", "post_status", "post_date", "post_parent"]
                        .iter()
                        .all(|n| c.iter().any(|x| x == n))
            })
            .map(|(t, _)| t)
            .collect();
        let posts_table = match posts.as_slice() {
            [one] => (*one).clone(),
            [] => return Err("no posts table: the table prefix cannot be told".to_string()),
            _ => {
                return Err(format!(
                    "{} tables look like the posts table: the table prefix cannot be told",
                    posts.len()
                ))
            }
        };
        let prefix = posts_table[..posts_table.len() - "posts".len()].to_string();
        if self.blind.contains_key(&posts_table) {
            return Err("rows of the posts table came before its columns".to_string());
        }
        let users_table = format!("{prefix}users");
        if !self.columns.contains_key(&users_table) && !self.rows.contains_key(&users_table) {
            return Err("no users table under the posts table's prefix".to_string());
        }
        let users = self.rows.get(&users_table).copied().unwrap_or(0);
        let posts = self.tally.get(&posts_table).cloned().unwrap_or_default();
        let total: u64 = posts.values().sum();
        if total == 0 {
            return Err("the backup holds no posts at all".to_string());
        }
        if users == 0 {
            return Err("the backup holds no users".to_string());
        }
        Ok(Expect {
            prefix,
            posts,
            users,
        })
    }
}

fn trim(b: &[u8]) -> &[u8] {
    let s = b
        .iter()
        .position(|c| !c.is_ascii_whitespace())
        .unwrap_or(b.len());
    let e = b
        .iter()
        .rposition(|c| !c.is_ascii_whitespace())
        .map_or(s, |e| e + 1);
    &b[s..e.max(s)]
}

fn starts_ci(h: &[u8], w: &[u8]) -> bool {
    h.len() >= w.len() && h[..w.len()].eq_ignore_ascii_case(w)
}

fn ends_ci(h: &[u8], w: &[u8]) -> bool {
    h.len() >= w.len() && h[h.len() - w.len()..].eq_ignore_ascii_case(w)
}

/// The first back-quoted name in a head and what follows it.
fn first_name(h: &[u8]) -> Option<(String, &[u8])> {
    let a = h.iter().position(|c| *c == b'`')?;
    let len = h[a + 1..].iter().position(|c| *c == b'`')?;
    let name = core::str::from_utf8(&h[a + 1..a + 1 + len]).ok()?;
    if name.is_empty() {
        return None;
    }
    Some((name.to_string(), &h[a + 2 + len..]))
}

/// Column names of a `CREATE TABLE` body: each line that starts with a back-quoted name.
fn create_columns(body: &[u8]) -> Vec<String> {
    body.split(|c| *c == b'\n')
        .filter_map(|line| {
            let l = trim(line);
            let l = l.strip_prefix(b"(").map_or(l, trim);
            if l.first() != Some(&b'`') {
                return None;
            }
            first_name(l).map(|(n, _)| n)
        })
        .collect()
}

/// What the backup says the restored site must hold.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expect {
    /// The table prefix as written in the dump (a placeholder, not the site's).
    pub prefix: String,
    /// (post type, post status) → rows.
    pub posts: BTreeMap<(String, String), u64>,
    /// Rows of the users table.
    pub users: u64,
}

/// What the restored site holds, counted the same way (per type × status, straight from
/// the database, auto-drafts included).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Actual {
    /// (post type, post status) → rows.
    pub posts: BTreeMap<(String, String), u64>,
    /// Rows of the users table; `None` when it could not be counted.
    pub users: Option<u64>,
}

/// What may differ without failing the restore.
#[derive(Debug, Clone, Copy)]
pub struct Policy<'a> {
    /// Post types WordPress prunes or expires itself.
    pub advisory_types: &'a [&'a str],
    /// Post statuses WordPress expires itself.
    pub advisory_statuses: &'a [&'a str],
    /// Users the restore adds on purpose (the platform's own administrators that were not in
    /// the backup); the caller says how many.
    pub users_added: u64,
}

/// Post types listed but never judged.
pub const ADVISORY_TYPES: &[&str] = &[
    "revision",
    "auto-draft",
    "customize_changeset",
    "oembed_cache",
];
/// Post statuses listed but never judged.
pub const ADVISORY_STATUSES: &[&str] = &["auto-draft", "trash"];

impl Policy<'static> {
    /// The standing policy, with `users_added` users the restore made on purpose.
    pub fn standard(users_added: u64) -> Self {
        Policy {
            advisory_types: ADVISORY_TYPES,
            advisory_statuses: ADVISORY_STATUSES,
            users_added,
        }
    }
}

/// One cell that differs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diff {
    /// Post type, or `users`.
    pub kind: String,
    /// Post status (empty for users).
    pub status: String,
    /// From the backup.
    pub expected: u64,
    /// On the site.
    pub actual: u64,
}

/// The answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Every judged cell matches; `advisory` lists what differs where it may.
    Match {
        /// Differences in advisory cells.
        advisory: Vec<Diff>,
    },
    /// Some judged cell differs.
    Differ {
        /// Judged cells that differ.
        strict: Vec<Diff>,
        /// Differences in advisory cells.
        advisory: Vec<Diff>,
    },
    /// The comparison cannot be made.
    Unknown(String),
}

/// Compares the backup's counts with the restored site's.
pub fn compare(expect: &Expect, actual: &Actual, policy: &Policy<'_>) -> Verdict {
    let Some(users) = actual.users else {
        return Verdict::Unknown("the restored site's users could not be counted".to_string());
    };
    let mut strict = Vec::new();
    let mut advisory = Vec::new();
    let mut judged = 0u64;
    let mut keys: Vec<&(String, String)> = expect.posts.keys().chain(actual.posts.keys()).collect();
    keys.sort();
    keys.dedup();
    for k in keys {
        let e = expect.posts.get(k).copied().unwrap_or(0);
        let a = actual.posts.get(k).copied().unwrap_or(0);
        let only_listed = policy.advisory_types.contains(&k.0.as_str())
            || policy.advisory_statuses.contains(&k.1.as_str());
        let d = Diff {
            kind: k.0.clone(),
            status: k.1.clone(),
            expected: e,
            actual: a,
        };
        if only_listed {
            if e != a {
                advisory.push(d);
            }
            continue;
        }
        judged += e;
        if e != a {
            strict.push(d);
        }
    }
    if judged == 0 {
        return Verdict::Unknown(
            "the backup holds nothing that is judged: an empty backup matches an empty site"
                .to_string(),
        );
    }
    if users < expect.users || users > expect.users.saturating_add(policy.users_added) {
        strict.push(Diff {
            kind: "users".to_string(),
            status: String::new(),
            expected: expect.users,
            actual: users,
        });
    }
    if strict.is_empty() {
        Verdict::Match { advisory }
    } else {
        Verdict::Differ { strict, advisory }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::string::String as S;

    fn header(name: &str, size: &str, folder: &str) -> Vec<u8> {
        let mut h = vec![0u8; HEADER_LEN];
        h[..name.len()].copy_from_slice(name.as_bytes());
        h[NAME_END..NAME_END + size.len()].copy_from_slice(size.as_bytes());
        h[SIZE_END..SIZE_END + 10].copy_from_slice(b"1793000000");
        h[MTIME_END..MTIME_END + folder.len()].copy_from_slice(folder.as_bytes());
        h
    }

    const DUMP: &str = "\
DROP TABLE IF EXISTS `SERVMASK_PREFIX_options`;
CREATE TABLE `SERVMASK_PREFIX_options` (
  `option_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `option_name` varchar(191) NOT NULL DEFAULT '',
  `option_value` longtext NOT NULL,
  PRIMARY KEY (`option_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='a; b (c)';
INSERT INTO `SERVMASK_PREFIX_options` VALUES (1,'active_plugins','a:0:{}'),(2,'blogname','It''s \\'x\\'');
-- a comment; with a semicolon
/* another; one */
CREATE TABLE `SERVMASK_PREFIX_posts` (
  `ID` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `post_date` datetime NOT NULL,
  `post_content` longtext NOT NULL,
  `post_status` varchar(20) NOT NULL DEFAULT 'publish',
  `post_parent` bigint(20) unsigned NOT NULL DEFAULT '0',
  `post_type` varchar(20) NOT NULL DEFAULT 'post',
  PRIMARY KEY (`ID`)
);
INSERT INTO `SERVMASK_PREFIX_posts` VALUES (1,'2026-01-01','hello),(world; \"q\" (x)','publish',0,'post'),
(2,'2026-01-02','line one
line two','draft',0,'post'),(3,'2026-01-03','','publish',0,'page'),
(4,'2026-01-04','','inherit',1,'revision'),(5,'2026-01-05','','publish',0,'attachment');
INSERT INTO `SERVMASK_PREFIX_posts` (`ID`,`post_date`,`post_content`,`post_status`,`post_parent`,`post_type`) VALUES (6,'2026-01-06','','trash',0,'post');
CREATE TABLE `SERVMASK_PREFIX_users` (
  `ID` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `user_login` varchar(60) NOT NULL DEFAULT ''
);
INSERT INTO `SERVMASK_PREFIX_users` VALUES (1,'admin'),(2,'editor');
CREATE TABLE `SERVMASK_PREFIX_maspik_options` (
  `option_name` varchar(191) NOT NULL,
  `option_value` longtext NOT NULL
);
";

    fn scan(text: &str, chunk: usize) -> Result<Expect, S> {
        let mut s = SqlScan::new();
        for c in text.as_bytes().chunks(chunk) {
            s.feed(c);
        }
        s.finish()
    }

    fn k(t: &str, s: &str) -> (S, S) {
        (t.to_string(), s.to_string())
    }

    #[test]
    fn a_header_is_read() {
        let h = header("database.sql", "1234", "");
        assert_eq!(
            parse_header(&h).unwrap(),
            Header::Entry(Entry {
                name: "database.sql".into(),
                folder: "".into(),
                size: 1234
            })
        );
        let h = header("x.php", "1", "./plugins/a/");
        let Header::Entry(e) = parse_header(&h).unwrap() else {
            panic!()
        };
        assert_eq!(e.folder, "plugins/a");
        assert!(!e.at_root());
        assert_eq!(parse_header(&vec![0u8; HEADER_LEN]).unwrap(), Header::End);
    }

    #[test]
    fn a_cut_header_is_refused() {
        let h = header("database.sql", "1234", "");
        assert!(parse_header(&h[..HEADER_LEN - 1]).is_err());
        assert!(parse_header(&[]).is_err());
    }

    #[test]
    fn a_path_that_climbs_out_is_refused() {
        for p in ["../etc", "plugins/../../x", "/etc", "a\\b"] {
            assert!(parse_header(&header("x", "1", p)).is_err(), "{p}");
        }
        for n in ["..", ".", "a/b", ""] {
            assert!(parse_header(&header(n, "1", "")).is_err(), "{n}");
        }
    }

    #[test]
    fn a_header_with_a_bad_number_or_hidden_bytes_is_refused() {
        assert!(parse_header(&header("x", "12a", "")).is_err());
        assert!(parse_header(&header("x", "", "")).is_err());
        assert!(parse_header(&header("x", "99999999999999", "")).is_ok());
        let mut h = header("x", "1", "");
        h[100] = b'y'; // after the name's NUL
        assert!(parse_header(&h).is_err());
        let mut h = header("x", "1", "");
        h[0] = 0x07;
        assert!(parse_header(&h).is_err());
    }

    #[test]
    fn the_dump_is_counted_in_any_chunk_size() {
        for chunk in [1, 2, 3, 7, 64, 4096, DUMP.len()] {
            let e = scan(DUMP, chunk).unwrap();
            assert_eq!(e.prefix, "SERVMASK_PREFIX_", "chunk {chunk}");
            assert_eq!(e.users, 2);
            assert_eq!(e.posts.get(&k("post", "publish")), Some(&1));
            assert_eq!(e.posts.get(&k("post", "draft")), Some(&1));
            assert_eq!(e.posts.get(&k("page", "publish")), Some(&1));
            assert_eq!(e.posts.get(&k("revision", "inherit")), Some(&1));
            assert_eq!(e.posts.get(&k("attachment", "publish")), Some(&1));
            assert_eq!(e.posts.get(&k("post", "trash")), Some(&1));
            assert_eq!(e.posts.values().sum::<u64>(), 6, "chunk {chunk}");
        }
    }

    #[test]
    fn row_separators_inside_strings_are_text() {
        let text = DUMP.replace("'hello),(world; \"q\" (x)'", "'a),(b),(c'");
        let e = scan(&text, 5).unwrap();
        assert_eq!(e.posts.values().sum::<u64>(), 6);
        assert_eq!(e.posts.get(&k("post", "publish")), Some(&1));
    }

    #[test]
    fn a_doubled_or_escaped_quote_stays_inside_the_string() {
        let text = DUMP.replace("'hello),(world; \"q\" (x)'", "'it''s),(x \\' ),('");
        let e = scan(&text, 1).unwrap();
        assert_eq!(e.posts.values().sum::<u64>(), 6);
    }

    #[test]
    fn an_inserts_own_column_list_decides_where_type_and_status_are() {
        let text = DUMP.replace(
            "(`ID`,`post_date`,`post_content`,`post_status`,`post_parent`,`post_type`) VALUES (6,'2026-01-06','','trash',0,'post')",
            "(`ID`,`post_type`,`post_date`,`post_content`,`post_status`,`post_parent`) VALUES (6,'post','2026-01-06','','trash',0)",
        );
        let e = scan(&text, 9).unwrap();
        assert_eq!(e.posts.get(&k("post", "trash")), Some(&1));
    }

    #[test]
    fn two_posts_tables_leave_the_prefix_unknown() {
        let text = DUMP.replace("CREATE TABLE `SERVMASK_PREFIX_maspik_options`", "CREATE TABLE `other_posts` (\n  `post_type` x,\n  `post_status` x,\n  `post_date` x,\n  `post_parent` x\n);\nCREATE TABLE `zz`");
        assert!(scan(&text, 64).unwrap_err().contains("prefix"));
        let text = DUMP.replace("`post_parent` bigint", "`parent` bigint");
        assert!(scan(&text, 64).unwrap_err().contains("prefix"));
    }

    #[test]
    fn missing_or_empty_tables_are_unknown() {
        let no_users = DUMP.replace("SERVMASK_PREFIX_users", "other_users");
        assert!(scan(&no_users, 64).is_err());
        let zero_users = DUMP.replace(
            "INSERT INTO `SERVMASK_PREFIX_users` VALUES (1,'admin'),(2,'editor');",
            "",
        );
        assert!(scan(&zero_users, 64).unwrap_err().contains("users"));
        let start = DUMP
            .find("INSERT INTO `SERVMASK_PREFIX_posts` VALUES")
            .unwrap();
        let end = DUMP.find("CREATE TABLE `SERVMASK_PREFIX_users`").unwrap();
        let no_posts = format!("{}{}", &DUMP[..start], &DUMP[end..]);
        assert!(scan(&no_posts, 64).unwrap_err().contains("no posts"));
    }

    #[test]
    fn a_broken_dump_is_unknown() {
        assert!(scan(&DUMP[..DUMP.len() - 300], 64).is_err());
        let junk = DUMP.replace("(1,'admin'),(2,'editor')", "(1,'admin') junk (2,'editor')");
        assert!(scan(&junk, 64).is_err());
        let long = DUMP.replace("'draft'", &format!("'{}'", "d".repeat(VALUE_CAP + 1)));
        assert!(scan(&long, 64).unwrap_err().contains("longer"));
        let mut huge = S::from("CREATE TABLE `t` (");
        huge.push_str(&"x".repeat(HEAD_CAP + 1));
        assert!(scan(&huge, 4096).is_err());
        let early = format!(
            "INSERT INTO `SERVMASK_PREFIX_posts` VALUES (9,'x','','publish',0,'post');\n{DUMP}"
        );
        assert!(scan(&early, 64).unwrap_err().contains("before its columns"));
        let no_values = format!("{DUMP}INSERT INTO `t` SELECT 1;\n");
        assert!(scan(&no_values, 64).is_err());
    }

    #[test]
    fn package_facts_are_read() {
        let p = package(r#"{"SiteURL":"https://example.test","HomeURL":"https://example.test","WordPress":{"Version":"6.8.1"},"Plugin":{"Version":"7.9"}}"#).unwrap();
        assert_eq!(p.site_url.as_deref(), Some("https://example.test"));
        assert_eq!(p.wp_version.as_deref(), Some("6.8.1"));
        assert!(package("[1]").is_none());
        assert!(package("nope").is_none());
    }

    fn expect() -> Expect {
        scan(DUMP, 4096).unwrap()
    }

    fn actual_like(e: &Expect) -> Actual {
        Actual {
            posts: e.posts.clone(),
            users: Some(e.users),
        }
    }

    #[test]
    fn the_same_counts_match() {
        let e = expect();
        assert_eq!(
            compare(&e, &actual_like(&e), &Policy::standard(0)),
            Verdict::Match { advisory: vec![] }
        );
    }

    #[test]
    fn revision_and_trash_are_listed_not_judged() {
        let e = expect();
        let mut a = actual_like(&e);
        a.posts.remove(&k("revision", "inherit"));
        a.posts.remove(&k("post", "trash"));
        a.posts.insert(k("post", "auto-draft"), 3);
        match compare(&e, &a, &Policy::standard(0)) {
            Verdict::Match { advisory } => assert_eq!(advisory.len(), 3),
            v => panic!("{v:?}"),
        }
    }

    #[test]
    fn fewer_or_more_posts_differ() {
        let e = expect();
        let mut a = actual_like(&e);
        a.posts.insert(k("post", "publish"), 0);
        a.posts.insert(k("page", "draft"), 1);
        match compare(&e, &a, &Policy::standard(0)) {
            Verdict::Differ { strict, .. } => {
                assert_eq!(strict.len(), 2);
                assert!(strict
                    .iter()
                    .any(|d| d.kind == "post" && d.expected == 1 && d.actual == 0));
                assert!(strict
                    .iter()
                    .any(|d| d.kind == "page" && d.status == "draft"));
            }
            v => panic!("{v:?}"),
        }
    }

    #[test]
    fn users_the_restore_added_are_allowed_and_no_more() {
        let e = expect();
        let mut a = actual_like(&e);
        a.users = Some(e.users + 2);
        assert!(matches!(
            compare(&e, &a, &Policy::standard(2)),
            Verdict::Match { .. }
        ));
        assert!(matches!(
            compare(&e, &a, &Policy::standard(1)),
            Verdict::Differ { .. }
        ));
        a.users = Some(e.users - 1);
        assert!(matches!(
            compare(&e, &a, &Policy::standard(5)),
            Verdict::Differ { .. }
        ));
        a.users = None;
        assert!(matches!(
            compare(&e, &a, &Policy::standard(0)),
            Verdict::Unknown(_)
        ));
    }

    #[test]
    fn nothing_judged_is_unknown() {
        let mut e = expect();
        e.posts.retain(|(t, s), _| t == "revision" || s == "trash");
        let a = actual_like(&e);
        assert!(matches!(
            compare(&e, &a, &Policy::standard(0)),
            Verdict::Unknown(_)
        ));
    }
}
