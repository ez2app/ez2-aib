// SPDX-License-Identifier: Apache-2.0
// aib-sehelper:用這台 Mac 的 Secure Enclave(SE)把秘密封起來,讀的時候由系統強制 Touch ID
// (@abb89 審 #222 10-21:母鑰放本機檔 = 同帳號 AI 直接讀就拿到,Touch ID 只是擺設 ⇒ 防線要由系統把關)。
//
// 為什麼不用鑰匙圈的 biometryCurrentSet:那要 data protection keychain + keychain-access-groups entitlement,
// 未簽章的 CLI 加不進去(-34018)。CryptoKit 的 SecureEnclave.P256 不需要 entitlement:私鑰永遠不離開 SE,
// 檔案裡只有「只有這台的 SE 解得開」的 dataRepresentation,用它一定要過 accessControl(這裡 = 生物辨識)。
//
//   seal   --name N --dir D [--reason R]   stdin 讀秘密 ⇒ D/N.sealed(600)。不需要指紋(只用到 SE 的公鑰)。
//   unseal --name N --dir D --reason R     系統彈 Touch ID ⇒ 明文只寫 stdout。
//   se-create --name N --dir D             SE 簽章鑰(機器鑰,@74e8b 之後用)⇒ D/N.sekey,stdout 回公鑰。
//   se-sign   --name N --dir D --reason R  系統彈 Touch ID ⇒ stdin 的資料簽 ECDSA P-256(SHA-256),stdout 回 base64 DER。
//                                          reason 超過 maxSignReason 字 ⇒ reason_too_long(不截,視窗字就是要簽的原話)。
//   grant-create --name N --dir D          TID-2b 授權單簽單鑰 ⇒ D/N.sekey(kind grantkey),ACL = userPresence
//                                          (Touch ID 或登入密碼;無指紋 Mac 也能用 —— @aeb40 10-29 Q1)。se-sign 一樣能用它簽。
//   se-pub    --name N --dir D             不要指紋:**從 SE 鑰本體重算**公鑰(不讀檔裡的 public_key 欄位,那欄改得到),
//                                          並探過這把鑰一定要本人(換成不帶 ACL 的 SE 鑰 ⇒ weak_key)。
//   se-verify --name N --dir D --sig B64   不要指紋:stdin 資料 + DER 簽章,用重算的公鑰驗;過 ⇒ {"ok": true},不過 ⇒ bad_sig。
//   lockwatch --name GRANT_KEY --dir D     長駐:鎖螢幕 / 螢幕保護開始 ⇒ D/lockwatch.json 的 last_lock_utc;每 30 秒心跳。
//
// 規矩:
//   - 沒有任何略過生物辨識的旗標;accessControl 固定 [.privateKeyUsage, .biometryCurrentSet](換了指紋就失效,要重封);
//     唯一例外是授權單簽單鑰 GRANT_KEY(kind grantkey):[.privateKeyUsage, .userPresence](指紋或登入密碼),只准這個名字用。
//   - se-pub / se-verify 載鑰後先探一次:全新 LAContext + interactionNotAllowed 試簽,只有拿到「需要使用者互動」
//     (LAError.notInteractive)才算合格;簽得出來 = 這把鑰不要本人 ⇒ weak_key;其他錯 ⇒ key_probe_failed(@aeb40 #419 M1)。
//   - reason 由 aib 組好傳進來;這裡再擋控制字元 / 方向字元、截長度(跟 touchid_approve.clean 同精神)。
//   - 秘密值只走 stdin / stdout;不進 argv、不寫 log。錯誤只回一行原因代碼到 stderr。
//   - 只用 Foundation / CryptoKit / LocalAuthentication / Security。
import CryptoKit
import Foundation
import LocalAuthentication
import Security

let version = 1
let maxSecret = 8192

struct Fail: Error {
    let code: String
    let rc: Int32
    init(_ code: String, _ rc: Int32 = 2) { self.code = code; self.rc = rc }
}

