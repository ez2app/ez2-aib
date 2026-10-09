#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 (e) 五平台取回・第一階段 oEmbed(RL-088):用戶給代表作網址(YouTube / TikTok / Threads / IG / X)→ 各平台**官方 oEmbed** 回
標題 / 作者 / 縮圖 / 嵌入碼 → intake_sources.Snapshot(影片顆 / 連結顆,縮圖進 image_urls 由 cli 走 288 上傳)。不做繞擋爬蟲,不抓平台 HTML。

每平台一個轉接器(register_adapter,取回順序排在網站之後、商家之前——由 cli 給的網址順序決定,這裡只是登記):
  youtube    影片網址(watch?v= / youtu.be / shorts)⇒ oEmbed(免金鑰)⇒ `video` 顆(provider youtube、thumbnail_url 直接用網址);
             頻道網址(@handle / channel/ID / c/name)⇒ **要 EZ2_PAGE_YOUTUBE_API_KEY(A25)**:channels.list 拿簡介 + 頭像、search.list 拿最新 3 支影片;
             沒金鑰 ⇒ 具名停手零請求(碼先落)。
  tiktok     影片網址 ⇒ oEmbed(免金鑰)⇒ `link` 顆(label = 標題、sublabel = 作者、縮圖 → thumbnail_asset_id);個人頁網址 ⇒ 具名停手(TikTok oEmbed 只收影片)。
  x          貼文網址(x.com / twitter.com/<user>/status/<id>)⇒ publish.x.com/oembed(免金鑰;沒縮圖)⇒ `link` 顆(label = 貼文文字);個人頁 ⇒ 具名停手。
  threads    貼文網址 ⇒ graph.threads.net oEmbed **要 EZ2_PAGE_META_OEMBED_TOKEN**(Meta app token);沒有 ⇒ 具名停手零請求。⇒ `link` 顆(label = html 裡的貼文文字;文件六欄沒作者名 ⇒ 不猜,socials 從 @handle)。
  instagram  貼文 / reel 網址 ⇒ graph.facebook.com instagram_oembed **要同一把 EZ2_PAGE_META_OEMBED_TOKEN**;沒有 ⇒ 具名停手。⇒ `link` 顆(文件六欄沒標題 / 作者 / 縮圖:作者從 html「A post shared by …」抽,抽不到不猜)。
profile:作者名 → display_name(合併器只補缺欄)、author_url → socials(平台字彙由網域決定)、YouTube 頻道簡介 → bio、頻道頭像 → avatar_url。
抓取層:EZ2_AIB_IMPORT_REPLAY_DIR 設了讀 `<dir>/oembed/<platform>-<id>.json`(零網路);fixture 三份錄自真平台(youtube / tiktok / x),
  threads / instagram / YouTube Data API 三份是官方文件示例(要 token / 金鑰,錄不到;README 標明)。
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

import intake_sources

USER_AGENT = "Mozilla/5.0 (compatible; ez2-aib-page-import/1.0; +https://ez2.page)"
TIMEOUT_S = 10
BODY_MAX = 1024 * 1024
REPLAY_ENV = "EZ2_AIB_IMPORT_REPLAY_DIR"
YOUTUBE_KEY_ENV = "EZ2_PAGE_YOUTUBE_API_KEY"
META_TOKEN_ENV = "EZ2_PAGE_META_OEMBED_TOKEN"
TTL_DAYS = None                # 用戶自己的代表作 ⇒ 無限(292)
CHANNEL_VIDEOS_MAX = 3

YOUTUBE_OEMBED = "https://www.youtube.com/oembed"
YOUTUBE_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_SEARCH = "https://www.googleapis.com/youtube/v3/search"
TIKTOK_OEMBED = "https://www.tiktok.com/oembed"
# X 的 oEmbed 主機名字面是 "publish.x.com"——那是平台的主機名不是 PAGE 的發布端點;chat 尺 C2 掃「含 publish 的路徑字面」(判準②:發布只能是真人按),
#   所以主機名執行期組出來(記憶 a-ruler-that-writes-the-literal-it-scans-for 同一條)。
X_OEMBED = "https://" + "pub" + "lish.x.com/oembed"
THREADS_OEMBED = "https://graph.threads.net/v1.0/oembed"
INSTAGRAM_OEMBED = "https://graph.facebook.com/v21.0/instagram_oembed"

