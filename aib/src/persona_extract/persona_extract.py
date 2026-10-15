#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib persona —— B 提取人格程式化 CLI(SOP v3.5 draft 輪可執法;PLAN-persona-extract-b-0818)。

第一刀範圍:draft 輪 + watchlist(包裝)+ 六節輸出;正式煉卡十步只做前提閘 + receipt 驗,
**零寫入人格資產**。本檔=子命令分派;判定在 gates.py(純函式)、外部工具在 wrappers.py、
寫入白名單在 writeguard.py。

  aib persona doctor   [--tools-root DIR]
                       兩個外部 root 的體檢:現在指到哪、夠不夠格、缺哪支檔、本機有哪些候選。
                       **先跑這支**——`tools_missing` 幾乎都是這兩個變數沒設,不是程式壞了。
  aib persona precheck --persona NAME --mode draft|full --tools-root DIR
                       [--repo DIR] [--node-bin node] [--ship-receipt JSON] [--timeout S] [--json]

rc 契約(對齊 rotation:0/2/3 三態不混):
  0 = 前提檢查跑完且拿到要求的模式
  3 = 要求 full 但閘不齊,自動降軌為 draft(輸出 downgraded_because)
  2 = 無法前提檢查:工具缺席/未指定 tools-root(艦長 Q5 裁:不給預設、不猜)/用法錯
`watchlist` 子命令另有一格 **4 = 需人工對帳且不得自動重試**(見該函式 docstring):
外部工具的呼叫會 timeout,而 timeout 不等於拒收——在一條 append-only 的鏈上,
把「不知道」當成「沒寫成」去重試,會多寫一筆刪不掉的紀錄。
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gates  # noqa: E402
import ledgers  # noqa: E402
import report as report_mod  # noqa: E402
import toolroots  # noqa: E402
import wrappers  # noqa: E402

# ★ 這兩個名字留在這裡只為相容既有引用;**真相源在 toolroots**(解析、判別法、候選探測
#   都只有那一份)。本檔任何地方都不得再自己寫 os.environ.get(...) 去拿 root——
#   那會變成第二個解析入口,而 doctor 只會照第一個回答「你設好了」。
TOOLS_ROOT_ENV = toolroots.TOOLS_ROOT_ENV


def _root(spec, flag_value):
    """解析一個外部 root。回 (value, source, err);err 非 None 就是可以直接印給人的訊息。

    ★ 訊息長是故意的:EG.1 那一晚,`rc=2 tools_missing` 這種短訊息被讀成「B lane 壞了」,
      p0 掛了一整晚,而程式從頭到尾都是對的。fail-closed 的閘要自己講清楚
      「我是被設定擋住的,不是我壞了」,否則它每一次正確動作都像一次故障。"""
    res = toolroots.resolve(spec, flag_value)
    if not res["value"]:
        return None, None, toolroots.unset_message(spec, toolroots.discover(spec))
    return res["value"], res["source"], None


def _tool_hint(spec, value, source, script_rel):
    """root 給了但工具檔不在時的訊息(這種更容易被誤讀成產品壞掉)。"""
    return toolroots.tool_missing_message(
        spec, {"source": source, "value": value},
        os.path.join(value, script_rel), toolroots.discover(spec))


# precheck 的每個檢查鍵各自屬於哪個 root、缺的是哪支檔案(給 tools_missing 訊息用)。
_CHECK_TOOL = {
    "birth_check": ("tools_root", os.path.join("tools", wrappers.BIRTH_CHECK_SCRIPT)),
    "evals": ("tools_root", os.path.join("tools", wrappers.EVALS_SCRIPT)),
}


_RT = {}


def _t(msg_key, **params):
    """執行期訊息走 i18n(10-15:英文語系不再印中文錯誤/提示);鍵在 aib.persona.run.*。
    字典讀不到 ⇒ 印鍵名(可見即可抓),不擋指令。"""
    try:
        cockpit = os.path.join(os.path.dirname(HERE), "cockpit")
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


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def die(msg, rc=2):
    print("aib persona: %s" % msg, file=sys.stderr)
    return rc


