// SPDX-License-Identifier: Apache-2.0
//! 席位視角(非艦長畫面):同一個座艙窗切換版面(Andy 11-13 07:5x 點頭的改案)。
//!
//! - 版面是**窗選項** `@aib_layout`(1ced7 條件 1):`seat` = 只留對話(hub)與進度樹(plan);
//!   沒標 / 其他值 = 完整版面(照 panes 設定檔,三個艦長座艙預設不變)。外框重開 = 窗選項消失 = 回完整版面。
//! - 這裡只碰**外框** tmux(座艙窗的格),從不碰 hub 伺服器裡任何窗(1ced7 條件 3)。
//! - `on`:標窗 ⇒ 收掉 seat 不要的格(left / right / bottom / hosts / review,都是畫面格,收掉不碰任何 AI)
//!   ⇒ 重開 plan 格(aib-plan 啟動時讀窗選項,seat 就固定在本席)。
//! - `off`:拿掉標記 ⇒ 缺右欄就整窗切到最右補回(跟 cockpit-resize.sh 的 _heal_right_pane 同法)
//!   ⇒ 重開 plan 格;主機群與待審框交給 `cockpit-full.sh --ensure` 照原本的格腳本補(呼叫端接著跑)。
//! - tmux 走 [`Tmux`] trait,測試用假的記下每一個呼叫。

/// 外框 tmux。`run` 回 stdout;指令失敗 / 起不來 ⇒ None。
pub trait Tmux {
    fn run(&self, args: &[&str]) -> Option<String>;
}

/// 真的 tmux:`AIB_TMUX_BIN` 可換(測試 / 沙箱),一律拿掉 TMUX 讓它找外框預設伺服器
/// (aib 常從 hub 裡叫,環境裡的 TMUX 指的是 hub 那台)。
pub struct RealTmux;

impl Tmux for RealTmux {
    fn run(&self, args: &[&str]) -> Option<String> {
        let bin = std::env::var("AIB_TMUX_BIN").unwrap_or_else(|_| "tmux".into());
        let o = std::process::Command::new(bin)
            .args(args)
            .env_remove("TMUX")
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .ok()?;
        o.status
            .success()
            .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
    }
}

/// 問「自己這一格所在的那台伺服器」:保留 TMUX(aib-plan 在 hub 裡被叫起來時,
/// $TMUX_PANE 是 hub 的格號,不能拿去問外框 —— 外框剛好有同號的格就會誤判,審查 L3)
pub struct OwnTmux;

impl Tmux for OwnTmux {
    fn run(&self, args: &[&str]) -> Option<String> {
        let bin = std::env::var("AIB_TMUX_BIN").unwrap_or_else(|_| "tmux".into());
        let o = std::process::Command::new(bin)
            .args(args)
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .ok()?;
        o.status
            .success()
            .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    Full,
    Seat,
}

impl Layout {
    pub fn parse(s: &str) -> Layout {
        if s.trim() == "seat" {
            Layout::Seat
        } else {
            Layout::Full
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Layout::Full => "full",
            Layout::Seat => "seat",
        }
    }
}

/// seat 版面留下的格
pub const SEAT_ROLES: [&str; 2] = ["hub", "plan"];
/// seat 版面收掉的格(hub / plan 永遠不在這裡)
/// edit = 待審框的編輯框(mouse-ctl.sh),底下的待審框收掉了它也要一起收(審查 L1)
pub const SEAT_DROPS: [&str; 6] = ["left", "right", "bottom", "hosts", "review", "edit"];

/// 窗的版面;讀不到 = 完整版面(讀不到不把格收掉)
pub fn layout(t: &dyn Tmux, win: &str) -> Layout {
    t.run(&["show-option", "-qvw", "-t", win, "@aib_layout"])
        .map_or(Layout::Full, |v| Layout::parse(&v))
}

/// 設定檔說的格(一行一個)再照版面收窄;seat 只留 hub、plan,順序照原本
pub fn filter_roles(roles: &str, l: Layout) -> Vec<String> {
    roles
        .lines()
        .map(str::trim)
        .filter(|r| !r.is_empty())
        .filter(|r| l == Layout::Full || SEAT_ROLES.contains(r))
        .map(str::to_string)
        .collect()
}

/// 中欄(對話與右欄之間：上 plan 進度樹、下 hosts 主機群)的開關檔，放在座艙狀態夾裡
/// (519d0 11-14:開關由 Rust 管，不往 panes.json 加 key,凍結的 panes.py 不改)。
pub const PLAN_FILE: &str = "seatview-plan";
/// 中欄的兩格，照建格順序;一個開關管兩格(hosts 是從 plan 切出來的)
pub const PLAN_ROLES: [&str; 2] = ["plan", "hosts"];

/// 中欄開關;沒檔 = 開(1ced7 原本的預設)
pub fn plan_read(dir: &std::path::Path) -> Result<bool, String> {
    match std::fs::read_to_string(dir.join(PLAN_FILE)) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(e) => Err(format!("read_failed: {e}")),
        Ok(s) => match s.trim() {
            "on" => Ok(true),
            "off" => Ok(false),
            _ => Err("bad_state".into()),
        },
    }
}

