#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""人格包:把一席綁定的人格卡組成一段純文字,五家 AI 都吃得下(開席第一個載入)。

the maintainer 2026-09-26:「全部AI的開席入口,這個也必要第一個載入對應的人格、第二大腦等等」。
設計稿:docs/DESIGN-persona-autoload-0926.md §2。呼叫端:a seat 的 seat_load(開場說明第二段)。

    persona_pack.py render --session-uuid U [--seat-id S] [--aib-home H] [--vault V] [--contract SPEC]
    ⇒ stdout 一行 JSON,rc 恆 0(用法錯才非 0):
       {"status": "ok|unbound|card_missing", "text": "...", "persona_uid", "persona_sha8",
        "route_key", "folder", "bytes", "code", "parts": {...}}
       status 不是 ok ⇒ text 為空;呼叫端照常開席不擋。

讀什麼(全部唯讀,本檔不寫任何東西):
  <aib>/identity/bindings.json   五碼 → persona_uid + route_key(binding_projection 產的投影,鍵=uuid 尾五碼)
  <aib>/identity/seat-display.json  上面那份沒有這一席時才讀:只綁人格、沒綁專案的席只在這一份裡
  <aib>/identity/cards.json      persona_uid → card_path_rel + vault_root(card_registry 產的投影)
  <vault>/<card_path_rel>        角色.md 正文(指紋現算,不信投影裡的舊值)
  同資料夾的 交接簡報.md、個人記憶.md(有才放)
  路由契約                        route_key → Vault 資料夾(讀不到就寫「量不到」,不猜)

長度:整包 ≤ MAX_BYTES(UTF-8)。超過時依序丟:個人記憶最舊的、交接簡報、卡片裡優先序低的段;
紅線那一段最後才動(a seat 09-26:長度由本檔定死,呼叫端不截)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "persona_route"))
from route_register import resolve_route_folder  # noqa: E402

MAX_BYTES = 4000
# 主人格檔(the maintainer 08-22 §二十六之七:「主人格獨立綁主艦長」;主人格 = P10-主人格萃煉 五份人格檔)。
# 用屬性找檔,不寫死資料夾(08-27 第二鐵律);保留優先序:數字小的最後才丟。
MAIN_FILE_TYPE = "persona-profile-file"
MAIN_FILE_KEEP = {"boundaries": 0, "decision-principles": 1, "stylebook": 2, "profile": 3, "audience-modes": 4}
MAIN_CONFIG = ("identity", "user-main-persona.json")   # {"persona_uid": "..."};沒有就用 seat_rebind 的常數
MEMORY_KEEP = 5
HEADER = "[AIB 人格包 v1]"
# 卡片段落的保留優先序:數字小的最後才丟。沒列到的段(含開頭那句定位)排在知識域前面。
SECTION_KEEP = {"紅線": 0, "職責": 1, "": 2, "語氣與口癖": 3, "知識域": 5}
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.S)
# 會改變終端機或貼上行為的控制字元一律拿掉;換行與 tab 保留
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def tail5(uuid: str) -> str:
    return (uuid or "").replace("-", "")[-5:]