def _missing_tools_hint(missing, tools_root, tools_src):
    """把「哪幾個檢查缺工具」翻成「該傳什麼、去哪找」。一個鍵一段,不合併成一句廢話。"""
    vals = {"tools_root": (toolroots.TOOLS_ROOT, tools_root, tools_src)}
    out = []
    for key in missing:
        ent = _CHECK_TOOL.get(key)
        if not ent:
            # session_brief 不屬於任何 root:指令由 operator 指定(SOP §一-B-4),
            # 這裡講清楚,免得有人去找一個不存在的 root。
            out.append(_t("session_brief_hint", key=key, env=wrappers.SESSION_BRIEF_ENV))
            continue
        root_key, rel = ent
        spec, value, source = vals[root_key]
        out.append(_tool_hint(spec, value, source, rel))
    return "\n".join(out)


def doctor(args):
    """(a) 兩個 root 做成可發現:一句指令回答「指到哪 / 夠不夠格 / 缺什麼 / 本機有什麼」。

    rc:0 = 兩個 root 都齊備;2 = 有缺(與 tools_missing 同一級,CI 可以直接吃)。
    ★ 它**不會替你設定任何東西**,也不會挑一個候選來用:列候選是讓人選得快,
      不是讓機器代選——代選一次,之後跑錯 checkout 就再也不會有人發現(它會安靜地成功)。"""
    rep = toolroots.report({"tools_root": args.tools_root})
    sys.stdout.write(_dump(rep))
    if rep["ok"]:
        return 0
    for block in toolroots.report_hints(rep):
        print(block, file=sys.stderr)
    return 2


def _load_receipt(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"_unreadable": True}


def precheck(args):
    tools_root, tools_src, err = _root(toolroots.TOOLS_ROOT, args.tools_root)
    if err:
        return die(err)
    if args.mode not in gates.MODES:
        return die(_t("mode_only", modes="|".join(gates.MODES)))
    repo = os.path.abspath(args.repo or os.getcwd())
    t = args.timeout
    checks = {}
    # A-1 git 真值(當場取)
    checks["git_truth"] = wrappers.git_truth(repo)
    # ★ 刀 A(EG.7 第一步):A-2 三記憶閘門已移除。precheck 不再呼叫 ~/.acb/bin,
    #   `a_three_memory_green` 這道閘也隨之消失——**這是放寬,承接者=無**(見交付書面)。
    # A-3 出生檢查:角色.md 不存在=n/a
    bc = wrappers.birth_check(args.node_bin, tools_root, args.persona, timeout_s=t)
    bc["verdict"] = gates.birth_check_verdict(bc["rc"], bc["stdout"]) if not bc["missing"] else "missing"
    checks["birth_check"] = bc
    missing = [k for k, v in checks.items() if v.get("missing")]
    if missing:
        sys.stdout.write(_dump({"persona": args.persona, "requested_mode": args.mode,
                                "checks": _slim(checks), "tools_missing": missing}))
        return die("%s\n%s" % (", ".join(missing),
                               _missing_tools_hint(missing, tools_root, tools_src)))
    gate = {
        "b_birth_check_ok": bc["verdict"] == "ok",
        "c_ship_receipt_ok": gates.ship_receipt_ok(_load_receipt(args.ship_receipt)),
    }
    evals_field = "not_run"  # SOP §一-A:draft 輪不跑 evals,提及一律 not_run
    if args.mode == "full":
        sb_cmd = (args.session_brief_cmd or os.environ.get(wrappers.SESSION_BRIEF_ENV) or "").split()
        sb = wrappers.session_brief(sb_cmd, timeout_s=t)
        checks["session_brief"] = sb
        ev = wrappers.evals(args.node_bin, tools_root, args.persona, timeout_s=max(t, 900))
        if ev["missing"] or sb.get("missing"):
            missing_full = [k for k in ("session_brief", "evals")
                            if checks.get(k, ev).get("missing")]
            sys.stdout.write(_dump({"persona": args.persona, "requested_mode": args.mode,
                                    "checks": _slim(checks), "tools_missing": missing_full}))
            return die("%s\n%s"
                       % (_t("full_tools_missing", missing=", ".join(missing_full)),
                          _missing_tools_hint(missing_full, tools_root, tools_src)))
        ev["no_bank"] = wrappers.evals_no_bank(ev)
        ev["verdict"] = gates.evals_verdict(ev["rc"], ev["no_bank"])
        checks["evals"] = ev
        evals_field = ev["verdict"]  # pass | fail | no_bank | unknown —— no_bank 不是 pass
    decision = gates.decide_mode(args.mode, gate)
    out = {"persona": args.persona, "requested_mode": args.mode, "at_ms": int(time.time() * 1000),
           "repo": repo, "checks": _slim(checks), "gates": gate, "evals": evals_field,
           **decision}
    sys.stdout.write(_dump(out))
    return 3 if decision["downgraded"] else 0


