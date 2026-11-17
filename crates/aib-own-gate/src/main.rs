//! `aib-own-gate turn-start | stop [--root DIR]`: the hook data comes on standard input
//! (Claude Code hook JSON: `session_id`, `transcript_path`). Exit 0 lets the turn go on or
//! end; exit 2 (stop only) holds it, the reason on standard error. Any fault exits 0.
use aib_own_gate::{
    card_written, current_turn_start_ms, decide_with_card, gated_seats, parse_ledger,
    resources_since, round_logged, seat_of, tools_since, trigger_of, turn_start_line, Card, Round,
    Verdict, LEDGER, PLAN_EVENTS, ROUNDS, SEATS_FILE, TAIL_BYTES, TRIGGER_AUTO,
};
use serde_json::Value;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "aib-own-gate turn-start | stop [--root DIR]  (hook JSON on standard input)";

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

/// The state root, found the way aib-drive finds it (1ced7 o5 L2): `EZ2_AIB_STATE_ROOT`, then
/// `EZ2_AIB_HOME`, then `<home>/.ez2-aib` — so the gate reads the round ledger aib-drive wrote.
fn default_root() -> Option<PathBuf> {
    aib_own_gate::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    )
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

/// The last [`TAIL_BYTES`] of a file as text (a cut first line dropped) and whether that is
/// the whole file; `None` when it cannot be read.
fn read_tail(path: &Path) -> Option<(String, bool)> {
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    let whole = len <= TAIL_BYTES;
    if !whole {
        f.seek(SeekFrom::Start(len - TAIL_BYTES)).ok()?;
    }
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    let mut text = String::from_utf8_lossy(&buf).into_owned();
    if !whole {
        text = text
            .split_once('\n')
            .map(|(_, rest)| rest.to_owned())
            .unwrap_or_default();
    }
    Some((text, whole))
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
    let transcript = hook.get("transcript_path").and_then(Value::as_str);
    if cmd == "turn-start" {
        if let Err(e) = append(&ledger, &turn_start_line(&seat, now, transcript)) {
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
    let start = lines
        .as_deref()
        .ok()
        .and_then(|l| current_turn_start_ms(l, &seat));
    let tail = transcript.and_then(|p| read_tail(Path::new(p)));
    let tools = match (start, tail.as_ref()) {
        (Some(st), Some((t, whole))) => tools_since(t, st, *whole),
        _ => None,
    };
    // S79-own o5: a turn 自動推進 started ends only with its round line; a round ledger that
    // cannot be read never holds a seat
    let round = match (start, tail.as_ref()) {
        (Some(st), Some((t, _))) if trigger_of(t) == TRIGGER_AUTO => {
            match std::fs::read_to_string(drive.join(ROUNDS)) {
                Ok(r) if round_logged(&parse_ledger(&r), &seat, st) => Round::Logged,
                Ok(_) => Round::Missing,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Round::Missing,
                Err(_) => Round::NotNeeded,
            }
        }
        _ => Round::NotNeeded,
    };
    // S107-loop: a turn that used a shared resource ends only after a card was written; a card
    // ledger that cannot be read never holds a seat
    let used = match (start, tail.as_ref()) {
        (Some(st), Some((t, _))) => resources_since(t, st),
        _ => Vec::new(),
    };
    let card = match (start, used.is_empty()) {
        (Some(st), false) => match std::fs::read_to_string(root.join(PLAN_EVENTS)) {
            Ok(e) if card_written(&e, &seat, st) => Card::Written,
            Ok(_) => Card::Missing,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Card::Missing,
            Err(_) => Card::NotNeeded,
        },
        _ => Card::NotNeeded,
    };
    let first = format!("aib-drive own --seat {seat}");
    let verdict = decide_with_card(
        lines.as_deref().map_err(ToString::to_string),
        &seat,
        tools,
        round,
        card,
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
            let lacks = |k: &str| {
                l.get("missing")
                    .and_then(Value::as_array)
                    .is_some_and(|m| m.iter().any(|x| x.as_str() == Some(k)))
            };
            if lacks("round") {
                eprintln!(
                    "aib-own-gate(S79-own o5):這是自動推進開的回合,還沒記這一輪。\n  \
                     aib-drive round done --seat {seat} --recv <收幾封> --sent <發幾封> --own <own 帳的 id>\n  \
                     什麼都沒做:aib-drive round done --seat {seat} --recv 0 --sent 0 --own none --why \"<原因>\"\n  \
                     有信的話加 --dm-in q_…,q_… / --dm-out q_…;查:aib-drive round log --seat {seat}"
                );
            }
            if lacks("card") {
                eprintln!(
                    "aib-own-gate(S107-loop):這回合用了{},還沒更新卡。\n  \
                     換站:aib-plan set <卡號> --status <站> --add \"<事實:頭 40 碼 / 收據檔 / PR>\"\n  \
                     不換站:aib-plan note <卡號> \"<事實>\"\n  \
                     沒有卡:aib-plan add <父卡> --title \"<標題>\";查自己的卡:aib-plan mine",
                    used.join("、")
                );
            }
            if !lacks("own") {
                eprintln!("再交回一次就會放行(記一筆 blocked)。");
                return 2;
            }
            eprintln!(
                "aib-own-gate(S79-own):這回合還沒有一筆可驗的推進。\n\
                 先跑 `aib-drive own --seat {seat}` 看能自己做的第 1 件,做完記帳:\n  \
                 aib-drive own done --seat {seat} --id <id> --evidence <type>:<ref>\n  \
                 或 aib-drive own progress --seat {seat} --id <id> --evidence <type>:<ref> --next \"<下一步>\"\n  \
                 清單真的是空的:aib-drive own none --seat {seat} --why \"<原因>\"\n\
                 type = file / commit / pr / receipt / review / node / dm(node 只算 progress)。\n\
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
