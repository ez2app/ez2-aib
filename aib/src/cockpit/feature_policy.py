#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""功能區各件的獨立開關 + 授權分級(第 47 項;the maintainer 2026-09-12 02:0x 令)。

**一句話**:功能區裡的每一件事各有一個開關,預設關;關掉等於不存在
——不畫、不佔高、不留可點擊的區域。

形狀原本**取自** `src/cockpit/dm_queue.py` 的 `policy` / `admit()`:

  開關檔住艙家   有艙家用艙家;沒有時只認明寫的狀態根,永不退回 `~/.ez2-aib`
  檔壞掉往關倒   `policy_unreadable` 一律擋,不 fail-open
  授權是硬前置   要 Plus 授權才准開(`EZ2_LICENSE` plan ∈ advanced/plus/max/ultra,見
                 `license_plan.ADVANCED_PLANS`);開關開著也擋得住。
                 the maintainer 裁:「Plus = 啟用 Ez2 AIB 功能區 Plus 的授權」—— 對人講一律講 Plus,不講 advanced/進階版。

★★ 第 47b 項(2026-09-13,a seat):**「開關檔住哪」與「准不准」各只剩一個函式**
—— `cabin_policy_path()` 與 `judge()`,都住本檔;`dm_queue` 的 `policy_path` /
`load_policy` / `admit` 直接呼叫它們,不再自己判。

住本檔而不是住 `dm_queue` 的理由:`dm_queue` 有 1400 行、管的是佇列的排序與投遞;
本檔只回答「准不准開」。判斷住在只做判斷的那一支,別的功能要賣時 import 的是這支小檔,
不必把整個佇列拖進來。
依賴方向:`dm_queue` → 本檔,單向。**本檔不得 import `dm_queue`**(會成環);
本檔自己只依賴 `atomic_json` / `license_plan` / `seats_dir_lib`(與用到才載的 `i18n_lib`),
四支都不回頭 import 本檔。守這條的是 `tests/test-feature-policy-47.py` 的 t19。

a seat 2026-09-12 比出的三處不同(原本寫在這裡的「三條現在就不一樣」那段以本段取代),
各自這樣收:

  a. 沒有艙家:採 `dm_queue` 原本那條 —— 明寫的 `EZ2_AIB_STATE_ROOT` / `EZ2_AIB_HOME`
     也算一個地方(那是呼叫端自己指的根);兩者都沒有才回 None。
     理由:`tests/test-dm-sort-queue.py` 只設狀態根就寫佇列開關,改用本檔原本的嚴格版
     它就紅;而「治具不得寫到真艙去」要擋的是**退回預設的 `~/.ez2-aib`**,這一條兩版都擋。
  b. 「沒艙家」與「檔不在」分兩態:採本檔 —— 兩者修法不同(殼沒帶變數 vs 還沒設過開關),
     併成一態會讓兩個問題在診斷上同形。
  c. 順序:採本檔 —— 沒艙家 → 壞檔 → 授權 → 關著。**基本版 + 壞檔**要先說壞檔,
     不然「檔壞了」被「授權不夠」蓋住,而兩件的修法完全不同。

★ 單源的是**判斷**,不是檔:開關仍是兩份檔(`feature-policy.json` 管功能區三件、
  `dm-queue-policy.json` 管佇列)。派工單原本寫「佇列併成登記簿第四件」,沒有照做:
  活艙 a801 與 4c24 的 `dm-queue-policy.json` 在 2026-09-13 10:1x 都是開著的,
  搬檔或改 schema 會讓佇列**靜靜變關**;而且登記簿那三件是功能區開關列畫得出來的東西,
  佇列不是。
★ 守這件事的是 `tests/test-feature-policy-47.py` 的 t18(同一組輸入餵兩支 `admit`
  比理由字面)與它的刀(把 `dm_queue` 任一處改回自己判,t18 必須紅)。

★ 兩件事不要混:
  **方案**(`basic` / `advanced`)是**閘** —— 決定准不准開。
  **檔次**(`Plus` / `Max` / `Ultra`)是**顯示** —— 框上那個字樣,不是閘。
  今天沒有任何一件功能是「Ultra 才有」的。下一個人看到 Ultra 兩個字很容易
  以為它開得比 Plus 多,所以這句話寫在這裡而不是寫在別的文件裡。

