#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 (h) 搜尋回退:沒串接就先用品牌名搜公開結果,搜到的候選走既有轉接器抓進草稿(RL-112 / RL-113 / RL-118 A30)。

規格 pending/SPEC-289-h-search-fallback-37ca8-0918.md §一。這支檔只做三件事:
  1. 查詢串(build_queries):`"<名>"`、`"<名>" <city>`、`"<名>" site:<domain>`、`"<名>" (instagram.com OR … OR linktr.ee)`,≤4 條/次執行;
  2. 引擎(search):`EZ2_PAGE_GOOGLE_API_KEY` **且** `EZ2_PAGE_SEARCH_CX` 都有 ⇒ Google Programmable Search JSON API;缺任一 ⇒ 先 GET
     DuckDuckGo 的 robots.txt,`/html/` 被 Disallow ⇒ **具名停手、零搜尋請求**;准 ⇒ DDG HTML 端點(UA 自報、逾時 10 秒、≤1MB,
     解析 `.result__a` 標題/網址 與 `.result__snippet`)。兩個引擎回同一形狀 `[{url, title, snippet}]`;
  3. 候選判準(candidates):NFKC、去空白與標點、小寫後 **標題或網址路徑含整個品牌名** 才是候選;同 host+path 去重、每個 host 只留第一筆、
     上限 5;`intake_sources.adapter_for(url)` 找得到既有轉接器才留,找不到印「跳過:沒有 <host> 的轉接器」。**不做勾選步**(RL-113):
     清單印出來是告知不是提問;唯一擋「抓到別人的東西」的是這條判準 + 來源標示(provenance.via:"search")+ 那句提醒 + 全部只進草稿。
每日上限 SEARCH_DAILY_MAX(本機計數檔 <狀態根>/page/search-usage.json,同 288 那套本機檔;多台機器各算各的——登債)。

抓取層:環境變數 EZ2_AIB_SEARCH_REPLAY_DIR 設了就**不上網**,讀 `<dir>/robots.txt`、`<dir>/ddg.html`、`<dir>/google.json`
(尺與離線示範用;檔缺就是錯,不會退回上網)。fixture 在 tests/fixtures/search/(README 記來源與日期)。

那句提醒(AI_SEARCH_NOTICE)四語字面 = PAGE `shared/ez2-page-i18n.js` 的 `editor.ai_search.review`;AIB 跑在用戶機器上沒有 ez2-ai 倉,
所以這裡帶一份**釘死的副本**,尺 H6 對 PAGE 那份檔逐語比對(EZ2_AI_ROOT 指到 ez2-ai 樹)——兩邊漂了是尺紅,不是靜默。
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import intake_sources
from seats_dir_lib import state_root

GOOGLE_KEY_ENV = "EZ2_PAGE_GOOGLE_API_KEY"
GOOGLE_CX_ENV = "EZ2_PAGE_SEARCH_CX"
GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
DDG_ROBOTS_URL = "https://html.duckduckgo.com/robots.txt"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_HTML_PATH = "/html/"
REPLAY_ENV = "EZ2_AIB_SEARCH_REPLAY_DIR"
USER_AGENT = "Mozilla/5.0 (compatible; ez2-aib-page-import/1.0; +https://ez2.page)"
TIMEOUT_S = 10
BODY_MAX = 1024 * 1024
SEARCH_DAILY_MAX = 20          # 每日上限(本機計數)
RUN_REQUEST_MAX = 4            # 每次執行最多幾條查詢
PER_QUERY_MAX = 10             # 每條最多幾筆
CANDIDATES_MAX = 5
PLATFORM_CLAUSE = "(instagram.com OR facebook.com OR youtube.com OR threads.net OR tiktok.com OR linktr.ee)"
USAGE_PARTS = ("page", "search-usage.json")
VIA = "search"                 # PAGE 契約 provenance.via 唯一准的值

# PAGE shared/ez2-page-i18n.js 的 editor.ai_search.review(四語逐字;尺 H6 對檔比對)
AI_SEARCH_NOTICE = {
    "zh-TW": "這份內容有 AI 搜尋帶入的部分,請仔細比對是否正確",
    "en": "Part of this content was brought in by AI search; please check it carefully for accuracy",
    "ja": "この内容には AI 検索で取り込んだ部分があります。正しいかよく確認してください",
    "ko": "이 내용에는 AI 검색으로 가져온 부분이 있습니다. 정확한지 꼼꼼히 확인해 주세요",
}


class SearchError(intake_sources.SourceError):
    """具名停手:訊息就是給人看的那一句。"""


