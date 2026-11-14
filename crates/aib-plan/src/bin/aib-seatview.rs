// SPDX-License-Identifier: Apache-2.0
//! `aib-seatview`:座艙窗切換席位視角(非艦長畫面,Andy 11-13:只留左邊對話 + 中間本席進度樹)。
//!
//! ```text
//! aib-seatview on  [--window S:cockpit]                         切到席位視角
//! aib-seatview off [--window S:cockpit] --want "hub right plan …"  切回完整版面(--want = 設定檔要的格,必帶)
//! aib-seatview status [--window S:cockpit]                      印 full / seat
//! aib-seatview filter-roles [--window S:cockpit] < roles        設定檔的格(一行一個)照窗的版面收窄
//! ```
//! - 窗預設 `$EZ2_TMUX_SESSION:cockpit`。只碰外框 tmux,不碰 hub 伺服器。
//! - `off` 補回 left / bottom / right(設定要、現在沒有的);右欄指令照 cockpit-resize.sh:
//!   `<狀態夾>/run-right-tabs.sh` 可執行就用它,不然 `env -u EZ2_ONESHOT -u EZ2_RIGHT_VIEW bash $EZ2_COCKPIT_DIR/pane-right-tabs.sh`;
//!   左 / 下欄 `bash $EZ2_COCKPIT_DIR/pane-left-tabs.sh` / `pane-bottom-tabs.sh`;
//!   主機群與待審框由呼叫端接著跑 `cockpit-full.sh --ensure` 照原本的格腳本補。
//! - 結束碼:0 好;2 用法錯(含 off 時沒有 EZ2_COCKPIT_DIR);3 座艙窗讀不到 / 沒有對話格(什麼都沒動);4 tmux 指令中途失敗。

use aib_plan::seatview::{self, RealTmux, Tmux};
use std::io::Read;
use std::path::Path;

const USAGE: &str = "usage: aib-seatview on|status|filter-roles [--window SESSION:cockpit]\n       aib-seatview off --want \"hub right plan …\" [--window SESSION:cockpit]";

struct Opts {
    cmd: String,
    window: Option<String>,
    want: Option<Vec<String>>,
}

fn parse(args: &[String]) -> Result<Opts, String> {
    let mut it = args.iter();
    let cmd = it.next().ok_or(USAGE)?.clone();
    if !["on", "off", "status", "filter-roles"].contains(&cmd.as_str()) {
        return Err(USAGE.into());
    }
    let mut o = Opts {
        cmd,
        window: None,
        want: None,
    };
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

/// `off` 要的格腳本資料夾:空的 ⇒ 結束碼 2(用法 / 環境錯),什麼都不動
fn cockpit_dir(v: Option<String>) -> Result<String, (i32, String)> {
    v.filter(|s| !s.trim().is_empty()).ok_or_else(|| {
        (
            2,
            "no_cockpit_dir: off 需要 EZ2_COCKPIT_DIR(格腳本所在),什麼都沒動".to_string(),
        )
    })
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

fn run(o: &Opts, t: &dyn Tmux) -> Result<String, (i32, String)> {
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
        "filter-roles" => {
            let mut roles = String::new();
            std::io::stdin()
                .read_to_string(&mut roles)
                .map_err(|e| (2, format!("stdin: {e}")))?;
            Ok(seatview::filter_roles(&roles, seatview::layout(t, &win)).join("\n"))
        }
        "on" => {
            let r = seatview::seat_on(t, &win).map_err(fail)?;
            Ok(format!(
                "seat: 收掉 {} 格{}",
                r.killed.len(),
                if r.respawned_plan {
                    ",進度樹重開"
                } else {
                    ""
                }
            ))
        }
        _ => {
            // 補回的格要跑 $EZ2_COCKPIT_DIR 底下的格腳本:沒設就什麼都不動(043dd #739 L2,不切出空格)
            cockpit_dir(std::env::var("EZ2_COCKPIT_DIR").ok())?;
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
                    ",進度樹重開"
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

    /// 043dd #739 L1 / L2:說明字跟參數一致;off 沒有格腳本資料夾就不動
    #[test]
    fn usage_matches_flags_and_off_needs_the_cockpit_dir() {
        assert!(!USAGE.contains("--no-right"), "not a flag");
        assert!(USAGE.contains("off --want"), "off needs --want");
        assert_eq!(cockpit_dir(None).map_err(|e| e.0), Err(2));
        assert_eq!(cockpit_dir(Some("  ".into())).map_err(|e| e.0), Err(2));
        assert_eq!(cockpit_dir(Some("/c".into())), Ok("/c".to_string()));
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
