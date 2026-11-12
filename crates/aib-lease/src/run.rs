// SPDX-License-Identifier: Apache-2.0
//! `aib-lease run`: run steps on another Mac under that Mac's host lease, in **one** remote
//! shell, and record one history line per run (D3 + D5, a63ab 2026-11-10 00:1x, Andy approved).
//!
//! - The tree goes with `git archive` on ssh's stdin. The remote script goes in as a base64
//!   argument: a script piped into `bash` together with the archive reads the archive as shell.
//! - The lease is taken **inside** the remote shell with `--pid $$`, tied to the shell that runs
//!   the steps. Nothing runs unless `acquire` returned 0. Release and clean-up are an `EXIT` trap
//!   set right after the lease is granted.
//! - Every step's exit code is printed; the command's exit code says what happened
//!   ([`RunOutcome`]). ssh always gets `ForwardAgent=no` and `ClearAllForwardings=yes`.
//! - cargo builds into one folder per seat per machine (`~/.cache/aib-lease/<seat>/target`,
//!   `AIB_LEASE_TARGET_ROOT`), so a short step is not a cold build every time.
//! - History (D5): each step is measured remotely (`/usr/bin/time -l` when it exists: user /
//!   sys CPU seconds, max RSS; `ps` once a second over the step's process tree: highest CPU %)
//!   and reported on marker lines carrying a per-run nonce. One JSON line per run is appended to
//!   `~/.ez2-aib/state/lease/history.jsonl` on the machine that ran `aib-lease` (override
//!   `AIB_LEASE_HISTORY`). Commands are written with secret-looking values masked. A failed
//!   write prints a warning and never changes the exit code.

use base64::Engine;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

/// What `aib-lease run`'s exit code means.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunOutcome {
    /// Lease granted, every step exited 0.
    AllPassed,
    /// Lease granted, at least one step did not exit 0.
    StepFailed,
    /// Bad arguments.
    Usage,
    /// ssh could not connect, or the remote side ended in a way the script never does.
    Connect,
    /// `git archive` failed locally (or the rev does not exist).
    Archive,
    /// The remote side could not make its folder or unpack the archive.
    Unpack,
    /// The lease was not granted (the machine is busy): nothing ran.
    NotGranted,
    /// The connection dropped after the runner started: it keeps running on the other Mac
    /// and keeps its lease until the steps end (A1). The step results are not known here.
    RemoteRunning,
}

impl RunOutcome {
    pub fn code(self) -> u8 {
        match self {
            RunOutcome::AllPassed => 0,
            RunOutcome::StepFailed => 1,
            RunOutcome::Usage => 2,
            RunOutcome::Connect => 3,
            RunOutcome::Archive => 4,
            RunOutcome::Unpack => 73,
            RunOutcome::NotGranted => 75,
            RunOutcome::RemoteRunning => 76,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            RunOutcome::AllPassed => "all_passed",
            RunOutcome::StepFailed => "step_failed",
            RunOutcome::Usage => "usage",
            RunOutcome::Connect => "connect",
            RunOutcome::Archive => "archive",
            RunOutcome::Unpack => "unpack",
            RunOutcome::NotGranted => "not_granted",
            RunOutcome::RemoteRunning => "remote_running",
        }
    }

    /// The remote command's exit status as ssh reports it. The script exits only 0, 1, 73 or
    /// 75; ssh itself uses 255. Anything else (a signal is `None`) is a connection problem,
    /// never a pass.
    pub fn from_remote(status: Option<i32>) -> RunOutcome {
        match status {
            Some(0) => RunOutcome::AllPassed,
            Some(1) => RunOutcome::StepFailed,
            Some(73) => RunOutcome::Unpack,
            Some(75) => RunOutcome::NotGranted,
            _ => RunOutcome::Connect,
        }
    }
}

/// Put in front of the remote `PATH` (Homebrew, rustup, `aib` on the team's Macs). `$HOME`
/// is expanded remotely.
pub const DEFAULT_REMOTE_PATH: &str =
    "$HOME/.local/bin:/opt/homebrew/opt/rustup/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin";

#[derive(Debug, Clone)]
pub struct RunSpec {
    pub host: String,
    pub jump: Option<String>,
    pub rid: String,
    pub owner: String,
    pub what: String,
    pub ttl: u32,
    /// Where `ttl` came from: `given`, `history_p90` or `default` (D6).
    pub ttl_source: String,
    /// Required for a ttl of an hour or more (D6): why it is that long.
    pub long_reason: Option<String>,
    /// Ask for the lease with `--exclusive` (default): this run is being measured (D5), so
    /// small `aib test` batches must not run next to it. `--shared` turns it off.
    pub exclusive: bool,
    pub steps: Vec<String>,
    /// The remote `aib` command (tests point it at a fake).
    pub remote_aib: String,
    /// Put in front of the remote `PATH`, inside double quotes.
    pub remote_path: String,
    /// Marks this run's stat lines, so a step's own output cannot pass for them.
    pub nonce: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SpecError {
    NoSteps,
    BadRunId(String),
    BadRemotePath(String),
    BadTtl(u32),
    NeedLong(u32),
    BadHost(String),
}

impl std::fmt::Display for SpecError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SpecError::NoSteps => write!(f, "nothing to run: give --step CMD or -- CMD ARGS"),
            SpecError::BadRunId(r) => {
                write!(f, "run id {r:?}: only letters, digits, '.', '_' and '-'")
            }
            SpecError::BadRemotePath(p) => {
                write!(
                    f,
                    "remote path {p:?}: no quotes, backslashes, backticks or $("
                )
            }
            SpecError::BadTtl(t) => write!(f, "--ttl {t}: 60 to 14400 seconds"),
            SpecError::NeedLong(t) => {
                write!(f, "--ttl {t}: an hour or more needs --long REASON")
            }
            SpecError::BadHost(h) => {
                write!(f, "host {h:?}: must not be empty or start with '-'")
            }
        }
    }
}

