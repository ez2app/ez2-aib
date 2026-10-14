#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""每包推主線、每批發一次版號(the maintainer 10-05 裁定;本檔把它變成必經之路)。

為什麼:改版號一定動 VERSION 等幾個檔,讀它們的測試有 243 支 ⇒ 每發一版推前檢查約 50 分鐘。
10-05 一天發了 12 版,光推前檢查就約 10 小時。改成:
  一包(package)= 推主線的一組提交,**不准**動版號相關的檔 ⇒ 推前檢查只跑受影響的測試;
  一批(release)= 一個發版提交,**只准**動版號相關的檔,內容由上一版以來推過的每一包自動整理。

指令:
  ship.py check   [--since REV] [--rev REV]   主線歷史形狀檢查(違規 rc 1);tests/test-release-batch-shape-1005.py 用同一份
  ship.py package [--branch B ...]            推一包:形狀 → 推前檢查 → 推 main(與 B)
  ship.py release --version X.Y.Z [--date D] [--title T] [--branch B ...] [--preview]
                                              發一批:先印預覽(會怎樣)→ **真人在終端機打出版號確認** → 產生發版提交
                                              → 形狀 → 推前檢查 → 推。沒有終端機(腳本/背景 AI)一律不發。
推前檢查有紅:只有「在 origin/main 上也紅」的才放行(自動拿到 origin/main 的副本重跑那幾支);
  其他紅、沒驗(4)、跑壞(其他結束碼)一律不推。不再靠手寫的「已知紅」名單。
  10-07(the maintainer 同意):只限 aib 自己的倉,「沒驗」是這台缺條件(缺工具、整支每格都跳過)⇒ 放行並大字列出;別的專案照舊不推。
★ 本檔不是倉裡的推送閘門(tools/governance 那些不動);它是推之前自己走的路,形狀測試讓別的路走不通。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_lib  # noqa: E402
import ship_profile as sp  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    return i18n_lib.t("aib.ship." + key, params, locale=LOC)

# 10-06 起規則來自專案設定(tools/release/ship_profile.py):`.aib/ship.toml` > aib 自己的倉 > 自動偵測。
# 下面是 aib 的那一份(發版內容的寫法 write_release 只認 aib;別的專案的發版下一包做)。
RELEASE_ONLY = tuple(sp.AIB.release_only)
RELEASE_ALLOWED = RELEASE_ONLY + tuple(sp.AIB.release_extra) + ("src/dataguard/pins.py", "deploy/line/manifest-v1.json")
PINS = "src/dataguard/pins.py"
MANIFEST = "deploy/line/manifest-v1.json"
PINNED_FILES = tuple(sp.AIB.pinned_files)
POLICY_START_FILE = sp.AIB.policy_start_file
# 發版要真人在終端機打版號確認(the maintainer 10-05:「除非真人強制發版,要告知會如何,如果同意就發版」)。
#   確認過的發版提交帶這一行;形狀檢查看到沒帶的發版提交 ⇒ 違規(繞過 ship.py 自己改版號的那種)。
CONFIRM_TRAILER = "Ship-Confirmed: human-tty v"     # 規則從「加進本檔的那一顆」起算(之前的歷史不追溯)


def git(repo, *a, check=True):
    p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr.strip()))
    return p.stdout


def show(repo, rev, path):
    p = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (rev, path)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.stdout if p.returncode == 0 else None


def changed_files(repo, sha):
    return [l for l in git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", sha)
            .splitlines() if l]


def _value(repo, rev, spec):
    path, key = sp.split_spec(spec)
    return sp.read_value(path, key, show(repo, rev, path))


def is_release(repo, sha, prof=None):
    """主版號(設定裡第一個版號規格)在這一顆變了 ⇒ 發版。整檔版號:碰到那個檔就算(10-05 的說法)。"""
    prof = prof or sp.load(repo)
    if not prof.version_files:
        return False
    path, key = sp.split_spec(prof.version_files[0])
    if key is None:
        return path in changed_files(repo, sha)
    return path in changed_files(repo, sha) and _value(repo, sha + "^", prof.version_files[0]) != _value(repo, sha, prof.version_files[0])


def check_commit(repo, sha, prof=None):
    """一顆提交違反的規則(字串清單;空 = 合格)。"""
    prof = prof or sp.load(repo)
    files = changed_files(repo, sha)
    bad = []
    if is_release(repo, sha, prof):
        allowed = prof.release_allowed()
        extra = sorted(f for f in files if f not in allowed)
        if extra:
            bad.append("release-has-other-files:%s" % ",".join(extra[:5]))
        ver = _value(repo, sha, prof.version_files[0]) or ""
        if (CONFIRM_TRAILER + ver) not in git(repo, "log", "-1", "--format=%B", sha):
            bad.append("release-without-human-confirmation")
        return bad
    for f in prof.release_only:
        if f in files:
            bad.append("package-touches-release-file:%s" % f)
    for spec in prof.version_files:
        path, key = sp.split_spec(spec)
        if key is not None and path in files and _value(repo, sha + "^", spec) != _value(repo, sha, spec):
            bad.append("package-changes-version:%s" % spec)
    return bad


def policy_start(repo, rev="HEAD", prof=None):
    """規則起點:第一顆加進規則起點檔的提交(含;aib 是 tools/release/ship.py,別的專案是 .aib/ship.toml)。
    找不到 ⇒ None(呼叫端判失敗,不當成沒事)。"""
    prof = prof or sp.load(repo)
    out = git(repo, "log", "--first-parent", "--diff-filter=A", "--format=%H", rev, "--", prof.policy_start_file).split()
    return out[-1] if out else None


def first_parent(repo, since_incl, rev="HEAD"):
    shas = git(repo, "rev-list", "--first-parent", "--reverse", "%s^..%s" % (since_incl, rev)).split()
    return shas


def check_range(repo, since_incl, rev="HEAD", prof=None):
    prof = prof or sp.load(repo)
    out = []
    for sha in first_parent(repo, since_incl, rev):
        for b in check_commit(repo, sha, prof):
            out.append((sha, b))
    return out


# ---- 推前檢查的紅:主線上也紅才放行 -----------------------------------------------------------------
RED_RE = re.compile(r"✗ \S+ ([^\s(（]+\.(?:py|sh))")


def gate_reds(text):
    return set(RED_RE.findall(text))


def gate_unverified(text):
    """推前檢查輸出裡「⊘ <測試>(沒驗:這台缺 …)」的測試名(aib test 的 row_unverified 那一行,依語系)。"""
    raw = i18n_lib.t("aib.test.row_unverified", None, locale=LOC)
    pat = re.compile("^[ \\t]*" + ".*?".join("(?P<t>[^\\s(（]+\\.(?:py|sh))" if x == "{test}" else re.escape(x)
                                      for x in re.split(r"(\{test\})", re.sub(r"\{(?!test\})\w+\}", "\x00", raw))
                                      if x).replace(re.escape("\x00"), ".*?") + "$", re.M)
    return {m.group("t") for m in pat.finditer(text)}


def may_push(gate_rc, reds, reds_on_base, unverified=(), machine_unverified_ok=False):
    """推前檢查 rc 0 ⇒ 推;rc 1 且每一支紅在 origin/main 上也紅 ⇒ 推;其他(沒驗、跑壞、新的紅)⇒ 不推。
    10-07(the maintainer 同意,只限 aib 自己的倉 = machine_unverified_ok):「沒驗」只會是這台缺條件(缺工具、整支每格都跳過、
      沙箱看不到真機安裝)⇒ 放行,但呼叫端照樣大字列出;rc 4 卻一支都認不出 ⇒ 不推(安全側)。
      同時補洞:以前 rc 1 時「沒驗」被紅蓋掉、從不檢查;現在別的專案有紅又有沒驗 ⇒ 不推。"""
    if gate_rc == 0:
        return True
    if unverified and not machine_unverified_ok:
        return False
    if gate_rc == 4:
        return bool(unverified)          # 上一行已擋掉別的專案;這裡只剩「認得出是哪幾支」
    return gate_rc == 1 and bool(reds) and set(reds) <= set(reds_on_base)


# 10-09 a seat(D10a):reds_on 每跑一次就留一份證據檔,路徑記在這裡給呼叫端印。
#   起因:10-09 02:4x 一支在 e26fc0e09 上是綠的測試被判成「主線也紅」、整套重跑後差點放行;事後重現不出,
#   而 reds_on 的輸出比對完就丟 ⇒ 無法定性。這裡只多留證據,判法一個字不改。
BASE_EVIDENCE = {"path": None}
EVIDENCE_ENV = ("EZ2_AIB_ROOT", "AIB_SOURCE_ROOT", "EZ2_AIB_HOME", "EZ2_AIB_STATE_ROOT", "TMPDIR",
                "AIB_COLLAB_REMOTE", "EZ2_AIHUB_SOCKET", "EZ2_AIB_INSTANCE")


def write_base_evidence(repo, base, wt, tests, rc, out):
    """副本那次重跑的證據:要的 base、副本實際 HEAD、每支測試在副本裡的 sha256、會左右「跑哪一棵樹」的環境變數、
    rc、解析出的紅、aib test 原始輸出(含沿用/實跑那行)。寫不出來不擋推包(證據是附帶的,不是判準)。"""
    import hashlib as _h
    try:
        head = git(wt, "rev-parse", "HEAD", check=False).strip()
        resolved = git(repo, "rev-parse", base, check=False).strip()
        lines = ["requested_base: %s" % base, "base_resolved: %s" % resolved, "copy_head: %s" % head,
                 "copy_path: %s" % wt, "rc: %s" % rc, "reds: %s" % sorted(gate_reds(out)), "tests:"]
        for t in sorted(tests):
            f = Path(wt) / t
            lines.append("  %s %s" % (_h.sha256(f.read_bytes()).hexdigest() if f.is_file() else "MISSING", t))
        lines.append("env:")
        for k in EVIDENCE_ENV + tuple(sorted(k for k in os.environ if k.startswith(("AIB_VERIFY", "EZ2_VERIFY")))):
            lines.append("  %s=%s" % (k, os.environ.get(k, "<unset>")))
        lines += ["--- aib test output ---", out]
        d = Path.home() / ".ez2-aib" / "ship"
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("base-run-%s-%d.log" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid()))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        BASE_EVIDENCE["path"] = str(f)
    except (OSError, RuntimeError):
        BASE_EVIDENCE["path"] = None
    return BASE_EVIDENCE["path"]


def _with_evidence(what):
    """提示裡 {what} 後面接證據檔路徑(不加新字典鍵:路徑本身就是說明)。"""
    p = BASE_EVIDENCE.get("path")
    return "%s  [%s]" % (what, p) if p else what


def reds_on(repo, base, tests, prof=None):
    """在 base 的暫存副本上跑這幾支,回紅的那些。aib 用 base 自己的 aib;別的專案用這一份 aib(--repo 指向副本)。"""
    prof = prof or sp.load(repo)
    if not tests:
        return set()
    d = tempfile.mkdtemp(prefix="ship-base-")
    wt = os.path.join(d, "wt")
    try:
        git(repo, "worktree", "add", "-f", "-q", "--detach", wt, base)
        cmd = (["bash", "src/cockpit/aib", "test"] if prof.gate_mode == "aib"
               else ["bash", str(ROOT / "src" / "cockpit" / "aib"), "test", "--repo", wt])
        p = subprocess.run(cmd + sorted(tests), cwd=wt, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=7200)
        write_base_evidence(repo, base, wt, tests, p.returncode, p.stdout + p.stderr)
        return gate_reds(p.stdout + p.stderr)
    finally:
        git(repo, "worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(d, ignore_errors=True)


EARLY_RED = 5   # pushgate --fail-fast:有紅、其餘沒跑完


def _known_base_reds_file(repo):
    """依倉分(同一個倉的每個工作樹共用一份;10-08 實踩:換一個工作樹推就讀不到 ⇒ 一紅就停又為舊紅停、多跑一輪)。
    取不到倉的身分(不是 git 倉)⇒ 退回依路徑。"""
    import hashlib as _h
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    try:
        import reuse_index
        rid = reuse_index.repo_identity(str(repo))
    except Exception:  # noqa: BLE001
        rid = None
    key = ("repo:%s" % rid) if rid else str(Path(repo).resolve())
    tag = _h.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".ez2-aib" / "ship" / ("known-base-reds-%s.json" % tag)


def _ship_result_file(repo, kind):
    """10-13 清查補強 2:推包結果留一份給 aib project sweep 讀(以前拒推只印一行就結束,事後看不到卡在哪)。
    跟主線舊紅同一個資料夾、同一個倉標籤:~/.ez2-aib/ship/last-<push|refuse>-<標籤>.json。"""
    return _known_base_reds_file(repo).with_name(_known_base_reds_file(repo).name.replace("known-base-reds-", "last-%s-" % kind))


def _record_tag_index(repo):
    """ship/tag-<倉標籤>.json = {repo, roots}:讓 aib project sweep 從正本也找得到推包樹(另一個 clone、標籤不同)
    留的檔 —— 清查拿自己的根提交比對,同專案的標籤都讀(審查 10-13)。寫不了不影響推包。"""
    try:
        f = _known_base_reds_file(repo).with_name(_known_base_reds_file(repo).name.replace("known-base-reds-", "tag-"))
        roots = sorted(git(repo, "rev-list", "--max-parents=0", "HEAD", check=False).split())
        if not roots:
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"repo": str(Path(repo).resolve()), "roots": roots}) + "\n", encoding="utf-8")
        os.replace(tmp, f)
    except Exception:  # noqa: BLE001
        pass


def record_result(repo, kind, **fields):
    """寫 last-<kind>-<標籤>.json(at=現在 epoch、branch、head,加上 fields)。寫不了不影響推包。"""
    try:
        f = _ship_result_file(repo, kind)
        f.parent.mkdir(parents=True, exist_ok=True)
        d = {"at": time.time(), "repo": str(repo), "kind": kind}
        try:
            d["branch"] = git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
            d["head"] = git(repo, "rev-parse", "HEAD", check=False).strip()
        except Exception:  # noqa: BLE001
            pass
        d.update(fields)
        _record_tag_index(repo)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, f)
    except Exception:  # noqa: BLE001
        pass


def load_known_base_reds(repo):
    """上一次推包確認過「主線上也紅」的測試(10-07)。讀不到 ⇒ []。只拿來讓一紅就停不為它停;能不能推照樣每次重新確認。"""
    try:
        d = json.loads(_known_base_reds_file(repo).read_text(encoding="utf-8"))
        return sorted(str(t) for t in d.get("tests") or [])
    except (OSError, ValueError, AttributeError):
        return []


def save_known_base_reds(repo, tests):
    _record_tag_index(repo)
    f = _known_base_reds_file(repo)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"tests": sorted(tests)}, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


LOW_DISK_GB = 2.0             # 暫存區所在磁碟剩不到這麼多 ⇒ 推前先提醒


def warn_low_disk(path=None, free_gb=None):
    """暫存區所在的磁碟快滿了 ⇒ 印一行提醒(含 aib tmp --apply);回 True 表示有提醒。讀不到 ⇒ 不提醒。"""
    if free_gb is None:
        try:
            import tempfile
            free_gb = shutil.disk_usage(path or tempfile.gettempdir()).free / 1024.0 ** 3
        except OSError:
            return False
    if free_gb >= LOW_DISK_GB:
        return False
    print(T("low_disk", {"limit": "%.1f" % free_gb}))
    return True


GATE_GB_PER_SHARD = 2.0      # 10-08 實測:記憶體不夠時三份一起被系統砍(OOM,rc=-9);每份留約 2 GB


