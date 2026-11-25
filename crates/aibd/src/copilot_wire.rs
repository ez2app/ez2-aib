//! AP30 (`copilot` 1.0.103): aibd speaking to `copilot` turn-by-turn over `--output-format json`
//! (`--resume=<sessionId>` on turn 2+ using the `sessionId` returned in turn 1's `result`).
//!
//! Translates `copilot` stdout JSON lines (`assistant.message`, `result`) into Claude-compatible
//! `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`, `session_output`, and the web
//! UI read them uniformly. One program per turn through [`crate::turn_wire`]; the persona card
//! and the user's words go on the turn's stdin, never in argv (08de5 11-25, ebab8 q_cd554b24
//! condition 4: `-p <prompt>` put them where `ps` shows them). copilot 1.0.115 takes a piped
//! stdin as the prompt: measured signed in on a Mac with the seat's words, its tools all off
//! and every MCP server disabled (EVIDENCE-brand-copilot-08de5-1125); Windows UNVERIFIED.

use crate::turn_wire::{Brand, PromptBy};
use serde_json::{json, Value};
use std::path::Path;

/// Whether `prog` is the `copilot` binary (`copilot` or `/…/copilot` or `copilot.exe`).
pub fn is_copilot(prog: &str) -> bool {
    let base = prog.rsplit(['/', '\\']).next().unwrap_or(prog);
    let stem = base
        .strip_suffix(".exe")
        .or_else(|| base.strip_suffix(".EXE"))
        .unwrap_or(base);
    stem.eq_ignore_ascii_case("copilot")
}

/// The words that turn off every MCP server `copilot mcp list --json` lists (each
/// `--disable-mcp-server <name>`): the user's own (`~/.copilot/mcp-config.json`, a folder's
/// `.mcp.json`), a plugin's, and the built-in ones `--disable-builtin-mcps` does not reach
/// (1.0.115 lists `computer-use` from its built-in plugin beside `github-mcp-server`). A
/// listing that is not read, or a name that is not plain, starts nothing (`Err`).
pub fn mcp_off(listed: &str) -> Result<Vec<String>, String> {
    let v: Value = serde_json::from_str(listed.trim())
        .map_err(|_| "its MCP list could not be read".to_owned())?;
    let servers = v
        .get("mcpServers")
        .and_then(Value::as_object)
        .ok_or("its MCP list has no mcpServers")?;
    let mut out = Vec::new();
    for name in servers.keys() {
        // a name starting with `-` would be read as a flag after --disable-mcp-server
        let plain = !name.is_empty()
            && !name.starts_with('-')
            && name.len() <= 64
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b));
        if !plain {
            return Err("an MCP server's name is not plain".into());
        }
        out.push("--disable-mcp-server".to_owned());
        out.push(name.clone());
    }
    Ok(out)
}

pub use crate::turn_wire::{turn_prompt, user_text};

/// Build `copilot` args for one turn: the seat's words, and `--resume=<sid>` once turn 1
/// gave its `sessionId`. No prompt: it goes on stdin.
pub fn turn_args(base_argv: &[String], session_id: Option<&str>) -> Vec<String> {
    let mut out: Vec<String> = base_argv.iter().skip(1).cloned().collect();
    if let Some(sid) = session_id {
        out.push(format!("--resume={sid}"));
    }
    out
}

/// Translate one `copilot --output-format json` line into zero or one Claude-compatible
/// `stream-json` JSON line (without trailing newline). Captures `sessionId` from `result`.
pub fn translate_line(raw: &str, session_id_out: &mut Option<String>) -> Option<String> {
    let v: Value = serde_json::from_str(raw.trim()).ok()?;
    match v.get("type").and_then(Value::as_str)? {
        "assistant.message" => {
            let content = v
                .get("data")
                .and_then(|d| d.get("content"))
                .and_then(Value::as_str)
                .unwrap_or("");
            if content.is_empty() {
                return None;
            }
            Some(
                json!({
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}]
                    }
                })
                .to_string(),
            )
        }
        "result" => {
            // the id goes into the next turn's words: a UUID only (3e53b REVIEW-copilot L1)
            let sid = v.get("sessionId").and_then(Value::as_str);
            let sid_bad = sid.is_some_and(|s| !uuid_shaped(s));
            if let Some(s) = sid.filter(|s| uuid_shaped(s)) {
                *session_id_out = Some(s.to_owned());
            }
            let exit_code = v.get("exitCode").and_then(Value::as_i64).unwrap_or(1);
            let is_err = exit_code != 0 || sid_bad;
            let usage = v.get("usage").cloned().unwrap_or_else(|| json!({}));
            let mut out = json!({
                "type": "result",
                "subtype": if is_err { "error" } else { "success" },
                "is_error": is_err,
                "result": if sid_bad { "copilot's session id is not a UUID (copilot_sid)" } else { "" },
                "usage": usage,
            });
            if let Some(sid) = session_id_out.as_deref() {
                out["session_id"] = json!(sid);
            }
            Some(out.to_string())
        }
        _ => None,
    }
}