def notice(locale="zh-TW"):
    return AI_SEARCH_NOTICE.get(locale) or AI_SEARCH_NOTICE["zh-TW"]


# ---- 查詢串 ----------------------------------------------------------------------------------------------------------

def build_queries(name, city=None, domain=None):
    name = " ".join(str(name or "").split())
    if not name:
        raise SearchError("先設名字:頁的 display_name 是空的,沒有品牌名就沒有東西可以搜(`aib page chat --ops` 先 set_profile)")
    quoted = '"%s"' % name.replace('"', "")
    queries = [quoted]
    if city and str(city).strip():
        queries.append("%s %s" % (quoted, " ".join(str(city).split())))
    if domain and str(domain).strip():
        host = str(domain).strip().lower()
        host = re.sub(r"^https?://", "", host).split("/")[0]
        if host.startswith("www."):
            host = host[4:]
        queries.append("%s site:%s" % (quoted, host))
    queries.append("%s %s" % (quoted, PLATFORM_CLAUSE))
    return queries[:RUN_REQUEST_MAX]


# ---- 引擎選路 --------------------------------------------------------------------------------------------------------

def engine_for(environ):
    """(引擎名, 說明)。key 與 cx **都**有 ⇒ google;缺任一 ⇒ ddg(A30:cx 沒到之前 Google 那條走不到,是具名的不是紅)。"""
    key = (environ.get(GOOGLE_KEY_ENV) or "").strip()
    cx = (environ.get(GOOGLE_CX_ENV) or "").strip()
    if key and cx:
        return "google", "Google Programmable Search(%s + %s 都有)" % (GOOGLE_KEY_ENV, GOOGLE_CX_ENV)
    missing = [n for n, v in ((GOOGLE_KEY_ENV, key), (GOOGLE_CX_ENV, cx)) if not v]
    return "ddg", "DuckDuckGo HTML(缺 %s)" % ", ".join(missing)


# ---- 抓取層 ----------------------------------------------------------------------------------------------------------

