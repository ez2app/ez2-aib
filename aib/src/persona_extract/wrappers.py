# SPDX-License-Identifier: Apache-2.0
"""外部工具包裝(只包裝呼叫 ez2-ai / ~/.acb 工具,不搬邏輯——艦長 Q2 裁)。

紀律(SOP §九-5 + PLAN §2):
  - subprocess.run(list, shell=False),**不接 pipe**;判定用 returncode,不用輸出猜成敗
  - 工具檔不存在/不可執行 = tools_missing(rc=2 那一級),不得靜默跳過當通過
  - 每次呼叫回 {"cmd", "rc", "stdout", "stderr", "timeout": bool, "missing": bool}
"""

import os
import subprocess

DEFAULT_TIMEOUT_S = 120

_RT = {}


def rt(msg_key, **params):
    """aib persona 執行期的人看字走 i18n(10-15);鍵在 aib.persona.run.*。toolroots / report / 十步清單共用這一支。
    字典讀不到 ⇒ 印鍵名(可見即可抓),不擋指令。"""
    try:
        import sys
        cockpit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cockpit")
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _RT.get(loc)
        if tr is None:
            tr = _RT[loc] = i18n_lib.Translator(loc)
        return tr.t("aib.persona.run." + msg_key, params or None)
    except Exception:  # noqa: BLE001
        return "aib.persona.run." + msg_key


class ToolMissing(Exception):
    pass


def _exists_exec(path):
    return os.path.isfile(path) and os.access(path, os.R_OK)


