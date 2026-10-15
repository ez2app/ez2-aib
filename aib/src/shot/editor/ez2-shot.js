/* SPDX-License-Identifier: Apache-2.0 */
// Ez2 截圖編輯器外框:用 Mac 內建 osascript(JavaScript)+ WKWebView 開編輯器,不用安裝 Electron。
// 用法:osascript -l JavaScript ez2-shot.js <暫存截圖.png> <存放資料夾> <編輯器資料夾>
//   - 暫存截圖由 AIB 截圖小幫手(Control+Shift+S,src/shot/hotkey.swift → shot_helper.py shoot)用 screencapture -i -x 拍好;取消(Esc/關視窗)就刪掉,不進第二大腦。
//   - 存回:編輯後的圖存進 <存放資料夾>/<uid>-截圖.<副檔名>(uid 與 Ez2 AI App 相同算法),並把 PNG 放進剪貼簿。
//   - 結束時把焦點還給截圖前的 App,方便直接 ⌘V。
// 紀錄:~/.ez2-aib/shot/shot.log(AIB 版;鼠鬚管安裝包 v3 那份寫在 ~/Library/Rime/,沒裝鼠鬚管的電腦沒有那個資料夾)
ObjC.import("Cocoa");
ObjC.import("WebKit");

var G = {};

function log(msg) {
  try {
    var p = $.NSHomeDirectory().js + "/.ez2-aib/shot/shot.log";
    var line = $.NSString.stringWithString(new Date().toTimeString().slice(0, 8) + " editor " + msg + "\n");
    var fh = $.NSFileHandle.fileHandleForWritingAtPath(p);
    if (fh.isNil()) { line.writeToFileAtomicallyEncodingError(p, true, $.NSUTF8StringEncoding, null); return; }
    fh.seekToEndOfFile; fh.writeData(line.dataUsingEncoding($.NSUTF8StringEncoding)); fh.closeFile;
  } catch (e) {}
}

function uid36(n) { var s = n.toString(36); while (s.length < 6) s = "0" + s; return s; }

function finish(why) {
  if (G.finished) return;
  G.finished = true;
  log("finish " + why);
  // 取色器(ColorSampler.xpc)還開著就關掉:它是獨立的系統服務,呼叫它的程式結束了它也不會自己關,
  //   會變成一層看不見的視窗蓋住螢幕、點不到東西(10-09 測試實踩)
  if (G.samplerActive) {
    try { $.NSTask.launchedTaskWithLaunchPathArguments("/usr/bin/pkill", $(["-9", "-x", "ColorSampler"])); log("killed leftover ColorSampler"); } catch (e) {}
  }
  try { $.NSFileManager.defaultManager.removeItemAtPathError(G.tmp, null); } catch (e) {}
  try { if (G.prev && !G.prev.isNil()) G.prev.activateWithOptions(0); } catch (e) {}
  $.NSApplication.sharedApplication.terminate(null);
}

function saveShot(b64, ext, pngB64) {
  var fm = $.NSFileManager.defaultManager;
  fm.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(G.outDir, true, $(), null);
  var n = Math.max(0, Math.floor((Date.now() - Date.UTC(2020, 0, 1)) / 1000));
  var path;
  do { path = G.outDir + "/" + uid36(n) + "-截圖." + ext; n++; } while (fm.fileExistsAtPath(path));
  var data = $.NSData.alloc.initWithBase64EncodedStringOptions(b64, 0);
  if (data.isNil() || !data.writeToFileAtomically(path, true)) { log("save FAIL " + path); return null; }
  copyPng(pngB64);
  log("saved " + path + " bytes=" + data.length);
  return path;
}

// 存回之後留一筆給 shot_helper.py:存在哪、要送哪一席(空字串 = 不送)、附的那句話;要送就另存一份 PNG(存檔格式可能是 webp/jpg)
function writeLastSave(path, seat, note, pngB64) {
  try {
    if (seat) {
      var png = $.NSData.alloc.initWithBase64EncodedStringOptions(pngB64, 0);
      if (!png.isNil()) png.writeToFileAtomically(G.lastPngPath, true);
    }
    var doc = { path: path, seat: seat, note: note, png: seat ? G.lastPngPath : "", ts: Math.floor(Date.now() / 1000) };
    $.NSString.stringWithString(JSON.stringify(doc)).writeToFileAtomicallyEncodingError(G.lastSavePath, true, $.NSUTF8StringEncoding, null);
    log("last-save seat=" + (seat || "-"));
  } catch (e) { log("last-save FAIL " + e); }
}

