#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 〇 共用骨架:取回轉接器介面 Snapshot + 只產缺欄的合併器 + 292 來源標示(provenance)。

規格 pending/SPEC-289-social-prefetch-import-37ca8-0918.md §〇。所有轉接器只做一件事:`fetch(url, http, clock) -> Snapshot`;
合併器只做一件事:`merge_into_ops(snapshot, current_state) -> (skill ops, notes)`——**只產缺欄**(current 有值的欄不進 ops,
印「已有,跳過」),每顆取回產生的區塊帶 `provenance:{source, url, fetched_at, ttl_days}`(PAGE 記錄層可選欄;292)。
所有 ops 都是草稿路(set_profile / set_theme 走 PUT、upsert_block 走 POST /draft);這支檔沒有 publish 這個字以外的任何發布動作。

Snapshot = {
  "source":  {"kind": str, "url": str|None, "fetched_at": ISO, "ttl_days": int|None, "attribution": str|None},
  "profile": {"display_name"?, "bio"?, "status_text"?, "avatar_url"?, "socials"?: [{"platform","url"}]},
  "blocks":  [{"block_type": str, "payload": dict, "image_urls"?: [str]}],
  "warnings": [str],
}
內建一個轉接器 `snapshot_file`(本機 JSON 檔 / file://):讓人或 AI 手寫一份 Snapshot、也讓每站的 fixture 能原樣回放;
四站 link-in-bio 等在包一以後各自 `register_adapter`。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

SOURCE_KEYS = frozenset(("kind", "url", "fetched_at", "ttl_days", "attribution"))
PROFILE_KEYS = frozenset(("display_name", "bio", "status_text", "avatar_url", "socials"))
BLOCK_KEYS = frozenset(("block_type", "payload", "image_urls"))
SNAPSHOT_KEYS = frozenset(("source", "profile", "blocks", "warnings"))
KIND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")          # 與 PAGE 契約 PROVENANCE_SOURCE_RE 同一條
TTL_MAX = 3650
SNAPSHOT_FILE_MAX = 1024 * 1024


class SourceError(Exception):
    """具名停手:訊息就是給人看的那一句。"""


def now_iso(clock=None):
    return (clock or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---- Snapshot 形狀 ------------------------------------------------------------------------------------------------

def validate_snapshot(snapshot):
    """回錯誤字串清單(空 = 合法)。不猜、不補:形狀錯就整份拒。"""
    errors = []
    if not isinstance(snapshot, dict):
        return ["snapshot 不是物件"]
    extra = sorted(set(snapshot) - SNAPSHOT_KEYS)
    if extra:
        errors.append("snapshot 多了鍵:%s" % ", ".join(extra))
    source = snapshot.get("source")
    if not isinstance(source, dict):
        errors.append("source 要是物件")
    else:
        if set(source) != SOURCE_KEYS:
            errors.append("source 的鍵要恰好是 %s" % ", ".join(sorted(SOURCE_KEYS)))
        else:
            if not isinstance(source["kind"], str) or not KIND_RE.match(source["kind"]):
                errors.append("source.kind 要是小寫英數(≤32)")
            if not (source["url"] is None or (isinstance(source["url"], str) and source["url"][:8] in ("https://", "http://"))):
                errors.append("source.url 要是 http(s) 網址或 null")
            if not isinstance(source["fetched_at"], str) or not _iso(source["fetched_at"]):
                errors.append("source.fetched_at 要是 ISO 時刻")
            ttl = source["ttl_days"]
            if not (ttl is None or (isinstance(ttl, int) and not isinstance(ttl, bool) and 0 <= ttl <= TTL_MAX)):
                errors.append("source.ttl_days 要是 null 或 0–%d 的整數" % TTL_MAX)
            if not (source["attribution"] is None or isinstance(source["attribution"], str)):
                errors.append("source.attribution 要是字串或 null")
    profile = snapshot.get("profile")
    if not isinstance(profile, dict):
        errors.append("profile 要是物件(可空)")
    else:
        extra = sorted(set(profile) - PROFILE_KEYS)
        if extra:
            errors.append("profile 多了鍵:%s(准的:%s)" % (", ".join(extra), ", ".join(sorted(PROFILE_KEYS))))
        for key in ("display_name", "bio", "status_text", "avatar_url"):
            if key in profile and not isinstance(profile[key], str):
                errors.append("profile.%s 要是字串" % key)
        socials = profile.get("socials")
        if socials is not None and (not isinstance(socials, list) or any(
                not isinstance(s, dict) or set(s) != {"platform", "url"} or not all(isinstance(s[k], str) for k in s) for s in socials)):
            errors.append("profile.socials 每個元素要是 {platform, url} 兩個字串鍵")
    blocks = snapshot.get("blocks")
    if not isinstance(blocks, list):
        errors.append("blocks 要是陣列(可空)")
    else:
        for i, block in enumerate(blocks):
            where = "blocks[%d]" % i
            if not isinstance(block, dict) or not (set(block) - {"image_urls"}) == {"block_type", "payload"} or set(block) - BLOCK_KEYS:
                errors.append("%s 要是 {block_type, payload, image_urls?}" % where)
                continue
            if not isinstance(block["block_type"], str) or not isinstance(block["payload"], dict):
                errors.append("%s 的 block_type 要是字串、payload 要是物件" % where)
            if "image_urls" in block and (not isinstance(block["image_urls"], list) or not all(isinstance(u, str) for u in block["image_urls"])):
                errors.append("%s 的 image_urls 要是字串陣列" % where)
    warnings = snapshot.get("warnings")
    if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        errors.append("warnings 要是字串陣列(可空)")
    return errors


def _iso(text):
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def provenance_of(snapshot):
    """Snapshot.source → PAGE 記錄層的 provenance(292)。"""
    source = snapshot["source"]
    return {"source": source["kind"], "url": source["url"], "fetched_at": source["fetched_at"], "ttl_days": source["ttl_days"]}


# ---- 轉接器登記 ----------------------------------------------------------------------------------------------------

_ADAPTERS = []   # [(kind, matches(url)->bool, fetch(url, http, clock)->Snapshot, explicit_only)]


def register_adapter(kind, matches, fetch, explicit_only=False):
    """explicit_only(289 (d) 網站爬取):只在**用戶自己給的網址**(`aib page import <url>`)才認;搜尋回退 (h) 那種機器挑出來的候選
    `adapter_for(url)` 預設看不到它——通用網站轉接器什麼網域都認得,不擋就會去爬別人的站。"""
    if not KIND_RE.match(kind):
        raise ValueError("adapter kind %r 不合法" % kind)
    _ADAPTERS[:] = [a for a in _ADAPTERS if a[0] != kind]
    _ADAPTERS.append((kind, matches, fetch, bool(explicit_only)))


def adapters():
    return [kind for kind, _m, _f, _e in _ADAPTERS]


def adapter_for(url, explicit=False):
    """哪個轉接器認這條網址;沒有 ⇒ None(呼叫端具名拒,零請求)。explicit=True = 這條網址是用戶親手給的。"""
    for kind, matches, fetch, explicit_only in _ADAPTERS:
        if explicit_only and not explicit:
            continue
        try:
            hit = matches(url)
        except Exception:      # 判別函式自己壞了不算命中
            hit = False
        if hit:
            return kind, fetch
    return None


def fetch_snapshot(url, http=None, clock=None, explicit=False):
    """挑轉接器 → 抓 → 驗形狀。沒有轉接器 / 形狀錯都是 SourceError(具名停手)。"""
    found = adapter_for(url, explicit=explicit)
    if not found:
        host = urlparse(url).netloc if isinstance(url, str) else ""
        raise SourceError("沒有 %s 的轉接器(准的:%s);一個請求都沒發" % (host or repr(url), ", ".join(adapters()) or "無"))
    kind, fetch = found
    snapshot = fetch(url, http, clock)
    errors = validate_snapshot(snapshot)
    if errors:
        raise SourceError("轉接器 %s 回的快照形狀不對:%s" % (kind, "; ".join(errors)))
    if snapshot["source"]["kind"] != kind:
        raise SourceError("轉接器 %s 回的快照 source.kind 是 %r,不一致" % (kind, snapshot["source"]["kind"]))
    return snapshot


# ---- robots.txt(289 (d) 網站爬取要守;(h) 搜尋回退對 DDG 也是同一支)-----------------------------------------------------------

def robots_allows(robots_text, path, agent="ez2-aib-page-import"):
    """最長前綴優先(RFC 9309):找適用的 User-agent 群(自報 UA 的產品名命中 > `*`),Allow/Disallow 比最長者;沒規則 / 空檔 ⇒ 准
    (§2.3.1:沒有 robots 就是全准;伺服器錯 5xx 由呼叫端當全禁)。"""
    if not isinstance(robots_text, str) or not robots_text.strip():
        return True
    product = agent.lower()
    groups, current, seen_rule = [], None, False
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if current is None or seen_rule:
                current = {"agents": [], "rules": []}
                groups.append(current)
                seen_rule = False
            current["agents"].append(value.lower())
        elif field in ("allow", "disallow") and current is not None:
            current["rules"].append((field, value))
            seen_rule = True
    chosen = next((g for g in groups if any(a and a != "*" and a in product for a in g["agents"])), None)
    if chosen is None:
        chosen = next((g for g in groups if "*" in g["agents"]), None)
    if chosen is None:
        return True
    best = None
    for field, value in chosen["rules"]:
        if value == "":
            continue                     # 空 Disallow = 全准,不算規則
        pattern = "^" + re.escape(value).replace(r"\*", ".*")
        if pattern.endswith(r"\$"):
            pattern = pattern[:-2] + "$"
        if re.match(pattern, path):
            if best is None or len(value) > len(best[1]) or (len(value) == len(best[1]) and field == "allow"):
                best = (field, value)
    return best is None or best[0] == "allow"


# ---- 內建轉接器:本機 Snapshot JSON 檔 -------------------------------------------------------------------------------

def _snapshot_file_path(url):
    if url.startswith("file://"):
        return url[len("file://"):]
    return url


def snapshot_file_matches(url):
    if not isinstance(url, str):
        return False
    return url.startswith("file://") or (url.endswith(".json") and not url.startswith(("http://", "https://")))


def snapshot_file_fetch(url, http=None, clock=None):
    path = _snapshot_file_path(url)
    try:
        if os.path.getsize(path) > SNAPSHOT_FILE_MAX:
            raise SourceError("快照檔 %s 超過 1MB" % path)
        with open(path, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except OSError as exc:
        raise SourceError("快照檔讀不到 %s:%s" % (path, exc))
    except ValueError as exc:
        raise SourceError("快照檔 %s 不是合法 JSON:%s" % (path, exc))
    if isinstance(snapshot, dict) and isinstance(snapshot.get("source"), dict) and snapshot["source"].get("kind") in (None, "snapshot_file"):
        # 手寫的檔可以不填 kind / fetched_at:kind 固定、時刻用現在(其餘鍵要自己寫齊,形狀由 validate_snapshot 守)
        snapshot["source"]["kind"] = "snapshot_file"
        if not snapshot["source"].get("fetched_at"):
            snapshot["source"]["fetched_at"] = now_iso(clock)
    return snapshot


register_adapter("snapshot_file", snapshot_file_matches, snapshot_file_fetch)


# ---- 社群列:PAGE 的平台字彙(docs/skills/page-intake-checklist.md §2 同一張表)-----------------------------------------
# 各站自己的鍵(linktree 的 INSTAGRAM / linkgoods 的 fbUrl …)一律不直接送 PAGE:PAGE 只收 ig/line/fb/youtube/threads/douyin/
#   xhs/whatsapp/wechat 而且 url 要在該平台的網域(PAGE_SOCIAL_PLATFORM_UNKNOWN / HOST_NOT_ALLOWED 整個 PUT /profile 會被拒)。
#   所以平台由**網址的網域**決定、一個平台只留第一條、認不得的網域回給呼叫端當提醒。
PAGE_SOCIAL_HOSTS = (
    ("ig", ("instagram.com",)), ("line", ("lin.ee", "line.me")), ("fb", ("facebook.com", "fb.com")),
    ("youtube", ("youtube.com", "youtu.be")), ("threads", ("threads.net", "threads.com")), ("douyin", ("douyin.com", "tiktok.com")),
    ("xhs", ("xiaohongshu.com", "xhslink.com")), ("whatsapp", ("wa.me", "whatsapp.com")), ("wechat", ("weixin.qq.com", "wechat.com")),
)


def page_platform_for(url):
    """網址 → PAGE 平台鍵;不是 https 或網域不在表上 ⇒ None。"""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for platform, hosts in PAGE_SOCIAL_HOSTS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return platform
    return None


def page_socials(urls):
    """候選網址(照頁面順序)→ ([{platform, url}], [被丟掉的網址])。同平台只留第一條。"""
    socials, dropped, seen = [], [], set()
    for url in urls:
        platform = page_platform_for(url)
        if platform is None or platform in seen:
            dropped.append(url)
            continue
        seen.add(platform)
        socials.append({"platform": platform, "url": url})
    return socials, dropped


# ---- 合併器:只產缺欄 --------------------------------------------------------------------------------------------------

def _current_profile(current_state):
    profile = current_state.get("profile") if isinstance(current_state, dict) else None
    return profile if isinstance(profile, dict) else {}


def _current_blocks(current_state):
    if not isinstance(current_state, dict):
        return []
    draft = current_state.get("draft")
    if isinstance(draft, dict) and isinstance(draft.get("blocks"), list):
        return draft["blocks"]
    return current_state["blocks"] if isinstance(current_state.get("blocks"), list) else []


def _filled(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return value is not None


def block_identity(block_type, payload):
    """同一顆的判準(取回再跑一次不會種第二顆):link/cta/shop/tip 看網址;music/video 看 provider+id;event 看標題+時間;text 看全文;image 看 asset。"""
    p = payload if isinstance(payload, dict) else {}
    if block_type in ("link", "shop", "tip", "music", "video"):
        key = p.get("href")
        if block_type in ("music", "video"):
            key = "%s:%s" % (p.get("provider"), p.get("track_id") or p.get("video_id") or key)
    elif block_type == "cta":
        action = p.get("action") if isinstance(p.get("action"), dict) else {}
        key = action.get("href") or action.get("target_id") or p.get("label")
    elif block_type == "event":
        key = "%s@%s" % (p.get("title"), p.get("starts_at"))
    elif block_type == "text":
        key = p.get("text")
    elif block_type == "image":
        key = p.get("asset_id")
    elif block_type == "mail":
        key = p.get("list_id")
    else:
        key = json.dumps(p, sort_keys=True, ensure_ascii=False)
    return "%s|%s" % (block_type, "" if key is None else str(key).strip().lower())


def merge_into_ops(snapshot, current_state, test_mutation=""):
    """Snapshot + 頁現值(/draft 回應體)→ (skill ops, notes)。**只產缺欄**:
    - profile 三欄 + socials:現值有 ⇒ 不進 set_profile,notes 記「已有,跳過」;avatar_url:個人檔案沒有 avatar 寫入端點 ⇒ 只記 notes(登債)。
    - blocks:同一顆(block_identity)已在頁上 ⇒ 跳過;否則 upsert_block(create)帶 provenance。
    刀 merge-ignores-current:不看現值 ⇒ Z1 必紅。"""
    ignore_current = test_mutation == "merge-ignores-current"
    current_profile = {} if ignore_current else _current_profile(current_state)
    current_blocks = [] if ignore_current else _current_blocks(current_state)
    ops, notes = [], []
    profile_op = {"op": "set_profile"}
    for key in ("display_name", "bio", "status_text"):
        value = snapshot["profile"].get(key)
        if not _filled(value):
            continue
        if _filled(current_profile.get(key)):
            notes.append("已有,跳過:%s(頁上是「%s」)" % (key, str(current_profile.get(key))[:40]))
        else:
            profile_op[key] = value.strip()
    socials = snapshot["profile"].get("socials")
    if _filled(socials):
        current_socials = current_profile.get("socials") or current_profile.get("social_links")
        if _filled(current_socials):
            notes.append("已有,跳過:socials(頁上 %d 條)" % len(current_socials))
        else:
            profile_op["socials"] = [{"platform": s["platform"], "url": s["url"]} for s in socials]
    if _filled(snapshot["profile"].get("avatar_url")):
        notes.append("頭像:快照有 %s,但個人檔案沒有 avatar 寫入端點(275(b) 既有限制),要在後台上傳——登債" % snapshot["profile"]["avatar_url"][:80])
    if len(profile_op) > 1:
        ops.append(profile_op)
    provenance = provenance_of(snapshot)
    seen = {block_identity(b.get("block_type"), b.get("payload")) for b in current_blocks if isinstance(b, dict)}
    for block in snapshot["blocks"]:
        identity = block_identity(block["block_type"], block["payload"])
        if identity in seen:
            notes.append("已有,跳過:%s 區塊(%s)" % (block["block_type"], identity.split("|", 1)[1][:60]))
            continue
        seen.add(identity)
        op = {"op": "upsert_block", "block_type": block["block_type"], "payload": json.loads(json.dumps(block["payload"])),
              "provenance": dict(provenance)}
        if block.get("image_urls"):
            notes.append("%s 區塊帶 %d 張圖的網址,圖要走 aib page upload(包一起做)" % (block["block_type"], len(block["image_urls"])))
        ops.append(op)
    for warning in snapshot.get("warnings") or []:
        notes.append("轉接器提醒:%s" % warning)
    return ops, notes


def refresh_into_ops(snapshot, current_state, source, test_mutation=""):
    """`aib page refresh <source>`:同一來源**重抓**。與 merge_into_ops 的差別只在區塊:
    - 頁上帶 provenance.source == source 且同一顆(block_identity)的 ⇒ upsert_block 帶 block_id **與新的 provenance**(update = 換內容換 fetched_at;
      RL-111 甲:update 帶 payload 不帶 provenance 會被 PAGE 當成「人改過」把來源清掉,所以這裡一定帶);
    - 頁上同一顆但**沒有** provenance(手寫的)或來源不同 ⇒ 不碰(notes「已有,跳過」);
    - 頁上沒有 ⇒ create 帶 provenance。個人檔案照舊只填缺欄。
    刀 refresh-drops-provenance:update 不帶 provenance ⇒ R1 必紅。"""
    owned = {}
    for block in _current_blocks(current_state):
        if not isinstance(block, dict):
            continue
        prov = block.get("provenance")
        if isinstance(prov, dict) and prov.get("source") == source and block.get("block_id"):
            owned.setdefault(block_identity(block.get("block_type"), block.get("payload")), (block["block_id"], block.get("visibility") or "public"))
    # 把這個來源的取回顆從「現值」拿掉再合併:合併器就會替它們產 create,下面把有 block_id 的改成 update
    others = [b for b in _current_blocks(current_state)
              if not (isinstance(b, dict) and block_identity(b.get("block_type"), b.get("payload")) in owned)]
    stripped = dict(current_state)
    if isinstance(stripped.get("draft"), dict) and isinstance(stripped["draft"].get("blocks"), list):
        stripped["draft"] = dict(stripped["draft"], blocks=others)
    else:
        stripped["blocks"] = others
    ops, notes = merge_into_ops(snapshot, stripped)
    refreshed = 0
    for op in ops:
        if op["op"] != "upsert_block":
            continue
        hit = owned.get(block_identity(op["block_type"], op["payload"]))
        if hit:
            op["block_id"], op["visibility"] = hit          # 店主藏起來的顆,重抓後還是藏的
            refreshed += 1
            if test_mutation == "refresh-drops-provenance":
                del op["provenance"]
    if refreshed:
        notes.append("重抓:%d 顆 %s 的取回顆換成最新內容" % (refreshed, source))
    return ops, notes
