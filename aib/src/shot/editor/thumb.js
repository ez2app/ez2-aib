/* SPDX-License-Identifier: Apache-2.0 */
// Ez2 AIB 截圖縮圖:圖送進某一席的輸入框後,在終端機畫面裡 [Image #…] 那一行的正上方浮一張真實縮圖(維護者 10-09)。
// 用法:osascript -l JavaScript thumb.js <設定檔.json>
//   設定檔(shot_helper.py 寫):{png, title, x, line_top, max_w, max_h, tmux, sock, pane, needle, reedit:[指令…], max_s}
//   x / line_top 是螢幕座標(左上為原點);縮圖的底邊貼在 line_top 上方。
// 跟著輸入框:每秒看一次 —— 訊息送出(畫面上沒有 needle 了)就關;那一席不在畫面上、或終端機不在前景就先藏起來。
// 點一下(沒拖動):開編輯器放大修改(reedit 指令,背景跑),然後關掉自己。拖曳:整張移到別處。右上角 [X]:關掉。
// 不搶焦點(非啟用型面板),不打斷正在打字的視窗。
ObjC.import("Cocoa");
var G = {};

function sh(argv) {
  try {
    var t = $.NSTask.alloc.init, pipe = $.NSPipe.pipe;
    t.launchPath = argv[0]; t.arguments = $(argv.slice(1));
    t.standardOutput = pipe; t.standardError = $.NSFileHandle.fileHandleWithNullDevice;
    t.standardInput = $.NSFileHandle.fileHandleWithNullDevice;
    t.launch; t.waitUntilExit;
    var d = pipe.fileHandleForReading.readDataToEndOfFile;
    return { rc: t.terminationStatus, out: $.NSString.alloc.initWithDataEncoding(d, $.NSUTF8StringEncoding).js || "" };
  } catch (e) { return { rc: -1, out: "" }; }
}

// 只看輸入框:畫面尾端幾行裡最後一個「提示符開頭」的行(grok 外面有一圈 │ 框,先剝掉)。
//   不能整個畫面找:送出後那則訊息留在對話裡,也有「[Image #1]」(同 shot_helper.find_needle)。
function composerHas(screen, needle) {
  var lines = screen.split("\n").filter(function (l) { return l.trim() !== ""; }).slice(-12);
  for (var i = lines.length - 1; i >= 0; i--) {
    var t = lines[i].replace(/\u00a0/g, " ").trim().replace(/^[│┃|]\s*/, "");
    if (t.charAt(0) === "\u276f" || t.charAt(0) === "\u203a") return lines[i].indexOf(needle) >= 0;
  }
  return false;
}

function spawn(argv) {
  try {
    var t = $.NSTask.alloc.init;
    t.launchPath = argv[0]; t.arguments = $(argv.slice(1));
    t.standardInput = $.NSFileHandle.fileHandleWithNullDevice;
    t.standardOutput = $.NSFileHandle.fileHandleWithNullDevice;
    t.standardError = $.NSFileHandle.fileHandleWithNullDevice;
    t.launch;
  } catch (e) {}
}

function openEditor() {
  if (G.cfg.reedit && G.cfg.reedit.length) spawn(G.cfg.reedit);
  $.NSApplication.sharedApplication.terminate(null);
}

// 蓋在整張縮圖上的透明一層:按下 → 移動超過 3 點就是拖曳(跟著滑鼠移);放開時沒拖過就是「點一下」
ObjC.registerSubclass({
  name: "Ez2ThumbDrag",
  superclass: "NSView",
  methods: {
    "acceptsFirstMouse:": { types: ["bool", ["id"]], implementation: function (e) { return true; } },
    "mouseDown:": { types: ["void", ["id"]], implementation: function (e) {
      G.down = $.NSEvent.mouseLocation; G.orig = G.win.frame.origin; G.dragged = false;
    } },
    "mouseDragged:": { types: ["void", ["id"]], implementation: function (e) {
      var p = $.NSEvent.mouseLocation, dx = p.x - G.down.x, dy = p.y - G.down.y;
      if (!G.dragged && Math.abs(dx) + Math.abs(dy) < 3) return;
      G.dragged = true;
      G.win.setFrameOrigin($.NSMakePoint(G.orig.x + dx, G.orig.y + dy));
    } },
    "mouseUp:": { types: ["void", ["id"]], implementation: function (e) { if (!G.dragged) openEditor(); } }
  }
});