/// 寫中欄開關：先寫暫存檔再改名，讀的人不會讀到半個檔
pub fn plan_write(dir: &std::path::Path, on: bool) -> std::io::Result<()> {
    let tmp = dir.join(format!(".{PLAN_FILE}.{}.tmp", std::process::id()));
    std::fs::write(&tmp, if on { "on\n" } else { "off\n" })?;
    std::fs::rename(&tmp, dir.join(PLAN_FILE)).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

/// 這個窗要建的格:panes 設定檔的格(一行一個，不含中欄)加上中欄，再照版面收窄。
/// - 中欄從 hub 切出來：沒有 hub 就不建;開關開著(或席位視角)才補 plan、hosts,已經有就不重複。
/// - 開關關著 ⇒ 中欄兩格拿掉(輸入裡有也拿掉)。
/// - 席位視角只留 hub、plan,不看開關(席位視角本來就是這兩格)。
pub fn roles_for(roles: &str, l: Layout, plan_on: bool) -> Vec<String> {
    let mut v: Vec<String> = roles
        .lines()
        .map(str::trim)
        .filter(|r| !r.is_empty())
        .map(str::to_string)
        .collect();
    let hub = v.iter().any(|r| r == "hub");
    if hub && (plan_on || l == Layout::Seat) {
        for r in PLAN_ROLES {
            if !v.iter().any(|x| x == r) {
                v.push(r.to_string());
            }
        }
    } else {
        v.retain(|r| !PLAN_ROLES.contains(&r.as_str()));
    }
    filter_roles(&v.join("\n"), l)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pane {
    pub id: String,
    pub role: String,
}

pub fn panes(t: &dyn Tmux, win: &str) -> Option<Vec<Pane>> {
    let out = t.run(&["list-panes", "-t", win, "-F", "#{pane_id}|#{@aib_role}"])?;
    Some(
        out.lines()
            .filter_map(|l| {
                let (id, role) = l.split_once('|')?;
                (!id.is_empty()).then(|| Pane {
                    id: id.to_string(),
                    role: role.trim().to_string(),
                })
            })
            .collect(),
    )
}

/// 一次切換做了什麼(印給人看、測試比對)
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub killed: Vec<String>,
    /// (格, pane id)
    pub added: Vec<(String, String)>,
    pub respawned_plan: bool,
}

fn need<'a>(ps: &'a [Pane], role: &str) -> Option<&'a Pane> {
    ps.iter().find(|p| p.role == role)
}

fn respawn_plan(t: &dyn Tmux, ps: &[Pane]) -> bool {
    // 不帶指令 = 用這格原本的指令重開(pane-plan.sh 與它的環境原樣)
    need(ps, "plan").is_some_and(|p| t.run(&["respawn-pane", "-k", "-t", &p.id]).is_some())
}

/// 切到席位視角。hub 不在 ⇒ 不動手(不是座艙窗,或還沒組好)。
pub fn seat_on(t: &dyn Tmux, win: &str) -> Result<Report, String> {
    let ps = panes(t, win).ok_or("cockpit_unreadable")?;
    if need(&ps, "hub").is_none() {
        return Err("no_hub_pane".into());
    }
    t.run(&["set-option", "-w", "-t", win, "@aib_layout", "seat"])
        .ok_or("set_option_failed")?;
    let mut r = Report::default();
    for p in ps.iter().filter(|p| SEAT_DROPS.contains(&p.role.as_str())) {
        t.run(&["kill-pane", "-t", &p.id])
            .ok_or_else(|| format!("kill_failed: {}", p.id))?;
        r.killed.push(p.id.clone());
    }
    r.respawned_plan = respawn_plan(t, &ps);
    Ok(r)
}

