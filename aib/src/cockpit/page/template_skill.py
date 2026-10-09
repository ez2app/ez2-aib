#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""275(b):首頁風格 S1–S8 ↔ 固定 skill,與「skill 只准動槽位」的守衛。

規格:`docs/spec/PAGE-TEMPLATE-PICKER-275-A-v1.md` §一(編號表)、§二(槽位表);
     `docs/spec/PAGE-TEMPLATE-PICKER-275-A-v2.md`(沿用 v1 的兩張表);
     大綱第 275 列 (b):「AIB 端讀套版編號載入對應 skill,只發 set_profile / set_theme /
     upsert_block / reorder / remove 五種操作到 262(d) 寫入 API」。

這支檔是**唯一真相源**的三件事:
  1. S1–S8 ↔ theme_id ↔ 首頁人設/文案在 styles.py 一張表;shape 不參與載入。
  2. 五種 skill 操作名(`SKILL_OPS`)與每種操作准動的鍵(`SLOT_KEYS`)—— 這就是槽位白名單。
     CSS / class 名 / style / keyframes / html 這類東西**沒有任何一種操作收得下**,
     不是靠黑名單擋,是白名單裡根本沒有那個鍵(黑名單只是第二道,給錯誤訊息點名用)。
  3. skill 操作 → 寫入 API 操作的對照(`API_ROUTE`)。寫入 API v1(規格 §5.1)只有
     五種區塊變換;`set_profile` / `set_theme` 在 v1 **沒有對應端點**,
     對照表裡明寫 `None`,翻譯時具名拒絕、不偷改成別的 op —— 等 262(d) 補了端點,
     改這張表一行就通(交件單第一題)。

★ 8 份 skill 檔 `docs/skills/page-style-Sn.md` 由 `tools/page/gen_template_skills.py`
  用同一份模板從這裡的表生成;模板與生成器都在倉裡,重生後 diff 必須為空(尺 S1)。
