// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site leakcheck <code> --out <file> [--records dir]` — what a site's own
//! user (and a customer's AI working as that user) can still read of the machine under
//! the site and of who runs it (E6, Andy: 「客戶與他的 AI 查不到主機與主機商的任何資訊」).
//! **Read-only**: nothing is written on the host but `--out`, nothing inside the CT.
//!
//! One `pct exec` into the site's CT runs one shell, as the site's own user with an empty
//! environment (`env -i … runuser -u <code>`), made of the reads of the inside checklist
//! (PLAN-e6-hide-host §4, in the forms f1fae ran on s-mzedn7 on 11-12) and one `wp eval`.
//! Each answer is judged here, three ways:
//!
//! * `leak` — the answer carries a word of the host's hide list (its names, the words of
//!   `/etc/ez2-cloud/hide-words`, the CT's number, host-only paths: `svc::HostHide`), or
//!   it is not what `aib_cloud::mask::INSIDE_EXPECT` (or the checklist) says it must be.
//!   Words match as `HostHide` matches them (c477a, PLAN-e6 §7 L2): at least
//!   `svc::HIDE_WORD_MIN` characters, ASCII case ignored, whole words only. Phrases known
//!   to be harmless (a public mirror's name that holds a word, say) go in the allow list
//!   [`ALLOW_WORDS`], root's and 0600: they are blanked out of an answer before the words
//!   are looked for. An allow phrase that is a hide word on its own is dropped (it would
//!   switch the word off). An allow list that is missing, cannot be read, or is not root's
//!   0600 is **not applied** — every hit stays a leak; it never lets everything pass;
//! * `unread` — it could not be read: the shell did not run, its answer is cut or has no
//!   end mark, it failed where it must not, it cannot be judged, or the hide list could
//!   not be read (a missing list included: then the provider's name was not looked for).
//!   **Unread is never a pass**;
//! * `pass` — read, judged, and none of the above.
//!
//! The answers are host information: they go **only** into `--out` (created 0600, never
//! inside a git work tree). Standard output gives the counts and the names of the checks
//! that leak or were not read — never an answer.
//!
//! Each answer is framed by marks carrying a fresh random nonce, so a file inside the CT
//! that prints a mark cannot end a check early. What the CT prints is the customer's: a
//! customer can make their own CT look clean to this check; it is our check of our mask,
//! not a proof against them. What a program inside the site answers stands only for a
//! site in normal order (c477a N1); for the `/sys` and `/proc` reads, the host can read
//! the same files as the CT's root sees them through `/proc/<CT init pid>/root/…` and
//! compare.
//!
//! Not run here (listed as `not_here`): §4 items 14 (the environment: measured through
//! the customer client's ssh path, `pct exec` passes ours — c477a N2), 16 (host keys of every CT, host
//! root), 20–22 and 24 (from outside), 23 (egress address: a baseline, not judged), 25 (a
//! test mail: a write), 26 (the customer's client). The kernel's release string in
//! `php_uname` names the platform and cannot be hidden in a container
//! (`aib_cloud::mask::CANNOT_HIDE`): it is only judged against the hide list.
//!
//! Exit: 0 every check passes; 1 a check leaks; 3 none leaks but one was not read; 2 a
//! usage error or `--out` not written.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use aib_cloud::mask;
use aib_cloud::site::SiteSpec;
use aib_cloud::svc::{self, HostHide};
use serde_json::{json, Value};

use crate::customercmd::{host_hide, HIDE_WORDS, HOSTNAME};
use crate::leakwordscmd::{inside_git_tree, write_private};
use crate::probe::run_capture;
use crate::sitescancmd::{Exec, HostExec};

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
/// Phrases that hold a hide word and are known to be harmless: one per line, root's,
/// 0600, never in this repository (c477a, PLAN-e6 §7 L2).
pub const ALLOW_WORDS: &str = "/etc/ez2-cloud/hide-words.allow";

/// The allow list's phrases out of its text: blank and `#` lines skipped, lower-cased,
/// shorter than `svc::HIDE_WORD_MIN` left out, and so is a phrase that is a hide word on
/// its own.
pub fn allow_list(text: &str, hide: &HostHide) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#') && l.len() >= svc::HIDE_WORD_MIN)
        .filter(|l| hide.hide(l).0 != svc::HIDDEN)
        .map(str::to_ascii_lowercase)
        .collect()
}

