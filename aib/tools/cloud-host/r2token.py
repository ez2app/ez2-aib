#!/usr/bin/env python3
"""r2token.py — 在 EU01 用那裡的 Cloudflare 憑證開一把「只限一個 R2 bucket、只從一台主機 IP 用」的 token,
直接送進目的主機的 root 0600 檔;值不印、不落 Mac 磁碟、不進金鑰櫃(伺服器專用)。

用法:
  r2token.py mint <bucket> --host <目的主機 ssh 別名> --ip <目的主機出口 IP> [--creds-host eu01-ts]
             [--zone ez2.work] [--dest /etc/ez2-cloud/backup-r2.env] [--remote-name r2] [--apply]
  沒 --apply = 只印計畫(帳號、bucket、權限、IP、目的檔),不彈窗、不打 API。
  --apply:approve_gate「cloud_r2token.mint」彈窗 + Touch ID(Andy 11-09:「key 從 eu01 取得,再彈出生物辨識」)
          → ssh <creds-host> 讀 /etc/ez2-cloud/cloudflare.env 打 Cloudflare API 建 token
          → stdout(JSON)直接接到 ssh <host> 寫 dest(0600)+ rclone.conf [remote-name]
          → 在目的主機驗:能列自己的 bucket、寫得進、刪不掉(bucket lock)、碰不到別的 bucket。
  值只經兩條 ssh 之間的管線(Mac 記憶體),這支程式不讀它、不印它。

權限:Workers R2 Storage Bucket Item Read + Item Write,資源只有那個 bucket;IP 條件只那台主機。
S3 憑證:access_key_id = token id;secret = sha256(token 值) hex(Cloudflare R2 規則)。

限制(同 robot.py):同帳號的程式能注入 approve_fn / 假 ssh,這支只擋「人沒按就不開」,不擋同帳號 AI。
"""
import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "..", "..", "src", "cockpit"), os.path.join(_HERE, "..", "..", "src", "bootstrap"),
           os.path.join(_HERE, "..", "..", "src", "i18n"), os.path.join(_HERE, "..", "..", "src")):
    _p = os.path.abspath(_p)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

PERM_ITEM_READ = "6a018a9f2fc74eb6b293b0c548f38b39"    # Workers R2 Storage Bucket Item Read
PERM_ITEM_WRITE = "2efd5506f9c8494dacb1fa10a3e7d5b6"   # Workers R2 Storage Bucket Item Write
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]

# 在憑證主機上跑:讀 cloudflare.env → 查帳號 → 建 token → 印 JSON(只印 S3 憑證,不印原 token 值)
MINT_REMOTE = r'''
import json, os, sys, hashlib, urllib.request
env = {}
for line in open("/etc/ez2-cloud/cloudflare.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"')
H = {"X-Auth-Email": env["CF_AUTH_EMAIL"], "X-Auth-Key": env["CF_GLOBAL_KEY"], "Content-Type": "application/json"}
def api(method, path, data=None):
    req = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, method=method, headers=H,
                                 data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
spec = json.loads(sys.argv[1])
z = api("GET", "/zones?name=" + spec["zone"])["result"]
if not z: print(json.dumps({"ok": False, "why": "zone_not_found"})); sys.exit(1)
acct = z[0]["account"]["id"]
body = {"name": spec["name"],
        "policies": [{"effect": "allow",
                      "resources": {"com.cloudflare.edge.r2.bucket.%s_default_%s" % (acct, spec["bucket"]): "*"},
                      "permission_groups": [{"id": spec["perm_read"]}, {"id": spec["perm_write"]}]}],
        "condition": {"request_ip": {"in": [spec["ip"] + "/32"]}}}
r = api("POST", "/accounts/%s/tokens" % acct, body)
if not r.get("success"): print(json.dumps({"ok": False, "why": "create_failed", "errors": r.get("errors")})); sys.exit(1)
tok = r["result"]
print(json.dumps({"ok": True, "token_id": tok["id"], "name": tok["name"],
                  "access_key_id": tok["id"], "secret_access_key": hashlib.sha256(tok["value"].encode()).hexdigest(),
                  "endpoint": "https://%s.r2.cloudflarestorage.com" % acct, "bucket": spec["bucket"], "ip": spec["ip"]}))
'''

