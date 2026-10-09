#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""BD(2026-09-03):pane → 現任 session 的對映,讓 CTX 讀端在 /clear 之後找對檔。

失效形狀(the maintainer 09-03 03:4x 報「clear 後右欄 CTX 不更新」):
  席位檔 `session_uuid` 只在開席登記;`/clear` 讓 Claude Code 換 session id,
  statusline hook 從此改寫**新 uuid** 的 `$EZ2_CTX_DIR/<uuid>.pct`;右欄與 seat_who
  只讀**登記 uuid** 那一份,而它 6h 內(CTX_STALE_S)仍算新鮮 ⇒ 凍在舊值,一格不紅。
  證據:2dd9 @2 登記 9b9c5507 的 .pct 停 01:44;同 pane 新 session a45d949f 的 .pct
  11:34 仍在更新,且沒有任何席位檔登記它。

修法(三件同族「登記不跟身份走」之一,另兩件:AS respawn、BC 家目錄 cwd):
  寫端 —— statusline hook(`~/.claude/statusline-ez2-v215.sh`,不在 repo)每次量到
  CTX 後,若 `$TMUX_PANE` 有值,另寫 `$EZ2_CTX_DIR/by-pane/<pane 數字>.json`:
      {"schema": "ez2-ctx-by-pane-v1", "pane_id": "%16", "session_id": "<uuid>",
       "pct": 9, "observed_at": "<與 .pct 同一個 UTC Z 時戳>", "cwd": "..."}
  讀端 —— 本模組 `resolve_live_session(registered, pane_id, since_ms)`:
      by-pane 檔的 session 與登記不同,且它的 observed_at **同時晚於**
      (a) 登記 uuid 那份 .pct 的 observed_at 與 (b) 席位開席時間 since_ms
      ⇒ 用 by-pane 的 session(回 rebound=True,列上標「↻」);否則照舊讀登記 uuid。
      (b) 擋的是 pane 被新席重用而新席的 hook 還沒開火的窗口:那時 by-pane 檔
      屬於前一任,拿它就是把前一任的 CTX 掛到新席頭上。
  唯一量尺不變:pct 仍只由 `<uuid>.pct` 經既有讀端(pane-right-ai.sh ctx_of /
  rotation.lifecycle.read_machine_ctx)量;本模組只回答「該讀哪一個 uuid」,
  不回 pct(by-pane 檔裡的 pct 欄只給人眼與除錯)。

必落空端:沒有 by-pane 檔 / 檔壞 / session 相同 / 不比登記新 / 早於開席 ⇒
  (registered, False, None),讀端行為與本修法之前逐位元組相同。
