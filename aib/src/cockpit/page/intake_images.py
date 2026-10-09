#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""289 (d):取回快照裡的圖(block.image_urls)→ 下載 → 288 壓縮上傳(upload.run_upload)→ 填回 payload(shop.image_asset_id / image.asset_id)。

在 merge_into_ops **之前**對 Snapshot 做(合併器只認 payload;image_urls 是轉接器層的暫存欄)。規則:
  - 只收 https、逾時 10 秒、≤ IMAGE_MAX_BYTES(5MB)、內容魔數要是 png / jpg / webp(upload.sniff,看內容不看副檔名);
  - 一張失敗不影響別張(記 notes,那顆照種、只是沒圖);
  - 上傳走 run_upload(縮圖、webp、2MB 上限、operation key 都是它的);ids 回 路徑 → asset_id。
回放:EZ2_AIB_IMPORT_REPLAY_DIR 設了讀 `<dir>/images/<檔名>`(檔名 = 網址最後一段),零網路。
刀 images-skip-upload:不上傳、image_urls 原樣留著 ⇒ W4 必紅(shop 顆沒有 image_asset_id)。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import upload as upload_mod

IMAGE_MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 10
REPLAY_ENV = "EZ2_AIB_IMPORT_REPLAY_DIR"
USER_AGENT = "Mozilla/5.0 (compatible; ez2-aib-page-import/1.0; +https://ez2.page)"
ASSET_FIELD = {"shop": "image_asset_id", "image": "asset_id", "link": "thumbnail_asset_id"}   # 289 (e):連結顆縮圖(317 第 6 項 link media)


def _name_of(url):
    segs = [s for s in urlsplit(url).path.split("/") if s]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", segs[-1]) if segs else "image"


def download(url, dest_dir, http=None, seq=0):
    """→ 本機路徑;失敗回 (None, 原因)。暫存檔名 = 序號前綴 + 網址最後一段:兩張圖同檔名不同目錄(/p/a/cover.jpg、/p/b/cover.jpg)
    才不會在暫存目錄互蓋、讓兩顆拿到同一個 asset_id(root2 複審 289 (d) F1)。回放檔仍照原檔名讀。"""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None, "不是 https 網址"
    name = _name_of(url)
    if http is not None:
        raw = http(url)
    else:
        root = os.environ.get(REPLAY_ENV)
        if root:
            path = os.path.join(root, "images", name)
            try:
                with open(path, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                return None, "回放檔讀不到 %s:%s" % (path, exc)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                    raw = response.read(IMAGE_MAX_BYTES + 1)
            except urllib.error.HTTPError as exc:
                return None, "HTTP %d" % exc.code
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return None, "抓不到:%s" % getattr(exc, "reason", exc)
    if len(raw) > IMAGE_MAX_BYTES:
        return None, "超過 5MB"
    if upload_mod.sniff(raw) is None:
        return None, "不是 png / jpg / webp(看內容)"
    path = os.path.join(dest_dir, "%02d-%s" % (seq, name))
    with open(path, "wb") as handle:
        handle.write(raw)
    return path, ""


def materialize_images(snapshot, api, seat_addr, out, environ=None, tmp_root=None, http=None, test_mutation=""):
    """把 snapshot.blocks[*].image_urls 變成 asset_id 填進 payload,拿掉 image_urls。回 notes。"""
    notes = []
    if test_mutation == "images-skip-upload":
        return notes
    jobs = [(block, block["image_urls"][0]) for block in snapshot.get("blocks") or []
            if block.get("image_urls") and block.get("block_type") in ASSET_FIELD]
    if not jobs:
        return notes
    environ = os.environ if environ is None else environ
    tmp_dir = tempfile.mkdtemp(prefix="page-images-", dir=tmp_root)
    try:
        paths = {}
        for seq, (block, url) in enumerate(jobs, 1):
            path, why = download(url, tmp_dir, http, seq=seq)
            if path is None:
                notes.append("圖沒帶進來(%s):%s" % (why, url[:120]))
                block.pop("image_urls", None)
                continue
            paths[id(block)] = (block, path, url)
        if paths:
            ids = {}
            upload_mod.run_upload(api, [p for _b, p, _u in paths.values()], out, seat_addr, environ=environ, tmp_root=tmp_root, ids=ids)
            for block, path, url in paths.values():
                asset_id = ids.get(path)
                if asset_id:
                    block["payload"][ASSET_FIELD[block["block_type"]]] = asset_id
                    notes.append("%s 顆的圖已上傳 → %s(%s)" % (block["block_type"], asset_id, url[:80]))
                else:
                    notes.append("圖上傳失敗,那顆沒圖:%s" % url[:120])
                block.pop("image_urls", None)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return notes
