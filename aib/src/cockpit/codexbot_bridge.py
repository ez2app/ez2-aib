#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Connect an AIB pane to an ALREADY LOADED Codex desktop thread.

Never launches Codex or grants approvals. Requires websocket-client >=1.8,<2.
The endpoint must belong to the desktop App Server, not a new server process.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import select
import shutil
import signal
import unicodedata
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse
import uuid
# 同目錄的 sibling 模組:呼叫端(尺)不一定把 src/cockpit 放進 sys.path;掛在**尾端**,不蓋掉尺放在前面的替身。
# ★ 09-25 a seat:aib_dm_tool 原本在這段**之前**就 import ⇒ 呼叫端沒把 src/cockpit 放進路徑時一載入就
#   ModuleNotFoundError(候選證詞在乾淨環境 env -i 抓到;在席位殼裡綠,是殼剛好有設路徑)。
#   原註解提到的替身 tests/aib_dm_tool.py 並不存在。改成先掛路徑再 import。
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parent))
import aib_dm_tool as _aib_dm  # noqa: E402
import codexbot_dm_socket as _dm_socket  # noqa: E402
import codexbot_rules as _rules  # noqa: E402
import bot_seat_name as _seat_name  # noqa: E402
import bot_closer as _closer  # noqa: E402
try:  # review r1 m7:人格包是輔助;模組缺了或壞了不能讓橋起不來
    import bridge_persona as _bridge_persona  # noqa: E402 —— 09-30 19:5x 橋接席的基因+人格包
except Exception:  # noqa: BLE001
    _bridge_persona = None


class BridgeError(RuntimeError):
    """kind(可選):auth / server-cwd / lock-held / thread-status / disconnected / server-down;給 classify_error 用。"""

    def __init__(self, message="", kind=None):
        super().__init__(message)
        self.kind = kind


class Disconnected(BaseException):
    """與本機 Codex server 的連線沒了(reader 收到 EOF / 錯誤)。

    10-07 00:03 a seat 真機:server 重起,橋印一行「desktop disconnected」後**還在**,席窗停在提示符,
    下一句才死,死法 rc=0 ⇒ 開席器以為正常結束,pane dead,沒人重連。
    改成:bridge/error 一來就對自己送 SIGUSR1,主執行緒(不管卡在 input() 還是 rpc.call)丟這個例外,
    主程式以 RECONNECT_RC 結束,由 provider-run-relogin.sh 重開同一席(同 session uuid,席碼不變)。
    繼承 BaseException 不是 Exception:送字那條路的 `except Exception` 會把它吃掉、印「no automatic retry」然後繼續等。
    """


RECONNECT_RC = 3                     # 開席器看到就重連(provider-run-relogin.sh);與 codex 席的 BACK_RC=3 不同路、不相撞
# 10-11 a seat(Bot 開發 P1b):席上 /bind 選了別條 Codex 對話 ⇒ 記下選擇(bridge/<席>/bind-override.json,與 sand_bridge 同一份格式)、
#   照正常關席把這一條的桌面名稱改回未綁定、以 REBIND_RC 結束;開席器馬上重開同一席(同 session uuid、席碼不變),
#   橋起來時讀到那份選擇就接新的那條。不在行程裡換:thread id 掛在鎖、dm socket、訂閱、人格記號、收據等 30 幾處。
REBIND_RC = 4
# 10-11 a seat(Bot 開發 P6,待修清單第 24 件的 CodexBot 那一半):推版後長跑的 CodexBot 橋還跑舊碼。sand_bridge(另三家)
#   閒著時會 exec 自己;這支握著 app-server 連線、訂閱、鎖,exec 不乾淨 ⇒ 改成「盯程式檔(與 idle_reaper 同一套指紋),
#   碼變了、席閒夠久 ⇒ 以 RELOAD_RC 結束,開席器馬上重開同一席」。不把桌面名稱改回未綁定(重開幾秒就接回同一條)。
#   EZ2_AIB_BRIDGE_REEXEC=0 關掉(同 sand_bridge)。
RELOAD_RC = 5
RELOAD_CHECK_S = 30.0
RELOAD_IDLE_S = 600.0          # 最後一次打字 / 回合結束後至少閒這麼久才重開


class Reload(BaseException):
    """看守執行緒用 SIGUSR2 把主執行緒從 input() 叫出來。繼承 BaseException:送字那條路的 except Exception 不能吃掉它。"""


def reload_enabled() -> bool:
    return (os.environ.get("EZ2_AIB_BRIDGE_REEXEC") or "1").strip().lower() not in ("0", "false", "no", "off")


def reload_due(fp0: str, fp: str, idle_s: float, prompt_up: bool, turn_active: bool) -> bool:
    """純判斷(測試用):指紋兩次都量得到、變了;提示符在等字、沒有回合在跑;閒 ≥ RELOAD_IDLE_S。"""
    return bool(fp0 and fp and fp != fp0 and prompt_up and not turn_active and idle_s >= RELOAD_IDLE_S)
BIND_LIST_LIMIT = 9
_BIND_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
try:  # 401 的判準沿用 codex 席換帳流程那一把尺(the maintainer 09-28「太敏感」修過:1401、行號 401 都不算)
    from codex_account_recover import _READ_FAILURE_RE as _AUTH_401_RE  # noqa: E402
except Exception:  # noqa: BLE001 —— 模組不在(拆包)時退回同一條規則的複本
    _AUTH_401_RE = re.compile(
        r"unauthorized[^0-9\n]{0,40}(?<![0-9])401(?![0-9])"
        r"|(?<![0-9])401(?![0-9])[^0-9\n]{0,40}unauthorized"
        r"|(?:status|http|code)[ :=]*(?<![0-9])401(?![0-9])", re.I)

HUMAN_ERRORS = {
    "server-cwd": "AIB 的本機 Codex server 工作目錄不見了(server 要重起;開席器 ensure 會自動做)",
    "auth": "Codex 登入失效(401):請重新登入 codex",
    "lock-held": "這條對話被另一邊握著(桌面或另一席),等它放手",
    "disconnected": "與本機 Codex server 斷線",
    "server-down": "本機 Codex server 起不來",
    "thread-status": "對話在這顆 server 不是 idle/active(server 剛重起、登入失效或被桌面握著)",
}


def classify_error(err):
    """錯誤 → (kind, 人話)。10-07 真機三種壞法,席窗以前全印成同一句「refusing resume on a different server」:
    真因(cwd 不見、401、鎖被握)只在 app-server.log。這裡把它們認出來,status.json 與席窗都講人話。
    認不出來 ⇒ ("unknown", 原文前 160 字),不硬套。"""
    text = _ANSI_RE.sub("", str(err))
    low = text.lower()
    kind = getattr(err, "kind", None)
    if "failed to load configuration" in low and ("no such file" in low or "os error 2" in low):
        return "server-cwd", HUMAN_ERRORS["server-cwd"]
    if _AUTH_401_RE.search(text) or "invalidated oauth" in low or "could not be refreshed" in low \
            or "please sign in again" in low or kind == "auth":
        return "auth", HUMAN_ERRORS["auth"]
    if "active writer" in low or "already has a live aib bridge" in low:
        return "lock-held", HUMAN_ERRORS["lock-held"]
    if kind == "server-down":
        return "server-down", HUMAN_ERRORS["server-down"] + ":" + text[:120]
    if kind == "disconnected" or "connection closed" in low or "disconnected" in low or "connection lost" in low:
        return "disconnected", HUMAN_ERRORS["disconnected"]
    if kind == "thread-status" or "not loaded in this server" in low:
        return "thread-status", HUMAN_ERRORS["thread-status"]
    return "unknown", text[:160]


CHECK_LOGIN_ENV = "AIB_CODEXBOT_CHECK_LOGIN"      # =0 不查登入(治具)
_LOGIN_STATUS_TIMEOUT = 15


def codex_bin():
    return os.environ.get("AIB_CODEXBOT_CODEX_BIN", "") or shutil.which("codex") or "codex"


