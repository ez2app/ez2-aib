// SPDX-License-Identifier: Apache-2.0
//! aib-signlint — read one sign-off script (`SIGN-*.sh`, run on a host after a person's
//! consent) and say where it can fail without saying so.
//!
//! Every rule answers **yes** (the shape is there), or **unknown** (this reader cannot
//! tell); a rule with nothing to say is silent. Unknown is not a pass: a script that cannot
//! be read is not run (DEV 4.6 r2 §10 #3: "have / have not / do not know; do not know ⇒ do
//! not change").
//!
//! | code | what |
//! |---|---|
//! | S1 | under `pipefail`, a pipeline whose last stage stops early (`grep -q`, `grep -m`, `head`): the stage before it can die of SIGPIPE, and a match reads as a failure |
//! | S2 | a failure swallowed: `\|\| true`, `\|\| :`, `a && b \|\| c`, `set +e` never undone, a command left unchecked while `set -e` is off |
//! | S3 | only the output is looked at: `local`/`export x=$(…)`, `$(…)` inside `[ ]`, `for … in $(…)`, `x=$(…)` unchecked while `set -e` is off |
//! | S4 | "could not ask" taken as "is not there": a call through ssh used as a condition with no separate 255, or, on a restore path, a missing backup that is skipped |
//! | S5 | a restore path that writes files and never reads them back (`cmp`, `sha256sum`, `diff`) |
//! | U1 | this reader cannot follow the script: `eval`, `source`, `bash -c "$x"`, a quote or heredoc left open |
//! | R1 | no `# 可丟環境: <where> 收據: <receipt>` line in the first 30 lines |
//!
//! S1–S5 can be acknowledged by name (`--ack`); U1 and R1 cannot.
//!
//! The reader is a shape reader, not a shell: it masks quoted text, follows heredocs (a
//! heredoc fed to `bash -s` / `sh -s` is read as a script of its own, with its own `set`
//! state), and knows functions, `if … fi` and `&&` / `||` lists. What it cannot place, it
//! reports as unknown or leaves to the person reading the script.

use std::collections::BTreeSet;

/// What a finding says about its rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// the shape is there
    Yes,
    /// this reader cannot tell
    Unknown,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Yes => "yes",
            State::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub rule: &'static str,
    /// 1-based line in the file given (a heredoc body is placed at its own lines)
    pub line: usize,
    pub state: State,
    pub msg: String,
}

/// Rule code, whether `--ack` may name it, one line on what it is.
pub const RULES: [(&str, bool, &str); 7] = [
    (
        "S1",
        true,
        "pipefail 下管線最後一節提早收工(grep -q / grep -m / head)",
    ),
    (
        "S2",
        true,
        "失敗被吞掉(|| true、|| :、&& … ||、set +e、set -e 關著時沒檢查)",
    ),
    (
        "S3",
        true,
        "只看輸出不看結束碼(local/export x=$(…)、[ $(…) ]、for … in $(…))",
    ),
    (
        "S4",
        true,
        "把「問不到」當「不存在」(ssh 255 沒分開;還原路徑上備份不在就跳過)",
    ),
    ("S5", true, "還原寫了檔卻沒讀回來比(cmp / sha256sum / diff)"),
    (
        "U1",
        false,
        "讀不懂(eval、source、bash -c \"$x\"、引號或 heredoc 沒收)",
    ),
    ("R1", false, "開頭 30 行沒有「# 可丟環境: … 收據: …」"),
];

/// Whether `--ack` may name `code`.
pub fn ackable(code: &str) -> bool {
    RULES.iter().any(|(c, a, _)| *c == code && *a)
}

/// Where the rehearsal was run and its receipt, from the `# 可丟環境: … 收據: …` line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rehearsal {
    pub env: String,
    pub receipt: String,
}

/// The rehearsal line in the first 30 lines, if any (empty or placeholder values do not count).
pub fn rehearsal(text: &str) -> Option<Rehearsal> {
    for l in text.lines().take(30) {
        let Some(rest) = l.trim_start().strip_prefix('#') else {
            continue;
        };
        let rest = rest.trim();
        let (env, receipt) = if let Some(r) = rest.strip_prefix("可丟環境:") {
            match r.split_once("收據:") {
                Some(p) => p,
                None => continue,
            }
        } else if let Some(r) = rest.strip_prefix("rehearsal:") {
            match r.split_once("receipt:") {
                Some(p) => p,
                None => continue,
            }
        } else {
            continue;
        };
        let (env, receipt) = (env.trim(), receipt.trim());
        let empty =
            |s: &str| s.is_empty() || matches!(s, "-" | "—" | "無" | "none" | "n/a" | "TODO" | "?");
        if !empty(env) && !empty(receipt) {
            return Some(Rehearsal {
                env: env.to_owned(),
                receipt: receipt.to_owned(),
            });
        }
    }
    None
}

/// Lint one script. `name` is its file name (a name with revert / rollback / restore / undo
/// makes the whole script a restore path).
pub fn lint(text: &str, name: &str) -> Vec<Finding> {
    let mut out = Vec::new();
    if rehearsal(text).is_none() {
        out.push(Finding {
            rule: "R1",
            line: 1,
            state: State::Yes,
            msg: "開頭 30 行沒有「# 可丟環境: <哪一台> 收據: <收據>」".to_owned(),
        });
    }
    let whole_restore = is_restore_name(name);
    lint_script(text, 0, whole_restore, &mut out);
    out.sort_by(|a, b| (a.line, a.rule).cmp(&(b.line, b.rule)));
    out.dedup();
    out
}

fn is_restore_name(name: &str) -> bool {
    let n = name.to_lowercase();
    [
        "restore", "rollback", "revert", "undo", "putback", "put_back", "還原", "回滾",
    ]
    .iter()
    .any(|w| n.contains(w))
}

