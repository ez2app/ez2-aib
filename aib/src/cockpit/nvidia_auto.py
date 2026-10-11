# SPDX-License-Identifier: Apache-2.0
"""NVIDIA 席依時段自動切換模型(/model auto)。規格:~/.ez2-aib/pending/SPEC-nvidia-auto-model-9d499-1002.md

時段表由 tools/model-bench/nvidia-code-speed.py 產生(schedule.json)。這裡只負責:
讀表 → 依台北時間挑候選串 → 決定哪種失敗要換下一個、哪種要停。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

AUTO = "auto"
TAIPEI = timezone(timedelta(hours=8))
MAX_AGE = timedelta(hours=48)          # 時段表超過 48 小時沒更新 ⇒ 不用它
STICKY = timedelta(minutes=15)         # 換到備援後,15 分鐘內先用它;之後再試主力
PER_MODEL_TIMEOUT_S = 45               # 非串流整段回覆;寫程式的長回答常超過 20 秒,所以不能用抽樣的 20 秒
SWITCH = {"timeout", "rate_limited", "server_error", "empty", "connect", "not_served"}
STOP = {"auth", "bad_request"}         # 金鑰或請求本身有問題:換模型也一樣壞,還會蓋掉真正的錯


def schedule_path() -> Path:
    return bench_dir() / "schedule.json"


def bench_dir() -> Path:
    """NVIDIA_BENCH_DIR > EZ2_AIB_STATE_ROOT/model-bench/… > ~/.ez2-aib/model-bench/…(測試用狀態根就不會寫進真資料)。"""
    return bench_dir_from(os.environ)


def _bench_override(env):
    """NVIDIA_BENCH_DIR 另包小函式讀:不跟艙單源名字(EZ2_AIB_STATE_ROOT…)同函式,否則 test-cabin-single-source-names
    把它算成艙消費者(10-11 推前檢查紅,a seat 代修)。相對路徑不收 ⇒ 回空字串。"""
    v = os.path.expanduser((env.get("NVIDIA_BENCH_DIR") or "").strip())
    return v if v and os.path.isabs(v) else ""


def bench_dir_from(env) -> Path:
    """和席位資料夾同一套順序(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),最前面多一個 NVIDIA_BENCH_DIR;
    展開 ~,相對路徑不收(會寫進使用者專案裡)⇒ 退回 ~/.ez2-aib。"""
    override = _bench_override(env)
    if override:
        return Path(override)
    root = os.path.expanduser((env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME") or "").strip())
    base = Path(root) if root and os.path.isabs(root) else Path.home() / ".ez2-aib"
    return base / "model-bench" / "nvidia-code-speed"


def short(model: str) -> str:
    return model.split("/", 1)[-1]


def load_chain(default_model: str, now: datetime | None = None, path: Path | None = None) -> tuple[list[str], str]:
    """(候選串, 一句給人看的說明)。讀不到、壞檔、過期、這小時沒有候選 ⇒ 只用預設模型,說明裡講原因。"""
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    path = path or schedule_path()
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [default_model], "還沒有時段表,先用預設模型 %s" % short(default_model)
    except (OSError, ValueError):
        return [default_model], "時段表讀不了,先用預設模型 %s" % short(default_model)
    try:
        made = datetime.fromisoformat(str(plan["generated_at"]))
        if made.tzinfo is None:
            made = made.replace(tzinfo=TAIPEI)
        slot = plan["hours"][str(now.hour)]
        rows = [row for row in slot["chain"] if isinstance(row, dict) and row.get("model")]
        chain = [str(row["model"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return [default_model], "時段表格式不對,先用預設模型 %s" % short(default_model)
    if now - made > MAX_AGE:
        return [default_model], "時段表過期(%s 產生),先用預設模型 %s" % (made.astimezone(TAIPEI).strftime("%m-%d %H:%M"), short(default_model))
    if not chain:
        return [default_model], "%d 點沒有合格的模型,先用預設模型 %s" % (now.hour, short(default_model))
    if all(row.get("filler") for row in rows):
        # 審查 nvauto r1 bench 次要-2:整串都是「只過低級」的補位 ⇒ 不把寫程式交給小模型
        return [default_model], "還沒有考過中級以上的模型(寫程式要用新批改重考),先用預設模型 %s" % short(default_model)
    where = "本時段資料" if slot.get("source") == "hour" else "全天資料(這個小時資料還不夠)"
    return chain, "%d 點:%s(依%s)" % (now.hour, " → ".join(short(m) for m in chain), where)


def failure_kind(err) -> str:
    code = getattr(err, "code", None)
    if code == 408:
        return "timeout"
    if code == 429:
        return "rate_limited"
    if isinstance(code, int) and 500 <= code < 600:
        return "server_error"
    if code in (401, 403):
        return "auth"
    if code == 404:
        return "not_served"
    if isinstance(code, int) and 400 <= code < 500:
        return "bad_request"
    text = str(err).lower()
    if isinstance(err, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "timeout"
    if isinstance(err, (ConnectionError, http.client.HTTPException)):
        return "connect"   # 斷線、對方重設、回應讀一半(審查 nvauto r1 seat 次要-3):跟 5xx 同類,換下一個
    if isinstance(err, urllib.error.URLError):
        reason = getattr(err, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
            return "timeout"
        return "connect"
    return "other"


KIND_WORDS = {
    "timeout": "逾時", "rate_limited": "太忙(429)", "server_error": "伺服器錯誤", "empty": "空回應",
    "connect": "連不上", "not_served": "沒有這個模型", "auth": "金鑰被拒", "bad_request": "請求被拒", "other": "其他錯誤",
}


class AutoState:
    """一個席位行程裡的自動切換狀態。"""

    def __init__(self):
        self.on = False
        self.last_ok: str | None = None
        self.last_ok_at: datetime | None = None
        self.session = ""
        self.session_id = ""
        self.shown = ""          # 上一次寫進右欄的模型(審查 nvauto r1 seat 次要-2)
        self.log_dir: Path | None = None

    def order(self, chain: list[str], now: datetime | None = None) -> list[str]:
        """黏住:換過備援之後 15 分鐘內先用那個備援(別每回合都先撞一次壞掉的主力);主力一路正常就照表走。"""
        now = now or datetime.now(TAIPEI)
        if self.last_ok and self.last_ok in chain and self.last_ok_at and now - self.last_ok_at < STICKY:
            return [self.last_ok] + [m for m in chain if m != self.last_ok]
        return list(chain)

    def mark_ok(self, model: str, switched: bool, now: datetime | None = None) -> None:
        """switched=這一次是前面的候選失敗才輪到它 ⇒ 黏住它 15 分鐘;沒換過就不黏(換小時立刻照新表)。"""
        if switched:
            self.last_ok_at = now or datetime.now(TAIPEI)
        elif model != self.last_ok:
            self.last_ok_at = None
        self.last_ok = model

    def log(self, event: dict) -> None:
        if not self.log_dir:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            row = dict(event, at=datetime.now(TAIPEI).isoformat(timespec="seconds"))
            with open(self.log_dir / "auto-switch.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass


# ── 各席回報(the maintainer 10-02:同時開多隻 NVIDIA,報表要看得出哪筆是誰跑回來的)──
# 每一次呼叫模型(不管 auto 或手動指定)記一行到 usage/<艙位碼>-<席碼>-<台北日期>.jsonl。
# 一席一檔、只追加,同時開很多席也不會互蓋。AIB_NVIDIA_USAGE=0 可關。
USAGE_SCHEMA = 1
_SIMPLIFIED = set("这们说为过时个来对会发么问实现应该还没开关点头机给让请读写")


def seat_label(session_id: str, env=None) -> str:
    env = os.environ if env is None else env
    flat = (session_id or "").replace("-", "").strip().lower()
    seat = flat[-5:] if len(flat) >= 5 and flat[-5:].isalnum() else "anon"
    inst = (env.get("EZ2_AIB_INSTANCE") or "").strip()
    cabin = inst.rsplit("-", 1)[-1][-4:] if inst else ""
    return "%s/%s" % (cabin, seat) if cabin else seat


def reply_traits(reply: dict) -> dict:
    """工具指令格式對不對(工具使用)、有沒有冒出簡體字(照指示)。只數,不判分。"""
    calls = reply.get("tool_calls") or []
    bad = 0
    for call in calls:
        fn = (call or {}).get("function") or {}
        args = fn.get("arguments")
        ok_name = fn.get("name") in ("aib_dm", "aib_local")
        try:
            ok_args = isinstance(args, dict) or isinstance(json.loads(args or "{}"), dict)
        except (TypeError, ValueError):
            ok_args = False
        if not (ok_name and ok_args):
            bad += 1
    text = reply.get("content") or ""
    return {
        "tool_calls": len(calls),
        "tool_bad": bad,
        "text_tool": "<aib_tool>" in text,
        "chars": len(text),
        "simp_hits": sum(1 for ch in text if ch in _SIMPLIFIED),
    }


def record_usage(session_id: str, row: dict, now: datetime | None = None, env=None) -> Path | None:
    env = os.environ if env is None else env
    if env.get("AIB_NVIDIA_USAGE", "1").strip() == "0":
        return None
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    label = seat_label(session_id, env)
    folder = bench_dir() / "usage"
    path = folder / ("%s-%s.jsonl" % (label.replace("/", "-"), now.strftime("%Y%m%d")))
    line = json.dumps(dict(row, schema=USAGE_SCHEMA, at=now.isoformat(timespec="seconds"), seat=label),
                      ensure_ascii=False) + "\n"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))   # 一次 write 寫完一行;O_APPEND 下多席同時寫不會交錯
        finally:
            os.close(fd)
    except OSError:
        return None   # 記不下來不影響回答
    return path


class Exhausted(Exception):
    """這個時段的候選全部失敗。訊息是給人看的一句話。"""
