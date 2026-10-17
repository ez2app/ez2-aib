#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""卡片正本家產生器(L2-01 / 總綱 §二-D 第一子項)。

    python3 src/memory/card_registry.py scan  [--vault DIR] [--contract F] [--out F]
                                              [--route-defaults F] [--state-root DIR] [--dry-run]
    python3 src/memory/card_registry.py check [--out F] [...同上]
    python3 src/memory/card_registry.py show  <uid|card_ref> [--out F]

## 這支在解決什麼

2026-08-17 實查:活體 18 席的人格綁定投影,provenance 記的卡片登記表是
`/private/tmp/claude-501/…/scratchpad/cards-main.json`——一個活在 Claude 暫存區、
只有 4 張卡、key 是 `P10/PM-專案經理` 這種**路徑字串**、persona_uid 是 `pm-main`
這種 **slug** 的孤兒檔。路徑會改、slug 是人取的、暫存區隨時被清:三個都不是身份。

角色庫(Obsidian vault `P-Ez2 AI專家/P10-專家模板/角色庫/<卡>/角色.md`)每張卡
frontmatter 有 `uid:`——ez2-ai `docs/260623-命名規範.md:5` 定的「6 碼小寫 base36
(0-9a-z)」,出生時 `ez2-role-create.js` 產生、`--update` 保留。**那才是身份。**

本模組:唯讀掃描角色庫 → 以 uid 為 key 生成 `<state_root>/identity/cards.json`(v2),
每張卡帶 `persona_sha` = `角色.md` **全檔 sha256**(版本指紋;uid 是出生證不隨內容變,
body 雜湊會漏 frontmatter 改動)。`card_ref`(`P10/<目錄名>`)只保留做既有 18 席
`persona_card` 舊值的**查表橋**,不是身份欄(艦長 08-17 22:22 裁(2))。

## 紅線(每條都有測試釘著;`tools/probes/mutate-card-registry.sh` 逐條證明拿掉會紅)

- **不動 vault 一個位元組**:對 vault 的所有讀取只經 `_read_bytes()`,而它只用
  `os.open(path, os.O_RDONLY)`。不建檔、不改 mtime、不寫 `.kb-index`。
- **key = uid,不是目錄名/label**:目錄名改了身份不變;兩張卡同 uid → **整份不寫 rc 2**
  (身份撞號一律全停,對齊 bind-seats 末五碼撞號)。
- **uid 形狀 `[0-9a-z]{6}`**:命名規範是 base36 不是 hex(角色庫實有 `3etmsi/gvw3k9/3etsp1`
  三張非 hex,合法照收;艦長裁(3))。缺 uid / 形狀不合 → 跳過並列在 `skipped`,不猜。
- **不替卡發明預設大腦**:`default_route_key` 只來自 `--route-defaults`(repo 內
  `src/memory/card-route-defaults.json`,現只有從舊登記表遷移的 4 張),其餘 `null`
  =「無預設大腦(待 E 新建/手選)」(艦長裁(4))。給了的值必過路由契約,不過 rc 2。
- **契約讀不到 = rc 2,不掃**:角色庫在 vault 裡的位置從契約 `product.expert.agent_library`
  取,不寫死;看不到規則就不投影,對齊 binding_projection。
- **來源端消毒**(qa 二審 P1):frontmatter 值去控制字元/引號/命令替換字元;目錄名帶這些字元
  → 整張卡跳過列名(路徑不能改寫)。投影檔不帶毒,下游選單/run-shell 不必各自防。
- **產出是投影,不得手改**:同目錄落 `cards.json.provenance.json` 收據;`check` 重掃
  比對,漂移 rc 1。