# 在目的主機上跑:stdin JSON → dest(0600)+ rclone.conf 段 → 驗(列 / 寫 / 刪不掉 / 別的 bucket 碰不到);只印結果
INSTALL_REMOTE = r'''
import json, os, sys, subprocess, configparser, time
d = json.load(sys.stdin)
if not d.get("ok"): print(json.dumps({"ok": False, "why": d.get("why"), "errors": d.get("errors")})); sys.exit(1)
dest, remote = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(dest), exist_ok=True)
fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("# R2 token %s(只限 bucket %s、只從 %s;建於 %s)\n" % (d["name"], d["bucket"], d["ip"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    f.write("R2_TOKEN_ID=%s\nR2_ENDPOINT=%s\nR2_BUCKET=%s\nAWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n"
            % (d["token_id"], d["endpoint"], d["bucket"], d["access_key_id"], d["secret_access_key"]))
os.chmod(dest, 0o600)
cfgp = os.path.expanduser("~/.config/rclone/rclone.conf"); os.makedirs(os.path.dirname(cfgp), exist_ok=True, mode=0o700)
cp = configparser.RawConfigParser(); cp.read(cfgp)
if not cp.has_section(remote): cp.add_section(remote)
for k, v in (("type", "s3"), ("provider", "Cloudflare"), ("access_key_id", d["access_key_id"]),
             ("secret_access_key", d["secret_access_key"]), ("endpoint", d["endpoint"]), ("acl", "private"), ("no_check_bucket", "true")):
    cp.set(remote, k, v)
fd = os.open(cfgp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: cp.write(f)
def rc(*a):
    # token 的 IP 條件是 IPv4 /32,主機若預設走 IPv6 會 403 ⇒ 一律 --bind 那個 IP(11-09 EU02 實測)
    p = subprocess.run(["rclone", "--bind", d["ip"], "--retries", "3", *a], capture_output=True, text=True, timeout=120)
    txt = (p.stderr or p.stdout).strip()
    return p.returncode, txt
b = d["bucket"]; probe = "%s:%s/_probe/r2token-%d.txt" % (remote, b, int(time.time()))
res = {"ok": True, "dest": dest, "remote": remote, "token_id": d["token_id"], "ip": d["ip"], "bind": d["ip"]}
res["list_own_bucket"] = rc("lsd", "%s:%s" % (remote, b))[0] == 0
open("/tmp/.r2probe", "w").write("probe\n")
res["write"] = rc("copyto", "/tmp/.r2probe", probe)[0] == 0
code, txt = rc("cat", probe); res["read_back"] = code == 0 and txt.strip() == "probe"
code, txt = rc("deletefile", probe)
res["delete_blocked_by_lock"] = ("status code: 409" in txt) or ("status code: 403" in txt)   # bucket lock 擋刪 = 409
res["delete_msg"] = txt.splitlines()[-1][:160] if txt else ("deleted(rc=%d)" % code)
code, txt = rc("lsd", "%s:ez2-media" % remote); res["other_bucket_denied"] = code != 0 and "403" in txt
res["ok"] = all(res[k] for k in ("list_own_bucket", "write", "read_back", "delete_blocked_by_lock", "other_bucket_denied"))
os.remove("/tmp/.r2probe")
print(json.dumps(res))
'''


def plan(a):
    return {"bucket": a.bucket, "host": a.host, "ip": a.ip, "creds_host": a.creds_host, "zone": a.zone, "dest": a.dest,
            "remote_name": a.remote_name, "name": a.name, "perms": ["R2 Storage Bucket Item Read", "R2 Storage Bucket Item Write"],
            "ip_condition": a.ip + "/32"}


def approve(a, env=None, platform=None, touchid=None, touchid_available=None):
    """彈窗 + Touch ID。回 None = 過;否則原因代碼(不打 API、不送)。"""
    import approve_gate as AG
    plat = platform or sys.platform
    off = AG.ready(env, plat, touchid_available)
    if off:
        return "touchid_" + off
    reason = AG.reason("cloud_r2token.mint", {"bucket": a.bucket, "host": a.host, "ip": a.ip})
    import seats_dir_lib
    root = seats_dir_lib.state_root(env)[0]
    res = AG.ask(reason, root, touchid)
    return None if res["ok"] else "touchid_" + res["why"].split(":")[0]


def mint(a, runner=None):
    """ssh 憑證主機建 token → 管線 → ssh 目的主機安裝 + 驗。runner 只給測試注入。回 (rc, dict)。"""
    spec = json.dumps({"zone": a.zone, "bucket": a.bucket, "ip": a.ip, "name": a.name,
                       "perm_read": PERM_ITEM_READ, "perm_write": PERM_ITEM_WRITE})
    c1 = ["ssh", *SSH_OPTS, a.creds_host, "python3", "-c", _q(MINT_REMOTE), _q(spec)]
    c2 = ["ssh", *SSH_OPTS, a.host, "python3", "-c", _q(INSTALL_REMOTE), _q(a.dest), _q(a.remote_name)]
    if runner:
        return runner(c1, c2)
    p1 = subprocess.Popen(c1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(c2, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p1.stdout.close()                      # 值只在 p1 → p2 的管線;本程式不讀
    out, err2 = p2.communicate(timeout=300)
    p1.wait(timeout=10); err1 = p1.stderr.read().decode(errors="replace")
    try:
        res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"ok": False, "why": "no_output"}
    except json.JSONDecodeError:
        res = {"ok": False, "why": "bad_output"}
    if p1.returncode != 0 and res.get("ok"):
        res = {"ok": False, "why": "creds_host_rc_%d" % p1.returncode}
    res["stderr_creds_host"] = err1.strip()[-300:]
    res["stderr_dest_host"] = (err2 or "").strip()[-300:]
    return (0 if res.get("ok") else 1), res


def _q(s):
    import shlex
    return shlex.quote(s)


def main(argv=None, approve_fn=None, runner=None, out=print):
    ap = argparse.ArgumentParser(prog="r2token.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("bucket"); m.add_argument("--host", required=True); m.add_argument("--ip", required=True)
    m.add_argument("--creds-host", default="eu01-ts"); m.add_argument("--zone", default="ez2.work")
    m.add_argument("--dest", default="/etc/ez2-cloud/backup-r2.env"); m.add_argument("--remote-name", default="r2")
    m.add_argument("--name", default=None); m.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    a.name = a.name or "ez2-cloud-%s-%s-r2" % (a.host.replace("-ts", ""), a.bucket)
    if not a.apply:
        out(json.dumps({"plan": plan(a), "apply": False}, ensure_ascii=False)); return 0
    why = (approve_fn or approve)(a)
    if why:
        out(json.dumps({"ok": False, "why": why, "plan": plan(a)}, ensure_ascii=False)); return 2
    rc, res = mint(a, runner)
    out(json.dumps(res, ensure_ascii=False)); return rc


if __name__ == "__main__":
    sys.exit(main())
