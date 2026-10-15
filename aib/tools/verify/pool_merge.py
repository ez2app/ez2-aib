#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""M1 合池(docs/DESIGN-verify-memory-pipeline-1012.md §5 M1;the maintainer 同意,a seat 10-12 派)—— 一次性搬移指令。

沿用池的身分從「git 目錄」改成「專案」(resume_store.project_key:最舊根提交 + 遠端)後,池檔名跟著變。
同一個專案的舊池(Mini 正本以前寫的、推包用的另一個 clone 寫的)要併進新池,作者跑過的測試推包時才沿用得到。

  python3 tools/verify/pool_merge.py [--repo R] [--dir D]            # 試跑:只列要合哪幾池、幾 MB,什麼都不寫
  python3 tools/verify/pool_merge.py [--repo R] [--dir D] --apply    # 真的合

--apply 做的事(審查 10-13 後的做法,不在測試寫列的路上做):
  1. 拿 <新池>.merge.lock(LOCK_NB:別人在合就說一聲、不等)。
  2. 舊池**一行一行**讀(不整檔進記憶體),照 time_utc 多路合併(每個舊池本來就照時間追加);
     只收 repo_id 是這幾個舊池身分之一的列,以及沒有 repo_id 也沒有 repo 的列;其他的跳過並計數(別的專案混進來的不收)。
     收下的列 repo_id 改寫成新身分,加 merged_from。
  3. 寫進暫存檔;新池已經有列(切換後已經有人寫)就接在後面(較新),再原子換上。
  4. 舊池搬進 <dir>/premerge-<時刻>/(**不刪**;搬回來就恢復)。reuse_index 只讀 <dir> 最上層的 *.rows.jsonl,
     搬走之後不會重複讀。.archive 不動。
