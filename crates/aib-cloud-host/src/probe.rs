// SPDX-License-Identifier: Apache-2.0
//! Execute an `aib_cloud::isolation` probe plan on a Proxmox host (or a public vantage
//! point) and write the report `judge()` accepts. Rust rewrite of the Python runner
//! `ez2-isolation-probe` (retired 11-09 after this binary re-ran the full host A matrix with
//! the same `plan_fnv` and identical cells; #568 r2 M2 rules kept one for one):
//!
//! * the whole plan is validated against a whitelist first (id charset, `from` shape,
//!   kind, ip parses, port range, expect, unique ids); one bad cell and nothing runs;
//! * `from = ct:<id>`: `lxc-info -n <id> -p -H` gives the init pid; the probe re-runs this
//!   binary under `nsenter -t <pid> -n`, after checking that the namespace it lands in is
//!   `/proc/<pid>/ns/net` and is not the host's own;
//! * every child process is an argument list, never a shell string; TCP is a plain
//!   `connect` with a 3 s timeout, DNS one UDP query for `example.com` to `<ip>:53`, ICMP
//!   one `ping -c 1 -W 2`;
//! * `plan_fnv` is `aib_cloud::isolation::plan_fingerprint` over the plan as loaded, so the
//!   report is bound to exactly the plan the host ran.

use std::fs;
use std::io::{Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, UdpSocket};
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use aib_cloud::isolation::plan_fingerprint;
use serde_json::{json, Map, Value};

const KINDS: [&str; 3] = ["tcp", "dns", "icmp"];
const EXPECTS: [&str; 4] = ["open", "blocked", "closed", "observe"]; // observe = recorded, never judged (#587 L1)
const TCP_TIMEOUT: Duration = Duration::from_secs(3);
const DNS_TIMEOUT: Duration = Duration::from_secs(2);

/// One validated cell of the plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    pub id: String,
    pub from: Source,
    pub kind: String,
    pub ip: IpAddr,
    pub port: u16,
    pub expect: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    Host,
    Ct(u32),
    External,
}

impl Source {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "host" => Some(Source::Host),
            "external" => Some(Source::External),
            _ => {
                let n = s.strip_prefix("ct:")?;
                if n.is_empty()
                    || n.len() > 9
                    || n.starts_with('0')
                    || !n.bytes().all(|b| b.is_ascii_digit())
                {
                    return None;
                }
                n.parse().ok().map(Source::Ct)
            }
        }
    }
    fn tag(&self) -> String {
        match self {
            Source::Host => "host".to_owned(),
            Source::Ct(n) => format!("ct:{n}"),
            Source::External => "external".to_owned(),
        }
    }
}

/// `^[a-z0-9][a-z0-9-]{0,63}$`
fn valid_id(id: &str) -> bool {
    let b = id.as_bytes();
    let Some(first) = b.first() else { return false };
    b.len() <= 64
        && (first.is_ascii_lowercase() || first.is_ascii_digit())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
}

/// Validate a plan document as a whole. Any defect refuses the whole plan.
pub fn validate_plan(plan: &Value) -> Result<Vec<Cell>, String> {
    let arr = plan
        .as_array()
        .filter(|a| !a.is_empty())
        .ok_or("plan is not a non-empty array")?;
    let mut cells: Vec<Cell> = Vec::with_capacity(arr.len());
    for (i, c) in arr.iter().enumerate() {
        let obj = c
            .as_object()
            .ok_or_else(|| format!("plan[{i}] is not an object"))?;
        for k in obj.keys() {
            if !["id", "from", "kind", "ip", "port", "expect"].contains(&k.as_str()) {
                return Err(format!("plan[{i}] has unknown key {k:?}"));
            }
        }
        let id = obj
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("plan[{i}] has no id"))?;
        if !valid_id(id) {
            return Err(format!("plan[{i}] id {id:?} not in [a-z0-9-]"));
        }
        if cells.iter().any(|x| x.id == id) {
            return Err(format!("duplicate id {id}"));
        }
        let from_s = obj.get("from").and_then(Value::as_str).unwrap_or("");
        let from = Source::parse(from_s)
            .ok_or_else(|| format!("{id}: from {from_s:?} must be host | ct:<id> | external"))?;
        let kind = obj.get("kind").and_then(Value::as_str).unwrap_or("");
        if !KINDS.contains(&kind) {
            return Err(format!("{id}: kind {kind:?} not in {KINDS:?}"));
        }
        let ip_s = obj.get("ip").and_then(Value::as_str).unwrap_or("");
        let ip: IpAddr = ip_s
            .parse()
            .map_err(|_| format!("{id}: ip {ip_s:?} is not an address"))?;
        let port = obj
            .get("port")
            .and_then(Value::as_u64)
            .ok_or_else(|| format!("{id}: port is not an integer"))?;
        if port > 65535 {
            return Err(format!("{id}: port {port} out of range"));
        }
        if kind == "icmp" && port != 0 {
            return Err(format!("{id}: icmp cell must have port 0"));
        }
        if kind != "icmp" && port == 0 {
            return Err(format!("{id}: {kind} cell needs a port"));
        }
        let expect = obj.get("expect").and_then(Value::as_str).unwrap_or("");
        if !EXPECTS.contains(&expect) {
            return Err(format!("{id}: expect {expect:?} not in {EXPECTS:?}"));
        }
        cells.push(Cell {
            id: id.to_owned(),
            from,
            kind: kind.to_owned(),
            ip,
            port: port as u16,
            expect: expect.to_owned(),
        });
    }
    Ok(cells)
}

// ---------------------------------------------------------------- probes (this namespace)

fn probe_tcp(ip: IpAddr, port: u16) -> bool {
    TcpStream::connect_timeout(&SocketAddr::new(ip, port), TCP_TIMEOUT).is_ok()
}

/// A minimal DNS query packet: id, RD flag, one question `example.com` A IN.
pub fn dns_query_packet(id: u16) -> Vec<u8> {
    let mut p = Vec::with_capacity(32);
    p.extend_from_slice(&id.to_be_bytes());
    p.extend_from_slice(&[0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]);
    for label in ["example", "com"] {
        p.push(label.len() as u8);
        p.extend_from_slice(label.as_bytes());
    }
    p.push(0);
    p.extend_from_slice(&[0, 1, 0, 1]);
    p
}

fn probe_dns(ip: IpAddr, port: u16) -> bool {
    let bind = if ip.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" };
    let Ok(sock) = UdpSocket::bind(bind) else {
        return false;
    };
    if sock.set_read_timeout(Some(DNS_TIMEOUT)).is_err() {
        return false;
    }
    let id = (std::process::id() & 0xffff) as u16 ^ 0x5a5a;
    if sock
        .send_to(&dns_query_packet(id), SocketAddr::new(ip, port))
        .is_err()
    {
        return false;
    }
    let mut buf = [0u8; 512];
    // c477a #584 L4: the answer must come from the address we asked, not from anyone who guessed
    // the id. One datagram only: a stray packet is a "closed", not a retry.
    match sock.recv_from(&mut buf) {
        Ok((n, from)) => {
            from == SocketAddr::new(ip, port)
                && n >= 12
                && buf[0..2] == id.to_be_bytes()
                && buf[2] & 0x80 != 0
        }
        Err(_) => false,
    }
}

fn probe_icmp(ip: IpAddr) -> bool {
    let mut cmd = Command::new("ping");
    cmd.args(["-c", "1", "-W", "2", "--"]).arg(ip.to_string());
    run_quiet(&mut cmd, Duration::from_secs(5))
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Run a probe of `kind` in the current network namespace.
pub fn probe_here(kind: &str, ip: IpAddr, port: u16) -> bool {
    match kind {
        "tcp" => probe_tcp(ip, port),
        "dns" => probe_dns(ip, port),
        "icmp" => probe_icmp(ip),
        _ => false,
    }
}

/// Wait for a child up to `timeout`, polling every 20 ms; on timeout kill it and reap it.
///
/// ADR 0005 named exception: this binary is a short-lived CLI that runs on the Proxmox host
/// (not inside aibd, which owns the timer registry); the only clock it needs is "give the
/// child at most N seconds". Nothing else in this crate sleeps.
#[allow(clippy::disallowed_methods)]
pub(crate) fn wait_with_timeout(
    child: &mut std::process::Child,
    timeout: Duration,
) -> Result<std::process::ExitStatus, String> {
    let t0 = Instant::now();
    loop {
        if let Some(st) = child.try_wait().map_err(|e| e.to_string())? {
            return Ok(st);
        }
        if t0.elapsed() > timeout {
            // c477a #584 L1': the child was spawned as its own process group, so grandchildren
            // (pct exec → lxc-attach, pct restore → tar / zstd) that inherited our pipes die
            // with it; otherwise the pipe readers would wait for an EOF that never comes.
            let _ = Command::new("kill")
                .args(["-KILL", &format!("-{}", child.id())])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = child.kill();
            let _ = child.wait();
            return Err("timeout".to_owned());
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// Every child becomes the leader of its own process group so a timeout can take its whole
/// subtree down (`kill -KILL -<pid>`), not just the direct child.
fn own_group(cmd: &mut Command) -> &mut Command {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0)
    }
    #[cfg(not(unix))]
    {
        cmd
    }
}

pub(crate) fn run_quiet(
    cmd: &mut Command,
    timeout: Duration,
) -> Result<std::process::ExitStatus, String> {
    let mut child = own_group(cmd)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    wait_with_timeout(&mut child, timeout)
}

/// Drain one pipe on its own thread so a child that writes more than the pipe buffer
/// (c477a #584 L3: ~64 KB) can never block against our `wait`.
fn drain(pipe: Option<impl Read + Send + 'static>) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(mut p) = pipe {
            let _ = p.read_to_string(&mut s);
        }
        s
    })
}

