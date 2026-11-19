mod ctl;
use aibd::{config::Config, platform, server};
use std::process::ExitCode;

fn init_tracing() {
    let filter = tracing_subscriber::EnvFilter::try_from_env("AIBD_LOG")
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .init();
}

const USAGE: &str = "usage: aibd [--version | selfcheck [--json] | ctl ... | status ... | install ... | uninstall ... | import <summary|handoff> [--dry-run | --undo] | start | stop | --help]\n  (no argument: start the local server; aibd ctl --help for client commands)";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    match args.as_slice() {
        // the selfcheck's own tree (Windows: no /bin/sh); hidden, not in USAGE
        [a, marker] if *a == platform::check_tree::ROOT_ARG => {
            return ExitCode::from(platform::check_tree::run_root(std::path::Path::new(marker)));
        }
        [a] if *a == platform::check_tree::LEAF_ARG => {
            return ExitCode::from(platform::check_tree::run_leaf());
        }
        [] => {}
        // S89-talk-1: the MCP server a seat's provider starts (hidden, not in USAGE)
        ["mcp"] | ["mcp", "--data-dir", _] => {
            return match ctl::mcp_run(args.get(2).copied()) {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    eprintln!("aibd mcp: {e}");
                    ExitCode::from(1)
                }
            };
        }
        // N-now slice 2: a seat's Stop hook (exit 2 = block, the reason on stderr)
        ["ctl", "now-gate"] | ["ctl", "now-gate", "--data-dir", _] => {
            return ExitCode::from(ctl::now_gate_run(args.get(3).copied()));
        }
        ["ctl", rest @ ..] => {
            return match ctl::run(rest) {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    eprintln!("aibd ctl: {e}");
                    ExitCode::from(1)
                }
            };
        }
        // §17-3: real conversations kept as files into the message library
        ["import", rest @ ..] => {
            let Some(dir) = std::env::var_os("AIBD_DATA_DIR")
                .map(std::path::PathBuf::from)
                .or_else(platform::default_data_dir)
            else {
                eprintln!("aibd import: no data folder");
                return ExitCode::from(2);
            };
            // the plan tree is read from this folder (519d0 04:2x: item.card was empty because
            // the import command never set it, so every card id looked unknown)
            aibd::workspace::set_data_dir(dir.clone());
            return match aibd::import::run(rest, &dir) {
                Ok(aibd::import::Done::Imported(c)) => {
                    println!("new {} already {} skipped {}", c.new, c.already, c.skipped);
                    ExitCode::SUCCESS
                }
                Ok(aibd::import::Done::Undone { posts, thread }) => {
                    println!(
                        "removed {posts} thread {}",
                        if thread { "removed" } else { "kept" }
                    );
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("aibd import: {e}");
                    ExitCode::from(2)
                }
            };
        }
        ["--version"] | ["-V"] => {
            println!("{}", aibd::selfcheck::version_line());
            return ExitCode::SUCCESS;
        }
        ["--help"] | ["-h"] => {
            println!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        // T97-1m / T97-2m: install / status / uninstall / start / stop (aibd::install)
        [cmd @ ("status" | "install" | "uninstall" | "start" | "stop"), rest @ ..] => {
            // §20 (5d33a M1): a preview build never installs, starts or stops the real one
            if aibd::preview::is_preview() && *cmd != "status" {
                eprintln!("aibd {cmd}: refused in the preview (AIBD_PREVIEW=1)");
                return ExitCode::from(aibd::install::RC_REFUSED);
            }
            let host = match aibd::install::Host::from_process() {
                Ok(h) => h,
                Err(e) => {
                    eprintln!("aibd {cmd}: {e}");
                    return ExitCode::from(aibd::install::RC_REFUSED);
                }
            };
            let ld = platform::launchd();
            let svc = aibd::install::Svc {
                ld: ld.as_ref(),
                health: &aibd::install::HttpHealth,
                pace: &aibd::install::pace,
            };
            let out = aibd::install::run(cmd, rest, &host, &svc);
            print!("{}", out.stdout);
            eprint!("{}", out.stderr);
            return ExitCode::from(out.code);
        }
        // T97-3m: update (the installed aibd decides; aibd::install::update)
        ["update", rest @ ..] => {
            // keep this build's version mark in the binary (read from the file by the next update)
            std::hint::black_box(aibd::install::update::VERSION_MARK.len());
            let host = match aibd::install::Host::from_process() {
                Ok(h) => h,
                Err(e) => {
                    eprintln!("aibd update: {e}");
                    return ExitCode::from(aibd::install::RC_REFUSED);
                }
            };
            let ld = platform::launchd();
            let svc = aibd::install::Svc {
                ld: ld.as_ref(),
                health: &aibd::install::HttpHealth,
                pace: &aibd::install::pace,
            };
            let cs = platform::codesign();
            let u = aibd::install::update::Upd {
                cs: cs.as_ref(),
                ap: &aibd::install::update::NoApprove,
                check: &aibd::install::update::ExeSelfCheck,
            };
            let out = aibd::install::update::run_update(rest, &host, &svc, &u);
            print!("{}", out.stdout);
            eprint!("{}", out.stderr);
            return ExitCode::from(out.code);
        }
        ["selfcheck"] => return selfcheck(false),
        ["selfcheck", "--json"] => return selfcheck(true),
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::from(2);
        }
    }
    init_tracing();
    // DECISION-21 J0 (Windows): aibd puts itself in a KILL_ON_JOB_CLOSE Job
    // before the runtime and before any child exists, and holds it until it
    // exits. Every session Job is nested in J0, so if aibd dies for any reason
    // the OS kills every tree it started. No "run unprotected" mode where the
    // platform has it; where it does not yet, that is logged, never silent.
    use aibd::platform::lifecycle::{Current, Lifecycle, Step};
    let kill_on_exit = match <Current as Lifecycle>::kill_on_exit() {
        Step::Done(Ok(g)) => Some(g),
        Step::Done(Err(tag)) => {
            tracing::error!(
                error = tag,
                "j0: cannot put aibd in its Job; refusing to start"
            );
            return ExitCode::from(1);
        }
        Step::Unsupported { step, why } => {
            tracing::warn!(
                step = step.name(),
                why,
                "kill on exit: not on this platform yet"
            );
            None
        }
        Step::NotApplicable { .. } => None,
    };
    let code = match aibd::runtime::build() {
        Ok(rt) => rt.block_on(run()),
        Err(e) => {
            tracing::error!(error = %e, "runtime");
            ExitCode::from(1)
        }
    };
    // J0 is never closed by aibd itself (1ced7 #806 L1): closing the last handle of a
    // KILL_ON_JOB_CLOSE Job kills every process in it, aibd included, before `code` is
    // returned, so a clean stop would end with the Job's kill code. The OS closes the
    // handle when the process exits (after its exit code is set), which still kills
    // whatever sessions are left. selfcheck does the same.
    std::mem::forget(kill_on_exit);
    code
}