/// `text` with every allowed phrase (ASCII case ignored) blanked out.
fn without_allowed(text: &str, allow: &[String]) -> String {
    let mut out = text.to_owned();
    for a in allow {
        let lower = out.to_ascii_lowercase();
        let mut kept = String::with_capacity(out.len());
        let mut i = 0;
        while let Some(k) = lower[i..].find(a.as_str()) {
            kept.push_str(&out[i..i + k]);
            kept.push(' ');
            i += k + a.len();
        }
        kept.push_str(&out[i..]);
        out = kept;
    }
    out
}

/// What a check's exit status must be for its answer to count.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Rc {
    /// 0, or the answer is not read.
    Zero,
    /// Any: the last command of a pipe, or a missing file is an answer.
    Any,
    /// Not 0 (refused); 126 / 127 (not runnable, not there) is not read.
    Refused,
}

/// One read of the checklist: its name, its §4 item, the shell text, the exit status it
/// needs, and the `INSIDE_EXPECT` entry it answers (if any).
struct Probe {
    id: &'static str,
    item: u8,
    sh: &'static str,
    rc: Rc,
    inside: Option<&'static str>,
}

const PROBES: &[Probe] = &[
    Probe {
        id: "cpuinfo",
        item: 1,
        sh: r#"grep -E "model name|microcode|cache size|MHz" /proc/cpuinfo | sort | uniq -c"#,
        rc: Rc::Any,
        inside: Some("/proc/cpuinfo"),
    },
    Probe {
        id: "dmi_vendor",
        item: 2,
        sh: "cat /sys/class/dmi/id/sys_vendor 2>&1",
        rc: Rc::Any,
        inside: Some("/sys/class/dmi/id/sys_vendor"),
    },
    Probe {
        id: "dmi_board",
        item: 2,
        sh: "cat /sys/class/dmi/id/board_name 2>&1",
        rc: Rc::Any,
        inside: Some("/sys/class/dmi/id/board_name"),
    },
    Probe {
        id: "meminfo",
        item: 3,
        sh: "head -3 /proc/meminfo; cat /proc/uptime; grep -c ^cpu /proc/stat",
        rc: Rc::Zero,
        inside: None,
    },
    Probe {
        id: "cmdline",
        item: 4,
        sh: "cat /proc/cmdline | wc -c",
        rc: Rc::Zero,
        inside: Some("/proc/cmdline"),
    },
    Probe {
        id: "cpu_dir",
        item: 5,
        sh: r#"ls /sys/devices/system/cpu | grep -c "^cpu[0-9]""#,
        rc: Rc::Any,
        inside: Some("/sys/devices/system/cpu"),
    },
    Probe {
        id: "hw_lists",
        item: 6,
        sh: "ls /sys/block /sys/bus/pci/devices /sys/class/hwmon 2>&1 | head",
        rc: Rc::Any,
        inside: None,
    },
    Probe {
        id: "hostname",
        item: 7,
        sh: "hostname; hostname -f",
        rc: Rc::Any,
        inside: None,
    },
    Probe {
        id: "resolv",
        item: 8,
        sh: "cat /etc/resolv.conf",
        rc: Rc::Zero,
        inside: Some("/etc/resolv.conf"),
    },
    Probe {
        id: "hosts",
        item: 9,
        sh: "cat /etc/hosts",
        rc: Rc::Zero,
        inside: Some("/etc/hosts"),
    },
    Probe {
        id: "mac",
        item: 10,
        sh: "cat /sys/class/net/eth0/address",
        rc: Rc::Zero,
        inside: Some("/sys/class/net/eth0/address"),
    },
    Probe {
        id: "ip6",
        item: 11,
        sh: "ip -6 addr show scope global",
        rc: Rc::Zero,
        inside: Some("ip -6 addr show scope global"),
    },
    Probe {
        id: "route",
        item: 11,
        sh: "ip route",
        rc: Rc::Zero,
        inside: None,
    },
    Probe {
        id: "os_apt",
        item: 12,
        sh: "cat /etc/os-release /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null",
        rc: Rc::Any,
        inside: None,
    },
    Probe {
        id: "platform_files",
        item: 13,
        sh:
            "ls /etc/appliance.info /etc/motd /etc/issue 2>&1; cat /etc/motd /etc/issue 2>/dev/null",
        rc: Rc::Any,
        inside: None,
    },
    Probe {
        id: "userns",
        item: 15,
        sh: "cat /proc/sys/user/max_user_namespaces",
        rc: Rc::Zero,
        inside: Some("/proc/sys/user/max_user_namespaces"),
    },
    Probe {
        id: "unshare",
        item: 15,
        sh: "unshare -Ur true",
        rc: Rc::Refused,
        inside: Some("unshare -Ur true"),
    },
    Probe {
        id: "dmesg",
        item: 17,
        sh: r#"out=$(dmesg 2>&1); rc=$?; printf '%s\n' "$out" | head -1; exit $rc"#,
        rc: Rc::Refused,
        inside: Some("dmesg"),
    },
    Probe {
        id: "lxc_copies",
        item: 18,
        sh: "ls /dev/.lxc/proc /dev/.lxc/sys 2>&1 | head -2",
        rc: Rc::Any,
        inside: None,
    },
    Probe {
        id: "wp_eval",
        item: 19,
        sh: r#"cd / && /usr/local/bin/wp --path="/var/www/$2" eval 'echo php_uname(), "\n", $_SERVER["SERVER_SOFTWARE"] ?? "-", "\n"; echo ini_get("expose_php");' --skip-plugins --skip-themes"#,
        rc: Rc::Zero,
        inside: None,
    },
];

