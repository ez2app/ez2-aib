// SPDX-License-Identifier: Apache-2.0
//! `aib-seatview`:座艙窗切換席位視角(非艦長畫面,Andy 11-13:只留左邊對話 + 中間本席進度樹)。
//!
//! ```text
//! aib-seatview on  [--window S:cockpit]                         切到席位視角
//! aib-seatview off [--window S:cockpit] --want "hub right plan …"  切回完整版面(--want = 設定檔要的格,必帶)
//! aib-seatview status [--window S:cockpit]                      印 full / seat
//! aib-seatview filter-roles [--window S:cockpit] [--state-dir D] < roles
//!                                                               設定檔的格(一行一個)加中欄、照窗的版面收窄
//! aib-seatview plan on|off|status [--state-dir D]               中欄(plan + hosts)開關;印 on / off
//! ```
//! - 中欄開關(519d0 11-14,取代 1ced7 在 panes.py 加的 plan key):狀態夾裡的 `seatview-plan`,
//!   沒檔 = on。狀態夾 = `--state-dir` → `$EZ2_COCKPIT_STATE_DIR` → 外框 session 的 `@aib_state_dir`。
//!   `filter-roles` 讀不到開關 / 窗 ⇒ 當開著、完整版面(不讓開艙中止)。
//! - 窗預設 `$EZ2_TMUX_SESSION:cockpit`。只碰外框 tmux,不碰 hub 伺服器。
//! - `off` 補回 left / bottom / right(設定要、現在沒有的);右欄指令照 cockpit-resize.sh:
//!   `<狀態夾>/run-right-tabs.sh` 可執行就用它,不然 `env -u EZ2_ONESHOT -u EZ2_RIGHT_VIEW bash $EZ2_COCKPIT_DIR/pane-right-tabs.sh`;
//!   左 / 下欄 `bash $EZ2_COCKPIT_DIR/pane-left-tabs.sh` / `pane-bottom-tabs.sh`;
//!   主機群與待審框由呼叫端接著跑 `cockpit-full.sh --ensure` 照原本的格腳本補。
//! - 結束碼:0 好;2 用法錯;3 座艙窗讀不到 / 沒有對話格 / 找不到狀態夾 / 開關檔內容壞(什麼都沒動);
//!   4 tmux 指令中途失敗 / 開關寫不進去。

use aib_plan::seatview::{self, RealTmux, Tmux};
use std::io::Read;
use std::path::Path;

const USAGE: &str = "usage: aib-seatview on|off|status|filter-roles [--window SESSION:cockpit] | plan on|off|status [--state-dir DIR]";

struct Opts {
    cmd: String,
    window: Option<String>,
    want: Option<Vec<String>>,
    /// `plan` 的 on / off / status
    plan: Option<String>,
    state_dir: Option<String>,
}

fn parse(args: &[String]) -> Result<Opts, String> {
    let mut it = args.iter();
    let cmd = it.next().ok_or(USAGE)?.clone();
    if !["on", "off", "status", "filter-roles", "plan"].contains(&cmd.as_str()) {
        return Err(USAGE.into());
    }
    let mut o = Opts {
        cmd,
        window: None,
        want: None,
        plan: None,
        state_dir: None,
    };
    if o.cmd == "plan" {
        let op = it.next().ok_or(USAGE)?;
        if !["on", "off", "status"].contains(&op.as_str()) {
            return Err(USAGE.into());
        }
        o.plan = Some(op.clone());
    }
    while let Some(k) = it.next() {
        match k.as_str() {
            "--window" => {
                let v = it
                    .next()
                    .filter(|v| !v.is_empty() && !v.starts_with("--"))
                    .ok_or("missing_value: --window")?;
                o.window = Some(v.clone());
            }
            "--want" if o.cmd == "off" => {
                let v = it.next().ok_or("missing_value: --want")?;
                let w: Vec<String> = v.split_whitespace().map(str::to_string).collect();
                // 空的 = 呼叫端讀不到設定:不猜(猜成「不要右欄」會讓之後的 ensure 卡在自癒拒絕,審查 L4)
                if w.is_empty() {
                    return Err("missing_value: --want".into());
                }
                o.want = Some(w);
            }
            "--state-dir" if o.cmd == "plan" || o.cmd == "filter-roles" => {
                let v = it
                    .next()
                    .filter(|v| !v.is_empty() && !v.starts_with("--"))
                    .ok_or("missing_value: --state-dir")?;
                o.state_dir = Some(v.clone());
            }
            _ => return Err(format!("unknown_flag: {k}\n{USAGE}")),
        }
    }
    if o.cmd == "off" && o.want.is_none() {
        return Err(format!("missing_flag: --want\n{USAGE}"));
    }
    Ok(o)
}