async fn run() -> ExitCode {
    let cfg = match Config::from_lookup(|k| std::env::var(k).ok(), platform::default_data_dir()) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "config");
            return ExitCode::from(2);
        }
    };
    // S79 A2: the 10-minute aib-drive wake, only with AIBD_DRIVE_WAKE=1
    aibd::drive_wake::start(|k| std::env::var(k).ok());
    // N-web-dmbridge: the switches live in <data>/bridge.json (a preview's environment is
    // cleared); the mirror only with "dm_mirror": true there
    aibd::bridge_cfg::set_dir(&cfg.data_dir);
    aibd::dm_import::start(|k| std::env::var(k).ok(), cfg.data_dir.clone());
    let shutdown = platform::shutdown_signal();
    match server::run(cfg, shutdown, |s| {
        // Port and file locations only; the token value is never printed.
        println!(
            "{}",
            serde_json::json!({"listening": s.addr.to_string(), "token_file": s.token_path})
        );
    })
    .await
    {
        Ok(()) => ExitCode::SUCCESS,
        Err(server::StartError::AlreadyRunning) => {
            tracing::error!("already running");
            ExitCode::from(3)
        }
        Err(e) => {
            tracing::error!(error = %e, "aibd failed");
            ExitCode::FAILURE
        }
    }
}

/// Print what this build can do here; rc 1 only when a check failed.
fn selfcheck(json: bool) -> ExitCode {
    let cfg = Config::from_lookup(|k| std::env::var(k).ok(), platform::default_data_dir())
        .map_err(|e| e.to_string());
    let lineage = std::sync::Arc::new(aibd::lineage::Lineage::detect());
    // Windows: aibd's start-up J0 step, run here and read back (1ced7 11-15); the check
    // tree below then runs nested in it, as a session does. The Job is never dropped:
    // closing J0 (KILL_ON_JOB_CLOSE) would end this process before its exit code is
    // set; the OS closes the handle when the process exits.
    #[cfg(windows)]
    let j0 = {
        let (seen, held) = aibd::selfcheck::check_j0();
        std::mem::forget(held);
        Some(seen)
    };
    #[cfg(not(windows))]
    let j0: Option<aibd::selfcheck::J0Seen> = None;
    // contract §6: one real start + stop, with the same lineage a session uses
    let process = aibd::selfcheck::process_row(&aibd::selfcheck::check_process_management(
        platform::default_spawn(),
        Some(lineage.clone()).filter(|l| l.enabled()),
    ));
    let mut rows = aibd::selfcheck::rows(
        cfg,
        &lineage,
        &platform::child_reaping(),
        std::env::consts::OS,
        process,
        j0.as_ref(),
    );
    rows.push(aibd::selfcheck::pairing_row(
        std::env::var("AIBD_PAIRING").ok().as_deref(),
    ));
    if json {
        println!("{}", aibd::selfcheck::render_json(&rows));
    } else {
        print!("{}", aibd::selfcheck::render_text(&rows));
    }
    ExitCode::from(aibd::selfcheck::exit_code(&rows))
}