def check_login(runner=None, strict=False):
    """`codex login status`:rc 0 = 登入著;非 0 = 沒登入。量不到(逾時、找不到指令)回 None,不當沒登入。
    strict(--check 用)⇒ 沒登入就丟 BridgeError(kind=auth):開席前就擋,不要開了一個「連著但一句都跑不動」的席。
    開席的 --check 以前只讀一次對話,10-07 23:47 真機:登入早失效,席照開、右欄全綠。"""
    if os.environ.get(CHECK_LOGIN_ENV, "1") == "0":
        return None
    run = runner or subprocess.run
    try:
        out = run([codex_bin(), "login", "status"], stdin=subprocess.DEVNULL, capture_output=True,
                  text=True, timeout=_LOGIN_STATUS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    ok = out.returncode == 0
    if not ok and strict:
        raise BridgeError("Codex 沒登入(codex login status rc=%d):先在終端機 codex login,再開席" % out.returncode, kind="auth")
    return ok


_DEVICE_LOGIN = {"started": 0.0}
DEVICE_LOGIN_TTL = 900.0                          # codex 說 one-time code 15 分鐘有效
_DEVICE_CODE_RE = re.compile(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{5})\b")
_URL_RE = re.compile(r"https://\S+")


def codex_auth_path():
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "auth.json"


def start_device_login(notice, save=None, popen=None, auth_path=None, clock=time.time,
                       poll_secs=5.0, wait_secs=DEVICE_LOGIN_TTL, sleep=time.sleep):
    """登入失效時,在席窗直接給登入網址與一次性碼(像 codex 席的換帳流程那樣),不要只印一句英文錯誤。
    同一輪 15 分鐘內只起一次。背景執行緒等 auth.json 回來就印「登入回來了」。回 True = 這次有起。
    不會自動重送任何訊息:登入回來後要人再送一次(避免重送造成兩份)。"""
    now = clock()
    if now - _DEVICE_LOGIN["started"] < DEVICE_LOGIN_TTL:
        return False
    _DEVICE_LOGIN["started"] = now
    run = popen or subprocess.Popen
    auth = Path(auth_path) if auth_path else codex_auth_path()
    try:
        proc = run([codex_bin(), "login", "--device-auth"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True, start_new_session=True)
    except OSError as exc:
        notice("codexbot: 起不了 codex login(%s);請自己在終端機跑 codex login" % str(exc)[:80])
        return False

    def worker():
        url = code = None
        try:
            for raw in proc.stdout:
                line = _ANSI_RE.sub("", raw)
                m = _URL_RE.search(line)
                if m and not url:
                    url = m.group(0).rstrip(".,)")
                m = _DEVICE_CODE_RE.search(line)
                if m and not code:
                    code = m.group(1)
                if url and code:
                    break
        except (OSError, ValueError):
            pass
        if url and code:
            notice("codexbot: Codex 登入失效。請在瀏覽器開 %s 輸入 %s(15 分鐘內;這是本席起的 codex login,不是別人給的碼)" % (url, code))
            if save:
                save(auth_login_url=url, auth_login_code=code, auth_login_started_epoch=now)
        else:
            notice("codexbot: codex login 沒印出登入碼;請自己在終端機跑 codex login")
        deadline = clock() + wait_secs
        while clock() < deadline:
            if auth.exists():
                notice("codexbot: Codex 登入回來了;剛才那句沒送出去,請再送一次")
                if save:
                    save(last_error="登入已回來,等下一句驗證")
                return
            sleep(poll_secs)
        notice("codexbot: 登入碼已過期(15 分鐘);下一句失敗時會再給一組")

    threading.Thread(target=worker, name="codexbot-device-login", daemon=True).start()
    return True


def _persona_mark_sent(base, thread_id, session_uuid, ux, what="codexbot"):
    """人格包隨這一則**送成功**之後才留記號(review r1 m6)。回 True = 這一席已送過(persona_sent)。
    記號寫不進去:這次開席不再接(免得每一則都帶一整包),下次開席會再送一次。
    review r4 m3:what 記送了什麼(bridge_persona.codexbot_pack 給的);只送了基因("genes")⇒ 回 False,
    之後每一則再看人格包讀不讀得到,讀到就補送一次人格包(記號改成 persona:<指紋>)。"""
    try:
        _bridge_persona.mark_sent(_bridge_persona.codexbot_marker_dir(base, thread_id, session_uuid), what)
    except OSError as exc:
        ux.notice("codexbot: 人格包記號寫不進去(%s),下次開席會再送一次" % str(exc)[:80])
        return True
    return what != "genes"


DELIVERY_UNCERTAIN = "delivery uncertain"     # DesktopRPC.call 逾時那一句;與下面 raise 的字面同一個


def _send_maybe_delivered(exc):
    """送出時的錯是不是「可能已經送進去了」(rpc 逾時,契約:勿自動重送)。其他錯(no input sent 等)= 沒送出去。"""
    return DELIVERY_UNCERTAIN in str(exc)


class UnixWebSocket:
    """Small RFC 6455 text client for the local Unix-socket transport."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(path)
        self._send_lock = threading.Lock()
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._until(b"\r\n\r\n", 65536)
        head = response.decode("iso-8859-1")
        if not head.startswith("HTTP/1.1 101 "):
            raise BridgeError("local App Server refused WebSocket upgrade")
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        headers = {}
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != expected:
            raise BridgeError("local App Server WebSocket handshake mismatch")

    def _exact(self, size):
        chunks = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise BridgeError("local App Server connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _until(self, marker, limit):
        data = bytearray()
        while marker not in data:
            data.extend(self._exact(1))
            if len(data) > limit:
                raise BridgeError("local App Server handshake too large")
        return bytes(data)

    @staticmethod
    def _frame(opcode, payload=b""):
        mask = secrets.token_bytes(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return header + mask + masked

    def send(self, text):
        payload = text.encode("utf-8")
        with self._send_lock:
            self.sock.sendall(self._frame(0x1, payload))

    def recv(self):
        assembled = bytearray()
        data_opcode = None
        while True:
            first, second = self._exact(2)
            final, opcode = bool(first & 0x80), first & 0x0F
            masked, length = bool(second & 0x80), second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8))[0]
            mask = self._exact(4) if masked else b""
            payload = self._exact(length)
            if masked:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 0x8:
                return ""
            if opcode == 0x9:
                with self._send_lock:
                    self.sock.sendall(self._frame(0xA, payload))
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                data_opcode = opcode
                assembled = bytearray(payload)
            elif opcode == 0x0 and data_opcode is not None:
                assembled.extend(payload)
            else:
                continue
            if final:
                if data_opcode != 0x1:
                    raise BridgeError("local App Server returned non-text WebSocket data")
                return assembled.decode("utf-8")

    def close(self):
        try:
            with self._send_lock:
                self.sock.sendall(self._frame(0x8))
        except OSError:
            pass
        self.sock.close()


def _legacy_codex_thread_id():
    # 舊別名。不和 AIB_CODEXBOT_THREAD_ID 讀在同一個函式,
    # 否則艙清單會把它算成換艙時也要登記的艙變數。
    return os.environ.get("CODEX_THREAD_ID") or ""


CLOSER_WAIT_ENV = "AIB_CODEXBOT_CLOSER_WAIT_S"
CLOSER_WAIT_S = 40.0   # 小幫手改名最多兩次 RPC、各 15 秒逾時,再留 10 秒餘裕


def wait_for_own_closer(lock_file, marker, limit=None, sleep=time.sleep, clock=time.monotonic, notice=None):
    """開席撞到對話鎖時:占鎖的如果是**上一隻橋的收尾小幫手**(bot_closer.closer_marker 的記號在、pid 活著),
    就等它改完名放鎖,最多 limit 秒(預設 CLOSER_WAIT_S,環境變數 AIB_CODEXBOT_CLOSER_WAIT_S 可調);等到了回 True(鎖已拿到)。
    沒有記號、記號的 pid 已死、或超過上限 ⇒ 回 False,呼叫端照舊 rc=2 —— 別席的活橋占著時**不等、不放寬**。
    09-25 a seat:艙 9260 a seat respawn-pane -k 之後新橋比小幫手早 2 秒起來,撞鎖退出(第二次重開才好)。"""
    if limit is None:
        try:
            limit = float(os.environ.get(CLOSER_WAIT_ENV) or CLOSER_WAIT_S)
        except ValueError:
            limit = CLOSER_WAIT_S
    t0 = clock()
    announced = False
    while True:
        # ★ 先試鎖、再看記號(09-25 19:23 a seat 第三批紅):小幫手是「先放鎖、再刪記號」,
        #   先看記號的話,放鎖與刪記號之間那一刻就會看到「記號沒了」而放棄 —— 鎖其實已經空出來了。
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            pass
        try:
            pid = int(marker.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        if not announced:
            (notice or (lambda s: print(s, file=sys.stderr, flush=True)))(
                "codexbot: 上一隻橋的收尾小幫手(pid %d)還拿著這條對話在改名,等它收完(最多 %d 秒)" % (pid, int(limit)))
            announced = True
        if clock() - t0 >= limit:
            return False
        sleep(0.2)


def _state_base() -> Path:
    return Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def apply_bind_override(base: Path, session_id: str, launch_thread: str):
    """開席時:這一席上次在席上 /bind 選過別條 ⇒ 回 (那一條, 一句話);沒有、或開席命令已換成別條 ⇒ (launch_thread, "")。
    選的那條現在被別席占著 ⇒ 不用它(用了橋會起不來、席就死了),退回開席命令那條並說一聲。"""
    if not session_id or not launch_thread:
        return launch_thread, ""
    from sand_bridge import load_bind_override
    has, chosen = load_bind_override(Path(base) / "bridge" / session_id, launch_thread)
    if not has or not chosen or chosen == launch_thread:
        return launch_thread, ""
    try:
        uuid.UUID(chosen)
    except (ValueError, TypeError, AttributeError):
        return launch_thread, ""
    import codexbot_threads as _cbt
    held = _cbt.held_by(chosen, base)
    if held:
        return launch_thread, ("codexbot: 席上 /bind 選的 Codex 對話 %s 正被 %s 使用,這次先接開席命令那條 %s"
                               % (chosen[:8], "、".join(held), launch_thread[:8]))
    return chosen, "codexbot: 沿用席上 /bind 選的 Codex 對話 %s(開席命令帶的是 %s)" % (chosen, launch_thread)


def codex_bind_rows(rpc, endpoint: str, current: str, base: Path, limit: int = BIND_LIST_LIMIT) -> list:
    """席上 /bind 的清單:Codex 桌面的對話(新的在前),不列現在綁的這一條;held 用右鍵選單同一把尺(codexbot_threads.held_by)。"""
    import codexbot_threads as _cbt
    rows = []
    for r in _cbt.list_threads(endpoint, limit + 1, rpc=rpc):
        if not r.get("id") or r["id"] == current:
            continue
        rows.append(dict(r, held="、".join(_cbt.held_by(r["id"], base))))
    return rows[:limit]


def render_codex_bind_list(rows: list, current: str) -> list:
    if not rows:
        return ["codexbot: Codex 桌面沒有別的對話可換;先在 ChatGPT 桌面開一條新對話,再打 /bind"]
    out = ["codexbot: 現在綁 %s;能換的 Codex 對話(打 /bind <編號>,或 /bind <完整 UUID>):" % current[:8]]
    for i, r in enumerate(rows, 1):
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated") or 0))
        name = _BIND_CTRL_RE.sub("", str(r.get("name") or ""))[:50]
        held = "  [正被 %s 使用,不能選]" % r["held"] if r.get("held") else ""
        out.append("  %d. %s  (%s · %s)%s" % (i, name or "(沒名字)", when, r["id"][:8], held))
    out.append("  換綁會先把現在這條的桌面名稱改回未綁定,再重開這一席接新的那條(席碼不變,幾秒)")
    return out


def resolve_codex_bind_arg(arg: str, rows: list, current: str):
    """/bind 後面那段 ⇒ (thread UUID, 錯誤句)。"""
    arg = arg.strip().lower()
    if arg.isdigit():
        n = int(arg)
        if not rows:
            return "", "先打 /bind 看清單,再打 /bind <編號>"
        if not 1 <= n <= len(rows):
            return "", "編號 %d 不在清單裡(1–%d)" % (n, len(rows))
        if rows[n - 1].get("held"):
            return "", "第 %d 條正被 %s 使用,不能綁" % (n, rows[n - 1]["held"])
        return rows[n - 1]["id"], ""
    try:
        want = str(uuid.UUID(arg))
    except (ValueError, TypeError, AttributeError):
        return "", "看不懂「%s」:/bind 列清單、/bind <編號>、/bind <完整 UUID>" % arg[:40]
    if want == str(current).lower():
        return "", "這一席本來就綁這一條"
    return want, ""


def validate_target(thread_id, endpoint):
    # Thread identity is supplied by the calling desktop task, never inferred
    # from a seat code, window number, most recent thread, or rollout filename.
    if not thread_id:
        raise BridgeError(
            "缺少桌面 thread。請設 AIB_CODEXBOT_THREAD_ID=<Codex 桌面 thread UUID>"
            "（舊名 AIB_CODEXAPP_THREAD_ID 也可）。不能猜席碼／窗號。")
    try:
        uuid.UUID(thread_id)
    except (ValueError, TypeError, AttributeError):
        raise BridgeError(
            "AIB_CODEXBOT_THREAD_ID 必須是桌面 thread 的 UUID，現在是：%r" % (thread_id,))
    if not endpoint:
        raise BridgeError(
            "缺少桌面 App Server 位址。請設 AIB_CODEXBOT_ENDPOINT="
            "ws://127.0.0.1:… 或 unix:///…（舊名 AIB_CODEXAPP_ENDPOINT 也可）。")
    parsed = urlparse(endpoint)
    if parsed.scheme == "unix" and not parsed.netloc and parsed.path.startswith("/"):
        return
    if (parsed.scheme == "ws" and parsed.hostname in ("127.0.0.1", "::1", "localhost")
            and not parsed.username and not parsed.password and not parsed.fragment):
        return
    raise BridgeError(
        "AIB_CODEXBOT_ENDPOINT 只能是本機 ws:// 或 unix:///，現在是：%r" % (endpoint,))


def connect(endpoint):
    if endpoint.startswith("unix://"):
        return UnixWebSocket(urlparse(endpoint).path)
    try:
        import websocket
    except ImportError as exc:
        raise BridgeError("Missing websocket-client; install tools/requirements-codexbot.txt in the bridge Python environment") from exc
    try:
        return websocket.create_connection(endpoint, timeout=10, suppress_origin=True,
                                           http_no_proxy=["localhost", "127.0.0.1", "::1"])
    except Exception:
        raise


class RPC:
    def __init__(self, ws, on_event=lambda event: None):
        self.ws = ws
        self.on_event = on_event
        self.pending = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                try:
                    raw = self.ws.recv()
                except TimeoutError:
                    continue
                except Exception as exc:
                    # websocket-client uses its own timeout exception.
                    if type(exc).__name__ == "WebSocketTimeoutException":
                        continue
                    raise
                if not raw:
                    break
                msg = json.loads(raw)
                if "method" in msg:
                    # Approval/tool requests belong to the desktop UI. This
                    # adapter must not approve or execute them itself.
                    self.on_event(msg)
                else:
                    with self.lock:
                        waiter = self.pending.get(msg.get("id"))
                    if waiter is not None:
                        waiter.put(msg)
        except Exception as exc:
            # 已經在關了就不要再往外丟事件:收尾時 daemon 執行緒對 stdout/stderr 寫字會撞直譯器的
            # 鎖 ⇒ 「Abort trap: 6 / Python runtime state: finalizing」(09-24 aib --check 間歇撞到一次)。
            if not self.closed.is_set():
                self.on_event({"method": "bridge/error", "params": {"message": str(exc)}})
        else:
            # server 正常關 socket(重起、被 kill)是走 `if not raw: break` 出來的,以前這條路**不發事件**,
            # 席窗什麼都不印、下一句才撞「connection closed」(10-07 00:03 真機)。
            if not self.closed.is_set():
                self.on_event({"method": "bridge/error", "params": {"message": "local App Server connection closed"}})
        finally:
            self.closed.set()
            with self.lock:
                for waiter in self.pending.values():
                    waiter.put({"error": {"message": "connection closed; delivery may be uncertain"}})

    def notify(self, method, params=None):
        with self.lock:
            self.ws.send(json.dumps({"method": method, "params": params or {}}))

    def respond(self, ident, result=None, error=None):
        """回覆 server→client 的請求(帶 id 的 method)。error 給字串就回 JSON-RPC error。
        A1(09-24):AIB 接手期間沒有人會按核准 ⇒ 橋自己答,不答就是死結。"""
        if error is not None:
            payload = {"id": ident, "error": {"code": -32000, "message": str(error)}}
        else:
            payload = {"id": ident, "result": result if result is not None else {}}
        with self.lock:
            self.ws.send(json.dumps(payload))

    def call(self, method, params, timeout=15):
        with self.lock:
            if self.closed.is_set():
                raise BridgeError("desktop connection closed")
            self.sequence += 1
            ident = self.sequence
            waiter = self.pending[ident] = queue.Queue()
            try:
                self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
            except Exception:
                self.pending.pop(ident, None)
                raise
        try:
            response = waiter.get(timeout=timeout)
            if "error" in response:
                raise BridgeError(str(response["error"]))
            return response["result"]
        except queue.Empty as exc:
            raise BridgeError("request timed out; %s; do not automatically resend" % DELIVERY_UNCERTAIN) from exc
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def close(self):
        self.closed.set()
        try:
            self.ws.close()
        except Exception:
            pass
        # ★ 等讀取執行緒真的結束(最多 2 秒):daemon 執行緒若在直譯器收尾時還活著並寫字,
        #   會 abort。join 是把「什麼時候結束」從運氣變成順序。
        if self.reader is not threading.current_thread():
            self.reader.join(timeout=2)


def desktop_account(rpc):
    """這顆 Codex app-server 登入的帳號,一行人話(例 `human@x.com(plus)`);讀不到回 ""。
    10-10 a seat(待修清單第 22 件):ClaudeBot 10-09 搞混過帳號 ⇒ 開 bot 席都要說用的是哪個帳號。
    問 server 自己的 `account/read`(不帶 refreshToken,不換權杖、不讀任何憑證檔);舊版沒這個方法、逾時、怪格式 ⇒ 空,不擋開席。"""
    try:
        acct = (rpc.call("account/read", {}, timeout=10) or {}).get("account")
    except Exception:  # noqa: BLE001 —— 顯示帳號是附加的,任何失敗都不得擋綁定
        return ""
    if not isinstance(acct, dict):
        return ""
    if acct.get("type") == "apiKey":
        return "API 金鑰(不是 ChatGPT 帳號)"
    email = acct.get("email")
    if not (isinstance(email, str) and "@" in email and len(email) < 200):
        return ""
    plan = acct.get("planType")
    return email.strip() + ("(%s)" % plan if isinstance(plan, str) and plan and plan != "unknown" else "")


def server_version(init_result):
    """F1:從 initialize 回的 userAgent(`ez2_aib_codexbot/0.156.1 (…)`)取 app-server 版本;拿不到回 'unknown'。"""
    ua = (init_result or {}).get("userAgent") if isinstance(init_result, dict) else None
    m = re.search(r"/(\d+\.\d+\.\d+)", ua or "")
    return m.group(1) if m else "unknown"


def method_missing(exc):
    """server 回「沒有這個方法」的各種寫法(JSON-RPC -32601 / Method not found / unknown method)。"""
    t = str(exc).lower()
    return "-32601" in t or "method not found" in t or "unknown method" in t or "unknown request" in t


class Desktop:
    server_version = "unknown"   # F1;類別預設,initialize 後蓋(不動 __init__:到達性閘算不出它的入口)

    def __init__(self, rpc, thread_id):
        self.rpc, self.thread_id = rpc, thread_id
        self.session_id = None

    def read(self, allow_not_loaded=False):
        thread = self.rpc.call("thread/read", {"threadId": self.thread_id,
                                               "includeTurns": True})["thread"]
        if thread.get("id") != self.thread_id:
            raise BridgeError("server returned a different thread")
        status = thread.get("status", {}).get("type")
        allowed = ("idle", "active", "notLoaded") if allow_not_loaded else ("idle", "active")
        if status not in allowed:
            raise BridgeError("thread is not loaded in this server, or is unavailable; refusing resume on a different server"
                              "(thread status=%s)" % status, kind="thread-status")
        if not thread.get("sessionId"):
            raise BridgeError("server did not return sessionId; do not infer it from threadId")
        if self.session_id is not None and thread["sessionId"] != self.session_id:
            raise BridgeError("desktop session changed; refusing delivery to replacement session")
        return thread

    def attach(self):
        """接回寫入權:只 resume,不重送 initialize(第二次 initialize 會被拒 Already initialized)。"""
        thread = self.read(allow_not_loaded=True)
        resumed = self.rpc.call("thread/resume", {"threadId": self.thread_id})["thread"]
        if resumed.get("id") != self.thread_id or resumed.get("sessionId") != thread["sessionId"]:
            raise BridgeError("desktop session changed during attach")
        return resumed

    def initialize(self, subscribe=False, load=True):
        """load=False(queue 模式):只 initialize + read,**不** resume ——
        resume 就是拿寫入權,而 queue 模式的全部意義就是不拿。"""
        init = self.rpc.call("initialize", {"clientInfo": {"name": "ez2_aib_codexbot",
                                                           "title": "AIB Codex desktop bridge", "version": "0.1.0"},
                                            "capabilities": {"experimentalApi": True}})   # thread/queue/list 要它
        self.server_version = server_version(init)
        self.rpc.notify("initialized")
        thread = self.read(allow_not_loaded=True)
        self.session_id = thread["sessionId"]
        if not load:
            # F1(09-24):桌面 app / codex CLI 升版會改 app-server API(0.153→0.156 方法表變過)。queue 模式靠
            #   thread/queue/list|add(experimental);這裡用唯讀的 list 探一次,缺就大聲死、印版本,不要開了席才每 3 秒失敗。
            try:
                self.rpc.call("thread/queue/list", {"threadId": self.thread_id}, timeout=15)
            except BridgeError as exc:
                if method_missing(exc):
                    raise BridgeError("桌面 app-server 版本 %s 沒有 thread/queue/list(queue 模式需要;0.156.1 有):升版 codex,或用 --mode writer" % self.server_version) from exc
                # 其他錯(對話沒載入之類)不是版本問題,照舊往下
            return thread
        if subscribe or thread.get("status", {}).get("type") == "notLoaded":
            # The AIB-managed server may know the persisted thread without
            # having it in memory yet. Resume that exact ID; never create,
            # fork, or guess a replacement thread.
            resumed = self.rpc.call("thread/resume", {"threadId": self.thread_id})["thread"]
            if resumed.get("id") != self.thread_id or resumed.get("sessionId") != thread["sessionId"]:
                raise BridgeError("desktop session changed during subscription")
            thread = resumed
        return thread

    def send(self, text):
        thread = self.read()
        params = {"threadId": self.thread_id, "input": [{"type": "text", "text": text}]}
        if thread["status"]["type"] == "active":
            active = [turn for turn in thread.get("turns", []) if turn.get("status") == "inProgress"]
            if len(active) != 1 or not active[0].get("id"):
                raise BridgeError("cannot identify the active turn; no input sent")
            params["expectedTurnId"] = active[0]["id"]
            return self.rpc.call("turn/steer", params)
        return self.rpc.call("turn/start", params)

    def interrupt(self, turn_id):
        return self.rpc.call("turn/interrupt", {"threadId": self.thread_id,
                                                 "turnId": turn_id})

    def find_turn(self, turn_id):
        thread = self.read()
        for turn in thread.get("turns", []):
            if turn.get("id") == turn_id:
                return turn
        raise BridgeError("completed turn is missing from thread history")


PROMPT = "codexbot> "
TITLE_MAX = 28
TITLE_REFRESH_EVERY = 10   # writer 模式:每 N 次輪詢(N×POLL_SECS,預設 30 秒)讀一次標題
# 桌面自動生成的樣板前綴:剝掉才看得出是哪一條(全形冒號也算)
BORING_TITLE_PREFIX = re.compile(r"^(Continuing from|延續自|接續)[^:：]{0,40}[:：]\s*")


# 模型那邊來的字(回覆、對話標題、工具卡)印上席位前濾掉的控制字元;與 sand_bridge.OUTBOX_CTRL_RE 同一個判準。
#   10-11 a seat(DIAG §9 #3 後半,the maintainer「第 3、14 條也修」):以前只濾回覆,標題行與工具卡沒濾。
MODEL_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


def title_line(title, thread_id):
    """提示符上面那一行:對話標題(截到 TITLE_MAX 字)+ uuid 前 8 碼。the maintainer 09-24 23:0x「codexbot> 帶出名稱適當的字數」。
    ★ 不能塞進 `codexbot> ` 本身:dm 引擎以「行首 codexbot>」找提示符、把它後面的字當草稿(composer_state)。
    標題行以 `codexbot: ` 開頭,與其他通知同形(busy marker 只在沒有提示符行時才算數)。"""
    t = MODEL_CTRL_RE.sub("", str(title or "").replace("\n", " ")).strip()
    # ★ 09-25 the maintainer:「codexbot> 還沒有取得對話名稱」——名字其實有,但**截在共同前綴上**:
    #   桌面每條都叫「Continuing from ChatGPT席位設定: …」,截 24 字後每條長得一模一樣 = 等於沒有名字。
    #   先剝掉這類樣板前綴(「Continuing from …:」),留下真正能分辨的那一段再截。剝完是空的就用原字串。
    stripped = BORING_TITLE_PREFIX.sub("", t, count=1).strip()
    if stripped:
        t = stripped
    if len(t) > TITLE_MAX:
        t = t[:TITLE_MAX - 1] + "…"
    return "codexbot: 對話「%s」 · %s" % (t or "(無標題)", str(thread_id)[:8])
ECHO_PREFIX = "you> "          # 回顯行的前綴,**故意**跟活提示符不同(舊預設;main 現在依來源算,見 echo_prefix_for)
HUMAN_NAME_ENVS = ("AIB_HUMAN_NAME", "AIB_CODEXBOT_HUMAN_NAME")   # 真人名字:環境變數 → git user.name → 登入名
DM_ECHO_PREFIX = "dm> "         # 別席 dm 貼進來的稿(正文自帶 [@寄件席 > @你] 抬頭),不是真人打的


def human_name(env=None, cwd=None):
    """回顯行要印的真人名字(the maintainer 09-24 23:0x「真人(the maintainer)>」)。只是顯示,不是身分證明(身分證明走 human_attest)。"""
    env = os.environ if env is None else env
    for k in HUMAN_NAME_ENVS:
        v = (env.get(k) or "").strip()
        if v:
            return v
    try:
        import subprocess
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5, cwd=cwd or None)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return "human"


# 09-25 a seat(a seat 派):跨艙抬頭「[192.168.0.10·d6a8·a seat(…) > 192.168.0.10·9260·a seat(…) / …]」
#   (the maintainer 18:1x/18:2x 定:最前面是寄件機器預設路由網卡的 IPv4,取不到寫「?」;再來艙號=實例名尾四碼;分隔「·」)
#   與跨艙代理寄件「[agent:offhub > …]」也是 dm。漏認的後果不是顯示小錯:回顯畫成「真人(the maintainer)>」,
#   看畫面的人(與截圖給 bot 看的人)會把另一個 AI 的話當成 the maintainer 本人的指示。
#   18:14 再加:位置段可以是 IPv4 / IPv6(壓縮形式、不加括號)/ 主機名稱 / 「?」。收件端一律寬認:@ 前面最多兩段
#   「不含空白、·、@、[、]、> 的字」都當位置段 —— 格式對不對是白名單(seat_address)的事;這裡寧可認成 dm,
#   也不要因為一個沒見過的位置寫法(例如帶 zone id)就退回「真人(the maintainer)>」。
# ★ 包 I(09-26):dm 抬頭的判讀一律走 dm_header.parse(新式 `IP:… >> …` 與上面三代舊式同一份);
#   本檔只自留「[AIB DM」—— 那是本橋自己給模型的標記(tag_origin),不是 dm 抬頭。
_AIB_DM_TAG = "[AIB DM"


def _dm_header_parse(line):
    from dm_header import parse
    return parse((line or "").strip())


def echo_prefix_for(line, name=None):
    """這一則是誰打的:別席 dm 貼進來的(正文以 AIB 抬頭開頭)⇒ `dm> `;其餘 = 真人在席位打字 ⇒ `真人(名字)> `。"""
    if (line or "").lstrip().startswith(_AIB_DM_TAG) or _dm_header_parse(line) is not None:
        return DM_ECHO_PREFIX
    return "真人(%s)> " % (name if name is not None else human_name())
REPLY_PREFIX = "codexbot< "    # 模型回話的前綴:原本也是 `codexbot> `,跟活提示符同字面 ⇒

def _pfx() -> str:
    """回話前綴上色(10-09 the maintainer 對話框):只在 TTY 且沒關掉對話卡時加 Codex 的品牌色(右欄同色號)。
    字面 `codexbot< ` 一個字不改 —— 它是 composer_state 的 cursor_stop 契約;tmux capture 不帶 -e 時顏色本來就剝掉。
    非 TTY(測試、日誌)⇒ 原樣,畫面逐位元組同以前。"""
    try:
        import bridge_cards as _bc
        if _bc.cards_enabled(sys.stdout.isatty()) and _bc.color_enabled():
            _, _, col = _bc.BRANDS.get("codexbot", ("", "", None))
            if col is not None:
                return "\x1b[38;5;%dm%s\x1b[0m" % (col, REPLY_PREFIX)
    except Exception:  # noqa: BLE001
        pass
    return REPLY_PREFIX
                               # composer_state 把上一則回話當成「有草稿」。改成 `<`(從 codexbot 來)。
MODEL_DM_ENV = "AIB_CODEXBOT_MODEL_DM"          # =0 關掉模型自主 dm
REWRITE_ECHO_ENV = "AIB_CODEXBOT_REWRITE_ECHO"  # =0 關掉回顯改寫


def read_message(prompt=PROMPT, stdin=None, drain_seconds=0.15, _input=None):
    """一次貼上的多行 = 一則訊息。

    2026-09-24 a seat 實測:`aib dm` 貼兩行進來,`input()` 在第一個換行就回,
    第二行變成**下一個回合**的輸入 —— 一則多行 dm 變成 N 次模型呼叫,而且第二行
    起會被當成新指令。修法:第一行讀到之後,只要 stdin 在 drain_seconds 內還有
    整行可讀(貼上是一口氣到的),就一直吸進來,合成一則。人打字不會這麼快,
    所以互動輸入不受影響。
    """
    stdin = stdin or sys.stdin
    t0 = time.monotonic()
    # ★ 不在參數預設值綁 input:預設值在 def 時求值,會綁死原生 builtins.input,
    #   測試(與任何要替換 input 的人)換掉的是**之後**的名字,永遠換不進來 ⇒ 真的去讀
    #   真 stdin ⇒ 尺卡住。呼叫時才查名字,模組層或 builtins 的替換都吃得到。
    if _input is None:
        _input = globals().get("input", input)
    first = _input(prompt)
    # 回合進行中貼進來的稿排在 tty 緩衝,input() 一叫就回(< 50ms):那段回顯早就
    # 印在別處了,不能對著「現在的游標往上 N 列」改寫 —— 會蓋到模型輸出。
    read_message.prebuffered = (time.monotonic() - t0) < 0.05
    lines = [first]
    try:
        while True:
            ready, _, _ = select.select([stdin], [], [], drain_seconds)
            if not ready:
                break
            nxt = stdin.readline()
            if not nxt:
                break
            lines.append(nxt.rstrip("\n"))
    except (OSError, ValueError, TypeError):
        pass
    return "\n".join(lines)


def display_width(text):
    w = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def echo_rows(prompt, message, cols):
    """剛才那則輸入在終端上佔了幾列(含提示符、含自動換行)。"""
    cols = max(int(cols or 80), 1)
    rows = 0
    for i, line in enumerate(message.split("\n")):
        w = display_width((prompt if i == 0 else "") + line)
        rows += max(1, -(-w // cols))
    return rows


def rewrite_echo(prompt, message, stream=None, cols=None, prefix=None):
    """把終端回顯的 `codexbot> <稿>` 改寫成 `<prefix><稿>`(prefix 預設 `you> `;main 傳 echo_prefix_for 算出來的)。

    為什麼:送出後那一行留在捲動區,前綴跟活提示符一模一樣,`composer_state`
    在回讀太快時會把它當成**還沒送出的草稿**(09-24 實測:訊息已送到、模型已
    回話,`aib dm` 收據仍 rc=3「未能確認送出」)。回顯行換一個前綴,歷史與
    活輸入框就分得開,而且不必碰 dm 那段有事故史的確認邏輯。
    只在 tty 上做;算錯列數最壞是畫面少一行,不影響送出。
    """
    stream = stream or sys.stdout
    if os.environ.get(REWRITE_ECHO_ENV, "1") == "0":
        return False
    try:
        if not stream.isatty():
            return False
        if cols is None:
            cols = shutil.get_terminal_size((80, 24)).columns
        rows = echo_rows(prompt, message, cols)
        lines = message.split("\n")
        pre = ECHO_PREFIX if prefix is None else prefix
        body = pre + lines[0] + "".join("\n" + " " * display_width(pre) + ln for ln in lines[1:])
        stream.write("\x1b[%dA\r\x1b[J%s\n" % (rows, body))
        stream.flush()
        return True
    except (OSError, ValueError):
        return False


ORIGIN_AIB = "[AIB DM"            # 經 AIB 進來的一律這個:另一席的 aib dm、或有人在 AIB 席提示符打字
ORIGIN_CHATGPT = "[ChatGPT DM]"    # 桌面 ChatGPT 打的。橋加不了字(那些回合不經橋),寫在說明裡讓模型自己套
TAIPEI_OFFSET = 8 * 3600


def local_stamp(now=None):
    """發出那一刻的**當地時間**,秒級。+0800 印「台北」,其他時區印「當地 …±HHMM」。

    ★ the maintainer 09-24 直令(程式規範):每一則轉給模型的訊息都要帶發出的當地時間,
      「不然不知時間」—— 另一席的 dm 有抬頭時間,席位操作者直接打的沒有,模型分不出先後。
      規範正本:docs/criteria/RULING-codexbot-origin-tag-local-time-0924.md;
      尺:tests/test-codexbot-bridge-io-9a2be.py OriginTag。
    """
    lt = time.localtime(now)
    off = lt.tm_gmtoff
    hms = time.strftime("%Y-%m-%d %H:%M:%S", lt)
    if off == TAIPEI_OFFSET:
        return "台北 " + hms
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return "當地 %s %s%02d%02d" % (hms, sign, off // 3600, (off % 3600) // 60)


SYSTEM_SENDER_LABELS = ("開席通知", "排序暫存", "系統", "巡查", "aib-system")
SYSTEM_NOTICE_PHRASES = ("aib seat onboard --window", "開席單選單", "已自動載入全部", "已自動不載入",
                         "你的選擇是:【不載入】", "你的選擇是：【不載入】")


def is_system_notice(line):
    """AIB 自己的系統通知(開席選單、排序暫存、巡查…)不是對話,不轉給模型。

    the maintainer 2026-09-24:「把系統通知濾掉,不要轉進對話」。兩種判法,命中任一就算:
      1. dm 抬頭的寄件者不是席(不以 @ 開頭)且是已知系統標籤 —— `[開席通知 > @我 / …]`。
      2. 正文含開席選單那幾句固定字面 —— 它們有時會以**某一席**當寄件人送出
         (開席鏈是從開席者的殼裡跑的,dm-ctl 把開席者推成寄件人),抬頭看不出是系統。
    源頭(seat_onboard.py)對橋接席已經不投了;這裡是第二道網,擋別的系統發送者與舊碼。
    """
    text = (line or "").strip()
    got = _dm_header_parse(text)     # 包 I:系統寄件者只看第三段 `系統@aib`(舊式白話名由 dm_header 對到同一類)
    if got is not None and got["sender_kind"] == "system":
        return True
    return any(ph in text for ph in SYSTEM_NOTICE_PHRASES)


def tag_origin(line, now=None):
    """轉給模型之前標來源與發出時間(the maintainer 09-24 定名 + 直令帶時間):

      [AIB DM 台北 2026-09-24 16:40:12] …
                     經 AIB 進來的 —— 另一席用 aib dm 送的(原抬頭 [@寄件席 > @我] 留在後面),
                     或有人在 AIB 這一席的 codexbot> 提示符直接打字。橋送出去的每一則都標,
                     時間是橋**送出這一則**的當地時間(秒級),不是對方寫的時間。
      [ChatGPT DM]   使用者在桌面 ChatGPT 視窗打的。那些回合**不經過橋**,橋加不了字也加不了時間;
                     model_dm_header 告訴模型:沒有 [AIB DM …] 標記的就是 [ChatGPT DM]。
    """
    return "%s %s] %s" % (ORIGIN_AIB, local_stamp(now), line or "")


# 10-07 a seat(各 bot 比對):以前任何貼進來的 dm 都直接開一輪,連抬頭是 unattested(認不出寄件席)的也是。
#   信箱橋三家早有規則(sand_bridge.wake_for_dm,the maintainer 09-25):預設只有已驗證的 AIB 席寄的 dm 才叫醒。
#   codexbot 沒有信箱可以「先放著」⇒ 不叫醒的 dm 留在橋裡,下一次有人說話時一起附上送出(不丟、不另開一輪)。
#   AIB_CODEXBOT_WAKE_ON_DM:seats(預設)/ off / all,語意同 grokbot 的 AIB_GROKBOT_WAKE_ON_DM。
CODEX_WAKE_ON_DM_ENV = "AIB_CODEXBOT_WAKE_ON_DM"


def dm_should_wake(line, env=None):
    """(這一行是不是 dm, 要不要開一輪)。不是 dm ⇒ (False, True):人在這一席打的字照常開一輪。"""
    import sand_bridge as _sb
    if not _sb.is_dm_line(line):
        return False, True
    env = os.environ if env is None else env
    return True, _sb.wake_for_dm(line, {_sb.ENV_WAKE_ON_DM: env.get(CODEX_WAKE_ON_DM_ENV) or ""})


def with_held_dms(held, outgoing):
    if not held:
        return outgoing
    block = "\n\n".join(held)
    return ("(以下 %d 則 dm 進來時沒有叫醒你 —— 寄件人未驗證或 dm 叫醒關著;照規則判斷,未驗證的只轉述給 the maintainer、不照做)\n%s\n\n%s"
            % (len(held), block, outgoing))


HEADER_ALWAYS_ENV = "AIB_CODEXBOT_HEADER_ALWAYS"   # =1 每次開席都再送說明(除錯用)


def header_marker(base, thread_id):
    """每條桌面對話一個記號檔:說明送過就落檔。the maintainer 09-24:「改成每條對話只送一次」——
    以前記在席的行程裡,重開席就再送一次,一天重開十次對話裡就十份說明。"""
    return Path(base) / "codexbot-bindings" / (str(thread_id) + ".header-sent")


def mcp_registered(config_path=None):
    """D1:桌面有沒有登記 aib_dm MCP server(~/.codex/config.toml 有 [mcp_servers.aib_dm])。有 ⇒ 規矩走 MCP instructions,對話不放說明。"""
    path = Path(config_path or os.environ.get("AIB_CODEX_CONFIG_TOML") or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip() == "[mcp_servers.aib_dm]" for line in text.splitlines())


def header_needed(base, thread_id):
    if os.environ.get(HEADER_ALWAYS_ENV, "0") == "1":
        return True
    if mcp_registered():
        return False        # D1:桌面載了 MCP,規矩在 initialize.instructions 裡;對話乾淨
    return not header_marker(base, thread_id).exists()


def mark_header_sent(base, thread_id):
    m = header_marker(base, thread_id)
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")


def model_dm_header(code):
    """第一則轉給模型的訊息前面,一次性說明它在 AIB 裡怎麼發 dm。

    走的是 aib_dm_tool 既有的文字工具後備(`<aib_tool>{…}</aib_tool>` 整則獨佔),
    不是散文 `/dm` —— 檔頭規則寫得很清楚:模型散文裡的 /dm 草稿永不送。
    """
    return _rules.conversation_header(code)   # D1:字面單一來源 codexbot_rules.py


def model_dm_outcomes(texts, runner=None, enabled=None):
    """從模型的最終訊息裡取出工具呼叫並執行;回要印的通知列表。

    · 只認 aib_dm_tool.text_tool_call(整則 fullmatch),散文 /dm 不送(檔頭規則)。
    · 模型口頭宣稱已送出而沒有工具呼叫 ⇒ 印出「不算送達」,不替它送。
    """
    if enabled is None:
        enabled = os.environ.get(MODEL_DM_ENV, "1") != "0"
    if not enabled:
        return []
    runner = runner or _aib_dm.handle_dm_tool_call
    out = []
    for text in texts:
        call = _aib_dm.text_tool_call(text)
        if call and call.get("name") == _aib_dm.DM_TOOL_NAME:
            result = runner(call.get("name"), call.get("arguments") or {})
            out.append("codexbot: [模型自主 dm] " + str(result))
        elif os.environ.get("AIB_CODEXBOT_STRICT_CLAIM", "0") == "1" and _aib_dm.UNVERIFIED_DM_CLAIM.search(text or ""):
            # 09-24 桌面有了真的 aib_dm MCP 工具後,模型會用人話說「已送給 @…」而橋只看到文字 ⇒ 這條提醒會誤報。
            # 預設關;要嚴格判就 AIB_CODEXBOT_STRICT_CLAIM=1。
            out.append("codexbot: [提醒] 模型口頭宣稱已送出,但沒有 aib_dm 工具呼叫 —— 不算送達。")
    return out


MODE_ENV = "AIB_CODEXBOT_MODE"                  # writer(預設)| queue
RECONNECT_ENV = "AIB_CODEXBOT_RECONNECT"        # =0 斷線不重連(舊行為:印一行、等下一句才死)
try:
    import codexbot_server as _server  # noqa: E402 —— 自管 server 的 ensure/health(同目錄)
except Exception:  # noqa: BLE001
    _server = None


def _server_managed_endpoint():
    return "unix://" + str(_server.server_dir() / "app-server.sock") if _server is not None else ""
RENAME_ENV = "AIB_CODEXBOT_RENAME"              # =0 不把席碼寫進桌面對話名稱
TAKEOVER_ENV = "AIB_CODEXBOT_QUEUE_TAKEOVER_SECS"  # 佇列項沒人吃超過這麼久,AIB 自己暫時接手
POLL_ENV = "AIB_CODEXBOT_QUEUE_POLL_SECS"
POLL_TURNS_ENV = "AIB_CODEXBOT_POLL_TURNS"   # C3:每輪只讀最近幾個回合(thread/turns/list desc);0 = 全量 thread/read
POLL_TURNS_DEFAULT = 5
WATCHDOG_ENV = "AIB_CODEXBOT_APPROVAL_WATCHDOG_SECS"  # 接手期間回合卡在等核准超過這麼久 ⇒ 中斷+放手
WATCHDOG_DEFAULT = 180.0
QUEUE_PAUSED_HINT = "(中斷會讓桌面「佇列已暫停」;桌面有排隊的稿要在桌面按 ▶ 繼續)"   # A2
AUTO_APPROVE_TOOLS = ("aib_dm",)          # 接手期間唯一自動核准的工具(它自己有 R30 lint/停投名單)
_TOOL_KEYS = frozenset(("tool", "toolName", "name", "server", "serverName", "mcpServer", "command"))


def _mentions_tool(params, names=AUTO_APPROVE_TOOLS):
    """params 的工具/伺服器欄位有沒有點到白名單工具。只看具名欄位,不做全文 grep(reason 文字裡提到不算)。"""
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in _TOOL_KEYS and isinstance(v, str) and any(n in v for n in names):
                    return True
                if k in _TOOL_KEYS and isinstance(v, list) and any(isinstance(e, str) and any(n in e for n in names) for e in v):
                    return True
                if walk(v):
                    return True
        elif isinstance(x, list):
            return any(walk(e) for e in x)
        return False
    return walk(params)


def approval_decision(method, params, holding):
    """AIB 接手期間對 server→client 請求的答法。回 (result, error, note);三個都 None = 不答(舊行為:等桌面)。

    09-24 死結:AIB 接手後模型叫工具要核准,AIB 這邊沒有人會按 ⇒ 回合永遠 waitingOnApproval ⇒ 不放手 ⇒ 桌面卡「重試」。
    規則(the maintainer 09-24):**aib_dm 自動核准、其餘一律回拒並印明**。回拒用 decline(回合繼續,模型自己收尾),不用 cancel。
    形狀來源:`codex app-server generate-json-schema`(0.156.1)ServerRequest 十種;MCP 工具核准在 schema 裡沒有專屬方法,
    所以每筆請求另外整包落 approvals.jsonl,抓到真形狀再收緊。
    """
    if not holding:
        return None, None, None
    p = params or {}
    allow = _mentions_tool(p)
    tag = "codexbot: [接手中] "
    if method == "item/commandExecution/requestApproval":
        cmd = p.get("command") if isinstance(p.get("command"), str) else " ".join(map(str, p.get("command") or []))
        if allow:
            return {"decision": "accept"}, None, tag + "自動核准 aib_dm(%s)" % cmd[:80]
        return {"decision": "decline"}, None, tag + "回拒指令核准(AIB 席無人可按):%s" % cmd[:120]
    if method == "execCommandApproval":                      # 舊版 server 的同一件事
        if allow:
            return {"decision": "approved"}, None, tag + "自動核准 aib_dm(legacy)"
        return {"decision": "denied"}, None, tag + "回拒指令核准(legacy):%s" % str(p.get("command"))[:120]
    if method == "item/fileChange/requestApproval":
        return {"decision": "decline"}, None, tag + "回拒改檔核准(AIB 席無人可審):%s" % str(p.get("reason") or "")[:120]
    if method == "applyPatchApproval":
        return {"decision": "denied"}, None, tag + "回拒改檔核准(legacy)"
    if method == "item/permissions/requestApproval":
        if allow:
            return {"permissions": p.get("permissions") or {}}, None, tag + "自動核准 aib_dm 的權限請求"
        return {"permissions": {}}, None, tag + "回拒額外權限請求(一項都不給):%s" % str(p.get("reason") or "")[:120]
    if method == "mcpServer/elicitation/request":
        return {"action": "decline"}, None, tag + "回拒 MCP 伺服器提問(%s):AIB 席無人可答" % p.get("serverName")
    if method == "item/tool/requestUserInput":
        return {"answers": {}}, None, tag + "工具要問人,AIB 席無人可答,回空答案"
    if method == "item/tool/call":
        return None, "AIB 席不提供動態工具:%s" % p.get("tool"), tag + "回拒動態工具呼叫:%s" % p.get("tool")
    return None, "AIB 接手中,無人可答:%s" % method, tag + "回拒未知請求:%s" % method


def route_message(msg, event, rpc, holding, notice, log_path):
    """A1(09-24)RPC 的總入口:server 請求(帶 id)先過答覆器 —— 接手中橋自己答(aib_dm 核准、其餘回拒並印明),
    每筆整包落 approvals.jsonl;沒接手或不是請求就交回 event(印「等待桌面核准」是 event 的舊行為)。"""
    if "id" in msg and "method" in msg:
        if handle_server_request(msg, rpc, holding, notice=notice, log_path=log_path):
            return
    event(msg)


def handle_server_request(msg, rpc, holding, notice=lambda text: None, log_path=None):
    """帶 id 的 server 請求走這裡。一定落檔(抓真形狀);接手中就答;沒接手不答也不印(交回舊路徑)。回 True=已答。"""
    if log_path is not None:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"epoch": time.time(), "holding": bool(holding), "request": msg}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    result, error, note = approval_decision(msg.get("method"), msg.get("params") or {}, holding)
    if result is None and error is None:
        return False
    try:
        rpc.respond(msg.get("id"), result=result, error=error)
    except Exception as exc:
        notice("codexbot: [接手中] 回覆核准請求失敗:%s" % str(exc)[:120])
        return False
    notice(note)
    return True


def queue_submit(rpc, thread_id, text):
    """把一則排進 thread 的佇列(`thread/queue/add`),**不需要寫入權**。

    the maintainer 09-24「最終要讓 aib 與 chatgpt 可以同時運作」。同一條 thread 只准一個寫入者,
    但佇列在共用的 ~/.codex 庫裡:誰此刻持有這條 thread、閒下來就吃佇列、跑那一回合
    (09-24 實測:持有者閒置 <10 秒就吃掉並回話)。所以 AIB 不必搶寫入權,桌面 ChatGPT
    照常可以打字;AIB 送進去的排在後面,由持有者執行。
    """
    r = rpc.call("thread/queue/add", {"threadId": thread_id, "clientUserMessageId": str(uuid.uuid4()),
                                      "input": [{"type": "text", "text": text}]}, timeout=30)
    return ((r or {}).get("queuedSubmission") or {}).get("id")


def thread_replay_rows(thread, kind: str = "codexbot"):
    """桌面對話(thread/read)⇒ 重放用的 [(who, text, when)]:userMessage 是真人、agentMessage 是 Codex;工具項目不放。
    userMessage 的字在 content[*].text(桌面 app-server 的形狀),保守也認 text 欄。"""
    rows = []
    for turn in (thread or {}).get("turns") or []:
        for it in turn.get("items") or []:
            t = it.get("type")
            if t == "agentMessage":
                rows.append((kind, str(it.get("text") or ""), ""))
            elif t == "userMessage":
                c = it.get("content")
                txt = it.get("text") if isinstance(it.get("text"), str) else ""
                if isinstance(c, list):
                    txt = "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") in ("text", "input_text"))
                elif isinstance(c, str):
                    txt = c
                rows.append(("human", txt, ""))
    return rows


def replay_thread(thread, session_id: str, human: str, out=None) -> int:
    """開席重放(10-09):TTY 且卡片沒關才印;失敗不擋綁定。回印了幾則。"""
    try:
        import bridge_cards as _bc
        from seat_address import uuid_tail5
        return _bc.print_replay("codexbot", thread_replay_rows(thread), seat=uuid_tail5(session_id) or "", human=human,
                                out=out, source="桌面對話")
    except Exception:  # noqa: BLE001
        return 0


def tool_card_line(item):
    """C2(09-24):桌面工具卡的一行字(queue 模式從 thread/read 讀回的項目)。不是工具項目回 None。
    模型只叫工具沒說話時,AIB 席原本什麼都看不到;現在至少看得到「叫了什麼、跑到哪」。"""
    t = item.get("type")
    status = item.get("status") or ""
    if t == "mcpToolCall":
        head = "工具 %s/%s" % (item.get("server"), item.get("tool"))
        err = item.get("error")
        tail = (" 失敗:%s" % str(err.get("message"))[:80]) if isinstance(err, dict) and err.get("message") else ""
    elif t == "commandExecution":
        cmd = item.get("command")
        cmd = cmd if isinstance(cmd, str) else " ".join(map(str, cmd or []))
        head = "指令 %s" % cmd[:80]
        tail = "" if item.get("exitCode") in (None, 0) else " rc=%s" % item.get("exitCode")
    elif t == "dynamicToolCall":
        head, tail = "工具 %s" % item.get("tool"), ""
    elif t == "webSearch":
        head, tail, status = "搜尋 %s" % str(item.get("query") or "")[:80], "", status or "done"
    else:
        return None
    line = "%s · %s%s" % (head, status or "?", tail)
    return "codexbot: [桌面工具] " + MODEL_CTRL_RE.sub("", line.replace("\n", " "))


class ReadPoller:
    """輪詢 `thread/read`(不需寫入權),只吐**新出現的** agentMessage 與工具卡(C2:同一項目狀態變了算新的)。第一次只記底、不吐。"""

    seen_tools = None   # C2;放類別層不放 __init__(到達性閘算不出 __init__ 的入口)
    paged = None        # C3:None=還沒試、True=走分頁、False=server 沒有 turns/list(退回全量讀)
    last_thread = None  # 最後一次讀到的 thread dict(main 拿它的 name/preview 更新標題行)

    def __init__(self, rpc, thread_id):
        self.rpc, self.thread_id = rpc, thread_id
        self.seen = None

    def _read_recent(self):
        """C3(09-24):對話一長,每 3 秒 `thread/read includeTurns=True` 整段歷史(實測 49 回合 = 1.5 MB 一次)。
        改成 `thread/read includeTurns=False`(狀態)+ `thread/turns/list limit=N desc`(最近 N 回合含 items),
        讀量與對話長度無關。server 沒有 turns/list、或回的形狀不對(沒有 data)⇒ 退回全量讀,之後不再試。
        回的 thread dict 把 turns 補成「時間順序的最近 N 回合」,下游(看門狗找 inProgress、放手看 idle)不必改。"""
        try:
            n = int(os.environ.get(POLL_TURNS_ENV, str(POLL_TURNS_DEFAULT)) or POLL_TURNS_DEFAULT)
        except ValueError:
            n = POLL_TURNS_DEFAULT
        if n <= 0 or self.paged is False:
            self.paged = False
            return self.rpc.call("thread/read", {"threadId": self.thread_id, "includeTurns": True}, timeout=30)["thread"]
        th = self.rpc.call("thread/read", {"threadId": self.thread_id, "includeTurns": False}, timeout=30)["thread"]
        try:
            page = self.rpc.call("thread/turns/list", {"threadId": self.thread_id, "limit": n, "sortDirection": "desc"}, timeout=30)
        except BridgeError as exc:
            if not method_missing(exc):
                raise
            page = None
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            self.paged = False           # 沒這個方法 / 形狀不對:退回全量讀(舊治具回 {} 也走這裡)
            return self.rpc.call("thread/read", {"threadId": self.thread_id, "includeTurns": True}, timeout=30)["thread"]
        self.paged = True
        th["turns"] = list(reversed(page["data"]))   # desc → 時間順序
        return th

    def _items(self):
        th = self._read_recent()
        self.last_thread = th
        out, tools = [], []
        for turn in th.get("turns") or []:
            for it in turn.get("items") or []:
                if it.get("type") == "agentMessage" and it.get("id"):
                    out.append((it["id"], it.get("text") or "", turn.get("id")))
                elif it.get("id"):
                    line = tool_card_line(it)
                    if line:
                        tools.append(((it["id"], it.get("status") or ""), line, turn.get("id")))
        return th, out, tools

    def poll(self):
        th, items, tools = self._items()
        if self.seen is None:
            self.seen = {i for i, _, _ in items}
            self.seen_tools = {k for k, _, _ in tools}
            return th, [], []
        new = [(i, t, tid) for i, t, tid in items if i not in self.seen]
        self.seen.update(i for i, _, _ in new)
        new_tools = [(k, line, tid) for k, line, tid in tools if k not in self.seen_tools]
        self.seen_tools.update(k for k, _, _ in new_tools)
        return th, new, new_tools


class QueueSupervisor:
    """佇列模式的狀態機:誰都不持有時,AIB 自己**暫時**接手把佇列跑完,再放手。

      · 每一輪:讀回新回話(ReadPoller)。
      · 佇列非空且已等超過 takeover_secs:試 `thread/resume`。
          - 撞到 already has an active writer ⇒ 桌面持有,它閒下來會吃,繼續等。
          - 成功 ⇒ 我成了持有者(server 會自己吃佇列、跑回合);holding=True。
      · holding 且佇列空且 thread idle ⇒ `thread/unsubscribe` 放手(server 帶
        thread_unload_delay_secs 幾秒後卸載,桌面就能接回);holding=False。
    紀律:能不接手就不接手 —— 接手是為了「沒人在」的情況,不是為了搶。
    """

    # A1 看門狗狀態(類別預設;main 依 env 蓋 watchdog_secs。放這裡不放 __init__:__init__ 的到達性閘算不出入口)
    watchdog_secs = WATCHDOG_DEFAULT
    waiting_since = None            # 接手中、回合卡在 waitingOnApproval 從什麼時候開始
    blocked_until_drained = False   # 看門狗放手後:佇列沒被看見空過一次,不准再接手(否則同一則卡稿無限循環)

    stuck_cooldown_secs = 60.0      # 放手後多久內不再接手(不然同一則壞稿每 20 秒接手→失敗→放手無限循環)
    cooldown_until = 0.0
    on_error = None                 # callable(exc):main 用它把錯誤分類寫進 status.json(health=degraded)

    def __init__(self, rpc, thread_id, takeover_secs=20.0, clock=time.monotonic, on_error=None):
        self.rpc, self.thread_id = rpc, thread_id
        self.takeover_secs, self.clock = takeover_secs, clock
        self.holding = False
        self.pending_since = None
        self.poller = ReadPoller(rpc, thread_id)
        self.notes = []
        self.on_error = on_error

    def _report(self, exc):
        if self.on_error is not None:
            try:
                self.on_error(exc)
            except Exception:  # noqa: BLE001 —— 回報失敗不得影響狀態機
                pass

    def _release_stuck(self, status, now):
        """接手中、對話狀態不是 idle/active(10-07 真機:401 之後 server 把對話留在壞狀態)⇒ 放手。
        以前只有「佇列空且 idle」才放 ⇒ AIB 握著寫入鎖不放,桌面顯示「已在另一個應用程式中開啟」,只有重起 server 能解。"""
        params = {"threadId": self.thread_id}
        try:
            self.rpc.call("thread/unsubscribe", params, timeout=15)
        except Exception as exc:
            self.notes.append("codexbot: 對話狀態 %s,放手失敗:%s" % (status, str(exc)[:120]))
            self._report(exc)
            return
        self.holding = False
        self.cooldown_until = now + self.stuck_cooldown_secs
        self.notes.append("codexbot: 接手中對話狀態變成 %s(不是 idle/active),已放手讓桌面可接回;%d 秒後再試接手"
                          % (status, int(self.stuck_cooldown_secs)))
        self._report(BridgeError("thread status=%s while holding" % status, kind="thread-status"))

    @staticmethod
    def waiting_on_approval(thread):
        status = thread.get("status") or {}
        return status.get("type") == "active" and "waitingOnApproval" in (status.get("activeFlags") or [])

    def _watchdog(self, th, now):
        """A1 看門狗:接手中卡等核准超過 watchdog_secs ⇒ turn/interrupt + unsubscribe 放手。
        (自動答核准是第一道;這是第二道:答不到、答了沒用、或形狀沒見過時,不讓桌面永遠卡「重試」。)"""
        if not self.holding or self.watchdog_secs <= 0 or not self.waiting_on_approval(th):
            self.waiting_since = None
            return False
        self.waiting_since = self.waiting_since or now
        if now - self.waiting_since < self.watchdog_secs:
            return False
        turn_id = next((t.get("id") for t in th.get("turns") or [] if t.get("status") == "inProgress" and t.get("id")), None)
        try:
            if turn_id:
                self.rpc.call("turn/interrupt", {"threadId": self.thread_id, "turnId": turn_id}, timeout=15)
            # 字面故意與 step 的放手行不同:queue-mode 尺的刀錨要求那一行恰好出現一次
            self.rpc.call("thread/unsubscribe", dict(threadId=self.thread_id), timeout=15)
        except Exception as exc:
            self.notes.append("codexbot: 看門狗中斷/放手失敗:%s" % str(exc)[:120])
            return False
        self.holding = False
        self.waiting_since = None
        self.blocked_until_drained = True
        head = "codexbot: 看門狗:回合等核准 %d 秒沒人答,已中斷本輪並放手;佇列清空前不再接手。" % int(self.watchdog_secs)
        # A2:turn/interrupt 會讓桌面顯示「由於你中斷了回應,佇列已暫停」。放手之後(不能在放手前:自己還是持有者,
        #   佇列一動就又在自己身上跑)試 thread/queue/start 程式化恢復;不行才叫人按 ▶。
        try:
            self.rpc.call("thread/queue/start", {"threadId": self.thread_id}, timeout=15)
        except Exception as exc:
            self.notes.append(head + "恢復佇列失敗(%s):桌面若顯示「佇列已暫停」,要在桌面按 ▶ 繼續。" % str(exc)[:100])
        else:
            self.notes.append(head + "桌面佇列已自動恢復(thread/queue/start)。")
        return True

    def _queue_len(self):
        try:
            return len(self.rpc.call("thread/queue/list", {"threadId": self.thread_id}, timeout=15).get("data") or [])
        except Exception:
            return None   # 量不到 ≠ 0

    def step(self):
        """回新回話;工具卡(C2)走 notes(poll_loop 本來就印 notes,不必改它)。"""
        th, new, tools = self.poller.poll()
        self.notes.extend(line for _k, line, _t in tools)
        qlen = self._queue_len()
        now = self.clock()
        if self._watchdog(th, now):
            return new                      # 剛放手這一步不做別的(尤其不准立刻再接手)
        status_type = (th.get("status") or {}).get("type")
        if self.holding and status_type not in ("idle", "active"):
            self._release_stuck(status_type, now)
            return new
        if qlen is None:
            # 量不到 ≠ 空:不接手也不放手(放手要正面看見佇列是空的)。尺 test_unmeasurable_queue_is_not_zero 釘住。
            return new
        if qlen and self.blocked_until_drained:
            return new                      # 看門狗放手過、同一批還在:等持有者(桌面)處理或清掉
        if qlen:
            self.pending_since = self.pending_since or now
            # takeover_secs <= 0 = **永不接手**(09-24 死結:AIB 接手後模型叫 aib_dm MCP 工具要核准,
            #   AIB 這邊沒有人會按核准 ⇒ 回合永遠 waitingOnApproval ⇒ 不放手 ⇒ 桌面卡在「重試」。
            #   在「AIB 持有時自動核准 aib_dm」做好之前,預設不接手,訊息留在共用佇列等持有者吃)。
            if self.takeover_secs > 0 and not self.holding and now - self.pending_since >= self.takeover_secs \
                    and now >= self.cooldown_until:
                try:
                    self.rpc.call("thread/resume", {"threadId": self.thread_id}, timeout=30)
                    self.holding = True
                    self.notes.append("codexbot: 佇列 %d 秒沒人吃,AIB 暫時接手執行(跑完就放手)" % int(self.takeover_secs))
                except Exception as exc:
                    if "active writer" in str(exc):
                        pass      # 桌面持有,它閒下來會吃
                    else:
                        kind, human = classify_error(exc)
                        self.notes.append("codexbot: 接手失敗:%s(%s)" % (human, str(exc)[:120]))
                        self.cooldown_until = now + self.stuck_cooldown_secs
                        self._report(exc)
        else:
            self.pending_since = None
            self.blocked_until_drained = False
            if self.holding and (th.get("status") or {}).get("type") == "idle":
                try:
                    self.rpc.call("thread/unsubscribe", {"threadId": self.thread_id}, timeout=15)
                except Exception as exc:
                    self.notes.append("codexbot: 放手失敗:%s" % str(exc)[:120])
                else:
                    self.holding = False
                    self.notes.append("codexbot: 佇列跑完,已放手(桌面可接回)")
        return new


class TurnUX:
    """Render one turn without letting background events corrupt input()."""

    TERMINAL = frozenset(("completed", "interrupted", "failed"))
    # A3(09-24):這些項目一旦 item/started 而還沒 item/completed,就是「工具在跑」——長工具回合幾分鐘沒任何事件是正常的,
    # 靜默逾時在這期間不算(否則 --silence-timeout 120 秒會誤殺它)。Ctrl-C 仍然隨時可中斷。
    TOOL_KINDS = frozenset(("commandExecution", "mcpToolCall", "dynamicToolCall", "webSearch",
                            "collabAgentToolCall", "subAgentActivity", "imageGeneration"))
    open_tools = frozenset()   # begin() 換成可變集合;放類別層不放 __init__(到達性閘算不出 __init__ 的入口)

    @staticmethod
    def silence_expired(progress, timeout_seconds):
        if progress.get("tool_open"):
            return False
        return bool(timeout_seconds and progress["silent_ms"] >= round(timeout_seconds * 1000))

    def __init__(self, thread_id, stream=None, clock=time.monotonic):
        self.thread_id = thread_id
        self.stream = stream or sys.stdout
        self.clock = clock
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.active = False
        self.turn_id = None
        self.sent_at = None
        self.last_activity_at = None
        self.accepted_at = None
        self.first_output_at = None
        self.completed_at = None
        self.turn_status = None
        self.turn_error = None
        self.item_text = {}
        self.final_items = set()
        self.stream_open = False

    def begin(self):
        with self.condition:
            self.active = True
            self.turn_id = None
            self.sent_at = self.clock()
            self.last_activity_at = self.sent_at
            self.accepted_at = None
            self.first_output_at = None
            self.completed_at = None
            self.turn_status = None
            self.turn_error = None
            self.item_text = {}
            self.final_items = set()
            self.stream_open = False
            self.open_tools = set()

    @staticmethod
    def result_turn_id(result):
        if not isinstance(result, dict):
            return None
        turn = result.get("turn") or {}
        return turn.get("id") or result.get("turnId")

    def bind_result(self, result):
        turn_id = self.result_turn_id(result)
        if not turn_id:
            raise BridgeError("App Server accepted input without returning a turn id")
        with self.condition:
            if self.turn_id is not None and self.turn_id != turn_id:
                raise BridgeError("turn/start response does not match streamed turn")
            self.turn_id = turn_id
            self.accepted_at = self.clock()
            self.condition.notify_all()
        return turn_id

    def cancel(self):
        with self.condition:
            self.active = False
            self.condition.notify_all()

    def _write(self, text):
        if not text:
            return
        with self.write_lock:
            self.stream.write(text)
            self.stream.flush()

    prompt_up = False   # main 在 input() 等稿時設 True(類別預設;不動 __init__)
    last_input = ""     # main 最近一次讀走的那一行(第一行);notice() 用它認出 readline 殘留的舊字
    title_text = ""     # 提示符上面那一行(title_line);空 = 不印

    def prompt_block(self):
        """重畫提示符時要印的東西:標題行(有的話)+ 提示符。"""
        return (self.title_text + "\n" if self.title_text else "") + PROMPT

    def notice(self, text):
        with self.condition:
            prefix = "\n" if self.stream_open else ""
            self.stream_open = False
        if self.prompt_up:
            # 09-24 22:4x 實測:queue 模式的回話由輪詢執行緒印,而 input() 正等在 `codexbot> ` 後面 ⇒ 畫面變成
            #   `codexbot> codexbot< 已送出…`,dm 引擎把它讀成**未送出的草稿**,之後每一則 dm 都被擋(block_kind=draft),
            #   直到有人按 Enter。修法:清掉提示符那一行、印通知、再把提示符(與人打到一半的字)重畫回最後一行。
            try:
                import readline
                buf = readline.get_line_buffer()
            except Exception:
                buf = ""
            # ★ 09-25 a seat(艙 9260 a seat 實見):macOS 的 readline 是 libedit,input() 回來之後、下一個按鍵之前,
            #   get_line_buffer() 還是**上一則已送出的字**。照畫 ⇒ 每印一次通知,提示符後面就冒出舊字,
            #   跟人打到一半的稿逐字同形 ⇒ dm 引擎判 draft、之後每封都被擋(16:43 起 14 封)。
            #   跟最近讀走的那一行相同 ⇒ 當成殘留,不畫。真人剛好又打了一模一樣的字 ⇒ 這一幀少畫,下一個按鍵就回來。
            if buf and buf.strip() == (self.last_input or "").strip():
                buf = ""
            self._write("\r\033[2K" + text + "\n" + self.prompt_block() + buf)
            return
        self._write(prefix + text + "\n")

    def _claim_turn(self, params):
        event_turn = params.get("turnId")
        if not event_turn:
            event_turn = (params.get("turn") or {}).get("id")
        if not self.active:
            return False
        if self.turn_id is not None and event_turn and event_turn != self.turn_id:
            return False
        if self.turn_id is None and event_turn:
            self.turn_id = event_turn
        return True

    # 10-07 a seat(各 bot 比對):模型的字會直接印在人的終端機上 ⇒ 濾控制字元(ESC 等),只留換行與 tab。
    #   與 sand_bridge.OUTBOX_CTRL_RE 同一個判準(信箱橋三家早就濾了,只有這裡沒濾)。只濾模型的字,不濾我們自己的提示符。
    MODEL_CTRL_RE = MODEL_CTRL_RE   # 與模組層同一個(標題行、工具卡也用)

    def _render_delta(self, item_id, delta):
        if not item_id or not isinstance(delta, str) or not delta:
            return
        with self.condition:
            if self.first_output_at is None:
                self.first_output_at = self.clock()
            prefix = ""
            if not self.stream_open:
                prefix = _pfx()
                self.stream_open = True
            self.item_text[item_id] = self.item_text.get(item_id, "") + delta
        self._write(prefix + self.MODEL_CTRL_RE.sub("", delta))

    def _render_final(self, item):
        item_id = item.get("id")
        text = item.get("text", "")
        if not item_id or item_id in self.final_items or not isinstance(text, str):
            return False
        with self.condition:
            previous = self.item_text.get(item_id, "")
            shown = self.MODEL_CTRL_RE.sub("", text)
            if text.startswith(previous):
                tail = self.MODEL_CTRL_RE.sub("", text[len(previous):])
                if self.stream_open:
                    output = tail + "\n"
                elif not previous:
                    output = _pfx() + shown + "\n"
                elif tail:
                    output = _pfx() + tail + "\n"
                else:
                    output = ""
            else:
                # The final item is authoritative. A mismatch is rare, but it
                # is safer to show the corrected complete answer on a new line
                # than silently preserve a malformed delta sequence.
                output = ("\n" if self.stream_open else "") + _pfx() + shown + "\n"
            if self.first_output_at is None and text:
                self.first_output_at = self.clock()
            self.item_text[item_id] = text
            self.final_items.add(item_id)
            self.stream_open = False
        self._write(output)
        return True

    def handle(self, msg):
        method = msg.get("method")
        params = msg.get("params", {})
        if params.get("threadId") not in (None, self.thread_id):
            return False
        with self.condition:
            if not self._claim_turn(params):
                return False
            if method.startswith("item/") or method in ("turn/started", "turn/completed"):
                self.last_activity_at = self.clock()
            started = (params.get("item") or {}) if method in ("item/started", "item/completed") else {}
            if method == "item/started" and started.get("type") in self.TOOL_KINDS and started.get("id"):
                self.open_tools = set(self.open_tools) | {started["id"]}
            elif method == "item/completed" and started.get("id"):
                self.open_tools = set(self.open_tools) - {started["id"]}
        if method == "item/agentMessage/delta":
            self._render_delta(params.get("itemId"), params.get("delta"))
            return True
        if method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                return self._render_final(item)
            return False
        if method == "turn/completed":
            turn = params.get("turn", {})
            with self.condition:
                self.turn_status = turn.get("status") or "failed"
                self.turn_error = turn.get("error")
                self.completed_at = self.clock()
                self.condition.notify_all()
            return True
        if method == "item/started" or method == "turn/started" or "/requestApproval" in method:
            return True
        return False

    def reconcile(self, turn):
        if turn.get("id") != self.turn_id:
            raise BridgeError("thread/read returned a different completed turn")
        rendered = False
        for item in turn.get("items", []):
            if item.get("type") == "agentMessage":
                rendered = self._render_final(item) or rendered
        status = turn.get("status")
        if status in self.TERMINAL:
            with self.condition:
                if self.turn_status is None:
                    self.turn_status = status
                    self.turn_error = turn.get("error")
                    self.completed_at = self.clock()
                self.condition.notify_all()
        return rendered

    def wait_step(self, timeout=0.25):
        with self.condition:
            if self.turn_status not in self.TERMINAL:
                self.condition.wait(timeout)
            return self.snapshot()

    def snapshot(self):
        with self.condition:
            now = self.clock()
            base = self.sent_at if self.sent_at is not None else now
            first_ms = None if self.first_output_at is None else round((self.first_output_at - base) * 1000)
            completed_ms = None if self.completed_at is None else round((self.completed_at - base) * 1000)
            activity = self.last_activity_at if self.last_activity_at is not None else base
            return {"turn_id": self.turn_id, "turn_status": self.turn_status,
                    "elapsed_ms": round((now - base) * 1000),
                    "silent_ms": round((now - activity) * 1000),
                    "first_output_ms": first_ms, "completed_ms": completed_ms,
                    "tool_open": len(self.open_tools),
                    "turn_error": self.turn_error}


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".codexbot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread-id", default=(os.environ.get("AIB_CODEXBOT_THREAD_ID") or os.environ.get("AIB_CODEXAPP_THREAD_ID") or _legacy_codex_thread_id()))
    ap.add_argument("--endpoint", default=(os.environ.get("AIB_CODEXBOT_ENDPOINT") or os.environ.get("AIB_CODEXAPP_ENDPOINT") or ""))
    ap.add_argument("--session-id")  # AIB seat UUID, NOT desktop thread/session ID
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mode", choices=("writer", "queue"), default=(os.environ.get(MODE_ENV) or "writer"),
                    help="writer=AIB 持有寫入權(預設);queue=不搶寫入權,用佇列投遞、輪詢讀回,桌面可同時打字")
    ap.add_argument("--silence-timeout", type=float,
                    default=os.environ.get("AIB_CODEXBOT_SILENCE_TIMEOUT", "120"),
                    help="interrupt a turn after this many seconds with no turn/item events; 0 disables")
    args = ap.parse_args(argv)
    if args.silence_timeout < 0:
        ap.error("--silence-timeout must be 0 or greater")
    launch_thread = args.thread_id      # 開席命令帶的那條;席上 /bind 的選擇以它為鍵記下(與 sand_bridge 同)
    rebind_to = [None]
    if not args.check and args.session_id and args.thread_id:
        args.thread_id, _bind_note = apply_bind_override(_state_base(), args.session_id, args.thread_id)
        if _bind_note:
            print(_bind_note, file=sys.stderr, flush=True)
    rpc, lock_file, root, dm_server = None, None, None, None
    closer = None   # 收尾小幫手:橋突然死時由它把桌面名稱改回未綁定
    if not args.check and threading.current_thread() is threading.main_thread():
        # [X] 關窗 / 艙收掉時 pane 收到 SIGHUP;預設動作是直接死,finally 不跑 ⇒ 關席收尾(改回未綁定)做不到。
        #   改成正常退出(SystemExit 不被下面的 except 吃掉,finally 會跑)。
        def _on_hup(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGHUP, _on_hup)

        def _on_usr1(signum, frame):
            raise Disconnected("與本機 Codex server 斷線")
        signal.signal(signal.SIGUSR1, _on_usr1)

        def _on_usr2(signum, frame):
            # P6:只在提示符等字時才丟(看守送訊號前判過一次,這裡再判一次:中間有人按了 Enter 就不丟,下一輪再說)
            # review r1:只在主執行緒真的卡在 input() 裡才丟 —— 貼上多行時 input() 已回、還在吸後面幾行(prompt_up 仍 True),
            #   這時丟會把已讀到的那則(例如一封貼進來的 dm)整則丟掉
            if reload_armed[0] and in_input[0] and not ux.active:
                raise Reload()
        signal.signal(signal.SIGUSR2, _on_usr2)
    # 10-07 a seat:記下本行程 pid —— 以前沒有,行程死了 status.json 仍停在 connected,右欄與 aib bot reap 都判不了
    #   (真機 10-07:一隻 codexbot 卡 connected 8 小時以上)。
    state = {"schema": "aib-codexbot/v1", "provider": "codexbot",
             "desktop_thread_id": args.thread_id, "state": "connecting", "pid": os.getpid()}
    state_lock = threading.Lock()
    ux = TurnUX(args.thread_id)
    holding_probe = [lambda: False]   # queue 模式建好 supervisor 後換成「AIB 現在有沒有接手」

    def save(**updates):
        with state_lock:
            state.update(updates, updated_epoch=time.time())
            if root:
                atomic_json(root / "status.json", state)

    reconnecting = [False]
    reload_armed = [False]       # P6:看守判定該重開了才 True(訊號處理器只認這個)
    in_input = [False]           # 主執行緒此刻在 input() 裡(read_message 第一行)
    reloading = [False]
    last_input_at = [time.monotonic()]

    def health_bad(exc):
        """任何一步失敗 ⇒ status.json health=degraded + 人話。state 維持 connected(連線確實在)。
        右欄/doctor 看 health 不看 state:10-07 真機「state=connected 但一句都跑不動」就是 state 說不出來的。"""
        kind, human = classify_error(exc)
        save(health="degraded", last_error_kind=kind, last_error=human, last_error_epoch=time.time())
        if kind == "auth":
            start_device_login(ux.notice, save)
        return kind, human

    def health_ok():
        if state.get("health") != "ok":
            save(health="ok", last_error_kind=None, last_error=None)

    def record_reply(item, turn_id, recovered=False):
        if root and item.get("id"):
            reply_id = str(uuid.uuid5(uuid.NAMESPACE_URL, args.thread_id + ":" + str(item["id"])))
            receipt = root / "outbox" / (reply_id + ".json")
            if not receipt.exists():
                atomic_json(receipt, {"thread_id": args.thread_id, "item": item,
                                      "turn_id": turn_id, "received_epoch": time.time(),
                                      "recovered_from_thread_read": bool(recovered)})
        save(last_reply_epoch=time.time())

    def event(msg):
        params = msg.get("params", {})
        if msg.get("method") == "bridge/error":
            save(state="disconnected", health="down", last_error_kind="disconnected",
                 last_error=HUMAN_ERRORS["disconnected"], error=params.get("message"))
            ux.notice("codexbot: desktop disconnected(%s)" % str(params.get("message") or "")[:80])
            if not args.check and threading.current_thread() is not threading.main_thread() \
                    and os.environ.get(RECONNECT_ENV, "1") != "0":
                # 主執行緒可能卡在 input()/rpc.call:用訊號把它叫醒,以 RECONNECT_RC 結束,開席器重開同一席。
                reconnecting[0] = True
                try:
                    os.kill(os.getpid(), signal.SIGUSR1)
                except OSError:
                    pass
            return
        # Some lifecycle notifications (notably turn/completed) identify the
        # target only through params.turn.id. TurnUX still requires the active
        # turn id to match before accepting those events.
        if params.get("threadId") not in (None, args.thread_id):
            return
        handled = ux.handle(msg)
        if "id" in msg:
            ux.notice("codexbot: 等待 Codex Desktop 核准...")
            return
        if handled and msg.get("method") == "item/started":
            kind = (params.get("item") or {}).get("type")
            progress_labels = {
                "commandExecution": "codexbot: 正在執行指令...",
                "fileChange": "codexbot: 正在整理檔案變更...",
                "webSearch": "codexbot: 正在搜尋...",
                "mcpToolCall": "codexbot: 正在使用工具...",
                "dynamicToolCall": "codexbot: 正在使用工具...",
            }
            if kind in progress_labels:
                ux.notice(progress_labels[kind])
        item = params.get("item", {})
        if msg.get("method") == "item/completed" and item.get("type") == "agentMessage":
            # A real desktop reply is evidence, merely connecting is not.
            record_reply(item, params.get("turnId"))

    try:
        validate_target(args.thread_id, args.endpoint)
        if not args.check:
            if not args.session_id:
                raise BridgeError("missing AIB seat session UUID")
            uuid.UUID(args.session_id)
            base = Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME")
                        or Path.home() / ".ez2-aib")
            # 10-07 a seat:這段桌面對話正被 AIB 自己活著的 codex 席用著 ⇒ 不綁(兩席串線;選單層同一判準 codexbot_threads.held_by)
            import codexbot_threads as _cbt
            _own = _cbt.live_codex_seat_threads(base).get(str(args.thread_id).lower())
            if _own:
                raise BridgeError("這段桌面對話是 AIB codex 席 @%s 正在用的,不能再綁成 codexbot(會兩席串線);"
                                  "請在 Codex 桌面另開一段對話再綁" % _own)
            locks = base / "codexbot-bindings"
            locks.mkdir(parents=True, exist_ok=True)
            lock_file = (locks / (args.thread_id + ".lock")).open("a+")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                if not wait_for_own_closer(lock_file, _closer.closer_marker(base, args.thread_id)):
                    raise BridgeError("this desktop thread already has a live AIB bridge") from exc
            root = base / "bridge" / args.session_id
            save(session_uuid=args.session_id)
            if os.environ.get(RENAME_ENV, "1") != "0":
                # 關席改回未綁定:正常結束時 finally 自己做;突然死(關窗 / 被殺)時由小幫手做(bot_closer.py)。
                #   在拿到對話鎖之後立刻生(以前放在綁定改名之後,頭一秒內死掉就沒人收尾;09-25)。
                #   副作用:拿到鎖之後、綁定改名之前就失敗,小幫手會把這條對話標成「( 未綁定AIB席位 )」——那一刻確實沒綁定。
                closer = _closer.spawn("codexbot", {"thread_id": args.thread_id, "session_id": args.session_id,
                                                    "endpoint": args.endpoint}, base)
                if closer is None:
                    print("codexbot: 收尾小幫手起不來;橋若突然死,桌面名稱不會改回未綁定", flush=True)
            # B1:橋開本機 socket 收 MCP 的 dm 單,在這個行程(席 pane 的子行程)跑 run_aib_dm ⇒ 抬頭是 @席碼
            try:
                dm_server = _dm_socket.DmSocketServer(_dm_socket.socket_path(base, args.thread_id), _aib_dm.run_aib_dm, notice=ux.notice)
                save(dm_socket=str(dm_server.start()))
            except OSError as exc:
                dm_server = None
                ux.notice("codexbot: dm socket 開不起來(%s);桌面 MCP 的 dm 會直送、抬頭 unattested" % str(exc)[:120])
        # 10-07:自管 server 由橋自己 ensure(以前只有 aib 開席那條路會做;respawn-pane / 開席器重連那兩條路不經過 aib,
        #   server 死了橋就連不上、rc 2、席死)。位址不是自管的那顆(自訂 ws://)不動。
        logged_in = None
        if _server is not None and args.endpoint == _server_managed_endpoint():
            try:
                _server.ensure_server()
            except _server.ServerError as exc:
                raise BridgeError(str(exc), kind="server-down") from exc
            # 登入只在自管 server 這條路查:它用 ~/.codex 的登入;桌面 app 自己的 app-server 登入歸桌面管。
            logged_in = check_login(strict=bool(args.check))
        # A1:RPC 的訊息先過 route_message(接手中自己答核准),其餘照舊給 event
        rpc = RPC(connect(args.endpoint),
                  lambda msg: route_message(msg, event, rpc, holding_probe[0](), ux.notice,
                                            (root / "approvals.jsonl") if root else None))
        desktop = Desktop(rpc, args.thread_id)
        mode = args.mode
        # queue 模式(含 --check):只 read 不 resume。桌面正持有那條對話時 resume 會撞 active writer,
        # 而 queue 模式本來就不需要寫入權 —— 09-24 the maintainer 開著桌面時右鍵開席被 --check 擋下,就是這裡。
        thread = desktop.initialize(subscribe=(not args.check) and mode == "writer",
                                    load=(mode == "writer"))
        if args.check:
            print(json.dumps({"desktop_thread_id": thread["id"],
                              "desktop_session_id": thread["sessionId"], "state": "connected"}))
            return 0
        title = str(thread.get("name") or thread.get("preview") or "").strip().replace("\n", " ")[:60]
        save(state="connected", desktop_session_id=thread["sessionId"], desktop_thread_title=title,
             health=("degraded" if logged_in is False else "ok"),
             last_error_kind=("auth" if logged_in is False else None),
             last_error=(HUMAN_ERRORS["auth"] if logged_in is False else None))
        if logged_in is False:
            ux.notice("codexbot: " + HUMAN_ERRORS["auth"] + "(連線在,但送字會失敗)")
            start_device_login(ux.notice, save)
        print("AIB Codex desktop bridge -> " + args.thread_id + "(app-server %s)" % desktop.server_version
              + (" 對話「%s」" % title if title else ""), flush=True)   # B4:席上看得到綁的是哪條對話
        account = desktop_account(rpc)                          # 第 22 件:席上看得到用的是哪個 Codex 帳號
        save(codex_account=account or None)
        print("codexbot: 這條對話用的 Codex 帳號:%s" % (account or "讀不到"), flush=True)
        ux.title_text = title_line(title, args.thread_id)       # 提示符上面那一行;改名時 poll_loop 更新

        def refresh_title(new_title):
            """桌面改名 ⇒ 換標題行並印一行。queue 模式每輪順便做;writer 模式走輕輪詢(TITLE_REFRESH_EVERY)。"""
            if not new_title:
                return
            line = title_line(new_title, args.thread_id)
            if line == ux.title_text:
                return
            ux.title_text = line
            save(desktop_thread_title=new_title)
            ux.notice("codexbot: 對話改名 ⇒ " + line)
        # the maintainer 09-25:綁定後把 AIB 席碼掛在桌面對話名稱後面(例 `CodexBot 開發01 ( @xxxxx )`),
        #   命名規則與 Grok 共用 bot_seat_name。改名失敗不讓綁定失敗:印一行實話,繼續。
        #   ★ 桌面 ChatGPT 畫面會不會即時跟著變:未驗(等 bot 重開真機看)。
        if os.environ.get(RENAME_ENV, "1") != "0":
            current_name = str(thread.get("name") or "").strip()
            try:
                from seat_address import uuid_tail5
                wanted = _seat_name.with_seat_tag(current_name or title, uuid_tail5(args.session_id), fallback="CodexBot")
                if wanted != current_name:
                    rpc.call("thread/name/set", {"threadId": args.thread_id, "name": wanted}, timeout=15)
                    refresh_title(wanted)
                    print("codexbot: 已把桌面對話名稱改成「%s」" % wanted, flush=True)
            except Exception as exc:  # noqa: BLE001 — 改名是附加功能,任何失敗都不得擋綁定
                print("codexbot: 沒把席碼寫進桌面對話名稱(%s);綁定照常" % str(exc)[:120], flush=True)

        human_label = human_name()                                # 回顯行「真人(名字)> 」;開席算一次
        print("Connected to existing conversation. Approvals remain in the desktop app. /dm <@席> <訊> 走原生 aib dm。", flush=True)
        print(_aib_dm.dm_boot_banner(), flush=True)
        print("指令：/status 看橋狀態 · Ctrl-C 中斷本輪 · /quit 離開 · /dm <@席> <訊>", flush=True)
        if ux.title_text:
            print(ux.title_text, flush=True)     # 第一個提示符上面也有標題行
        print("模型自主 dm：整則回覆只放 <aib_tool>{…aib_dm…}</aib_tool>,橋代送並印收據(%s=0 關閉)" % MODEL_DM_ENV, flush=True)
        print("模式：%s（writer=AIB 持有寫入權;queue=不搶寫入權、佇列投遞、桌面可同時打字。/handoff 放手轉 queue,/attach 接回轉 writer）" % mode, flush=True)
        replay_thread(thread, args.session_id, human_label)   # 10-09 開席重放:桌面對話最近幾則畫成卡(橋接席已有,codexbot 補齊)
        save(mode=mode)
        supervisor = QueueSupervisor(rpc, args.thread_id,
                                     takeover_secs=float(os.environ.get(TAKEOVER_ENV, "20") or 20),
                                     on_error=health_bad)
        supervisor.watchdog_secs = float(os.environ.get(WATCHDOG_ENV, str(WATCHDOG_DEFAULT)) or WATCHDOG_DEFAULT)
        holding_probe[0] = lambda: supervisor.holding
        poll_secs = float(os.environ.get(POLL_ENV, "3") or 3)
        poll_stop = threading.Event()

        def poll_loop():
            _title_tick = [0]
            while not poll_stop.wait(poll_secs):
                if rpc.closed.is_set():
                    continue
                if mode_box[0] != "queue":
                    # ★ 09-25 the maintainer:「用戶改了名稱,何時更新?」queue 模式每輪 thread/read 順便更新;
                    #   writer 模式原本整段 continue ⇒ **改名永遠不更新,要重開席**。這裡補一條只讀標題的輕輪詢
                    #   (thread/read 不帶 turns,不需寫入權、不干擾回合),週期 TITLE_REFRESH_EVERY × poll_secs。
                    _title_tick[0] += 1
                    if _title_tick[0] % TITLE_REFRESH_EVERY == 0:
                        try:
                            th = rpc.call("thread/read", {"threadId": args.thread_id, "includeTurns": False}, timeout=20)["thread"]
                            refresh_title(str(th.get("name") or th.get("preview") or "").strip()[:60])
                        except Exception:
                            pass      # 讀不到就下一輪再說,不吵
                    continue
                try:
                    new = supervisor.step()
                except Exception as exc:
                    kind, human = health_bad(exc)
                    ux.notice("codexbot: 佇列輪詢失敗:%s(%s)" % (human, str(exc)[:120]))
                    continue
                if new:
                    health_ok()
                for item_id, text, turn_id in new:          # 先印回話,再印接手/放手的通知(順序才對得上事實)
                    ux.notice(_pfx() + text)
                    record_reply({"id": item_id, "type": "agentMessage", "text": text}, turn_id)
                    if model_dm_enabled:
                        for note in model_dm_outcomes([text]):
                            ux.notice(note)
                for note in supervisor.notes:
                    ux.notice(note)
                supervisor.notes.clear()
                new_title = str((supervisor.poller.last_thread or {}).get("name") or (supervisor.poller.last_thread or {}).get("preview") or "").strip()[:60]
                refresh_title(new_title)
        mode_box = [mode]
        poll_thread = threading.Thread(target=poll_loop, name="codexbot-queue-poll", daemon=True)
        poll_thread.start()
        model_dm_enabled = os.environ.get(MODEL_DM_ENV, "1") != "0"
        header_sent = not header_needed(base, args.thread_id)   # 每條對話只送一次(記號在 codexbot-bindings/)
        held_dms = []   # 10-07:沒叫醒的 dm(dm_should_wake),下一次說話時一起送
        bind_rows = []   # 席上 /bind 上一次列出的清單(/bind <編號> 照它挑)

        def reload_watch():
            try:
                import idle_reaper as _ir
            except Exception:  # noqa: BLE001 —— 看守是附加的
                return
            fp0 = _ir.reexec_fingerprint()
            while not rpc.closed.wait(RELOAD_CHECK_S):
                if not reload_enabled():
                    continue
                fp = _ir.reexec_fingerprint()
                if not fp0:
                    fp0 = fp
                    continue
                busy_until = max(last_input_at[0], ux.completed_at or 0.0, ux.last_activity_at or 0.0) \
                    if ux.clock is time.monotonic else last_input_at[0]
                if reload_due(fp0, fp, time.monotonic() - busy_until, ux.prompt_up, ux.active):
                    if not reload_armed[0]:
                        try:
                            _ir.reexec_log(os.path.abspath(__file__), fp0, fp)
                        except Exception:  # noqa: BLE001
                            pass
                    reload_armed[0] = True
                    # review r1:處理器當下不在 input() 裡就不丟(例如剛好按了 Enter);不 return,下一輪再送,不然這個行程永遠不重開
                    os.kill(os.getpid(), signal.SIGUSR2)

        if threading.current_thread() is threading.main_thread():
            threading.Thread(target=reload_watch, name="codexbot-reload-watch", daemon=True).start()

        def guarded_input(prompt):
            _inp = globals().get("input", input)   # 與 read_message 同一種查法(測試換 builtins.input 也吃得到)
            in_input[0] = True
            try:
                return _inp(prompt)
            finally:
                in_input[0] = False
        persona_sent = _bridge_persona is None   # 基因+人格包:每條對話 × 每一席一次(記號在 codexbot-bindings/<對話>.<席碼>/)
        my_code = (args.session_id or "")[-5:]
        try:
            import readline  # noqa: F401 — arrow keys / edit if available
        except Exception:
            pass
        while not rpc.closed.is_set():
            ux.prompt_up = True
            try:
                raw = read_message(PROMPT, _input=guarded_input)
            except EOFError:
                break
            finally:
                ux.prompt_up = False
            ux.last_input = raw.split("\n", 1)[0]    # notice() 認 readline 殘留用(只有第一行經過 readline)
            last_input_at[0] = time.monotonic()
            line = raw.strip()
            if line and not getattr(read_message, "prebuffered", False):
                rewrite_echo(PROMPT, raw, prefix=echo_prefix_for(raw, human_label))
            if line in ("/quit", "/exit", ":q"):
                break
            if line in ("/status", "/redraw"):
                print(json.dumps(state, ensure_ascii=False, sort_keys=True), flush=True)
                continue
            if line == "/handoff":
                # 放手:unsubscribe ⇒ server 幾秒後卸載 ⇒ 桌面「重試」就能接回。之後改走佇列投遞。
                try:
                    rpc.call("thread/unsubscribe", {"threadId": args.thread_id}, timeout=15)
                except Exception as exc:
                    print("codexbot: 放手失敗:%s" % str(exc)[:160], flush=True); continue
                mode_box[0] = mode = "queue"; supervisor.holding = False; save(mode=mode)
                print("codexbot: 已放手(幾秒後桌面可接回)。現在是 queue 模式:你打的字會排進佇列,由持有者執行;/attach 接回。", flush=True)
                continue
            if line == "/attach":
                try:
                    desktop.attach()
                except Exception as exc:
                    kind, human = health_bad(exc)
                    print("codexbot: 接回失敗:%s(%s)" % (human, str(exc)[:160]), flush=True); continue
                supervisor.holding = False
                mode_box[0] = mode = "writer"; save(mode=mode)
                print("codexbot: 已接回寫入權,writer 模式。", flush=True)
                continue
            if line == "/unbind":
                print("codexbot: CodexBot 席一定要綁一條 Codex 對話,不能只解除;要換別條打 /bind", flush=True)
                continue
            if line == "/bind" or line.startswith("/bind "):
                arg = line[len("/bind"):].strip()
                if not arg:
                    try:
                        bind_rows = codex_bind_rows(rpc, args.endpoint, args.thread_id, base)
                    except Exception as exc:  # noqa: BLE001
                        print("codexbot: 清單讀不到(%s);可以直接打 /bind <完整 UUID>" % str(exc)[:120], flush=True)
                        continue
                    for ln in render_codex_bind_list(bind_rows, args.thread_id):
                        print(ln, flush=True)
                    continue
                want, err = resolve_codex_bind_arg(arg, bind_rows, args.thread_id)
                if not want:
                    print("codexbot: " + err, flush=True)
                    continue
                import codexbot_threads as _cbt2
                _held = _cbt2.held_by(want, base)     # 先擋:重開後才發現被占,橋起不來、席就死了
                if _held:
                    print("codexbot: 綁不上:這條正被 %s 使用" % "、".join(_held), flush=True)
                    continue
                if _cbt2.thread_exists(args.endpoint, want, rpc=rpc) is False:
                    print("codexbot: 綁不上:Codex 桌面找不到 %s" % want[:8], flush=True)
                    continue
                from sand_bridge import save_bind_override
                save_bind_override(root, launch_thread, want)
                rebind_to[0] = want
                print("codexbot: 換綁到 Codex 對話 %s:先把現在這條的桌面名稱改回未綁定,再重開這一席接新的那條(席碼不變)…"
                      % want, flush=True)
                break
            if not line:
                continue
            if is_system_notice(line):
                ux.notice("codexbot: [系統通知,不轉進對話] " + line[:80].replace("\n", " "))
                continue
            slash = _aib_dm.parse_dm_slash(line)
            if slash:
                target, body = slash
                outcome = _aib_dm.run_aib_dm(target, body)
                print(outcome, flush=True)
                continue
            _is_dm, _wake = dm_should_wake(line)
            if not _wake:
                held_dms.append(tag_origin(line))
                ux.notice("codexbot: 這則 dm 不開一輪(寄件人未驗證或 dm 叫醒關著),先留著;你下一次說話時一起附上(%s=all 可改)"
                          % CODEX_WAKE_ON_DM_ENV)
                continue
            message_id = str(uuid.uuid4())
            message = {"id": message_id, "thread_id": args.thread_id,
                       "text": line, "state": "pending", "created_epoch": time.time()}
            path = root / "inbox" / (message_id + ".json")
            atomic_json(path, message)
            outgoing = with_held_dms(held_dms, tag_origin(line))
            held_dms.clear()   # 送失敗也不再留:已經併進這一則(這一則留在 inbox 有紀錄)
            if model_dm_enabled and not header_sent:
                outgoing = model_dm_header(my_code) + "\n\n" + outgoing
                header_sent = True
                try:
                    mark_header_sent(base, args.thread_id)
                except OSError as exc:
                    ux.notice("codexbot: 說明記號寫不進去(%s),下次開席會再送一次" % str(exc)[:80])
            persona_attached = False
            pack_what = ""
            if not persona_sent:
                # the maintainer 09-30 19:5x:橋接席也要收到人格包。桌面對話沒有系統說明可塞、開席也不自己開一輪(會燒額度)
                #   ⇒ 接在這一席送出的第一則前面(送的路就是下面同一條 desktop.send / queue_submit)。
                # ★ review r1 m6:記號與 persona_sent 都等**送成功**才寫(送失敗 ⇒ 下一則再接);
                #   這一次沒東西可接(人格包/基因都是空的)⇒ 不算送過,之後每一則再試,直到真的送出一次。
                # ★ review r4 m3:只送過基因(那時人格還沒綁)不算送過人格包 ⇒ persona_done 看記號內容;
                #   codexbot_pack 回 (字, what),what 在送成功之後記進記號(genes / persona:<指紋>)。
                try:
                    # ★ review r9 M1:人格包送過但基因之後才打開 ⇒ 還沒完(codexbot_pack 會只補基因)
                    persona_sent = _bridge_persona.pack_done(
                        _bridge_persona.codexbot_marker_dir(base, args.thread_id, args.session_id or ""))
                    prefix, pack_what = ("", "") if persona_sent else _bridge_persona.codexbot_pack(base, args.thread_id, args.session_id or "")
                except Exception as exc:  # noqa: BLE001 —— 人格包壞掉不能擋這一則
                    prefix = ""
                    ux.notice("codexbot: 人格包讀不出來(%s),這一則照送" % type(exc).__name__)
                if prefix:
                    outgoing = prefix + "\n\n" + outgoing
                    persona_attached = True
            if mode == "queue":
                try:
                    qid = queue_submit(rpc, args.thread_id, outgoing)
                except Exception as exc:
                    # ★ review r3 m5:與 writer 那條同一個判準(review r2 m11)—— 排佇列的 rpc 逾時
                    #   (delivery uncertain)= 可能已經排進去了 ⇒ 人格包當作送過,不然下一則再接一整包。
                    if persona_attached and _send_maybe_delivered(exc):
                        persona_sent = _persona_mark_sent(base, args.thread_id, args.session_id or "", ux, pack_what)
                    kind, human = health_bad(exc)
                    print("codexbot: 排入佇列失敗:%s(%s)" % (human, str(exc)[:160]), flush=True); continue
                if persona_attached:
                    persona_sent = _persona_mark_sent(base, args.thread_id, args.session_id or "", ux, pack_what)
                if supervisor.takeover_secs > 0:
                    print("codexbot: 已排入桌面佇列(%s)。持有者閒下來就會執行;%d 秒沒人吃 AIB 會暫時接手。" % (qid, int(supervisor.takeover_secs)), flush=True)
                else:
                    print("codexbot: 已排入桌面佇列(%s)。由桌面(持有者)閒下來時執行;AIB 不接手(TAKEOVER_SECS=0)。" % qid, flush=True)
                continue
            ux.begin()
            ux.notice("codexbot: 已送出，等待回覆...")
            try:
                try:
                    result = desktop.send(outgoing)
                except Exception as exc:
                    # ★ review r2 m11:rpc 逾時(「delivery uncertain; do not automatically resend」)= 可能已經送進去了
                    #   ⇒ 人格包當作送過(寫記號),不然下一則會再接一整包、桌面收到兩份。
                    #   只有證明一個字都沒送出去的錯(no input sent、讀對話失敗…)才留到下一則再接。
                    if persona_attached and _send_maybe_delivered(exc):
                        persona_sent = _persona_mark_sent(base, args.thread_id, args.session_id or "", ux, pack_what)
                    raise
                if persona_attached:
                    persona_sent = _persona_mark_sent(base, args.thread_id, args.session_id or "", ux, pack_what)
                turn_id = ux.bind_result(result)
                message.update(state="accepted", result=result, turn_id=turn_id,
                               accepted_epoch=time.time())
                atomic_json(path, message)

                slow_notice = False
                long_notice = False
                interrupt_sent = False
                while True:
                    try:
                        progress = ux.wait_step()
                    except KeyboardInterrupt:
                        if interrupt_sent or not ux.turn_id:
                            raise
                        desktop.interrupt(ux.turn_id)
                        interrupt_sent = True
                        ux.notice("codexbot: 已要求中斷本輪，等待 Desktop 收尾..." + QUEUE_PAUSED_HINT)
                        continue
                    if progress["turn_status"] in TurnUX.TERMINAL:
                        break
                    if rpc.closed.is_set():
                        raise BridgeError("desktop disconnected while the turn was running; input was not resent")
                    if progress["first_output_ms"] is None and progress["elapsed_ms"] >= 1500 and not slow_notice:
                        ux.notice("codexbot: 思考中...")
                        slow_notice = True
                    if progress["first_output_ms"] is None and progress["elapsed_ms"] >= 10000 and not long_notice:
                        ux.notice("codexbot: 仍在處理；若需要核准，請查看 Codex Desktop。")
                        long_notice = True
                    if not interrupt_sent and TurnUX.silence_expired(progress, args.silence_timeout):
                        desktop.interrupt(ux.turn_id)
                        interrupt_sent = True
                        ux.notice("codexbot: 長時間沒有任何進展事件，已自動中斷本輪。" + QUEUE_PAUSED_HINT)

                recovered = False
                try:
                    completed_turn = desktop.find_turn(turn_id)
                    recovered = ux.reconcile(completed_turn)
                    for item in completed_turn.get("items", []):
                        if item.get("type") == "agentMessage":
                            record_reply(item, turn_id, recovered=recovered)
                except Exception:
                    if ux.snapshot()["first_output_ms"] is None:
                        raise
                progress = ux.snapshot()
                turn_status = progress["turn_status"] or "failed"
                if turn_status == "failed":
                    error = progress.get("turn_error") or {}
                    ux.notice("codexbot: 本輪失敗：" + str(error.get("message") or error))
                elif turn_status == "interrupted":
                    ux.notice("codexbot: 本輪已中斷。")
                message.update(
                    state=("answered" if turn_status == "completed" else turn_status),
                    turn_status=turn_status,
                    first_output_ms=progress["first_output_ms"],
                    completed_ms=progress["completed_ms"],
                    recovered_from_thread_read=bool(recovered),
                )
                save(last_turn_id=turn_id, last_turn_status=turn_status,
                     last_turn_first_output_ms=progress["first_output_ms"],
                     last_turn_completed_ms=progress["completed_ms"])
                if turn_status == "completed":
                    health_ok()
                elif turn_status == "failed":
                    health_bad(BridgeError(str((progress.get("turn_error") or {}).get("message") or progress.get("turn_error") or "turn failed")))
                ux.cancel()
                if model_dm_enabled and turn_status == "completed":
                    with ux.condition:
                        finals = [ux.item_text.get(i, "") for i in sorted(ux.final_items)]
                    for note in model_dm_outcomes(finals):
                        ux.notice(note)
                        if root:
                            atomic_json(root / "outbox" / ("modeldm-" + str(uuid.uuid4()) + ".json"),
                                        {"thread_id": args.thread_id, "turn_id": turn_id,
                                         "note": note, "epoch": time.time()})
            except Exception as exc:
                ux.cancel()
                message.update(state="unconfirmed", error=str(exc))
                kind, human = health_bad(exc)
                ux.notice("codexbot: " + human + "(" + str(exc)[:160] + "); no automatic retry")
                if mode == "writer" and kind in ("auth", "server-cwd", "thread-status"):
                    # 10-07 真機死結:writer 模式握著寫入鎖、回合因 401 失敗、對話卡在壞狀態 ⇒ 桌面「已在另一個應用程式中開啟」、
                    #   席「not loaded」,兩邊互指,只有重起 server 能解。改成:這三種失敗一律放手轉 queue,登入/伺服器好了再 /attach。
                    try:
                        rpc.call("thread/unsubscribe", dict(threadId=args.thread_id), timeout=15)
                    except Exception as rel:
                        ux.notice("codexbot: 放手失敗:%s" % str(rel)[:120])
                    else:
                        mode_box[0] = mode = "queue"; supervisor.holding = False; save(mode=mode)
                        ux.notice("codexbot: 已放手讓桌面可接回,改 queue 模式;問題排除後 /attach 接回寫入權")
            atomic_json(path, message)
        if rebind_to[0]:
            save(state="rebinding", rebind_to=rebind_to[0])
            return REBIND_RC
        return 0
    except Reload:
        reloading[0] = True
        save(state="reloading")
        print("codexbot: 程式已更新、席閒著 ⇒ 重開同一席載新碼(綁定不變,幾秒)", file=sys.stderr, flush=True)
        return RELOAD_RC
    except Disconnected as exc:
        reconnecting[0] = True
        save(state="disconnected", health="down", last_error_kind="disconnected", last_error=HUMAN_ERRORS["disconnected"],
             error=str(exc))
        print("codexbot: %s;以 rc=%d 結束,交給開席器重連同一席" % (exc, RECONNECT_RC), file=sys.stderr, flush=True)
        return RECONNECT_RC
    except (Exception, KeyboardInterrupt) as exc:
        kind, human = classify_error(exc)
        save(state="failed", error=str(exc), health="down", last_error_kind=kind, last_error=human)
        print("codexbot: " + (human + "(" + str(exc)[:200] + ")" if kind != "unknown" else str(exc)), file=sys.stderr)
        return 2
    finally:
        try:
            poll_stop.set()
            # 10-09 a seat:等輪詢執行緒真的停(最多 5 秒)再往下收尾 —— 以前只設旗號就走,
            #   在同一個行程裡叫 main() 的測試(codexbot-bridge、bridge-persona-pack)收暫存夾時它還在寫 state/,
            #   機器忙時撞成「Directory not empty」,推包閘門上時紅時綠。正式環境行程結束它就沒了,行為不變。
            if poll_thread is not None:
                poll_thread.join(timeout=5)
        except NameError:
            pass
        # the maintainer 09-25:關席取消舊碼 ⇒ 桌面對話名稱改成「原名 ( 未綁定AIB席位 )」。要在關連線之前做;失敗不擋關閉。
        if rpc and root and not args.check and os.environ.get(RENAME_ENV, "1") != "0" and not reloading[0] \
                and not rpc.closed.is_set():
            try:
                th = rpc.call("thread/read", {"threadId": args.thread_id, "includeTurns": False}, timeout=10)["thread"]
                cur = str(th.get("name") or "").strip()
                unbound = _seat_name.with_unbound(cur, fallback="CodexBot")
                if unbound != cur:
                    rpc.call("thread/name/set", {"threadId": args.thread_id, "name": unbound}, timeout=10)
                if closer is not None:
                    closer.done()          # 自己改好了,小幫手不用動
                    closer = None
                if unbound != cur:
                    print("codexbot: 已把桌面對話名稱改回「%s」" % unbound, flush=True)
            except Exception as exc:  # noqa: BLE001
                try:
                    print("codexbot: 沒把桌面對話名稱改回未綁定(%s)" % str(exc)[:120], flush=True)
                except OSError:
                    pass   # 關窗後終端機已不在
        if rpc:
            rpc.close()
        if dm_server is not None:
            dm_server.stop()
        if root and state.get("state") not in ("failed", "disconnected"):
            save(state="stopped")
        if closer is not None and (reconnecting[0] or reloading[0]):
            # 重連路:開席器幾秒後重開同一席,桌面名稱不改回未綁定(不然每次重連名稱閃一下「未綁定」)
            try:
                closer.done()
            except Exception:  # noqa: BLE001
                pass
            closer = None
        if closer is not None:
            # 小幫手動手前要搶這條對話的鎖;橋自己還握著就會被判成「新席已占住」而跳過(09-25 尺抓到)⇒ 先放鎖
            if lock_file is not None:
                try:
                    lock_file.close()
                except OSError:
                    pass
            closer.close()   # 沒說 DONE 就關管子 ⇒ 小幫手馬上接手(自己改名失敗時由它重試)
        if lock_file:
            lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
