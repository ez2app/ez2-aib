#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""沿用池的列要離開這台機器之前,先過這一關(docs/PLAN-shadow-cross-device-1002.md 第 5 步)。

一列(tools/verify/runner.py `_traced_run` 寫的)帶著讀過的路徑、子程式參數(argv)、輸出尾巴。
同一台機器自己用沒關係;要給別台/別人,這些會外流。本檔只做「清乾淨或不匯出」:

  1. 白名單:只留別台判斷「能不能沿用」要的欄位(EXPORT_FIELDS),其餘(tail、fail_cells、
     repo 絕對路徑、ran…)一律不帶。加 `exported: true`。
  2. 路徑:inputs 裡的絕對路徑,在倉底下 → `<REPO>/…`(已是 `./x` 的不動);
     argv / why 文字裡的倉路徑 → `<REPO>`、家目錄 → `<HOME>`。
  3. 綁主機:inputs 讀了真實家目錄(倉以外、HOME 底下的任何路徑),或碰到活駕駛艙的暫存路徑
     (路徑某一段以 `aihub-` 開頭:tmux socket `aihub-<艙>`、駕駛艙暫存目錄)⇒ 整列不匯出。
     它的結果本來就不能代表別台。
  4. 秘密:argv 項與 why 過秘密掃描;命中就整欄清空(argv 項換成 `argv:<redacted>`、
     why 換成 ["<redacted>"])。清完再對整列做一次掃描,還有命中 ⇒ 整列不匯出。
  5. 失敗方向:任何看不懂的(不是 JSON、欄位型別不對、inputs 項種類不認得、argv 不是字串清單)
     ⇒ 整列不匯出。**絕不匯出清一半的列。**

秘密規則的來源:倉裡唯一的掃描規則在 src/persona-merge/staging-merge.py `_SECRET_RES`
(資料夾名帶連字號、模組本身是 CLI,不能乾淨地 import)。這裡抄了那四條,再補幾條保守的
(GitHub 其他前綴、Slack xox?-、token=/key=/secret=/password= 後面的長值、Bearer)。
寬鬆方向 = 多清(誤判只是少沿用一列),不是少清。

已知邊界(不宣稱):
  - input_fp / key 是原機器用**清理前**的 inputs 算的;本檔不重算。別台要比的是指紋,不是重算。
  - stdlib、/opt 之類倉外且家目錄外的絕對路徑原樣保留(它們本來就是環境的一部分)。
  - `<tmpN>/aihub-…` 這種治具裡的假艙名也會被當成綁主機(保守側)。

CLI:
  row_export.py export --rows IN.jsonl --out OUT.jsonl --repo R [--home H]
  stdout 印一行 JSON 摘要;rc 0 = 跑完(不論匯出幾列),rc 2 = 參數或讀寫失敗。
