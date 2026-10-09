#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E2.8 許可政策代碼化(draft)。

哨兵(E2.10)咽喉的判定核心:輸入(工具類別/指令文字/目標路徑/執行者檔案域),
輸出三值 allow|deny|escalate,並把每筆判定組成 schemas/event.schema.json 形狀的
帳本事件(escalate=decision.requested 進決策收件匣;allow/deny=system.note 留痕)。

政策語意(大綱 E2.8 原文+fail-closed 補完):
  唯讀類     -> allow(危險樣式/寫入偽裝/看不透構造除外)
  寫入類     -> 目標在執行者檔案域內 allow,域外 escalate
  危險類     -> 一律 escalate(sudo/git push/rm -rf/憑證;掃指令全文+目標路徑,管線夾帶照抓)
  憲法級禁區 -> deny(vault 角色.md 直寫;連人核可的直寫都不合法,合法路只有 writeback-gateway)
  說不清楚   -> fail-closed:自報矛盾(唯讀申報夾寫入樣式)deny;靜態看不透
               (子殼/eval/串鏈/變數重導)escalate;未知類別/缺參/規則檔壞 deny
               (原理 10:守門者壞掉不癱瘓——規則檔不可用時全拒但照樣出事件)。

判定優先序(高到低,先中先判):
  malformed(deny)-> rules 不可用(deny)-> 未知類別(deny)-> deny_rules(deny)
  -> dangerous_rules(escalate)-> 類別語意(readonly:偽裝 deny/看不透 escalate/乾淨 allow;
  write:串鏈 escalate/看不透 escalate/域外重導 escalate/域內 allow/域外 escalate)
  deny_rules 排在 dangerous 之前:憲法級禁區比「上呈候裁」更強,人也不能核可。

真相邊界:
  - src/policy/policy-rules.json 是政策唯一資料正本,本模組只是直譯器;改政策=改規則檔+補測試。
  - 事件組裝/HLC/actor 復用 src/collectors/collector_common.py(E8.3 反重造閘,不另造第二套)。
  - 路徑判定是純字串規則(expanduser+normpath+前綴含界符),不碰檔案系統;symlink 逃逸屬
    執行層(L2 咽喉)責任,本層只做申報內容的靜態判定。POSIX 路徑限定(draft)。
  - 檔案域=允許寫入的路徑集合(對齊 E8.1 §4 詞彙;T0-T3 只是換不同的域集合與規則檔
    參數進來,三檔結構本身不開後門)。actor_domain 收單一路徑或路徑列表。
  - 落帳走 decision_draft()(hlc/integrity/event_id 留給 E1.1 帳本蓋);decision_event()
    的完整形狀只供 CLI/檢視。
  - 事件 actor=system(policy-engine)=判定者本人;被判定的請求以 decision_id 進 subject,
    不把請求方自報身份寫成事件主人(原理 1:不信自報)。

CLI(哨兵接線用):
  python3 policy.py <tool_class> <command> [target_path] [actor_domain]
  stdout=判定事件 JSON 一行;stderr=人讀一句;rc:0=allow 3=escalate 4=deny 2=用法錯
