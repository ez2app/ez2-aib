#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""create-and-bind-persona.py — 一鍵建立角色卡(有指定席位才綁定)至 Ez2 AIB 席位

the maintainer 10-01 裁(取代 09-30「沒說就開新席」):沒指定席位 ⇒ 只建卡,不開新席、不綁任何席,
回一句「已創立好 XXX 人格(uid …)」;有 --seat 才綁那一席(--deliver 照舊寄人格包);
--new-seat 只有明講才開新席,不再是預設。

用法:
    aib newrole --name "角色名稱" [選項]            ← 09-29 起的正式入口(同一支程式)
    aib-create-persona --name "角色名稱" [選項]
    python3 create-and-bind-persona.py --name "角色名稱" [選項]

參數:
    --name NAME        角色名稱 (必填，例如 "九品芝麻官-包龍星"、"賭俠-陳刀仔")
    --code CODE        英文代號 (可選，預設由名稱自動生成或隨機縮寫)
    --emoji EMOJI      代表圖示 (可選，預設 🎭)
    --category CAT     角色分類 (可選，預設 "客製/專家")
    --prompt PROMPT    角色人設、性格與說話口吻描述 (可選)
                       一句話也行:三段 ## 職責/## 紅線/## 知識域 一段都沒寫 ⇒ 那句當開頭、三段補預設內文(10-15);
                       寫了其中幾段 ⇒ 缺的段具名擋下,不替人猜
    --prompt-file F    同上,但從檔案讀(人設有引號、反引號、多行時用這個,不必過殼)
    --seat SEAT        目標席位(可選;不給 ⇒ 只建卡、不綁任何席)。三種寫法都收:
                       完整地址 f643/77324、畫面上的席碼 a seat、席位檔的 seat_id
    --vault VAULT      Obsidian Vault 路徑 (預設 $EZ2_VAULT_DIR 或 ~/Ez2/Vault)
    --brain ROUTE      大腦路由 (預設 core.handoff);--brain none = 只綁人格、不綁專案
    --deliver          綁完把人格包用 aib dm 寄給那一席(正在跑的對話不會自己知道被綁了;要配 --seat 或 --new-seat)
    --new-seat         明講才用:在你所在的艙開一個新席,等它啟動完成再綁(10-01 起不是預設;沒指定席就只建卡)
    --engine ENGINE    --new-seat 用哪個引擎(預設 claude)
    --actor ACTOR      帳本署名 role@hex/Engine(要綁席位時必填,只建卡不用;不給就讀 EZ2_OWNER_SIG,兩個都沒有 ⇒ 具名停手不猜)
    --overwrite        既有同名卡也重寫角色.md(不給 ⇒ 既有卡一個位元組都不動,只 scan + bind + 記憶)
                       席位記憶:同一張卡的人設只寫一次;帶 --overwrite 才會把新內容再接一段
    --dry-run          僅模擬執行，不變更實體檔案