def available_gb():
    """可用記憶體(GB);讀不到 ⇒ None(照原本的份數)。Linux 讀 /proc/meminfo 的 MemAvailable。"""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0 / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def gate_jobs_for_memory(want, avail=None):
    """推前檢查分幾份:想要 want 份,可用記憶體每份不到 GATE_GB_PER_SHARD 就少分(至少 1 份)。讀不到記憶體 ⇒ want。"""
    avail = available_gb() if avail is None else avail
    if avail is None:
        return want
    n = max(1, min(want, int(avail // GATE_GB_PER_SHARD)))
    if n < want:
        print(T("gate_jobs_memory", {"n": n, "was": want, "limit": "%.1f" % avail}))
    return n


def run_gate(repo, fail_fast=False, prof=None, gate_base=None):
    """aib:用要推的那份程式自己的推前檢查(--jobs 3,照 10-05);別的專案:用這一份 aib 的,通用模式。"""
    prof = prof or sp.load(repo)
    if prof.gate_mode == "aib":
        cmd = [sys.executable, str(Path(repo) / "tools" / "verify" / "pushgate.py"), "--repo", str(repo),
               "--jobs", str(gate_jobs_for_memory(3))]
    else:
        cmd = [sys.executable, str(ROOT / "tools" / "verify" / "pushgate.py"), "--repo", str(repo), "--mode", "generic",
               "--jobs", str(prof.gate_jobs())]
    known = []
    if fail_fast and (prof or sp.load(repo)).gate_mode == "aib":   # 一般專案不放行舊紅,不用跳過
        for t in load_known_base_reds(repo):
            known += ["--known-red", t]
    return _stream(cmd + (["--fail-fast"] if fail_fast else []) + known + (["--base", gate_base] if gate_base else []))


def _stream(cmd, out=None):
    """10-07:邊跑邊印(以前整段收起來、跑完才印最後 6000 字 ⇒ 「預計幾點完成」要等整輪跑完才看得到,
    寫進檔案的也一樣)。每一行立刻寫出並 flush;回 (結束碼, 全文) 給後面判紅、判沒驗。"""
    out = out or sys.stdout
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,   # stderr 直接接到終端機,不進判讀
                         bufsize=1, env=dict(os.environ, PYTHONUNBUFFERED="1"))
    lines = []
    for line in p.stdout:
        lines.append(line)
        out.write(line)
        out.flush()
    return p.wait(), "".join(lines)


MOVED_ROUNDS = 3      # 10-11 the maintainer「主線動了也能放行」:推之前主線又動了,最多追 3 次,之後照舊停


def _is_doc(path):
    """只認 .md。docs/ 底下也有 .py/.sh/.json(掃描工具、治具、MANIFEST,有測試在讀),不能整個目錄當文件(10-11 審查第二輪)。
    10-14:src/ tools/ bin/ 底下的 .md 不算 —— pre-push 的 B8 閘把這三個目錄底下一律當碼(推包收據要求檢查那顆到推的那顆
    碼類零差),這裡放行疊上去會在推的那一步才被擋、白等重試;兩邊同一把尺,在這裡就說清楚停。"""
    return path.endswith(".md") and not path.startswith(("src/", "tools/", "bin/"))


def _rerun_selected(repo, tests, prof):
    """把挑出來的測試在疊好的樹上重跑。回 (rc, 紅的集合);rc≠0 而列不出紅 ⇒ 呼叫端當紅看(沒證據不算過)。"""
    cmd = ["bash", str(Path(repo) / "src" / "cockpit" / "aib"), "test"]
    p = subprocess.run(cmd + sorted(tests), cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=7200)
    sys.stdout.write((p.stdout or "")[-3000:])
    return p.returncode, gate_reds(p.stdout + p.stderr)


def _select_tests(repo, base, head):
    """base..head 會影響哪些測試:用要推的那份程式自己的 select_changed(同一套挑選規則,不另寫)。
    回 (rc, 測試清單);量不到(rc≠0 或印出 unmeasurable)⇒ rc 非 0。"""
    p = subprocess.run([sys.executable, str(Path(repo) / "tools" / "verify" / "select_changed.py"), "--repo", str(repo),
                        "--base", base, "--head", head, "--print-tests"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    if p.returncode != 0 or "unmeasurable" in p.stdout:
        return (p.returncode or 1), []
    return 0, [l.strip() for l in p.stdout.splitlines() if l.strip().startswith("tests/")]


def catch_up_docs_only(repo, branches, expect_remote, remote, prof):
    """推前發現主線從 expect_remote 動到 remote。新來的全是文件(*.md、docs/)⇒ 疊上去、只重跑受影響的測試,綠了回 None
    (呼叫端照推);任何一步不符合或量不到 ⇒ 回一句為什麼,呼叫端照舊停(10-11 the maintainer 令,設計稿 DESIGN-ship-remote-moved)。
    ★ 為什麼不是「無交集就直接推」:10-11 實量,主線只多一份 docs/*.md,影響挑選也有 44 支(掃全倉的閘、量到讀過它的),
      所以一律重跑挑出來的那些,不靠「應該沒關係」放行。
    ★ 程式檔一律不追:兩邊改不同程式檔也可能合起來壞(改名、改介面),那要整輪重跑,照舊停給人。"""
    if prof is None or prof.gate_mode != "aib":
        return T("moved_why_generic")
    if os.environ.get("AIB_SHIP_MOVED_STRICT") == "1":
        return T("moved_why_off")
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", expect_remote, remote],
                      stdin=subprocess.DEVNULL, capture_output=True).returncode != 0:
        return T("moved_why_rewritten")
    # --no-renames:改名只印新路徑 ⇒ 把 tools/x.py 搬成 docs/x.py 會被當成「只動文件」(10-11 審查抓到);拆成刪 + 加,舊路徑算程式
    files = [f for f in git(repo, "diff", "--name-only", "--no-renames", expect_remote, remote).splitlines() if f]
    code = [f for f in files if not _is_doc(f)]
    if code:
        return T("moved_why_not_docs", {"what": code[:5]})
    if git(repo, "rev-list", "--merges", "%s..HEAD" % expect_remote).strip():
        return T("moved_why_merges")                # 包裡有合併提交:rebase 會把它攤平,改了主線的形狀,不自動做
    on_remote = [b for b in branches if git(repo, "ls-remote", "--heads", "origin", b, check=False).strip()]
    if on_remote:                                  # 疊過之後同名分支推不上去(不是快轉);不硬推
        return T("moved_why_branch_on_remote", {"what": on_remote})
    try:
        rc, tests = _select_tests(repo, expect_remote, remote)
    except subprocess.TimeoutExpired:
        rc, tests = -1, []
    if rc != 0:
        return T("moved_why_unmeasurable", {"rc": rc})
    orig = git(repo, "rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", str(repo), "rebase", "-q", remote], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True, stdin=subprocess.DEVNULL)
        return T("moved_why_conflict")
    print(T("moved_rerun", {"n": len(files), "sha": remote[:9], "what": len(tests)}))
    if tests:
        try:
            rc, reds = _rerun_selected(repo, tests, prof)
        except subprocess.TimeoutExpired:
            rc, reds = -1, set()
        if rc != 0 or reds:
            git(repo, "reset", "-q", "--hard", orig)      # 不推就退回疊之前的樣子,不留一棵疊了一半的樹給人猜
            return T("moved_why_red", {"rc": rc, "what": sorted(reds)})
    print(T("moved_caught_up", {"n": len(files), "sha": remote[:9], "what": len(tests)}))
    return None


def ship_receipt_env(repo, base, gate, caught_up=False):
    """10-14(a seat 派):推包樹裝了 pre-push 之後,ship.py 推 main 被 B8 閘擋(碼類 main push 缺收據)。
    推前寫一張推包收據(b8-machine-rules.ship_receipt_record,落在 B8 證據落點),只帶給這一次 git push。
    用的是被推那棵樹自己的 b8 程式(pre-push 掛勾驗的也是那一份)。寫不出來 ⇒ 印一行、回 None 照推,讓掛勾照規矩擋。"""
    f = Path(repo) / "tools" / "governance" / "b8-machine-rules.py"
    if not f.is_file():
        return None
    saved_path = list(sys.path)          # b8 檔頭會把它那棵樹的 src/cockpit、tests 插到最前面,用完還原
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("b8_machine_rules_ship", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        path = mod.ship_receipt_record(git(repo, "rev-parse", "HEAD").strip(), base, gate["head"], gate["rc"],
                                       gate.get("old_reds", ()), gate.get("unverified", ()),
                                       gate.get("flaky_allowed", ()), caught_up=caught_up)
    except Exception as e:  # noqa: BLE001 - 收據寫不出來不擋在這裡,交給 pre-push 掛勾擋
        print(T("ship_receipt_failed", {"reason": str(e)[:300]}))
        return None
    finally:
        sys.path[:] = saved_path
    print(T("ship_receipt_written", {"path": path}))
    env = dict(os.environ, AIB_B8_SHIP_RECEIPT=str(Path(path).resolve()))   # 掛勾的工作目錄不同,給絕對路徑
    env.pop("AIB_B8_MAIN_PUSH_RECEIPT", None)    # 殼裡留著的舊 B8 收據會蓋過推包收據、驗不過而擋
    return env


def push(repo, branches, expect_remote, prof=None, pushed_base=None, gate=None):
    """prof 有給(推前檢查跑完的那條路)⇒ 主線動了、新來的全是文件時,疊上去重跑受影響的測試再推;
    沒給(緊急推)⇒ 照舊一動就停。pushed_base(list)有給 ⇒ 推成後放進最後疊上的主線 sha(回報草稿要用疊後的編號)。
    gate 有給(推前檢查放行的結果:head / rc / old_reds / unverified / flaky_allowed)⇒ 推前寫推包收據給 pre-push 的 B8 閘;
    沒給(緊急推)不寫,碼類推 main 照樣被掛勾擋(緊急推要人自己決定)。"""
    pushed_base = pushed_base if pushed_base is not None else []
    start = git(repo, "rev-parse", "HEAD").strip() if prof is not None else ""
    for attempt in range(MOVED_ROUNDS + 1):
        git(repo, "fetch", "-q", "origin", "main")
        remote = git(repo, "rev-parse", "origin/main").strip()
        if remote == expect_remote:
            break
        what = "%s != %s" % (remote[:8], expect_remote[:8])
        if prof is None:
            raise SystemExit(T("remote_moved", {"what": what}))
        why = catch_up_docs_only(repo, branches, expect_remote, remote, prof) if attempt < MOVED_ROUNDS \
            else T("moved_why_rounds", {"n": MOVED_ROUNDS})
        if why:
            if start and git(repo, "rev-parse", "HEAD").strip() != start:
                git(repo, "reset", "-q", "--hard", start)   # 前幾輪已經疊過 ⇒ 退回最一開始,不留疊到中途的樹(審查第三輪)
            raise SystemExit(T("remote_moved", {"what": what}) + "\n" + why)
        expect_remote = remote
    caught_up = bool(start) and git(repo, "rev-parse", "HEAD").strip() != start
    pushed_base[:] = [expect_remote]
    refs = ["HEAD:main"] + ["HEAD:%s" % b for b in branches]
    env = ship_receipt_env(repo, expect_remote, gate, caught_up=caught_up) if gate is not None else None
    for d in (0, 2, 4, 8, 16):
        time.sleep(d)
        p = subprocess.run(["git", "-C", str(repo), "push", "origin", *refs], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env=env)
        if p.returncode == 0:
            sync_canonical(repo)
            return
    raise SystemExit(T("push_failed", {"reason": p.stderr[-1000:]}))


def _norm_url(u):
    u = (u or "").strip().rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def ensure_canonical_guard(canon):
    """P8:正本快轉後順手確認「正本 main 禁直接提交」掛勾裝著(裝的是正本自己那份,剛快轉到最新)。
    只在有變化或被別的掛勾佔著時印一行;任何錯都不影響推包。回安裝器的結束碼(-1 = 沒跑)。"""
    tool = Path(canon) / "src" / "policy" / "hooks" / "install-canonical-guard.sh"
    if not tool.exists():
        return -1
    try:
        p = subprocess.run(["bash", str(tool), "install"], cwd=str(canon), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return -1
    if p.returncode == 3:
        print(T("canon_guard_foreign", {"path": canon, "what": p.stdout.strip().replace("\t", " ").replace("\n", "; ")}))
    elif "installed" in p.stdout:
        print(T("canon_guard_installed", {"path": canon}))
    return p.returncode


def sync_canonical(repo, canon=None):
    """10-09 the maintainer「推包推成後自動更新正本」(a seat 提):右鍵與各席的 hook 都跑正本 ~/ez2-aib,正本不跟上 = 推了沒生效。
    只在安全時快轉:正本在 main、沒有未提交的追蹤檔、HEAD 是遠端 main 的祖先 ⇒ git merge --ff-only;
    不符合 ⇒ 只印一行提醒、不動。這一步出任何錯都不影響「推成了」(推送已完成)。回傳狀態字串給測試用。
    正本位置:AIB_CANONICAL_REPO,預設 ~/ez2-aib;用哪個 remote = 網址跟推包 clone 的 origin 相同的那個。"""
    canon = Path(canon or os.environ.get("AIB_CANONICAL_REPO") or (Path.home() / "ez2-aib"))
    try:
        if not (canon / ".git").exists():
            return "absent"
        if canon.resolve() == Path(repo).resolve():
            return "self"
        url = _norm_url(git(repo, "remote", "get-url", "origin"))
        rem = next((r for r in git(canon, "remote").split()
                    if _norm_url(git(canon, "remote", "get-url", r, check=False)) == url), None)
        if rem is None:      # 推的不是正本那個倉(別的專案、測試的暫存倉)⇒ 跟正本無關,安靜跳過
            return "no_remote"
        if git(canon, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() != "main":
            print(T("canon_skip", {"path": canon, "reason": T("canon_not_main")}))
            return "not_main"
        if git(canon, "status", "--porcelain", "--untracked-files=no").strip():
            print(T("canon_skip", {"path": canon, "reason": T("canon_dirty")}))
            return "dirty"
        git(canon, "fetch", "-q", rem, "main")
        target = "%s/main" % rem
        if subprocess.run(["git", "-C", str(canon), "merge-base", "--is-ancestor", "HEAD", target],
                          stdin=subprocess.DEVNULL, capture_output=True).returncode != 0:
            print(T("canon_skip", {"path": canon, "reason": T("canon_diverged")}))
            return "diverged"
        before = git(canon, "rev-parse", "--short", "HEAD").strip()
        git(canon, "merge", "-q", "--ff-only", target)
        after = git(canon, "rev-parse", "--short", "HEAD").strip()
        ensure_canonical_guard(canon)
        if before == after:
            return "same"
        print(T("canon_synced", {"path": canon, "sha": after}))
        return "synced"
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(T("canon_skip", {"path": canon, "reason": str(e)[-300:]}))
        return "error"


def rerun_same(repo, tests, prof):
    """同一份程式上把這幾支再跑一次,回「這次還是紅」的那些(aib test 紅永遠重跑,不會沿用)。"""
    cmd = (["bash", str(Path(repo) / "src" / "cockpit" / "aib"), "test"] if prof.gate_mode == "aib"
           else ["bash", str(ROOT / "src" / "cockpit" / "aib"), "test", "--repo", str(repo)])
    p = subprocess.run(cmd + sorted(tests), cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=7200)
    return gate_reds(p.stdout + p.stderr)


def judge_flaky(repo, new, flaky, out, base, sha):
    """P1(docs/DESIGN-ship-merge-queue-1011.md §4 P1):每支新紅判一次能不能窄放行,重跑綠的那些記帳。
    回 {test: (allowed, why)}。任何一步出錯 ⇒ 該支不放行(why=error)。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import flaky_ledger as fl
    try:
        import pushgate
        import reuse_index as ri
        idx = ri.build_index([], repo=str(repo))       # 剛跑完推前檢查,最新一列就是這一輪量到的讀檔
        changed = fl.changed_files(repo, base)         # git 失敗/空 ⇒ None ⇒ 每支都「量不到讀了什麼」⇒ 不放行
        pkg = fl.package_id(repo, base)
        banner = pushgate._line_re("output_banner")    # 跟 red_outputs 同一個判準:只認整行的「--- X 的輸出 ---」
        blocks, cur = {}, None                          # (第三輪審查:測試自己印「--- tests/y.py …」會把輸出歸到別支頭上)
        for ln in pushgate.red_outputs(out or ""):
            m = re.search(r"([^\s(（]+\.(?:py|sh))", ln) if banner.fullmatch(ln) else None
            if m:
                cur = m.group(1)
                blocks[cur] = []
            elif cur:
                blocks[cur].append(ln)
    except Exception:  # noqa: BLE001 - 量不到 ⇒ 全部不放行
        return {t: (False, "error") for t in new}
    got = {}
    for t in new:
        text = "\n".join(blocks.get(t, []))
        reads = fl.reads_changed(idx, t, changed) if changed is not None else None
        ok, why = fl.judge(t, t in flaky, text, reads, pkg=pkg)
        got[t] = (ok, why)
        if t in flaky and not fl.record(t, sha, ok, why, text, pkg=pkg):
            print(T("flaky_ledger_unwritable", {"path": fl.ledger_path()}))
    return got


def notify_flaky(repo, tests):
    """放行的不穩定測試,aib dm 退給最近改它的那一席;查不到就請推包席轉告。送不出去不擋推包。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import flaky_ledger as fl
    for t in tests:
        code = fl.author_seat(repo, t)
        addr = fl.seat_address(code) if code else None
        if not addr:
            print(T("flaky_dm_none", {"test": t}))
            continue
        n = fl.marks(t, fl.LIMIT_DAYS)
        rc = fl.notify(addr, T("flaky_dm", {"test": t, "n": n, "path": fl.ledger_path()}))
        print(T("flaky_dm_sent" if rc == 0 else "flaky_dm_failed", {"what": addr, "rc": rc}))


def gate_savings(repo):
    """這一輪推前檢查省了多少(pushgate 寫在 ship-stats.jsonl 的最後一列):{ran, reused, serial, actual, less, pct}(秒);
    讀不到 ⇒ None。10-14 the maintainer「為什麼沒有程式化成必要?為什麼能跳過?」:省多少只印在中間那段報表,
      轉述的人只抓最後的結論行就漏掉 ⇒ 結論行(推成 / 不推)一律帶這一段,也寫進 last-push / last-refuse。"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import pushgate
        rows = [r for r in pushgate.read_stats(repo) if r.get("files_total")]
        r = rows[-1] if rows else None
    except Exception:  # noqa: BLE001 - 讀不到就照實說讀不到
        r = None
    if not r:
        return None
    serial, actual = float(r.get("full_s") or 0), float(r.get("wall_s") or 0)
    ran = int(r.get("files_ran") or 0)
    less = max(0.0, serial - actual)
    return {"ran": ran, "reused": max(0, int(r.get("files_total") or 0) - ran), "serial": serial, "actual": actual,
            "less": less, "pct": (100.0 * less / serial) if serial > 0 else 0.0}


def savings_text(repo, sv=None):
    sv = sv if sv is not None else gate_savings(repo)
    if not sv:
        return T("verdict_savings_none")
    import pushgate
    return T("verdict_savings", {"ran": sv["ran"], "reused": sv["reused"], "serial": pushgate.dur_text(sv["serial"]),
                                 "actual": pushgate.dur_text(sv["actual"]), "seconds": pushgate.dur_text(sv["less"]),
                                 "pct": "%.0f" % sv["pct"]})


def refuse(repo, rc, new, prof, old=(), unverified=(), out="", base=None, allow=False):
    """不推。新的紅先在同一份程式上再跑一次:這次綠了 ⇒ 標「時紅時綠」(還是不推,但說清楚多半不是這一包弄壞的)。
    10-08:擋下這包的測試記下來,下一次推前檢查一律最先跑(pushgate.add_blockers / tiers)。
    10-11(the maintainer「遇紅不停 程式化」):拒推時把整套跑出來的**全部**列出 —— 新的紅(refuse_push)之外,
      主線上也紅的舊紅、沒驗的也各印一行,修的人一次看到全部,不用一輪修一支。
    10-12 P1(allow=True,只有 aib 自己的倉、整套跑完 rc=1 那條路):新紅**每一支**都三條全中(judge_flaky)⇒
      不推改成放行:回放行的清單給呼叫端照推,並記帳、退給作者。有一支不中 ⇒ 照舊不推。"""
    if old:
        print(T("refuse_also_old", {"what": sorted(old)}))
    if unverified:
        print(T("refuse_also_unverified", {"what": sorted(unverified)}))
    if new:
        try:
            sys.path.insert(0, str(ROOT / "tools" / "verify"))
            import pushgate
            pushgate.add_blockers(repo, new)
        except Exception:  # noqa: BLE001 - 記不了不影響「不推」
            pass
        try:
            flaky = sorted(set(new) - rerun_same(repo, new, prof))
        except Exception:  # noqa: BLE001 - 再跑失敗就不標,照常不推
            flaky = []
        if flaky:
            print(T("flaky", {"what": flaky}))
        if allow and base and flaky:
            verdicts = judge_flaky(repo, new, set(flaky), out, base, git(repo, "rev-parse", "HEAD").strip())
            for t in sorted(verdicts):
                print(T("flaky_verdict", {"test": t, "what": T("flaky_why_" + verdicts[t][1].replace("-", "_"))}))
            if all(ok for ok, _ in verdicts.values()):
                allowed = sorted(verdicts)
                print(T("flaky_allowed", {"what": allowed}))
                notify_flaky(repo, allowed)
                return allowed
    sv = gate_savings(repo)
    record_result(repo, "refuse", rc=rc, new=sorted(new or []), old=sorted(old or []), unverified=sorted(unverified or []),
                  savings=sv)
    raise SystemExit(T("refuse_push", {"rc": rc, "what": new or T("no_red_rc")}) + " | " + savings_text(repo, sv))


def base_reds(repo, base, reds, prof):
    """這幾支紅在 base 上也紅嗎(也紅 ⇒ 舊問題,放行)。只有 aib 模式查。
    ★ 10-06 itsdangerous 實踩:別的專案常用 `pip install -e .`,測試 import 的永遠是工作目錄那份(壞的)程式,
      在 base 副本上重跑一樣紅 ⇒ 被當成「主線也紅」放行,壞程式推上去了。aib 的測試只讀自己那棵樹,不會這樣。
      所以通用模式不放行舊紅:任何紅都不推(要推就先修紅)。"""
    if not reds:
        return set()
    if prof.gate_mode != "aib":
        print(T("generic_no_base_exempt", {"what": sorted(reds)}))
        return set()
    return reds_on(repo, base, reds, prof=prof)


def gate_then_push(repo, branches, base, prof=None, gate_base=None, before_push=None, fail_fast=False, catch_up=True):
    """10-11(the maintainer「遇紅不停 程式化」):預設整套跑完,再用全部的紅 / 主線舊紅 / 沒驗判能不能推(may_push 規則不變),
      拒推時一次列出全部。以前預設 --fail-fast:一紅就停、其餘沒跑 ⇒ 10-11 一個 158 筆的包連紅三輪、每輪只看到一支。
    fail_fast=True(ship.py package/release --fail-fast,明示才開):照舊先一紅就停 ——
      停在新的紅 ⇒ 馬上拒推;停在主線上也紅的舊紅 ⇒ 其餘沒驗,整套再跑一次(沿用剛才跑綠的),照原規則判。"""
    prof = prof or sp.load(repo)
    gk = {"gate_base": gate_base} if gate_base else {}
    rc, out = run_gate(repo, fail_fast=fail_fast, prof=prof, **gk)
    gate_head = git(repo, "rev-parse", "HEAD", check=False).strip()    # 推包收據要記檢查跑在哪一顆上
    if rc == EARLY_RED:
        reds = gate_reds(out)
        on_base = base_reds(repo, base, reds, prof)
        new = sorted(set(reds) - on_base)
        if new or not reds:
            refuse(repo, rc, new, prof)
        print(T("early_old_red", {"what": _with_evidence(sorted(reds))}))
        rc, out = run_gate(repo, prof=prof, **gk)
    reds = gate_reds(out)
    unv = gate_unverified(out)
    on_base = base_reds(repo, base, reds, prof) if rc == 1 else set()
    if prof.gate_mode == "aib":
        save_known_base_reds(repo, on_base)    # 下一包一紅就停時不為這幾支停(照樣跑、照樣報、照樣重新確認)
    allowed = []
    if not may_push(rc, reds, on_base, unv, machine_unverified_ok=(prof.gate_mode == "aib")):
        allowed = refuse(repo, rc, sorted(reds - on_base), prof, old=reds & on_base, unverified=unv, out=out, base=base,
                         allow=(rc == 1 and prof.gate_mode == "aib"))
    if reds & on_base:                     # 放行的不穩定測試不是「主線舊紅」,不混在這一行(上面 flaky_allowed 已大字列出)
        print(T("allow_base_red", {"what": _with_evidence(sorted(reds & on_base))}))
    if unv:
        print(T("allow_unverified", {"what": sorted(unv)}))
    if before_push is not None:
        before_push()
    final = []
    gate = {"head": gate_head, "rc": rc, "old_reds": sorted(on_base & set(reds)), "unverified": sorted(unv),
            "flaky_allowed": list(allowed or [])}
    push(repo, branches, base, prof=prof if catch_up else None, pushed_base=final,   # 發版不追:疊進來的文件會掉出發版說明
         gate=gate)
    record_result(repo, "push", old=sorted(on_base & set(reds)), unverified=sorted(unv), savings=gate_savings(repo))
    return {"old_reds": sorted(on_base & set(reds)), "unverified": sorted(unv), "flaky_allowed": list(allowed or []),
            "pushed_base": final[0] if final else base}


# ---- 發版提交 ----------------------------------------------------------------------------------------
def last_release(repo, rev="HEAD"):
    out = git(repo, "log", "--first-parent", "--format=%H", "-1", rev, "--", "VERSION").split()
    return out[0] if out else None


def packages_since(repo, rel, rev="HEAD"):
    return [l.split(" ", 1) for l in git(repo, "log", "--first-parent", "--reverse", "--format=%h %s",
                                          "%s..%s" % (rel, rev)).splitlines() if l]


def write_release(repo, ver, date, title, items, branch):
    repo = Path(repo)
    old = (repo / "VERSION").read_text().strip()
    (repo / "VERSION").write_text(ver + "\n")
    r = (repo / "README.md").read_text(encoding="utf-8").split("\n")
    if not r[0].startswith("# Ez2 AIB v" + old):
        raise SystemExit(T("readme_bad", {"version": old, "what": repr(r[0])}))
    r[0] = r[0].replace("v" + old, "v" + ver, 1)
    sec = ["## v%s 這一版做了什麼(%s)" % (ver, date), "", "一句話:" + title, ""] + ["- %s(%s)" % (s, h) for h, s in items] + [""]
    (repo / "README.md").write_text("\n".join(r[:1] + sec + r[1:]), encoding="utf-8")
    c = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    i = c.index("\n## [")
    c = c[:i] + "\n## [%s] - %s\n\n### Changed\n" % (ver, date) + "".join("- %s(%s)\n" % (s, h) for h, s in items) + c[i:]
    (repo / "CHANGELOG.md").write_text(c, encoding="utf-8")
    p = (repo / PINS).read_text(encoding="utf-8")
    p2 = p.replace('PINNED_AT_VERSION = "%s"' % old, "# v%s(%s %s)重新確認:v%s..本版 pinned 兩檔 0 顆改動(ship.py 查過),sha256 同前。\n"
                   'PINNED_AT_VERSION = "%s"' % (ver, date, branch, old, ver))
    if p2 == p:
        raise SystemExit(T("pins_missing", {"version": old}))
    (repo / PINS).write_text(p2, encoding="utf-8")
    m = (repo / MANIFEST).read_text(encoding="utf-8")
    m2 = m.replace('"version": "%s"' % old, '"version": "%s"' % ver, 1)
    if m2 == m:
        raise SystemExit(T("manifest_missing", {"version": old}))
    (repo / MANIFEST).write_text(m2, encoding="utf-8")
    o = (repo / "docs/EZ2-AIB-DEV-OUTLINE.md").read_text(encoding="utf-8")
    mv = re.search(r"^版本:v2\.(\d+)\((.*)$", o, re.M)
    ms = re.search(r"^## §(\d+)\(", o, re.M)
    n, s = int(mv.group(1)) + 1, int(ms.group(1)) + 1
    o = o.replace(mv.group(0), "版本:v2.%d(%s %s:**§%d** v%s %s;承 v2.%s = §%d)\n前一版:v2.%s(%s" % (
        n, date, branch, s, ver, title, mv.group(1), s - 1, mv.group(1), mv.group(2)), 1)
    o = o.replace(ms.group(0), "## §%d(v2.%d,%s,%s) v%s——%s\n\n%s\n\n%s" % (
        s, n, date, branch, ver, title, "\n".join("- %s(%s)" % (x, h) for h, x in items), ms.group(0)), 1)
    (repo / "docs/EZ2-AIB-DEV-OUTLINE.md").write_text(o, encoding="utf-8")
    return old


def move_baseline(repo, prev_bump, prev_ver, ver, date):
    """發版基準移到上一個改版號顆;它有 tag 就照實說沒有 tag 債,沒有就記一筆。"""
    p = Path(repo) / "docs/reports/RELEASE-TAG-BASELINE.md"
    s = p.read_text(encoding="utf-8")
    cur = [l for l in s.splitlines() if l.startswith("baseline: ")][0]
    tagged = bool(git(repo, "tag", "--points-at", prev_bump, check=False).strip())
    debt = "已打 tag,沒有新的 tag 債" if tagged else "沒有打 tag,tag 債照記不抹"
    txt = ("baseline: %s\n\n%s 改基準(ship.py release,為發 v%s;the maintainer 10-05「每包推主線、每批發一次版號」):v%s 改版號顆 `%s` 已在 "
           "origin/main,%s。基準移到它,自它起 VERSION 只改一次(%s→%s)。the maintainer 反對就回退這一顆。\n舊 baseline(為發 v%s): %s\n") % (
        prev_bump, date, ver, prev_ver, prev_bump[:9], debt, prev_ver, ver, prev_ver, cur.split()[1])
    p.write_text(s.replace(cur + "\n", txt, 1), encoding="utf-8")


def release_readers(repo, files=RELEASE_ALLOWED):
    """讀版號相關檔的測試支數與上次秒數(從紀錄池;拿不到 ⇒ None)。"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import reuse_index as ri
        idx = ri.build_index([], repo=repo)
        tests = set()
        for f in files:
            tests |= set(idx.reverse.get(f, ()))
        secs = sum(float((idx.latest.get(t) or {}).get("duration_s") or 0) for t in tests)
        return len(tests), secs
    except Exception:  # noqa: BLE001 - 預覽拿不到數字就照實說拿不到
        return None


def preview(repo, cur, ver, items, rel, prof=None):
    prof = prof or sp.AIB
    files = list(RELEASE_ALLOWED) if prof.gate_mode == "aib" else release_files(prof)
    lines = [T("pv_header"), T("pv_version", {"what": "v%s -> v%s" % (cur, ver)}),
             T("pv_packages", {"sha": rel[:9], "n": len(items)})]
    lines += ["  - %s %s" % (h, s) for h, s in items]
    lines.append(T("pv_files", {"what": ", ".join(files)}))
    if prof.gate_mode == "aib":
        tagged = bool(git(repo, "tag", "--points-at", rel, check=False).strip())
        lines.append(T("pv_baseline_tagged" if tagged else "pv_baseline_debt", {"version": cur, "sha": rel[:9]}))
    rr = release_readers(repo, files)
    if rr:
        lines.append(T("pv_gate", {"tests": rr[0], "seconds": "%.0f" % (rr[1] / 60), "total": "%.0f" % (rr[1] / 180)}))
    else:
        lines.append(T("pv_gate_unknown"))
    lines.append(T("pv_red_policy" if prof.gate_mode == "aib" else "pv_red_policy_generic"))
    lines.append(T("pv_after"))
    return "\n".join(lines)


# ---- 別的專案的發版(10-06,P1 後半)--------------------------------------------------------------------
def release_files(prof):
    return list(dict.fromkeys(prof.paths(prof.version_files) + ([prof.changelog] if prof.changelog else [])))


def last_release_generic(repo, prof, rev="HEAD"):
    """上一顆發版提交(主版號變了的那顆,只看第一父);沒有 ⇒ 設定檔那顆記的 Ship-Base(沒記 ⇒ 設定檔那顆)。"""
    path = sp.split_spec(prof.version_files[0])[0]
    for sha in git(repo, "log", "--first-parent", "--format=%H", rev, "--", path).split():
        if is_release(repo, sha, prof):
            return sha
    start = policy_start(repo, rev, prof)
    if start is None:
        return None
    m = re.search(r"^%s([0-9a-f]{7,40})$" % re.escape(BASE_TRAILER), git(repo, "log", "-1", "--format=%B", start), re.M)
    return m.group(1) if m else start


def set_value(path, key, text, old, new):
    """把檔裡的版號從 old 換成 new;只動那一行,換完讀回來要等於 new,否則丟錯(不寫壞檔)。"""
    if key is None:
        out = text.replace(old, new, 1) if text.strip() == old else None
    else:
        lines = text.splitlines(keepends=True)
        hit = [i for i, ln in enumerate(lines) if old in ln and re.search(
            r'(^|["\s])%s["\']?\s*[:=]' % re.escape(key.rsplit(".", 1)[-1]), ln)]
        out = None
        for i in hit:
            cand = lines[:]
            cand[i] = cand[i].replace(old, new, 1)
            if sp.read_value(path, key, "".join(cand)) == new:
                out = "".join(cand)
                break
    if out is None or sp.read_value(path, key, out) != new:
        raise SystemExit(T("version_write_failed", {"path": path, "version": old}))
    return out


def add_changelog(text, ver, date, items):
    sec = "## [%s] - %s\n\n%s\n" % (ver, date, "".join("- %s(%s)\n" % (x, h) for h, x in items))
    i = text.find("\n## ")
    if i >= 0:
        return text[:i + 1] + sec + text[i + 1:]
    return text.rstrip("\n") + "\n\n" + sec


def write_release_generic(repo, prof, ver, date, items):
    repo = Path(repo)
    cur = None
    for spec in prof.version_files:
        path, key = sp.split_spec(spec)
        f = repo / path
        text = f.read_text(encoding="utf-8")
        old = sp.read_value(path, key, text)
        if old is None:
            raise SystemExit(T("version_write_failed", {"path": spec, "version": "?"}))
        cur = cur or old
        f.write_text(set_value(path, key, text, old, ver), encoding="utf-8")
    if prof.changelog and (repo / prof.changelog).exists():
        c = repo / prof.changelog
        c.write_text(add_changelog(c.read_text(encoding="utf-8"), ver, date, items), encoding="utf-8")
    return cur


def confirmed_by_human(ver, stdin=None, stdout=None):
    """要真人在終端機打出版號。沒有終端機(腳本、背景 AI、管線)⇒ 不發。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not (stdin.isatty() and stdout.isatty()):
        print(T("no_tty"))
        return False
    stdout.write(T("prompt", {"version": ver}))
    stdout.flush()
    ans = stdin.readline().strip()
    if ans != ver:
        print(T("cancel", {"version": ver}))
        return False
    return True


# ---- 指令 ----------------------------------------------------------------------------------------------
def _check_base(repo):
    """交件檢查的底 = 推包時的底:最新的 GitHub main。推包那棵 clone 的 origin 就是 GitHub;各席的工作樹在 Mini 上
    origin 是 Pro 鏡像、github 才是 GitHub ⇒ 有 github 遠端用它,否則 origin。先 fetch 再用(推包也是推前才抓最新)。
    回 (ref, 錯誤字串);抓不到 ⇒ (None, 原因)。"""
    remotes = git(repo, "remote", check=False).split()
    rem = "github" if "github" in remotes else ("origin" if "origin" in remotes else None)
    if rem is None:
        return None, "no remote"
    p = subprocess.run(["git", "-C", str(repo), "fetch", "-q", rem, "+refs/heads/main:refs/remotes/%s/main" % rem],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return None, (p.stderr or "fetch rc=%d" % p.returncode).strip()[-200:]
    return "%s/main" % rem, ""


def _gate_counts(repo, since, run_id=""):
    """推前檢查自己記的這一輪統計(pushgate.record_stats,分到別台跑的也算在裡面)⇒ (實跑, 沿用);
    取 since 之後最新那一筆,沒有 ⇒ (None, None)(不從畫面文字猜:成功時畫面上沒有逐批那一行,第二輪審查實測)。"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import pushgate
        rows = pushgate.read_stats(repo, since=since)
    except Exception:  # noqa: BLE001
        return None, None
    if run_id:                                       # 同一個倉的別棵樹同時直接跑 pushgate 也會寫一筆;只認自己這輪
        rows = [r for r in rows if r.get("run_id") == run_id]
    if not rows:
        return None, None
    return rows[-1].get("ran"), rows[-1].get("reused")


def cmd_check_gate(a):
    """P13(docs/DESIGN-ship-merge-queue-1011.md §4b;the maintainer 10-11 核准):交件前跑一次**跟推包一樣**的推前檢查,不推。
    一樣的意思(第二輪審查逐條對齊 cmd_package):
      底   = 最新 GitHub main(先 fetch);有還沒補驗的緊急推 ⇒ 選測試從第一次緊急推之前算起(emergency_base)
      判準 = may_push:紅都在主線上也紅 ⇒ 算過(推包也會放行),跟推包同一條
      租約 = 呼叫自己的殼已拿著 ⇒ 沿用,不重拿也不放;否則自己拿、跑完放
    ★ 比推包嚴一處(安全方向,第二輪審查):推包有 P1 的「不穩定測試窄放行」,這裡沒有 ——
      交件前遇到時紅時綠就先修或重跑,不在交件時就拿放行。
    分支沒疊在底上 ⇒ rc 2,不跑。最後印一行給交件信原樣帶。結束碼:0 推得過 / 推前檢查的 rc。"""
    if a.base:
        base, why = a.base, ""
    else:
        base, why = _check_base(a.repo)
    if not base:
        print(T("check_gate_no_base", {"what": why}))
        return 2
    if subprocess.run(["git", "-C", str(a.repo), "merge-base", "--is-ancestor", base, "HEAD"], capture_output=True,
                      stdin=subprocess.DEVNULL).returncode != 0:
        print(T("check_gate_not_rebased", {"what": base}))
        return 2
    pending = pending_emergencies(a.repo, rev=base)
    gate_base = emergency_base(a.repo, pending[0]) if pending else base
    HL = _host_lease()
    lease_file, ledger = HL.lease_path(), HL.ledger_path()
    cur, _why = HL.read_lease(lease_file)
    mine = bool(cur) and cur.get("host") == HL.socket.gethostname() and \
        int(cur.get("pid") or 0) in _ancestors(os.getpid())
    run_id = None
    if not mine:
        run_id = "check-%d-%d" % (os.getpid(), int(time.time()))
        ns = argparse.Namespace(owner=os.environ.get("AIB_SHIP_OWNER") or "ship.py", what="ship.py check --gate",
                                run_id=run_id, ttl=3 * 3600, pid=os.getpid(), cwd=os.path.abspath(a.repo),
                                exclusive=True, priority=True)   # 10-13:推包在量耗時 ⇒ 獨佔;排隊名單有人也照拿(推包優先)
        if acquire_after_test_batches(HL, ns, lease_file, ledger) != HL.RC_OK:
            raise SystemExit(T("lease_denied"))
    since = time.strftime("%Y-%m-%dT%H:%M:%S")
    gate_id = "check-%d-%d" % (os.getpid(), int(time.time()))
    old_id = os.environ.get("AIB_GATE_RUN_ID")
    os.environ["AIB_GATE_RUN_ID"] = gate_id            # pushgate 記統計時帶上,_gate_counts 只認這一筆
    try:
        rc, out = run_gate(a.repo, prof=a.prof, gate_base=gate_base)
        reds = gate_reds(out)
        unv = gate_unverified(out)
        on_base = base_reds(a.repo, base, reds, a.prof) if rc == 1 else set()
    finally:
        if old_id is None:
            os.environ.pop("AIB_GATE_RUN_ID", None)
        else:
            os.environ["AIB_GATE_RUN_ID"] = old_id
        if run_id:
            HL.cmd_release(argparse.Namespace(run_id=run_id), time.time(), lease_file, ledger)
    ok = may_push(rc, reds, on_base, unv, machine_unverified_ok=(a.prof.gate_mode == "aib"))
    ran, reused = _gate_counts(a.repo, since, run_id=gate_id)
    out_rc = 0 if ok else (rc or 1)
    print(T("check_gate_line", {"rc": out_rc, "n": "?" if ran is None else ran, "count": "?" if reused is None else reused,
                                "what": base, "sha": git(a.repo, "rev-parse", "--short=9", "HEAD").strip()}))
    if reds - on_base:
        print(T("check_gate_reds", {"what": sorted(reds - on_base)}))
    if on_base:
        print(T("check_gate_old_reds", {"what": sorted(on_base)}))
    return out_rc


def cmd_check(a):
    if getattr(a, "gate", False):
        return cmd_check_gate(a)
    since = a.since or policy_start(a.repo, a.rev, a.prof)
    if since is None:
        print(T("no_start", {"path": a.prof.policy_start_file}))
        return 2
    bad = check_range(a.repo, since, a.rev, a.prof)
    for sha, why in bad:
        print("%s %s" % (sha[:9], why))
    print(T("check_summary", {"n": len(first_parent(a.repo, since, a.rev)), "total": len(bad)}))
    return 1 if bad else 0


BASE_TRAILER = "Ship-Base: "
NO_TEST_TRAILER = "No-Test-Reason:"
CODE_EXT = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb",
            ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".m", ".scala", ".lua", ".pl")


def is_test_path(path):
    parts = path.split("/")
    name = parts[-1]
    return (any(d in ("tests", "test", "__tests__", "spec") for d in parts[:-1]) or name.startswith(("test_", "test-"))
            or name.endswith(("_test.py", "_test.go", ".test.js", ".test.ts", ".spec.js", ".spec.ts", "_spec.rb")))


def is_code_path(repo, path, rev="HEAD"):
    """程式檔:常見程式副檔名,或沒有副檔名但開頭是 #!(像 src/cockpit/aib)。文件、設定、資料都不算。"""
    if is_test_path(path):
        return False
    if path.endswith(CODE_EXT):
        return True
    if "." in path.split("/")[-1]:
        return False
    head = show(repo, rev, path)
    return bool(head) and head.startswith("#!")


def missing_test_reason(repo, shas):
    """§6 第 8 條(10-06):這一包改了程式卻沒有改或新增任何測試,也沒有一顆提交寫 No-Test-Reason: ⇒ 回那些程式檔。
    只要求說出來(純重構、只改文字之類),不擋正當的改動。"""
    files = sorted({f for sha in shas for f in changed_files(repo, sha)})
    if any(is_test_path(f) for f in files):
        return []
    code = [f for f in files if is_code_path(repo, f)]
    if not code:
        return []
    if any(re.search(r"^%s[ \t]*\S" % re.escape(NO_TEST_TRAILER), git(repo, "log", "-1", "--format=%B", sha), re.M) for sha in shas):
        return []
    return code


def ensure_config(a, base=None):
    """別的專案第一次推:把偵測結果寫成 .aib/ship.toml,自己一顆提交加進這一包(規則從這一顆起算)。
    提交訊息記下推這一包之前的主線(Ship-Base),第一次發版就從那裡算,這一包前面幾顆也列進發版內容。"""
    if a.prof.source != "detect":
        return
    # 10-08:第一次推要提交一顆設定檔;這台的 git 還沒設名字/信箱 ⇒ 以前在提交那一步噴一整串 Python 錯誤。先檢查,什麼都不寫
    if subprocess.run(["git", "-C", str(a.repo), "var", "GIT_COMMITTER_IDENT"], capture_output=True, text=True,
                      stdin=subprocess.DEVNULL).returncode != 0:
        raise SystemExit(T("no_git_identity"))
    print(T("detected", {"what": getattr(a.prof, "detected", "?"), "files": a.prof.version_files or "-",
                         "path": a.prof.release_only or "-"}))
    cfg = Path(a.repo) / sp.CONFIG
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(sp.render(a.prof), encoding="utf-8")
    git(a.repo, "add", sp.CONFIG)
    msg = "chore(aib): add %s (detected by aib ship)" % sp.CONFIG + ("\n\n%s%s" % (BASE_TRAILER, base) if base else "")
    git(a.repo, "commit", "-q", "-m", msg, "--", sp.CONFIG)
    a.prof = sp.load(a.repo)
    print(T("config_added", {"path": sp.CONFIG}))


# ---- 緊急推(10-06,開發檔 §5.4;the maintainer 預設「要做,限真人」)----------------------------------------------
EMERGENCY_TRAILER = "Ship-Emergency: "
VERIFIED_TRAILER = "Ship-Verified-Emergency: "


def emergency_reason(stdin=None, stdout=None):
    """真人在終端機打理由(不能空白)。沒有終端機(腳本、背景 AI、管線)⇒ None。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not (stdout.isatty() and stdin.isatty()):
        print(T("emergency_no_tty"))
        return None
    stdout.write(T("emergency_prompt"))
    stdout.flush()
    reason = " ".join(stdin.readline().split())
    if not reason:
        print(T("emergency_no_reason"))
        return None
    return reason


def pending_emergencies(repo, rev="origin/main"):
    """主線上緊急推過、還沒有補驗的提交(由舊到新)。"""
    shas = git(repo, "log", "--first-parent", "--reverse", "--format=%H", "--grep", "^" + EMERGENCY_TRAILER.strip(),
               rev).split()
    if not shas:
        return []
    verified = set()
    for body in git(repo, "log", "--first-parent", "--format=%B%x00", "--grep", "^" + VERIFIED_TRAILER.strip(), rev).split("\0"):
        for m in re.finditer(r"^%s([0-9a-f ]+)$" % re.escape(VERIFIED_TRAILER), body, re.M):
            verified |= set(m.group(1).split())
    return [s for s in shas if s not in verified and s[:12] not in {v[:12] for v in verified}]


def emergency_base(repo, sha):
    """緊急推標記那顆記下的「推之前的主線」;沒記(不該發生)⇒ 標記那顆的上一顆往前到第一父的根都算(寬,不漏)。"""
    m = re.search(r"^%s([0-9a-f]{7,40})$" % re.escape(BASE_TRAILER), git(repo, "log", "-1", "--format=%B", sha), re.M)
    if m:
        return m.group(1)
    roots = git(repo, "rev-list", "--max-parents=0", "--first-parent", sha).split()
    return roots[-1] if roots else sha + "^"


def tree_busy(repo):
    """同一個工作區還有別的 aib test 在跑?(它們的樹守衛會把這時候被改的檔還原 —— 10-08 實踩兩次)"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import test_entry
        return bool(test_entry._other_batches(repo))
    except Exception:  # noqa: BLE001 - 判不出 ⇒ 當沒在跑(不擋)
        return False


def cmd_sync(a):
    """把主線合進這個工作區(10-08:合併會改檔,同一個工作區有測試在跑就不准 —— 樹守衛會還原、測到一半的結果也不算數)。"""
    if tree_busy(a.repo):
        raise SystemExit(T("tree_busy_sync"))
    git(a.repo, "fetch", "-q", "origin", "main")
    r = subprocess.run(["git", "-C", a.repo, "merge", "--no-edit", "origin/main"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(T("sync_conflict", {"what": (r.stdout + r.stderr).strip()[-400:]}))
    print(T("sync_done"))
    return 0


TEST_BATCH_POLL_S = 30
TEST_BATCH_LONG_S = 45 * 60      # 推包等滿這麼久 ⇒ 印出誰拿著、照舊拒推(a seat 10-12 16:17:不無限等)


def acquire_after_test_batches(HL, ns, lease_file, ledger, sleep=time.sleep, say=print):
    """拿機器租約;被某一席的 aib test 大批拿著(run_id aib-test-…,10-12 起大批會拿租約排隊)⇒ 等它跑完再拿,
    每 30 秒看一次、印一行。被別的(另一個推包、發版…)拿著 ⇒ 照舊回被拒,由呼叫端停下。
    先看再拿:等的時候不去 acquire,免得每 30 秒在租約紀錄寫一筆被拒。
    推包優先:開始等就在租約旁寫「推包在等」記號(拿到或離開時清掉),新的大批 aib test 看到就排在推包後面;
    等滿 45 分鐘 ⇒ 印出誰拿著(席、pid、開始時間)並照舊回被拒,不無限等(a seat 16:17)。"""
    def test_batch(cur):
        return bool(cur) and str(cur.get("run_id") or "").startswith("aib-test-") and not HL.is_expired(cur, time.time())

    waited, marked = 0, False
    try:
        while True:
            cur, _why = HL.read_lease(lease_file)
            if not test_batch(cur):
                rc = HL.cmd_acquire(ns, time.time(), lease_file, ledger)
                if rc == HL.RC_OK and waited:
                    say(T("lease_test_batch_done", {"waited": waited}))
                cur, _why = HL.read_lease(lease_file)
                if rc == HL.RC_OK or not test_batch(cur):
                    return rc
            if not marked:          # 推包優先:新的大批看到這個記號就排在推包後面(正在跑的那批照跑完)
                HL.ship_wait_mark(lease_file, os.getpid(), ns.what)
                marked = True
            if waited >= TEST_BATCH_LONG_S:    # 上限(a seat 16:17):印出誰拿著,照舊拒推,不無限等
                say(T("lease_test_batch_long", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"),
                                                "waited": waited, "pid": cur.get("pid", "?"),
                                                "since": cur.get("acquired_at", "?")}))
                return HL.RC_HELD
            say(T("lease_test_batch_wait", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"), "waited": waited,
                                            "n": TEST_BATCH_POLL_S}))
            sleep(TEST_BATCH_POLL_S)
            waited += TEST_BATCH_POLL_S
    finally:
        if marked:
            HL.ship_wait_clear(lease_file, os.getpid())


def _host_lease():
    sys.path.insert(0, str(ROOT / "tools" / "governance"))
    import host_lease
    return host_lease


def other_ships(repo):
    """同一棵樹上「別的」推包:排除自己與自己的祖先(10-09 D11 第四輪真推抓到:推包跑推前檢查時,
    測試在同一個 clone 裡叫 ship package,祖先正是這次的推包 ⇒ 被當成「別人在推」擋下,test-ship-blockers-first B4 紅)。"""
    mine = set(_ancestors(os.getpid()))
    return [o for o in _host_lease().live_ships(repo) if int(o.get("pid") or 0) not in mine]


def _ancestors(pid):
    """pid 自己與它所有的祖先(往上找 ppid 到 1 為止)。"""
    seen = []
    p = int(pid)
    while p > 1 and p not in seen and len(seen) < 64:
        seen.append(p)
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(p)], capture_output=True, text=True, timeout=5,
                                 stdin=subprocess.DEVNULL).stdout.strip()
            p = int(out) if out else 1
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return seen


def cmd_package(a):
    """10-09 a seat D11(a seat 提案 A/B):真推(不是 --preview)時,工具自己守三件事,不靠各席記規矩 ——
    B) 同一棵樹已有推包在跑 ⇒ 不起(點名 pid/分支);
    A) 自己拿機器租約、綁自己的 pid(外層殼死了租約不跟著死),結束一定放;
       租約已被「自己或呼叫自己的殼」拿著 ⇒ 沿用,不重拿也不放(相容先拿鎖再起 ship 的排隊腳本);
    登記 <git dir>/aib-ship-running/<pid>,讓 host_lease 在持有者死了時看得到「這棵樹還有推包在跑」而不准接管(提案 C)。"""
    if getattr(a, "preview", False):
        return _cmd_package_body(a)
    # 10-09 D11(a seat 提的第 1 種撞法):有人在共用 clone 的 tests/ 暫放沒追蹤的檔,推前檢查照樣撿去跑、打到別人的閘。
    #   原本的「工作區要乾淨」用 --untracked-files=no,看不到它們。只看 tests/:那是推前檢查會掃的地方。
    stray = git(a.repo, "status", "--porcelain", "--untracked-files=all", "--", "tests").split("\n")
    stray = [l[3:] for l in stray if l.startswith("?? ")]
    if stray:
        raise SystemExit(T("untracked_tests", {"what": ", ".join(stray[:5]) + (" …" if len(stray) > 5 else "")}))
    HL = _host_lease()
    others = other_ships(a.repo)
    if others:
        o = others[0]
        raise SystemExit(T("another_ship_running", {"pid": o.get("pid"), "what": o.get("branch") or "?",
                                                    "at": o.get("at") or "?"}))
    if getattr(getattr(a, "prof", None), "gate_mode", None) == "aib" and not getattr(a, "emergency", False):
        github_lock_wait_free(a.repo)       # 10-12:先等 GitHub 推包鎖空(不佔機器),等到了才拿機器租約、才算底;
        #                                     緊急推是人在終端機上要馬上推,原本就不看鎖,不在這裡等(審查)
    lease_file, ledger = HL.lease_path(), HL.ledger_path()
    cur, _why = HL.read_lease(lease_file)
    mine = bool(cur) and cur.get("host") == HL.socket.gethostname() and \
        int(cur.get("pid") or 0) in _ancestors(os.getpid())
    run_id = None
    if not mine:
        run_id = "ship-%d-%d" % (os.getpid(), int(time.time()))
        ns = argparse.Namespace(owner=os.environ.get("AIB_SHIP_OWNER") or "ship.py",
                                what="ship.py package %s" % " ".join(a.branch), run_id=run_id, ttl=3 * 3600,
                                pid=os.getpid(), cwd=os.path.abspath(a.repo),
                                exclusive=True, priority=True)   # 10-13:推包在量耗時 ⇒ 獨佔;排隊名單有人也照拿(推包優先)
        if acquire_after_test_batches(HL, ns, lease_file, ledger) != HL.RC_OK:
            raise SystemExit(T("lease_denied"))
    HL.ship_register(a.repo, " ".join(a.branch))
    try:
        return _cmd_package_body(a)
    finally:
        HL.ship_unregister(a.repo)
        if run_id:
            HL.cmd_release(argparse.Namespace(run_id=run_id), time.time(), lease_file, ledger)


# 10-11 the maintainer 02:2x(經守望官 a seat 發派):推前檢查一輪 50 分鐘,雲端席 30–60 分推一次 main ⇒ Mac 這邊
#   10-10 晚上三輪全綠卻三輪都輸給「檢查期間主線動了」。解法:檢查期間在 GitHub 上一把鎖,雲端推 main 前
#   ls-remote 看到就等。鎖 = 一顆不碰工作樹的空提交(commit-tree,父=候選 HEAD),訊息寫誰、哪包、起訖時刻。
#   只在 aib 自己的倉做(ship 也給別的專案用,不能在別人的 GitHub 倉放鎖);上鎖失敗只警告、不擋推包。
LOCK_REF = "refs/heads/lock/mac-ship"
LOCK_TTL_S = 3 * 3600


def _git_raw(repo, *args, timeout=60):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


# 10-14:鎖那顆提交自帶作者 —— 沒設過 git 名字的機器(新電腦、測試用的暫存家目錄)commit-tree 會失敗 ⇒ 鎖上不成
#   (aib test 底下 test-ship-github-lock-* 四支紅的原因)。鎖不是誰的作品,不需要用人的名字。
LOCK_IDENT = ("-c", "user.name=aib ship", "-c", "user.email=aib-ship@localhost")
LOCK_POLL_S = 60
LOCK_PEEK_REF = "refs/aib/lock-peek"


def github_lock_holder(repo):
    """遠端現在那把鎖:None = 沒有鎖;否則 {"sha", "host", "owner", "branch", "expires"(int 或 None), "parent", "landed"}。
    landed = 上鎖當時的那顆候選(鎖提交的父)已經在 origin/main 裡 ⇒ 那次推包早推完了,鎖只是沒撤掉(雲端刪不掉分支,
    鎖會留到 3 小時後;docs/handoff/cloud-1002/HANDOFF-cloud-self-1010.md §七 A)。讀不到內容 ⇒ host/expires 為空。"""
    p = _git_raw(repo, "ls-remote", "origin", LOCK_REF)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:160] or "ls-remote rc=%d" % p.returncode)
    sha = (p.stdout.split() or [""])[0]
    if not sha:
        return None
    info = {"sha": sha, "host": "", "owner": "", "branch": "", "expires": None, "parent": "", "landed": False}
    if _git_raw(repo, "fetch", "-q", "origin", "+%s:%s" % (LOCK_REF, LOCK_PEEK_REF)).returncode != 0:
        return info
    body = _git_raw(repo, "log", "-1", "--format=%P%n%B", LOCK_PEEK_REF).stdout
    lines = body.splitlines()
    info["parent"] = (lines[0].split() or [""])[0] if lines else ""
    for ln in lines[1:]:
        k, sep, v = ln.partition(":")
        if sep and k.strip() in ("host", "owner", "branch"):
            info[k.strip()] = v.strip()
        elif sep and k.strip() == "expires":
            try:
                info["expires"] = int(v.strip())
            except ValueError:
                pass
    if info["parent"] and _git_raw(repo, "fetch", "-q", "origin", "main").returncode == 0:
        info["landed"] = _git_raw(repo, "merge-base", "--is-ancestor", info["parent"], "origin/main").returncode == 0
    return info


