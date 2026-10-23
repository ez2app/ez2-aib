#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib create submit / status / list(10-23 Andy「像素工坊 + 一鍵上傳受審」;契約 contracts/page-api.json creator)。
網路、Touch ID 全換假的;暫存狀態根。

P1 好包:讀得出 kind / items / rev / manifest_sha256(= MANIFEST.json 的 sha256)
P2 壞包各回自己的碼:壓縮 / 不是 tar ⇒ unreadable;連結、..、絕對路徑、多餘頂層檔 ⇒ path_rejected;
   副檔名或魔術位元組不對 ⇒ file_type_rejected;清單多 / 少 / 雜湊錯 ⇒ manifest_mismatch;帶 author ⇒ unknown_field;
   件的 kind 不一樣 ⇒ kind_mixed;schema 錯、rev 跟編號對不上 ⇒ schema_unsupported;編號格式錯 ⇒ id_invalid
S1 送出:先本人證明、才連網;POST 本體 = tar 原樣、五個標頭;202 ⇒ creator_submitted、200 ⇒ creator_submitted_existing
S2 本人證明沒過 ⇒ rc 5、一個請求都沒送;S2b 正式路徑(不帶 prove,含 main)一定叫 chat_owner.prove
S3 本機檢查擋 ⇒ rc 2、沒叫本人證明、沒送;沒綁 ⇒ rc 2 creator_not_bound;binding 從憑證拿 machine_id
S4 伺服器錯:4xx 帶碼 ⇒ rc 3 原碼;401 被撤 ⇒ 原碼 + 何時被誰撤;404 沒碼 ⇒ creator_unsupported;
   503 帶碼 ⇒ rc 4 原碼;連不上 / 回應缺編號 ⇒ rc 4 creator_unreachable
S5 重送:路徑 /creator/submissions/{id}/resubmit、X-AIB-Rev 照包
S6 status:編號不合 ⇒ rc 2 不連網;文字輸出把伺服器給的字清掉控制 / 隱形字元,detail_url 不是 ez2.page 不印
S7 --json 一行回條、rc 照傳;每個代碼四語都有 hint
   稀疏成員 ⇒ path_rejected(小包補零吃爆記憶體);壞稀疏表 / 截斷 ⇒ unreadable(不帶 traceback)
   標頭 size 負數(舊 tarfile 無限迴圈)、成員超過 MAX_MEMBERS ⇒ unreadable
   伺服器純文字 413 / 415 ⇒ too_large / media_type_rejected;不認得的碼 ⇒ creator_refused(原碼清過才進 hint)
刀(複本):K1 不叫本人證明 ⇒ S2 紅;K2 不比雜湊 ⇒ P2 紅;K3 收連結 / 稀疏 ⇒ P2 紅;K4 預設不叫本人證明 ⇒ S2b 紅;
    K5 不擋成員超量 ⇒ P2 紅
