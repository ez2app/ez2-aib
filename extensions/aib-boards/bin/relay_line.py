"""dm 長訊落檔時,從附檔摘出「要你做什麼」那一句(seat-info.py 用)。

附檔路徑寫在別席寄來的信裡,**不可信**:只開狀態根底下的檔(realpath 比對),指到別處一律不開、原文照回。"""
import os
import re

ASK = re.compile(r"任務|請你|回覆|交辦|派工")


def relay_summary(msg, home):
    if "請讀:" not in (msg or ""):
        return msg
    try:
        rp = os.path.realpath(msg.split("請讀:", 1)[1].strip())
        if not rp.startswith(os.path.realpath(home) + os.sep):
            return msg
        with open(rp, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except Exception:
        return msg
    asks = [ln for ln in lines if ASK.search(ln)]
    return (asks[-1] if asks else lines[0]) if lines else msg
