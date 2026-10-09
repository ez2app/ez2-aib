#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 包一 (f):link-in-bio 四站轉接器(linktree / portaly / linkgoods / lnk.bio)→ intake_sources.Snapshot。

只抓用戶自己那一頁的**公開 HTML**:UA 自報、逾時 10 秒、≤1MB、只跟 3xx 一次;不繞擋爬蟲、不進登入牆——
403 / 需登入 / 404 都是具名停手(L3),不猜。每站的解析對準 tests/fixtures/<站>/demo.html(錄自各平台自己的公開頁、去識別化;
形狀漂了就重錄),尺 L1 對 fixture 解析出 ≥3 條連結且順序與頁面一致、名字對。

抓取層 `fetch_html(url)`:環境變數 EZ2_AIB_IMPORT_REPLAY_DIR 設了就**不上網**,讀 `<dir>/<adapter>/<檔名>.html`
(尺與離線示範用;檔名 = 網址最後一段,沒有就 demo)。
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlparse

import intake_sources

USER_AGENT = "Mozilla/5.0 (compatible; ez2-aib-page-import/1.0; +https://ez2.page)"
TIMEOUT_S = 10
BODY_MAX = 1024 * 1024
REPLAY_ENV = "EZ2_AIB_IMPORT_REPLAY_DIR"
TTL_DAYS = None          # 292:link-in-bio 是用戶自己的東西 ⇒ 無限


class FetchError(intake_sources.SourceError):
    pass


# ---- 抓取層 -------------------------------------------------------------------------------------------------------

def _replay_path(adapter, url):
    root = os.environ.get(REPLAY_ENV)
    if not root:
        return None
    name = [seg for seg in urlparse(url).path.split("/") if seg]
    stem = re.sub(r"[^A-Za-z0-9_.@-]", "_", name[-1]) if name else "demo"
    return os.path.join(root, adapter, stem + ".html")