"""
from __future__ import annotations

import os
import re
import styles

# ---- 首頁八格 --------------------------------------------------------------

THEME_ORDER = tuple(styles.STYLES[sid]["theme_id"] for sid in styles.STYLE_ORDER)
SHAPE_ORDER = ("rounded", "rectangle", "pill")  # 舊頁仍可能存這三值,但不參與風格身份

STYLE_ID_RE = re.compile(r"^S[1-8]$")
SKILL_FILE = "page-style-%s.md"
SKILL_DIR = os.path.join("docs", "skills")


class SkillError(Exception):
    """給人看的一句話;每一種拒絕都點名是哪個 op、哪個鍵。"""


def id_for(theme, shape=None):
    """由 theme_id 找 S1–S8;shape 只為讀舊狀態相容,不影響結果。"""
    style = styles.for_theme(theme)
    if style is None:
        raise SkillError("色系 %r 不在首頁 S1–S8 風格表裡" % (theme,))
    return style["id"]


def parse_id(style_id):
    """`S1` → 對應首頁風格;舊 Tnn 不再是可載入 skill。"""
    if not isinstance(style_id, str) or not STYLE_ID_RE.match(style_id):
        raise SkillError("風格編號 %r 不合法:要是 S1–S8" % (style_id,))
    return styles.STYLES[style_id]


# ---- 槽位白名單(v1 §二) ------------------------------------------------------

SKILL_OPS = ("set_profile", "set_theme", "upsert_block", "reorder", "remove")

# 316 v1.1(RL-114):avatar / cover 的值 = `aib page upload` 印出的 asset_id(這一頁自己圖庫裡的;PAGE 驗「在不在」),"" = 清空;
#   翻成 PUT /profile 的 avatar_asset_id / cover_asset_id。
PROFILE_SLOTS = ("display_name", "bio", "status_text", "avatar", "cover", "socials", "language")   # 317 第 19a 項:+language(頁面語言)
# 區塊層:產品真相是 ez2-ai `shared/ez2-page-editable-workspace.js` 的 EDITABLE_BLOCK_TYPES —— 276(b)
# (r18 候選 0231b889)起由 design-pack BLOCKS.contract_types 派生的 10 種;`form` 沒有渲染器、不在裡面。
# 275 v1 §二 槽位表寫的 video 曾是錯的(RL-042 裁 cta),276(b) 之後 video 真的進了表。
# ★ 這張 Python 表與 JS 表沒有跨倉守衛(RL-042 增補 §三 登債):改型別要兩邊一起動,尺 S3 釘的是這裡的 10 種。
# 317 第 5 項(RL-115):+heading(標題 h2/h3)/ divider(分隔線)。
# 289 五(RL-092/093/094;艦長 09-18 04:59 派):+place(Google 商家格)——AIB 端取回種入走同一條 upsert_block,payload 14 鍵抄
#   ez2-ai block-contract placeErrors 的 PLACE_KEYS(r30 517bb9bf);必填七欄 = 沒有 optional() 保護的那些。
# 317 第 4 項(SPEC-317-batch1 §五;艦長 09-18 11:17 派):+gallery(相簿 / 輪播)——payload 只有兩鍵 items / style,抄 ez2-ai
#   block-contract 的 gallery 分支(r43 b74d4255):items 2–12 張、每張 {asset_id, alt, href|null}(asset_id = `aib page upload` 印的),
#   style = carousel / grid;張數、每張的鍵、href 只收 https 都由契約守(這裡只守頂層「不得多」)。
# 317 第二批(艦長 09-18 11:30 派):+faq / testimonial / countdown / booking / file / qr / form 七種——PAGE 第二批(r32–r40)早有渲染器,AIB 端
#   一直沒進白名單 = AI 種不了;鍵集抄 r43 契約各分支 exactKeys,必填 = 沒有 `!= null` 保護的那些(qr 一個必填都沒有:caption 選填)。
#   form 自 317 第 18 項起有渲染器 + 提交端點,「form 沒有渲染器不收」那句作廢;fields[] 的巢狀鍵 field_id / label / type / required 與敏感欄由契約守。
BLOCK_TYPES = ("text", "image", "link", "cta", "music", "video", "shop", "event", "mail", "tip", "heading", "divider", "place", "gallery",
               "faq", "testimonial", "countdown", "booking", "file", "qr", "form")
# 每種區塊 payload 准帶的鍵,抄自 ez2-ai `shared/ez2-page-block-contract.js` `payloadErrors()`
# 的 exactKeys(r18 0231b889)。那邊是 exactKeys(整組相等),這裡只守「不得多」;少了由契約回 422。
# cta 的 action 是巢狀物件 {type, href, target_id};巢狀鍵不在這張表裡,由契約守。
BLOCK_PAYLOAD_KEYS = {
    "text":  ("text", "format"),
    "image": ("asset_id", "alt", "href"),
    "link":  ("label", "href", "rel", "sublabel", "icon", "thumbnail_asset_id", "emphasis"),   # 317 第 6/7 項:小圖示 / 縮圖 / 強調(PAGE r41 exactKeys)
    "cta":   ("label", "action", "sublabel"),
    "music": ("provider", "track_id", "title", "href", "sublabel"),
    "video": ("provider", "video_id", "title", "href", "autoplay", "thumbnail_url", "uploaded_at",
              "description", "duration_seconds"),
    "shop":  ("provider", "product_id", "title", "href", "price_display", "price_amount", "currency",
              "availability", "image_asset_id", "button_label"),
    "event": ("title", "starts_at", "ends_at", "location", "href", "timezone"),
    "mail":  ("list_id", "title", "consent_text", "button_label"),
    "tip":   ("provider", "href", "title", "suggested_amounts", "currency"),
    "heading": ("text", "level", "align"),
    "divider": ("style",),
    "place": ("place_id", "name", "address", "phone", "hours", "rating", "review_count", "reviews", "map_asset_id",
              "photo_asset_ids", "maps_url", "nav_url", "review_url", "show_review_button"),
    "gallery": ("items", "style"),
    "faq": ("items", "open_first"),
    "testimonial": ("quote", "author", "role", "rating", "avatar_asset_id"),
    "countdown": ("title", "target_at", "done_text"),
    "booking": ("provider", "href", "title", "sublabel", "duration_minutes", "next_slot_text"),
    "file": ("asset_id", "label", "size_bytes", "note"),
    "qr": ("caption",),
    "form": ("form_id", "title", "fields"),
}

# 288:每種區塊**缺了就 422** 的鍵(抄同一支 payloadErrors():沒寫 `!= null` 保護的那些)。清單 docs/skills/page-intake-checklist.md
#   第 4 節的表要與這張逐字相等(尺 I1 從 markdown 抽出來比,清單過期就紅)。規格 §(a) 列的是給人看的短版(少了 format / rel /
#   autoplay / button_label / suggested_amounts 這些 AI 自己會填預設的技術欄);這張是契約真相,清單裡另外標「AI 代填」。
BLOCK_REQUIRED_KEYS = {
    "text":  ("text", "format"),
    "image": ("asset_id", "alt"),
    "link":  ("label", "href", "rel"),
    "cta":   ("label", "action"),
    "music": ("provider", "track_id", "title", "href"),
    "video": ("provider", "video_id", "title", "href", "autoplay"),
    "shop":  ("provider", "product_id", "title", "href", "price_display", "currency"),
    "event": ("title", "starts_at"),
    "mail":  ("list_id", "title", "consent_text", "button_label"),
    "tip":   ("provider", "href", "title", "suggested_amounts", "currency"),
    "heading": ("text", "level", "align"),
    "divider": ("style",),
    "place": ("place_id", "name", "address", "map_asset_id", "maps_url", "nav_url", "show_review_button"),
    "gallery": ("items", "style"),
    "faq": ("items", "open_first"),
    "testimonial": ("quote", "author"),
    "countdown": ("title", "target_at"),
    "booking": ("provider", "href", "title"),
    "file": ("asset_id", "label"),
    "qr": (),
    "form": ("form_id", "title", "fields"),
}
if set(BLOCK_REQUIRED_KEYS) != set(BLOCK_PAYLOAD_KEYS) or any(
        set(BLOCK_REQUIRED_KEYS[t]) - set(BLOCK_PAYLOAD_KEYS[t]) for t in BLOCK_REQUIRED_KEYS):
    raise RuntimeError("BLOCK_REQUIRED_KEYS 不是 BLOCK_PAYLOAD_KEYS 的子集")

# 每種操作的鍵集(op 本身之外)。`upsert_block` 的 `payload` 再由 BLOCK_PAYLOAD_KEYS 管一層。
SLOT_KEYS = {
    "set_profile":  frozenset(PROFILE_SLOTS),
    "set_theme":    frozenset(("theme_id",)),
    # 289 〇 / 292:provenance{source,url,fetched_at,ttl_days} 是記錄層可選欄(取回產生的顆才有),原樣傳給寫入 API 的 create / update
    "upsert_block": frozenset(("block_id", "block_type", "visibility", "payload", "provenance")),
    "reorder":      frozenset(("order",)),
    "remove":       frozenset(("block_id",)),
}

# 第二道:這些鍵在任何深度出現都點名拒絕(白名單已經收不下它們;這一道只為了錯誤訊息
# 能說「你在改 CSS」而不是「未知鍵」)。v1 §二「不可改:CSS、版面結構、動畫庫」。
FORBIDDEN_KEYS = frozenset((
    "css", "style", "styles", "class", "class_name", "className", "classes",
    "keyframes", "animation", "html", "script", "template", "layout", "tokens",
))

# skill 操作 → 寫入 API 操作(規格 §5.1 五種)。None = v1 沒有這條路,翻譯時具名拒絕。
API_ROUTE = {
    "upsert_block": ("create", "update"),   # 沒 block_id ⇒ create;有 ⇒ update(整份取代)
    "reorder":      ("reorder",),
    "remove":       ("delete",),
    # 262(d) 增補 §二(RL-042;PAGE 端 r16 起有):這兩種不進 /draft 批次,各走自己的 PUT 端點(寫註冊表、立刻生效)。
    "set_profile":  ("PUT /profile",),
    "set_theme":    ("PUT /appearance",),
}
DRAFT_OPS = ("upsert_block", "reorder", "remove")       # 進 POST /draft 批次(整批原子)的三種
REGISTRY_OPS = ("set_profile", "set_theme")             # 各走 PUT 端點的兩種(不經草稿)
# PUT /profile 收的欄(PAGE page-api putProfile 的 exact 集合;316 v1.1 起多 avatar_asset_id / cover_asset_id)。
PROFILE_PUT_FIELDS = ("display_name", "bio", "status_text", "socials", "avatar_asset_id", "cover_asset_id", "language")   # 317 第 19a 項:+language
# 317 第 19a 項:頁面語言四值(PAGE i18n SUPPORTED_LOCALES);"" = 回預設 zh-TW。不在這四個裡在這裡就具名拒。
PAGE_LANGUAGES = ("zh-TW", "en", "ja", "ko")
# asset_id 的形狀(抄 ez2-ai asset-storage ASSET_ID_RE);形狀不對在這裡就具名拒,不把「img_1」這種猜的字送去吃 400。
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
PROFILE_IMAGE_SLOTS = (("avatar", "avatar_asset_id"), ("cover", "cover_asset_id"))


def _asset_ref(slot, value):
    if value == "":
        return ""
    if not isinstance(value, str) or not ASSET_ID_RE.match(value):
        raise SkillError("set_profile 的 %s 要填 `aib page upload` 印出的 asset_id(asset_… 那串),或 \"\" 清空;拿到的是 %r,這一批沒送"
                         % (slot, value))
    return value


def split_skill_ops(ops):
    """`(registry_ops, draft_ops)`:順序各自保留。**先過 validate_skill_ops**。"""
    validate_skill_ops(ops)
    return ([op for op in ops if op["op"] in REGISTRY_OPS], [op for op in ops if op["op"] in DRAFT_OPS])


def profile_request(ops):
    """多個 set_profile 合成一份 PUT /profile 本體(後者蓋前者;沒有 set_profile 回 None)。
    `avatar` / `cover` 翻成 avatar_asset_id / cover_asset_id(值要是 asset_id 形狀或 "",否則具名拒);
    `socials` 每個元素只准 {platform, url}(PAGE 的 exact 集合)。刀 K1/K9(拿掉 avatar 那行)⇒ P2/P7/S9 必紅。"""
    fields = {}
    for op in ops:
        if op.get("op") != "set_profile":
            continue
        if "avatar" in op:
            fields["avatar_asset_id"] = _asset_ref("avatar", op["avatar"])
        if "cover" in op:
            fields["cover_asset_id"] = _asset_ref("cover", op["cover"])
        if "language" in op and op["language"] != "" and op["language"] not in PAGE_LANGUAGES:
            raise SkillError("set_profile 的 language 只收 %s(或 \"\" 回預設 zh-TW);拿到的是 %r,這一批沒送" % (" / ".join(PAGE_LANGUAGES), op["language"]))
        for key in PROFILE_PUT_FIELDS:
            if key in op and key not in ("avatar_asset_id", "cover_asset_id"):
                fields[key] = op[key]
    if not fields:
        return None
    if "socials" in fields:
        socials = fields["socials"]
        if not isinstance(socials, list) or any(not isinstance(item, dict) or set(item) != {"platform", "url"}
                                                or not all(isinstance(item[k], str) for k in ("platform", "url"))
                                                for item in socials):
            raise SkillError("set_profile 的 socials 每個元素要是 {platform, url} 兩個字串鍵(PAGE 只收這個形狀)")
    return fields


def appearance_request(ops, current_shape):
    """最後一個 set_theme 決定 theme_id;shape 用現值原樣帶過(skill 不動形狀)。沒有 set_theme 回 None。
    現值讀不到 ⇒ 具名拒:兩欄齊給是 PAGE 的契約,這裡不猜 rounded 去把店主的形狀改掉。"""
    themes = [op["theme_id"] for op in ops if op.get("op") == "set_theme"]
    if not themes:
        return None
    if current_shape not in SHAPE_ORDER:
        raise SkillError("讀不到目前的形狀(appearance.shape_id),不能只改色系不帶形狀;set_theme 這一批沒送")
    return {"theme_id": themes[-1], "shape_id": current_shape}


def _walk_keys(value, path, hits, allow_at=frozenset()):
    if isinstance(value, dict):
        for key, inner in value.items():
            here = (path + "." + str(key)) if path else str(key)
            if key in FORBIDDEN_KEYS and here not in allow_at:
                hits.append(here)
            _walk_keys(inner, here, hits, allow_at)
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _walk_keys(inner, "%s[%d]" % (path, i), hits, allow_at)


def _contract_keys_that_look_forbidden(op):
    """317 第 4 項(a seat 11:3x 現查):divider(317 第 5 項)與 gallery 的契約鍵就叫 `style`(值是 line / carousel 這種
    **選項**,不是 CSS),而第二道 FORBIDDEN_KEYS 在任何深度都點名 ⇒ 75fb60c6 起 divider 從 skill 根本建不出來(尺只比鍵集沒真建過)。
    只放行「這型別 payload 白名單裡本來就有的那個頂層鍵」,深一層(items[0].style)照樣拒。刀 K3e:回傳空集合 ⇒ S3d / S3e 必紅。"""
    if op.get("op") != "upsert_block":
        return frozenset()
    return frozenset("payload." + key for key in BLOCK_PAYLOAD_KEYS.get(op.get("block_type"), ()) if key in FORBIDDEN_KEYS)


def validate_skill_ops(ops):
    """五種操作名之外拒;鍵集超出槽位白名單拒。回原 list(不改內容)。"""
    if not isinstance(ops, list) or not ops:
        raise SkillError("skill ops 要是一個非空的陣列")
    for i, op in enumerate(ops):
        where = "第 %d 個 op" % (i + 1)
        if not isinstance(op, dict):
            raise SkillError("%s 不是物件" % where)
        kind = op.get("op")
        if kind not in SKILL_OPS:
            raise SkillError("%s 的 `op` %r 不是五種之一(%s);set_shape 不由 skill 發送"
                             % (where, kind, "/".join(SKILL_OPS)))
        forbidden = []
        _walk_keys(op, "", forbidden, _contract_keys_that_look_forbidden(op))
        if forbidden:
            raise SkillError("%s(%s)想動槽位外的東西:%s —— CSS、class、版面與動畫庫只能選不能改"
                             % (where, kind, ", ".join(forbidden)))
        extra = sorted(set(op) - {"op"} - SLOT_KEYS[kind])
        if extra:
            raise SkillError("%s(%s)帶了槽位外的鍵:%s;准的只有 %s"
                             % (where, kind, ", ".join(extra), ", ".join(sorted(SLOT_KEYS[kind]))))
        if kind == "set_theme" and op.get("theme_id") not in THEME_ORDER:
            raise SkillError("%s 的 theme_id %r 不在色系序裡" % (where, op.get("theme_id")))
        if kind == "upsert_block":
            block_type = op.get("block_type")
            if block_type not in BLOCK_TYPES:
                raise SkillError("%s 的 block_type %r 不在型別表裡(%s)"
                                 % (where, block_type, "/".join(BLOCK_TYPES)))
            payload = op.get("payload")
            if not isinstance(payload, dict):
                raise SkillError("%s(upsert_block)的 payload 要是物件" % where)
            extra_payload = sorted(set(payload) - set(BLOCK_PAYLOAD_KEYS[block_type]))
            if extra_payload:
                raise SkillError("%s(upsert_block/%s)payload 帶了槽位外的鍵:%s"
                                 % (where, block_type, ", ".join(extra_payload)))
    return ops


def to_api_ops(ops):
    """三種草稿 skill 操作 → 規格 §5.1 的 API 操作。**先過 validate_skill_ops**。

    set_profile / set_theme 不進這裡(它們各走 PUT 端點,由 `split_skill_ops` 先分出去);
    混進來就具名拒,不偷偷丟掉:一批 ops 在 API 端是原子的(§5.1「整批原子」),
    翻譯層不該靜默改變一批的內容。
    """
    validate_skill_ops(ops)
    registry = [op["op"] for op in ops if op["op"] in REGISTRY_OPS]
    if registry:
        raise SkillError(
            "這批裡有 %s:它們不進 /draft 批次,各走 PUT /profile、PUT /appearance(先用 split_skill_ops 分開);"
            "這一批整批沒送" % ", ".join(sorted(set(registry))))
    out = []
    for op in ops:
        kind = op["op"]
        if kind == "upsert_block":
            if op.get("block_id"):
                # RL-145 F3:update 只在 op 有給 visibility 時帶(PAGE 的 update 沒給就不動);之前一律補 public,任何「只改 payload」的 update 都會把 members / hidden 顆改公開
                api_op = {"op": "update", "block_id": op["block_id"], "payload": op["payload"]}
                if "visibility" in op:
                    api_op["visibility"] = op["visibility"]
            else:
                api_op = {"op": "create", "block_type": op["block_type"],
                          "visibility": op.get("visibility", "public"),
                          "payload": op["payload"]}
            if "provenance" in op:
                api_op["provenance"] = op["provenance"]      # 292:來源標示原樣過(PAGE 契約驗形狀,這裡不重驗)
            out.append(api_op)
        elif kind == "reorder":
            out.append({"op": "reorder", "order": list(op["order"])})
        elif kind == "remove":
            out.append({"op": "delete", "block_id": op["block_id"]})
    return out


# ---- 載入 skill:只跟 state 的 theme_id 走 ------------------------

def template_for_state(state):
    """state 有 theme_id 即可;shape_id 不參與。讀不到就具名停手,不猜 S1。"""
    if not isinstance(state, dict):
        raise SkillError("讀不到套版狀態(不是物件)")
    src = state.get("appearance") if isinstance(state.get("appearance"), dict) else state
    theme = src.get("theme_id")
    if not theme:
        raise SkillError("讀不到風格:狀態裡沒有 theme_id;要指名就用 --template S1–S8")
    return id_for(theme)


def skill_path(style_id, root):
    parse_id(style_id)
    return os.path.join(root, SKILL_DIR, SKILL_FILE % style_id)


def load_skill(state, root, template_id=None):
    """回 `(Sn, skill 全文)`。明示編號優先於 state。"""
    sid = template_id or template_for_state(state)
    parse_id(sid)
    path = skill_path(sid, root)
    if not os.path.isfile(path):
        raise SkillError("風格 %s 的 skill 檔不在:%s(用 tools/page/gen_template_skills.py 重生)"
                         % (sid, path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("# 風格 %s" % sid):
        raise SkillError("skill 檔 %s 的標題不是 %s,拿錯檔了" % (path, sid))
    return sid, text