// ---------------------------------------------------------------- reading

/// One logical line: comments gone, quoted text masked (`code`), and as written (`raw`).
#[derive(Debug, Clone)]
struct Logical {
    line: usize,
    code: String,
    raw: String,
}

#[derive(Debug, Clone)]
struct Heredoc {
    /// index of the logical line that opened it
    owner: usize,
    /// 1-based line of the body's first line
    body_line: usize,
    body: String,
    shell: bool,
}

struct Lexed {
    lines: Vec<Logical>,
    docs: Vec<Heredoc>,
    open: Vec<(usize, String)>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Ctx {
    Normal,
    Single,
    AnsiSingle,
    Double,
    /// `$(` … `)` (inside double quotes or not), with its paren depth
    Sub(u32),
    Backtick,
}

fn is_word_start(prev: Option<char>) -> bool {
    match prev {
        None => true,
        Some(c) => c.is_whitespace() || matches!(c, ';' | '&' | '|' | '(' | ')'),
    }
}

fn lex(text: &str) -> Lexed {
    let phys: Vec<&str> = text.split('\n').collect();
    let mut lines = Vec::new();
    let mut docs = Vec::new();
    let mut open = Vec::new();
    let mut stack: Vec<Ctx> = vec![Ctx::Normal];
    let mut code = String::new();
    let mut raw = String::new();
    let mut start = 1usize;
    let mut pending: Vec<(bool, String)> = Vec::new(); // (dash, delimiter)
    let mut i = 0usize;
    while i < phys.len() {
        let line = phys[i];
        if code.is_empty() && raw.is_empty() {
            start = i + 1;
        }
        let chars: Vec<char> = line.chars().collect();
        let mut j = 0usize;
        let mut continued = false;
        while j < chars.len() {
            let c = chars[j];
            let top = *stack.last().unwrap_or(&Ctx::Normal);
            raw.push(c);
            match top {
                Ctx::Single => {
                    if c == '\'' {
                        stack.pop();
                        code.push('\'');
                    } else {
                        code.push('_');
                    }
                }
                Ctx::AnsiSingle => {
                    if c == '\\' && j + 1 < chars.len() {
                        code.push_str("__");
                        raw.push(chars[j + 1]);
                        j += 1;
                    } else if c == '\'' {
                        stack.pop();
                        code.push('\'');
                    } else {
                        code.push('_');
                    }
                }
                Ctx::Double => {
                    if c == '\\' && j + 1 < chars.len() {
                        code.push_str("__");
                        raw.push(chars[j + 1]);
                        j += 1;
                    } else if c == '"' {
                        stack.pop();
                        code.push('"');
                    } else if c == '$' && chars.get(j + 1) == Some(&'(') {
                        code.push_str("$(");
                        raw.push('(');
                        j += 1;
                        stack.push(Ctx::Sub(1));
                    } else if c == '`' {
                        code.push('`');
                        stack.push(Ctx::Backtick);
                    } else if c.is_whitespace() || "|&;<>()".contains(c) {
                        code.push('_');
                    } else {
                        code.push(c);
                    }
                }
                Ctx::Normal | Ctx::Sub(_) | Ctx::Backtick => {
                    let prev = code.chars().last();
                    if c == '\\' {
                        if j + 1 < chars.len() {
                            code.push_str("__");
                            raw.push(chars[j + 1]);
                            j += 1;
                        } else {
                            // line continuation
                            raw.pop();
                            continued = true;
                        }
                    } else if c == '#' && is_word_start(prev) {
                        // comment: the rest of the line
                        raw.pop();
                        break;
                    } else if c == '\'' {
                        if prev == Some('$') {
                            stack.push(Ctx::AnsiSingle);
                        } else {
                            stack.push(Ctx::Single);
                        }
                        code.push('\'');
                    } else if c == '"' {
                        stack.push(Ctx::Double);
                        code.push('"');
                    } else if c == '`' {
                        if top == Ctx::Backtick {
                            stack.pop();
                        } else {
                            stack.push(Ctx::Backtick);
                        }
                        code.push('`');
                    } else if c == '(' {
                        if let Ctx::Sub(d) = top {
                            if let Some(t) = stack.last_mut() {
                                *t = Ctx::Sub(d + 1);
                            }
                        } else if prev == Some('$') {
                            stack.push(Ctx::Sub(1));
                        }
                        code.push('(');
                    } else if c == ')' {
                        if let Ctx::Sub(d) = top {
                            if d <= 1 {
                                stack.pop();
                            } else if let Some(t) = stack.last_mut() {
                                *t = Ctx::Sub(d - 1);
                            }
                        }
                        code.push(')');
                    } else if c == '<'
                        && chars.get(j + 1) == Some(&'<')
                        && chars.get(j + 2) != Some(&'<')
                    {
                        // heredoc: <<[-] ['"]DELIM['"]
                        let mut k = j + 2;
                        let dash = chars.get(k) == Some(&'-');
                        if dash {
                            k += 1;
                        }
                        while chars.get(k).is_some_and(|c| *c == ' ' || *c == '\t') {
                            k += 1;
                        }
                        let q = chars.get(k).copied().filter(|c| *c == '\'' || *c == '"');
                        if q.is_some() {
                            k += 1;
                        }
                        let mut delim = String::new();
                        while let Some(&d) = chars.get(k) {
                            if d.is_alphanumeric() || d == '_' || d == '-' && !delim.is_empty() {
                                delim.push(d);
                                k += 1;
                            } else {
                                break;
                            }
                        }
                        if let Some(q) = q {
                            if chars.get(k) == Some(&q) {
                                k += 1;
                            }
                        }
                        if delim.is_empty() {
                            code.push(c);
                        } else {
                            code.push_str("<<");
                            code.push_str(&delim);
                            raw.extend(&chars[j + 1..k]);
                            pending.push((dash, delim));
                            j = k;
                            continue;
                        }
                    } else {
                        code.push(c);
                    }
                }
            }
            j += 1;
        }
        i += 1;
        let quoted_open = stack.len() > 1;
        if continued {
            code.push(' ');
            raw.push(' ');
            continue;
        }
        if quoted_open {
            // a quote or $( ) spanning lines: same logical line
            code.push(' ');
            raw.push('\n');
            continue;
        }
        let owner = lines.len();
        lines.push(Logical {
            line: start,
            code: std::mem::take(&mut code),
            raw: std::mem::take(&mut raw),
        });
        for (dash, delim) in std::mem::take(&mut pending) {
            let body_line = i + 1;
            let mut body = String::new();
            let mut closed = false;
            while i < phys.len() {
                let l = phys[i];
                i += 1;
                let t = if dash { l.trim_start_matches('\t') } else { l };
                if t == delim {
                    closed = true;
                    break;
                }
                body.push_str(l);
                body.push('\n');
            }
            if !closed {
                open.push((
                    body_line.saturating_sub(1),
                    format!("heredoc {delim} 沒有結束行"),
                ));
            }
            let shell = feeds_shell(&lines[owner].raw);
            docs.push(Heredoc {
                owner,
                body_line,
                body,
                shell,
            });
        }
    }
    if stack.len() > 1 || !code.trim().is_empty() {
        open.push((start, "引號或 $( ) 到檔尾都沒收".to_owned()));
        lines.push(Logical {
            line: start,
            code,
            raw,
        });
    }
    Lexed { lines, docs, open }
}

/// Whether the command that owns a heredoc is a shell reading it (`bash -s`, `sh`, `sudo bash -euo pipefail`).
fn feeds_shell(raw: &str) -> bool {
    let words: Vec<&str> = raw
        .split(|c: char| c.is_whitespace() || c == '"' || c == '\'' || c == ';')
        .filter(|w| !w.is_empty())
        .collect();
    for (n, w) in words.iter().enumerate() {
        let base = w.rsplit('/').next().unwrap_or(w);
        if matches!(base, "bash" | "sh" | "dash" | "zsh") {
            let rest = &words[n + 1..];
            let ok = rest
                .iter()
                .take_while(|w| !w.starts_with("<<"))
                .all(|w| w.starts_with('-') || matches!(*w, "pipefail" | "errexit" | "nounset"));
            if ok {
                return true;
            }
        }
    }
    false
}

// ---------------------------------------------------------------- statements

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Op {
    And,
    Or,
}

#[derive(Debug, Clone, Default)]
struct Stage {
    words: Vec<String>,
    /// after `if` / `elif` / `while` / `until` / `!`
    cond: bool,
    negated: bool,
    looped: bool,
    /// the command name (basename), after keywords, assignments and sudo / env / timeout
    cmd: String,
    args: Vec<String>,
    assign_only: bool,
    text: String,
}

#[derive(Debug, Clone)]
struct Elem {
    stages: Vec<Stage>,
}

#[derive(Debug, Clone)]
struct Stmt {
    line: usize,
    /// index of its logical line
    lix: usize,
    raw: String,
    code: String,
    elems: Vec<Elem>,
    ops: Vec<Op>,
    /// first word as written (`then`, `else`, `fi`, `}` …)
    head: String,
}

impl Stmt {
    fn first(&self) -> Option<&Stage> {
        self.elems.first().and_then(|e| e.stages.first())
    }
}

/// Split code at depth 0 (parens, braces) on `;`, `&`, newline-equivalents; keep `&&`/`||`/`|`.
fn split_list(code: &str) -> Vec<String> {
    let chars: Vec<char> = code.chars().collect();
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut paren = 0i32;
    let mut brace = 0i32;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let prev_ws = i == 0 || chars[i - 1].is_whitespace() || chars[i - 1] == ';';
        let next_ws = i + 1 >= chars.len() || chars[i + 1].is_whitespace() || chars[i + 1] == ';';
        match c {
            '(' => paren += 1,
            ')' => paren = (paren - 1).max(0),
            '{' if prev_ws && next_ws => brace += 1,
            '}' if prev_ws => brace = (brace - 1).max(0),
            _ => {}
        }
        let top = paren == 0 && brace == 0;
        if top && c == ';' {
            if chars.get(i + 1) == Some(&';') {
                i += 1;
            }
            out.push(std::mem::take(&mut cur));
        } else if top
            && c == '&'
            && chars.get(i + 1) != Some(&'&')
            && (i == 0 || chars[i - 1] != '&')
            && chars.get(i + 1) != Some(&'>')
            && (i == 0 || chars[i - 1] != '>')
        {
            out.push(std::mem::take(&mut cur));
        } else {
            cur.push(c);
        }
        i += 1;
    }
    out.push(cur);
    out.into_iter().filter(|s| !s.trim().is_empty()).collect()
}

