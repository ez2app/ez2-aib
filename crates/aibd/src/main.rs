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

#[tokio::main]
async fn main() -> ExitCode {
    init_tracing();
    let cfg = match Config::from_lookup(|k| std::env::var(k).ok(), platform::default_data_dir()) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "config");
            return ExitCode::from(2);
        }
    };
    let shutdown = async {
        let _ = tokio::signal::ctrl_c().await;
    };
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