/// The §4 items this command does not run, and why.
const NOT_HERE: &[&str] = &[
    "14 environment: measured through the customer client's ssh path (pct exec passes ours)",
    "16 ssh host keys of every CT: host root",
    "20-22, 24 headers, direct address, DNS, SPF: from outside",
    "23 egress address: a baseline, not judged",
    "25 a test mail's headers: sending one is a write",
    "26 the customer client's logs",
];

/// The shell run inside the CT: `$1` the nonce, `$2` the site's code.
fn script() -> String {
    let mut s = String::from(
        "n=\"$1\"\nb() { printf '\\n%s begin %s\\n' \"$n\" \"$1\"; }\ne() { printf '\\n%s end %s %s\\n' \"$n\" \"$1\" \"$2\"; }\n",
    );
    for p in PROBES {
        s.push_str(&format!(
            "b {id}; ( {sh} ) </dev/null 2>&1; e {id} $?\n",
            id = p.id,
            sh = p.sh
        ));
    }
    s
}

/// The one command: the shell as the site's own user inside its CT, environment emptied.
pub fn command(spec: &SiteSpec, nonce: &str) -> Vec<String> {
    let ct = spec.ct.to_string();
    let sh = script();
    let parts: [&str; 18] = [
        "pct",
        "exec",
        &ct,
        "--",
        "env",
        "-i",
        "PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL=C",
        "runuser",
        "-u",
        &spec.code,
        "--",
        "sh",
        "-c",
        &sh,
        "sh",
        nonce,
        &spec.code,
    ];
    parts.iter().map(|s| (*s).to_owned()).collect()
}

/// One check's answer as read: its text and exit status, or why it was not read.
type Answer = Result<(String, i32), &'static str>;

/// The answer of `id` in the shell's output.
fn answer_of(out: &str, nonce: &str, id: &str) -> Answer {
    let begin = format!("\n{nonce} begin {id}\n");
    let at = out.find(&begin).ok_or("no_begin_mark")? + begin.len();
    let rest = &out[at..];
    let end = format!("\n{nonce} end {id} ");
    let stop = rest.find(&end).ok_or("no_end_mark")?;
    let tail = &rest[stop + end.len()..];
    let rc = tail
        .split('\n')
        .next()
        .and_then(|r| r.trim().parse::<i32>().ok())
        .ok_or("no_exit_status")?;
    Ok((rest[..stop].to_owned(), rc))
}

/// Lines of an `ls` listing that are entries: headers (`dir:`), error lines and blanks
/// left out.
fn listed(text: &str) -> usize {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.ends_with(':') && !l.starts_with("ls:"))
        .count()
}

/// A missing file's `cat … 2>&1`, or nothing at all: `Some(true)`; a refusal: not known.
fn missing_or_empty(text: &str) -> Option<bool> {
    let t = text.trim();
    if t.is_empty() || t.contains("No such file or directory") {
        Some(true)
    } else if t.contains("Permission denied") {
        None
    } else {
        Some(false)
    }
}

