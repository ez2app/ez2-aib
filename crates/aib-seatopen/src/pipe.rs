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
pub const PIPE_PROVIDERS: &[&str] = &["claude", "codex", "grok", "copilot"];

/// The modes each of [`PIPE_PROVIDERS`] starts in (GET version's capabilities).
pub fn pipe_modes(provider: &str) -> &'static [&'static str] {
    match provider {
        "claude" => &["dgrs", "safe", "work"],
        // S89-live-2 r3: a dgrs codex seat still reads the user's ~/.codex/AGENTS.md (the 1.x
        // seat start-up it then runs); only a seat CODEX_HOME keeps it out, and how that
        // home shares the login is not measured yet. A safe seat has no tool to run it with.
        "codex" => CODEX_MODES,
        // AP31 (andymini 11-25 probe on grok 1.0.70): safe-only until work sandbox probes pass.
        "grok" => &["safe"],
        // AP30 (andymini 11-25 probe on copilot 1.0.103): safe-only.
        "copilot" => &["safe"],
        _ => &[],
    }
}

/// Built-in tools removed in grok `safe` mode (measured on grok 1.0.70: `--tools ""` still
/// left all 24 built-in tools enabled and ran `read_file` / `curl`, whereas `--disallowed-tools`
/// with this list yields `"tools":[]` and refuses file, shell, and web-fetch).
pub const GROK_SAFE_DISALLOWED: &str = "run_terminal_command,read_file,search_replace,list_dir,grep,kill_command_or_subagent,todo_write,get_command_or_subagent_output,scheduler_create,scheduler_delete,scheduler_list,monitor,search_tool,use_tool,workflow,enter_plan_mode,exit_plan_mode,ask_user_question,send_feedback,image_gen,image_edit,image_to_video,reference_to_video,write";

/// The grok versions whose flags were measured (BRAND-grok-ae921-1124: 1.0.70). `out` is what
/// `grok --version` prints (`grok 1.0.70 (f50904eaee6c)`); anything else is refused with it.
pub fn grok_version_ok(out: &str) -> bool {
    out.lines().any(|l| {
        l.trim()
            .strip_prefix("grok 1.0.70")
            .is_some_and(|r| r.is_empty() || r.starts_with(' '))
    })
}

/// What a grok safe seat must be started with, each exactly once: flag and value (`None` for a
/// bare flag). 08de5 11-25 (ebab8 q_cd554b24 condition 1): checked where the seat starts,
/// not only where [`pipe_argv`] builds the words.
pub const GROK_SAFE_REQUIRED: &[(&str, Option<&str>)] = &[
    ("--permission-mode", Some("plan")),
    ("--disable-web-search", None),
    ("--no-subagents", None),
    ("--sandbox", Some("readonly")),
    ("--disallowed-tools", Some(GROK_SAFE_DISALLOWED)),
];

/// Words that would give a grok safe seat back what [`GROK_SAFE_REQUIRED`] takes away (an
/// allow rule, a tool list, an extra folder, an agent, a prompt of its own, auto-approval).
pub const GROK_SAFE_FORBIDDEN: &[&str] = &[
    "--allow",
    "--allowedTools",
    "--allowed-tools",
    "--always-approve",
    "--tools",
    "--add-dir",
    "--agent",
    "--agents",
    "--system-prompt-override",
    "--system-prompt",
    "--worktree",
    "-w",
    "--cwd",
];

/// The first reason a grok argv is not a safe seat's ([`safe_refusal`] with grok's lists).
pub fn grok_safe_refusal(argv: &[String]) -> Option<String> {
    safe_refusal(argv, GROK_SAFE_REQUIRED, GROK_SAFE_FORBIDDEN)
}

