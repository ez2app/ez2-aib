// SPDX-License-Identifier: Apache-2.0
//! aib-lease — the per-machine run lease for a holder that may run on another machine.
//!
//!   aib-lease acquire   --owner O --what W --run-id ID [--host H --heartbeat-s S] [--pid P]
//!                       [--ttl S] [--exclusive] [--priority] [--cwd DIR]
//!   aib-lease queue add --owner O --what W --run-id ID [--host H --heartbeat-s S] [--pid P]
//!                       [--ttl S] [--notify CABIN/SEAT] [--priority]
//!   aib-lease queue list [--all]
//!   aib-lease heartbeat --run-id ID
//!   global: --now EPOCH (fixtures)
//!
//! `--host` is the machine the holder process runs on. Another machine ⇒ `--heartbeat-s` is
//! required and the record lives only while `heartbeat` keeps arriving (each one buys
//! 3 × interval). release / renew / status / queue remove: the 1.x `aib lease` (same files).
//!
//! Prints one JSON line. Exit: 0 done (acquired / re-entered / took over / queued / beat),
//! 2 usage (or: the pid given is already gone), 3 lease files unreadable (fail closed),
//! 4 held by someone else / queue ahead / heartbeat for a record that is gone. Judge by rc.

use aib_lease::{
    acquire, heartbeat, queue_add, queue_list, AcquireArgs, Holder, Outcome, Paths, QueueAddArgs,
    SysProbe, RC_USAGE,
};
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Default)]
struct Args {
    cmd: Vec<String>,
    now: Option<f64>,
    holder: Holder,
    ttl: Option<i64>,
    exclusive: bool,
    priority: bool,
    all: bool,
    cwd: Option<String>,
    notify: Option<String>,
}

fn int(k: &str, v: String) -> Result<i64, String> {
    v.parse().map_err(|_| format!("{k}: integer"))
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args::default();
    let mut it = argv.iter();
    while let Some(k) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{k} needs a value"))
        };
        match k.as_str() {
            "--now" => a.now = Some(val()?.parse().map_err(|_| "--now: number")?),
            "--owner" => a.holder.owner = val()?,
            "--what" => a.holder.what = val()?,
            "--run-id" => a.holder.run_id = val()?,
            "--pid" => a.holder.pid = Some(int(k, val()?)?),
            "--host" => a.holder.host = Some(val()?),
            "--heartbeat-s" => a.holder.heartbeat_s = Some(int(k, val()?)?),
            "--ttl" => a.ttl = Some(int(k, val()?)?),
            "--cwd" => a.cwd = Some(val()?),
            "--notify" => a.notify = Some(val()?),
            "--exclusive" => a.exclusive = true,
            "--priority" => a.priority = true,
            "--all" => a.all = true,
            _ if k.starts_with("--") => return Err(format!("unknown argument {k}")),
            _ => a.cmd.push(k.clone()),
        }
    }
    Ok(a)
}

fn need(a: &Args, keys: &[&str]) -> Result<(), String> {
    for k in keys {
        let empty = match *k {
            "--owner" => a.holder.owner.is_empty(),
            "--what" => a.holder.what.is_empty(),
            _ => a.holder.run_id.is_empty(),
        };
        if empty {
            return Err(format!("{k} is required"));
        }
    }
    Ok(())
}

fn run(a: &Args) -> Result<Outcome, String> {
    let now = a.now.unwrap_or_else(|| {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    });
    let paths = Paths::from_env();
    let p = SysProbe;
    let cmd: Vec<&str> = a.cmd.iter().map(String::as_str).collect();
    match cmd.as_slice() {
        ["acquire"] => {
            need(a, &["--owner", "--what", "--run-id"])?;
            let args = AcquireArgs {
                holder: a.holder.clone(),
                ttl: a.ttl,
                exclusive: a.exclusive,
                priority: a.priority,
                cwd: a.cwd.clone(),
            };
            Ok(acquire(&paths, &p, now, &args))
        }
        ["queue", "add"] => {
            need(a, &["--owner", "--what", "--run-id"])?;
            let args = QueueAddArgs {
                holder: a.holder.clone(),
                ttl: a.ttl,
                notify: a.notify.clone(),
                priority: a.priority,
            };
            Ok(queue_add(&paths, &p, now, &args))
        }
        ["queue", "list"] => Ok(queue_list(&paths, &p, now, a.all)),
        ["heartbeat"] => {
            need(a, &["--run-id"])?;
            Ok(heartbeat(&paths, &p, now, &a.holder.run_id))
        }
        _ => Err("command: acquire | queue add | queue list | heartbeat".into()),
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let o = match parse(&argv).and_then(|a| run(&a)) {
        Ok(o) => o,
        Err(e) => Outcome {
            rc: RC_USAGE,
            out: json!({"result": "usage", "error": e}),
        },
    };
    println!("{}", o.out);
    std::process::exit(o.rc);
}
