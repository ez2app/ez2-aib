#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""兩台機器透過協作廣場交換影子續跑紀錄(docs/PLAN-shadow-cross-device-1002.md 第 7 步)。

不另開分支、不另做簽章(the maintainer 10-02:「仔細了解 GIT 廣場的部份,裡面有類似的,不要重新打造」):
  傳 = 廣場的 put(),紀錄種類 "shadow",鍵 = 倉識別(repo_id);廣場自己會分片、重試、只加不改、每筆簽章。
  收 = 廣場的 records("shadow") + verify_record();聯邦規格 §8 本來就寫「轉送的是任務、證據、影子續跑的結果」。
本檔只管影子續跑自己的規矩:

  push  只收 row_export.py 清過的列(每列 exported: true、不是 analysis 列);一筆廣場紀錄最多 CHUNK 列。
  pull  只收:簽章驗得過(ok)、簽署者在信任名單上而且鑰匙指紋對得上、同一個倉(repo_id)的紀錄。
        寫成影子目錄裡的 `shared-<簽署者>-<紀錄編號>.rows.jsonl`,每列加 via / via_record ⇒
        reuse_index 照常讀得到,而且列上有別台的 host_id:停用旗標(第 3 步)一立,這些列就不用。

「同一個倉」怎麼認:**不能用 reuse_index.repo_identity**(那是本機 git 目錄**路徑**的雜湊,Mac 與 Fish 的 clone
路徑不同 ⇒ 同一個倉兩個身分,永遠對不上)。改用 share_id():環境變數 AIB_REPO_SHARE_ID,否則任一個指向
GitHub 的 remote 的「擁有者/倉名」(例 ez2app/ez2-aib)。兩樣都沒有 ⇒ 不傳不收(說明原因)。
收進來的列把 repo_id 換成本機的、拿掉 repo 路徑 ⇒ reuse_index 認得是同一個倉(否則當成別人的倉丟掉)。

信任名單(誰的結果算數)= `--trust @名字=鑰匙指紋,…` 或環境變數 AIB_VERIFY_TRUST,同一格式。
沒給 ⇒ 一筆都不收。**給誰 = 改受信機器名單,要 the maintainer 一句話**;本檔不提供自動加入。
鑰匙指紋 = 廣場名錄上那把公鑰的 key_fp(`collab_plaza.py who --json` 看得到 public_key;key_fp() 算)。

CLI:
  pool_share.py push --me @身分 --rows 清過的.jsonl
  pool_share.py pull [--trust @a=fp,@b=fp] [--out 目錄]      stdout 一行 JSON 摘要
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KIND = "shadow"
CHUNK = 200
ENV_TRUST = "AIB_VERIFY_TRUST"
sys.path.insert(0, str(HERE))
import reuse_index as ri  # noqa: E402
import shadow_tracer as st  # noqa: E402


