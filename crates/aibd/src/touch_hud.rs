//! A small always-on-top window beside the system's fingerprint window, with what is asked
//! and the seconds left (DESIGN-touch-countdown-7b81e-1121 甲1; Andy 11-21 01:42:「彈出的生物
//! 辨識，應該都要倒數」; 5d33a PREREAD-touchcountdown). The system's window cannot count down:
//! its words are fixed when it is raised.
//!
//! It only shows. It has no button, takes no part in the approval, and the fingerprint is
//! asked whether or not it came up. Its first line says the system's window is the one to
//! read, and both show the same four-character code ([`crate::owner_ok::pair_code`]): any
//! program of this account can draw a window like this one (5d33a L1).
//!
//! The system's `osascript` runs one fixed script; the words travel as arguments and the
//! script never evaluates them. One window at a time (the fingerprint gate opens one), killed
//! by the pid aibd started when the fingerprint window ends; left alone it closes by itself a
//! second after the ticket expires. macOS only; off until the server turns it on, so no test
//! raises a window.
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};

pub const PROGRAM: &str = "/usr/bin/osascript";

/// The whole script. Arguments: the plain sentence, the unix second the ticket expires, the
/// code. Nothing here evaluates an argument: they are only set as a label's text.
pub const SCRIPT: &str = r#"ObjC.import('Cocoa');
function run(argv) {
  if (argv.length && argv[0] === '--') { argv = argv.slice(1); }
  var words = String(argv[0] || '');
  var until = parseInt(argv[1], 10);
  var code = String(argv[2] || '');
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);
  function rgb(r, g, b) { return $.NSColor.colorWithSRGBRedGreenBlueAlpha(r / 255, g / 255, b / 255, 1); }
  var bg = rgb(0x16, 0x1b, 0x24), gold = rgb(0xe3, 0xc0, 0x6a), fg = rgb(0xe6, 0xe8, 0xee);
  var dim = rgb(0x9a, 0xa3, 0xb5), warn = rgb(0xe8, 0x93, 0x5a);
  var w = 460, h = 132;
  var scr = $.NSScreen.mainScreen.visibleFrame;
  var rect = $.NSMakeRect(scr.origin.x + scr.size.width - w - 24, scr.origin.y + scr.size.height - h - 24, w, h);
  var win = $.NSPanel.alloc.initWithContentRectStyleMaskBackingDefer(rect, 1 | 128, 2, false);
  win.setLevel(25);
  win.setCollectionBehavior(1 | 256);
  win.title = 'AIB';
  win.appearance = $.NSAppearance.appearanceNamed($.NSAppearanceNameDarkAqua);
  win.backgroundColor = bg;
  var view = win.contentView;
  view.wantsLayer = true;
  view.layer.cornerRadius = 12;
  function label(x, y, width, height, font, color) {
    var t = $.NSTextField.labelWithString('');
    t.setFrame($.NSMakeRect(x, y, width, height));
    t.setFont(font);
    t.setTextColor(color);
    t.setLineBreakMode(4);
    view.addSubview(t);
    return t;
  }
  var bold = function (size) { return $.NSFont.boldSystemFontOfSize(size); };
  var secs = label(14, 52, 72, 40, $.NSFont.monospacedDigitSystemFontOfSizeWeight(32, 0.4), gold);
  secs.setAlignment(1);
  var unit = label(14, 30, 72, 18, $.NSFont.systemFontOfSize(12), dim);
  unit.setAlignment(1);
  var x0 = 98, tw = w - x0 - 16;
  var top = label(x0, 96, tw, 18, $.NSFont.systemFontOfSize(12), dim);
  var act = label(x0, 66, tw, 24, bold(15), fg);
  var pre = label(x0, 30, 54, 22, $.NSFont.systemFontOfSize(13), dim);
  var box = label(x0 + 54, 28, 64, 26, $.NSFont.monospacedSystemFontOfSizeWeight(15, 0.4), gold);
  box.setAlignment(1);
  box.wantsLayer = true;
  box.layer.borderWidth = 1;
  box.layer.borderColor = gold.CGColor;
  box.layer.cornerRadius = 6;
  var post = label(x0 + 124, 30, tw - 124, 22, $.NSFont.systemFontOfSize(12), dim);
  top.stringValue = '以系統指紋視窗上的字為準';
  act.stringValue = words;
  pre.stringValue = '對照碼';
  box.stringValue = code;
  post.stringValue = '跟系統指紋視窗上的一樣才按';
  win.orderFrontRegardless;
  for (;;) {
    var left = until - Math.floor(Date.now() / 1000);
    if (left > 0) {
      secs.stringValue = String(left);
      unit.stringValue = '秒';
      secs.setTextColor(left <= 5 ? warn : gold);
    } else {
      secs.stringValue = '';
      unit.stringValue = '這張過期了';
      unit.setTextColor(warn);
    }
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.5));
    if (left <= -1) break;
  }
}"#;

static ON: AtomicBool = AtomicBool::new(false);

/// Called by the server when the owner's fingerprint is set up; never by a test.
pub fn turn_on() {
    ON.store(true, Ordering::SeqCst);
}