/// Whether an answer is what the checklist wants of it; `None` when it cannot be judged.
fn expect(id: &str, text: &str, spec: &SiteSpec) -> Option<bool> {
    let lines = || text.lines().map(str::trim).filter(|l| !l.is_empty());
    let number = || text.trim().parse::<u64>().ok();
    match id {
        "cpuinfo" => {
            // `uniq -c` lines: "<count> model name\t: vCPU"
            let mut any = false;
            for l in lines() {
                let body = l.trim_start_matches(|c: char| c.is_ascii_digit()).trim();
                let (key, value) = body.split_once(':')?;
                if key.trim() != "model name" || value.trim() != "vCPU" {
                    return Some(false);
                }
                any = true;
            }
            any.then_some(true)
        }
        "dmi_vendor" | "dmi_board" => missing_or_empty(text),
        "meminfo" => {
            let mem_kb = lines()
                .find_map(|l| l.strip_prefix("MemTotal:"))
                .and_then(|v| v.trim().trim_end_matches("kB").trim().parse::<u64>().ok())?;
            let cpu_lines = lines().next_back()?.parse::<u64>().ok()?;
            Some(
                mem_kb <= u64::from(spec.limits.memory_mb) * 1024
                    && cpu_lines == u64::from(spec.limits.cores) + 1,
            )
        }
        "cmdline" => number().map(|n| n == 0),
        "cpu_dir" => number().map(|n| n == u64::from(spec.limits.cores)),
        "hw_lists" | "lxc_copies" => Some(listed(text) == 0),
        "hostname" => {
            let mut any = false;
            for l in lines() {
                if l != spec.code {
                    return Some(false);
                }
                any = true;
            }
            any.then_some(true)
        }
        "resolv" => {
            let mut search = Vec::new();
            let mut servers = Vec::new();
            for l in lines() {
                if l.contains("PVE") {
                    return Some(false);
                }
                let mut w = l.split_whitespace();
                match w.next() {
                    Some("search" | "domain") => search.extend(w),
                    Some("nameserver") => servers.extend(w),
                    _ => {}
                }
            }
            Some(search == ["invalid"] && mask::resolver_is_neutral(&servers.join(" ")))
        }
        "hosts" => {
            let want = mask::hosts_text(&spec.code)?;
            let norm = |t: &str| -> Vec<String> {
                t.lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty() && !l.starts_with('#'))
                    .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
                    .collect()
            };
            Some(!text.contains("PVE") && norm(text) == norm(&want))
        }
        "mac" => {
            let mac = text.trim();
            let shaped = mac.len() == 17 && mac.split(':').count() == 6;
            shaped.then(|| mask::hwaddr_is_local(mac))
        }
        "ip6" => Some(text.trim().is_empty()),
        "platform_files" => Some(!lines().any(|l| l == "/etc/appliance.info")),
        "userns" => mask::userns_closed(Some(text)),
        // unshare / dmesg are judged by their exit status; the rest against the hide list
        // only
        _ => Some(true),
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Pass,
    Leak,
    Unread,
}

impl State {
    fn word(self) -> &'static str {
        match self {
            State::Pass => "pass",
            State::Leak => "leak",
            State::Unread => "unread",
        }
    }
}

/// One check judged.
pub struct Judged {
    id: &'static str,
    item: u8,
    /// The `INSIDE_EXPECT` row it answers, if any.
    inside: Option<&'static str>,
    state: State,
    why: &'static str,
    answer: Answer,
}

fn judge(
    p: &Probe,
    answer: &Answer,
    hide: Option<&HostHide>,
    allow: &[String],
    spec: &SiteSpec,
) -> (State, &'static str) {
    let unread = match answer {
        Err(why) => Some(*why),
        Ok((_, rc)) => match p.rc {
            Rc::Zero if *rc != 0 => Some("failed"),
            Rc::Refused if *rc == 126 || *rc == 127 => Some("not_runnable"),
            _ => None,
        },
    };
    if let Some(why) = unread {
        return (State::Unread, why);
    }
    let Ok((text, rc)) = answer else {
        return (State::Unread, "no_answer");
    };
    let seen = without_allowed(text, allow);
    if hide.is_some_and(|h| h.hide(&seen).1 > 0) {
        return (State::Leak, "hide_word");
    }
    if p.rc == Rc::Refused && *rc == 0 {
        return (State::Leak, "not_refused");
    }
    match expect(p.id, text, spec) {
        Some(false) => return (State::Leak, "expect"),
        None => return (State::Unread, "not_judged"),
        Some(true) => {}
    }
    if hide.is_none() {
        return (State::Unread, "hide_words_unread");
    }
    (State::Pass, "")
}

/// Run the checklist in the site's CT and judge each answer.
pub fn check(
    exec: &mut impl Exec,
    spec: &SiteSpec,
    hide: Option<&HostHide>,
    allow: &[String],
    nonce: &str,
) -> Vec<Judged> {
    let out = exec.run(&command(spec, nonce));
    PROBES
        .iter()
        .map(|p| {
            let answer = match &out {
                Ok((text, _cut)) => answer_of(text, nonce, p.id),
                Err(_) => Err("not_run"),
            };
            let (state, why) = judge(p, &answer, hide, allow, spec);
            Judged {
                id: p.id,
                item: p.item,
                inside: p.inside,
                state,
                why,
                answer,
            }
        })
        .collect()
}