/// Split one list item into and-or elements and pipeline stages, at paren / brace depth 0.
fn split_andor(code: &str) -> (Vec<Vec<String>>, Vec<Op>) {
    let chars: Vec<char> = code.chars().collect();
    let mut elems: Vec<Vec<String>> = vec![vec![]];
    let mut ops = Vec::new();
    let mut cur = String::new();
    let mut paren = 0i32;
    let mut brace = 0i32;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let prev_ws = i == 0 || chars[i - 1].is_whitespace();
        let next_ws = i + 1 >= chars.len() || chars[i + 1].is_whitespace() || chars[i + 1] == ';';
        match c {
            '(' => paren += 1,
            ')' => paren = (paren - 1).max(0),
            '{' if prev_ws && next_ws => brace += 1,
            '}' if prev_ws => brace = (brace - 1).max(0),
            _ => {}
        }
        let top = paren == 0 && brace == 0;
        let n = chars.get(i + 1).copied();
        if top && c == '&' && n == Some('&') {
            if let Some(e) = elems.last_mut() {
                e.push(std::mem::take(&mut cur));
            }
            elems.push(vec![]);
            ops.push(Op::And);
            i += 2;
            continue;
        }
        if top && c == '|' && n == Some('|') {
            if let Some(e) = elems.last_mut() {
                e.push(std::mem::take(&mut cur));
            }
            elems.push(vec![]);
            ops.push(Op::Or);
            i += 2;
            continue;
        }
        if top && c == '|' && (i == 0 || chars[i - 1] != '>') {
            if let Some(e) = elems.last_mut() {
                e.push(std::mem::take(&mut cur));
            }
            i += if n == Some('&') { 2 } else { 1 };
            continue;
        }
        cur.push(c);
        i += 1;
    }
    if let Some(e) = elems.last_mut() {
        e.push(cur);
    }
    (elems, ops)
}

