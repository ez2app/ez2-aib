#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""備存與 90 天(記憶整理第 1 包;docs/DESIGN-memory-tidy-1013.md §5,a seat 10-13 01:25 放行)。

改人格卡、專案卡、席位記憶之前先拍一份快照;快照最久留 90 天,過期的丟系統垃圾桶。
the maintainer 裁示、紅線、身分欄另存「永久.md」,永遠不清。

位置(統一一個根,不放 Vault —— 角色.md 的備份帶同一個 uid,Vault 的索引與搜尋會看到兩份):
  <狀態根>/archive/<kind>/<key>/<YYYY-MM-DD>/<檔名>-<HHMMSS>.<副檔名>
  <狀態根>/archive/<kind>/<key>/永久.md                     只往上加、同句去重
  <狀態根>/trash-pending/<搬入日 YYYY-MM-DD>/<原相對路徑>    丟不進垃圾桶時先放這裡,30 天後刪
  <狀態根>/logs/tidy-sweep.log                              每一筆清理結果
  kind ∈ persona / project / memory;key = 人格 uid / 專案 uid / 席碼

  memory_archive.py snapshot <檔> --kind persona|project|memory --key <uid 或席碼>
  memory_archive.py archive-sweep [--dry-run] [--force] [--today YYYY-MM-DD]
  (由 aib memory snapshot / aib memory archive-sweep 轉來)

清理規則:只看名稱完全是 YYYY-MM-DD 的資料夾;超過 90 天(第 91 天起)⇒ 垃圾桶;永久.md 與其他名稱一律不碰。
垃圾桶:有 /usr/bin/trash(macOS)就用(逐一呼叫、30 秒逾時);沒有(公開版、Linux)、失敗或逾時 ⇒ 搬進 trash-pending。
trash-pending 由同一支清:搬入日超過 30 天 ⇒ 刪除。最久保留 = 90 天備存 + 30 天 trash-pending。
一天只清一次(state/tidy/last-sweep 記日期);--force 略過這個標記。
測試治具:AIB_ARCHIVE_TRASH=none 關掉垃圾桶、=<路徑> 指定垃圾桶程式。

拍不成(磁碟滿、權限)⇒ snapshot 回 failed,呼叫端不准寫(寧可不改也不毀舊檔)。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KINDS = ("persona", "project", "memory")
KEEP_DAYS = 90
PENDING_DAYS = 30
TRASH_TIMEOUT = 30
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY_RE = re.compile(r"^[A-Za-z0-9:_\-.]{1,80}$")
# 角色.md 永久類段名(10-13 實掃 87 張卡:沒有一張有「the maintainer 裁示」段;裁示散在「已定的規矩(the maintainer 10-01…)」這類段名)
PERMANENT_HEADINGS = (
    re.compile(r"^##\s*紅線"),
    re.compile(r"^##\s*.*(已定的規矩|the maintainer)"),
    re.compile(r"^##\s*身分"),
)


def state_root():
    for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(k)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def archive_root():
    return state_root() / "archive"


def pending_root():
    return state_root() / "trash-pending"


def log_path():
    return state_root() / "logs" / "tidy-sweep.log"


def marker_path():
    return state_root() / "state" / "tidy" / "last-sweep"


def _log(event, path, result, detail=""):
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\t%s\n" % (datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                                              event, path, result, detail))
    except OSError:
        pass


# ---------------------------------------------------------------- 永久類
def _persona_permanent(text):
    out, keep = [], False
    for line in text.splitlines():
        if re.match(r"^##(?!#)", line):          # 只有二級標題換段;### 子標題留在原段裡
            keep = any(r.match(line) for r in PERMANENT_HEADINGS)
        if keep and line.strip():
            out.append(line.rstrip())
    return out


