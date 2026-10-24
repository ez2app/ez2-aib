# SPDX-License-Identifier: Apache-2.0
"""兩個外部 root 的單一解析入口與可發現性(EG.1 改寫:B lane 可執行化)。

★ 這支存在的理由,是 EG.1 那一晚:precheck 回 `rc=2 tools_missing`,被讀成
  「B lane 的程式壞了」而掛 p0 一整晚——實際上程式行為完全正確,缺的只是兩個
  沒人知道要設的環境變數。**fail-closed 的錯誤訊息會被讀成「它守護的東西壞了」**:
  一個只說「我拒絕了」的閘,和一個真的壞掉的功能,在讀的人眼裡長得一模一樣。
  所以本檔規定每一則 tools_missing 都得自己講三件事:
    (1) 這是設定缺席,不是程式故障   (2) 該傳什麼   (3) 去哪找

★ 「不給預設、不猜」(艦長 Q5 裁)仍然成立,而且與可發現性不衝突:
  doctor **只列候選並附證據,絕不自動採用**。找得到 ≠ 替你決定;一旦替人決定,
  跑錯 checkout 這件事就不會有人發現——它會安靜地成功。

★ 單一真相源:解析(flag→env→無)、判別法(哪個檔案存在才算數)、候選探測
  三者都只有這一份。呼叫端不得自己再寫一次 `os.environ.get(...)` 或自己拼路徑。
"""

import glob
import os

import wrappers
from wrappers import rt

TOOLS_ROOT_ENV = "EZ2_PERSONA_TOOLS_ROOT"
DOCTOR_CMD = "aib persona doctor"

# 探測候選的上限。★ 截斷一定要講出來(truncated 欄位),不然「只列了 8 個」會被讀成「只有 8 個」。
MAX_CANDIDATES = 8


class RootSpec(object):
    """一個外部 root 的規格。markers[0] = 判別法(拿它證明「這個目錄真的是那個 root」)。"""

    def __init__(self, key, env, flag, what, markers, candidate_globs, gotcha=None,
                 sample=None, sample_provenance=None):
        self.key = key
        self.env = env
        self.flag = flag
        # what / gotcha / sample_provenance 給的是 i18n 鍵名(aib.persona.run.<鍵>),讀的時候才照當下語系取字。
        self._what = what
        self.markers = tuple(markers)
        self.candidate_globs = tuple(candidate_globs)
        self._gotcha = gotcha
        # ★ sample 是**證據樣本不是預設值**:它只被印出來給人看,任何解析路徑都不得讀它。
        #   (差別在於:預設值會在沒人注意時被用掉;樣本要有人親手抄過去才會生效。)
        self.sample = sample
        self._sample_provenance = sample_provenance

    @property
    def what(self):
        return rt(self._what)

    @property
    def gotcha(self):
        return rt(self._gotcha) if self._gotcha else None

    @property
    def sample_provenance(self):
        return rt(self._sample_provenance) if self._sample_provenance else None


TOOLS_ROOT = RootSpec(
    key="tools_root", env=TOOLS_ROOT_ENV, flag="--tools-root",
    what="root.tools_what",
    markers=(os.path.join("tools", wrappers.BIRTH_CHECK_SCRIPT),
             os.path.join("tools", wrappers.EVALS_SCRIPT),
             os.path.join("tools", wrappers.WATCHLIST_SCRIPT),
             os.path.join("tools", wrappers.OWNER_APPROVAL_SCRIPT)),
    candidate_globs=("~/ez2-ai", "~/worktrees/ez2ai-*", "~/Desktop/ez2-ai-*"),
    # ★ 08-18 現查:最像的那個目錄不是它。~/ez2-ai 的 core.bare=true,tools/ 裡沒有那些 .js。
    #    猜路徑的人會第一個試它、第一個失敗,然後把失敗歸給程式。
    gotcha="root.tools_gotcha",
    sample="~/Desktop/ez2-ai-converge-main",
    sample_provenance="root.tools_sample_provenance")