fn is_assignment(w: &str) -> bool {
    let Some((name, _)) = w.split_once('=') else {
        return false;
    };
    let name = name.strip_suffix('+').unwrap_or(name);
    !name.is_empty()
        && name
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn stage_of(text: &str) -> Stage {
    let words: Vec<String> = text.split_whitespace().map(str::to_owned).collect();
    let mut s = Stage {
        words: words.clone(),
        text: text.trim().to_owned(),
        ..Stage::default()
    };
    let mut k = 0;
    while k < words.len() {
        let w = words[k].as_str();
        match w {
            "if" | "elif" => s.cond = true,
            "while" | "until" => {
                s.cond = true;
                s.looped = true;
            }
            "!" => {
                s.cond = true;
                s.negated = true;
            }
            "then" | "do" | "else" | "{" | "(" | "time" | "exec" => {}
            _ if w.ends_with(')') && !w.contains('(') && !w.contains('$') => {} // a case pattern
            _ => break,
        }
        k += 1;
    }
    let mut assigns = 0;
    while k < words.len() && is_assignment(&words[k]) {
        assigns += 1;
        if words[k].contains("=(") {
            // an array: the rest of the line is its items
            s.assign_only = true;
            return s;
        }
        k += 1;
    }
    if k >= words.len() {
        s.assign_only = assigns > 0;
        return s;
    }
    // wrappers that run the next word
    loop {
        let w = words.get(k).map(String::as_str).unwrap_or("");
        let base = w.rsplit('/').next().unwrap_or(w);
        match base {
            "sudo" | "doas" => {
                k += 1;
                while words.get(k).is_some_and(|w| w.starts_with('-')) {
                    let takes = matches!(words[k].as_str(), "-u" | "-g" | "-C" | "-D");
                    k += if takes { 2 } else { 1 };
                }
            }
            "command" | "nohup" | "nice" | "builtin" => k += 1,
            "env" => {
                k += 1;
                while words
                    .get(k)
                    .is_some_and(|w| w.starts_with('-') || is_assignment(w))
                {
                    k += 1;
                }
            }
            "timeout" => {
                k += 1;
                while words.get(k).is_some_and(|w| w.starts_with('-')) {
                    k += 1;
                }
                k += 1; // the duration
            }
            _ => break,
        }
    }
    if let Some(w) = words.get(k) {
        s.cmd = w.rsplit('/').next().unwrap_or(w).to_owned();
        s.args = words[k + 1..].to_vec();
    }
    s
}

fn stmts_of(lines: &[Logical]) -> Vec<Stmt> {
    let mut out = Vec::new();
    for (lix, l) in lines.iter().enumerate() {
        for item in split_list(&l.code) {
            let (els, ops) = split_andor(&item);
            let elems: Vec<Elem> = els
                .iter()
                .map(|st| Elem {
                    stages: st.iter().map(|t| stage_of(t.as_str())).collect(),
                })
                .collect();
            let head = item.split_whitespace().next().unwrap_or("").to_owned();
            out.push(Stmt {
                line: l.line,
                lix,
                raw: l.raw.clone(),
                code: item,
                elems,
                ops,
                head,
            });
        }
    }
    out
}

// ---------------------------------------------------------------- the script's shape

struct Func {
    name: String,
    /// logical lines [from, to] (both inclusive)
    from: usize,
    to: usize,
}

fn func_name(code: &str) -> Option<String> {
    let t = code.trim_start();
    if let Some(r) = t.strip_prefix("function ") {
        let n: String = r
            .trim_start()
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '-')
            .collect();
        return (!n.is_empty()).then_some(n);
    }
    let n: String = t
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_' || *c == '-')
        .collect();
    if n.is_empty() {
        return None;
    }
    let rest = t[n.len()..].trim_start();
    rest.starts_with("()").then_some(n)
}

fn brace_delta(code: &str) -> i32 {
    let mut d = 0;
    for w in code.split(|c: char| c.is_whitespace() || c == ';') {
        if w == "{" {
            d += 1;
        } else if w == "}" || w.starts_with('}') && !w.contains('{') && w.len() <= 3 {
            d -= 1;
        }
    }
    d
}

fn funcs_of(lines: &[Logical]) -> Vec<Func> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        if let Some(name) = func_name(&lines[i].code) {
            let mut depth = brace_delta(&lines[i].code);
            let mut j = i;
            // the `{` may be on the next line
            if depth == 0 && !lines[i].code.contains('{') && i + 1 < lines.len() {
                j += 1;
                depth += brace_delta(&lines[j].code);
            }
            while depth > 0 && j + 1 < lines.len() {
                j += 1;
                depth += brace_delta(&lines[j].code);
            }
            out.push(Func {
                name,
                from: i,
                to: j,
            });
            i = j + 1;
        } else {
            i += 1;
        }
    }
    out
}

