//! `aib-pending`(S81 p-1b / p-1c,PLAN-s81-p1-aib-pending-b433b-1114 r2):pending 的索引與查詢。
//!
//! ```text
//! aib-pending index    [--dir D]                       # 寫 D/INDEX.json(tmp + rename,INDEX.lock 鎖)
//! aib-pending list     [--dir D] [--status S] [--owner X | --seat X] [--node N] [--kind K] [--json]
//! aib-pending show     [--dir D] <檔名 | d_xxxxxx>     # 檔頭、取代鏈、被誰引用(檔名:行號)
//! aib-pending patterns [--dir D]                       # 排除表讀出來的樣式,一行一條(給 519d0 對)
//! aib-pending check    [--dir D]                       # 只報不擋(見 check.rs),一行一條
//! aib-pending new      [--dir D] --kind K --node N --owner X <檔名>   # 發 d_ id、只建新檔
//! ```
//!
//! D 預設 `<root>/pending`,root = `EZ2_AIB_HOME` 或 `~/.ez2-aib`(Windows 的 `~` = USERPROFILE)。
//! 只掃頂層檔:不進子資料夾、不跟 symlink、不掃 INDEX.json / INDEX.lock / INDEX.json.tmp。
//! 除了 index 寫 INDEX.json(與暫存檔、鎖檔)、new 建一個新檔以外不寫任何檔;不搬、不刪、不改既有檔。
//!
//! rc 0 完成 / check 沒事 · 1 show 找不到 / check 有報告 · 2 new 的檔已存在(不蓋)· 3 資料夾 / 排除表讀不到(index 這時照寫 INDEX.json、exclude_error 寫原因,仍回 3)· 4 鎖拿不到(另一個 index 在跑)或寫檔失敗 · 64 用法錯。

use aib_pending::check::{check, doc_id_from, field_ok, new_header, new_name_ok, WHY_KEYS};
use aib_pending::index::{
    chain, diff, find, moved_during_scan, refs_to, rows, Changes, Previous, Row, ScanFile,
    MAX_SCAN_BYTES, SCOPE_NOTE,
};
use aib_pending::{
    parse_exclude_table, Entry, HeaderRead, EXCLUDE_FILE, INDEX_FILE, INDEX_LOCK, INDEX_TMP,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions, TryLockError};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "\
aib-pending index    [--dir D]
aib-pending list     [--dir D] [--status S] [--owner X | --seat X] [--node N] [--kind K] [--json]
aib-pending show     [--dir D] <name | d_xxxxxx>
aib-pending patterns [--dir D]
aib-pending check    [--dir D]
aib-pending new      [--dir D] --kind K --node N --owner X <name>";

/// 大檔只讀開頭這麼多找檔頭
const HEAD_BYTES: u64 = 64 * 1024;

enum Fail {
    /// check 有報告:印到 stdout、rc 1
    Report(String),
    /// new 的檔已存在
    Exists(String),
    Usage(String),
    NotFound(String),
    Unreadable(String),
    Io(String),
}

impl Fail {
    fn rc(&self) -> u8 {
        match self {
            Fail::NotFound(_) | Fail::Report(_) => 1,
            Fail::Exists(_) => 2,
            Fail::Unreadable(_) => 3,
            Fail::Io(_) => 4,
            Fail::Usage(_) => 64,
        }
    }
    fn msg(&self) -> &str {
        match self {
            Fail::Usage(m)
            | Fail::NotFound(m)
            | Fail::Unreadable(m)
            | Fail::Io(m)
            | Fail::Report(m)
            | Fail::Exists(m) => m,
        }
    }
}

fn nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

fn default_dir() -> Result<PathBuf, Fail> {
    if let Some(h) = nonempty("EZ2_AIB_HOME") {
        return Ok(PathBuf::from(h).join("pending"));
    }
    #[cfg(windows)]
    let home = nonempty("USERPROFILE").or_else(|| nonempty("HOME"));
    #[cfg(not(windows))]
    let home = nonempty("HOME").or_else(|| nonempty("USERPROFILE"));
    home.map(|h| PathBuf::from(h).join(".ez2-aib").join("pending"))
        .ok_or_else(|| Fail::Usage("no EZ2_AIB_HOME and no home folder".into()))
}