func usage() -> String {
    return """
    usage: aib-sehelper seal      --name NAME --dir DIR [--reason TEXT]   (secret on stdin)
           aib-sehelper unseal    --name NAME --dir DIR --reason TEXT     (Touch ID; secret on stdout)
           aib-sehelper se-create --name NAME --dir DIR                   (Secure Enclave signing key; public key on stdout)
           aib-sehelper se-sign   --name NAME --dir DIR --reason TEXT     (Touch ID; data on stdin, base64 DER signature on stdout)
           aib-sehelper grant-create --name NAME --dir DIR                (grant signing key; Touch ID or login password to sign)
           aib-sehelper se-pub    --name NAME --dir DIR                   (public key recomputed from the Secure Enclave key)
           aib-sehelper se-verify --name NAME --dir DIR --sig B64         (data on stdin; verify with the recomputed public key)
    Keys never leave the Secure Enclave and every signature needs the owner (biometrics; grant keys: Touch ID or login
    password). There is no way to skip it.

    """
}

// se-sign 的視窗字是要簽的原話本身(aib quote):截掉 = 本人沒看全就簽 ⇒ 超過這個長度直接拒,不截(@abb89 10-21 Q3)
let maxSignReason = 320
// unseal 的視窗字(cf grant:zone、權限、主機、⚠ 高風險權限):截掉 = 警告可能被推出視窗 ⇒ 同樣超長拒、不截(@aeb40 #404 R1)
let maxUnsealReason = 200

func cleanReason(_ s: String, limit: Int = 200) -> String {
    let t = cleanFull(s)
    return t.count > limit ? String(t.prefix(limit)) : t
}

func cleanFull(_ s: String) -> String {
    var out = ""
    for u in s.unicodeScalars {
        let v = u.value
        if v < 0x20 || v == 0x7f || (0x200b...0x200f).contains(v) || (0x202a...0x202e).contains(v)
            || (0x2060...0x2069).contains(v) || v == 0xfeff {
            out.append(" ")
        } else {
            out.unicodeScalars.append(u)
        }
    }
    return out.trimmingCharacters(in: .whitespaces)
}

func parseArgs(_ argv: [String]) throws -> (String, [String: String]) {
    guard let sub = argv.first else { throw Fail("usage") }
    var opts: [String: String] = [:]
    var i = 1
    while i < argv.count {
        let k = argv[i]
        guard ["--name", "--dir", "--reason", "--sig"].contains(k), i + 1 < argv.count else { throw Fail("bad_arg") }
        opts[String(k.dropFirst(2))] = argv[i + 1]
        i += 2
    }
    guard let name = opts["name"], name.range(of: "^[A-Z][A-Z0-9_]{1,63}$", options: .regularExpression) != nil else {
        throw Fail("bad_name")
    }
    guard let dir = opts["dir"], dir.hasPrefix("/"), !dir.contains("/../"), !dir.hasSuffix("/..") else {
        throw Fail("bad_dir")
    }
    return (sub, opts)
}

func accessControl(presence: Bool = false) throws -> SecAccessControl {
    var err: Unmanaged<CFError>?
    // presence = 授權單簽單鑰(Touch ID 或登入密碼);其他照舊 biometryCurrentSet
    let flags: SecAccessControlCreateFlags = presence ? [.privateKeyUsage, .userPresence] : [.privateKeyUsage, .biometryCurrentSet]
    guard let ac = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, flags, &err) else {
        throw Fail("access_control")
    }
    return ac
}

func context(_ reason: String, limit: Int = 200) -> LAContext {
    let ctx = LAContext()
    ctx.localizedReason = cleanReason(reason, limit: limit)
    ctx.touchIDAuthenticationAllowableReuseDuration = 0      // 每次都要按,不沿用剛剛那次
    return ctx
}

