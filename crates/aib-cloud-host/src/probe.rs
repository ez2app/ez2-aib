// SPDX-License-Identifier: Apache-2.0
//! Execute an `aib_cloud::isolation` probe plan on a Proxmox host (or a public vantage
//! point) and write the report `judge()` accepts. Rust rewrite of the Python runner
//! `aib/tools/cloud-host/ez2-isolation-probe` (#568 r2 M2 rules kept one for one):
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
    match sock.recv_from(&mut buf) {
        Ok((n, _)) => n >= 12 && buf[0..2] == id.to_be_bytes() && buf[2] & 0x80 != 0,
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
fn wait_with_timeout(
    child: &mut std::process::Child,
    timeout: Duration,
) -> Result<std::process::ExitStatus, String> {
    let t0 = Instant::now();
    loop {
        if let Some(st) = child.try_wait().map_err(|e| e.to_string())? {
            return Ok(st);
        }
        if t0.elapsed() > timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err("timeout".to_owned());
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn run_quiet(cmd: &mut Command, timeout: Duration) -> Result<std::process::ExitStatus, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    wait_with_timeout(&mut child, timeout)
}

fn run_capture(cmd: &mut Command, timeout: Duration) -> Result<String, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    let mut out = String::new();
    let status = wait_with_timeout(&mut child, timeout)
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    if let Some(mut so) = child.stdout.take() {
        let _ = so.read_to_string(&mut out);
    }
    if !status.success() {
        let mut err = String::new();
        if let Some(mut se) = child.stderr.take() {
            let _ = se.read_to_string(&mut err);
        }
        return Err(format!(
            "{:?} exited {status}: {}",
            cmd.get_program(),
            err.trim()
        ));
    }
    Ok(out)
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
fn nsenter_for(ct: u32, host_ns: &str, me: &Path) -> Result<Vec<String>, String> {
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
    Ok(prefix)
}

fn probe_in(prefix: &[String], me: &Path, kind: &str, ip: IpAddr, port: u16) -> bool {
    let mut cmd = Command::new(&prefix[0]);
    cmd.args(&prefix[1..])
        .arg(me)
        .args(["probe", "one", kind])
        .arg(ip.to_string())
        .arg(port.to_string());
    match run_capture(&mut cmd, Duration::from_secs(12)) {
        Ok(out) => out.trim() == "open",
        Err(_) => false,
    }
}

// ---------------------------------------------------------------- commands

fn unix_now() -> u64 {
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
    let mut prefixes: Vec<(u32, Vec<String>)> = Vec::new();
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
                if !prefixes.iter().any(|(k, _)| k == n) {
                    let p = nsenter_for(*n, &host_ns, &me)?;
                    prefixes.push((*n, p));
                }
                let p = &prefixes
                    .iter()
                    .find(|(k, _)| k == n)
                    .map(|(_, p)| p)
                    .ok_or("prefix")?;
                probe_in(p, &me, &c.kind, c.ip, c.port)
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
    let mut so = std::io::stdout();
    let _ = writeln!(so, "{}", if open { "open" } else { "closed" });
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
    let zeros = vec![0u8; text.len().max(64)];
    let _ = fs::write(file, zeros);
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
        "root password set; passwd -S: {}; {file} shredded",
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
    let mut names: Vec<std::path::PathBuf> = fs::read_dir(&dir)
        .map_err(|e| format!("{dir}: {e}"))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
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

pub fn main(args: &[String]) -> Result<u8, String> {
    match args.first().map(String::as_str) {
        Some("site") => match args.get(1).map(String::as_str) {
            Some("drift") => cmd_site_drift(&args[2..]),
            _ => Err("site needs drift".to_owned()),
        },
        Some("root-password") => cmd_root_password(&args[1..]),
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
        Some("--version") => {
            println!("aib-cloud-host {}", env!("CARGO_PKG_VERSION"));
            Ok(0)
        }
        _ => Err("usage: aib-cloud-host probe run <plan.json> --host <ip> [--out f] [--external] | probe merge <a> <b>… --out f | root-password apply <file> | site drift [--records dir] [--json]".to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::net::TcpListener;

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
            "136.243.105.110",
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
            {"port": 22, "id": "public-host-22", "expect": "open", "kind": "tcp", "ip": "135.181.75.90", "from": "external"},
            {"id": "public-host-443", "from": "external", "kind": "tcp", "ip": "135.181.75.90", "port": 443, "expect": "open"},
            {"id": "public-host-8006", "from": "external", "kind": "tcp", "ip": "135.181.75.90", "port": 8006, "expect": "blocked"},
            {"id": "host-egress-dns", "from": "host", "kind": "dns", "ip": "1.1.1.1", "port": 53, "expect": "open"}
        ]);
        assert_eq!(plan_fingerprint(&plan), "52240dcf9134f54d");
    }

    #[test]
    fn h3_tcp_probe_sees_a_listener_and_a_closed_port() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        assert!(probe_here("tcp", "127.0.0.1".parse().unwrap(), port));
        drop(l);
        // the port we just released: nothing listens now
        assert!(!probe_here("tcp", "127.0.0.1".parse().unwrap(), port));
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
            parse_root_password(&format!("HOST_ROOT_PASSWORD_EU02={pw}\n")).unwrap(),
            pw
        );
        assert_eq!(parse_root_password(&format!("{pw}\r\n")).unwrap(), pw);
        assert!(parse_root_password("").is_err(), "empty");
        assert!(
            parse_root_password("HOST_ROOT_PASSWORD_EU02=short\n").is_err(),
            "short"
        );
        assert!(
            parse_root_password(&format!("PVE_API_TOKEN={pw}\n")).is_err(),
            "other key"
        );
        assert!(
            parse_root_password(&format!("HOST_ROOT_PASSWORD_EU02={pw}\x07\n")).is_err(),
            "control char"
        );
        // a value that itself contains '=' keeps everything after the first '='
        assert_eq!(
            parse_root_password(&format!("HOST_ROOT_PASSWORD_EU02={pw}=x=y\n")).unwrap(),
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
            "135.181.75.90".into(),
            "--external".into(),
        ])
        .unwrap();
        assert!(a.external && a.out.is_none());
        assert!(parse_run(&["p.json".into()]).is_err(), "host required");
        assert!(
            parse_run(&["p.json".into(), "--host".into(), "eu02".into()]).is_err(),
            "host must be an ip"
        );
        assert!(
            parse_run(&["--host".into(), "1.1.1.1".into()]).is_err(),
            "plan required"
        );
    }
}
