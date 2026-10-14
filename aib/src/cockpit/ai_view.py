#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ai` —— 右欄 AI 清單「角色顯示 / 第二大腦顯示」兩個開關的唯一入口。

the maintainer 2026-09-05 原話:「在對話欄中,可以有指令開關右方的 ai 的角色顯示 /
綁定第二大腦顯示,指令是 `aib ai role on/off` 與 `aib ai sb on/off`,設定之後
下次啟用 aib 時要自動套用。」

## 兩個開關管的是哪兩行

右欄每一席是三行制(設計正本 `docs/reports/three-line-rows-20260815/`):

    ├▸ 👑 CLA a seat …        ← 第一行,本工具不管
    │  ├▸ 人格名 [解綁]            ← `role`(the maintainer 口語叫「角色」)
    │  │  📁 大腦資料夾 [解綁]      ← `sb`(第二大腦)

★ 用字兩層刻意不一致,不要「順手統一」:CLI 字面照 the maintainer 的 `role`,
  而畫面上第二行顯示的是**綁定的人格**。統一成 persona 會讓 the maintainer 打不出他
  自己講的那個指令;統一成 role 會與席位檔的 `role` 欄位(dev6/pm/root)撞名,
  那是完全不同的東西。**兩個名字各自對一個讀者,合併會同時弄壞兩邊。**

## 設定住檔案,不住環境變數

派工單第五節明禁「加環境變數當開關」。理由不是潔癖:環境變數活在殼裡,
而 the maintainer 要的是「下次啟用 aib 時自動套用」——**殼會死,檔案不會**。
`EZ2_AIB_AI_VIEW` 只覆寫**路徑**(治具要一個不碰真設定的落點),不是開關。

狀態根解析沿用全 repo 既有那條鏈(`role_model.py:44`、`bind_one.py:81`、
`binding_projection.py:371`):

    EZ2_AIB_AI_VIEW(整條路徑) > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib

★ 只認 `HOME` 一個的話,帶了 `EZ2_AIB_STATE_ROOT` 的席會讀到**另一個檔**,
  而症狀是「我明明關掉了,它還是顯示」——沒有任何一格會紅。

## 缺檔與壞檔都等於「兩個都開」,但只有壞檔會出聲

  - **缺檔** = 使用者從沒設定過 = 今天的畫面。這是正常狀態,不出聲。
  - **壞檔** = 有人設定過但檔壞了。值退回全開(不因為讀不到就少畫東西),
    **但一定在 stderr 具名說一句**。

★ 這兩態刻意用同一個值、不同的音量。若壞檔靜默退預設,「讀不到」與
  「使用者要全開」在畫面上逐字相同 —— 那正是本 repo 記過的
  「觀測法讀不到時回的值,跟正常值同形」。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pane_view  # noqa: E402  設定的讀寫機制:與往後的 panes.py 共用同一份
from i18n_lib import Translator, resolve_locale  # noqa: E402

SCHEMA = "aib-ai-view/v1"
#: 兩個開關的鍵。順序=CLI 說明與 `aib ai` 輸出的順序,不另抄第二份清單。
KEYS = ("role", "sb")
#: 缺檔時的值。★ 必須等於「今天的畫面」——不得因為沒有設定檔而少畫東西。
DEFAULTS = {"role": True, "sb": True}
ON, OFF = "on", "off"


#: 設定檔名與它的路徑覆寫變數(治具用)。★ 覆寫變數**一個檔一個**:
#: 一條完整檔路徑指不到兩個檔,所以 panes.json 不能沿用 EZ2_AIB_AI_VIEW。
#: ★ 新增覆寫變數要同時進 cabin_resolve.sh:aib_cabin_env_names()——
#:   本檔就是漏了那一步(09-05 52d3fbda 補的)。
FILENAME = "ai-view.json"
OVERRIDE_ENV = "EZ2_AIB_AI_VIEW"


def config_path(env=None):
    """設定檔絕對路徑。env 可注入(治具用),預設讀真環境。"""
    return pane_view.config_path(FILENAME, OVERRIDE_ENV, env)


def load(path=None, env=None):
    """回 `(values, warning)`。機制在 `pane_view.load`,本函式只決定**政策**:
    檔在哪(`config_path`)、預設是什麼(`DEFAULTS`)、訊息前綴叫什麼。

    ★ 政策不搬進共用層是刻意的:`panes` 的預設(the maintainer 更正一:只開 center+right)
      與這裡的「兩個都開」**本來就不一樣**,共用預設會把「共用機制」變成
      「共用政策」,而那正是下一次分岔的來源。
    """
    path = config_path(env) if path is None else path
    return pane_view.load(path, DEFAULTS, "ai-view", "兩個都開")


def save(values, path=None, env=None):
    """原子寫。回寫入的路徑。機制(tmp+rename、pid 檔名)在 `pane_view.save`。"""
    path = config_path(env) if path is None else path
    return pane_view.save(values, path, SCHEMA, DEFAULTS)


def _mark(tr, flag):
    return tr.t("aib.ai_view.shown" if flag else "aib.ai_view.hidden")


def main(argv, env=None, out=None, err=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    tr = Translator(resolve_locale(env=env))
    path = config_path(env)

    if not argv:
        values, warning = load(path, env)
        if warning:
            err.write(warning + "\n")
        out.write(tr.t("aib.ai_view.status_role",
                       {"value": _mark(tr, values["role"])}) + "\n")
        out.write(tr.t("aib.ai_view.status_sb",
                       {"value": _mark(tr, values["sb"])}) + "\n")
        out.write(tr.t("aib.ai_view.status_path", {"path": path}) + "\n")
        return 0

    key = argv[0]
    if key not in KEYS:
        err.write(tr.t("aib.ai_view.bad_key",
                       {"key": key, "keys": " ".join(KEYS)}) + "\n")
        err.write(tr.t("aib.ai_view.usage") + "\n")
        return 2
    if len(argv) < 2:
        err.write(tr.t("aib.ai_view.usage") + "\n")
        return 2
    if len(argv) > 2:
        # ★ 多餘引數不靜默忽略:`aib ai role off sb off` 看起來像一次設兩個,
        #   靜默吃掉後半段會讓使用者以為兩個都設了,而只有一個生效。
        err.write(tr.t("aib.ai_view.too_many_args",
                       {"extra": " ".join(argv[2:])}) + "\n")
        err.write(tr.t("aib.ai_view.usage") + "\n")
        return 2
    word = argv[1]
    if word not in (ON, OFF):
        err.write(tr.t("aib.ai_view.bad_value",
                       {"value": word, "values": "%s/%s" % (ON, OFF)}) + "\n")
        err.write(tr.t("aib.ai_view.usage") + "\n")
        return 2

    values, warning = load(path, env)
    if warning:
        err.write(warning + "\n")
    values[key] = (word == ON)
    save(values, path, env)
    out.write(tr.t("aib.ai_view.set_ok",
                   {"key": key, "value": _mark(tr, values[key]),
                    "path": path}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
