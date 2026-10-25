#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席 → 綁人格 → 綁專案 一步到位(SPEC-open-seat-bind-3step-7c510-1025;@abb89 10-25 裁:掛 newproj、疊 #373、0.1.61)。

`aib newproj --new-seat --project-uid <uid> --persona-card <卡 uid> [--cabin <艙>] [--role dev] [--engine claude] [--actor …]`
艦長室 2F 任務辦公室「空桌點兩下 = 開一席,而且這席直接為這間的專案工作」要的就是這一支:外掛不自己串三支指令。

階段(回條 stage):resolve → open → persona → project → done。任何一步失敗 ⇒ 停在那步,回 {ok:false, stage, seat, why, hint, next};
**不自動關席、不回滾已綁的**(刪辦公室不殺席同一原則);重跑同一行帶 --seat <艙>/<席> 從卡住那步續做(已綁的跳過)。
why 是代碼(契約),人話只走 hint(四語)。

同帳號 AI 威脅模型(GUIDE-same-user-ai-threat-model):
- 終端機路徑沿用現有規則(誰能跑 aib claude / bind_one 不變)。
- 頁面路徑(`--confirm touchid`,外掛 captain_offices 走這條)要本人:Touch ID 視窗那句由這裡組;沒生物辨識的 Mac ⇒ why=no_biometry 拒、不開、
  hint 列替代路(照 #371);取消 / 逾時 ⇒ 拒。外掛的 offices.json 只管位置,不能當「可以開席」的依據。
- 艙:只收完整艙名;短碼走 #373 那一道(cabin_resolve.sh aib_cabin_pick_named,不另寫一份):對不到 / 多個 ⇒ 開席前就拒,不留空艙。
- 散熱:開席前看負載,偏高 ⇒ why=heat-high(門檻 AIB_SEAT_HEAT_MAX_LOAD,預設 = CPU 數)。
- 兩個頁面同時點兩下別開兩席:開席期間握一把艙鎖(state/seat-bootstrap.lock,O_EXCL),第二個進來 ⇒ why=busy。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

STAGES = ("resolve", "open", "persona", "project", "done")
LOCK_NAME = "seat-bootstrap.lock"
CONFIRM_MODES = ("", "none", "touchid")


def _cockpit_dir(repo_root: Path) -> Path:
    return repo_root / "src" / "cockpit"


def pick_cabin(want: str, instances_root: Path, repo_root: Path, run=subprocess.run) -> tuple[str, str]:
    """艙名解析共用 #373 的 aib_cabin_pick_named(完整名照名、4 碼 hex 唯一對到才換、其餘拒)。⇒ (艙名, 錯誤訊息)。"""
    script = _cockpit_dir(repo_root) / "cabin_resolve.sh"
    try:
        # $0 給個假名:cabin_resolve.sh 用 BASH_SOURCE[0] == $0 判「直接執行」,用 $0 傳路徑會讓它跑進查詢器印 usage
        r = run(["bash", "-c", 'source "$1" && aib_cabin_pick_named "$2" "$3" 0', "seat_bootstrap", str(script), want, str(instances_root)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", "cabin_resolve.sh:%s" % exc.__class__.__name__
    if r.returncode != 0:
        return "", (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "rc=%d" % r.returncode
    name = (r.stdout or "").strip().splitlines()
    return (name[-1].strip() if name else ""), ""


def heat_state(load: Optional[float] = None, limit: Optional[float] = None, environ=None) -> dict:
    """開席前的散熱判斷:1 分鐘負載 > 門檻 ⇒ high。門檻 AIB_SEAT_HEAT_MAX_LOAD(預設 CPU 數);量不到負載 ⇒ 不擋(回 unknown)。"""
    env = os.environ if environ is None else environ
    if limit is None:
        raw = str(env.get("AIB_SEAT_HEAT_MAX_LOAD", "")).strip()
        try:
            limit = float(raw) if raw else float(os.cpu_count() or 4)
        except ValueError:
            limit = float(os.cpu_count() or 4)
    if load is None:
        raw = str(env.get("AIB_SEAT_HEAT_LOAD", "")).strip()   # 測試 seam:平台注入,不在產品路徑上設
        try:
            load = float(raw) if raw else os.getloadavg()[0]
        except (ValueError, OSError, AttributeError):
            return {"state": "unknown", "load": None, "limit": limit}
    return {"state": "high" if load > limit else "ok", "load": round(load, 2), "limit": limit}


def lock_path(root: Path, cabin: str) -> Path:
    return root / "instances" / cabin / "state" / LOCK_NAME


def acquire_lock(path: Path, project_uid: str, stale_s: int, now: Optional[float] = None) -> tuple[bool, dict]:
    """O_EXCL 建鎖;已有而且還新 ⇒ (False, 鎖內容);過期的鎖(開席逾時 + 緩衝)換掉。"""
    now = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"project_uid": project_uid, "pid": os.getpid(), "at": int(now)}
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = {}
            if isinstance(old, dict) and now - float(old.get("at") or 0) <= stale_s:
                return False, old
            try:
                path.unlink()
            except OSError:
                return False, old if isinstance(old, dict) else {}
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        return True, body
    return False, {}


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class Bootstrap:
    """一次執行的狀態;np = new_project 模組(T / load_card / run_newproj / _resolve_seat 都用它的,不另寫一份)。"""

    def __init__(self, args: argparse.Namespace, root: Path, np, say=None):
        self.args, self.root, self.np = args, Path(root), np
        self.say = say or np._say
        self.T = np.T
        self.sig = (args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip()
        self.uid = (args.project_uid or "").strip()
        self.card = (args.persona_card or "").strip()
        self.cabin = ""
        self.seat_id, self.code, self.seat_rec = "", "", None

    # ── 回條 ──
    def cabin_code(self) -> str:
        """右欄 / dm 抬頭用的艙位碼:新式艙名最後一段四碼;主艙與舊流水號照原字。"""
        return self.cabin.rsplit("-", 1)[-1] if self.cabin.count("-") == 2 else self.cabin

    def seat_addr(self) -> Optional[str]:
        return "%s/%s" % (self.cabin_code(), self.code) if self.cabin and self.code else None

    def next_cmd(self) -> str:
        parts = ["aib newproj --new-seat --project-uid %s --persona-card %s" % (self.uid, self.card)]
        if self.seat_addr():
            parts.append("--seat %s" % self.seat_addr())
        elif self.cabin:
            parts.append("--cabin %s" % self.cabin)
        if self.sig:
            parts.append("--actor %s" % self.sig)
        return " ".join(parts)

    def refuse(self, stage: str, why: str, hint: str, rc: int = 2, next_cmd: Optional[str] = None, **extra) -> int:
        out = {"ok": False, "stage": stage, "seat": self.seat_addr(), "why": why, "hint": hint,
               "next": self.next_cmd() if next_cmd is None else next_cmd, "approved": False}
        out.update(extra)
        self.say(hint, "[x]")
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return rc

    # ── 階段 ──
    def resolve(self) -> Optional[int]:
        """不需要席位就能做的檢查全在開席前:署名、帳本、專案卡、人格卡、艙、確認方式。"""
        import bind_one as BO  # noqa: E402
        import create_and_bind_persona as CBP  # noqa: E402
        a, T, root = self.args, self.T, self.root
        if not self.uid or not self.card:
            return self.refuse("resolve", "need-project-and-persona", T("aib.newproj.boot_need_both"))
        if str(getattr(a, "confirm", "") or "").strip().lower() not in CONFIRM_MODES:
            return self.refuse("resolve", "bad-confirm", T("aib.newproj.boot_bad_confirm", {"value": a.confirm}))
        if not self.sig:
            return self.refuse("resolve", "need-actor", T("aib.newproj.need_actor"))
        try:
            BO.actor_of(self.sig)
        except BO.BindError as exc:
            return self.refuse("resolve", "bad-actor", str(exc))
        if not (root / "ledger").is_dir():
            return self.refuse("resolve", "no-ledger", T("aib.newproj.no_ledger", {"path": root / "ledger"}))
        if self.np.load_card(root, self.uid) is None:
            return self.refuse("resolve", "project-not-found", self.np._uid_refusal(root, self.uid))
        try:
            cards = BO.load_card_registry(str(BO.default_cards_path(root)))
            self.card, _sha, _entry = BO.resolve_card_ref(cards, self.card)
        except Exception as exc:  # noqa: BLE001  登記表讀不到 / 卡不在表裡:都是「開席前就該擋」
            return self.refuse("resolve", "persona-not-found", T("aib.newproj.boot_persona_missing", {"card": self.card, "err": exc}))
        role, role_err = CBP.new_seat_role_or_die(getattr(a, "role", ""))
        if role_err:
            return self.refuse("resolve", "bad-role", role_err)
        self.role = role
        if a.seat:                                           # 續做:席已在,不解析艙、不開席
            seat_id, rec, instance, err = self.np._resolve_seat(root, a.seat.strip())
            if err:
                return self.refuse("resolve", "seat-not-found", err)
            self.seat_id, self.seat_rec, self.cabin = seat_id, rec, instance or (rec or {}).get("cabin_id") or ""
            self.code = str((rec or {}).get("session_uuid") or "")[-5:] or seat_id
            return None
        want = (getattr(a, "cabin", "") or "").strip() or CBP.current_cabin()
        if not want:
            return self.refuse("resolve", "no-cabin", T("aib.newproj.boot_no_cabin"))
        cabin, err = pick_cabin(want, root / "instances", CBP.REPO_ROOT)
        if err or not cabin:
            return self.refuse("resolve", "cabin-unresolved", T("aib.newproj.boot_cabin_unresolved", {"cabin": want, "err": err}))
        self.cabin = cabin
        if not (root / "instances" / cabin / "seats").is_dir():
            return self.refuse("resolve", "cabin-no-seats", T("aib.newproj.boot_cabin_unresolved", {"cabin": cabin, "err": "seats/"}))
        return None

    def confirm(self) -> Optional[int]:
        """頁面路徑的本人確認。終端機路徑(--confirm 沒給 / none)沿用現有規則,這裡不另加一道。"""
        mode = str(getattr(self.args, "confirm", "") or "").strip().lower()
        if mode in ("", "none"):
            return None
        sys.path.insert(0, str(_cockpit_dir(Path(__import__("create_and_bind_persona").REPO_ROOT))))
        import touchid_approve  # noqa: E402
        card_title = self.card
        proj = (self.np.load_card(self.root, self.uid) or {}).get("title") or self.uid
        reason = self.T("aib.newproj.boot_confirm", {"cabin": self.cabin, "role": self.role,
                                                     "persona": touchid_approve.clean(card_title), "project": touchid_approve.clean(proj)})
        if not touchid_approve.available():
            return self.refuse("resolve", "no_biometry", self.T("aib.newproj.boot_no_biometry", {"cmd": self.next_cmd()}), next_cmd=self.next_cmd())
        r = touchid_approve.ask(reason)
        if not r.get("ok"):
            return self.refuse("resolve", "touchid_%s" % r.get("why", "denied"), self.T("aib.newproj.boot_denied", {"why": r.get("why")}))
        return None

    def open(self) -> Optional[int]:
        import create_and_bind_persona as CBP  # noqa: E402
        T = self.T
        heat = heat_state()
        if heat["state"] == "high":
            return self.refuse("open", "heat-high", T("aib.newproj.boot_heat_high", {"load": heat["load"], "limit": heat["limit"]}), heat=heat)
        timeout = CBP.new_seat_timeout()
        lock = lock_path(self.root, self.cabin)
        got, holder = acquire_lock(lock, self.uid, stale_s=timeout + 60)
        if not got:
            return self.refuse("open", "busy", T("aib.newproj.boot_busy", {"cabin": self.cabin, "uid": holder.get("project_uid") or "?"}),
                               next_cmd=self.next_cmd())
        try:
            if self.args.dry_run:
                self.say(T("aib.newproj.dry_new_seat", {"instance": self.cabin, "engine": self.args.engine, "role": self.role}))
                return None
            CBP.AIB_HOME = self.root
            try:
                self.seat_id, self.seat_rec, _inst = CBP.open_new_seat(self.role, engine=self.args.engine, cabin=self.cabin, timeout=timeout,
                                                                      model=getattr(self.args, "model", "") or "")
            except CBP.NewSeatError as exc:
                if getattr(exc, "opened", False):
                    return self.refuse("open", "open-timeout", T("aib.newproj.boot_open_timeout", {"msg": exc}), seat_opened=True)
                return self.refuse("open", "open-failed", str(exc))
            self.code = str((self.seat_rec or {}).get("session_uuid") or "")[-5:] or self.seat_id
        finally:
            release_lock(lock)
        return None

    def persona(self) -> Optional[int]:
        import bind_one as BO  # noqa: E402
        T, root = self.T, self.root
        if self.args.dry_run:
            self.say(T("aib.newproj.boot_dry_persona", {"seat": self.seat_addr() or "(new seat)", "card": self.card}))
            return None
        try:
            agent = BO.seat_status(root, self.seat_id).get("agent") or {}
        except Exception:  # noqa: BLE001  從沒綁過
            agent = {}
        bound = str(agent.get("persona_card") or "")
        if bound == self.card:
            self.say(T("aib.newproj.boot_persona_kept", {"seat": self.seat_addr(), "card": self.card}))
            return None
        if bound:
            # 換人格是敏感動作,一步到位不代勞;要換走 newrole --rebind
            return self.refuse("persona", "persona-differs", T("aib.newproj.boot_persona_differs", {"seat": self.seat_addr(), "bound": bound, "card": self.card}))
        route = str((self.np.load_card(root, self.uid) or {}).get("route_key") or "")
        try:
            plan = BO.bind_seat(root, self.seat_id, self.card, BO.actor_of(self.sig), brain=route or None,
                                contract_spec=BO.default_contract_spec())
        except BO.BindError as exc:
            return self.refuse("persona", "bind-failed", T("aib.newproj.boot_persona_failed", {"seat": self.seat_addr(), "err": exc}))
        for d in ((plan or {}).get("reproject") or {}).get("seat_unresolved") or []:
            self.say(T("aib.newproj.seat_dropped", {"seat": self.seat_id, "reason": d.get("reason"), "detail": d.get("detail"),
                                                    "contract": BO.default_contract_spec() or "-"}))
        self.say(T("aib.newproj.boot_persona_bound", {"seat": self.seat_addr(), "card": self.card, "route": route or "-"}))
        return None

    def project(self) -> tuple[Optional[int], dict]:
        a = self.args
        ns = argparse.Namespace(**vars(a))
        ns.seat, ns.new_seat, ns.persona_card, ns.cabin, ns.confirm = self.seat_id, False, "", "", ""
        ns.title, ns.folder, ns.persona_uid, ns.unbind = "", "", "", False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.np.run_newproj(ns)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        for ln in lines[:-1]:
            print(ln)
        try:
            inner = json.loads(lines[-1]) if lines else {}
        except ValueError:
            inner = {}
        if rc != 0:
            return self.refuse("project", "newproj-rc-%d" % rc, str(inner.get("error") or self.T("aib.newproj.boot_project_failed")), rc=rc), inner
        return None, inner if isinstance(inner, dict) else {}

    def run(self) -> int:
        rc = self.resolve()
        if rc is not None:
            return rc
        if not self.seat_id:
            rc = self.confirm()
            if rc is not None:
                return rc
            rc = self.open()
            if rc is not None:
                return rc
            if self.args.dry_run:
                print(json.dumps({"ok": True, "dry_run": True, "stage": "resolve", "seat": None, "cabin": self.cabin, "role": self.role,
                                  "persona_card": self.card, "project_uid": self.uid, "approved": False}, ensure_ascii=False, sort_keys=True))
                return 0
        rc = self.persona()
        if rc is not None:
            return rc
        rc, inner = self.project()
        if rc is not None:
            return rc
        out = {"ok": True, "stage": "done", "seat": self.seat_addr(), "seat_id": self.seat_id, "code": self.code,
               "persona_card": self.card, "project_uid": self.uid, "route_key": inner.get("route_key") or "",
               "cabin": self.cabin, "approved": False, "dry_run": bool(self.args.dry_run)}
        self.say(self.T("aib.newproj.boot_done", {"seat": self.seat_addr(), "card": self.card, "uid": self.uid}))
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0


def run(args: argparse.Namespace, root: Path, np) -> int:
    return Bootstrap(args, root, np).run()