pub(crate) fn run_capture(cmd: &mut Command, timeout: Duration) -> Result<String, String> {
    let mut child = own_group(cmd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    let out_h = drain(child.stdout.take());
    let err_h = drain(child.stderr.take());
    let status = match wait_with_timeout(&mut child, timeout) {
        Ok(s) => s,
        Err(e) => {
            // timeout: the group is dead, but do not wait on the pipes at all (c477a #584 L1');
            // the reader threads finish on their own once the last holder closes.
            drop(out_h);
            drop(err_h);
            return Err(format!("{:?}: {e}", cmd.get_program()));
        }
    };
    // normal exit: anything the child left behind in its group (a backgrounded grandchild
    // still holding our pipes) is killed before we wait for the readers (c477a #585 r3 L).
    let _ = Command::new("kill")
        .args(["-KILL", &format!("-{}", child.id())])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let out = out_h.join().unwrap_or_default();
    let err = err_h.join().unwrap_or_default();
    if !status.success() {
        return Err(format!(
            "{:?} exited {status}: {}",
            cmd.get_program(),
            err.trim()
        ));
    }
    Ok(out)
}

/// [`run_capture`] for a child whose output is **not trusted to end**: at most `max_bytes`
/// of its standard output are read, and once that much has arrived — or the time is up —
/// the child's whole process group is killed. Returns what was read and whether the child
/// had more to say. Standard error is not read at all.
///
/// 33e28, #631 seg. 5 M1: `pct exec <ct> -- journalctl -n N` leaves the counting to a
/// program inside the customer's container; whoever replaces that program must not be
/// able to fill this host's memory.
#[allow(clippy::disallowed_methods)] // the same short poll as wait_with_timeout
pub(crate) fn run_capture_bounded(
    cmd: &mut Command,
    timeout: Duration,
    max_bytes: usize,
) -> Result<(String, bool), String> {
    let mut child = own_group(cmd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    let pipe = child.stdout.take();
    let cap = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let mut more = false;
        if let Some(p) = pipe {
            let mut limited = p.take(cap);
            let _ = limited.read_to_end(&mut buf);
            // one byte past the cap tells "exactly that much" from "more than that"
            more = matches!(limited.into_inner().read(&mut [0u8; 1]), Ok(1));
        }
        (buf, more)
    });
    let t0 = Instant::now();
    while !reader.is_finished() && t0.elapsed() <= timeout {
        std::thread::sleep(Duration::from_millis(20));
    }
    let finished = reader.is_finished();
    let (buf, more) = if finished {
        reader.join().unwrap_or_default()
    } else {
        (Vec::new(), false)
    };
    // the reader is done (end of output, or the cap) or the time is up: in every case
    // nothing more is wanted from the group
    let kill_group = |id: u32| {
        let _ = Command::new("kill")
            .args(["-KILL", &format!("-{id}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    };
    if !finished || more {
        kill_group(child.id());
        let _ = child.kill();
        let _ = child.wait();
        if !finished {
            return Err(format!("{:?}: timeout", cmd.get_program()));
        }
        return Ok((String::from_utf8_lossy(&buf).into_owned(), true));
    }
    let left = timeout.saturating_sub(t0.elapsed());
    let status =
        wait_with_timeout(&mut child, left).map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    kill_group(child.id());
    if !status.success() {
        return Err(format!("{:?} exited {status}", cmd.get_program()));
    }
    Ok((String::from_utf8_lossy(&buf).into_owned(), false))
}

/// [`run_capture_bounded`] that also hands the child `input` on its standard input (and
/// closes it): for what must never be in an argument list, such as a password. More than
/// `max_bytes` of output is a failure, not a cut answer: a program that says more than it
/// should is not believed.
#[allow(clippy::disallowed_methods)] // the same short poll as wait_with_timeout
pub(crate) fn run_bounded_input(
    cmd: &mut Command,
    timeout: Duration,
    max_bytes: usize,
    input: &[u8],
) -> Result<String, String> {
    let mut child = own_group(cmd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    let program = format!("{:?}", cmd.get_program());
    let mut stdin = child.stdin.take();
    let feed = input.to_vec();
    let writer = std::thread::spawn(move || {
        if let Some(mut w) = stdin.take() {
            // a child that does not read is not an error here: its answer decides
            let _ = w.write_all(&feed);
        }
    });
    let pipe = child.stdout.take();
    let cap = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let mut more = false;
        if let Some(p) = pipe {
            let mut limited = p.take(cap);
            let _ = limited.read_to_end(&mut buf);
            more = matches!(limited.into_inner().read(&mut [0u8; 1]), Ok(1));
        }
        (buf, more)
    });
    let t0 = Instant::now();
    while !reader.is_finished() && t0.elapsed() <= timeout {
        std::thread::sleep(Duration::from_millis(20));
    }
    let finished = reader.is_finished();
    let kill_group = |id: u32| {
        let _ = Command::new("kill")
            .args(["-KILL", &format!("-{id}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    };
    let (buf, more) = if finished {
        reader.join().unwrap_or_default()
    } else {
        (Vec::new(), false)
    };
    if !finished || more {
        kill_group(child.id());
        let _ = child.kill();
        let _ = child.wait();
        drop(writer);
        return Err(if finished {
            format!("{program}: more than {max_bytes} bytes of output")
        } else {
            format!("{program}: timeout")
        });
    }
    let left = timeout.saturating_sub(t0.elapsed());
    let status = wait_with_timeout(&mut child, left).map_err(|e| format!("{program}: {e}"))?;
    kill_group(child.id());
    let _ = writer.join();
    if !status.success() {
        return Err(format!("{program} exited {status}"));
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

// ---------------------------------------------------------------- container namespaces

fn netns_of(pid: &str) -> Result<String, String> {
    fs::read_link(format!("/proc/{pid}/ns/net"))
        .map(|p| p.to_string_lossy().into_owned())
        .map_err(|e| format!("/proc/{pid}/ns/net: {e}"))
}

fn self_netns() -> Result<String, String> {
    netns_of("self")
}

/// `lxc-info -n <ct> -p -H` → init pid (digits only, > 1).
fn ct_init_pid(ct: u32) -> Result<String, String> {
    let mut cmd = Command::new("lxc-info");
    cmd.args(["-n", &ct.to_string(), "-p", "-H"]);
    let out = run_capture(&mut cmd, Duration::from_secs(10))
        .map_err(|e| format!("ct {ct}: {e} (not running?)"))?;
    let pid = out.trim();
    if pid.is_empty() || !pid.bytes().all(|b| b.is_ascii_digit()) || pid == "0" || pid == "1" {
        return Err(format!("ct {ct}: lxc-info gave no init pid ({pid:?})"));
    }
    Ok(pid.to_owned())
}

/// The nsenter prefix for a CT, proven: the namespace we land in is the CT's and not ours.
/// Returns the prefix and the namespace it must keep landing in; every later cell re-checks
/// that (c477a #584 L1: a CT restart mid-run frees the init pid, and a host process could
/// inherit it, so one proof at the start is not enough).
fn nsenter_for(ct: u32, host_ns: &str, me: &Path) -> Result<(Vec<String>, String), String> {
    let pid = ct_init_pid(ct)?;
    let want = netns_of(&pid)?;
    if want == host_ns {
        return Err(format!(
            "ct {ct}: init pid {pid} shares the host network namespace"
        ));
    }
    let prefix = vec![
        "nsenter".to_owned(),
        "-t".to_owned(),
        pid.clone(),
        "-n".to_owned(),
        "--".to_owned(),
    ];
    let mut cmd = Command::new(&prefix[0]);
    cmd.args(&prefix[1..]).arg(me).args(["probe", "netns"]);
    let got = run_capture(&mut cmd, Duration::from_secs(10))?;
    if got.trim() != want {
        return Err(format!(
            "ct {ct}: nsenter landed in {:?}, expected {want:?}",
            got.trim()
        ));
    }
    Ok((prefix, want))
}

/// `probe one` prints `open ns=<netns>` / `closed ns=<netns>`: the verdict and the namespace
/// it was measured in. Both parts are required.
pub fn parse_one_output(out: &str) -> Option<(bool, &str)> {
    let mut it = out.split_whitespace();
    let verdict = match it.next()? {
        "open" => true,
        "closed" => false,
        _ => return None,
    };
    let ns = it.next()?.strip_prefix("ns=")?;
    if ns.is_empty() || it.next().is_some() {
        return None;
    }
    Some((verdict, ns))
}

/// One cell inside a CT. `Err` = the measurement is not trustworthy (nsenter failed, output
/// unreadable, or it ran in a namespace other than the one proven for this CT): the whole
/// run must stop, a report must not be written from it.
fn probe_in(
    prefix: &[String],
    want_ns: &str,
    me: &Path,
    kind: &str,
    ip: IpAddr,
    port: u16,
) -> Result<bool, String> {
    let mut cmd = Command::new(&prefix[0]);
    cmd.args(&prefix[1..])
        .arg(me)
        .args(["probe", "one", kind])
        .arg(ip.to_string())
        .arg(port.to_string());
    let out = run_capture(&mut cmd, Duration::from_secs(12))?;
    let (open, ns) =
        parse_one_output(&out).ok_or_else(|| format!("probe one printed {:?}", out.trim()))?;
    if ns != want_ns {
        return Err(format!(
            "netns changed mid-run: cell measured in {ns:?}, proven namespace was {want_ns:?} (CT restarted?)"
        ));
    }
    Ok(open)
}

// ---------------------------------------------------------------- commands

pub(crate) fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// FNV-1a 64 (hex) of the trimmed `/etc/machine-id`, or `"unknown"`; the id itself never
/// leaves the host, the hash is enough to tell two hosts' receipts apart.
fn machine_id_fingerprint() -> String {
    let Ok(text) = fs::read_to_string("/etc/machine-id") else {
        return "unknown".to_owned();
    };
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in text.trim().bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{h:016x}")
}

struct RunArgs {
    plan: String,
    host: IpAddr,
    out: Option<String>,
    external: bool,
}

fn parse_run(args: &[String]) -> Result<RunArgs, String> {
    let mut it = args.iter();
    let plan = it
        .next()
        .filter(|a| !a.starts_with("--"))
        .ok_or("run needs <plan.json>")?
        .clone();
    let (mut host, mut out, mut external) = (None, None, false);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--host" => host = Some(it.next().ok_or("--host needs a value")?.clone()),
            "--out" => out = Some(it.next().ok_or("--out needs a value")?.clone()),
            "--external" => external = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let host_s =
        host.ok_or("--host <public ip> is required (judge() refuses a report without it)")?;
    let host: IpAddr = host_s
        .parse()
        .map_err(|_| format!("--host {host_s:?} is not an address"))?;
    Ok(RunArgs {
        plan,
        host,
        out,
        external,
    })
}

fn write_or_print(out: &Option<String>, doc: &Value, what: &str) -> Result<(), String> {
    let text = serde_json::to_string_pretty(doc).map_err(|e| e.to_string())?;
    match out {
        Some(p) => {
            fs::write(p, text + "\n").map_err(|e| format!("{p}: {e}"))?;
            eprintln!("{what} → {p}");
        }
        None => println!("{text}"),
    }
    Ok(())
}

fn cmd_run(args: &[String]) -> Result<u8, String> {
    let a = parse_run(args)?;
    let text = fs::read_to_string(&a.plan).map_err(|e| format!("{}: {e}", a.plan))?;
    let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{}: {e}", a.plan))?;
    let cells = validate_plan(&plan)?;
    let fnv = plan_fingerprint(&plan);
    let me = std::env::current_exe().map_err(|e| format!("current_exe: {e}"))?;
    let started = unix_now();
    let machine = machine_id_fingerprint();
    let host_ns = if a.external {
        String::new()
    } else {
        self_netns()?
    };
    let mut prefixes: Vec<(u32, Vec<String>, String)> = Vec::new();
    let mut results: Vec<Value> = Vec::new();
    for c in &cells {
        let is_ext = c.from == Source::External;
        if a.external != is_ext {
            continue;
        }
        let t0 = Instant::now();
        let open = match &c.from {
            Source::Host | Source::External => probe_here(&c.kind, c.ip, c.port),
            Source::Ct(n) => {
                if !prefixes.iter().any(|(k, _, _)| k == n) {
                    let (p, ns) = nsenter_for(*n, &host_ns, &me)?;
                    prefixes.push((*n, p, ns));
                }
                let (p, ns) = prefixes
                    .iter()
                    .find(|(k, _, _)| k == n)
                    .map(|(_, p, ns)| (p, ns))
                    .ok_or("prefix")?;
                probe_in(p, ns, &me, &c.kind, c.ip, c.port)
                    .map_err(|e| format!("cell {} (ct {n}): {e}", c.id))?
            }
        };
        let ms = t0.elapsed().as_millis() as u64;
        let mark = if c.expect == "observe" || open == (c.expect == "open") {
            "ok"
        } else {
            "!!"
        };
        eprintln!(
            "{mark} {} {:34} {:9} {}://{}:{} {ms}ms",
            if open { "OPEN  " } else { "closed" },
            c.id,
            c.from.tag(),
            c.kind,
            c.ip,
            c.port
        );
        results.push(json!({"id": c.id, "from": c.from.tag(), "kind": c.kind, "ip": c.ip.to_string(), "port": c.port, "open": open, "ms": ms}));
    }
    // ebb36 #568 r2 L3: say which machine and when (FNV-1a 64 of /etc/machine-id, not the id
    // itself; unix seconds). judge() does not check these; the receipt carries them.
    let report = json!({
        "plan_fnv": fnv, "host": a.host.to_string(), "results": results,
        "runner": {"machine_id_fnv": machine, "started": started, "finished": unix_now(), "external": a.external,
                   "tool": format!("aib-cloud-host {}", env!("CARGO_PKG_VERSION"))}
    });
    write_or_print(
        &a.out,
        &report,
        &format!("{} results (plan_fnv {fnv})", results.len()),
    )?;
    Ok(0)
}

/// Merge reports of the same plan and host (host run + external run); duplicate ids refused.
pub fn merge_reports(reports: &[Value]) -> Result<Value, String> {
    let first = reports.first().ok_or("merge needs at least two reports")?;
    if reports.len() < 2 {
        return Err("merge needs at least two reports".to_owned());
    }
    let fnv = first
        .get("plan_fnv")
        .and_then(Value::as_str)
        .ok_or("first report has no plan_fnv")?;
    let host = first
        .get("host")
        .and_then(Value::as_str)
        .ok_or("first report has no host")?;
    let mut merged: Vec<Value> = Vec::new();
    for (i, r) in reports.iter().enumerate() {
        if r.get("plan_fnv").and_then(Value::as_str) != Some(fnv)
            || r.get("host").and_then(Value::as_str) != Some(host)
        {
            return Err(format!("report {i}: plan_fnv / host differ from report 0"));
        }
        for x in r
            .get("results")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("report {i}: results is not an array"))?
        {
            let id = x
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("report {i}: result without id"))?;
            if merged
                .iter()
                .any(|m| m.get("id").and_then(Value::as_str) == Some(id))
            {
                return Err(format!("report {i}: duplicate result {id}"));
            }
            merged.push(x.clone());
        }
    }
    merged.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    let runners: Vec<Value> = reports
        .iter()
        .filter_map(|r| r.get("runner").cloned())
        .collect();
    let mut m = Map::new();
    m.insert("plan_fnv".into(), json!(fnv));
    m.insert("host".into(), json!(host));
    m.insert("results".into(), Value::Array(merged));
    if !runners.is_empty() {
        m.insert("runners".into(), Value::Array(runners));
    }
    Ok(Value::Object(m))
}

fn cmd_merge(args: &[String]) -> Result<u8, String> {
    let mut out = None;
    let mut paths: Vec<&String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--out" {
            out = Some(it.next().ok_or("--out needs a value")?.clone());
        } else {
            paths.push(a);
        }
    }
    let mut reports = Vec::new();
    for p in &paths {
        let text = fs::read_to_string(p).map_err(|e| format!("{p}: {e}"))?;
        reports.push(serde_json::from_str(&text).map_err(|e| format!("{p}: {e}"))?);
    }
    let merged = merge_reports(&reports)?;
    let n = merged["results"].as_array().map_or(0, Vec::len);
    write_or_print(&out, &merged, &format!("{n} results"))?;
    Ok(0)
}

fn cmd_one(args: &[String]) -> Result<u8, String> {
    let [kind, ip, port] = args else {
        return Err("one needs <kind> <ip> <port>".to_owned());
    };
    if !KINDS.contains(&kind.as_str()) {
        return Err(format!("kind {kind:?}"));
    }
    let ip: IpAddr = ip.parse().map_err(|_| format!("ip {ip:?}"))?;
    let port: u16 = port.parse().map_err(|_| format!("port {port:?}"))?;
    let open = probe_here(kind, ip, port);
    let ns = self_netns()?;
    let mut so = std::io::stdout();
    let _ = writeln!(so, "{} ns={ns}", if open { "open" } else { "closed" });
    Ok(0)
}

/// `HOST_ROOT_PASSWORD_<HOST>=<value>` (what `aib secret ask --remote-only --push` writes) or a
/// bare value → the password. Refuses short values and anything with control characters.
pub fn parse_root_password(text: &str) -> Result<String, String> {
    let line = text
        .lines()
        .find(|l| !l.trim().is_empty())
        .ok_or("password file is empty")?;
    let value = match line.split_once('=') {
        Some((k, v)) if k.starts_with("HOST_ROOT_PASSWORD") => v,
        Some(_) => {
            return Err(
                "password file: unexpected KEY= (want HOST_ROOT_PASSWORD_<HOST>=…)".to_owned(),
            )
        }
        None => line,
    };
    let value = value.trim_end_matches(['\r', '\n']);
    if value.chars().count() < 24 {
        return Err("password shorter than 24 characters; refuse (Andy: 24 字以上)".to_owned());
    }
    if value.chars().any(|c| c.is_control()) {
        return Err("password contains control characters".to_owned());
    }
    Ok(value.to_owned())
}

/// `root-password apply <file>`: set root's password from a file that `aib secret ask
/// HOST_ROOT_PASSWORD_<HOST> --remote-only --generate --push` delivered (Touch ID on the Mac,
/// value never in a terminal), then overwrite and delete the file. This is the "forgot the
/// root password → reset it with biometrics" path (Andy 11-09): the Mac side proves the human,
/// this side only applies. Only `root` is ever changed.
fn cmd_root_password(args: &[String]) -> Result<u8, String> {
    let [sub, file] = args else {
        return Err("root-password apply <file>".to_owned());
    };
    if sub != "apply" {
        return Err(format!("root-password: unknown {sub}"));
    }
    let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
    let pw = parse_root_password(&text)?;
    let mut cmd = Command::new("chpasswd");
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("chpasswd: {e}"))?;
    if let Some(mut si) = child.stdin.take() {
        si.write_all(format!("root:{pw}\n").as_bytes())
            .map_err(|e| format!("chpasswd stdin: {e}"))?;
    }
    let st = wait_with_timeout(&mut child, Duration::from_secs(20))
        .map_err(|e| format!("chpasswd: {e}"))?;
    // the file is single-use whatever happened
    // Overwrite the bytes in place (no truncate, so the same blocks are rewritten on ext4),
    // then unlink. This is one pass, not a secure shred (c477a #584 L2): the sender puts the
    // file on tmpfs (/run) when the disk must never see it.
    if let Ok(mut f) = fs::OpenOptions::new().write(true).open(file) {
        let len = f.metadata().map(|m| m.len()).unwrap_or(0) as usize;
        let zeros = vec![0u8; len.max(text.len())];
        let _ = f.write_all(&zeros);
        let _ = f.sync_all();
    }
    let _ = fs::remove_file(file);
    if !st.success() {
        let mut err = String::new();
        if let Some(mut se) = child.stderr.take() {
            let _ = se.read_to_string(&mut err);
        }
        return Err(format!("chpasswd exited {st}: {}", err.trim()));
    }
    let mut status = Command::new("passwd");
    status.args(["-S", "root"]);
    let line = run_capture(&mut status, Duration::from_secs(5)).unwrap_or_default();
    let fields: Vec<&str> = line.split_whitespace().take(2).collect();
    println!(
        "root password set; passwd -S: {}; {file} overwritten once and deleted (not a secure shred; keep it on tmpfs)",
        fields.join(" ")
    );
    Ok(0)
}

// ---------------------------------------------------------------- site drift (outline #18)

/// `site drift [--records <dir>] [--json]`: for every `<dir>/*.json` record (written by
/// `site create`), rebuild the spec, read `pct config <ct>` and `/etc/pve/firewall/<ct>.fw`,
/// and print every difference (`aib_cloud::site::drift`). Exit 0 = every CT exactly as
/// declared, 1 = drift found, 2 = could not read something (never silently clean).
fn cmd_site_drift(args: &[String]) -> Result<u8, String> {
    use aib_cloud::site::{drift, spec_from_record};
    let mut dir = "/var/lib/ez2-cloud/sites".to_owned();
    let mut as_json = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => dir = it.next().ok_or("--records needs a dir")?.clone(),
            "--json" => as_json = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    // An entry that cannot be read is an error, not a site to skip: a drift report that
    // silently leaves a record out would read as "that site is fine".
    let mut names: Vec<std::path::PathBuf> = Vec::new();
    for entry in fs::read_dir(&dir).map_err(|e| format!("{dir}: {e}"))? {
        let path = entry.map_err(|e| format!("{dir}: {e}"))?.path();
        if path.extension().is_some_and(|x| x == "json") {
            names.push(path);
        }
    }
    names.sort();
    if names.is_empty() {
        return Err(format!("{dir}: no site records"));
    }
    let mut all: Vec<Value> = Vec::new();
    let mut checked = 0u32;
    for path in &names {
        let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rec: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let spec = spec_from_record(&rec).map_err(|e| format!("{}: {e}", path.display()))?;
        let mut cmd = Command::new("pct");
        cmd.args(["config", &spec.ct.to_string()]);
        let cfg = run_capture(&mut cmd, Duration::from_secs(15))?;
        let fw = fs::read_to_string(format!("/etc/pve/firewall/{}.fw", spec.ct)).ok();
        let d = drift(&spec, &cfg, fw.as_deref()).map_err(|e| format!("ct {}: {e}", spec.ct))?;
        checked += 1;
        for x in &d {
            if !as_json {
                eprintln!(
                    "!! ct {} {}: want {:?} got {:?}",
                    x.ct, x.key, x.want, x.got
                );
            }
            all.push(x.to_json());
        }
        if d.is_empty() && !as_json {
            eprintln!("ok ct {} ({}) as declared", spec.ct, spec.code);
        }
    }
    let doc = json!({"checked": checked, "drift": all, "ok": all.is_empty()});
    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
        );
    } else {
        eprintln!("{checked} CTs checked, {} drift", all.len());
    }
    Ok(u8::from(!all.is_empty()))
}

// ---------------------------------------------------------------- site suspend / resume / restore (P2)

pub(crate) const EDGE_CT: &str = "101";
pub(crate) const CADDYFILE: &str = "/etc/caddy/Caddyfile";
const SUSPENDED_PAGE: &str = "/srv/errors/suspended.html";

/// Read one site record. The CLI code is validated before it touches a path (c477a #585 L3:
/// `../` would otherwise walk out of the records dir) and must equal the record's own `site`,
/// so the Caddy block we swap and the spec we render always belong to the same site.
fn load_site(code: &str, records_dir: &str) -> Result<aib_cloud::site::SiteSpec, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("site code {code:?} is not valid"));
    }
    let path = format!("{records_dir}/{code}.json");
    let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
    let rec: Value = serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
    let spec = aib_cloud::site::spec_from_record(&rec).map_err(|e| format!("{path}: {e}"))?;
    if spec.code != code {
        return Err(format!(
            "{path}: record is for site {:?}, not {code:?}",
            spec.code
        ));
    }
    Ok(spec)
}

