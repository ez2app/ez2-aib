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
    /// The provider starts over pipes, but not in this mode yet (codex dgrs: S89-live-2 r3).
    ProviderMode(String, String),
}

impl fmt::Display for PipeRefused {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PipeRefused::Provider(p) => write!(f, "{p} cannot be started over pipes yet"),
            PipeRefused::Mode => f.write_str("mode must be dgrs or safe"),
            PipeRefused::Model => f.write_str("model has the wrong shape"),
            PipeRefused::SessionId => f.write_str("session id is not a uuid"),
            PipeRefused::ProviderMode(p, m) => write!(f, "{p} cannot be started in {m} mode yet"),
        }
    }
}

impl std::error::Error for PipeRefused {}

/// The providers [`pipe_argv`] can start.
pub const PIPE_PROVIDERS: &[&str] = &["claude", "codex"];

/// The modes each of [`PIPE_PROVIDERS`] starts in (GET version's capabilities).
pub fn pipe_modes(provider: &str) -> &'static [&'static str] {
    match provider {
        "claude" => &["dgrs", "safe"],
        // S89-live-2 r3: a dgrs codex seat still reads the user's ~/.codex/AGENTS.md (the 1.x
        // seat start-up it then runs); only a seat CODEX_HOME keeps it out, and how that
        // home shares the login is not measured yet. A safe seat has no tool to run it with.
        "codex" => &["safe"],
        _ => &[],
    }
}

/// S89-live-2: the codex versions whose app-server protocol was measured (r2 / r3 on
/// 0.167.0). `out` is what `codex --version` prints; anything else is refused with it.
pub fn codex_version_ok(out: &str) -> bool {
    out.lines().any(|l| {
        l.trim()
            .strip_prefix("codex-cli 0.167.")
            .is_some_and(|r| !r.is_empty() && r.bytes().all(|c| c.is_ascii_digit()))
    })
}

/// S89-live-2 r3: what keeps the user's codex set-up out of a seat and, in safe mode, every
/// tool: the account MCP servers this Mac has, 1.x hooks and plugins, apps, and for safe the
/// shell / exec / code mode / sub-agents / browser / images / web. Measured on 0.167.0
/// (scratchpad probe-a..d): MCP starts 0, hook runs 0, a safe seat asked to read a file runs
/// 0 commands. `-c` keys only: nothing here is a path or the persona text.
const CODEX_OFF: &[&str] = &[
    "features.hooks=false",
    "features.plugins=false",
    "features.apps=false",
    "features.browser_use=false",
    "features.computer_use=false",
    "features.image_generation=false",
    "features.tool_suggest=false",
    "features.skill_search=false",
    "features.multi_agent=false",
    "mcp_servers.openaiDeveloperDocs.enabled=false",
    "mcp_servers.node_repl.enabled=false",
    "mcp_servers.computer-use.enabled=false",
    "project_doc_max_bytes=0",
    "approval_policy=\"never\"",
];
const CODEX_SAFE_OFF: &[&str] = &[
    "sandbox_mode=\"read-only\"",
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.code_mode_host=false",
    "features.view_image=false",
    "features.sleep_tool=false",
    "web_search=\"disabled\"",
];

/// S89-live-mcp (519d0 / 5d33a 11-18): a seat loads no MCP server but the ones its own
/// settings name, so the account-level connectors stay out; a seat that needs one gets it
/// through the session policy's allow list later.
pub const NO_CONNECTORS: &str = "--strict-mcp-config";

/// The argv that starts `provider` in `mode` as a long-lived stream-json conversation.
///
/// - claude: `claude -p --input-format stream-json --output-format stream-json --verbose
///   --session-id <uuid> [--model <m>] [--dangerously-skip-permissions]`. `--session-id` makes
///   the session's id the one aibd chose (1.x: measured on claude 2.1.233). dgrs skips the
///   permission prompts; safe adds nothing. With no one to answer a prompt over pipes,
///   safe means a tool that needs permission is refused, never asked about: only tools
///   that need none run; writing files or running commands needs dgrs (043dd F16 L1).
///   `--session-id` is for a new conversation only: claude refuses an id used before, so
///   picking one up again (after aibd restarts) is [`resume_argv`] (F16 N1, N-revive).
/// - every other provider: [`PipeRefused::Provider`] (codex: its pipe forms differ by version
///   and are measured first; IFACE §3 Q1).
pub fn pipe_argv(
    provider: &str,
    mode: &str,
    model: Option<&str>,
    session_id: &str,
) -> Result<Vec<String>, PipeRefused> {
    argv_with(provider, mode, model, NEW_FLAG, session_id)
}

