//! aib-receipt-gate entry: sshd runs it for every use of aibd's delegate key (`command=`).
//! The client's words are only in `SSH_ORIGINAL_COMMAND`; `command=` gives this program no
//! argument. Arguments are how the gate starts its own supervisor (`--supervise <run id>
//! <head>`, [`aib_receipt_gate::serve`]).

use std::io::Write;
use std::process::ExitCode;

fn refuse(code: &str) -> ExitCode {
    println!("ERR {code}");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let Some(home) = aib_receipt_gate::serve::home_of(std::env::var_os("HOME")) else {
        return refuse("no_home");
    };
    let args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    if !args.is_empty() {
        return match args.as_slice() {
            [verb, run_id, head]
                if verb == "--supervise"
                    && aib_receipt_gate::run_id_ok(run_id)
                    && aib_receipt_gate::head_ok(head)
                    && aib_receipt_gate::head9_of(run_id) == head.get(..9) =>
            {
                aib_receipt_gate::serve::supervise(&home, run_id, head);
                ExitCode::SUCCESS
            }
            _ => refuse("bad_words"),
        };
    }
    let raw = std::env::var_os("SSH_ORIGINAL_COMMAND");
    // a command that is not UTF-8 is not one of ours
    let cmd = match raw.as_deref().map(|s| s.to_str()) {
        Some(None) => return refuse("bad_words"),
        Some(Some(s)) => Some(s),
        None => None,
    };
    let req = match aib_receipt_gate::parse(cmd) {
        Ok(r) => r,
        Err(code) => return refuse(code),
    };
    let Ok(me) = std::env::current_exe() else {
        return refuse("no_self");
    };
    let out = aib_receipt_gate::serve::serve(&req, &home, &me, &mut std::io::stdin().lock());
    let ok = out.starts_with(b"OK ");
    let mut stdout = std::io::stdout().lock();
    if stdout
        .write_all(&out)
        .and_then(|()| stdout.flush())
        .is_err()
    {
        return ExitCode::from(4);
    }
    if ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(3)
    }
}