fn window(o: &Opts) -> Result<String, String> {
    if let Some(w) = &o.window {
        return Ok(w.clone());
    }
    std::env::var("EZ2_TMUX_SESSION")
        .ok()
        .filter(|s| !s.is_empty())
        .map(|s| format!("{s}:cockpit"))
        .ok_or_else(|| "no_window: 沒有 EZ2_TMUX_SESSION,請帶 --window".into())
}

/// 外欄格的指令:右欄同 cockpit-resize.sh 的 _heal_right_pane;左 / 下欄跑各自的格腳本
fn pane_cmd(t: &dyn Tmux, win: &str, role: &str) -> String {
    let dir = std::env::var("EZ2_COCKPIT_DIR").unwrap_or_default();
    if role != "right" {
        let script = if role == "left" {
            "pane-left-tabs.sh"
        } else {
            "pane-bottom-tabs.sh"
        };
        return format!(
            "bash {}",
            sh_quote(&Path::new(&dir).join(script).to_string_lossy())
        );
    }
    let session = win.split(':').next().unwrap_or(win);
    let state = std::env::var("EZ2_COCKPIT_STATE_DIR")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| {
            t.run(&["show-option", "-qv", "-t", session, "@aib_state_dir"])
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        });
    if let Some(run) = state.map(|s| Path::new(&s).join("run-right-tabs.sh")) {
        if is_exec(&run) {
            return format!("bash {}", sh_quote(&run.to_string_lossy()));
        }
    }
    format!(
        "env -u EZ2_ONESHOT -u EZ2_RIGHT_VIEW bash {}",
        sh_quote(&Path::new(&dir).join("pane-right-tabs.sh").to_string_lossy())
    )
}