function copyPng(pngB64) {
  var png = $.NSData.alloc.initWithBase64EncodedStringOptions(pngB64, 0);
  var pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  var ok = pb.setDataForType(png, $.NSPasteboardTypePNG);
  log("clipboard png ok=" + ok + " bytes=" + png.length);
  return ok;
}

// 取色:macOS 原生 NSColorSampler(放大鏡、整個螢幕);選到就把 sRGB 色碼丟回編輯器,按 Esc 取消就什麼都不做
function pickScreenColor() {
  try {
    G.sampler = $.NSColorSampler.alloc.init;
    G.samplerActive = true;
    G.sampler.showSamplerWithSelectionHandler(function (color) {
      G.samplerActive = false;
      try {
        if (!color || color.isNil()) { log("eyedropper cancel"); return; }
        var c = color.colorUsingColorSpace($.NSColorSpace.sRGBColorSpace);
        var hex = "#" + [c.redComponent, c.greenComponent, c.blueComponent].map(function (v) {
          var n = Math.max(0, Math.min(255, Math.round(v * 255))); return (n < 16 ? "0" : "") + n.toString(16);
        }).join("");
        log("eyedropper " + hex);
        G.web.evaluateJavaScriptCompletionHandler("window.ez2SetColor && ez2SetColor(" + JSON.stringify(hex) + ")", function (r, e) {});
      } catch (e) { log("eyedropper err " + e); }
    });
  } catch (e) { log("eyedropper start err " + e); }
}

ObjC.registerSubclass({
  name: "Ez2ShotHandler",
  superclass: "NSObject",
  methods: {
    "userContentController:didReceiveScriptMessage:": {
      types: ["void", ["id", "id"]],
      implementation: function (ucc, message) {
        var m;
        try { m = JSON.parse(ObjC.unwrap(message.body)); } catch (e) { log("bad message"); return; }
        if (m.cmd === "save") {
          var p = saveShot(m.b64, m.ext, m.png);
          var name = p ? p.split("/").pop() : "";
          if (p) writeLastSave(p, m.seat || "", m.note || "", m.png);
          G.web.evaluateJavaScriptCompletionHandler("ez2Saved(" + (p ? "true" : "false") + "," + JSON.stringify(p ? name : "寫檔失敗") + ")", function (res, err) {});   // 不能傳 null:JXA 會當掉
        } else if (m.cmd === "copy") {
          copyPng(m.png);
        } else if (m.cmd === "copytext") {
          var pb = $.NSPasteboard.generalPasteboard; pb.clearContents; pb.setStringForType(m.text, $.NSPasteboardTypeString);
        } else if (m.cmd === "prefs") {
          try { $.NSString.stringWithString(JSON.stringify(m.prefs)).writeToFileAtomicallyEncodingError(G.prefsPath, true, $.NSUTF8StringEncoding, null); } catch (e) {}
        } else if (m.cmd === "cancel") {
          finish("cancel");
        } else if (m.cmd === "eyedropper") {
          pickScreenColor();
        } else if (m.cmd === "ready") {
          log("ready " + JSON.stringify(m.info));
        } else if (m.cmd === "done") {
          finish("saved");
        }
      }
    }
  }
});

ObjC.registerSubclass({
  name: "Ez2ShotWinDelegate",
  superclass: "NSObject",
  methods: {
    "windowWillClose:": { types: ["void", ["id"]], implementation: function (n) { finish("window-closed"); } }
  }
});

function menuItem(title, action, key, mask) {
  var it = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent(title, action, key);
  if (mask !== undefined) it.keyEquivalentModifierMask = mask;
  return it;
}

