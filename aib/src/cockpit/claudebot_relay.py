#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ClaudeBot 雲端橋 v1・本機中繼(v0.70.0 #16;the maintainer 2026-09-27 裁「先做本機中繼」)。

把今天本機版已在用的 claudebot 席位信箱(bridge/<席>/inbox|outbox)套一層 HTTP 面,給雲端 session 的 hook 用。
不是第二套信箱:資料還是同一個目錄,本機 hook 讀檔、雲端 hook 走 HTTP,席端一行不改。

對外靠 Tailscale Funnel / Cloudflare Tunnel 把 127.0.0.1:8787 端到公網 ⇒ 任何人都打得到 ⇒
  一把中繼通行碼(X-AIB-Token,只放雲端環境變數,絕不進倉庫)、認碼不認 IP、失敗計數、限速、
  健康入口不洩服務名、只記次數不記內容(記憶 claudebot-cloud-bridge-design-checklist-0927 第 2/3/10 條)。

入口(全部 JSON):
  GET  /v1/health                                  → {"ok": true}
  POST /v1/sessions/<sid>/register  {cwd,branch}   → 雲端 session 報到(給開席選單列);回 {"bound": bool}
  GET  /v1/sessions/<sid>/inbox?wait=N             → 長輪詢最多 N 秒(上限 25);有信就取走(同 pull),回 rendered
  POST /v1/sessions/<sid>/outbox    {text,role,source} → 雲端回覆 / the maintainer 在 app 打的字,寫進席的 outbox
  GET  /v1/sessions/<sid>/inbox?wait=N&ack=1        → 帶 ack=1 才記 claim(r8 G1);回 {"claim": id}
  POST /v1/sessions/<sid>/ack       {claim}            → 收到 inbox 那批信了;沒 ack 的 ACK_TIMEOUT_S 後退回 inbox 重送一次(r7 F2/r8 G1),claims 落檔重啟不丟(G3)
  GET  /v1/health?nonce=X                           → {"ok":true,"proof":HMAC(碼,X)}:up 開 Funnel 前證明埠上是自己的中繼(r7 F1)