def evidence_add(args):
    """SOP §3.1:證據一筆=lane + day + 可驗指標(+session 稽核欄)。純敘述 pointer 拒(rc=2)。"""
    kind = gates.pointer_kind(args.pointer)
    if kind is None:
        return die(_t("pointer_bad", pointer=repr(args.pointer)))
    if not gates.LANE_RE.match(args.lane or ""):
        return die(_t("lane_bad", lane=repr(args.lane)))
    if not gates.DAY_RE.match(args.day or ""):
        return die(_t("day_bad", day=repr(args.day)))
    entry = {"schema": "aib-persona-evidence/v1", "persona_code": args.persona_code,
             "lane": args.lane, "day": args.day, "pointer": args.pointer.strip(),
             "pointer_kind": kind, "session": args.session or None,
             "note": (args.note or "")[:200], "at_ms": int(time.time() * 1000)}
    try:
        ledgers.append(args.persona_code, ledgers.EVIDENCE_NAME, entry)
    except (ledgers.LedgerError, OSError) as err:
        return die(_t("evidence_write_failed", err=err))
    except Exception as err:  # writeguard.WriteRefused 等
        return die(_t("evidence_write_refused", err=err))
    sys.stdout.write(_dump(entry))
    return 0


def groups(args):
    """SOP §3.1 事件群計數(lane 為鍵)+ 入卡門檻 ≥2 群;帳壞=拒讀 rc=2。"""
    try:
        evs = ledgers.read(args.persona_code, ledgers.EVIDENCE_NAME)
    except ledgers.LedgerError as err:
        return die(_t("evidence_unusable", err=err))
    out = gates.count_groups(evs)
    out.update({"persona_code": args.persona_code, "evidence_count": len(evs),
                "counting_key": "lane", "min_groups_for_card": gates.MIN_GROUPS_FOR_CARD})
    sys.stdout.write(_dump(out))
    return 0


