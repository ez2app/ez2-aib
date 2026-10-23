#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""262(c):PAGE 寫入 API 的用戶端(只用標準函式庫)。

規格正本 `docs/spec/PAGE-WRITE-API-v1.md`。本檔只做傳輸與錯誤形狀,**不補語意**:
規格沒寫的東西這裡不發明,照原碼往上拋。

三件刻意的事:
1. **不送 cookie、不送 Origin。** 規格 §2.1:API 不讀 cookie(所以不需要 CSRF),
   帶 `Origin` 的請求一律 403。用 urllib 自己組 Request,不裝任何 cookie handler。
2. **錯誤碼原樣保留。** 契約與 workspace 的碼(`page_block_*`/`PAGE_*`)穿過 API,
   這裡不重新分類、不包一個泛用碼(規格 §七 A9 那把刀就是在守這件事)。
3. **這支用戶端裡沒有 publish 這條路。** 規格 §四說 `POST /publish` 存在而且對任何
   token 恆 403;但艦長 a seat 09-17 00:0x 判準②逐字是「**程式裡不准有 publish 呼叫**」,
   而「發布要你去後台按」需要的 `publish_url` 在 `POST /draft` 的回應體
   (規格 §5.2 `preview.publish_url`)裡本來就有 —— 所以**不打那支端點也拿得到那句話**。
   兩個要求同時滿足的唯一寫法就是這裡:不實作 `publish()`,連字面都不要有。
   ★ 派工單(root2 a seat)原話是「publish 403 時用回應體的 publish_url」,那條路要先
   打一次 publish 才會 403;我選了比較嚴的那條並已回報兩位,若他們要 403 那條再改。
   ★ 尺 `test-aib-page-chat.py` 有一格 grep 整個套件的 `/publish` 字面必須 0 命中(刀:加回去必紅)。
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE = "https://ez2.page"
API_PREFIX = "/api/v1/page"
USER_AGENT = "ez2-aib-page/1"
DEFAULT_TIMEOUT_S = 20
MAX_BODY_BYTES = 256 * 1024          # 規格 §2.1:請求體上限
ASSET_MAX_BYTES = 2 * 1024 * 1024     # 288:單張圖上傳上限(壓縮後)


class ApiError(Exception):
    """一次失敗的呼叫。`code` 是規格的錯誤碼字面,不是我們自己的分類。"""

    def __init__(self, http_status, code, details=None, request_id=None,
                 body=None, message=None):
        self.http_status = http_status
        self.code = code or ""
        self.details = list(details or [])
        self.request_id = request_id or ""
        self.body = body if isinstance(body, dict) else {}
        super().__init__(message or ("%s %s" % (http_status, self.code or "")).strip())


class TransportError(Exception):
    """連不上 / 逾時 / 回的不是 JSON。與 ApiError 分開:這一種重試有意義。"""