def _project_permanent(text):
    try:
        doc = json.loads(text)
    except ValueError:
        return []
    cons = ((doc.get("layers") or {}).get("constitution") or {}) if isinstance(doc, dict) else {}
    out = []
    for r in cons.get("red_lines") or []:
        out.append("紅線:%s" % (r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)))
    for r in cons.get("human_rulings") or []:
        if isinstance(r, dict):
            out.append("the maintainer 裁示:%s · %s" % (r.get("at") or "", r.get("text") or ""))
        else:
            out.append("the maintainer 裁示:%s" % r)
    return out


def extract_permanent(path, kind):
    """回永久類的行;席位記憶沒有分段結構 ⇒ 不抽(回 [])。"""
    if kind == "memory":
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return _persona_permanent(text) if kind == "persona" else _project_permanent(text)


def _append_permanent(folder, lines, src_name, now):
    if not lines:
        return 0
    p = folder / "永久.md"
    have = set()
    if p.exists():
        have = {ln.rstrip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()}
    new = []
    for ln in lines:
        if ln not in have and ln not in new:
            new.append(ln)
    if not new:
        return 0
    with open(p, "a", encoding="utf-8") as f:
        if not have:
            f.write("# 永久(只往上加,不清)\n")
        f.write("\n<!-- %s · %s -->\n" % (now.strftime("%Y-%m-%d %H:%M:%S"), src_name))
        f.write("\n".join(new) + "\n")
    return len(new)


# ---------------------------------------------------------------- 快照
def snapshot(path, kind, key, now=None):
    """改之前拍一份。回 {status: ok|skipped|failed, dest, permanent, error}。failed ⇒ 呼叫端不准寫。"""
    src = Path(path)
    if not src.exists():
        return {"status": "skipped", "error": "no file yet"}
    if kind not in KINDS or not KEY_RE.match(key or "") or ".." in key or set(key) <= {"."}:
        return {"status": "failed", "error": "bad kind/key: %r/%r" % (kind, key)}
    now = now or datetime.datetime.now()
    folder = archive_root() / kind / key.replace(":", "_")
    day = folder / now.strftime("%Y-%m-%d")
    stem, suffix = src.stem, src.suffix
    try:
        day.mkdir(parents=True, exist_ok=True)
        dest = day / ("%s-%s%s" % (stem, now.strftime("%H%M%S"), suffix))
        n = 1
        while dest.exists():
            n += 1
            dest = day / ("%s-%s-%d%s" % (stem, now.strftime("%H%M%S"), n, suffix))
        shutil.copy2(src, dest)
        perm = _append_permanent(folder, extract_permanent(src, kind), src.name, now)
    except OSError as e:
        return {"status": "failed", "error": str(e)}
    return {"status": "ok", "dest": str(dest), "permanent": perm}


# ---------------------------------------------------------------- 清理
def _trash_bin():
    v = os.environ.get("AIB_ARCHIVE_TRASH")
    if v == "none":
        return None
    if v:
        return v if os.access(v, os.X_OK) else None
    return "/usr/bin/trash" if os.access("/usr/bin/trash", os.X_OK) else None


def _to_pending(path, today):
    rel = Path(path).relative_to(state_root())
    dest = pending_root() / today.isoformat() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.name + "-%d" % os.getpid())
    shutil.move(str(path), str(dest))
    return dest


def _discard(path, today, dry_run):
    if dry_run:
        _log("archive", path, "dry-run")
        return "dry-run"
    tb = _trash_bin()
    if tb:
        try:
            r = subprocess.run([tb, "--stopOnError", str(path)], capture_output=True, timeout=TRASH_TIMEOUT)
            if r.returncode == 0 and not Path(path).exists():
                _log("archive", path, "trash")
                return "trash"
            why = "rc=%s %s" % (r.returncode, (r.stderr or b"").decode("utf-8", "replace").strip()[:120])
        except (OSError, subprocess.SubprocessError) as e:
            why = str(e)[:120]
    else:
        why = "no trash"
    try:
        dest = _to_pending(path, today)
    except OSError as e:
        _log("archive", path, "failed", str(e)[:120])
        return "failed"
    _log("archive", path, "pending", "%s -> %s" % (why, dest))
    return "pending"