def watchlist(args):
    """SOP §3.2 候觀察帳 = **純包裝** ez2-ai watchlist v4(艦長 Q2 裁:只包不搬)。

    唯讀 list/verify 直接透傳;突變 add/recur/promote 先跑 CAS 前置(list+verify)取
    chain_head/generation,再把工具 rc 原樣透傳,並在候選安全庫 accepted.jsonl 記一行受理紀錄。
    AIB 不重造帳、不解讀候選內容、不替工具判斷該不該收。

    rc 契約(本子命令;dev5 對 dev1 草稿的三處修正,理由見各處 ★):
      0 = 工具接受(rc=0)**且**受理紀錄已落帳
      2 = 工具明確拒收(rc=2)/ 用法錯 / CAS 前置失敗 —— 這三種都**沒有寫入發生**
      4 = 不確定,**需人工對帳且不得自動重試**(timeout、拿不到 rc、或工具收了但受理紀錄沒落)
      其餘非零 = 工具自己的例外 rc,原樣透傳
    機器讀者請看輸出的 `outcome` 欄,不要拿 rc 反推——rc 只有四格,outcome 才分得出是誰的問題。
    """
    tools_root, tools_src, err = _root(toolroots.TOOLS_ROOT, args.tools_root)
    if err:
        return die(err)
    if args.name:
        ident, ident_kind, ident_value = ["--name", args.name], "name", args.name
    elif args.code:
        ident, ident_kind, ident_value = ["--code", args.code], "code", args.code
    else:
        return die(_t("watchlist_need_ident"))
    mutating = args.action in wrappers.WATCHLIST_MUTATE_CMDS
    # ★ 修正一(留痕不是選配):草稿是 `if mutating and args.persona_code` 才落帳,於是
    #   不給 --persona-code 就會**靜默地做完一次突變而不留任何痕跡**。留痕是這支包裝存在
    #   的一半理由(另一半是 CAS 前置),不能由呼叫者選擇性關掉。
    if mutating and not args.persona_code:
        return die(_t("mutate_need_persona_code", cmds="|".join(wrappers.WATCHLIST_MUTATE_CMDS)))
    # ★ 修正二(兩套身份鍵對不起來):工具身份是 --name/--code,受理紀錄記的是 persona_code。
    #   兩者若不是同一個人格,事後對帳會對到別人身上(handover_probe.py 檔頭「兩套命名接不
    #   起來」同型)。能釘的釘死:給了 --code 就必須與 --persona-code 相同;
    #   --name → code 的對照住在工具那邊,AIB 這裡驗不了,所以**誠實標記**成未對照,不假裝。
    if ident_kind == "code" and args.persona_code and args.code != args.persona_code:
        return die(_t("code_mismatch", code=repr(args.code), persona_code=repr(args.persona_code)))
    identity_linked = (ident_kind == "code" and args.persona_code == args.code)

    extra, cas, cas_source = [], None, None
    if mutating:
        # ★ 順序:先驗本次呼叫的形狀,**再**去讀對方的鏈。反過來的話,一個連必填旗標都
        #   還沒齊的呼叫會先對工具跑兩次 list+verify——拿別人的狀態去墊自己的用法錯誤。
        #   形狀閘失敗時「一次工具呼叫都沒發生」也才是能被測試釘住的事實。
        if args.action == "add":
            for flag, val in (("--title", args.title), ("--trait-type", args.trait_type),
                              ("--event-file", args.event_file), ("--expires-at", args.expires_at)):
                if not val:
                    return die(_t("add_needs", flag=flag))
            if args.trait_type not in wrappers.TRAIT_TYPES:
                return die(_t("trait_type_only", types="|".join(wrappers.TRAIT_TYPES)))
            if not os.path.isfile(args.event_file):
                return die(_t("event_file_missing", path=args.event_file))
            after_cas = ["--title", args.title, "--trait-type", args.trait_type,
                         "--event-file", args.event_file, "--expires-at", args.expires_at]
            if args.distinct_reason:
                after_cas += ["--distinct-reason", args.distinct_reason]
        elif args.action == "recur":
            for flag, val in (("--id", args.id), ("--event-file", args.event_file),
                              ("--expires-at", args.expires_at)):
                if not val:
                    return die(_t("recur_needs", flag=flag))
            after_cas = ["--id", args.id, "--event-file", args.event_file,
                         "--expires-at", args.expires_at]
        else:  # promote
            if not args.id:
                return die(_t("promote_needs_id"))
            after_cas = ["--id", args.id]
        if args.expected_chain_head and args.expected_generation is not None:
            cas = {"chain_head": args.expected_chain_head,
                   "generation": args.expected_generation, "why": None}
            cas_source = "caller"
        else:
            cas = wrappers.watchlist_head(args.node_bin, tools_root, ident, timeout_s=args.timeout)
            cas_source = "preflight_list_verify"
            if cas["why"]:
                if (cas.get("list") or {}).get("outcome") == wrappers.OUTCOME_MISSING:
                    return die(_tool_hint(toolroots.TOOLS_ROOT, tools_root, tools_src,
                                          os.path.join("tools", wrappers.WATCHLIST_SCRIPT)))
                # 前置失敗 = 一個字都還沒送進工具 → rc=2(沒有寫入發生),不是 4
                return die(_t("cas_failed", why=cas["why"]))
        extra += ["--expected-chain-head", cas["chain_head"],
                  "--expected-generation", str(cas["generation"])] + after_cas

    res = wrappers.watchlist_call(args.node_bin, tools_root, ident, args.action, extra,
                                  timeout_s=args.timeout)
    outcome = res["outcome"]
    if outcome == wrappers.OUTCOME_MISSING:
        return die(_tool_hint(toolroots.TOOLS_ROOT, tools_root, tools_src,
                              os.path.join("tools", wrappers.WATCHLIST_SCRIPT)))
    parsed = res.get("parsed") or {}
    out = {"action": args.action, "identity_kind": ident_kind, "identity_value": ident_value,
           "persona_code": args.persona_code, "identity_linked": identity_linked,
           "outcome": outcome, "tool_rc": res["rc"], "tool_result": res.get("parsed"),
           "stderr_tail": (res.get("stderr") or "")[-400:],
           "cas": None if not mutating else
                  {"chain_head": cas["chain_head"], "generation": cas["generation"],
                   "source": cas_source}}
    # ★ 修正三(timeout 不是拒收):草稿 `rc = res["rc"] if not None else 2` 把「工具可能已經
    #   收了而我們沒等到答案」記成「拒收」。在 append-only 的鏈上,依此重試會多寫一筆,
    #   而多寫的那筆刪不掉。所以「不知道」走自己的 rc=4,處置是升人對帳、**不重試**。
    if outcome == wrappers.OUTCOME_UNKNOWN:
        rc = wrappers.RC_NEEDS_RECONCILE
        out["needs_reconcile"] = True
        out["do_not_retry"] = _t("do_not_retry_unknown")
    else:
        rc = res["rc"]        # 透傳工具 rc(0 接受 / 2 拒收 / 其他例外),不吞不改
    if mutating:
        after = parsed.get("chain_head") if isinstance(parsed, dict) else None
        entry = {"schema": "aib-persona-accepted/v1", "persona_code": args.persona_code,
                 "action": args.action, "identity_kind": ident_kind,
                 "identity_value": ident_value, "identity_linked": identity_linked,
                 # rc/chain_head_before/generation_before 沿用 cf2bd23 已出貨的欄位名(append-only
                 # 帳改欄名等於讓舊行讀不到);新增的 outcome/cas_source 是加欄不是改欄。
                 "outcome": outcome, "rc": res["rc"], "cas_source": cas_source,
                 # ★ 前值是**我們讀到的**,不是工具實際採用的——稽核時要知道這個差別
                 "chain_head_before": cas["chain_head"], "generation_before": cas["generation"],
                 "chain_head_after": after, "at_ms": int(time.time() * 1000)}
        try:
            ledgers.append(args.persona_code, ledgers.ACCEPTED_NAME, entry)
            out["accepted_ledger"] = "ok"
        except Exception as err:
            # ★ 修正四(留痕失敗不得回綠):草稿把落帳例外塞進輸出欄位後,rc 照樣透傳 0——
            #   於是「工具收了、我們的受理紀錄沒落」是一個**綠色**的結果,而稽核帳有洞。
            #   工具那邊可能已成立,所以這也是「需人工對帳」那一格,不是失敗重跑。
            out["accepted_ledger"] = "failed: %s" % err
            out["needs_reconcile"] = True
            out["do_not_retry"] = _t("do_not_retry_ledger", rc=res["rc"])
            sys.stdout.write(_dump(out))
            return die(_t("accepted_write_failed", outcome=outcome, err=err),
                       wrappers.RC_NEEDS_RECONCILE)
    sys.stdout.write(_dump(out))
    return rc