function buildMenu(app) {
  var main = $.NSMenu.alloc.init;
  var appItem = $.NSMenuItem.alloc.init; main.addItem(appItem);
  var appMenu = $.NSMenu.alloc.init;
  appMenu.addItem(menuItem("結束 Ez2 截圖", "terminate:", "q"));
  appItem.submenu = appMenu;
  var editItem = $.NSMenuItem.alloc.init; main.addItem(editItem);
  var edit = $.NSMenu.alloc.initWithTitle("編輯");
  edit.addItem(menuItem("剪下", "cut:", "x"));
  edit.addItem(menuItem("拷貝", "copy:", "c"));
  edit.addItem(menuItem("貼上", "paste:", "v"));
  edit.addItem(menuItem("全選", "selectAll:", "a"));
  editItem.submenu = edit;
  app.mainMenu = main;
}

function run(argv) {
  G.tmp = argv[0]; G.outDir = argv[1]; G.dir = argv[2];
  G.prefsPath = $.NSHomeDirectory().js + "/.ez2-aib/shot/prefs.json";
  // AIB 版:存回時可以順便送到某一席(shot_helper.py shoot 開編輯器前寫 seats.json,編輯器關掉後讀 last-save.json 去送)
  G.seatsPath = $.NSHomeDirectory().js + "/.ez2-aib/shot/seats.json";
  G.lastSavePath = $.NSHomeDirectory().js + "/.ez2-aib/shot/last-save.json";
  G.lastPngPath = $.NSHomeDirectory().js + "/.ez2-aib/shot/last.png";
  log("start tmp=" + G.tmp + " out=" + G.outDir);
  G.prev = $.NSWorkspace.sharedWorkspace.frontmostApplication;

  var data = $.NSData.dataWithContentsOfFile(G.tmp);
  if (data.isNil() || data.length < 100) { log("no image"); finish("no-image"); return; }
  var rep = $.NSBitmapImageRep.imageRepWithData(data);
  var pxW = rep.pixelsWide, pxH = rep.pixelsHigh;
  var prefs = {};
  try { prefs = JSON.parse($.NSString.stringWithContentsOfFileEncodingError(G.prefsPath, $.NSUTF8StringEncoding, null).js || "{}"); } catch (e) {}
  var seats = null;
  try { seats = JSON.parse($.NSString.stringWithContentsOfFileEncodingError(G.seatsPath, $.NSUTF8StringEncoding, null).js || "null"); } catch (e) {}
  var init = { dataUrl: "data:image/png;base64," + data.base64EncodedStringWithOptions(0).js, prefs: prefs, name: G.tmp.split("/").pop(), seats: seats };

  var app = $.NSApplication.sharedApplication;
  // 10-12 維護者:編輯器開著時 Dock 冒一個「exec / osascript」圖示 ⇒ 改輔助程式(Accessory):不進 Dock、不進 ⌘Tab、不顯示選單列。
  //   buildMenu 照留:選單列雖不顯示,⌘X/C/V/A 的快捷鍵仍由主選單分派(要真人試過);縮圖 thumb.js 本來就是 Accessory。
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
  buildMenu(app);

  var config = $.WKWebViewConfiguration.alloc.init;
  var ucc = config.userContentController;
  ucc.addUserScript($.WKUserScript.alloc.initWithSourceInjectionTimeForMainFrameOnly(
    "window.__EZ2_SHOT__=" + JSON.stringify(init) + ";", $.WKUserScriptInjectionTimeAtDocumentStart, true));
  ucc.addScriptMessageHandlerName($.Ez2ShotHandler.alloc.init, "ez2");

  var screen = $.NSScreen.mainScreen;
  var vis = screen.visibleFrame;
  var scale = screen.backingScaleFactor || 1;
  var w = Math.min(Math.max(pxW / scale + 80, 760), vis.size.width * 0.92);
  var h = Math.min(Math.max(pxH / scale + 140, 420), vis.size.height * 0.92);
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(0, 0, w, h), 15, 2, false);
  win.title = "Ez2 截圖";
  win.releasedWhenClosed = false;
  win.delegate = $.Ez2ShotWinDelegate.alloc.init;
  var web = $.WKWebView.alloc.initWithFrameConfiguration(win.contentView.bounds, config);
  web.autoresizingMask = 18;   // 寬高跟著視窗
  win.contentView.addSubview(web);
  G.web = web; G.win = win;
  web.loadFileURLAllowingReadAccessToURL($.NSURL.fileURLWithPath(G.dir + "/editor.html"), $.NSURL.fileURLWithPath(G.dir));
  win.center;
  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);
  app.run;
}
