#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""route-gate-stop v1.0.0 —— 把選擇丟回給人之前,先問路由器答應了沒。

Stop hook。守的是規格 §12.9.3(the maintainer 2026-09-09 12:1x 常設裁定):

    「之後遇到有問題卡住要選擇的,你發給你自己與其他同專案的 AI…
      由你總結後下決定,如果真人在,你最後的總合決定再給真人看即可。」

## 它擋什麼

**本回合把選擇丟回給真人(用了 AskUserQuestion),而沒有一筆 route=HUMAN 的路由紀錄。**

## 為什麼需要它 —— 這不是設想,是量出來的

2026-09-09 現查:`tools/dispatch/decide-route.py` 的消費者只有 **1 個**,
就是測它自己的臂 `tests/test-decide-route.py`;產品呼叫點 0、hook 0。
⇒ 它是本庫自己記過的那一類「**沒有任何自動呼叫點的閘**」,100% 靠紀律。

而紀律在同一個工作階段內失效兩次,兩次都是主導席 `80ec5` 自己:
  1. 把三個選項丟給真人選,而路由跑出來是 MEASURE(量了就有答案)
  2. 路由已判 COUNCIL,卻回頭問真人「要我起合議嗎」——
     §12.9.3 逐字寫著「是給他看,不是等他批」,問那一句本身就違反它。
★ 兩次的**外表逐字不同、機制相同**:都是「該自己做的事被丟回去」。

## 判準:只認機器事實

- 「有沒有問人」不靠 grep 中文問句,靠**本回合工具名 set 裡有沒有 AskUserQuestion**。
  理由:問句的寫法無窮多,而工具呼叫是 harness 的事實。
- 「有沒有路由」讀 `decide-route` 的留痕 JSONL,且**只認本回合之後那幾列**
  (拿上一輪的路由替這一輪背書,與沒路由過同形)。

## 設計原則(全部來自本庫既有判例,不新造)

* **fail-open + 留痕**:讀不到 transcript、讀不到留痕檔、自己炸掉 —— 一律放行並出聲。
  判例:「閘自己壞掉時不准癱瘓它守的東西」(2026-09-09 一顆三錯)。
  一個會把全艦 Stop 卡死的守門程式,比沒有守門程式更糟。
* **`stop_hook_active` 直接放行**:避免阻擋迴圈,同 claim-gate-stop。
* **必落空端要存在**:「問了人 + 有 HUMAN 路由」必須放行,否則這道閘只是把
  所有問句都擋掉,那不是閘是禁令。臂裡有這一格。
* **逃生門**:`AIB_ROUTE_GATE_REASON="理由"` —— 沒有逃生門的閘會被整支拔掉。
  理由記檔;記不下來仍放行但出聲(同 release-tag-gate 的 F3 取捨)。

## 新增閘三問

1. 自己壞掉會怎樣? 放行並在 stderr 與閘日誌出聲,不讓壞掉的 Stop hook 卡死全艦。
2. 從不開火長什麼樣? 問了真人,但路由留痕檔從未建立;這一格必須擋,不能冒充讀檔故障。
3. 會不會擋到正確輸入? 最近一筆有效路由是 HUMAN 就放行;沒有問人也放行。

## 它擋不住什麼(寫下來,免得被當成全涵蓋)

