#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""288 `aib page upload <path…>`:本地縮圖 → 轉 webp → 上傳 POST /api/v1/page/assets,一檔一行、一檔失敗不影響其他檔。

  1. 讀檔 → 看魔數(png / jpg / webp,副檔名不算數);不是就具名拒、**不送**。
  2. 縮圖:長邊 ≤1600、轉 webp q82。工具順位 Pillow → 沒有就 sips -Z 1600 + cwebp -q 82 → 都沒有就原檔上傳並印警告。
     縮圖結果落 <狀態根>/tmp/,送完刪。Pillow / cwebp 轉出來的 webp 都不帶 EXIF(295);伺服器端再剝一次。
  3. 上傳:api.post_asset(自己組 multipart,不受 MAX_BODY_BYTES 256KB 管,自己上限 2MB)。
  4. 印 `<原檔名> → <asset_id>(<寬>×<高>,<KB>)`;失敗印契約碼翻的人話;回傳碼 0 全成 / 1 有一檔以上失敗。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from api import ApiError, TransportError

MAX_EDGE = 1600
WEBP_QUALITY = 82
UPLOAD_MAX_BYTES = 2 * 1024 * 1024
PNG_SIG = b"\x89PNG\r\n\x1a\n"


class UploadError(Exception):
    pass


def sniff(data):
    """魔數定型別;不是三種之一回 None。與 PAGE 端 shared/ez2-page-image-bytes.js sniff() 同一套判準。"""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:8] == PNG_SIG:
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