沒跑 --apply 之前:本樹舊身分寫的列 reuse_index 照收(過渡),只是別的 clone 的列還看不到 —— 不會錯,只是少沿用。
"""
from __future__ import annotations

import argparse
import fcntl
import heapq
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jsonl_safe  # noqa: E402 - 同資料夾;行界只認 "\n"
sys.path.insert(0, str(HERE.parents[1] / "src" / "cockpit"))
import i18n_lib  # noqa: E402 - 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py)

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    return i18n_lib.t("aib.verify.pool_merge." + key, params, locale=LOC)


def _first_repo(path, max_lines=50):
    try:
        with open(path, "rb") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    return None
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("repo"):
                    return str(row["repo"])
    except OSError:
        return None
    return None


def _pool_id(path):
    name = Path(path).name
    return name[len("verify-"):-len(".rows.jsonl")] if name.startswith("verify-") and name.endswith(".rows.jsonl") else None


def candidates(shadow_dir, repo):
    """(新身分, 新池路徑, [同專案的舊池])。舊池 = 本樹舊身分那一池 + 第一列 repo 指向(還在的)同專案樹的池。"""
    import resume_store
    d = Path(shadow_dir)
    new_id = resume_store.repo_id(repo)
    new = d / ("verify-%s.rows.jsonl" % new_id)
    legacy = resume_store.legacy_repo_id(repo)
    out = []
    if legacy and legacy != new_id and (d / ("verify-%s.rows.jsonl" % legacy)).is_file():
        out.append(d / ("verify-%s.rows.jsonl" % legacy))
    for p in sorted(d.glob("verify-*.rows.jsonl")):
        if p == new or p in out:
            continue
        r = _first_repo(p)
        if r and os.path.isdir(r) and resume_store.repo_id(r) == new_id:
            out.append(p)
    return new_id, new, out


_TEST_IN_RAW = re.compile(rb'"test"\s*:\s*"([^"\\]+)"')


def _tomb(test, t_utc, stats, path):
    """壞行/寫一半的行 ⇒ 必跑墓碑(與 reuse_index.quarantine 同形):那一行可能是這支最新的紅,丟掉它舊綠就變最新。"""
    stats["tombstones"] = stats.get("tombstones", 0) + 1
    row = {"test": test, "rc": -1, "inputs": [], "measured": "unmeasured", "external": False,
           "why": ["quarantined-bad-row"], "time": time.strftime("%Y-%m-%d %H:%M"),
           "time_utc": t_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "repo_id": stats["new_id"], "merged_from": Path(path).name}
    return jsonl_safe.dumps(row) + "\n"


def _rows(path, allowed, stats):
    """一行一行讀;回 (time_utc, 序號, 改寫後的那一行)。不收的列計數。
    10-15 A4:(1) 壞行、寫到一半的最後一行不再靜默丟掉 —— 原地換成必跑墓碑(認不出是哪支 ⇒ 這一池每支都留);
    (2) 沒有 time_utc 的列(舊墓碑、舊列)排序鍵沿用同一池前一列的時間(每個池本來就照時間追加),
        以前用空字串 ⇒ 排到最前面、被更舊的池裡較晚的綠蓋掉。
    墓碑的時間取「壞行最晚可能是幾點」:同一池下一列的時間;後面沒有列(寫一半的最後一行)⇒ 合池當下。錯在多跑。
    沒有 time_utc 的紅/墓碑同理(補上那個時間上限寫進 time_utc,讀的那邊 reuse_index 才排得一致);沒時間的綠照前一列。"""
    last_t = ""
    names, unknown = set(), False
    pending = []          # 還不知道時間上限的墓碑(測試名)
    pending_rows = []     # 還不知道時間上限、本身沒有 time_utc 的紅/墓碑列
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh):
            partial = not raw.endswith(b"\n")
            row = None
            if not partial:
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    row = None
            if not isinstance(row, dict):
                stats["partial" if partial else "bad"] += 1
                if not raw.strip():
                    continue
                hit = sorted({m.decode("utf-8", "replace") for m in _TEST_IN_RAW.findall(raw)})
                if not hit:
                    unknown = True
                pending.extend(hit)
                continue
            rid = row.get("repo_id")
            if rid not in allowed and not (rid is None and not row.get("repo")):
                stats["foreign"] += 1
                continue
            tu = row.get("time_utc")
            if isinstance(row.get("test"), str):
                names.add(row["test"])
            row["repo_id"] = stats["new_id"]
            row.setdefault("merged_from", Path(path).name)
            if not (isinstance(tu, str) and tu):
                if _green(row) or "void" in row:
                    yield (last_t, n, jsonl_safe.dumps(row) + "\n")
                else:
                    pending_rows.append((n, row))      # 沒時間的紅/墓碑:時間上限 = 下一列的時間
                continue
            last_t = max(last_t, tu)
            for t in pending:
                yield (last_t, n, _tomb(t, last_t, stats, path))
            pending = []
            for pn, prow in pending_rows:
                yield (last_t, pn, jsonl_safe.dumps(dict(prow, time_utc=last_t)) + "\n")
            pending_rows = []
            yield (tu, n, jsonl_safe.dumps(row) + "\n")   # 同寫列的寫法(U+2028 等照樣跳脫 —— 二審)
    end = max(last_t, now_utc)
    for pn, prow in pending_rows:
        yield (end, pn, jsonl_safe.dumps(dict(prow, time_utc=end)) + "\n")
    tail = sorted(set(pending) | (names if unknown else set()))
    for t in tail:
        yield (end, 1 << 62, _tomb(t, end, stats, path))


def _green(row):
    rc = row.get("rc")
    return (isinstance(rc, int) and not isinstance(rc, bool) and rc == 0 and row.get("measured") is True
            and not row.get("timed_out"))


def _merge_locked(new, tmp, moved, allowed, stats, done=None):
    """二審:先開(必要時建立)新池並拿寫列用的同一把鎖,合完、接上既有列、換檔都在鎖裡;
    寫列那邊(jsonl_safe.append_locked)拿到鎖後會檢查檔案有沒有被換掉,換了就重開。回合進的列數。"""
    copied = 0
    with open(new, "ab") as fn:
        fcntl.flock(fn.fileno(), fcntl.LOCK_EX)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fo:
            for _t, _n, line in heapq.merge(*[_rows(p, allowed, stats) for p in moved], key=lambda x: x[0]):
                fo.write(line)
                copied += 1
            fo.flush()
        with open(new, "rb") as cur, open(tmp, "ab") as fb:    # 換身分後已經寫進新池的列比舊池新,接在後面
            fb.write(cur.read())
        os.chmod(tmp, 0o600)
        os.replace(tmp, new)
        if done is not None:
            done["committed"] = True
    return copied


def merge(shadow_dir, repo, apply=False, out=sys.stdout, now=None):
    """回 {"new": 路徑, "olds": [...], "copied": n, "foreign": n, "bad": n, "partial": n, "moved_to": 資料夾 / None, "busy": bool}。"""
    new_id, new, olds = candidates(shadow_dir, repo)
    res = {"new": str(new), "olds": [str(p) for p in olds], "copied": 0, "foreign": 0, "bad": 0, "partial": 0,
           "moved_to": None, "busy": False, "applied": False}
    size = sum(p.stat().st_size for p in olds if p.exists())
    out.write(T("head", {"mode": "" if apply else T("dry_note"), "name": new.name, "n": len(olds),
                         "mb": "%.1f" % (size / 1048576)}) + "\n")
    for p in olds:
        out.write("  %s %.1f MB\n" % (p.name, p.stat().st_size / 1048576))
    if not apply or not olds:
        return res
    new.parent.mkdir(parents=True, exist_ok=True)
    with open(str(new) + ".merge.lock", "a+") as lf:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            res["busy"] = True
            out.write(T("busy") + "\n")
            return res
        stats = {"bad": 0, "foreign": 0, "partial": 0, "new_id": new_id}
        allowed = {_pool_id(p) for p in olds} | {new_id}
        # 二審:先把舊池搬進 premerge-<時刻>/ 再從那裡合 —— 搬到一半失敗時沒有任何一列已經進新池,重跑不會重複
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        dest, k = Path(shadow_dir) / ("premerge-" + stamp), 1
        while True:                                   # 同一秒重跑不撞名(三審)
            try:
                dest.mkdir()
                break
            except FileExistsError:
                k += 1
                dest = Path(shadow_dir) / ("premerge-%s-%d" % (stamp, k))
        moved = []
        tmp = Path(str(new) + ".merge.tmp")
        copied = 0
        done = {"committed": False}
        held = []
        try:
            for p in olds:
                # 10-15 A4:搬舊池之前拿它的寫列鎖(jsonl_safe.append_locked 同一把),合完才放:
                #   以前不拿 ⇒ 舊版還在往舊池追加的那一列,寫進「已經搬走、已經讀過」的檔 ⇒ 不見。
                #   拿著鎖搬走 ⇒ 等鎖的寫列者拿到鎖時發現檔案換了,會在原路徑重開新檔(留給下一次合池)。
                lk = open(p, "ab")
                held.append(lk)
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
                os.replace(p, dest / p.name)
                moved.append(dest / p.name)
            copied = _merge_locked(new, tmp, moved, allowed, stats, done)
        except BaseException:
            if done["committed"]:     # 四審:新池已經換上合好的內容 ⇒ 不搬回(搬回來重跑會重複合)
                raise
            # 三審:中途失敗 ⇒ 舊池搬回原位、刪暫存;新池在 os.replace 之前都沒動過,所以什麼都沒進去也什麼都沒丟
            stuck = []
            for q in moved:
                back = Path(shadow_dir) / q.name
                if back.exists():     # 四審:合的期間舊程式又建了同名舊池 ⇒ 不覆蓋它,這份留在 premerge
                    stuck.append(q)
                    continue
                try:
                    os.replace(q, back)
                except OSError:
                    stuck.append(q)
            if stuck:
                out.write(T("stuck", {"dir": dest, "names": ", ".join(x.name for x in stuck)}) + "\n")
            try:
                tmp.unlink()
            except OSError:
                pass
            try:
                dest.rmdir()
            except OSError:
                pass
            raise
        finally:
            for lk in held:
                try:
                    fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
                finally:
                    lk.close()
        res.update(copied=copied, foreign=stats["foreign"], bad=stats["bad"], partial=stats["partial"],
                   moved_to=str(dest), applied=True)
        out.write(T("done", {"n": copied, "other": stats["foreign"], "bad": stats["bad"], "count": stats["partial"],
                             "dir": dest}) + "\n")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pool_merge.py", description=T("cli_desc"))
    ap.add_argument("--repo", default=str(HERE.parents[1]))
    ap.add_argument("--dir", default=None, help=T("cli_dir"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.dir is None:
        import shadow_tracer
        a.dir = shadow_tracer.shadow_dir_default()
    res = merge(a.dir, a.repo, apply=a.apply)
    return 1 if res["busy"] else 0


if __name__ == "__main__":
    sys.exit(main())