def _lock_blocks(info, now, first_seen):
    """這把鎖要不要等:別台的、沒過期、那次推包還沒落地 ⇒ 等。讀不出到期時間 ⇒ 從第一次看到起最多等 LOCK_TTL_S。"""
    if info is None or info["landed"] or info["host"] == socket.gethostname():
        return False
    return now < _lock_expiry(info, first_seen)


def _lock_expiry(info, first_seen):
    """寫的到期時間不全信:最多算到第一次看到後 LOCK_TTL_S(時鐘不準或寫錯成很遠的未來,不能等無限久;審查)。"""
    cap = first_seen + LOCK_TTL_S
    return min(info["expires"], cap) if info["expires"] is not None else cap


LOCK_RACE_RETRIES = 5
_LOCK_FIRST_SEEN = {}                # 鎖 sha → 第一次看到的時刻;wait_free 與 take 共用,同一把鎖只有一個 3 小時上限(審查)


def _first_seen(info, t_now):
    if info is None:
        return t_now
    return _LOCK_FIRST_SEEN.setdefault(info["sha"], t_now)
LOCK_WAITED = {"waited": False}      # 這一次上鎖有沒有等過別台(with_github_lock 用來判斷要不要重看主線)


def github_lock_wait_free(repo, sleep=time.sleep, clock=time.time):
    """只看不上鎖:別台的鎖還有效(_lock_blocks)就等,每 LOCK_POLL_S 秒看一次。回有沒有等過。
    10-12(a seat 排,審查提的):github_lock_take 是在拿了機器租約、算好底之後才跑的 ⇒ 等鎖時白佔機器最多 3 小時、
      等完主線多半已動。cmd_package 先在這裡等,等到了才拿租約、才 fetch 算底;之後 take 正常會馬上拿到
      (看完到上鎖之間又被搶先 ⇒ take 照舊會等,with_github_lock 也照舊會在主線動了時停)。讀不到遠端 ⇒ 不等(交給 take)。"""
    waited = False
    while True:
        try:
            info = github_lock_holder(repo)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            return waited
        t_now = int(clock())
        first_seen = _first_seen(info, t_now)
        if not _lock_blocks(info, t_now, first_seen):
            return waited
        waited = True
        print(T("lock_wait", {"what": "%s %s %s" % (info["host"] or "?", info["owner"], info["branch"]),
                              "at": time.strftime("%m-%d %H:%M", time.localtime(_lock_expiry(info, first_seen))),
                              "n": LOCK_POLL_S}))
        sleep(LOCK_POLL_S)