"""

import argparse
import datetime
import glob
import json
import os
import re
import secrets
import string
import subprocess
import sys
from pathlib import Path

LIBRARY_ROUTE_KEY = "product.expert.agent_library"

AIB_HOME = Path(os.environ.get("EZ2_AIB_HOME", os.path.expanduser("~/.ez2-aib")))
# 10-12 新用戶全面檢查:vault 預設跟全套件同一條規則(route_register.default_vault):
#   EZ2_VAULT_DIR > EZ2_VAULT_ROOT > ~/Desktop/Obsidian/Ez2(有才算)> ~/.ez2-aib/vault。以前的 ~/Ez2/Vault 沒有人有。
def _aib_default_vault():
    return Path(os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")) / "vault"


def _default_vault():   # 與 route_register.default_vault 同一條規則(這裡不在載入時 import 它)
    for var in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
        if os.environ.get(var):
            return Path(os.path.expanduser(os.environ[var]))
    legacy = Path(os.path.expanduser("~/Desktop/Obsidian/Ez2"))
    return legacy if legacy.is_dir() else _aib_default_vault()


DEFAULT_VAULT = _default_vault()

REPO_ROOT = Path(os.environ.get("EZ2_AIB_REPO", str(Path(__file__).resolve().parents[2])))

# ★ review r6 m2:只建卡那條路的回報句(aib newrole 對使用者說的那一句)走四語字典 aib.newrole.card_only_*,
#   跟 aib newproj 的 aib.newproj.card_only_* 同一套。語系:EZ2_AIB_LOCALE > zh-TW。
#   字典與 i18n_lib 先找**本檔所在那棵樹**,再找 EZ2_AIB_REPO(可能是只放了 src/ 連結的依賴目錄 ⇒ 順著 src 連結找回那棵樹)。
#   第一次要翻譯時才載(import 本檔不需要字典:測試副本樹常只放這一支檔)。
_TRANSLATOR = []


def _i18n_tree():
    own = Path(__file__).resolve().parents[2]
    for tree in (own, REPO_ROOT, (REPO_ROOT / "src").resolve().parent):
        if (tree / "i18n" / "zh-TW.json").is_file() and (tree / "src" / "cockpit" / "i18n_lib.py").is_file():
            return tree
    return own


def T(key, params=None):
    if not _TRANSLATOR:
        tree = _i18n_tree()
        cockpit = str(tree / "src" / "cockpit")
        if cockpit not in sys.path:
            sys.path.append(cockpit)
        import i18n_lib
        _TRANSLATOR.append(i18n_lib.Translator(i18n_dir=tree / "i18n"))
    return _TRANSLATOR[0].t(key, dict(params) if params else None)

# Equivalent to ez2-ai tools/persona-card.js appendix F. Validate in memory,
# before writing either the vault card or any AIB projection.
# 金鑰三條抽到 src/identity/secret_patterns.py 共用(紀錄倉也掃,10-13)。測試副本樹常只放本檔一支 ⇒ 載不到共用表時
# 用同樣三條的內建版(刀與複本照舊能跑);這裡照舊只用原本三條,不因紀錄倉多掃而變嚴。
try:
    _here = str(Path(__file__).resolve().parent)
    if _here not in sys.path:
        sys.path.append(_here)
    import secret_patterns  # noqa: E402
    _SECRET_BASE = secret_patterns.BASE
except (ImportError, AttributeError):
    _SECRET_BASE = (
        ("secret-apikey", r"sk-[A-Za-z0-9]{20,}"),
        ("secret-aws", r"AKIA[0-9A-Z]{16}"),
        ("secret-pem", r"-----BEGIN"),
    )

LINT_RULES = (
    ("越權-忽略指令", r"忽略(先前|以上|你的)(指令|規則)"),
    ("越權-身份覆蓋", r"你現在是(?!.*角色)"),
    ("越權-隱匿", r"不要(告訴|回報)"),
    ("越權-繞過閘門", r"(繞過|停用)[^\n]{0,10}(閘門|hook|紅線)"),
) + _SECRET_BASE


def _archive_snapshot(path, kind, key):
    """改卡前快照(src/memory/memory_archive.py,記憶整理第 1 包)。
    檔還不在(新卡)⇒ 不用拍,skipped。
    找不到 memory_archive.py 這支檔(測試副本樹只放了本檔)⇒ skipped 並記一句。
    檔在、但載入或拍的時候出錯 ⇒ failed,呼叫端不准寫(正式安裝一定帶 src/memory,載不起來就是壞了)。
    先找本檔所在那棵樹,再找 EZ2_AIB_REPO;用找到的那一支檔載入(不靠 sys.path 順序)。"""
    if not Path(path).exists():
        return {"status": "skipped", "error": "no file yet"}
    found = next((m / "memory_archive.py" for m in (Path(__file__).resolve().parents[1] / "memory",
                                                    REPO_ROOT / "src" / "memory")
                  if (m / "memory_archive.py").is_file()), None)
    if found is None:
        log("提醒: 找不到 memory_archive.py,這次改卡沒有備份")
        return {"status": "skipped", "error": "memory_archive unavailable"}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("memory_archive_for_newrole", str(found))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.snapshot(path, kind, key)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": "memory_archive: %s" % exc}


# 10-14 新用戶走查:三個必填標題接受四語寫法(## Duties / ## 役割 / ## 역할 …),寫卡前換成中文正本標題。
#   只換標題、不動內文;下游 7 處(persona_pack 打包時紅線排第一、seat_screen、bindme、persona_check …)
#   都認中文正本標題,所以在入口統一,不讓英文卡的紅線在後面被認不出來。
#   各語的寫法在 aib/i18n 的 aib.persona.heading_aliases.*(| 分隔),四語全部都收,不看當下語系。
HEADING_CANON = (("duties", "職責"), ("red_lines", "紅線"), ("knowledge", "知識域"))


def _heading_aliases():
    tree = _i18n_tree()
    aliases = {}
    for path in sorted((tree / "i18n").glob("*.json")):
        try:
            table = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key, canon in HEADING_CANON:
            for alias in str(table.get("aib.persona.heading_aliases." + key) or "").split("|"):
                if alias.strip():
                    aliases[alias.strip().casefold()] = canon
    return aliases


def normalize_headings(text):
    """把三個必填標題的各語寫法換成中文正本(只認 `## 標題` 整行,可帶結尾冒號);其他行一字不動。"""
    aliases = _heading_aliases()
    if not aliases:
        return text
    out = []
    for line in text.split("\n"):
        m = re.match(r"^(##[ \t]*)(.+?)[ \t]*[:：]?[ \t]*$", line)
        canon = aliases.get(m.group(2).strip().casefold()) if m else None
        out.append("## " + canon if canon else line)
    return "\n".join(out)


def lint_card(text):
    failures = []
    if len(text.encode("utf-8")) > 20 * 1024:
        failures.append("人格檔>20KB(知識請入知識庫)")
    for name, pattern in LINT_RULES:
        if re.search(pattern, text):
            failures.append(name)
    for heading in ("職責", "紅線", "知識域"):
        match = re.search(r"^##[ \t]*" + heading + r"[ \t]*\n([\s\S]*?)(?=^##[ \t]|\Z)", text, re.M)
        if not match or not match.group(1).strip():
            failures.append("缺 ## %s 段" % heading)
    return failures


# 10-15 C10/E5:三個必填段的標題(卡上一律寫中文正本,下游 persona_pack 等都認這三個字)與各語說明鍵
REQUIRED_HEADINGS = (("職責", "duties"), ("紅線", "red_lines"), ("知識域", "knowledge"))


def has_any_required_heading(text):
    return any(re.search(r"^##[ \t]*" + h + r"[ \t]*$", text or "", re.M) for h, _k in REQUIRED_HEADINGS)


def wrap_short_prompt(prompt):
    """--prompt 一個 ## 必填段都沒有(常見:一句話人設)⇒ 那句當開頭定位,三段補上預設內文(內文照使用者語系)。
    已經寫了任一段 ⇒ 原樣,缺的段交給 lint 具名擋(不替人猜他漏寫的那段)。"""
    if has_any_required_heading(prompt):
        return prompt, False
    parts = [prompt.strip(), ""]
    for heading, key in REQUIRED_HEADINGS:
        parts += ["## " + heading, T("aib.newrole.default_" + key), ""]
    return "\n".join(parts), True


def lint_messages(failures):
    """lint_card 的結果 → 給人看的句子(四語)。缺段那一條附上三段標題的正確寫法。"""
    out, missing = [], False
    for f in failures:
        m = re.match(r"缺 ## (\S+) 段$", f)
        if m:
            key = dict(REQUIRED_HEADINGS).get(m.group(1), "")
            out.append(T("aib.newrole.lint_missing", {"heading": "## " + m.group(1),
                                                       "label": T("aib.newrole.heading_" + key)}))
            missing = True
        elif f.startswith("人格檔>20KB"):
            out.append(T("aib.newrole.lint_too_big"))
        else:
            out.append(T("aib.newrole.lint_rule", {"rule": f}))
    if missing:
        out.append(T("aib.newrole.lint_headings_hint", {"headings": " / ".join(
            "## %s (%s)" % (h, T("aib.newrole.heading_" + k)) for h, k in REQUIRED_HEADINGS)}))
    return out


def commit_card(role_lib_dir, card_file, name):
    rc, top, _ = run_cmd(["git", "-C", str(role_lib_dir), "rev-parse", "--show-toplevel"])
    if rc:
        log("角色庫不是 git 倉,跳過 vault git commit")
        return
    repo = Path(top.strip()).resolve()
    relative = str(card_file.resolve().relative_to(repo))
    rc, _, err = run_cmd(["git", "add", "--", relative], cwd=repo)
    if rc:
        raise RuntimeError("vault git add 失敗:" + err.strip())
    rc, _, err = run_cmd(["git", "diff", "--cached", "--quiet", "--", relative], cwd=repo)
    if rc == 0:
        log("角色卡內容沒有變動,跳過 vault git commit")
        return
    if rc != 1:
        raise RuntimeError("vault git diff 失敗:" + err.strip())
    rc, _, err = run_cmd(["git", "commit", "--only", "-m", "feat(persona): create " + name,
                          "--", relative], cwd=repo)
    if rc:
        raise RuntimeError("角色卡已寫入,但 vault git commit 失敗:" + err.strip())
    log("   -> vault git commit 完成(只含本張角色卡)")


def log(msg, prefix="[*]"):
    print(f"{prefix} {msg}", file=sys.stderr)


def get_existing_cards(cards_file):
    if not cards_file.is_file():
        return {}
    try:
        with open(cards_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("cards", {})
    except Exception as e:
        log(f"讀取 cards.json 警告: {e}")
        return {}


def allocate_uid(cards_dict):
    taken = set(cards_dict.keys())
    alphabet = string.digits + string.ascii_lowercase
    for _ in range(128):
        candidate = "".join(secrets.choice(alphabet) for _ in range(6))
        if candidate not in taken:
            return candidate
    raise RuntimeError("無法分配未衝突的 6 碼 UID")


def seat_is_alive(seat):
    """Only a live PID or the recorded socket/window proves liveness."""
    try:
        pid = int(seat.get("pid") or 0)
        if pid > 0:
            os.kill(pid, 0)
            return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        pass
    socket = seat.get("socket")
    window = seat.get("window_id")
    if not isinstance(socket, str) or not socket or not isinstance(window, str):
        return False
    if not re.fullmatch(r"@[0-9]+", window):
        return False
    try:
        result = subprocess.run(
            ["tmux", "-S" if socket.startswith("/") else "-L", socket,
             "list-windows", "-a", "-F", "#{window_id}"],
            capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL)
        return result.returncode == 0 and window in result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False


def _cockpit_modules():
    """dm_address / seat_address:席碼與艙位碼的算法只住在 src/cockpit 那兩支,這裡不另寫一份。"""
    cockpit = str(REPO_ROOT / "src" / "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    import dm_address
    import seat_address
    return dm_address, seat_address


def iter_seat_records():
    """狀態根底下每一個席位檔 ⇒ (路徑, 內容, 艙 id);主艙的 seats/ 艙 id 是空字串。"""
    folders = [(AIB_HOME / "seats", "")]
    inst_root = AIB_HOME / "instances"
    if inst_root.is_dir():
        folders += [(inst / "seats", inst.name) for inst in sorted(inst_root.iterdir())]
    for folder, instance in folders:
        for sfile in sorted(folder.glob("w*.json")):
            try:
                with open(sfile, "r", encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("seat_id"):
                yield sfile, rec, instance


def _seat_cabin(rec, instance):
    """這一席的艙位碼(艙 id 最後一個 - 之後四碼);量不到回空字串,不猜。"""
    dm_address, _ = _cockpit_modules()
    cabin_id = instance or (rec.get("cabin_id") if isinstance(rec.get("cabin_id"), str) else "")
    return dm_address.cabin_code(cabin_id) if cabin_id else ""


def resolve_seat_arg(seat_arg):
    """--seat 的三種寫法 ⇒ (seat_id, 席位紀錄或 None, 艙 id, 錯誤訊息或 None)。

    畫面與 dm 抬頭上看得到的是席碼(session_uuid 尾五碼),席位檔的 seat_id 畫面上看不到;
    09-29 之前這裡只收 seat_id,人要自己去翻席位檔。席碼只認**活著**的席(死艙的檔不算)。
    同一個字同時是甲席的 seat_id 與乙席的席碼 ⇒ 兩席都列出來、不挑。"""
    raw = (seat_arg or "").strip()
    dm_address, seat_address = _cockpit_modules()
    records = list(iter_seat_records())
    explicit_code = ("/" in raw) or raw.startswith("@") or raw.startswith("IP:")
    parsed = dm_address.parse(raw)
    id_hits = [] if explicit_code else [r for r in records if r[1].get("seat_id") == raw]
    code_hits = []
    if parsed:
        for sfile, rec, instance in records:
            if seat_address.code_of(rec) != parsed["seat"]:
                continue
            if parsed["cabin"] and _seat_cabin(rec, instance) != parsed["cabin"]:
                continue
            if seat_is_alive(rec):
                code_hits.append((sfile, rec, instance))
    other = [h for h in code_hits if h[0] not in [i[0] for i in id_hits]]

    def named(hit):
        return "%s(seat_id=%s 席碼=@%s)" % (hit[0], hit[1].get("seat_id"), seat_address.code_of(hit[1]) or "量不到")

    if id_hits and other:
        return None, None, "", "--seat %s 同時是一席的 seat_id 與另一席的席碼,不挑:%s。請寫完整地址 <艙位碼>/<席碼>" % (
            raw, ";".join(named(h) for h in id_hits + other))
    if id_hits:
        hit = id_hits[0] if len(id_hits) == 1 else None      # 同 seat_id 多檔由 bind_one 具名拒
        return raw, (hit[1] if hit else None), (hit[2] if hit else ""), None
    if len(code_hits) == 1:
        sfile, rec, instance = code_hits[0]
        return rec["seat_id"], rec, instance, None
    if len(code_hits) > 1:
        return None, None, "", "--seat %s 有 %d 個活席符合,不挑:%s。請寫完整地址 <艙位碼>/<席碼>" % (
            raw, len(code_hits), ";".join(named(h) for h in code_hits))
    if explicit_code:
        return None, None, "", "--seat %s:找不到活著的這一席(席碼 = 畫面上 @ 後面那五碼)" % raw
    return raw, None, "", None            # 照舊交給 bind_one 去找,找不到由它具名拒


def memory_key_of(seat_id, seat_rec=None):
    """席位記憶資料夾的名字:席碼;席位檔找不到或沒有 session_uuid ⇒ seat_id(不猜)。"""
    _, seat_address = _cockpit_modules()
    rec = seat_rec
    if rec is None:
        hits = [r for r in iter_seat_records() if r[1].get("seat_id") == seat_id]
        rec = hits[0][1] if len(hits) == 1 else None
    code = seat_address.code_of(rec) if isinstance(rec, dict) else None
    return code if isinstance(code, str) and re.fullmatch(r"[0-9a-z]{4,8}", code) else seat_id


class NewSeatError(RuntimeError):
    """開新席失敗。opened=True:開席指令成功、窗已開(等不到席碼或冒出多席)—— 那一席(們)在跑,只是沒綁。"""

    def __init__(self, msg, opened=False):
        super().__init__(msg)
        self.opened = bool(opened)


def _tmux_socket_name():
    """$TMUX 那個 socket 的檔名(艙裡是 aihub-<艙 id>);不在 tmux 裡回空字串。
    ★ 只讀 TMUX 這一個名字、單獨一支:它不是艙導向變數(不在 cabin_resolve.sh 的洗名單裡,也不該在 ——
      洗掉它會讓 tmux 以為自己不在 tmux 裡),所以不和艙導向變數擠在同一個函式裡讀
      (test-cabin-read-set / test-cabin-single-source-names:同一個函式讀了艙導向變數,它讀的每個名字都要在單源裡)。"""
    return os.path.basename((os.environ.get("TMUX") or "").split(",", 1)[0])


def current_cabin():
    """你所在的艙 id:EZ2_AIB_INSTANCE,或 TMUX 那個 socket 的名字 aihub-<艙 id>。量不到回空字串。"""
    inst = (os.environ.get("EZ2_AIB_INSTANCE") or "").strip()
    if inst:
        return inst
    sock = _tmux_socket_name()
    return sock[len("aihub-"):] if sock.startswith("aihub-") and len(sock) > len("aihub-") else ""


def _hub_name(cabin):
    """那一艙的 tmux socket 名,向單源 `cabin_resolve.sh --hub` 要,不在這裡自己拼
    (test-cabin-resolution R8)。解析器不收這個名字 ⇒ NewSeatError,不猜一個 socket。"""
    try:
        p = subprocess.run(["bash", str(REPO_ROOT / "src" / "cockpit" / "cabin_resolve.sh"), "--hub", cabin],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NewSeatError("量不到艙 %s 的 socket 名:%s" % (cabin, exc))
    hub = p.stdout.strip()
    if p.returncode != 0 or not hub:
        raise NewSeatError("量不到艙 %s 的 socket 名:%s" % (cabin, (p.stderr or "").strip()[-200:]))
    return hub


def new_seat_role_or_die(role):
    """--new-seat 開的席用哪個角色:--role 給的(要是已定義角色),沒給 = dev。
    the maintainer 10-15「預設新開新從原有的角色上選,不另開新角色」⇒ 不再拿人格代號 / 專案標題當角色名。
    回 (role, 錯誤訊息);錯誤訊息非空 ⇒ 呼叫端擋下,什麼都不寫。"""
    role = (role or "dev").strip()
    sys.path.insert(0, str(REPO_ROOT / "src" / "cockpit"))
    try:
        import roles as _roles
    except Exception as exc:  # noqa: BLE001
        return role, "讀不到角色清單(%s)" % exc
    if not _roles.is_defined(role):
        return role, "角色「%s」沒有定義;可用:%s" % (role, ", ".join(_roles.defined_roles()))
    return role, ""


def seat_role_of(text, fallback):
    """新席的角色名(aib 只收小寫英數與連字號):取英數字;一個都沒有(例如中文名稱)就用 fallback。"""
    role = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:24]
    return role or fallback


def new_seat_timeout():
    """等新席啟動的秒數;AIB_NEWSEAT_TIMEOUT_S 可改(測試用短的)。"""
    try:
        return max(1, int(os.environ.get("AIB_NEWSEAT_TIMEOUT_S") or 300))
    except ValueError:
        return 300


def _seat_snapshot(seats_dir):
    out = {}
    for p in sorted(Path(seats_dir).glob("w*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out[str(p)] = rec
    return out


def new_seat_precheck(engine="claude", cabin=""):
    """開新席之前、不需要真的開就能做的檢查(review r16 m1:試跑與真跑都在寫卡前跑這一組)。
    ⇒ (艙 id, 引擎, 席位資料夾, tmux socket 名);不合 ⇒ NewSeatError(opened=False,什麼都沒做)。"""
    cabin = (cabin or current_cabin()).strip()
    if not cabin or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z-]{0,63}", cabin):
        raise NewSeatError("量不到你所在的艙;在艙裡的席位上執行,或設 EZ2_AIB_INSTANCE")
    engine = (engine or "claude").strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{1,32}", engine):
        raise NewSeatError("引擎名稱不對:%r" % engine)
    seats_dir = AIB_HOME / "instances" / cabin / "seats"
    if not seats_dir.is_dir():
        raise NewSeatError("這一艙沒有席位資料夾:%s" % seats_dir)
    return cabin, engine, seats_dir, _hub_name(cabin)


def open_new_seat(role, engine="claude", cabin="", timeout=300, interval=3.0, sleep=None, clock=None):
    """在 `cabin` 開一個新席(同艙再開一席,不搶使用者的畫面),等它的席位檔帶上 session_uuid。
    ⇒ (seat_id, 席位紀錄, 艙 id)。開不起來或等不到 ⇒ NewSeatError(什麼都還沒寫)。

    ★ 用 aib 本來就有的 `--again`;`AIB_NO_ATTACH=1 AIB_NO_CLIENT_SWITCH=1` 讓它不附著、不切換任何人的畫面。
    ★ 認「新席」的方法:開之前記下這一艙每個席位檔的 session_uuid,開之後出現**新的 session_uuid**
      (新檔、或同一個窗重開換了 uuid)。恰好一個才算;兩個以上不猜。"""
    import time as _time
    sleep = sleep or _time.sleep
    clock = clock or _time.time
    cabin, engine, seats_dir, hub = new_seat_precheck(engine, cabin)
    role = re.sub(r"[^a-z0-9-]", "-", (role or "").lower()).strip("-")[:24] or "persona"
    before = {rec.get("session_uuid") for rec in _seat_snapshot(seats_dir).values() if rec.get("session_uuid")}
    launcher = os.environ.get("AIB_NEWSEAT_LAUNCHER") or str(REPO_ROOT / "src" / "cockpit" / "aib")
    env = dict(os.environ, AIB_NO_ATTACH="1", AIB_NO_CLIENT_SWITCH="1",
               EZ2_AIHUB_SOCKET=hub, EZ2_AIB_INSTANCE=cabin)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    log("開新席:引擎 %s、角色 %s、艙 %s" % (engine, role, cabin))
    res = subprocess.run(["bash", launcher, engine, "dgrs", role, "--again"], env=env,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise NewSeatError("開席指令失敗(rc=%s):%s" % (res.returncode, (res.stderr or res.stdout).strip()[-300:]))
    deadline = clock() + max(1, int(timeout))
    while True:
        fresh = [(p, rec) for p, rec in _seat_snapshot(seats_dir).items()
                 if rec.get("session_uuid") and rec.get("session_uuid") not in before and rec.get("seat_id")]
        if len(fresh) == 1:
            path, rec = fresh[0]
            log("新席已啟動:%s(席碼 %s)" % (rec.get("window_id") or path, str(rec["session_uuid"])[-5:]))
            return rec["seat_id"], rec, cabin
        if len(fresh) > 1:
            raise NewSeatError("等新席時同一艙出現 %d 個新席,不猜是哪一個:%s" % (
                len(fresh), "、".join(str(r.get("session_uuid"))[-5:] for _, r in fresh)), opened=True)
        if clock() >= deadline:
            raise NewSeatError("新席的窗已開,但 %d 秒內沒等到它的席碼(AI 可能還在啟動)。"
                               "看右欄那一席出現後,改用 --seat <艙位碼>/<席碼> 再跑一次" % int(timeout), opened=True)
        sleep(interval)


def card_display_name(name):
    """卡上 name: 欄的值:去掉會破壞單行 frontmatter 的字元(引號、換行、控制字元)。"""
    return re.sub(r'[\x00-\x1f\x7f"]', "", str(name or "")).strip()


def write_label_title(vault_dir, folder_id, title):
    """uid 版面(<vault>/.ez2/display/labels.json 已存在)⇒ 補 folders[folder_id].title;
    舊版面沒有這份檔 ⇒ 不建(轉換工具建),回 False。寫法:讀 → 改 → 暫存檔 → 換名。"""
    path = Path(vault_dir) / ".ez2" / "display" / "labels.json"
    if not path.is_file():
        return False
    try:
        labels = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log("警告: labels.json 讀不懂,不改它;卡上的 name: 仍會當顯示名")
        return False
    folders = labels.setdefault("folders", {})
    rec = folders.setdefault(folder_id, {})
    if rec.get("title") == title:
        return True
    rec["title"] = title
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return True


def folder_for_new_card(name, code):
    """新卡的資料夾名。名字開頭若是「英數字-」,卡片索引會把那一段當代號去掉
    (`PM-專案經理` 顯示成「專案經理」),於是「SEO-GEO大師」會顯示成「GEO大師」。
    給名字的人要的是整個名字 ⇒ 資料夾前面補一段代號,讓被去掉的是代號不是名字。"""
    memory = str(REPO_ROOT / "src" / "memory")
    if memory not in sys.path:
        sys.path.insert(0, memory)
    try:
        from card_registry import display_name_of
    except Exception as exc:                      # 索引規則讀不到 ⇒ 不改資料夾名,但要說
        log("警告: 讀不到卡片索引的顯示名規則(%s),資料夾照名字建;畫面上的名字可能少一段" % exc)
        return name
    if display_name_of(name)[0] == name:
        return name
    prefix = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()[:12] or "R"
    folder = "%s-%s" % (prefix, name)
    log("名字開頭像代號(%s),資料夾改用 %s,畫面才會顯示完整的「%s」" % (name.split("-", 1)[0], folder, name))
    return folder


def deliver_persona(seat_id, seat_rec, instance, name, vault_dir, dry_run=False):
    """把這一席的人格包用 aib dm 寄過去 ⇒ dict。寄不出去不改變「已綁」這件事,只如實回報。"""
    dm_address, seat_address = _cockpit_modules()
    if seat_rec is None:
        hits = [r for r in iter_seat_records() if r[1].get("seat_id") == seat_id]
        if len(hits) != 1:
            return {"ok": False, "reason": "席位檔找不到或不唯一(%d 個)" % len(hits)}
        _, seat_rec, instance = hits[0]
    code = seat_address.code_of(seat_rec)
    cabin = _seat_cabin(seat_rec, instance)
    if not code or not cabin:
        return {"ok": False, "reason": "席碼或艙位碼量不到(席碼=%s 艙位碼=%s)" % (code or "?", cabin or "?")}
    address = dm_address.format_address(code, cabin)
    if dry_run:
        log("   -> [DRY-RUN] 會把人格包寄給 %s" % address)
        return {"ok": None, "dry_run": True, "address": address}
    pack_cmd = [sys.executable, str(REPO_ROOT / "src" / "identity" / "persona_pack.py"), "render",
                "--session-uuid", str(seat_rec.get("session_uuid") or ""), "--seat-id", seat_id,
                "--aib-home", str(AIB_HOME), "--vault", str(vault_dir)]
    rc, out, err = run_cmd(pack_cmd, cwd=REPO_ROOT)
    try:
        pack = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "address": address, "reason": "人格包組不出來(rc=%s):%s" % (rc, err.strip()[-200:])}
    if pack.get("status") != "ok" or not pack.get("text"):
        return {"ok": False, "address": address, "reason": "人格包狀態 %s" % pack.get("status")}
    body = "你這一席已綁定人格「%s」,請從現在起照下面這份人格包工作。\n\n%s\n" % (name, pack["text"])
    rc, out, err = dm_send_body(address, body, seat_rec)
    stage = ""
    for line in reversed(out.splitlines()):
        if line.startswith("RESULT "):
            stage = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv).get("stage", "")
            break
    result = {"ok": rc == 0, "rc": rc, "address": address, "stage": stage,
              "expect_reply": "人格已載入 %s" % (pack.get("persona_sha8") or "")}
    if rc in (3, 4):
        # ★ review r14 M1:aib dm 回 3 = 已送出但沒能確認、4 = 已進對方框但沒送出 —— 兩種都「絕不重送」(dm-ctl.sh 契約)。
        #   以前一律當沒寄到 ⇒ rc=6「修好後重跑」⇒ 對方框裡疊兩份。改成 ok=None + no_retry,整支照常 rc=0。
        result["ok"] = None
        result["no_retry"] = True
        result["reason"] = "aib dm rc=%d:已送出或已進對方框但沒能確認,不要重跑;等對方回「%s」" % (
            rc, result["expect_reply"])
    elif rc != 0:
        result["reason"] = dm_fail_reason(rc, out, err)
    elif stage != "delivered":
        # ★ review r7 M1:rc=0 但還沒真的送進去(codexbot 席 queue 模式 stage=queued:排在桌面佇列等持有者;
        #   sort-queued:排進待審區等排空器)⇒ 不先寫「人格包送過了」記號。橋那時照常補送,人格包多一份可以接受;
        #   先寫了而這一則最後沒送到,橋就永遠不補。
        if seat_rec.get("provider") in ("grokbot", "claudebot", "codexbot", "musebot"):
            result["bridge_marks_skipped"] = "stage=%s" % (stage or "?")
    else:
        # ★ review r6 m5:橋接席(grokbot / claudebot / codexbot)收到這份之後,把那一席的「人格包送過了」記號改成
        #   persona:<指紋>,橋自己的補送(upgrade_after_genes / codexbot 第一則)才不會再送第二份。
        #   記號寫不進去不改變「已寄出」,只如實回報在 bridge_marks_error。
        try:
            import bridge_persona
            marks = bridge_persona.note_delivered_by_dm(seat_rec, str(pack.get("persona_sha8") or ""), home=AIB_HOME)
        except Exception as exc:  # noqa: BLE001
            result["bridge_marks_error"] = "%s: %s" % (type(exc).__name__, exc)
        else:
            if marks:
                result["bridge_marks"] = marks
    return result


def sanitize_code(name, code=None):
    if code:
        clean = re.sub(r"[^a-zA-Z0-9_-]", "", code).lower()
        if clean:
            return clean[:12]
    # 從 name 抽取英數字或隨機
    alnum = re.sub(r"[^a-zA-Z0-9]", "", name).lower()
    if alnum:
        return alnum[:8]
    return "p" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))


def run_cmd(cmd, cwd=None, extra_env=None):
    # stdin 一律關掉:子指令(aib dm 的探測與寄送)沒拿到訊息時會去讀 stdin,開著就會一直等
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **(extra_env or {})))
    return res.returncode, res.stdout, res.stderr


def dm_inline_text(text, seat_rec):
    """走 dm 的家(seat_onboard.inline_onboard_provider)要整行貼的那一份字:攤成一行 + 換掉出站檢查會擋的字。
    回 (字, 是否走 dm 的家);不是 / 判不出 ⇒ (原字, False)。
    review r13:new_project 的「請回覆一行:已接手 …」也用這一支換一次 —— 專案名裡的全形英數/圈號在貼出去的那一份
    被換掉了,expect_reply 要跟對方真的看到的字一樣(lint_safe 逐字替換,換一段子字串與換整份結果一致)。"""
    try:
        sys.path.insert(0, str(REPO_ROOT / "src" / "cockpit"))
        import seat_onboard
        if seat_onboard.inline_onboard_provider((seat_rec or {}).get("provider")):
            return seat_onboard.lint_safe(seat_onboard.INLINE_SEP.join(
                p for p in (ln.strip() for ln in str(text).splitlines()) if p)), True
    except Exception:  # noqa: BLE001 —— 判不出家別:照舊(落檔)
        pass
    return text, False


def dm_fail_reason(rc, out, err):
    """aib dm 失敗的原因 ⇒ 一行字。

    ★ 2026-10-11 the maintainer:寄失敗要印出原因。以前只取 stderr 最後一行,而 aib dm 被擋(rc=2,例如對方輸入框有
      沒送出的字)時原因只寫在 stdout 那行 JSON 的 detail,stderr 是空的 ⇒ 回報只剩「?」,人以為沒寄到又重寄,
      對方框裡疊兩份。順序:stderr 最後一行 → stdout JSON 的 detail → stdout 最後一行(RESULT 行除外)
      → 都沒有就明講沒印原因。"""
    lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    reason = lines[-1] if lines else ""
    if not reason:
        for ln in reversed((out or "").splitlines()):
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    detail = json.loads(ln).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                if detail:
                    reason = str(detail)
                    break
    if not reason:
        rest = [ln.strip() for ln in (out or "").splitlines() if ln.strip() and not ln.startswith("RESULT ")]
        reason = rest[-1] if rest else "aib dm 沒印任何原因"
    reason = "aib dm rc=%d:%s" % (rc, reason[-200:])
    if rc == 2:
        reason += "(這一則沒送進對方框,可以安全重送)"
    return reason


def dm_send_body(address, body, seat_rec):
    """用 aib dm 把一份長內容(人格包 / 專案交接)寄給那一席。回 (rc, stdout, stderr)。

    ★ review r12 M2:長內容過 600 位元組會被 dm-ctl 換成「長訊已落檔,請讀:…」;走 dm 的家(copilot/gemini 等,
      seat_onboard.inline_onboard_provider)讀不到落檔 ⇒ 人格/交接沒到。對這些家:攤成一行、換掉 dm 出站檢查
      會擋的字(seat_onboard.lint_safe)、AIB_DM_RELAY_BYTES=0 整行貼。能自己讀檔的家照舊落檔。"""
    import tempfile
    extra = None
    flat, inline = dm_inline_text(body, seat_rec)
    if inline:
        body = flat + "\n"
        extra = {"AIB_DM_RELAY_BYTES": "0"}
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt",
                                         prefix="aib-dm-body-", delete=False)
    try:
        with handle:
            handle.write(body)
        return run_cmd(["bash", str(REPO_ROOT / "src" / "cockpit" / "aib"), "dm", address,
                        "--file", handle.name, "--kind", "rule"], cwd=REPO_ROOT, extra_env=extra)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


class _HeadingsKeptFormatter(argparse.HelpFormatter):
    """縮排的行(epilog 裡那三個標題)原樣保留;其他文字照常依寬度換行。"""

    def _fill_text(self, text, width, indent):
        out = []
        for line in text.splitlines():
            if line.startswith("  "):
                out.append(indent + line)
            else:
                out.append(super()._fill_text(line, width, indent))
        return "\n".join(out)


def main():
    # 三個必填標題放在 epilog、各佔一行且原樣輸出(_HeadingsKeptFormatter;其餘文字照常換行):argparse 只在空白處斷行,CJK 說明裡唯一的空白就是
    #   「## 職責」中間那格 ⇒ 放在選項說明裡會被斷成兩行,照抄就過不了 lint(10-14 審查抓到)。
    parser = argparse.ArgumentParser(prog=os.environ.get("AIB_NEWROLE_PROG") or None,
                                     description=T("aib.newrole.help_desc"),
                                     epilog=T("aib.newrole.help_epilog"),
                                     formatter_class=_HeadingsKeptFormatter)
    parser.add_argument("--name", required=True, help=T("aib.newrole.help_name"))
    parser.add_argument("--code", help=T("aib.newrole.help_code"))
    parser.add_argument("--emoji", default="", help=T("aib.newrole.help_emoji"))
    parser.add_argument("--category", default="客製/專家", help=T("aib.newrole.help_category"))
    parser.add_argument("--prompt", help=T("aib.newrole.help_prompt"))
    parser.add_argument("--prompt-file", help=T("aib.newrole.help_prompt_file"))
    parser.add_argument("--seat", help=T("aib.newrole.help_seat"))
    parser.add_argument("--vault", default=str(DEFAULT_VAULT), help=T("aib.newrole.help_vault"))
    parser.add_argument("--brain", default=None,
                        help=T("aib.newrole.help_brain"))
    parser.add_argument("--deliver", action="store_true", help=T("aib.newrole.help_deliver"))
    parser.add_argument("--new-seat", action="store_true", help=T("aib.newrole.help_new_seat"))
    parser.add_argument("--engine", default="claude", help=T("aib.newrole.help_engine"))
    parser.add_argument("--role", default="", help=T("aib.newrole.help_role"))
    parser.add_argument("--actor", help=T("aib.newrole.help_actor"))
    parser.add_argument("--overwrite", action="store_true", help=T("aib.newrole.help_overwrite"))
    parser.add_argument("--dry-run", action="store_true", help=T("aib.newrole.help_dry_run"))

    args = parser.parse_args()

    if args.prompt_file:
        if args.prompt:
            parser.error("--prompt 與 --prompt-file 二選一")
        try:
            with open(args.prompt_file, "r", encoding="utf-8") as f:
                args.prompt = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            log("讀不到 --prompt-file:%s。未建卡、未綁定" % exc, prefix="[x]")
            return 2
        if not args.prompt.strip():
            log("--prompt-file 是空的:%s。未建卡、未綁定" % args.prompt_file, prefix="[x]")
            return 2
    if args.prompt:
        args.prompt = normalize_headings(args.prompt)

    seat_id, seat_rec, seat_instance = None, None, ""
    # the maintainer 10-01:有明講席位(--seat)或明講開新席(--new-seat)才綁;都沒有 ⇒ 只建卡,不開席、不綁、不猜當前席
    bind_target = bool(args.new_seat or (args.seat and args.seat.strip()))
    if args.new_seat:
        if args.seat:
            parser.error("--seat 與 --new-seat 二選一")
        # ★ review r15 M1(人格):新席**最後才開**(緊接在綁定之前)。以前一進來就開 ⇒ 後面任何一道檢查擋下
        #   (新卡沒人設、名字格式、契約、內容 lint、沒署名)都留下一個在跑的新席,訊息卻說「什麼都沒寫」;
        #   試跑也在檢查之前就回綠燈。現在試跑與真跑走同一組檢查,開席失敗時卡已建、沒綁(照實說)。
        if not (args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip():
            log("錯誤: --new-seat 要帳本署名 --actor role@hex/Engine(或設 EZ2_OWNER_SIG);不從席位猜。"
                "什麼都沒寫:沒開席、卡沒建", prefix="[x]")
            return 2
        # review r16(人格):署名格式也在開席前驗(以前格式不合要等 bind_one 才擋 ⇒ 新席已開、卡已建)
        sys.path.insert(0, str(REPO_ROOT / "src" / "identity"))
        import bind_one as _BO
        try:
            _BO.actor_of((args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip())
        except _BO.BindError as exc:
            log("錯誤: %s。什麼都沒寫:沒開席、卡沒建" % exc, prefix="[x]")
            return 2
        new_seat_role, role_err = new_seat_role_or_die(args.role)
        if role_err:
            log("錯誤: %s。什麼都沒寫:沒開席、卡沒建" % role_err, prefix="[x]")
            return 2
        # review r16(人格):量不到艙真跑也在建卡前擋(以前只有試跑先擋,真跑要等開席才發現 ⇒ 卡已建)
        if not current_cabin():
            log(("[DRY-RUN] " if args.dry_run else "") + "量不到你所在的艙,--new-seat 開不了席;在艙裡的席位上執行,"
                "或設 EZ2_AIB_INSTANCE。什麼都沒寫:沒開席、卡沒建", prefix="[x]")
            return 2
        # review r16 m1:艙號格式、引擎名、席位資料夾、socket 名 —— 開席前能驗的全在寫卡前驗(試跑同一組)
        try:
            new_seat_precheck(args.engine)
        except NewSeatError as exc:
            log(("[DRY-RUN] " if args.dry_run else "") + "%s。什麼都沒寫:沒開席、卡沒建" % exc, prefix="[x]")
            return 2
    elif args.seat and args.seat.strip():
        seat_id, seat_rec, seat_instance, seat_err = resolve_seat_arg(args.seat)
        if seat_err:
            log(seat_err + "。未建卡、未綁定", prefix="[x]")
            return 2
    elif args.deliver:
        log("--deliver 要配 --seat <艙位碼>/<席碼>(沒指定席位就只建卡、不綁、不寄)。未建卡、未綁定", prefix="[x]")
        return 2
    if bind_target and not seat_id and not args.new_seat:
        log("無法唯一辨識活席;請用 --seat 指定目標。未建卡、未綁定", prefix="[x]")
        return 2

    vault_dir = Path(args.vault).expanduser().resolve()
    # 角色庫位置從路由契約現查(the maintainer 08-27 第二鐵律;09-26 R9 資料夾要改 uid,改的是契約,這裡不動)
    sys.path.insert(0, str(REPO_ROOT / "src" / "persona_route"))   # 其他零件一律從 REPO_ROOT(EZ2_AIB_REPO)找
    from route_register import resolve_route_folder, default_contract_spec, BUNDLED_CONTRACT
    lib_rel = resolve_route_folder(LIBRARY_ROUTE_KEY, root=str(vault_dir))   # 用 --vault 那一個查索引,不是環境變數
    if not lib_rel:
        log("錯誤: 路由契約裡查不到 %s(角色庫在哪),不猜" % LIBRARY_ROUTE_KEY, prefix="[x]")
        # 10-12(a seat Pro):自訂 vault 有內容、看不出版面 ⇒ 說清楚兩條路,不只說「不猜」
        log("  知識庫 %s 已經有東西,aib 看不出角色庫該放哪。二選一:\n"
            "  1) 在知識庫裡建一個 10-Roles 資料夾(mkdir \"%s/10-Roles\"),aib 就用預設版面\n"
            "  2) 把 EZ2_ROUTE_REGISTRY 指到你自己的路由契約檔" % (vault_dir, vault_dir), prefix="  ")
        sys.exit(2)
    role_lib_dir = vault_dir / lib_rel
    # aib 自己的預設 vault(新機器)⇒ 角色庫資料夾第一次用時建起來;別人的 vault 照舊不動、不猜。
    # 10-12(a seat:Pro 的 ~/Ez2/Vault):用的是套件預設契約 = route_register 判過這個 vault 安全(空的 / 有索引 / 已有 10-Roles)⇒ 也建
    # 10-14 新用戶走查:--dry-run 一個檔都不寫 ⇒ 試跑時只判「會建」,不真的建(下面的存在檢查也跟著放過)。
    would_create_lib = not role_lib_dir.is_dir() and (str(vault_dir) == os.path.realpath(_aib_default_vault())
                                                      or default_contract_spec() == BUNDLED_CONTRACT)
    if would_create_lib and not args.dry_run:
        role_lib_dir.mkdir(parents=True, exist_ok=True)

    if not role_lib_dir.is_dir() and not (args.dry_run and would_create_lib):
        log(f"錯誤: 找不到 Vault 角色庫路徑: {role_lib_dir}", prefix="[x]")
        sys.exit(2)

    cards_json = AIB_HOME / "identity" / "cards.json"
    existing_cards = get_existing_cards(cards_json)

    # 1. 檢查是否既有同名卡，或分配新 UID
    name = args.name.strip()
    if not name or name in (".", "..") or any(c in name for c in "/\\\n\r\x00"):
        parser.error("角色名稱必須是單一資料夾名稱")
    target_card_uid = None
    for uid, card in existing_cards.items():
        if card.get("display_name") == name or card.get("card_ref") == f"P10/{name}":
            target_card_uid = uid
            log(f"找到既有角色卡: {name} (UID: {target_card_uid})")
            break

    new_uid = not target_card_uid
    if new_uid:                     # 10-15 E5:先挑號不印;內容檢查過了才印「分配新角色 UID」
        target_card_uid = allocate_uid(existing_cards)

    # 既有卡沒另外指定時,代號與圖示報卡上那一份(席位記憶與回報才不會寫成預設值)
    known = existing_cards.get(target_card_uid) or {}
    code = sanitize_code(name, args.code or (known.get("code") if isinstance(known.get("code"), str) else None))
    emoji = args.emoji.strip() or (known.get("emoji") if isinstance(known.get("emoji"), str) else "") or "🎭"
    # 10-17:跟 note-format / aib vault newpost 同一個格式(UTC ISO、毫秒、Z);以前寫本機「2026-10-17 16:40:42」,
    #   全庫統一成 ISO 後新卡又冒出舊格式,aib vault doctor 會一直抓到
    _now = datetime.datetime.now(datetime.timezone.utc)
    now_str = _now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (_now.microsecond // 1000)

    # 2. 寫入 Vault 角色.md
    #    既有卡 ⇒ 用索引記的那個資料夾(顯示名「專案經理」的卡住在 PM-專案經理/);新卡 ⇒ 看名字會不會被吃掉一段
    known_rel = (existing_cards.get(target_card_uid) or {}).get("card_path_rel")
    if isinstance(known_rel, str) and known_rel.endswith("/角色.md"):
        folder_name = Path(known_rel).parent.name
    elif (role_lib_dir / name / "角色.md").is_file():
        folder_name = name
    else:
        # the maintainer 10-15:「人格與專案都用 uid 建立,顯示才用 title」⇒ 新卡資料夾 = 卡 uid;
        #   顯示名讀卡上的 name:(uid 版面另寫進 .ez2/display/labels.json),改名不必搬資料夾。
        folder_name = target_card_uid
    target_role_dir = role_lib_dir / folder_name
    target_role_file = target_role_dir / "角色.md"

    wrapped = False
    if args.prompt:
        args.prompt, wrapped = wrap_short_prompt(args.prompt)
    prompt_content = args.prompt or (
        f"你是「{name}」。\n\n"
        "## 職責\n"
        "依指示執行專門任務，嚴守回報紀律，提供真實往返與可重現證據。\n\n"
        "## 紅線\n"
        "第一句交代現在能不能用，不講虛詞，未驗證的項目絕不含糊。\n\n"
        "## 語氣與口癖\n"
        "性格鮮明，風格一致，以繁體中文直接回答。\n"
        "\n## 知識域\n"
        "依派工範圍查核資料與來源,缺乏依據時明示未知。\n"
    )

    card_markdown = (
        "---\n"
        f'uid: "{target_card_uid}"\n'
        f'name: "{card_display_name(name)}"\n'
        f"code: {code}\n"
        f"model: auto\n"
        f"emoji: {emoji}\n"
        "tools: []\n"
        "kb: []\n"
        f"category: {args.category}\n"
        f"created: {now_str}\n"
        f"updated: {now_str}\n"
        "---\n"
        f"{prompt_content.strip()}\n"
    )

    # root2 複審 318 F1(甲):既有同名卡(cards.json 有、或角色.md 已在磁碟上)⇒ **不改卡**,只 scan + bind + 記憶;要改要明給 --overwrite。
    #   一鍵工具最常見的手滑是「再跑一次只想綁席位」,沒帶 --prompt 會把手寫人設洗成預設四段再 commit 進 vault。刀:拿掉這個判斷 ⇒ 既有卡逐位元組不變那格紅。
    # ★ review r6 m3:「寫之前這張卡在不在」先定下來(existed_before),回報與 JSON 都看它,不看寫完之後的狀態。
    existed_before = target_card_uid in existing_cards or target_role_file.is_file()
    keep_existing = existed_before and not args.overwrite
    # ★ review r14 M3:新卡沒給人設(--prompt / --prompt-file)⇒ 卡上會是預設四段範本;綁進席位(或再 --deliver 寄出)
    #   等於讓那一席照一份空範本做事。要綁席時擋下;只建卡照舊(之後可用 --overwrite 補人設)。
    if not existed_before and (bind_target or args.deliver) and not (args.prompt or args.prompt_file):
        log("錯誤: 「%s」是新卡,要綁席位得先給人設(--prompt 或 --prompt-file);不把預設範本綁進席位。"
            "什麼都沒寫" % name, prefix="[x]")
        return 2
    if keep_existing:
        log(f"1. 沿用既有角色卡 {target_card_uid}({target_role_file}),卡沒動;要重寫請帶 --overwrite")
        if target_role_file.is_file():
            try:
                with open(target_role_file, "r", encoding="utf-8") as f:
                    prompt_content = f.read().split("---", 2)[-1] if f else prompt_content
            except OSError:
                pass
    else:
        failures = lint_card(card_markdown)
        if failures:
            log("PERSONA_LINT_FAIL: " + " ".join(lint_messages(failures)), prefix="[x]")
            return 4
        if new_uid:
            log(T("aib.newrole.uid_allocated", {"uid": target_card_uid}))
        if wrapped:
            log(T("aib.newrole.prompt_wrapped"), prefix="[!]")
        log(f"1. 準備寫入 Vault 角色卡: {target_role_file}")
    if args.dry_run or keep_existing:
        if args.dry_run:
            log("[DRY-RUN] 跳過寫入檔案")
    else:
        target_role_dir.mkdir(parents=True, exist_ok=True)
        # 記憶整理第 1 包(10-13):--overwrite 會蓋掉舊卡 ⇒ 先拍快照到 <狀態根>/archive/persona/<uid>/;拍不成就不寫
        snap = _archive_snapshot(target_role_file, "persona", target_card_uid)
        if snap.get("status") == "failed":
            log("錯誤: 改卡前備份失敗(%s);卡沒動" % snap.get("error"), prefix="[x]")
            return 2
        with open(target_role_file, "w", encoding="utf-8") as f:
            f.write(card_markdown)
        log("   -> Vault 檔案已寫入")
        if folder_name == target_card_uid and write_label_title(vault_dir, target_card_uid, card_display_name(name)):
            log("   -> 顯示名已寫進 .ez2/display/labels.json")
        try:
            commit_card(role_lib_dir, target_role_file, name)
        except (OSError, RuntimeError, ValueError) as exc:
            log(str(exc), prefix="[x]")
            return 5

    # 10-12 新用戶:狀態樹還沒建(沒跑過 aib init)⇒ 先建,不讓卡已寫進 vault 卻在掃描那步卡住
    #   試跑不建(aib_init 會種 secrets 範本、帳本、說明檔;--dry-run 一個檔都不寫)。
    if not (AIB_HOME / "identity").is_dir() and not args.dry_run:
        subprocess.run([sys.executable, str(REPO_ROOT / "src" / "bootstrap" / "aib_init.py")],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    # 3. 重新掃描卡庫投影 (card_registry.py scan)
    log("2. 執行 card_registry scan 同步卡片索引...")
    scan_script = REPO_ROOT / "src" / "memory" / "card_registry.py"
    scan_cmd = [sys.executable, str(scan_script), "scan", "--vault", str(vault_dir)]
    if args.dry_run:
        scan_cmd.append("--dry-run")

    if args.dry_run and (not vault_dir.is_dir() or would_create_lib):
        # 試跑而知識庫或角色庫還沒建(真跑會建、試跑不建)⇒ 沒有東西可掃,新卡試跑本來就不寫 ⇒ 跳過,不當成失敗
        log("   -> [DRY-RUN] 角色庫還沒建立,跳過索引掃描")
    else:
        rc, out, err = run_cmd(scan_cmd, cwd=REPO_ROOT)
        if rc != 0:
            log(f"錯誤: card_registry.py scan 失敗 (rc={rc}):\n{err}", prefix="[x]")
            sys.exit(rc)
        log("   -> 卡片索引掃描完成")
        # K6(10-14):知識庫裡有角色庫以外的 角色.md ⇒ 照實說沒收進索引、怎麼搬(scan 的摘要帶回來)
        try:
            summary = json.loads(out) if out.strip().startswith("{") else {}
        except ValueError:
            summary = {}
        if summary.get("outside_library_count"):
            paths = summary.get("outside_library") or []
            log(T("aib.cards.outside_library", {"n": summary["outside_library_count"],
                                                "lib": summary.get("library_folder") or lib_rel,
                                                "paths": ", ".join(paths[:5]) + (" …" if summary["outside_library_count"] > 5 else "")}),
                prefix="[!]")

    if not bind_target:
        # the maintainer 10-01:沒指定席位 ⇒ 到此為止:卡建好、索引掃好,不綁席、不寫席位記憶、不寄人格包
        # ★ review r6 m3:既有卡先判(試跑也照實說「本來就有」);--overwrite 既有卡 ⇒ 說覆寫,不說創立。
        if keep_existing:
            reply_key = "aib.newrole.card_only_exists"
        elif args.dry_run and existed_before:
            reply_key = "aib.newrole.card_only_dry_run_overwrite"
        elif args.dry_run:
            reply_key = "aib.newrole.card_only_dry_run"
        elif existed_before:
            reply_key = "aib.newrole.card_only_overwritten"
        else:
            reply_key = "aib.newrole.card_only_created"
        reply = T(reply_key, {"name": name, "uid": target_card_uid})
        print(reply, file=sys.stderr)
        print(json.dumps({
            "ok": True,
            "uid": target_card_uid,
            "name": name,
            "code": code,
            "emoji": emoji,
            "card_file": str(target_role_file),
            "created": not existed_before,
            "overwritten": bool(existed_before and args.overwrite and not args.dry_run),
            "seat_bound": None,
            "reply": reply,
        }, ensure_ascii=False, indent=2))
        return 0

    # 4. 偵測席位與綁定 (bind_one.py)
    if args.new_seat:
        # review r15 M1(人格):所有不需要席位的檢查都過了才開新席
        if args.dry_run:
            log("[DRY-RUN] 會在艙 %s 開一個新的 %s 席(角色 %s),等它啟動後綁上「%s」" % (
                current_cabin() or "(量不到)", args.engine, new_seat_role, name))
            seat_id = "(新席)"
        else:
            try:
                seat_id, seat_rec, seat_instance = open_new_seat(new_seat_role, engine=args.engine,
                                                                 timeout=new_seat_timeout())
            except NewSeatError as exc:
                # ★ review r16 M1:開席指令成功但等不到席碼/冒出多席 ⇒ 席已開、在跑,不能說「沒開成」
                #   (AI 會再下一次 --new-seat 多開一席)。卡在不在也照實說。
                card_said = "角色卡已建、索引已掃" if not existed_before or args.overwrite else "角色卡本來就有(沒動)"
                if getattr(exc, "opened", False):
                    log(str(exc) + "。%s;**新席已經開了**(在跑),只是沒綁 —— 不要再下 --new-seat,"
                        "等右欄出現那一席後改用 --seat <艙位碼>/<席碼> 綁" % card_said, prefix="[x]")
                else:
                    log(str(exc) + "。%s;新席沒開成、沒綁" % card_said, prefix="[x]")
                return 2
    log(f"3. 準備綁定席位 seat_id: {seat_id} (UID: {target_card_uid})...")
    # 帳本署名不猜(bind_one 自己也「必填,無預設」):--actor > EZ2_OWNER_SIG > 具名停手。卡已寫、索引已掃,綁定一步都沒做。
    actor = (args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip()
    if not actor:
        # review r14 M3:試跑時卡沒寫、索引沒掃 ⇒ 不能說「角色卡已建」(AI 會以為卡在了,改真跑就建出範本卡)
        log("錯誤: 綁定席位要帳本署名 --actor role@hex/Engine(或設 EZ2_OWNER_SIG);不從席位猜。" +
            ("(試跑)什麼都沒寫:卡沒建、席位沒綁" if args.dry_run else "角色卡已建、索引已掃,席位沒綁"), prefix="[x]")
        return 2
    bind_script = REPO_ROOT / "src" / "identity" / "bind_one.py"
    # ★ review r14 M2:沒明給 --brain 而那一席已綁專案(席位檔有 project_uid)⇒ 不動資料夾(= none)。
    #   以前一律預設 core.handoff ⇒ 換人格時 bind_one 把專案那一席的資料夾悄悄改成 core.handoff,專案卡記的還是舊的。
    if args.brain is None:
        brain = "" if (seat_rec or {}).get("project_uid") else "core.handoff"
    else:
        brain = (args.brain or "").strip()
    if brain.lower() in ("", "none"):
        brain = ""                                  # 只綁人格:不帶 --brain,專案(第二大腦)維持原樣
    bind_cmd = [
        sys.executable, str(bind_script), "bind",
        "--seat", seat_id,
        "--card", target_card_uid,
        "--actor", actor,
        "--rebind-persona"
    ] + (["--brain", brain] if brain else [])
    fit_warn = None
    if args.dry_run:
        log(f"   -> [DRY-RUN] 模擬綁定席位 {seat_id} 至新卡 {target_card_uid} (大腦: {brain or '不綁'})")
    else:
        rc, out, err = run_cmd(bind_cmd, cwd=REPO_ROOT)
        if rc != 0:
            log(f"錯誤: bind_one.py 失敗 (rc={rc}):\n{err}", prefix="[x]")
            # 10-15 @977e3 實機:卡在步驟 1 已寫進 vault、索引已掃,只有綁席沒成 ⇒ 講明卡怎麼了、重跑要怎麼下
            #   (審查:帶 --new-seat 時新席已開,重跑同一行會再開一席;帶 --overwrite 重跑會再覆寫一次 ⇒ 各自講)
            card_key = ("aib.newrole.bind_failed_card_existing" if keep_existing
                        else "aib.newrole.bind_failed_card_overwritten" if existed_before
                        else "aib.newrole.bind_failed_card_kept")
            log(T(card_key, {"name": name, "uid": target_card_uid, "path": str(target_role_file)}), prefix="[x]")
            if args.new_seat:
                log(T("aib.newrole.bind_failed_rerun_new_seat", {"seat": seat_id}), prefix="[x]")
            elif existed_before and args.overwrite:
                log(T("aib.newrole.bind_failed_rerun_drop_overwrite"), prefix="[x]")
            else:
                log(T("aib.newrole.bind_failed_rerun"), prefix="[x]")
            sys.exit(rc)
        log("   -> 席位綁定完成")
        # 10-12 Bot 開發第 16 件:bind_one 收據裡的 provider_warning ⇒ 只警告,照綁、不改卡
        try:
            _bind_out = json.loads(out) or {}
            fit_warn = _bind_out.get("provider_warning")
            # 10-15:整艙重投影時別席的路由過不了表(保留原綁定、標未解析)⇒ 照印,不吞
            for _line in _bind_out.get("route_notices") or []:
                log(_line, prefix="[!]")
        except (ValueError, AttributeError):
            fit_warn = None
        if fit_warn:
            log(T("aib.newrole.provider_mismatch", {
                "name": fit_warn.get("display_name") or name, "uid": fit_warn.get("card_uid") or target_card_uid,
                "card_kind": fit_warn.get("card_kind"), "seat_provider": fit_warn.get("seat_provider")}), prefix="[!]")

    # 5. 寫入席位記憶 context.md
    # 席位記憶的資料夾用**席碼**(session_uuid 尾五碼):人格包告訴那一席的就是 memory/<席碼>/context.md,
    #   那一席自己寫記憶也寫在那裡。09-29 之前這裡用 seat_id ⇒ 同一席兩份記憶互相看不到
    #   (真機:指令寫 memory/dad88/、Grok 自己寫 memory/77324/)。席碼量不到才退回 seat_id。
    mem_key = memory_key_of(seat_id, seat_rec)
    log(f"4. 寫入席位記憶: ~/.ez2-aib/memory/{mem_key}/context.md")
    mem_dir = AIB_HOME / "memory" / mem_key
    mem_file = mem_dir / "context.md"
    mem_content = (
        f"# {mem_key} 席記憶 ({name})\n\n"
        f"## 綁定角色人設\n"
        f"- **角色**：{name}\n"
        f"- **UID**：{target_card_uid} / 圖示：{emoji}\n\n"
        f"## 人設與職責\n"
        f"{prompt_content.strip()}\n"
    )
    if args.dry_run:
        log("[DRY-RUN] 跳過寫入席位記憶")
    else:
        mem_dir.mkdir(parents=True, exist_ok=True)
        uid_line = f"- **UID**：{target_card_uid} /"
        already = False
        if mem_file.is_file() and not args.overwrite:
            try:
                already = uid_line in mem_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                already = False
        if already:
            # 同一張卡重綁(換席、補寄人格包)不再貼一份:記憶檔每次開席都要讀,重複的人設只會把它撐長
            log("   -> 席位記憶已經有這張卡的人設,沒有再貼一份")
        elif mem_file.is_file() and mem_file.stat().st_size:
            # 既有席位記憶不整檔蓋掉(那是這一席累積的交接點):把人設段接在後面
            with open(mem_file, "a", encoding="utf-8") as f:
                f.write("\n\n" + mem_content)
            log("   -> 席位記憶已有內容,人設段接在檔尾(沒有蓋掉原有記憶)")
        else:
            with open(mem_file, "w", encoding="utf-8") as f:
                f.write(mem_content)
            log("   -> 席位記憶已寫入")

    # 6. 驗收 (aib-hdr.py)
    hdr_script = REPO_ROOT / "tools" / "hdr" / "aib-hdr.py"
    hdr_text = ""
    if not args.dry_run and hdr_script.is_file():
        rc, out, _ = run_cmd([sys.executable, str(hdr_script)], cwd=REPO_ROOT)
        if rc == 0:
            hdr_text = out.strip()
            log(f"5. 驗收標頭: {hdr_text}")

    result = {
        "ok": True,
        "uid": target_card_uid,
        "name": name,
        "code": code,
        "emoji": emoji,
        "card_file": str(target_role_file),
        "seat_bound": seat_id,
        "brain": brain or None,
        "memory_file": str(mem_file),
        "header": hdr_text
    }
    if fit_warn:
        result["provider_warning"] = fit_warn
    if args.deliver:
        log("6. 把人格包寄給那一席(aib dm)...")
        if args.new_seat and args.dry_run:
            # 試跑時新席還沒開,沒有地址可寄;不是寄送失敗
            log("   -> [DRY-RUN] 會把人格包寄給剛開的新席")
            result["delivery"] = {"ok": None, "dry_run": True, "address": "(新席)"}
        else:
            result["delivery"] = deliver_persona(seat_id, seat_rec, seat_instance, name, vault_dir,
                                                 dry_run=args.dry_run)
        if result["delivery"].get("ok") is False:
            log("人格已綁好,但人格包沒寄到:%s" % result["delivery"].get("reason"), prefix="[x]")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 6
        if result["delivery"].get("no_retry"):
            log("人格已綁好;人格包%s" % result["delivery"].get("reason"), prefix="[!]")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