fn plain_id(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

impl RunSpec {
    pub fn check(&self) -> Result<(), SpecError> {
        if self.steps.is_empty() {
            return Err(SpecError::NoSteps);
        }
        if !plain_id(&self.rid) || !plain_id(&self.nonce) {
            return Err(SpecError::BadRunId(self.rid.clone()));
        }
        let p = &self.remote_path;
        if p.contains(['"', '\\', '`', '\n']) || p.contains("$(") {
            return Err(SpecError::BadRemotePath(p.clone()));
        }
        if !(60..=14400).contains(&self.ttl) {
            return Err(SpecError::BadTtl(self.ttl));
        }
        if self.ttl >= LONG_TTL
            && self
                .long_reason
                .as_deref()
                .is_none_or(|r| r.trim().is_empty())
        {
            return Err(SpecError::NeedLong(self.ttl));
        }
        for h in std::iter::once(&self.host).chain(self.jump.iter()) {
            if h.is_empty() || h.starts_with('-') {
                return Err(SpecError::BadHost(h.clone()));
            }
        }
        Ok(())
    }
}

/// One POSIX shell word: single-quoted, with `'` written as `'\''`.
pub fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// `-- CMD ARGS...` as one shell command line, each word quoted.
pub fn join_command(words: &[String]) -> String {
    words
        .iter()
        .map(|w| shell_quote(w))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Shell functions of the remote script: the step's process tree, its CPU % now, and
/// stopping it when the script ends early.
const REMOTE_FUNCS: &str = r#"tree_pids() { ps -A -o pid=,ppid= | awk -v r="$1" '{p[$1]=$2} END{s[r]=1; ch=1; while(ch){ch=0; for(i in p) if(!(i in s) && (p[i] in s)){s[i]=1; ch=1}} for(i in s) print i}'; }
tree_cpu() { ps -A -o pid=,ppid=,%cpu= | awk -v r="$1" '{p[$1]=$2; c[$1]=$3} END{s[r]=1; ch=1; while(ch){ch=0; for(i in p) if(!(i in s) && (p[i] in s)){s[i]=1; ch=1}} t=0; for(i in s) t+=c[i]; printf "%d", t}'; }
stop_step() { if [ -n "${cpid:-}" ] && kill -0 "$cpid" 2>/dev/null; then for p in $(tree_pids "$cpid"); do kill -TERM "$p" 2>/dev/null; done; fi; }
"#;

/// Decode base64 `b` into file `f` on the remote side: GNU `base64 -d`, else macOS `-D`.
fn b64_to_file(b: &str, f: &str) -> String {
    format!(
        "{{ printf %s '{b}' | base64 -d > {f} 2>/dev/null || printf %s '{b}' | base64 -D > {f}; }}"
    )
}

/// The bash script that runs on the other Mac, in the ssh session. It reads the tar archive
/// on stdin, then starts [`runner_script`] **detached** (`nohup`, stdin `/dev/null`, output
/// to `$d.log`) and only follows its log (A1, Andy 11-12 04:2x: an ssh drop does not give up
/// the lease, the remote side keeps running and this end says so). When the runner ends it
/// prints the rest of the log and exits with the runner's code; the log, the code file and
/// the runner script are removed. A beat line every 5 s lets a dropped connection end this
/// follower (no tty, no HUP) without touching the runner.
pub fn remote_script(spec: &RunSpec) -> String {
    let q = shell_quote;
    let beat = format!("AIB_LEASE_BEAT {}", spec.nonce);
    let runner = base64::engine::general_purpose::STANDARD.encode(runner_script(spec).as_bytes());
    let mut s = String::new();
    s += "set -uo pipefail\n";
    s += &format!("rid={}\n", q(&spec.rid));
    s += "d=\"${TMPDIR:-/tmp}/aib-lease-$rid.noindex\"\n";
    s += "rm -rf \"$d\" \"$d.log\" \"$d.rc\" \"$d.runner\"; mkdir -p \"$d\" || { echo \"aib-lease: cannot make $d\"; exit 73; }\n";
    s += "if ! tar -x -C \"$d\"; then echo \"aib-lease: unpacking the archive failed\"; rm -rf \"$d\"; exit 73; fi\n";
    s += &format!("echo \"AIB_LEASE_HOST {} $(hostname)\"\n", spec.nonce);
    s += &b64_to_file(&runner, "\"$d.runner\"");
    s += " || { echo \"aib-lease: cannot write the runner\"; rm -rf \"$d\" \"$d.runner\"; exit 73; }\n";
    s += ": > \"$d.log\"\n";
    s += "nohup bash \"$d.runner\" </dev/null >\"$d.log\" 2>&1 &\n";
    s += "rp=$!\n";
    s += &format!("echo \"AIB_LEASE_DETACHED {} $rp $d.log\"\n", spec.nonce);
    // print the log from byte o; liveness is read before the size, so the last round after
    // the runner ended prints everything it wrote
    s += "o=0; t=0; while :; do up=0; kill -0 \"$rp\" 2>/dev/null && up=1; \
sz=$(wc -c < \"$d.log\" | tr -d ' '); \
if [ \"${sz:-0}\" -gt \"$o\" ]; then tail -c +$((o+1)) \"$d.log\" | head -c $((sz-o)); [ \"${PIPESTATUS[1]}\" = 0 ] || exit 141; o=$sz; fi; \
[ \"$up\" = 1 ] || break; sleep 1; t=$((t+1)); ";
    s += &format!("[ $((t % 5)) -ne 0 ] || echo \"{beat}\" 2>/dev/null || exit 141; done\n");
    s += "r=$(cat \"$d.rc\" 2>/dev/null); rm -f \"$d.log\" \"$d.rc\" \"$d.runner\"\n";
    // no code file: the runner was killed outright (-9) ⇒ 71, a connection-class result
    s += "exit \"${r:-71}\"\n";
    s
}

/// The script the detached runner runs (see [`remote_script`]). Each step runs as its own
/// `bash -c` (a step that does not parse fails alone), in the background so its tree can be
/// sampled, and is waited for before the next one. The lease is taken with this shell's pid;
/// HUP is ignored, INT / TERM stop the steps; the `EXIT` trap releases, removes the tree and
/// writes the exit code to `$d.rc` for the follower.
pub fn runner_script(spec: &RunSpec) -> String {
    let q = shell_quote;
    let n = spec.steps.len();
    let mark = format!("AIB_LEASE_STAT {}", spec.nonce);
    let mut s = String::new();
    s += "set -uo pipefail\n";
    s += &format!("rid={}\n", q(&spec.rid));
    s += &format!("aib_bin={}\n", q(&spec.remote_aib));
    s += REMOTE_FUNCS;
    s += "d=\"${TMPDIR:-/tmp}/aib-lease-$rid.noindex\"\n";
    s += "rm -f \"$d.runner\"\n"; // bash keeps the open file; nothing left behind on a drop
    s += &format!("export PATH=\"{}:$PATH\"\n", spec.remote_path);
    s += &format!(
        "\"$aib_bin\" lease acquire --owner {} --what {} --run-id \"$rid\" --ttl {} --pid $${}\n",
        q(&spec.owner),
        q(&spec.what),
        spec.ttl,
        if spec.exclusive { " --exclusive" } else { "" }
    );
    s += "lrc=$?\n";
    s += "if [ \"$lrc\" -ne 0 ]; then echo \"aib-lease: lease not granted (rc $lrc), nothing ran\"; rm -rf \"$d\"; echo 75 > \"$d.rc\"; exit 75; fi\n";
    s += "cpid=\n";
    s += "trap 'r=$?; stop_step; \"$aib_bin\" lease release --run-id \"$rid\" >/dev/null 2>&1; rm -rf \"$d\" \"$d.time\"; echo \"$r\" > \"$d.rc\"' EXIT\n";
    s += "trap '' HUP\n";
    s += "trap 'exit 130' INT TERM\n";
    s += "cd \"$d\" || exit 73\n";
    // one cargo target folder per seat per machine (043dd ANS-test-classify, DECISION v1.1):
    // a fresh folder per run would make every cargo step a cold build
    let seat: String = spec
        .owner
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        .collect();
    s += &format!(
        "export CARGO_TARGET_DIR=\"${{AIB_LEASE_TARGET_ROOT:-$HOME/.cache/aib-lease}}/{}/target\"\n",
        if seat.is_empty() { "anon" } else { &seat }
    );
    s += "tb=; if [ -x /usr/bin/time ] && /usr/bin/time -l -o /dev/null true >/dev/null 2>&1; then tb=/usr/bin/time; fi\n";
    s += "echo \"aib-lease: host $(hostname) run $rid pid $$\"\n";
    s += "fail=0\n";
    for (i, step) in spec.steps.iter().enumerate() {
        let k = i + 1;
        s += &format!("printf 'aib-lease: step {k}/{n}: %s\\n' {}\n", q(step));
        s += "rm -f \"$d.time\"; s0=$(date +%s)\n";
        s += &format!(
            "if [ -n \"$tb\" ]; then \"$tb\" -l -o \"$d.time\" bash -c {0} & else bash -c {0} & fi\n",
            q(step)
        );
        // chrome: any Chrome process on the machine while the step ran, in its tree or not
        // (2240e #609 L1: a browser test reparents its helpers; tonight's queues were Chrome)
        s += "cpid=$!; peak=0; ch=0\n";
        s += "while kill -0 \"$cpid\" 2>/dev/null; do c=$(tree_cpu \"$cpid\"); [ \"${c:-0}\" -gt \"$peak\" ] 2>/dev/null && peak=$c; [ \"$ch\" = 1 ] || { ps -A -o comm= 2>/dev/null | grep -qiE 'chrome|chromium' && ch=1; }; sleep 1; done\n";
        s += "wait \"$cpid\"; r=$?; cpid=; s1=$(date +%s)\n";
        s += "set -- -1 -1 -1; if [ -s \"$d.time\" ]; then set -- $(awk '{for(i=1;i<NF;i++){if($(i+1)==\"user\")u=$i; if($(i+1)==\"sys\")y=$i}} /maximum resident set size/{m=$1} END{printf \"%s %s %s\", (u==\"\"?-1:u), (y==\"\"?-1:y), (m==\"\"?-1:m)}' \"$d.time\"); fi\n";
        s += &format!("echo \"{mark} {k} $s0 $s1 $r $1 $2 $3 $peak $ch\"\n");
        s += &format!("echo \"aib-lease: step {k}/{n} rc=$r\"\n");
        s += "[ \"$r\" -eq 0 ] || fail=1\n";
    }
    s += "echo \"aib-lease: all rc=$fail\"\n";
    s += "exit \"$fail\"\n";
    s
}

/// The command line handed to ssh: decode the script into a temp file, run it with bash
/// (stdin still carries the archive), remove it, pass its exit code. Any POSIX login shell.
pub fn remote_command(script: &str) -> String {
    let b = base64::engine::general_purpose::STANDARD.encode(script.as_bytes());
    format!(
        "f=$(mktemp \"${{TMPDIR:-/tmp}}/aib-lease.XXXXXX\") || exit 73; {} \
         || {{ rm -f \"$f\"; exit 73; }}; bash \"$f\"; r=$?; rm -f \"$f\"; exit $r",
        b64_to_file(&b, "\"$f\"")
    )
}

/// ssh arguments (without the program): hardening options always, `-J` for a jump host,
/// then `--`, the host and the remote command.
pub fn ssh_args(spec: &RunSpec, remote_cmd: &str) -> Vec<String> {
    let mut a: Vec<String> = [
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        // 2240e #606 r2 L1: a network that vanishes (no FIN / RST) would leave this ssh
        // waiting for ever; 4 unanswered probes 15 s apart end it (rc 3)
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if let Some(j) = &spec.jump {
        a.push("-J".into());
        a.push(j.clone());
    }
    a.push("--".into());
    a.push(spec.host.clone());
    a.push(remote_cmd.to_string());
    a
}

/// One step's measurements, from its marker line.
#[derive(Debug, Clone, PartialEq)]
pub struct StepStat {
    pub n: usize,
    pub start: i64,
    pub end: i64,
    pub rc: i32,
    pub user_s: Option<f64>,
    pub sys_s: Option<f64>,
    pub maxrss: Option<u64>,
    pub peak_cpu: Option<u32>,
    /// A Chrome / Chromium process was on the machine while the step ran (any tree).
    pub chrome_seen: Option<bool>,
}

/// `AIB_LEASE_STAT <nonce> n start end rc user sys maxrss peak chrome` ⇒ a [`StepStat`]; another
/// nonce or a malformed line ⇒ `None`. `-1` means "not measured".
pub fn parse_stat(line: &str, nonce: &str) -> Option<StepStat> {
    let rest = line
        .strip_prefix("AIB_LEASE_STAT ")?
        .strip_prefix(nonce)?
        .strip_prefix(' ')?;
    let f: Vec<&str> = rest.split_whitespace().collect();
    if f.len() != 9 {
        return None;
    }
    let opt_f = |s: &str| s.parse::<f64>().ok().filter(|v| *v >= 0.0);
    Some(StepStat {
        n: f[0].parse().ok()?,
        start: f[1].parse().ok()?,
        end: f[2].parse().ok()?,
        rc: f[3].parse().ok()?,
        user_s: opt_f(f[4]),
        sys_s: opt_f(f[5]),
        maxrss: f[6].parse::<i64>().ok().and_then(|v| u64::try_from(v).ok()),
        peak_cpu: f[7].parse::<i64>().ok().and_then(|v| u32::try_from(v).ok()),
        chrome_seen: match f[8] {
            "1" => Some(true),
            "0" => Some(false),
            _ => None,
        },
    })
}

const SECRET_NAMES: &[&str] = &[
    "TOKEN", "KEY", "SECRET", "PASS", "AUTH", "CRED", "COOKIE", "SESSION",
];
const SECRET_PREFIXES: &[&str] = &[
    "sk-",
    "sk_",
    "ghp_",
    "gho_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "xoxb-",
    "xoxp-",
    "AKIA",
    "eyJ",
];

fn looks_secret(v: &str) -> bool {
    if SECRET_PREFIXES.iter().any(|p| v.starts_with(p)) && v.len() >= 12 {
        return true;
    }
    // commit / tree ids stay readable
    if (7..=64).contains(&v.len()) && v.chars().all(|c| c.is_ascii_hexdigit()) {
        return false;
    }
    v.len() >= 32
        && v.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '/' | '_' | '-' | '='))
        && v.chars().any(|c| c.is_ascii_digit())
        && v.chars().any(|c| c.is_ascii_alphabetic())
}

/// A command for the history line: `NAME=value` with a secret-looking name, or any word that
/// looks like a key / token, becomes `***`. Best effort (a log, not a defence).
pub fn mask_secrets(cmd: &str) -> String {
    cmd.split(' ')
        .map(|w| {
            let core = w.trim_matches(|c| c == '\'' || c == '"');
            if let Some((name, val)) = core.split_once('=') {
                let up = name.to_ascii_uppercase();
                let named = SECRET_NAMES.iter().any(|s| up.contains(s));
                if !val.is_empty() && (named || looks_secret(val)) {
                    return format!("{name}=***");
                }
            }
            if looks_secret(core) {
                "***".to_string()
            } else {
                w.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The history line of one run.
#[allow(clippy::too_many_arguments)]
pub fn history_line(
    spec: &RunSpec,
    remote_host: Option<&str>,
    rev: &str,
    tree: Option<&str>,
    start: i64,
    end: i64,
    outcome: RunOutcome,
    stats: &[StepStat],
) -> Value {
    let steps: Vec<Value> = spec
        .steps
        .iter()
        .enumerate()
        .map(|(i, cmd)| {
            let st = stats.iter().find(|s| s.n == i + 1);
            json!({
                "n": i + 1,
                "cmd": mask_secrets(cmd),
                "ran": st.is_some(),
                "start_epoch": st.map(|s| s.start),
                "end_epoch": st.map(|s| s.end),
                "secs": st.map(|s| s.end - s.start),
                "rc": st.map(|s| s.rc),
                "cpu_user_s": st.and_then(|s| s.user_s),
                "cpu_sys_s": st.and_then(|s| s.sys_s),
                // `/usr/bin/time -l`: the largest single process of the step, not the tree's sum
                "maxrss_bytes": st.and_then(|s| s.maxrss),
                "peak_cpu_pct": st.and_then(|s| s.peak_cpu),
                "chrome_seen": st.and_then(|s| s.chrome_seen),
            })
        })
        .collect();
    json!({
        "schema": "aib-lease-history/v1",
        "run_id": spec.rid,
        "seat": spec.owner,
        "ssh_host": spec.host,
        "host": remote_host,
        "rev": rev,
        "tree": tree,
        "start_epoch": start,
        "end_epoch": end,
        "secs": end - start,
        "rc": outcome.code(),
        "outcome": outcome.name(),
        "ttl": spec.ttl,
        "ttl_source": spec.ttl_source,
        "long_reason": spec.long_reason,
        "exclusive": spec.exclusive,
        "steps": steps,
    })
}

/// D6 (a63ab §14, Andy approved 00:2x): a ttl of this or more needs `--long REASON`.
pub const LONG_TTL: u32 = 3600;
/// D6: no `--ttl` and no history for these steps on this host.
pub const DEFAULT_TTL: u32 = 900;
/// D6: the most a ttl taken from history can be.
pub const AUTO_TTL_MAX: u32 = 1800;

/// D6: the ttl when none is given: p90 (nearest rank) of earlier runs of the **same steps**
/// (as recorded, masked) on the **same host** that ran (`all_passed` / `step_failed`), × 1.5,
/// between 60 and [`AUTO_TTL_MAX`]; no such run ⇒ [`DEFAULT_TTL`]. Unreadable lines are skipped.
pub fn auto_ttl(history: &str, host: &str, steps: &[String]) -> (u32, &'static str) {
    let want: Vec<String> = steps.iter().map(|s| mask_secrets(s)).collect();
    let mut secs: Vec<i64> = history
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|v| v["ssh_host"] == host)
        .filter(|v| matches!(v["outcome"].as_str(), Some("all_passed" | "step_failed")))
        .filter(|v| {
            v["steps"].as_array().is_some_and(|a| {
                a.len() == want.len() && a.iter().zip(&want).all(|(x, w)| x["cmd"] == w.as_str())
            })
        })
        .filter_map(|v| v["secs"].as_i64())
        .filter(|s| *s >= 0)
        .collect();
    if secs.is_empty() {
        return (DEFAULT_TTL, "default");
    }
    secs.sort_unstable();
    let rank = (secs.len() * 9).div_ceil(10).max(1);
    let p90 = secs[rank - 1];
    let t = ((p90 * 3 + 1) / 2).clamp(60, i64::from(AUTO_TTL_MAX));
    (u32::try_from(t).unwrap_or(AUTO_TTL_MAX), "history_p90")
}

/// `AIB_LEASE_HISTORY`, else `~/.ez2-aib/state/lease/history.jsonl` (per machine, like the
/// lease file; not the cabin state root).
pub fn history_path() -> PathBuf {
    if let Ok(p) = std::env::var("AIB_LEASE_HISTORY") {
        if !p.trim().is_empty() {
            return PathBuf::from(p);
        }
    }
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    Path::new(&home).join(".ez2-aib/state/lease/history.jsonl")
}

/// Append one line; the error is for a warning only.
pub fn append_history(path: &Path, line: &Value) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut o = std::fs::OpenOptions::new();
    o.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600); // the commands are masked best effort only (2240e N2)
    }
    let mut f = o.open(path)?;
    f.write_all(format!("{line}\n").as_bytes())
}

const USAGE: &str = "usage: aib-lease run --host HOST [--jump HOST] [--repo DIR] [--rev REV] \
[--ttl SECS [--long REASON]] [--shared] [--owner SEAT] [--what TEXT] [--step CMD]... [-- CMD ARGS...]\n\
no --ttl: p90 x 1.5 of the same steps on the same host from history (none: 900 s), at most 1800; \
--ttl 3600 or more needs --long REASON\n\
exit: 0 all steps passed, 1 a step failed, 2 usage, 3 ssh, 4 git archive, 73 remote unpack, \
75 lease not granted (nothing ran), 76 the connection dropped and the run is still going on the \
other Mac (it keeps the lease until its steps end; check with `aib lease status` there, stop it \
by killing the pid printed)";

struct Args {
    spec: RunSpec,
    repo: PathBuf,
    rev: String,
    ssh: String,
}

fn epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn nonce() -> String {
    let mut b = [0u8; 8];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut b))
        .is_ok();
    if !ok {
        let t = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        b = ((t as u64) ^ u64::from(std::process::id()).rotate_left(32)).to_le_bytes();
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut host = None;
    let mut jump = None;
    let mut repo = PathBuf::from(".");
    let mut rev = "HEAD".to_string();
    let mut ttl: Option<u32> = None;
    let mut long_reason = None;
    let mut exclusive = true;
    let mut owner = std::env::var("AIB_LEASE_OWNER").ok();
    let mut what = None;
    let mut steps = Vec::new();
    let mut ssh = "ssh".to_string();
    let mut remote_aib = "aib".to_string();
    let mut remote_path = DEFAULT_REMOTE_PATH.to_string();
    let mut it = argv.iter();
    while let Some(a) = it.next() {
        if a == "--" {
            let rest: Vec<String> = it.by_ref().cloned().collect();
            if rest.is_empty() {
                return Err("nothing after --".into());
            }
            steps.push(join_command(&rest));
            break;
        }
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--host" => host = Some(val()?),
            "--jump" => jump = Some(val()?),
            "--repo" => repo = PathBuf::from(val()?),
            "--rev" => rev = val()?,
            "--ttl" => {
                let v = val()?;
                ttl = Some(
                    v.parse()
                        .map_err(|_| format!("--ttl {v:?} is not a number"))?,
                );
            }
            "--long" => long_reason = Some(val()?),
            "--shared" => exclusive = false,
            "--owner" => owner = Some(val()?),
            "--what" => what = Some(val()?),
            "--step" => steps.push(val()?),
            "--ssh" => ssh = val()?,
            "--remote-aib" => remote_aib = val()?,
            "--remote-path" => remote_path = val()?,
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    let host: String = host.ok_or("--host is required")?;
    let (ttl, ttl_source) = match ttl {
        Some(t) => (t, "given"),
        None => auto_ttl(
            &std::fs::read_to_string(history_path()).unwrap_or_default(),
            &host,
            &steps,
        ),
    };
    let owner = owner
        .filter(|o| !o.is_empty())
        .ok_or("--owner (or AIB_LEASE_OWNER) is required")?;
    let clean_owner: String = owner
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        .collect();
    let rid = format!("{clean_owner}-run-{}-{}", epoch(), std::process::id());
    let what = what.unwrap_or_else(|| {
        let first = steps.first().map(|s| mask_secrets(s)).unwrap_or_default();
        format!("{owner} aib-lease run {rev}: {first}")
    });
    Ok(Args {
        spec: RunSpec {
            host,
            jump,
            rid,
            owner,
            what,
            ttl,
            ttl_source: ttl_source.to_string(),
            long_reason,
            exclusive,
            steps,
            remote_aib,
            remote_path,
            nonce: nonce(),
        },
        repo,
        rev,
        ssh,
    })
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).trim().to_string())
}