fn now_s() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

fn mtime_s(m: &fs::Metadata) -> u64 {
    m.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs())
}

/// 不掃的自家檔
fn own_file(name: &str) -> bool {
    [INDEX_FILE, INDEX_LOCK, INDEX_TMP]
        .iter()
        .any(|f| name.eq_ignore_ascii_case(f))
}

/// 頂層一覽:檔名 → (mtime, 大小)。只有一般檔;資料夾與 symlink 另外計數。
struct Listing {
    files: BTreeMap<String, (u64, u64)>,
    dirs: usize,
    links: usize,
    odd_names: usize,
}

fn list_top(dir: &Path) -> Result<Listing, Fail> {
    let rd = fs::read_dir(dir).map_err(|e| Fail::Unreadable(format!("{}: {e}", dir.display())))?;
    let mut l = Listing {
        files: BTreeMap::new(),
        dirs: 0,
        links: 0,
        odd_names: 0,
    };
    for ent in rd {
        let ent = ent.map_err(|e| Fail::Unreadable(format!("{}: {e}", dir.display())))?;
        let Ok(name) = ent.file_name().into_string() else {
            l.odd_names += 1;
            continue;
        };
        // symlink_metadata:不跟連結
        let Ok(m) = fs::symlink_metadata(ent.path()) else {
            l.odd_names += 1;
            continue;
        };
        let ft = m.file_type();
        if ft.is_symlink() {
            l.links += 1;
        } else if ft.is_dir() {
            l.dirs += 1;
        } else if ft.is_file() && !own_file(&name) {
            l.files.insert(name, (mtime_s(&m), m.len()));
        }
    }
    Ok(l)
}

/// 讀一個檔:檔頭三態 + 要掃引用的文字
fn read_one(path: &Path, size: u64) -> (HeaderRead, Option<String>) {
    let bytes = if size > MAX_SCAN_BYTES {
        let mut buf = Vec::new();
        match File::open(path).and_then(|f| f.take(HEAD_BYTES).read_to_end(&mut buf)) {
            Ok(_) => buf,
            Err(e) => return (HeaderRead::Unreadable(e.to_string()), None),
        }
    } else {
        match fs::read(path) {
            Ok(b) => b,
            Err(e) => return (HeaderRead::Unreadable(e.to_string()), None),
        }
    };
    let body = bytes.strip_prefix("\u{feff}".as_bytes()).unwrap_or(&bytes);
    let has_header = body.starts_with(b"---");
    if size > MAX_SCAN_BYTES {
        // 只看開頭:切在字中間也沒關係,檔頭在前面
        let t = String::from_utf8_lossy(&bytes);
        let h = if has_header {
            HeaderRead::of(Ok(&t))
        } else {
            HeaderRead::Absent
        };
        return (h, None);
    }
    match String::from_utf8(bytes) {
        Ok(t) => {
            let h = HeaderRead::of(Ok(&t));
            let text = (!t.contains('\0')).then_some(t);
            (h, text)
        }
        Err(_) if has_header => (HeaderRead::Unreadable("不是 UTF-8".into()), None),
        Err(_) => (HeaderRead::Absent, None),
    }
}

struct Scan {
    files: Vec<ScanFile>,
    listing: Listing,
    skipped_large: Vec<String>,
    moved: Vec<String>,
}

fn scan(dir: &Path, read_text: bool) -> Result<Scan, Fail> {
    let listing = list_top(dir)?;
    let mut files = Vec::with_capacity(listing.files.len());
    let mut skipped_large = Vec::new();
    for (name, &(mtime, size)) in &listing.files {
        let (header, text) = read_one(&dir.join(name), size);
        if size > MAX_SCAN_BYTES {
            skipped_large.push(name.clone());
        }
        files.push(ScanFile {
            entry: Entry {
                name: name.clone(),
                is_dir: false,
                mtime,
                header,
            },
            size,
            text: if read_text { text } else { None },
        });
    }
    let after = list_top(dir)?;
    let moved = moved_during_scan(&listing.files, &after.files);
    Ok(Scan {
        files,
        listing,
        skipped_large,
        moved,
    })
}

