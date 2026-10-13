#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""載入舊席位 —— 所有 AI 都經過這一支(the maintainer 09-26:「做成一個模組,所有 AI 載入都經過這個流程,不然要改很多版本」)。

「載入舊席位」= 用新席載入舊席位的 人格、第二大腦、上一場紀錄(the maintainer 09-26)。
每一家都一樣帶人格(seat_fork.carry_binding)與第二大腦(seat_fork.copy_memory);
上一場紀錄怎麼接看家別(seat_fork.load_kind),**家別的差異只准寫在本檔的 KINDS 表**,aib 不分家:

  kind     要重起那一格  重起前做什麼                      重起帶的環境                          起好後投什麼
  claude   是            移開新席剛開的空白對話檔           AIB_FORK_FROM_SESSION_UUID/PREAMBLE    (開場說明由 provider-launch 當第一句)
  history  是            舊 history.jsonl 複製給新席+說明   —                                      —(說明已寫進歷史)
  pack     否            —                                  —                                      開場說明(交接、第二大腦、最近幾輪)
  persona  否            —                                  —                                      —(bot 的對話在桌面 App)

人格包(a seat persona_pack.py)一律插在開場說明第二段(身分行之後),三條會送出開場說明的路都帶到;
status 不是 ok 或呼叫失敗 ⇒ 不插、不擋。bot(kind=persona)怎麼收人格包待 the maintainer 裁(R5)。

  seat_load.py plan  --provider P --to NEW_UUID [--config-dir D]
      ⇒ {"kind","restart","blocked":""|原因}      新席已經有對話 ⇒ blocked(不載入、不動它)
  seat_load.py apply --provider P --from OLD --to NEW --window @N --seat-id S [--model M] --aib-home H
      ⇒ {"kind","launch_env":{…},"deliver_file":"",…}   在「那一格已停下」之後呼叫(要重起的家)
新增一家 AI:在 seat_fork.load_kind / find_record / record_turns / record_model 認得它,本檔 KINDS 挑一種。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seat_fork  # noqa: E402

KINDS = {
    "claude":  {"restart": True},
    "history": {"restart": True},
    "pack":    {"restart": False},
    "persona": {"restart": False},
}


def _has_conversation(path: Path) -> bool:
    for d in seat_fork._rows(path):
        if d.get("type") in ("user", "assistant") or d.get("role") in ("user", "assistant"):
            return True
    return False


def new_seat_record(provider: str, new_uuid: str, aib_home: str, config_dir: Path | None = None) -> Path | None:
    return seat_fork.find_record(provider, new_uuid, aib_home, config_dir)


def plan(provider: str, new_uuid: str, aib_home: str, config_dir: Path | None = None) -> dict:
    kind = seat_fork.load_kind(provider)
    out = {"kind": kind, "restart": KINDS[kind]["restart"], "blocked": ""}
    # 只有要重起的家才擋:重起會停下那一格、換掉它的紀錄;不重起的家不碰新席紀錄
    #   (09-26 實機:grok 一起來就自己寫幾列 type=user 的載入內容,不是人說過話)。
    if not out["restart"]:
        return out
    rec = new_seat_record(provider, new_uuid, aib_home, config_dir)
    if rec is not None and _has_conversation(rec):
        out["blocked"] = "這一席已經有對話了(%s),不載入舊席位(要載入請另開一席再選)" % rec
    return out


def _move_blank(provider: str, new_uuid: str, aib_home: str, config_dir: Path | None) -> str:
    """claude:新席剛開的空白對話檔移開(保留),不然重起會被判成重生、接回空白那段。"""
    moved = ""
    pat = str((config_dir or seat_fork.claude_config_dir()) / "projects" / "*" / (new_uuid + ".jsonl"))
    for hit in glob.glob(pat):
        p = Path(hit)
        if _has_conversation(p):
            raise SystemExit("seat_load: %s 已經有對話,不移開" % p)
        dst = p.with_name(p.name + ".blank-%d" % int(time.time()))
        p.rename(dst)
        moved = str(dst)
    return moved


PERSONA_PACK = HERE.parent / "identity" / "persona_pack.py"
PERSONA_PACK_TIMEOUT_S = 10