/// What goes to standard output (counts and names, no answer), what goes to `--out`
/// (every answer), and the exit code.
pub fn report(
    code: &str,
    judged: &[Judged],
    hide_read: bool,
    allow: &str,
    out: &str,
) -> (Value, Value, u8) {
    let of = |s: State| {
        judged
            .iter()
            .filter(|j| j.state == s)
            .map(|j| j.id)
            .collect::<Vec<_>>()
    };
    let (pass, leak, unread) = (of(State::Pass), of(State::Leak), of(State::Unread));
    let rc = if !leak.is_empty() {
        1
    } else if !unread.is_empty() {
        3
    } else {
        0
    };
    let hide_words = if hide_read { "read" } else { "unread" };
    let stdout = json!({
        "check": "site-leakcheck",
        "site": code,
        "counts": { "pass": pass.len(), "leak": leak.len(), "unread": unread.len() },
        "leak": leak,
        "unread": unread,
        "hide_words": hide_words,
        "allow_list": allow,
        "not_here": NOT_HERE,
        "answers_in": out,
    });
    let file = json!({
        "check": "site-leakcheck",
        "site": code,
        "hide_words": hide_words,
        "allow_list": allow,
        "inside_expect": mask::INSIDE_EXPECT.iter().map(|(r, w)| json!({ "read": r, "want": w })).collect::<Vec<_>>(),
        "checks": judged.iter().map(|j| {
            let (text, rc) = match &j.answer {
                Ok((t, rc)) => (Value::from(t.as_str()), Value::from(*rc)),
                Err(_) => (Value::Null, Value::Null),
            };
            json!({ "id": j.id, "item": j.item, "inside_expect": j.inside, "state": j.state.word(), "why": j.why, "rc": rc, "answer": text })
        }).collect::<Vec<_>>(),
        "not_here": NOT_HERE,
    });
    (stdout, file, rc)
}

/// The host's hide list as `customer logs` builds it, except that a **missing** list is
/// not read either: without it the provider's name is not looked for.
fn read_hide(ct: u32) -> Option<HostHide> {
    let name = fs::read_to_string(HOSTNAME).ok();
    let mut cmd = Command::new("hostname");
    cmd.arg("-f").env("LC_ALL", "C");
    let full = run_capture(&mut cmd, Duration::from_secs(5)).ok();
    let list = fs::read_to_string(HIDE_WORDS).ok();
    host_hide(
        name.as_deref(),
        full.as_deref(),
        list.as_deref().ok_or(()).map(Some),
        ct,
    )
}

/// The allow list as read, and what became of it: `read`, or `missing` / `unread` /
/// `not_private` (root's and 0600 only) — then it is not applied.
fn read_allow(hide: Option<&HostHide>) -> (Vec<String>, &'static str) {
    use std::os::unix::fs::MetadataExt;
    let meta = match fs::metadata(ALLOW_WORDS) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (Vec::new(), "missing"),
        Err(_) => return (Vec::new(), "unread"),
    };
    if meta.uid() != 0 || meta.mode() & 0o077 != 0 {
        return (Vec::new(), "not_private");
    }
    match (fs::read_to_string(ALLOW_WORDS), hide) {
        (Ok(t), Some(h)) => (allow_list(&t, h), "read"),
        _ => (Vec::new(), "unread"),
    }
}