fn opt(v: &Option<String>) -> Value {
    v.as_deref()
        .map_or(Value::Null, |s| Value::String(s.into()))
}

fn row_json(r: &Row) -> Value {
    json!({
        "name": r.name, "kind": r.kind, "prefix": r.prefix, "header": r.header,
        "id": opt(&r.id), "status": opt(&r.status), "rev": opt(&r.rev),
        "supersedes": opt(&r.supersedes), "node": opt(&r.node), "owner": opt(&r.owner),
        "mtime": r.mtime, "size": r.size, "refs": r.refs, "superseded_by": r.superseded_by,
    })
}

fn changes_json(c: &Changes) -> Value {
    json!({
        "added": c.added, "removed": c.removed, "status_changed": c.status_changed,
        "added_names": c.added_names, "removed_names": c.removed_names,
        "status_names": c.status_names.iter()
            .map(|(n, a, b)| json!({"name": n, "from": a, "to": b}))
            .collect::<Vec<_>>(),
    })
}

/// 上一份 INDEX.json 的 檔名 → 狀態;沒有 / 讀不懂 = None(changes 寫 null,不當成全部新增)
fn previous(dir: &Path) -> Option<Previous> {
    let v: Value = serde_json::from_slice(&fs::read(dir.join(INDEX_FILE)).ok()?).ok()?;
    let rows = v.get("rows")?.as_array()?;
    let mut p = Previous::new();
    for r in rows {
        let name = r.get("name")?.as_str()?;
        let status = r.get("status").and_then(Value::as_str).unwrap_or("");
        p.insert(name.into(), status.into());
    }
    Some(p)
}

/// INDEX.lock 上的獨佔鎖(File::try_lock:Unix flock、Windows LockFileEx;行程結束 OS 自動放,
/// 檔不刪)。拿不到就退,不等(ADR 0005 不睡)。
fn take_lock(dir: &Path) -> Result<File, Fail> {
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join(INDEX_LOCK))
        .map_err(|e| Fail::Io(format!("lock open: {e}")))?;
    match f.try_lock() {
        Ok(()) => Ok(f),
        Err(TryLockError::WouldBlock) => Err(Fail::Io(
            "另一個 aib-pending index / new 正在跑(INDEX.lock 拿不到)".into(),
        )),
        Err(TryLockError::Error(e)) => Err(Fail::Io(format!("lock: {e}"))),
    }
}