沒綁席 ⇒ 404 {"bound": false}(hook 據此放行零輸出);碼錯 ⇒ 401;太頻繁 ⇒ 429;太大 ⇒ 413。
★ 單執行緒會掉信(09-27 探針:Stop 與門鈴同時打,一個 0 bytes)⇒ ThreadingHTTPServer。
"""
from __future__ import annotations

import argparse
import hmac
import math
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claudebot_mailbox as cm  # noqa: E402
import claudebot_threads  # noqa: E402

ENV_URL = "AIB_CLAUDEBOT_RELAY_URL"       # 雲端環境變數:https://<funnel 主機>
ENV_TOKEN = "AIB_CLAUDEBOT_RELAY_TOKEN"   # 雲端環境變數:中繼通行碼(不進倉庫)
HEADER_TOKEN = "X-AIB-Token"
DEFAULT_PORT = 8787
DEFAULT_BIND = "127.0.0.1"
LONG_POLL_MAX_S = 25          # UserPromptSubmit 30 秒上限 ⇒ 長輪詢最多 25
POLL_STEP_S = 0.5
MAX_BODY = 256 * 1024
RATE_PER_SEC = 10.0           # 每把碼每秒 10 次、桶 30
RATE_BURST = 30.0
AUTH_FAIL_LIMIT = 20          # 同一來源連續碼錯 20 次 ⇒ 之後一律 429(五分鐘)
AUTH_FAIL_WINDOW_S = 300
AUTH_FAIL_TABLE_MAX = 1024    # 失敗表最多記幾個來源(r1 M1:公網換來源可無限塞);滿了丟最舊的
TRUSTED_PROXY_PEERS = ("127.0.0.1", "::1")   # 只有本機代理(tailscale funnel/serve)進來的連線才信 X-Forwarded-For
HANDLER_TIMEOUT_S = 30        # 每條連線讀寫逾時(r1 M2:沒逾時 ⇒ 慢連線永久佔執行緒);長輪詢 25 秒在它之內
REG_TTL_S = 24 * 3600
HEALTH_TIMEOUT_S = 2.0
ACK_TIMEOUT_S = float(os.environ.get("AIB_CLAUDEBOT_RELAY_ACK_S") or 60)   # r7 F2:取走的信沒在這時間內 ack 就退回 inbox 重送
NONCE_MAX = 64
REQUEUED_KEEP_S = 7 * 86400   # r9 H2:退回名單留 7 天
UP_WAIT_S = 6.0               # up 起中繼後等 health 的上限


def state_root(base=None) -> Path:
    return claudebot_threads._state_root(base)


def relay_dir(base=None) -> Path:
    d = state_root(base) / "relay"
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path(base=None) -> Path:
    return relay_dir(base) / "relay-token"


def ensure_token(base=None) -> str:
    """中繼通行碼:一台 Mac 一把,第一次生成,0600。印給 the maintainer 貼進雲端環境變數用。"""
    p = token_path(base)
    try:
        t = p.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(32)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(t + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return t


def hits_log(base=None) -> Path:
    return relay_dir(base) / "relay-hits.log"


def cloud_sessions_dir(base=None) -> Path:
    d = relay_dir(base) / "cloud-sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_cloud_session(sid: str, info: dict, base=None) -> Path:
    p = cloud_sessions_dir(base) / (sid + ".json")
    now = int(time.time())
    old = {}
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    rec = {"session_id": sid, "first_seen": int(old.get("first_seen") or now), "last_seen": now,
           "cwd": str(info.get("cwd") or "")[:200], "branch": str(info.get("branch") or "")[:120],
           "remote_env": str(info.get("remote_env") or "")[:60]}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def list_cloud_sessions(base=None, ttl_s: int = REG_TTL_S) -> list[dict]:
    """給開席選單:最近 ttl 內報到過的雲端 session,新的在前。"""
    out = []
    now = time.time()
    for p in cloud_sessions_dir(base).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if now - float(d.get("last_seen") or 0) <= ttl_s:
            out.append(d)
    out.sort(key=lambda d: -float(d.get("last_seen") or 0))
    return out


def pid_path(base=None) -> Path:
    return relay_dir(base) / "relay.pid"


def out_path(base=None) -> Path:
    return relay_dir(base) / "relay.out"


def _health_get(port: int, timeout: float, nonce: str = "") -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/health%s" % (port, ("?nonce=" + nonce) if nonce else ""), timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8") or "{}")
            return obj if r.status == 200 and isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001 — 連不上就是沒在跑
        return {}


def health(port: int, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """127.0.0.1:port 的 /v1/health 回 {"ok": true} 才算在跑(不看 pid 檔:pid 檔會過期,health 不會)。"""
    return bool(_health_get(port, timeout).get("ok"))


def proof_for(token: str, nonce: str) -> str:
    return hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), "sha256").hexdigest()


def is_ours(port: int, base=None, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """r7 F1:health 回 ok 只證明「有東西在聽」;開 Funnel 前要證明那是**這把通行碼的中繼**——
    帶 nonce 問 health,只有拿著同一把碼的中繼答得出 HMAC 證明。沒鑰匙檔就答不出 ⇒ 不是我們的。"""
    tok = read_token(base)
    if not tok:
        return False
    nonce = secrets.token_hex(8)
    got = str(_health_get(port, timeout, nonce).get("proof") or "")
    return bool(got) and hmac.compare_digest(got, proof_for(tok, nonce))


def _run(argv: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    """跑外部命令(tailscale);找不到/逾時 ⇒ (-1, "", "")。永不拋。
    stdout 與 stderr 分開回:tailscale 版本不合會在 stderr 印 Warning,混進 stdout 會把 JSON 弄壞(真機 09-27 18:5x 抓到)。"""
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception:  # noqa: BLE001
        return -1, "", ""


def tailscale_dns_name() -> str:
    """這台在 tailnet 的名字(例 macbook-pro.tailxxxx.ts.net);沒 tailscale / 沒登入 ⇒ ""。"""
    if os.environ.get("AIB_CLAUDEBOT_RELAY_NO_TAILSCALE"):
        return ""
    rc, out, _ = _run(["tailscale", "status", "--json"], timeout=4)
    if rc != 0:
        return ""
    try:
        return str((json.loads(out).get("Self") or {}).get("DNSName") or "").rstrip(".")
    except ValueError:
        return ""


def parse_funnel_status(text: str, port: int) -> str | None:
    """`tailscale funnel status` 的字面 → 端出 127.0.0.1:port 的那個 https 網址;沒端出 ⇒ "";None 只給量不到用。
    形狀(1.98–1.102 實測):
        https://host.tailxxxx.ts.net (Funnel on)
        |-- / proxy http://127.0.0.1:8787
    """
    import re
    url = ""
    for line in text.splitlines():
        m = re.match(r"^\s*(https://\S+)\s+\(Funnel on\)", line)
        if m:
            url = m.group(1)
            continue
        if url and re.search(r"proxy http://127\.0\.0\.1:%d(\D|$)" % port, line):
            return url
        if line.strip() and not line.startswith("|") and not line.startswith(" "):
            url = ""          # 換到下一個主機/埠的區塊
    return ""


def funnel_status_text() -> str | None:
    if os.environ.get("AIB_CLAUDEBOT_RELAY_NO_TAILSCALE"):
        return None
    rc, out, _ = _run(["tailscale", "funnel", "status"], timeout=4)
    return out if rc == 0 else None


def funnel_on(port: int) -> bool | None:
    """Funnel 有沒有把 port 端出去;量不到回 None(不是 False)。"""
    text = funnel_status_text()
    return None if text is None else bool(parse_funnel_status(text, port))


def public_url(port: int) -> str:
    """雲端該填的 AIB_CLAUDEBOT_RELAY_URL = funnel status 裡端出這個埠的那個網址;沒開/量不到 ⇒ ""。
    (不用 tailscale status 的 DNSName 湊:funnel status 印的才是真的對外網址。)"""
    text = funnel_status_text()
    return parse_funnel_status(text, port) if text else ""


def read_token(base=None) -> str:
    """只讀不生成(開席路徑用:沒用雲端的純本機席不該因為開席就多一把鑰匙檔,r4 D2)。"""
    try:
        return token_path(base).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


TOKEN_SHOW_CHARS = 4


def mask_token(tok: str) -> str:
    return (tok[:TOKEN_SHOW_CHARS] + "…") if tok else ""


def cloud_env_lines(port: int = DEFAULT_PORT, base=None, show_token: bool = False, generate: bool = True) -> list[str]:
    """席上與 status 印給 the maintainer 貼進雲端環境的兩行(the maintainer 09-27 15:2x「席上印兩個環境變數」)。
    r4 D1(a seat 19:37):席位畫面在 AIB 會被存檔、被別席讀 ⇒ 通行碼預設只印前 4 碼 + 取全文的指令;
    全文只在 `aib claudebot relay token`(或 status --show-token)印。the maintainer 沒裁前照這條走。"""
    url = public_url(port)
    tok = ensure_token(base) if generate else read_token(base)
    if show_token:
        tok_line = "%s=%s" % (ENV_TOKEN, tok)
    elif tok:
        tok_line = "%s=%s(全文:aib claudebot relay token)" % (ENV_TOKEN, mask_token(tok))
    else:
        tok_line = "%s=(還沒生成:aib claudebot relay up)" % ENV_TOKEN
    return ["%s=%s" % (ENV_URL, url or "(Funnel 沒開:aib claudebot relay up)"), tok_line]


def relay_status(port: int, base=None) -> dict:
    return {"running": health(port), "ours": is_ours(port, base), "port": port, "funnel": funnel_on(port), "url": public_url(port),
            "token_path": str(token_path(base)), "pid_file": str(pid_path(base)),
            "cloud_sessions": len(list_cloud_sessions(base))}


def relay_up(port: int, base=None, funnel: bool = True, out=sys.stdout) -> int:
    """起中繼(背景、脫離這個殼)+ 開 Funnel;已在跑就不起第二個;印雲端要填的兩行。rc 0=在跑。"""
    import subprocess
    ensure_token(base)
    if health(port):
        if not is_ours(port, base):
            out.write("127.0.0.1:%d 上有別的服務在聽(health 回 ok,但答不出這把通行碼的證明):不當成中繼、不開 Funnel、不動它。"
                      "換埠:AIB_CLAUDEBOT_RELAY_PORT=<埠> aib claudebot relay up\n" % port)
            return 1
        out.write("中繼已在跑(127.0.0.1:%d)\n" % port)
    else:
        log = out_path(base).open("ab")
        p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(port)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True, env=dict(os.environ, PYTHONUNBUFFERED="1"))
        log.close()
        pid_path(base).write_text("%d\n" % p.pid, encoding="utf-8")
        deadline = time.monotonic() + UP_WAIT_S
        while time.monotonic() < deadline and not health(port, 0.5):
            if p.poll() is not None:
                out.write("中繼起不來(rc=%s),看 %s\n" % (p.returncode, out_path(base)))
                return 1
            time.sleep(0.2)
        if not health(port):
            out.write("中繼 %.0f 秒內沒回 health,看 %s\n" % (UP_WAIT_S, out_path(base)))
            return 1
        if not is_ours(port, base):
            out.write("起來的東西答不出通行碼證明(碼檔被換過?),不開 Funnel;看 %s\n" % out_path(base))
            return 1
        out.write("中繼已起(pid %d,127.0.0.1:%d,日誌 %s)\n" % (p.pid, port, out_path(base)))
    if funnel:
        on = funnel_on(port)
        if on is None:
            out.write("Funnel:量不到(沒裝 tailscale 或沒登入);對外要自己端出 127.0.0.1:%d\n" % port)
        elif not on:
            rc, so, se = _run(["tailscale", "funnel", "--bg", str(port)], timeout=30)
            if rc == 0 and funnel_on(port):
                out.write("Funnel 已開:%s\n" % public_url(port))
            else:
                out.write("Funnel 開不起來(rc=%s):%s\n  → 管理台先啟用 Funnel,再跑 tailscale funnel --bg %d\n" % (rc, (so + se).strip()[-300:], port))
        else:
            out.write("Funnel 已開:%s\n" % public_url(port))
    out.write("雲端環境變數(貼進 Claude 環境的 Environment variables;通行碼是鑰匙,只放自己的環境):\n")
    for line in cloud_env_lines(port, base):
        out.write("  " + line + "\n")
    return 0


def _pid_is_relay(pid: int, port: int) -> bool:
    """r4 D3 + r7 F4:pid 檔裡的號碼可能已被別的程序重用,kill 前比對命令列——要有**這支檔的完整路徑**、serve、和這個埠,
    三件齊才算(up 起的命令列就是這三件)。量不到當不是。"""
    rc, out, _ = _run(["ps", "-o", "command=", "-p", str(pid)], timeout=5)
    if rc != 0:
        return False
    argv = out.split()
    return str(Path(__file__).resolve()) in argv and "serve" in argv and "--port" in argv and str(port) in argv


def relay_down(port: int, base=None, out=sys.stdout) -> int:
    """停中繼(用 pid 檔;沒 pid 檔就只報 health)。Funnel 不動:要關自己跑 tailscale funnel --bg %d off。"""
    import signal
    pid = 0
    try:
        pid = int(pid_path(base).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        pid = 0
    if pid > 0 and not _pid_is_relay(pid, port):
        out.write("pid 檔裡的 %d 不是中繼(已被別的程序用走或早就不在),不動它;清掉過期 pid 檔\n" % pid)
        try:
            pid_path(base).unlink()
        except OSError:
            pass
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            out.write("pid %d 不是我能停的\n" % pid)
            return 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and health(port, 0.5):
            time.sleep(0.2)
        try:
            pid_path(base).unlink()
        except OSError:
            pass
    if health(port):
        out.write("中繼還在跑(127.0.0.1:%d),不是 pid 檔那個程序;自己 pgrep claudebot_relay\n" % port)
        return 1
    out.write("中繼已停(Funnel 沒動;要關:tailscale funnel --bg %d off)\n" % port)
    return 0


class _Bucket:
    """每把碼一個 token bucket;失敗計數另一桶。"""
    def __init__(self):
        self.lock = threading.Lock()
        self.rate: dict[str, tuple[float, float]] = {}      # key -> (tokens, last)
        self.fail: dict[str, list[float]] = {}              # source -> [ts...]

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.rate.get(key, (RATE_BURST, now))
            tokens = min(RATE_BURST, tokens + (now - last) * RATE_PER_SEC)
            if tokens < 1.0:
                self.rate[key] = (tokens, now)
                return False
            self.rate[key] = (tokens - 1.0, now)
            return True

    def _prune(self, now: float) -> None:
        """呼叫者持鎖。清掉整個視窗都過期的來源;還太多就丟最久沒失敗的,表永遠有上限。"""
        for k in [k for k, xs in self.fail.items() if not xs or now - xs[-1] >= AUTH_FAIL_WINDOW_S]:
            del self.fail[k]
        while len(self.fail) > AUTH_FAIL_TABLE_MAX:
            del self.fail[min(self.fail, key=lambda k: self.fail[k][-1])]

    def auth_fail(self, source: str) -> int:
        now = time.time()
        with self.lock:
            xs = [t for t in self.fail.get(source, []) if now - t < AUTH_FAIL_WINDOW_S]
            xs.append(now)
            self.fail[source] = xs
            self._prune(now)
            return len(xs)

    def fail_sources(self) -> int:
        with self.lock:
            return len(self.fail)

    def auth_blocked(self, source: str) -> bool:
        now = time.time()
        with self.lock:
            xs = [t for t in self.fail.get(source, []) if now - t < AUTH_FAIL_WINDOW_S]
            return len(xs) >= AUTH_FAIL_LIMIT


def make_handler(relay_token: str, base=None, bucket: _Bucket | None = None):
    bucket = bucket or _Bucket()
    log_lock = threading.Lock()

    def log(method: str, path: str, status: int, sid: str = "") -> None:
        # 只記次數與形狀,不記內容、不記通行碼
        line = "%s\t%s\t%s\t%d\tsid=%s\n" % (time.strftime("%FT%T"), method, path.split("?", 1)[0][:80], status, sid[:8])
        with log_lock:
            try:
                with hits_log(base).open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass

    # r7 F2 + r8 G1/G2/G3(a seat 20:58):claim id -> (sid, claimed 檔, 取走時刻 wall-clock, 這批裡已退回過一次的檔)
    #   G1 只對帶 ack=1 的 GET 記 claim(舊 hook 不會 ack,照舊不重送);每封最多退回一次,第二次沒 ack 就留在 claimed 記 gave-up。
    #   G3 claims 落檔 relay/claims.json,中繼重啟後在途的信照樣到時退回。
    claims: dict[str, dict] = {}
    claims_lock = threading.Lock()
    claims_path = relay_dir(base) / "claims.json"
    requeued_once: dict[str, float] = {}  # 檔名 -> 退回時刻;退回過一次的不再退回(r9 H2:7 天後清掉,不只增不減)

    def _claims_save() -> None:   # 呼叫者持鎖
        try:
            tmp = claims_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"claims": {c: {"sid": d["sid"], "paths": [str(p) for p in d["paths"]], "ts": d["ts"]}
                                                  for c, d in claims.items()},
                                       "requeued_once": requeued_once}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, claims_path)
        except OSError:
            pass

    def _claims_load() -> None:
        try:
            d = json.loads(claims_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with claims_lock:
            for c, v in (d.get("claims") or {}).items():
                if isinstance(v, dict) and isinstance(v.get("paths"), list):
                    try:
                        ts = float(v.get("ts") or 0)
                        if not math.isfinite(ts):
                            ts = 0.0                      # NaN/Infinity 會讓那筆永不逾期、信卡在 claimed ⇒ 當作早就逾期,下一次取信退回
                        claims[str(c)] = {"sid": str(v.get("sid") or ""), "paths": [Path(str(p)) for p in v["paths"]], "ts": ts}
                    except (TypeError, ValueError):
                        continue
            ro = d.get("requeued_once") or {}
            if isinstance(ro, list):                      # 舊格式(03e81ae5):清單,時刻當現在
                ro = {str(x): time.time() for x in ro}
            if isinstance(ro, dict):
                for k, v in ro.items():             # r10 低:壞值略過,不讓中繼起不來
                    try:
                        fv = float(v or 0)
                    except (TypeError, ValueError):
                        continue
                    requeued_once[str(k)] = fv if math.isfinite(fv) else 0.0   # NaN/Infinity ⇒ 0,七天清理照常清
    _claims_load()

    def requeue_expired(sid: str) -> int:
        """沒 ack 的取信超過 ACK_TIMEOUT_S ⇒ 把 claimed 裡的檔搬回 inbox 重送(傳輸中斷的掉信窗);每封只退回一次。"""
        now = time.time()
        back = 0
        changed = False
        with claims_lock:
            for cid in [c for c, d in claims.items() if d["sid"] == sid and now - d["ts"] >= ACK_TIMEOUT_S]:
                changed = True
                d = claims.pop(cid)
                for p in d["paths"]:
                    if p.name in requeued_once:
                        log("REQUEUE", "gave-up/" + p.name[:24], 0, sid)   # 退回過一次還是沒 ack:不再退,留在 claimed
                        continue
                    try:
                        os.rename(p, p.parent.parent / p.name)
                        requeued_once[p.name] = now
                        back += 1
                    except OSError:
                        pass
            # r9 H2:過了保留期的名單清掉;**先處理完過期 claim 再清**,否則同一封會被當成沒退回過又退一次
            for name in [n for n, ts in requeued_once.items() if now - ts >= REQUEUED_KEEP_S]:
                del requeued_once[name]; changed = True
            if changed:                  # r9 H1:沒變就不寫(長輪詢每 0.5 秒進來一次)
                _claims_save()
        return back

    class H(BaseHTTPRequestHandler):
        server_version = ""               # r7 F5:免驗證的 health 不洩服務指紋(連 Server 標頭都不送)
        sys_version = ""
        timeout = HANDLER_TIMEOUT_S       # socket 讀寫逾時;不送完 header 的連線到時就被收掉

        def send_response(self, code, message=None):
            self.send_response_only(code, message)
            self.send_header("Date", self.date_time_string())

        def _send(self, status: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _source(self) -> str:
            """連錯鎖的鍵。r1 M1:舊版取 X-Forwarded-For 第一段 = 呼叫者自己寫的字,換一個就繞過。
            只在連線來自本機代理(funnel/serve 進來一律 127.0.0.1)時才看 XFF,而且取最後一段(代理放的那段;
            Go ReverseProxy 的 Rewrite 會先剝掉來話的 X-Forwarded-*,tailscale serve 再自己放真 IP——依碼推定,未真機驗);
            直連就用 socket 位址,呼叫者寫什麼都不算。"""
            peer = ((self.client_address[0] if self.client_address else "") or "?")[:64]
            xff = self.headers.get("X-Forwarded-For") or ""
            if xff and peer in TRUSTED_PROXY_PEERS:
                last = xff.rsplit(",", 1)[-1].strip()
                if last:
                    return last[:64]
            return peer

        def _auth(self, method: str = "GET", sid: str = "") -> bool:
            """過碼與限速;拒絕時自己送回應、自己記日誌(C5:以前由呼叫端一律記 401,429 也記成 401)。"""
            src = self._source()
            if bucket.auth_blocked(src):
                log(method, self.path, 429, sid)
                self._send(429, {"error": "too_many_auth_failures"})
                return False
            got = self.headers.get(HEADER_TOKEN) or ""
            if not got.isascii():
                got = ""                  # r1 L2:非 ASCII 碼讓 compare_digest 拋 TypeError ⇒ 500 且不計連錯;當碼錯
            if not got or not hmac.compare_digest(got, relay_token):
                bucket.auth_fail(src)
                log(method, self.path, 401, sid)
                self._send(401, {"error": "bad_token"})
                return False
            if not bucket.allow(got):
                log(method, self.path, 429, sid)
                self._send(429, {"error": "rate_limited"})
                return False
            return True

        def _route(self):
            """(kind, sid, query) ; kind ∈ health/register/inbox/outbox/None"""
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            if parts == ["v1", "health"]:
                return "health", "", q
            if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] in ("register", "inbox", "outbox", "ack"):
                sid = parts[2]
                if not claudebot_threads.valid_session_id(sid):
                    return None, "", q
                return parts[3], sid, q
            return None, "", q

        def _body(self) -> dict | None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:
                self._send(400, {"error": "bad_length"})   # r1 L1:負數會 read(-1) 讀到 EOF,繞過 256K
                return None
            if n > MAX_BODY:
                self._send(413, {"error": "too_large"})
                return None
            raw = self.rfile.read(n) if n else b""
            try:
                obj = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                self._send(400, {"error": "bad_json"})
                return None
            if not isinstance(obj, dict):
                self._send(400, {"error": "bad_json"})
                return None
            return obj

        def do_GET(self):
            try:
                self._do_get()
            except Exception as exc:  # noqa: BLE001 — 任何內部錯都回 500,不准把連線靜默斷掉
                log("GET", self.path, 500)
                self._send(500, {"error": "internal"})   # r1 L3:不洩例外類別名

        def do_POST(self):
            try:
                self._do_post()
            except Exception as exc:  # noqa: BLE001
                log("POST", self.path, 500)
                self._send(500, {"error": "internal"})

        def _do_get(self):
            kind, sid, q = self._route()
            if kind == "health":
                obj = {"ok": True}
                nonce = str(q.get("nonce") or "")
                if nonce and len(nonce) <= NONCE_MAX and nonce.isalnum():
                    obj["proof"] = proof_for(relay_token, nonce)   # r7 F1:只有拿同一把碼的中繼答得出;證明不洩碼
                log("GET", self.path, 200)
                self._send(200, obj)
                return
            if kind != "inbox":
                log("GET", self.path, 404)
                self._send(404, {"error": "no_such_route"})
                return
            if not self._auth("GET", sid):
                return
            try:
                wait = max(0.0, min(LONG_POLL_MAX_S, float(q.get("wait") or 0)))
            except ValueError:
                wait = 0.0
            deadline = time.monotonic() + wait
            while True:
                found = cm.seat(sid, base)
                if not found:
                    log("GET", self.path, 404, sid)
                    self._send(404, {"bound": False})
                    return
                requeue_expired(sid)
                rc, msgs, code = cm.pull(sid, base)
                if rc == cm.RC_OK and msgs:
                    resp = {"bound": True, "code": code, "project": claudebot_threads.seat_project_label(code, base), "messages": msgs,
                            "rendered": cm.render(msgs, code, sid, cm.token_of(sid, base))}
                    if str(q.get("ack") or "") == "1":      # G1:只有會 ack 的客戶端才記 claim
                        cid = secrets.token_hex(8)
                        claimed = found[0] / "inbox" / "claimed"
                        with claims_lock:
                            claims[cid] = {"sid": sid, "paths": [claimed / (m["id"] + ".json") for m in msgs], "ts": time.time()}
                            _claims_save()
                        resp["claim"] = cid
                    log("GET", self.path, 200, sid)
                    self._send(200, resp)
                    return
                if time.monotonic() >= deadline:
                    log("GET", self.path, 204, sid)
                    self._send(200, {"bound": True, "code": code, "project": claudebot_threads.seat_project_label(code, base), "messages": []})
                    return
                time.sleep(POLL_STEP_S)

        def _do_post(self):
            kind, sid, q = self._route()
            if kind not in ("register", "outbox", "ack"):
                log("POST", self.path, 404)
                self._send(404, {"error": "no_such_route"})
                return
            if not self._auth("POST", sid):
                return
            body = self._body()
            if body is None:
                log("POST", self.path, 400, sid)
                return
            if kind == "ack":
                cid = str(body.get("claim") or "")
                with claims_lock:
                    d = claims.get(cid) if cid else None
                    ok = d is not None and d["sid"] == sid           # G2:先比 sid 再 pop,別席路徑動不了別人的 claim
                    if ok:
                        claims.pop(cid)
                        _claims_save()
                log("POST", self.path, 200, sid)
                self._send(200, {"acked": ok})
                return
            if kind == "register":
                register_cloud_session(sid, body, base)
                bound = cm.seat(sid, base) is not None
                log("POST", self.path, 200, sid)
                self._send(200, {"registered": True, "bound": bound})
                return
            text = body.get("text")
            role = body.get("role") or "claude"
            source = body.get("source") or "cloud"
            if not isinstance(text, str) or not text.strip() or role not in ("claude", "user"):
                log("POST", self.path, 400, sid)
                self._send(400, {"error": "bad_message"})
                return
            found = cm.seat(sid, base)
            if not found:
                log("POST", self.path, 404, sid)
                self._send(404, {"bound": False})
                return
            rc, code = cm.post(sid, text, role=role, source=source, base=base, token=cm.token_of(sid, base))
            if rc != cm.RC_OK:
                log("POST", self.path, 500, sid)
                self._send(500, {"error": "post_failed", "rc": rc})
                return
            log("POST", self.path, 200, sid)
            self._send(200, {"posted": True, "code": code})

        def log_message(self, *a):   # 不用預設 stderr 記錄(會印路徑與來源)
            pass

    return H


def serve(bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT, base=None, token: str | None = None,
          ready_cb=None) -> None:
    tok = token or ensure_token(base)

    class _Server(ThreadingHTTPServer):
        request_queue_size = 128     # 預設 5:Stop+門鈴+席端同時打就有連線被拒(尺 test_f 抓到)
        allow_reuse_address = True
        daemon_threads = True
    httpd = _Server((bind, port), make_handler(tok, base))
    if ready_cb:
        ready_cb(httpd)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="claudebot_relay.py", description="ClaudeBot 雲端橋・本機中繼")
    sub = ap.add_subparsers(dest="cmd")
    ps = sub.add_parser("serve", help="起中繼(前景)")
    ps.add_argument("--bind", default=DEFAULT_BIND)
    ps.add_argument("--port", type=int, default=int(os.environ.get("AIB_CLAUDEBOT_RELAY_PORT") or DEFAULT_PORT))
    sub.add_parser("token", help="印中繼通行碼(第一次會生成);貼進雲端環境變數 " + ENV_TOKEN)
    sub.add_parser("sessions", help="列最近報到的雲端 session")
    for name, hlp in (("up", "起中繼(背景)+ 開 Funnel,印雲端要填的兩行;已在跑就只印"),
                      ("down", "停中繼(Funnel 不動)"), ("status", "在不在跑、Funnel、網址、兩行環境變數")):
        px = sub.add_parser(name, help=hlp)
        px.add_argument("--port", type=int, default=int(os.environ.get("AIB_CLAUDEBOT_RELAY_PORT") or DEFAULT_PORT))
        if name == "up":
            px.add_argument("--no-funnel", action="store_true", help="只起中繼,不碰 tailscale")
        if name == "status":
            px.add_argument("--json", action="store_true")
            px.add_argument("--show-token", action="store_true", help="印通行碼全文(預設只印前 4 碼)")
    args = ap.parse_args(argv)
    if args.cmd == "up":
        return relay_up(args.port, funnel=not args.no_funnel)
    if args.cmd == "down":
        return relay_down(args.port)
    if args.cmd == "status":
        st = relay_status(args.port)
        if args.json:
            sys.stdout.write(json.dumps(st, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write("中繼:%s(127.0.0.1:%d)\nFunnel:%s\n雲端 session(24h 內報到):%d\n" % (
                "在跑" if st["running"] else "沒在跑", args.port,
                {True: "開 " + st["url"], False: "沒開", None: "量不到(沒 tailscale)"}[st["funnel"]], st["cloud_sessions"]))
            for line in cloud_env_lines(args.port, show_token=args.show_token, generate=False):   # r5' E2:status 只讀不生成
                sys.stdout.write("  " + line + "\n")
        return 0 if st["running"] else 1
    if args.cmd == "token":
        sys.stdout.write(ensure_token() + "\n")
        return 0
    if args.cmd == "sessions":
        for d in list_cloud_sessions():
            sys.stdout.write("%s\t%s\t%s\t%s\n" % (d["session_id"], time.strftime("%m-%d %H:%M", time.localtime(d["last_seen"])),
                                                 d.get("branch", ""), d.get("cwd", "")))
        return 0
    if args.cmd == "serve":
        sys.stderr.write("aib-claudebot-relay: %s:%d(通行碼在 %s;對外請用 tailscale funnel %d)\n" % (args.bind, args.port, token_path(), args.port))
        serve(args.bind, args.port)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