func mapError(_ e: Error) -> Fail {
    if let f = e as? Fail { return f }
    let ns = e as NSError
    if ns.domain == LAError.errorDomain {
        switch ns.code {
        case LAError.userCancel.rawValue, LAError.appCancel.rawValue, LAError.systemCancel.rawValue,
             LAError.userFallback.rawValue:
            return Fail("cancel", 3)
        case LAError.biometryNotAvailable.rawValue, LAError.biometryNotEnrolled.rawValue,
             LAError.biometryLockout.rawValue, LAError.passcodeNotSet.rawValue:
            return Fail("no_biometry", 3)
        default:
            return Fail("auth_failed", 3)
        }
    }
    // SE 把 LocalAuthentication 的取消包在 CryptoKit / Security 錯誤裡;訊息含 -128 / cancel 就當取消
    let text = String(describing: e).lowercased()
    if text.contains("cancel") || text.contains("-128") { return Fail("cancel", 3) }
    if text.contains("authentication") { return Fail("auth_failed", 3) }
    return Fail("se_error", 4)
}

func secureDir(_ dir: String) throws {
    let fm = FileManager.default
    if !fm.fileExists(atPath: dir) {
        try fm.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir)
}

func writePrivate(_ path: String, _ data: Data) throws {
    let tmp = path + ".tmp-\(getpid())"
    guard FileManager.default.createFile(atPath: tmp, contents: data, attributes: [.posixPermissions: 0o600]) else {
        throw Fail("write_failed")
    }
    if rename(tmp, path) != 0 {
        unlink(tmp)
        throw Fail("write_failed")
    }
}

func readJSON(_ path: String) throws -> [String: Any] {
    guard FileManager.default.fileExists(atPath: path) else { throw Fail("not_found") }
    guard let d = FileManager.default.contents(atPath: path),
          let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { throw Fail("corrupt") }
    return obj
}

func b64(_ obj: [String: Any], _ key: String) throws -> Data {
    guard let s = obj[key] as? String, let d = Data(base64Encoded: s) else { throw Fail("corrupt") }
    return d
}

func info(_ name: String) -> Data { return Data("aib-sehelper v\(version) \(name)".utf8) }

func seal(_ name: String, _ dir: String) throws {
    let secret = FileHandle.standardInput.readDataToEndOfFile()
    guard !secret.isEmpty, secret.count <= maxSecret else { throw Fail("bad_secret") }
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    try secureDir(dir)
    // SE 私鑰(用的時候要指紋);封的時候只用它的公鑰 ⇒ 封不需要指紋,解才需要
    let se = try SecureEnclave.P256.KeyAgreement.PrivateKey(accessControl: try accessControl())
    let eph = P256.KeyAgreement.PrivateKey()
    let shared = try eph.sharedSecretFromKeyAgreement(with: se.publicKey)
    var salt = Data(count: 32)
    guard salt.withUnsafeMutableBytes({ SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }) == errSecSuccess else {
        throw Fail("random")
    }
    let key = shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: salt, sharedInfo: info(name), outputByteCount: 32)
    let box = try AES.GCM.seal(secret, using: key, authenticating: info(name))
    let doc: [String: Any] = [
        "v": version, "name": name, "kind": "sealed",
        "se_key": se.dataRepresentation.base64EncodedString(),
        "eph_pub": eph.publicKey.x963Representation.base64EncodedString(),
        "salt": salt.base64EncodedString(),
        "box": box.combined!.base64EncodedString(),
        "created": ISO8601DateFormatter().string(from: Date()),
    ]
    try writePrivate(dir + "/" + name + ".sealed", try JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]))
    let fp = SHA256.hash(data: secret).map { String(format: "%02x", $0) }.joined().prefix(8)
    print("{\"ok\": true, \"name\": \"\(name)\", \"fingerprint\": \"\(fp)\"}")
}

