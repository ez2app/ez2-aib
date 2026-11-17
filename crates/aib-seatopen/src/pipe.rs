//! How a 2.0 seat's provider is started over pipes (B4-1b-2a, IFACE-b4-1b-2-session-a9136-1114.md
//! §3; 519d0 r5: pipe first, PTY in P3). One table, read by the CLI and by aibd.
//!
//! Only flags known for certain are written, as in 1.x `provider-launch.sh` ("a flag not known
//! for sure is left out rather than guessed"). A provider whose pipe form has not been measured
//! on the test machines yet is refused, never started in a guessed form.
//!
//! `argv[0]` is the program's name, not a path: aibd's allow-list (M1 gate) turns the name into
//! the registered path, and refuses a name that is not listed.

use std::fmt;

/// Why there is no pipe argv.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PipeRefused {
    /// The provider's pipe form is not in the table (yet).
    Provider(String),
    /// Not `dgrs` or `safe`.
    Mode,
    /// The model has the wrong shape ([`crate::model_shape`]).
    Model,
    /// The session id is not an 8-4-4-4-12 hex uuid.
    SessionId,
}

impl fmt::Display for PipeRefused {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PipeRefused::Provider(p) => write!(f, "{p} cannot be started over pipes yet"),
            PipeRefused::Mode => f.write_str("mode must be dgrs or safe"),
            PipeRefused::Model => f.write_str("model has the wrong shape"),
            PipeRefused::SessionId => f.write_str("session id is not a uuid"),
        }
    }
}

impl std::error::Error for PipeRefused {}

/// The providers [`pipe_argv`] can start.
pub const PIPE_PROVIDERS: &[&str] = &["claude"];

/// The argv that starts `provider` in `mode` as a long-lived stream-json conversation.
///
/// - claude: `claude -p --input-format stream-json --output-format stream-json --verbose
///   --session-id <uuid> [--model <m>] [--dangerously-skip-permissions]`. `--session-id` makes
///   the session's id the one aibd chose (1.x: measured on claude 2.1.233). dgrs skips the
///   permission prompts; safe adds nothing. With no one to answer a prompt over pipes,
///   safe means a tool that needs permission is refused, never asked about: only tools
///   that need none run; writing files or running commands needs dgrs (043dd F16 L1).
///   `--session-id` is for a new conversation only: claude refuses an id used before, so
///   picking one up again (after aibd restarts) is `--resume <id>`, a later slice (F16 N1).
/// - every other provider: [`PipeRefused::Provider`] (codex: its pipe forms differ by version
///   and are measured first; IFACE §3 Q1).
pub fn pipe_argv(
    provider: &str,
    mode: &str,
    model: Option<&str>,
    session_id: &str,
) -> Result<Vec<String>, PipeRefused> {
    if !crate::MODES.contains(&mode) {
        return Err(PipeRefused::Mode);
    }
    if model.is_some_and(|m| !crate::model_shape(m)) {
        return Err(PipeRefused::Model);
    }
    if !crate::uuid_shape(session_id) {
        return Err(PipeRefused::SessionId);
    }
    match provider {
        "claude" => {
            // --setting-sources project,local: the seat does not run the user's 1.x global hooks
            // (0eed7 MEASURE-aib2-seat-no-1x-hooks-0eed7-1118: 14 hook runs / 6 s per turn ⇒ 0;
            // OAuth still read; --bare would drop it)
            let mut a: Vec<String> = [
                "claude",
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--setting-sources",
                "project,local",
                "--session-id",
                session_id,
            ]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
            if let Some(m) = model {
                a.push("--model".into());
                a.push(m.into());
            }
            if mode == "dgrs" {
                a.push("--dangerously-skip-permissions".into());
            }
            Ok(a)
        }
        other => Err(PipeRefused::Provider(other.to_owned())),
    }
}

/// S89-live-persona: give the provider the seat's persona card, from the file aibd kept.
/// `false` when this provider has no such flag (the caller refuses rather than open a seat
/// without its persona). The text never goes in argv (seen by `ps`; Windows' command line
/// is 32 K), only the file's path.
pub fn add_persona_file(provider: &str, argv: &mut Vec<String>, file: &str) -> bool {
    // appended to Claude Code's own system prompt: the tools and rules stay
    if provider != "claude" {
        return false;
    }
    argv.push("--append-system-prompt-file".into());
    argv.push(file.into());
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claude_gets_the_persona_as_a_file_others_are_refused() {
        let mut a = pipe_argv("claude", "safe", None, ID).unwrap();
        assert!(add_persona_file("claude", &mut a, "/d/persona-ab.md"));
        assert_eq!(
            a[a.len() - 2..],
            ["--append-system-prompt-file", "/d/persona-ab.md"]
        );
        let mut b = vec!["codex".to_string()];
        assert!(!add_persona_file("codex", &mut b, "/d/p.md"));
        assert_eq!(b, ["codex"]);
    }

    const ID: &str = "0f1e2d3c-4b5a-4968-8776-655443322110";

    fn words(a: &[String]) -> Vec<&str> {
        a.iter().map(String::as_str).collect()
    }

    #[test]
    fn claude_dgrs_with_a_model_is_the_whole_row() {
        let a = pipe_argv("claude", "dgrs", Some("opus"), ID).unwrap();
        assert_eq!(
            words(&a),
            [
                "claude",
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--setting-sources",
                "project,local",
                "--session-id",
                ID,
                "--model",
                "opus",
                "--dangerously-skip-permissions",
            ]
        );
    }

    #[test]
    fn claude_safe_adds_no_permission_flag() {
        let a = pipe_argv("claude", "safe", None, ID).unwrap();
        assert_eq!(a.last().map(String::as_str), Some(ID));
        assert!(!a.iter().any(|w| w.contains("dangerously")), "{a:?}");
        assert!(!a.iter().any(|w| w == "--model"), "{a:?}");
    }

    #[test]
    fn a_provider_not_measured_is_refused() {
        for p in ["codex", "agy", "grok", "copilot", "nope"] {
            assert_eq!(
                pipe_argv(p, "dgrs", None, ID),
                Err(PipeRefused::Provider(p.into())),
                "{p}"
            );
        }
        assert_eq!(PIPE_PROVIDERS, ["claude"]);
    }

    #[test]
    fn mode_model_and_session_id_are_checked() {
        assert_eq!(
            pipe_argv("claude", "yolo", None, ID),
            Err(PipeRefused::Mode)
        );
        assert_eq!(
            pipe_argv("claude", "dgrs", Some("a b"), ID),
            Err(PipeRefused::Model)
        );
        for bad in [
            "",
            "0f1e2d3c4b5a49688776655443322110",
            "0f1e2d3c-4b5a-4968-8776-65544332211",
            "0f1e2d3c-4b5a-4968-8776-65544332211g",
        ] {
            assert_eq!(
                pipe_argv("claude", "dgrs", None, bad),
                Err(PipeRefused::SessionId),
                "{bad:?}"
            );
        }
    }
}