"""
import argparse
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import sys
import tempfile

EXPORT_FIELDS = (
    "test", "rc", "timed_out", "measured", "external", "why", "inputs", "input_fp", "env_fp",
    "key", "lock_fp", "repo_id", "host_id", "duration_s", "source", "time", "tz", "tz_off_s",
)
REQUIRED_FIELDS = ("test", "rc", "inputs", "input_fp", "env_fp", "key")
# 分析模式(10-02 建議 5):把 Mac 的發版證詞列清乾淨推到資料分支,讓雲端能自己量。
# 證詞列(合入見證/發版全套)不一定有指紋欄(量不到的列沒有);分析要的是「哪支、綠紅、幾秒、量不量得到、為什麼」。
# ★ 分析列**永遠不能被沿用**:拿掉 key(reuse_index 沒有 key 一律必跑),並標 analysis: true。
# 綁主機的列(讀了家目錄/碰活駕駛艙)也收,但 inputs 整個拿掉、標 host_bound: <原因> —— 數得到,看不到內容。
ANALYSIS_REQUIRED = ("test", "rc")
ANALYSIS_EXTRA = ("outcome", "execution_mode", "scope", "skipped", "reused", "verdict", "receipt_grade", "elapsed_s")

REDACTED = "<redacted>"
REPO_TAG = "<REPO>"
HOME_TAG = "<HOME>"

# inputs 項種類(與 reuse_index._PATH_KINDS_* / shadow_tracer.inputs_from_events 對齊)
#   kind:path:suffix → 路徑是最後一個冒號之前的全部
_PATH_WITH_SUFFIX = ("open", "listdir", "probe", "handoff", "handoffdir", "selfwrite")
#   kind:path
_PATH_BARE = ("missing",)
#   沒有路徑語意
_NO_PATH = ("argv", "tool")
KNOWN_KINDS = _PATH_WITH_SUFFIX + _PATH_BARE + _NO_PATH

# 前四條抄自 src/persona-merge/staging-merge.py _SECRET_RES;其餘為本檔補的保守規則。
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[osur]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)(?:token|api[_-]?key|key|secret|passwd|password|pwd|auth)"
               r"[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9+/_.=-]{16,}"),
]

_AIHUB_SEG = re.compile(r"(?:^|/)aihub-[^/\s\"']+")


class Unexportable(ValueError):
    """這一列不匯出;args[0] 是原因鍵(進摘要)。"""


def find_secret(text):
    """回第一個命中的規則索引;沒有命中回 None。"""
    s = str(text)
    for i, rx in enumerate(SECRET_PATTERNS):
        if rx.search(s):
            return i
    return None


def _roots(path):
    """一個根的所有寫法(給的 + realpath),長的在前;空的不收。"""
    out = []
    if not path:
        return out
    for p in (os.path.abspath(path), os.path.realpath(path)):
        p = p.rstrip("/") or "/"
        if p not in out:
            out.append(p)
    return sorted(out, key=len, reverse=True)


def _under(path, roots):
    for r in roots:
        if r == "/":
            return r
        if path == r or path.startswith(r + "/"):
            return r
    return None


def _sub_root(text, roots, tag):
    for r in roots:
        if r == "/":
            continue
        text = re.sub(re.escape(r) + r"(?=/|$|[^A-Za-z0-9._-])", tag, text)
    return text


def split_item(item):
    """一個 inputs 項 → (kind, path 或 None, suffix 或 None)。認不得的種類 raise。"""
    if not isinstance(item, str):
        raise Unexportable("input-not-string")
    kind, sep, rest = item.partition(":")
    if not sep or kind not in KNOWN_KINDS:
        raise Unexportable("input-kind-unknown")
    if kind in _PATH_WITH_SUFFIX:
        path, sep2, suffix = rest.rpartition(":")
        if not sep2 or not path:
            raise Unexportable("input-shape")
        return kind, path, suffix
    if kind in _PATH_BARE:
        if not rest:
            raise Unexportable("input-shape")
        return kind, rest, None
    return kind, None, rest


_BIN_DIR = re.compile(r"/s?bin$")


def _tree_of(row):
    """列沒寫 repo 時(較舊的 aib test 列),從「測試檔本身」那一項推出是在哪棵樹量的。
    10-03 Mac 第二次匯出:aib test 池 2572 列(約 36% 時間)沒有 repo 欄、又是在 ~/worktrees/… 量的 ⇒ 全被判讀家目錄。
    只認路徑結尾剛好是 /<test> 的那一項;推不出來就回 None(照舊保守)。"""
    test = row.get("test")
    if not isinstance(test, str) or not test or test.startswith("/") or ".." in test.split("/"):
        return None
    tail = "/" + test.lstrip("./")
    for item in row.get("inputs") or []:
        if not isinstance(item, str) or item.startswith("argv:"):
            continue
        _kind, path, _suffix = split_item(item)
        if isinstance(path, str) and path.startswith("/") and path.endswith(tail) and len(path) > len(tail):
            return path[:-len(tail)]
    return None


def _clean_path(path, repo_roots, home_roots, kind=""):
    """inputs 裡的一條路徑 → 可以匯出的寫法;綁主機就 raise。"""
    if _AIHUB_SEG.search(path):
        raise Unexportable("cockpit-tmp")
    if not path.startswith("/"):
        return path          # ./x、<tmpN>/x:已經是可攜的寫法
    r = _under(path, repo_roots)
    if r:
        rest = path[len(r):]
        return REPO_TAG + rest
    h = _under(path, home_roots)
    if h:
        # 10-03 實查:「讀家目錄」有很多其實是在 PATH 的目錄裡找程式(~/.local/bin/git 存不存在),那是環境不是測試。
        #   只認「探測存不存在」且在某個 bin 目錄底下的,換成 <HOME>/…;真的開檔、列目錄照樣算綁主機
        if kind in ("probe", "missing") and _BIN_DIR.search(os.path.dirname(path)):
            return HOME_TAG + path[len(h):]
        raise Unexportable("home-read", path)
    return path


def _clean_argv(rest, repo_roots, home_roots, stats):
    try:
        argv = json.loads(rest)
    except ValueError:
        raise Unexportable("argv-unparsable")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise Unexportable("argv-unparsable")
    for a in argv:
        if a.startswith("aihub-") or "/aihub-" in a:
            raise Unexportable("cockpit-tmp")
    if find_secret(rest) is not None:
        stats["redacted_argv"] += 1
        return "argv:" + REDACTED
    out = [_sub_root(_sub_root(a, repo_roots, REPO_TAG), home_roots, HOME_TAG) for a in argv]
    return "argv:" + json.dumps(out, ensure_ascii=False)


def _clean_why(why, repo_roots, home_roots, stats):
    if isinstance(why, str):
        items, as_str = [why], True
    elif isinstance(why, list) and all(isinstance(w, str) for w in why):
        items, as_str = why, False
    else:
        raise Unexportable("why-shape")
    if any(find_secret(w) is not None for w in items):
        stats["redacted_why"] += 1
        return REDACTED if as_str else [REDACTED]
    cleaned = [_sub_root(_sub_root(w, repo_roots, REPO_TAG), home_roots, HOME_TAG) for w in items]
    return cleaned[0] if as_str else cleaned


def export_row(row, repo, home, stats=None, analysis=False):
    """一列 → 可匯出的乾淨列;不能匯出就 raise Unexportable(原因鍵)。純函數(stats 只累加計數)。
    analysis=True:分析模式(見 ANALYSIS_REQUIRED 上方說明)。"""
    if stats is None:
        stats = {"redacted_argv": 0, "redacted_why": 0}
    if not isinstance(row, dict):
        raise Unexportable("not-object")
    if analysis:
        return _export_analysis(row, repo, home, stats)
    for f in REQUIRED_FIELDS:
        if f not in row:
            raise Unexportable("missing-field")
    if not isinstance(row["test"], str) or not row["test"]:
        raise Unexportable("field-type")
    if not isinstance(row["rc"], int) or isinstance(row["rc"], bool):
        raise Unexportable("field-type")
    for f in ("input_fp", "env_fp", "key"):
        if not isinstance(row[f], str):
            raise Unexportable("field-type")
    if not isinstance(row["inputs"], list):
        raise Unexportable("field-type")
    repo_roots = _roots(repo)
    # 10-03 Mac 實跑:同一個倉的沿用池裡有很多工作樹(~/worktrees/…)量的列;只認命令列那個 --repo 的話,
    #   別的工作樹的路徑全被當成「讀了家目錄」(aib test 池 99.7% 被判綁主機)。列自己寫了它是在哪棵樹量的 ⇒ 那棵也算倉
    own = row.get("repo")
    if not (isinstance(own, str) and own.startswith("/")):
        own = _tree_of(row)
    if isinstance(own, str) and own.startswith("/") and own.rstrip("/") not in ("", "/"):
        repo_roots = sorted(set(repo_roots + _roots(own)), key=len, reverse=True)
    home_roots = _roots(home)
    if not repo_roots or not home_roots:
        raise Unexportable("no-roots")

    local = {"redacted_argv": 0, "redacted_why": 0}
    inputs = []
    for item in row["inputs"]:
        kind, path, suffix = split_item(item)
        if kind == "argv":
            inputs.append(_clean_argv(suffix, repo_roots, home_roots, local))
        elif kind == "tool":
            inputs.append(item)
        elif suffix is None:
            inputs.append("%s:%s" % (kind, _clean_path(path, repo_roots, home_roots, kind)))
        else:
            inputs.append("%s:%s:%s" % (kind, _clean_path(path, repo_roots, home_roots, kind), suffix))

    out = {}
    for f in EXPORT_FIELDS:
        if f in row:
            out[f] = row[f]
    out["inputs"] = inputs
    if "why" in row:
        out["why"] = _clean_why(row["why"], repo_roots, home_roots, local)
    out["exported"] = True

    # 收尾閘:清完的整列再掃一次;還有秘密或家目錄/倉的絕對路徑殘留 ⇒ 不匯出
    try:
        blob = json.dumps(out, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raise Unexportable("not-serializable")
    if find_secret(_scan_view(out)) is not None:
        raise Unexportable("secret-residual")
    for r in repo_roots + home_roots:
        if r != "/" and r in blob:
            raise Unexportable("path-residual")
    for k in local:
        stats[k] = stats.get(k, 0) + local[k]
    return out


# 指紋欄是我們自己算的雜湊(十六進位);收尾掃描時拿掉,否則 `"key": "<64 碼>"` 會被「key=長字串」那條規則當成金鑰,
#   每一列都被擋(10-02 Mac 實跑:498 列全擋、匯出 0;治具用的是 k1 這種短值,所以沒抓到)。
#   只拿掉「看起來確實是雜湊」的值;值不像雜湊(例如真的被塞了一把金鑰)⇒ 照樣掃、照樣擋。
FINGERPRINT_FIELDS = ("key", "input_fp", "env_fp", "lock_fp", "repo_id", "host_id")
_HEXISH = re.compile(r"^(?:[0-9a-f]{8,64}|none)$")


_ITEM_HASH_SUFFIX = re.compile(r":[0-9a-f]{8,64}$")


def _scan_view(out):
    view = {k: v for k, v in out.items()
            if not (k in FINGERPRINT_FIELDS and isinstance(v, str) and _HEXISH.match(v))}
    # 10-03 Mac 實跑 256 列被擋:讀過的檔後面接著內容雜湊(open:./x.key:<64 碼>),檔名結尾是 key/token/secret 時,
    #   整串像「key:長字串」。掃描時拿掉每一項尾巴的雜湊(雜湊是我們算的);路徑本身照樣掃
    if isinstance(view.get("inputs"), list):
        view["inputs"] = [_ITEM_HASH_SUFFIX.sub("", i) if isinstance(i, str) and not i.startswith("argv:") else i
                          for i in view["inputs"]]
    return json.dumps(view, ensure_ascii=False, sort_keys=True)


# 10-03 審查第 9 條:outcome 是測試輸出的最後一行,原文可能帶路徑、信箱、主機名、不合規則的金鑰。
#   只留固定格式(Ran N test、OK、FAILED (…=N)、SUMMARY checks=N failed=N、| 與空白);其他整欄換掉
_OUTCOME_OK = re.compile(r"^(?:\s|\||Ran \d+ tests?|OK(?: \([a-z]+=\d+(?:, [a-z]+=\d+)*\))?|"
                         r"FAILED(?: \([a-z]+=\d+(?:, [a-z]+=\d+)*\))?|SUMMARY checks=\d+ failed=\d+)*$")


HOST_BOUND_REASONS = ("home-read", "cockpit-tmp")


def _export_analysis(row, repo, home, stats):
    for f in ANALYSIS_REQUIRED:
        if f not in row:
            raise Unexportable("missing-field")
    if not isinstance(row["test"], str) or not row["test"]:
        raise Unexportable("field-type")
    if not isinstance(row["rc"], int) or isinstance(row["rc"], bool):
        raise Unexportable("field-type")
    base = dict(row)
    base.setdefault("inputs", [])
    for f in ("input_fp", "env_fp", "key"):
        if not isinstance(base.get(f), str):
            base[f] = ""
    host_bound = None
    try:
        out = export_row(base, repo, home, stats)
    except Unexportable as e:
        reason = e.args[0] if e.args else "unknown"
        if reason not in HOST_BOUND_REASONS:
            raise
        host_bound = reason
        where = ""
        if reason == "home-read" and len(e.args) > 1:
            for h in _roots(home):
                if str(e.args[1]).startswith(h + "/"):
                    where = str(e.args[1])[len(h) + 1:].split("/", 1)[0]   # 只給家目錄底下第一層的名字(例 .ez2-aib)
                    break
        out = export_row(dict(base, inputs=[]), repo, home, stats)
        if where and find_secret(where) is None:
            out["host_bound_where"] = where
    for f in ANALYSIS_EXTRA:
        v = row.get(f)
        if isinstance(v, (int, float, bool)) or v is None:
            if f in row:
                out[f] = v
        elif isinstance(v, str):
            if f == "outcome" and not _OUTCOME_OK.match(v):
                out[f] = "<outcome-redacted>"
                continue
            if find_secret(v) is not None:
                raise Unexportable("secret-residual")
            out[f] = v
    for r in _roots(repo) + _roots(home):
        if r != "/" and r in json.dumps(out, ensure_ascii=False):
            raise Unexportable("path-residual")
    out.pop("key", None)
    out["analysis"] = True
    if host_bound:
        out["host_bound"] = host_bound
        out["inputs"] = []
    return out


def export_lines(lines, repo, home, analysis=False):
    """多行 JSONL 文字 → (匯出列清單, 摘要 dict)。"""
    summary = {"read": 0, "exported": 0, "host_bound": 0, "host_bound_reasons": {},
               "malformed": 0, "malformed_reasons": {}, "redacted_argv": 0, "redacted_why": 0}
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        summary["read"] += 1
        try:
            try:
                row = json.loads(line)
            except ValueError:
                raise Unexportable("json")
            stats = {"redacted_argv": 0, "redacted_why": 0}
            rows.append(export_row(row, repo, home, stats, analysis=analysis))
        except Unexportable as e:
            reason = e.args[0] if e.args else "unknown"
            if reason in HOST_BOUND_REASONS:
                summary["host_bound"] += 1
                bucket = summary["host_bound_reasons"]
            else:
                summary["malformed"] += 1
                bucket = summary["malformed_reasons"]
            bucket[reason] = bucket.get(reason, 0) + 1
            continue
        except Exception as e:  # noqa: BLE001 - 看不懂一律不匯出
            summary["malformed"] += 1
            k = "error:%s" % type(e).__name__
            summary["malformed_reasons"][k] = summary["malformed_reasons"].get(k, 0) + 1
            continue
        summary["exported"] += 1
        summary["redacted_argv"] += stats["redacted_argv"]
        summary["redacted_why"] += stats["redacted_why"]
    return rows, summary


def _write_atomic(path, rows):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".row-export-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(jsonl_safe.dumps(r, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(prog="row_export.py")
    sub = ap.add_subparsers(dest="cmd")
    ex = sub.add_parser("export", help="clean reuse-pool rows for export")
    ex.add_argument("--rows", required=True, help="input JSONL")
    ex.add_argument("--out", required=True, help="output JSONL")
    ex.add_argument("--repo", required=True, help="repo root the rows were measured in")
    ex.add_argument("--home", default=None, help="home dir (default: ~)")
    ex.add_argument("--analysis", action="store_true",
                    help="witness/release rows for analysis: keep unmeasured rows, never reusable (no key)")
    args = ap.parse_args(argv)
    if args.cmd != "export":
        ap.print_usage(sys.stderr)
        return 2
    home = args.home or os.path.expanduser("~")
    try:
        with open(args.rows, encoding="utf-8") as fh:
            lines = jsonl_safe.split_lines(fh.read())
    except (OSError, UnicodeDecodeError) as e:
        print(json.dumps({"ok": False, "error": "read", "detail": type(e).__name__}))
        return 2
    rows, summary = export_lines(lines, args.repo, home, analysis=args.analysis)
    summary["analysis"] = bool(args.analysis)
    try:
        _write_atomic(args.out, rows)
    except OSError as e:
        print(json.dumps({"ok": False, "error": "write", "detail": type(e).__name__}))
        return 2
    summary["ok"] = True
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