/// The flag that starts a new conversation with the id aibd chose.
const NEW_FLAG: &str = "--session-id";
/// The flag that picks an existing conversation up again by its id.
pub const RESUME_FLAG: &str = "--resume";

/// N-revive (a9136 11-18, ORDER-workspace 第三波): the argv that picks the conversation
/// `session_id` up again after aibd restarted, the same as [`pipe_argv`] but `--resume <id>`
/// in place of `--session-id <id>`, so the seat goes on where it stopped.
pub fn resume_argv(
    provider: &str,
    mode: &str,
    model: Option<&str>,
    session_id: &str,
) -> Result<Vec<String>, PipeRefused> {
    argv_with(provider, mode, model, RESUME_FLAG, session_id)
}

/// N-revive: the folder where claude keeps the conversations started in `cwd`:
/// `<config>/projects/<cwd with every character other than A-Z a-z 0-9 - written as ->`, each
/// conversation `<id>.jsonl` (measured, claude 2.1.326 on macOS: `/Users/x/Library/Application
/// Support/app.Ez2.AIB/seat-work/seat_ab` → `-Users-x-Library-Application-Support-app-Ez2-AIB-
/// seat-work-seat-ab`). `config` is `CLAUDE_CONFIG_DIR` or `<home>/.claude`. A conversation that
/// never had a turn has no file, and `--resume` of it is refused.
pub fn claude_project_dir(config: &std::path::Path, cwd: &str) -> std::path::PathBuf {
    let name: String = cwd
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect();
    config.join("projects").join(name)
}

