#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 發送者的「來源名」—— 機器推導,不是自報。

★ 為什麼不開環境變數讓送方自己填:`dm-ctl.sh` 的 offhub 那一段明文裁過
  「讓送方自選顯示名 = 又開一個冒充面(`agent:root-brain` 這種),本刀不開」。
  本模組**不繞過那條裁定**,而是讓它的適用前提消失 —— 名字不是送方寫的,
  是從**行程祖先鏈**上讀出來的;要冒充得先偽造自己的 ppid 鏈。

★ 09-10 HDR:長訊轉送的行程鏈常常先碰到 `provider-launch.sh`,抬頭就印
  「AI 啟動器」——那是在回答「誰啟動的」,問題問的是「哪一席寫的」。
  **席位對映優先於腳本名**:祖先鏈上的 pid 對到恰好一個活 pane、那個窗
  有恰好一份席位檔且 `session_uuid` 末五碼算得出,就用那個五碼。
  對不到、對到兩個、檔沒有 uuid,一律不猜,退回腳本名(舊行為)。
  不讀 `EZ2_AIB_SEAT` / `EZ2_AIB_AGENT` —— 那是自報,本模組不開那個冒充面。

★ 腳本名仍只認**產品碼**:`<倉根>/src/` 底下的 `.py` / `.sh`(10-12 加:裝好的外掛資料夾,見 plugins_root)。理由有二 ——
  (一)這一格要回答的問題是「本倉哪一支自動化送的」,測試 harness 不是自動化;
  (二)`tests/` 底下的臂送 dm 時**必須**仍落在「推導不出身分」那一格,
       因為它們驗的正是那一格;把 tests/ 也認進來會讓那三支臂改成驗別的東西。

★ 取不到一律回 `None`,呼叫端維持寫 `unknown`。**失效方向刻意落在低權格**:
  推導失敗不得升權、不得猜。

★ 名字的字元集與 `seat_address._ADDR_UNATTESTED_RE` 同一條:
  `[a-z0-9][a-z0-9._-]{0,31}`。不合就回 None —— 寧可 unknown,
  也不要送出一個會被抬頭白名單當場拒收的標籤(那會把降溫通知整條打死)。

★ kind 與權限**一格不動**:仍是 `unattested`,血緣閘仍走 `unattested_sender`。
  這一刀只讓抬頭說得出來源,不動任何一格權限。