def _plaza():
    spec = importlib.util.spec_from_file_location("collab_plaza", ROOT / "tools" / "collab" / "collab_plaza.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parse_trust(text):
    """'@a=fp1,@b=fp2' → {'@a': 'fp1', '@b': 'fp2'};格式不對的項整個丟掉(寧可少收)。"""
    out = {}
    for item in (text or "").split(","):
        name, sep, fp = item.strip().partition("=")
        if sep and name.startswith("@") and fp.strip():
            out[name.strip()] = fp.strip()
    return out


def _rows_from(path):
    rows = []
    for n, line in enumerate(jsonl_safe.split_lines(Path(path).read_text(encoding="utf-8")), start=1):
        if not line.strip():
            continue
        r = json.loads(line)
        if not isinstance(r, dict) or r.get("exported") is not True or r.get("analysis"):
            raise ValueError("line %d is not a row_export.py row (exported: true, not analysis)" % n)
        rows.append(r)
    return rows


_GITHUB = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def share_id(repo):
    """跨機器都一樣的倉識別:AIB_REPO_SHARE_ID,否則 GitHub remote 的 擁有者/倉名;都沒有回空字串。"""
    forced = os.environ.get("AIB_REPO_SHARE_ID", "").strip()
    if forced:
        return forced
    try:
        p = subprocess.run(["git", "-C", str(repo), "remote", "-v"], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    names = sorted({"%s/%s" % m.groups() for m in (_GITHUB.search(l.split()[1]) for l in p.stdout.splitlines()
                                                    if len(l.split()) >= 2) if m})
    return names[0] if len(names) == 1 else ""


def push(me, rows_path, repo=None, plaza=None):
    plaza = plaza or _plaza()
    repo = str(Path(repo or ROOT).resolve())
    rows = _rows_from(rows_path)
    rid = share_id(repo)
    if not rid:
        raise ValueError("no shared repo identity (set AIB_REPO_SHARE_ID or add a GitHub remote)")
    host = ri.host_id()
    ids = []
    for i in range(0, len(rows), CHUNK):
        part = rows[i:i + CHUNK]
        blob = json.dumps(part, ensure_ascii=False, sort_keys=True).encode("utf-8")
        rec = plaza.put(KIND, rid, {"repo_id": rid, "host_id": host, "count": len(part),
                                    "sha256": hashlib.sha256(blob).hexdigest(), "rows": part,
                                    "tracer": st.tracer_code_digest()}, me)
        ids.append(rec["id"])
    return {"pushed_rows": len(rows), "records": ids, "repo_id": rid, "host_id": host}


def pull(trust, out_dir=None, repo=None, plaza=None):
    plaza = plaza or _plaza()
    repo = str(Path(repo or ROOT).resolve())
    rid = share_id(repo)
    local_rid = ri.repo_identity(repo) or ""
    out = Path(out_dir or st.shadow_dir_default())
    summary = {"seen": 0, "accepted": 0, "rows": 0, "skipped": {}, "files": []}

    def skip(why):
        summary["skipped"][why] = summary["skipped"].get(why, 0) + 1

    recs = plaza.records(KIND)
    summary["seen"] = len(recs)
    if not rid:
        summary["skipped"]["no-shared-repo-identity"] = len(recs)
        return summary
    if not trust:
        summary["skipped"]["no-trust-list"] = len(recs)
        return summary
    chains = plaza.key_chains()
    pins = {a: c[-1] for a, c in chains.items() if c}
    for r in recs:
        if r.get("repo_id") != rid:
            skip("other-repo")
            continue
        who = r.get("by")
        if who not in trust:
            skip("not-trusted")
            continue
        v = plaza.verify_record(r, pins, chains)   # 只收 ok:舊鑰匙簽的(ok-old-key)不收,換鑰匙常是因為可能外洩
        if v != "ok":
            skip(v)
            continue
        if r.get("key_fp") != trust[who]:
            skip("trust-fp-mismatch")
            continue
        rows = r.get("rows")
        blob = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8") if isinstance(rows, list) else b""
        if not rows or hashlib.sha256(blob).hexdigest() != r.get("sha256") or len(rows) != r.get("count"):
            skip("rows-mismatch")
            continue
        # 10-03 審查第 12 條:收進來的列逐列過索引自己的檢查;壞列(不是物件、缺 inputs)、作廢列都不收,
        #   否則一列壞的就讓整次 pull 中斷、或讓整份索引降級成全部必跑。檔名只留安全字元(不能穿越目錄)
        good = []
        for row in rows:
            ok = ri._parse_row(json.dumps(row, ensure_ascii=False)) if isinstance(row, dict) else None
            if ok is None or "void" in ok:
                skip("bad-row")
                continue
            good.append(ok)
        if not good:
            continue
        rows = good
        safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "_", str(s))  # noqa: E731
        out.mkdir(parents=True, exist_ok=True)
        dst = out / ("shared-%s-%s.rows.jsonl" % (safe(who.lstrip("@")), safe(r.get("id"))))
        if not dst.exists():
            tmp = dst.with_name(dst.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for row in rows:
                    row = {k: v for k, v in row.items() if k != "repo"}
                    # 10-03 審查第 2 條:host_id 一律用紀錄層級、有簽章的那一個(列自己寫的不算數)
                    row.update(via=who, via_record=r["id"], repo_id=local_rid, host_id=r.get("host_id") or "remote-unknown")
                    f.write(jsonl_safe.dumps(row) + "\n")
            os.replace(str(tmp), str(dst))
        summary["accepted"] += 1
        summary["rows"] += len(rows)
        summary["files"].append(dst.name)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pool_share")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push")
    p.add_argument("--me", required=True)
    p.add_argument("--rows", required=True)
    q = sub.add_parser("pull")
    q.add_argument("--trust", default=None)
    q.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "push":
            print(json.dumps(push(args.me, args.rows), ensure_ascii=False))
            return 0
        trust = parse_trust(args.trust if args.trust is not None else os.environ.get(ENV_TRUST, ""))
        print(json.dumps(pull(trust, args.out), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as e:  # noqa: BLE001 - 廣場推不上去、讀不懂,一律說清楚並失敗,不靜默
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