def _replay(name):
    root = os.environ.get(REPLAY_ENV)
    if not root:
        return None
    path = os.path.join(root, name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return 200, handle.read()
    except OSError as exc:
        raise SearchError("回放檔讀不到 %s:%s" % (path, exc))


def http_get(url, headers=None):
    """回 (status, body_text)。>1MB 截斷成錯;網路錯 ⇒ SearchError。"""
    request = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read(BODY_MAX + 1)
            if len(raw) > BODY_MAX:
                raise SearchError("%s 的回應超過 1MB,不收" % url.split("?")[0])
            return response.status, raw.decode(response.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SearchError("抓不到 %s:%s" % (url.split("?")[0], getattr(exc, "reason", exc)))


def _fetch(kind, url, http=None, headers=None):
    """kind ∈ robots / ddg / google。http 明給 ⇒ 用它(尺);回放目錄設了 ⇒ 讀檔;否則上網。"""
    if http is not None:
        return http(url, headers or {})
    replayed = _replay({"robots": "robots.txt", "ddg": "ddg.html", "google": "google.json"}[kind])
    if replayed is not None:
        return replayed
    return http_get(url, headers)


# ---- robots.txt ------------------------------------------------------------------------------------------------------

def robots_allows(robots_text, path=DDG_HTML_PATH, agent=USER_AGENT):
    """最長前綴優先(RFC 9309):找適用的 User-agent 群(自報 UA 的產品名命中 > `*`),Allow/Disallow 比最長者;沒規則 / 空檔 ⇒ 准
    (RFC 9309 §2.3.1:沒有 robots 就是全准;伺服器錯 5xx 由呼叫端當全禁)。"""
    if not isinstance(robots_text, str) or not robots_text.strip():
        return True
    product = agent.lower().split("(compatible;")[-1].split(";")[0].strip().split("/")[0] if "(compatible;" in agent else agent.split("/")[0].lower()
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
    chosen = None
    for group in groups:
        if any(a and a != "*" and a in product for a in group["agents"]):
            chosen = group
            break
    if chosen is None:
        for group in groups:
            if "*" in group["agents"]:
                chosen = group
                break
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


# ---- 兩個引擎(同一形狀 [{url, title, snippet}])------------------------------------------------------------------------

def _strip_tags(text):
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def parse_ddg_html(body):
    """DDG HTML 端點:每筆 `.result__a`(href 是 //duckduckgo.com/l/?uddg=<真網址>)+ `.result__snippet`。選擇器會漂 ⇒ fixture 紅了重錄。"""
    results = []
    blocks = re.split(r'<div class="result results_links', body)[1:]
    for block in blocks:
        link = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        href = html_lib.unescape(link.group(1))
        target = href
        if "uddg=" in href:
            target = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg", [""])[0])
        elif href.startswith("//"):
            target = "https:" + href
        snippet = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        if target.startswith(("http://", "https://")):
            results.append({"url": target, "title": _strip_tags(link.group(2)), "snippet": _strip_tags(snippet.group(1) if snippet else "")})
    return results


def parse_google_json(body):
    """Programmable Search JSON:items[].link / title / snippet。"""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise SearchError("Google 搜尋回的不是合法 JSON:%s" % exc)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        raise SearchError("Google 搜尋拒絕:%s" % (data["error"].get("message") or data["error"]))
    items = data.get("items") if isinstance(data, dict) else None
    results = []
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("link"), str) and item["link"].startswith(("http://", "https://")):
            results.append({"url": item["link"], "title": str(item.get("title") or ""), "snippet": str(item.get("snippet") or "")})
    return results


def google_search(query, key, cx, http=None):
    url = GOOGLE_ENDPOINT + "?" + urllib.parse.urlencode({"key": key, "cx": cx, "q": query, "num": PER_QUERY_MAX})
    status, body = _fetch("google", url, http, {"Accept": "application/json"})
    if status != 200:
        raise SearchError("Google 搜尋回 HTTP %d(key / cx 不對或配額用完);零候選" % status)
    return parse_google_json(body)[:PER_QUERY_MAX]


def ddg_search(query, http=None):
    url = DDG_HTML_URL + "?" + urllib.parse.urlencode({"q": query})
    status, body = _fetch("ddg", url, http, {"Accept": "text/html"})
    if status != 200:
        raise SearchError("DuckDuckGo 回 HTTP %d(通常是擋機器人);不繞、不猜,零候選" % status)
    return parse_ddg_html(body)[:PER_QUERY_MAX]


# ---- 每日計數(本機檔)----------------------------------------------------------------------------------------------------

def _usage_path(environ):
    root, _source = state_root(environ)
    return os.path.join(root, *USAGE_PARTS)


def _today(clock=None):
    return (clock or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%d")


def usage_today(environ, clock=None):
    path = _usage_path(environ)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return 0
    if not isinstance(record, dict) or record.get("day") != _today(clock):
        return 0
    count = record.get("count")
    return count if isinstance(count, int) and count >= 0 else 0


def usage_bump(environ, n, clock=None):
    path = _usage_path(environ)
    record = {"day": _today(clock), "count": usage_today(environ, clock) + n}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return record["count"]


# ---- 候選判準 ----------------------------------------------------------------------------------------------------------

_PUNCT_WS = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text):
    """NFKC → 去空白與標點(含全形)→ 小寫。"""
    return _PUNCT_WS.sub("", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _host_path(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host, (parts.path or "/").rstrip("/") or "/"


def candidates(results, brand, adapter_for=None, test_mutation="", domain=None):
    """→ (候選 [{url, title, adapter}], 跳過訊息 [str])。
    刀 search-skip-brand-match:不比品牌名 ⇒ H3 必紅(別家會進來)。"""
    adapter_for = intake_sources.adapter_for if adapter_for is None else adapter_for
    requested = str(domain or "").strip().lower()
    domain_host = _host_path(requested if "://" in requested else "https://" + requested)[0] if requested else ""
    key = normalize(brand)
    if not key:
        raise SearchError("先設名字:品牌名標準化後是空的")
    kept, skipped, seen_pair, seen_host = [], [], set(), set()
    for item in results:
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        host, path = _host_path(url)
        hit = test_mutation == "search-skip-brand-match" or key in normalize(item.get("title")) or key in normalize(path)
        if not hit:
            continue
        if (host, path) in seen_pair:
            continue
        seen_pair.add((host, path))
        if host in seen_host:
            skipped.append("跳過:%s 同一站已有一筆(留第一)" % url)
            continue
        seen_host.add(host)
        if len(kept) >= CANDIDATES_MAX:
            skipped.append("跳過:候選已滿 %d 筆(%s)" % (CANDIDATES_MAX, url))
            continue
        explicit = bool(domain_host and host == domain_host)
        found = adapter_for(url, explicit=True) if explicit else adapter_for(url)
        if not found:
            skipped.append("跳過:沒有 %s 的轉接器(%s)" % (host, url))
            continue
        kept.append({"url": url, "title": str(item.get("title") or ""), "adapter": found[0]})
        if explicit:
            kept[-1]["explicit"] = True
    return kept, skipped


# ---- 一次搜尋(查詢 → 引擎 → 候選)-----------------------------------------------------------------------------------------

def run_search(name, environ, city=None, domain=None, http=None, clock=None, out=None, test_mutation=""):
    """→ (候選, 印給人的行)。具名停手都是 SearchError(零搜尋請求的那幾種在發任何請求**之前**就 raise)。
    刀 search-ignore-robots:robots 禁也照打 ⇒ H2 必紅。"""
    say = out or (lambda _line: None)
    queries = build_queries(name, city, domain)
    used = usage_today(environ, clock)
    if used + len(queries) > SEARCH_DAILY_MAX:
        raise SearchError("今天的搜尋次數 %d/%d,再 %d 條會超過每日上限;明天再試(計數檔 %s)"
                          % (used, SEARCH_DAILY_MAX, len(queries), _usage_path(environ)))
    engine, why = engine_for(environ)
    say("搜尋引擎:%s;查詢 %d 條:%s" % (why, len(queries), " / ".join(queries)))
    results = []
    # 計數以「實際發出」為準,每發一條就記一條(root2 複審 F2:全部成功後才加 ⇒ 第 3 條失敗前兩條沒計,連續失敗可繞過每日上限;少報是放行值)
    sent = [0]
    def send(fn):
        sent[0] += 1
        usage_bump(environ, 1, clock)
        return fn()
    if engine == "google":
        key, cx = environ.get(GOOGLE_KEY_ENV).strip(), environ.get(GOOGLE_CX_ENV).strip()
        for query in queries:
            results.extend(send(lambda: google_search(query, key, cx, http)))
    else:
        status, robots = _fetch("robots", DDG_ROBOTS_URL, http, {"Accept": "text/plain"})
        # RFC 9309 §2.3.1:200 照檔判;404 = 沒有 robots = 全准;其他(5xx / 429 …)= 全禁
        allowed = robots_allows(robots) if status == 200 else status == 404
        if test_mutation == "search-ignore-robots":
            allowed = True
        if not allowed:
            raise SearchError("沒有搜尋金鑰且 DuckDuckGo 不准機器抓(robots.txt 對 %s 是 Disallow);設 %s + %s 才能搜——零搜尋請求"
                              % (DDG_HTML_PATH, GOOGLE_KEY_ENV, GOOGLE_CX_ENV))
        for query in queries:
            results.extend(send(lambda: ddg_search(query, http)))
    kept, skipped = candidates(results, name, test_mutation=test_mutation, domain=domain)
    lines = ["搜尋結果 %d 筆 → 候選 %d 筆(判準:標題或網址路徑含整個品牌名「%s」,每站一筆,≤%d,要有轉接器)"
             % (len(results), len(kept), name, CANDIDATES_MAX)]
    for item in kept:
        lines.append("  候選:%s(%s)→ 走 %s 轉接器" % (item["url"], item["title"][:60], item["adapter"]))
    lines.extend("  " + line for line in skipped)
    for line in lines:
        say(line)
    return kept, lines


# ---- 合併時的來源標示 --------------------------------------------------------------------------------------------------------

def mark_search_ops(ops, snapshot, test_mutation=""):
    """merge_into_ops 產的 ops → 每顆 upsert_block 的 provenance 多 via:"search";profile 由搜尋帶入而**沒有**任何一顆帶 via 的區塊時,
    補一顆 hidden 的 text 顆(payload 印來源網址、provenance = 同一份取回來源 + via:search)讓 PAGE 的 draftHasSearchSource 看得到
    (規格 §二 2;登債 profile_provenance)。刀 search-drop-via:不標 via ⇒ H4 必紅(PAGE 端 H5 同一把刀名)。"""
    if test_mutation == "search-drop-via":
        return ops, []
    url = snapshot["source"]["url"]
    notes, profile_fields, marked = [], [], 0
    for op in ops:
        if op.get("op") == "upsert_block" and isinstance(op.get("provenance"), dict):
            op["provenance"]["via"] = VIA
            marked += 1
        elif op.get("op") == "set_profile":
            profile_fields = [k for k in ("display_name", "bio", "status_text", "socials") if k in op]
    if profile_fields:
        notes.extend("%s 由搜尋帶入:%s" % (field, url) for field in profile_fields)
        if not marked:
            ops.append({"op": "upsert_block", "block_type": "text", "visibility": "hidden",
                        "payload": {"text": "個人檔案(%s)由 AI 搜尋帶入:%s" % ("、".join(profile_fields), url), "format": "plain"},
                        "provenance": dict(intake_sources.provenance_of(snapshot), via=VIA)})
            notes.append("補一顆隱藏的文字顆記來源(個人檔案沒有來源欄,PAGE 靠它判「含 AI 搜尋」)")
    return ops, notes