struct Ran {
    outcome: RunOutcome,
    tree: Option<String>,
    host: Option<String>,
    stats: Vec<StepStat>,
    /// `pid log` of the detached runner, from its marker line.
    detached: Option<String>,
}

fn execute(a: &Args) -> Ran {
    let mut ran = Ran {
        outcome: RunOutcome::Archive,
        tree: None,
        host: None,
        stats: Vec::new(),
        detached: None,
    };
    let Some(tree) = git(
        &a.repo,
        &["rev-parse", "--verify", &format!("{}^{{tree}}", a.rev)],
    ) else {
        eprintln!(
            "aib-lease: {:?} is not a commit or tree in {}",
            a.rev,
            a.repo.display()
        );
        return ran;
    };
    ran.tree = Some(tree.clone());
    if a.rev == "HEAD"
        && git(&a.repo, &["status", "--porcelain", "--untracked-files=no"])
            .is_some_and(|s| !s.is_empty())
    {
        eprintln!(
            "aib-lease: warning: uncommitted changes in {} are NOT sent (HEAD only)",
            a.repo.display()
        );
    }
    let commit = git(
        &a.repo,
        &["rev-parse", "--verify", &format!("{}^{{commit}}", a.rev)],
    );
    eprintln!(
        "aib-lease: sending {} (tree {tree}) to {} as run {}",
        commit.as_deref().unwrap_or("a tree"),
        a.spec.host,
        a.spec.rid
    );
    let mut archive = match Command::new("git")
        .arg("-C")
        .arg(&a.repo)
        .args(["archive", "--format=tar", &a.rev])
        .stdout(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("aib-lease: git archive: {e}");
            return ran;
        }
    };
    let Some(tar) = archive.stdout.take() else {
        return ran;
    };
    let cmd = remote_command(&remote_script(&a.spec));
    let child = Command::new(&a.ssh)
        .args(ssh_args(&a.spec, &cmd))
        .stdin(Stdio::from(tar))
        .stdout(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(e) => {
            eprintln!("aib-lease: cannot start {}: {e}", a.ssh);
            let _ = archive.wait();
            ran.outcome = RunOutcome::Connect;
            return ran;
        }
    };
    let host_mark = format!("AIB_LEASE_HOST {} ", a.spec.nonce);
    let beat = format!("AIB_LEASE_BEAT {}", a.spec.nonce);
    let det_mark = format!("AIB_LEASE_DETACHED {} ", a.spec.nonce);
    if let Some(out) = child.stdout.take() {
        let stdout = std::io::stdout();
        for line in BufReader::new(out).lines() {
            let Ok(line) = line else { break };
            if let Some(st) = parse_stat(&line, &a.spec.nonce) {
                ran.stats.push(st);
            } else if let Some(h) = line.strip_prefix(&host_mark) {
                ran.host = Some(h.trim().to_string());
            } else if let Some(d) = line.strip_prefix(&det_mark) {
                let d = d.trim().to_string();
                // 043dd #720 M1: say it before anything can go wrong, so whoever presses
                // Ctrl-C (or closes the terminal) has already seen how to stop the run
                let (pid, logf) = d.split_once(' ').unwrap_or((d.as_str(), "?"));
                eprintln!(
                    "aib-lease: run {} is detached on {} (pid {pid}, log {logf}); a dropped \
                     connection or Ctrl-C here does not stop it. Stop: ssh {} kill {pid} \
                     (it releases on the way out).",
                    a.spec.rid, a.spec.host, a.spec.host
                );
                ran.detached = Some(d);
            } else if line == beat {
                // the remote side checking that this end is still here
            } else {
                let mut o = stdout.lock();
                let _ = writeln!(o, "{line}");
                let _ = o.flush();
            }
        }
    }
    let status = child.wait();
    let archive_ok = archive.wait().map(|s| s.success()).unwrap_or(false);
    ran.outcome = match &status {
        Ok(s) => RunOutcome::from_remote(s.code()),
        Err(_) => RunOutcome::Connect,
    };
    // ssh's own failure (255) or a signal, after the runner started: the runner is on its
    // own and still holds the lease (A1). The follower's normal exits are the runner's codes.
    let dropped = matches!(&status, Ok(s) if matches!(s.code(), Some(255) | None));
    if let (true, Some(d)) = (dropped, ran.detached.as_deref()) {
        let (pid, logf) = d.split_once(' ').unwrap_or((d, "?"));
        eprintln!(
            "aib-lease: the connection to {} dropped; run {} is still running there (pid {pid}, \
             log {logf}) and keeps its lease until its steps end. Check: `aib lease status` on \
             that Mac; stop: kill {pid} there (it releases on the way out).",
            a.spec.host, a.spec.rid
        );
        ran.outcome = RunOutcome::RemoteRunning;
    } else if dropped && ran.host.is_some() {
        // 043dd #720 L1: the drop came after unpacking but before the runner's marker; it may
        // already be running with the lease, so a plain connection error (rc 3) would invite
        // a second copy of the same run
        eprintln!(
            "aib-lease: the connection to {} dropped after unpacking; run {} may already be \
             running there. Check `aib lease status` on that Mac before sending it again.",
            a.spec.host, a.spec.rid
        );
        ran.outcome = RunOutcome::RemoteRunning;
    }
    // ssh ending early (no connection, lease refused) breaks the pipe, so a failed archive
    // only matters when the remote side says it could not unpack
    if !archive_ok && ran.outcome == RunOutcome::Unpack {
        eprintln!("aib-lease: git archive failed");
        ran.outcome = RunOutcome::Archive;
    }
    ran
}