ObjC.registerSubclass({
  name: "Ez2ThumbCtl",
  methods: {
    "close:": { types: ["void", ["id"]], implementation: function (s) { $.NSApplication.sharedApplication.terminate(null); } },
    "tick:": { types: ["void", ["id"]], implementation: function (s) {
      var c = G.cfg;
      if ((Date.now() - G.t0) / 1000 > (c.max_s || 1800)) { $.NSApplication.sharedApplication.terminate(null); return; }
      var cap = sh([c.tmux, "-S", c.sock, "capture-pane", "-p", "-t", c.pane]);
      if (cap.rc !== 0 || !composerHas(cap.out, c.needle)) { $.NSApplication.sharedApplication.terminate(null); return; }   // 送出了、或那一席關了
      var act = sh([c.tmux, "-S", c.sock, "display", "-p", "-t", c.pane, "#{window_active}"]).out.trim() === "1";
      var front = $.NSWorkspace.sharedWorkspace.frontmostApplication;
      var termFront = !front.isNil() && front.bundleIdentifier.js === "com.apple.Terminal";
      if (act && termFront) G.win.orderFrontRegardless; else G.win.orderOut(null);
    } }
  }
});

function run(argv) {
  var raw = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
  if (raw.isNil()) return "no-config";
  var c = JSON.parse(raw.js); G.cfg = c; G.t0 = Date.now();
  var img = $.NSImage.alloc.initWithContentsOfFile(c.png);
  if (img.isNil()) return "no-image";
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
  var sz = img.size, top = 24, pad = 8;
  var k = Math.min((c.max_w - pad * 2) / sz.width, (c.max_h - pad - top) / sz.height, 2);
  var iw = Math.max(40, Math.round(sz.width * k)), ih = Math.max(30, Math.round(sz.height * k));
  var W = iw + pad * 2, H = ih + pad + top;
  var primaryH = $.NSScreen.screens.objectAtIndex(0).frame.size.height;   // 左上原點 → Cocoa 左下原點
  var x = c.x, yTop = c.line_top - 4 - H;
  // 128 = NSWindowStyleMaskNonactivatingPanel:點它、拖它都不會把焦點從終端機搶走
  var win = $.NSPanel.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(x, primaryH - yTop - H, W, H), 128, 2, false);
  win.level = 3;
  win.hidesOnDeactivate = false;                   // ★ NSPanel 預設「不在前景就藏」;我們刻意不搶焦點,不關掉就永遠看不到
  win.opaque = false;
  win.backgroundColor = $.NSColor.colorWithCalibratedWhiteAlpha(0.12, 0.96);
  win.hasShadow = true;
  win.collectionBehavior = 1 | 16;
  var cv = win.contentView;
  cv.wantsLayer = true; cv.layer.cornerRadius = 10; cv.layer.masksToBounds = true;
  var lbl = $.NSTextField.labelWithString("📷 " + (c.title || "") + "  ·  點一下放大修改,可拖曳");
  lbl.textColor = $.NSColor.whiteColor; lbl.font = $.NSFont.systemFontOfSize(11);
  lbl.frame = $.NSMakeRect(pad, H - top + 5, W - pad * 2 - 26, 16); lbl.lineBreakMode = 5;
  cv.addSubview(lbl);
  var iv = $.NSImageView.alloc.initWithFrame($.NSMakeRect(pad, pad, iw, ih));
  iv.image = img; iv.imageScaling = 3;
  iv.wantsLayer = true; iv.layer.cornerRadius = 5; iv.layer.masksToBounds = true;
  cv.addSubview(iv);
  G.ctl = $.Ez2ThumbCtl.alloc.init;
  G.win = win;
  cv.addSubview($.Ez2ThumbDrag.alloc.initWithFrame($.NSMakeRect(0, 0, W, H)));   // 蓋在最上面(除了 [X])
  var x_ = $.NSButton.alloc.initWithFrame($.NSMakeRect(W - 26, H - top + 2, 22, 20));
  x_.bordered = false; x_.title = "✕"; x_.font = $.NSFont.boldSystemFontOfSize(13);
  x_.contentTintColor = $.NSColor.whiteColor;
  x_.toolTip = "關掉縮圖(輸入框裡的圖不受影響)";
  x_.target = G.ctl; x_.action = "close:";
  cv.addSubview(x_);
  win.orderFrontRegardless;
  $.NSTimer.scheduledTimerWithTimeIntervalTargetSelectorUserInfoRepeats(1.0, G.ctl, "tick:", null, true);
  app.run;
}
