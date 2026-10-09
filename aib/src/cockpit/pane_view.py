# SPDX-License-Identifier: Apache-2.0
"""座艙顯示開關的**共用設定層**(`ai_view.py` 與往後的 `panes.py` 都從這裡拿)。

## 為什麼要抽這一支

`aib ai role|sb on/off`(L3,已在 main)與 `aib left|center|right|footer on/off`
(L4)是**同一種東西**:一份住在狀態根底下的小 JSON,缺檔等於「今天的畫面」,
壞檔退預設但要出聲,寫入用 tmp+rename。

複製一份的代價不是多幾行,是**兩份會分岔而分岔無聲**:
今天兩邊的「缺檔=預設」一致,明天有人只改一邊的退檔行為,
使用者看到的是「role 開關記得住、footer 開關記不住」,而沒有一格會紅。

★ 這一支**不決定任何預設值**。預設住在呼叫端(`ai_view.DEFAULTS` 是兩個都開,
  `panes` 的預設由 the maintainer 的更正一定成 center+right)——把預設搬進來會讓
  「共用的是機制」變成「共用的是政策」,而政策**本來就該不一樣**。

## 兩態同值、不同音量(從 `ai_view.py` 原樣搬過來的判準)

  缺檔 = 沒設定過 = 正常狀態,**不出聲**
  壞檔 = 設定過但讀不出來,值退回預設,**一定出聲**

若壞檔靜默退預設,「我讀不到你的設定」與「使用者要預設」在畫面上逐字相同——
那正是本 repo 記過的「觀測法讀不到時回的值,跟正常值同形」。

## 狀態根鏈(全 repo 既有那條,不另立)

    <覆寫變數>(整條路徑) > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib

覆寫變數**一個檔一個**(`EZ2_AIB_AI_VIEW` / `EZ2_AIB_PANES`),不共用:
一條完整檔路徑指不到兩個檔。★ 新增覆寫變數要同時進
`cabin_resolve.sh:aib_cabin_env_names()`,否則 `test-cabin-single-source-names`
會紅 —— L3 就是漏了這一步(09-05 `52d3fbda` 補的)。
"""

import json
import os


def state_root(env=None):
    """狀態根目錄(不含檔名)。與 role_model / bind_one / binding_projection 同鏈。"""
    env = os.environ if env is None else env
    root = (env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME")
            or "~/.ez2-aib")
    return os.path.abspath(os.path.expanduser(root))


def config_path(filename, override_var, env=None):
    """設定檔絕對路徑:`override_var` 有值就用它(整條路徑),否則狀態根 + filename。"""
    env = os.environ if env is None else env
    explicit = (env.get(override_var) or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    return os.path.join(state_root(env), filename)


def load(path, defaults, label, fallback_phrase="預設"):
    """回 `(values, warning)`。

    `values` 永遠是完整的布林字典(以 `defaults` 的鍵為母體,缺鍵補預設)。
    `warning` 是 None 或一句人話 —— **呼叫端有責任把它印到 stderr**。

    ★ 回 tuple 而不是自己印:採集端每幀讀一次,自己印會刷屏;
      但也不能吞掉,所以由呼叫端決定**音量**,不由這裡決定**有沒有**。
    ★ `label` 只進訊息前綴(`ai-view` / `panes`),`fallback_phrase` 只進訊息正文
      (ai-view 說「兩個都開」、panes 會說它自己的)——**兩者都不參與任何判斷**。
      把措辭也交給呼叫端不是潔癖:抽共用層時我一度讓它統一說「預設」,
      差分實測抓到那是**唯一一處**行為變更(8 個情境的 rc/stdout/檔位元組全同,
      只有壞檔那一行 stderr 不同)。一個重構若「順手」改掉使用者看得到的字,
      那就不再是行為不變的重構,而它在測試上是**綠的**。
    """
    values = dict(defaults)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return values, None                      # 沒設定過=正常,不出聲
    except (OSError, ValueError) as err:
        return values, ("%s: 設定檔讀不到或格式壞掉,已退回%s(%s: %s)"
                        % (label, fallback_phrase, path, err))
    if not isinstance(raw, dict):
        return values, ("%s: 設定檔不是一個物件,已退回%s(%s)"
                        % (label, fallback_phrase, path))
    bad = []
    for key in defaults:
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, bool):
            values[key] = val
        else:
            # ★ `"role": "off"` 是**字串**不是布林,而 `bool("off")` 是 True。
            #   靜默接受會讓「我明明關了」變成永遠的謎。
            bad.append(key)
    if bad:
        return values, ("%s: 設定檔的 %s 不是布林值,該格退回預設(%s)"
                        % (label, "/".join(sorted(bad)), path))
    return values, None


def save(values, path, schema, defaults):
    """原子寫(tmp+rename,與 `ctx_by_pane.write_by_pane` 同形)。回寫入的路徑。

    ★ tmp 檔名帶 pid:固定檔名在兩席同時寫時會互相蓋,而那不會叫
      (`atomic_json.py` 檔頭記過這件事的實際事故)。
    """
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    rec = {"schema": schema}
    for key in defaults:
        rec[key] = bool(values.get(key, defaults[key]))
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path