fn raw_words(raw: &str) -> Vec<String> {
    raw.split(|c: char| c.is_whitespace() || "\"';|&(){}".contains(c))
        .filter(|w| !w.is_empty())
        .map(|w| w.rsplit('/').next().unwrap_or(w).to_owned())
        .collect()
}

/// Names that call another host: functions that run ssh, variables that hold ssh.
struct Shape {
    funcs: Vec<Func>,
    remote_funcs: BTreeSet<String>,
    remote_vars: BTreeSet<String>,
    stop_funcs: BTreeSet<String>,
}

fn shape_of(lines: &[Logical]) -> Shape {
    let funcs = funcs_of(lines);
    let mut remote_vars = BTreeSet::new();
    for l in lines {
        for w in l.raw.split_whitespace() {
            if let Some((name, val)) = w.split_once('=') {
                let val = val.trim_start_matches(['"', '\'', '(']);
                if is_assignment(w) && (val == "ssh" || val.starts_with("ssh")) {
                    remote_vars.insert(name.to_owned());
                }
            }
        }
    }
    let body = |f: &Func| -> Vec<String> {
        let mut ws = Vec::new();
        for l in &lines[f.from..=f.to] {
            ws.extend(raw_words(&l.raw));
        }
        ws
    };
    let mut remote_funcs = BTreeSet::new();
    let mut stop_funcs = BTreeSet::new();
    loop {
        let mut changed = false;
        for f in &funcs {
            let ws = body(f);
            let remote = ws.iter().any(|w| {
                w == "ssh"
                    || remote_funcs.contains(w)
                    || remote_vars
                        .iter()
                        .any(|v| *w == format!("${v}") || *w == format!("${{{v}}}"))
            });
            if remote && remote_funcs.insert(f.name.clone()) {
                changed = true;
            }
            let stops = ws.iter().any(|w| w == "exit");
            if stops && stop_funcs.insert(f.name.clone()) {
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    Shape {
        funcs,
        remote_funcs,
        remote_vars,
        stop_funcs,
    }
}

impl Shape {
    fn func_at(&self, lix: usize) -> Option<&Func> {
        self.funcs
            .iter()
            .filter(|f| f.from <= lix && lix <= f.to)
            .min_by_key(|f| f.to - f.from)
    }

    fn is_remote_word(&self, w: &str) -> bool {
        let w = w.trim_matches('"');
        if w == "ssh" || self.remote_funcs.contains(w) {
            return true;
        }
        let v = w
            .strip_prefix("${")
            .and_then(|v| v.strip_suffix('}'))
            .or_else(|| w.strip_prefix('$'));
        v.is_some_and(|v| {
            let v = v.split('[').next().unwrap_or(v);
            self.remote_vars.contains(v)
        })
    }

    fn is_remote(&self, st: &Stage) -> bool {
        if self.is_remote_word(&st.cmd) {
            return true;
        }
        // `"${SSH[@]}"` or `$SSH` as the first word
        st.words
            .iter()
            .find(|w| !matches!(w.as_str(), "if" | "elif" | "!" | "then" | "while" | "until"))
            .is_some_and(|w| self.is_remote_word(w))
    }

    /// The first word of `$(` … in an assignment calls another host.
    fn assigns_remote(&self, st: &Stage) -> bool {
        st.words.iter().any(|w| {
            w.split_once("$(")
                .is_some_and(|(_, r)| !r.is_empty() && self.is_remote_word(r))
        }) || st
            .words
            .windows(2)
            .any(|p| p[0].ends_with("$(") && self.is_remote_word(&p[1]))
    }

    fn is_stop(&self, st: &Stage) -> bool {
        match st.cmd.as_str() {
            "exit" => st.args.first().map(String::as_str) != Some("0"),
            "return" => st.args.first().is_some_and(|a| a != "0"),
            "false" | "die" | "abort" | "fail" | "bail" => true,
            c => self.stop_funcs.contains(c),
        }
    }

    /// An element stops the script (or the function) when it fails, by its first stage or any
    /// stage of a `{ …; exit 1; }` group.
    fn elem_stops(&self, e: &Elem) -> bool {
        let Some(st) = e.stages.first() else {
            return false;
        };
        if self.is_stop(st) {
            return true;
        }
        if st.text.starts_with('{') || st.text.starts_with('(') {
            let inner = st
                .text
                .trim_start_matches(['{', '('])
                .trim_end_matches(['}', ')']);
            return split_list(inner).iter().any(|item| {
                let (els, _) = split_andor(item);
                els.iter().flatten().any(|t| self.is_stop(&stage_of(t)))
            });
        }
        false
    }
}

// ---------------------------------------------------------------- rules

const PRINTERS: &[&str] = &["echo", "printf", "log", ":", "true", "say"];

const UNCHECKED_OK: &[&str] = &[
    "echo",
    "printf",
    "log",
    ":",
    "true",
    "false",
    "exit",
    "return",
    "set",
    "shift",
    "trap",
    "local",
    "declare",
    "typeset",
    "export",
    "readonly",
    "unset",
    "wait",
    "sleep",
    "break",
    "continue",
    "[",
    "[[",
    "test",
    "fi",
    "done",
    "esac",
    "then",
    "else",
    "do",
    "read",
    "say",
    "case",
    "for",
    "select",
    "function",
    "umask",
    "cd_or_die",
];

fn is_test_cmd(c: &str) -> bool {
    matches!(c, "[" | "[[" | "test")
}

fn has_subst(s: &str) -> bool {
    s.contains("$(") || s.contains('`')
}

fn uses_status(s: &Stmt) -> bool {
    s.code.contains("$?") || s.code.contains("PIPESTATUS")
}

/// `set` words: pipefail / errexit turned on or off.
fn set_change(st: &Stage) -> (Option<bool>, Option<bool>) {
    if st.cmd != "set" {
        return (None, None);
    }
    let mut pipefail = None;
    let mut errexit = None;
    let mut it = st.args.iter().peekable();
    while let Some(a) = it.next() {
        let on = a.starts_with('-');
        let off = a.starts_with('+');
        if !on && !off {
            continue;
        }
        let flags = &a[1..];
        if flags == "o" || flags.ends_with('o') {
            if flags.contains('e') {
                errexit = Some(on);
            }
            if let Some(n) = it.peek() {
                match n.as_str() {
                    "pipefail" => {
                        pipefail = Some(on);
                        it.next();
                    }
                    "errexit" => {
                        errexit = Some(on);
                        it.next();
                    }
                    _ => {}
                }
            }
        } else if flags.contains('e') {
            errexit = Some(on);
        }
    }
    (pipefail, errexit)
}

fn early_stop(st: &Stage) -> bool {
    match st.cmd.as_str() {
        "head" => true,
        "grep" | "egrep" | "fgrep" => st.args.iter().any(|a| {
            a == "--quiet"
                || a == "--silent"
                || a.starts_with("--max-count")
                || a.starts_with('-')
                    && !a.starts_with("--")
                    && (a.contains('q') || a.contains('m'))
        }),
        _ => false,
    }
}

struct Script<'a> {
    shape: &'a Shape,
    stmts: &'a [Stmt],
    lines: &'a [Logical],
    offset: usize,
    whole_restore: bool,
}

impl Script<'_> {
    fn restore_at(&self, lix: usize) -> bool {
        self.whole_restore
            || self
                .shape
                .func_at(lix)
                .is_some_and(|f| is_restore_name(&f.name))
    }

    fn at(&self, s: &Stmt) -> usize {
        s.line + self.offset
    }

    /// `255` handled within this statement or the next six.
    fn handles_255(&self, i: usize) -> bool {
        self.stmts[i..self.stmts.len().min(i + 7)]
            .iter()
            .any(|s| s.raw.contains("255"))
    }

    /// The statement ends the body of the function it is in (its failure is the caller's).
    fn returns_to_caller(&self, i: usize) -> bool {
        let s = &self.stmts[i];
        let Some(f) = self.shape.func_at(s.lix) else {
            return false;
        };
        match self.stmts.get(i + 1) {
            None => true,
            Some(n) => n.head.starts_with('}') || n.lix > f.to,
        }
    }

    /// The `else` of the `if` that statement `i` opens, if any.
    fn else_of(&self, i: usize) -> Option<usize> {
        let mut depth = 0;
        for (j, s) in self.stmts.iter().enumerate().skip(i + 1) {
            let first = s.code.split_whitespace().next().unwrap_or("");
            let first = if first == "then" {
                s.code.split_whitespace().nth(1).unwrap_or("")
            } else {
                first
            };
            match first {
                "if" => depth += 1,
                "fi" if depth == 0 => return None,
                "fi" => depth -= 1,
                "else" | "elif" if depth == 0 => return Some(j),
                _ => {}
            }
        }
        None
    }

    /// Whether the statement after `then` (or the `else` branch) stops.
    fn branch_stops(&self, j: usize) -> bool {
        let mut j = j;
        while let Some(s) = self.stmts.get(j) {
            // a bare `then` / `else` line: the branch starts on the next one
            if s.first().is_some_and(|f| f.cmd.is_empty()) {
                j += 1;
                continue;
            }
            return s.elems.first().is_some_and(|e| self.shape.elem_stops(e));
        }
        false
    }

    /// What happens when element `k` of statement `i` fails: it stops (true), or the script
    /// goes on as if the answer were "no" (false).
    fn failure_stops(&self, i: usize, k: usize, errexit: bool) -> bool {
        let s = &self.stmts[i];
        // the next `||` after k takes the failure
        for (n, op) in s.ops.iter().enumerate().skip(k) {
            if *op == Op::Or {
                return s.elems.get(n + 1).is_some_and(|e| self.shape.elem_stops(e));
            }
        }
        let first = s.elems[k].stages.first();
        if k == 0 && first.is_some_and(|f| f.cond && !f.looped) {
            let f = first.cloned().unwrap_or_default();
            if f.negated {
                // `if ! probe; then <this>`
                return self.branch_stops(i + 1);
            }
            // `if probe; then …; else <this>`
            return self.else_of(i).is_some_and(|e| self.branch_stops(e));
        }
        // no `||`: under set -e only the last element of the list stops the script
        errexit && k + 1 == s.elems.len() && !first.is_some_and(|f| f.cond)
    }
}

fn lint_script(text: &str, offset: usize, whole_restore: bool, out: &mut Vec<Finding>) {
    let lexed = lex(text);
    for (line, why) in &lexed.open {
        out.push(Finding {
            rule: "U1",
            line: line + offset,
            state: State::Unknown,
            msg: why.clone(),
        });
    }
    let shape = shape_of(&lexed.lines);
    let stmts = stmts_of(&lexed.lines);
    let cx = Script {
        shape: &shape,
        stmts: &stmts,
        lines: &lexed.lines,
        offset,
        whole_restore,
    };
    let mut pipefail = false;
    let mut errexit = false;
    let mut plus_e: Option<usize> = None;
    let mut push = |rule: &'static str, line: usize, state: State, msg: String| {
        out.push(Finding {
            rule,
            line,
            state,
            msg,
        })
    };
    for (i, s) in stmts.iter().enumerate() {
        let at = cx.at(s);
        if let Some(st) = s.first() {
            let (p, e) = set_change(st);
            if let Some(p) = p {
                pipefail = p;
            }
            if let Some(e) = e {
                errexit = e;
                if e {
                    plus_e = None;
                } else {
                    plus_e = Some(at);
                }
            }
        }
        let restore = cx.restore_at(s.lix);
        let in_fn = shape.func_at(s.lix).is_some();
        let fn_def = func_name(&s.code).is_some();

        for (k, e) in s.elems.iter().enumerate() {
            // ---- U1
            for st in &e.stages {
                if matches!(st.cmd.as_str(), "eval" | "source" | ".") {
                    push(
                        "U1",
                        at,
                        State::Unknown,
                        format!("`{}` 讀進來的內容這裡看不到", st.cmd),
                    );
                }
                if matches!(st.cmd.as_str(), "bash" | "sh")
                    && st.args.iter().any(|a| a.starts_with("-c"))
                    && st.text.contains('$')
                {
                    push(
                        "U1",
                        at,
                        State::Unknown,
                        format!("`{} -c` 跑的字串是變數,這裡看不到", st.cmd),
                    );
                }
            }
            // ---- S1
            if pipefail && e.stages.len() >= 2 {
                let last = &e.stages[e.stages.len() - 1];
                let producer = &e.stages[0];
                if s1_hits(last, producer) {
                    push(
                        "S1",
                        at,
                        State::Yes,
                        format!(
                            "pipefail 下 `{} | … | {}`:{} 一拿到就收工,前一節收到 SIGPIPE ⇒ 找到了反而算失敗;改成先存變數再比,或 `grep -c`",
                            producer.cmd, last.cmd, last.cmd
                        ),
                    );
                }
            }
            // ---- S2: swallowed
            if k > 0 && s.ops[k - 1] == Op::Or {
                if let Some(st) = e.stages.first() {
                    if (st.cmd == "true" || st.cmd == ":")
                        && st.args.is_empty()
                        && e.stages.len() == 1
                    {
                        push(
                            "S2",
                            at,
                            State::Yes,
                            format!("`|| {}` 把失敗吞掉了", st.cmd),
                        );
                    }
                }
            }
            // ---- S3
            for st in &e.stages {
                let w0 = st.words.first().map(String::as_str).unwrap_or("");
                if matches!(w0, "local" | "export" | "readonly" | "declare" | "typeset")
                    && st
                        .words
                        .iter()
                        .any(|w| w.contains("=$(") || w.contains("=`"))
                {
                    push("S3", at, State::Yes, format!("`{w0} x=$(…)`:結束碼是 {w0} 的,$(…) 失敗看不到;先 `{w0} x` 再 `x=$(…) || …`"));
                }
                if is_test_cmd(&st.cmd) && has_subst(&st.text) {
                    push(
                        "S3",
                        at,
                        State::Yes,
                        "測試括號裡的 $(…):只比輸出,指令失敗(含連不上)跟「不一樣」分不開"
                            .to_owned(),
                    );
                }
            }
        }
        // ---- S2: a && b || c
        for (n, pair) in s.ops.windows(2).enumerate() {
            if pair[0] == Op::And && pair[1] == Op::Or {
                let b = s.elems[n + 1].stages.first();
                let b_print = b.is_some_and(|b| PRINTERS.contains(&b.cmd.as_str()));
                let c_stops = shape.elem_stops(&s.elems[n + 2]);
                if !b_print && !c_stops {
                    push(
                        "S2",
                        at,
                        State::Yes,
                        "`a && b || c`:b 失敗時 c 也會跑,當成 a 不成立".to_owned(),
                    );
                }
            }
        }
        // ---- S3: for / case on $(…)
        let w = s.code.split_whitespace().collect::<Vec<_>>();
        if (w.first() == Some(&"for") && w.iter().skip_while(|x| **x != "in").any(|x| has_subst(x)))
            || (w.first() == Some(&"case") && w.get(1).is_some_and(|x| has_subst(x)))
        {
            push(
                "S3",
                at,
                State::Yes,
                "`for … in $(…)` / `case $(…)`:$(…) 失敗了只會變成空的".to_owned(),
            );
        }
        let first = s.first().cloned().unwrap_or_default();
        let checked_next = stmts.get(i + 1).is_some_and(uses_status);
        // ---- S3: x=$(…) unchecked while set -e is off
        if !errexit
            && s.ops.is_empty()
            && first.assign_only
            && has_subst(&s.code)
            && !checked_next
            && !cx.returns_to_caller(i)
        {
            push(
                "S3",
                at,
                State::Yes,
                "set -e 沒開:`x=$(…)` 失敗了照樣往下,後面只看得到 x 是空的;加 `|| die …`"
                    .to_owned(),
            );
        }
        // ---- S2: a command unchecked while set -e is off
        let local_fn = shape.funcs.iter().any(|f| f.name == first.cmd)
            && !shape.remote_funcs.contains(&first.cmd);
        let last_in_fn = in_fn && cx.returns_to_caller(i);
        let a_command = first
            .cmd
            .chars()
            .next()
            .is_some_and(|c| c.is_alphanumeric() || matches!(c, '$' | '"' | '_' | '/' | '.'));
        if !errexit
            && a_command
            && s.ops.is_empty()
            && s.elems.len() == 1
            && !first.cond
            && !first.assign_only
            && !first.cmd.is_empty()
            && !fn_def
            && !UNCHECKED_OK.contains(&first.cmd.as_str())
            && !shape.stop_funcs.contains(&first.cmd)
            && !local_fn
            && !checked_next
            && !last_in_fn
        {
            push(
                "S2",
                at,
                State::Yes,
                format!("set -e 沒開:`{}` 失敗了沒人看,照樣往下", first.cmd),
            );
        }
        // ---- S4: a remote call as a condition, 255 not told apart
        for (k, e) in s.elems.iter().enumerate() {
            let Some(st) = e.stages.first() else { continue };
            let as_cond = st.cond || s.ops.get(k).is_some() || (k > 0);
            if shape.is_remote(st)
                && as_cond
                && !st.looped
                && !cx.failure_stops(i, k, errexit)
                && !cx.handles_255(i)
                && !cx.returns_to_caller(i)
            {
                let where_ = if restore { "還原路徑裡:" } else { "" };
                push("S4", at, State::Yes, format!("{where_}`{}` 當條件用:連不上(255)跟「不存在 / 不成立」走同一條路;先分出 255 再判斷", st.cmd));
            }
            // ---- S4: x=$(remote …) that may come back empty
            let or_stops = s.ops.first() == Some(&Op::Or)
                && s.elems.get(1).is_some_and(|e| shape.elem_stops(e));
            if st.assign_only
                && shape.assigns_remote(st)
                && (!errexit || s.ops.contains(&Op::Or))
                && !or_stops
                && !cx.handles_255(i)
            {
                let where_ = if restore { "還原路徑裡:" } else { "" };
                push(
                    "S4",
                    at,
                    State::Yes,
                    format!("{where_}`x=$(遠端 …)`:讀不到跟空的分不開;失敗要停,或分出 255"),
                );
            }
            // ---- S4: on a restore path, a missing backup is skipped
            if restore
                && is_test_cmd(&st.cmd)
                && (st.cond || s.ops.get(k).is_some())
                && st
                    .args
                    .iter()
                    .any(|a| matches!(a.as_str(), "-e" | "-f" | "-s" | "-d" | "-r"))
                && !st.negated
                && !cx.failure_stops(i, k, errexit)
            {
                push(
                    "S4",
                    at,
                    State::Yes,
                    "還原路徑裡:檔案不在就跳過 = 把「不知道備份在哪」當「不用還原」;不在要停"
                        .to_owned(),
                );
            }
        }
    }
    if let Some(at) = plus_e {
        push(
            "S2",
            at,
            State::Yes,
            "`set +e` 之後沒有再 `set -e`:之後每一行失敗都沒人看".to_owned(),
        );
    }
    // ---- S5: restore writes, no read-back after the last write
    s5(&cx, &mut push);
    // ---- heredocs read by a shell: their own script
    for d in &lexed.docs {
        if d.shell {
            let restore = cx.restore_at(d.owner);
            let mut inner = Vec::new();
            lint_script(&d.body, offset + d.body_line - 1, restore, &mut inner);
            out.extend(inner);
        }
    }
}

