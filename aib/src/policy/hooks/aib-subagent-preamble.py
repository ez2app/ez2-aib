#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""子代理契約前言注入(BE,2026-09-03):舊載體 `subagent-guard` 的 aib 正本。

the maintainer 09-03 直令關閉舊載體。舊 hook `subagent-guard.{sh,py}` 掛在 `PreToolUse(Agent)`,
第二方 08-22 已逐條證過它的**兩條 deny 都不可達**(型別白名單恆為空 list;子 AI 配額的讀端 glob
`unbound_*.kids` 與寫端的真角色名目錄永不相交,且 running 全庫是 0)。所以搬過來的不是那兩條,
是它**唯一活著的效果**:把角色庫的子 AI 模版接在每一個子代理提示詞前面。

★ 為什麼這一支不能跟著舊載體一起關掉:它每一次派子代理都在改寫送出去的提示詞
  (實測 `permissionDecision: allow` + `updatedInput.prompt`)。關掉它,子代理不再拿到
  「只讀不寫、查無即說無、宣稱=落檔」那份契約,而**畫面上一模一樣**——行為變了,沒有任何一格會紅。

模版來源是 the maintainer 的 Obsidian 角色庫(不是舊載體的資料):
`~/Desktop/Obsidian/Ez2/P-Ez2 AI專家/P10-專家模板/角色庫/_子AI模版/{探索型,審查型,執行型}.md`
`AIB_SUBAGENT_TEMPLATE_DIR` 可覆寫(治具用)。vault 缺席=不注入(優雅降級),不是錯誤。

契約細節(逐條照舊,改動會改變子代理行為):
- 型別 → 模版:含 `explore`=探索型;`review|plan|qa|judge`=審查型;`general|code|fix|dev`=執行型;
  其餘落探索型。
- 已經帶著前言的提示詞(開頭是「你是主 AI 的」)不重複注入。
- `updatedInput` 必須**保留原 tool_input 的每一個欄位**,只換 `prompt`。舊版曾只回 `{"prompt"}`,
  害 Agent 工具的 schema 驗證失敗(2026-07-11 實測四次)。
- 讀不到輸入 / 任何例外 ⇒ 直接放行(fail-open):這支是 PreToolUse,它壞掉不該擋住派工。
"""
import json
import os
import re
import sys

LIBRARY_ROUTE_KEY = "product.expert.agent_library"
TEMPLATE_SUBDIR = "_子AI模版"   # 角色庫內的結構夾(06-23 命名規範:結構檔不改名);角色庫本身的位置走路由
PREAMBLE_HEAD = "你是主 AI 的"
FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.S)


def template_dir():
    """AIB_SUBAGENT_TEMPLATE_DIR > Vault 根 + 路由契約裡的角色庫 + _子AI模版。
    角色庫位置不寫死(the maintainer 08-27 第二鐵律、09-26 R9);查不到回空字串 ⇒ load_template 讀不到 ⇒ 不注入。"""
    env = os.environ.get("AIB_SUBAGENT_TEMPLATE_DIR")
    if env:
        return env
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "..", "persona_route"))
        sys.path.insert(0, os.path.join(here, "..", "..", "memory"))
        import route_register
        import brain_write_plan          # Vault 根只有一個判準(認 EZ2_VAULT_DIR 與 EZ2_VAULT_ROOT;不存在回 None)
        root = brain_write_plan.vault_root()
        lib = route_register.resolve_route_folder(LIBRARY_ROUTE_KEY, root=str(root) if root else None)
        return os.path.join(str(root), lib, TEMPLATE_SUBDIR) if (lib and root) else ""
    except Exception:
        return ""


def kind_of(subagent_type):
    """子代理型別 → 模版名。判準逐字照舊(改它就是改子代理拿到的契約)。"""
    low = (subagent_type or "").lower()
    if "explore" in low:
        return "探索型"
    if re.search(r"review|plan|qa|judge", low):
        return "審查型"
    if re.search(r"general|code|fix|dev", low):
        return "執行型"
    return "探索型"


def load_template(kind, tdir=None):
    """模版正文(去掉 frontmatter);讀不到回空字串=不注入。"""
    base = tdir or template_dir()
    if not base:
        return ""
    path = os.path.join(base, "%s.md" % kind)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ""
    return FRONTMATTER_RE.sub("", raw).strip()


def decide(payload, tdir=None):
    """回要印出去的 dict,或 None=不改(放行原樣)。純函式,治具直接餵 dict。"""
    if not isinstance(payload, dict):
        return None
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return None
    prompt = ti.get("prompt") or ""
    if prompt.startswith(PREAMBLE_HEAD):
        return None                       # 已帶前言,不疊第二份
    tpl = load_template(kind_of(ti.get("subagent_type")), tdir)
    if not tpl:
        return None                       # vault 缺席=優雅降級
    updated = dict(ti)                    # ★ 保留每一個原欄位,只換 prompt
    updated["prompt"] = tpl + "\n---\n" + prompt
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "allow",
                                   "updatedInput": updated}}


_TR = {}


def T(key, **params):
    """人和 AI 會讀到的字走四語鍵(語系盤查第 16 條);每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名。"""
    try:
        cockpit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cockpit"))
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def main(argv=None):
    try:
        payload = json.load(sys.stdin)
    except Exception:                                          # noqa: BLE001
        return 0                          # 讀不到輸入=放行
    try:
        out = decide(payload)
    except Exception as exc:                                   # noqa: BLE001
        print(T("aib.preamble.self_fault", err=exc), file=sys.stderr)
        return 0
    if out is not None:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
