# SPDX-License-Identifier: Apache-2.0
"""席位目錄解析:**單一真相源**(EH.2,2026-08-21)。

## 為什麼要有這支檔

2026-08-21 全庫有 8 份以上各自實作的 `seats_dir()`。它們的差異不是風格,
是**答案**——有的先看 HUB、有的直接退主艙,而退主艙時拿到的是一份
**格式完全正確的錯答案**:主艙 `~/.ez2-aib/seats/wN.json` 與艙內
`instances/<id>/seats/wN.json` 同檔名同窗號,解錯不報錯。
實測代價見 `seat_onboard.py:seats_dir` 的 docstring(開五席印出的 id 五席全錯,
其中一席整天用死檔的 id 署名而從未失敗)。

## 解析順位

    HUB 導出 > EZ2_AIB_INSTANCE_HOME > 狀態根/seats
    狀態根(ED.1,2026-08-22)= EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib
    HUB 導出的 instances/<N>/seats 也掛在**狀態根**之下,不再寫死家目錄——
    ED.1 D5:「注入了 instance 家、下層卻退回主艙」就是沒注入。

★★ **主艙不是回退來源,是衝突。** HUB 說得出是哪一艙時就**只**用那一艙;
那一艙的目錄不存在是錯誤,不是「那就用主艙的吧」。今天的失效形狀是
**回退成功了,而成功不會叫**。

## 這支檔**不管**的事(重要,免得下一個人拿它去改錯地方)

「我在哪一艙」與「全部有哪些艙」是兩個問題,只有前者屬於本檔。
底下這幾支是**刻意跨艙**的,不得改成走本檔——改了會製造回歸而判準會轉綠:

    seat_ident.taken_seat_ids   全域已用號(EG.3 防跨艙撞號;窄化=撞號回來)
    seat_who.seats_dirs         who 是跨艙查詢(三修之後才定案「全部都掃」)
    fleet_status                列舉所有 instances 報殘缺艙(看不見≠零席)

## 沙盒任務板(ED.1 (e),2026-08-22)

`tasks_dir()` 與席位無關,放在這裡是因為它要的是**同一個狀態根**。
上游是 `cockpit-env.sh:148-155`:

    EZ2_COCKPIT_STATE_DIR = EZ2_AIB_INSTANCE_HOME/cockpit
    EZ2_TASKS_DIR         = EZ2_COCKPIT_STATE_DIR/sandbox-tasks

★ **不從 HUB 導出**,與 `seats_dir()` 不同。cockpit-env 的 instance 家是由
`EZ2_AIB_INSTANCE` 決定的,而 `main` 對應狀態根**本身**(不是 `instances/main`);
「hub 說哪一艙」與「EZ2_AIB_INSTANCE 說哪一艙」是兩個問題,合併它們是艙解析
單源那一刀(SPEC-cabin-resolution-single-source-0822.md)的事,不在這裡搶跑。
"""

import os

# ED.1 狀態根的 env 順位。與 src/memory/binding_projection.py:171、card_registry.py:381
# 同一個順序;aib 的 aib_seats_dir / cockpit-env.sh 的 _AIB_INSTANCE_HOME 照同一式子。
STATE_ROOT_ENV = ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME")
STATE_ROOT_DEFAULT = "~/.ez2-aib"


def state_root(environ=None):
    """(狀態根絕對路徑, 來源)。來源 ∈ env:<名> / default:~/.ez2-aib(ED.1 D2:
    每個入口要答得出「我用的根從哪來」,不是只回路徑)。純函式。"""
    environ = os.environ if environ is None else environ
    for var in STATE_ROOT_ENV:
        value = environ.get(var)
        if value:
            return value, "env:" + var
    return os.path.expanduser(STATE_ROOT_DEFAULT), "default:" + STATE_ROOT_DEFAULT


def instance_of_hub(hub):
    """從 --hub 導出艙號:aihub-13 → "13";aihub(主艙)→ None。

    ★ 二修根因(dev4⚓ 08-18 阻擋級,艦長複驗屬實並收回自己的終審綠):
      一修讓路徑跟 **env** 走,但掃描目標跟 **--hub** 走——**兩個真相源**。
      後果:(a) 乾淨 env 照文件跑 `scan --hub aihub-13`,路徑回共用檔,P1
      原封不動;(b) 反向 `EZ2_AIB_INSTANCE=9` 掃 aihub-13,會把 13 的樣本
      寫進 9 的檔——比原本更糟(污染別艙)。
      修法:**狀態路徑由掃描目標導出**,env 只當 fallback。同一個問題只能有
      一個真相源,而那個源必須是「這次實際在掃誰」。"""
    if not hub:
        return None
    name = str(hub).strip()
    if name.startswith("aihub-"):
        tail = name[len("aihub-"):]
        return tail if tail and all(c.isalnum() or c == "-" for c in tail) else None
    return None


def seats_dir_with_source(home_env=("EZ2_AIB_INSTANCE_HOME",), hub=None,
                          environ=None):
    """(seats 目錄, 來源)。純函式,不碰檔案系統。
    hub 可傳入(seats_report 那種已經拿著 hub 的呼叫端),不傳就讀 env。"""
    environ = os.environ if environ is None else environ
    hub = hub or environ.get("AIB_DM_SOCKET") or environ.get("EZ2_AIHUB_SOCKET")
    root, root_src = state_root(environ)
    if hub and hub.startswith("aihub-"):
        inst = hub[len("aihub-"):]
        if inst:
            return (os.path.join(root, "instances", inst, "seats"),
                    "hub:%s+%s" % (hub, root_src))
    for name in home_env:
        home = environ.get(name)
        if home:
            return os.path.join(home, "seats"), "env:" + name
    return os.path.join(root, "seats"), root_src