/// The first reason `argv` (`argv[0]` the program) is not a safe seat's: a `required` flag
/// missing, given twice or with another value (`missing:--sandbox`), or a `forbidden` one
/// present (`forbidden:--tools`). A `--flag=value` form counts as the flag.
pub fn safe_refusal(
    argv: &[String],
    required: &[(&str, Option<&str>)],
    forbidden: &[&str],
) -> Option<String> {
    let words = argv.get(1..).unwrap_or_default();
    let name = |w: &str| w.split_once('=').map_or(w, |(n, _)| n).to_owned();
    for w in words {
        if forbidden.contains(&name(w).as_str()) {
            return Some(format!("forbidden:{}", name(w)));
        }
    }
    for (flag, value) in required {
        let at: Vec<usize> = (0..words.len())
            .filter(|&i| name(&words[i]) == *flag)
            .collect();
        let ok = match (at.as_slice(), value) {
            ([i], None) => words[*i] == *flag,
            ([i], Some(v)) => words[*i] == *flag && words.get(i + 1).is_some_and(|x| x == v),
            _ => false,
        };
        if !ok {
            return Some(format!("missing:{flag}"));
        }
    }
    None
}

/// The one copilot version aibd starts (3e53b 11-26): conditions 1-5 read again on it without
/// a login, and its tools-off turn, MCP servers and hooks measured on it
/// (EVIDENCE-copilot-116-08de5-1126). aibd pins each seat to it (`COPILOT_CLI_DIST_DIR`), so a
/// copilot that updated itself does not change what a seat runs.
pub const COPILOT_PINNED: &str = "1.0.116";

/// Is `out` (what `copilot --version` printed) the pinned version, [`COPILOT_PINNED`]?
pub fn copilot_version_ok(out: &str) -> bool {
    out.lines().any(|l| {
        l.trim()
            .strip_prefix("GitHub Copilot CLI ")
            .and_then(|r| r.strip_prefix(COPILOT_PINNED))
            .is_some_and(|r| r.is_empty() || r == ".")
    })
}

/// What a copilot safe seat must be started with, each exactly once.
pub const COPILOT_SAFE_REQUIRED: &[(&str, Option<&str>)] = &[
    ("--output-format", Some("json")),
    ("--available-tools", Some("nonexistent_tool")),
    ("--disable-builtin-mcps", None),
    ("--disallow-temp-dir", None),
    ("--deny-url", Some("*")),
    ("--no-custom-instructions", None),
];

/// Words that would give a copilot safe seat back what [`COPILOT_SAFE_REQUIRED`] takes away,
/// or put the prompt in argv (`-p`, `-i`).
pub const COPILOT_SAFE_FORBIDDEN: &[&str] = &[
    "--allow-all",
    "--allow-all-paths",
    "--allow-all-tools",
    "--allow-all-urls",
    "--allow-all-mcp-server-instructions",
    "--allow-tool",
    "--allow-url",
    "--yolo",
    "--autopilot",
    "--mode",
    "--fleet",
    "--add-dir",
    "--plugin-dir",
    "--additional-mcp-config",
    "--add-github-mcp-tool",
    "--add-github-mcp-toolset",
    "--enable-all-github-mcp-tools",
    "--enable-mcp-server",
    "--agent",
    "--trust-folder",
    "--assisted-approval",
    "--connect",
    "--acp",
    "--share",
    "--share-gist",
    "--sync-session",
    "-C",
    "-p",
    "--prompt",
    "-i",
    "--interactive",
];

/// The model a copilot seat asks for when none is given: copilot picks among those the plan has.
pub const COPILOT_ANY_MODEL: &str = "auto";

/// The first reason a copilot argv is not a safe seat's ([`safe_refusal`]).
pub fn copilot_safe_refusal(argv: &[String]) -> Option<String> {
    safe_refusal(argv, COPILOT_SAFE_REQUIRED, COPILOT_SAFE_FORBIDDEN)
}

/// N-ws-codex-work (FEASIBILITY-codex-work-seat-7ebca-1120 C, COUNCIL-worktree-gitdir Q3): a
/// codex work seat runs in a codex permission profile that only macOS was measured with
/// (codex's Windows sandbox is not measured), so Windows offers safe only.
#[cfg(target_os = "macos")]
const CODEX_MODES: &[&str] = &["safe", "work"];
#[cfg(not(target_os = "macos"))]
const CODEX_MODES: &[&str] = &["safe"];

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
];
/// Off for a safe and a work codex seat alike.
const CODEX_NO_CODE: &[&str] = &[
    // 0eed7 VERIFY-realentry-ea0885a7 H1 (7ebca measured codex-cli 0.167.0, codex exec with
    // these settings and a one-tool MCP server): with code_mode_host off every MCP tool call
    // fails "code-mode host is disabled" (aibd's seat_send / thread_send / ask_person never
    // reach aibd); with it on (codex's default) the call goes through, no approval asked.
    // What a safe seat must not have is code mode itself (the model writing code to run):
    // that stays off, by name.
    "features.code_mode=false", // no code written to run
    "features.code_mode_only=false",
    "features.view_image=false",
    "features.sleep_tool=false",
    "web_search=\"disabled\"",
];

