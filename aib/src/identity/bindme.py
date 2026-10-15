#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib bindme —— 一次看清、補齊各席的人格與專案綁定(the maintainer 10-11 01:0x 直令)。

## 由來
10-11 量到:艙 931e 活著的 17 席,人格+專案都齊的只有 2 席;6 席 claude 有人格沒專案,
9 席非 claude 兩個都沒有。現有工具分兩支(`aib newrole` 綁人格、`aib newproj` 綁專案),
「這一席該綁哪個」要人自己查清楚再分兩次下。the maintainer 要一次做完,而且要**先問各席自己
24 小時內做了什麼,照它們的回答摘要後提出綁定**,不是由旁人猜。

## 五個子指令
    status   全艙一張表:每席人格/專案/缺什麼/怎麼修(唯讀)
    survey   對缺綁定的席寄 dm,請它照固定格式回「24 小時內的開發」;回信要帶 [bindme:<編號>]
    collect  收回覆 → 每席一行摘要 → 比對既有專案卡與人格卡 → 提出綁定建議(寫進調查檔)
    apply    照建議綁:先人格(aib newrole)後專案(aib newproj --deliver),綁完讀回驗證
    repair   人格不見但延續檔還記得的席,把那張人格接回來

## 紅線
- **不猜。** 合不合適由那一席自己從清單挑、程式驗「清單裡剛好一張對得上」;專案比不出唯一一張就不直接綁
  (自動模式改成跳選擇視窗);倉指紋共用很常見(10-11 實量:3 張 SEO 卡共用同一個指紋),所以本工具
  **不用倉指紋自動綁**。
- **不另寫綁定檔。** 一律呼叫 `aib newrole` / `aib newproj`(帳本、投影、接手包都走它們)。
- **手動 apply:沒講 --create-missing 不建新卡。** 建議裡出現「新建」的列,apply 預設跳過。
- **自動模式(auto_apply,交接 / 整理記憶時對自己這一席)—— the maintainer 10-13 01:5x 在 a seat 窗裡確認「同意自動綁」:**
  找到合適的既有卡 ⇒ 直接綁,另發「已綁 X」通知(可換掉);沒有合適的 ⇒ Mac 彈窗 30 秒,沒選照預設建立並綁;
  彈窗叫不起來(沒人看著畫面)⇒ 一樣等 30 秒照預設,另送一張決定卡當事後通知(取代舊規則「人沒看到不能當成沒選」)。
  **冠位人格(CROWN_PERSONA)永遠不自動綁;bot 席整段不跑(bot 綁定照舊讀不到就問人)。**