def dimensions(data):
    """從標頭讀寬高(png IHDR / jpg SOFn / webp VP8·VP8L·VP8X);讀不到回 None。與 PAGE 端 dimensions() 同一套。"""
    kind = sniff(data)
    try:
        if kind == "png":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if kind == "jpg":
            at = 2
            while at + 4 <= len(data):
                if data[at] != 0xFF:
                    at += 1
                    continue
                marker = data[at + 1]
                if marker in (0xD8, 0x01, 0xFF) or 0xD0 <= marker <= 0xD7:
                    at += 2
                    continue
                if marker in (0xD9, 0xDA):
                    return None
                length = int.from_bytes(data[at + 2:at + 4], "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return int.from_bytes(data[at + 7:at + 9], "big"), int.from_bytes(data[at + 5:at + 7], "big")
                at += 2 + length
            return None
        if kind == "webp":
            tag, d = data[12:16], 20
            if tag == b"VP8X":
                return 1 + int.from_bytes(data[d + 4:d + 7], "little"), 1 + int.from_bytes(data[d + 7:d + 10], "little")
            if tag == b"VP8 ":
                return int.from_bytes(data[d + 6:d + 8], "little") & 0x3FFF, int.from_bytes(data[d + 8:d + 10], "little") & 0x3FFF
            if tag == b"VP8L":
                b = int.from_bytes(data[d + 1:d + 5], "little")
                return 1 + (b & 0x3FFF), 1 + ((b >> 14) & 0x3FFF)
    except (IndexError, ValueError):
        return None
    return None


def _pillow():
    try:
        from PIL import Image  # noqa: F401
        return Image
    except Exception:          # ImportError 與任何載入失敗都算「沒有」
        return None


def shrink(path, tmp_dir, environ=None, tools=None):
    """回 (bytes, tool, warning)。tool ∈ pillow / sips / none。"""
    environ = os.environ if environ is None else environ
    tools = tools or {}
    image_cls = tools.get("pillow", _pillow)()
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(tmp_dir, base + ".webp")
    if image_cls is not None:
        with image_cls.open(path) as img:
            img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            img.save(out, format="WEBP", quality=WEBP_QUALITY)   # 不給 exif= ⇒ 不帶 EXIF
        with open(out, "rb") as handle:
            return handle.read(), "pillow", ""
    which = tools.get("which", lambda name: shutil.which(name, path=environ.get("PATH", "")))
    sips, cwebp = which("sips"), which("cwebp")
    if sips and cwebp:
        run = tools.get("run", subprocess.run)
        with open(path, "rb") as handle:
            head = handle.read(65536)
        size = dimensions(head)
        source = path
        # ★ sips -Z 會把小圖放大到 1600(A5 那格 40×30 被拉成 1600×1200 抓到的);長邊本來就 ≤1600 就不縮,只轉 webp。
        if size is None or max(size) > MAX_EDGE:
            mid = os.path.join(tmp_dir, base + ".sips.png")
            r1 = run([sips, "-s", "format", "png", "-Z", str(MAX_EDGE), path, "--out", mid], capture_output=True, text=True, timeout=120)
            if r1.returncode != 0 or not os.path.exists(mid):
                raise UploadError("sips 縮圖失敗:%s" % (r1.stderr or r1.stdout or "").strip()[:200])
            source = mid
        r2 = run([cwebp, "-quiet", "-q", str(WEBP_QUALITY), "-metadata", "none", source, "-o", out], capture_output=True, text=True, timeout=120)
        if r2.returncode != 0 or not os.path.exists(out):
            raise UploadError("cwebp 轉檔失敗:%s" % (r2.stderr or r2.stdout or "").strip()[:200])
        with open(out, "rb") as handle:
            return handle.read(), "sips", ""
    with open(path, "rb") as handle:
        data = handle.read()
    return data, "none", "沒縮圖工具(Pillow 或 sips+cwebp),原檔 %d KB 上傳(>2MB 會被拒)" % max(1, len(data) // 1024)


def explain(exc):
    """把契約碼翻成人話;翻不出來就印原碼,不假裝看得懂。"""
    if isinstance(exc, TransportError):
        return "連不上或逾時:%s" % exc
    code = exc.code or ""
    if exc.http_status == 415:
        return "伺服器不收這種檔(只收 png / jpg / webp,看檔案內容不看副檔名)"
    if exc.http_status == 413:
        return "檔太大(壓縮後仍超過 2MB)"
    if exc.http_status == 429 and code == "page_api_asset_quota_exceeded":
        details = exc.body.get("details") if isinstance(exc.body, dict) else None
        used = details.get("used") if isinstance(details, dict) else "?"
        return "這頁的圖已滿(%s / 50 張),先在後台刪一些或換一頁" % used
    if exc.http_status == 429:
        return "送太快了,等一分鐘再試"
    if exc.http_status == 403 and code == "page_api_scope_mismatch":
        return "這把憑證是舊的(沒有上傳權限),重跑 aib page bind"
    if exc.http_status == 401:
        return "憑證失效或過期,重跑 aib page bind"
    return "%s(HTTP %s)" % (code or "未知錯誤", exc.http_status)


def run_upload(api, paths, out, seat_addr, alt="", clock=None, environ=None, tmp_root=None, tools=None, key_fn=None, ids=None):
    """逐檔獨立;回 0 全部成功,1 至少一檔失敗。ids(289 (d)):給一個 dict 就把 路徑 → asset_id 填進去(取回的圖要接回區塊 payload)。"""
    from chat import operation_key
    environ = os.environ if environ is None else environ
    key_fn = key_fn or operation_key
    failures = 0
    tmp_dir = tempfile.mkdtemp(prefix="page-upload-", dir=tmp_root)
    try:
        for seq, path in enumerate(paths, 1):
            name = os.path.basename(path)
            try:
                with open(path, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                out("%s ✗ 讀不到:%s" % (name, exc))
                failures += 1
                continue
            if sniff(raw) is None:
                out("%s ✗ 不是 png / jpg / webp(看檔案內容不看副檔名),不送" % name)
                failures += 1
                continue
            try:
                data, tool, warning = shrink(path, tmp_dir, environ=environ, tools=tools)
            except UploadError as exc:
                out("%s ✗ %s" % (name, exc))
                failures += 1
                continue
            if warning:
                out("%s ⚠ %s" % (name, warning))
            if len(data) > UPLOAD_MAX_BYTES:
                out("%s ✗ 壓完還是 %d KB,超過 2MB,不送" % (name, len(data) // 1024))
                failures += 1
                continue
            send_name = os.path.splitext(name)[0] + ".webp" if tool != "none" else name
            try:
                answer = api.post_asset(key_fn(seat_addr, clock=clock, seq=seq, tag="a"), data, send_name, alt=alt)
            except (ApiError, TransportError) as exc:
                out("%s ✗ %s" % (name, explain(exc)))
                failures += 1
                continue
            out("%s → %s(%s×%s,%d KB%s)" % (name, answer.get("asset_id"), answer.get("width"), answer.get("height"),
                                            max(1, int(answer.get("bytes") or len(data)) // 1024),
                                            ",已存在" if answer.get("replayed") else ""))
            if ids is not None and answer.get("asset_id"):
                ids[path] = answer["asset_id"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return 1 if failures else 0