func unseal(_ name: String, _ dir: String, _ reason: String) throws {
    let doc = try readJSON(dir + "/" + name + ".sealed")
    guard (doc["v"] as? Int) == version, (doc["name"] as? String) == name, (doc["kind"] as? String) == "sealed" else {
        throw Fail("corrupt")
    }
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    let se = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: try b64(doc, "se_key"),
                                                             authenticationContext: context(reason, limit: maxUnsealReason))
    let eph = try P256.KeyAgreement.PublicKey(x963Representation: try b64(doc, "eph_pub"))
    let shared = try se.sharedSecretFromKeyAgreement(with: eph)        // ← 這裡 SE 要求生物辨識
    let key = shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: try b64(doc, "salt"), sharedInfo: info(name),
                                             outputByteCount: 32)
    let plain: Data
    do {
        plain = try AES.GCM.open(try AES.GCM.SealedBox(combined: try b64(doc, "box")), using: key, authenticating: info(name))
    } catch {
        throw Fail("corrupt")
    }
    FileHandle.standardOutput.write(plain)
}

func seCreate(_ name: String, _ dir: String, kind: String = "sekey") throws {
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    try secureDir(dir)
    let path = dir + "/" + name + ".sekey"
    if FileManager.default.fileExists(atPath: path) { throw Fail("exists") }      // 不覆蓋:換鑰要本人先移走舊的
    let k = try SecureEnclave.P256.Signing.PrivateKey(accessControl: try accessControl(presence: kind == "grantkey"))
    let doc: [String: Any] = ["v": version, "name": name, "kind": kind,
                              "se_key": k.dataRepresentation.base64EncodedString(),
                              "public_key": k.publicKey.x963Representation.base64EncodedString(),
                              "created": ISO8601DateFormatter().string(from: Date())]
    try writePrivate(path, try JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]))
    print("{\"ok\": true, \"name\": \"\(name)\", \"public_key\": \"\(k.publicKey.x963Representation.base64EncodedString())\"}")
}

let grantKeyName = "GRANT_KEY"
let grantMaxWindow = 7 * 3600                  // = approve_grants.WINDOW_CAP_S(日誌涵蓋得到的範圍)
let grantDurations = ["15m": 900, "30m": 1800, "1h": 3600, "3h": 3 * 3600, "24h": 24 * 3600]   // = approve_grants.DURATIONS

// kern.boottime 的秒數(= approve_grants.boot_epoch);量不到 ⇒ nil(不簽)
func bootEpoch() -> Int64? {
    var tv = timeval()
    var size = MemoryLayout<timeval>.stride
    guard sysctlbyname("kern.boottime", &tv, &size, nil, 0) == 0, tv.tv_sec > 0 else { return nil }
    return Int64(tv.tv_sec)
}

// GRANT_KEY ⇔ kind grantkey(userPresence);其他名字只收 biometryCurrentSet 的 sekey(不讓別的鑰降級成可用密碼簽)
func loadKeyDoc(_ name: String, _ dir: String) throws -> [String: Any] {
    let doc = try readJSON(dir + "/" + name + ".sekey")
    let kind = name == grantKeyName ? "grantkey" : "sekey"
    guard (doc["v"] as? Int) == version, (doc["name"] as? String) == name, (doc["kind"] as? String) == kind else {
        throw Fail("corrupt")
    }
    return doc
}

// 這把鑰一定要本人:不准互動的 LAContext 試簽兩次,都只認 LAError.notInteractive;簽得出來 ⇒ weak_key。
// 第二次帶 32 位元組隨機 applicationPassword(#419 R2-M1 實機:applicationPassword 鑰第一次也回 -1004,
// 帶隨機密碼回 CryptoTokenKit -3 ⇒ 擋下;真 userPresence 鑰兩次都 -1004)。只在 10-29 這台 macOS 實測,其他 ACL 組合沒窮舉。
func requireOwnerACL(_ doc: [String: Any]) throws {
    try probeOnce(doc, password: nil)
    var pw = Data(count: 32)
    let rc = pw.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
    guard rc == errSecSuccess else { throw Fail("key_probe_failed", 6) }
    try probeOnce(doc, password: pw)
}