fn cmd_index(dir: &Path) -> Result<String, Fail> {
    let lock = take_lock(dir)?;
    let s = scan(dir, true)?;
    let r = rows(&s.files, true);
    let changes = previous(dir).map(|p| diff(&p, &r));
    let (patterns, table_error) = match fs::read_to_string(dir.join(EXCLUDE_FILE)) {
        Ok(t) => match parse_exclude_table(&t) {
            Ok(t) => (t.patterns, Value::Null),
            Err(e) => (Vec::new(), Value::String(e.to_string())),
        },
        Err(e) => (Vec::new(), Value::String(format!("{EXCLUDE_FILE}: {e}"))),
    };
    let partial = !s.moved.is_empty();
    let doc = json!({
        "version": 1,
        "generated_at": now_s(),
        "partial": partial,
        "moved_during_scan": s.moved,
        "scope": SCOPE_NOTE,
        "dirs_not_scanned": s.listing.dirs,
        "symlinks_skipped": s.listing.links,
        "unreadable_names": s.listing.odd_names,
        "skipped_large": s.skipped_large,
        "exclude_patterns": patterns,
        "exclude_error": table_error,
        "changes": changes.as_ref().map_or(Value::Null, changes_json),
        "rows": r.iter().map(row_json).collect::<Vec<_>>(),
    });
    let mut body = serde_json::to_vec_pretty(&doc).map_err(|e| Fail::Io(format!("json: {e}")))?;
    body.push(b'\n');
    let tmp = dir.join(INDEX_TMP);
    let write = || -> std::io::Result<()> {
        let mut f = File::create(&tmp)?;
        f.write_all(&body)?;
        f.sync_all()?;
        fs::rename(&tmp, dir.join(INDEX_FILE))
    };
    if let Err(e) = write() {
        // 半份的暫存檔不留在 pending(1ced7 p-1b L2)
        let _ = fs::remove_file(&tmp);
        return Err(Fail::Io(format!("write {INDEX_FILE}: {e}")));
    }
    drop(lock);
    let ch = changes.map_or("changes null(沒有上一份)".to_string(), |c| {
        format!(
            "changes +{} -{} 狀態變 {}",
            c.added, c.removed, c.status_changed
        )
    });
    let summary = format!(
        "{INDEX_FILE} rows {} · 子資料夾 {} 沒掃 · 大檔 {} 沒掃引用 · 排除樣式 {}{} · {ch} · partial {}\n{SCOPE_NOTE}",
        r.len(),
        s.listing.dirs,
        s.skipped_large.len(),
        doc["exclude_patterns"].as_array().map_or(0, Vec::len),
        if doc["exclude_error"].is_null() { "" } else { "(排除表讀不到)" },
        if partial { "yes(掃描中有檔變動)" } else { "no" },
    );
    // 排除表讀不到:索引照寫(exclude_error 寫原因),但 rc 3 —— 不當成沒有排除(1ced7 p-1b L1)
    match doc["exclude_error"].as_str() {
        Some(why) => Err(Fail::Unreadable(format!(
            "{summary}\n排除表讀不到:{why};INDEX.json 已寫但 exclude_patterns 是空的,不可當成沒有排除"
        ))),
        None => Ok(summary),
    }
}

struct Filter {
    status: Option<String>,
    owner: Option<String>,
    node: Option<String>,
    kind: Option<String>,
}

impl Filter {
    fn keep(&self, r: &Row) -> bool {
        let eq = |want: &Option<String>, got: &Option<String>| {
            want.as_ref()
                .is_none_or(|w| got.as_deref().is_some_and(|g| g.eq_ignore_ascii_case(w)))
        };
        eq(&self.status, &r.status)
            && eq(&self.owner, &r.owner)
            && eq(&self.node, &r.node)
            && self
                .kind
                .as_ref()
                .is_none_or(|k| k.eq_ignore_ascii_case(r.kind) || k.eq_ignore_ascii_case(&r.prefix))
    }
}

fn dash(v: &Option<String>) -> &str {
    v.as_deref().unwrap_or("-")
}

fn cmd_list(dir: &Path, f: &Filter, as_json: bool) -> Result<String, Fail> {
    let s = scan(dir, false)?;
    let r: Vec<Row> = rows(&s.files, false)
        .into_iter()
        .filter(|r| f.keep(r))
        .collect();
    if as_json {
        let doc = json!({
            "partial": !s.moved.is_empty(),
            "rows": r.iter().map(row_json).collect::<Vec<_>>(),
        });
        return serde_json::to_string_pretty(&doc).map_err(|e| Fail::Io(format!("json: {e}")));
    }
    let mut out = String::new();
    for x in &r {
        out.push_str(&format!(
            "{}\t{}/{}\t{}\t{}\t{}\n",
            x.name,
            x.kind,
            x.prefix,
            dash(&x.status),
            dash(&x.node),
            dash(&x.owner)
        ));
    }
    out.push_str(&format!(
        "# {} 筆{}",
        r.len(),
        if s.moved.is_empty() {
            ""
        } else {
            " · partial(掃描中有檔變動)"
        }
    ));
    Ok(out)
}

/// show 印的引用位置最多幾行
const SHOW_REFS_MAX: usize = 100;