- **bot 席不碰**(claudebot/codexbot/grokbot/musebot 的「綁定」是另一回事)。
- 席位用席碼(session uuid 末五碼,走 seat_address.uuid_tail5),輸出同時印 seat_id。
- 不讀任何艙環境變數:「我是哪一席」用 --me 明講(從工具子行程認不出窗格)。
"""
import argparse
import datetime
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import seat_address  # noqa: E402

AIB = REPO / "src" / "cockpit" / "aib"
DM_QUEUE = REPO / "src" / "cockpit" / "dm_queue.py"
BOT_PROVIDERS = {"claudebot", "codexbot", "grokbot", "musebot"}
CODE_RE = re.compile(r"^[0-9a-z]{5}$")
UID_RE = re.compile(r"proj:[0-9a-f]{16}")
RELAY_RE = re.compile(r"請讀[::]\s*(\S+\.txt)")
FIELD_RE = re.compile(r"^\s*(席碼|主項目|24\s*小時做了|做了|建議專案|建議人格|人格理由|新人格名稱)\s*[::]\s*(.+?)\s*$", re.M)
UNKNOWN = {"", "不知道", "沒有", "無", "none", "n/a", "-"}


# ── 讀現況(全部唯讀)────────────────────────────────────────────────

def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def live_seats(state_root):
    """活著的席:各艙席位檔裡沒有退出痕的那些。回 list[dict]。"""
    import cabin_restart    # hub 名向單源 cabin_resolve.sh 要(R8)
    import seats_dir_lib    # 席位登記簿位置向單源要(HUB 導出),不自己拼
    rows = []
    files = []
    for home in sorted(glob.glob(os.path.join(state_root, "instances", "*"))):
        cabin = os.path.basename(home)
        hub = cabin_restart._hub_of(cabin)
        if not hub:
            continue
        sd = seats_dir_lib.seats_dir(hub=hub, environ={"EZ2_AIB_STATE_ROOT": state_root})
        files += [(cabin, f) for f in sorted(glob.glob(os.path.join(sd, "w*.json")))]
    for cabin, f in files:
        rec = _load(f) or {}
        uuid = rec.get("session_uuid") or ""
        if not uuid or rec.get("exited_at_epoch"):
            continue
        rows.append({
            "code": seat_address.uuid_tail5(uuid),
            "seat_id": rec.get("seat_id") or "",
            "cabin": cabin,
            "cabin4": cabin.rsplit("-", 1)[-1],
            "provider": rec.get("provider") or "",
            "role": rec.get("role") or "",
            "project_uid": rec.get("project_uid") or "",
            "project_title": rec.get("project_title") or "",
            "appointed": rec.get("captain_assigned") is True,
        })
    return rows


def persona_cards(state_root):
    """uid → name。"""
    raw = _load(os.path.join(state_root, "identity", "cards.json"), {}) or {}
    cards = raw.get("cards", raw) if isinstance(raw, dict) else {}
    out = {}
    for uid, c in cards.items():
        if isinstance(c, dict):
            ref = (c.get("card_ref") or "").split("/")[-1].strip()
            out[uid] = c.get("name") or c.get("display_name") or ref or uid
    return out


CROWN_PERSONA = "71c97a"   # 冠位首綁指名的卡(a persona card,職責=以 the maintainer 語氣代言);seat_rebind.DIRECTED_PERSONA_UID
DEFAULT_VAULT = os.path.expanduser("~/Ez2/Vault")


def persona_profiles(state_root, vault=DEFAULT_VAULT):
    """可比對的人格卡:uid → {name, category, text}。text = 名稱 + 分類 + 角色.md 本文。
    沒名字的、沒本文的不收(空卡比不出東西,收了只會被當成「很像」)。"""
    raw = _load(os.path.join(state_root, "identity", "cards.json"), {}) or {}
    cards = raw.get("cards", raw) if isinstance(raw, dict) else {}
    names = persona_cards(state_root)
    files = {}
    for f in glob.glob(os.path.join(vault, "**", "角色.md"), recursive=True):
        files[os.path.basename(os.path.dirname(f))] = f
    out = {}
    for uid, c in cards.items():
        name = names.get(uid) or ""
        if not name or name == uid or uid not in files:
            continue
        try:
            body = Path(files[uid]).read_text(encoding="utf-8")
        except OSError:
            continue
        body = re.sub(r"^---.*?---", "", body, count=1, flags=re.S).strip()
        if len(body) < 20:
            continue
        cat = (c.get("category") or "") if isinstance(c, dict) else ""
        m = re.search(r"##\s*職責\s*\n+(.+?)(\n##|\Z)", body, re.S)
        duty = re.sub(r"\s+", " ", (m.group(1) if m else body)).strip()
        out[uid] = {"name": name, "category": cat, "duty": duty[:40]}
    return out


def project_cards(state_root):
    """list[{uid,title,folder}]。"""
    out = []
    for f in sorted(glob.glob(os.path.join(state_root, "projects", "proj:*", "card.json"))):
        c = _load(f) or {}
        uid = c.get("project_uid") or Path(f).parent.name
        out.append({"uid": uid, "title": c.get("title") or "", "folder": c.get("folder") or ""})
    return out


def seat_state(state_root, seat, display=None, cards=None):
    """一席的綁定現況 + 問題 + 修法提示。"""
    if display is None:
        display = (_load(os.path.join(state_root, "identity", "seat-display.json"), {}) or {}).get("display") or {}
    if cards is None:
        cards = persona_cards(state_root)
    d = display.get(seat["code"]) or {}
    persona_uid = d.get("persona_uid") or ""
    brain = d.get("brain_binding_status") == "bound"
    cont = _load(os.path.join(state_root, "identity", "seat-continuity", seat["seat_id"] + ".json"), {}) or {}
    st = dict(seat)
    st.update({
        "persona_uid": persona_uid,
        "persona_name": cards.get(persona_uid, d.get("display_name") or "") if persona_uid else "",
        "brain_bound": brain,
        "folder": d.get("folder") or "",
        "continuity_persona": cont.get("persona_uid") or "",
    })
    if seat["provider"] in BOT_PROVIDERS:
        st["problem"], st["hint"] = "bot", "bot 席不在本工具範圍"
    elif persona_uid == CROWN_PERSONA and not seat.get("appointed"):
        # 10-11:冠位人格是「the maintainer 的代言人」,只該在受派艦長身上;最早開而戴冠、或歸隊後冠沒了的席都不對
        st["problem"] = "crown_persona"
        st["hint"] = "頂著冠位人格(首席人格卡)但不是受派艦長:survey 後換一張"
    elif persona_uid and seat["project_uid"]:
        st["problem"], st["hint"] = "ok", ""
    elif persona_uid:
        st["problem"], st["hint"] = "no_project", "只差專案:survey 問它在做什麼"
    elif st["continuity_persona"]:
        st["problem"] = "no_persona"
        st["hint"] = "延續檔記得人格 %s,可 repair 接回" % st["continuity_persona"]
    else:
        st["problem"], st["hint"] = "none", "人格、專案都沒有:survey 問它"
    return st


def status(state_root):
    display = (_load(os.path.join(state_root, "identity", "seat-display.json"), {}) or {}).get("display") or {}
    cards = persona_cards(state_root)
    return [seat_state(state_root, s, display, cards) for s in live_seats(state_root)]


# ── 調查(survey / collect)──────────────────────────────────────────

def survey_dir(state_root):
    return os.path.join(state_root, "bindme")


def survey_path(state_root, sid):
    return os.path.join(survey_dir(state_root), sid + ".json")


def roster_text(profiles, appointed=False):
    """問卷附的既有人格清單:依分類排,一行一張(名稱 —— 一句職責)。冠位人格只給受派艦長。"""
    rows = sorted((p["category"] or "其他", p["name"], p["duty"]) for u, p in profiles.items()
                  if appointed or u != CROWN_PERSONA)
    return "\n".join("[%s] %s —— %s" % r for r in rows)


def survey_message(sid, hours, roster=""):
    tail = ("\n\n既有人格清單(建議人格請從這裡挑名稱;都不合適才寫 新建):\n" + roster) if roster else ""
    return ("[bindme:%s] the maintainer 要整理各席的人格與專案綁定。請只回報、不要開新工作,"
            "照下面格式用 aib dm 回我(第一行原樣抄 [bindme:%s]):\n"
            "席碼: <你自己的五碼席碼>\n"
            "主項目: <一句話,你現在的主線>\n"
            "24小時做了: <最多三件,用分號隔開>\n"
            "建議專案: <你覺得該綁的專案標題,或 proj: 編號;不確定寫 不知道>\n"
            "建議人格: <從下面清單挑一張最貼近你在做的事的名稱;都不合適寫 新建>\n"
            "人格理由: <一句話,為什麼挑它(或為什麼要新建)>\n"
            "範圍:最近 %d 小時內你自己做的開發。" % (sid, sid, hours)) + tail


def run_survey(state_root, me, targets, hours=24, dry_run=False, sender=None, now=None, vault=DEFAULT_VAULT):
    """寄問卷給 targets(list of seat_state)。回調查記錄 dict。"""
    now = now or datetime.datetime.now()
    sid = "%s-%s" % (now.strftime("%Y%m%d-%H%M"), me)
    msg = survey_message(sid, hours, roster_text(persona_profiles(state_root, vault)))
    sent = []
    for st in targets:
        addr = "%s/%s" % (st["cabin4"], st["code"])
        stage = "dry-run"
        if not dry_run:
            stage = (sender or send_dm)(addr, msg)
        sent.append({"code": st["code"], "addr": addr, "seat_id": st["seat_id"], "stage": stage})
    rec = {"sid": sid, "me": me, "hours": hours, "created": now.isoformat(timespec="seconds"),
           "targets": sent, "token": "[bindme:%s]" % sid}
    if not dry_run:
        os.makedirs(survey_dir(state_root), exist_ok=True)
        Path(survey_path(state_root, sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def send_dm(addr, msg):
    p = subprocess.run(["bash", str(AIB), "dm", addr, msg], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    m = re.search(r"stage=([a-z-]+)", p.stdout + p.stderr)
    return m.group(1) if m else "rc=%d" % p.returncode


def read_queue(me):
    p = subprocess.run([sys.executable, str(DM_QUEUE), "list", "--seat", me, "--tab", "all"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return []


def reply_text(row):
    """短訊直接回;長訊落檔的,照指標讀檔。"""
    msg = row.get("msg") or ""
    m = RELAY_RE.search(msg)
    if m:
        try:
            return Path(os.path.expanduser(m.group(1))).read_text(encoding="utf-8")
        except OSError:
            return msg
    return msg


def parse_reply(text):
    out = {}
    for key, val in FIELD_RE.findall(text):
        key = "做了" if "做了" in key else key
        out.setdefault(key, val)
    return out


def _norm(s):
    return re.sub(r"[\s\-_·・()()「」]", "", (s or "").lower())


def match_project(suggest, projects):
    """回 (uid|None, title, how)。how ∈ uid / title / contains / new / unknown / ambiguous。"""
    s = (suggest or "").strip()
    if s.lower() in UNKNOWN:
        return None, "", "unknown"
    m = UID_RE.search(s)
    if m:
        for p in projects:
            if p["uid"] == m.group(0):
                return p["uid"], p["title"], "uid"
        return None, s, "unknown"
    exact = [p for p in projects if _norm(p["title"]) == _norm(s)]
    if len(exact) == 1:
        return exact[0]["uid"], exact[0]["title"], "title"
    part = [p for p in projects if _norm(s) and (_norm(s) in _norm(p["title"]) or _norm(p["title"]) in _norm(s))]
    if len(part) == 1:
        return part[0]["uid"], part[0]["title"], "contains"
    if len(part) > 1:
        return None, "、".join(p["title"] for p in part), "ambiguous"
    return None, s, "new"


def match_persona(suggest, st, cards, reason=""):
    """the maintainer 10-11 01:3x 定的順序:先找既有卡,合適就**問**綁不綁;沒有合適的才用總結新建。
    「合不合適」由那一席自己從問卷附的清單挑(語意判斷交給 AI 席;10-11 實測字面比對 1/5 命中,不用)。
    回 dict {uid, name, how, why}。how:
      keep          已有人格、而且不是放錯位置的冠位人格 ⇒ 不動
      ask_existing  它從清單挑了一張既有卡 ⇒ 要人點頭才綁
      continuity    它寫新建/沒挑,但延續檔記得上一次的人格 ⇒ 接回(同 repair)
      draft_new     它寫新建 ⇒ 用總結擬新卡草稿,不綁
    """
    cur = st.get("persona_uid") or ""
    crown_misplaced = cur == CROWN_PERSONA and not st.get("appointed")
    if cur and not crown_misplaced:
        return {"uid": cur, "name": st.get("persona_name") or cards.get(cur, cur), "how": "keep"}
    exclude = set() if st.get("appointed") else {CROWN_PERSONA}
    s = (suggest or "").strip()
    if s and s.lower() not in UNKNOWN and s not in ("沿用", "新建"):
        hits = [u for u, n in cards.items() if n == s and u not in exclude]
        if len(hits) == 1:
            return {"uid": hits[0], "name": s, "how": "ask_existing", "why": reason or "它自己從清單挑的"}
    cont = st.get("continuity_persona") or ""
    if s != "新建" and cont and cont not in exclude:
        return {"uid": cont, "name": cards.get(cont, cont), "how": "continuity"}
    return {"uid": None, "name": "", "how": "draft_new",
            "why": reason or ("它寫了新建" if s == "新建" else "它挑的「%s」不在清單或不能給它" % s if s else "沒挑")}


def write_persona_draft(state_root, sid, code, fields):
    """沒有合適的既有卡 ⇒ 用它自己的總結擬一張草稿(名稱留給人取),回檔案路徑。"""
    did = [x.strip() for x in re.split(r"[;;]", fields.get("做了") or "") if x.strip()]
    body = "\n".join([
        "# 人格卡草稿(@%s,調查 %s)—— 名稱、紅線待 the maintainer 定" % (code, sid),
        "",
        "名稱:(待取名)",
        "",
        "你是 AIB 的 (待取名),負責「%s」這條線。" % (fields.get("主項目") or "?"),
        "",
        "## 職責",
        fields.get("主項目") or "?",
        "",
        "## 這 24 小時實際做過(擬紅線與知識域的依據)",
    ] + ["- " + d for d in did] + ["", "## 紅線", "(待定)", "", "## 知識域", "(待定)", ""])
    path = os.path.join(survey_dir(state_root), "%s-%s-persona-draft.md" % (sid, code))
    os.makedirs(survey_dir(state_root), exist_ok=True)
    Path(path).write_text(body, encoding="utf-8")
    return path


def summarize(fields):
    head = fields.get("主項目") or ""
    did = (fields.get("做了") or "").split(";")[0].split(";")[0]
    text = head if not did else "%s(%s)" % (head, did)
    return text[:60]


def collect(state_root, sid, rows=None, reader=None, vault=DEFAULT_VAULT):
    """收回覆、提出建議;把建議寫回調查檔。回調查記錄。"""
    rec = _load(survey_path(state_root, sid))
    if not rec:
        raise SystemExit(_ui("aib.bindme.no_survey", id=sid, path=survey_path(state_root, sid)))
    rows = rows if rows is not None else (reader or read_queue)(rec["me"])
    states = {s["code"]: s for s in status(state_root)}
    cards = persona_cards(state_root)
    projects = project_cards(state_root)
    replies = {}
    want = {t["code"] for t in rec["targets"]}
    for r in rows:
        text = reply_text(r)
        if rec["token"] not in text:
            continue
        # ★ 10-11 實踩:背景分身寄的回信,寄件人會被認成別席(44ed3 的回覆掛 a seat)。
        #   內文自報的席碼優先(只認名單內的);沒寫才退回寄件人。
        said = (parse_reply(text).get("席碼") or "").strip().lstrip("@")[:5]
        code = said if said in want else (r.get("sender_seat") or "")
        if code in want:
            replies[code] = text            # 同一席回多次,取最後一封(列表由舊到新)
    proposals = []
    for t in rec["targets"]:
        st = states.get(t["code"]) or dict(t, persona_uid="", persona_name="", continuity_persona="",
                                           project_uid="", project_title="", provider="", problem="gone")
        text = replies.get(t["code"])
        row = {"code": t["code"], "seat_id": t.get("seat_id", ""), "addr": t["addr"], "replied": bool(text),
               "cabin": st.get("cabin") or ""}
        if not text:
            row.update({"summary": "(還沒回)", "persona": None, "project": None})
            proposals.append(row)
            continue
        f = parse_reply(text)
        per = match_persona(f.get("建議人格"), st, cards, f.get("人格理由") or "")
        if per["how"] == "draft_new":
            per["draft_path"] = write_persona_draft(state_root, sid, t["code"], f)
        suid, stitle, show = match_project(f.get("建議專案"), projects)
        if st.get("project_uid"):
            juid, jtitle, jhow = st["project_uid"], st.get("project_title") or "", "keep"
        else:
            juid, jtitle, jhow = suid, stitle, show
        row.update({"summary": summarize(f), "fields": f,
                    "persona": per,
                    "project": {"uid": juid, "title": jtitle, "how": jhow},
                    # 已綁的席也拿它自己的建議來比:別人替它綁的(10-11 先猜後綁)要能被它自己的回答推翻
                    "self_suggested": {"uid": suid, "title": stitle, "how": show},
                    "mismatch": bool(jhow == "keep" and suid and suid != juid)})
        proposals.append(row)
    rec["proposals"] = proposals
    rec["collected"] = datetime.datetime.now().isoformat(timespec="seconds")
    Path(survey_path(state_root, sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


# ── 倒數選單(the maintainer 10-11 02:2x:「建議的部份要有個倒數 30 秒,如果沒有選就依你的預設」)──────────

MENU_TIMEOUT_S = 30


def _hub_session(sock, tmux="tmux"):
    p = subprocess.run([tmux, "-L", sock, "list-sessions", "-F", "#{session_name}"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=5,
                       env={k: v for k, v in os.environ.items() if not k.startswith("TMUX")})
    names = p.stdout.split() if p.returncode == 0 else []
    return names[0] if names else ""


def _hub_client(sock, tmux="tmux"):
    p = subprocess.run([tmux, "-L", sock, "list-clients", "-F", "#{client_name}"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=5,
                       env={k: v for k, v in os.environ.items() if not k.startswith("TMUX")})
    names = p.stdout.split() if p.returncode == 0 else []
    return names[0] if names else ""


def countdown_menu(cabin, title, items, default_id, timeout=MENU_TIMEOUT_S, state_root=None, tmux="tmux"):
    """在那一席所屬艙的畫面跳 tmux 浮窗,**每秒更新剩幾秒**(the maintainer 10-11 02:4x:「30 秒沒選,沒倒數?」——
    display-menu 只能顯示固定標題,所以改成 display-popup 裡跑一小段倒數腳本)。按 1/2… 當場選;
    倒數到 0 浮窗自己關、走預設。items=[(id, 顯示字)]。回 (choice, how):
      picked  有人選了
      timeout 浮窗跳出來、倒數到 0 沒人選 ⇒ default_id
      closed  倒數沒到就被關掉(沒選)⇒ 當成「不要」:人主動關掉不算「沒選」
      no_menu 跳不出來(那一艙沒人接著、socket 不在…)⇒ **None**:手動 apply 照舊「人沒看到就不能當成沒選」;
              自動模式(auto_apply)依 the maintainer 10-13 01:5x 確認改成等滿時間照預設,另送決定卡通知"""
    import cabin_restart   # R8:hub 名一律向單源要(cabin_resolve.sh --hub),不在這裡自己拼
    sock = cabin_restart._hub_of(cabin) or ""
    env = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}
    try:
        client = _hub_client(sock, tmux) if sock else ""
    except (OSError, subprocess.SubprocessError):
        client = ""
    if not client:
        return None, "no_menu"
    d = survey_dir(state_root or os.path.expanduser("~/.ez2-aib"))
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, "choice-%d-%d" % (os.getpid(), int(time.time() * 1000)))
    default_label = next((lab for cid, lab in items if cid == default_id), default_id)
    lines = "".join("printf '  [%d] %%s\\n' %s; " % (n, shlex.quote(lab)) for n, (cid, lab) in enumerate(items, 1))
    cases = "".join("%d) printf %%s %s > \"$f\"; exit 0;; " % (n, shlex.quote(cid)) for n, (cid, lab) in enumerate(items, 1))
    script = ("f=%s; t=%d; while [ \"$t\" -gt 0 ]; do printf '\\033[2J\\033[H\\n  %%s\\n\\n' %s; %s"
              "printf '\\n  ⏳ 剩 %%2d 秒 —— 沒選 = %%s\\n' \"$t\" %s; "
              "if read -rsn1 -t 1 k; then case \"$k\" in %s esac; fi; t=$((t-1)); done"
              % (shlex.quote(f), timeout, shlex.quote(title), lines, shlex.quote(default_label), cases))
    w = max(48, min(100, max(len(title), 30) * 2 + 8))
    args = [tmux, "-L", sock, "display-popup", "-c", client, "-E", "-w", str(w), "-h", str(len(items) + 8),
            "-T", " AIB 綁定提案 ", "bash -c %s" % shlex.quote(script)]
    t0 = time.time()
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return None, "no_menu"
    try:
        proc.wait(timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        proc.kill()
    rc, spent = proc.returncode, time.time() - t0
    choice = None
    if os.path.exists(f):
        choice = Path(f).read_text(encoding="utf-8").strip() or None
        try:
            os.unlink(f)
        except OSError:
            pass
    if choice:
        return choice, "picked"
    if rc not in (0, None, -9) and spent < 1.5:
        return None, "no_menu"
    if spent < timeout - 1:
        return "no", "closed"
    return default_id, "timeout"


def ask_row(row, menu=None, timeout=MENU_TIMEOUT_S, state_root=None):
    """一列建議跳倒數選單。回 (accept: bool|None, how)。None = 選單沒跳出來 ⇒ 不綁、留提案。"""
    per, prj = row.get("persona") or {}, row.get("project") or {}
    parts = []
    if per.get("how") == "ask_existing":
        parts.append("人格「%s」" % per.get("name"))
    if prj.get("how") in ("uid", "title", "contains") and per.get("how") in ("keep", "ask_existing", "continuity"):
        parts.append("專案「%s」" % prj.get("title"))        # 沒人格綁不了專案 ⇒ 不為它跳選單
    if not parts:
        return None, "nothing_to_ask"
    cabin = row.get("cabin") or ""
    title = "@%s 綁 %s?(%d 秒沒選 = 照建議綁)" % (row["code"], "+".join(parts), timeout)
    items = [("yes", "照建議綁(預設)"), ("no", "不綁,留著提案")]
    choice, how = (menu or countdown_menu)(cabin, title, items, "yes", timeout=timeout, state_root=state_root)
    if choice is None:
        return None, how
    return choice == "yes", how


# ── 自動綁定(記憶整理第 2 包;the maintainer 10-13 01:5x 在 a seat 窗裡確認「同意自動綁」)────────────

MAC_TIMEOUT_S = 30
_OSA_GAVE_UP = re.compile(r"gave up:\s*true")
_OSA_BUTTON = re.compile(r"button returned:([^,\n]*)")
_OSA_TEXT = re.compile(r"text returned:(.*?)(?:, gave up:|$)", re.S)


def _osa_quote(text):
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


def _osa(script, timeout, runner=None):
    """跑一段 AppleScript。回 (rc, stdout, stderr);叫不起來(不是 macOS、沒 osascript、逾時)⇒ (None, "", 原因)。"""
    if runner:
        return runner(script, timeout)
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return None, "", "no osascript"
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", str(exc)
    return p.returncode, p.stdout, p.stderr


def mac_dialog(title, message, buttons, default, cancel, timeout=MAC_TIMEOUT_S, runner=None):
    """Mac 跳出視窗(最多 3 顆按鈕)。回 (按了哪顆或預設, how):
      picked  有人按了 ⇒ 那顆
      timeout 逾時(gave up:true)⇒ default
      closed  按了 cancel 那顆或 Esc(osascript 錯誤 -128)⇒ cancel
      no_menu 叫不起來(沒有畫面、不是 macOS…)⇒ None"""
    script = ("display dialog %s with title %s buttons {%s} default button %s cancel button %s giving up after %d"
              % (_osa_quote(message), _osa_quote(title), ", ".join(_osa_quote(b) for b in buttons),
                 _osa_quote(default), _osa_quote(cancel), int(timeout)))
    rc, out, err = _osa(script, timeout, runner)
    if rc is None:
        # 視窗跳出來了、只是 osascript 本身被逾時砍掉(機器忙)⇒ 跟 gave up 一樣算逾時,不是「沒畫面」
        return (default, "timeout") if err == "timeout" else (None, "no_menu")
    if rc != 0:
        return (cancel, "closed") if "-128" in (err or "") else (None, "no_menu")
    if _OSA_GAVE_UP.search(out or ""):
        return default, "timeout"
    m = _OSA_BUTTON.search(out or "")
    btn = (m.group(1).strip() if m else "") or default
    return btn, ("closed" if btn == cancel else "picked")


def mac_text(title, prompt, default, timeout=MAC_TIMEOUT_S, runner=None):
    """輸入框(預填 default)。回字串;逾時、取消、叫不起來 ⇒ default。"""
    script = ('display dialog %s with title %s default answer %s buttons {"取消", "好"} default button "好" '
              'cancel button "取消" giving up after %d' % (_osa_quote(prompt), _osa_quote(title), _osa_quote(default),
                                                          int(timeout)))
    rc, out, _err = _osa(script, timeout, runner)
    if rc != 0 or _OSA_GAVE_UP.search(out or ""):
        return default
    m = _OSA_TEXT.search(out or "")
    got = (m.group(1).strip() if m else "")
    return got or default


def mac_choose(title, prompt, items, timeout=MAC_TIMEOUT_S, runner=None):
    """清單挑一個(choose from list 沒有 giving up ⇒ 靠逾時砍掉)。回 (選到的字串或 None, how):
    picked 選了;closed 按了取消(回 false);timeout 逾時被砍;no_menu 叫不起來或清單是空的。"""
    if not items:
        return None, "no_menu"
    script = "choose from list {%s} with title %s with prompt %s" % (
        ", ".join(_osa_quote(i) for i in items[:9]), _osa_quote(title), _osa_quote(prompt))
    rc, out, err = _osa(script, timeout, runner)
    if rc is None:
        return None, ("timeout" if err == "timeout" else "no_menu")
    got = (out or "").strip()
    if rc != 0:
        return None, ("closed" if "-128" in (err or "") else "no_menu")
    if not got or got == "false":
        return None, "closed"
    return (got, "picked") if got in items else (None, "closed")


# 回報句的開頭(「能用:…」「不能用:…」),不是名字(10-14 有一席 Grok 的人格被取成「能用 待審預設、主艙改名」)
REPORT_HEAD_RE = re.compile(r"^\s*(現在)?(能不能用|還不能用|不能用|能用|可以用|還不能)\s*[::,,]\s*")


def clean_name(s, limit):
    """建議的名稱 ⇒ 拿掉回報句開頭與檔名不能用的字、截到 limit 字;剩空 ⇒ ""。"""
    s = REPORT_HEAD_RE.sub("", (s or "").strip())
    return re.sub(r"[\\/:*?\"<>|\n\r\t]", " ", s).strip()[:limit].strip()


def new_persona_name(fields, cards):
    """新卡名稱:只用回覆的「新人格名稱」或記憶整理的「建議人格」(拿掉回報句開頭);沒有 ⇒ ""(不再拿「主項目」那句進度回報硬切 12 字當名字,
    呼叫端不自動建、交給人取名)。和既有卡同名 ⇒ 加序號(不覆蓋既有卡)。"""
    sug = (fields.get("建議人格") or "").strip()      # 記憶整理「新建:<名稱>」把名稱放這裡;關鍵字不算名字
    if sug.lower() in UNKNOWN or sug in ("沿用", "新建"):
        sug = ""
    base = clean_name(fields.get("新人格名稱") or sug, 12)
    if not base:
        return ""
    taken = set(cards.values())
    name, n = base, 1
    while name in taken:
        n += 1
        name = "%s-%d" % (base, n)
    return name


def finalize_draft(path, name):
    """草稿的「(待取名)」換成名稱,回新路徑(newrole --prompt-file 用)。"""
    text = Path(path).read_text(encoding="utf-8").replace("(待取名)", name)
    out = path.replace("-persona-draft.md", "-persona-final.md")
    Path(out).write_text(text, encoding="utf-8")
    return out


def _candidates(row, cards, kind, projects=()):
    """「改綁其他候選」的清單(最多 9 個):人格 = 既有卡(不含冠位);專案 = 對到的那幾張,沒有就列全部專案。"""
    if kind == "persona":
        return [n for u, n in sorted(cards.items(), key=lambda x: x[1]) if u != CROWN_PERSONA][:9]
    prj = row.get("project") or {}
    hit = [t for t in (prj.get("title") or "").split("、") if t] if prj.get("how") == "ambiguous" else []
    return (hit or [p["title"] for p in projects if p.get("title")])[:9]


def _ask(row, msg, label, timeout, dialog, menu, sleep, out):
    """一個「照預設 / 改… / 先不要」問題。Mac 彈窗 → 叫不起來退回那一艙的 tmux 倒數浮窗(沒有「改…」)→
    都叫不起來:等滿 timeout 照預設,記 out["card"](事後送決定卡)。回 (按鈕, how)。"""
    btn, asked = dialog("AIB 綁定", msg, ["先不要", "改…", label], label, "先不要", timeout)
    if asked != "no_menu":
        return btn, asked
    choice, how = (menu or countdown_menu)(row.get("cabin") or "", msg.split("\n")[0],
                                           [("yes", label + "(預設)"), ("no", "先不要")], "yes", timeout=timeout)
    if choice is not None:
        return (label if choice == "yes" else "先不要"), how
    (sleep or time.sleep)(timeout)
    out["card"] = True
    return label, "no_menu"


def auto_decide(row, cards, timeout=MAC_TIMEOUT_S, dialog=None, text=None, choose=None, sleep=None, projects=(),
                menu=None):
    """一列 → 自動模式的決定。回 dict:
      persona: {"act": "keep"|"bind"|"create"|"skip", "name", "how"}
      project: {"act": "keep"|"bind"|"create"|"skip", "uid"|"title", "how"}
      asked:   picked / timeout / closed / no_menu / none
      notice:  True ⇒ 要發「已綁 X」通知;card: True ⇒ 要送事後決定卡(沒畫面時)"""
    dialog, text, choose = dialog or mac_dialog, text or mac_text, choose or mac_choose
    per, prj = row.get("persona") or {}, row.get("project") or {}
    out = {"persona": {"act": "skip"}, "project": {"act": "skip"}, "asked": "none", "notice": False, "card": False}
    how = per.get("how")
    if how == "keep":
        out["persona"] = {"act": "keep", "how": how}
    elif how in ("ask_existing", "continuity") and per.get("name") and per.get("uid") != CROWN_PERSONA \
            and row.get("confirm"):
        # 10-13 a seat(the maintainer 16:5x「應該彈出對話,並倒數30秒,如果用戶沒有做選擇,就照你的預設更新」):
        #   綁定檢查(已綁、但跟最近 4 小時不合)要換成既有卡 ⇒ 也問,30 秒沒選照預設換
        msg = "@%s 最近 4 小時做的事跟現在的人格「%s」不合,要換成「%s」嗎?\n\n%d 秒後自動換" % (
            row["code"], row.get("old_persona") or "-", per["name"], timeout)
        btn, asked = _ask(row, msg, "換成這張", timeout, dialog, menu, sleep, out)
        out["asked"] = asked
        if btn == "改…":
            pick, pick_how = choose("AIB 綁定", "改綁哪一張?", _candidates(row, cards, "persona"), timeout)
            btn = None
            if pick:
                out["persona"] = {"act": "bind", "name": pick, "how": "picked"}
            elif pick_how != "closed":
                btn = "換成這張"
        if btn == "換成這張":
            out["persona"] = {"act": "bind", "name": per["name"], "how": how}
        elif out["persona"]["act"] == "skip":
            out["persona"] = {"act": "keep", "how": "kept-by-human"}      # 先不要 = 留著現在那張
    elif how in ("ask_existing", "continuity") and per.get("name") and per.get("uid") != CROWN_PERSONA:
        out["persona"] = {"act": "bind", "name": per["name"], "how": how}
        out["notice"] = True
    elif how == "draft_new" and not new_persona_name(row.get("fields") or {}, cards):
        # 沒有建議名稱 ⇒ 不自動建(名字要人取),送事後決定卡;草稿照擬,名稱維持(待取名)
        out["persona"] = {"act": "skip", "how": "needs_name"}
        out["card"] = True
        out.setdefault("why", []).append("沒有合適的人格,也沒有建議名稱 ⇒ 不自動建,等人取名")
    elif how == "draft_new":
        name = new_persona_name(row.get("fields") or {}, cards)
        msg = "@%s 沒有合適的人格,要建一張新的\n\n名字:%s\n\n%d 秒後自動建立" % (row["code"], name, timeout)
        btn, asked = _ask(row, msg, "照這樣建立", timeout, dialog, menu, sleep, out)
        out["asked"] = asked
        if btn == "改…":
            sub, sub_how = dialog("AIB 綁定", "要改什麼?", ["取消", "改綁其他候選", "改名字"], "改名字", "取消", timeout)
            if sub_how == "timeout":
                btn = "照這樣建立"
            elif sub == "改名字":
                name = new_persona_name({"新人格名稱": text("AIB 綁定", "新人格名稱", name, timeout)}, cards) or name
                btn = "照這樣建立"
            elif sub == "改綁其他候選":
                pick, pick_how = choose("AIB 綁定", "改綁哪一張?", _candidates(row, cards, "persona"), timeout)
                if pick:
                    out["persona"] = {"act": "bind", "name": pick, "how": "picked"}
                    btn = None
                else:
                    btn = "先不要" if pick_how == "closed" else "照這樣建立"   # 取消 = 不要;逾時 / 叫不起來 = 照預設
            else:
                btn = "先不要"
        if btn == "照這樣建立":
            out["persona"] = {"act": "create", "name": name, "how": how}
    persona_ready = out["persona"]["act"] in ("keep", "bind", "create")
    if not persona_ready and out["persona"]["act"] == "skip":
        out.setdefault("why", []).append("沒有人格 ⇒ 專案先不綁(newproj 要先有人格)")
    phow = prj.get("how")
    if phow == "keep":
        out["project"] = {"act": "keep", "how": phow}
    elif phow in ("uid", "title") and prj.get("uid") and persona_ready and row.get("confirm"):
        msg = "@%s 最近 4 小時做的事跟現在的專案「%s」不合,要換成「%s」嗎?\n\n%d 秒後自動換" % (
            row["code"], row.get("old_project") or "-", prj.get("title") or prj["uid"], timeout)
        btn, asked = _ask(row, msg, "換成這個", timeout, dialog, menu, sleep, out)
        if out["asked"] in ("none", "picked"):
            out["asked"] = asked
        if btn == "改…":
            pick, pick_how = choose("AIB 綁定", "改綁哪一個專案?", _candidates(row, cards, "project", projects), timeout)
            btn = None
            if pick:
                out["project"] = {"act": "bind_title", "title": pick, "how": "picked"}
            elif pick_how != "closed":
                btn = "換成這個"
        if btn == "換成這個":
            out["project"] = {"act": "bind", "uid": prj["uid"], "title": prj.get("title"), "how": phow}
        elif out["project"]["act"] == "skip":
            out["project"] = {"act": "keep", "how": "kept-by-human"}
    elif phow in ("uid", "title") and prj.get("uid") and persona_ready:
        out["project"] = {"act": "bind", "uid": prj["uid"], "title": prj.get("title"), "how": phow}
        out["notice"] = True
    elif phow in ("ambiguous", "unknown") or (phow == "new" and not clean_name(prj.get("title"), 24)):
        # 沒有建議專案名稱(或對到多張)⇒ 不自動建、不拿「主項目」那句進度回報當名字;送事後決定卡
        if persona_ready:
            out["card"] = True
            out.setdefault("why", []).append("沒有合適的專案,也沒有可用的建議名稱 ⇒ 不自動建,等人決定")
    elif phow in ("contains", "new") and persona_ready:
        title = (prj.get("title") or "") if phow == "contains" else clean_name(prj.get("title"), 24)
        if phow == "contains":
            label, msg = "綁這張", "@%s 專案部分對到「%s」,要綁它嗎?\n\n%d 秒後自動綁" % (row["code"], title, timeout)
        else:
            label, msg = "照這樣建立", "@%s 沒有合適的專案,要建一個新的\n\n名稱:%s\n\n%d 秒後自動建立" % (
                row["code"], title, timeout)
        btn, asked = _ask(row, msg, label, timeout, dialog, menu, sleep, out)
        if out["asked"] in ("none", "picked"):
            out["asked"] = asked
        if btn == "改…":
            pick, pick_how = choose("AIB 綁定", "改綁哪一個專案?", _candidates(row, cards, "project", projects), timeout)
            btn = None
            if pick:
                out["project"] = {"act": "bind_title", "title": pick, "how": "picked"}
            elif pick_how != "closed":
                btn = label                                   # 逾時 / 叫不起來 ⇒ 照第一層預設
        if btn == label:
            if phow == "contains" and prj.get("uid"):
                out["project"] = {"act": "bind", "uid": prj["uid"], "title": title, "how": phow}
            else:
                if title:
                    out["project"] = {"act": "create", "title": title, "how": phow}
    return out


def auto_actions(row, decision, draft_path=None):
    """決定 → (要跑的 aib 指令清單 [(kind, args_without_actor)], 跳過原因)。人格先、專案後(newproj 要先有人格)。"""
    acts = []
    per, prj = decision["persona"], decision["project"]
    if per["act"] == "bind":
        acts.append(("persona", ["newrole", "--name", per["name"], "--seat", row["addr"], "--brain", "none"]))
    elif per["act"] == "create":
        if not draft_path or not os.path.isfile(draft_path):
            return acts, ["要建新人格「%s」但沒有草稿可當人設(newrole 不准綁空範本)⇒ 不建" % per["name"]]
        acts.append(("persona", ["newrole", "--name", per["name"], "--seat", row["addr"], "--brain", "none",
                                 "--prompt-file", finalize_draft(draft_path, per["name"])]))
    if prj["act"] == "bind":
        acts.append(("project", ["newproj", "--seat", row["addr"], "--project-uid", prj["uid"]]))
    elif prj["act"] == "create":
        acts.append(("project", ["newproj", "--seat", row["addr"], "--title", prj["title"]]))
    return acts, []


def notify_bound(row, names, runner=None):
    """「已綁 X」通知:Mac 右上角通知(不擋流程);要換掉照通知上的指令。叫不起來就算了。"""
    if not names:
        return False
    msg = "@%s 已綁:%s(不對就跑 aib bindme repick --me %s)" % (row["code"], "、".join(names), row["code"])
    rc, _out, _err = _osa("display notification %s with title %s" % (_osa_quote(msg), _osa_quote("AIB 綁定")), 10, runner)
    return rc == 0


def captain_addr(state_root):
    """艦長室收件匣讀的是艦長席的佇列(state/captain-room/last-state.json 的 cabin / captain);讀不到回 None。"""
    d = _load(os.path.join(state_root, "state", "captain-room", "last-state.json"), {}) or {}
    cab, cap = str(d.get("cabin") or ""), str(d.get("captain") or "")
    cab4 = cab.rsplit("-", 1)[-1] if cab else ""
    return "%s/%s" % (cab4, cap) if cab4 and CODE_RE.match(cap) else None


def send_decide(addr, body, thread):
    p = subprocess.run(["bash", str(AIB), "dm", addr, "--kind", "decide", "--thread", thread, body],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    m = re.search(r"stage=([a-z-]+)", p.stdout + p.stderr)
    return m.group(1) if m else "rc=%d" % p.returncode


def decide_card(row, summary, sender=None, state_root=None):
    """沒畫面時照預設建了 ⇒ 送一張艦長室決定卡當事後通知(行首編號選項才有按鈕);讀不到艦長席就不送。"""
    addr = captain_addr(state_root or os.path.expanduser("~/.ez2-aib"))
    if not addr and not sender:
        return None
    body = ("@%s 綁定:沒有人看著畫面,30 秒後已照預設%s。\n1. 照這樣\n2. 換掉(@%s 會跑 aib bindme repick)\n"
            "你不回我就走 1。" % (row["code"], summary, row["code"]))
    try:
        if sender:
            return sender(addr or row["addr"], body)
        return send_decide(addr, body, "decide/bind-%s" % row["code"])
    except Exception:  # noqa: BLE001 —— 通知失敗不影響已經做好的綁定
        return None


def auto_apply(state_root, row, actor, runner=None, dialog=None, text=None, choose=None, sleep=None,
               notifier=None, sender=None, timeout=MAC_TIMEOUT_S, menu=None):
    """自動模式:決定 → 綁 / 建 → 通知。回 {"decision", "done", "skipped"}。bot 席不跑。"""
    if row.get("provider") in BOT_PROVIDERS:
        return {"decision": None, "done": [], "skipped": ["bot 席不碰"]}
    cards = persona_cards(state_root)
    projects = project_cards(state_root)
    d = auto_decide(row, cards, timeout, dialog, text, choose, sleep, projects, menu)
    if d["project"]["act"] == "bind_title":            # 從清單挑的是標題 ⇒ 換回 uid(綁既有,不新建)
        uid = next((p["uid"] for p in projects if p["title"] == d["project"]["title"]), None)
        d["project"] = ({"act": "bind", "uid": uid, "title": d["project"]["title"], "how": "picked"} if uid
                        else {"act": "skip", "how": "picked_missing"})
    acts, why = auto_actions(row, d, (row.get("persona") or {}).get("draft_path"))
    why = list(d.get("why") or []) + why
    done = []
    for kind, args in acts:
        rc, out = (runner or run_aib)(args + ["--actor", actor])
        done.append({"kind": kind, "rc": rc, "tail": out.strip().splitlines()[-1:] if out else []})
        if rc != 0:
            why.append("%s 失敗 rc=%d,後面不做" % (kind, rc))
            break
    ok = {r["kind"] for r in done if r["rc"] == 0}
    names = [x for x in ((d["persona"].get("name") if "persona" in ok else None),
                         (d["project"].get("title") if "project" in ok else None)) if x]
    if d["notice"] and names:
        (notifier or notify_bound)(row, names)
    card_note = ""
    if d["card"]:
        if names:
            stage = decide_card(row, "建立並綁上「%s」" % "、".join(names), sender, state_root)
            if stage in ("delivered", "queued", "sort-queued"):      # sort-queued = 艦長席忙、已排進佇列會自動送
                card_note = "已送決定卡"
            elif stage == "sent-unverified":
                card_note = "決定卡已送、沒驗到"
            else:
                card_note = "決定卡沒送出(%s)" % (stage or "讀不到艦長席")
        else:
            card_note = "沒有綁成,不送決定卡"
    if d["asked"] != "none":
        why.insert(0, {"picked": "視窗:有人選了", "timeout": "視窗:30 秒沒選,照預設",
                       "closed": "視窗:按了先不要",
                       "no_menu": "沒有畫面:等滿 30 秒照預設;%s" % card_note}.get(d["asked"], d["asked"]))
    return {"decision": d, "done": done, "skipped": why}


def auto_enabled(state_root=None):
    """自動綁開不開 —— 只看明確的開關,不看路徑長得像不像真家目錄(a seat 10-13 11:34):
    環境變數 AIB_AUTO_BIND=0/1 優先;沒設看 <狀態根>/config/auto-bind.json {"enabled": false} ;都沒有 ⇒ 開(預設開)。
    在暫存狀態根跑交接的測試要自己設 AIB_AUTO_BIND=0,不然背景會跳真視窗、真的建卡。"""
    v = os.environ.get("AIB_AUTO_BIND")
    if v in ("0", "1"):
        return v == "1"
    cfg = _load(os.path.join(state_root or os.path.expanduser("~/.ez2-aib"), "config", "auto-bind.json"), {}) or {}
    return cfg.get("enabled", True) is not False


def spawn_auto(state_root, sid, me):
    """交接第三步不等:背景跑 `bindme.py auto <sid> --me <席碼>`(視窗最多約 1~3 分鐘),結果寫回調查檔。
    輸入輸出接 /dev/null、自成一組行程;任何失敗回 None。"""
    try:
        log = os.path.join(survey_dir(state_root), "%s-auto.log" % sid)     # 背景出錯要有痕跡可查
        os.makedirs(survey_dir(state_root), exist_ok=True)
        with open(os.devnull, "rb") as fin, open(log, "ab") as ferr:
            p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--state-root", state_root,
                                  "auto", sid, "--me", me], stdin=fin, stdout=ferr, stderr=ferr,
                                 start_new_session=True, env=dict(os.environ), close_fds=True)
        return p.pid
    except Exception:  # noqa: BLE001
        return None


def repick(state_root, me, actor, runner=None, choose=None):
    """「不對,換掉」:列既有人格卡(不含冠位)讓人挑,改綁所選那張;專案同樣列出可挑。回 [(kind, rc)]。"""
    choose = choose or mac_choose
    addr = next(("%s/%s" % (st["cabin4"], st["code"]) for st in status(state_root) if st["code"] == me), None)
    if not addr:
        raise SystemExit("認不出 --me %s 是哪一席" % me)
    out = []
    cards = persona_cards(state_root)
    pick, _how = choose("AIB 換掉", "改綁哪一張人格?(取消 = 人格不換)",
                        [n for u, n in sorted(cards.items(), key=lambda x: x[1]) if u != CROWN_PERSONA][:9], 120)
    if pick:
        rc, _ = (runner or run_aib)(["newrole", "--name", pick, "--seat", addr, "--brain", "none", "--actor", actor])
        out.append(("persona", rc))
    projects = project_cards(state_root)
    pick, _how = choose("AIB 換掉", "改綁哪一個專案?(取消 = 專案不換)", [p["title"] for p in projects][:9], 120)
    uid = next((p["uid"] for p in projects if p["title"] == pick), None) if pick else None
    if uid:
        rc, _ = (runner or run_aib)(["newproj", "--seat", addr, "--project-uid", uid, "--actor", actor])
        out.append(("project", rc))
    return out


# ── 綁(apply / repair)──────────────────────────────────────────────

def run_aib(args):
    p = subprocess.run(["bash", str(AIB)] + args, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=300)
    return p.returncode, p.stdout + p.stderr


def plan_for(row, create_missing=False, accept=False):
    """一列建議 → 要跑的動作清單 [(kind, args_without_actor)] 與跳過原因。
    人格:ask_existing 要 accept(人點頭)才綁;continuity 直接接回;draft_new 永遠不綁(名稱要人取)。
    專案:這一席有人格(原本就有,或這一輪會綁上)才綁 —— newproj 要先有人格才綁得上資料夾。"""
    acts, why = [], []
    if not row.get("replied"):
        return [], ["還沒回"]
    per, prj = row.get("persona") or {}, row.get("project") or {}
    how = per.get("how")
    persona_ready = how == "keep"
    if how == "ask_existing":
        if accept:
            acts.append(("persona", ["newrole", "--name", per["name"], "--seat", row["addr"], "--brain", "none"]))
            persona_ready = True
        else:
            why.append("人格建議綁既有「%s」,要你點頭(--accept)" % per.get("name"))
    elif how == "continuity" and per.get("name"):
        acts.append(("persona", ["newrole", "--name", per["name"], "--seat", row["addr"], "--brain", "none"]))
        persona_ready = True
    elif how == "draft_new":
        why.append("沒有合適的既有人格,已擬新卡草稿 %s(名稱要你取)" % (per.get("draft_path") or "?"))
    if prj.get("how") in ("uid", "title", "contains") and prj.get("uid"):
        if persona_ready:
            acts.append(("project", ["newproj", "--seat", row["addr"], "--project-uid", prj["uid"], "--deliver"]))
        else:
            why.append("還沒有人格,專案先不綁(newproj 要先有人格)")
    elif prj.get("how") == "new":
        if create_missing and persona_ready:
            acts.append(("project", ["newproj", "--seat", row["addr"], "--title", prj["title"], "--deliver"]))
        else:
            why.append("專案要新建「%s」(要加 --create-missing,且要先有人格)" % prj.get("title"))
    elif prj.get("how") in ("unknown", "ambiguous"):
        why.append("專案要人決定(%s)" % (prj.get("title") or "沒給"))
    return acts, why


def apply(state_root, sid, actor, only=None, create_missing=False, dry_run=False, runner=None, accept=False,
          ask=False, menu=None, timeout=MENU_TIMEOUT_S, deliver=True):
    rec = _load(survey_path(state_root, sid))
    if not rec or "proposals" not in rec:
        raise SystemExit(_ui("aib.bindme.not_collected", id=sid))
    results = []
    for row in rec["proposals"]:
        if only and row["code"] not in only:
            continue
        row_accept, asked = accept, None
        if ask and not accept:
            row_accept, asked = ask_row(row, menu=menu, timeout=timeout, state_root=state_root)
        acts, why = plan_for(row, create_missing, bool(row_accept))
        if not deliver:
            acts = [(k, [x for x in a if x != "--deliver"]) for k, a in acts]
        if asked:
            why.insert(0, {"picked": "選單:%s" % ("照建議綁" if row_accept else "不綁"),
                           "closed": "倒數沒到就被關掉,當成不綁",
                           "timeout": "選單:%d 秒沒選,照預設綁" % timeout,
                           "no_menu": "選單跳不出來(人沒看到),不綁、留著提案",
                           "nothing_to_ask": ""}.get(asked, asked))
            why = [w for w in why if w]
        done = []
        for kind, args in acts:
            args = args + ["--actor", actor] + (["--dry-run"] if dry_run else [])
            rc, out = (runner or run_aib)(args)
            done.append({"kind": kind, "rc": rc, "tail": out.strip().splitlines()[-1:] if out else []})
            if rc != 0:
                why.append("%s 失敗 rc=%d,後面不做" % (kind, rc))
                break
        results.append({"code": row["code"], "done": done, "skipped": why})
    return results


def repair(state_root, actor, only=None, dry_run=False, runner=None):
    """人格不見、延續檔記得 → 用 newrole 把同一張卡接回(只綁人格,不動專案)。"""
    cards = persona_cards(state_root)
    out = []
    for st in status(state_root):
        if st["problem"] != "no_persona" or (only and st["code"] not in only):
            continue
        name = cards.get(st["continuity_persona"])
        if not name:
            out.append({"code": st["code"], "skipped": "延續檔的人格 %s 在卡片表查不到" % st["continuity_persona"]})
            continue
        args = ["newrole", "--name", name, "--seat", "%s/%s" % (st["cabin4"], st["code"]),
                "--brain", "none", "--actor", actor] + (["--dry-run"] if dry_run else [])
        rc, txt = (runner or run_aib)(args)
        out.append({"code": st["code"], "persona": name, "rc": rc})
    return out


# ── 印表 ────────────────────────────────────────────────────────────

HOW_TEXT = {"keep": "沿用現有", "ask_existing": "它挑的既有卡", "continuity": "從延續檔接回",
            "draft_new": "要新建(已擬草稿)", "uid": "它點名的編號", "title": "標題對到", "contains": "標題部分對到",
            "new": "要新建", "unknown": "要人決定", "ambiguous": "對到多張,要人決定"}
PROBLEM_TEXT = {"ok": "✅ 齊", "crown_persona": "⚠ 冠位人格錯置", "no_project": "缺專案", "no_persona": "缺人格", "none": "兩個都缺", "bot": "bot(略)"}


def print_status(rows):
    print("%-12s %-6s %-9s %-14s %-22s %s" % (_ui("aib.bindme.col_seat"), "seat_id", _ui("aib.bindme.col_engine"),
                                              _ui("aib.bindme.col_persona"), _ui("aib.bindme.col_project"), _ui("aib.bindme.col_status")))
    for r in rows:
        print("%-12s %-6s %-9s %-14s %-22s %s%s" % (
            "%s/@%s" % (r["cabin4"], r["code"]), r["seat_id"], r["provider"],
            (r["persona_name"] or "—")[:14], (r["project_title"] or "—")[:22],
            PROBLEM_TEXT.get(r["problem"], r["problem"]), (" —— " + r["hint"]) if r["hint"] else ""))


def print_proposals(rec):
    print(_ui("aib.bindme.survey_head", id=rec["sid"], n=len(rec["proposals"]),
              count=sum(1 for p in rec["proposals"] if p["replied"])))
    for p in rec["proposals"]:
        per, prj = p.get("persona") or {}, p.get("project") or {}
        acts, why = plan_for(p)
        print("@%s(%s) %s" % (p["code"], p.get("seat_id", ""), p["summary"]))
        if p["replied"]:
            print("    " + _ui("aib.bindme.row_detail", name=per.get("name") or "—", detail=per.get("how"),
                         title=prj.get("title") or "—", kind=prj.get("how")))
            if per.get("how") == "ask_existing":
                print("    " + _ui("aib.bindme.picked_existing", name=per.get("name"), why=per.get("why") or ""))
            if per.get("how") == "draft_new":
                print("    " + _ui("aib.bindme.no_fit_draft", why=per.get("why") or "", path=per.get("draft_path") or "?"))
            if p.get("mismatch"):
                print("    " + _ui("aib.bindme.self_vs_bound", name=(p.get("self_suggested") or {}).get("title"), title=prj.get("title")))
            print("    " + _ui("aib.bindme.apply_will", what="、".join(k for k, _ in acts) or _ui("aib.bindme.nothing"),
                         detail=_ui("aib.bindme.skipped", items=";".join(why)) if why else ""))


def _actor(state_root, me, explicit):
    if explicit:
        return explicit
    for s in live_seats(state_root):
        if s["code"] == me:
            return "%s@%s/%s" % (s["role"] or "root", me, (s["provider"] or "claude").capitalize())
    raise SystemExit(_ui("aib.bindme.me_unknown", value=me))


_UI_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵(語系盤查,10-14);每次照 i18n_lib.resolve_locale() 取(長駐程式切語系也跟得上),讀不到印鍵名。"""
    try:
        _cockpit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cockpit")
        if _cockpit not in sys.path:
            sys.path.insert(0, _cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _UI_TR.get(loc)
        if tr is None:
            tr = _UI_TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001 —— 印不出字不能讓程式停
        return key


def main(argv=None):
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_BINDME_PROG", "aib bindme"),
                                 description="一次看清、補齊各席的人格與專案綁定")
    ap.add_argument("--state-root", default=os.path.expanduser("~/.ez2-aib"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="全艙一張表(唯讀)")
    s.add_argument("--json", action="store_true")
    v = sub.add_parser("survey", help="寄問卷給缺綁定的席")
    v.add_argument("--me", required=True, help="你自己的席碼(回信寄回這一席)")
    v.add_argument("--seat", help="只問這幾席,逗號分隔席碼")
    v.add_argument("--all", action="store_true", help="連已經齊的也問")
    v.add_argument("--hours", type=int, default=24)
    v.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("collect", help="收回覆、提出建議")
    c.add_argument("sid")
    c.add_argument("--json", action="store_true")
    a = sub.add_parser("apply", help="照建議綁")
    a.add_argument("sid")
    a.add_argument("--me", required=True)
    a.add_argument("--actor")
    a.add_argument("--only", help="只綁這幾席,逗號分隔席碼")
    a.add_argument("--create-missing", action="store_true", help="建議裡要新建的專案也建")
    a.add_argument("--accept", action="store_true", help="同意建議裡「綁既有人格」的那些(沒給就只問不綁)")
    a.add_argument("--ask", action="store_true", help="逐席在那一艙跳倒數選單;%d 秒沒選 = 照建議綁" % MENU_TIMEOUT_S)
    a.add_argument("--dry-run", action="store_true")
    au = sub.add_parser("auto", help="(交接背景用)自動模式綁自己這一席:找到就綁、沒有就 30 秒彈窗後建立")
    au.add_argument("sid")
    au.add_argument("--me", required=True)
    rp = sub.add_parser("repick", help="「不對,換掉」:彈清單改綁人格 / 專案")
    rp.add_argument("--me", required=True)
    rp.add_argument("--actor")
    r = sub.add_parser("repair", help="從延續檔把人格接回")
    r.add_argument("--me", required=True)
    r.add_argument("--actor")
    r.add_argument("--only")
    r.add_argument("--dry-run", action="store_true")
    o = ap.parse_args(argv)
    root = o.state_root

    if o.cmd == "status":
        rows = status(root)
        print(json.dumps(rows, ensure_ascii=False, indent=2)) if o.json else print_status(rows)
        return 0
    if o.cmd == "survey":
        if not CODE_RE.match(o.me):
            raise SystemExit(_ui("aib.bindme.me_shape"))
        rows = [st for st in status(root) if st["code"] != o.me and st["problem"] != "bot"]
        if o.seat:
            want = set(o.seat.split(","))
            rows = [st for st in rows if st["code"] in want]
        elif not o.all:
            rows = [st for st in rows if st["problem"] != "ok"]
        if not rows:
            print(_ui("aib.bindme.nobody"))
            return 0
        rec = run_survey(root, o.me, rows, o.hours, o.dry_run)
        for t in rec["targets"]:
            print("  %-12s %s" % (t["addr"], t["stage"]))
        if o.dry_run:
            print(_ui("aib.bindme.dry"))
        else:
            print(_ui("aib.bindme.survey_id", id=rec["sid"]))
        return 0
    if o.cmd == "collect":
        rec = collect(root, o.sid)
        print(json.dumps(rec["proposals"], ensure_ascii=False, indent=2)) if o.json else print_proposals(rec)
        return 0
    only = set(o.only.split(",")) if getattr(o, "only", None) else None
    try:
        actor = _actor(root, o.me, getattr(o, "actor", None))
    except SystemExit as exc:
        if o.cmd == "auto":                                  # 背景跑的:認不出席也要寫回調查檔
            rec = _load(survey_path(root, o.sid)) or {}
            if rec:
                rec["auto_result"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "asked": None, "done": [],
                                      "skipped": ["認不出這一席:%s" % exc]}
                Path(survey_path(root, o.sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    if o.cmd == "auto":
        rec = _load(survey_path(root, o.sid))
        if not rec or not rec.get("proposals"):
            raise SystemExit("調查 %s 沒有提案" % o.sid)
        row = next((r for r in rec["proposals"] if r.get("code") == o.me), None)
        if not row:
            raise SystemExit("調查 %s 裡沒有 @%s" % (o.sid, o.me))
        try:
            res = auto_apply(root, row, actor)
        except Exception as exc:  # noqa: BLE001 —— 背景跑的,出錯要寫回調查檔,不能無聲
            res = {"decision": None, "done": [], "skipped": ["自動綁定出錯:%r" % (exc,)]}
        rec["auto_result"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "asked": (res["decision"] or {}).get("asked"),
                              "done": res["done"], "skipped": res["skipped"]}
        Path(survey_path(root, o.sid)).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec["auto_result"], ensure_ascii=False, indent=2))
        return 1 if any(d.get("rc") for d in res["done"]) else 0
    if o.cmd == "repick":
        res = repick(root, o.me, actor)
        print(json.dumps(res, ensure_ascii=False))
        return 1 if any(rc for _k, rc in res) else 0
    if o.cmd == "apply":
        res = apply(root, o.sid, actor, only, o.create_missing, o.dry_run, accept=o.accept, ask=o.ask)
    else:
        res = repair(root, actor, only, o.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    bad = [x for x in res if any(d.get("rc") for d in x.get("done", [])) or x.get("rc")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