/// `aib-lease run …` (the arguments after `run`); returns the exit code.
pub fn cli(argv: &[String]) -> u8 {
    if argv.iter().any(|a| a == "-h" || a == "--help") && !argv.iter().any(|a| a == "--") {
        println!("{USAGE}");
        return 0;
    }
    let a = match parse(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-lease run: {e}\n{USAGE}");
            return RunOutcome::Usage.code();
        }
    };
    if let Err(e) = a.spec.check() {
        eprintln!("aib-lease run: {e}\n{USAGE}");
        return RunOutcome::Usage.code();
    }
    let start = epoch();
    let ran = execute(&a);
    let line = history_line(
        &a.spec,
        ran.host.as_deref(),
        &a.rev,
        ran.tree.as_deref(),
        start,
        epoch(),
        ran.outcome,
        &ran.stats,
    );
    let path = history_path();
    if let Err(e) = append_history(&path, &line) {
        eprintln!(
            "aib-lease: warning: history line not written to {}: {e}",
            path.display()
        );
    }
    eprintln!(
        "aib-lease: result {} (rc {})",
        ran.outcome.name(),
        ran.outcome.code()
    );
    ran.outcome.code()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> RunSpec {
        RunSpec {
            host: "mac-b".into(),
            jump: None,
            rid: "043dd-run-1-2".into(),
            owner: "043dd".into(),
            what: "it's a test".into(),
            ttl: 2400,
            ttl_source: "given".into(),
            long_reason: None,
            exclusive: true,
            steps: vec!["cargo test -p aibd".into(), "echo 'a b'".into()],
            remote_aib: "aib".into(),
            remote_path: DEFAULT_REMOTE_PATH.into(),
            nonce: "00ff".into(),
        }
    }

    #[test]
    fn quoting_survives_single_quotes() {
        assert_eq!(shell_quote("it's"), r"'it'\''s'");
        assert_eq!(
            join_command(&["cargo".into(), "a b".into(), "".into()]),
            "'cargo' 'a b' ''"
        );
    }

    #[test]
    fn the_lease_comes_before_any_step_and_the_release_is_a_trap() {
        let s = runner_script(&spec());
        let acquire = s.find("lease acquire").unwrap();
        let gate = s.find("exit 75").unwrap();
        let trap = s
            .find("trap 'r=$?; stop_step; \"$aib_bin\" lease release")
            .unwrap();
        let first_step = s.find("bash -c 'cargo test -p aibd'").unwrap();
        assert!(acquire < gate && gate < trap && trap < first_step, "{s}");
        assert!(
            s.contains("--pid $$ --exclusive\n"),
            "the lease is tied to the runner's shell, exclusive by default (2240e L1): {s}"
        );
        assert!(
            s.contains("trap '' HUP\n") && !s.contains("PIPE"),
            "A1: a dropped connection does not stop the runner: {s}"
        );
        assert!(s.contains("echo \"$r\" > \"$d.rc\"' EXIT"), "{s}");
        let mut shared = spec();
        shared.exclusive = false;
        let s = runner_script(&shared);
        assert!(
            s.contains("--pid $$\n") && !s.contains("--exclusive"),
            "{s}"
        );
        let s = runner_script(&spec());
        assert!(s.contains("--what 'it'\\''s a test'"), "{s}");
        assert!(s.contains("bash -c 'echo '\\''a b'\\'''"), "{s}");
        assert!(s.contains("echo \"AIB_LEASE_STAT 00ff 2 $s0"), "{s}");
        assert!(
            s.contains("export CARGO_TARGET_DIR=\"${AIB_LEASE_TARGET_ROOT:-$HOME/.cache/aib-lease}/043dd/target\""),
            "one warm target folder per seat: {s}"
        );
    }

    /// A1: the ssh session unpacks, starts the runner detached and only follows its log.
    #[test]
    fn the_session_starts_the_runner_detached_and_follows_it() {
        let s = remote_script(&spec());
        let unpack = s.find("tar -x -C").unwrap();
        let start = s
            .find("nohup bash \"$d.runner\" </dev/null >\"$d.log\" 2>&1 &")
            .unwrap();
        let mark = s
            .find("echo \"AIB_LEASE_DETACHED 00ff $rp $d.log\"")
            .unwrap();
        assert!(unpack < start && start < mark, "{s}");
        assert!(
            !s.contains("lease acquire"),
            "the lease is the runner's: {s}"
        );
        assert!(
            s.contains("echo \"AIB_LEASE_BEAT 00ff\" 2>/dev/null || exit 141"),
            "a dropped connection ends the follower (2240e L2): {s}"
        );
        assert!(s.contains("exit \"${r:-71}\""), "{s}");
        let runner =
            base64::engine::general_purpose::STANDARD.encode(runner_script(&spec()).as_bytes());
        assert!(s.contains(&runner), "the runner travels inside: {s}");
        assert_eq!(RunOutcome::RemoteRunning.code(), 76);
        assert_eq!(RunOutcome::RemoteRunning.name(), "remote_running");
    }

    #[test]
    fn ssh_is_always_hardened_and_the_host_cannot_be_an_option() {
        let mut sp = spec();
        sp.jump = Some("jump-host".into());
        let joined = ssh_args(&sp, "true").join(" ");
        assert!(joined.contains("-o ForwardAgent=no -o ClearAllForwardings=yes"));
        assert!(
            joined.contains("-o ServerAliveInterval=15 -o ServerAliveCountMax=4"),
            "a vanished network ends this ssh: {joined}"
        );
        let a = ssh_args(&sp, "true");
        let dashes = a.iter().position(|x| x == "--").unwrap();
        assert!(
            a.iter()
                .position(|x| x == "ServerAliveInterval=15")
                .unwrap()
                < dashes,
            "options come before the host: {a:?}"
        );
        assert!(joined.contains("-J jump-host -- mac-b true"), "{joined}");
        sp.host = "-oProxyCommand=x".into();
        assert_eq!(sp.check(), Err(SpecError::BadHost(sp.host.clone())));
    }

    #[test]
    fn exit_codes_never_turn_a_failure_into_a_pass() {
        assert_eq!(RunOutcome::from_remote(Some(0)), RunOutcome::AllPassed);
        assert_eq!(RunOutcome::from_remote(Some(1)), RunOutcome::StepFailed);
        assert_eq!(RunOutcome::from_remote(Some(75)), RunOutcome::NotGranted);
        assert_eq!(RunOutcome::from_remote(Some(73)), RunOutcome::Unpack);
        for other in [Some(255), Some(127), Some(2), None] {
            assert_eq!(
                RunOutcome::from_remote(other),
                RunOutcome::Connect,
                "{other:?}"
            );
        }
    }

    #[test]
    fn checks_reject_what_would_break_the_script() {
        let mut sp = spec();
        sp.rid = "a b".into();
        assert!(matches!(sp.check(), Err(SpecError::BadRunId(_))));
        let mut sp = spec();
        sp.remote_path = "$(rm -rf ~)".into();
        assert!(matches!(sp.check(), Err(SpecError::BadRemotePath(_))));
        let mut sp = spec();
        sp.steps.clear();
        assert_eq!(sp.check(), Err(SpecError::NoSteps));
        let mut sp = spec();
        sp.ttl = 5;
        assert_eq!(sp.check(), Err(SpecError::BadTtl(5)));
        assert_eq!(spec().check(), Ok(()));
    }

    #[test]
    fn the_remote_command_only_carries_base64() {
        let c = remote_command("echo hi\n");
        let b = base64::engine::general_purpose::STANDARD.encode("echo hi\n");
        assert!(c.contains(&format!("'{b}'")), "{c}");
        assert!(c.ends_with("exit $r"), "{c}");
    }

    #[test]
    fn stat_lines_need_this_runs_nonce() {
        let l = "AIB_LEASE_STAT 00ff 2 100 160 1 12.5 0.75 104857600 230 1";
        assert_eq!(
            parse_stat(l, "00ff"),
            Some(StepStat {
                n: 2,
                start: 100,
                end: 160,
                rc: 1,
                user_s: Some(12.5),
                sys_s: Some(0.75),
                maxrss: Some(104_857_600),
                peak_cpu: Some(230),
                chrome_seen: Some(true),
            })
        );
        assert_eq!(parse_stat(l, "beef"), None, "a step printing a stat line");
        assert_eq!(parse_stat(l, "00f"), None, "a nonce that is only a prefix");
        let unmeasured = parse_stat("AIB_LEASE_STAT 00ff 1 1 2 0 -1 -1 -1 0 0", "00ff").unwrap();
        assert_eq!((unmeasured.user_s, unmeasured.maxrss), (None, None));
        assert_eq!(unmeasured.chrome_seen, Some(false));
        assert_eq!(
            parse_stat("AIB_LEASE_STAT 00ff 1 1 2 0 -1 -1 -1 0", "00ff"),
            None,
            "nine fields"
        );
        assert_eq!(parse_stat("AIB_LEASE_STAT 00ff 1 2", "00ff"), None);
    }

    #[test]
    fn secrets_are_masked_and_commit_ids_are_not() {
        let m = mask_secrets(
            "ANTHROPIC_API_KEY=sk-ant-abcdef123456 GH_TOKEN='ghp_abcdefghijklmnop' cargo test --rev 967111ff447dcd6d6c561df2f860d95d698109d0 x=QWxhZGRpbjpvcGVuIHNlc2FtZQ12345678",
        );
        assert!(!m.contains("sk-ant"), "{m}");
        assert!(!m.contains("ghp_"), "{m}");
        assert!(!m.contains("QWxhZGRp"), "{m}");
        assert!(m.contains("ANTHROPIC_API_KEY=***"), "{m}");
        assert!(
            m.contains("967111ff447dcd6d6c561df2f860d95d698109d0"),
            "{m}"
        );
        assert!(m.contains("cargo test --rev"), "{m}");
        assert_eq!(mask_secrets("cargo test -p aibd"), "cargo test -p aibd");
    }

    #[test]
    fn the_ttl_comes_from_this_hosts_history_of_the_same_steps() {
        let steps = vec!["cargo test -p x".to_string()];
        let line = |host: &str, cmd: &str, secs: i64, outcome: &str| {
            json!({"ssh_host": host, "outcome": outcome, "secs": secs,
                   "steps": [{"cmd": cmd}]})
            .to_string()
        };
        assert_eq!(auto_ttl("", "mac-b", &steps), (DEFAULT_TTL, "default"));
        let mut h: Vec<String> = (1..=10)
            .map(|i| line("mac-b", "cargo test -p x", i * 100, "all_passed"))
            .collect();
        h.push(line("mini", "cargo test -p x", 99_999, "all_passed"));
        h.push(line("mac-b", "cargo test -p y", 99_999, "all_passed"));
        h.push(line("mac-b", "cargo test -p x", 99_999, "not_granted"));
        h.push("not json".into());
        // p90 of 100..1000 = 900, × 1.5 = 1350
        assert_eq!(
            auto_ttl(&h.join("\n"), "mac-b", &steps),
            (1350, "history_p90")
        );
        let slow = line("mac-b", "cargo test -p x", 5000, "all_passed");
        assert_eq!(
            auto_ttl(&slow, "mac-b", &steps),
            (AUTO_TTL_MAX, "history_p90")
        );
        let fast = line("mac-b", "cargo test -p x", 2, "step_failed");
        assert_eq!(auto_ttl(&fast, "mac-b", &steps), (60, "history_p90"));
    }

    #[test]
    fn an_hour_or_more_needs_a_reason() {
        let mut sp = spec();
        sp.ttl = 3600;
        assert_eq!(sp.check(), Err(SpecError::NeedLong(3600)));
        sp.long_reason = Some("  ".into());
        assert_eq!(sp.check(), Err(SpecError::NeedLong(3600)));
        sp.long_reason = Some("full suite with Chrome".into());
        assert_eq!(sp.check(), Ok(()));
        sp.ttl = 3599;
        sp.long_reason = None;
        assert_eq!(sp.check(), Ok(()));
    }

    #[test]
    fn the_history_line_lists_every_step_ran_or_not() {
        let st = StepStat {
            n: 1,
            start: 10,
            end: 25,
            rc: 0,
            user_s: Some(3.0),
            sys_s: None,
            maxrss: Some(5),
            peak_cpu: Some(90),
            chrome_seen: None,
        };
        let v = history_line(
            &spec(),
            Some("Mac-Pro"),
            "HEAD",
            Some("abc"),
            5,
            30,
            RunOutcome::StepFailed,
            &[st],
        );
        assert_eq!(v["rc"], 1);
        assert_eq!(v["outcome"], "step_failed");
        assert_eq!(v["secs"], 25);
        assert_eq!(v["steps"][0]["secs"], 15);
        assert_eq!(v["steps"][0]["peak_cpu_pct"], 90);
        assert_eq!(v["steps"][1]["ran"], false);
        assert!(v["steps"][1]["rc"].is_null());
    }
}