fn nonce() -> Result<String, String> {
    use std::io::Read;
    let mut b = [0u8; 12];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("/dev/urandom: {e}"))?;
    Ok(format!(
        "ez2-leak-{}",
        b.iter().map(|x| format!("{x:02x}")).collect::<String>()
    ))
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = RECORDS.to_owned();
    let mut out: Option<String> = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--out" => out = Some(it.next().ok_or("--out needs a file")?.clone()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let out = out
        .filter(|o| !o.is_empty())
        .ok_or("needs --out <file>: the answers are host information and go there only (0600)")?;
    if inside_git_tree(Path::new(&out)) {
        return Err(format!(
            "{out} is inside a git work tree: the answers are host information; write it elsewhere"
        ));
    }
    let spec = crate::quarantinecmd::load_spec(&code, &records)?;
    let hide = read_hide(spec.ct);
    let (allow, allow_state) = read_allow(hide.as_ref());
    let judged = check(&mut HostExec, &spec, hide.as_ref(), &allow, &nonce()?);
    let (stdout, file, rc) = report(&spec.code, &judged, hide.is_some(), allow_state, &out);
    let text = serde_json::to_string_pretty(&file).map_err(|e| e.to_string())?;
    write_private(&out, &format!("{text}\n"))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&stdout).map_err(|e| e.to_string())?
    );
    Ok(rc)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::site::Limits;
    use aib_cloud::svc;

    const NONCE: &str = "ez2-leak-00112233445566778899aabb";

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-abc123",
            112,
            12,
            &["shop.example.test"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
    }

    fn hide() -> HostHide {
        HostHide::new(
            &svc::host_names("node7", Some("node7.tail0abc.ts.net")),
            "vendor-x\n203.0.113.70\n",
            112,
        )
    }

    /// What a masked site answers, in the shapes of the 11-12 baseline run.
    fn clean(id: &str) -> (String, i32) {
        let s = |t: &str| t.to_owned();
        match id {
            "cpuinfo" => (s("      2 model name\t: vCPU\n"), 0),
            "dmi_vendor" => (s("cat: /sys/class/dmi/id/sys_vendor: No such file or directory\n"), 1),
            "dmi_board" => (s(""), 0),
            "meminfo" => (s("MemTotal:        1048576 kB\nMemFree:          524288 kB\nMemAvailable:     786432 kB\n12.00 40.00\n3\n"), 0),
            "cmdline" => (s("0\n"), 0),
            "cpu_dir" => (s("2\n"), 0),
            "hw_lists" => (s("ls: cannot access '/sys/block': No such file or directory\n"), 0),
            "hostname" => (s("s-abc123\ns-abc123\n"), 0),
            "resolv" => (s("search invalid\nnameserver 1.1.1.1\n"), 0),
            "hosts" => (mask::hosts_text("s-abc123").unwrap(), 0),
            "mac" => (s("06:aa:bb:cc:dd:ee\n"), 0),
            "ip6" => (s(""), 0),
            "route" => (s("default via 10.77.0.1 dev eth0\n10.77.0.0/24 dev eth0 proto kernel scope link src 10.77.0.12\n"), 0),
            "os_apt" => (s("PRETTY_NAME=\"Debian GNU/Linux 13 (trixie)\"\ndeb http://deb.debian.org/debian trixie main\n"), 0),
            "platform_files" => (s("ls: cannot access '/etc/appliance.info': No such file or directory\n/etc/issue\n/etc/motd\nDebian GNU/Linux 13\n"), 0),
            "userns" => (s("0\n"), 0),
            "unshare" => (s("unshare: unshare failed: Operation not permitted\n"), 1),
            "dmesg" => (s("dmesg: read kernel buffer failed: Operation not permitted\n"), 1),
            "lxc_copies" => (s("ls: cannot access '/dev/.lxc/proc': No such file or directory\n"), 0),
            "wp_eval" => (s("Linux s-abc123 7.0.14-28-xyz #2 SMP XYZ 7.0.14-28 x86_64\nnginx\n0"), 0),
            other => panic!("no clean answer for {other}"),
        }
    }

    /// The shell's whole output for the given answers (`None` leaves a check out).
    fn shell_out(nonce: &str, answer: &dyn Fn(&str) -> Option<(String, i32)>) -> String {
        let mut o = String::new();
        for p in PROBES {
            o.push_str(&format!("\n{nonce} begin {}\n", p.id));
            if let Some((text, rc)) = answer(p.id) {
                o.push_str(&text);
                o.push_str(&format!("\n{nonce} end {} {rc}\n", p.id));
            }
        }
        o
    }

    struct Fake {
        seen: Vec<Vec<String>>,
        out: Result<(String, bool), String>,
    }

    impl Exec for Fake {
        fn run(&mut self, argv: &[String]) -> Result<(String, bool), String> {
            self.seen.push(argv.to_vec());
            self.out.clone()
        }
    }

    fn run_with(
        answer: &dyn Fn(&str) -> Option<(String, i32)>,
        hide: Option<&HostHide>,
    ) -> Vec<Judged> {
        let mut f = Fake {
            seen: vec![],
            out: Ok((shell_out(NONCE, answer), false)),
        };
        check(&mut f, &spec(), hide, &[], NONCE)
    }

    fn state(j: &[Judged], id: &str) -> (State, &'static str) {
        let x = j.iter().find(|x| x.id == id).unwrap();
        (x.state, x.why)
    }

    #[test]
    fn a_masked_site_passes_every_check() {
        let h = hide();
        let j = run_with(&|id| Some(clean(id)), Some(&h));
        for x in &j {
            assert_eq!(x.state, State::Pass, "{} {}", x.id, x.why);
        }
        assert_eq!(report("s-abc123", &j, true, "read", "/root/x.json").2, 0);
    }

    #[test]
    fn a_leak_word_in_any_answer_is_a_leak() {
        let h = hide();
        for word in [
            "vendor-x",
            "Vendor-X",
            "mirror.vendor-x.example",
            "203.0.113.70",
            "node7",
            "tail0abc.ts.net",
            "ct112",
            "/etc/pve/nodes",
        ] {
            for p in PROBES {
                let j = run_with(
                    &|id| {
                        let (t, rc) = clean(id);
                        Some(if id == p.id {
                            (format!("{t}\n{word}\n"), rc)
                        } else {
                            (t, rc)
                        })
                    },
                    Some(&h),
                );
                assert_eq!(
                    state(&j, p.id),
                    (State::Leak, "hide_word"),
                    "{word} in {}",
                    p.id
                );
                assert_eq!(report("s-abc123", &j, true, "read", "/root/x.json").2, 1);
            }
        }
    }

    #[test]
    fn words_match_whole_and_the_allow_list_only_blanks_its_phrases() {
        let h = hide();
        let with = |route: &str, allow: &[String]| {
            let mut f = Fake {
                seen: vec![],
                out: Ok((
                    shell_out(NONCE, &|id| {
                        Some(if id == "route" {
                            (route.to_owned(), 0)
                        } else {
                            clean(id)
                        })
                    }),
                    false,
                )),
            };
            state(&check(&mut f, &spec(), Some(&h), allow, NONCE), "route")
        };
        // part of a longer word, or of a longer address, is not the word
        assert_eq!(with("via vendor-xyz 203.0.113.700\n", &[]).0, State::Pass);
        // an allowed phrase is blanked; the same word elsewhere still leaks
        let allow = allow_list("# harmless\nDocs.Vendor-X.example\nvendor-x\nab\n", &h);
        assert_eq!(
            allow,
            ["docs.vendor-x.example"],
            "the bare word and a short one are dropped"
        );
        assert_eq!(with("see docs.vendor-x.example\n", &[]).0, State::Leak);
        assert_eq!(with("see DOCS.VENDOR-X.EXAMPLE\n", &allow).0, State::Pass);
        assert_eq!(
            with("see docs.vendor-x.example and vendor-x\n", &allow),
            (State::Leak, "hide_word")
        );
    }

    #[test]
    fn what_the_checklist_does_not_want_is_a_leak_even_without_a_word() {
        let h = hide();
        let base = |id: &str, t: &str, rc: i32| {
            run_with(
                &|x| {
                    Some(if x == id {
                        (t.to_owned(), rc)
                    } else {
                        clean(x)
                    })
                },
                Some(&h),
            )
        };
        // the 11-12 baseline's shapes, host words taken out
        for (id, t, rc) in [
            ("cpuinfo", "      2 cache size\t: 32768 KB\n      2 model name\t: Some CPU 9000X\n", 0),
            ("dmi_vendor", "Board Maker Y\n", 0),
            ("cmdline", "118\n", 0),
            ("cpu_dir", "12\n", 0),
            ("hw_lists", "/sys/block:\ndm-0\ndm-1\n", 0),
            ("resolv", "# --- BEGIN PVE ---\nsearch example.ts.net\nnameserver 1.1.1.1\n# --- END PVE ---\n", 0),
            ("resolv", "search invalid\nnameserver 10.77.0.1\n", 0),
            ("hosts", "127.0.0.1 localhost\n10.77.0.12 s-abc123.lan s-abc123\n", 0),
            ("mac", "00:11:22:33:44:55\n", 0),
            ("ip6", "inet6 2001:db8::5/64 scope global\n", 0),
            ("platform_files", "/etc/appliance.info\n/etc/issue\n/etc/motd\n", 0),
            ("userns", "2147483647\n", 0),
            ("unshare", "", 0),
            ("lxc_copies", "/dev/.lxc/proc:\n1\n", 0),
            ("hostname", "s-abc123\ns-abc123.lan\n", 0),
        ] {
            let j = base(id, t, rc);
            assert_eq!(state(&j, id).0, State::Leak, "{id}: {t:?}");
        }
    }

    #[test]
    fn an_unreadable_probe_is_not_a_pass() {
        let h = hide();
        // the shell did not run at all
        let mut f = Fake {
            seen: vec![],
            out: Err("pct: timeout".to_owned()),
        };
        let j = check(&mut f, &spec(), Some(&h), &[], NONCE);
        assert!(j
            .iter()
            .all(|x| x.state == State::Unread && x.why == "not_run"));
        assert_eq!(report("s-abc123", &j, true, "read", "/root/x.json").2, 3);
        // cut before its end mark
        let j = run_with(
            &|id| if id == "hosts" { None } else { Some(clean(id)) },
            Some(&h),
        );
        assert_eq!(state(&j, "hosts"), (State::Unread, "no_end_mark"));
        // failed where it must not
        let j = run_with(
            &|id| {
                Some(if id == "resolv" {
                    (String::new(), 1)
                } else {
                    clean(id)
                })
            },
            Some(&h),
        );
        assert_eq!(state(&j, "resolv"), (State::Unread, "failed"));
        // a refusal check whose program is not there
        let j = run_with(
            &|id| {
                Some(if id == "unshare" {
                    ("sh: unshare: not found".to_owned(), 127)
                } else {
                    clean(id)
                })
            },
            Some(&h),
        );
        assert_eq!(state(&j, "unshare"), (State::Unread, "not_runnable"));
        // an answer that cannot be judged
        let j = run_with(
            &|id| {
                Some(if id == "cpu_dir" {
                    ("ls: Permission denied".to_owned(), 2)
                } else {
                    clean(id)
                })
            },
            Some(&h),
        );
        assert_eq!(state(&j, "cpu_dir"), (State::Unread, "not_judged"));
        // no hide list: nothing passes
        let j = run_with(&|id| Some(clean(id)), None);
        assert!(j.iter().all(|x| x.state != State::Pass));
        assert_eq!(report("s-abc123", &j, false, "read", "/root/x.json").2, 3);
    }

    #[test]
    fn a_mark_without_the_nonce_does_not_end_a_check() {
        let h = hide();
        let j = run_with(
            &|id| {
                Some(if id == "platform_files" {
                    (
                        "/etc/motd\nez2-leak-ffff end platform_files 0\nVENDOR-X\n".to_owned(),
                        0,
                    )
                } else {
                    clean(id)
                })
            },
            Some(&h),
        );
        assert_eq!(state(&j, "platform_files"), (State::Leak, "hide_word"));
    }

    #[test]
    fn values_never_reach_stdout() {
        let h = hide();
        let leaked = "VENDOR-X 203.0.113.70 node7.tail0abc.ts.net";
        let j = run_with(
            &|id| {
                let (t, rc) = clean(id);
                Some((format!("{t}\n{leaked}\nPRIVATE-ANSWER-{id}\n"), rc))
            },
            Some(&h),
        );
        let (stdout, file, rc) = report("s-abc123", &j, true, "read", "/root/leak.json");
        assert_eq!(rc, 1);
        let printed = serde_json::to_string_pretty(&stdout).unwrap();
        for v in [
            "vendor-x",
            "203.0.113.70",
            "node7",
            "tail0abc",
            "PRIVATE-ANSWER",
            "vCPU",
            "1.1.1.1",
        ] {
            assert!(!printed.contains(v), "{v} reached stdout: {printed}");
        }
        assert_eq!(stdout["counts"]["leak"], json!(PROBES.len()));
        let kept = serde_json::to_string(&file).unwrap();
        assert!(kept.contains("PRIVATE-ANSWER-cpuinfo") && kept.contains("203.0.113.70"));
    }

    #[test]
    fn every_inside_expectation_has_its_check() {
        for (read, _) in mask::INSIDE_EXPECT {
            assert!(
                PROBES.iter().any(|p| p.inside == Some(*read)),
                "no check reads {read}"
            );
        }
        for p in PROBES {
            if let Some(r) = p.inside {
                assert!(mask::INSIDE_EXPECT.iter().any(|(x, _)| *x == r), "{}", p.id);
            }
        }
        let mut ids: Vec<&str> = PROBES.iter().map(|p| p.id).collect();
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), PROBES.len());
    }

    #[test]
    fn the_one_command_runs_as_the_site_user_with_an_empty_environment() {
        let mut f = Fake {
            seen: vec![],
            out: Err("x".to_owned()),
        };
        let _ = check(&mut f, &spec(), None, &[], NONCE);
        assert_eq!(f.seen.len(), 1, "one pct exec");
        let c = &f.seen[0];
        assert_eq!(&c[..6], ["pct", "exec", "112", "--", "env", "-i"]);
        let ru = c.iter().position(|a| a == "runuser").unwrap();
        assert_eq!(
            &c[ru..ru + 6],
            ["runuser", "-u", "s-abc123", "--", "sh", "-c"]
        );
        assert_eq!(&c[c.len() - 2..], [NONCE, "s-abc123"]);
        let sh = &c[ru + 6];
        assert_eq!(sh.matches(" eval ").count(), 1, "one wp eval");
        assert!(sh.contains("/usr/local/bin/wp --path="));
        for word in ["rm ", " > /", "tee ", "sed -i", "wp option", "wp config"] {
            assert!(!sh.contains(word), "{word}");
        }
    }

    #[test]
    fn without_out_nothing_runs() {
        let e = cmd(&["s-abc123".to_owned()]).unwrap_err();
        assert!(e.contains("--out"), "{e}");
    }
}