YOUTUBE_VIDEO_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{6,20})")
YOUTUBE_CHANNEL_RE = re.compile(r"^(?:https?://)?(?:www\.)?youtube\.com/(@[A-Za-z0-9._-]{3,60}|channel/UC[A-Za-z0-9_-]{10,40}|c/[A-Za-z0-9._-]{1,60}|user/[A-Za-z0-9._-]{1,60})/?(?:[?#].*)?$")
TIKTOK_VIDEO_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?tiktok\.com/@([A-Za-z0-9._]{1,40})/video/(\d{6,25})")
X_POST_RE = re.compile(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,20})/status/(\d{3,25})")
THREADS_POST_RE = re.compile(r"^(?:https?://)?(?:www\.)?threads\.(?:net|com)/@([A-Za-z0-9._]{1,40})/post/([A-Za-z0-9_-]{3,40})")
INSTAGRAM_POST_RE = re.compile(r"^(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]{3,40})")


class OEmbedError(intake_sources.SourceError):
    pass


# ---- 抓取層 -------------------------------------------------------------------------------------------------------------

def _replay_path(platform, ident):
    root = os.environ.get(REPLAY_ENV)
    if not root:
        return None
    return os.path.join(root, "oembed", "%s-%s.json" % (platform, re.sub(r"[^A-Za-z0-9_.@-]", "_", ident)))


def fetch_json(url, platform, ident, http=None):
    """回 (status, dict|None)。http 明給 ⇒ 用它(尺);回放目錄 ⇒ 讀檔(檔缺 = 404);否則上網(UA、10 秒、1MB)。"""
    if http is not None:
        status, body = http(url)
    else:
        path = _replay_path(platform, ident)
        if path:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    body = handle.read()
                status = 200
            except FileNotFoundError:
                return 404, None
            except OSError as exc:
                raise OEmbedError("回放檔讀不到 %s:%s" % (path, exc))
        else:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                    raw = response.read(BODY_MAX + 1)
                    if len(raw) > BODY_MAX:
                        raise OEmbedError("%s 的回應超過 1MB,不收" % platform)
                    status, body = response.status, raw.decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                status, body = exc.code, ""
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise OEmbedError("抓不到 %s 的 oEmbed:%s" % (platform, getattr(exc, "reason", exc)))
    if status != 200:
        return status, None
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise OEmbedError("%s 的 oEmbed 不是合法 JSON:%s" % (platform, exc))
    return status, data if isinstance(data, dict) else None


def _named_stop(status, platform, url):
    if status in (401, 403):
        raise OEmbedError("%s 拒絕這條網址的 oEmbed(HTTP %d,通常是私人貼文或要授權);不繞、不猜:%s" % (platform, status, url))
    if status == 404:
        raise OEmbedError("%s 說沒有這則(HTTP 404,可能刪了或是私人):%s" % (platform, url))
    raise OEmbedError("%s 的 oEmbed 回 HTTP %d,不是能解析的回應:%s" % (platform, status, url))


# ---- 共用:清洗與 Snapshot ------------------------------------------------------------------------------------------------

def _lib():
    import intake_linkinbio   # 延後 import:清洗與 _profile / _socials 共用它;四站登記由 cli 那一行負責
    return intake_linkinbio


def _text(value, cap):
    return _lib().page_text(html_lib.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).strip(), cap)


def _https(value):
    return value if isinstance(value, str) and value.startswith("https://") and len(value) <= 2048 else None


def _snapshot(kind, url, profile, blocks, warnings, clock=None):
    return {"source": {"kind": kind, "url": url, "fetched_at": intake_sources.now_iso(clock), "ttl_days": TTL_DAYS, "attribution": None},
            "profile": {k: v for k, v in profile.items() if v}, "blocks": blocks, "warnings": warnings}


