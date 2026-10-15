// SPDX-License-Identifier: Apache-2.0
// Ez2 AIB 截圖小幫手(10-09 維護者):AIB 開著時,在 Mac 任何地方按 Control+Shift+S 截圖。
// 用系統內建的 RegisterEventHotKey 登記全域快捷鍵 —— 不需要「輔助使用」權限;
//   按下去會先被這支接走,輸入法、前景 App 都收不到,所以不會跟鼠鬚管的同一組鍵兩邊一起截。
// 這支只管「接鍵」與「AIB 還在不在」;截圖、編輯器、存放位置都由 shot_helper.py shoot 做(同一份邏輯,好測)。
// 設定:~/.ez2-aib/shot/agent.json {"python": …, "helper": …, "interval": 30}(shot_helper.py ensure 寫)
// 單飛:~/.ez2-aib/shot/agent.lock(flock);狀態:~/.ez2-aib/shot/agent-status.json(每輪寫一次)
// AIB 全關:每 interval 秒跑一次 `helper alive`,rc=1 ⇒ 自己結束;探測連錯 3 次也結束(寧可下次開艙再起)。
import AppKit
import Carbon.HIToolbox
import Foundation

let home = NSHomeDirectory()
let dir = home + "/.ez2-aib/shot"

func writeStatus(_ state: String, _ extra: [String: Any] = [:]) {
    var doc: [String: Any] = ["state": state, "pid": getpid(), "ts": Int(Date().timeIntervalSince1970)]
    for (k, v) in extra { doc[k] = v }
    if let data = try? JSONSerialization.data(withJSONObject: doc) {
        try? data.write(to: URL(fileURLWithPath: dir + "/agent-status.json"), options: .atomic)
    }
}

guard let cfgData = FileManager.default.contents(atPath: dir + "/agent.json"),
      let cfg = (try? JSONSerialization.jsonObject(with: cfgData)) as? [String: Any],
      let python = cfg["python"] as? String, let helper = cfg["helper"] as? String else {
    writeStatus("no_config"); exit(2)
}
let interval = (cfg["interval"] as? Double) ?? 30

let lockFd = open(dir + "/agent.lock", O_CREAT | O_RDWR, 0o600)
if lockFd < 0 || flock(lockFd, LOCK_EX | LOCK_NB) != 0 { exit(0) }   // 已經有一支在跑

func run(_ args: [String]) -> Int32 {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: python)
    p.arguments = [helper] + args
    p.standardInput = FileHandle.nullDevice
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return -1 }
    p.waitUntilExit()
    return p.terminationStatus
}

// 系統的快捷鍵回呼是 C 函式指標,不能抓外面的變數 ⇒ 狀態放在型別的靜態成員裡
enum Shot {
    static var python = ""
    static var helper = ""
    static var running: Process? = nil
    static func fire() {
        if let s = running, s.isRunning { return }   // 上一張還在框選或編輯,不疊第二張
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [helper, "shoot"]
        p.standardInput = FileHandle.nullDevice
        do { try p.run(); running = p } catch { writeStatus("shoot_spawn_failed") }
    }
}
Shot.python = python
Shot.helper = helper

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // 不出現在 Dock、不搶焦點

var hotRef: EventHotKeyRef? = nil
var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in Shot.fire(); return noErr }, 1, &spec, nil, nil)
let hkID = EventHotKeyID(signature: OSType(0x455A3253), id: 1)   // 'EZ2S'
let rc = RegisterEventHotKey(UInt32(kVK_ANSI_S), UInt32(controlKey | shiftKey), hkID,
                             GetApplicationEventTarget(), 0, &hotRef)
if rc != noErr { writeStatus("hotkey_busy", ["os_status": Int(rc)]); exit(3) }
writeStatus("running")

var probeErrors = 0
Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { _ in
    switch run(["alive"]) {
    case 0: probeErrors = 0; writeStatus("running")
    case 1: writeStatus("stopped_no_cabin"); exit(0)
    default:
        probeErrors += 1
        writeStatus("probe_error", ["count": probeErrors])
        if probeErrors >= 3 { exit(4) }
    }
}
app.run()
