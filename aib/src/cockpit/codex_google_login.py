# SPDX-License-Identifier: Apache-2.0
"""把 Codex 的 Google 登入頁點完。密碼和二次驗證不代按。每次留下時間與次數。"""
import json
import os
import subprocess
import time
from pathlib import Path

LOG = Path.home() / ".ez2-aib" / "logs" / "codex-google-login.jsonl"


def action_for(url: str) -> str:
    """這一頁該按什麼。認網址，不認會變的 class。"""
    u = (url or "").split("?", 1)[0].lower()
    if u.startswith("http://localhost:1455/success") or u.startswith("http://127.0.0.1:1455/success"):
        return "close_success"
    if "choose-an-account" in u:
        return "other_account"
    if "log-in-or-create-account" in u:
        return "google"
    if "accounts.google.com" in u:
        return "pick_email"
    if "/codex/consent" in u:
        return "consent_continue"
    return ""


def click_js(action: str, email: str) -> str:
    """在頁面上用可見文字點一下。找不到就什麼都不按。"""
    email = json.dumps(email, ensure_ascii=False)
    if action == "other_account":
        label = json.dumps("登入其他帳戶", ensure_ascii=False)
    elif action == "google":
        label = json.dumps("使用 Google 帳號繼續", ensure_ascii=False)
    elif action == "consent_continue":
        label = json.dumps("繼續", ensure_ascii=False)
    elif action == "pick_email":
        label = email
    else:
        return "false"
    return (
        "(function(){"
        "var want=%s;"
        "function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}"
        "var nodes=document.querySelectorAll('a,button,[role=link],[role=button]');"
        "var exact=null, loose=null;"
        "for (var i=0;i<nodes.length;i++){"
        "var t=norm(nodes[i].innerText);"
        "if(!t) continue;"
        "if(t===want){exact=nodes[i]; break;}"
        "if(!loose && t.indexOf(want)>=0 && t.length<80) loose=nodes[i];"
        "}"
        "var el=exact||loose;"
        "if(!el){"
        "var all=document.querySelectorAll('span,div,p');"
        "for (var j=0;j<all.length;j++){"
        "if(norm(all[j].innerText)!==want) continue;"
        "var p=all[j];"
        "for (var k=0;k<6 && p;k++){"
        "if(p.matches && p.matches('a,button,[role=link],[role=button]')){el=p; break;}"
        "p=p.parentElement;"
        "}"
        "if(!el) el=all[j];"
        "break;"
        "}"
        "}"
        "if(!el) return false;"
        "el.click();"
        "return true;"
        "})()"
    ) % label


def append_cycle(email: str, result: str, started: float, steps: list[str]) -> dict:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    if LOG.is_file():
        with LOG.open(encoding="utf-8") as fh:
            n = sum(1 for line in fh if line.strip()) + 1
    row = {
        "n": n,
        "email": email,
        "result": result,
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "steps": steps,
    }
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def chrome_js(script: str, runner=None) -> str:
    """在登入相關的分頁上執行。不碰最前面那個無關的分頁。"""
    runner = runner or subprocess.run
    osa = '''
on run argv
  set js to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        set u to URL of t
        if u contains "auth.openai.com" or u contains "accounts.google.com" or u contains "localhost:1455" or u contains "127.0.0.1:1455" then
          return execute t javascript js
        end if
      end repeat
    end repeat
    return "NO_TAB"
  end tell
end run
'''
    done = runner(["osascript", "-e", osa, script], capture_output=True, text=True, timeout=8)
    err = (done.stderr or "") + (done.stdout or "")
    if done.returncode != 0:
        if "不允許" in err or "not allowed" in err.lower():
            raise RuntimeError("js_disabled")
        raise RuntimeError("chrome_js_failed")
    return (done.stdout or "").strip()


def close_success_tab(runner=None) -> int:
    """關掉登入完成的那個分頁。網址只認到 success，不讀後面的憑證。"""
    runner = runner or subprocess.run
    osa = r'''
tell application "Google Chrome"
  set n to 0
  repeat with w in windows
    set tabList to tabs of w
    repeat with i from (count of tabList) to 1 by -1
      set u to URL of item i of tabList
      if u starts with "http://localhost:1455/success" or u starts with "http://127.0.0.1:1455/success" then
        close item i of tabList
        set n to n + 1
      end if
    end repeat
  end repeat
  return n
end tell
'''
    done = runner(["osascript", "-e", osa], capture_output=True, text=True, timeout=8)
    try:
        return int((done.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def drive(email: str, runner=None, sleep=time.sleep, ticks: int = 40) -> dict:
    """點完選帳、Google、對應信箱、工作區的繼續，然後關掉完成頁。"""
    started = time.time()
    steps: list[str] = []
    result = "timeout"
    misses = 0
    try:
        for _ in range(ticks):
            try:
                url = chrome_js("location.href", runner=runner)
            except RuntimeError as exc:
                if str(exc) == "js_disabled":
                    raise
                misses += 1
                if misses >= 8:
                    result = str(exc)
                    break
                sleep(0.5)
                continue
            if url == "NO_TAB":
                sleep(0.5)
                continue
            action = action_for(url)
            if action == "close_success":
                close_success_tab(runner=runner)
                steps.append(action)
                result = "ok"
                break
            if action and action not in steps:
                try:
                    clicked = chrome_js(click_js(action, email), runner=runner)
                except RuntimeError as exc:
                    if str(exc) == "js_disabled":
                        raise
                    misses += 1
                    if misses >= 8:
                        result = str(exc)
                        break
                    sleep(0.5)
                    continue
                if str(clicked).strip().lower() != "true":
                    sleep(0.5)
                    continue
                steps.append(action)
            sleep(0.5)
    except RuntimeError as exc:
        result = str(exc)
    row = append_cycle(email, result, started, steps)
    return row