def _age(name, today):
    try:
        return (today - datetime.date.fromisoformat(name)).days
    except ValueError:
        return None


def archive_sweep(today=None, dry_run=False, force=False):
    """回 {skipped?, archive: {結果: 數}, purged: n}。一天一次。"""
    today = today or datetime.date.today()
    mk = marker_path()
    if not force and not dry_run:
        try:
            if mk.read_text(encoding="utf-8").strip() == today.isoformat():
                return {"skipped": "already today"}
        except OSError:
            pass
    if not dry_run:
        # 先佔今天(O_EXCL 建一個當天的佔位檔):同時開好幾席時只有一個搶得到,其他直接回 skipped,不搶著搬同一批
        try:
            mk.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(mk.parent / ("sweep-%s.claim" % today.isoformat())), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            if not force:
                return {"skipped": "already today"}
        except OSError:
            pass
        try:
            mk.write_text(today.isoformat() + "\n", encoding="utf-8")
            for old in mk.parent.glob("sweep-*.claim"):
                if old.name != "sweep-%s.claim" % today.isoformat():
                    old.unlink()
        except OSError:
            pass
    counts = {}
    root = archive_root()
    if root.is_dir():
        for kind_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for key_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
                for d in sorted(p for p in key_dir.iterdir() if p.is_dir() and DATE_RE.match(p.name)):
                    age = _age(d.name, today)
                    if age is not None and age > KEEP_DAYS:
                        res = _discard(d, today, dry_run)
                        counts[res] = counts.get(res, 0) + 1
    purged = 0
    pr = pending_root()
    if pr.is_dir():
        for d in sorted(p for p in pr.iterdir() if p.is_dir() and DATE_RE.match(p.name)):
            age = _age(d.name, today)
            if age is not None and age > PENDING_DAYS:
                if dry_run:
                    _log("pending", d, "dry-run")
                    continue
                try:
                    shutil.rmtree(d)
                    purged += 1
                    _log("pending", d, "purged")
                except OSError as e:
                    _log("pending", d, "failed", str(e)[:120])
    return {"archive": counts, "purged": purged}


def spawn_background():
    """背景跑一次 archive-sweep 就回(不等)。給 SessionStart hook 與 handoff collect 用:
    stdin/stdout/stderr 全接 /dev/null(那支 hook 的 stdout 會直接進對話)、自成一組行程;
    環境原樣傳下去(測試換掉的 HOME / 狀態根要跟著走);任何失敗回 None,不丟例外。
    沒東西可清或今天清過 ⇒ 不起行程,回 None。"""
    try:
        # 沒東西可清(沒有 archive / trash-pending)或今天清過 ⇒ 不起行程:別席的測試在暫存狀態根叫到掛點時不會多一支背景程式
        if not (archive_root().is_dir() or pending_root().is_dir()):
            return None
        try:
            if marker_path().read_text(encoding="utf-8").strip() == datetime.date.today().isoformat():
                return None
        except OSError:
            pass
        with open(os.devnull, "rb") as fin, open(os.devnull, "wb") as fout:
            p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "archive-sweep"],
                                 stdin=fin, stdout=fout, stderr=fout, start_new_session=True,
                                 env=dict(os.environ), close_fds=True)
        return p.pid
    except Exception:  # noqa: BLE001 —— 掛點炸掉也不能影響開席與交接
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib memory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("path")
    s.add_argument("--kind", required=True, choices=KINDS)
    s.add_argument("--key", required=True)
    w = sub.add_parser("archive-sweep")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--force", action="store_true")
    w.add_argument("--today", default="")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        r = snapshot(a.path, a.kind, a.key)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["status"] in ("ok", "skipped") else 2
    today = datetime.date.fromisoformat(a.today) if a.today else None
    print(json.dumps(archive_sweep(today, a.dry_run, a.force), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
