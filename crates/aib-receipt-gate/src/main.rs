//! aib-receipt-gate entry: sshd runs it for every use of aibd's delegate key (`command=`).
//! Slice 1 only reads and checks the request; serving it comes in slices 2 and 3, so every
//! well-formed request answers `ERR not_ready`.

use std::process::ExitCode;

fn main() -> ExitCode {
    let raw = std::env::var_os("SSH_ORIGINAL_COMMAND");
    // a command that is not UTF-8 is not one of ours
    let cmd = match raw.as_deref().map(|s| s.to_str()) {
        Some(None) => {
            println!("ERR bad_words");
            return ExitCode::from(2);
        }
        Some(Some(s)) => Some(s),
        None => None,
    };
    match aib_receipt_gate::parse(cmd) {
        Err(code) => {
            println!("ERR {code}");
            ExitCode::from(2)
        }
        Ok(_) => {
            println!("ERR not_ready");
            ExitCode::from(3)
        }
    }
}