def github_lock_take(repo, branches, now=None, sleep=time.sleep, clock=time.time):
    """上鎖;回鎖那顆提交的 sha,失敗回 ""(印一行警告,推包照走)。
    10-12(a seat 派,10-11 23:53 Mac 蓋掉雲端的鎖、兩邊各推各的):不再直接 -f 蓋 ——
      先看遠端那把:別台的、沒過期、那次推包還沒落地 ⇒ 等(每 LOCK_POLL_S 秒看一次,印誰在推、到期時間);
      過期 / 自己這台留的 / 那次推包已落地 ⇒ 蓋。蓋的時候用 --force-with-lease=<剛看到的那顆>,
      看完到推之間別台搶先上鎖 ⇒ 推不上去 ⇒ 回頭再看一次(不會蓋掉剛上的鎖)。"""
    races = 0
    LOCK_WAITED["waited"] = False
    while True:
        try:
            info = github_lock_holder(repo)
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            print(T("lock_take_failed", {"why": str(e) or type(e).__name__}))
            return ""
        t_now = int(clock())
        first_seen = _first_seen(info, t_now)
        if _lock_blocks(info, t_now, first_seen):
            LOCK_WAITED["waited"] = True
            exp = _lock_expiry(info, first_seen)
            print(T("lock_wait", {"what": "%s %s %s" % (info["host"] or "?", info["owner"], info["branch"]),
                                  "at": time.strftime("%m-%d %H:%M", time.localtime(exp)), "n": LOCK_POLL_S}))
            sleep(LOCK_POLL_S)
            continue
        seen = info["sha"] if info else ""
        sha = _github_lock_push(repo, branches, now, seen)
        if sha is None:                               # 看完到推之間鎖變了 ⇒ 再看一次(有上限,不無限重推)
            races += 1
            if races >= LOCK_RACE_RETRIES:
                print(T("lock_take_failed", {"why": "lock changed %d times while taking it" % races}))
                return ""
            continue
        return sha


