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
- 散熱:開席前跑核心 `aib room heat` 同一支 room_heat.measure()(cooldown 四級),hot / critical ⇒ why=heat-high;不另放門檻。
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


HEAT_BLOCK_LEVELS = ("hot", "critical")   # cooldown 四級裡「偏紅」的兩級


def heat_state(repo_root: Optional[Path] = None, measure=None) -> dict:
    """開席前的散熱判斷:直接用核心 `aib room heat` 同一支 room_heat.measure()(machine_status 量測 + cooldown 四級門檻:
    負載 / 溫度 / 記憶體),外掛顯示的和這裡擋的是同一個數字(@abb89 10-25)。hot / critical ⇒ block;量不到 ⇒ 不擋、照實回。"""
    if measure is None:
        cockpit = str(_cockpit_dir(repo_root or Path(__import__("create_and_bind_persona").REPO_ROOT)))
        if cockpit not in sys.path:
            sys.path.insert(0, cockpit)
        import room_heat  # noqa: E402
        measure = room_heat.measure
    try:
        m = measure() or {}
    except Exception as exc:  # noqa: BLE001  量測壞了不等於機器涼:照實回 unmeasured,但不擋
        m = {"level": "unmeasured", "detail": exc.__class__.__name__}
    level = str(m.get("level") or "unmeasured")
    cells = m.get("cells") or {}
    text = "、".join(str(c.get("text")) for k, c in cells.items() if isinstance(c, dict) and c.get("state") in HEAT_BLOCK_LEVELS and c.get("text"))
    return {"level": level, "block": level in HEAT_BLOCK_LEVELS, "detail": text or str(m.get("detail") or "")}


CLI_PROBE_TIMEOUT_S = 10   # @abb89 10-26:探測要有逾時、不觸發 Touch ID、不花錢(只查本機登入狀態與模型表)