/// `8-4-4-4-12` hex digits: the session id shape copilot 1.0.115 prints.
pub fn uuid_shaped(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    parts.len() == 5
        && parts
            .iter()
            .zip([8, 4, 4, 4, 12])
            .all(|(p, n)| p.len() == n && p.bytes().all(|b| b.is_ascii_hexdigit()))
}

/// What copilot runs by itself, outside the seat's tools (3e53b REVIEW-copilot M1, measured
/// 08de5 11-25 on 1.0.115 with the safe seat's words): hooks under `<COPILOT_HOME>/hooks`
/// ran (sessionStart, userPromptSubmitted, sessionEnd), and `disableAllHooks` in the user
/// settings did not stop them; a folder in `trustedFolders` also runs its `.github/hooks` and
/// `.github/copilot/settings.json` hooks. A seat opens only with none of it: no hook file, no
/// `hooks` in settings or config, no installed or enabled plugin, the seat's folder (or one
/// above it) not trusted, `COPILOT_ALLOW_ALL` not on; a file not read refuses too.
pub fn nothing_runs(
    env: &[(std::ffi::OsString, std::ffi::OsString)],
    cwd: &Path,
) -> Result<(), String> {
    let var = |k: &str| {
        env.iter()
            .find(|(n, _)| n == k)
            .map(|(_, v)| v.to_string_lossy().into_owned())
    };
    if var("COPILOT_ALLOW_ALL").is_some_and(|v| {
        let v = v.trim().to_ascii_lowercase();
        !(v.is_empty() || ["false", "0", "no", "off", "n"].contains(&v.as_str()))
    }) {
        return Err("COPILOT_ALLOW_ALL is on".to_owned());
    }
    let home = match (var("COPILOT_HOME"), var("HOME")) {
        (Some(c), _) if !c.is_empty() => std::path::PathBuf::from(c),
        (_, Some(h)) if !h.is_empty() => Path::new(&h).join(".copilot"),
        _ => return Err("copilot's home folder is not known".to_owned()),
    };
    for dir in ["hooks", "installed-plugins"] {
        match std::fs::read_dir(home.join(dir)) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(format!("{dir}/ not read")),
            Ok(mut rd) => {
                if let Some(e) = rd.next() {
                    let name: String = e
                        .map(|e| e.file_name().to_string_lossy().into_owned())
                        .unwrap_or_default()
                        .chars()
                        .filter(|c| !c.is_control())
                        .take(40)
                        .collect();
                    return Err(format!("{dir}/{name} is set up: it runs outside the seat"));
                }
            }
        }
    }
    let read = |name: &str, need: bool| -> Result<Option<Value>, String> {
        match std::fs::read_to_string(home.join(name)) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && !need => Ok(None),
            Err(_) => Err(format!("{name} not read")),
            Ok(s) => {
                let body: Vec<&str> = s
                    .lines()
                    .filter(|l| !l.trim_start().starts_with("//"))
                    .collect();
                serde_json::from_str::<Value>(&body.join("\n"))
                    .map(Some)
                    .map_err(|_| format!("{name} not read"))
            }
        }
    };
    let some = |v: Option<&Value>| match v {
        None | Some(Value::Null) => false,
        Some(Value::Object(o)) => !o.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(_) => true,
    };
    for (name, need) in [("settings.json", false), ("config.json", true)] {
        let Some(v) = read(name, need)? else { continue };
        if some(v.get("hooks")) {
            return Err(format!("{name} has hooks: they run outside the seat"));
        }
        if v.get("enabledPlugins")
            .and_then(Value::as_object)
            .is_some_and(|o| o.values().any(|x| x != &Value::Bool(false)))
        {
            return Err(format!("{name} turns a plugin on"));
        }
        if let Some(t) = v.get("trustedFolders") {
            let Some(list) = t.as_array() else {
                return Err(format!("{name} trustedFolders not read"));
            };
            for f in list {
                let Some(f) = f.as_str() else {
                    return Err(format!("{name} trustedFolders not read"));
                };
                if cwd.starts_with(f) {
                    return Err(
                        "the seat's folder is trusted by copilot: its repository hooks run"
                            .to_owned(),
                    );
                }
            }
        }
    }
    Ok(())
}