func probeOnce(_ doc: [String: Any], password: Data?) throws {
    let ctx = LAContext()
    ctx.interactionNotAllowed = true
    if let pw = password, !ctx.setCredential(pw, type: .applicationPassword) { throw Fail("key_probe_failed", 6) }
    do {
        let k = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: try b64(doc, "se_key"), authenticationContext: ctx)
        _ = try k.signature(for: Data("aib-sehelper owner-acl probe".utf8))
    } catch {
        let ns = error as NSError
        if ns.domain == LAError.errorDomain && ns.code == LAError.notInteractive.rawValue { return }
        throw Fail("key_probe_failed", 6)
    }
    throw Fail("weak_key", 6)
}

// 公鑰一律從 SE 鑰本體重算(dataRepresentation 只有這台 SE 解得開);檔裡的 public_key 欄位不採信
func sePublicKey(_ doc: [String: Any]) throws -> P256.Signing.PublicKey {
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    try requireOwnerACL(doc)
    return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: try b64(doc, "se_key")).publicKey
}

func sePub(_ name: String, _ dir: String) throws {
    let pub = try sePublicKey(try loadKeyDoc(name, dir))
    // owner_acl = probed:這版 helper 探過 ACL(Python 沒看到這欄 = 舊 helper ⇒ 拒;#419 R2-L1)
    print("{\"ok\": true, \"name\": \"\(name)\", \"owner_acl\": \"probed\", \"public_key\": \"\(pub.x963Representation.base64EncodedString())\"}")
}

func seVerify(_ name: String, _ dir: String, _ sigB64: String) throws {
    let pub = try sePublicKey(try loadKeyDoc(name, dir))
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 65536 else { throw Fail("bad_data") }
    guard let der = Data(base64Encoded: sigB64), let sig = try? P256.Signing.ECDSASignature(derRepresentation: der) else {
        throw Fail("bad_sig", 5)
    }
    guard pub.isValidSignature(sig, for: data) else { throw Fail("bad_sig", 5) }
    print("{\"ok\": true, \"owner_acl\": \"probed\"}")
}

// lockwatch(TID-2c,D3):長駐(launchd KeepAlive)。螢幕鎖定 ⇒ 立刻寫 last_lock;每 30 秒寫一次心跳。
// 只寫 D/lockwatch.json(600)。是本機檔 ⇒ 只防誤操作;Python 端心跳過期 / 讀不到一律當鎖過(@aeb40 Q2)。
func lockwatch(_ dir: String) throws {
    try secureDir(dir)
    let path = dir + "/lockwatch.json"
    let iso = ISO8601DateFormatter()
    var lastLock = iso.string(from: Date())          // 剛啟動 = 當作剛鎖過(開機 / 重啟前的單一律不算)
    func write() {
        let doc: [String: Any] = ["v": version, "last_lock_utc": lastLock, "beat_utc": iso.string(from: Date()),
                                  "pid": Int(getpid())]
        if let d = try? JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]) { try? writePrivate(path, d) }
    }
    write()
    let center = DistributedNotificationCenter.default()
    for n in ["com.apple.screenIsLocked", "com.apple.screensaver.didstart"] {
        center.addObserver(forName: Notification.Name(n), object: nil, queue: .main) { _ in
            lastLock = iso.string(from: Date())
            write()
        }
    }
    Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in write() }
    RunLoop.main.run()
}