"""
import argparse
import hashlib
import json
import sys
import os
import re
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
for _p in ("memory", "persona_route"):
    sys.path.insert(0, str(_SRC / _p))

from route_register import check_against_contract, load_contract, contract_digest  # noqa: E402
import route_register as _RR  # noqa: E402  10-12:預設 vault / 預設契約同一條規則

SCHEMA_VERSION = "aib-cards/v2"
LIBRARY_ROUTE_KEY = "product.expert.agent_library"
CARD_FILE = "角色.md"
CARD_REF_PREFIX = "P10/"
# 人格風格 12 維(套乙)。名字正本 = docs/spec/persona-dimensions.md:16 那 12 個中文。
# id 是機器鍵,label 走 i18n `aib.persona.dim.<id>`。AI 紀律 12D(套甲)不進卡。
# 本倉產投影時一律 unmeasured、value 不填;值由 Ez2 AI app 填。
PERSONA_STYLE_DIMS = (
    ("expression_tone", "表達語氣"),
    ("decision_style", "決策風格"),
    ("risk_line", "風險底線"),
    ("product_taste", "產品品味"),
    ("work_pace", "工作節奏"),
    ("learning_style", "學習方式"),
    ("customer_service", "客服風格"),
    ("marketing_style", "行銷風格"),
    ("tech_governance", "技術治理"),
    ("game_preference", "遊戲偏好"),
    ("creation_preference", "創作偏好"),
    ("shareable_persona", "可分享人格"),
)
DIM_STATES = ("measured", "unmeasured")
# 命名規範(ez2-ai docs/260623-命名規範.md:5):6 碼小寫 base36。fullmatch,不用 $。
UID_RE = re.compile(r"[0-9a-z]{6}")
DISPLAY_NAME_MAX = 24  # 與 binding_projection.DISPLAY_SCHEMA 同一個上限
DEFAULT_VAULT = "~/Desktop/Obsidian/Ez2"  # 與 cockpit-env.sh EZ2_VAULT_DIR 預設同源
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)

REASON_MISSING_UID = "missing_uid"
REASON_UID_SHAPE = "invalid_uid_shape"
REASON_UNREADABLE = "invalid_card_unreadable"
REASON_NO_FRONTMATTER = "missing_frontmatter"


class CardRegistryError(Exception):
    """來源不可用/身份撞號 —— fail-closed,呼叫端回 rc 2,一個位元組都不寫。"""


def unmeasured_dim(dim_id, name):
    """本倉投影的一維:沒量過。value 不填(None),不是 0。"""
    return {
        "id": dim_id,
        "name": name,
        "state": "unmeasured",
        "value": None,
        "confidence": None,
        "measured_at": None,
        "corpus_size": None,
    }


def default_dims():
    """12 維全 unmeasured,順序照 PERSONA_STYLE_DIMS(= persona-dimensions.md:16)。"""
    return [unmeasured_dim(dim_id, name) for dim_id, name in PERSONA_STYLE_DIMS]


def validate_dims(dims):
    """形狀閘。state=measured 但 value 缺 → 紅(量不到是獨立一態,不是 0)。

    本倉 scan 只產 unmeasured;這支給讀端/測試擋「標了已量卻沒值」。
    """
    if not isinstance(dims, list) or len(dims) != len(PERSONA_STYLE_DIMS):
        raise CardRegistryError("dims 必須恰好 %d 項" % len(PERSONA_STYLE_DIMS))
    for i, (want_id, want_name) in enumerate(PERSONA_STYLE_DIMS):
        item = dims[i]
        if not isinstance(item, dict):
            raise CardRegistryError("dims[%d] 不是物件" % i)
        if item.get("id") != want_id or item.get("name") != want_name:
            raise CardRegistryError(
                "dims[%d] 不是正本第 %d 維(%s/%s)" % (i, i + 1, want_id, want_name))
        state = item.get("state")
        if state not in DIM_STATES:
            raise CardRegistryError("dims[%d] state 不在 measured/unmeasured" % i)
        if "value" not in item or item.get("value") is None:
            if state == "measured":
                raise CardRegistryError(
                    "dims[%d] state=measured 但 value 缺——量不到不得標 measured"
                    % i)
        elif state == "unmeasured":
            raise CardRegistryError(
                "dims[%d] state=unmeasured 卻填了 value——沒量過不是 0" % i)
    return True


def ensure_dims(card):
    """舊投影沒 dims 欄仍能讀:補上 12 維 unmeasured。不猜分數。"""
    if not isinstance(card, dict):
        raise CardRegistryError("card 不是物件")
    out = dict(card)
    if "dims" not in out or out["dims"] is None:
        out["dims"] = default_dims()
    validate_dims(out["dims"])
    return out


# ---- 唯讀讀取:vault 的每一個 byte 都只從這裡進來 ------------------------------

_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _read_bytes(path):
    """唯讀取檔。O_RDONLY(+O_NOFOLLOW:symlink 即拒,對齊 ED.2 的鏈上 symlink fail-closed)。
    ★ 這是模組裡唯一碰 vault 檔案內容的入口;測試釘 `_READ_FLAGS` 不含任何寫旗標。"""
    fd = os.open(str(path), _READ_FLAGS)
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse_frontmatter(text):
    """最小 YAML 子集:`key: value` 一行一對,去掉成對引號。不是 YAML 解析器——
    persona-card.js lint 規定純數字 uid 必加引號免得被 YAML 吃成數字,本函式一律
    當字串讀,所以有沒有引號結果一樣。回 None = 沒有 frontmatter。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t#" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