**「該問而沒問」擋不住。** 那是本閘的反向,而反向沒有觸發點 ——
沒發生的事不會在正文裡留下形狀。the maintainer 2026-09-09 22:2x 明示:先做這一半,
該問沒問之後再改。★ 所以本閘綠**不代表**這一回合的路由是對的。
"""
import json
import os
import sys
import time

AIB_HOME = os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
ROUTE_LOG = os.environ.get("AIB_ROUTE_LOG") or os.path.join(
    AIB_HOME, "logs", "decide-route-runs.jsonl")
LOG = os.path.join(AIB_HOME, "logs", "route-gate.log")

ASK_TOOL = "AskUserQuestion"


def _note(msg):
    """留痕。寫不進去不是理由停下來 —— 這道閘的任何自我故障都不得擋人。"""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg))
    except OSError:
        pass


def turn_facts(transcript_path):
    """回 (本回合用過的工具名 set, 本回合起始 epoch)。

    ★ 這一段的切法與 src/claimtruth/claim-gate-stop.py::current_turn 同一條:
      由後往前找最後一則**真人** user 訊息(content 不是 tool_result),
      它之後的 assistant 區塊才算本回合。不重寫第二套切法。
    """
    rows = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue

    start = 0
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("type") != "user":
            continue
        content = (r.get("message") or {}).get("content")
        if isinstance(content, str):
            start = i + 1
            break
        if isinstance(content, list) and not any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content):
            start = i + 1
            break

    since = 0
    if start > 0:
        ts = rows[start - 1].get("timestamp") or ""
        try:
            from datetime import datetime
            since = int(datetime.fromisoformat(
                ts.replace("Z", "+00:00")).timestamp())
        except ValueError:
            since = 0

    tools = set()
    for r in rows[start:]:
        if r.get("type") != "assistant":
            continue
        content = (r.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.add(b.get("name") or "")
    return tools, since


def routes_since(since):
    """回本回合之後的有效路由,依留痕順序排列。

    ★ 回 None 表示檔案存在卻讀不動;回 [] 表示檔不存在或本回合零筆。
      呼叫端必須分開處置:[] 要擋,None 要放行並出聲。
      這一格是本庫踩最多次的形狀(觀測法讀不到時回的值,跟正常值同形)。
    """
    if not os.path.isfile(ROUTE_LOG):
        # ★★ 這一格是本閘最致命的洞,臂在寫的當下就抓到了:
        #   「檔不存在」原本被我判成「讀不到 ⇒ 放行」,而**本閘的常態就是檔一開始
        #   不存在**(還沒有人跑過任何路由)⇒ 它從上線第一天起恆放行、永遠不開火,
        #   而外表與「一直都沒事」逐字相同。判例:「會關掉自己修復路徑的閘」與
        #   「從不開火像什麼」。
        # ⇒ 分辨器:**檔不存在 = 從沒路由過(要擋)**;
        #           **檔在而讀不動 = 真的讀不到(放行)**。兩者處置相反。
        return []
    routes = []
    try:
        with open(ROUTE_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                route = row.get("route")
                if route not in {"MEASURE", "HUMAN", "CHAIR", "COUNCIL"}:
                    continue
                ts = row.get("ts_utc") or ""
                try:
                    from datetime import datetime
                    epoch = int(datetime.fromisoformat(
                        ts.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    continue
                if epoch >= since:
                    routes.append(route)
    except OSError:
        return None
    return routes


def decide(payload):
    """回 (should_block, message)。純函式 —— 臂可以直接餵字典。"""
    if payload.get("stop_hook_active"):
        return False, "stop_hook_active,放行(避免阻擋迴圈)"

    reason = (os.environ.get("AIB_ROUTE_GATE_REASON") or "").strip()
    if reason:
        _note("BYPASS %s" % reason)
        return False, "逃生門放行:%s(已記檔 %s)" % (reason, LOG)

    tp = payload.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        _note("FAILOPEN 讀不到 transcript")
        return False, "警告:讀不到 transcript,放行(fail-open)"

    try:
        tools, since = turn_facts(tp)
    except (OSError, ValueError) as e:
        _note("FAILOPEN 解析 transcript 失敗:%s" % e)
        return False, "警告:解析 transcript 失敗,放行(fail-open):%s" % e

    if ASK_TOOL not in tools:
        return False, "本回合沒有把選擇丟回給人"

    routes = routes_since(since)
    if routes is None:
        # 只有「檔在而讀不動」(權限/IO 錯)才走到這裡 —— 那是真的量不到。
        _note("FAILOPEN 路由留痕存在但讀不動 %s" % ROUTE_LOG)
        return False, ("警告:用了 %s,而路由留痕存在卻讀不動(%s)—— 放行,"
                       "但這一次沒有被檢查過" % (ASK_TOOL, ROUTE_LOG))
    if not routes:
        route_detail = "路由留痕不存在,或本回合從沒留下有效路由"
    else:
        latest = routes[-1]
        if latest == "HUMAN":
            return False, "用了 %s,且本回合最近一筆是 route=HUMAN" % ASK_TOOL
        direction = {
            "MEASURE": "先去量",
            "CHAIR": "由主導席自行決定",
            "COUNCIL": "開 AI 合議",
        }[latest]
        route_detail = "本回合最近一筆是 route=%s -> %s" % (latest, direction)

    return True, (
        "本回合用了 %s 把選擇丟回給真人,但%s,不是 route=HUMAN。\n"
        "  規格 §12.9.3:卡住要選的先跑 tools/dispatch/decide-route.py,\n"
        "  判 MEASURE 就去量、判 CHAIR/COUNCIL 就自己決定或開合議 —— 那些都不該問人。\n"
        "  只有判 HUMAN 才輪得到真人保留格。\n"
        "  ★ 走到 HUMAN 之前先查常設令有沒有已經下放/回答過它(判例:merge-ez2-aib\n"
        "    的 routing_error,那次實付 60 顆停一天)。\n"
        "  真的要問:AIB_ROUTE_GATE_REASON=\"理由\"(理由會記檔)。" %
        (ASK_TOOL, route_detail))


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError) as e:
        _note("FAILOPEN 讀不到 hook payload:%s" % e)
        return 0
    try:
        block, msg = decide(payload)
    except Exception as e:  # noqa: BLE001 —— 閘自己壞掉不得擋人
        _note("FAILOPEN decide() 自己炸了:%r" % e)
        sys.stderr.write("route-gate: 自身故障,已放行並留痕:%r\n" % e)
        return 0
    if block:
        sys.stderr.write("遇事不決閘 [BLOCK] " + msg + "\n")
        return 2
    if msg.startswith("警告:"):
        sys.stderr.write("route-gate [WARN] " + msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