fn cmd_show(dir: &Path, key: &str) -> Result<String, Fail> {
    let s = scan(dir, true)?;
    let r = rows(&s.files, false);
    let row = find(&r, key).ok_or_else(|| Fail::NotFound(format!("pending 頂層沒有 {key}")))?;
    let (older, newer) = chain(&r, row);
    let at = refs_to(&s.files, &row.name);
    let mut out = format!(
        "{}\n種類 {}/{} · 檔頭 {} · id {} · 狀態 {} · rev {} · node {} · owner {}\n",
        row.name,
        row.kind,
        row.prefix,
        row.header,
        dash(&row.id),
        dash(&row.status),
        dash(&row.rev),
        dash(&row.node),
        dash(&row.owner)
    );
    out.push_str(&format!("supersedes {}\n", dash(&row.supersedes)));
    out.push_str(&format!(
        "取代鏈 舊→ {} · 新→ {}\n",
        if older.is_empty() {
            "-".into()
        } else {
            older.join(" → ")
        },
        if newer.is_empty() {
            "-".into()
        } else {
            newer.join(" → ")
        }
    ));
    out.push_str(&format!("被引用 {} 處:\n", at.len()));
    for (f, line) in at.iter().take(SHOW_REFS_MAX) {
        out.push_str(&format!("  {f}:{line}\n"));
    }
    if at.len() > SHOW_REFS_MAX {
        out.push_str(&format!("  …另 {} 處\n", at.len() - SHOW_REFS_MAX));
    }
    out.push_str(SCOPE_NOTE);
    Ok(out)
}

fn cmd_patterns(dir: &Path) -> Result<String, Fail> {
    let text = fs::read_to_string(dir.join(EXCLUDE_FILE))
        .map_err(|e| Fail::Unreadable(format!("{EXCLUDE_FILE}: {e}")))?;
    let t = parse_exclude_table(&text).map_err(|e| Fail::Unreadable(e.to_string()))?;
    let mut out = t.patterns.join("\n");
    out.push_str(&format!("\n# {} 條(來源 {EXCLUDE_FILE})", t.patterns.len()));
    Ok(out)
}

fn cmd_check(dir: &Path) -> Result<String, Fail> {
    let s = scan(dir, false)?;
    let r = rows(&s.files, false);
    let why: BTreeMap<String, bool> = s
        .files
        .iter()
        .map(|f| {
            let has = match &f.entry.header {
                HeaderRead::Present(h) => WHY_KEYS
                    .iter()
                    .any(|k| h.get(k).is_some_and(|v| !v.is_empty())),
                _ => false,
            };
            (f.entry.name.clone(), has)
        })
        .collect();
    let table = fs::read_to_string(dir.join(EXCLUDE_FILE))
        .map_err(|e| format!("{EXCLUDE_FILE}: {e}"))
        .and_then(|t| parse_exclude_table(&t).map_err(|e| e.to_string()));
    let found = check(&r, table.as_ref().ok(), &why);
    let mut out: String = found.iter().map(|x| format!("{x}\n")).collect();
    out.push_str(&format!(
        "# {} 條 · 掃了 {} 檔{}",
        found.len(),
        r.len(),
        if s.moved.is_empty() {
            ""
        } else {
            " · partial(掃描中有檔變動)"
        }
    ));
    match table {
        // 排除表讀不到:報告照印,rc 3(常駐那條沒辦法比)
        Err(why) => Err(Fail::Unreadable(format!(
            "{out}\n排除表讀不到:{why}(「常駐不在排除表」這條沒比)"
        ))),
        Ok(_) if found.is_empty() => Ok(out),
        Ok(_) => Err(Fail::Report(out)),
    }
}

/// 30 位元亂數:std 隨機種子的雜湊 + pid + 時間(唯一性在鎖內對現有 id 查,不靠這裡)
fn random_bits() -> u64 {
    use std::hash::{BuildHasher, Hasher};
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u32(std::process::id());
    h.write_u128(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos()),
    );
    h.finish() & ((1 << 30) - 1)
}