# ---- 來源端消毒(qa 二審 P1,08-18 00:54):角色庫的目錄名與 frontmatter 是**外部輸入**,
#      原封進投影檔=投影檔本身帶毒,下游(選單/run-shell/面板)每一個消費端都得各自防。
#      規則:控制字元、引號、命令替換/管線/重導字元一律拿掉;目錄名(它是路徑,不能改寫
#      語意)含這些字元 → 整張卡 **跳過並列名**,不清洗、不猜。uid/route 形狀閘不變。
# 禁字集=真的能改變 shell/引號語意的字元;括號、逗號、方括號本身不是向量(`$(` 靠 `$` 擋),
# 角色庫實有 `a persona card (從LINE,FB對話提取風格)`,禁括號會把一張真卡整張丟掉(實機 dry-run 抓到)。
_BANNED = set("\"'`$|;&<>\\")
_BANNED_RE = re.compile("[%s]" % re.escape("".join(sorted(_BANNED))))
REASON_DIRNAME_CHARS = "invalid_dirname_chars"


def has_unsafe_chars(text):
    """控制字元(含換行/tab)或命令替換/引號類字元 → True。"""
    return any(ch < " " or ch == "\x7f" for ch in text) or bool(_BANNED_RE.search(text))


def clean_value(text, limit=64):
    """frontmatter 值(顯示用 metadata)的消毒:去控制字元、去 _BANNED、收攏空白、限長。
    None/空 → None。"""
    if text is None:
        return None
    text = "".join(ch if (ch >= " " and ch != "\x7f") else " " for ch in str(text))
    text = _BANNED_RE.sub("", text)
    text = " ".join(text.split())[:limit]
    return text or None


_CODE_RE = re.compile(r"[A-Za-z0-9._-]{1,32}")
# 10-17:全庫 updated 統一成 UTC ISO(2026-10-06T16:12:16.000Z);舊的本機「日期 時間」照收
_UPDATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?|T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z)?")


def _shape_or_none(text, regex):
    """有固定形狀的欄位(code / updated)不清洗、只驗形:不合就 None,不猜。"""
    text = clean_value(text, 64)
    return text if text and regex.fullmatch(text) else None


_LABELS_CACHE = {}