自動輪替(lifecycle 由 uuid 讀 .pct)不在本模組範圍:登記本身由 AS re-register 修。
"""
import calendar
import json
import os
import re
import stat

SCHEMA = "ez2-ctx-by-pane-v1"
BY_PANE_DIR = "by-pane"
MAX_BYTES = 65536
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PANE_RE = re.compile(r"%?([0-9]{1,9})")
# 只收 UTC(與 lifecycle._OBSERVED_RE 同紀律:無尾碼不猜本地)。
_OBS_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,6})\d*)?(?:Z|\+00:00)")


def ctx_dir(explicit=None):
    return explicit or os.environ.get("EZ2_CTX_DIR") \
        or os.path.expanduser("~/.ez2/ctx")


def pane_key(pane_id):
    """`%16` / `16` → `16`;形狀不合回 None(檔名只收數字,不讓 pane 值帶路徑)。"""
    if pane_id is None:
        return None
    m = _PANE_RE.fullmatch(str(pane_id).strip())
    return m.group(1) if m else None


_SOCKET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def socket_key(socket):
    """socket 名 → 路徑段;形狀不合回 None(不讓 socket 值帶路徑、不猜)。"""
    if not isinstance(socket, str):
        return None
    m = _SOCKET_RE.fullmatch(socket.strip())
    return m.group(0) if m else None


def by_pane_path(pane_id, ctx_root=None, socket=None):
    """缺陷三十四:**鍵**要有 socket。給了 socket ⇒ `by-pane/<socket>/<n>.json`(每艘母艦一個子目錄,
    兩艙同號 pane 不再共用一個檔);沒給 ⇒ 舊鍵 `by-pane/<n>.json`(生產寫端 statusline 尚未帶 socket
    之前它寫的就是這個)。socket 形狀不合回 None:不寫、也不讀。"""
    key = pane_key(pane_id)
    if key is None:
        return None
    if socket is None:
        return os.path.join(ctx_dir(ctx_root), BY_PANE_DIR, key + ".json")
    sk = socket_key(socket)
    if sk is None:
        return None
    return os.path.join(ctx_dir(ctx_root), BY_PANE_DIR, sk, key + ".json")


def parse_observed_utc(text):
    """UTC Z 時戳 → epoch ms;不合形狀回 None。"""
    if not isinstance(text, str):
        return None
    m = _OBS_RE.fullmatch(text.strip())
    if not m:
        return None
    try:
        secs = calendar.timegm(tuple(int(m.group(i)) for i in range(1, 7))
                               + (0, 0, 0))
    except (ValueError, OverflowError):
        return None
    frac = (m.group(7) or "0").ljust(3, "0")[:3]
    return secs * 1000 + int(frac)


def _read_json(path):
    """regular file、大小上限、合法 JSON 物件;任一不符回 None(讀不到=沒有)。"""
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return None
        blob = b""
        while len(blob) <= MAX_BYTES:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            blob += chunk
        data = json.loads(blob.decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    return data if isinstance(data, dict) else None


def pane_session(pane_id, ctx_root=None, socket=None):
    """by-pane 檔 → {session_id, observed_ms, pct, cwd, socket};沒有或壞形狀回 None。
    給了 socket ⇒ 先讀本艙的 `by-pane/<socket>/<n>.json`;沒有那份才退舊鍵 `by-pane/<n>.json`
    (舊鍵的記錄沒有 socket 維度,resolve_live_session 對它走 cwd 那道守衛)。"""
    data = None
    if socket is not None:
        scoped = by_pane_path(pane_id, ctx_root, socket)
        data = _read_json(scoped) if scoped else None
        if data is not None:
            # 與下面「檔名與內容 pane 不符=拷錯/混入」同一條紀律:落在 <socket>/ 底下的檔,
            # 內容欄必須寫著同一個 socket;缺或不同=拷錯/混入,**不用它**,而且要說出來
            # (qa 47a06 二方 test_03:只把內容欄拿掉,讀端必須擋且留痕)。
            if data.get("socket") != socket_key(socket):
                return {"reject": "by-pane %s under socket %s has socket field %r: ignored"
                        % (pane_key(pane_id), socket_key(socket), data.get("socket"))}
    if data is None:
        path = by_pane_path(pane_id, ctx_root)
        if path is None:
            return None
        data = _read_json(path)
    if data is None or data.get("schema") != SCHEMA:
        return None
    if pane_key(data.get("pane_id")) != pane_key(pane_id):
        return None                  # 檔名與內容 pane 不符=拷錯/混入
    sid = data.get("session_id")
    if not isinstance(sid, str) or not _UUID_RE.fullmatch(sid):
        return None
    observed_ms = parse_observed_utc(data.get("observed_at"))
    if observed_ms is None:
        return None                  # 沒有可比的時間就沒有「比較新」可言
    sock = data.get("socket")
    return {"session_id": sid, "observed_ms": observed_ms,
            "pct": data.get("pct"), "cwd": data.get("cwd"),
            "socket": sock if isinstance(sock, str) and sock else None}


def registered_observed_ms(uuid, ctx_root=None):
    """登記 uuid 那份 .pct 的 observed_at(ms);檔缺/壞回 None。只讀時戳,不量 pct。"""
    if not isinstance(uuid, str) or not _UUID_RE.fullmatch(uuid):
        return None
    data = _read_json(os.path.join(ctx_dir(ctx_root), uuid + ".pct"))
    if data is None:
        return None
    return parse_observed_utc(data.get("observed_at"))


def other_socket_claims(pane_id, ctx_root=None, socket=None):
    """別的 socket 子目錄裡有沒有同號 pane 的記錄(只給留痕用;不讀內容)。"""
    key = pane_key(pane_id)
    if key is None:
        return []                    # pane 形狀不合=沒有可查的鍵(k2 刀:pane None 時面板不得炸)
    base = os.path.join(ctx_dir(ctx_root), BY_PANE_DIR)
    mine = socket_key(socket)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        if name == mine or socket_key(name) != name:
            continue
        if os.path.isfile(os.path.join(base, name, key + ".json")):
            out.append(name)
    return out


def resolve_live_session(registered, pane_id, ctx_root=None, since_ms=None,
                         seat_cwd=None, socket=None):
    """回 (uuid, rebound, note)。

    rebound=True 只在:by-pane 有合法 session ≠ registered,且 observed 晚於登記
    uuid 的 .pct(缺檔視為無限舊)**且**晚於 since_ms(開席時間;None=不設下限)
    **且**那份 by-pane 檔真的是本艙本席寫的(下面那段)。
    其餘一律 (registered, False, note) —— 與修法前同路,note 只在被擋時給人看。

    ★ 缺陷三十四(09-06):by-pane 的檔名只有 pane 數字,而 pane 編號是每個 tmux
      server 各自從 %0 起算的——兩艘母艦各有一個 %8,同一個檔被兩艙輪流覆寫。
      舊艙 2dd9 的 %8(Claude,38%)與本艙 %8(codex 席)互相串線,右欄印出
      「Fable 5.1 a seat 38%↻」:一席 codex 印別艙 Claude 的模型與 CTX,而那一列
      格式正確、數字新鮮、還誠實地標了 ↻。錯在鍵少一個維度,不在標記。
    ★ 兩道守衛,失效側都站「不重綁」:
      1. 記錄帶 socket ⇒ 必須等於本艙 socket(寫端補上 socket 之後這條才有牙)。
      2. 記錄沒有 socket(今天所有生產記錄)⇒ 退而比 cwd:by-pane 的 cwd 是寫它的
         session 的工作目錄,席位檔的 cwd 是本席的;**兩邊都知道且相同**才准重綁。
         任一邊不知道 ⇒ 判不出同一性 ⇒ 不重綁(讀登記 uuid 的舊路)。
         cwd 不是完整的鍵(兩艙同目錄仍會撞),所以第 1 道才是正解,第 2 道是
         寫端還沒改之前的止血,兩道都留著。
    """
    by = pane_session(pane_id, ctx_root, socket)
    if by is None:
        # 本艙讀不到:別艙若有同號 pane 的記錄,說出來——「被擋」與「本來就沒檔」不得同形
        # (qa 47a06 二方 test_01)。
        others = other_socket_claims(pane_id, ctx_root, socket) if socket else []
        if others:
            return registered, False, (
                "by-pane %s claimed by other socket(s) %s: no rebind"
                % (pane_key(pane_id), ",".join(others)))
        return registered, False, None
    if by.get("reject"):
        return registered, False, by["reject"]
    if by["session_id"] == registered:
        return registered, False, None
    if by.get("socket"):
        if not socket or by["socket"] != socket:
            return registered, False, (
                "by-pane %s belongs to socket %s, not %s: no rebind"
                % (pane_key(pane_id), by["socket"], socket or "-"))
    else:
        same = _same_path(by.get("cwd"), seat_cwd)
        if same is not True:
            return registered, False, (
                "by-pane %s has no socket and cwd %s (seat cwd %s): no rebind"
                % (pane_key(pane_id),
                   "unknown" if not by.get("cwd") else ("differs" if same is False else "unknown"),
                   "unknown" if not seat_cwd else "known"))
    floor_ms = registered_observed_ms(registered, ctx_root)
    if floor_ms is None:
        floor_ms = -1
    if isinstance(since_ms, (int, float)) and since_ms > floor_ms:
        floor_ms = since_ms
    if by["observed_ms"] <= floor_ms:
        return registered, False, None
    return by["session_id"], True, (
        "session rebound by pane %s: %s -> %s"
        % (pane_key(pane_id), (registered or "-")[:8], by["session_id"][:8]))


def _same_path(a, b):
    """兩個 cwd 是不是同一個目錄;任一缺回 None(缺=判不出,不是不同)。"""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return None
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return None


def write_by_pane(pane_id, session_id, observed_at, pct=None, cwd=None,
                  ctx_root=None, socket=None):
    """參考寫端(治具與人手工具用;生產寫端在 statusline hook,schema 同上)。
    tmp+rename 原子寫;pane 形狀不合回 None,成功回路徑。"""
    path = by_pane_path(pane_id, ctx_root, socket)
    if path is None:
        return None
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    rec = {"schema": SCHEMA, "pane_id": "%" + pane_key(pane_id),
           "session_id": session_id, "pct": pct, "observed_at": observed_at,
           "cwd": cwd}
    if socket:
        rec["socket"] = socket
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path