/// 切回完整版面時要補回的外欄、怎麼切(都整窗切,跟 cockpit-resize.sh 的 _heal_right_pane 同法):
/// left 貼最左全高、bottom 貼最下全寬 12 列、right 貼最右全高 32%。
/// 主機群(hosts)與待審框(review)不在這裡補:呼叫端接著跑 `cockpit-full.sh --ensure`,照原本的格腳本長回來。
const OUTER_GROW: [(&str, &[&str]); 3] = [
    ("left", &["split-window", "-h", "-f", "-b", "-p", "20"]),
    ("bottom", &["split-window", "-v", "-f", "-l", "12"]),
    ("right", &["split-window", "-h", "-f", "-p", "32"]),
];

/// 切回完整版面。`want` = 設定檔說完整版面要的格(panes.py --expected-roles,讀不到由呼叫端報錯,不猜);
/// `cmd(role)` = 那一格要跑的指令(呼叫端照 cockpit-resize.sh 的寫法給)。
/// 設定要、而現在沒有的 left / bottom / right 補回來(審查 M1:只補右欄 ⇒ 開了左 / 下欄的人卡在自癒拒絕)。
pub fn seat_off(
    t: &dyn Tmux,
    win: &str,
    want: &[String],
    cmd: &dyn Fn(&str) -> String,
) -> Result<Report, String> {
    let ps = panes(t, win).ok_or("cockpit_unreadable")?;
    let hub = need(&ps, "hub").ok_or("no_hub_pane")?.id.clone();
    t.run(&["set-option", "-w", "-u", "-t", win, "@aib_layout"])
        .ok_or("set_option_failed")?;
    let mut r = Report::default();
    for (role, split) in OUTER_GROW {
        if !want.iter().any(|w| w == role) || need(&ps, role).is_some() {
            continue;
        }
        let c = cmd(role);
        let mut args: Vec<&str> = split.to_vec();
        args.extend(["-t", hub.as_str(), "-P", "-F", "#{pane_id}", c.as_str()]);
        let id = t
            .run(&args)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("split_failed: {role}"))?;
        let _ = t.run(&["set-option", "-p", "-t", &id, "@aib_role", role]);
        if role == "right" {
            for (k, v) in [("@aib_pane", "ai-list"), ("@aib_cockpit", "1")] {
                let _ = t.run(&["set-option", "-p", "-t", &id, k, v]);
            }
        }
        r.added.push((role.to_string(), id));
    }
    r.respawned_plan = respawn_plan(t, &ps);
    Ok(r)
}