def _label_title(vault_root, folder_id):
    """uid 版面:<vault>/.ez2/display/labels.json 的 folders[<folderId>].title(卡夾名=卡片 uid 時用)。"""
    key = str(vault_root)
    if key not in _LABELS_CACHE:
        try:
            _LABELS_CACHE[key] = json.loads((Path(vault_root) / ".ez2" / "display" / "labels.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LABELS_CACHE[key] = {}
    return ((_LABELS_CACHE[key].get("folders") or {}).get(folder_id) or {}).get("title")


def display_name_of(dirname):
    """目錄名 → 顯示名:去掉 `CODE-` 前綴(`PM-專案經理`→`專案經理`,與活體 18 席
    現行顯示一致);沒有前綴就整個目錄名。超過 24 截斷並回旗標(schema 上限)。"""
    m = re.match(r"^[A-Za-z0-9]{1,12}-(.+)$", dirname)
    name = m.group(1) if m else dirname
    return fit_display_name(name)


def fit_display_name(name):
    """顯示名超過上限時的截法(display_name_of 與卡上 name: 共用)⇒ (名字, 是否截過)。"""
    name = name.strip()
    if len(name) > DISPLAY_NAME_MAX:
        # 09-14(the maintainer「標題顯示不正確」判準④「人格名正確:不是描述文、括號閉合」):
        # 角色庫有「a persona card (從LINE,FB對話提取風格)」這種「名字 (描述)」的目錄名,
        # 硬切 24 字會切在括號裡,畫面上出現「a persona card (從LINE,FB對話提取風格」——
        # 括號不閉合、描述文當名字用。超長時**先丟括號尾段**(描述不是名字),
        # 還是太長才硬切;旗標照回 True(schema 上限那條語意不變)。
        cut = _strip_trailing_parenthetical(name)
        if cut and cut != name:
            return (cut[:DISPLAY_NAME_MAX], True)
        return name[:DISPLAY_NAME_MAX], True
    return name, False


_PAREN_TAIL_RE = re.compile(r"\s*[(\uff08][^()\uff08\uff09]*[)\uff09]?\s*$")


def _strip_trailing_parenthetical(name):
    """去掉名字尾端的「(描述)」(半形/全形括號都認;沒閉合的也算尾段)。
    整個名字都是括號 ⇒ 回原名(不把名字丟成空字串)。"""
    cut = _PAREN_TAIL_RE.sub("", name).strip()
    return cut or name


# ---- 來源解析 ------------------------------------------------------------------

def resolve_vault(explicit=None):
    root = explicit or os.environ.get("EZ2_VAULT_DIR") or _RR.default_vault()   # 10-12:全套件同一條 vault 規則
    root = os.path.expanduser(root)
    if not os.path.isabs(root):
        raise CardRegistryError("vault 路徑必須是絕對路徑:%r" % (root,))
    p = Path(root)
    if not p.is_dir():
        raise CardRegistryError("vault 不存在或不是目錄:%s" % p)
    return p


def library_folder_of(contract, vault_root=None):
    if vault_root is not None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "persona_route"))
            import route_register as _RR
            idx = _RR.vault_index_folder(LIBRARY_ROUTE_KEY, str(vault_root))
            if idx:
                return idx                  # uid 版面:角色庫真路徑在 vault 索引
        except Exception:
            pass
    for route in (contract or {}).get("routes", []):
        if route.get("routeKey") == LIBRARY_ROUTE_KEY:
            folder = route.get("folder")
            if folder:
                return folder
    raise CardRegistryError(
        "路由契約裡沒有 %s(或沒有 folder)——不知道角色庫在哪,不猜" % LIBRARY_ROUTE_KEY)


def load_route_defaults(path):
    """uid → default_route_key。檔不存在=空表(不是錯:預設本來就可以一張都沒有)。
    給了路徑但讀不出來/形狀不對 = 錯。"""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise CardRegistryError("--route-defaults 檔不存在:%s" % p)
    try:
        data = json.loads(_read_bytes(p).decode("utf-8"))
    except (OSError, ValueError) as err:
        raise CardRegistryError("--route-defaults 讀不出來:%s" % err)
    defaults = data.get("defaults") if isinstance(data, dict) else None
    if not isinstance(defaults, dict):
        raise CardRegistryError("--route-defaults 形狀不對:要 {\"defaults\": {uid: route_key}}")
    for uid, rk in defaults.items():
        if not UID_RE.fullmatch(str(uid)) or not isinstance(rk, str) or not rk:
            raise CardRegistryError("--route-defaults 條目不合:%r -> %r" % (uid, rk))
    return dict(defaults)


# ---- 掃描 ----------------------------------------------------------------------