fn cmd_new(dir: &Path, name: &str, kind: &str, node: &str, owner: &str) -> Result<String, Fail> {
    new_name_ok(name, kind).map_err(Fail::Usage)?;
    if !field_ok(node) || !field_ok(owner) {
        return Err(Fail::Usage(
            "--node / --owner 不能空、不能換行、不能有 #".into(),
        ));
    }
    // 跟 index 同一把鎖:兩個 new 同時發不會撞號
    let held = take_lock(dir)?;
    let s = scan(dir, false)?;
    let taken: std::collections::BTreeSet<String> = rows(&s.files, false)
        .into_iter()
        .filter_map(|r| r.id)
        .collect();
    let id = loop {
        let id = doc_id_from(random_bits());
        if !taken.contains(&id) {
            break id;
        }
    };
    let path = dir.join(name);
    // 已存在就拒絕只靠 create_new(原子;Mac / Windows 的檔案系統本來就不分大小寫,大小寫不同也算已存在)
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::AlreadyExists => Fail::Exists(format!("{name} 已存在,不蓋")),
            _ => Fail::Io(format!("create {name}: {e}")),
        })?;
    if let Err(e) = f
        .write_all(new_header(&id, node, owner).as_bytes())
        .and_then(|()| f.sync_all())
    {
        // 這個檔是這次 create_new 建的、還沒寫完:刪掉,不留半份檔頭擋住下次同名(1ced7 p-1c L2)
        drop(f);
        let _ = fs::remove_file(&path);
        return Err(Fail::Io(format!("write {name}: {e}(剛建的檔已刪)")));
    }
    drop(held);
    Ok(format!("{id} {name}"))
}

fn run(args: &[String]) -> Result<String, Fail> {
    let Some((cmd, rest)) = args.split_first() else {
        return Err(Fail::Usage(USAGE.into()));
    };
    let mut dir: Option<PathBuf> = None;
    let mut f = Filter {
        status: None,
        owner: None,
        node: None,
        kind: None,
    };
    let mut as_json = false;
    let mut pos: Vec<&String> = Vec::new();
    let mut it = rest.iter();
    while let Some(a) = it.next() {
        let mut val = |what: &str| {
            it.next()
                .cloned()
                .ok_or_else(|| Fail::Usage(format!("{what} 要一個值\n{USAGE}")))
        };
        match a.as_str() {
            "--dir" => dir = Some(PathBuf::from(val("--dir")?)),
            "--status" => f.status = Some(val("--status")?),
            "--owner" | "--seat" => f.owner = Some(val("--owner")?),
            "--node" => f.node = Some(val("--node")?),
            "--kind" => f.kind = Some(val("--kind")?),
            "--json" => as_json = true,
            s if s.starts_with("--") => return Err(Fail::Usage(format!("不認得 {s}\n{USAGE}"))),
            _ => pos.push(a),
        }
    }
    let dir = match dir {
        Some(d) => d,
        None => default_dir()?,
    };
    let filtered = f.status.is_some() || f.owner.is_some() || f.node.is_some() || f.kind.is_some();
    match cmd.as_str() {
        "index" if pos.is_empty() && !filtered && !as_json => cmd_index(&dir),
        "list" if pos.is_empty() => cmd_list(&dir, &f, as_json),
        "show" if pos.len() == 1 && !filtered && !as_json => cmd_show(&dir, pos[0]),
        "patterns" if pos.is_empty() && !filtered && !as_json => cmd_patterns(&dir),
        "check" if pos.is_empty() && !filtered && !as_json => cmd_check(&dir),
        "new" if pos.len() == 1 && f.status.is_none() && !as_json => {
            match (&f.kind, &f.node, &f.owner) {
                (Some(k), Some(n), Some(o)) => cmd_new(&dir, pos[0], k, n, o),
                _ => Err(Fail::Usage(format!(
                    "new 要 --kind --node --owner 三個都給\n{USAGE}"
                ))),
            }
        }
        "-h" | "--help" | "help" => Ok(USAGE.into()),
        _ => Err(Fail::Usage(USAGE.into())),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(out) => {
            println!("{out}");
            ExitCode::SUCCESS
        }
        Err(Fail::Report(out)) => {
            println!("{out}");
            ExitCode::from(1)
        }
        Err(e) => {
            eprintln!("aib-pending: {}", e.msg());
            ExitCode::from(e.rc())
        }
    }
}