def run(cmd, timeout_s=DEFAULT_TIMEOUT_S, cwd=None, env=None):
    """跑一個指令(list),回結果 dict;不拋例外(timeout/missing 都收進欄位)。"""
    out = {"cmd": list(cmd), "rc": None, "stdout": "", "stderr": "",
           "timeout": False, "missing": False}
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True,
                           timeout=timeout_s, cwd=cwd, env=env, shell=False)
        out.update(rc=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except FileNotFoundError as err:
        out.update(missing=True, stderr=str(err))
    except subprocess.TimeoutExpired as err:
        out.update(timeout=True, stdout=err.stdout or "", stderr=err.stderr or "")
    return out


def git_truth(repo_dir):
    """SOP §一-A-1 / §九-1:當場取 git 真值(HEAD 與工作樹是否乾淨),不信文件寫死的 hash。"""
    head = run(["git", "-C", repo_dir, "rev-parse", "HEAD"])
    status = run(["git", "-C", repo_dir, "status", "--porcelain"])
    ok = head["rc"] == 0 and status["rc"] == 0
    return {"cmd": head["cmd"], "rc": head["rc"] if head["rc"] is not None else 2,
            "head": head["stdout"].strip() if ok else None,
            "dirty": bool(status["stdout"].strip()) if ok else None,
            "missing": head["missing"] or status["missing"]}


# 判別法用的檔名一律在這裡定義:toolroots 拿這些字串去證明「這個目錄真的是那個 root」。
# ★ 兩邊各寫一次字面值,就是兩個真相源——改了工具名而只改一邊,doctor 會說「齊了」而呼叫時才炸。
BIRTH_CHECK_SCRIPT = "ez2-role-birth-check.js"
EVALS_SCRIPT = "ez2-persona-evals.js"


SESSION_BRIEF_ENV = "EZ2_PERSONA_SESSION_BRIEF_CMD"


def session_brief(cmd_argv, timeout_s=DEFAULT_TIMEOUT_S):
    """SOP §一-B-4 席位簡報:**指令由 operator 指定**(環境變數 $EZ2_PERSONA_SESSION_BRIEF_CMD,
    空白分隔 argv),AIB 不寫死任何 ACB hook 名——本 repo EM3 已裁 ACB 身份 hook 移除,
    tests/test-em3-acb-identity-hooks-removed.py 釘「可執行層不得指名」。未指定 → not_configured
    (誠實記錄,不算通過也不算缺席致 rc=2;SOP 此項是否仍為前提列 open question Q7)。"""
    if not cmd_argv:
        return {"cmd": [], "rc": None, "stdout": "", "stderr": "not configured (%s)" % SESSION_BRIEF_ENV,
                "timeout": False, "missing": False, "not_configured": True}
    return run(list(cmd_argv), timeout_s=timeout_s)


def node_tool(node_bin, tools_root, script_name, args, timeout_s=DEFAULT_TIMEOUT_S):
    """跑 ez2-ai tools/<script_name>(node)。工具檔不存在=missing。"""
    script = os.path.join(tools_root, "tools", script_name)
    cmd = [node_bin, script] + list(args)
    if not _exists_exec(script):
        return {"cmd": cmd, "rc": None, "stdout": "", "stderr": "not found: %s" % script,
                "timeout": False, "missing": True}
    return run(cmd, timeout_s=timeout_s, cwd=tools_root)


def birth_check(node_bin, tools_root, persona, timeout_s=DEFAULT_TIMEOUT_S):
    return node_tool(node_bin, tools_root, BIRTH_CHECK_SCRIPT,
                     ["--name", persona, "--json"], timeout_s=timeout_s)


def evals(node_bin, tools_root, persona, timeout_s=900):
    """含對抗題約 10 分鐘(SOP §一-B-5),timeout 給足。"""
    return node_tool(node_bin, tools_root, EVALS_SCRIPT,
                     ["--name", persona, "--json"], timeout_s=timeout_s)


# ---------------------------------------------------------------- SOP §3.2 watchlist(工具 v4)
# 只包裝呼叫 ez2-ai tools/ez2-persona-watchlist.js,不搬邏輯(艦長 Q2 裁)。
# v4 事實(dev1 L5-00 對表,出處 origin/dev help/main):
#   唯讀 list/verify/repair-plan;突變 add/recur/promote/revalidate/revoke/close/witness 皆要
#   --expected-chain-head H --expected-generation N(CAS);add 另要 --title --trait-type
#   --event-file JSON --expires-at ISO;list --json 回 {generation, chain_head, items, ...};
#   rc:0 接受 / 2 result.accepted===false / 1 例外(--json 時 stderr 有 {ok:false, code})。
WATCHLIST_SCRIPT = "ez2-persona-watchlist.js"
WATCHLIST_READ_CMDS = ("list", "verify")
WATCHLIST_MUTATE_CMDS = ("add", "recur", "promote")
TRAIT_TYPES = ("observed_pattern", "desired_trait", "safety_rule", "recurring_defect")

# 一次工具呼叫的四態。★ 這個分類存在的唯一理由:**timeout 不是拒收**。
#   工具可能已經把事件寫進鏈了,只是我們沒等到它的答案;把這種情形記成「拒收」,
#   下一次重試就會再寫一筆——一個 append-only 的鏈上,重複入帳改不掉。
#   所以「不知道」必須有自己的名字,而且它的處置是**升人對帳,不是重試**。
OUTCOME_ACCEPTED = "accepted"      # 工具 rc=0
OUTCOME_REFUSED = "refused"        # 工具 rc=2(明確拒收:accepted===false)——沒有寫入發生
OUTCOME_TOOL_ERROR = "tool_error"  # 其他非零:工具自己的例外
OUTCOME_UNKNOWN = "unknown"        # timeout / 拿不到 rc —— 可能已寫入,不得重試
OUTCOME_MISSING = "missing"        # 工具檔不在
# 需人工對帳的退出碼(本包裝自己的判定,不是工具的 rc)。
RC_NEEDS_RECONCILE = 4


def call_outcome(result):
    """把 run() 的結果分成上面五態。判 outcome 用它,不要在呼叫端各自看 rc 猜。"""
    if result.get("missing"):
        return OUTCOME_MISSING
    if result.get("timeout") or result.get("rc") is None:
        return OUTCOME_UNKNOWN
    rc = result["rc"]
    if rc == 0:
        return OUTCOME_ACCEPTED
    if rc == 2:
        return OUTCOME_REFUSED
    return OUTCOME_TOOL_ERROR


def watchlist_call(node_bin, tools_root, identity_args, cmd, extra_args,
                   timeout_s=DEFAULT_TIMEOUT_S):
    """回 run() 結果 + parsed(stdout JSON 或 None)+ outcome。identity_args 例 ["--name", NAME]。"""
    import json
    args = list(identity_args) + [cmd] + list(extra_args)
    if "--json" not in args:
        args.append("--json")
    res = node_tool(node_bin, tools_root, WATCHLIST_SCRIPT, args, timeout_s=timeout_s)
    try:
        res["parsed"] = json.loads(res.get("stdout") or "")
    except ValueError:
        res["parsed"] = None
    if not isinstance(res["parsed"], dict):
        res["parsed"] = None
    res["outcome"] = call_outcome(res)
    return res


def watchlist_head(node_bin, tools_root, identity_args, timeout_s=DEFAULT_TIMEOUT_S):
    """CAS 前置:**list 取頭 + verify 確認鏈完整**,兩步都過才給值。

    ★ 為什麼一定要跑 verify(方案 §1 3.2 判準寫的是「先 list+verify」):只 list 拿得到
      chain_head,但拿不到「這條鏈有沒有壞」。跳過 verify 等於**拿一條可能已損毀的鏈的頭
      去做 CAS**——工具會照著我們給的頭接上去,壞掉的那一段就永久留在鏈裡。
      文件寫了要驗、碼沒驗,是本 repo 最貴的那種不一致(handover_probe.py:322 同型)。

    回 dict:{chain_head, generation, why, list, verify}。why 非 None = 前置失敗,
    **此時沒有任何寫入發生**,呼叫端該回 rc=2(不是 4)。"""
    out = {"chain_head": None, "generation": None, "why": None,
           "list": None, "verify": None}
    lst = watchlist_call(node_bin, tools_root, identity_args, "list", [], timeout_s=timeout_s)
    out["list"] = lst
    if lst["outcome"] != OUTCOME_ACCEPTED:
        out["why"] = "list_%s" % lst["outcome"]
        return out
    parsed = lst.get("parsed") or {}
    head, gen = parsed.get("chain_head"), parsed.get("generation")
    if not isinstance(head, str) or not head \
            or not isinstance(gen, int) or isinstance(gen, bool):
        out["why"] = "list_shape"   # 拿不到形狀對的頭 = 不猜、不用 0 頂替
        return out
    ver = watchlist_call(node_bin, tools_root, identity_args, "verify", [], timeout_s=timeout_s)
    out["verify"] = ver
    if ver["outcome"] != OUTCOME_ACCEPTED:
        out["why"] = "verify_%s" % ver["outcome"]
        return out
    out["chain_head"], out["generation"] = head, gen
    return out


def evals_no_bank(result):
    """從 evals 輸出判「無題庫」:JSON 有 cases/bank 計數且為 0 → True;
    看得到計數且 >0 → False;判不出 → None(呼叫端不得記 pass)。"""
    import json
    try:
        doc = json.loads(result.get("stdout") or "")
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    for key in ("cases", "questions", "bank"):
        val = doc.get(key)
        if isinstance(val, list):
            return len(val) == 0
        if isinstance(val, int) and not isinstance(val, bool):
            return val == 0
    for key in ("no_bank", "evals_no_bank"):
        if isinstance(doc.get(key), bool):
            return doc[key]
    return None


# ---------------------------------------------------------------- SOP §七 receipt(verify-only)
# owner-approval CLI 明文 no approve/sign/issue/publish/write(F2 裁):AIB 只做 verify 透傳。
# exit 契約(工具檔頭):0 通過 / 3 壞或過期或重放 / 4 有效撤銷適用 / 5 撤銷狀態未知。
OWNER_APPROVAL_SCRIPT = "ez2-persona-owner-approval.js"


def owner_approval_verify(node_bin, tools_root, receipt, package, persona_state,
                          key_file, key_id, revocation=None, timeout_s=DEFAULT_TIMEOUT_S):
    args = ["verify", "--receipt", receipt, "--package", package,
            "--persona-state", persona_state, "--key-file", key_file,
            "--key-id", key_id, "--json"]
    if revocation:
        args += ["--revocation", revocation]
    return node_tool(node_bin, tools_root, OWNER_APPROVAL_SCRIPT, args, timeout_s=timeout_s)


# SOP §七 正式煉卡十步(AIB 只列印,不執行;每步的唯一寫入器在 ez2-ai)。
def formal_steps():
    """(n, what, cmd) ×10;what 與 cmd 的人看字照當下語系取(aib.persona.run.step.<n>.what / .cmd)。"""
    return tuple((str(n), rt("step.%d.what" % n), rt("step.%d.cmd" % n)) for n in range(1, 11))