def scan(vault_root, library_folder, contract, route_defaults=None):
    """純函數(來源全由呼叫端給)。回 {"cards": {...}, "skipped": [...]}。
    同 uid 撞號 → raise(整份不產)。"""
    route_defaults = route_defaults or {}
    lib = Path(vault_root) / library_folder
    if not lib.is_dir():
        raise CardRegistryError("角色庫目錄不存在:%s" % lib)

    cards, skipped, owners = {}, [], {}
    for entry in sorted(os.scandir(str(lib)), key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("_"):
            continue  # `_` 開頭=基建夾(_wizard_rollback/_子AI模版/…),不是卡
        card_path = Path(entry.path) / CARD_FILE
        if not card_path.is_file():
            continue  # 沒有角色.md 的夾不是卡(例:對話/會議夾)
        if has_unsafe_chars(entry.name):
            # 目錄名是路徑也是 display_name/card_ref 的來源;帶注入字元就整張跳過,
            # 不改寫(改寫過的路徑指不到卡),列在 skipped 讓人去角色庫改名。
            skipped.append({"dir": clean_value(entry.name, 80) or "?", "reason": REASON_DIRNAME_CHARS})
            continue
        try:
            raw = _read_bytes(card_path)
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            skipped.append({"dir": entry.name, "reason": REASON_UNREADABLE,
                            "detail": str(err)[:120]})
            continue
        fm = parse_frontmatter(text)
        if fm is None:
            skipped.append({"dir": entry.name, "reason": REASON_NO_FRONTMATTER})
            continue
        uid = fm.get("uid", "")
        if not uid:
            skipped.append({"dir": entry.name, "reason": REASON_MISSING_UID})
            continue
        if not UID_RE.fullmatch(uid):
            skipped.append({"dir": entry.name, "reason": REASON_UID_SHAPE,
                            "detail": "uid=%r 不合 [0-9a-z]{6}" % clean_value(uid, 32)})
            continue
        if uid in owners:
            raise CardRegistryError(
                "uid 撞號:%s 同時是 %r 與 %r 的身份——整份不寫,先去角色庫修"
                % (uid, owners[uid], entry.name))
        owners[uid] = entry.name
        shown_name = entry.name
        if UID_RE.fullmatch(entry.name):
            shown_name = _label_title(Path(vault_root), entry.name) or entry.name
        # the maintainer 10-15:「用 uid 建立,顯示才用 title」⇒ 顯示名順位:labels.json(uid 版面)>
        #   卡上的 name: > 資料夾名。card_ref 仍照資料夾/labels 那一份(舊綁定的比對不變)。
        fm_name = clean_value(fm.get("name"), 80) or ""
        if fm_name and shown_name == entry.name:
            name, truncated = fit_display_name(fm_name)   # 使用者給的全名:不剝代號前綴
        else:
            name, truncated = display_name_of(shown_name)
        default_rk = route_defaults.get(uid)
        if default_rk is not None:
            ok, code, detail = check_against_contract(default_rk, contract)
            if not ok:
                raise CardRegistryError(
                    "uid %s 的 default_route_key %s 過不了契約(%s):%s"
                    % (uid, default_rk, code, detail))
        card = {
            "uid": uid,
            "code": _shape_or_none(fm.get("code"), _CODE_RE),
            "emoji": clean_value(fm.get("emoji"), 8),
            "category": clean_value(fm.get("category"), 32),
            "display_name": name,
            "card_ref": CARD_REF_PREFIX + shown_name,
            "card_path_rel": str(Path(library_folder) / entry.name / CARD_FILE),
            "persona_sha": hashlib.sha256(raw).hexdigest(),
            "card_updated": _shape_or_none(fm.get("updated"), _UPDATED_RE),
            "default_route_key": default_rk,
            # 角色.md 沒這些欄也照樣能讀:投影一律補 12 維 unmeasured,不讀 vault 分數。
            "dims": default_dims(),
        }
        if truncated:
            card["display_name_truncated"] = True
        cards[uid] = card
    return {"cards": cards, "skipped": skipped}


OUTSIDE_LIMIT = 50


def cards_outside_library(vault_root, library_folder, limit=OUTSIDE_LIMIT):
    """知識庫裡、角色庫以外的 角色.md(相對知識庫的路徑,排序,最多 limit 筆)+ 總數。
    10-14(6ddf 走查 K6):scan 只收角色庫那一層,別處的舊卡(例 06-工作區/…/角色.md)安靜地不在索引裡,
    用戶以為建過的角色怎麼綁不上。不收進來(收哪裡由路由契約定),只列出來讓人知道、知道怎麼搬。
    略過 . 開頭的資料夾(.obsidian、.git、.trash)與角色庫本身。"""
    root = Path(vault_root)
    lib = root / library_folder

    def is_library(path):
        # 比「是不是同一個資料夾」,不比路徑字面(審查抓到):macOS 不分大小寫、中文名可能 NFC / NFD 兩種寫法,
        # 契約寫 10-roles 也開得到 10-Roles —— 比字面會把角色庫本身當成「庫外」。
        try:
            return os.path.samefile(str(path), str(lib))
        except OSError:
            return False

    found = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not is_library(here / d))
        if CARD_FILE in filenames:
            found.append(str((here / CARD_FILE).relative_to(root)))
    found.sort()
    return found[:limit], len(found)