/// Every last octet in use on vmbr1: all site records plus `pct config` of every CT
/// (c477a #585 M2). Unreadable records or CTs are errors, not skipped: a gap here is how
/// two CTs end up on one address.
fn occupied_ip_lasts(records_dir: &str) -> Result<Vec<u8>, String> {
    let mut taken: Vec<u8> = Vec::new();
    for e in fs::read_dir(records_dir).map_err(|e| format!("{records_dir}: {e}"))? {
        let path = e.map_err(|e| e.to_string())?.path();
        if path.extension().is_none_or(|x| x != "json") {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rec: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let spec = aib_cloud::site::spec_from_record(&rec)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        taken.push(spec.ip_last);
    }
    let mut list = Command::new("pct");
    list.arg("list");
    let out = run_capture(&mut list, Duration::from_secs(20))?;
    for line in out.lines().skip(1) {
        let Some(id) = line.split_whitespace().next() else {
            continue;
        };
        if !id.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let mut cfg = Command::new("pct");
        cfg.args(["config", id]);
        let text = run_capture(&mut cfg, Duration::from_secs(15))?;
        for l in text.lines() {
            if let Some(rest) = l.strip_prefix("net0:") {
                for part in rest.split(',') {
                    if let Some(ip) = part.trim().strip_prefix("ip=") {
                        if let Some(last) = ip
                            .strip_prefix("10.77.0.")
                            .and_then(|t| t.split('/').next())
                            .and_then(|t| t.parse::<u8>().ok())
                        {
                            taken.push(last);
                        }
                    }
                }
            }
        }
    }
    taken.sort_unstable();
    taken.dedup();
    Ok(taken)
}

pub(crate) fn edge_caddyfile() -> Result<String, String> {
    let mut cmd = Command::new("pct");
    cmd.args(["exec", EDGE_CT, "--", "cat", CADDYFILE]);
    run_capture(&mut cmd, Duration::from_secs(15))
}

/// Push a new Caddyfile to the Edge: backup → write → `caddy validate` → reload. On a failed
/// validation the backup is restored and nothing is reloaded.
pub(crate) fn edge_apply_caddyfile(text: &str) -> Result<String, String> {
    let stamp = unix_now();
    let backup = format!("{CADDYFILE}.bak-{stamp}");
    let mut cp = Command::new("pct");
    cp.args(["exec", EDGE_CT, "--", "cp", "-a", CADDYFILE, &backup]);
    run_capture(&mut cp, Duration::from_secs(15))?;
    // c477a #585 L1: a guessable /tmp name written as root follows a planted symlink; use /run,
    // create_new (fails if anything is there) and 0600.
    let tmp = format!(
        "/run/aib-cloud-host-caddyfile-{}-{stamp}",
        std::process::id()
    );
    {
        let mut oo = fs::OpenOptions::new();
        oo.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            oo.mode(0o600);
        }
        let mut f = oo.open(&tmp).map_err(|e| format!("{tmp}: {e}"))?;
        f.write_all(text.as_bytes())
            .map_err(|e| format!("{tmp}: {e}"))?;
    }
    let mut push = Command::new("pct");
    push.args(["push", EDGE_CT, &tmp, CADDYFILE, "--perms", "0644"]);
    let pushed = run_capture(&mut push, Duration::from_secs(20));
    let _ = fs::remove_file(&tmp);
    pushed?;
    let mut val = Command::new("pct");
    val.args([
        "exec", EDGE_CT, "--", "caddy", "validate", "--config", CADDYFILE,
    ]);
    if let Err(e) = run_capture(&mut val, Duration::from_secs(30)) {
        let mut undo = Command::new("pct");
        undo.args(["exec", EDGE_CT, "--", "cp", "-a", &backup, CADDYFILE]);
        let _ = run_capture(&mut undo, Duration::from_secs(15));
        return Err(format!(
            "caddy validate failed, Caddyfile restored from {backup}: {e}"
        ));
    }
    let mut reload = Command::new("pct");
    reload.args(["exec", EDGE_CT, "--", "systemctl", "reload", "caddy"]);
    if let Err(e) = run_capture(&mut reload, Duration::from_secs(30)) {
        // c477a #585 L2: a validated file that failed to reload must not stay on disk, or the
        // next unrelated reload brings it live. Put the backup back and reload that.
        let mut undo = Command::new("pct");
        undo.args(["exec", EDGE_CT, "--", "cp", "-a", &backup, CADDYFILE]);
        let _ = run_capture(&mut undo, Duration::from_secs(15));
        let mut re = Command::new("pct");
        re.args(["exec", EDGE_CT, "--", "systemctl", "reload", "caddy"]);
        let _ = run_capture(&mut re, Duration::from_secs(30));
        return Err(format!(
            "caddy reload failed, Caddyfile restored from {backup} and reloaded: {e}"
        ));
    }
    Ok(backup)
}

