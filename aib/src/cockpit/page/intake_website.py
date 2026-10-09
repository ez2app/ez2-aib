#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 (d) 網站爬取(RL-087):`aib page import <用戶自己的網站網址>` → 同網域最多 20 頁 → intake_sources.Snapshot。

只抓**用戶親手給的網域**(register_adapter explicit_only:搜尋回退挑出來的候選看不到這個轉接器);規則:
  - 跨網域不抓(host 逐字相等,www. 視為同一站;子網域算別站);
  - 先讀 `<scheme>://<host>/robots.txt`(RFC 9309:200 照檔、404 全准、其他全禁),每一頁抓前都問 `robots_allows`;禁的頁**零請求**、記 warnings;
  - 不進登入牆:401 / 403 具名記 warnings 跳過;200 的頁裡有 `<input type=password>` 也當登入牆跳過(那一頁抓到了,但內容不用);
  - 逾時 10 秒 / 頁、≤1MB / 頁(_lib().fetch_html 同一條抓取層);頁數上限 PAGE_MAX=20(BFS,起始頁的連結先);
  - 抽:og:* / meta description / `<title>` / JSON-LD(Organization·LocalBusiness·Product·Event)/ h1 / 段落 / 商品卡 / 活動 / 聯絡(tel:·mailto:·address)/ 社群連結。
Snapshot 對應:profile{display_name = og:site_name > Organization.name > title;bio = og:description > meta description;avatar_url = logo > og:image;
  socials = 頁上指到平台網域的連結(intake_sources.page_socials 翻字彙)};blocks = 起始頁 heading(h1)+ text(前幾段)+ shop(Product;
  provider external、product_id = 網址路徑、image_urls 給 cli 走 288 上傳後填 image_asset_id)+ event(Event;starts_at 轉 UTC ISO)+
  聯絡 text 顆(電話 / 信箱 / 地址;PAGE 的 href 只收 https,tel: / mailto: 不能當連結)。
回放:EZ2_AIB_IMPORT_REPLAY_DIR 設了讀 `<dir>/website/<最後一段>.html`(根頁 = index.html)與 `<dir>/website/robots.txt`,零網路。
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import intake_sources


def _lib():
    """延後 import intake_linkinbio(抓取層 fetch_html、清洗 page_text / _profile / _socials 共用它):
    ★ 不在模組頂 import——四站轉接器的登記由 cli.py 那一行負責(linkinbio 尺 K5 的刀錨);這裡若頂層 import,那把刀就落不下(替它登記了)。"""
    import intake_linkinbio
    return intake_linkinbio

KIND = "website"
PAGE_MAX = 20
TEXT_PARAGRAPHS_MAX = 3
PARAGRAPH_MIN_CHARS = 20
SHOP_MAX = 12
EVENT_MAX = 6
TTL_DAYS = None                   # 用戶自己的網站 ⇒ 無限(292)
PRODUCT_TYPES = ("Product",)
EVENT_TYPES = ("Event", "BusinessEvent", "EducationEvent", "SocialEvent", "MusicEvent", "Festival", "ExhibitionEvent")
ORG_TYPES = ("Organization", "LocalBusiness", "Store", "Restaurant", "Corporation", "ProfessionalService", "Person")
CURRENCY_OK = ("TWD", "USD", "JPY", "KRW", "EUR")
LINKINBIO_HOSTS = ("linktr.ee", "portaly.cc", "linkgoods.com", "linkgoods.tw", "lnk.bio")
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".css", ".js", ".xml", ".ico", ".mp4", ".mp3", ".woff", ".woff2")


class CrawlError(intake_sources.SourceError):
    pass


# ---- 誰算「用戶自己的網站」 -------------------------------------------------------------------------------------------------

def _host_of(url):
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def website_matches(url):
    """http(s) 且不是 link-in-bio 四站、不是 PAGE 認得的社群平台網域(那些是平台頁不是用戶的網站)。"""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return False
    host = _host_of(url)
    if not host or "." not in host:
        return False
    if any(host == h or host.endswith("." + h) for h in LINKINBIO_HOSTS):
        return False
    return intake_sources.page_platform_for("https://" + host + "/") is None


# ---- 抓取層 -------------------------------------------------------------------------------------------------------------

def _replay_root():
    root = os.environ.get(_lib().REPLAY_ENV)
    return os.path.join(root, KIND) if root else None