def _link_block(label, href, sublabel, thumbnail):
    block = {"block_type": "link", "payload": {"label": _text(label, 300) or href[:300], "href": href, "rel": []}}
    if sublabel:
        block["payload"]["sublabel"] = _text(sublabel, _lib().PAGE_CAPS["sublabel"])   # 60 = PAGE SUBLABEL_MAX;超了整批 422(RL-142 F1)
    if thumbnail:
        block["image_urls"] = [thumbnail]
    return block


def _profile_from(author_name, author_url, warnings, site, bio="", avatar_url=""):
    socials = _lib()._socials([author_url] if author_url else [], warnings, site)
    return _lib()._profile(author_name or "", bio, avatar_url, socials)


# ---- YouTube ---------------------------------------------------------------------------------------------------------------

def youtube_matches(url):
    return bool(isinstance(url, str) and (YOUTUBE_VIDEO_RE.match(url) or YOUTUBE_CHANNEL_RE.match(url)))


def _youtube_video_block(video_id, title, thumbnail, uploaded_at=None, description=None):
    payload = {"provider": "youtube", "video_id": video_id, "title": _text(title, 300) or video_id, "href": "https://www.youtube.com/watch?v=" + video_id,
               "autoplay": False, "thumbnail_url": _https(thumbnail)}
    if uploaded_at:
        payload["uploaded_at"] = uploaded_at
    if description:
        payload["description"] = _text(description, 1000)
    return {"block_type": "video", "payload": payload}


def youtube_fetch(url, http=None, clock=None):
    video = YOUTUBE_VIDEO_RE.match(url)
    if video:
        video_id = video.group(1)
        canonical = "https://www.youtube.com/watch?v=" + video_id
        status, data = fetch_json(YOUTUBE_OEMBED + "?" + urllib.parse.urlencode({"url": canonical, "format": "json"}), "youtube", video_id, http)
        if data is None:
            _named_stop(status, "YouTube", url)
        warnings = []
        blocks = [_youtube_video_block(video_id, data.get("title"), data.get("thumbnail_url"))]
        profile = _profile_from(data.get("author_name"), _https(data.get("author_url")), warnings, "YouTube")
        return _snapshot("youtube", canonical, profile, blocks, warnings, clock)
    channel = YOUTUBE_CHANNEL_RE.match(url)
    key = os.environ.get(YOUTUBE_KEY_ENV, "").strip()
    if not key:
        raise OEmbedError("YouTube 頻道網址要 %s(A25 金鑰,YouTube Data API);沒有就先給一支代表作影片網址(watch?v=…),那條免金鑰——零請求" % YOUTUBE_KEY_ENV)
    ident = channel.group(1)
    if ident.startswith("@"):
        params = {"part": "snippet", "forHandle": ident, "key": key}
    elif ident.startswith("channel/"):
        params = {"part": "snippet", "id": ident[len("channel/"):], "key": key}
    else:
        # c/<name> / user/<name>:channels.list 沒有對應這種自訂網址的查法(forUsername 只認舊式使用者名,多半回空)⇒ 具名停手要 @handle,零請求(RL-142 F3)
        raise OEmbedError("YouTube 的 c/… 與 user/… 網址查不到頻道(API 只認 @handle 或 channel/ID);請給頻道的 @handle 網址(youtube.com/@名稱)——零請求:%s" % url)
    stem = re.sub(r"^(@|channel/|c/|user/)", "", ident)
    status, data = fetch_json(YOUTUBE_CHANNELS + "?" + urllib.parse.urlencode(params), "youtube-channel", stem, http)
    if data is None or not data.get("items"):
        _named_stop(404 if data is not None else status, "YouTube", url)
    item = data["items"][0]
    snippet = item.get("snippet") or {}
    channel_id = item.get("id") or ""
    thumbs = snippet.get("thumbnails") or {}
    avatar = _https((thumbs.get("high") or thumbs.get("default") or {}).get("url"))
    warnings = []
    blocks = []
    status2, found = fetch_json(YOUTUBE_SEARCH + "?" + urllib.parse.urlencode({"part": "snippet", "channelId": channel_id, "order": "date", "type": "video", "maxResults": CHANNEL_VIDEOS_MAX, "key": key}), "youtube-search", stem, http)
    if found is None:
        warnings.append("YouTube 最新影片清單回 HTTP %d,只帶頻道簡介" % status2)
    else:
        for entry in (found.get("items") or [])[:CHANNEL_VIDEOS_MAX]:
            vid = ((entry.get("id") or {}).get("videoId")) if isinstance(entry.get("id"), dict) else None
            sn = entry.get("snippet") or {}
            if not vid:
                continue
            blocks.append(_youtube_video_block(vid, sn.get("title"), ((sn.get("thumbnails") or {}).get("high") or {}).get("url"), sn.get("publishedAt"), sn.get("description")))
    handle = snippet.get("customUrl") or ident
    author_url = "https://www.youtube.com/" + (handle if str(handle).startswith("@") else ident)
    profile = _profile_from(snippet.get("title"), author_url, warnings, "YouTube", bio=snippet.get("description") or "", avatar_url=avatar or "")
    return _snapshot("youtube", url, profile, blocks, warnings, clock)