/// aib-plan 自己這一格所在的窗是不是席位視角(啟動時問一次,不每幀問)
pub fn pane_layout(t: &dyn Tmux, pane: &str) -> Layout {
    t.run(&["show-option", "-qvw", "-t", pane, "@aib_layout"])
        .map_or(Layout::Full, |v| Layout::parse(&v))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// 假外框:記下每一個呼叫;格表照 kill / split 更新
    struct Fake {
        calls: RefCell<Vec<String>>,
        rows: RefCell<Vec<(String, String)>>,
        layout: RefCell<String>,
        fail_list: bool,
    }

    impl Fake {
        fn new(rows: &[(&str, &str)]) -> Fake {
            Fake {
                calls: RefCell::new(Vec::new()),
                rows: RefCell::new(
                    rows.iter()
                        .map(|(a, b)| (a.to_string(), b.to_string()))
                        .collect(),
                ),
                layout: RefCell::new(String::new()),
                fail_list: false,
            }
        }
        fn roles(&self) -> Vec<String> {
            self.rows.borrow().iter().map(|r| r.1.clone()).collect()
        }
    }

    impl Tmux for Fake {
        fn run(&self, a: &[&str]) -> Option<String> {
            self.calls.borrow_mut().push(a.join(" "));
            match a[0] {
                "list-panes" if self.fail_list => None,
                "list-panes" => Some(
                    self.rows
                        .borrow()
                        .iter()
                        .map(|(i, r)| format!("{i}|{r}\n"))
                        .collect(),
                ),
                "show-option" => Some(self.layout.borrow().clone()),
                "set-option" if a.contains(&"@aib_layout") => {
                    *self.layout.borrow_mut() = if a.contains(&"-u") {
                        String::new()
                    } else {
                        a[a.len() - 1].to_string()
                    };
                    Some(String::new())
                }
                "set-option" => {
                    if a[a.len() - 2] == "@aib_role" {
                        let id = a[3];
                        for r in self.rows.borrow_mut().iter_mut().filter(|r| r.0 == id) {
                            r.1 = a[a.len() - 1].to_string();
                        }
                    }
                    Some(String::new())
                }
                "kill-pane" => {
                    self.rows.borrow_mut().retain(|r| r.0 != a[2]);
                    Some(String::new())
                }
                "split-window" => {
                    let id = format!("%{}", 50 + self.rows.borrow().len());
                    self.rows.borrow_mut().push((id.clone(), String::new()));
                    Some(format!("{id}\n"))
                }
                "respawn-pane" => Some(String::new()),
                _ => None,
            }
        }
    }

    const FULL: [(&str, &str); 6] = [
        ("%0", "hub"),
        ("%1", "plan"),
        ("%2", "hosts"),
        ("%3", "right"),
        ("%4", "review"),
        ("%5", "edit"),
    ];

    #[test]
    fn filter_roles_by_layout() {
        let all = "left\nbottom\nhub\nright\nplan\nhosts\n";
        assert_eq!(filter_roles(all, Layout::Seat), ["hub", "plan"]);
        assert_eq!(
            filter_roles(all, Layout::Full),
            ["left", "bottom", "hub", "right", "plan", "hosts"]
        );
        assert_eq!(filter_roles("hub\nright\n", Layout::Seat), ["hub"]);
    }

    #[test]
    fn the_middle_column_follows_its_switch_and_needs_the_hub() {
        let base = "hub\nright\n";
        assert_eq!(
            roles_for(base, Layout::Full, true),
            ["hub", "right", "plan", "hosts"]
        );
        assert_eq!(roles_for(base, Layout::Full, false), ["hub", "right"]);
        // 輸入已經有中欄：不重複;開關關著就拿掉
        assert_eq!(
            roles_for("hub\nplan\nright\n", Layout::Full, true),
            ["hub", "plan", "right", "hosts"]
        );
        assert_eq!(
            roles_for("hub\nplan\nhosts\n", Layout::Full, false),
            ["hub"]
        );
        // 沒有 hub ⇒ 沒有錨，不建中欄
        assert_eq!(roles_for("right\n", Layout::Full, true), ["right"]);
        // 席位視角：只留 hub + plan,開關關著也一樣
        assert_eq!(roles_for(base, Layout::Seat, true), ["hub", "plan"]);
        assert_eq!(roles_for(base, Layout::Seat, false), ["hub", "plan"]);
        assert!(roles_for("", Layout::Seat, true).is_empty());
    }

    #[test]
    fn the_switch_file_defaults_on_and_refuses_a_bad_value() {
        let d = std::env::temp_dir().join(format!(
            "aib-sv-plan-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|t| t.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&d).expect("dir");
        assert_eq!(plan_read(&d), Ok(true), "no file = on");
        plan_write(&d, false).expect("write");
        assert_eq!(plan_read(&d), Ok(false));
        plan_write(&d, true).expect("write");
        assert_eq!(plan_read(&d), Ok(true));
        std::fs::write(d.join(PLAN_FILE), "maybe\n").expect("write");
        assert_eq!(plan_read(&d), Err("bad_state".into()));
        let left: Vec<_> = std::fs::read_dir(&d)
            .expect("ls")
            .filter_map(Result::ok)
            .map(|e| e.file_name())
            .collect();
        assert_eq!(left, [std::ffi::OsString::from(PLAN_FILE)], "no tmp left");
        let _ = std::fs::remove_dir_all(&d);
        assert_eq!(Layout::parse("seat\n"), Layout::Seat);
        for v in ["", "full", "Seat", "seat2"] {
            assert_eq!(Layout::parse(v), Layout::Full, "{v:?}");
        }
    }

    /// 1ced7 條件 3:切換只碰外框格;hub 格、plan 格不收;不對 hub 伺服器下任何指令
    #[test]
    fn seat_on_drops_outer_panes_only() {
        let t = Fake::new(&FULL);
        let r = seat_on(&t, "S:cockpit").expect("on");
        assert_eq!(r.killed, ["%2", "%3", "%4", "%5"]);
        assert!(r.respawned_plan);
        assert_eq!(t.roles(), ["hub", "plan"]);
        assert_eq!(layout(&t, "S:cockpit"), Layout::Seat);
        for c in t.calls.borrow().iter() {
            assert!(
                !c.contains(" -L ") && !c.contains(" -S "),
                "hub server touched: {c}"
            );
            assert!(!c.starts_with("kill-pane -t %0"), "hub pane killed: {c}");
            assert!(!c.starts_with("kill-pane -t %1"), "plan pane killed: {c}");
        }
        assert!(t
            .calls
            .borrow()
            .iter()
            .any(|c| c == "respawn-pane -k -t %1"));
        // 再切一次:沒東西可收,不壞
        let r2 = seat_on(&t, "S:cockpit").expect("on again");
        assert!(r2.killed.is_empty());
    }

    fn roles(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn seat_off_adds_right_back_with_role() {
        let cmd = |r: &str| format!("bash {r}.sh");
        let t = Fake::new(&[("%0", "hub"), ("%1", "plan")]);
        *t.layout.borrow_mut() = "seat".into();
        let want = roles(&["hub", "right", "plan", "hosts"]);
        let r = seat_off(&t, "S:cockpit", &want, &cmd).expect("off");
        assert_eq!(layout(&t, "S:cockpit"), Layout::Full);
        assert_eq!(r.added.len(), 1);
        let id = r.added[0].1.clone();
        assert_eq!(t.roles(), ["hub", "plan", "right"]);
        assert!(t.calls.borrow().iter().any(|c| {
            c.starts_with("split-window -h -f -p 32 -t %0 ") && c.ends_with("bash right.sh")
        }));
        assert!(t
            .calls
            .borrow()
            .iter()
            .any(|c| c == &format!("set-option -p -t {id} @aib_role right")));
        // aib right off 的人:不補右欄
        let t2 = Fake::new(&[("%0", "hub"), ("%1", "plan")]);
        let r2 = seat_off(&t2, "S:cockpit", &roles(&["hub", "plan"]), &cmd).expect("off");
        assert!(r2.added.is_empty());
        // 右欄已經在:不重複補
        let t3 = Fake::new(&FULL);
        let r3 = seat_off(&t3, "S:cockpit", &want, &cmd).expect("off");
        assert!(r3.added.is_empty());
    }

    /// 審查 M1:開了左欄 / 下欄的人,切回完整版面時三格都要補回(角色標齊,validate 才過)
    #[test]
    fn seat_off_restores_left_and_bottom_too() {
        let cmd = |r: &str| format!("bash {r}.sh");
        let t = Fake::new(&[("%0", "hub"), ("%1", "plan")]);
        let want = roles(&["left", "bottom", "hub", "right", "plan", "hosts"]);
        let r = seat_off(&t, "S:cockpit", &want, &cmd).expect("off");
        let got: Vec<&str> = r.added.iter().map(|a| a.0.as_str()).collect();
        assert_eq!(got, ["left", "bottom", "right"]);
        let mut have = t.roles();
        have.sort();
        assert_eq!(have, ["bottom", "hub", "left", "plan", "right"]);
        let calls = t.calls.borrow();
        assert!(calls
            .iter()
            .any(|c| c.starts_with("split-window -h -f -b -p 20 -t %0 ")
                && c.ends_with("bash left.sh")));
        assert!(calls
            .iter()
            .any(|c| c.starts_with("split-window -v -f -l 12 -t %0 ")
                && c.ends_with("bash bottom.sh")));
    }

    #[test]
    fn no_hub_or_unreadable_touches_nothing() {
        let t = Fake::new(&[("%3", "right")]);
        assert_eq!(seat_on(&t, "S:cockpit"), Err("no_hub_pane".into()));
        assert_eq!(
            seat_off(&t, "S:cockpit", &roles(&["hub", "right"]), &|_: &str| "x"
                .to_string()),
            Err("no_hub_pane".into())
        );
        let mut t2 = Fake::new(&FULL);
        t2.fail_list = true;
        assert_eq!(seat_on(&t2, "S:cockpit"), Err("cockpit_unreadable".into()));
        for f in [&t, &t2] {
            assert!(
                f.calls.borrow().iter().all(|c| c.starts_with("list-panes")),
                "{:?}",
                f.calls.borrow()
            );
        }
    }
}
