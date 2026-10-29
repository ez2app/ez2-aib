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
//
// 規矩:
//   - 沒有任何略過生物辨識的旗標;accessControl 固定 [.privateKeyUsage, .biometryCurrentSet](換了指紋就失效,要重封)。
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
    Keys never leave the Secure Enclave and every use needs biometrics (biometryCurrentSet). There is no way to skip it.

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
        guard ["--name", "--dir", "--reason"].contains(k), i + 1 < argv.count else { throw Fail("bad_arg") }
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

func accessControl() throws -> SecAccessControl {
    var err: Unmanaged<CFError>?
    guard let ac = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                                                   [.privateKeyUsage, .biometryCurrentSet], &err) else {
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

func seCreate(_ name: String, _ dir: String) throws {
    guard SecureEnclave.isAvailable else { throw Fail("no_se") }
    try secureDir(dir)
    let path = dir + "/" + name + ".sekey"
    if FileManager.default.fileExists(atPath: path) { throw Fail("exists") }      // 不覆蓋:換鑰要本人先移走舊的
    let k = try SecureEnclave.P256.Signing.PrivateKey(accessControl: try accessControl())
    let doc: [String: Any] = ["v": version, "name": name, "kind": "sekey",
                              "se_key": k.dataRepresentation.base64EncodedString(),
                              "public_key": k.publicKey.x963Representation.base64EncodedString(),
                              "created": ISO8601DateFormatter().string(from: Date())]
    try writePrivate(path, try JSONSerialization.data(withJSONObject: doc, options: [.sortedKeys]))
    print("{\"ok\": true, \"name\": \"\(name)\", \"public_key\": \"\(k.publicKey.x963Representation.base64EncodedString())\"}")
}

func seSign(_ name: String, _ dir: String, _ reason: String) throws {
    let doc = try readJSON(dir + "/" + name + ".sekey")
    guard (doc["v"] as? Int) == version, (doc["name"] as? String) == name, (doc["kind"] as? String) == "sekey" else {
        throw Fail("corrupt")
    }
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 65536 else { throw Fail("bad_data") }
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
