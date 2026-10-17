#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""狀態列腳本的「aib 發過的版本」指紋清單(deploy/statusline/KNOWN-SHAS.json)。

給 statusline-install.sh --auto 認「這支是 aib 裝的舊版」用:家目錄那支的 sha256 在清單裡 ⇒ 是 aib 裝的,可以自動換;
不在清單裡 ⇒ 使用者改過或不是 aib 的,不動(the maintainer 10-17「以後都應該自動更新」、@abb89 交辦第 2 點)。

清單只增不減:每支 MANIFEST 來源在 git 歷史裡的每一版都記下來,舊的永遠留著(使用者可能停在任何一版)。
MANIFEST 刻意不放 sha(那裡的 sha 是「現在該長怎樣」,會過期);這裡記的是「以前長過怎樣」,不會過期。

  python3 tools/statusline-known-shas.py --write   # 改了 deploy/statusline/ 底下的腳本後跑一次,連同腳本一起提交
  python3 tools/statusline-known-shas.py --check   # 現在每支來源的 sha 都在清單裡 ⇒ rc 0;缺 ⇒ rc 1 並列出
  python3 tools/statusline-known-shas.py --add-from-git <別的倉>
      # 公開倉之前,這些腳本在舊開發倉別的路徑下發過很多版(公開倉歷史只有一筆)。照檔名把那個倉所有分支、
      # 每一版的 sha 收進來(只記指紋,不搬任何檔);不收的話,用舊版裝過的機器會被當成「使用者改過」而不自動換。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "statusline" / "MANIFEST.json"
KNOWN = ROOT / "deploy" / "statusline" / "KNOWN-SHAS.json"
SCHEMA = "ez2-statusline-known-shas-v1"


def sources():
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))["items"]
    return sorted({it["src"] for it in items})


def load():
    try:
        doc = json.loads(KNOWN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    shas = doc.get("shas") if isinstance(doc, dict) else None
    return {k: list(v) for k, v in shas.items()} if isinstance(shas, dict) else {}


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_versions(src):
    """src 在 git 歷史裡每一版的 sha256(含改名前的舊路徑)。沒有 git ⇒ 空清單。"""
    try:
        log = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--name-only", "--follow", "--", src],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, commit = [], None
    for ln in log.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if len(ln) == 40 and all(c in "0123456789abcdef" for c in ln):
            commit = ln
            continue
        if commit:
            # git log 印的路徑相對倉庫最上層(aib/ 的上一層),--follow 的舊名也是;<commit>:<路徑> 也吃這種
            r = subprocess.run(["git", "-C", str(ROOT), "show", "%s:%s" % (commit, ln)],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                out.append(sha_bytes(r.stdout))
            commit = None
    return out


def current(src):
    return sha_bytes((ROOT / src).read_bytes())


def missing(known=None):
    known = load() if known is None else known
    return [s for s in sources() if current(s) not in known.get(s, [])]


def write():
    known = load()
    for s in sources():
        have = known.setdefault(s, [])
        for h in git_versions(s) + [current(s)]:
            if h not in have:
                have.append(h)
    _dump(known)
    return 0


def _dump(known):
    doc = {"schema": SCHEMA,
           "note": "aib 發過的每一版狀態列腳本的 sha256(只增不減)。statusline-install.sh --auto 只換 sha 在這裡的檔;"
                   "改了 deploy/statusline/ 的腳本要跑 python3 tools/statusline-known-shas.py --write 一起提交。",
           "shas": {k: known[k] for k in sorted(known)}}
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if not KNOWN.exists() or KNOWN.read_text(encoding="utf-8") != text:
        KNOWN.write_text(text, encoding="utf-8")


def blobs_by_name(repo, names):
    """repo 所有分支歷史裡,檔名(basename)在 names 裡的每一版 ⇒ {檔名: [sha256…]}。"""
    log = subprocess.run(["git", "-C", str(repo), "log", "--all", "--format=", "--raw", "--no-abbrev", "--no-renames"],
                         capture_output=True, text=True, timeout=300)
    seen, out = set(), {}
    for ln in log.stdout.splitlines():
        # :100644 100644 <舊 blob> <新 blob> M\t<路徑>
        if not ln.startswith(":") or "\t" not in ln:
            continue
        meta, path = ln.split("\t", 1)
        blob = meta.split()[3]
        name = path.rsplit("/", 1)[-1]
        if name not in names or set(blob) == {"0"} or blob in seen:
            continue
        seen.add(blob)
        r = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", blob], capture_output=True, timeout=30)
        if r.returncode == 0:
            out.setdefault(name, []).append(sha_bytes(r.stdout))
    return out


def add_from_git(repo):
    known = load()
    by_name = blobs_by_name(repo, {s.rsplit("/", 1)[-1] for s in sources()})
    n = 0
    for s in sources():
        have = known.setdefault(s, [])
        for h in by_name.get(s.rsplit("/", 1)[-1], []):
            if h not in have:
                have.append(h)
                n += 1
    _dump(known)
    print("收進 %d 個舊版指紋" % n, file=sys.stderr)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--write"]:
        return write()
    if len(argv) == 2 and argv[0] == "--add-from-git":
        return add_from_git(argv[1])
    if argv == ["--check"]:
        bad = missing()
        for s in bad:
            print("缺:%s 現在這一版的 sha 不在 %s(跑 python3 tools/statusline-known-shas.py --write)"
                  % (s, KNOWN.relative_to(ROOT)), file=sys.stderr)
        return 1 if bad else 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