fn s1_hits(last: &Stage, producer: &Stage) -> bool {
    early_stop(last) && !PRINTERS.contains(&producer.cmd.as_str())
}

const WRITES: &[&str] = &["cp", "mv", "install", "rsync", "tee", "dd", "ln", "unzip"];
const READBACKS: &[&str] = &[
    "cmp",
    "sha256sum",
    "shasum",
    "diff",
    "b3sum",
    "md5sum",
    "sha256",
];

fn writes(raw: &str) -> bool {
    let ws = raw_words(raw);
    if ws.iter().any(|w| WRITES.contains(&w.as_str())) {
        return true;
    }
    let extracts = |w: &String| {
        (w.starts_with('-') && !w.starts_with("--") && w.contains('x'))
            || w == "--extract"
            || matches!(w.as_str(), "x" | "xf" | "xzf" | "xvf" | "xpf" | "xJf")
    };
    if ws.iter().any(|w| w == "tar") && ws.iter().any(extracts) {
        return true;
    }
    // a redirect into a file
    let b = raw.as_bytes();
    for (n, c) in raw.char_indices() {
        if c != '>' {
            continue;
        }
        let to_fd = b.get(n + 1) == Some(&b'&')
            || (b.get(n + 1) == Some(&b'>') && b.get(n + 2) == Some(&b'&'));
        if to_fd {
            continue;
        }
        if n > 0 && matches!(b[n - 1], b'-' | b'=' | b'>') {
            continue;
        }
        let rest = raw[n + 1..].trim_start_matches('>').trim_start();
        let target: String = rest
            .chars()
            .take_while(|c| !c.is_whitespace() && *c != ';' && *c != '|' && *c != ')')
            .collect();
        let target = target.trim_matches(['"', '\'']);
        if target.is_empty() || target.starts_with("/dev/") || target.starts_with('&') {
            continue;
        }
        return true;
    }
    false
}