★ 本模組**不知道**任何一件功能長什麼樣,也不會去畫它。它只回答
  「這件事在這個艙、這個授權下,准不准開」。**關掉之後畫面有沒有真的不畫,
  這支檔量不到** —— 那是第 39 項接進渲染器時要自己帶臂的。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BOOT = _HERE.parent / "bootstrap"
for _p in (_HERE, _BOOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import atomic_json as AJ  # noqa: E402
import license_plan as LP  # noqa: E402
import seats_dir_lib  # noqa: E402

POLICY_SCHEMA = "aib-feature-policy/v1"

# 登記簿:功能區裡「各自一個開關」的那幾件。順序=開關列的顯示順序。
FEATURES = ("task_zone", "global_box", "queue_stale_mark", "dialog_panel", "pending_panel")

# 字樣走四語字典(`i18n/*.json` 的 `aib.feature.*`)。模組內這一份是**保底**,
# 不是第二份正本:這支檔在閘的路徑上,字典讀不到時它仍然要答得出「准不准開」,
# 所以不能讓字典壞掉變成閘壞掉。保底字面與 `zh-TW.json` 同,對不上時以字典為準。
_FALLBACK_LABEL = {
    "task_zone": "AI任務區頁簽",
    "global_box": "全域訊息框",
    "queue_stale_mark": "佇列過時標記",
    "dialog_panel": "AIB 私訊對話區",
    "pending_panel": "AIB 訊息待審區",
}
_FALLBACK_OFF_MEANS = "關掉即不畫、不佔高、不留 hit"
_FALLBACK_UNREADABLE = "開關檔讀不懂,全部當關"


def _t(key, fallback, locale=None):
    """查字典;查不到回 fallback。

    ★ `i18n_lib` 查不到時回的是 `!!key!!`(它刻意 fail-loud,不回空字串),
    **不是** key 本身 —— 把判準寫成 `text == key` 會讓「字典沒有這個鍵」漏掉,
    畫面上就出現 `!!aib.feature.xxx!!`。這一格是本包的臂 t0 抓到的。
    """
    try:
        import i18n_lib
        text = i18n_lib.t(key, None,
                          locale=locale or i18n_lib.resolve_locale())
    except Exception:                      # noqa: BLE001 字典壞掉不得癱瘓閘
        return fallback
    if not isinstance(text, str) or not text:
        return fallback
    if text == key or (text.startswith("!!") and text.endswith("!!")):
        return fallback
    return text


def feature_label(name, locale=None):
    """功能的中文(或當地語)名。不認得的名字回它自己,不回空字串。"""
    return _t("aib.feature.%s" % name, _FALLBACK_LABEL.get(name, name), locale)


def off_means(locale=None):
    """關掉的語意,逐字。畫面那一行的尾巴用它,doctor 也用它,只有一個入口。"""
    return _t("aib.feature.off_means", _FALLBACK_OFF_MEANS, locale)


def unreadable_notice(locale=None):
    """「開關檔讀不懂」那一句。畫面那一列與 doctor **共用這一份**。

    ★ 沒有這一句的時候,畫面上「三件都關著」與「開關檔讀不懂(你以為開著、
    其實是關的)」逐字相同 —— 而使用者看的是那一列,不是 doctor。
    """
    return _t("aib.feature.unreadable_row", _FALLBACK_UNREADABLE, locale)


# admit 的理由字面。每一個都要能單獨被人讀懂 ——
# 「擋住了」而沒有理由,跟「壞了」在畫面上同形。
R_OK = "ok"
R_UNKNOWN = "unknown_feature"
R_NO_HOME = "no_cabin_home"
R_UNREADABLE = "policy_unreadable"
R_NOT_ADVANCED = "not_advanced"
R_DISABLED = "disabled"

REASON_TEXT = {
    R_OK: "開著",
    R_UNKNOWN: "沒有這件功能(名字不在登記簿裡)",
    R_NO_HOME: "量不到艙家,當作沒開",
    R_UNREADABLE: "開關檔讀不懂,一律當關",
    R_NOT_ADVANCED: "功能區要 Plus 授權才開(Plus = 啟用 Ez2 AIB 功能區 Plus 的授權)",
    R_DISABLED: "開關是關的",
}


def plus_needed_notice():
    """「沒有 Plus 授權」那一句。開關列與 doctor **共用這一份**(同 unreadable_notice 的理由):
    沒有這一句時,基本版畫面上功能區什麼都不畫,讀的人分不出「關著」與「授權不夠」。"""
    return REASON_TEXT[R_NOT_ADVANCED]


def lacks_plus(env=None, how=None):
    """True = 開關檔讀得懂(或還沒設)但授權不是 Plus —— 這時才該說「要 Plus」。
    沒艙家/壞檔的理由排在授權前面(`judge` 的順序),那兩種不說這一句。"""
    if how is None:
        how = load_policy(env)[1]
    if how in (R_NO_HOME, "unreadable"):
        return False
    return plan_of(env) != LP.ADVANCED


def _env(env=None):
    return os.environ if env is None else env


def cabin_policy_path(filename, env=None):
    """開關檔住哪。**本檔與 `dm_queue` 共用這一個**(第 47b 項)。

    順位:艙家 `EZ2_AIB_INSTANCE_HOME` > 明寫的狀態根(`seats_dir_lib.state_root`
    回的來源是 `env:` 才算)> None。**永不退回預設的 `~/.ez2-aib`** ——
    治具沒指任何地方,就當沒開,不得讀寫到真艙去。
    """
    e = _env(env)
    home = (e.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if home:
        return Path(os.path.expanduser(home)) / "state" / filename
    root, src = seats_dir_lib.state_root(e)
    if src.startswith("env:"):
        return Path(os.path.expanduser(root)) / "state" / filename
    return None


def judge(how, plan_fn, enabled):
    """(准不准, 理由)。**本檔與 `dm_queue` 共用這一個**(第 47b 項)。

    how      ∈ no_cabin_home / missing / unreadable / ok(各自 `load_policy` 的回傳)
    plan_fn  無參數、回 `basic` / `advanced`;**用到才叫**,沒艙家與壞檔時不去讀授權
    enabled  這一件開關的值

    ★ 順序就是判準:沒艙家 → 壞檔 → 授權 → 關著。授權排在壞檔**後面**是刻意的:
      開關檔壞掉要先被說出來,不然「檔壞了」會被「授權不夠」蓋住,而兩件修法完全不同。
    """
    if how == R_NO_HOME:
        return False, R_NO_HOME
    if how == "unreadable":
        return False, R_UNREADABLE
    if plan_fn() != LP.ADVANCED:
        return False, R_NOT_ADVANCED
    if not enabled:
        return False, R_DISABLED
    return True, R_OK


def policy_path(env=None):
    """本艙功能區開關檔。住哪的規則見 `cabin_policy_path`。"""
    return cabin_policy_path("feature-policy.json", env)


def cabin_name(env=None):
    """艙的短名(`20260908-103133-a801` → `a801`)。量不到就說量不到。"""
    e = _env(env)
    raw = (e.get("EZ2_AIB_INSTANCE") or "").strip()
    if not raw:
        home = (e.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
        raw = os.path.basename(home.rstrip("/")) if home else ""
    if not raw:
        return "量不到"
    return raw.rsplit("-", 1)[-1] or raw


def plan_of(env=None):
    """`basic` 或 `advanced`。這是閘,不是顯示。"""
    return LP.license_plan(_env(env))


def default_policy():
    return {name: False for name in FEATURES}


def load_policy(env=None):
    """回 (每件的開關, how)。how ∈ no_cabin_home / missing / unreadable / ok。

    **四種情形裡有三種都回全關** —— 這是刻意的:讀不到的時候要長得像關著,
    不能長得像開著。認不得的鍵一律忽略(不讓打錯字的鍵靜靜地變成一個功能)。
    """
    path = policy_path(env)
    if path is None:
        return default_policy(), R_NO_HOME
    if not path.is_file():
        return default_policy(), "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_policy(), "unreadable"
    if not isinstance(data, dict) or data.get("schema") != POLICY_SCHEMA:
        return default_policy(), "unreadable"
    rows = data.get("features")
    if not isinstance(rows, dict):
        return default_policy(), "unreadable"
    out = default_policy()
    for name in FEATURES:
        val = rows.get(name)
        if isinstance(val, bool):
            out[name] = val
        elif val is not None:
            # 有這個鍵但不是 true/false:整份當壞檔,不要只忽略這一格
            # ——只忽略那一格的話,「打錯型別」與「沒寫」在畫面上同形。
            return default_policy(), "unreadable"
    return out, "ok"


def write_policy(feature, enabled, env=None):
    """改一件的開關。其餘的原樣保留。"""
    if feature not in FEATURES:
        raise ValueError("沒有這件功能:%r" % (feature,))
    path = policy_path(env)
    if path is None:
        raise OSError("feature_policy: 沒有艙家,寫不了開關")
    current, how = load_policy(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    if how == "unreadable":
        # 壞檔要重建,不要在壞檔上疊寫 —— 疊寫會把別件的狀態悄悄變成預設。
        # ★ 但重建會丟掉使用者原本的設定,所以**先備份再印一行**:
        #   靜靜重建的話,「設定被丟掉」與「本來就沒設過」在畫面上同形。
        backup = path.with_name("%s.bad-%s" % (
            path.name, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
        try:
            os.replace(str(path), str(backup))
        except OSError:
            backup = None
        sys.stderr.write("%s\n" % _t(
            "aib.feature.policy_rebuilt", "開關檔讀不懂,已重建:%s" % backup,
        ).replace("{backup}", str(backup or path)))
        current = default_policy()
    current[feature] = bool(enabled)
    AJ.write_json(str(path), {
        "schema": POLICY_SCHEMA,
        "features": {name: bool(current[name]) for name in FEATURES},
    })
    # ★ 只收緊**這個檔**。舊版 chmod 的是艙家的 `state/` 整個目錄,而那裡面
    #   還住著佇列與待決簿 —— 一支檔順手改掉共用目錄的權限不是它的事(a seat)。
    os.chmod(str(path), 0o600)
    return current


def admit(feature, env=None):
    """(准不准, 理由)。理由是上面那幾個字面之一,不是自由文字。"""
    if feature not in FEATURES:
        return False, R_UNKNOWN
    policy, how = load_policy(env)
    return judge(how, lambda: plan_of(env), policy.get(feature))


def admit_all(env=None):
    """{功能名: (准不准, 理由)},照登記簿順序。"""
    return {name: admit(name, env=env) for name in FEATURES}


def _stamp():
    t = time.localtime()
    off = -(time.altzone if t.tm_isdst else time.timezone)
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return "%s%s%02d:%02d" % (time.strftime("%Y-%m-%dT%H:%M:%S", t),
                              sign, off // 3600, (off % 3600) // 60)


def status(env=None):
    """給 doctor 與任務區投影用的一份現況。**每一格都帶來源與量測時刻。**"""
    policy, how = load_policy(env)
    verdicts = admit_all(env)
    path = policy_path(env)
    return {
        "cabin": cabin_name(env),
        "plan": plan_of(env),
        "policy_how": how,
        "policy_path": str(path) if path else "",
        "on": [n for n in FEATURES if verdicts[n][0]],
        "off": [n for n in FEATURES if not verdicts[n][0]],
        "reasons": {n: verdicts[n][1] for n in FEATURES},
        "off_means": off_means(),
        "measured_at": _stamp(),
    }


def switch_row(env=None):
    """任務區畫面上的開關列,逐字。

    `開關:task_zone=on(艙 a801,advanced)・global_box=on・queue_stale_mark=off ── 關掉即不畫、不佔高、不留 hit`

    ★ 三件一律**逐件列出來**,關的也列。理由:「沒有這一件功能」與
    「這一件關著」在版面上不得同形 —— 少列一件,讀的人會以為那件不存在。
    """
    verdicts = admit_all(env)
    bits = []
    for i, name in enumerate(FEATURES):
        onoff = "on" if verdicts[name][0] else "off"
        if i == 0:
            bits.append("%s=%s(艙 %s,%s)"
                        % (name, onoff, cabin_name(env), plan_of(env)))
        else:
            bits.append("%s=%s" % (name, onoff))
    tail = off_means()
    _how = load_policy(env)[1]
    if _how == "unreadable":
        # 這一列本來只帶得出艙名與方案,帶不出「為什麼是 off」。
        tail = "%s ── %s" % (tail, unreadable_notice())
    elif lacks_plus(env, _how):
        # 10-01:基本版時功能區什麼都不畫;這一列要說出是授權不夠,不是關著。
        tail = "%s ── %s" % (tail, plus_needed_notice())
    return "開關:" + "・".join(bits) + " ── " + tail


def _cli(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="feature_policy",
                               description="功能區各件的開關與授權")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="印 JSON 現況")
    sub.add_parser("row", help="印畫面上的開關列")
    s = sub.add_parser("set", help="開/關一件")
    s.add_argument("feature", choices=list(FEATURES))
    s.add_argument("state", choices=("on", "off"))
    args = p.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "row":
        print(switch_row())
        return 0
    try:
        write_policy(args.feature, args.state == "on")
    except OSError as err:
        print("%s" % err, file=sys.stderr)
        return 2
    print(switch_row())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