測試跑法(exit code 紀律):python3 tests/test-policy.py >log 2>&1; echo $?
"""
import json
import os
import posixpath
import re
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC / "collectors"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector_common import Hlc, clip, make_event, seal_standalone, sha256_text, system_actor  # noqa: E402
import trust_tiers  # noqa: E402(E8.1 矩陣=trust 前置閘單一真相源)

RULES_PATH = Path(__file__).resolve().parent / "policy-rules.json"

ALLOW, DENY, ESCALATE = "allow", "deny", "escalate"
RC_BY_DECISION = {ALLOW: 0, ESCALATE: 3, DENY: 4}
_KIND_BY_DECISION = {ALLOW: "system.note", DENY: "system.note", ESCALATE: "decision.requested"}

# fd 複製(2>&1、>&2)不是檔案寫入;無害匯流(>/dev/null)不構成寫入偽裝
_FD_DUP_RE = re.compile(r"[0-9]*>&[0-9-]+")
_SINK_RE = re.compile(r">{1,2}\s*/dev/(null|stdout|stderr)\b", re.IGNORECASE)
_REDIR_TARGET_RE = re.compile(r">{1,2}\s*([^\s;|&<>]+)")

# 內建規則的 i18n 理由(pattern 規則的理由住在 policy-rules.json)
_BUILTIN_REASONS = {
    "malformed_input": ("輸入不完整或形狀不對,fail-closed 拒收",
                        "Malformed or incomplete input; fail-closed"),
    "rules_unavailable": ("政策規則檔不可用:全拒但不癱瘓(原理 10)",
                          "Policy rules unavailable: deny-all without crashing (principle 10)"),
    "unknown_tool_class": ("未知工具類別,fail-closed 拒收",
                           "Unknown tool class; fail-closed"),
    "readonly_clean": ("唯讀類且無寫入/危險樣式,自動批",
                       "Read-only class with no write/dangerous patterns; auto-approved"),
    "readonly_write_disguise": ("唯讀申報卻含寫入樣式({rid})——自報矛盾,拒收",
                                "Read-only claim contains write pattern ({rid}); self-report mismatch, denied"),
    "opaque_construct": ("指令含靜態看不透的構造({rid}),上呈人裁",
                         "Command contains statically opaque construct ({rid}); escalate to human"),
    "write_chain_opaque": ("寫入類夾多段指令,無法靜態綁定單一目標,上呈",
                           "Write-class command chains multiple segments; cannot bind to one target; escalate"),
    "write_redirect_out_of_domain": ("指令內重導向目標({t})不在檔案域內或不可靜態解析,上呈",
                                     "In-command redirect target ({t}) is outside the domain or unresolvable; escalate"),
    "write_in_domain": ("寫入目標在執行者檔案域內,自動批",
                        "Write target inside the actor's file domain; auto-approved"),
    "write_out_of_domain": ("寫入目標在執行者檔案域外,上呈",
                            "Write target outside the actor's file domain; escalate"),
}


class PolicyError(Exception):
    """僅供內部標示規則檔問題;evaluate 對外不 raise(守門者壞掉不癱瘓,回 deny)。"""


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _load_rules(rules_path=None):
    """讀+編譯規則檔;任何缺鍵/壞 JSON/壞 regex 都 raise,由 evaluate 收斂成 deny。"""
    path = Path(rules_path) if rules_path else RULES_PATH
    data = json.loads(path.read_text(encoding="utf-8"))

    def rx(pattern):
        return re.compile(pattern, re.IGNORECASE)

    return {
        "classes": {k: v["mode"] for k, v in data["classes"].items()},
        "deny": [(r["id"], rx(r["pattern"]), r) for r in data["deny_rules"]],
        "dangerous": [(r["id"], rx(r["pattern"]), r) for r in data["dangerous_rules"]],
        "write_indicators": [(r["id"], rx(r["pattern"])) for r in data["write_indicator_rules"]],
        "opaque": [(r["id"], rx(r["pattern"])) for r in data["opaque_rules"]],
        "chain": rx(data["chain_pattern"]),
        "sinks": set(data["harmless_sinks"]),
    }


# ---- E2.8 拆段取最嚴(2026-08-22;qa 釘子 tests/test-e28-policy-segments.py)---------------
# 審單(reviews/E2.8-agy.md):`echo ready > $D/ready.txt && chmod 644 $D/ready.txt` 純寫入、目標在域內,
# 卻因命中 chain_pattern 在 write 類一律 escalate——過度升級。修法不是放寬 chain,而是:
#   1. 段界只認**引號外**的 chain 字元(字元集仍由 policy-rules.json 的 chain_pattern 定,不另抄一份);
#      引號內出現 chain 字元、或引號不平衡 → 靜態看不透 → 整句 escalate(write_chain_opaque),
#      絕不把它當兩段各自良性(釘子 S2);
#   2. 每一段各走同一條 write 判定:opaque → 上呈;重導向目標逐一驗域;**沒有重導向的段必須點名申報的
#      target_path**(例:`chmod 644 D/ready.txt`),否則它寫了什麼靜態不可知 → 上呈(write_chain_opaque)
#      ——這條同時保住既有釘子:`echo a > n.txt; cp x.txt y.txt`、`sleep 1 & touch other` 仍上呈;
#   3. 取**最嚴**不取第一段(釘子 S5);deny/dangerous 的整句掃描在更前面,不分段(釘子 S4)。
_QUOTES = ("'", '"')


def split_chain_segments(command, chain_re):
    """(segments|None)。None = 拆不可靠(引號內有段界字元 / 引號不平衡 / 反斜線逃逸在引號外)。
    純函數;段界字元由 chain_re(policy-rules.json chain_pattern)判,本函式不複製字元集。"""
    segs, buf, quote, i = [], [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            elif chain_re.fullmatch(ch):
                return None                      # 引號內的段界字元:看不透
            buf.append(ch)
        elif ch in _QUOTES:
            quote = ch
            buf.append(ch)
        elif ch == "\\":
            return None                          # 逃逸字元:不猜下一個字元的語意
        elif chain_re.fullmatch(ch):
            segs.append("".join(buf)); buf = []
        else:
            buf.append(ch)
        i += 1
    if quote:
        return None                              # 引號不平衡
    segs.append("".join(buf))
    return [x.strip() for x in segs if x.strip()]


def _strip_redirect_noise(command):
    """去掉 fd 複製與無害匯流,剩下的 > 才是真的檔案寫入意圖。"""
    return _SINK_RE.sub(" ", _FD_DUP_RE.sub(" ", command))


def _norm_in_domain(target, domains):
    """(是否在域內, 正規化後目標)。檔案域=允許寫入的路徑集合(E8.1 §4 詞彙),
    單一字串視為單元素集合。相對路徑逐域掛上再 normpath,../ 逃逸自然現形;
    任一域含目標即在域內。"""
    if isinstance(domains, str):
        domains = [domains]
    t_raw = os.path.expanduser(str(target))
    t = None
    for domain in domains:
        d = posixpath.normpath(os.path.expanduser(str(domain)))
        t = t_raw if posixpath.isabs(t_raw) else posixpath.join(d, t_raw)
        t = posixpath.normpath(t)
        if t == d or t.startswith(d.rstrip("/") + "/"):
            return True, t
    return False, t


def _decision(decision, rule_id, zh, en, tool_class, command, target_path, actor_domain,
              normalized_target=None):
    key = _canon([tool_class, command, target_path, actor_domain, decision, rule_id])
    return {
        "decision": decision,
        "rule_id": rule_id,
        "reason": {"zh-TW": zh, "en": en},
        "tool_class": tool_class,
        "command": command,
        "target_path": target_path,
        "actor_domain": actor_domain,
        "normalized_target": normalized_target,
        "decision_id": "pol_" + sha256_text(key)[:16],
    }


def _builtin(decision, rule_id, tool_class, command, target_path, actor_domain, **fmt):
    zh, en = _BUILTIN_REASONS[rule_id]
    return _decision(decision, rule_id, zh.format(**fmt), en.format(**fmt),
                     tool_class, command, target_path, actor_domain,
                     normalized_target=fmt.get("normalized_target"))


def trust_pregate(trust, trust_action):
    """E8.1×E2.8 咽喉接線(正本開放問題 3,08-14):trust 維度前置閘。

    純消費 trust_tiers 矩陣,零政策發明;呼叫端(咽喉)**顯式**宣告本指令
    對應的矩陣動作(trust_action),policy 不從指令文字猜動作——猜=在政策
    引擎裡長第二份對映表,漂移必至。判定→決策對映(逐條有正本出處):
      deny 通道        → DENY(rev2 修訂三:無論誰批准都不合法)
      白名單未列       → DENY(正本 §1:未列即禁)
      verified_gate    → DENY(rev2 §1.1 原文:unattested 提合併/部署/改執法=「否」)
      stale_gate       → ESCALATE(正本:心跳恢復即解除=暫時態,人可裁)
      矩陣 raise(形狀壞)→ DENY(fail-closed,不得把 raise 當 allow)
    回 None=放行交回原判定流。trust 或 trust_action 缺=本閘不介入
    (向後相容鎖:未接線的呼叫端行為零變化)。"""
    if not trust or not trust_action:
        return None
    try:
        verdict = trust_tiers.judge(
            trust.get("tier"), trust_action,
            trust.get("authority_level"), bool(trust.get("stale", False)))
    except trust_tiers.TrustTierError as err:
        return DENY, "trust:malformed", str(err)
    if verdict["allowed"]:
        return None
    channel = verdict["channel"]
    if channel == "stale_gate":
        return ESCALATE, "trust:stale_gate", verdict["reason"]
    return DENY, "trust:" + channel, verdict["reason"]


def evaluate(tool_class, command, target_path=None, actor_domain=None, rules_path=None,
             trust=None, trust_action=None):
    """單筆判定。永遠回 decision dict,不 raise(fail-closed 一律收斂成 deny)。"""
    tc = tool_class.strip() if isinstance(tool_class, str) else ""
    cmd = command if isinstance(command, str) else ""
    args = (tc, cmd, target_path, actor_domain)
    if not tc or not cmd.strip():
        return _builtin(DENY, "malformed_input", *args)

    gate = trust_pregate(trust, trust_action)
    if gate is not None:
        decision, rule_id, reason = gate
        return _decision(decision, rule_id,
                         "trust 閘:%s" % reason, "trust gate: %s" % reason,
                         *args)

    try:
        rules = _load_rules(rules_path)
    except Exception as err:
        d = _builtin(DENY, "rules_unavailable", *args)
        d["reason"]["en"] += " (%s)" % err
        return d

    mode = rules["classes"].get(tc)
    if mode is None:
        return _builtin(DENY, "unknown_tool_class", *args)

    # deny/dangerous 掃「指令全文+目標路徑」:管線夾帶、只藏在 target 的憑證都逃不掉
    scan = cmd + ("\n" + str(target_path) if target_path else "")
    for rid, pattern, raw in rules["deny"]:
        if pattern.search(scan):
            return _decision(DENY, "deny:" + rid, raw["zh-TW"], raw["en"], *args)
    for rid, pattern, raw in rules["dangerous"]:
        if pattern.search(scan):
            return _decision(ESCALATE, "dangerous:" + rid, raw["zh-TW"], raw["en"], *args)

    if mode == "readonly":
        cleaned = _strip_redirect_noise(cmd)
        for rid, pattern in rules["write_indicators"]:
            if pattern.search(cleaned):
                return _builtin(DENY, "readonly_write_disguise", *args, rid=rid)
        for rid, pattern in rules["opaque"]:
            if pattern.search(cmd):
                return _builtin(ESCALATE, "opaque_construct", *args, rid=rid)
        return _builtin(ALLOW, "readonly_clean", *args)

    # mode == "write";檔案域可為單一路徑或路徑集合(E8.1 §4:域=允許寫入的路徑集合)
    if isinstance(actor_domain, str):
        domains = [actor_domain]
    elif isinstance(actor_domain, (list, tuple)):
        domains = list(actor_domain)
    else:
        domains = []
    if (not target_path or not isinstance(target_path, str) or not domains
            or not all(isinstance(x, str) and posixpath.isabs(os.path.expanduser(x))
                       for x in domains)):
        return _builtin(DENY, "malformed_input", *args)
    if rules["chain"].search(cmd):
        segments = split_chain_segments(cmd, rules["chain"])
        if not segments:
            return _builtin(ESCALATE, "write_chain_opaque", *args)
        worst = None
        for seg in segments:
            d = _evaluate_write_segment(seg, target_path, domains, rules, args)
            if worst is None or _SEVERITY[d["decision"]] > _SEVERITY[worst["decision"]]:
                worst = d
            if worst["decision"] == DENY:
                break
        return worst
    return _evaluate_write_segment(cmd, target_path, domains, rules, args,
                                   require_target_mention=False)


_SEVERITY = {ALLOW: 0, ESCALATE: 1, DENY: 2}


def _evaluate_write_segment(seg, target_path, domains, rules, args,
                            require_target_mention=True):
    """單一段的 write 判定(整句不帶段界時 seg=整句;此時不要求點名 target——舊行為不變)。"""
    for rid, pattern in rules["opaque"]:
        if pattern.search(seg):
            return _builtin(ESCALATE, "opaque_construct", *args, rid=rid)
    # 指令內重導向目標逐一驗域:申報目標在域內、真身 > 到域外的夾帶在這裡現形
    redirects = _REDIR_TARGET_RE.findall(_strip_redirect_noise(seg))
    for token in redirects:
        token = token.strip("'\"")
        if "$" in token:
            return _builtin(ESCALATE, "write_redirect_out_of_domain", *args, t=token)
        inside, _ = _norm_in_domain(token, domains)
        if not inside:
            return _builtin(ESCALATE, "write_redirect_out_of_domain", *args, t=token)
    if require_target_mention and not redirects:
        # 這一段沒有重導向,它寫了什麼只有兩種情況算「看得見」:
        #   (a) 點名了申報目標;(b) 段內有絕對路徑 token,且**全部**在域內(`chmod 644 D/ready.txt`)。
        # 相對 token(`cp x.txt y.txt`、`touch other`)不算證據——靜態分不出它寫哪裡 → 上呈。
        mention = str(target_path)
        if not mention or mention not in seg:
            paths = [tok.strip("'\"") for tok in seg.split()
                     if tok.strip("'\"").startswith(("/", "~/", "./", "../"))]
            if not paths or any(not _norm_in_domain(tok, domains)[0] for tok in paths):
                return _builtin(ESCALATE, "write_chain_opaque", *args)
    inside, norm_t = _norm_in_domain(target_path, domains)
    if inside:
        return _builtin(ALLOW, "write_in_domain", *args, normalized_target=norm_t)
    return _builtin(ESCALATE, "write_out_of_domain", *args, normalized_target=norm_t)


def _event_parts(decision):
    """判定 -> (kind, body):escalate=decision.requested 進決策收件匣,allow/deny=system.note。"""
    kind = _KIND_BY_DECISION[decision["decision"]]
    zh = "許可判定 %s(%s):%s「%s」— %s" % (
        decision["decision"], decision["rule_id"], decision["tool_class"],
        clip(decision["command"], 100), decision["reason"]["zh-TW"])
    en = "Policy %s (%s): %s '%s' — %s" % (
        decision["decision"], decision["rule_id"], decision["tool_class"],
        clip(decision["command"], 100), decision["reason"]["en"])
    body = {
        "summary": {"zh-TW": clip(zh, 400), "en": clip(en, 400)},
        "subject": {
            "kind": "decision" if decision["decision"] == ESCALATE else "other",
            "id": decision["decision_id"],
        },
    }
    return kind, body


def decision_draft(decision):
    """判定 -> E1.1 帳本 Ledger.append 可收的草稿(僅 kind/actor/body/causation)。
    hlc/integrity/event_id 是帳本擁有欄位,草稿不得自帶——落帳時由帳本蓋 HLC 與鏈,
    「判定當下」的時間語意由帳本的發行型時鐘承擔(COLLECTORS-SPEC §5.5/開放問題 5:
    政策判定是發行型時戳,不該用回填型 Hlc 自蓋)。哨兵逐筆落帳走這條。"""
    kind, body = _event_parts(decision)
    return {
        "kind": kind,
        "actor": system_actor("policy-engine"),
        "body": body,
        "causation": {"causation_id": None,
                      "correlation_id": "policy:" + decision["decision_id"]},
    }


def decision_event(decision, wall_ms=None, hlc=None):
    """判定 -> schemas/event.schema.json 完整形狀的獨立事件(unsigned_draft、不入鏈),
    供 CLI stdout/檢視用;要落帳請用 decision_draft()。wall_ms 可注入取得確定性
    (同輸入同時戳=同 event_id);連續多筆要嚴格遞增時,呼叫端自帶跨執行不倒退的
    collector_common.PersistentHlc 傳入 hlc 參數。"""
    kind, body = _event_parts(decision)
    stamp = (hlc or Hlc("policy-engine")).stamp(
        wall_ms if wall_ms is not None else int(time.time() * 1000))
    return seal_standalone(
        make_event(kind, stamp, system_actor("policy-engine"), body,
                   correlation_id="policy:" + decision["decision_id"]),
        stamp)


def judge(tool_class, command, target_path=None, actor_domain=None, rules_path=None, wall_ms=None):
    """哨兵一站式入口:回 (decision, event)。"""
    d = evaluate(tool_class, command, target_path, actor_domain, rules_path)
    return d, decision_event(d, wall_ms=wall_ms)


def main(argv):
    if len(argv) < 3:
        print("usage: policy.py <tool_class> <command> [target_path] [actor_domain]",
              file=sys.stderr)
        return 2
    target = argv[3] if len(argv) > 3 and argv[3] else None
    domain = argv[4] if len(argv) > 4 and argv[4] else None
    d, event = judge(argv[1], argv[2], target, domain)
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    print("[policy] %s (%s) %s" % (d["decision"], d["rule_id"], d["reason"]["zh-TW"]),
          file=sys.stderr)
    return RC_BY_DECISION[d["decision"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