def _say_outside(paths, total, library_folder):
    """stderr 一行(走 aib/i18n 四語;字典讀不到就印鍵名)。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cockpit"))
        from i18n_lib import t
        text = t("aib.cards.outside_library", {"n": total, "lib": library_folder,
                                                "paths": ", ".join(paths[:5]) + (" …" if total > 5 else "")})
    except Exception:
        text = "aib.cards.outside_library"
    print("card-registry: " + text, file=sys.stderr)


# ---- 落檔 / 收據 ----------------------------------------------------------------

def _digest_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _digest_file(path):
    try:
        return _digest_bytes(_read_bytes(path))
    except OSError:
        return None


def build_document(result, vault_root, library_folder, now_ms):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_wall_ms": int(now_ms),
        "source": {"vault_root": str(vault_root),
                   "library_route_key": LIBRARY_ROUTE_KEY,
                   "library_folder": str(library_folder)},
        "cards": result["cards"],
        "skipped": result["skipped"],
    }


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def provenance_path(out_path):
    p = Path(out_path)
    return p.with_name(p.name + ".provenance.json")


def cards_fingerprint(doc):
    """只對 cards 本體算指紋(不含 generated_wall_ms),`check` 用它判「內容有沒有變」。"""
    return _digest_bytes(json.dumps(doc["cards"], ensure_ascii=False,
                                    sort_keys=True).encode("utf-8"))


def write_atomic(path, text):
    p = Path(path)
    if p.is_symlink():
        raise CardRegistryError("輸出路徑是 symlink,拒寫:%s" % p)
    if not p.parent.is_dir():
        raise CardRegistryError("輸出目錄不存在(狀態樹是 aib init 的事,本工具不建):%s"
                               % p.parent)
    tmp = p.with_name(p.name + ".tmp.%d" % os.getpid())
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(p))
    finally:
        if tmp.exists():
            tmp.unlink()


def write_registry(out_path, doc, sources):
    text = _dump(doc)
    write_atomic(out_path, text)
    receipt = {
        "record": "card-registry-provenance-v1",
        "cards_json_sha256": _digest_bytes(text.encode("utf-8")),
        "cards_fingerprint": cards_fingerprint(doc),
        "card_count": len(doc["cards"]),
        "skipped_count": len(doc["skipped"]),
        "sources": sources,
    }
    write_atomic(provenance_path(out_path), _dump(receipt))
    return receipt


def resolve_card(cards, ref):
    """給消費端(binding_projection)用:先 uid,再舊 card_ref 橋。回 (entry, legacy)。"""
    if ref in cards:
        return cards[ref], False
    for entry in cards.values():
        if entry.get("card_ref") == ref:
            return entry, True
    return None, False


# ---- CLI -----------------------------------------------------------------------

def _default_out(state_root):
    return Path(state_root) / "identity" / "cards.json"


def _resolve_state_root(explicit):
    if explicit:
        return Path(explicit)
    for var in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(var)
        if v:
            return Path(v)
    return Path(os.path.expanduser("~/.ez2-aib"))


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
    ap = argparse.ArgumentParser(prog="card-registry")
    ap.add_argument("command", choices=["scan", "check", "show"])
    ap.add_argument("ref", nargs="?", help="show 用:uid 或 card_ref")
    ap.add_argument("--vault", help="vault 根(預設 EZ2_VAULT_DIR > %s)" % DEFAULT_VAULT)
    ap.add_argument("--contract", help="路由契約(預設 EZ2_ROUTE_REGISTRY)")
    ap.add_argument("--state-root", help="狀態根(預設 EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)")
    ap.add_argument("--out", help="輸出檔(預設 <state_root>/identity/cards.json)")
    ap.add_argument("--route-defaults",
                    help="uid→default_route_key 表(預設 src/memory/card-route-defaults.json)")
    ap.add_argument("--dry-run", action="store_true", help="scan 只印不寫")
    args = ap.parse_args(argv)

    out_path = Path(args.out) if args.out else _default_out(_resolve_state_root(args.state_root))

    if args.command == "show":
        if not args.ref:
            print("card-registry: " + _ui("aib.cardreg.show_usage"), file=sys.stderr)
            return 2
        try:
            doc = json.loads(_read_bytes(out_path).decode("utf-8"))
        except (OSError, ValueError) as err:
            print("card-registry: " + _ui("aib.cardreg.unreadable", path=out_path, err=err), file=sys.stderr)
            return 2
        entry, legacy = resolve_card(doc.get("cards", {}), args.ref)
        if entry is None:
            print("card-registry: " + _ui("aib.cardreg.not_found", value=repr(args.ref)), file=sys.stderr)
            return 1
        sys.stdout.write(_dump({"card": entry, "matched_by": "legacy_card_ref" if legacy else "uid"}))
        return 0

    try:
        vault = resolve_vault(args.vault)
        # 10-12:新機器(vault 是 aib 預設)沒有契約 ⇒ 套件附的預設契約;有自己 vault 的照舊要明給
        # 10-15:沒給 --contract 走唯一來源(SPEC > ez2-ai main > REGISTRY > 隨附/預設),不再自己挑
        contract_path = args.contract or _RR.default_contract_spec()
        contract = load_contract(contract_path)
        if contract is None:
            raise CardRegistryError("路由契約讀不到(--contract 或 EZ2_ROUTE_REGISTRY);"
                                    "拒絕在看不到規則的情況下掃描")
        library_folder = library_folder_of(contract, vault)
        defaults_path = args.route_defaults
        if defaults_path is None:
            candidate = Path(__file__).resolve().parent / "card-route-defaults.json"
            defaults_path = str(candidate) if candidate.is_file() else None
        route_defaults = load_route_defaults(defaults_path)
        result = scan(vault, library_folder, contract, route_defaults)
    except CardRegistryError as err:
        print("card-registry: %s" % err, file=sys.stderr)
        return 2

    doc = build_document(result, vault, library_folder, int(time.time() * 1000))
    sources = {
        "vault_root": str(vault),
        "library_folder": str(library_folder),
        # 契約來源可能是 git: 規格(不走檔案系統),雜湊交給 route_register 同一條規則算
        "route_contract": {"path": contract_path,
                           "sha256": contract_digest(contract_path),
                           "route_count": len(contract.get("routes", []))},
        "route_defaults": {"path": defaults_path,
                           "sha256": _digest_file(defaults_path) if defaults_path else None,
                           "count": len(route_defaults)},
        "generator": {"path": str(Path(__file__).resolve()),
                      "sha256": _digest_file(Path(__file__).resolve())},
    }
    summary = {"cards": len(doc["cards"]), "skipped": doc["skipped"],
               "fingerprint": cards_fingerprint(doc), "out": str(out_path)}
    outside, outside_total = cards_outside_library(vault, library_folder)
    if outside_total:
        summary["library_folder"] = str(library_folder)
        summary["outside_library"] = outside
        summary["outside_library_count"] = outside_total
        _say_outside(outside, outside_total, library_folder)

    if args.command == "check":
        try:
            current = json.loads(_read_bytes(out_path).decode("utf-8"))
            receipt = json.loads(_read_bytes(provenance_path(out_path)).decode("utf-8"))
        except (OSError, ValueError) as err:
            print("card-registry: " + _ui("aib.cardreg.compare_unreadable", err=err), file=sys.stderr)
            return 2
        on_disk_sha = _digest_file(out_path)
        edited_by_hand = receipt.get("cards_json_sha256") != on_disk_sha
        drift = cards_fingerprint(current) != summary["fingerprint"] if "cards" in current else True
        added = sorted(set(doc["cards"]) - set(current.get("cards", {})))
        removed = sorted(set(current.get("cards", {})) - set(doc["cards"]))
        changed = sorted(u for u in set(doc["cards"]) & set(current.get("cards", {}))
                         if doc["cards"][u] != current["cards"][u])
        summary.update({"drift": drift, "edited_by_hand": edited_by_hand,
                        "added": added, "removed": removed, "changed": changed})
        sys.stdout.write(_dump(summary))
        return 1 if (drift or edited_by_hand) else 0

    # scan
    if args.dry_run:
        summary["dry_run"] = True
        sys.stdout.write(_dump(summary))
        return 0
    try:
        receipt = write_registry(out_path, doc, sources)
    except (CardRegistryError, OSError) as err:
        print("card-registry: %s" % err, file=sys.stderr)
        return 2
    summary["provenance"] = str(provenance_path(out_path))
    summary["cards_json_sha256"] = receipt["cards_json_sha256"]
    sys.stdout.write(_dump(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