/// The one command: the script is the constant, the three words are arguments after `--`.
pub fn command(program: &str, words: &str, expires_at: u64, code: &str) -> Command {
    let mut c = Command::new(program);
    c.args(["-l", "JavaScript", "-e"])
        .arg(SCRIPT)
        .arg("--")
        .arg(words)
        .arg(expires_at.to_string())
        .arg(code)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    c
}

/// The window, for as long as this lives. Nothing when it could not be started.
pub struct Hud(Option<Child>);

impl Hud {
    /// Raised only on macOS and only when [`turn_on`] was called.
    pub fn show(words: &str, expires_at: u64, code: &str) -> Hud {
        if cfg!(target_os = "macos") && ON.load(Ordering::SeqCst) {
            Hud::start(PROGRAM, words, expires_at, code)
        } else {
            Hud(None)
        }
    }

    /// A failure to start is logged and is nothing else: the fingerprint is asked all the same.
    pub fn start(program: &str, words: &str, expires_at: u64, code: &str) -> Hud {
        match command(program, words, expires_at, code).spawn() {
            Ok(child) => Hud(Some(child)),
            Err(e) => {
                tracing::warn!(error = %e, "touch hud: not started (the fingerprint window is asked all the same)");
                Hud(None)
            }
        }
    }

    pub fn pid(&self) -> Option<u32> {
        self.0.as_ref().map(Child::id)
    }
}

impl Drop for Hud {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            // the child aibd started, by its handle: never a pid looked up
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 5d33a PREREAD-touchcountdown 3: the script is the constant; the words are arguments.
    #[test]
    fn the_script_is_fixed_and_the_words_are_only_arguments() {
        let hostile = "x'); doShellScript('touch /tmp/x'); ('";
        let c = command(PROGRAM, hostile, 1_795_192_000, "A1B2");
        let args: Vec<String> = c
            .get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        assert_eq!(
            args,
            [
                "-l",
                "JavaScript",
                "-e",
                SCRIPT,
                "--",
                hostile,
                "1795192000",
                "A1B2"
            ],
            "reason: hud_arguments"
        );
        assert_eq!(c.get_program(), PROGRAM);
        // an empty environment
        assert_eq!(c.get_envs().count(), 0, "reason: hud_environment");
        // nothing in the script runs a text
        for word in [
            "eval",
            "doShellScript",
            "run script",
            "Function(",
            "runScript",
            "system(",
            // r2 (3e53b 11-23, ORDER-touchhud 第 1 項): the other ways JXA turns text into code
            "setTimeout",
            "setInterval",
            "NSAppleScript",
            "NSTask",
            "NSUserScriptTask",
            "performSelector",
            "bindFunction",
            "ObjC.import('Foundation')",
            "Application(",
            "Library(",
        ] {
            assert!(!SCRIPT.contains(word), "reason: script_can_run_{word}");
        }
        // the three arguments are read once each, as text or a number, never anything else
        assert_eq!(
            SCRIPT.matches("argv[").count(),
            4,
            "reason: argv_read_elsewhere"
        );
        for read in [
            "String(argv[0] || '')",
            "parseInt(argv[1], 10)",
            "String(argv[2] || '')",
            "argv[0] === '--'",
        ] {
            assert!(SCRIPT.contains(read), "reason: argv_read_{read}");
        }
        // the three places say the same: the system window is the one to read, the code is
        // the ticket's (owner_ok::pair_code), the action the window's first line
        assert!(
            SCRIPT.contains("跟系統指紋視窗上的一樣才按"),
            "reason: no_compare_line"
        );
        assert!(SCRIPT.contains("這張過期了"), "reason: no_expired_line");
        assert!(
            SCRIPT.contains("以系統指紋視窗上的字為準"),
            "reason: no_source_line"
        );
        // off unless the server turned it on: a test never raises a window
        assert!(
            Hud::show("w", 1, "c").pid().is_none(),
            "reason: hud_on_in_tests"
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_window_ends_with_its_owner_and_a_failed_start_is_nothing() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let fake = dir.path().join("hud");
        std::fs::write(
            &fake,
            "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > \"$0.argv\"\nexec /bin/sleep 60\n",
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o700)).unwrap();
        let hud = Hud::start(fake.to_str().unwrap(), "登記這個資料夾當倉:demo", 9, "A1B2");
        let pid = hud.pid().expect("reason: hud_not_started");
        let argv = std::path::PathBuf::from(format!("{}.argv", fake.display()));
        let end = std::time::Instant::now() + std::time::Duration::from_secs(20);
        while !argv.exists() && std::time::Instant::now() < end {
            std::thread::yield_now();
        }
        assert!(argv.exists(), "precondition: the fake window ran");
        let alive = |pid: u32| {
            std::process::Command::new("/bin/kill")
                .args(["-0", &pid.to_string()])
                .stderr(Stdio::null())
                .status()
                .is_ok_and(|s| s.success())
        };
        assert!(alive(pid), "precondition: the fake window is running");
        let dropped = std::time::Instant::now();
        drop(hud);
        assert!(
            dropped.elapsed() < std::time::Duration::from_secs(20),
            "reason: hud_waited_out"
        );
        assert!(!alive(pid), "reason: hud_left_running");
        // no program: no window, no panic
        let none = Hud::start("/no/such/osascript", "w", 9, "c");
        assert!(none.pid().is_none(), "reason: hud_from_nothing");
    }
}