fn site_args(args: &[String]) -> Result<(String, String, Vec<String>), String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = "/var/lib/ez2-cloud/sites".to_owned();
    let mut rest = Vec::new();
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        if a == "--records" {
            records = it.next().ok_or("--records needs a dir")?.clone();
        } else {
            rest.push(a.clone());
        }
    }
    Ok((code, records, rest))
}

/// `site suspend <code>`: the Edge answers every request for the site with the suspension
/// page (503); the CT keeps running. `site resume <code>` puts the normal block back.
fn cmd_site_suspend(args: &[String], suspend: bool) -> Result<u8, String> {
    use aib_cloud::site::{
        caddy_block_is_suspended, caddy_block_suspended, caddy_block_upstream, caddy_swap_block,
    };
    let (code, records, rest) = site_args(args)?;
    if !rest.is_empty() {
        return Err(format!("unknown arguments {rest:?}"));
    }
    // 33e28 B1: the Edge change runs as an operation (host lock + journal). Everything it
    // decides from — the record, the quarantine marks, the Caddyfile — is read under the
    // lock (c477a #635 seg. 2 M1), so a restore --switch of another site cannot land
    // between the reading and the writing and be overwritten with the old Caddyfile.
    let command = if suspend {
        "site.suspend"
    } else {
        "site.resume"
    };
    let done = crate::opstore::run_op_prepared(
        &crate::opstore::OpPlace::host()?,
        command,
        &format!("site:{code}"),
        crate::opstore::BY_LOCAL_ROOT,
        unix_now(),
        || {
            let spec = load_site(&code, &records)?;
            // a quarantined site is neither reopened (resume) nor has its quarantine block
            // replaced by the suspension page (suspend): only unquarantine ends a quarantine
            crate::quarantinecmd::refuse_if_quarantined_here(
                &code,
                spec.ct,
                aib_cloud::quarantine::Reopens::Resume,
            )?;
            let current = edge_caddyfile()?;
            let now = caddy_block_is_suspended(&current, &code).map_err(|e| e.to_string())?;
            if now == suspend {
                return Ok(None);
            }
            let block = if suspend {
                caddy_block_suspended(&spec, SUSPENDED_PAGE)
            } else {
                caddy_block_upstream(&spec, &spec.ip())
            };
            let next = caddy_swap_block(&current, &code, &block).map_err(|e| e.to_string())?;
            let plan = json!({ "command": command, "site": code, "edge_block": block });
            Ok(Some((plan, next)))
        },
        |_, next: String| edge_apply_caddyfile(&next),
    )?;
    let Some(backup) = done else {
        eprintln!(
            "{code} already {}",
            if suspend { "suspended" } else { "live" }
        );
        return Ok(0);
    };
    eprintln!(
        "{code} {} (Edge Caddyfile backup {backup})",
        if suspend {
            "suspended: 503 + suspension page"
        } else {
            "resumed: proxy to CT"
        }
    );
    Ok(0)
}