class PageApi:
    """一把 token 對一個頁面。沒有 token 也能用,但只能走 `public_html()`。"""

    def __init__(self, base_url=None, token=None, timeout=DEFAULT_TIMEOUT_S,
                 opener=None):
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.token = token or None
        self.timeout = timeout
        # opener 可注入 ⇒ 尺用本機假伺服器跑,不碰正式站。
        self._opener = opener or urllib.request.build_opener()

    # ---- 低層 -------------------------------------------------------------

    def _url(self, path):
        return self.base_url + API_PREFIX + path

    def _request(self, method, path, body=None, auth=True):
        data = None
        # 正式站前面的 Cloudflare 對 urllib 預設的 `Python-urllib/x` UA 回 403(error 1010),bind/start 第一步就進不去
        #   (root2 09-17 19:4x 端到端實測);與 public_html 同一個自報 UA,不再讓 urllib 自己填。
        headers = {"accept": "application/json", "user-agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if len(data) > MAX_BODY_BYTES:
                raise TransportError(
                    "請求體 %d 位元組,超過規格上限 %d —— 先把 ops 拆小,不要讓伺服器回 413"
                    % (len(data), MAX_BODY_BYTES))
            headers["content-type"] = "application/json"
        if auth:
            if isinstance(self.token, str) and self.token.upper().startswith("EZP-"):
                raise TransportError("序號不能當助理憑證寫頁;先跑 `aib page bind --serial <序號>`")
            if not self.token:
                raise TransportError("這條路要憑證,但這台機器還沒綁:先跑 `aib page bind`")
            headers["authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self._url(path), data=data,
                                         headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:            # 4xx/5xx 仍有回應體
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError("連不上 %s:%s" % (self.base_url, exc))
        return self._parse(status, raw)

    @staticmethod
    def _parse(status, raw):
        text = (raw or b"").decode("utf-8", "replace")
        try:
            payload = json.loads(text) if text.strip() else {}
        except RecursionError:                                # 舊直譯器遇到極深巢狀(例 6 萬層 [)
            raise TransportError("回應巢狀太深(HTTP %s)" % status)
        except ValueError:
            # 413/415/405 規格說是純文字;這時 code 用 HTTP 狀態表示,不編一個假碼。
            if 200 <= status < 300:
                raise TransportError("回應不是 JSON(HTTP %s)" % status)
            raise ApiError(status, "", details=[text[:200]],
                           message="HTTP %s(回應不是 JSON)" % status)
        if not isinstance(payload, dict):
            raise TransportError("回應不是 JSON 物件(HTTP %s)" % status)
        if 200 <= status < 300:
            return payload
        raise ApiError(status, payload.get("error"),
                       details=payload.get("details"),
                       request_id=payload.get("request_id"),
                       body=payload)

    # ---- 端點(規格 §二總表) ----------------------------------------------

    def bind_start(self, seat_label, machine=None):
        body = {"seat_label": seat_label}
        if machine:
            body["machine"] = machine   # 10-21 @9f808:配對碼也綁機器(跟 /bind/serial 同形狀)
        return self._request("POST", "/bind/start", body, auth=False)

    def bind_serial(self, serial, seat_label, machine):
        return self._request("POST", "/bind/serial", {
            "serial": serial, "seat_label": seat_label, "machine": machine,
        }, auth=False)

    def bind_revoke(self):
        # 契約(@9f808 10-21):bearer = 要撤銷的那把、body 必須空(帶 body ⇒ 400 page_api_body_invalid)
        return self._request("POST", "/bind/revoke")

    def machines_list(self):
        # 契約 machines_list(@9f808 10-22,核心 #261):同帳號機器 fp / name / last_used_at / live_tokens / this
        return self._request("GET", "/machines")

    def machine_revoke(self, fp):
        # 契約 machine_revoke:本體空;那台每把 token 各回一筆 {kind, id, outcome}
        return self._request("POST", "/machines/%s/revoke" % fp)

    def bind_poll(self, bind_ticket):
        return self._request("POST", "/bind/poll", {"bind_ticket": bind_ticket},
                             auth=False)

    def get_blocks(self):
        """已發布的頁與區塊。"""
        return self._request("GET", "/blocks")

    def get_draft(self):
        """草稿;沒草稿是 `has_draft:false`,不是 404(規格 §二)。"""
        return self._request("GET", "/draft")

    def get_stats(self):
        """317 第 16 項(RL-132 A42):每頁基本統計 {stats:{day, retention_days, today|last_7|last_30:{views, clicks{block_id{slot}}, days}}};
        唯讀、不扣 AIB 配額;伺服器沒開統計 ⇒ 404 page_api_not_found(呼叫端翻人話)。"""
        return self._request("GET", "/stats")

    def post_draft(self, operation_key, expected_draft_revision, ops):
        return self._request("POST", "/draft", {
            "operation_key": operation_key,
            "expected_draft_revision": expected_draft_revision,
            "ops": list(ops),
        })

    def put_appearance(self, operation_key, theme_id, shape_id):
        """262(d) 增補 §二:PUT /appearance(寫註冊表,立刻生效在公開頁;同 operation key 冪等)。
        兩欄齊給(theme_id + shape_id),不用 template_id——skill 不動形狀,形狀是讀回來原樣帶過的。"""
        return self._request("PUT", "/appearance", {
            "operation_key": operation_key, "theme_id": theme_id, "shape_id": shape_id,
        })

    def put_profile(self, operation_key, fields):
        """262(d) 增補 §二:PUT /profile(display_name / bio / status_text / socials[{platform,url}] 任幾欄;
        空字串=清空;socials 新增或改網址要過同意帳本,缺同意 422 PAGE_SOCIAL_CONSENT_MISSING)。"""
        body = {"operation_key": operation_key}
        body.update(fields)
        return self._request("PUT", "/profile", body)

    def post_asset(self, operation_key, data, filename, alt="", content_type="application/octet-stream"):
        """288:上傳一張圖(multipart 自己組,不加相依)。★ 不走 _request 的 MAX_BODY_BYTES(那是 JSON 的 256KB),自己上限 2MB。"""
        if len(data) > ASSET_MAX_BYTES:
            raise TransportError("圖 %d 位元組,超過上傳上限 %d —— 先縮圖,不要讓伺服器回 413" % (len(data), ASSET_MAX_BYTES))
        if not self.token:
            raise TransportError("這條路要憑證,但這台機器還沒綁:先跑 `aib page bind`")
        boundary = "----ez2aib" + uuid.uuid4().hex
        safe_name = os.path.basename(filename or "upload").replace('"', "")
        parts = []
        for name, value in (("operation_key", operation_key), ("alt", alt)):
            if value is None or value == "":
                if name == "operation_key":
                    raise TransportError("operation_key 不可空")
                continue
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, name, value)).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                      % (boundary, safe_name, content_type)).encode("utf-8"))
        parts.append(bytes(data))
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        body = b"".join(parts)
        headers = {"accept": "application/json", "user-agent": USER_AGENT,
                   "content-type": "multipart/form-data; boundary=" + boundary,
                   "authorization": "Bearer " + self.token}
        request = urllib.request.Request(self._url("/assets"), data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=max(self.timeout, 60)) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError("連不上 %s:%s" % (self.base_url, exc))
        return self._parse(status, raw)

    def oneshot_call(self, method, path, body=None):
        """`aib page oneshot` 的組版器代打:回 (HTTP 狀態, 回應體),4xx/5xx 不拋(組版器自己看狀態碼)。

        只准 oneshot.ALLOWED_ROUTES 那五條(草稿讀寫、線上顆讀取、個人檔案 / 外觀寫進草稿);
        這裡再擋一次,不靠呼叫端自律。
        """
        from oneshot import ALLOWED_ROUTES   # 同套件;晚一點 import,避免 api 反過來依賴組版器
        if (method, path) not in ALLOWED_ROUTES:
            raise ValueError("oneshot 不准打 %s %s" % (method, path))   # 程式錯,不是連線問題(別被當成「送出後斷線」)
        try:
            return 200, self._request(method, path, body)
        except ApiError as exc:
            payload = dict(exc.body) if exc.body else {"error": exc.code, "details": exc.details}
            return exc.http_status, payload

    def get_preview(self, fmt=None):
        path = "/preview" + ("?format=" + fmt if fmt else "")
        return self._request("GET", path)

    def post_usage(self, receipt):
        """用量收據(規格 v2 §十三)。與 `/draft` **完全分開的一條路**:
        它失敗不准影響草稿寫入(§13.5 第 1 條),所以這裡不共用任何重試或狀態。"""
        return self._request("POST", "/usage", receipt)

    # ---- 作品提交(契約 page-api.json creator_*)----------------------------

    def creator_upload(self, path, tar_bytes, machine_id, manifest_sha256, rev):
        """POST 本體直接是 tar(不走 _request:那條只收 JSON、上限 256KB;大小上限在伺服器設定表,這裡不寫死)。
        回 (http 狀態, 回應) —— 202 新件、200 同包冪等回原件,呼叫端要分得出來。"""
        if not self.token:
            raise TransportError("這條路要憑證,但這台機器還沒綁:先跑 `aib page bind`")
        headers = {"accept": "application/json", "user-agent": USER_AGENT, "content-type": "application/x-tar",
                   "authorization": "Bearer " + self.token, "x-aib-machine": machine_id,
                   "x-aib-manifest-sha256": manifest_sha256, "x-aib-rev": str(int(rev))}
        request = urllib.request.Request(self._url(path), data=bytes(tar_bytes), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=max(self.timeout, 60)) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError("連不上 %s:%s" % (self.base_url, exc))
        return status, self._parse(status, raw)

    def creator_status(self, submission_id):
        return self._request("GET", "/creator/submissions/" + submission_id)

    def creator_list(self, cursor=None, limit=20):
        q = "?mine=1&limit=%d" % int(limit) + ("&cursor=" + urllib.parse.quote(cursor, safe="") if cursor else "")
        return self._request("GET", "/creator/submissions" + q)

    # ---- 外掛下載(契約 page-api.json plugins_*)------------------------------

    def plugins_catalog(self, max_bytes=1024 * 1024):
        """公開目錄;回應(含錯誤體)最多讀 max_bytes,太大 ⇒ 回 {}(呼叫端判 plugin_catalog_invalid)。"""
        status, raw = self._get_raw("/plugins/catalog", {"accept": "application/json", "user-agent": USER_AGENT}, max_bytes)
        if 200 <= status < 300 and len(raw) > max_bytes:
            return {}
        return self._parse(status, raw)

    @staticmethod
    def _read_capped(response, limit, total_s):
        """最多讀 limit 位元組、總共最多 total_s 秒(socket 逾時只管每次讀;伺服器一次滴一個位元組會拖不完)。"""
        deadline = time.monotonic() + total_s
        read = getattr(response, "read1", None) or response.read
        buf = bytearray()
        while len(buf) < limit:
            if time.monotonic() > deadline:
                raise TransportError("回應讀超過 %d 秒" % total_s)
            chunk = read(min(65536, limit - len(buf)))
            if not chunk:
                break
            buf += chunk
        return bytes(buf)

    def _get_raw(self, path, headers, max_bytes):
        request = urllib.request.Request(self._url(path), headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=max(self.timeout, 60)) as response:
                return response.status, self._read_capped(response, max_bytes + 1, max(self.timeout, 60) * 3)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(64 * 1024)
            except (http.client.HTTPException, OSError):
                return exc.code, b""
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:   # 回應讀到一半斷(IncompleteRead)也算連不上
            raise TransportError("連不上 %s:%s" % (self.base_url, type(exc).__name__))

    def plugins_package(self, name, version, max_bytes, machine_id=None):
        """GET 簽章包本體(tgz,不走 _request:那條只收 JSON)。免費包不帶憑證;付費包(呼叫端給 machine_id)帶 bearer + X-AIB-Machine。
        回包的 bytes(最多讀 max_bytes + 1,呼叫端判太大);非 2xx ⇒ ApiError(同 _parse)。"""
        headers = {"accept": "application/gzip", "user-agent": USER_AGENT}
        if machine_id is not None:
            if not self.token:
                raise TransportError("這條路要憑證,但這台機器還沒綁:先跑 `aib page bind`")
            headers.update({"authorization": "Bearer " + self.token, "x-aib-machine": machine_id})
        path = "/plugins/packages/%s/%s.tgz" % (urllib.parse.quote(name, safe=""), urllib.parse.quote(version, safe=""))
        status, raw = self._get_raw(path, headers, max_bytes)
        if 200 <= status < 300:
            return raw
        self._parse(status, raw)
        raise ApiError(status, "", message="HTTP %s" % status)

    def plugins_license(self, addon, machine_id):
        if not self.token:
            raise TransportError("這條路要憑證,但這台機器還沒綁:先跑 `aib page bind`")
        headers = {"accept": "application/json", "user-agent": USER_AGENT,
                   "authorization": "Bearer " + self.token, "x-aib-machine": machine_id}
        status, raw = self._get_raw("/plugins/licenses/" + urllib.parse.quote(addon, safe=""), headers, 256 * 1024)
        return self._parse(status, raw)

    # ★ 這裡**刻意沒有** publish 方法,理由見檔頭第 3 點。要印「發布要你按」時,
    #   用 `post_draft()` 回應體裡的 preview.publish_url(規格 §5.2)。

    # ---- 未綁時的唯讀 -----------------------------------------------------

    def public_html(self, url):
        """未綁時只讀公開頁 HTML(第二臂)。不帶憑證、不帶 cookie、只 GET。

        ★ 回字串就結束:**不落檔、不進任何指紋**(規格沒要求留痕,我不自己發明;
          已在交件單問 root2/pm2,要留痕再加)。
        """
        request = urllib.request.Request(url, headers={
            "accept": "text/html",
            "user-agent": USER_AGENT + " (read-only)",
        }, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, "", details=["讀公開頁失敗"],
                           message="讀公開頁 HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError("讀不到公開頁 %s:%s" % (url, exc))