def _fetch(url, http=None, kind="page"):
    """回 (status, text)。http 明給 ⇒ 用它(尺);回放目錄 ⇒ 讀檔(根頁 index.html、robots.txt);否則走 _lib().fetch_html(UA、10 秒、1MB)。"""
    if http is not None:
        return http(url)
    root = _replay_root()
    if root:
        if kind == "robots":
            name = "robots.txt"
        else:
            segs = [s for s in urlsplit(url).path.split("/") if s]
            name = (re.sub(r"[^A-Za-z0-9_.@-]", "_", segs[-1]) if segs else "index") + ".html"
        path = os.path.join(root, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return 200, handle.read()
        except FileNotFoundError:
            return 404, ""
        except OSError as exc:
            raise CrawlError("回放檔讀不到 %s:%s" % (path, exc))
    return _lib().fetch_html(url, KIND, None)


def _normalize_url(url):
    parts = urlsplit(url)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


# ---- 抽取 ---------------------------------------------------------------------------------------------------------------

def _meta(body, key):
    k = re.escape(key)
    for pattern in (r'<meta\s+(?:property|name)=["\']%s["\']\s+content="([^"]*)"' % k, r"<meta\s+(?:property|name)=[\"']%s[\"']\s+content='([^']*)'" % k,
                    r'<meta\s+content="([^"]*)"\s+(?:property|name)=["\']%s["\']' % k, r"<meta\s+content='([^']*)'\s+(?:property|name)=[\"']%s[\"']" % k):
        m = re.search(pattern, body, re.I)
        if m:
            return html_lib.unescape(m.group(1)).strip()
    return ""


def _title(body):
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    return html_lib.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def _strip(text):
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _jsonld(body):
    out = []
    for raw in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body, re.S | re.I):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                out.extend(x for x in item["@graph"] if isinstance(x, dict))
            elif isinstance(item, dict):
                out.append(item)
    return out


def _types(item):
    t = item.get("@type")
    return [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]