def seats_dir(home_env=("EZ2_AIB_INSTANCE_HOME",), hub=None, environ=None):
    """本席所屬的 seats 目錄(絕對路徑)。純函式,不碰檔案系統。"""
    return seats_dir_with_source(home_env, hub, environ)[0]


def instance_home_with_source(environ=None):
    """(艙家目錄, 來源)。EZ2_AIB_INSTANCE_HOME > 狀態根。純函式。
    不看 HUB —— 理由見檔頭「沙盒任務板」節。"""
    environ = os.environ if environ is None else environ
    home = environ.get("EZ2_AIB_INSTANCE_HOME")
    if home:
        return home, "env:EZ2_AIB_INSTANCE_HOME"
    return state_root(environ)


def cockpit_state_dir_with_source(environ=None):
    """(執行期狀態目錄, 來源)= EZ2_COCKPIT_STATE_DIR > <艙家>/cockpit。"""
    environ = os.environ if environ is None else environ
    value = environ.get("EZ2_COCKPIT_STATE_DIR")
    if value:
        return value, "env:EZ2_COCKPIT_STATE_DIR"
    home, src = instance_home_with_source(environ)
    return os.path.join(home, "cockpit"), src


def tasks_dir_with_source(environ=None):
    """(沙盒任務板目錄, 來源)= EZ2_TASKS_DIR > <執行期狀態目錄>/sandbox-tasks。
    單路徑 env 仍優先(相容);沒設才落在狀態根下,所以「只注入根」也找得到。"""
    environ = os.environ if environ is None else environ
    value = environ.get("EZ2_TASKS_DIR")
    if value:
        return value, "env:EZ2_TASKS_DIR"
    state, src = cockpit_state_dir_with_source(environ)
    return os.path.join(state, "sandbox-tasks"), src


def tasks_dir(environ=None):
    """沙盒任務板目錄(絕對路徑)。純函式,不碰檔案系統。"""
    return tasks_dir_with_source(environ)[0]

# ── 艙路徑判定(紅2,2026-08-27)────────────────────────────────────────────
# 「這條路徑是不是艙狀態樹裡的東西」以前被各自用字面寫在守衛裡(社交側車那支就
# 是)。字面只認得**預設**主艙長相,`EZ2_AIB_STATE_ROOT` 把狀態根搬走之後,
# 三個字面一個都不會命中 —— 守衛看起來還在,而它守的那棵樹已經不在它認得的
# 位置上。★ 這正是本檔存在的理由的另一面:**知道艙在哪** 只准有一份。
CABIN_LITERAL_PARTS = (".ez2-aib", "instances")


def _within(path, base):
    """path 等於 base 或在 base 之下(逐段比,不做前綴字串比對)。"""
    if not base:
        return False
    path = os.path.abspath(os.path.expanduser(str(path)))
    base = os.path.abspath(os.path.expanduser(str(base)))
    if path == base:
        return True
    return path.startswith(base.rstrip(os.sep) + os.sep)


def cabin_path_reason(path, environ=None):
    """path 落在艙狀態樹裡的**理由**;不在回 None。純函式(不碰檔案系統)。

    回理由而不回布林,是為了讓拒絕訊息說得出「因為什麼而拒絕」——
    字面命中與導出命中要分得出來,否則搬過根之後沒人知道守衛還認不認得。

    兩層:
      literal:*  預設主艙的長相(`.ez2-aib` / `instances`,或 `.ez2-aib` 樹裡的
                 `seats`)——與搬根與否無關,永遠先擋。
      state-root / cabin-seats / cabin-home:**導出**端,狀態根被搬走時唯一
                 還認得出艙的東西。順位與本檔其餘函式同一份。
    """
    environ = os.environ if environ is None else environ
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    folded = tuple(part.casefold() for part in resolved.split(os.sep) if part)
    for name in CABIN_LITERAL_PARTS:
        if name in folded:
            return "literal:" + name
    if "seats" in folded and ".ez2-aib" in resolved.casefold():
        return "literal:seats"
    root, root_src = state_root(environ)
    if _within(resolved, root):
        return "state-root:" + root_src
    seats, seats_src = seats_dir_with_source(environ=environ)
    if _within(resolved, seats):
        return "cabin-seats:" + seats_src
    home, home_src = instance_home_with_source(environ)
    if _within(resolved, home):
        return "cabin-home:" + home_src
    return None


def cabin_seats_dir_with_source(environ=None):
    """(本艙 seats 目錄, 來源, 衝突理由)——**沒有艙證據時回 (None, 來源, None)**。

    與 `seats_dir()` 的差別只有一個,而那個差別是刻意的:
    `seats_dir()` 給的是「席位登記簿在哪」,問不出艙時退狀態根是對的;
    本函式給的是「**這一艙**的登記簿在哪」,問不出艙時**不准退主艙** ——
    退成功了不會叫,而那份主艙檔與艙內檔同檔名同窗號(見檔頭)。

    衝突理由:HUB 說得出是哪一艙,而 `EZ2_AIB_INSTANCE_HOME` 指向另一艙。
    兩邊都是「艙」的明示來源,挑一邊就是猜;呼叫端要 fail-closed。
    """
    environ = os.environ if environ is None else environ
    derived, source = seats_dir_with_source(environ=environ)
    if source.startswith("hub:"):
        home = environ.get("EZ2_AIB_INSTANCE_HOME")
        conflict = None
        if home:
            other = os.path.abspath(os.path.expanduser(
                os.path.join(home, "seats")))
            if other != os.path.abspath(os.path.expanduser(derived)):
                conflict = "hub=%s vs instance-home=%s" % (derived, other)
        return derived, source, conflict
    if source == "env:EZ2_AIB_INSTANCE_HOME":
        return derived, source, None
    return None, source, None