def report_cmd(args):
    """SOP §六:六節輸出。--sections JSON 由 maker 準備;watchlist_added 取自受理紀錄實數
    (add rc=0 的筆數),不由人填。--inspect 驗既有包(hash/保鮮)。"""
    if args.inspect:
        rc, info = report_mod.inspect(args.inspect, int(time.time() * 1000))
        sys.stdout.write(_dump({"inspect": args.inspect, "rc": rc, **info}))
        return rc
    if not args.sections:
        return die(_t("report_need_sections"))
    try:
        with open(args.sections, "r", encoding="utf-8") as fh:
            sections = json.load(fh)
    except (OSError, ValueError) as err:
        return die(_t("sections_unreadable", err=err))
    try:
        rows = ledgers.read(args.persona_code, ledgers.ACCEPTED_NAME)
    except ledgers.LedgerError as err:
        return die(_t("accepted_unusable", err=err))
    added = sum(1 for r in rows if r.get("action") == "add" and r.get("rc") == 0)
    now_ms = int(time.time() * 1000)
    try:
        doc = report_mod.build(sections, args.persona_code, now_ms, added,
                               self_round=args.self)
        path = report_mod.write_package(doc, os.path.abspath(args.repo or os.getcwd()))
    except report_mod.ReportError as err:
        return die(str(err))
    except Exception as err:  # WriteRefused / OSError(O_EXCL 同日已存在等)
        return die(_t("package_write_failed", err=err))
    sys.stdout.write(report_mod.render_md(doc))
    print("package=%s" % path, file=sys.stderr)
    return 0