/// N-ws-codex-work: a codex work seat. It names the permission profile `seat`; the profile
/// itself (which folders it reads and writes) needs the seat's folder and is only added by
/// [`codex_work_args`]. Measured on 0.167.0: a `default_permissions` with no `[permissions]`
/// table refuses to start ("default_permissions requires a `[permissions]` table"), so argv
/// without the folder half never opens a seat with codex's own sandbox. No `sandbox_mode`:
/// `workspace-write` let a seat read `~/.ssh` and write `/tmp` (C vs A in the measure).
const CODEX_WORK_OFF: &[&str] = &[
    "default_permissions=\"seat\"", // the profile codex_work_args fills
    "permissions.seat.network.enabled=false", // no network
    // the seat's git reads no user or system config (`:minimal` cannot read ~/.gitconfig,
    // git then stops; and a user's config is not the seat's)
    "shell_environment_policy.set={GIT_CONFIG_GLOBAL=\"/dev/null\", GIT_CONFIG_NOSYSTEM=\"1\"}",
];

/// Folders a codex work seat may read besides codex's `:minimal` set, when they exist: the
/// developer tools `/usr/bin/git` runs from (without them git asks to install the tools and
/// stops) and the usual places of `node`. A folder that does not exist is left out: codex's
/// sandbox helper fails on one (measured, exit 71).
pub const CODEX_WORK_READ: &[&str] = &[
    "/Library/Developer/CommandLineTools",
    "/Applications/Xcode.app/Contents/Developer",
    "/private/var/db/xcode_select_link",
    "/var/db/xcode_select_link",
    "/opt/homebrew",
    "/usr/local/bin",
    "/usr/local/lib/node_modules",
];