#[cfg(unix)]
fn is_exec(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn is_exec(p: &Path) -> bool {
    p.is_file()
}

/// 單引號包起來給 tmux 的 shell(路徑裡有空白也安全)
fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// 座艙狀態夾:--state-dir → $EZ2_COCKPIT_STATE_DIR → 外框 session 的 @aib_state_dir;都沒有 ⇒ None
fn state_dir(o: &Opts, t: &dyn Tmux) -> Option<std::path::PathBuf> {
    let env = || {
        std::env::var("EZ2_COCKPIT_STATE_DIR")
            .ok()
            .filter(|s| !s.is_empty())
    };
    let tmux = || {
        let win = window(o).ok()?;
        let session = win.split(':').next().unwrap_or(&win).to_string();
        t.run(&["show-option", "-qv", "-t", &session, "@aib_state_dir"])
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    };
    o.state_dir
        .clone()
        .or_else(env)
        .or_else(tmux)
        .map(std::path::PathBuf::from)
}

fn run(o: &Opts, t: &dyn Tmux) -> Result<String, (i32, String)> {
    if let Some(op) = &o.plan {
        let dir = state_dir(o, t).ok_or((3, "no_state_dir".to_string()))?;
        if op != "status" {
            seatview::plan_write(&dir, op == "on")
                .map_err(|e| (4, format!("write_failed: {e}")))?;
        }
        let on = seatview::plan_read(&dir).map_err(|e| (3, e))?;
        return Ok((if on { "on" } else { "off" }).to_string());
    }
    if o.cmd == "filter-roles" {
        let mut roles = String::new();
        std::io::stdin()
            .read_to_string(&mut roles)
            .map_err(|e| (2, format!("stdin: {e}")))?;
        // 讀不到窗 = 完整版面;讀不到開關 = 開著(--print-plan 時外框還不在,不讓開艙中止)
        let layout = window(o)
            .map(|w| seatview::layout(t, &w))
            .unwrap_or(seatview::Layout::Full);
        // 檔在但讀不到 / 內容壞 ⇒ 照開著，但 stderr 說一聲(stdout 不變;043dd svplan L1)
        let on = match state_dir(o, t).map(|d| seatview::plan_read(&d)) {
            Some(Err(e)) => {
                eprintln!("aib-seatview filter-roles: 中欄開關讀不到({e}),照開著處理");
                true
            }
            Some(Ok(on)) => on,
            None => true,
        };
        return Ok(seatview::roles_for(&roles, layout, on).join("\n"));
    }
    let win = window(o).map_err(|e| (2, e))?;
    let fail = |e: String| {
        let rc = if e == "cockpit_unreadable" || e == "no_hub_pane" {
            3
        } else {
            4
        };
        (rc, e)
    };
    match o.cmd.as_str() {
        "status" => Ok(seatview::layout(t, &win).name().to_string()),
        "on" => {
            let r = seatview::seat_on(t, &win).map_err(fail)?;
            Ok(format!(
                "seat: 收掉 {} 格{}",
                r.killed.len(),
                if r.respawned_plan {
                    ",專案項目重開"
                } else {
                    ""
                }
            ))
        }
        _ => {
            let want = o.want.clone().unwrap_or_default();
            let cmd = |role: &str| pane_cmd(t, &win, role);
            let r = seatview::seat_off(t, &win, &want, &cmd).map_err(fail)?;
            let added: Vec<&str> = r.added.iter().map(|a| a.0.as_str()).collect();
            Ok(format!(
                "full: 補回 {}{}",
                if added.is_empty() {
                    "無".to_string()
                } else {
                    added.join(" ")
                },
                if r.respawned_plan {
                    ",專案項目重開"
                } else {
                    ""
                }
            ))
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let o = match parse(&args) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    match run(&o, &RealTmux) {
        Ok(out) => {
            if !out.is_empty() {
                println!("{out}");
            }
        }
        Err((rc, e)) => {
            eprintln!("aib-seatview: {e}");
            std::process::exit(rc);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn parse_args() {
        assert!(parse(&a(&[])).is_err());
        assert!(parse(&a(&["toggle"])).is_err());
        assert!(parse(&a(&["on", "--window"])).is_err());
        assert!(parse(&a(&["on", "--window", "--want"])).is_err());
        assert!(
            parse(&a(&["on", "--want", "hub"])).is_err(),
            "--want only for off"
        );
        assert!(parse(&a(&["off"])).is_err(), "off needs --want");
        assert!(
            parse(&a(&["off", "--want", " "])).is_err(),
            "empty --want is not a guess"
        );
        let o = parse(&a(&[
            "off",
            "--window",
            "S:cockpit",
            "--want",
            "hub right plan",
        ]))
        .expect("ok");
        assert_eq!(o.window.as_deref(), Some("S:cockpit"));
        assert_eq!(
            o.want,
            Some(vec!["hub".into(), "right".into(), "plan".into()])
        );
    }

    #[test]
    fn parse_plan_switch() {
        assert!(parse(&a(&["plan"])).is_err(), "plan needs on|off|status");
        assert!(parse(&a(&["plan", "toggle"])).is_err());
        assert!(parse(&a(&["plan", "on", "--state-dir"])).is_err());
        assert!(
            parse(&a(&["on", "--state-dir", "/x"])).is_err(),
            "--state-dir only for plan / filter-roles"
        );
        let o = parse(&a(&["plan", "off", "--state-dir", "/x"])).expect("ok");
        assert_eq!(
            (o.plan.as_deref(), o.state_dir.as_deref()),
            (Some("off"), Some("/x"))
        );
    }

    struct NoTmux;
    impl Tmux for NoTmux {
        fn run(&self, _: &[&str]) -> Option<String> {
            None
        }
    }

    #[test]
    fn plan_writes_and_reads_its_own_file() {
        let d = std::env::temp_dir().join(format!("aib-sv-bin-{}", std::process::id()));
        std::fs::create_dir_all(&d).expect("dir");
        let dir = d.to_string_lossy().to_string();
        let go = |op: &str| {
            run(
                &parse(&a(&["plan", op, "--state-dir", &dir])).expect("ok"),
                &NoTmux,
            )
        };
        assert_eq!(go("status"), Ok("on".into()), "no file = on");
        assert_eq!(go("off"), Ok("off".into()));
        assert_eq!(go("status"), Ok("off".into()));
        assert_eq!(go("on"), Ok("on".into()));
        std::fs::write(d.join(seatview::PLAN_FILE), "x").expect("write");
        assert_eq!(go("status"), Err((3, "bad_state".into())));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn quote_survives_spaces_and_quotes() {
        assert_eq!(sh_quote("/a b/c"), "'/a b/c'");
        assert_eq!(sh_quote("it's"), "'it'\\''s'");
    }

    struct Fixed;
    impl Tmux for Fixed {
        fn run(&self, args: &[&str]) -> Option<String> {
            (args[0] == "show-option").then(|| "seat\n".to_string())
        }
    }

    #[test]
    fn status_reads_the_window_option() {
        let o = parse(&a(&["status", "--window", "S:cockpit"])).expect("ok");
        assert_eq!(run(&o, &Fixed).expect("ok"), "seat");
        assert_eq!(seatview::Layout::parse(""), seatview::Layout::Full);
    }
}