def insert_persona_pack(preamble_file: str, new_uuid: str, seat_id: str, aib_home: str) -> str:
    """把人格包插在開場說明第二段(身分兩行之後)。回狀態:ok / 不是 ok 的原因。
    status 不是 ok、檔不在、非 0、逾時、讀不懂 ⇒ 一律不插、不擋(約定:人格包是附帶,不是前置條件)。"""
    if not preamble_file or not PERSONA_PACK.is_file():
        return "absent"
    try:
        r = subprocess.run([sys.executable, str(PERSONA_PACK), "render", "--session-uuid", new_uuid,
                            "--seat-id", seat_id or "", "--aib-home", aib_home],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=PERSONA_PACK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "timeout"
    if r.returncode != 0:
        return "rc=%d" % r.returncode
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return "unreadable"
    text = (d.get("text") or "").strip()
    if d.get("status") != "ok" or not text:
        return d.get("status") or "empty"
    p = Path(preamble_file)
    lines = p.read_text(encoding="utf-8").rstrip("\n").split("\n")
    head, rest = lines[:2], lines[2:]              # 身分行 + dm 位址行
    p.write_text("\n".join(head + ["", text, ""] + rest) + "\n", encoding="utf-8")
    return "ok"



# 09-30 19:5x the maintainer「首要基因要到全部 AI」:載入舊席位的開場說明也帶基因(開關 ez2_genes;關 ⇒ 不插)。
#   只給會把開場說明交給模型的家:claude(provider-launch 當第一句)、pack(投進對話)。
#   history = API-key 席:它們每一輪的系統說明已帶 GENE_HEAD,再寫進歷史就重複;persona = 橋接席:由 bridge_persona 送。
GENE_KINDS = ("claude", "pack")


def insert_genes(preamble_file: str, kind: str) -> str:
    """基因插在開場說明第二段(身分兩行之後、人格包之前)。回 ok / off / skipped / present / absent。"""
    if kind not in GENE_KINDS:
        return "skipped"
    if not preamble_file or not Path(preamble_file).is_file():
        return "absent"
    try:
        import ez2_genes
        block = ez2_genes.seat_block()
        has_genes = ez2_genes.has_genes
    except Exception:  # noqa: BLE001 —— 基因讀不到不擋載入
        return "absent"
    if not block:
        return "off"
    p = Path(preamble_file)
    text = p.read_text(encoding="utf-8")
    # ★ review r3 m4:只認「自成一行的基因標題」(檔頭或前後都是換行)。舊版 `mark in text` 會被開場說明裡
    #   引用的上一場對話(pack 模式的「最近幾輪」逐行縮排引用)裡剛好出現的基因標題騙到,整段不插。
    # ★ review r4 m5:判準收成一個 ez2_genes.has_genes(with_genes / persona_reinject_hook 同一個)。
    if has_genes(text):
        return "present"
    lines = text.rstrip("\n").split("\n")
    p.write_text("\n".join(lines[:2] + ["", block, ""] + lines[2:]) + "\n", encoding="utf-8")
    return "ok"


# 10-01 the maintainer「85% 或準備 clear 必做三步交接(aib handoff),全部 AI、不可跳過」:載入舊席位 = 舊席那一輪結束。
#   舊席最後一輪沒有交接收據 ⇒ aib_handoff 自動補抓事實、記缺漏旗標,提醒接在開場說明最後一段
#   (以「[AIB 交接缺漏]」開頭)。所有家都查;只有會把開場說明交給模型的家才接得上。
HANDOFF_NOTICE_KINDS = ("claude", "pack", "history")


def insert_handoff_notice(preamble_file: str, old_uuid: str, new_uuid: str, aib_home: str, kind: str) -> str:
    """回 ok(接上了)/ clean(舊席有收據)/ not_inserted(缺漏已記,但這一家沒有開場說明可插)/ error。不擋載入。"""
    try:
        import aib_handoff
        text = aib_handoff.predecessor_check(old_uuid, new_uuid, source="load:" + kind, home=aib_home or None)
    except Exception:  # noqa: BLE001 —— 交接檢查壞掉不擋載入(aib_handoff 自己記 log)
        return "error"
    if not text:
        return "clean"
    if kind not in HANDOFF_NOTICE_KINDS or not preamble_file or not Path(preamble_file).is_file():
        return "not_inserted"
    try:
        with open(preamble_file, "a", encoding="utf-8") as fh:
            fh.write("\n\n" + text + "\n")
    except OSError:
        return "error"
    return "ok"


HANDOFF_IDENTITY = HERE.parent / "identity"
OPEN_ACK_STATE_NAME = "open-ack.json"


def insert_handoff_pack(
    preamble_file: str,
    new_uuid: str,
    seat_id: str,
    aib_home: str,
    *,
    seat_record: dict | None = None,
) -> dict:
    """把專案接手包插在人格包之後(開場說明)。PM 寫集列 seat_fork;真注入在 seat_load(鏡像 insert_persona_pack)。

    回 dict: status(ok|absent|no_project|error)、delivered(bool)、open_ack(狀態)、text_bytes。
    缺專案卡／渲染失敗 ⇒ delivered=false + signal「未送」,不擋開席。
    """
    import time as _time
    id_dir = str(HANDOFF_IDENTITY)
    if id_dir not in sys.path:
        sys.path.insert(0, id_dir)
    try:
        import handoff_pack as HP  # noqa: E402
        import open_ack as OA  # noqa: E402
        import project_pick as PP  # noqa: E402
    except ImportError as exc:
        return {
            "status": "error",
            "delivered": False,
            "open_ack": {"delivered": False, "signal": "未送", "error": str(exc)},
            "text_bytes": 0,
        }

    card = PP.pick_project(seat_record=seat_record, environ=os.environ, state_root=aib_home or None)
    stamp = _time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if not card:
        ack = OA.mark_pack_missing()
        ack["asked_at"] = stamp
        _write_open_ack(aib_home, seat_id or seat_fork.tail5(new_uuid), ack)
        return {"status": "no_project", "delivered": False, "open_ack": ack, "text_bytes": 0}

    text = HP.build_handoff_for_seat(card, last_event_at=stamp)
    if not text or not preamble_file:
        ack = OA.mark_pack_missing()
        ack["asked_at"] = stamp
        ack["project_uid"] = str(card.get("project_uid") or "")
        _write_open_ack(aib_home, seat_id or seat_fork.tail5(new_uuid), ack)
        return {"status": "absent", "delivered": False, "open_ack": ack, "text_bytes": 0}

    p = Path(preamble_file)
    try:
        body = p.read_text(encoding="utf-8").rstrip("\n")
        # 接在全文末(人格包已由 insert_persona_pack 插在第 2 段);接手包獨立一段
        p.write_text(body + "\n\n" + text.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        ack = OA.mark_pack_missing()
        ack["asked_at"] = stamp
        return {
            "status": "error",
            "delivered": False,
            "open_ack": {**ack, "error": str(exc)},
            "text_bytes": 0,
        }

    ack = OA.mark_pack_delivered(
        None,
        project_uid=str(card.get("project_uid") or ""),
        asked_at=stamp,
    )
    _write_open_ack(aib_home, seat_id or seat_fork.tail5(new_uuid), ack)
    # 開席接上專案 ⇒ 寫進「席綁專案」唯一正本(docs/DESIGN-project-sweep-1012.md §2);寫不進去不擋開席
    import project_seats  # noqa: E402 —— 鍵是 seat_id(一席長期的身分),不是席碼
    project_seats.bind_quietly(str(card.get("project_uid") or ""), seat_id or str((seat_record or {}).get("seat_id") or ""),
                               title=str(card.get("title") or ""), by="seat_load", root=aib_home or None)
    return {
        "status": "ok",
        "delivered": True,
        "open_ack": ack,
        "text_bytes": len(text.encode("utf-8")),
        "project_uid": card.get("project_uid"),
    }


def _write_open_ack(aib_home: str, seat_key: str, ack: dict) -> None:
    """席態 open-ack.json;右欄消費者待艙綁定 —— 今晚只落檔。"""
    if not seat_key:
        return
    root = Path(aib_home or "") / "state" / "open-ack"
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("%s.json" % seat_key.lstrip("@"))
        path.write_text(json.dumps(ack, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass

def _old_seat_record(old_uuid: str, aib_home: str):
    """前身那一席的席位檔(含 retired/);找不到回 None。"""
    root = Path(aib_home or "") / "instances"
    for pat in ("*/seats/w*.json", "*/seats/retired/*.json"):
        for f in sorted(root.glob(pat)):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("session_uuid") == old_uuid:
                return rec
    return None


def insert_memory_brief(preamble_file: str, old_uuid: str) -> str:
    """09-30 aib memory:前身(或它的專案)最新幾則記憶接在開場說明最後。回 inserted / empty / error。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))
        import aib_memory
        b = aib_memory.seat_brief(old_uuid)
    except Exception:  # noqa: BLE001
        return "error"
    if not b:
        return "empty"
    try:
        with open(preamble_file, "a", encoding="utf-8") as fh:
            fh.write("\n\n" + b + "\n")
    except OSError:
        return "error"
    return "inserted"


def apply(provider: str, old_uuid: str, new_uuid: str, aib_home: str, window: str = "", seat_id: str = "",
          model: str = "", config_dir: Path | None = None) -> dict:
    kind = seat_fork.load_kind(provider)
    prep = seat_fork.prepare(old_uuid, new_uuid, aib_home, window, provider=provider, model=model,
                             config_dir=config_dir)
    out = {"kind": kind, "restart": KINDS[kind]["restart"], "mode": prep["mode"], "reason": prep["reason"],
           "memory": prep["memory"].get("memory"), "record": prep.get("record", ""),
           "launch_env": {}, "deliver_file": "", "binding": "skipped"}
    if seat_id:
        b = seat_fork.carry_binding(seat_fork.tail5(old_uuid), seat_id, new_uuid, aib_home)
        out["binding"] = b["binding"]
        if b["binding"] == "failed":
            out["binding_detail"] = b.get("detail", "")
    # 人格包(a seat src/identity/persona_pack.py)放開場說明第二段 —— 綁好人格之後才組得出來。
    # 第一段維持身分行(你是 @新碼、前身是 @舊碼),不然新席會把卡片或舊對話裡的「我是 @舊碼」當成自己。
    out["persona_pack"] = insert_persona_pack(prep["preamble_file"], new_uuid, seat_id, aib_home)
    # 基因在人格包之後才插,同樣插在第二段 ⇒ 落在人格包前面(鐵則優先)。
    out["genes"] = insert_genes(prep["preamble_file"], kind)
    # 包 B:接手包投遞(PM 寫集列 seat_fork;真注入在 seat_load,鏡像人格包)。
    # 缺專案 ⇒ delivered=false／「未送」,右欄消費者待艙綁定。
    # 09-30:以前這裡寫死 seat_record=None ⇒ 專案永遠挑不到(open-ack 一律 no_project)。改成把前身的席位檔交進去,
    #   它記的 project_uid 就能帶到新席。找不到前身席位檔時照舊 None。
    out["handoff_pack"] = insert_handoff_pack(
        prep["preamble_file"], new_uuid, seat_id, aib_home, seat_record=_old_seat_record(old_uuid, aib_home)
    )
    out["memory_brief"] = insert_memory_brief(prep["preamble_file"], old_uuid)
    # 交接缺漏提醒:開場說明的最後一段(第二段的位置留給人格包與基因,各測試釘著它們的行號)。
    out["handoff_check"] = insert_handoff_notice(prep["preamble_file"], old_uuid, new_uuid, aib_home, kind)
    if kind == "claude":
        out["moved_blank"] = _move_blank(provider, new_uuid, aib_home, config_dir)
        out["launch_env"] = {"AIB_FORK_FROM_SESSION_UUID": prep["fork_from"],
                             "AIB_FORK_PREAMBLE_FILE": prep["preamble_file"]}
    elif kind == "history":
        # API-key 席的提示符不收貼入(C21:會被當成人打的草稿)⇒ 說明一律寫進歷史;前身沒紀錄就只寫說明那一列
        h = seat_fork.copy_history(provider, old_uuid, new_uuid, aib_home, prep["preamble_file"])
        out["history"] = h["history"]
    elif kind == "pack":
        out["deliver_file"] = prep["preamble_file"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seat_load.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--provider", required=True)
    p.add_argument("--to", dest="new", required=True)
    p.add_argument("--aib-home", default="")
    p.add_argument("--config-dir", default="")
    a2 = sub.add_parser("apply")
    a2.add_argument("--provider", required=True)
    a2.add_argument("--from", dest="old", required=True)
    a2.add_argument("--to", dest="new", required=True)
    a2.add_argument("--window", default="")
    a2.add_argument("--seat-id", default="")
    a2.add_argument("--model", default="")
    a2.add_argument("--aib-home", default="")
    a2.add_argument("--config-dir", default="")
    a = ap.parse_args(argv)
    home = a.aib_home or seat_fork.seat_liveness.default_aib_home()
    cfg = Path(a.config_dir) if a.config_dir else None
    if a.cmd == "plan":
        print(json.dumps(plan(a.provider, a.new, home, cfg), ensure_ascii=False))
        return 0
    print(json.dumps(apply(a.provider, a.old, a.new, home, a.window, a.seat_id, a.model, cfg), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