# ★ 刀 A(EG.7 第一步,the maintainer 裁):舊載體 bin 這個 root 已移除——precheck 不再需要它,
#   對應的命令列旗標與環境變數一併消失。舊載體本身 10-25 已整個退役。

ROOTS = (TOOLS_ROOT,)
BY_KEY = {r.key: r for r in ROOTS}


def resolve(spec, flag_value=None, environ=None):
    """flag → env → 無。回 {key, value, source, env, flag};source ∈ flag|env|None。

    ★ 沒有第四種來源。不落預設、不從 cwd 猜、不從別的 root 推另一個 root。"""
    env = os.environ if environ is None else environ
    if flag_value:
        return {"key": spec.key, "value": flag_value, "source": "flag",
                "env": spec.env, "flag": spec.flag}
    from_env = env.get(spec.env)
    if from_env:
        return {"key": spec.key, "value": from_env, "source": "env",
                "env": spec.env, "flag": spec.flag}
    return {"key": spec.key, "value": None, "source": None,
            "env": spec.env, "flag": spec.flag}


def inspect(spec, path):
    """檢查一個目錄。回 {path, exists, markers[...], is_root, ok, missing[...]}。

    兩個判準刻意分開,因為它們回答的是不同問題:
      is_root = 判別法檔案(markers[0])在不在 —— 「這個目錄是不是那一型的東西」
      ok      = 這支 CLI 真的會呼叫的工具**全部**都在 —— 「我現在跑得動嗎」
    ★ 合成一個會說謊:半套 checkout(是 root、但缺 watchlist)如果被判 ok,
      使用者要等到真的跑那個子命令才知道;判 not-a-root 又會叫他去換一個其實對的目錄。
    ★ 兩者都不是「目錄名字像不像」——名字像是 ~/ez2-ai 那個坑的全部內容。"""
    if not path:
        return {"path": None, "exists": False, "markers": [], "is_root": False, "ok": False,
                "missing": list(spec.markers)}
    full = os.path.abspath(os.path.expanduser(path))
    marks = []
    for rel in spec.markers:
        p = os.path.join(full, rel)
        marks.append({"rel": rel, "path": p, "present": os.path.isfile(p)})
    missing = [m["rel"] for m in marks if not m["present"]]
    exists = os.path.isdir(full)
    return {"path": full, "exists": exists, "markers": marks,
            "is_root": exists and marks[0]["present"],
            "ok": exists and not missing, "missing": missing}


def discover(spec, limit=MAX_CANDIDATES):
    """列出本機**看起來像**這個 root 的目錄,每個都附證據(哪些判別檔在/不在)。

    ★ 只回報,不回傳「該用哪個」。合格的排前面,但排第一 ≠ 被選中——選哪個 checkout
      是人的判斷(不同 worktree 的工具版本不同),機器替他選會安靜地跑錯版本。"""
    seen, out = set(), []
    for pattern in spec.candidate_globs:
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            full = os.path.abspath(path)
            if full in seen or not os.path.isdir(full):
                continue
            seen.add(full)
            info = inspect(spec, full)
            # 候選清單用 is_root 當「命中」:列的是「這型的目錄有哪些」,
            # 缺哪支工具另外列(missing),讓人自己看得到半套的那個。
            out.append({"path": full, "ok": info["is_root"], "complete": info["ok"],
                        "missing": info["missing"]})
    out.sort(key=lambda c: (not c["complete"], not c["ok"], c["path"]))
    truncated = max(0, len(out) - limit)
    return {"candidates": out[:limit], "truncated": truncated, "scanned": len(out),
            "globs": list(spec.candidate_globs)}


def _how_to_pass(spec):
    return rt("root.how_to_pass", flag=spec.flag, env=spec.env)


