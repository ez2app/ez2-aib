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

const USAGE: &str = "usage: aibd [--version | selfcheck [--json] | --help]\n  (no argument: start the local server)";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    match args.as_slice() {
        [] => {}
        ["--version"] | ["-V"] => {
            println!("{}", aibd::selfcheck::version_line());
            return ExitCode::SUCCESS;
        }
        ["--help"] | ["-h"] => {
            println!("{USAGE}");
            return ExitCode::SUCCESS;
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
    // the OS kills every tree it started. No "run unprotected" mode.
    #[cfg(windows)]
    let _j0 = match aib_sys::job::Job::adopt_current() {
        Ok(j) => j,
        Err(e) => {
            tracing::error!(
                error = e.tag(),
                "j0: cannot put aibd in its Job; refusing to start"
            );
            return ExitCode::from(1);
        }
    };
    match aibd::runtime::build() {
        Ok(rt) => rt.block_on(run()),
        Err(e) => {
            tracing::error!(error = %e, "runtime");
            ExitCode::from(1)
        }
    }
}

async fn run() -> ExitCode {
    let cfg = match Config::from_lookup(|k| std::env::var(k).ok(), platform::default_data_dir()) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "config");
            return ExitCode::from(2);
        }
    };
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
    let lineage = aibd::lineage::Lineage::detect();
    let rows = aibd::selfcheck::rows(
        cfg,
        &lineage,
        &platform::child_reaping(),
        std::env::consts::OS,
    );
    if json {
        println!("{}", aibd::selfcheck::render_json(&rows));
    } else {
        print!("{}", aibd::selfcheck::render_text(&rows));
    }
    ExitCode::from(aibd::selfcheck::exit_code(&rows))
}