/// Whether `--yes-<code>` was typed for exactly this site.
fn switch_confirmed(code: &str, yes: Option<&str>) -> bool {
    yes == Some(code)
}

/// `site restore <code> --backup <vzdump> --ct <new id> --ip <last octet> [--switch --yes-<code>]`:
/// blue-green — restore the backup as a new CT beside the live one, apply the site's limits,
/// start it, check it answers 200 locally; with `--switch`, point the Edge at it. The old CT
/// is left running for comparison (deleting it is a separate, human-approved step).
fn cmd_site_restore(args: &[String]) -> Result<u8, String> {
    use aib_cloud::site::{caddy_block_upstream, caddy_swap_block, desired_firewall, restore_plan};
    let (code, records, rest) = site_args(args)?;
    let (mut backup, mut ct, mut ip, mut switch) = (None, None, None, false);
    let mut yes: Option<String> = None;
    let mut it = rest.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--backup" => backup = Some(it.next().ok_or("--backup needs a path")?.clone()),
            "--ct" => {
                ct = Some(
                    it.next()
                        .ok_or("--ct needs an id")?
                        .parse::<u32>()
                        .map_err(|_| "--ct must be a number")?,
                )
            }
            "--ip" => {
                ip = Some(
                    it.next()
                        .ok_or("--ip needs the last octet")?
                        .parse::<u8>()
                        .map_err(|_| "--ip must be 0-255")?,
                )
            }
            "--switch" => switch = true,
            a if a.starts_with("--yes-") => yes = Some(a.trim_start_matches("--yes-").to_owned()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    // --switch moves the Edge and rewrites the site record: no inverse command, so a
    // standing approval is not enough — the person types the site code (4542e 11-10 c)
    if switch && !switch_confirmed(&code, yes.as_deref()) {
        return Err(format!(
            "--switch moves {code}'s live traffic: add --yes-{code} to confirm"
        ));
    }
    // a confirmation with nothing to confirm is a mistake in the command, not a no-op
    // (c477a #635 seg. 2 N3)
    if !switch && yes.is_some() {
        return Err(
            "--yes-<code> confirms --switch; without --switch there is nothing to confirm"
                .to_owned(),
        );
    }
    let backup = backup.ok_or("--backup required")?;
    let (ct_n, ip_n) = (ct.ok_or("--ct required")?, ip.ok_or("--ip required")?);
    let new_ct = ct_n.to_string();
    // 33e28 B1: the restore is an operation (host lock + journal); a failure from the first
    // step that changes the host leaves it `failed` for a person to check. The record, the
    // quarantine marks, the IPs and CT ids in use are read and checked under the lock
    // (c477a #635 seg. 2 M1): two restores cannot both find the same IP or CT id free.
    let done = crate::opstore::run_op_prepared(
        &crate::opstore::OpPlace::host()?,
        "site.restore",
        &format!("site:{code}"),
        crate::opstore::BY_LOCAL_ROOT,
        unix_now(),
        || {
            let spec = load_site(&code, &records)?;
            // restoring a quarantined site would bring its backup up with egress (and, with
            // --switch, back on the edge): refused until unquarantine
            crate::quarantinecmd::refuse_if_quarantined_here(
                &code,
                spec.ct,
                aib_cloud::quarantine::Reopens::Restore,
            )?;
            let taken = occupied_ip_lasts(&records)?;
            let plan = restore_plan(&spec, &backup, ct_n, ip_n, &taken)
                .map_err(|e| format!("{e} (in use on vmbr1: {taken:?})"))?;
            let mut probe = Command::new("pct");
            probe.args(["status", &new_ct]);
            if run_capture(&mut probe, Duration::from_secs(10)).is_ok() {
                return Err(format!("CT {new_ct} already exists; pick another id"));
            }
            let plan_doc = json!({
                "command": "site.restore",
                "site": code,
                "steps": plan.iter().map(|s| s.argv.clone()).collect::<Vec<_>>(),
                "switch": switch,
                // --switch is only accepted with the site code typed (checked above)
                "confirmed_code": switch,
            });
            Ok(Some((plan_doc, (spec, plan))))
        },
        |_, (spec, plan)| {
            for step in &plan {
                if step.id == "verify" {
                    break;
                }
                let mut cmd = Command::new(&step.argv[0]);
                cmd.args(&step.argv[1..]);
                eprintln!("→ {}", step.argv.join(" "));
                run_capture(&mut cmd, Duration::from_secs(900))
                    .map_err(|e| format!("step {}: {e}", step.id))?;
                if step.id == "set" {
                    let fw = format!("/etc/pve/firewall/{new_ct}.fw");
                    if !Path::new(&fw).exists() {
                        fs::write(&fw, desired_firewall(&spec.fw_group, spec.ssh_port))
                            .map_err(|e| format!("{fw}: {e}"))?;
                    }
                }
            }
            // wait for the site inside the new CT
            let verify = plan
                .iter()
                .find(|s| s.id == "verify")
                .ok_or("no verify step")?;
            let mut code_seen = String::new();
            for _ in 0..30 {
                let mut cmd = Command::new(&verify.argv[0]);
                cmd.args(&verify.argv[1..]);
                if let Ok(out) = run_capture(&mut cmd, Duration::from_secs(20)) {
                    code_seen = out.trim().to_owned();
                    if code_seen == "200" {
                        break;
                    }
                }
                // pause 2 s between checks via a child (ADR 0005: this CLI has no timer registry; see wait_with_timeout)
                if let Ok(mut pause) = Command::new("sleep").arg("2").spawn() {
                    let _ = wait_with_timeout(&mut pause, Duration::from_secs(5));
                }
            }
            if code_seen != "200" {
                return Err(format!(
                    "new CT {new_ct} answers {code_seen:?}, not 200; Edge not switched"
                ));
            }
            eprintln!(
                "new CT {new_ct} up and answering 200 for {}",
                spec.primary()
            );
            if switch {
                let new_ip = format!("10.77.0.{}", ip.unwrap_or(0));
                // c477a #585 增量 L1: build and validate the new record *before* the Edge moves, so a
                // record that cannot be written (ssh_port overflow, bad fields) never leaves the Edge
                // pointing at a CT no record describes.
                let rec_path = format!("{records}/{code}.json");
                let text = fs::read_to_string(&rec_path).map_err(|e| format!("{rec_path}: {e}"))?;
                let mut rec: Value =
                    serde_json::from_str(&text).map_err(|e| format!("{rec_path}: {e}"))?;
                let new_ct_n = ct.unwrap_or(0);
                rec["ct"] = json!(new_ct_n);
                rec["ip"] = json!(new_ip);
                let had_ssh = rec.get("ssh_port").is_some_and(|v| !v.is_null());
                if had_ssh {
                    let port = 2000u64 + u64::from(new_ct_n);
                    if port > 65535 {
                        return Err(format!(
                    "new ct {new_ct_n}: customer ssh port 2000+ct would be {port}; not switching"
                ));
                    }
                    rec["ssh_port"] = json!(port);
                }
                aib_cloud::site::spec_from_record(&rec)
                    .map_err(|e| format!("new record invalid, not switching: {e}"))?;
                let new_text = serde_json::to_string(&rec).map_err(|e| e.to_string())?;
                let current = edge_caddyfile()?;
                let next = caddy_swap_block(&current, &code, &caddy_block_upstream(&spec, &new_ip))
                    .map_err(|e| e.to_string())?;
                let backup_file = edge_apply_caddyfile(&next)?;
                eprintln!("Edge switched {} → {new_ip} (backup {backup_file}); old CT {} left running — delete it only after checking", spec.code, spec.ct);
                // the record follows the switch (c477a #585 L4); old record kept, new one written via tmp + rename
                fs::write(format!("{rec_path}.bak-{}", unix_now()), &text)
                    .map_err(|e| format!("{rec_path}.bak: {e}"))?;
                let tmp = format!("{rec_path}.tmp-{}", std::process::id());
                fs::write(&tmp, new_text).map_err(|e| format!("{tmp}: {e}"))?;
                fs::rename(&tmp, &rec_path).map_err(|e| format!("{tmp} → {rec_path}: {e}"))?;
                eprintln!(
                    "record {rec_path} now points at ct {new_ct_n} / {new_ip}{}",
                    if had_ssh {
                        format!("; ssh_port {} — run ez2-site-ssh {code} {new_ct_n} <pubkey> to move the DNAT / CT rules", 2000 + new_ct_n)
                    } else {
                        String::new()
                    }
                );
            } else {
                eprintln!("not switched (--switch to point the Edge at it)");
            }
            Ok(0)
        },
    )?;
    Ok(done.unwrap_or(0))
}

pub fn main(args: &[String]) -> Result<u8, String> {
    match args.first().map(String::as_str) {
        Some("site") => match args.get(1).map(String::as_str) {
            Some("drift") => cmd_site_drift(&args[2..]),
            Some("verify") => crate::siteverify::cmd(&args[2..]),
            Some("usage") => crate::usagecmd::cmd(&args[2..]),
            Some("create") => crate::sitecreatecmd::cmd(&args[2..]),
            Some("remove") => crate::siteremovecmd::cmd(&args[2..]),
            Some("mask") => crate::sitemaskcmd::cmd(&args[2..]),
            Some("restore-wpress") => crate::restorestepscmd::cmd(&args[2..]),
            Some("quarantine") => {
                crate::quarantinecmd::cmd(&args[2..], crate::quarantinecmd::Mode::Quarantine)
            }
            Some("unquarantine") => {
                crate::quarantinecmd::cmd(&args[2..], crate::quarantinecmd::Mode::Unquarantine)
            }
            Some("quarantine-archive") => {
                crate::quarantinecmd::cmd(&args[2..], crate::quarantinecmd::Mode::Archive)
            }
            Some("suspend") => cmd_site_suspend(&args[2..], true),
            Some("resume") => cmd_site_suspend(&args[2..], false),
            Some("restore") => cmd_site_restore(&args[2..]),
            _ => Err("site needs drift | verify [<code>] [--json] | usage [--json] | quarantine <code> --reason <r> --plan | unquarantine <code> --plan | quarantine-archive <code> --plan | remove <code> --address <main address> --plan | restore-wpress <code> --plan --from <id> | --plan --close resolved|abandoned | suspend <code> | resume <code> | restore <code> --backup <vzdump> --ct <id> --ip <octet> [--switch --yes-<code>]".to_owned()),
        },
        Some("root-password") => cmd_root_password(&args[1..]),
        Some("customer") => crate::customercmd::cmd(&args[1..]),
        Some("roster") => crate::rostercmd::cmd(&args[1..]),
        Some("leak-words") => crate::leakwordscmd::cmd(&args[1..]),
        Some("ct") => crate::maskcmd::cmd(&args[1..]),
        Some("edge") => crate::edgecmd::cmd(&args[1..]),
        Some("backup") => match args.get(1).map(String::as_str) {
            Some("check") => crate::backupcmd::cmd(&args[2..]),
            _ => Err("backup needs check [--remote <name>:<path>] [--records dir] [--json]".to_owned()),
        },
        Some("spec") => crate::speccmd::cmd(&args[1..]),
        Some("op") => crate::opstore::cmd(&args[1..]),
        Some("probe") => match args.get(1).map(String::as_str) {
            Some("run") => cmd_run(&args[2..]),
            Some("merge") => cmd_merge(&args[2..]),
            Some("one") => cmd_one(&args[2..]),
            Some("netns") => {
                println!("{}", self_netns()?);
                Ok(0)
            }
            _ => Err("probe needs run | merge | one | netns".to_owned()),
        },
        Some("self-update") => crate::update::main(&args[1..]),
        Some("--version") => {
            println!("{}", crate::update::own_version_line());
            Ok(0)
        }
        Some("build-info") if args.len() == 1 => {
            println!("{}", crate::update::BUILD_INFO);
            Ok(0)
        }
        _ => Err("usage: aib-cloud-host probe run <plan.json> --host <ip> [--out f] [--external] | probe merge <a> <b>… --out f | root-password apply <file> | site drift [--records dir] [--json] | site verify [<code>] [--records dir] [--thin vg/pool] [--json] | site suspend|resume <code> | site restore <code> --backup <vzdump> --ct <id> --ip <octet> [--switch --yes-<code>] | edge domain preflight <name> --ip <ipv4> | edge domain add|rm <site> <name> --plan | backup check [--remote <name>:<path>] [--json] | op status [<op id>] | op unfinished <target> | op recovered <op id> | op archive <op id> [--target <t>] | spec lint <file> | spec plan <file> [--from <step>] [--json] | leak-words --out <file> | self-update <version> [--dest f] [--allow-downgrade] [--base-url file:///d] | build-info | --version".to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    /// 4542e 11-10 condition c: --switch has no inverse, so the site code is typed.
    #[test]
    fn a_switch_needs_the_site_code_typed() {
        assert!(switch_confirmed("site2", Some("site2")));
        assert!(!switch_confirmed("site2", None));
        assert!(!switch_confirmed("site2", Some("site3")));
        assert!(!switch_confirmed("site2", Some("")));
        // the check runs before anything is read or changed
        let args: Vec<String> = [
            "site2",
            "--records",
            "/nonexistent",
            "--backup",
            "b",
            "--ct",
            "200",
            "--ip",
            "40",
            "--switch",
        ]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
        let err = cmd_site_restore(&args).unwrap_err();
        assert!(err.contains("--yes-site2"), "{err}");
        // a confirmation without --switch is refused, before anything is read
        let mut no_switch = args.clone();
        no_switch.pop();
        no_switch.push("--yes-site2".to_owned());
        let err = cmd_site_restore(&no_switch).unwrap_err();
        assert!(err.contains("nothing to confirm"), "{err}");
    }

    fn cell(id: &str, from: &str, kind: &str, ip: &str, port: u64, expect: &str) -> Value {
        json!({"id": id, "from": from, "kind": kind, "ip": ip, "port": port, "expect": expect})
    }

    #[test]
    fn h1_whitelist_refuses_each_defect() {
        let good = cell("host-egress-dns", "host", "dns", "1.1.1.1", 53, "open");
        assert_eq!(validate_plan(&json!([good.clone()])).unwrap().len(), 1);
        let bad = [
            ("empty", json!([])),
            ("not array", json!({})),
            (
                "id uppercase",
                json!([cell("X", "host", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "id leading dash",
                json!([cell("-a", "host", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "ct:0",
                json!([cell("a", "ct:0", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "ct leading zero",
                json!([cell("a", "ct:010", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "ct too long",
                json!([cell("a", "ct:1234567890", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "from garbage",
                json!([cell("a", "host;id", "tcp", "1.1.1.1", 80, "open")]),
            ),
            (
                "kind",
                json!([cell("a", "host", "udp", "1.1.1.1", 80, "open")]),
            ),
            (
                "ip shell",
                json!([cell("a", "host", "tcp", "1.1.1.1; rm -rf /", 80, "open")]),
            ),
            (
                "ip hostname",
                json!([cell("a", "host", "tcp", "example.com", 80, "open")]),
            ),
            (
                "port range",
                json!([cell("a", "host", "tcp", "1.1.1.1", 70000, "open")]),
            ),
            (
                "port string",
                json!([{"id": "a", "from": "host", "kind": "tcp", "ip": "1.1.1.1", "port": "80", "expect": "open"}]),
            ),
            (
                "icmp with port",
                json!([cell("a", "host", "icmp", "1.1.1.1", 80, "open")]),
            ),
            (
                "tcp port 0",
                json!([cell("a", "host", "tcp", "1.1.1.1", 0, "open")]),
            ),
            (
                "expect",
                json!([cell("a", "host", "tcp", "1.1.1.1", 80, "maybe")]),
            ),
            (
                "unknown key",
                json!([{"id": "a", "from": "host", "kind": "tcp", "ip": "1.1.1.1", "port": 80, "expect": "open", "cmd": "x"}]),
            ),
            ("duplicate", json!([good.clone(), good.clone()])),
        ];
        for (tag, plan) in bad {
            assert!(validate_plan(&plan).is_err(), "{tag}: accepted");
        }
        // observe cells (recorded, never judged) are a valid expectation
        assert!(validate_plan(&json!([cell(
            "site-110-to-peerhost-443",
            "ct:110",
            "tcp",
            "203.0.113.110",
            443,
            "observe"
        )]))
        .is_ok());
        // ipv6 is an address too
        assert!(validate_plan(&json!([cell(
            "v6",
            "ct:101",
            "tcp",
            "2606:4700:4700::1111",
            443,
            "blocked"
        )]))
        .is_ok());
    }

    #[test]
    fn h2_fingerprint_is_aib_cloud_s_over_the_loaded_plan() {
        // same value the Python runner produced for this 4-cell plan (aib-cloud i9 pins it too)
        let plan = json!([
            {"port": 22, "id": "public-host-22", "expect": "open", "kind": "tcp", "ip": "203.0.113.90", "from": "external"},
            {"id": "public-host-443", "from": "external", "kind": "tcp", "ip": "203.0.113.90", "port": 443, "expect": "open"},
            {"id": "public-host-8006", "from": "external", "kind": "tcp", "ip": "203.0.113.90", "port": 8006, "expect": "blocked"},
            {"id": "host-egress-dns", "from": "host", "kind": "dns", "ip": "1.1.1.1", "port": 53, "expect": "open"}
        ]);
        assert_eq!(plan_fingerprint(&plan), "e75b1af045774800");
    }

    #[test]
    fn h3_tcp_probe_sees_a_listener_and_a_closed_port() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        assert!(probe_here("tcp", "127.0.0.1".parse().unwrap(), port));
        drop(l);
        // LINUX ONLY from here (1ced7, #625 seg. 2 N1: on other systems this test checks
        // the "open" half alone).
        // the port we just released: nothing listens now. Only asserted on Linux, where
        // this binary runs: on a shared macOS machine the port was seen answering again
        // straight after the release (c477a, mac-c, two runs).
        // Another test of this binary may bind the port in the instant after the release
        // (seen twice in a row on CI, #641): a few fresh ports are tried, and one seen
        // closed is enough — the probe saying "open" for every one of them is the failure.
        if cfg!(target_os = "linux") {
            let seen_closed = (0..5).any(|_| {
                let l = TcpListener::bind("127.0.0.1:0").unwrap();
                let port = l.local_addr().unwrap().port();
                drop(l);
                !probe_here("tcp", "127.0.0.1".parse().unwrap(), port)
            });
            assert!(seen_closed || !probe_here("tcp", "127.0.0.1".parse().unwrap(), port));
        }
    }

    #[test]
    fn h4_dns_packet_shape() {
        let p = dns_query_packet(0xbeef);
        assert_eq!(&p[0..2], &[0xbe, 0xef]);
        assert_eq!(p[2], 0x01, "RD set");
        assert_eq!(&p[4..6], &[0, 1], "one question");
        assert_eq!(&p[12..], b"\x07example\x03com\x00\x00\x01\x00\x01");
        // a closed UDP port answers nothing: closed
        assert!(!probe_dns("127.0.0.1".parse().unwrap(), 9));
    }

    #[test]
    fn h5_merge_binds_plan_and_host_and_refuses_duplicates() {
        let r = |fnv: &str, host: &str, ids: &[&str]| json!({"plan_fnv": fnv, "host": host, "results": ids.iter().map(|i| json!({"id": i, "open": true})).collect::<Vec<_>>()});
        let m = merge_reports(&[r("f", "h", &["b", "a"]), r("f", "h", &["c"])]).unwrap();
        let ids: Vec<&str> = m["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, ["a", "b", "c"]);
        assert!(
            merge_reports(&[r("f", "h", &["a"]), r("g", "h", &["b"])]).is_err(),
            "fnv differs"
        );
        assert!(
            merge_reports(&[r("f", "h", &["a"]), r("f", "h2", &["b"])]).is_err(),
            "host differs"
        );
        assert!(
            merge_reports(&[r("f", "h", &["a"]), r("f", "h", &["a"])]).is_err(),
            "duplicate id"
        );
        assert!(merge_reports(&[r("f", "h", &["a"])]).is_err(), "one report");
    }

    #[test]
    fn h7_root_password_file() {
        let pw = "Ab3!".repeat(7);
        assert_eq!(
            parse_root_password(&format!("HOST_ROOT_PASSWORD_HOSTA={pw}\n")).unwrap(),
            pw
        );
        assert_eq!(parse_root_password(&format!("{pw}\r\n")).unwrap(), pw);
        assert!(parse_root_password("").is_err(), "empty");
        assert!(
            parse_root_password("HOST_ROOT_PASSWORD_HOSTA=short\n").is_err(),
            "short"
        );
        assert!(
            parse_root_password(&format!("PVE_API_TOKEN={pw}\n")).is_err(),
            "other key"
        );
        assert!(
            parse_root_password(&format!("HOST_ROOT_PASSWORD_HOSTA={pw}\x07\n")).is_err(),
            "control char"
        );
        // a value that itself contains '=' keeps everything after the first '='
        assert_eq!(
            parse_root_password(&format!("HOST_ROOT_PASSWORD_HOSTA={pw}=x=y\n")).unwrap(),
            format!("{pw}=x=y")
        );
    }

    #[test]
    fn h8_merge_keeps_runner_blocks_and_fingerprint_is_not_the_id() {
        let r1 = json!({"plan_fnv": "f", "host": "h", "results": [{"id": "a", "open": true}], "runner": {"machine_id_fnv": "x", "started": 1, "finished": 2}});
        let r2 = json!({"plan_fnv": "f", "host": "h", "results": [{"id": "b", "open": true}], "runner": {"machine_id_fnv": "y", "started": 3, "finished": 4}});
        let m = merge_reports(&[r1, r2]).unwrap();
        assert_eq!(m["runners"].as_array().unwrap().len(), 2);
        let fp = machine_id_fingerprint();
        assert!(fp == "unknown" || (fp.len() == 16 && fp.bytes().all(|b| b.is_ascii_hexdigit())));
        if let Ok(id) = std::fs::read_to_string("/etc/machine-id") {
            assert_ne!(fp, id.trim(), "the raw id must not leave the host");
        }
        assert!(unix_now() > 1_700_000_000);
    }

    #[test]
    fn h6_run_args() {
        let a = parse_run(&[
            "p.json".into(),
            "--host".into(),
            "203.0.113.90".into(),
            "--external".into(),
        ])
        .unwrap();
        assert!(a.external && a.out.is_none());
        assert!(parse_run(&["p.json".into()]).is_err(), "host required");
        assert!(
            parse_run(&["p.json".into(), "--host".into(), "hosta".into()]).is_err(),
            "host must be an ip"
        );
        assert!(
            parse_run(&["--host".into(), "1.1.1.1".into()]).is_err(),
            "plan required"
        );
    }

    #[test]
    fn p9_probe_one_output_carries_the_namespace() {
        assert_eq!(
            parse_one_output("open ns=net:[4026532345]\n"),
            Some((true, "net:[4026532345]"))
        );
        assert_eq!(
            parse_one_output("closed ns=net:[1]"),
            Some((false, "net:[1]"))
        );
        for bad in [
            "open",
            "open ns=",
            "maybe ns=net:[1]",
            "open net:[1]",
            "open ns=net:[1] extra",
            "",
        ] {
            assert!(parse_one_output(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn p10_run_capture_survives_output_larger_than_a_pipe_buffer() {
        // c477a #584 L3: 300 KB on stdout and 300 KB on stderr at once must not deadlock
        let mut cmd = Command::new("sh");
        cmd.args([
            "-c",
            "head -c 300000 /dev/zero | tr '\\0' a; head -c 300000 /dev/zero | tr '\\0' b >&2",
        ]);
        let out = run_capture(&mut cmd, Duration::from_secs(20)).unwrap();
        assert_eq!(out.len(), 300_000);
        assert!(out.bytes().all(|b| b == b'a'));
    }

    #[test]
    fn p11_dns_answer_must_come_from_the_target() {
        use std::net::{Ipv4Addr, UdpSocket};
        // a "resolver" on localhost that answers from the queried socket → open
        let good = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let good_port = good.local_addr().unwrap().port();
        good.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let t = std::thread::spawn(move || {
            let mut b = [0u8; 512];
            if let Ok((n, from)) = good.recv_from(&mut b) {
                let mut r = b[..n].to_vec();
                r[2] |= 0x80;
                let _ = good.send_to(&r, from);
            }
        });
        assert!(probe_dns(IpAddr::V4(Ipv4Addr::LOCALHOST), good_port));
        t.join().unwrap();
        // the same answer sent from a *different* socket than the one queried → closed (L4)
        let target = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let target_port = target.local_addr().unwrap().port();
        target
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let t2 = std::thread::spawn(move || {
            let mut b = [0u8; 512];
            if let Ok((n, from)) = target.recv_from(&mut b) {
                let mut r = b[..n].to_vec();
                r[2] |= 0x80;
                let other = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
                let _ = other.send_to(&r, from);
            }
        });
        assert!(!probe_dns(IpAddr::V4(Ipv4Addr::LOCALHOST), target_port));
        t2.join().unwrap();
    }

    #[test]
    fn p12_timeout_kills_the_whole_group_and_returns_promptly() {
        // c477a #584 L1': a grandchild holding our pipes must not turn a 1 s timeout into a hang
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "sleep 30 & sleep 30"]);
        let t0 = Instant::now();
        let r = run_capture(&mut cmd, Duration::from_secs(1));
        assert!(r.is_err(), "{r:?}");
        assert!(r.unwrap_err().contains("timeout"));
        assert!(
            t0.elapsed() < Duration::from_secs(6),
            "took {:?}",
            t0.elapsed()
        );
        let mut q = Command::new("sh");
        q.args(["-c", "sleep 30 & sleep 30"]);
        let t1 = Instant::now();
        assert!(run_quiet(&mut q, Duration::from_secs(1)).is_err());
        assert!(t1.elapsed() < Duration::from_secs(6));
        // c477a #585 r3 L: the child exits at once but leaves a grandchild holding our pipes;
        // the normal path must still return promptly with the child's own output
        let mut bg = Command::new("sh");
        bg.args(["-c", "echo parent-done; sleep 30 &"]);
        let t2 = Instant::now();
        let out = run_capture(&mut bg, Duration::from_secs(20)).unwrap();
        assert_eq!(out.trim(), "parent-done");
        assert!(
            t2.elapsed() < Duration::from_secs(6),
            "took {:?}",
            t2.elapsed()
        );
    }
}