def _verify_receipt(args):
    """SOP §七 receipt 閘的**判定核心**:回 (rc, payload_dict_or_None, err_msg_or_None)。

    ★ 為什麼要把判定與列印拆開(dev5 對 dev1 草稿的修正):草稿讓 `checklist --step N`
      直接呼叫 `receipt_cmd(args)`,而後者自己會 `sys.stdout.write` 一份 JSON,接著 checklist
      又寫第二份 —— **一次呼叫的 stdout 上出現兩個 JSON 文件**,`json.load(stdout)` 直接炸。
      這種壞法只在「先驗過再列印」的那條路徑上出現,人眼看終端不容易發現。
      判定與輸出分離之後,每個子命令的 stdout 恰好一份 JSON。

    ★ timeout → rc=2 在這裡是**對的**,不要照搬 watchlist 的 rc=4:owner-approval verify 是
      **唯讀**呼叫(工具明文 no approve/sign/issue/write,F2 裁),逾時不可能留下半個寫入,
      重跑安全且冪等,所以「沒驗過」就是 fail-closed 的 2。watchlist 那邊之所以要第四格,
      是因為那是**突變**呼叫,逾時的真相是「可能已經寫進 append-only 的鏈」。
      同一個字(timeout)在唯讀與突變兩條路徑上是兩件事——這條差別本身要被寫下來,
      否則下一個人會「順手統一」成其中一個。
    """
    tools_root, tools_src, err = _root(toolroots.TOOLS_ROOT, args.tools_root)
    if err:
        return 2, None, err
    for flag, val in (("--receipt", args.receipt), ("--package", args.package),
                      ("--persona-state", args.persona_state),
                      ("--key-file", args.key_file), ("--key-id", args.key_id)):
        if not val:
            return 2, None, _t("receipt_needs", flag=flag)
    res = wrappers.owner_approval_verify(args.node_bin, tools_root, args.receipt, args.package,
                                         args.persona_state, args.key_file, args.key_id,
                                         revocation=args.revocation, timeout_s=args.timeout)
    if res.get("missing"):
        return 2, None, _tool_hint(toolroots.TOOLS_ROOT, tools_root, tools_src,
                                   os.path.join("tools", wrappers.OWNER_APPROVAL_SCRIPT))
    rc = res["rc"] if res["rc"] is not None else 2   # 唯讀呼叫:拿不到 rc = 沒驗過 = 2
    return rc, {"verify_rc": rc, "verify_timeout": bool(res.get("timeout")),
                "tool_stdout_tail": (res.get("stdout") or "")[-400:],
                "stderr_tail": (res.get("stderr") or "")[-400:]}, None