/// copilot as a [`Brand`] of [`crate::turn_wire`]: the prompt on stdin, `--resume` with
/// the session id its first `result` said.
#[derive(Default)]
struct Copilot {
    session_id: Option<String>,
}

impl Brand for Copilot {
    fn name(&self) -> &'static str {
        "copilot"
    }
    fn prompt_by(&self) -> PromptBy {
        PromptBy::Stdin // the prompt never in argv (condition 4)
    }
    fn turn_args(&mut self, base: &[String], _turn: usize, _file: Option<&Path>) -> Vec<String> {
        turn_args(base, self.session_id.as_deref())
    }
    fn lines(&mut self, raw: &str) -> Vec<String> {
        translate_line(raw, &mut self.session_id)
            .into_iter()
            .collect()
    }
    fn finish(&mut self) -> Vec<String> {
        Vec::new()
    }
}

/// copilot's words and output for [`crate::turn_wire::spawn`].
pub fn brand() -> Box<dyn Brand> {
    Box::new(Copilot::default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copilot_binary_name_matches_plain_and_path() {
        assert!(is_copilot("copilot"));
        assert!(is_copilot("/opt/homebrew/bin/copilot"));
        assert!(is_copilot(r"C:\bin\copilot.exe"));
        assert!(!is_copilot("claude"));
    }

    #[test]
    fn translates_assistant_message_and_result_with_session_id() {
        let mut sid = None;
        let a = translate_line(
            r#"{"type":"assistant.message","data":{"content":"PONG"}}"#,
            &mut sid,
        )
        .unwrap();
        let va: Value = serde_json::from_str(&a).unwrap();
        assert_eq!(va["type"], "assistant");
        assert_eq!(va["message"]["content"][0]["text"], "PONG");
        let r = translate_line(
            r#"{"type":"result","sessionId":"0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0","exitCode":0,"usage":{"premiumRequests":1}}"#,
            &mut sid,
        )
        .unwrap();
        let vr: Value = serde_json::from_str(&r).unwrap();
        assert_eq!(vr["type"], "result");
        assert_eq!(vr["is_error"], false);
        assert_eq!(vr["session_id"], "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0");
        assert_eq!(sid.as_deref(), Some("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"));
    }

    /// 3e53b REVIEW-copilot L1: the id that goes into the next turn's words is a UUID only.
    #[test]
    fn a_session_id_that_is_not_a_uuid_is_not_kept() {
        let mut sid = None;
        for bad in [
            "--allow-all",
            "f8781f79",
            "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1fz",
            "",
        ] {
            let line = json!({"type": "result", "sessionId": bad, "exitCode": 0}).to_string();
            let r: Value = serde_json::from_str(&translate_line(&line, &mut sid).unwrap()).unwrap();
            assert_eq!(sid, None, "reason: sid_kept {bad}");
            assert_eq!(r["is_error"], true, "reason: sid_not_said {bad}");
        }
        assert!(uuid_shaped("0F1E2D3C-4b5a-6978-8796-a5b4c3d2e1f0"));
        assert!(!uuid_shaped("0f1e2d3c4b5a-6978-8796-a5b4c3d2e1f0-"));
    }

    /// 3e53b REVIEW-copilot M1 (measured: user hooks run on a safe seat; a trusted folder's
    /// repository hooks too): anything copilot runs by itself refuses the seat.
    #[test]
    fn a_copilot_set_up_that_runs_anything_by_itself_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let home = d.path().join("h");
        let cp = home.join(".copilot");
        let cwd = d.path().join("repo");
        std::fs::create_dir_all(&cp).unwrap();
        std::fs::create_dir_all(&cwd).unwrap();
        let env = |extra: &[(&str, &str)]| {
            let mut e: Vec<(std::ffi::OsString, std::ffi::OsString)> =
                vec![("HOME".into(), home.clone().into())];
            e.extend(extra.iter().map(|(k, v)| ((*k).into(), (*v).into())));
            e
        };
        let cfg = |body: &str| {
            std::fs::write(cp.join("config.json"), format!("// managed\n{body}")).unwrap();
        };
        cfg(r#"{"loggedInUsers":[{"login":"u"}],"trustedFolders":["/elsewhere"]}"#);
        assert_eq!(nothing_runs(&env(&[]), &cwd), Ok(()));
        // a hook file
        std::fs::create_dir(cp.join("hooks")).unwrap();
        std::fs::write(cp.join("hooks").join("m.json"), "{}").unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: hook_file_opens"
        );
        std::fs::remove_file(cp.join("hooks").join("m.json")).unwrap();
        assert_eq!(nothing_runs(&env(&[]), &cwd), Ok(()));
        // an installed plugin
        std::fs::create_dir_all(cp.join("installed-plugins").join("p")).unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: plugin_opens"
        );
        std::fs::remove_dir(cp.join("installed-plugins").join("p")).unwrap();
        // hooks / a plugin turned on in the settings
        std::fs::write(
            cp.join("settings.json"),
            r#"{"hooks":{"sessionStart":[{}]}}"#,
        )
        .unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: settings_hooks_open"
        );
        std::fs::write(
            cp.join("settings.json"),
            r#"{"enabledPlugins":{"a@b":true}}"#,
        )
        .unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: enabled_plugin_opens"
        );
        std::fs::write(
            cp.join("settings.json"),
            r#"{"enabledPlugins":{"a@b":false}}"#,
        )
        .unwrap();
        assert_eq!(nothing_runs(&env(&[]), &cwd), Ok(()));
        std::fs::write(cp.join("settings.json"), "not json").unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: unread_settings_opens"
        );
        std::fs::remove_file(cp.join("settings.json")).unwrap();
        // the seat's folder or one above it trusted
        cfg(&format!(
            r#"{{"trustedFolders":[{:?}]}}"#,
            d.path().to_string_lossy()
        ));
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: trusted_folder_opens"
        );
        cfg(r#"{"trustedFolders":"x"}"#);
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: unread_trust_opens"
        );
        cfg(r#"{"hooks":{"x":1}}"#);
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: config_hooks_open"
        );
        // no config read; COPILOT_ALLOW_ALL on; COPILOT_HOME followed
        std::fs::remove_file(cp.join("config.json")).unwrap();
        assert!(
            nothing_runs(&env(&[]), &cwd).is_err(),
            "reason: unread_config_opens"
        );
        cfg("{}");
        assert!(
            nothing_runs(&env(&[("COPILOT_ALLOW_ALL", "yes")]), &cwd).is_err(),
            "reason: allow_all_opens"
        );
        assert_eq!(
            nothing_runs(&env(&[("COPILOT_ALLOW_ALL", "off")]), &cwd),
            Ok(())
        );
        let other = d.path().join("other");
        std::fs::create_dir_all(other.join("hooks")).unwrap();
        std::fs::write(other.join("config.json"), "{}").unwrap();
        std::fs::write(other.join("hooks").join("m.json"), "{}").unwrap();
        assert!(
            nothing_runs(&env(&[("COPILOT_HOME", other.to_str().unwrap())]), &cwd).is_err(),
            "reason: copilot_home_not_followed"
        );
    }

    #[test]
    fn turn_args_adds_resume_only_when_session_id_is_present() {
        let base = vec![
            "copilot".to_owned(),
            "--output-format".to_owned(),
            "json".to_owned(),
        ];
        let t0 = turn_args(&base, None);
        assert_eq!(t0, ["--output-format", "json"]);
        let t1 = turn_args(&base, Some("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"));
        assert_eq!(
            t1,
            [
                "--output-format",
                "json",
                "--resume=0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
            ]
        );
    }

    /// 1.0.115 `mcp list --json` here (trimmed): each server turned off by name; an unread
    /// list or a name that is not plain starts nothing.
    #[test]
    fn every_listed_mcp_server_is_turned_off() {
        let listed = r#"{"mcpServers":{"computer-use":{"source":"builtin"},"github-mcp-server":{"source":"builtin"}}}"#;
        assert_eq!(
            mcp_off(listed).unwrap(),
            [
                "--disable-mcp-server",
                "computer-use",
                "--disable-mcp-server",
                "github-mcp-server"
            ]
        );
        assert_eq!(
            mcp_off(r#"{"mcpServers":{}}"#).unwrap(),
            Vec::<String>::new()
        );
        for bad in [
            "",
            "nope",
            "{}",
            r#"{"mcpServers":{"a b":{}}}"#,
            r#"{"mcpServers":{"--allow-all":{}}}"#,
        ] {
            assert!(mcp_off(bad).is_err(), "reason: bad_list_started {bad}");
        }
    }

    /// Condition 4: copilot takes its prompt on stdin, and its words carry no prompt.
    #[test]
    fn the_copilot_prompt_goes_on_stdin_never_in_its_words() {
        let mut b = brand();
        assert_eq!(
            b.prompt_by(),
            PromptBy::Stdin,
            "reason: prompt_not_on_stdin"
        );
        let base = [
            "copilot".to_owned(),
            "--output-format".to_owned(),
            "json".to_owned(),
        ];
        for turn in [0, 1] {
            let a = b.turn_args(&base, turn, None);
            assert!(
                !a.iter().any(|w| w == "-p" || w == "--prompt" || w == "-i"),
                "reason: prompt_flag_in_words {a:?}"
            );
        }
    }
}