func seSign(_ name: String, _ dir: String, _ reason: String) throws {
    let doc = try loadKeyDoc(name, dir)
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 65536 else { throw Fail("bad_data") }
    if name == grantKeyName {
        // 授權單:視窗字的雜湊要等於單上的 text_sha256(直接叫 helper 自帶無害字 ⇒ 不簽;@aeb40 #419 L1 加固)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let want = obj["text_sha256"] as? String, cleanFull(reason) == reason else { throw Fail("reason_mismatch") }
        let got = SHA256.hash(data: Data(reason.utf8)).map { String(format: "%02x", $0) }.joined()
        guard got == want else { throw Fail("reason_mismatch") }
        // #441 R2 H1:簽發時間 / 期限 / 開機識別由 helper 自己產生、簽進 stamp(系統時鐘 + kern.boottime,同帳號改不了);
        // 呼叫端給的一律不收 ⇒ 「簽 → 鎖屏 → 假起算 → 用」做不出來(issued = 這一刻,彈指紋之前)。
        guard obj["issued_utc"] == nil, obj["expires_utc"] == nil, obj["boot"] == nil else { throw Fail("bad_time") }
        guard let dur = obj["dur"] as? String, let secs = grantDurations[dur], secs > 0, secs <= grantMaxWindow
        else { throw Fail("bad_time") }
        guard let boot = bootEpoch() else { throw Fail("bad_time") }
        let issued = Int64(Date().timeIntervalSince1970)
        let stamp = "\naib-grant-stamp v1 issued=\(issued) expires=\(issued + Int64(secs)) boot=\(boot)"
        guard SecureEnclave.isAvailable else { throw Fail("no_se") }
        let k = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: try b64(doc, "se_key"),
                                                          authenticationContext: context(reason, limit: maxSignReason))
        let sig = try k.signature(for: data + Data(stamp.utf8))       // ← 這裡 SE 要求生物辨識
        let out: [String: Any] = ["sig": sig.derRepresentation.base64EncodedString(), "issued": issued,
                                  "expires": issued + Int64(secs), "boot": boot]
        let js = try JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
        print(String(decoding: js, as: UTF8.self))
        return
    }
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    let k = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: try b64(doc, "se_key"),
                                                      authenticationContext: context(reason, limit: maxSignReason))
    let sig = try k.signature(for: data)                               // ← 這裡 SE 要求生物辨識
    print(sig.derRepresentation.base64EncodedString())
}

@main
struct Main {
    static func main() {
        let argv = Array(CommandLine.arguments.dropFirst())
        if argv.isEmpty || ["-h", "--help", "help"].contains(argv[0]) {
            FileHandle.standardOutput.write(Data(usage().utf8))
            exit(argv.isEmpty ? 2 : 0)
        }
        do {
            let (sub, o) = try parseArgs(argv)
            let name = o["name"]!, dir = o["dir"]!, reason = o["reason"] ?? ""
            switch sub {
            case "seal": try seal(name, dir)
            case "unseal":
                guard !cleanFull(reason).isEmpty else { throw Fail("no_reason") }
                guard cleanFull(reason).count <= maxUnsealReason else { throw Fail("reason_too_long") }
                try unseal(name, dir, reason)
            case "se-create": try seCreate(name, dir)
            case "grant-create":
                guard name == grantKeyName else { throw Fail("bad_name") }
                try seCreate(name, dir, kind: "grantkey")
            case "se-pub": try sePub(name, dir)
            case "lockwatch":
                guard name == grantKeyName else { throw Fail("bad_name") }
                try lockwatch(dir)
            case "se-verify":
                guard let sig = o["sig"], !sig.isEmpty else { throw Fail("bad_arg") }
                try seVerify(name, dir, sig)
            case "se-sign":
                guard !cleanFull(reason).isEmpty else { throw Fail("no_reason") }
                guard cleanFull(reason).count <= maxSignReason else { throw Fail("reason_too_long") }
                try seSign(name, dir, reason)
            default: throw Fail("usage")
            }
            exit(0)
        } catch {
            let f = mapError(error)
            FileHandle.standardError.write(Data((f.code + "\n").utf8))
            exit(f.rc)
        }
    }
}