def _body_html(body):
    m = re.search(r"<body[^>]*>(.*)</body>", body, re.S | re.I)
    inner = m.group(1) if m else body
    return re.sub(r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", inner, flags=re.S | re.I)


def _paragraphs(body):
    out = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", _body_html(body), re.S | re.I):
        text = _strip(raw)
        if len(text) >= PARAGRAPH_MIN_CHARS:
            out.append(text)
    return out


def _h1(body):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
    return _strip(m.group(1)) if m else ""


def _links(body, base):
    out = []
    for href in re.findall(r'<a\s[^>]*href=["\']([^"\'#]+)["\']', body, re.I):
        out.append(urljoin(base, html_lib.unescape(href).strip()))
    return out


def _multiline(text, cap=20000):
    """text 顆的內容要留換行(PAGE safeText 收 \n;linkinbio.page_text 把所有控制字元含 \n 都洗掉,那是給單行欄用的)。"""
    lines = [_lib().page_text(line, cap) for line in str(text or "").split("\n")]
    return "\n".join(line for line in lines if line)[:cap].rstrip()


def _is_login_wall(body):
    return bool(re.search(r'<input[^>]+type=["\']password["\']', body, re.I))


def _iso_utc(value):
    """schema.org 的日期(含時區或不含)→ UTC ISO;不含時區 ⇒ 當 Asia/Taipei(+08:00,Ez2 母市場)。解析不了 ⇒ None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        from datetime import timedelta
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _price(offers):
    """schema.org offers → (price_display, price_amount, currency);幣別不在 PAGE 清單 ⇒ None。"""
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return None
    price = offers.get("price") if offers.get("price") is not None else offers.get("lowPrice")
    currency = str(offers.get("priceCurrency") or "").upper()
    try:
        amount = round(float(str(price).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None
    if currency not in CURRENCY_OK or amount < 0:
        return None
    display = ("NT$%s" if currency == "TWD" else "%s " + currency) % ("{:,.0f}".format(amount) if amount == int(amount) else "{:,.2f}".format(amount))
    return display, (int(amount) if amount == int(amount) else amount), currency


def _product_block(item, page_url):
    href = _lib()._page_href(item.get("url") if isinstance(item.get("url"), str) else page_url)
    priced = _price(item.get("offers"))
    title = _lib().page_text(str(item.get("name") or ""), 300)
    if not href or not priced or not title:
        return None
    product_id = re.sub(r"[^A-Za-z0-9_.:-]", "-", (urlsplit(href).path.strip("/") or "root"))[:180] or "root"
    if not re.match(r"^[A-Za-z0-9]", product_id):
        product_id = "p-" + product_id
    display, amount, currency = priced
    block = {"block_type": "shop", "payload": {"provider": "external", "product_id": product_id[:180], "title": title, "href": href,
                                              "price_display": display, "price_amount": amount, "currency": currency}}
    image = item.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    if isinstance(image, str) and _lib()._page_href(image):
        block["image_urls"] = [image]
    return block


def _event_block(item, page_url):
    title = _lib().page_text(str(item.get("name") or ""), 300)
    starts = _iso_utc(item.get("startDate"))
    if not title or not starts:
        return None
    payload = {"title": title, "starts_at": starts}
    ends = _iso_utc(item.get("endDate"))
    if ends and ends >= starts:
        payload["ends_at"] = ends
    location = item.get("location")
    if isinstance(location, dict):
        addr = location.get("address")
        place = location.get("name") or (addr if isinstance(addr, str) else (addr or {}).get("streetAddress") if isinstance(addr, dict) else "")
        if place:
            payload["location"] = _lib().page_text(str(place), 300)
    elif isinstance(location, str) and location.strip():
        payload["location"] = _lib().page_text(location, 300)
    href = _lib()._page_href(item.get("url") if isinstance(item.get("url"), str) else page_url)
    if href:
        payload["href"] = href
    return {"block_type": "event", "payload": payload}


def _contacts(body, jsonld):
    phones, mails, addresses = [], [], []
    for href in re.findall(r'href=["\'](tel:[^"\']+|mailto:[^"\']+)["\']', body, re.I):
        value = html_lib.unescape(href.split(":", 1)[1]).split("?")[0].strip()
        (phones if href.lower().startswith("tel:") else mails).append(value)
    for item in jsonld:
        if any(t in ORG_TYPES for t in _types(item)):
            if isinstance(item.get("telephone"), str):
                phones.append(item["telephone"].strip())
            if isinstance(item.get("email"), str):
                mails.append(item["email"].strip())
            addr = item.get("address")
            if isinstance(addr, dict):
                parts = [str(addr.get(k) or "").strip() for k in ("addressRegion", "addressLocality", "streetAddress")]
                text = "".join(p for p in parts if p)
                if text:
                    addresses.append(text)
            elif isinstance(addr, str) and addr.strip():
                addresses.append(addr.strip())
    for raw in re.findall(r"<address[^>]*>(.*?)</address>", body, re.S | re.I):
        text = _strip(raw)
        if text:
            addresses.append(text)
    dedupe = lambda xs: list(dict.fromkeys(x for x in xs if x))
    return dedupe(phones), dedupe(mails), dedupe(addresses)


# ---- 爬 ------------------------------------------------------------------------------------------------------------------

def crawl(start_url, http=None, clock=None, test_mutation=""):
    """→ (pages [{url, status, body}], warnings, robots_text)。同網域 BFS、robots、登入牆、上限 20 頁。
    刀 crawl-ignore-robots:禁的頁照抓 ⇒ W2 必紅;刀 crawl-cross-domain:別的網域也抓 ⇒ W1 必紅。"""
    start = _normalize_url(start_url)
    parts = urlsplit(start)
    host = _host_of(start)
    warnings = []
    status, robots = _fetch(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")), http, kind="robots")
    if status == 200:
        allowed = lambda path: intake_sources.robots_allows(robots, path)
    elif status == 404:
        allowed = lambda path: True
    else:
        warnings.append("robots.txt 回 HTTP %d(不是 200 / 404)⇒ 當整站禁抓(RFC 9309),零頁面請求" % status)
        allowed = lambda path: False
    if test_mutation == "crawl-ignore-robots":
        allowed = lambda path: True
    pages, seen, queue = [], {start}, deque([start])
    robots_blocked, foreign = [], []
    while queue and len(pages) < PAGE_MAX:
        url = queue.popleft()
        path = urlsplit(url).path or "/"
        if not allowed(path):
            robots_blocked.append(url)
            continue
        try:
            status, body = _fetch(url, http)
        except intake_sources.SourceError as exc:
            warnings.append("抓不到 %s:%s" % (url, exc))
            continue
        if status in (401, 403):
            warnings.append("%s 要登入或擋機器人(HTTP %d),不繞、不猜,跳過" % (url, status))
            continue
        if status != 200:
            warnings.append("%s 回 HTTP %d,跳過" % (url, status))
            continue
        if _is_login_wall(body):
            warnings.append("%s 是登入頁(有密碼欄),不進登入牆,跳過" % url)
            continue
        pages.append({"url": url, "status": status, "body": body})
        for link in _links(body, url):
            link = _normalize_url(link.split("#")[0])
            lp = urlsplit(link)
            if lp.scheme not in ("http", "https"):
                continue
            if _host_of(link) != host and test_mutation != "crawl-cross-domain":
                if lp.netloc and link not in foreign:
                    foreign.append(link)
                continue
            if lp.path.lower().endswith(SKIP_EXT) or link in seen:
                continue
            seen.add(link)
            queue.append(link)
    if robots_blocked:
        warnings.append("robots.txt 禁抓 %d 頁,一個請求都沒發:%s" % (len(robots_blocked), ", ".join(robots_blocked)[:300]))
    if queue:
        warnings.append("同網域還有 %d 頁沒抓(上限 %d 頁)" % (len(queue), PAGE_MAX))
    return pages, warnings, {"foreign": foreign, "robots_blocked": robots_blocked}


def website_fetch(url, http=None, clock=None, test_mutation=""):
    if not website_matches(url):
        raise CrawlError("%s 不是可以爬的網站網址(要 http(s),且不是 link-in-bio / 社群平台的頁)" % url)
    pages, warnings, extra = crawl(url, http, clock, test_mutation)
    if not pages:
        raise CrawlError("%s 一頁都抓不到(%s)" % (url, "; ".join(warnings)[:300] or "沒有回應"))
    first = pages[0]
    body = first["body"]
    jsonld = [item for page in pages for item in _jsonld(page["body"])]
    org = next((item for item in jsonld if any(t in ORG_TYPES for t in _types(item))), {})
    site = next((item for item in jsonld if "WebSite" in _types(item)), {})
    display_name = _meta(body, "og:site_name") or str(org.get("name") or site.get("name") or "") or _title(body).split("|")[0].split(" - ")[0].strip()
    bio = _meta(body, "og:description") or _meta(body, "description") or str(org.get("description") or "")
    logo = org.get("logo")
    if isinstance(logo, dict):
        logo = logo.get("url")
    avatar = logo if isinstance(logo, str) else _meta(body, "og:image")
    social_urls = []
    for page in pages:
        for link in _links(page["body"], page["url"]):
            if intake_sources.page_platform_for(link) and link not in social_urls:
                social_urls.append(link)
    same_as = org.get("sameAs") or []
    for link in (same_as if isinstance(same_as, list) else [same_as]):
        if isinstance(link, str) and intake_sources.page_platform_for(link) and link not in social_urls:
            social_urls.append(link)
    socials = _lib()._socials(social_urls, warnings, "網站")
    profile = _lib()._profile(display_name, bio, avatar, socials)

    blocks = []
    h1 = _h1(body)
    if h1 and _lib().page_text(h1, 80) and _lib().page_text(h1, 80) != profile.get("display_name"):
        blocks.append({"block_type": "heading", "payload": {"text": _lib().page_text(h1, 80), "level": "h2", "align": "left"}})
    paragraphs = _paragraphs(body)[:TEXT_PARAGRAPHS_MAX]
    if paragraphs:
        text = _multiline("\n\n".join(paragraphs))
        if text and text != profile.get("bio"):
            blocks.append({"block_type": "text", "payload": {"text": text, "format": "plain"}})
    seen_products, seen_events = set(), set()
    for page in pages:
        for item in _jsonld(page["body"]):
            types = _types(item)
            if any(t in PRODUCT_TYPES for t in types) and len(seen_products) < SHOP_MAX:
                block = _product_block(item, page["url"])
                if block and block["payload"]["href"] not in seen_products:
                    seen_products.add(block["payload"]["href"])
                    blocks.append(block)
            elif any(t in EVENT_TYPES for t in types) and len(seen_events) < EVENT_MAX:
                block = _event_block(item, page["url"])
                if block:
                    key = block["payload"]["title"] + "@" + block["payload"]["starts_at"]
                    if key not in seen_events:
                        seen_events.add(key)
                        blocks.append(block)
    phones, mails, addresses = [], [], []
    for page in pages:
        p, m, a = _contacts(page["body"], _jsonld(page["body"]))
        phones += [x for x in p if x not in phones]
        mails += [x for x in m if x not in mails]
        addresses += [x for x in a if x not in addresses]
    lines = [label + ":" + "、".join(values) for label, values in (("電話", phones[:3]), ("信箱", mails[:3]), ("地址", addresses[:2])) if values]
    if lines:
        blocks.append({"block_type": "text", "payload": {"text": _multiline("聯絡\n" + "\n".join(lines)), "format": "plain"}})
    if extra["foreign"]:
        warnings.append("有 %d 條連到別的網域的連結沒抓(跨網域不抓):%s" % (len(extra["foreign"]), ", ".join(extra["foreign"])[:200]))
    warnings.insert(0, "抓了 %d 頁:%s" % (len(pages), ", ".join(p["url"] for p in pages)[:400]))
    return {"source": {"kind": KIND, "url": url, "fetched_at": intake_sources.now_iso(clock), "ttl_days": TTL_DAYS, "attribution": None},
            "profile": {k: v for k, v in profile.items() if v}, "blocks": blocks, "warnings": warnings}


intake_sources.register_adapter(KIND, website_matches, website_fetch, explicit_only=True)