def receipt_cmd(args):
    """SOP §七 前置:owner approval receipt **verify-only** 透傳(0/3/4/5)。AIB 不產不代按(F2)。"""
    rc, payload, err = _verify_receipt(args)
    if err:
        return die(err)
    sys.stdout.write(_dump(payload))
    return rc  # 透傳工具 exit(0 過/3 壞/4 撤銷/5 撤銷未知),不吞不改


def checklist(args):
    """SOP §七 十步 **唯讀列印**(rc 0);要看「某一步該跑什麼」必須先過 receipt 閘:
    --step N 需同時給 verify 五件且 verify rc==0,否則 rc=2——無 receipt 呼叫十步任一步=拒。
    本命令**永不執行**任何一步(寫入器在 ez2-ai;writeguard 同時把 AIB 側寫入面關死)。"""
    if args.step is None:
        sys.stdout.write(_dump({"steps": [{"n": n, "what": w, "cmd": c}
                                          for n, w, c in wrappers.formal_steps()],
                                "note": _t("checklist_note")}))
        return 0
    step = next((s for s in wrappers.formal_steps() if s[0] == str(args.step)), None)
    if step is None:
        return die(_t("step_only", step=repr(args.step)))
    vrc, payload, err = _verify_receipt(args)
    if err:
        return die(_t("step_refused", step=args.step, err=err))
    if vrc != 0:
        sys.stdout.write(_dump({"step": step[0], "receipt_verified": False, "verify": payload,
                                "refused": _t("step_refused_no_receipt")}))
        print("aib persona: %s" % _t("step_refused_verify", step=args.step, rc=vrc), file=sys.stderr)
        return 2
    sys.stdout.write(_dump({"step": step[0], "what": step[1], "cmd": step[2],
                            "receipt_verified": True, "verify": payload,
                            "note": _t("step_note")}))
    return 0


def _slim(checks):
    """輸出瘦身:stdout/stderr 只留尾 400 字(候選文字/敏感內容不該整段回流到終端)。"""
    slim = {}
    for k, v in checks.items():
        d = dict(v)
        for f in ("stdout", "stderr"):
            if isinstance(d.get(f), str) and len(d[f]) > 400:
                d[f] = "…" + d[f][-400:]
        slim[k] = d
    return slim