def cli_ready(engine: str, model: str = "", repo_root: Optional[Path] = None, which=None, logged_in=None, models=None) -> dict:
    """開席前 AI CLI 就緒三項(@abb89 10-26;共用核心現成的探測,不另寫):
    installed = aib doctor 同一招(shutil.which);login = 動力室 power_login.logged_in(10 分鐘快取,探測逾時 10 秒);
    model = providers.json 那一家列的模型(沒給 --model 就不查)。回 {ok, failed: [installed|login|model…], engine, model}。
    登入「量不到」(None,例如那一家沒有狀態指令)不算不過:只有明確 False 才擋,不然 grok / agy 永遠開不了。"""
    import shutil
    cockpit = _cockpit_dir(repo_root or Path(__import__("create_and_bind_persona").REPO_ROOT))
    if str(cockpit) not in sys.path:
        sys.path.insert(0, str(cockpit))
    which = which or shutil.which
    failed = []
    if not which(engine):
        failed.append("installed")
    else:
        if logged_in is None:
            import power_login
            logged_in = lambda key: power_login.logged_in(key, run=lambda cmd: power_login._run(cmd, timeout=CLI_PROBE_TIMEOUT_S))  # noqa: E731
        try:
            if logged_in(engine) is False:
                failed.append("login")
        except Exception:  # noqa: BLE001  探測壞了 = 量不到,不當不過
            pass
    if model:
        if models is None:
            def models(eng):
                import json as _json
                try:
                    with open(cockpit / "providers.json", encoding="utf-8") as f:
                        provs = _json.load(f).get("providers") or []
                except (OSError, ValueError, AttributeError):
                    return None
                for p in provs:
                    if isinstance(p, dict) and p.get("id") == eng:
                        return [m.get("id") for m in (p.get("models") or []) if isinstance(m, dict)]
                return None
        known = models(engine)
        if known is not None and model not in known:
            failed.append("model")
    return {"ok": not failed, "failed": failed, "engine": engine, "model": model or ""}


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
        self.brain_route, self.brain_plan = "", None     # --folder 解出來的路由;plan 非空 = 要先建新夾(--folder new)

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
        want = str(getattr(self.args, "folder", "") or "").strip()
        if want:                                   # #469 r1 M3:續做那一行要帶著大腦的選擇,不然重跑會變成沒綁大腦
            import shlex
            parts.append("--folder %s" % shlex.quote(want))
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
        if not self.np.ensure_ledger(root, create=not a.dry_run):   # 10-28 G1:aib init 沒跑成的新會員,開第一席時補建帳本
            return self.refuse("resolve", "no-ledger", T("aib.newproj.no_ledger", {"path": root / "ledger"}))
        if self.np.load_card(root, self.uid) is None:
            return self.refuse("resolve", "project-not-found", self.np._uid_refusal(root, self.uid))
        try:
            cards = BO.load_card_registry(str(BO.default_cards_path(root)))
            self.card, _sha, entry = BO.resolve_card_ref(cards, self.card)
            self.card_name = str((entry or {}).get("display_name") or self.card)
        except Exception as exc:  # noqa: BLE001  登記表讀不到 / 卡不在表裡:都是「開席前就該擋」
            return self.refuse("resolve", "persona-not-found", T("aib.newproj.boot_persona_missing", {"card": self.card, "err": exc}))
        role, role_err = CBP.new_seat_role_or_die(getattr(a, "role", ""))
        if role_err:
            return self.refuse("resolve", "bad-role", role_err)
        self.role = role
        if not a.seat:
            # 開席前 AI CLI 要就緒(@abb89 10-26):任一項不過 ⇒ 不開席、不建夾;登入過期開出來的席起不來,會變成「看起來有人的空桌」
            ready = cli_ready(a.engine or "claude", getattr(a, "model", "") or "", CBP.REPO_ROOT)
            if not ready["ok"]:
                import power_login  # noqa: E402  (cli_ready 已把 cockpit 放進 sys.path)
                login_cmd = (power_login.LOGIN.get(ready["engine"]) or {}).get("cmd") or "aib doctor"
                return self.refuse("resolve", "cli_not_ready",
                                   T("aib.newproj.boot_cli_not_ready", {"engine": ready["engine"], "detail": "/".join(ready["failed"]), "cmd": login_cmd}),
                                   detail=ready["failed"])
        rc = self.pick_brain()
        if rc is not None:
            return rc
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

    def pick_brain(self) -> Optional[int]:
        """--folder(11-01 Andy「直接接已有大腦,或綁目前第一席的第二大腦」):開席前就解析、驗契約,解不出來不開席。
        寫法:路由代號 / 資料夾 / seat:<艙位碼>/<席碼>(跟那一席同一個大腦)/ new(照專案標題建新夾)。"""
        import bind_one as BO  # noqa: E402
        import brain_pick  # noqa: E402
        want = str(getattr(self.args, "folder", "") or "").strip()
        if not want:
            return None
        T = self.T
        contract = BO.load_contract(BO.default_contract_spec())
        if contract is None:
            return self.refuse("resolve", "brain-no-contract", T("aib.newproj.no_folder_list"))
        route, plan, err = brain_pick.resolve_pick(self.np, self.root, contract, want, self.uid)
        if err or not route:
            return self.refuse("resolve", "brain-unresolved", str(err or T("aib.newproj.folder_unknown", {"value": want})))
        card_route = str((self.np.load_card(self.root, self.uid) or {}).get("route_key") or "")
        if card_route and card_route != route:
            # 卡已經記了別的大腦:換專案的大腦不在一步開席裡代做(跟 newproj --seat 同一條規則)
            return self.refuse("resolve", "folder-mismatch", T("aib.newproj.folder_mismatch", {"key": card_route, "path": card_route, "value": route}))
        if plan is None:
            ok, code, detail = BO.check_against_contract(route, contract)
            if not ok:
                return self.refuse("resolve", "brain-rejected", T("aib.newproj.folder_rejected", {"key": route, "code": code, "detail": detail}))
        self.brain_route, self.brain_plan = route, plan
        return None

    def make_brain(self) -> Optional[int]:
        """--folder new 的建夾(folder-add --route-key + reindex)。席開好、綁人格之前做;建過的(重跑)resolve 那步就直接接了。"""
        if not self.brain_plan:
            return None
        import brain_pick  # noqa: E402
        c = self.brain_plan["create"]
        if self.args.dry_run:
            self.say(self.T("aib.newproj.brain_new_dry", {"title": c["title"], "key": c["route_key"]}))
            return None
        try:
            brain_pick.create_new(self.brain_plan)
        except brain_pick.ReindexWarning as exc:     # 夾和代號都在了,只是 reindex 沒跑成:照常綁,提醒一句
            self.say(self.T("aib.newproj.brain_new_reindex_warn", {"title": c["title"], "key": c["route_key"], "err": exc}), "[!]")
        except SystemExit as exc:
            return self.refuse("persona", "brain-create-failed", self.T("aib.newproj.brain_new_failed", {"title": c["title"], "err": exc.code}))
        except (OSError, ValueError, KeyError) as exc:
            return self.refuse("persona", "brain-create-failed", self.T("aib.newproj.brain_new_failed", {"title": c["title"], "err": exc}))
        else:
            self.say(self.T("aib.newproj.brain_new_made", {"title": c["title"], "key": c["route_key"]}))
        self.brain_plan = None
        return None

    def confirm(self) -> Optional[int]:
        """頁面路徑的本人確認。終端機路徑(--confirm 沒給 / none)沿用現有規則,這裡不另加一道。"""
        mode = str(getattr(self.args, "confirm", "") or "").strip().lower()
        if mode in ("", "none"):
            return None
        sys.path.insert(0, str(_cockpit_dir(Path(__import__("create_and_bind_persona").REPO_ROOT))))
        import approve_gate as AG  # noqa: E402  統一入口:類別 seat_input
        import touchid_approve  # noqa: E402
        card_title = self.card
        proj = (self.np.load_card(self.root, self.uid) or {}).get("title") or self.uid
        reason = AG.reason("newproj.boot_confirm", {"cabin": self.cabin, "role": self.role, "persona": card_title, "project": proj})
        if not touchid_approve.available():
            return self.refuse("resolve", "no_biometry", self.T("aib.newproj.boot_no_biometry", {"cmd": self.next_cmd()}), next_cmd=self.next_cmd())
        r = AG.ask(reason, AG.state_root_default())
        if not r.get("ok"):
            hint = self.T("aib.newproj.boot_denied", {"why": r.get("why")})
            extra = AG.too_long_hint(r)
            if extra:                # 代碼不換(touchid_refused),只多一句人話(#420 r1 L1)
                hint = hint + " " + extra
            return self.refuse("resolve", "touchid_%s" % r.get("why", "denied"), hint, detail=r.get("detail"))
        return None

    def open(self) -> Optional[int]:
        import create_and_bind_persona as CBP  # noqa: E402
        T = self.T
        heat = heat_state()
        if heat["block"]:
            return self.refuse("open", "heat-high", T("aib.newproj.boot_heat_high", {"level": heat["level"], "detail": heat["detail"] or "-"}), heat=heat)
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
        route = self.brain_route or str((self.np.load_card(root, self.uid) or {}).get("route_key") or "")
        try:
            plan = BO.bind_seat(root, self.seat_id, self.card, BO.actor_of(self.sig), brain=route or None,
                                contract_spec=BO.default_contract_spec())
        except BO.BindError as exc:
            return self.refuse("persona", "bind-failed", T("aib.newproj.boot_persona_failed", {"seat": self.seat_addr(), "err": exc}))
        for d in ((plan or {}).get("reproject") or {}).get("seat_unresolved") or []:
            self.say(T("aib.newproj.seat_dropped", {"seat": self.seat_id, "reason": d.get("reason"), "detail": d.get("detail"),
                                                    "contract": BO.default_contract_spec() or "-"}))
        self.say(T("aib.newproj.boot_persona_bound", {"seat": self.seat_addr(), "card": self.card, "route": route or "-"}))
        if getattr(self.args, "deliver", False):
            # 正在跑的新席不會自己知道被綁了:人格包用 aib dm 寄過去(newrole --deliver 同一支);寄不到不改變「已綁」,只照實回
            import create_and_bind_persona as CBP  # noqa: E402
            CBP.AIB_HOME = root
            r = CBP.deliver_persona(self.seat_id, self.seat_rec, self.cabin, getattr(self, "card_name", self.card), CBP._default_vault())
            self.persona_pack = {k: r.get(k) for k in ("ok", "address", "stage", "reason", "expect_reply") if k in r}
            key = "aib.newproj.boot_pack_sent" if r.get("ok") else "aib.newproj.boot_pack_failed"
            self.say(T(key, {"seat": self.seat_addr(), "reason": r.get("reason") or r.get("stage") or "-"}), "[*]" if r.get("ok") else "[!]")
        return None

    def project(self) -> tuple[Optional[int], dict]:
        a = self.args
        ns = argparse.Namespace(**vars(a))
        ns.seat, ns.new_seat, ns.persona_card, ns.cabin, ns.confirm = self.seat_id, False, "", "", ""
        ns.title, ns.folder, ns.persona_uid, ns.unbind = "", self.brain_route or "", "", False   # 有 --folder ⇒ newproj 補記卡的 route_key
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
                self.make_brain()
                print(json.dumps({"ok": True, "dry_run": True, "stage": "resolve", "seat": None, "cabin": self.cabin, "role": self.role,
                                  "persona_card": self.card, "project_uid": self.uid, "route_key": self.brain_route or None,
                                  "brain_new": bool(self.brain_plan), "approved": False}, ensure_ascii=False, sort_keys=True))
                return 0
        rc = self.make_brain()
        if rc is not None:
            return rc
        rc = self.persona()
        if rc is not None:
            return rc
        rc, inner = self.project()
        if rc is not None:
            return rc
        out = {"ok": True, "stage": "done", "seat": self.seat_addr(), "seat_id": self.seat_id, "code": self.code,
               "persona_card": self.card, "project_uid": self.uid, "route_key": inner.get("route_key") or "",
               "cabin": self.cabin, "approved": False, "dry_run": bool(self.args.dry_run),
               "persona_pack": getattr(self, "persona_pack", None)}
        self.say(self.T("aib.newproj.boot_done", {"seat": self.seat_addr(), "card": self.card, "uid": self.uid}))
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0


def run(args: argparse.Namespace, root: Path, np) -> int:
    return Bootstrap(args, root, np).run()
