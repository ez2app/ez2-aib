#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""對話被壓縮或清除後,把這一席綁的人格包再印回對話(Claude Code 的 SessionStart hook)。

the maintainer 09-30「如何確定綁定人格之後,就會用那個人格做事?」:人格包只在開席時進對話一次(seat_load 插在開場說明
第二段)。對話太長被自動壓縮(source=compact)或按了 /clear(source=clear)之後,那段字可能就不在了,
沒有東西會再補。這支掛在 SessionStart:只在 compact / clear 時動作,印出來的字 Claude Code 會放進新對話。

認席:TMUX_PANE ⇒ 窗號 ⇒ <艙>/seats/w<窗號>.json 的 session_uuid(clear 之後對話編號會換,但席位檔記的是
綁定當時那一個,綁定表的鍵就是它的尾五碼)⇒ persona_pack.render_pack。
不在 AIB 席裡、這一席沒綁人格、任何一步讀不到 ⇒ 什麼都不印,rc 0(hook 不能擋住對話)。

  persona_reinject_hook.py session-start      (stdin = Claude Code 的 hook JSON)
環境:AIB_PERSONA_REINJECT=0 關;AIB_PERSONA_REINJECT_SOURCES 預設 "compact,clear"。
10-01 起 /clear 時另查交接收據(aib handoff;見 handoff_notice),那一段沒有開關。
★ r18 M2:/resume(source=resume)不查、不開新的一輪,只把接回的對話檔併進目前這一輪(aib_handoff.round_resume);
  不然 /clear 之後馬上 /resume,下一次 /clear 量的是空的那份對話檔,接回之後整輪做的事就被當成「沒講話」放過。
★ r17 M2:人格包與基因先補、無條件先補;交接檢查排在後面,而且只讀幾個小檔、起一個背景行程(毫秒級)——
  settings.json 給這支 hook 的時限只有 10 秒,交接那段要是同步跑 git,一支卡住的 git 就會讓人格和基因都補不回去。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if str(HERE.parent / "cockpit") not in sys.path:
    sys.path.append(str(HERE.parent / "cockpit"))      # seats_dir_lib:席位目錄與狀態根的單源;ez2_genes:基因開關

SOURCE_WORD = {"compact": "壓縮", "clear": "清除", "resume": "接回", "startup": "開啟"}


def aib_home() -> Path:
    """狀態根,走單源 seats_dir_lib.state_root(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。"""
    import seats_dir_lib
    return Path(seats_dir_lib.state_root()[0])


def _tmux_socket_name() -> str:
    """$TMUX 那個 socket 的檔名(艙裡是 aihub-<艙 id>);不在 tmux 裡回空字串。
    ★ 單獨一支、只讀 TMUX:它不是艙導向變數(洗掉它 tmux 會以為自己不在 tmux 裡),
      不和艙導向變數擠在同一個函式裡讀(test-cabin-read-set 的判準)。"""
    return os.path.basename((os.environ.get("TMUX") or "").split(",")[0])


def _instance_home() -> Path | None:
    """這一席所在艙的家目錄(<狀態根>/instances/<艙 id>),走單源 seats_dir_lib:
    HUB 導出 > EZ2_AIB_INSTANCE_HOME(兩個不一致 ⇒ None,不挑);都沒有 ⇒
    艙門洗過環境的席(Claude 席實測:沒有 EZ2_AIB_INSTANCE* 也沒有 HUB)從 $TMUX 的 socket 名 aihub-<艙 id>
    認艙,把它當 HUB 交給同一個單源。量不到 ⇒ None(不退主艙:主艙的 wN.json 與艙內同檔名同窗號)。"""
    import seats_dir_lib
    seats, _src, conflict = seats_dir_lib.cabin_seats_dir_with_source()
    if conflict:
        return None
    if not seats:
        sock = _tmux_socket_name()
        if not (sock.startswith("aihub-") and len(sock) > len("aihub-")):
            return None
        seats, src = seats_dir_lib.seats_dir_with_source(hub=sock)
        if not src.startswith("hub:"):
            return None
    return Path(seats).parent