fn argv_with(
    provider: &str,
    mode: &str,
    model: Option<&str>,
    id_flag: &str,
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
                NO_CONNECTORS,
                id_flag,
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
            // 5d33a REVIEW-S89-talk1 M1: claude -p's default mode still lets a seat use the
            // built-in read tools (Read / Grep / Glob) without asking, so "a safe seat has no
            // tools" was not true. `--tools ""` takes every built-in away (this Mac, claude
            // 2.1.326: init tools [] ); aibd's own MCP tools are not built-ins and stay. It goes
            // before --session-id: --tools takes the words after it up to the next flag.
            if mode == "safe" {
                let at = a.iter().position(|w| w == id_flag).unwrap_or(a.len());
                a.splice(at..at, ["--tools".to_owned(), String::new()]);
            }
            Ok(a)
        }
        // S89-live-2: `codex app-server` (stdio JSON-RPC, one process a seat, like claude's
        // stream-json); aibd speaks the protocol (crate aibd codex_wire), the persona goes in
        // thread/start's developerInstructions, never argv. The session id stays aibd's own:
        // codex names its thread itself.
        "codex" => {
            if !pipe_modes("codex").contains(&mode) {
                return Err(PipeRefused::ProviderMode("codex".into(), mode.into()));
            }
            let mut a = vec!["codex".to_owned(), "app-server".to_owned()];
            for kv in CODEX_OFF.iter().chain(CODEX_SAFE_OFF) {
                a.push("-c".into());
                a.push((*kv).to_owned());
            }
            if let Some(m) = model {
                a.push("-c".into());
                a.push(format!("model=\"{m}\""));
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
    // codex: aibd hands the card over in the protocol (thread/start developerInstructions,
    // codex_wire), so nothing goes in argv
    if provider == "codex" {
        return true;
    }
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
        assert!(add_persona_file("codex", &mut b, "/d/p.md"));
        assert_eq!(b, ["codex"], "reason: persona_in_codex_argv");
        let mut c = vec!["agy".to_string()];
        assert!(!add_persona_file("agy", &mut c, "/d/p.md"));
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
                "--strict-mcp-config",
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

    /// 5d33a REVIEW-S89-talk1 M1: a safe seat gets no built-in tool (`--tools ""`, before
    /// `--session-id` so the empty word is its value); a dgrs seat keeps the default set.
    #[test]
    fn a_safe_claude_seat_gets_no_built_in_tool() {
        let a = pipe_argv("claude", "safe", None, ID).unwrap();
        let at = a.iter().position(|w| w == "--tools");
        assert!(at.is_some(), "reason: safe_seat_keeps_built_in_tools {a:?}");
        let at = at.unwrap();
        assert_eq!(a[at + 1], "", "{a:?}");
        assert_eq!(a[at + 2], "--session-id", "{a:?}");
        let d = pipe_argv("claude", "dgrs", None, ID).unwrap();
        assert!(!d.iter().any(|w| w == "--tools"), "{d:?}");
    }

    #[test]
    fn a_provider_not_measured_is_refused() {
        for p in ["agy", "grok", "copilot", "nope"] {
            assert_eq!(
                pipe_argv(p, "dgrs", None, ID),
                Err(PipeRefused::Provider(p.into())),
                "{p}"
            );
        }
        assert_eq!(PIPE_PROVIDERS, ["claude", "codex"]);
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

    #[test]
    fn codex_starts_safe_only_with_the_users_set_up_kept_out() {
        assert_eq!(
            pipe_argv("codex", "dgrs", None, ID),
            Err(PipeRefused::ProviderMode("codex".into(), "dgrs".into()))
        );
        let a = pipe_argv("codex", "safe", Some("gpt-5.5"), ID).unwrap();
        assert_eq!(&a[..2], ["codex", "app-server"]);
        let set: Vec<&str> = a
            .chunks(2)
            .skip(1)
            .map(|p| {
                assert_eq!(p[0], "-c", "{a:?}");
                p[1].as_str()
            })
            .collect();
        for kv in [
            "features.hooks=false",
            "features.plugins=false",
            "features.shell_tool=false",
            "sandbox_mode=\"read-only\"",
            "approval_policy=\"never\"",
            "mcp_servers.node_repl.enabled=false",
            "features.code_mode_host=false",
        ] {
            assert!(set.contains(&kv), "reason: codex_flag_missing {kv} {a:?}");
        }
        assert_eq!(set.last(), Some(&"model=\"gpt-5.5\""));
        assert!(!a.iter().any(|w| w.contains("danger")), "{a:?}");
        assert_eq!(pipe_modes("codex"), ["safe"]);
        assert_eq!(pipe_modes("claude"), ["dgrs", "safe"]);
        assert!(pipe_modes("agy").is_empty());
    }

    #[test]
    fn only_the_measured_codex_version_is_taken() {
        assert!(codex_version_ok("codex-cli 0.167.0\n"));
        assert!(codex_version_ok("banner\ncodex-cli 0.167.12"));
        for v in [
            "codex-cli 0.168.0",
            "codex-cli 0.167.",
            "codex-cli 0.167.x",
            "codex-cli 1.167.0",
            "",
            "0.167.0",
        ] {
            assert!(!codex_version_ok(v), "{v}");
        }
    }

    /// N-revive: picking a seat up again names the same conversation with --resume, keeps
    /// the mode's flags, and never also says --session-id (claude refuses an id used before).
    #[test]
    fn a_seat_picked_up_again_resumes_its_conversation() {
        let id = "0b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f10";
        for mode in ["safe", "dgrs"] {
            let a = resume_argv("claude", mode, Some("opus"), id).unwrap();
            let at = a.iter().position(|w| w == "--resume").unwrap();
            assert_eq!(a[at + 1], id, "{a:?}");
            assert!(
                !a.iter().any(|w| w == "--session-id"),
                "reason: resume_says_new {a:?}"
            );
            let mut b = pipe_argv("claude", mode, Some("opus"), id).unwrap();
            let bt = b.iter().position(|w| w == "--session-id").unwrap();
            b[bt] = "--resume".into();
            assert_eq!(a, b, "reason: resume_differs_beyond_the_flag");
        }
        assert!(resume_argv("claude", "safe", None, "not-a-uuid").is_err());
    }

    /// The folder name is the cwd with every other character as `-` (macOS and Windows shapes).
    #[test]
    fn claudes_folder_for_a_seat_follows_its_cwd() {
        let c = std::path::Path::new("/h/.claude");
        assert_eq!(
            claude_project_dir(
                c,
                "/Users/x/Library/Application Support/app.Ez2.AIB/seat-work/seat_ab"
            ),
            c.join("projects")
                .join("-Users-x-Library-Application-Support-app-Ez2-AIB-seat-work-seat-ab")
        );
        assert_eq!(
            claude_project_dir(c, r"C:\Users\x\AppData\seat_ab"),
            c.join("projects").join("C--Users-x-AppData-seat-ab")
        );
    }
}