"""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit" / "page", REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import api as A  # noqa: E402
import create_cli as CC  # noqa: E402
import store  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

C = json.loads((REPO / "contracts" / "page-api.json").read_text(encoding="utf-8"))
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
SID = "sub_" + "A1b2C3d4E5f6G7"
MID = "11111111-2222-3333-4444-555555555555"
REC = {"assistant_token": "ast_create_x", "tenant_id": "tenant_ab12", "page_id": "page_cd34", "slug": "andy", "machine_id": MID}


def sub_obj(state="queued", **kw):
    return dict({"id": SID, "rev": 1, "state": state, "kind": "desk",
                 "items": [{"item_id": "desk_a", "state": "queued", "reasons": [], "hint": None}]}, **kw)


def make_tar(files=None, creator=None, manifest=None, extra=None, gz=False, links=()):
    creator = creator if creator is not None else {"schema": "aib-creation/v1", "category": "furniture", "kind": "desk",
                                                   "title": {"zh-TW": "木桌"}, "rev": 1, "submission_id": None,
                                                   "items": [{"item_id": "desk_a", "files": ["assets/desk.png"]}]}
    files = dict(files if files is not None else {"assets/desk.png": PNG})
    files["creator.json"] = json.dumps(creator, ensure_ascii=False).encode()
    if manifest is None:
        manifest = {"schema": "aib-creation-manifest/v1", "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    files["MANIFEST.json"] = json.dumps(manifest).encode()
    files.update(extra or {})
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for name, target in links:
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
    return buf.getvalue()


def pax_tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for ti, d in entries:
            tf.addfile(ti, io.BytesIO(d))
    return buf.getvalue()


def sparse_tar(realsize, smap=None):
    """GNU sparse 1.0(pax 標頭):512 位元組的包,讀出來會補零到 realsize(審查員 10-23 抓的小包吃爆記憶體)。"""
    smap = smap or b"1\n%d\n0\n" % realsize
    ti = tarfile.TarInfo("GNUSparseFile.0/x")
    ti.size = 512
    ti.pax_headers = {"GNU.sparse.major": "1", "GNU.sparse.minor": "0", "GNU.sparse.name": "assets/a.txt",
                      "GNU.sparse.realsize": str(realsize)}
    return pax_tar([(ti, smap + b"\0" * (512 - len(smap)))])


def neg_size_tar():
    """第二個標頭 size 用 base-256 寫成 -512(CVE-2025-8194):沒修的 tarfile 讀回同一個標頭、無限迴圈(審查員 10-23 第 2 輪)。"""
    raw = bytearray(make_tar())
    h = tarfile.open(fileobj=io.BytesIO(bytes(raw))).getmembers()[1].offset
    raw[h + 124:h + 136] = tarfile.itn(-512, 12, tarfile.GNU_FORMAT)
    raw[h + 148:h + 156] = b"        "
    raw[h + 148:h + 156] = b"%06o\0 " % sum(raw[h:h + 512])
    return bytes(raw)


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, status=202, payload=None, boom=False, log=None):
        self.status, self.payload, self.boom = status, payload if payload is not None else {"ok": True, "submission": sub_obj()}, boom
        self.seen, self.log = [], log if log is not None else []

    def open(self, req, timeout=None):
        self.seen.append(req)
        self.log.append("net")
        if self.boom:
            raise urllib.error.URLError("down")
        raw = json.dumps(self.payload).encode()
        if self.status >= 400:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(raw))
        return _Resp(self.status, raw)


class TextOpener(FakeOpener):
    def open(self, req, timeout=None):
        self.seen.append(req)
        raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(b"<html>413 Request Entity Too Large</html>"))


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-create-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}
        saved = {k: os.environ.get(k) for k in ("EZ2_AIB_LOCALE", "EZ2_AIB_STATE_ROOT")}
        os.environ.update({"EZ2_AIB_LOCALE": "zh-TW", "EZ2_AIB_STATE_ROOT": td.name})

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        self.log = []

    def tar(self, data, name="pkg.tar"):
        p = self.td / name
        p.write_bytes(data)
        return str(p)

    def prove(self, ok=True):
        def f(reason, env):
            self.log.append("prove")
            self.reason = reason
            return None if ok else "touchid_cancelled"
        return f

    def submit(self, data, opener, ok=True):
        api = A.PageApi(base_url="https://page.test", token=REC["assistant_token"], opener=opener)
        return CC.submit(self.env, self.tar(data), prove=self.prove(ok), api=api, machine_id=MID)


class Package(unittest.TestCase):
    def test_p1_good(self):
        data = make_tar()
        pkg, code = CC.read_package(data)
        self.assertIsNone(code)
        self.assertEqual((pkg["kind"], pkg["items"], pkg["rev"], pkg["submission_id"]), ("desk", ["desk_a"], 1, None))
        self.assertEqual(pkg["manifest_sha256"], hashlib.sha256(pkg["files"]["MANIFEST.json"]).hexdigest())

    def test_p2_bad(self):
        good = {"schema": "aib-creation/v1", "kind": "desk", "rev": 1, "submission_id": None,
                "items": [{"item_id": "desk_a"}]}
        cases = [
            ("gz", make_tar(gz=True), "creator_package_unreadable"),
            ("garbage", b"not a tar at all" * 64, "creator_package_unreadable"),
            ("symlink", make_tar(links=[("assets/x.png", "/etc/passwd")]), "creator_path_rejected"),
            ("dotdot", make_tar(extra={"assets/../x.txt": b"x"}), "creator_path_rejected"),
            ("abs", make_tar(extra={"/assets/x.txt": b"x"}), "creator_path_rejected"),
            ("top", make_tar(extra={"run.sh": b"x"}), "creator_path_rejected"),
            ("deep", make_tar(extra={"assets/a/b/c/d.txt": b"x"}), "creator_path_rejected"),
            ("svg", make_tar(files={"assets/a.svg": b"<svg/>"}), "creator_file_type_rejected"),
            ("fakepng", make_tar(files={"assets/a.png": b"GIF89a....."}), "creator_file_type_rejected"),
            ("prompt_png", make_tar(files={"prompts/a.png": PNG}), "creator_file_type_rejected"),
            ("extra_file", make_tar(extra={"assets/more.txt": b"x"}), "creator_manifest_mismatch"),
            ("missing", make_tar(manifest={"schema": "aib-creation-manifest/v1", "files": {"assets/desk.png": "0" * 64}}),
             "creator_manifest_mismatch"),
            ("hash", make_tar(manifest={"schema": "aib-creation-manifest/v1", "files": {
                "assets/desk.png": "0" * 64, "creator.json": hashlib.sha256(json.dumps(good).encode()).hexdigest()}},
                creator=good), "creator_manifest_mismatch"),
            ("author", make_tar(creator=dict(good, author="冒名")), "creator_unknown_field"),
            ("mixed", make_tar(creator=dict(good, items=[{"item_id": "a"}, {"item_id": "b", "kind": "sofa"}])),
             "creator_kind_mixed"),
            ("schema", make_tar(creator=dict(good, schema="aib-creation/v9")), "creator_schema_unsupported"),
            ("rev2_noid", make_tar(creator=dict(good, rev=2)), "creator_schema_unsupported"),
            ("dup_item", make_tar(creator=dict(good, items=[{"item_id": "a"}, {"item_id": "a"}])),
             "creator_schema_unsupported"),
            ("badid", make_tar(creator=dict(good, rev=2, submission_id="sub_x")), "creator_id_invalid"),
            ("sparse", sparse_tar(300 * 1024 * 1024), "creator_path_rejected"),
            ("badsparse", sparse_tar(100, b"2\n0\n5\n"), "creator_package_unreadable"),
            ("foo_dir", make_tar(extra={"foo/x.txt": b"x"}), "creator_path_rejected"),
            ("manifest_empty", make_tar(manifest={"schema": "aib-creation-manifest/v1", "files": {}}),
             "creator_manifest_mismatch"),
            ("rev0", make_tar(creator=dict(good, rev=0)), "creator_schema_unsupported"),
            ("rev_true", make_tar(creator=dict(good, rev=True)), "creator_schema_unsupported"),
            ("rev1_with_id", make_tar(creator=dict(good, submission_id=SID)), "creator_schema_unsupported"),
        ]
        for label, data, want in cases:
            self.assertEqual(CC.read_package(data), (None, want), label)
        dup = io.BytesIO()
        with tarfile.open(fileobj=dup, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            for _ in range(2):
                ti = tarfile.TarInfo("assets/desk.png")
                ti.size = len(PNG)
                tf.addfile(ti, io.BytesIO(PNG))
        self.assertEqual(CC.read_package(dup.getvalue()), (None, "creator_path_rejected"), "重名")
        # 已修 CVE 的 tarfile 自己停在壞標頭 ⇒ 只讀到前段(manifest_mismatch);沒修的(3.9)靠我們的檢查回 unreadable。兩種都要拒收、不卡住
        self.assertIn(CC.read_package(neg_size_tar())[1], ("creator_package_unreadable", "creator_manifest_mismatch"), "負 size")
        old = CC.MAX_MEMBERS
        CC.MAX_MEMBERS = 2
        try:
            self.assertEqual(CC.read_package(make_tar()), (None, "creator_package_unreadable"), "成員超過上限")
        finally:
            CC.MAX_MEMBERS = old


class Submit(Base):
    def test_s1_submit_order_and_shape(self):
        data = make_tar()
        op = FakeOpener(202, log=self.log)
        res, rc = self.submit(data, op)
        self.assertEqual((rc, res["ok"], res["detail"], res["submission"]["id"]), (0, True, "creator_submitted", SID))
        self.assertEqual(self.log, ["prove", "net"], "先本人證明、才連網")
        self.assertIn("desk", self.reason)
        req = op.seen[0]
        self.assertEqual((req.get_method(), req.full_url), ("POST", "https://page.test/api/v1/page/creator/submissions"))
        self.assertEqual(req.data, data, "本體 = tar 原樣")
        pkg, _ = CC.read_package(data)
        self.assertEqual({k.lower(): v for k, v in req.header_items() if k.lower().startswith(("x-aib", "content-type", "authorization"))},
                         {"content-type": "application/x-tar", "authorization": "Bearer " + REC["assistant_token"],
                          "x-aib-machine": MID, "x-aib-manifest-sha256": pkg["manifest_sha256"], "x-aib-rev": "1"})
        ep = C["endpoints"]["creator_submit"]
        self.assertEqual(set(ep["headers"]), {"Authorization", "X-AIB-Machine", "X-AIB-Manifest-Sha256", "X-AIB-Rev", "Content-Type"})
        res, rc = self.submit(data, FakeOpener(200))
        self.assertEqual((rc, res["detail"]), (0, "creator_submitted_existing"))
        self.assertEqual(set(res), set(C["creator"]["cli_receipt"]["submit_ok"]["fields"]))

    def test_s2_attest_fails_nothing_sent(self):
        op = FakeOpener(202, log=self.log)
        res, rc = self.submit(make_tar(), op, ok=False)
        self.assertEqual((rc, res["ok"], res["detail"]), (5, False, "creator_attest_cancelled"))
        self.assertEqual(op.seen, [], "本人證明沒過 ⇒ 一個請求都不送")
        self.assertEqual(self.log, ["prove"])

    def test_s2b_default_attest_path(self):
        """正式路徑(不帶 prove)一定叫 chat_owner.prove;它說沒過 ⇒ rc 5、一個請求都不送(直接呼叫 + main 兩條)。"""
        import chat_owner
        calls = []
        orig = chat_owner.prove
        chat_owner.prove = lambda reason, env, **kw: calls.append(reason) or "touchid_cancelled"
        self.addCleanup(setattr, chat_owner, "prove", orig)
        op = FakeOpener(202)
        api = A.PageApi(base_url="https://page.test", token=REC["assistant_token"], opener=op)
        res, rc = CC.submit(self.env, self.tar(make_tar()), api=api, machine_id=MID)
        self.assertEqual((rc, res["detail"], op.seen, len(calls)), (5, "creator_attest_cancelled", [], 1))
        orig_b = CC.binding
        CC.binding = lambda env: (api, MID)
        self.addCleanup(setattr, CC, "binding", orig_b)
        out = io.StringIO()
        rc = CC.main(["submit", self.tar(make_tar(), "b.tar"), "--json"], env=self.env, out=out)
        self.assertEqual((rc, json.loads(out.getvalue())["detail"], op.seen, len(calls)), (5, "creator_attest_cancelled", [], 2))

    def test_s3_local_block_and_binding(self):
        op = FakeOpener(202, log=self.log)
        res, rc = self.submit(make_tar(extra={"run.sh": b"x"}), op)
        self.assertEqual((rc, res["detail"]), (2, "creator_path_rejected"))
        self.assertEqual((self.log, op.seen), ([], []), "本機擋 ⇒ 不叫本人證明、不送")
        res, rc = CC.submit(self.env, self.tar(make_tar()), prove=self.prove())
        self.assertEqual((rc, res["detail"]), (2, "creator_not_bound"))
        self.assertEqual(self.log, [], "沒綁 ⇒ 不叫本人證明")
        store.save_token(dict(REC), self.env)
        api, mid = CC.binding(self.env)
        self.assertEqual((mid, api.token), (MID, REC["assistant_token"]))
        rec = dict(REC)
        rec.pop("machine_id")
        store.save_token(rec, self.env)
        self.assertEqual(CC.binding(self.env), (None, None), "憑證沒帶 machine_id ⇒ 不送")

    def test_s4_server_errors(self):
        data = make_tar()
        for status, payload, want in (
                (422, {"error": "creator_manifest_mismatch"}, (3, "creator_manifest_mismatch")),
                (429, {"error": "creator_quota_daily", "retry_after": 60}, (3, "creator_quota_daily")),
                (404, {}, (3, "creator_unsupported")),
                (405, {}, (3, "creator_unsupported")),
                (404, {"error": "creator_submission_unknown"}, (3, "creator_submission_unknown")),
                (503, {"error": "creator_intake_paused"}, (4, "creator_intake_paused")),
                (500, {}, (4, "creator_unreachable")),
                (202, {"ok": True, "submission": {"id": "bad"}}, (4, "creator_unreachable")),
                (202, {"ok": True}, (4, "creator_unreachable")),
                (400, {"error": "something_new"}, (3, "creator_refused")),
                (503, {"error": "something_new"}, (4, "creator_unreachable"))):
            res, rc = self.submit(data, FakeOpener(status, payload))
            self.assertEqual((rc, res["detail"]), want, (status, payload))
            self.assertFalse(res["ok"])
            self.assertTrue(res["hint"])
        for status, want in ((413, "creator_too_large"), (415, "creator_media_type_rejected"), (400, "creator_refused")):
            res, rc = self.submit(data, TextOpener(status))   # 前面的 CF / nginx 回純文字,沒有碼
            self.assertEqual((rc, res["detail"]), (3, want), status)
        res, rc = self.submit(data, FakeOpener(400, {"error": "weird\u202ecode"}))
        self.assertEqual(res["detail"], "creator_refused")
        self.assertNotIn("\u202e", res["hint"])
        res, rc = self.submit(data, FakeOpener(boom=True))
        self.assertEqual((rc, res["detail"]), (4, "creator_unreachable"))
        body = {"error": "page_api_token_revoked", "revoked_at": "2026-10-22T07:30:00Z", "revoked_by_name": "舊筆電",
                "manage_url": "https://ez2.page/manage/aib"}
        res, rc = self.submit(data, FakeOpener(401, body))
        self.assertEqual((rc, res["detail"]), (3, "page_api_token_revoked"))
        self.assertIn("舊筆電", res["hint"])

    def test_s5_resubmit(self):
        cr = {"schema": "aib-creation/v1", "kind": "desk", "rev": 3, "submission_id": SID,
              "items": [{"item_id": "desk_a"}]}
        op = FakeOpener(202, {"ok": True, "submission": sub_obj(rev=3)})
        res, rc = self.submit(make_tar(creator=cr), op)
        self.assertEqual(rc, 0, res)
        req = op.seen[0]
        self.assertTrue(req.full_url.endswith("/creator/submissions/%s/resubmit" % SID), req.full_url)
        self.assertEqual(req.get_header("X-aib-rev"), "3")
        self.assertEqual(C["endpoints"]["creator_resubmit"]["path"], "/creator/submissions/{id}/resubmit")


class Status(Base):
    def api(self, opener):
        return A.PageApi(base_url="https://page.test", token=REC["assistant_token"], opener=opener)

    def test_s6_status(self):
        op = FakeOpener(200)
        res, rc = CC.status(self.env, "sub_x", api=self.api(op))
        self.assertEqual((rc, res["detail"], op.seen), (2, "creator_id_invalid", []))
        evil = sub_obj("decided", hint="改好\u202e再送\n第二行", detail_url="https://evil.example/x",
                       items=[{"item_id": "desk_a", "state": "rejected", "reasons": ["creator_x\u200b"], "hint": "太亮\u2066"}])
        op = FakeOpener(200, {"ok": True, "submission": evil})
        res, rc = CC.status(self.env, SID, api=self.api(op))
        self.assertEqual((rc, res["detail"], res["submission"]["id"]), (0, "creator_status", SID))
        self.assertTrue(op.seen[0].full_url.endswith("/creator/submissions/" + SID))
        out = io.StringIO()
        CC._print_sub(res["submission"], out)
        text = out.getvalue()
        for ch in ("\u202e", "\u200b", "\u2066"):
            self.assertNotIn(ch, text)
        self.assertNotIn("evil.example", text)
        self.assertNotIn("\n第二行", text)
        good = sub_obj(detail_url="https://ez2.page/manage/creator/" + SID)
        out = io.StringIO()
        CC._print_sub(good, out)
        self.assertIn("https://ez2.page/manage/creator/" + SID, out.getvalue())
        out = io.StringIO()
        CC._print_sub(sub_obj(items=7), out)
        CC._print_sub(sub_obj(items=[{"item_id": "a", "reasons": 3}]), out)   # 型別不對也不當
        op = FakeOpener(200, {"ok": True, "submissions": 5})
        self.assertEqual(CC.list_mine(self.env, api=self.api(op))[0]["submissions"], [])
        op = FakeOpener(200, {"ok": True, "submissions": [sub_obj(), {"id": "bad"}], "next_cursor": "c2"})
        res, rc = CC.list_mine(self.env, api=self.api(op))
        self.assertEqual((rc, len(res["submissions"]), res["next_cursor"]), (0, 1, "c2"))
        self.assertIn("mine=1", op.seen[0].full_url)


class Cli(Base):
    def test_s7_json_and_wording(self):
        out = io.StringIO()
        rc = CC.main(["status", "sub_x", "--json"], env=self.env, out=out)
        line = json.loads(out.getvalue())
        self.assertEqual((rc, line["ok"], line["detail"]), (2, False, "creator_id_invalid"))
        out = io.StringIO()
        rc = CC.main(["submit", str(self.td / "nope.tar"), "--json"], env=self.env, out=out)
        self.assertEqual((rc, json.loads(out.getvalue())["detail"]), (2, "creator_package_unreadable"))
        cr = C["creator"]
        served = {c for c, d in cr["codes"].items() if d["http"] is not None}
        self.assertEqual(set(CC.SERVER_CODES), served, "核心認得的伺服器碼 = 契約裡有 http 的碼")
        codes = set(cr["codes"]) | set(cr["cli_receipt"]["local_codes"]) | set(cr["cli_receipt"]["submit_ok"]["detail"])
        codes -= {"creator_reported", "creator_admin_hold", "creator_merge_failed", "listing_signature_invalid",
                  "creator_attest_required"}    # 只出現在 reasons / 套用 / 定價那邊,不是 create 指令的回條
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for code in codes:
                self.assertTrue(d.get("aib.create.hint." + code), (loc, code))
            self.assertIn("{id}", d["aib.create.hint.creator_submitted"], loc)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/create_cli.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_attest(self):
        self._knife("    why = (prove or (lambda r, e: CO.prove(r, e)))(reason, env)\n", "    why = None\n",
                    "Submit.test_s2_attest_fails_nothing_sent")

    def test_k4_default_attest_skipped(self):
        self._knife("(lambda r, e: CO.prove(r, e))", "(lambda r, e: None)", "Submit.test_s2b_default_attest_path")

    def test_k2_no_hash_check(self):
        self._knife("hashlib.sha256(files[name]).hexdigest() != digest.lower()", "False", "Package.test_p2_bad")

    def test_k3_links_accepted(self):
        self._knife("if m.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or m.issparse() or name in files:",
                    "if name in files:", "Package.test_p2_bad")

    def test_k5_neg_size_accepted(self):
        # 只砍成員上限:整條換 False 的話,沒修 CVE 的 3.9 讀 neg_size_tar 會無限迴圈、刀測逾時(第 3 輪審查員)
        self._knife(" or len(members) >= MAX_MEMBERS:", ":", "Package.test_p2_bad")


if __name__ == "__main__":
    unittest.main()
