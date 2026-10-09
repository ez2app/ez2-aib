#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""E5.5 天花板狀態的**單一判讀入口**(`PKG-CEILING-FLIP` 甲件落地)。

判準正本 `qa3/criteria-ceiling-flip-0827` = `0e9fd6f7`(§更正二 §三 B1-B5 全准)。

本模組做兩件事,兩件都只讀不寫:

1. `ceiling_fields()` —— 產出 `truth_ceiling` 那五個鍵的值。**鍵名一字不改**
   (正本 schema 是 `src/binding/approval-receipt.schema.json`);改的是「值從哪裡來」:
   由寫死的常數改成**向 `owner_adapter` 現查導出**。
   ★ 這不是把 `False` 改成 `True`。owner source 沒設定時,導出的值與舊值逐字相同 ——
   翻的是「從寫死變成可測」,不是「從否變成是」。
2. `read_triple()` / `check()` —— 三處天花板的三元組與一致性判讀(判準 `H2`)。
   分件期間三處必然有中間態,而**中間態的觀測值與「漏翻了」逐字相同**;
   這個入口的存在就是為了讓那個狀態**可以被問出來**,而不是靠「反正下一件會翻」。

★ 本模組是 `src/` 下 `owner_adapter` 的**唯一呼叫端**(判準 `E1` 改判版:呼叫端集合
恰等於 `{src/fleet_identity/ceiling_state.py}`,多一處紅、少一處紅)。
其餘三處天花板都向本模組讀,不各自去接 adapter —— 兩份真相的失效形狀是不一致時沒有格子會紅。

