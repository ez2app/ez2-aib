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
ObjC.import('CoreGraphics');
function run(argv) {
  if (argv.length && argv[0] === '--') { argv = argv.slice(1); }
  var words = String(argv[0] || '');
  var until = parseInt(argv[1], 10);
  var code = String(argv[2] || '');
  var total = Math.max(1, until - Math.floor(Date.now() / 1000));
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);
  function rgb(r, g, b) { return $.NSColor.colorWithSRGBRedGreenBlueAlpha(r / 255, g / 255, b / 255, 1); }
  var GOLD = rgb(227, 192, 106), FG = rgb(230, 232, 238), DIM = rgb(154, 163, 181);
  var WARN = rgb(232, 147, 90), PANEL = rgb(22, 27, 36), LINE = rgb(42, 50, 66);
  var h = 124;
  // the system's fingerprint window: only its owner's name and its bounds are read, never what
  // it shows; a hint where to stand, not a defence (Andy 11-23「移到 生物辨識框的正上方」「一樣寬」)
  function fingerprintWindow() {
    var list = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(1 | 16, 0))) || [];
    var box = null;
    for (var i = 0; i < list.length; i++) {
      var o = list[i], b = o.kCGWindowBounds;
      if (o.kCGWindowOwnerName === 'coreautha' && o.kCGWindowLayer >= 1000 && b && b.Width > 100 && b.Height > 100) {
        if (!box || b.Width * b.Height > box.Width * box.Height) { box = b; }
      }
    }
    return box;
  }
  // it comes up after the fingerprint window: look every 0.2 s for at most 3 s
  var box = null, started = Date.now() / 1000;
  while (!box && Date.now() / 1000 - started < 3) {
    box = fingerprintWindow();
    if (!box) { $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.2)); }
  }
  var w = box ? Math.max(220, Math.min(600, box.Width)) : 260;
  var scr = $.NSScreen.mainScreen.visibleFrame;
  var rect = $.NSMakeRect(scr.origin.x + scr.size.width - w - 24, scr.origin.y + scr.size.height - h - 24, w, h);
  if (box) {
    var screens = $.NSScreen.screens, top = screens.objectAtIndex(0).frame.size.height;
    var byTop = top - box.Y, byLow = top - box.Y - box.Height, cx = box.X + box.Width / 2, area = scr;
    for (var k = 0; k < screens.count; k++) {
      var sf = screens.objectAtIndex(k).frame;
      if (cx >= sf.origin.x && cx <= sf.origin.x + sf.size.width && byLow >= sf.origin.y && byLow <= sf.origin.y + sf.size.height) {
        area = screens.objectAtIndex(k).visibleFrame;
      }
    }
    var x = Math.max(area.origin.x, Math.min(cx - w / 2, area.origin.x + area.size.width - w));
    var y = byTop + 8;
    if (y + h > area.origin.y + area.size.height) { y = byLow - 8 - h; }
    rect = $.NSMakeRect(x, y, w, h);
  }
  var win = $.NSPanel.alloc.initWithContentRectStyleMaskBackingDefer(rect, 1 | 128 | 32768, 2, false);
  win.titlebarAppearsTransparent = true;
  win.titleVisibility = 1;
  win.setAppearance($.NSAppearance.appearanceNamed('NSAppearanceNameDarkAqua'));
  win.setLevel(25);
  win.setCollectionBehavior(1 | 256);
  win.setHasShadow(true);
  win.setBackgroundColor(PANEL);
  var view = win.contentView;
  function label(x, y, ww, hh, size, color, bold, mono) {
    var t = $.NSTextField.labelWithString('');
    t.setFrame($.NSMakeRect(x, y, ww, hh));
    t.setFont(mono ? $.NSFont.monospacedSystemFontOfSizeWeight(size, 0.4) : (bold ? $.NSFont.boldSystemFontOfSize(size) : $.NSFont.systemFontOfSize(size)));
    t.setTextColor(color);
    t.setLineBreakMode(4);
    view.addSubview(t);
    return t;
  }
  function panel(x, y, ww, hh, radius, border, fill) {
    var b = $.NSBox.alloc.initWithFrame($.NSMakeRect(x, y, ww, hh));
    b.setBoxType(4);
    b.setCornerRadius(radius);
    b.setBorderWidth(border);
    b.setBorderColor(GOLD);
    b.setFillColor(fill);
    view.addSubview(b);
    return b;
  }
  // the gold frame is an NSBox over the whole panel: a layer border on the content view does
  // not show under the window frame (ebab8 demo 11-23 02:15)
  var frame = panel(0, 0, w, h, 10, 1.5, PANEL);
  var num = label(14, h - 52, 62, 38, 30, GOLD, true, true);
  num.setAlignment(1);
  var sec = label(14, h - 66, 62, 14, 10.5, DIM, false, false);
  sec.setAlignment(1);
  sec.stringValue = '秒';
  var act = label(84, h - 60, w - 96, 44, 14.5, FG, true, false);
  act.setLineBreakMode(0);
  act.setMaximumNumberOfLines(2);
  act.stringValue = words;
  panel(14, 48, w - 28, 4, 2, 0, LINE);
  var fill = panel(14, 48, w - 28, 4, 2, 0, GOLD);
  var pre = label(14, 22, 44, 16, 12, DIM, false, false);
  pre.stringValue = '對照碼';
  panel(58, 18, 58, 22, 6, 1, PANEL);
  var chip = label(58, 20, 58, 16, 13, GOLD, true, true);
  chip.setAlignment(1);
  chip.stringValue = code;
  var post = label(14, 3, w - 28, 14, 10.5, DIM, false, false);
  post.stringValue = box ? '跟下面指紋視窗上的一樣才按' : '跟系統指紋視窗上的一樣才按';
  win.orderFrontRegardless;
  for (;;) {
    var now = Date.now() / 1000, left = Math.ceil(until - now);
    var late = left <= 5;
    num.stringValue = left > 0 ? String(left) : '0';
    num.setTextColor(late ? WARN : GOLD);
    fill.setFillColor(late ? WARN : GOLD);
    frame.setBorderColor(late ? WARN : GOLD);
    if (left <= 0) { act.stringValue = '這張過期了'; }
    fill.setFrame($.NSMakeRect(14, 48, Math.max(0, (w - 28) * (until - now) / total), 4));
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
    if (left <= -2) break;
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
        // where it stands: only the fingerprint window's owner and bounds are read
        assert!(
            SCRIPT.contains("o.kCGWindowOwnerName === 'coreautha'")
                && !SCRIPT.contains("kCGWindowName")
                && !SCRIPT.contains("CGWindowListCreateImage"),
            "reason: hud_reads_other_windows"
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