fn reads_back(raw: &str) -> bool {
    raw_words(raw)
        .iter()
        .any(|w| READBACKS.contains(&w.as_str()))
}

fn s5(cx: &Script<'_>, push: &mut impl FnMut(&'static str, usize, State, String)) {
    // contexts: the whole script, or each restore function
    let mut ranges: Vec<(usize, usize)> = Vec::new();
    if cx.whole_restore {
        ranges.push((0, cx.lines.len().saturating_sub(1)));
    } else {
        for f in &cx.shape.funcs {
            if is_restore_name(&f.name) {
                ranges.push((f.from, f.to));
            }
        }
    }
    for (from, to) in ranges {
        let mut last_write: Option<usize> = None;
        let mut read_after = false;
        for l in &cx.lines[from..=to.min(cx.lines.len().saturating_sub(1))] {
            if writes(&l.raw) && !reads_back(&l.raw) {
                last_write = Some(l.line);
                read_after = false;
            } else if reads_back(&l.raw) {
                read_after = true;
            }
        }
        if let Some(line) = last_write {
            if !read_after {
                push(
                    "S5",
                    line + cx.offset,
                    State::Yes,
                    "還原寫了檔,之後沒有 cmp / sha256sum / diff 讀回來比".to_owned(),
                );
            }
        }
    }
}

/// The findings left after `acks` (codes the author named), and the ones acknowledged.
pub fn split_acked(
    findings: Vec<Finding>,
    acks: &BTreeSet<String>,
) -> (Vec<Finding>, Vec<Finding>) {
    findings
        .into_iter()
        .partition(|f| !(ackable(f.rule) && acks.contains(f.rule)))
}