def _where_to_look(spec, found=None):
    lines = ["  " + rt("root.where_to_look", cmd=DOCTOR_CMD)]
    if spec.gotcha:
        lines.append("  " + rt("root.gotcha_line", gotcha=spec.gotcha))
    if found:
        ok = [c["path"] for c in found.get("candidates", []) if c["ok"]]
        if ok:
            lines.append("  " + rt("root.found_ok", paths=rt("root.list_sep").join(ok[:3])))
    if spec.sample:
        lines.append("  " + rt("root.sample_line", env=spec.env, sample=spec.sample,
                                why=spec.sample_provenance))
    return lines


def unset_message(spec, found=None):
    """未指定 root 的訊息。★ 第一句一定是「設定缺席,不是程式故障」——EG.1 掛 p0 一整晚買來的。"""
    lines = ["tools_missing: " + rt("root.unset_head", what=spec.what),
             "  " + rt("root.pass_line", how=_how_to_pass(spec)),
             "  " + rt("root.marker_line", marker=spec.markers[0])]
    lines += _where_to_look(spec, found)
    lines.append("  " + rt("root.why_no_default"))
    return "\n".join(lines)


def tool_missing_message(spec, resolved, script_path, found=None):
    """root 給了、但工具檔不在的訊息(比「未指定」更常被誤讀成產品壞掉)。"""
    src = {"flag": rt("root.src_flag", flag=spec.flag), "env": rt("root.src_env", env=spec.env)}.get(
        resolved.get("source"), rt("root.src_unknown"))
    partial = inspect(spec, resolved.get("value"))["is_root"]
    lines = ["tools_missing: " + rt("root.tool_missing_head", path=script_path),
             "  " + rt("root.current_line", what=spec.what, value=resolved.get("value"), src=src),
             "  " + (rt("root.dir_partial", marker=spec.markers[0]) if partial
                     else rt("root.dir_not_root", marker=spec.markers[0])),
             "  " + rt("root.pass_line", how=_how_to_pass(spec))]
    lines += _where_to_look(spec, found)
    return "\n".join(lines)


def report(flag_values=None, environ=None, discover_candidates=True):
    """doctor 的資料面:兩個 root 各自的解析結果、檢查結果與候選。ok=兩個都齊。"""
    flag_values = flag_values or {}
    roots = {}
    for spec in ROOTS:
        res = resolve(spec, flag_values.get(spec.key), environ=environ)
        info = inspect(spec, res["value"])
        entry = {"env": spec.env, "flag": spec.flag, "what": spec.what,
                 "source": res["source"], "value": res["value"],
                 "marker": spec.markers[0], "gotcha": spec.gotcha,
                 "exists": info["exists"], "missing_files": info["missing"],
                 "is_root": info["is_root"], "ok": info["ok"]}
        if not entry["ok"] and discover_candidates:
            entry["discovery"] = discover(spec)
        roots[spec.key] = entry
    return {"schema": "aib-persona-doctor/v1", "roots": roots,
            "ok": all(r["ok"] for r in roots.values())}


def report_hints(rep):
    """把 report 轉成人看的行(只針對沒過的 root;過了的不用講廢話)。"""
    lines = []
    for spec in ROOTS:
        entry = rep["roots"][spec.key]
        if entry["ok"]:
            continue
        found = entry.get("discovery")
        if entry["source"] is None:
            lines.append(unset_message(spec, found))
        elif not entry["exists"]:
            lines.append("tools_missing: %s\n  %s"
                         % (rt("root.dir_missing", value=entry["value"],
                               src=rt("root.src_env", env=spec.env) if entry["source"] == "env"
                               else rt("root.src_flag", flag=spec.flag)),
                            rt("root.pass_line", how=_how_to_pass(spec))))
            lines += _where_to_look(spec, found)
        else:
            lines.append(tool_missing_message(
                spec, {"source": entry["source"], "value": entry["value"]},
                os.path.join(entry["value"], entry["missing_files"][0]), found))
    return lines
