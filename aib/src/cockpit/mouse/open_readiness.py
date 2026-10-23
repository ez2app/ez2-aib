#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵「另開 AI」引擎層:開席前先看這一家缺不缺東西(10-24 @abb89,右鍵 14 家實測;Andy「依照你的建議開始修到全好」)。

事故:計費組(deepseek / nvidia / openrouter / typesafe / openrouterjev)金鑰檔在、值是空的 ⇒ 點下去開出一個
馬上死掉的窗,畫面上只剩「缺少 *_API_KEY」;三個 bot 沒接好(grokbot 沒有一個 agent 接門鈴、codexbot 沒連 Codex
桌面版、musebot 沒綁過 Muse 對話)⇒ 開得起來、但收不到信,要到席上才知道。
現在引擎層先查:
  api_key 那幾家缺金鑰 ⇒ 選單寫「缺金鑰」,點了叫出 `aib secret ask <名>`(遮字框 + Touch ID),不開死窗
  bot 沒接好         ⇒ 選單寫缺哪一步,照樣點得開(那幾步多半要在席上做:/connect、/bind)

只讀設定、不印值、不花額度;自己查壞了(讀檔例外)一律當成 ok —— 寧可照舊開席讓它自己講原因,也不擋人開席。

    python3 open_readiness.py <provider>...   每家印一行:ok | need_key<TAB>金鑰名<TAB>選單字 | hint<TAB>代碼<TAB>選單字
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COCKPIT = HERE.parent
for _p in (COCKPIT, COCKPIT.parent / "bootstrap"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CODEXBOT_THREAD_KEYS = ("AIB_CODEXBOT_THREAD_ID", "AIB_CODEXAPP_THREAD_ID", "CODEX_THREAD_ID")


def _env(environ):
    return os.environ if environ is None else environ


def _row(provider, providers_json=None):
    path = Path(providers_json) if providers_json else COCKPIT / "providers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = data.get("providers") if isinstance(data, dict) else data
    return next((r for r in rows or [] if isinstance(r, dict) and r.get("id") == provider), None)


def _key_present(name, environ):
    """環境變數有值,或金鑰櫃那一格有值(加密的也算有;解不解得開交給開席那一步講)。"""
    if (_env(environ).get(name) or "").strip():
        return True
    import aib_secrets as S
    rel = S.file_for_name(name)
    if not rel:
        return True                      # 金鑰櫃不認得這個名字:不是這裡該判的
    try:
        rows = S.load_file(rel, env=_env(environ))
    except S.SecretError:
        return True                      # 權限太寬之類:開席那一步會講清楚,這裡不擋
    return bool((rows.get(name) or "").strip())


def _state_root(environ):
    env = _env(environ)
    return Path(os.path.expanduser(env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME") or "~/.ez2-aib"))


def _bot_hint(provider, environ):
    base = _state_root(environ)
    if provider == "grokbot":
        import grokbot_threads as gt
        return "" if gt.connected_agents(base) else "no_doorbell"
    if provider == "codexbot":
        env = _env(environ)
        if any((env.get(k) or "").strip() for k in CODEXBOT_THREAD_KEYS):
            return ""
        import aib_secrets as S
        try:
            rows = S.load_file("codexbot.env", env=env)
        except S.SecretError:
            return ""
        return "" if any((rows.get(k) or "").strip() for k in CODEXBOT_THREAD_KEYS) else "no_codex_thread"
    if provider == "musebot":
        import musebot_threads as mt
        return "" if mt.history(base) else "no_muse_conv"
    return ""


def check(provider, environ=None, providers_json=None):
    """回 (狀態, 細節):("ok", "") / ("need_key", 金鑰名) / ("hint", 短字代碼)。"""
    try:
        row = _row(provider, providers_json)
        if row and row.get("auth") == "api_key":
            name = row.get("api_key_env") or ""
            if name and not _key_present(name, environ):
                return "need_key", name
            return "ok", ""
        if row and row.get("menu_group") == "bot":
            hint = _bot_hint(provider, environ)
            return ("hint", hint) if hint else ("ok", "")
    except Exception:  # noqa: BLE001 —— 附加檢查自己壞了不能擋開席
        return "ok", ""
    return "ok", ""


def label_suffix(state, detail, locale=None):
    """選單上接在引擎名後面的那幾個字;ok ⇒ 空字串。"""
    if state == "ok":
        return ""
    import i18n_lib
    key = "aib.context.menu.need_key" if state == "need_key" else "aib.context.menu.bot_%s" % detail
    try:
        text = i18n_lib.t(key, locale=locale)
    except Exception:  # noqa: BLE001
        return ""
    # i18n_lib.t 找不到鍵回 "!!鍵!!" ⇒ 當成沒字(main 退回 ok),選單上不出現 !!aib.context…!!
    return "" if not text or text == key or text.startswith("!!") else text


def ask_key(provider, environ=None, runner=None, locale=None):
    """缺金鑰那一項點下去:叫出 aib secret ask(遮字框 + Touch ID;值不經過這裡),回一句給艙底部看的話。"""
    import subprocess
    import i18n_lib
    state, name = check(provider, environ)
    if state != "need_key":
        return ""
    # 測試用:EZ2_OPEN_READINESS_AIB 換成樁,dry-run 被突變吃掉也不會在真人螢幕叫出輸入框 / Touch ID。
    #   不是防線(同帳號程式本來就能換檔);真正擋 AI 替人存金鑰的是 secret ask 自己的 Touch ID。
    aib_bin = _env(environ).get("EZ2_OPEN_READINESS_AIB") or str(COCKPIT / "aib")
    cmd = ["bash", aib_bin, "secret", "ask", name]
    try:
        rc = (runner or subprocess.run)(cmd, stdin=subprocess.DEVNULL, timeout=600).returncode
    except (OSError, subprocess.TimeoutExpired):
        rc = 1
    saved = rc == 0 and check(provider, environ)[0] == "ok"
    key = "aib.context.menu.key_saved" if saved else "aib.context.menu.key_not_saved"
    return i18n_lib.t(key, {"name": name, "engine": provider}, locale=locale)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "ask-key":
        print(ask_key(argv[1]))
        return 0
    if not argv:
        print("用法:open_readiness.py <provider>... | ask-key <provider>", file=sys.stderr)
        return 2
    for provider in argv:                # 一家一行、順序照參數(選單一次問完,不是一家起一次 python)
        state, detail = check(provider)
        suffix = label_suffix(state, detail)
        if state == "ok" or not suffix:
            print("ok")
        else:
            print("%s\t%s\t%s" % (state, detail, suffix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