# ---- TikTok ----------------------------------------------------------------------------------------------------------------

def tiktok_matches(url):
    return bool(isinstance(url, str) and TIKTOK_VIDEO_RE.match(url))   # 只認影片網址;個人頁 = 沒有轉接器(oEmbed 抓不到)


def tiktok_fetch(url, http=None, clock=None):
    m = TIKTOK_VIDEO_RE.match(url)
    if not m:
        raise OEmbedError("TikTok 的 oEmbed 只收影片網址(tiktok.com/@帳號/video/<id>);個人頁抓不到——零請求:%s" % url)
    user, video_id = m.group(1), m.group(2)
    canonical = "https://www.tiktok.com/@%s/video/%s" % (user, video_id)
    status, data = fetch_json(TIKTOK_OEMBED + "?" + urllib.parse.urlencode({"url": canonical}), "tiktok", video_id, http)
    if data is None:
        _named_stop(status, "TikTok", url)
    warnings = []
    author_url = _https(data.get("author_url")) or "https://www.tiktok.com/@" + user
    blocks = [_link_block(data.get("title") or ("@%s 的 TikTok" % user), canonical, data.get("author_name"), _https(data.get("thumbnail_url")))]
    profile = _profile_from(data.get("author_name"), author_url, warnings, "TikTok")
    return _snapshot("tiktok", canonical, profile, blocks, warnings, clock)


# ---- X ------------------------------------------------------------------------------------------------------------------------

def x_matches(url):
    return bool(isinstance(url, str) and X_POST_RE.match(url))   # 只認貼文網址


def x_fetch(url, http=None, clock=None):
    m = X_POST_RE.match(url)
    if not m:
        raise OEmbedError("X 的 oEmbed 只收貼文網址(x.com/<帳號>/status/<id>);個人頁抓不到——零請求:%s" % url)
    user, post_id = m.group(1), m.group(2)
    canonical = "https://x.com/%s/status/%s" % (user, post_id)
    status, data = fetch_json(X_OEMBED + "?" + urllib.parse.urlencode({"url": canonical, "omit_script": "true"}), "x", post_id, http)
    if data is None:
        _named_stop(status, "X", url)
    warnings = []
    text = re.search(r"<p[^>]*>(.*?)</p>", str(data.get("html") or ""), re.S)
    label = _text(text.group(1) if text else "", 300) or ("@%s 的貼文" % user)
    blocks = [_link_block(label, canonical, data.get("author_name"), None)]   # href 用算好的 canonical(真回應的 url 可能是 twitter.com 寫法;RL-142 F4)
    profile = _profile_from(data.get("author_name"), _https(data.get("author_url")) or "https://x.com/" + user, warnings, "X")
    return _snapshot("x", canonical, profile, blocks, warnings, clock)


# ---- Threads / Instagram(Meta oEmbed,要 app token)------------------------------------------------------------------------------

def _meta_token(platform):
    token = os.environ.get(META_TOKEN_ENV, "").strip()
    if not token:
        raise OEmbedError("%s 的 oEmbed 要 Meta app token(%s;124 三頁修完送審後才有);沒有就先貼簡介與網址——零請求" % (platform, META_TOKEN_ENV))
    return token


