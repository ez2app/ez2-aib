//! `aib-own-gate turn-start | stop [--root DIR]`: the hook data comes on standard input
//! (Claude Code hook JSON: `session_id`, `transcript_path`). Exit 0 lets the turn go on or
//! end; exit 2 (stop only) holds it, the reason on standard error. Any fault exits 0.
use aib_own_gate::{
    decide, gated_seats, parse_ledger, seat_of, tools_this_turn, turn_start_line, Verdict, LEDGER,
    SEATS_FILE,
};
use serde_json::Value;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "aib-own-gate turn-start | stop [--root DIR]  (hook JSON on standard input)";

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

fn default_root() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(|h| PathBuf::from(h).join(".ez2-aib"))
}

/// Seat files: `<root>/seats/*.json` and `<root>/instances/*/seats/*.json`.
fn seat_files(root: &Path) -> Vec<Value> {
    let mut dirs = vec![root.join("seats")];
    if let Ok(rd) = std::fs::read_dir(root.join("instances")) {
        dirs.extend(rd.flatten().map(|e| e.path().join("seats")));
    }
    dirs.iter()
        .filter_map(|d| std::fs::read_dir(d).ok())
        .flat_map(|rd| rd.flatten())
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| std::fs::read_to_string(e.path()).ok())
        .filter_map(|t| serde_json::from_str::<Value>(&t).ok())
        .collect()
}

/// One line, one write, append only (a ledger line is far under a pipe buffer).
fn append(path: &Path, v: &Value) -> Result<(), String> {
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(format!("{v}\n").as_bytes())
        .map_err(|e| format!("{}: {e}", path.display()))
}

fn run(args: &[String], input: &str) -> i32 {
    let mut root = default_root();
    let mut cmd = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--root" => root = it.next().map(PathBuf::from),
            "turn-start" | "stop" if cmd.is_none() => cmd = Some(a.as_str()),
            _ => {
                eprintln!("{USAGE}");
                return 0;
            }
        }
    }
    let (Some(cmd), Some(root)) = (cmd, root) else {
        eprintln!("{USAGE}");
        return 0;
    };
    let hook: Value = serde_json::from_str(input).unwrap_or(Value::Null);
    let session = hook.get("session_id").and_then(Value::as_str).unwrap_or("");
    let Some(seat) = seat_of(session, &seat_files(&root)) else {
        return 0;
    };
    let drive = root.join("drive");
    let gated = std::fs::read_to_string(drive.join(SEATS_FILE))
        .map(|t| gated_seats(&t))
        .unwrap_or_default();
    if !gated.contains(&seat) {
        return 0;
    }
    let ledger = drive.join(LEDGER);
    let now = now_ms();
    if cmd == "turn-start" {
        if let Err(e) = append(&ledger, &turn_start_line(&seat, now)) {
            eprintln!("aib-own-gate: {e}");
        }
        return 0;
    }
    let text = match std::fs::read_to_string(&ledger) {
        Ok(t) => Ok(t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(e) => Err(format!("{}: {e}", ledger.display())),
    };
    let lines = text.as_deref().map(parse_ledger);
    let tools = hook
        .get("transcript_path")
        .and_then(Value::as_str)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .map(|t| tools_this_turn(&t));
    let first = format!("aib-drive own --seat {seat}");
    let verdict = decide(
        lines.as_deref().map_err(Clone::clone),
        &seat,
        tools,
        &first,
        now,
    );
    match verdict {
        Verdict::Pass(line) => {
            if let Some(l) = line {
                // a ledger that cannot be written never holds a seat
                let _ = append(&ledger, &l);
            }
            0
        }
        Verdict::Hold(l) => {
            if append(&ledger, &l).is_err() {
                return 0;
            }
            eprintln!(
                "aib-own-gate(S79-own):這回合還沒有一筆可驗的推進。\n\
                 先跑 `aib-drive own --seat {seat}` 看能自己做的第 1 件,做完記帳:\n  \
                 aib-drive own done <id> --seat {seat} --evidence <檔|commit|PR|收據|審查稿|dm>\n  \
                 或 aib-drive own progress <id> --seat {seat} --evidence <…> --next \"<下一步>\"\n  \
                 清單真的是空的:aib-drive own none --seat {seat} --why \"<原因>\"\n\
                 再交回一次就會放行(記一筆 blocked)。"
            );
            2
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    std::process::exit(run(&args, &input));
}