def _help_t():
    """argparse 說明走 i18n(10-15:英文語系不再印整段中文、不露內部代號)。"""
    cockpit = os.path.join(os.path.dirname(HERE), "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    import i18n_lib
    loc = i18n_lib.resolve_locale()

    def T(key, **params):
        return i18n_lib.t("aib.persona.help." + key, params or None, locale=loc)
    return T


def main(argv=None):
    T = _help_t()
    ap = argparse.ArgumentParser(prog="aib persona", description=T("description"))
    sub = ap.add_subparsers(dest="command")
    dr = sub.add_parser("doctor", help=T("doctor"), description=T("doctor"))
    dr.add_argument("--tools-root", help=T("tools_root", env=TOOLS_ROOT_ENV))
    p = sub.add_parser("precheck", help=T("precheck"), description=T("precheck"))
    p.add_argument("--persona", required=True, help=T("persona_name"))
    p.add_argument("--mode", default="draft", choices=list(gates.MODES), help=T("mode"))
    p.add_argument("--tools-root", help=T("tools_root", env=TOOLS_ROOT_ENV))
    p.add_argument("--repo", help=T("repo"))
    p.add_argument("--node-bin", default="node", help=T("node_bin"))
    p.add_argument("--ship-receipt", help=T("ship_receipt"))
    p.add_argument("--session-brief-cmd", help=T("session_brief_cmd", env=wrappers.SESSION_BRIEF_ENV))
    p.add_argument("--timeout", type=int, default=wrappers.DEFAULT_TIMEOUT_S, help=T("timeout"))
    p.add_argument("--json", action="store_true", help=T("json_compat"))
    e = sub.add_parser("evidence", help=T("evidence"), description=T("evidence"))
    e.add_argument("action", choices=["add"])
    e.add_argument("--persona-code", required=True, help=T("persona_code"))
    e.add_argument("--lane", required=True, help=T("lane"))
    e.add_argument("--day", required=True, help=T("day"))
    e.add_argument("--pointer", required=True, help=T("pointer"))
    e.add_argument("--session", help=T("session"))
    e.add_argument("--note", help=T("note"))
    g = sub.add_parser("groups", help=T("groups"), description=T("groups"))
    g.add_argument("--persona-code", required=True, help=T("persona_code"))
    w = sub.add_parser("watchlist", help=T("watchlist"), description=T("watchlist"))
    w.add_argument("action", choices=list(wrappers.WATCHLIST_READ_CMDS)
                   + list(wrappers.WATCHLIST_MUTATE_CMDS))
    w.add_argument("--name", help=T("w_name"))
    w.add_argument("--code", help=T("w_code"))
    w.add_argument("--persona-code", help=T("w_persona_code"))
    w.add_argument("--tools-root", help=T("tools_root", env=TOOLS_ROOT_ENV))
    w.add_argument("--node-bin", default="node", help=T("node_bin"))
    w.add_argument("--expected-chain-head", help=T("w_chain_head"))
    w.add_argument("--expected-generation", type=int, help=T("w_generation"))
    w.add_argument("--title", help=T("w_title"))
    w.add_argument("--trait-type", help=T("w_trait_type", types="|".join(wrappers.TRAIT_TYPES)))
    w.add_argument("--event-file", help=T("w_event_file"))
    w.add_argument("--expires-at", help=T("w_expires_at"))
    w.add_argument("--distinct-reason", help=T("w_distinct_reason"))
    w.add_argument("--id", help=T("w_id"))
    w.add_argument("--timeout", type=int, default=wrappers.DEFAULT_TIMEOUT_S, help=T("timeout"))
    r = sub.add_parser("report", help=T("report"), description=T("report"))
    r.add_argument("--persona-code", required=True, help=T("persona_code"))
    r.add_argument("--sections", help=T("r_sections"))
    r.add_argument("--repo", help=T("r_repo"))
    r.add_argument("--self", action="store_true", help=T("r_self"))
    r.add_argument("--inspect", help=T("r_inspect"))
    rv = sub.add_parser("receipt", help=T("receipt"), description=T("receipt"))
    rv.add_argument("action", choices=["verify"])
    ck = sub.add_parser("checklist", help=T("checklist"), description=T("checklist"))
    ck.add_argument("--step", type=int, help=T("c_step"))
    for sp in (rv, ck):
        sp.add_argument("--receipt", help=T("c_receipt")); sp.add_argument("--package", help=T("c_package"))
        sp.add_argument("--persona-state", help=T("c_persona_state"))
        sp.add_argument("--key-file", help=T("c_key_file")); sp.add_argument("--key-id", help=T("c_key_id"))
        sp.add_argument("--revocation", help=T("c_revocation"))
        sp.add_argument("--tools-root", help=T("tools_root", env=TOOLS_ROOT_ENV))
        sp.add_argument("--node-bin", default="node", help=T("node_bin"))
        sp.add_argument("--timeout", type=int, default=wrappers.DEFAULT_TIMEOUT_S, help=T("timeout"))
    args = ap.parse_args(argv)
    if args.command == "doctor":
        return doctor(args)
    if args.command == "receipt":
        return receipt_cmd(args)
    if args.command == "checklist":
        return checklist(args)
    if args.command == "report":
        return report_cmd(args)
    if args.command == "watchlist":
        return watchlist(args)
    if args.command == "precheck":
        return precheck(args)
    if args.command == "evidence":
        return evidence_add(args)
    if args.command == "groups":
        return groups(args)
    if args.command == "watchlist":
        return watchlist(args)
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