def _window_id() -> str:
    pane = os.environ.get("TMUX_PANE") or ""
    if not pane:
        return ""
    try:
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"], capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out if out.startswith("@") else ""


_UUID_CACHE: dict = {}


def seat_session_uuid() -> str:
    """同一次 hook 只問一次 tmux(人格補回與交接檢查都要它)。"""
    if "v" not in _UUID_CACHE:
        _UUID_CACHE["v"] = _seat_session_uuid()
    return _UUID_CACHE["v"]


def _seat_session_uuid() -> str:
    home, wid = _instance_home(), _window_id()
    if not home or not wid:
        return ""
    try:
        rec = json.loads((home / "seats" / ("w%s.json" % wid.lstrip("@"))).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(rec.get("session_uuid") or "") if isinstance(rec, dict) else ""


def reinject_text(source: str) -> str:
    uuid = seat_session_uuid()
    if not uuid:
        return ""
    return _with_genes(_persona_text(uuid, source), source)


def _with_genes(text: str, source: str) -> str:
    """09-30 19:5x the maintainer「首要基因要到全部 AI」:壓縮/清除後連基因一起補回,放最前面(開關 ez2_genes;關 ⇒ 原樣)。
    沒綁人格、也沒有記憶的席,開關開著時一樣補回基因。單一來源 ez2_genes.with_genes(已經有就不再加)。"""
    try:
        import ez2_genes
        block = ez2_genes.seat_block()
    except Exception:  # noqa: BLE001 —— hook 絕不擋住對話
        return text
    if not block or ez2_genes.has_genes(text):      # review r4 m5:與 with_genes / seat_load 同一個判準
        return text
    head = "[AIB 對話剛被%s,自動補回 AIB 基因;照它做事,和開席鐵則或 the maintainer 當次指令衝突時以後者為準]" % SOURCE_WORD.get(source, source)
    return head + "\n" + block + ("\n\n" + text if text else "")


def _persona_text(uuid: str, source: str) -> str:
    """人格包 + 記憶那一段;任何一步壞掉 ⇒ ""(review r1 m8)。
    ★ 錯誤要在這裡接住:reinject_text 是 _with_genes(_persona_text(...)),例外往上丟的話 main 整段吞掉,
      連基因都補不回去 —— 人格那一半壞了,不能拖著基因一起不見。"""
    try:
        return _persona_text_raw(uuid, source)
    except Exception:  # noqa: BLE001 —— review r1 m8:人格壞掉只丟人格這一段
        return ""


def _persona_text_raw(uuid: str, source: str) -> str:
    import persona_pack
    pack = persona_pack.render_pack(uuid, aib_home())
    if pack.get("status") != "ok" or not pack.get("text"):
        mem = _memory_brief(uuid)             # 沒綁人格也要把記憶帶回來
        return ("[AIB 對話剛被%s,自動補回這一席的記憶]%s" % (SOURCE_WORD.get(source, source), mem)) if mem else ""
    text = ("[AIB 對話剛被%s,自動補回這一席綁定的人格包;照它做事,和開席鐵則或 the maintainer 當次指令衝突時以後者為準]\n%s"
            % (SOURCE_WORD.get(source, source), pack["text"]))
    return text + _memory_brief(uuid)


def _memory_brief(uuid: str) -> str:
    """09-30:人格之外,把這一席(或它綁的專案)最新幾則 aib memory 一起補回。讀不到就不加。"""
    try:
        sys.path.insert(0, str(HERE.parent / "memory"))
        import aib_memory
        b = aib_memory.seat_brief(uuid)
    except Exception:  # noqa: BLE001
        return ""
    return ("\n\n" + b) if b else ""


# 10-01 the maintainer「85% 或準備 clear 必做三步交接,全部 AI、不可跳過」:/clear = 這一席上一輪結束、新一輪開始。
#   上一輪沒有 aib handoff 收據 ⇒ aib_handoff 自動補抓事實、記缺漏旗標,提醒放在新對話的第一行。
#   ★ 不受 AIB_PERSONA_REINJECT 開關管(那是人格補回的開關;交接檢查不能被跳過)。compact 不是新的一輪,不查。
HANDOFF_SOURCES = ("clear",)
RESUME_SOURCE = "resume"


def handoff_notice(source: str, payload: dict) -> str:
    """回提醒文字或 ""。任何錯都吞掉、記到 logs/handoff.jsonl(hook 絕不擋住對話)。
    毫秒級:aib_handoff.round_start 只讀收據/開始點小檔、寫旗標,補抓事實丟到背景(不等)。
    對話檔路徑(transcript_path)記進這一輪的開始點:下一次 /clear 用它判斷這一輪真人有沒有講過話。"""
    if source not in HANDOFF_SOURCES and source != RESUME_SOURCE:
        return ""
    try:
        uuid = seat_session_uuid()
        if not uuid:
            return ""
        import aib_handoff
        payload = payload or {}
        if source == RESUME_SOURCE:      # r18 M2:不是新的一輪;接回的對話檔併進這一輪,下一次 /clear 會量它
            aib_handoff.round_resume(uuid, str(payload.get("transcript_path") or ""),
                                     str(payload.get("session_id") or ""))
            return ""
        return aib_handoff.round_start(uuid, "claude:" + source, cwd=str(payload.get("cwd") or ""),
                                       transcript=str(payload.get("transcript_path") or ""),
                                       session_id=str(payload.get("session_id") or ""))
    except Exception as exc:  # noqa: BLE001
        try:
            import aib_handoff
            aib_handoff._log("hook_failed", None, why=repr(exc)[:300])
        except Exception:  # noqa: BLE001
            pass
        return ""


# 10-10 the maintainer「席位恢復的第一件事是,重新總結所有記憶,並用白話標題,加白話總結 150 字,告訴用戶這席上一次在做什麼,
#   現在可以接著做的有那些?AI 建議的下一步選項有那些?」:只認 AIB 原地復活(aib 開席時 export 的
#   AIB_RESUME_SESSION_UUID 等於這一席的 session_uuid)。席內自己打 /resume 接別的對話、或環境裡殘留別席的 uuid ⇒ 不印。
REVIVE_ASK = """[AIB 席位剛復活] 你的第一則回答,先做這件事,再做別的(包括回應別席的訊息):
重新總結這一席所有記憶 —— 上面帶回的記憶、席位記憶(~/.ez2-aib/memory/<席碼>/context.md)、這條對話裡最後一次交接 —— 照下面格式回給用戶,全部用國三生聽得懂的白話:
1. 白話標題:一行,說這一席在忙什麼。
2. 白話總結:約 150 字,說這一席上一次在做什麼、做到哪裡、有什麼東西還在跑或在等人。
3. 現在可以接著做的:條列,每項一句。
4. AI 建議的下一步選項:條列 2~4 個,標出推薦哪一個,最後寫「你不回我就走哪一條」。
沒查到的寫「沒查到」,不要猜。"""


def revive_text(source: str) -> str:
    if source != RESUME_SOURCE or os.environ.get("AIB_REVIVE_BRIEF") == "0":
        return ""
    want = (os.environ.get("AIB_RESUME_SESSION_UUID") or "").strip()
    uuid = seat_session_uuid()
    if not want or not uuid or want != uuid:
        return ""
    return _with_genes(_persona_text(uuid, source), source) + "\n\n" + REVIVE_ASK


# 10-12 專案清查(docs/DESIGN-project-sweep-1012.md §3 第一版):開工 / 清對話 / 復活時附這一席專案的清查表前 10 筆。
#   只讀;最多等 10 秒,逾時印「這次沒算完」;任何錯都不擋對話。AIB_SWEEP_BRIEF=0 關。
SWEEP_BRIEF_SOURCES = ("startup", "clear", RESUME_SOURCE)
SWEEP_BRIEF_TIMEOUT_S = 10


def _seat_has_project(uuid: str) -> bool:
    """行程內快查:這個對話的席位檔有綁專案(席位檔 project_uid,或席綁專案正本裡有它的 seat_id)。
    任何形狀不對都當沒有(hook 絕不因為它當掉 —— 審查第二輪)。"""
    try:
        return _seat_has_project_raw(uuid)
    except Exception:  # noqa: BLE001
        return False


def _seat_has_project_raw(uuid: str) -> bool:
    import glob as _glob
    home = aib_home()
    try:
        reg = json.loads((home / "state" / "project-seats.json").read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        reg = {}
    bound = {sid for proj in reg.values() for sid, r in (proj.get("seats") or {}).items() if not r.get("until")}
    for f in _glob.glob(str(home / "instances" / "*" / "seats" / "*.json")):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("session_uuid") == uuid:
            if rec.get("project_uid") or rec.get("seat_id") in bound:
                return True
    return False


def sweep_brief(source: str) -> str:
    if source not in SWEEP_BRIEF_SOURCES or os.environ.get("AIB_SWEEP_BRIEF") == "0":
        return ""
    uuid = seat_session_uuid()
    if not uuid or not _seat_has_project(uuid):
        return ""          # 沒綁專案的席(含測試治具)不起子行程:每次開工省一支 python(審查實量:多叫幾次就慢 26%)
    try:
        sys.path.insert(0, str(HERE.parent / "cockpit"))
        import seat_address
        code = seat_address.uuid_tail5(uuid) or ""
        tool = HERE.parent / "cockpit" / "project_sweep.py"
        p = subprocess.run([sys.executable, str(tool), "--brief", "--limit", "10"], capture_output=True, text=True,
                           timeout=SWEEP_BRIEF_TIMEOUT_S, stdin=subprocess.DEVNULL, env=dict(os.environ, AIB_SEAT_CODE=code))
    except subprocess.TimeoutExpired:
        return "[AIB 專案清查] 這次沒算完(超過 %d 秒);要看就跑 aib project sweep" % SWEEP_BRIEF_TIMEOUT_S
    except Exception:  # noqa: BLE001 —— hook 絕不擋住對話
        return ""
    if p.returncode != 0 or not p.stdout.strip():
        return ""
    return "[AIB 專案清查] 這一席專案現在手上的事(照順序;只讀,全文 aib project sweep)\n" + p.stdout.strip()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["session-start"]:
        return 0
    _UUID_CACHE.clear()              # 快取只活在這一次 hook(測試會在同一個行程裡連叫幾次 main)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    source = str(payload.get("source") or "")
    wanted = [s.strip() for s in (os.environ.get("AIB_PERSONA_REINJECT_SOURCES") or "compact,clear").split(",")]
    text = ""
    if os.environ.get("AIB_PERSONA_REINJECT") != "0" and source in wanted:
        try:
            text = reinject_text(source)
        except Exception:  # noqa: BLE001 —— hook 絕不擋住對話
            text = ""
    if not text:
        try:
            text = revive_text(source)
        except Exception:  # noqa: BLE001 —— hook 絕不擋住對話
            text = ""
    if text:                         # r17 M2:人格/基因先印出去,後面的交接檢查就算出事也不會把它拖走
        print(text, flush=True)
    notice = handoff_notice(source, payload)
    if notice:
        print(("\n" if text else "") + notice, flush=True)
    brief = sweep_brief(source)
    if brief:
        print(("\n" if (text or notice) else "") + brief, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