def _github_lock_push(repo, branches, now, seen):
    """推一把新鎖;只在遠端還是剛才看到的那顆(seen,空字串 = 沒有鎖)時才蓋。回 sha / ""(失敗,推包照走)/ None(被搶先,要重看)。"""
    now = int(now or time.time())
    try:
        tree = _git_raw(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
        head = _git_raw(repo, "rev-parse", "HEAD").stdout.strip()
        msg = ("mac-ship lock\n\nowner: %s\nhost: %s\nbranch: %s\nstarted: %d\nexpires: %d\n"
               % (os.environ.get("AIB_SHIP_OWNER") or "ship.py", socket.gethostname(),
                  " ".join(branches), now, now + LOCK_TTL_S))
        p = _git_raw(repo, *LOCK_IDENT, "commit-tree", tree, "-p", head, "-m", msg)
        sha = p.stdout.strip()
        if p.returncode != 0 or not sha:
            raise RuntimeError(p.stderr.strip()[:160])
        p = _git_raw(repo, "push", "-q", "--force-with-lease=%s:%s" % (LOCK_REF, seen), "origin", "%s:%s" % (sha, LOCK_REF))
        if p.returncode != 0:
            if "stale info" in p.stderr:              # 只有「遠端那把不是剛才看到的」才算被搶先;
                return None                           # 權限/掛勾拒絕([remote rejected])照失敗處理,不重推
            raise RuntimeError(p.stderr.strip()[:160])
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        print(T("lock_take_failed", {"why": str(e) or type(e).__name__}))
        return ""
    print(T("lock_taken", {"what": LOCK_REF, "n": str(LOCK_TTL_S // 3600)}))
    return sha


def github_lock_drop(repo, sha):
    """撤鎖:只撤自己那一把(遠端那把已經不是自己的 sha ⇒ 不動,印一行)。"""
    if not sha:
        return False
    try:
        p = _git_raw(repo, "ls-remote", "origin", LOCK_REF)
        remote = (p.stdout.split() or [""])[0]
        if remote != sha:
            print(T("lock_not_mine", {"what": LOCK_REF}))
            return False
        p = _git_raw(repo, "push", "-q", "origin", ":%s" % LOCK_REF)
        if p.returncode != 0:
            if _github_lock_release(repo, sha):
                return True
            raise RuntimeError(p.stderr.strip()[:160])
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        print(T("lock_drop_failed", {"why": str(e) or type(e).__name__, "n": str(LOCK_TTL_S // 3600)}))
        return False
    print(T("lock_dropped", {"what": LOCK_REF}))
    return True


def _github_lock_release(repo, sha, now=None):
    """刪不掉(雲端代理擋刪分支,HTTP 403)⇒ 把自己那把改寫成「已到期」:別台的 _lock_blocks 看到到期就不等。
    10-12(a seat:雲端 18:02 / 19:22 / 21:57 推完都留鎖,Mini 白等):上鎖那種推送雲端推得上去,刪除推不上去。
    只在遠端還是自己那把時才蓋(--force-with-lease=<自己的 sha>)。"""
    now = int(now or time.time())
    tree = _git_raw(repo, "rev-parse", "%s^{tree}" % sha).stdout.strip()
    msg = ("mac-ship lock\n\nowner: %s\nhost: %s\nbranch: released\nstarted: %d\nexpires: %d\n"
           "released: %s\n" % (os.environ.get("AIB_SHIP_OWNER") or "ship.py", socket.gethostname(), now, now, sha))
    p = _git_raw(repo, *LOCK_IDENT, "commit-tree", tree, "-p", sha, "-m", msg) if tree else None
    rel = p.stdout.strip() if p is not None and p.returncode == 0 else ""
    if not rel:
        return False
    p = _git_raw(repo, "push", "-q", "--force-with-lease=%s:%s" % (LOCK_REF, sha), "origin", "%s:%s" % (rel, LOCK_REF))
    if p.returncode != 0:
        return False
    print(T("lock_released", {"what": LOCK_REF}))
    return True


def with_github_lock(repo, prof, branches, fn, base=None):
    """fn 前上鎖、之後一定撤(推成、推紅、拒推、例外、SystemExit 都撤)。不是 aib 自己的倉 ⇒ 不上鎖直接跑。
    10-12(審查):上鎖時等過別台 ⇒ 對方多半推了 ⇒ 主線動了;給了 base 就重看,動了就撤鎖停下(請疊新主線再推),
      不跑一整輪推前檢查之後才在 push() 被 remote_moved 擋下。"""
    if prof.gate_mode != "aib":
        return fn()
    sha = github_lock_take(repo, branches)
    try:
        if base and LOCK_WAITED["waited"] and _git_raw(repo, "fetch", "-q", "origin", "main").returncode == 0:
            now_main = _git_raw(repo, "rev-parse", "origin/main").stdout.strip()
            if now_main and now_main != base:
                raise SystemExit(T("lock_waited_main_moved", {"what": "%s != %s" % (now_main[:8], base[:8])}))
        return fn()
    finally:
        github_lock_drop(repo, sha)


def _cmd_package_body(a):
    if tree_busy(a.repo):                       # 10-08:推前檢查會在這棵樹跑測試、合併會改檔 ⇒ 別的批次在跑就不開始
        raise SystemExit(T("tree_busy_package"))
    git(a.repo, "fetch", "-q", "origin", "main")
    base = git(a.repo, "rev-parse", "origin/main").strip()
    if subprocess.run(["git", "-C", a.repo, "merge-base", "--is-ancestor", base, "HEAD"],
                      stdin=subprocess.DEVNULL).returncode != 0:
        raise SystemExit(T("not_contain_main"))
    if not git(a.repo, "rev-list", "--reverse", "%s..HEAD" % base).split():
        raise SystemExit(T("nothing_to_push"))
    ensure_config(a, base)
    shas = git(a.repo, "rev-list", "--reverse", "%s..HEAD" % base).split()
    for sha in shas:
        if is_release(a.repo, sha, a.prof):
            raise SystemExit(T("release_in_package", {"sha": sha[:9], "what": a.prof.version_files[0]}))
        bad = check_commit(a.repo, sha, a.prof)
        if bad:
            raise SystemExit(T("package_violation", {"sha": sha[:9], "reason": bad}))
    code = missing_test_reason(a.repo, shas)
    if code:
        raise SystemExit(T("no_test_reason", {"what": code[:8], "line": NO_TEST_TRAILER}))
    print_tracer_split(a.repo, shas)            # 10-08 §27.5:碰到追蹤器 ⇒ 整套重跑;先說哪幾顆碰、哪幾顆不碰
    if not getattr(a, "preview", False):         # 10-08 /tmp 方案第 3 層:推前先清 aib 自己留在暫存區、超過 24 小時沒人用的東西
        try:
            sys.path.insert(0, str(ROOT / "tools" / "verify"))
            import tmp_sweep
            found = tmp_sweep.orphans()            # 已經結束的測試留下、還在跑的背景程式(10-08 實踩:吃掉 8 GB 害推前檢查被砍)
            if found:
                tmp_sweep.kill_orphans(found)
                print(tmp_sweep.T("orphans_killed", {"n": len(found)}))
            r = tmp_sweep.sweep(24.0, apply=True)
            if r["checked"] and r["removed"]:
                print(tmp_sweep.T("removed", {"n": len(r["removed"]), "bytes": tmp_sweep.human(r["bytes"]), "hours": "24"}))
        except Exception:  # noqa: BLE001 - 清理失敗不擋推包
            pass
        warn_low_disk()                         # 10-08 /tmp 方案:磁碟快滿就先講,免得跑到一半整批莫名失敗
    if getattr(a, "preview", False):            # 不跑:只印預告、主因、熱門檔提醒
        gate = Path(a.repo) / "tools" / "verify" / "pushgate.py" if a.prof.gate_mode == "aib" else ROOT / "tools" / "verify" / "pushgate.py"
        cmd = [sys.executable, str(gate), "--repo", a.repo, "--dry-run"] + ([] if a.prof.gate_mode == "aib" else ["--mode", "generic"])
        p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        sys.stdout.write(p.stdout)
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        print_scenarios(a.repo)                 # 10-06 the maintainer:什麼情況下推會省最多、要做什麼
        return 0
    branches = a.branch + list(getattr(a.prof, "also_branches", []))
    if getattr(a, "emergency", False):
        reason = emergency_reason()
        if reason is None:
            return 3
        git(a.repo, "commit", "-q", "--allow-empty", "-m",
            "ship: emergency push, pre-push check skipped\n\n%s%s\n%s%s" % (EMERGENCY_TRAILER, reason, BASE_TRAILER, base))
        push(a.repo, branches, base)
        print(T("emergency_done", {"n": len(shas)}))
        return 0
    pending = pending_emergencies(a.repo)
    gate_base = None
    if pending:
        print(T("emergency_pending", {"what": [p[:9] for p in pending]}))
        gate_base = emergency_base(a.repo, pending[0])     # 選測試從第一次緊急推之前的主線算起,緊急推的改動一併驗

        def mark():
            git(a.repo, "commit", "-q", "--allow-empty", "-m",
                "ship: emergency pushes verified by this package's pre-push check\n\n%s%s" % (VERIFIED_TRAILER, " ".join(pending)))
    got = with_github_lock(a.repo, a.prof, branches, base=base, fn=lambda: gate_then_push(
        a.repo, branches, base, a.prof, gate_base=gate_base, before_push=mark if pending else None,
        fail_fast=getattr(a, "fail_fast", False))) or {}
    print(T("package_done", {"n": len(shas)}))
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    print_ledger_brief(a.repo)                  # 10-06 the maintainer:總計 → 這一次 → 從第一次到現在
    print_scenarios(a.repo)                     # 10-06 the maintainer:推完告訴客戶什麼情況下推會省最多、要做什麼
    print_stale_hint(a.repo)                    # 10-07:很久沒跑的測試提醒一行(紅藏在沒被挑到裡)
    if a.prof.gate_mode == "aib":               # 10-12 P9:推成後把艦長室名冊上的另一台(Pro)快轉、重啟艦長室;不能快轉不動
        got["after_push"] = after_push_lines(a.repo)
    if a.prof.gate_mode == "aib":               # 10-08 the maintainer:回報不能靠記憶 ⇒ 推完自動產生七段回報草稿
        if got.get("pushed_base") and got["pushed_base"] != base:     # 主線動過、疊上去才推 ⇒ 回報要列推上去的編號
            shas = [h for h in git(a.repo, "rev-list", "--reverse", "%s..HEAD" % got["pushed_base"]).split()
                    if VERIFIED_TRAILER not in git(a.repo, "log", "-1", "--format=%B", h)] or shas
        report_draft(a.repo, shas, got)
        announce_savings(a.repo, shas, got)    # 10-13 the maintainer:省多少不靠 AI 記性 ⇒ 推包工具自己送(通知 + 艦長室紀錄 + 待回報記號)
    # 10-14:最後一行 = 結論 + 省多少(轉述只看最後一行也帶得到;the maintainer「為什麼能跳過?」)
    print(T("verdict_pushed", {"n": len(shas), "sha": git(a.repo, "rev-parse", "--short", "HEAD").strip(),
                               "savings": savings_text(a.repo)}))
    return 0


def tracer_split(repo, shas):
    """(碰到 aib 追蹤器的提交, 不碰的提交),各為 [(sha, 標題, [碰到的追蹤器檔])]。不是 aib 自己的倉 ⇒ ([], [])。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    touch, clean = [], []
    for sha in shas:
        files = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, check=False).split()
        hit = pushgate.tracer_hit(repo, files)
        title = git(repo, "log", "-1", "--format=%s", sha, check=False).strip()
        (touch if hit else clean).append((sha, title, hit))
    return (touch, clean) if touch else ([], [])


def print_tracer_split(repo, shas):
    """推之前說清楚:這包有幾顆改到追蹤器(⇒ 所有紀錄作廢、整套重跑),其他幾顆不碰 ——
    不碰的那幾顆如果不依賴前者,可以另成一包先推(只跑受影響的)。只說明,不改推包行為。"""
    try:
        touch, clean = tracer_split(repo, shas)
    except Exception:  # noqa: BLE001 - 說明失敗不影響推包
        return
    if not touch:
        return
    print(T("tracer_split", {"n": len(touch), "count": len(clean)}))
    for sha, title, hit in touch[:5]:
        print("  ✱ %s %s(%s)" % (sha[:9], title[:60], "、".join(os.path.basename(f) for f in hit[:3])))
    if clean:
        print(T("tracer_split_clean"))
        for sha, title, _ in clean[:8]:
            print("    %s %s" % (sha[:9], title[:60]))


def after_push_lines(repo, run=None, config=None):
    """P9:對每台跑 after_push,印一行並回那幾行(寫進推包回報)。出任何錯都不影響「推成了」。"""
    try:
        import after_push
    except ImportError:          # 10-13:公開版不帶這支(艦長室主機名冊、跨主機重啟)⇒ 沒有這一步,不印錯
        return []
    try:
        res = after_push.run_all(repo, run=run, config=config)
    except Exception as e:  # noqa: BLE001
        line = T("after_push_error", {"what": str(e)[-200:]})
        print(line)
        return [line]
    lines = []
    for r in res:
        if r["state"] in ("synced", "same"):
            room = T("after_push_room_" + (r.get("room") or "none")) if r["state"] == "synced" else ""
            line = T("after_push_" + r["state"], {"name": r["name"], "sha": r["sha"][:9], "what": room})
        else:
            why = T("after_push_why_" + r["state"], {"n": r.get("ahead") or "?", "rc": r.get("rc")})
            line = T("after_push_skip", {"name": r["name"], "what": why})
        if r.get("via"):                          # 主位址連不上,走 ssh_fallback 成功
            line += T("after_push_via_fallback", {"host": r["via"]})
        if r.get("fb_off"):                       # 名冊設了 ssh_fallback 但用不了(主位址是別名 / 格式不對)
            line += T("after_push_fallback_off_" + r["fb_off"])
        if r.get("guard") == "3":                 # P8:那台的提交前/合併前槽被別的掛勾佔著,沒裝,要人看
            line += T("after_push_guard_foreign")
        print(line)
        lines.append(line)
    return lines


def report_draft(repo, shas, got):
    """推完一包:用這一輪的統計紀錄產生回報草稿(tools/release/ship_report.py)。失敗只印一行,不影響推包。"""
    try:
        import pushgate
        import ship_report
        rows = pushgate.read_stats(repo)
        if not rows:
            return
        commits = [(sha, git(repo, "log", "-1", "--format=%s", sha).strip()) for sha in shas]
        txt, js, rc = ship_report.write(repo, T, pushgate.dur_text, rows[-1], commits,
                                        got.get("old_reds") or [], got.get("unverified") or [], history=rows,
                                        flaky=got.get("flaky_allowed") or [], notes=got.get("after_push") or [])
        print(T("rp_saved", {"path": str(txt or js), "rc": "0" if txt else str(rc)}))
    except Exception as e:  # noqa: BLE001
        print(T("rp_failed", {"reason": "%s: %s" % (type(e).__name__, e)}))


def update_result(repo, kind, **fields):
    """在 record_result 寫好的 last-<kind>-<標籤>.json 上加欄位(讀不到就當空的)。寫不了不影響推包。"""
    try:
        f = _ship_result_file(repo, kind)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {"at": time.time(), "repo": str(repo), "kind": kind}
        d.update(fields)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, f)
        return f
    except Exception:  # noqa: BLE001
        return None


def _pusher_seat():
    """跑這次推包的是哪一席(TMUX_PANE ⇒ 窗號 ⇒ 席位檔;同 aib-now-gate 的認席)。認不出 ⇒ None。"""
    try:
        sys.path.insert(0, str(ROOT / "src" / "cockpit"))
        import seat_now
        return seat_now.self_code()
    except Exception:  # noqa: BLE001
        return None


def notify_mac(title, text, runner=None):
    """Mac 通知(osascript;文字走 argv,不拼進 AppleScript 原始碼)。沒有 osascript / AIB_SHIP_NOTIFY=0 / 失敗 ⇒ 略過,回 False。"""
    if os.environ.get("AIB_SHIP_NOTIFY") == "0":
        return False
    if runner is None:
        if not shutil.which("osascript"):
            return False
        runner = subprocess.run
    script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run"
    try:
        p = runner(["osascript", "-e", script, title, text], capture_output=True, text=True, timeout=10,
                   stdin=subprocess.DEVNULL)
        return getattr(p, "returncode", 1) == 0
    except Exception:  # noqa: BLE001
        return False


def captain_ship_log():
    """艦長室推包紀錄(一筆一列 JSON):<狀態根>/state/captain-room/ship-log.jsonl。"""
    root = os.environ.get("EZ2_AIB_STATE_ROOT") or str(Path.home() / ".ez2-aib")
    return Path(root) / "state" / "captain-room" / "ship-log.jsonl"


def announce_savings(repo, shas, got, notifier=None):
    """10-13 the maintainer「推版前後沒照影子續跑說明可以省的部份,如何程式化讓任何 AI 都不能跳過」(a seat 派工):
    推成就由推包工具自己把 G2「省多少」四行送出去,不經 AI —— (a) Mac 通知;(b) 艦長室推包紀錄一列;
    (c) last-push 檔加 reported:false + pusher + savings,讓回報守門(src/policy/hooks/ship_report_gate.py)
    要求推的那一席在回覆裡原樣貼出。任何一步失敗只印一行,不影響推包。回 {notified, logged, marked}。"""
    out = {"notified": False, "logged": False, "marked": False}
    try:
        import pushgate
        import ship_report
        rows = pushgate.read_stats(repo)
        if not rows:
            return out
        row = rows[-1]
        lines = ship_report.savings_lines(T, pushgate.dur_text, row)
        head = git(repo, "rev-parse", "HEAD", check=False).strip()
        rec = {"reported": False, "pusher": _pusher_seat(), "savings": lines,
               "numbers": ship_report.savings_numbers(row), "stats_utc": row.get("utc"),
               "commits": len(shas or []), "base": got.get("pushed_base"), "head": head}
        out["marked"] = update_result(repo, "push", **rec) is not None
        try:
            log = captain_ship_log()
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(rec, at=time.time(), repo=str(repo)), ensure_ascii=False) + "\n")
            out["logged"] = True
        except OSError:
            pass
        out["notified"] = (notifier or notify_mac)(T("notify_title", {"n": len(shas or [])}), "\n".join(lines[:2]))
        try:
            import shadow_explain
            shadow_explain.show_in_pane([""] + list(lines))      # 推的那一席視窗也顯示一行(不靠各家 hook)
        except Exception:  # noqa: BLE001
            pass
        print(T("announce_done", {"what": ", ".join(k for k, v in out.items() if v) or "-"}))
    except Exception as e:  # noqa: BLE001
        print(T("announce_failed", {"reason": "%s: %s" % (type(e).__name__, e)}))
    return out


def print_stale_hint(repo, days=7):
    """推完一包:有測試超過 days 天沒實跑過 ⇒ 提醒一行怎麼補跑;沒有就不印。算不出來不影響推包。"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import stale_tests
        n = len(stale_tests.stale(repo, days=days))
    except Exception:  # noqa: BLE001
        return
    if n:
        print(T("stale_hint", {"count": n, "n": days}))


def cmd_release(a):
    if pending_emergencies(a.repo):
        raise SystemExit(T("emergency_blocks_release"))
    if a.prof.gate_mode != "aib":
        return cmd_release_generic(a)
    git(a.repo, "fetch", "-q", "origin", "main")
    base = git(a.repo, "rev-parse", "origin/main").strip()
    if git(a.repo, "rev-parse", "HEAD").strip() != base:
        raise SystemExit(T("head_not_main"))
    if git(a.repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit(T("dirty"))
    rel = last_release(a.repo)
    items = packages_since(a.repo, rel)
    if not items:
        raise SystemExit(T("no_packages"))
    moved = [f for f in PINNED_FILES if git(a.repo, "diff", "--name-only", rel, "HEAD", "--", f).strip()]
    if moved:
        raise SystemExit(T("pins_moved", {"what": moved}))
    date = a.date or time.strftime("%Y-%m-%d")
    title = a.title or T("default_title", {"n": len(items), "what": ";".join(s for _h, s in items[:3]) + ("..." if len(items) > 3 else "")})
    cur = (Path(a.repo) / "VERSION").read_text().strip()
    print(preview(a.repo, cur, a.version, items, rel))
    if a.preview:
        return 0
    if not confirmed_by_human(a.version):
        return 3
    prev_ver = write_release(a.repo, a.version, date, title, items, a.branch[0] if a.branch else "main")
    move_baseline(a.repo, rel, prev_ver, a.version, date)
    git(a.repo, "commit", "-qam", "release: v%s — %s\n\n%s\n\n%s%s" % (
        a.version, title, "\n".join("- %s %s" % (h, s) for h, s in items), CONFIRM_TRAILER, a.version))
    head = git(a.repo, "rev-parse", "HEAD").strip()
    bad = check_commit(a.repo, head, a.prof)
    if bad:
        raise SystemExit(T("release_shape_bad", {"reason": bad}))
    gate_then_push(a.repo, a.branch, base, a.prof, fail_fast=getattr(a, "fail_fast", False), catch_up=False)
    print(T("released", {"version": a.version, "n": len(items)}))
    return 0


def cmd_release_generic(a):
    """別的專案:版號檔換成新版號、CHANGELOG(有的話)加一節,其他照 aib 的規則(預覽 → 真人終端機 → 形狀 → 推前檢查 → 推)。"""
    if not a.prof.version_files:
        print(T("release_no_version", {"what": getattr(a.prof, "detected", "-"), "path": sp.CONFIG}))
        return 2
    git(a.repo, "fetch", "-q", "origin", "main")
    base = git(a.repo, "rev-parse", "origin/main").strip()
    if git(a.repo, "rev-parse", "HEAD").strip() != base:
        raise SystemExit(T("head_not_main"))
    if git(a.repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit(T("dirty"))
    rel = last_release_generic(a.repo, a.prof)
    if rel is None:
        print(T("no_start", {"path": a.prof.policy_start_file}))
        return 2
    items = packages_since(a.repo, rel)
    if not items:
        raise SystemExit(T("no_packages"))
    date = a.date or time.strftime("%Y-%m-%d")
    title = a.title or T("default_title", {"n": len(items), "what": ";".join(s for _h, s in items[:3]) + ("..." if len(items) > 3 else "")})
    cur = _value(a.repo, "HEAD", a.prof.version_files[0])
    print(preview(a.repo, cur, a.version, items, rel, a.prof))
    if a.preview:
        return 0
    if not confirmed_by_human(a.version):
        return 3
    write_release_generic(a.repo, a.prof, a.version, date, items)
    git(a.repo, "commit", "-qam", "release: v%s — %s\n\n%s\n\n%s%s" % (
        a.version, title, "\n".join("- %s %s" % (h, s) for h, s in items), CONFIRM_TRAILER, a.version))
    head = git(a.repo, "rev-parse", "HEAD").strip()
    bad = check_commit(a.repo, head, a.prof)
    if bad:
        raise SystemExit(T("release_shape_bad", {"reason": bad}))
    gate_then_push(a.repo, a.branch + list(getattr(a.prof, "also_branches", [])), base, a.prof,
                   fail_fast=getattr(a, "fail_fast", False), catch_up=False)
    print(T("released", {"version": a.version, "n": len(items)}))
    return 0


def stats(repo, path=None, last=30, days=7, now=None):
    """這個倉最近 days 天(最多 last 輪)推前檢查的累計(aib ship stats)。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime((now or time.time()) - days * 86400))
    rows = pushgate.read_stats(repo, path=path, since=since)[-last:]
    tot = {k: sum(float(r.get(k) or 0) for r in rows) for k in ("ran", "ran_s", "reused", "saved_s", "unknown")}
    tot["rounds"] = len(rows)
    whole = tot["ran_s"] + tot["saved_s"]
    tot["pct"] = 100.0 * tot["saved_s"] / whole if whole > 0 else 0.0
    tot["days"] = days
    return tot


def _local_when(row):
    """統計列的時間 ⇒ 這台電腦的時區(10-08:以前直接切 UTC 字串印出來,台北看到 11:04 其實是 19:04)。
    回 ("YYYY-MM-DD HH:MM", "YYYY-MM-DD");讀不懂(舊列只有 time)⇒ 原字串切。"""
    import datetime as _dt
    raw = str(row.get("utc") or row.get("time"))
    try:
        t = _dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).astimezone()
    except ValueError:
        return raw[:16].replace("T", " "), raw[:10]
    return t.strftime("%Y-%m-%d %H:%M"), t.strftime("%Y-%m-%d")


def ledger(repo, path=None, audit_path=None, rows=None, rid=None):
    """10-06 the maintainer:從第一次起的總計帳。只用有記總支數的完整輪(提早停的不算一次推)。
    回 {"base", "pushes", "weeks", "total", "old"}。每輪:全部跑約 = 每支上次實跑秒數相加 / 分份數;省 = 全部跑約 − 實際牆鐘。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    rows = pushgate.read_stats(repo, path=path) if rows is None else rows
    full = [r for r in rows if r.get("files_total") and int(r.get("rc") or 0) != 5]
    old = sum(1 for r in rows if not r.get("files_total") and int(r.get("rc") or 0) != 5)
    pushes = []
    calib = None            # §20 第 2 項:最近一次「真的全部跑」(實跑 ≥ 9 成)的 實測牆鐘 / 估計;之後的估計都乘上它
    for r in full:
        fw = float(r.get("full_s") or 0) / max(1, int(r.get("shards") or 1))
        wall = float(r.get("wall_s") or 0)
        if int(r.get("files_ran") or 0) >= 0.9 * int(r["files_total"]) and fw > 0 and wall > 0:
            calib = {"when": _local_when(r)[1], "est": fw, "real": wall, "ratio": wall / fw}
        if calib:
            fw *= calib["ratio"]
        reused = int(r["files_total"]) - int(r.get("files_ran") or 0)
        kept = int(r["cells_total"]) - int(r.get("cells_ran") or 0)
        pushes.append({"when": _local_when(r)[0], "day": _local_when(r)[1],
                       "files": int(r["files_total"]), "cells": int(r["cells_total"]), "ran": int(r.get("files_ran") or 0),
                       "cells_ran": int(r.get("cells_ran") or 0), "reused": reused, "kept": kept,
                       "pct": 100.0 * reused / max(1, int(r["files_total"])), "full_wall": fw, "wall": wall,
                       "saved": max(0.0, fw - wall), "causes": r.get("causes") or {}, "by_feature": r.get("by_feature") or {}})
    weeks = []
    import datetime as _dt
    for p in pushes:
        d = _dt.date.fromisoformat(p["day"])
        monday = (d - _dt.timedelta(days=d.weekday())).isoformat()
        if not weeks or weeks[-1]["when"] != monday:
            weeks.append({"when": monday, "pushes": []})
        weeks[-1]["pushes"].append(p)
    for w in weeks:
        ps = w.pop("pushes")
        w.update(n=len(ps), files=ps[-1]["files"], cells=ps[-1]["cells"], pct=sum(x["pct"] for x in ps) / len(ps),
                 actual=sum(x["wall"] for x in ps), full=sum(x["full_wall"] for x in ps), saved=sum(x["saved"] for x in ps),
                 causes=[c for x in ps for c in cause_lines(x["causes"])])
    tot = {"n": len(pushes), "full": sum(p["full_wall"] for p in pushes), "actual": sum(p["wall"] for p in pushes),
           "saved": sum(p["saved"] for p in pushes), "serial": sum(float(r.get("full_s") or 0) for r in full)}
    last = full[-1] if full else {}
    # 10-07:改到 aib 自己追蹤器的那幾次(紀錄全部作廢、整套重跑)是 aib 開發自己的事,不是用的人平常會遇到的 ⇒
    #   「有 aib 平均每次」不算它們、另外一行說幾次花多少;總計照實全算。全部都是這種 ⇒ 照舊全算(沒別的可比)。
    steady = [p for p in pushes if not p["causes"].get("tracer")] or pushes
    voided = [p for p in pushes if p["causes"].get("tracer")] if steady is not pushes else []
    versus = {"serial": float(last.get("full_s") or 0), "mean_wall": (sum(p["wall"] for p in steady) / len(steady)) if steady else 0.0,
              "n": len(steady), "voided_n": len(voided), "voided_s": sum(p["wall"] for p in voided),
              "slowest": float(last.get("slowest_s") or 0)}
    # §20 第 1 項:抽查結果放在省時旁邊(按同一個週一分組)
    import false_reuse_audit
    audits = false_reuse_audit.read_audits(repo, path=audit_path, rid=rid)
    for w in weeks:
        w["audit"] = {"n": 0, "passed": 0, "fails": 0, "red": 0, "names": []}
    by_week = {w["when"]: w for w in weeks}
    # §20 第 5 項:紅燈與提早停的輪也是真花掉的時間(不算一次推,另列成本)
    for w in weeks:
        w["red_n"], w["red_s"] = 0, 0.0
    for r in rows:
        if int(r.get("rc") or 0) == 0 or not r.get("utc"):
            continue
        d = _dt.date.fromisoformat(_local_when(r)[1])
        k = (d - _dt.timedelta(days=d.weekday())).isoformat()
        if k in by_week:
            by_week[k]["red_n"] += 1
            by_week[k]["red_s"] += float(r.get("wall_s") or 0)
    atot = {"n": 0, "passed": 0, "fails": 0, "red": 0}
    for a in audits:
        d = _dt.date.fromisoformat(_local_when(a)[1])
        k = (d - _dt.timedelta(days=d.weekday())).isoformat()
        for key, src in (("n", "sampled"), ("passed", "match"), ("fails", "unstable"), ("red", "real_miss")):
            atot[key] += int(a.get(src) or 0)
            if k in by_week:
                by_week[k]["audit"][key] += int(a.get(src) or 0)
        if k in by_week:
            by_week[k]["audit"]["names"] += list(a.get("misses") or [])
    return {"base": pushes[0] if pushes else None, "pushes": pushes, "weeks": weeks, "total": tot, "old": old, "audit": atot,
            "calib": calib, "versus": versus}


def cause_lines(c):
    """一輪的重跑原因 → 幾行白話(§20 第 4 項)。"""
    out = []
    if c.get("first"):
        out.append(T("lg_c_first"))
    for f in c.get("tracer") or []:
        out.append(T("lg_c_tracer", {"file": f}))
        break
    for f in c.get("deps") or []:
        out.append(T("lg_c_deps", {"file": f}))
    for f, n in c.get("wide") or []:
        out.append(T("lg_c_wide", {"file": f, "tests": n}))
    return out


def print_ledger(repo, path=None, audit_path=None):
    import pushgate
    d = pushgate.dur_text
    lg = ledger(repo, path=path, audit_path=audit_path)
    if not lg["base"]:
        print(T("lg_none"))
        return
    print_ledger_versus(lg)
    b = lg["base"]
    print(T("lg_base", {"when": b["day"], "files": b["files"], "items": b["cells"], "total": d(b["full_wall"])}))
    print(T("lg_push_head"))
    c = lg["calib"]
    print(T("lg_calib", {"when": c["when"], "total": d(c["est"]), "actual": d(c["real"]), "value": "%.2f" % c["ratio"]})
          if c else T("lg_uncalib"))
    for p in lg["pushes"][-10:]:
        print(T("lg_push", {"when": p["when"], "files": p["files"], "items": p["cells"], "ran": p["ran"], "count": p["cells_ran"],
                            "reused": p["reused"], "kept": p["kept"], "pct": "%.0f" % p["pct"], "total": d(p["full_wall"]),
                            "actual": d(p["wall"]), "seconds": d(p["saved"])}))
        bf = p["by_feature"]
        tot_bf = sum(float(v or 0) for v in bf.values())
        if tot_bf >= 1:
            pc = lambda k: "%.0f" % (100.0 * float(bf.get(k) or 0) / tot_bf)  # noqa: E731
            print(T("lg_feature", {"pct": pc("select"), "percent": pc("reuse"), "value": pc("keyed"), "count": pc("resume")}))
        for ln in cause_lines(p["causes"]):
            print(T("lg_cause", {"what": ln}))
    print(T("lg_week_head"))
    prev = None
    for w in lg["weeks"]:
        delta = growth = ""
        if prev:
            delta = T("lg_delta", {"files": "%+d" % (w["files"] - prev["files"]), "items": "%+d" % (w["cells"] - prev["cells"])})
            diff = w["saved"] - prev["saved"]
            growth = T("lg_growth" if diff >= 0 else "lg_shrink", {"seconds": d(abs(diff))})
        print(T("lg_week", {"when": w["when"], "n": w["n"], "files": w["files"], "items": w["cells"], "delta": delta,
                            "pct": "%.0f" % w["pct"], "actual": d(w["actual"]), "total": d(w["full"]), "seconds": d(w["saved"]),
                            "percent": "%.0f" % (100.0 * w["saved"] / w["full"] if w["full"] else 0), "growth": growth}))
        if w.get("red_n"):
            print(T("lg_red_cost", {"n": w["red_n"], "seconds": d(w["red_s"])}))
        if prev and prev["saved"] > 0 and w["saved"] < 0.5 * prev["saved"]:
            print(T("lg_drop"))
            for ln in sorted(set(w["causes"])) or [T("lg_c_unknown")]:
                print(T("lg_cause", {"what": ln}))
        au = w["audit"]
        if au["n"]:
            print(T("lg_audit", {k: au[k] for k in ("n", "passed", "fails", "red")}))
            if au["names"]:
                print(T("lg_audit_miss", {"names": ", ".join(au["names"])}))
        else:
            print(T("lg_audit_none"))
        prev = w
    t = lg["total"]
    print(T("lg_total", {"n": t["n"], "total": d(t["full"]), "actual": d(t["actual"]), "seconds": d(t["saved"]),
                         "pct": "%.0f" % (100.0 * t["saved"] / t["full"] if t["full"] else 0)}))
    if t["serial"] > 0:
        print(T("lg_vs_total", {"n": t["n"], "total": d(t["serial"]), "actual": d(t["actual"]),
                                "seconds": d(max(0.0, t["serial"] - t["actual"]))}))
    if lg["audit"]["n"]:
        print(T("lg_audit_total", lg["audit"]))
    if lg["old"]:
        print(T("lg_old", {"count": lg["old"]}))


def print_ledger_versus(lg):
    """§21:總計帳最上面三行 —— 沒有 aib 一支一支跑每次多久、有 aib 這台平均每次多久、加開機器推算(多台分工還沒做)。"""
    import pushgate
    d = pushgate.dur_text
    v = lg.get("versus") or {}
    if not v.get("serial"):
        return
    print(T("lg_vs_serial", {"total": d(v["serial"])}))
    print(T("lg_vs_aib", {"actual": d(v["mean_wall"]), "n": v.get("n", lg["total"]["n"]),
                          "pct": "%.0f" % (100.0 * (1 - v["mean_wall"] / v["serial"]))}))
    if v.get("voided_n"):
        print(T("lg_vs_tracer", {"count": v["voided_n"], "actual": d(v["voided_s"])}))
    print(T("lg_vs_multi", {"total": d(max(v["slowest"], v["mean_wall"] / 2)), "expected": d(max(v["slowest"], v["mean_wall"] / 4))}))


def print_ledger_brief(repo):
    """推完一包:總計、這一次、從第一次到現在(10-06 the maintainer:每一次真實省多少)。"""
    import pushgate
    d = pushgate.dur_text
    lg = ledger(repo)
    if not lg["pushes"]:
        return
    b, p, t = lg["base"], lg["pushes"][-1], lg["total"]
    print_ledger_versus(lg)
    print(T("lg_base", {"when": b["day"], "files": b["files"], "items": b["cells"], "total": d(b["full_wall"])}))
    print(T("lg_push", {"when": p["when"], "files": p["files"], "items": p["cells"], "ran": p["ran"], "count": p["cells_ran"],
                        "reused": p["reused"], "kept": p["kept"], "pct": "%.0f" % p["pct"], "total": d(p["full_wall"]),
                        "actual": d(p["wall"]), "seconds": d(p["saved"])}))
    print(T("lg_total", {"n": t["n"], "total": d(t["full"]), "actual": d(t["actual"]), "seconds": d(t["saved"]),
                         "pct": "%.0f" % (100.0 * t["saved"] / t["full"] if t["full"] else 0)}))


def cmd_stale(a):
    """aib ship stale(10-07):列出超過 N 天沒實跑過的測試(推前檢查只挑受影響的,沒被碰到的可以很久沒人跑);
    --run 就把它們實跑一次(紀錄照常寫進紀錄池,下週就不再是「很久沒跑」)。結束碼同 aib test(0 綠 / 1 紅 / 4 有沒驗的)。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import stale_tests
    rows, cant = stale_tests.split(a.repo, days=a.days)
    if cant:
        print(T("stale_cant", {"count": len(cant), "what": ["%s(%s)" % (t, ",".join(n)) for t, n in cant]}))
    if not rows:
        print(T("stale_none", {"n": a.days}))
        return 0
    print(T("stale_head", {"count": len(rows), "n": a.days}))
    for t, when in rows[:30]:
        print(T("stale_row", {"test": t, "when": (when or "")[:10] or T("stale_never")}))
    if len(rows) > 30:
        print(T("stale_more", {"count": len(rows) - 30}))
    if not a.run:
        return 0
    rc, _out = _stream([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(Path(a.repo).resolve()),
                        *[t for t, _ in rows]])
    return rc


def cmd_crosscheck(a):
    """aib ship crosscheck(10-07):整套對答案。aib 一次只跑一支;測試互相影響的專案,整套跑可能跟單支跑不一樣。
    先 aib test 全部(能沿用就沿用),再用專案平常的方式整套跑一次,逐檔比紅綠。0 一致 / 1 不一致 / 4 比不了或有沒比到的。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import crosscheck
    import pushgate
    import runner
    repo = Path(a.repo).resolve()
    tests = pushgate.all_tests(str(repo))
    kind, x = crosscheck.framework(repo, tests)
    if not kind:
        print(T("cc_cant", {"why": T("cc_why_" + x.replace("-", "_"))}))
        return 4
    print(T("cc_head", {"count": len(tests), "kind": kind}))
    _stream([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(repo), *tests])
    aib = crosscheck.latest_verdicts(runner._pool_file(repo))
    print(T("cc_suite", {"kind": kind}))
    suite, tail = crosscheck.SUITES[kind](repo, x, tests)
    if suite is None:
        print(T("cc_suite_failed", {"kind": kind}))
        print(tail)
        return 4
    diff, missing = crosscheck.compare(aib, suite, tests)
    crosscheck.record(repo, len(tests), diff, missing)
    for t, one, whole in diff:
        print(T("cc_row", {"test": t, "got": T("cc_" + one), "other": T("cc_" + whole)}))
    if missing:
        print(T("cc_missing", {"count": len(missing), "names": ", ".join(m[0] for m in missing[:10])}))
    if diff:
        print(T("cc_diff", {"count": len(diff)}))
        return 1
    print(T("cc_ok", {"count": len(tests) - len(missing)}))
    return 4 if missing else 0


def cmd_why(a):
    """aib ship why(10-07):這個專案「每次都得重跑」的測試,依原因排行(支數、每輪花掉的秒數),附白話與怎麼辦。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    import rerun_reasons
    import runner
    repo = Path(a.repo).resolve()
    tests = pushgate.all_tests(str(repo))
    rows = rerun_reasons.latest_rows(runner._pool_file(repo))
    groups, n, rerun_s, all_s = rerun_reasons.summarize(tests, rows)
    seen = sum(1 for t in tests if t in rows)
    if seen < len(tests):
        print(T("why_unseen", {"count": len(tests) - seen}))
    if not groups:
        print(T("why_none", {"count": seen}))
        return 0
    print(T("why_head", {"total": seen, "count": n, "seconds": int(round(rerun_s)),
                         "pct": int(round(100.0 * rerun_s / all_s)) if all_s else 0}))
    for g in groups[:a.top]:
        print(T("why_row", {"count": len(g["tests"]), "seconds": int(round(g["seconds"])),
                            "what": T("why_hint_" + g["cat"], {"detail": g["detail"]}),
                            "names": ", ".join(g["tests"][:3]) + (" …" if len(g["tests"]) > 3 else "")}))
    if len(groups) > a.top:
        print(T("why_more", {"count": len(groups) - a.top}))
    return 0


def cmd_audit(a):
    """aib ship audit(§20 第 1 項):從可沿用的測試裡抽幾支真的重跑、比對;結果記進帳。沿用錯 ⇒ rc 1。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import false_reuse_audit
    plan = false_reuse_audit.audit(a.repo, pct=a.pct, min_n=a.n, dry_run=True)
    print(T("audit_run", {"count": plan.get("pool_reusable", 0), "n": plan.get("sampled", 0)}))
    if not plan.get("sampled"):
        return 0
    res = false_reuse_audit.audit(a.repo, pct=a.pct, min_n=a.n, seed=plan.get("seed"))
    print(T("lg_audit", {"n": res["sampled"], "passed": res["match"], "fails": res["unstable"], "red": res["real_miss"]}))
    names = [d["test"] for d in res.get("details") or [] if d.get("verdict") == "real-miss"]
    if names:
        print(T("lg_audit_miss", {"names": ", ".join(names)}))
        return 1
    return 0


def print_all_projects(path=None, audit_path=None):
    """aib ship stats --all(§20 第 6 項前半):這台機器上每個專案一行,再一行合計。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    d = pushgate.dur_text
    groups = {}
    for r in pushgate.read_stats(None, path=path):
        groups.setdefault(str(r.get("repo") or ""), []).append(r)
    tot = {"count": 0, "n": 0, "full": 0.0, "actual": 0.0, "saved": 0.0}
    for rid, rows in sorted(groups.items(), key=lambda kv: str(kv[1][-1].get("name") or kv[0])):
        lg = ledger(None, rows=rows, rid=rid, audit_path=audit_path)
        if not lg["pushes"]:
            continue
        last, t = lg["pushes"][-1], lg["total"]
        name = next((str(r["name"]) for r in reversed(rows) if r.get("name")), rid[:8] or "?")
        print(T("lg_proj", {"name": name, "files": last["files"], "items": last["cells"], "n": t["n"], "total": d(t["full"]),
                            "actual": d(t["actual"]), "seconds": d(t["saved"]),
                            "pct": "%.0f" % (100.0 * t["saved"] / t["full"] if t["full"] else 0)}))
        tot["count"] += 1
        for k in ("n", "full", "actual", "saved"):
            tot[k] += t[k]
    if not tot["count"]:
        print(T("lg_none"))
        return
    print(T("lg_all_total", {"count": tot["count"], "n": tot["n"], "total": d(tot["full"]), "actual": d(tot["actual"]),
                             "seconds": d(tot["saved"]), "pct": "%.0f" % (100.0 * tot["saved"] / tot["full"] if tot["full"] else 0)}))


def cmd_worker(a):
    """aib ship worker(§22):在閒置機器上開著,認領受信的人發的推前檢查工作、跑完回報。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import aib_plugins
    farm, _name, why = aib_plugins.get(a.repo, "ship.worker")            # §23:外掛;沒裝/沒開/沒授權 ⇒ 照實說
    if farm is None:
        print(T("plugin_unavailable", {"what": "ship.worker", "reason": why}))
        return 3
    return farm.main(["worker", "--repo", a.repo, "--me", a.me] + (["--once"] if a.once else [])
                     + (["--trust", a.trust] if a.trust else []))


def cmd_plugins(a):
    """aib ship plugins(§23):裝了哪些外掛、開了沒、有沒有授權、為什麼不能用。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import aib_plugins
    rows = aib_plugins.status(a.repo)
    if not rows:
        print(T("plugins_none"))
    for r in rows:
        print(T("plugin_row", {"name": r["name"], "version": r["version"], "source": r["where"],
                               "state": T("plugin_on" if r["on"] else "plugin_off"),
                               "reason": T("plugin_licensed") if r["licensed"] else r["why"]}))
    if any(r.get("addon") for r in rows) and not aib_plugins.license_check_enforced():
        print(T("plugin_license_draft"))           # 10-07:要加購的外掛擋不住自己寫的授權檔 ⇒ 照實說
    return 0


def print_team(repo, me, trust, farm=None):
    """aib ship stats --team(§20 第 6 項後半;外掛 ship.team):先把這台的紀錄送上廣場,再收受信的人送的,每台一行 + 團隊合計。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    d = pushgate.dur_text
    if farm is None:
        import aib_plugins
        farm, _name, why = aib_plugins.get(repo, "ship.team")
        if farm is None:
            print(T("plugin_unavailable", {"what": "ship.team", "reason": why}))
            return 3
    if me:
        farm.publish_stats(repo, me)
    groups = {}
    for r in farm.team_rows(repo, trust):
        groups.setdefault((r.get("by"), r.get("host")), []).append(r)
    tot = {"count": 0, "n": 0, "full": 0.0, "actual": 0.0, "saved": 0.0, "serial": 0.0}
    for (who, host), rows in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        lg = ledger(None, rows=rows, rid="-")
        if not lg["pushes"]:
            continue
        last, t = lg["pushes"][-1], lg["total"]
        print(T("lg_proj", {"name": "%s(%s)" % (who, host), "files": last["files"], "items": last["cells"], "n": t["n"],
                            "total": d(t["full"]), "actual": d(t["actual"]), "seconds": d(t["saved"]),
                            "pct": "%.0f" % (100.0 * t["saved"] / t["full"] if t["full"] else 0)}))
        tot["count"] += 1
        for k in ("n", "full", "actual", "saved", "serial"):
            tot[k] += t[k]
    if not tot["count"]:
        print(T("lg_none"))
        return 0
    print(T("lg_team_total", {"count": tot["count"], "n": tot["n"], "total": d(tot["serial"]), "actual": d(tot["actual"]),
                              "seconds": d(max(0.0, tot["serial"] - tot["actual"]))}))
    return 0


def cmd_stats(a):
    if getattr(a, "team", False):
        sys.path.insert(0, str(ROOT / "tools" / "verify"))
        import pool_share
        return print_team(a.repo, a.me, pool_share.parse_trust(a.trust or os.environ.get(pool_share.ENV_TRUST, "")))
    if getattr(a, "all", False):
        print_all_projects()
        return 0
    t = stats(a.repo)
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    if getattr(a, "at", ""):               # 10-08:回報裡的數字指到哪一輪,這裡就印那一輪(查證指令真的能跑)
        import ship_report
        hit = ship_report.find_rows(pushgate.read_stats(a.repo), a.at)
        if not hit:
            print(T("stats_at_none", {"when": a.at}))
            return 1
        for r in hit:
            for ln in ship_report.row_lines(T, pushgate.dur_text, r):
                print(ln)
        return 0
    print(T("stats", {"n": t["days"], "count": t["rounds"], "pct": "%.0f" % t["pct"], "total": pushgate.dur_text(t["saved_s"])}))
    print_ledger(a.repo)
    print_prediction(a.repo)
    return 0


# ---- 專案名稱與專案樹(10-06 the maintainer:每個 aib ship 指令最上面一行一定列專案名稱;init 另列專案樹)----------------
def project_name(repo):
    url = git(repo, "remote", "get-url", "origin", check=False).strip()
    m = re.search(r"([^/:]+?)(?:\.git)?/?$", url) if url else None
    return m.group(1) if m else Path(repo).name


def project_tree(repo, depth=2, max_lines=16):
    """[(相對路徑, 檔數)]:頂層資料夾(與 src/、tests/ 這類再往下一層),略過虛擬環境、隱藏資料夾;最多 max_lines 行。"""
    root = Path(repo)

    def count(d):
        n = 0
        for dp, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in sp.SKIP_DIRS and not x.startswith(".")]
            n += len(fns)
        return n
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in sp.SKIP_DIRS and not p.name.startswith(".")):
        rows.append((d.name + "/", count(d)))
        if depth > 1 and d.name in ("src", "lib", "tests", "test", "tools", "packages", "apps"):
            for sub in sorted(x for x in d.iterdir() if x.is_dir() and x.name not in sp.SKIP_DIRS and not x.name.startswith(".")):
                rows.append(("  " + d.name + "/" + sub.name + "/", count(sub)))
    files = sum(1 for p in root.iterdir() if p.is_file())
    rows.append(("./", files))
    return rows[:max_lines] + ([("…", len(rows) - max_lines)] if len(rows) > max_lines else [])


def print_header(repo):
    print(T("project", {"name": project_name(repo), "path": repo}))


def print_scenarios(repo):
    """10-06 the maintainer:讓客戶知道什麼情況下推會省最多、要做什麼才能更常落在那種情況(replay_savings.scenarios)。印了回 True。"""
    import pushgate
    import replay_savings
    try:
        sc = replay_savings.scenarios(str(repo))
    except Exception:  # noqa: BLE001
        sc = None
    if not sc or not sc["best_pct"] or sc["best_pct"] < 1:
        return False
    k, wall = sc["kinds"], sc["wall_s"]
    try:
        n_all = len(pushgate.all_tests(repo)) or sc.get("tests_all") or 0   # 與總計帳同一個母體(目前樹上的測試檔)
    except Exception:  # noqa: BLE001
        n_all = sc.get("tests_all") or 0
    print(T("sc_best", {"n": n_all, "count": "%.0f" % (sc.get("best_tests") or 0),
                        "pct": "%.0f" % (100.0 * (sc.get("best_tests") or 0) / n_all if n_all else 0),
                        "total": pushgate.dur_text(sc.get("best_cpu_s") or 0), "percent": "%.0f" % sc["best_pct"],
                        "expected": pushgate.dur_text(wall * sc["best_pct"] / 100.0)}))
    print(T("sc_share", {"n": sc["commits"], "count": k["ordinary"]["n"], "pct": "%.0f" % sc["now_pct"]}))
    if sc["files"] or k["deps"]["n"] or k["aib"]["n"]:
        print(T("sc_why"))
        for path, cnt, readers, keyable in sc["files"]:
            print(T("sc_wide_key" if keyable else "sc_wide", {"count": cnt, "file": path, "tests": readers}))
        if k["deps"]["n"]:
            print(T("sc_deps", {"count": k["deps"]["n"]}))
        if k["aib"]["n"]:
            print(T("sc_aib", {"count": k["aib"]["n"]}))
    if sc["after_n"] > k["ordinary"]["n"]:
        print(T("sc_after", {"n": sc["commits"], "count": sc["after_n"], "pct": "%.0f" % sc["after_pct"]}))
    what = T("sc_rate_gates" if sc["rate_source"] == "gates" else "sc_rate_commits", {"count": sc["rate_counted"]})
    now_t, after_t = pushgate.dur_text(sc["week_now_s"]), pushgate.dur_text(sc["week_after_s"])
    if now_t == after_t:                        # 10-07:兩個一樣時「照做之後」沒有意義 ⇒ 只印一個
        print(T("sc_week_same", {"what": what, "hours": now_t, "expected": pushgate.dur_text(wall)}))
    else:
        print(T("sc_week", {"what": what, "hours": now_t, "total": after_t, "expected": pushgate.dur_text(wall)}))
    return True


def print_prediction(repo):
    """預測:在什麼動作之後開始大幅省時間(依這個專案自己的紀錄與最近 50 個提交)。紀錄還不夠 ⇒ 先講要做什麼。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    try:
        import replay_savings
        pr = replay_savings.predict(str(repo))
    except Exception:  # noqa: BLE001
        pr = None
    if not pr:
        print(T("predict_none"))
        return
    if pr["coverage"] < 90:
        print(T("predict_build", {"pct": "%.0f" % pr["coverage"]}))
    if print_scenarios(repo):
        return
    if pr["ordinary_pct"] is not None and pr["ordinary_pct"] >= 1:
        print(T("predict_ordinary", {"pct": "%.0f" % pr["ordinary_pct"], "n": pr["ordinary_n"]}))
    if pr["wide_pct"] is not None and pr["wide_pct"] >= 1:
        print(T("predict_wide", {"pct": "%.0f" % pr["wide_pct"], "n": pr["wide_n"]}))
    elif pr["wide_n"]:                       # 不到 1% 不報「約 0%」(the maintainer:不寫沒省到之類)
        print(T("predict_wide_full", {"n": pr["wide_n"]}))


def cmd_init(a):
    """aib ship init(10-06,開發檔 §14):新用戶第一步。偵測 → 檢查測的是不是這棵樹 → 全部跑一次建紀錄 → 估之後能省多少。
    不推、不提交;設定檔寫出來給人看,要不要留由人決定。"""
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    print(T("tree_head"))
    for path, n in project_tree(a.repo):
        print(T("tree_row", {"path": path, "n": n}))
    prof = a.prof
    print(T("init_version", {"files": prof.version_files or "-", "what": getattr(prof, "detected", prof.source)}))
    tests = sp.discover_tests(a.repo)
    print(T("init_tests", {"n": len(tests)}))
    foreign = pushgate.probe_foreign(a.repo)
    if foreign:
        for name, where in foreign:
            print(pushgate.T("foreign_import", {"what": name, "path": where}))
        return 4
    cfg = Path(a.repo) / sp.CONFIG
    if prof.source == "detect" and not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(sp.render(prof), encoding="utf-8")
        print(T("init_config", {"path": sp.CONFIG}))
    if a.no_run or not tests:
        print(T("init_next"))
        return 0
    gate = Path(a.repo) / "tools" / "verify" / "pushgate.py" if prof.gate_mode == "aib" else ROOT / "tools" / "verify" / "pushgate.py"
    p = subprocess.run([sys.executable, str(gate), "--repo", a.repo, "--mode", "generic", "--jobs", str(prof.gate_jobs()),
                        "--base", "HEAD", "--no-red-output", "--no-resume"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)   # init 是來建紀錄的:不接續(紀錄池被清過時,接續日誌會讓測試永遠不留紀錄)
    # init 不推 ⇒ 不印「可以推/不能推」與紅燈的整段輸出;紅的那幾支照列(✗ 那行)
    red_line = pushgate.T("red", {"reason": "\0"}).split("\0")[0]
    keep = [ln for ln in p.stdout.splitlines() if ln != pushgate.T("ok") and not ln.startswith(red_line)
            and not ln.startswith(("  合計", "  Total")) and not re.match(r"^(第 \d+ 份|shard \d+) ", ln)]
    sys.stdout.write("\n".join(keep) + "\n")
    try:
        import replay_savings
        est, _n, _total = replay_savings.estimate(str(a.repo), n=50)
    except Exception:  # noqa: BLE001
        est = None
    if est and est.get("mean_pct"):
        print(T("init_estimate", {"pct": "%.0f" % est["mean_pct"], "n": est["commits"]}))
    print_prediction(a.repo)
    rows = pushgate.hot_files(a.repo, top=3)
    if rows:
        print(T("hot_head", {"n": len(rows)}))
        for f, n, c in rows:
            print(T("hot_row", {"file": f, "tests": n, "count": c}))
    print(T("init_next"))
    return 0 if p.returncode in (0, 4) else p.returncode


def _aib_source():
    """產生 CI 檔用:aib 自己這份的 GitHub 倉名(owner/repo)與目前提交;量不到 ⇒ ("", "")。"""
    def git(*args):
        try:
            p = subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return ""
        return p.stdout.strip() if p.returncode == 0 else ""
    url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    head = git("rev-parse", "HEAD")
    if head and not git("branch", "-r", "--contains", head):
        # 本機還沒推的提交 GitHub 上拿不到;退回較舊的已推提交又可能缺 CI 檔要用的功能(--all)⇒ 不猜,回空、請人先推
        head = ""
    return (m.group(1) if m else ""), head


def cmd_ci(a):
    """aib ship ci(10-07):寫出 GitHub Actions 工作流程檔(紀錄池進快取、只跑該跑的、摘要頁印省了多少)。
    已有那個檔 ⇒ 不蓋(要蓋加 --force);--print 只印不寫。不提交、不推。"""
    import ci_workflow
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import shadow_tracer as st
    aib_repo, aib_ref = _aib_source()
    if not aib_repo:
        print(T("ci_no_source"))
        return 2
    if not aib_ref:
        print(T("ci_unpushed", {"cmd": "aib ship ci"}))
        return 2
    ver = st.python_id(st.test_python(Path(a.repo)))[0]
    py = ".".join(ver.split(".")[:2]) if ver[:1].isdigit() else "3.12"
    text = ci_workflow.render(a.repo, aib_repo, aib_ref, python=py)
    if a.print:
        sys.stdout.write(text)
        return 0
    out = Path(a.repo) / ci_workflow.WORKFLOW
    if out.exists() and not a.force:
        print(T("ci_exists", {"path": ci_workflow.WORKFLOW}))
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(T("ci_written", {"path": ci_workflow.WORKFLOW, "what": aib_repo, "version": aib_ref[:9]}))
    print(T("ci_next"))
    print()
    print(T("ci_contributing"))
    print(ci_workflow.contributing(aib_repo))
    return 0


def cmd_hotfiles(a):
    sys.path.insert(0, str(ROOT / "tools" / "verify"))
    import pushgate
    rows = pushgate.hot_files(a.repo)
    print(T("hot_head", {"n": len(rows)}))
    for f, n, c in rows:
        print(T("hot_row", {"file": f, "tests": n, "count": c}))
    return 0


def cmd_watch(a):
    import ship_watch
    if a.once:
        rc = ship_watch.once(a.repo, a.prof.gate_mode, T=T)
        return 0 if rc in (0, None) else rc
    try:
        ship_watch.loop(a.repo, a.prof.gate_mode, interval=a.interval, quiet=a.quiet, T=T)
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ship")
    ap.add_argument("--repo", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--since")
    c.add_argument("--rev", default="HEAD")
    c.add_argument("--gate", action="store_true",
                   help="before handing a package over: run the same selection the pre-push check runs, without pushing")
    c.add_argument("--base", help="--gate: the main to measure against (default: fetch the GitHub remote — github, else origin — and use its main)")
    pk = sub.add_parser("package")
    pk.add_argument("--branch", action="append", default=[])
    pk.add_argument("--emergency", action="store_true", help="human at a terminal only: skip the pre-push check once")
    pk.add_argument("--preview", action="store_true", help="do not run: show the forecast, the main causes and hot files")
    pk.add_argument("--fail-fast", action="store_true",
                    help="stop the pre-push check at the first new red (default: run everything and list every red)")
    r = sub.add_parser("release")
    r.add_argument("--version", required=True)
    r.add_argument("--date")
    r.add_argument("--title")
    r.add_argument("--branch", action="append", default=[])
    r.add_argument("--preview", action="store_true", help="print the preview only, change nothing")
    r.add_argument("--fail-fast", action="store_true",
                   help="stop the pre-push check at the first new red (default: run everything and list every red)")
    ini = sub.add_parser("init")
    ini.add_argument("--no-run", action="store_true", help="detect and check only; do not run the tests")
    stp = sub.add_parser("stats")
    stp.add_argument("--all", action="store_true", help="every project on this machine, one line each, plus a total")
    stp.add_argument("--at", default="", help='one round: "MM-DD HH:MM" Taipei time as printed in the report draft')
    stp.add_argument("--team", action="store_true", help="every trusted machine's ledger for this project (plugin farm)")
    stp.add_argument("--me", default="", help="with --team: publish this machine's rounds as @name first")
    stp.add_argument("--trust", default="", help="with --team: @name=key_fp,… (default AIB_VERIFY_TRUST)")
    sub.add_parser("hotfiles")
    sub.add_parser("plugins")
    wk = sub.add_parser("worker")
    wk.add_argument("--me", required=True, help="this machine's plaza name, e.g. @fish")
    wk.add_argument("--trust", default="", help="@name=key_fp,… whose jobs to take (default: AIB_VERIFY_TRUST)")
    wk.add_argument("--once", action="store_true")
    au = sub.add_parser("audit")
    au.add_argument("--n", type=int, default=10, help="at least this many reused tests are rerun")
    au.add_argument("--pct", type=float, default=5.0, help="or this share of the reusable tests, whichever is larger")
    sl = sub.add_parser("stale")
    sl.add_argument("--days", type=int, default=7, help="tests whose last real run is older than this many days")
    sl.add_argument("--run", action="store_true", help="run them now (results go into the record pool as usual)")
    wy = sub.add_parser("why")
    wy.add_argument("--repo", default=".")
    wy.add_argument("--top", type=int, default=8)
    cc = sub.add_parser("crosscheck")
    cc.add_argument("--repo", default=".")
    ci = sub.add_parser("ci")
    ci.add_argument("--print", action="store_true", help="print the workflow instead of writing it")
    ci.add_argument("--force", action="store_true", help="overwrite an existing .github/workflows/aib-test.yml")
    sub.add_parser("sync")
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--quiet", type=float, default=10.0)
    w.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    a.repo = str(Path(a.repo).resolve())
    a.prof = sp.load(a.repo)
    if not (a.cmd == "ci" and a.print):     # --print 的輸出要能直接存成檔:不印專案抬頭
        print_header(a.repo)
    return {"init": cmd_init, "check": cmd_check, "package": cmd_package, "release": cmd_release, "watch": cmd_watch, "stats": cmd_stats, "hotfiles": cmd_hotfiles, "audit": cmd_audit, "stale": cmd_stale, "crosscheck": cmd_crosscheck, "why": cmd_why, "ci": cmd_ci, "worker": cmd_worker, "plugins": cmd_plugins, "sync": cmd_sync}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