def aib_home(explicit: str = "") -> Path:
    return Path(explicit or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib"))


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _load_obj(path: Path) -> dict:
    """讀一份應該是物件的 JSON;讀不到、壞掉、或頂層不是物件(例如一個清單)一律當空物件。

    ★ 09-30:seat-display.json 頂層被寫成清單時,`(… or {}).get(…)` 直接 AttributeError ⇒
      整個人格包組不出來(rc=1),而「真的沒綁」應該回 unbound。形狀不對 = 讀不到,不是崩潰。
    """
    doc = _load_json(path)
    return doc if isinstance(doc, dict) else {}


def clean(text: str) -> str:
    return _CTRL_RE.sub("", (text or "").replace("\r\n", "\n"))


def body_of(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def sections(body: str) -> list[tuple[str, str]]:
    """以 `## ` 標題切段;標題前那一段(卡片開頭的定位句)名字是空字串。"""
    out, name, buf = [], "", []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if "\n".join(buf).strip():
                out.append((name, "\n".join(buf).strip()))
            name, buf = m.group(1), [line]
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        out.append((name, "\n".join(buf).strip()))
    return out


def memory_entries(text: str) -> list[str]:
    return [ln.strip() for ln in body_of(text).split("\n") if ln.strip().startswith("- ")]


def inside(root: Path, rel: str) -> Path | None:
    """rel 必須落在 root 底下(擋 .. 與符號連結逃出去);不合格回 None。"""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    try:
        real_root = root.resolve(strict=True)
        p = (root / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return p if p == real_root or real_root in p.parents else None


def main_persona_uid(home: Path) -> str:
    """該用戶的主人格卡 uid(the maintainer 09-26 R4)。先讀 <aib>/identity/user-main-persona.json,
    沒有才用 seat_rebind.DIRECTED_PERSONA_UID(= 第一艦長首綁指名的卡)——卡號只寫在那一處。
    ★ 只拿來比對卡號;「這一席是不是主人格席」由呼叫端的 first_captain 決定(a seat 審查 09-26 重大 1:
      真機 22 席全綁 71c97a,只比卡號會讓每一席都被當成主人格)。"""
    d = _load_json(home.joinpath(*MAIN_CONFIG))
    if isinstance(d, dict) and isinstance(d.get("persona_uid"), str) and d["persona_uid"]:
        return d["persona_uid"]
    try:
        import seat_rebind  # 同目錄
        return str(seat_rebind.DIRECTED_PERSONA_UID)
    except Exception:
        return ""


def _fm_value(head: str, key: str) -> str:
    m = re.search(r"^%s:\s*\"?([^\"\n]*)\"?\s*$" % re.escape(key), head, re.M)
    return m.group(1).strip() if m else ""


def main_persona_blocks(vault: Path) -> list[tuple[str, str, str, str]]:
    """掃 Vault 找 frontmatter `type: persona-profile-file` 的檔,回 [(種類, uid, 精簡正文, 全檔 sha256)]。
    同 uid 多份(鏡像/備份)只取一份:依 (updated, 路徑) 取最大——缺 updated 視為最舊,平手用路徑定序,
    換機器、換走訪順序都選到同一份(基準人格檔指紋才不會無故變動)。
    每份只留「最新…/固定安全邊界/新增邊界」段,沒有這種段才用全文;「(尚無)」的段不放。"""
    found: dict[str, tuple] = {}
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if not f.endswith(".md") or ".bak" in f:
                continue
            p = Path(dp) / f
            try:
                with open(p, "rb") as fh:
                    lead = fh.read(8192).decode("utf-8", "ignore")
                m = _FRONTMATTER_RE.match(lead)
                head = m.group(0) if m else ""          # 讀到 frontmatter 結束為止,不是固定位元組
                if not re.search(r"^type:\s*%s\s*$" % MAIN_FILE_TYPE, head, re.M):
                    continue
                raw = p.read_bytes()
                text = clean(raw.decode("utf-8", "replace"))
            except (OSError, UnicodeDecodeError):
                continue
            uid = _fm_value(head, "uid")
            kind = _fm_value(head, "title").replace("人格檔-", "") or f
            updated = _fm_value(head, "updated")
            if not re.fullmatch(r"[a-z0-9]{6}", uid) or kind not in MAIN_FILE_KEEP:
                continue
            rank = (updated, str(p))
            if uid in found and found[uid][5] >= rank:
                continue
            secs = [(n, t) for n, t in sections(body_of(text)) if "(尚無)" not in t]
            keep = [t for n, t in secs if re.search(r"最新|固定安全邊界|新增邊界", n)]
            body = "\n".join(keep or [t for _, t in secs]).strip()
            if body:
                found[uid] = (kind, uid, body, updated, hashlib.sha256(raw).hexdigest(), rank)
    return sorted(((k, u, b, h) for k, u, b, _, h, _r in found.values()), key=lambda x: MAIN_FILE_KEEP[x[0]])


# 09-30 測試艙 b1af:Codex 讀了人格卡、背得出紅線,回答卻沒照做(卡上「每則最後一行寫…」一次都沒寫)。
# 讀過 ≠ 照做 ⇒ 明說是每一則都要做,並給一個自己對照的動作。
COMPLY_LINE = ("不是讀過就好:從現在起每一則回答都照這張卡做 —— 紅線逐條遵守、職責與口癖照用;"
               "送出回答前,自己對一次紅線。")

LANGUAGE_NAMES = {"zh-TW": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}


def answer_language() -> str:
    """這台 AIB 的介面語系(EZ2_AIB_LOCALE,與右欄、選單同一個來源);讀不到就是 zh-TW。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cockpit"))
        import i18n_lib
        return i18n_lib.resolve_locale()
    except Exception:  # noqa: BLE001
        return "zh-TW"


def answer_language_line() -> str:
    """the maintainer 09-30:「應該是用他的對應 i18n 語系回答,因為用戶不一定是繁中語言」。
    角色卡多半寫死「以繁體中文回答」(建卡範本留下的);卡是使用者的資料不在這裡改,改由這一行蓋過去。"""
    loc = answer_language()
    return ("回答語言:%s(%s)。跟著這台 AIB 的語系設定;卡片裡若寫了別的語言,以這一行為準。Answer in this language."
            % (LANGUAGE_NAMES.get(loc, loc), loc))


def base_fingerprint(blocks) -> str:
    """修正紀錄要帶的「基準人格檔指紋」(08-22 §二十六之七 第 2 條):主人格檔各自全檔 sha,
    依 uid 排序後再雜湊一次。任何一份人格檔改了,這個值就變。"""
    h = hashlib.sha256()
    for _, u, _, fsha in sorted(blocks, key=lambda b: b[1]):
        h.update(("%s:%s\n" % (u, fsha)).encode())
    return h.hexdigest()


def route_folder(route_key: str, spec: str = "") -> str:
    """共用解析器(route_register.resolve_route_folder,契約來源順序與 cockpit-env 同);查不到回空字串。"""
    return (resolve_route_folder(route_key, spec=spec or None, use_index=False) or "") if route_key else ""   # 人格包這行是給人看的:印契約字面,不印 uid 版面的 fld_ 路徑


def _empty(status: str, code: str, **extra) -> dict:
    out = {"status": status, "text": "", "code": code, "persona_uid": "", "persona_sha8": "",
           "route_key": "", "folder": "", "bytes": 0, "parts": {}}
    out.update(extra)
    return out


# 10-01 the maintainer「85% 或準備 clear 必做三步交接,全部 AI、不可跳過」:寫回段的固定一行(開席單鐵則八同一句)。
HANDOFF_RULE = "對話用量到 85%(或 the maintainer 說準備 clear)必須跑 `aib handoff`(collect → 三步 write),不可跳過。"


def assemble(name, uid, sha8, card_secs, handoff, memories, route_key, folder, code, max_bytes=MAX_BYTES,
             main_blocks=(), read_at="", base8=""):
    """組字;超過 max_bytes 依序丟:最舊的記憶 → 交接簡報(從尾逐行)→ 主人格檔丟到剩一份(邊界留著)
    → 卡片低優先段丟到剩一段(紅線留著)→ 最後一份主人格檔 → 截卡片最後那段。"""
    card = list(card_secs)
    main = list(main_blocks)
    mem = list(memories)
    brief_lines = [ln for ln in (handoff or "").split("\n")]
    brief_cut = False
    parts = {"card_sections": [n or "(開頭)" for n, _ in card], "handoff": bool(handoff),
             "main_files": [k for k, *_ in main],
             "memory": len(mem), "dropped": []}
    if not (handoff or "").strip():
        brief_lines = []

    def render() -> str:
        lines = ["%s 你綁定的人格卡:%s(uid %s,指紋 %s)" % (HEADER, name, uid, sha8),
                 "照這張卡的職責、紅線與語氣工作;它和開席鐵則或 the maintainer 當次指令衝突時,以鐵則與 the maintainer 為準。",
                 answer_language_line(),
                 COMPLY_LINE,
                 "── 角色卡"]
        lines += [t for _, t in card] or ["(卡片正文被截掉了,讀原檔)"]
        if main:
            lines += ["── 主人格檔(用戶本人;讀這幾份,不要改它們)"]
            for kind, muid, text, _ in main:
                lines += ["[[%s]] %s" % (muid, kind), text]
        if brief_lines:
            lines += ["── 交接簡報"] + brief_lines + (["…(交接簡報截斷,讀原檔)"] if brief_cut else [])
        if mem:
            lines += ["── 個人記憶(最近 %d 條)" % len(mem)] + mem
        lines += ["── 第二大腦",
                  "- Vault 寫入位置:routeKey=%s(%s)" % (route_key or "未綁", folder or "量不到"),
                  "- 席位記憶:~/.ez2-aib/memory/%s/context.md" % code,
                  "── 寫回",
                  "- 工作進度、決定、下一步:aib memory write --kind progress|decision|next|handoff --title <短標題> "
                  "--body-file <檔> [--tags 英文]。以 uid 串這一席、專案、人格;換席與壓縮後會自動帶回。",
                  "- 學到的人格面:aib memory write --kind note --tags persona/correction,內文帶「基準人格檔指紋 %s、"
                  "讀取時刻 %s」(不改人格檔;人格修正的專屬路由還沒登記,先放這裡)。" % (base8 or sha8, read_at),
                  "- 角色卡正文(職責/紅線/語氣)不直接改,要改就提候選給 the maintainer 核准。",
                  "- " + HANDOFF_RULE,
                  "- 引用一律用 uid([[%s]] 這種),不寫名稱或路徑;寫筆記要帶屬性、category_uid、英文標籤與雙向連結。" % uid,
                  "── 請回覆一行:人格已載入 %s" % sha8]
        return "\n".join(lines) + "\n"

    text = render()
    guard = 5000   # 每圈都真的變短;這道上限只防將來改壞時變成死循環(09-26 第一版就踩過)
    while len(text.encode("utf-8")) > max_bytes and guard > 0:
        guard -= 1
        if mem:
            mem.pop(0)
            parts["dropped"].append("memory")
        elif brief_lines:
            # 交接簡報從尾巴逐行截(開頭的【接手指令】與來龍去脈最重要);每圈真的少一行,標記只在組字時加
            brief_lines.pop()
            brief_cut = True
            tag = "handoff:truncated" if brief_lines else "handoff"
            if tag not in parts["dropped"]:
                parts["dropped"].append(tag)
        elif len(main) > 1:
            # 主人格檔:邊界(boundaries)留到最後,其餘依 MAIN_FILE_KEEP 從低優先丟
            worst = max(range(len(main)), key=lambda i: MAIN_FILE_KEEP.get(main[i][0], 9))
            parts["dropped"].append("main:" + main[worst][0])
            main.pop(worst)
        elif len(card) > 1:
            worst = max(range(len(card)), key=lambda i: (SECTION_KEEP.get(card[i][0], 4), i))
            parts["dropped"].append("card:" + (card[worst][0] or "(開頭)"))
            card.pop(worst)
        elif main:
            parts["dropped"].append("main:" + main[0][0])
            main.pop(0)
        else:
            cut = card[0][1].encode("utf-8")[: max(0, len(card[0][1].encode("utf-8")) - 200)]
            card[0] = (card[0][0], cut.decode("utf-8", "ignore") + "…(截斷)")
            parts["dropped"].append("card:truncated")
            if not cut:
                break
        text = render()
    parts["memory"] = len(mem)
    parts["main_files"] = [k for k, *_ in main]
    parts["handoff"] = ("truncated" if brief_cut else "full") if brief_lines else False
    parts["card_sections"] = [n or "(開頭)" for n, _ in card]
    return text, parts


def render_pack(session_uuid: str, home: Path, vault: str = "", contract: str = "", max_bytes: int = MAX_BYTES,
                first_captain: bool = False) -> dict:
    code = tail5(session_uuid)
    bindings = _load_obj(home / "identity" / "bindings.json").get("bindings") or {}
    b = bindings.get(code) if isinstance(bindings, dict) else None
    if not isinstance(b, dict) or not b.get("persona_uid"):
        # 09-29 a seat:bindings.json 只列「人格與專案(路由)都綁了」的席。只綁人格的席不在裡面,
        #   以前在這裡就被說成 unbound ⇒ 人格包組不出來:重開席位不載入人格、newrole 的人格包也寄不出去
        #   (真機:a seat 的資料夾被解掉之後、a seat 只綁人格)。人格是第一大腦,不該要先綁專案才算數。
        #   顯示投影 seat-display.json 是「綁了人格就有」的那一份(seat_address.nickname_of 09-27 同一個修法)。
        shown = _load_obj(home / "identity" / "seat-display.json").get("display") or {}
        d = shown.get(code) if isinstance(shown, dict) else None
        if not isinstance(d, dict) or not d.get("persona_uid"):
            return _empty("unbound", code)
        b = {"persona_uid": d["persona_uid"], "route_key": d.get("route_key") or ""}
    uid = str(b["persona_uid"])
    route_key = str(b.get("route_key") or "")
    cards_doc = _load_obj(home / "identity" / "cards.json")
    card = (cards_doc.get("cards") or {}).get(uid)
    if not isinstance(card, dict):
        return _empty("card_missing", code, persona_uid=uid, reason="not_in_cards_json")
    root = Path(vault or (cards_doc.get("source") or {}).get("vault_root") or "")
    path = inside(root, str(card.get("card_path_rel") or "")) if str(root) not in ("", ".") else None
    if path is None or not path.is_file():
        return _empty("card_missing", code, persona_uid=uid, reason="card_file_unreadable")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    body = clean(body_of(raw.decode("utf-8", "replace")))
    name = clean(str(card.get("display_name") or path.parent.name))

    def sibling(fn: str) -> str:
        p = path.parent / fn
        try:
            return clean(p.read_text(encoding="utf-8")) if p.is_file() else ""
        except (OSError, UnicodeDecodeError):
            return ""

    handoff = body_of(sibling("交接簡報.md"))
    memories = memory_entries(sibling("個人記憶.md"))[-MEMORY_KEEP:]
    folder = clean(route_folder(route_key, contract))
    # 主人格席 = 呼叫端明說是第一艦長(與 seat_rebind --directed-first-captain 同一個判斷)且綁的是主人格卡
    is_main = bool(first_captain) and bool(uid) and uid == main_persona_uid(home)
    blocks = main_persona_blocks(root) if is_main else []
    read_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    base = base_fingerprint(blocks) if blocks else sha
    text, parts = assemble(name, uid, sha[:8], sections(body), handoff, memories, route_key, folder, code, max_bytes,
                           main_blocks=blocks, read_at=read_at, base8=base[:8])
    return {"status": "ok", "text": text, "code": code, "persona_uid": uid, "persona_sha8": sha[:8],
            "persona_sha": sha, "projection_sha_matches": sha == card.get("persona_sha"),
            "is_main_persona": is_main, "read_at": read_at, "base_persona_sha8": base[:8],
            "route_key": route_key, "folder": folder, "bytes": len(text.encode("utf-8")), "parts": parts}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="persona_pack.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--session-uuid", required=True)
    r.add_argument("--seat-id", default="", help="只帶回輸出,不參與查找(綁定表的鍵是 uuid 尾五碼)")
    r.add_argument("--aib-home", default="")
    r.add_argument("--vault", default="")
    r.add_argument("--contract", default="")
    r.add_argument("--first-captain", action="store_true",
                   help="這一席是第一艦長(aib 的 aib_directed_first_captain_flag 判);只有它帶主人格檔")
    a = ap.parse_args(argv)
    if not _UUID_RE.fullmatch(a.session_uuid):
        print(json.dumps(_empty("unbound", "", reason="bad_session_uuid"), ensure_ascii=False))
        return 0
    out = render_pack(a.session_uuid, aib_home(a.aib_home), a.vault, a.contract, first_captain=a.first_captain)
    out["seat_id"] = a.seat_id
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