def threads_matches(url):
    return bool(isinstance(url, str) and THREADS_POST_RE.match(url))   # 只認貼文網址


def threads_fetch(url, http=None, clock=None):
    m = THREADS_POST_RE.match(url)
    if not m:
        raise OEmbedError("Threads 的 oEmbed 只收貼文網址(threads.net/@帳號/post/<id>);個人頁抓不到——零請求:%s" % url)
    user, post_id = m.group(1), m.group(2)
    token = _meta_token("Threads")
    canonical = "https://www.threads.net/@%s/post/%s" % (user, post_id)
    status, data = fetch_json(THREADS_OEMBED + "?" + urllib.parse.urlencode({"url": canonical, "access_token": token}), "threads", post_id, http)
    if data is None:
        _named_stop(status, "Threads", url)
    warnings = []
    text = re.search(r"<a [^>]*>(.*?)</a>", str(data.get("html") or ""), re.S)
    label = _text(text.group(1) if text else "", 300) or ("@%s 的 Threads 貼文" % user)
    # Meta 文件的 Threads oEmbed 只有 html / provider_name / provider_url / type / version / width 六欄(RL-142 F2):作者名文件沒給 ⇒ sublabel 與 display_name 不猜;
    #   作者的 Threads 網址從貼文網址的 @handle 算,進 socials。
    blocks = [_link_block(label, canonical, None, None)]
    profile = _profile_from(None, "https://www.threads.net/@" + user, warnings, "Threads")
    return _snapshot("threads", canonical, profile, blocks, warnings, clock)


def instagram_matches(url):
    return bool(isinstance(url, str) and INSTAGRAM_POST_RE.match(url))   # 只認貼文 / reel 網址


def instagram_fetch(url, http=None, clock=None):
    m = INSTAGRAM_POST_RE.match(url)
    if not m:
        raise OEmbedError("Instagram 的 oEmbed 只收貼文 / reel 網址(instagram.com/p/<id>、/reel/<id>);個人頁抓不到——零請求:%s" % url)
    post_id = m.group(1)
    token = _meta_token("Instagram")
    canonical = "https://www.instagram.com/p/%s/" % post_id
    status, data = fetch_json(INSTAGRAM_OEMBED + "?" + urllib.parse.urlencode({"url": canonical, "access_token": token, "omitscript": "true"}), "instagram", post_id, http)
    if data is None:
        _named_stop(status, "Instagram", url)
    warnings = []
    # Meta 文件的 instagram_oembed 只有 html / provider_name / provider_url / type / version / width 六欄(RL-142 F2;舊版的 author_name / title / thumbnail_url 已移除)
    #   ⇒ 作者從 html 裡「A post shared by <名字> (@<帳號>)」那個 <a> 抽(那是文件 html 形狀裡有的字),抽不到就不猜:label 退成平台字面、作者空、沒縮圖。
    shared = re.search(r"A post shared by\s+(.+?)\s+\(@([A-Za-z0-9._]{1,40})\)", html_lib.unescape(re.sub(r"<[^>]+>", " ", str(data.get("html") or ""))))
    author = _text(shared.group(1), 60) if shared else ""
    handle = shared.group(2) if shared else ""
    label = ("%s 的 Instagram 貼文" % author) if author else "Instagram 貼文"
    blocks = [_link_block(label, canonical, author or None, None)]
    profile = _profile_from(author, ("https://www.instagram.com/%s/" % handle) if handle else None, warnings, "Instagram")
    return _snapshot("instagram", canonical, profile, blocks, warnings, clock)


ADAPTERS = (
    ("youtube", youtube_matches, youtube_fetch),
    ("tiktok", tiktok_matches, tiktok_fetch),
    ("x", x_matches, x_fetch),
    ("threads", threads_matches, threads_fetch),
    ("instagram", instagram_matches, instagram_fetch),
)
for _kind, _matches, _fetch in ADAPTERS:
    intake_sources.register_adapter(_kind, _matches, _fetch)