/// A TOML basic string: `"` and `\\` escaped, control characters written `\\uXXXX`.
fn q(v: &str) -> String {
    let mut o = String::with_capacity(v.len() + 2);
    o.push('"');
    for c in v.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if c.is_control() => o.push_str(&format!("\\u{:04X}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

/// The shared temporary folder a codex work seat may not read or write.
const CODEX_WORK_NO_TMP: &str = "/private/tmp";

/// N-ws-codex-work: the folder half of a codex work seat's settings: the profile `seat` reads
/// `:minimal` and the [`CODEX_WORK_READ`] folders `exists` says are there, and writes only
/// `folder` and `folder/.git` (codex keeps `.git` read-only inside a writable folder unless it
/// is named). `folder` is the seat's own folder aibd made (never a page's path, never a tree
/// of another seat's repository: COUNCIL-worktree-gitdir Q3). Measured on 0.167.0 with this
/// profile: `ls ~/.ssh` refused, a write outside the folder and to `/tmp` refused, the network
/// refused, `git commit` in the folder made.
pub fn codex_work_args(folder: &str, exists: &dyn Fn(&str) -> bool) -> Vec<String> {
    let mut fs = vec![format!("{}={}", q(":minimal"), q("read"))];
    for d in CODEX_WORK_READ.iter().filter(|d| exists(d)) {
        fs.push(format!("{}={}", q(d), q("read")));
    }
    // 0eed7 VERIFY-codexwork-2f27b914 L1: `:minimal` lets a seat read and write `/private/tmp`
    // (shared by every account program); named `none` it is refused through `/tmp` too, and
    // git and node still run (measured, codex 0.167.0)
    fs.push(format!("{}={}", q(CODEX_WORK_NO_TMP), q("none")));
    let git = format!("{}/.git", folder.trim_end_matches('/'));
    fs.push(format!("{}={}", q(folder), q("write")));
    fs.push(format!("{}={}", q(&git), q("write")));
    vec![
        "-c".to_owned(),
        format!("permissions.seat.filesystem={{{}}}", fs.join(", ")),
    ]
}

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
    if !crate::SEAT_MODES.contains(&mode) {
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
            // W1 (DESIGN-seat-work-tier-a9136-1119; flags checked on claude 2.1.327 --help, and
            // --permission-prompt-tool parsed): --restricted keeps the file tools inside the
            // seat's folder and ignores settings files; --permission-prompts none refuses
            // whatever is not on the list (aibd adds it to --allowedTools, talk_args) instead
            // of asking anyone. Before the id flag: --tools takes the words after it.
            if mode == "work" {
                let at = a.iter().position(|w| w == id_flag).unwrap_or(a.len());
                let w = [
                    "--restricted",
                    "--tools",
                    crate::WORK_TOOLS,
                    "--permission-mode",
                    "dontAsk",
                    "--permission-prompts",
                    "none",
                    "--settings",
                ];
                a.splice(at..at, w.iter().map(|s| (*s).to_owned()));
                a.insert(
                    at + w.len(),
                    work_settings_for(crate::home_uid(), cfg!(unix))?,
                );
                // M1: no project / local settings (the seat can write in its folder)
                if let Some(i) = a.iter().position(|w| w == "--setting-sources") {
                    a[i + 1] = crate::WORK_SETTING_SOURCES.to_owned();
                }
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
            let by_mode = if mode == "work" {
                CODEX_WORK_OFF
            } else {
                CODEX_SAFE_OFF
            };
            for kv in CODEX_OFF.iter().chain(by_mode).chain(CODEX_NO_CODE) {
                a.push("-c".into());
                a.push((*kv).to_owned());
            }
            if let Some(m) = model {
                a.push("-c".into());
                a.push(format!("model=\"{m}\""));
            }
            Ok(a)
        }
        "copilot" => {
            if !pipe_modes("copilot").contains(&mode) {
                return Err(PipeRefused::ProviderMode("copilot".into(), mode.into()));
            }
            let a = vec![
                "copilot".to_owned(),
                "--output-format".to_owned(),
                "json".to_owned(),
                "--available-tools".to_owned(),
                "nonexistent_tool".to_owned(),
                "--disable-builtin-mcps".to_owned(),
                "--disallow-temp-dir".to_owned(),
                "--deny-url".to_owned(),
                "*".to_owned(),
                "--no-custom-instructions".to_owned(),
                "--model".to_owned(),
                // no model given: copilot's own pick, never the user's config default (on this
                // Mac a grok model copilot does not serve: every turn failed, EVIDENCE-brand-
                // copilot-08de5-1125)
                model.unwrap_or(COPILOT_ANY_MODEL).to_owned(),
            ];
            Ok(a)
        }
        "grok" => {
            if !pipe_modes("grok").contains(&mode) {
                return Err(PipeRefused::ProviderMode("grok".into(), mode.into()));
            }
            let grok_id_flag = if id_flag == RESUME_FLAG { "-r" } else { "-s" };
            let mut a = vec![
                "grok".to_owned(),
                "--output-format".to_owned(),
                "streaming-json".to_owned(),
                "--permission-mode".to_owned(),
                "plan".to_owned(),
                "--disable-web-search".to_owned(),
                "--no-subagents".to_owned(),
                "--sandbox".to_owned(),
                "readonly".to_owned(),
                "--disallowed-tools".to_owned(),
                GROK_SAFE_DISALLOWED.to_owned(),
                grok_id_flag.to_owned(),
                session_id.to_owned(),
            ];
            if let Some(m) = model {
                a.push("--model".into());
                a.push(m.into());
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
    // codex / grok / copilot: aibd hands the card over in the turn protocol, the prompt file or
    // the turn's stdin, so nothing goes in argv
    if provider == "codex" || provider == "grok" || provider == "copilot" {
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

/// A work seat's `--settings` for the account `uid`. 5d33a PREREAD-workguard-e54bf5f2 L1: on
/// unix an unknown uid would leave claude's temp folder readable, so the seat is not opened
/// (fail closed).
fn work_settings_for(uid: Option<u32>, unix: bool) -> Result<String, PipeRefused> {
    if uid.is_none() && unix {
        return Err(PipeRefused::ProviderMode("claude".into(), "work".into()));
    }
    Ok(crate::work_settings(uid))
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
        let mut g = vec!["grok".to_string()];
        assert!(add_persona_file("grok", &mut g, "/d/p.md"));
        assert_eq!(g, ["grok"], "reason: persona_in_grok_argv");
        let mut c = vec!["agy".to_string()];
        assert!(!add_persona_file("agy", &mut c, "/d/p.md"));
    }

    #[test]
    fn grok_safe_has_all_safety_flags_and_refuses_other_modes() {
        let a = pipe_argv("grok", "safe", Some("grok-4.6"), ID).unwrap();
        assert_eq!(
            words(&a),
            [
                "grok",
                "--output-format",
                "streaming-json",
                "--permission-mode",
                "plan",
                "--disable-web-search",
                "--no-subagents",
                "--sandbox",
                "readonly",
                "--disallowed-tools",
                GROK_SAFE_DISALLOWED,
                "-s",
                ID,
                "--model",
                "grok-4.6",
            ]
        );
        let r = resume_argv("grok", "safe", None, ID).unwrap();
        assert!(r.windows(2).any(|w| w == ["-r", ID]), "{r:?}");
        assert_eq!(
            pipe_argv("grok", "dgrs", None, ID),
            Err(PipeRefused::ProviderMode("grok".into(), "dgrs".into()))
        );
        assert_eq!(
            pipe_argv("grok", "work", None, ID),
            Err(PipeRefused::ProviderMode("grok".into(), "work".into()))
        );
    }

    /// 08de5 11-25 condition 1: every required flag, taken away alone, refuses the seat; so
    /// does each forbidden word added, a flag given twice, another value, and `--flag=value`.
    #[test]
    fn a_grok_safe_seat_missing_any_flag_is_refused() {
        let a = pipe_argv("grok", "safe", Some("grok-4.6"), ID).unwrap();
        assert_eq!(grok_safe_refusal(&a), None, "reason: built_argv_refused");
        let r = resume_argv("grok", "safe", None, ID).unwrap();
        assert_eq!(grok_safe_refusal(&r), None, "reason: resume_argv_refused");
        for (flag, value) in GROK_SAFE_REQUIRED {
            let i = a.iter().position(|w| w == flag).unwrap();
            let mut b = a.clone();
            b.drain(i..i + if value.is_some() { 2 } else { 1 });
            assert_eq!(
                grok_safe_refusal(&b),
                Some(format!("missing:{flag}")),
                "reason: dropped_flag_opened {flag}"
            );
            if let Some(v) = value {
                let mut c = a.clone();
                c[i + 1] = format!("{v}x");
                assert_eq!(
                    grok_safe_refusal(&c),
                    Some(format!("missing:{flag}")),
                    "{flag}"
                );
                let mut d = a.clone();
                d[i] = format!("{flag}={v}");
                d.remove(i + 1);
                assert_eq!(
                    grok_safe_refusal(&d),
                    Some(format!("missing:{flag}")),
                    "{flag}="
                );
            }
            let mut twice = a.clone();
            twice.push((*flag).to_owned());
            if let Some(v) = value {
                twice.push((*v).to_owned());
            }
            assert_eq!(
                grok_safe_refusal(&twice),
                Some(format!("missing:{flag}")),
                "reason: twice_opened {flag}"
            );
        }
        for bad in GROK_SAFE_FORBIDDEN {
            let mut b = a.clone();
            b.push(format!("{bad}=x"));
            assert_eq!(
                grok_safe_refusal(&b),
                Some(format!("forbidden:{bad}")),
                "reason: forbidden_opened {bad}"
            );
        }
    }

    /// Condition 1 for copilot: each required flag taken away, given another value or twice,
    /// and each forbidden word added (the prompt in argv among them) refuses the seat.
    #[test]
    fn a_copilot_safe_seat_missing_any_flag_is_refused() {
        let a = pipe_argv("copilot", "safe", Some("gpt-5"), ID).unwrap();
        assert_eq!(copilot_safe_refusal(&a), None, "reason: built_argv_refused");
        for (flag, value) in COPILOT_SAFE_REQUIRED {
            let i = a.iter().position(|w| w == flag).unwrap();
            let mut b = a.clone();
            b.drain(i..i + if value.is_some() { 2 } else { 1 });
            assert_eq!(
                copilot_safe_refusal(&b),
                Some(format!("missing:{flag}")),
                "reason: dropped_flag_opened {flag}"
            );
            let mut twice = a.clone();
            twice.push((*flag).to_owned());
            twice.extend(value.map(str::to_owned));
            assert_eq!(
                copilot_safe_refusal(&twice),
                Some(format!("missing:{flag}"))
            );
            if let Some(v) = value {
                let mut c = a.clone();
                c[i + 1] = format!("{v}x");
                assert_eq!(copilot_safe_refusal(&c), Some(format!("missing:{flag}")));
            }
        }
        for bad in COPILOT_SAFE_FORBIDDEN {
            let mut b = a.clone();
            b.push(format!("{bad}=x"));
            assert_eq!(
                copilot_safe_refusal(&b),
                Some(format!("forbidden:{bad}")),
                "reason: forbidden_opened {bad}"
            );
        }
    }

    #[test]
    fn only_the_read_copilot_version_starts() {
        assert!(copilot_version_ok(
            "GitHub Copilot CLI 1.0.116.\nRun 'copilot update'"
        ));
        assert!(copilot_version_ok("GitHub Copilot CLI 1.0.116"));
        for v in [
            "GitHub Copilot CLI 1.0.115.",
            "GitHub Copilot CLI 1.0.117.",
            "GitHub Copilot CLI 1.0.1160.",
            "GitHub Copilot CLI 1.0.116.1", // a four-part version, not an address; ip-literal-ok
            "GitHub Copilot CLI 1.0.103.",
            "",
        ] {
            assert!(!copilot_version_ok(v), "reason: unread_version_ok {v}");
        }
    }

    #[test]
    fn only_the_measured_grok_version_starts() {
        assert!(grok_version_ok("grok 1.0.70 (f50904eaee6c)\n"));
        assert!(grok_version_ok("grok 1.0.70"));
        for v in [
            "grok 1.0.71 (x)",
            "grok 1.0.700",
            "grok 1.0.7",
            "",
            "codex-cli 0.167.0",
        ] {
            assert!(!grok_version_ok(v), "reason: unmeasured_version_ok {v}");
        }
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
        for p in ["agy", "nope"] {
            assert_eq!(
                pipe_argv(p, "dgrs", None, ID),
                Err(PipeRefused::Provider(p.into())),
                "{p}"
            );
        }
        assert_eq!(PIPE_PROVIDERS, ["claude", "codex", "grok", "copilot"]);
    }

    #[test]
    fn copilot_safe_has_all_safety_flags_and_refuses_other_modes() {
        let a = pipe_argv("copilot", "safe", Some("gpt-5"), ID).unwrap();
        assert_eq!(
            words(&a),
            [
                "copilot",
                "--output-format",
                "json",
                "--available-tools",
                "nonexistent_tool",
                "--disable-builtin-mcps",
                "--disallow-temp-dir",
                "--deny-url",
                "*",
                "--no-custom-instructions",
                "--model",
                "gpt-5",
            ]
        );
        let none = pipe_argv("copilot", "safe", None, ID).unwrap();
        assert_eq!(
            &none[none.len() - 2..],
            ["--model", "auto"],
            "reason: user_default_model_used"
        );
        assert_eq!(
            pipe_argv("copilot", "dgrs", None, ID),
            Err(PipeRefused::ProviderMode("copilot".into(), "dgrs".into()))
        );
        assert_eq!(
            pipe_argv("copilot", "work", None, ID),
            Err(PipeRefused::ProviderMode("copilot".into(), "work".into()))
        );
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
            "features.code_mode=false",
        ] {
            assert!(set.contains(&kv), "reason: codex_flag_missing {kv} {a:?}");
        }
        // 0eed7 H1: the host runs every MCP tool call; turned off, a codex seat calls none
        assert!(
            !set.iter()
                .any(|kv| kv.starts_with("features.code_mode_host")),
            "reason: mcp_calls_blocked {a:?}"
        );
        assert_eq!(set.last(), Some(&"model=\"gpt-5.5\""));
        assert!(!a.iter().any(|w| w.contains("danger")), "{a:?}");
        assert_eq!(pipe_modes("codex"), CODEX_MODES);
        #[cfg(target_os = "macos")]
        assert_eq!(pipe_modes("codex"), ["safe", "work"]);
        #[cfg(not(target_os = "macos"))]
        assert_eq!(pipe_modes("codex"), ["safe"]);
        assert_eq!(pipe_modes("claude"), ["dgrs", "safe", "work"]);
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

    /// W1: a work seat gets a fixed tool list, confined and never asking; nothing that skips
    /// permissions, and not the safe seat's empty list.
    #[test]
    fn a_work_seat_has_its_listed_tools_confined_and_asks_nobody() {
        let a = pipe_argv("claude", "work", None, ID).unwrap();
        let after = |f: &str| {
            let i = a
                .iter()
                .position(|w| w == f)
                .unwrap_or_else(|| panic!("{f} {a:?}"));
            a[i + 1].clone()
        };
        assert!(
            a.iter().any(|w| w == "--restricted"),
            "reason: not_confined {a:?}"
        );
        assert_eq!(after("--tools"), "Read,Glob,Grep,Edit,Write,Bash");
        assert_eq!(
            after("--permission-prompts"),
            "none",
            "reason: asks_someone"
        );
        assert_eq!(after("--permission-mode"), "dontAsk");
        // condition 5: its commands are sandboxed and read nothing outside its folder
        let set = after("--settings");
        for (want, why) in [
            (r#""enabled":true"#, "not_sandboxed"),
            (r#""failIfUnavailable":true"#, "runs_unsandboxed"),
            (r#""allowUnsandboxedCommands":false"#, "unsandboxed_allowed"),
            (
                r#""blockReadsOutsideWorkingDirectories":true"#,
                "reads_outside",
            ),
            (r#""/var/folders""#, "temp_folders_readable"),
        ] {
            assert!(set.contains(want), "reason: {why} {set}");
        }
        // 5d33a PREREAD-workguard M / e54bf5f2 L2: the argv itself denies claude's temp folder
        if cfg!(unix) {
            assert!(
                set.contains(r#""/private/tmp/claude-"#),
                "reason: claude_temp_readable {set}"
            );
        }
        // 5d33a PREREAD-workguard M: claude's own temp folder (every session's outputs)
        let mine = crate::work_settings(Some(501));
        assert!(
            mine.contains(r#""denyRead":["/var/folders","/private/tmp/claude-501"]"#),
            "reason: claude_temp_readable {mine}"
        );
        assert_eq!(crate::work_settings(None), crate::WORK_SETTINGS);
        // L1: an unknown uid on unix opens nothing; off unix there is no such folder
        assert!(
            work_settings_for(None, true).is_err(),
            "reason: unknown_uid_opens"
        );
        assert_eq!(
            work_settings_for(None, false).unwrap(),
            crate::WORK_SETTINGS
        );
        // 5d33a DESIGNREVIEW M1: no settings file is loaded (a hook the seat wrote)
        assert_eq!(
            after("--setting-sources"),
            "",
            "reason: work_loads_project_settings"
        );
        assert!(
            !a.iter().any(|w| w.contains("dangerously")),
            "reason: skips_permissions {a:?}"
        );
        let tools = a.iter().position(|w| w == "--tools").unwrap();
        let id = a.iter().position(|w| w == "--session-id").unwrap();
        assert!(tools < id, "reason: tools_eat_the_id");
        assert_eq!(after("--session-id"), ID);
        assert!(
            crate::WORK_ALLOWED.iter().all(|t| !t.contains(',')),
            "reason: comma_splits_a_rule"
        );
        assert!(
            crate::WORK_ALLOWED
                .iter()
                .filter(|t| t.starts_with("Bash("))
                .all(|t| [
                    "Bash(git ",
                    "Bash(ls)",
                    "Bash(pwd)",
                    "Bash(node --test",
                    "Bash(node --check ",
                    "Bash(node apps/web/test/",
                    "Bash(rustfmt --check ",
                ]
                .iter()
                .any(|p| t.starts_with(p))),
            "reason: bash_rule_beyond_reading"
        );
        // 5d33a M2: only the listed rules take free arguments (no `ls *`; G4a's git reads are
        // listed, under the sandbox)
        assert!(
            crate::WORK_ALLOWED
                .iter()
                .filter(|t| t.ends_with("*)"))
                .all(|t| crate::WORK_FREE_ARGS.contains(t)),
            "reason: free_args_rule"
        );
        assert!(
            !crate::WORK_ALLOWED.iter().any(|t| t.contains("aib test")),
            "reason: aib_test_listed"
        );
        assert!(matches!(
            pipe_argv("claude", "wrok", None, ID),
            Err(PipeRefused::Mode)
        ));
    }

    /// N-ws-codex-work (FEASIBILITY-codex-work-seat-7ebca-1120 C): a codex work seat names
    /// the profile `seat` and no `sandbox_mode` (workspace-write read ~/.ssh and wrote /tmp),
    /// keeps the network, web search and code mode off, and its git reads no user config; the
    /// shell is not turned off (it is a work seat). Windows: not offered.
    #[test]
    fn a_codex_work_seat_runs_in_the_seat_profile() {
        let r = pipe_argv("codex", "work", None, ID);
        if !cfg!(target_os = "macos") {
            assert!(matches!(r, Err(PipeRefused::ProviderMode(..))), "{r:?}");
            return;
        }
        let a = r.unwrap();
        let set: Vec<&str> = a
            .windows(2)
            .filter(|w| w[0] == "-c")
            .map(|w| w[1].as_str())
            .collect();
        for kv in [
            "default_permissions=\"seat\"",
            "permissions.seat.network.enabled=false",
            "web_search=\"disabled\"",
            "features.code_mode=false",
            "project_doc_max_bytes=0",
            "approval_policy=\"never\"",
        ] {
            assert!(set.contains(&kv), "reason: work_key_missing {kv} {a:?}");
        }
        assert!(
            set.iter()
                .any(|kv| kv.starts_with("shell_environment_policy.set=")
                    && kv.contains("GIT_CONFIG_GLOBAL=\"/dev/null\"")
                    && kv.contains("GIT_CONFIG_NOSYSTEM=\"1\"")),
            "reason: git_reads_user_config {a:?}"
        );
        for k in [
            "sandbox_mode",
            "features.shell_tool",
            "features.unified_exec",
            "features.code_mode_host",
        ] {
            assert!(
                !set.iter().any(|kv| kv.starts_with(k)),
                "reason: work_has_safe_key {k} {a:?}"
            );
        }
        assert!(!a.iter().any(|w| w.contains("danger")), "{a:?}");
    }

    /// The folder half: `:minimal` and the tool folders that exist are read, the seat's folder
    /// and its `.git` written, nothing else; a missing tool folder is left out (codex's helper
    /// fails on one); a quote or backslash in the path stays inside the string.
    #[test]
    fn a_codex_work_seat_writes_only_its_folder() {
        let a = codex_work_args("/d/seat-work/seat_ab/", &|d| d == "/opt/homebrew");
        assert_eq!(a.len(), 2);
        assert_eq!(a[0], "-c");
        assert_eq!(
            a[1],
            "permissions.seat.filesystem={\":minimal\"=\"read\", \"/opt/homebrew\"=\"read\", \
             \"/private/tmp\"=\"none\", \"/d/seat-work/seat_ab/\"=\"write\", \
             \"/d/seat-work/seat_ab/.git\"=\"write\"}",
            "reason: profile_paths"
        );
        assert_eq!(
            a[1].matches("=\"write\"").count(),
            2,
            "reason: writes_beyond_folder"
        );
        let b = codex_work_args("/d/a\"b\\c", &|_| false);
        assert!(
            b[1].contains("\"/d/a\\\"b\\\\c\"=\"write\""),
            "reason: path_quote {}",
            b[1]
        );
        assert_eq!(q("x\ny"), "\"x\\u000Ay\"");
    }

    /// N-revive: picking a seat up again names the same conversation with --resume, keeps
    /// the mode's flags, and never also says --session-id (claude refuses an id used before).
    #[test]
    fn a_seat_picked_up_again_resumes_its_conversation() {
        let id = "0b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f10";
        for mode in ["safe", "dgrs", "work"] {
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