def fetch_html(url, adapter, http=None):
    """回 (status, body_text)。3xx 只跟一次;>1MB 截斷成錯;網路錯 ⇒ FetchError。"""
    if http is not None:
        return http(url)
    path = _replay_path(adapter, url)
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                body = handle.read()
        except OSError as exc:
            raise FetchError("回放檔讀不到 %s:%s" % (path, exc))
        status = 403 if os.path.basename(path).startswith("blocked-403") else 200
        return status, body
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    opener = urllib.request.build_opener(_OneHopRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_S) as response:
            raw = response.read(BODY_MAX + 1)
            if len(raw) > BODY_MAX:
                raise FetchError("%s 的頁面超過 1MB,不收" % url)
            return response.status, raw.decode(response.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError("抓不到 %s:%s" % (url, getattr(exc, "reason", exc)))


class _OneHopRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 1


def _named_stop(status, url, site):
    if status in (401, 403):
        raise FetchError("%s 把這一頁擋下(HTTP %d,通常是要登入或擋機器人);不繞、不猜——請確認 %s 是公開頁,或把頁面另存 HTML 交給我" % (site, status, url))
    if status == 404:
        raise FetchError("%s 說沒有這一頁(HTTP 404):%s" % (site, url))
    if status != 200:
        raise FetchError("%s 回了 HTTP %d,不是能解析的頁:%s" % (site, status, url))


def _next_data(body, site):
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', body, re.S)
    if not match:
        raise FetchError("%s 這一頁沒有 __NEXT_DATA__(可能要登入、或版面換了 ⇒ 重錄 fixture)" % site)
    try:
        return json.loads(match.group(1))
    except ValueError as exc:
        raise FetchError("%s 的 __NEXT_DATA__ 不是合法 JSON:%s" % (site, exc))


def _og(body, prop):
    match = re.search(r'<meta (?:property|name)="%s" content="([^"]*)"' % re.escape(prop), body)
    return html_lib.unescape(match.group(1)) if match else ""


def _snapshot(kind, url, profile, blocks, warnings, clock=None):
    return {"source": {"kind": kind, "url": url, "fetched_at": intake_sources.now_iso(clock), "ttl_days": TTL_DAYS, "attribution": None},
            "profile": {k: v for k, v in profile.items() if v}, "blocks": blocks, "warnings": warnings}


# ---- PAGE 收得下的清洗(root2 複審 289 包一 F1)------------------------------------------------------------------------
# 取回的值要過 PAGE 契約才進得去,而契約是「一個值壞、整批 422」:link label / profile 走 safeText(有 HTML 標籤形狀就紅、有上限),
#   href 走 safeHref(**只收 https**)。所以轉接器層先洗:去控制字元與 < >、trim、截上限(label 300 / display_name 80 / bio 300 / status 40 —
#   抄 ez2-ai block-contract safeText 的上限與 registration validateProfileTarget 的上限);http:// 連結**不升級成 https(那是猜)**,
#   跳過並寫 warnings。刀 skip-page-text-wash:不洗直接送 ⇒ L6 必紅。
PAGE_CAPS = {"label": 300, "sublabel": 60, "display_name": 80, "bio": 300, "status_text": 40}   # sublabel 60 = PAGE block-contract SUBLABEL_MAX(root2 複審 289 (e) F1)


def page_text(value, cap, test_mutation=""):
    if not isinstance(value, str):
        return ""
    if test_mutation == "skip-page-text-wash":
        return value
    cleaned = re.sub(r"[\u0000-\u001f<>]", "", value).strip()
    return utf16_cap(cleaned, cap).rstrip()


# ---- 上限是 UTF-16 單元不是碼點(root2 09-18 06:0x 複審殘留;照 289 二 A2 PAGE seedableStatusText 的做法)----------------------
# PAGE 的 safeText / validateProfileTarget 用 JS `.length`(UTF-16 單元):一個 emoji 算 2、帶膚色或 ZWJ 的家族 emoji 算 4–11;
#   Python 的 `[:cap]` 是碼點 ⇒ 300 字含 emoji 的 bio 會在 PAGE 端超單元 422。改成逐 grapheme 累加到 UTF-16 單元上限,不切半個 emoji。
_GRAPHEME_TAIL = re.compile("[\U0001F3FB-\U0001F3FF\uFE00-\uFE0F\u200D]")
_REGIONAL = ("\U0001F1E6", "\U0001F1FF")


def _graphemes(text):
    """近似 grapheme 切法(沒有 Intl.Segmenter):基字 + 結合記號 / 變體選擇子 / 膚色修飾;ZWJ 把下一個黏進來;成對的區域指示字(國旗)一組。"""
    out, i, n = [], 0, len(text)
    while i < n:
        j = i + 1
        while j < n:
            ch = text[j]
            if unicodedata.combining(ch) or _GRAPHEME_TAIL.match(ch):
                j += 1
                if ch == "\u200D" and j < n:      # ZWJ:下一個碼點也是這一團的
                    j += 1
                continue
            if _REGIONAL[0] <= text[i] <= _REGIONAL[1] and _REGIONAL[0] <= ch <= _REGIONAL[1] and j == i + 1:
                j += 1
                continue
            break
        out.append(text[i:j])
        i = j
    return out


def utf16_units(text):
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def utf16_cap(text, cap):
    out, used = [], 0
    for piece in _graphemes(text):
        units = utf16_units(piece)
        if used + units > cap:
            break
        out.append(piece)
        used += units
    return "".join(out)


def _link_block(label, href):
    text = page_text(label, PAGE_CAPS["label"])
    return {"block_type": "link", "payload": {"label": text or href[:PAGE_CAPS["label"]], "href": href, "rel": []}}


def _http_url(value):
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _page_href(value):
    """PAGE safeHref 只收 https(≤2048、無控制字元);http:// 與其他一律回 None,呼叫端寫 warnings。"""
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > 2048 or re.search(r"[\u0000-\u001f]", value):
        return None
    return value


def _dropped_links_warning(site, dropped, warnings):
    if dropped:
        warnings.append("%s 有 %d 條不是 https 的連結沒帶進來(PAGE 只收 https,不猜升級):%s" % (site, len(dropped), ", ".join(dropped)[:200]))


def _profile(display_name, bio, avatar_url, socials, status_text=""):
    out = {"display_name": page_text(display_name, PAGE_CAPS["display_name"]), "bio": page_text(bio, PAGE_CAPS["bio"]),
           "avatar_url": avatar_url if _page_href(avatar_url) else "", "socials": socials}
    if status_text:
        out["status_text"] = page_text(status_text, PAGE_CAPS["status_text"])
    return out


def _socials(urls, warnings, site):
    """各站的社群網址 → PAGE 的 {platform, url}(平台由網域決定,見 intake_sources.PAGE_SOCIAL_HOSTS);認不得的網域寫進 warnings 不送。
    刀 socials-raw-keys:不經字彙表直接送 ⇒ L1 紅(PAGE 會以 PAGE_SOCIAL_PLATFORM_UNKNOWN 拒整個 PUT /profile)。"""
    socials, dropped = intake_sources.page_socials([u for u in urls if _http_url(u)])
    if dropped:
        warnings.append("%s 的社群列有 %d 條 PAGE 沒有對應平台(或同平台第二條),沒帶進來:%s" % (site, len(dropped), ", ".join(dropped)[:200]))
    return socials


# ---- linktree:頁內 __NEXT_DATA__ ---------------------------------------------------------------------------------------

def linktree_matches(url):
    return urlparse(url).netloc.lower() in ("linktr.ee", "www.linktr.ee")


def linktree_parse(body, url, clock=None, test_mutation=""):
    data = _next_data(body, "Linktree")
    props = ((data.get("props") or {}).get("pageProps") or {})
    account = props.get("account") or {}
    links = props.get("links") or []
    groups = {l.get("id"): l for l in links if l.get("type") == "GROUP"}
    ordered = []
    dropped = []
    for index, link in enumerate(links):
        if link.get("type") == "GROUP" or not _http_url(link.get("url")):
            continue
        if not _page_href(link.get("url")):
            dropped.append(str(link.get("url"))[:120])
            continue
        parent = link.get("parent") or {}
        group = groups.get(parent.get("id")) if isinstance(parent, dict) else None
        if group is not None:
            key = (0, _num(group.get("position")), _num(link.get("position")), index)
        elif parent:
            key = (1, 0, _num(link.get("position")), index)        # parent 不在清單裡:排最後、穩定序
        else:
            key = (0, _num(link.get("position")), -1, index)
        ordered.append((key, link))
    ordered.sort(key=lambda item: item[0], reverse=(test_mutation == "reverse-order"))   # 刀 ⇒ L1 必紅
    blocks = [_link_block(link.get("title") or link["url"], link["url"]) for _key, link in ordered]
    warnings = []
    social_links = sorted((props.get("socialLinks") or []), key=lambda s: _num(s.get("position")) if isinstance(s, dict) else 0)
    socials = _socials([s.get("url") for s in social_links if isinstance(s, dict)], warnings, "Linktree")
    _dropped_links_warning("Linktree", dropped, warnings)
    profile = _profile(account.get("pageTitle") or props.get("pageTitle") or _og(body, "og:title").split("|")[0].strip(),
                       account.get("description") or props.get("description") or "", account.get("profilePictureUrl"), socials)
    if not blocks:
        warnings.append("Linktree 這一頁解析不到任何連結(GROUP 之外沒有帶 url 的項目)")
    return _snapshot("linktree", url, profile, blocks, warnings, clock)


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def linktree_fetch(url, http=None, clock=None):
    status, body = fetch_html(url, "linktree", http)
    _named_stop(status, url, "Linktree")
    return linktree_parse(body, url, clock)


# ---- portaly:頁內 __NEXT_DATA__ props.pageProps.data ----------------------------------------------------------------------

def portaly_matches(url):
    return urlparse(url).netloc.lower() in ("portaly.cc", "www.portaly.cc")


def portaly_parse(body, url, clock=None, test_mutation=""):
    data = (((_next_data(body, "Portaly").get("props") or {}).get("pageProps") or {}).get("data") or {})
    links = data.get("links") or {}
    by_id = {b.get("id"): b for b in (data.get("blocks") or []) if isinstance(b, dict)}
    blocks = []
    dropped = []
    order = list(data.get("blockOrders") or [])
    if test_mutation == "reverse-order":
        order.reverse()
    for block_id in order:
        block = by_id.get(block_id)
        if not block or str(block.get("hidden")).lower() == "true":
            continue
        if block.get("type") not in ("list", "grid", "banner"):
            continue
        for item in block.get("items") or []:
            target = links.get(item.get("linkId")) if isinstance(item, dict) else None
            href = (target or {}).get("url")
            if not _http_url(href):
                continue
            if not _page_href(href):
                dropped.append(str(href)[:120])
                continue
            blocks.append(_link_block(item.get("text") or item.get("label") or item.get("alt") or href, href))
    social = ((data.get("social") or {}).get("links") or {})
    candidates = []
    for platform, value in social.items():
        if isinstance(value, str) and value.startswith("@") and platform == "instagram":    # Portaly 的 IG 只存帳號
            value = "https://www.instagram.com/%s/" % value[1:]
        candidates.append(value)
    warnings = [] if blocks else ["Portaly 這一頁可見的 list / grid / banner 裡解析不到連結"]
    _dropped_links_warning("Portaly", dropped, warnings)
    profile = _profile(data.get("name") or _og(body, "og:title").split("|")[0].strip(), data.get("description") or "", data.get("avatar"),
                       _socials(candidates, warnings, "Portaly"))
    return _snapshot("portaly", url, profile, blocks, warnings, clock)


def portaly_fetch(url, http=None, clock=None):
    status, body = fetch_html(url, "portaly", http)
    _named_stop(status, url, "Portaly")
    return portaly_parse(body, url, clock)


# ---- linkgoods:Nuxt devalue __NUXT_DATA__(SSR 只有個人檔案與社群;連結清單是前端 GraphQL 才載)-----------------------------

def linkgoods_matches(url):
    return urlparse(url).netloc.lower() in ("linkgoods.com", "www.linkgoods.com", "linkgoods.tw", "www.linkgoods.tw")


def linkgoods_parse(body, url, clock=None):
    match = re.search(r'<script type="application/json" data-nuxt-data="nuxt-app"[^>]*id="__NUXT_DATA__">(.*?)</script>', body, re.S)
    if not match:
        raise FetchError("LINKGOODS 這一頁沒有 __NUXT_DATA__(可能要登入、或版面換了 ⇒ 重錄 fixture)")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise FetchError("LINKGOODS 的 __NUXT_DATA__ 不是合法 JSON:%s" % exc)
    if not isinstance(data, list):
        raise FetchError("LINKGOODS 的 __NUXT_DATA__ 不是 devalue 陣列")

    def resolve(value, depth=0):
        if depth > 6:
            return None
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(data):
            return resolve(data[value], depth + 1)
        if isinstance(value, dict):
            return {k: resolve(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v, depth + 1) for v in value]
        return value

    user = None
    for entry in data:
        if isinstance(entry, dict) and entry.get("__typename") == "GuestSideUser" or (isinstance(entry, dict) and "nickname" in entry and "username" in entry):
            user = resolve(entry)
            break
    if not user:
        raise FetchError("LINKGOODS 這一頁的 __NUXT_DATA__ 裡找不到 GuestSideUser(版面換了 ⇒ 重錄 fixture)")
    warnings = ["LINKGOODS 的連結清單(productList)是前端 GraphQL 才載入,公開 HTML 裡沒有;這裡只帶個人檔案與社群連結"]
    profile = _profile(user.get("nickname") or _og(body, "og:title").split("|")[0].strip(),
                       user.get("description") if isinstance(user.get("description"), str) else "", user.get("photo"),
                       _socials([value for key, value in user.items() if key.endswith("Url")], warnings, "LINKGOODS"))
    return _snapshot("linkgoods", url, profile, [], warnings, clock)


def linkgoods_fetch(url, http=None, clock=None):
    status, body = fetch_html(url, "linkgoods", http)
    _named_stop(status, url, "LINKGOODS")
    return linkgoods_parse(body, url, clock)


# ---- lnk.bio:og:* + 連結區 DOM(2026-09-18 實測被 Cloudflare 擋 403 ⇒ L3 具名停手;200 的形狀等錄得到再定選擇器)-----------

def lnkbio_matches(url):
    return urlparse(url).netloc.lower() in ("lnk.bio", "www.lnk.bio")


def lnkbio_parse(body, url, clock=None):
    title = _og(body, "og:title")
    if not title:
        raise FetchError("lnk.bio 這一頁沒有 og:title(可能被擋或版面換了)")
    anchors = re.findall(r'<a [^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', body, re.S)
    blocks = []
    seen = set()
    dropped = []
    for href, inner in anchors:
        if "lnk.bio" in urlparse(href).netloc or href in seen:
            continue
        text = html_lib.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if not text:
            continue
        seen.add(href)
        if not _page_href(href):
            dropped.append(href[:120])
            continue
        blocks.append(_link_block(text, href))
    warnings = [] if blocks else ["lnk.bio 這一頁解析不到外部連結"]
    _dropped_links_warning("lnk.bio", dropped, warnings)
    profile = _profile(title.split("|")[0].strip(), _og(body, "og:description"), _og(body, "og:image"), [])
    return _snapshot("lnkbio", url, profile, blocks, warnings, clock)


def lnkbio_fetch(url, http=None, clock=None):
    status, body = fetch_html(url, "lnkbio", http)
    _named_stop(status, url, "lnk.bio")
    return lnkbio_parse(body, url, clock)


ADAPTERS = (
    ("linktree", linktree_matches, linktree_fetch),
    ("portaly", portaly_matches, portaly_fetch),
    ("linkgoods", linkgoods_matches, linkgoods_fetch),
    ("lnkbio", lnkbio_matches, lnkbio_fetch),
)
for _kind, _matches, _fetch in ADAPTERS:
    intake_sources.register_adapter(_kind, _matches, _fetch)