"""

import glob
import json
import os
import re
import subprocess

import bg_fork

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
MAX_DEPTH = 12                      # 祖先鏈上限;夠深又不會在壞掉的鏈上繞
SCRIPT_SUFFIXES = (".py", ".sh")

# ★ 傳輸層自己不算來源。`dm-ctl.sh` 就住在 `src/cockpit/`,不排掉的話祖先鏈
#   第一格就命中它,抬頭會變成 `unattested:dm-ctl` —— 那是在回答「誰遞送的」,
#   問題問的是「誰寫的」。排掉之後繼續往上走,取真正的發起者。
# ★ 這一格是**結構性**的,所以住在模組裡而不是靠呼叫端從 $PPID 起算:
#   起算點的選擇住在殼裡,沒有任何一格會紅;寫在這裡才有臂守得到。
SKIP_NAMES = ("dm-ctl", "aib-dm", "aib-send")

# 席位碼字元集與 seat_address._CODE_RE 同一條;不合就當沒對到。
SEAT_CODE_RE = re.compile(r"^[0-9a-z]{4,8}$")
# 自報通道:讀了就把「推導」變成「對方說自己是誰」。臂釘著不讀。
SELF_REPORT_ENV = ("EZ2_AIB_SEAT", "EZ2_AIB_AGENT")


def _read_ppid(pid):
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    text = out.stdout.strip()
    return int(text) if text.isdigit() else None


def _read_cmd(pid):
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return out.stdout.strip()


def repo_root_from(here):
    """`<倉根>` = 本檔所在的 `src/cockpit` 往上兩層。呼叫端可覆寫。"""
    return os.path.abspath(os.path.join(here, "..", ".."))


#: `name_from_command` 的第三種回傳。**「是來源但名字表達不出來」不得與
#: 「這不是來源」回同一個值** —— 前者必須讓整條推導停在 unknown,後者要繼續
#: 往上走。兩者同形的話,一支名字含怪字元的腳本會讓抬頭掛上它**祖父**的名字,
#: 而那是一個看起來完全正常的錯誤歸屬。
UNNAMEABLE = object()


def name_from_command(cmd, src_root):
    """一條命令列 → 來源名。

    三種回傳,刻意分開:
      * `str`         —— 這是來源,名字是它
      * `None`        —— 這不是來源(不是 src/ 腳本,或是傳輸層自己),繼續往上找
      * `UNNAMEABLE`  —— 是來源,但名字過不了字元集;呼叫端必須停下來回 unknown

    ★ 逐 token 比對,而且比的是 **realpath**:相對路徑、`./`、symlink
      三種寫法在字串上完全不同,而它們指的是同一支檔。
    """
    if not cmd:
        return None
    src_root = os.path.realpath(src_root)
    for token in cmd.split():
        if not token.endswith(SCRIPT_SUFFIXES):
            continue
        real = os.path.realpath(token)
        # os.path.commonpath 會把 `/a/srcx` 誤判成在 `/a/src` 底下,所以補分隔符
        if not (real == src_root or real.startswith(src_root + os.sep)):
            continue
        stem = os.path.basename(real)
        for suffix in SCRIPT_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        stem = stem.strip().lower().replace("_", "-")
        if stem in SKIP_NAMES:
            continue                      # 傳輸層:不是來源,繼續往上找
        return stem if NAME_RE.match(stem) else UNNAMEABLE
    return None


def plugins_root():
    """裝好的外掛(`${EZ2_AIB_HOME:-~/.ez2-aib}/plugins`,aib_plugins 同一條)也算產品碼:
    艦長室網頁 10-12 改成外掛後,從頁面寄的 dm 抬頭仍要是「艦長室」。
    外掛是使用者自己裝的,信任等同本倉 src/;kind 照舊 unattested,不升權。"""
    return os.path.join(os.environ.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib"), "plugins")


def derive_sender_name(pid=None, repo_root=None, max_depth=MAX_DEPTH,
                       read_ppid=None, read_cmd=None):
    """走祖先鏈,回**最近**一支本倉 `src/` 產品腳本的名字;沒有就回 None。

    `read_ppid` / `read_cmd` 是具名的拆除縫:臂用它們餵合成的鏈,
    不必真的生出一棵行程樹(不然「臂跑不到那條分支」會偽裝成綠)。
    """
    read_ppid = read_ppid or _read_ppid
    read_cmd = read_cmd or _read_cmd
    root = repo_root or repo_root_from(os.path.dirname(os.path.abspath(__file__)))
    roots = (os.path.join(root, "src"), plugins_root())
    seen = set()
    cur = pid if pid is not None else os.getppid()
    for _ in range(max_depth):
        if cur is None or cur <= 1 or cur in seen:
            return None
        seen.add(cur)
        cmd = read_cmd(cur)
        name = None
        for src_root in roots:
            name = name_from_command(cmd, src_root)
            if name is not None:
                break
        if name is UNNAMEABLE:
            return None                   # 是來源但叫不出名字 ⇒ 誠實回 unknown
        if name:
            return name
        cur = read_ppid(cur)
    return None


#: 白話名字的 i18n 鍵前綴(the maintainer 09-05 令:要有一個國三生白話的名字)。
#: ★ 查不到就**不加括號**,抬頭原樣只有機器名 —— 不編一個假的白話名。
#:   `Translator.t` 查不到會回 `!!鍵!!`,那個字面必須被當成「沒有」,
#:   否則抬頭會掛上 `unattested:x(!!aib.sender.origin.x!!)`。
FRIENDLY_KEY_PREFIX = "aib.sender.origin."

#: 倉根的環境入口(缺陷十七)。只在 `__main__` 讀 —— 函式庫行為一格不動,
#: 臂要掛的鉤子就**只有一個地方**。空字串視同沒設(空的倉根會讓 src_root 變成
#: `/src`,那是一個看起來正常的錯誤答案)。
REPO_ROOT_ENV = "AIB_SENDER_ORIGIN_REPO_ROOT"

#: 白話名字裡不准出現的字元:抬頭是 `[A > B / 時間]`,
#: 而 `header_addresses` 用 `>` 與 ` / ` 切欄位。一個含 `>` 的暱稱會把抬頭切錯,
#: 而切錯之後**位址半邊仍然過得了白名單** —— 失效形狀是「送到了、抬頭掛別人的名字」。
FRIENDLY_FORBIDDEN = set(">/[]()（）")


def friendly_name(name, locale=None, i18n_dir=None):
    """機器名 → 白話名(四語);查不到、或含會切壞抬頭的字元,一律回 None。"""
    if not name:
        return None
    key = FRIENDLY_KEY_PREFIX + str(name).replace("-", "_")
    try:
        import i18n_lib
        text = i18n_lib.Translator(locale=locale, i18n_dir=i18n_dir).t(key)
    except Exception:
        return None
    if not text or text.startswith("!!"):
        return None
    text = " ".join(str(text).split())
    if not text or FRIENDLY_FORBIDDEN & set(text):
        return None
    return text



# GrokBot 席顯示名(括號修飾)。只當抬頭「GrokBot (…)」的變數段;
# 不能改機器 stem、不能升權。空／不合字元 ⇒ 不加括號。
AGENT_NAME_ENV = "AIB_GROKBOT_AGENT_NAME"
GROKBOT_STEM = "grokbot"
# 與 FRIENDLY_FORBIDDEN 同形,另加控制字元與長度上限。
AGENT_QUALIFIER_MAX = 40


def sanitize_agent_qualifier(raw):
    """括號內 Agent 顯示名;失敗回 None(寧可不加,不猜)。"""
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text or len(text) > AGENT_QUALIFIER_MAX:
        return None
    if FRIENDLY_FORBIDDEN & set(text):
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    return text


def display_label(name, locale=None, i18n_dir=None, agent_name=None):
    """畫面抬頭用的來源標籤。

    - 一般腳本:有白話名就回白話名(與 dm-ctl 現況同)。
    - grokbot:白話名「GrokBot」+ 可選「 (Agent顯示名)」;
      Agent 名來自參數或環境變數 AIB_GROKBOT_AGENT_NAME(變數,非寫死)。
    """
    if not name:
        return ""
    plain = friendly_name(name, locale=locale, i18n_dir=i18n_dir)
    if name == GROKBOT_STEM:
        base = plain or "GrokBot"
        if agent_name is None:
            import os as _os
            agent_name = _os.environ.get(AGENT_NAME_ENV)
        qual = sanitize_agent_qualifier(agent_name)
        return "%s (%s)" % (base, qual) if qual else base
    return plain or ""


def label(name, locale=None, i18n_dir=None):
    """抬頭右半要寫的字:有白話名就 `機器名(白話名)`,沒有就只有機器名。

    ★ 機器名**永遠留著**:它可 grep、可反查是哪一支腳本,而白話名是給人看的。
      只留白話名的話,收件端看到「機器狀態列」查不出對應哪一支檔。
    """
    if not name:
        return ""
    nice = friendly_name(name, locale=locale, i18n_dir=i18n_dir)
    return "%s(%s)" % (name, nice) if nice else name


def _hub_name():
    return (os.environ.get("EZ2_AIHUB_SOCKET") or "aihub").strip() or "aihub"


def _seats_dir(hub=None):
    """與 dm-ctl.sh 同一條:hub 導出 > INSTANCE_HOME > 主艙。"""
    hub = hub or _hub_name()
    home = os.environ.get("EZ2_AIB_HOME") or os.path.join(
        os.path.expanduser("~"), ".ez2-aib")
    inst = ""
    if hub.startswith("aihub-"):
        inst = hub[len("aihub-"):]
        if inst and re.search(r"[^0-9a-zA-Z-]", inst):
            inst = ""
        if inst:
            return os.path.join(home, "instances", inst, "seats")
    inst_home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
    if inst_home:
        return os.path.join(inst_home, "seats")
    return os.path.join(home, "seats")


def _live_pane_rows(hub=None):
    """活 pane 快照:`window_id, pane_pid`。取不到回空,不猜。"""
    hub = hub or _hub_name()
    try:
        out = subprocess.run(
            ["env", "-u", "TMUX", "tmux", "-L", hub, "list-panes", "-a",
             "-F", "#{window_id}|#{pane_pid}"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return ()
    rows = []
    for line in (out.stdout or "").splitlines():
        wid, _, pid = line.partition("|")
        wid, pid = wid.strip(), pid.strip()
        if re.fullmatch(r"@[0-9]{1,10}", wid) and pid.isdigit():
            rows.append((wid, int(pid)))
    return tuple(rows)


def _load_seats(seats_dir):
    """席位檔 → {window_id: rec}。schema / 檔名交叉不符就跳過。"""
    out = {}
    if not seats_dir:
        return out
    for path in sorted(glob.glob(os.path.join(seats_dir, "w*.json"))):
        base = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
            continue
        wid = str(rec.get("window_id") or "")
        if not re.fullmatch(r"@[0-9]{1,10}", wid):
            continue
        if base != "w%s.json" % wid.lstrip("@"):
            continue
        out[wid] = rec
    return out


def _code_of(rec):
    try:
        import seat_address as sa
        return sa.code_of(rec)
    except Exception:
        return None


def map_seat_five(pid=None, max_depth=MAX_DEPTH, read_ppid=None,
                  panes=None, seats=None, hub=None, seats_dir=None, read_cmd=None):
    """祖先鏈上第一個「恰好對到一個活 pane」的 pid → 席位 uuid 末五碼。

    `panes` / `seats` 是具名拆除縫:臂餵合成快照,不必真開 tmux。
    `panes is None` 才現查;`panes=()` 是「查過了,沒有」——兩者不得同形。

    ★ 10-10 a seat(the maintainer 裁):鏈上先碰到 Claude Code 的**背景分身**或它的宿主/守護程式就停、
      回 None —— 守護程式是全機共用的,再往上爬到的是「第一個用背景工作的席」,不是寄件人
      (真機:44ed3 的分身被記成 a seat)。分身本身由 `map_bg_fork` 認,這裡只負責不冒認。
    """
    enabled = True  # HDR-seat-map-on
    if not enabled:
        return None
    read_ppid = read_ppid or _read_ppid
    read_cmd = read_cmd or _read_cmd
    if panes is None:
        panes = _live_pane_rows(hub)
    if seats is None:
        seats = _load_seats(seats_dir if seats_dir is not None else _seats_dir(hub))
    pid_to_wids = {}
    for wid, pane_pid in panes:
        pid_to_wids.setdefault(int(pane_pid), []).append(wid)
    seen = set()
    cur = pid if pid is not None else os.getppid()
    for _ in range(max_depth):
        try:
            cur_pid = int(cur)
        except (TypeError, ValueError):
            return None
        if cur_pid <= 1 or cur_pid in seen:
            return None
        seen.add(cur_pid)
        _cmd = read_cmd(cur_pid) or ""
        if bg_fork.fork_of(_cmd) or bg_fork.is_bg_host(_cmd):
            return None               # 分身/宿主:停,不往上認「生守護程式的那一席」
        wids = pid_to_wids.get(cur_pid)
        if wids is not None:
            uniq = []
            for wid in wids:
                if wid not in uniq:
                    uniq.append(wid)
            if len(uniq) != 1:
                return None               # 同一 pid 對到兩窗:不猜
            rec = seats.get(uniq[0]) if isinstance(seats, dict) else None
            code = _code_of(rec) if rec else None
            if code and SEAT_CODE_RE.match(code):
                return code
            return None                   # 對到窗但沒有合法五碼:停,不往上找祖父窗
        cur = read_ppid(cur_pid)
    return None


def map_bg_fork(pid=None, max_depth=MAX_DEPTH, read_ppid=None, read_cmd=None):
    """祖先鏈上先碰到的 Claude Code 背景分身 → **原席**的 uuid 末五碼(分身是那一席的工人,
    回信要回到那一席);鏈上沒有分身、或先碰到宿主,回 None。分身 uuid 不當席碼(它不在任何 pane)。"""
    read_ppid = read_ppid or _read_ppid
    read_cmd = read_cmd or _read_cmd
    start = pid if pid is not None else os.getppid()
    fk = bg_fork.walk_to_fork(start, read_ppid, read_cmd, max_depth=max_depth)
    if not fk:
        return None
    try:
        import seat_address as sa
        code = sa.uuid_tail5(fk[1])
    except Exception:
        return None
    return code if code and SEAT_CODE_RE.match(code) else None


def derive_origin(pid=None, repo_root=None, max_depth=MAX_DEPTH,
                  read_ppid=None, read_cmd=None,
                  panes=None, seats=None, hub=None, seats_dir=None):
    """回 `(kind, name)`:`bgfork`(原席五碼)> `seat` 五碼 > `script` 腳本莖,都沒有就 `(None, None)`。
    bgfork 排最前:分身的鏈再往上會爬到別席的 pane,先認分身才不會冒認。"""
    code = map_bg_fork(pid=pid, max_depth=max_depth, read_ppid=read_ppid, read_cmd=read_cmd)
    if code:
        return "bgfork", code
    code = map_seat_five(pid=pid, max_depth=max_depth, read_ppid=read_ppid,
                         panes=panes, seats=seats, hub=hub, seats_dir=seats_dir,
                         read_cmd=read_cmd)
    if code:
        return "seat", code
    name = derive_sender_name(pid=pid, repo_root=repo_root, max_depth=max_depth,
                              read_ppid=read_ppid, read_cmd=read_cmd)
    if name:
        return "script", name
    return None, None


def relay_pointer_line(nbytes, path, seat_five=None, bg_fork_of=False):
    """長訊指標。有席碼就寫進「來自 @五碼」,沒有就維持舊字面(短訊測試仍綠);
    `bg_fork_of=True` ⇒ 「來自 @五碼 的背景分身」(讀的人要分得出不是那一席本人寫的)。"""
    try:
        n = str(int(nbytes))
    except (TypeError, ValueError):
        n = "0"
    path = "" if path is None else str(path)
    if seat_five and SEAT_CODE_RE.match(str(seat_five)):
        who = "@%s 的背景分身" % seat_five if bg_fork_of else "@%s" % seat_five
        return "長訊已落檔(%s 位元組,來自 %s),請讀:%s" % (n, who, path)
    return "長訊已落檔(%s 位元組),請讀:%s" % (n, path)


def tagged_origin(pid=None, repo_root=None, **kwargs):
    """CLI / dm-ctl 用:`bgfork:原席五碼`、`seat:五碼`、`script:莖` 或空字串。"""
    kind, name = derive_origin(pid=pid, repo_root=repo_root, **kwargs)
    if kind == "bgfork" and name:
        return "bgfork:%s" % name
    if kind == "seat" and name:
        return "seat:%s" % name
    if kind == "script" and name:
        return "script:%s" % name
    return ""


if __name__ == "__main__":
    # 出口兩個,刻意分開:
    #   `sender_origin.py [倉根]`          → 裸的機器名(端到端臂用這個)
    #   `sender_origin.py --label [倉根]`  → 抬頭要寫的字(含白話名)
    # ★ 選填的倉根是給臂用的具名縫:不給它,端到端那格就只能在真倉底下造檔
    #   才跑得起來,而「臂跑不到那條分支」會偽裝成綠。
    # ★ 缺陷十七(2026-09-05):倉根再加一個**環境**入口,優先序 引數 > 環境 > 本檔位置。
    #   為什麼非有不可:`derive_sender_name` 的 `read_ppid`/`read_cmd`/`repo_root`
    #   三個具名縫都是 **Python 參數**,而端到端的臂是把 `dm-ctl.sh` 當**子行程**跑的
    #   —— 那條路上沒有任何地方遞得進 kwargs。於是「推導失敗」那幾格實際上是由
    #   **呼叫者從哪棵樹開出來的**決定:同一顆 sha,主樹 3 紅、worktree 全綠
    #   (主樹的啟動器命令列含主樹 `src/` 路徑 ⇒ 命中;src_root 換成 worktree 就不命中)。
    #   ★ 失效形狀是**兩邊都不叫**:紅的那邊看起來像合入壞了,綠的那邊看起來像沒事。
    #   給了這條縫,臂就能把倉根指到治具目錄,讓推導成功或失敗**由治具製造**。
    import sys as _sys
    _args = _sys.argv[1:]
    _want_label = "--label" in _args
    _want_tagged = "--tagged" in _args
    _args = [a for a in _args if a not in ("--label", "--tagged")]
    _root = _args[0] if _args else (os.environ.get(REPO_ROOT_ENV) or None)
    if _want_tagged:
        print(tagged_origin(repo_root=_root))
    else:
        _name = derive_sender_name(repo_root=_root)
        print((label(_name) if _want_label else _name) or "")