★ 本檔頭不宣稱任何行為成立(判準 `S9`)。承重的是臂表,不是這段話。
"""

import os

import owner_adapter

# ---- 五鍵的鍵名(正本在 schema;這裡只引用,不另立第二份真相)----------------
KEY_OWNER_AUTHORITY_STATUS = "owner_authority_status"
KEY_AUTHORITY_LEVEL = "authority_level"
KEY_FORMAL_ELIGIBLE = "formal_eligible"
KEY_FORMAL_WRITES = "formal_writes"
KEY_MAIN_ADAPTER_CONNECTED = "main_adapter_connected"

FIELD_ORDER = (KEY_OWNER_AUTHORITY_STATUS, KEY_AUTHORITY_LEVEL, KEY_FORMAL_ELIGIBLE,
               KEY_FORMAL_WRITES, KEY_MAIN_ADAPTER_CONNECTED)

# ---- 值:舊值一個字不改(舊收據仍要合法),新值另立 --------------------------
STATUS_UNCONFIGURED = "owner_authority_source_unconfigured"   # 舊值
STATUS_BOUND = "owner_authority_source_bound"                 # 新值:來源可用
STATUS_REJECTED = "owner_authority_source_rejected"           # 新值:有設定但 adapter 不收

LEVEL_CHECKER_HINT = "checker_hint"                           # 舊值
LEVEL_OWNER_ATTESTED = "owner_attested"                       # 新值

# 環境注入點(沒有任何預設落點;讀不到就是「沒設定」,不猜)
ENV_OWNER_SOURCE = "EZ2_OWNER_SOURCE"                # 指向 owner source 描述檔

# 三處天花板(路徑是識別,`H1` 要求不一致時逐字點名)
SITE_DECISION_INBOX = "src/binding/decision_inbox.py"
SITE_PYRAMID = "src/memory/pyramid.py"
SITE_ROUTE_REGISTER = "src/persona_route/route_register.py"
SITES = (SITE_DECISION_INBOX, SITE_PYRAMID, SITE_ROUTE_REGISTER)

# 每一處翻好之後要掛的模組層屬性:一個 callable,回該處**當下宣告的**天花板狀態。
# 沒有這個屬性 = 那一處還沒翻(不是壞了)。
SITE_HOOK = "CEILING_SITE"

_SITE_IMPORT = {
    SITE_DECISION_INBOX: ("binding", "decision_inbox"),
    SITE_PYRAMID: ("memory", "pyramid"),
    SITE_ROUTE_REGISTER: ("persona_route", "route_register"),
}


class CeilingStateError(Exception):
    pass


def _env(env):
    return os.environ if env is None else env


def _read_json(path):
    import json
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def owner_source_spec(env=None):
    """呼叫端明給的 owner source 描述;沒設定回 None(不猜預設落點)。"""
    path = _env(env).get(ENV_OWNER_SOURCE) or ""
    if not path:
        return None
    try:
        spec = _read_json(path)
    except (OSError, ValueError, UnicodeDecodeError) as err:
        raise CeilingStateError("owner source 描述檔讀不到或不合法(%s):%s"
                                % (path, err.__class__.__name__))
    if not isinstance(spec, dict):
        raise CeilingStateError("owner source 描述檔頂層不是物件:%s" % path)
    return spec


def observe_owner(env=None, source=None, adapter=None):
    """向 adapter 現查一次。**只回可斷言的事實,不回看起來合理的預設值。**

    回 dict:`adapter_consulted`(這一次真的問到了 adapter)、`status`、`detail`。
    """
    mod = owner_adapter if adapter is None else adapter
    spec = source if source is not None else owner_source_spec(env)
    if spec is None:
        # 沒有設定來源:adapter 沒有被問(沒東西可問),狀態維持舊值。
        return {"adapter_consulted": False, "status": STATUS_UNCONFIGURED,
                "detail": "owner source 未設定"}
    try:
        verdict = mod.inspect_owner_source(spec)
    except mod.OwnerAdapterError as err:
        return {"adapter_consulted": True, "status": STATUS_REJECTED,
                "detail": getattr(err, "reason", None) or str(err)}
    if verdict.get("status") != mod.STATUS_USABLE:
        return {"adapter_consulted": True, "status": STATUS_REJECTED,
                "detail": "adapter 回了非可用狀態:%r" % (verdict.get("status"),)}
    return {"adapter_consulted": True, "status": STATUS_BOUND,
            "detail": verdict.get("owner_subject_hash")}


def formal_write_count():
    """正式寫入次數。**本批恆為 0,而且刻意不接任何輸入。**

    ★ 舊 schema 的 `formal_writes: const 0` **不是型別宣告,是把「還沒實作」釘死在機器上**。
    第一版我把它放寬成讀一個呼叫端指定的計數檔——那個檔**零生產者、也沒有人驗它**,
    等於用一個環境變數就能讓收據宣稱「我寫過 5 次」,**門開得比原本更大**。
    我放寬它的真正原因是為了讓「五鍵各自一發、雙向跟隨」那個判準格好寫:
    ★★★ **臂要量產品,不是產品去長成臂想要的樣子。**
    (判準 qa3/criteria-ceiling-flip-0827 更正四 §一/§三 裁定收窄。)

    真正的計數要等 native writer 落地時**連同它的生產者一起**進來,不是在這裡先開一個洞。
    """
    return 0


def ceiling_fields(env=None, source=None, adapter=None):
    """五鍵的值,逐鍵由現查導出。鍵名與順序都與 schema 正本相同。"""
    obs = observe_owner(env=env, source=source, adapter=adapter)
    status = obs["status"]
    bound = status == STATUS_BOUND
    return {
        KEY_OWNER_AUTHORITY_STATUS: status,
        KEY_AUTHORITY_LEVEL: LEVEL_OWNER_ATTESTED if bound else LEVEL_CHECKER_HINT,
        KEY_FORMAL_ELIGIBLE: bound,
        KEY_FORMAL_WRITES: formal_write_count(),
        KEY_MAIN_ADAPTER_CONNECTED: bool(obs["adapter_consulted"]),
    }


# ---- H2:三處的三元組與一致性 ------------------------------------------------

def _load_site(site):
    import importlib
    import sys
    pkg, mod = _SITE_IMPORT[site]
    here = os.path.dirname(os.path.abspath(__file__))
    src_root = os.path.dirname(here)
    for path in (os.path.join(src_root, pkg), src_root):
        if path not in sys.path:
            sys.path.insert(0, path)
    return importlib.import_module(mod)


def read_triple(env=None, source=None, adapter=None):
    """三處各回一格:`wired`(已翻並掛上 hook)/`declared`(該處當下宣告的狀態)/
    `error`(讀不到的理由)。**讀不到不等於沒翻**,兩者分開記。"""
    out = []
    for site in SITES:
        row = {"site": site, "wired": False, "declared": None, "error": None}
        try:
            mod = _load_site(site)
        except Exception as err:                      # noqa: BLE001 —— 讀不到就照實記
            row["error"] = "%s: %s" % (err.__class__.__name__, err)
            out.append(row)
            continue
        hook = getattr(mod, SITE_HOOK, None)
        if hook is None:
            out.append(row)                            # 還沒翻
            continue
        if not callable(hook):
            row["error"] = "%s 不是 callable" % SITE_HOOK
            out.append(row)
            continue
        try:
            row["declared"] = hook(env=env, source=source, adapter=adapter)
            row["wired"] = True
        except Exception as err:                       # noqa: BLE001
            row["error"] = "%s: %s" % (err.__class__.__name__, err)
        out.append(row)
    return out


def check(env=None, source=None, adapter=None):
    """一致性判讀。回 `(ok, detail)`;不一致時 detail **逐字點名是哪一處**。

    ★ 「只紅不點名不算數」是判準 `H1` 的原話:分件期間三處狀態必然走過中間態,
    而「有東西不一致」這種訊息與「漏翻了」一樣無法據以行動。
    """
    rows = read_triple(env=env, source=source, adapter=adapter)
    broken = [r for r in rows if r["error"]]
    if broken:
        return False, "讀不到 %d 處:%s" % (
            len(broken), "; ".join("%s(%s)" % (r["site"], r["error"]) for r in broken))
    wired = [r["site"] for r in rows if r["wired"]]
    unwired = [r["site"] for r in rows if not r["wired"]]
    if not unwired:
        return True, "三處皆已翻:%s" % ", ".join(wired)
    if not wired:
        return True, "三處皆未翻(翻轉尚未開始):%s" % ", ".join(unwired)
    return False, ("三處狀態不一致 —— 已翻 %d 處(%s);**未翻**:%s"
                   % (len(wired), ", ".join(wired), ", ".join(unwired)))


def main():
    import json
    import sys
    rows = read_triple()
    ok, detail = check()
    out = {"record": "ceiling-state-v1", "ok": ok, "detail": detail,
           "triple": rows, "fields": ceiling_fields()}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
