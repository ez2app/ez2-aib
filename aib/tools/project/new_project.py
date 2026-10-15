#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""新開專案(包 O):建專案卡 + 從人格生成憲法初稿(候選、不生效) + 綁本席。

設計:docs/DESIGN-persona-autoload-0926.md §10.2 / §10.5 / J6。
免費全開(無 license 閘)。憲法 approved 必須保持 False,直到本人核准。

    python3 tools/project/new_project.py --seat SEAT [--title T] [--cwd PATH]
        [--state-root DIR] [--persona-uid UID] [--dry-run]

    aib newproj --title T --seat <艙位碼>/<席碼> --actor role@五碼/引擎 [--cwd PATH]
        [--project-uid UID] [--deliver] [--unbind] [--dry-run]        ← 09-29 a seat
      同一支程式加 `--entry newproj`(入口自己帶,人不用打)。與選單那條路的差別:
        · --seat 收完整地址/席碼(只認活席);署名必填,綁定先進帳本再寫檔
        · 同標題的專案已經有一張 ⇒ 沿用那一張、只綁席位(不另開、不改卡)
        · --deliver 把接手包用 aib dm 寄給那一席;--unbind 把這一席的專案解掉
        · --folder 專案對應的資料夾(the maintainer 09-29:「資料夾本來就是專案」「專案卡要記下對應的資料夾」):
          專案卡記下 route_key 與 folder,那一席右欄的資料夾也一起綁上;不給就沿用那一席現在綁的資料夾
        · 不給 --seat(也沒給 --new-seat)⇒ 只建專案卡,不開席、不綁任何席(the maintainer 10-01 裁,取代 09-30
          「沒說就開新席」);回一句「已創立 XXX 專案(uid …)」。--new-seat 只有明講才開新席
      rc:0 完成 / 1 建卡失敗(沿用選單那條路的碼)/ 2 寫檔前就拒(席位、署名、標題撞名、uid 不存在)
          / 6 綁好了但接手包沒寄到

stdout JSON:{ok, project_uid, card_path, candidate_path, seat, approved:false}
不印 secrets。

落檔:
  $EZ2_AIB_HOME/projects/<project_uid>/card.json
  $EZ2_AIB_HOME/projects/<project_uid>/candidates/constitution-draft.json
席位綁定:seat-continuity 與席位檔寫入 project_uid(+project_title),供 pick_project 見。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY = REPO_ROOT / "src" / "identity"
if str(IDENTITY) not in sys.path:
    sys.path.insert(0, str(IDENTITY))

import project_card as PC  # noqa: E402
from project_pick import pick_project  # noqa: E402

# aib newproj 那條路的畫面字(stderr 的 [*] / [x] 行、拒絕理由、--help)一律走四語字典
# (i18n/{zh-TW,en,ja,ko}.json,鍵 aib.newproj.*);09-29 起它是 aib newproj 與四家技能的介面,不只給艦長席看
# (test-tools-i18n-ratchet 第 94 項那條:做成客戶介面時同顆接上四語)。字典根用本樹的,不讀主庫那份。
_COCKPIT = REPO_ROOT / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.append(str(_COCKPIT))
import i18n_lib  # noqa: E402


_TRANSLATOR: list = []


def T(key: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """語系:EZ2_AIB_LOCALE > zh-TW(i18n_lib 的規則);字典一個行程只載一次。"""
    if not _TRANSLATOR:
        _TRANSLATOR.append(i18n_lib.Translator(i18n_dir=REPO_ROOT / "i18n"))
    return _TRANSLATOR[0].t(key, dict(params) if params else None)

SEAT_ID_RE = re.compile(r"^[0-9a-z]{4,8}$")
# 同一席+同一倉指紋在此秒數內重點 ⇒ 重用 uid(防連點);否則每次新 uid。
IDEMPOTENCY_WINDOW_S = 30
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
_SECTION_HDR_RE = re.compile(r"^##\s+(.+?)\s*$")


class NewProjectError(RuntimeError):
    """可回報給選單的失敗。"""


def state_root_of(explicit: Optional[str] = None) -> Path:
    """狀態根:--state-root > EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib(跟 seats_dir_lib 單源同一條順序)。
    10-13 a seat 准:以前只認 EZ2_AIB_HOME,跟 aib memory tidy(先認 EZ2_AIB_STATE_ROOT)只設 STATE_ROOT 時拿到不同的卡與卡鎖。
    只設 EZ2_AIB_HOME 的舊行為不變(測試釘著)。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    for name in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(name)
        if v:
            return Path(os.path.expanduser(v)).resolve()
    return Path(os.path.expanduser("~/.ez2-aib")).resolve()


def new_project_uid() -> str:
    return "proj:%s" % secrets.token_hex(8)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_git(cwd: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def repo_fingerprints_for(cwd: Path) -> dict[str, str]:
    """倉指紋:key=repo basename(或 path);value=sha256(HEAD) 或 sha256(remote+root)。"""
    root = cwd.resolve()
    git_root = _run_git(root, "rev-parse", "--show-toplevel")
    if git_root:
        gr = Path(git_root)
        head = _run_git(gr, "rev-parse", "HEAD")
        key = gr.name or str(gr)
        if head:
            return {key: _sha256_hex(head)}
        remote = _run_git(gr, "config", "--get", "remote.origin.url") or ""
        return {key: _sha256_hex("%s|%s" % (remote, gr))}
    # 非 git:用路徑(+optional remote 空)指紋
    key = root.name or str(root)
    return {key: _sha256_hex("nogit|%s" % root)}


def _load_json(path: Path) -> Optional[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


class _card_lock:
    """專案卡讀改寫的鎖(<卡資料夾>/card.lock,fcntl,10 秒拿不到就丟例外);aib memory tidy 用同一把。"""

    def __init__(self, card_path: Path):
        self.path = Path(card_path).parent / "card.lock"

    def __enter__(self):
        import fcntl
        import time as _t
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a")
        end = _t.time() + 10
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if _t.time() > end:
                    self.f.close()
                    raise
                _t.sleep(0.1)

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _archive_snapshot(path: Path, kind: str, key: str) -> dict:
    """改卡前快照(src/memory/memory_archive.py,記憶整理第 1 包)。檔不在 ⇒ skipped;
    找不到 memory_archive.py(測試副本樹)⇒ skipped 並記一句;檔在但載入或拍壞 ⇒ failed(呼叫端不准寫)。"""
    if not Path(path).exists():
        return {"status": "skipped", "error": "no file yet"}
    found = Path(__file__).resolve().parents[2] / "src" / "memory" / "memory_archive.py"
    if not found.is_file():
        print(T("aib.newproj.archive_unavailable"), file=sys.stderr)
        return {"status": "skipped", "error": "memory_archive unavailable"}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("memory_archive_for_newproj", str(found))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.snapshot(path, kind, key)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": "memory_archive: %s" % exc}


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    name, buf = "", []
    for line in (body or "").split("\n"):
        m = _SECTION_HDR_RE.match(line)
        if m:
            if "\n".join(buf).strip():
                out[name] = "\n".join(buf).strip()
            name, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        out[name] = "\n".join(buf).strip()
    return out


def _lines_from_section(section_body: str) -> list[str]:
    """紅線／做事方式 → list[str]。有 bullet 用 bullet;否則整段當一條(截短)。"""
    lines: list[str] = []
    for ln in (section_body or "").split("\n"):
        m = _BULLET_RE.match(ln)
        if m:
            text = m.group(1).strip()
            if text:
                lines.append(text[:500])
    if lines:
        return lines
    compact = " ".join((section_body or "").split())
    if compact:
        return [compact[:500]]
    return []


def resolve_persona_uid(
    state_root: Path,
    seat: str,
    explicit: Optional[str] = None,
) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    cont = _load_json(state_root / "identity" / "seat-continuity" / ("%s.json" % seat))
    if cont:
        uid = str(cont.get("persona_uid") or "").strip()
        if uid:
            return uid
    return ""


def load_persona_red_lines(
    state_root: Path,
    persona_uid: str,
) -> list[str]:
    """從人格卡抽出紅線 + 做事方式暗示。缺卡 ⇒ 空 list(仍建空憲法)。"""
    if not persona_uid:
        return []
    cards_doc = _load_json(state_root / "identity" / "cards.json") or {}
    entries = cards_doc.get("cards") if isinstance(cards_doc.get("cards"), dict) else {}
    entry = entries.get(persona_uid) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return []
    rel = str(entry.get("card_path_rel") or "").strip()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return []
    source = cards_doc.get("source") if isinstance(cards_doc.get("source"), dict) else {}
    vault_root = (
        str(entry.get("vault_root") or "").strip()
        or str(source.get("vault_root") or "").strip()
        or os.environ.get("EZ2_VAULT_DIR", "")
        or str(Path.home() / "Ez2" / "Vault")
    )
    card_path = Path(vault_root).expanduser() / rel
    try:
        text = card_path.read_text(encoding="utf-8")
    except OSError:
        return []
    # 去 frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    sections = _parse_sections(text)
    red: list[str] = []
    for key in ("紅線", "boundaries", "Boundaries"):
        if key in sections:
            red.extend(_lines_from_section(sections[key]))
            break
    for key in ("語氣與口癖", "職責", "stylebook", "working-style"):
        if key in sections:
            for item in _lines_from_section(sections[key]):
                red.append("做事方式: %s" % item)
            break
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for item in red:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def constitution_draft_from_persona(
    state_root: Path,
    persona_uid: str,
) -> dict[str, Any]:
    draft = PC.empty_constitution()
    draft["red_lines"] = load_persona_red_lines(state_root, persona_uid)
    draft["goals"] = []
    draft["approved"] = False
    return draft


def project_dir(state_root: Path, project_uid: str) -> Path:
    return state_root / "projects" / project_uid


def card_path_of(state_root: Path, project_uid: str) -> Path:
    return project_dir(state_root, project_uid) / "card.json"


def candidate_path_of(state_root: Path, project_uid: str) -> Path:
    return project_dir(state_root, project_uid) / "candidates" / "constitution-draft.json"


def _recent_reuse_uid(
    state_root: Path,
    seat: str,
    fingerprints: Mapping[str, str],
    now: float,
    title: str = "",
) -> Optional[str]:
    """同一席+同一指紋+同一標題在 IDEMPOTENCY_WINDOW_S 內 ⇒ 重用既有 uid。

    ★ 09-30:這個窗是給「選單連點兩下」用的(同一件事按兩次不開兩張)。沒比標題時,
      同一席 30 秒內明講「另一個專案」也會被併進剛綁的那張(卡被改寫成新標題、少一張卡)。
      標題不同就是不同的專案,不重用。
    """
    cont = _load_json(state_root / "identity" / "seat-continuity" / ("%s.json" % seat))
    if not cont:
        return None
    uid = str(cont.get("project_uid") or "").strip()
    if not uid:
        return None
    updated_ms = cont.get("updated_wall_ms")
    try:
        age_s = now - (int(updated_ms) / 1000.0)
    except (TypeError, ValueError):
        return None
    if age_s < 0 or age_s > IDEMPOTENCY_WINDOW_S:
        return None
    card = _load_json(card_path_of(state_root, uid))
    if not card:
        return None
    fps = card.get("repo_fingerprints") or {}
    if not isinstance(fps, dict):
        return None
    if dict(fps) != dict(fingerprints):
        return None
    if title and str(card.get("title") or "").strip() != title:
        return None
    return uid


def _update_continuity(
    state_root: Path,
    seat: str,
    project_uid: str,
    title: str,
    now_ms: int,
) -> Path:
    path = state_root / "identity" / "seat-continuity" / ("%s.json" % seat)
    rec = _load_json(path) or {
        "record": "seat-continuity-v1",
        "seat_id": seat,
        "agent_uid": "",
        "session_uuid": "",
        "persona_uid": "",
        "persona_sha": None,
        "brain_route_key": None,
    }
    if rec.get("record") != "seat-continuity-v1":
        rec["record"] = "seat-continuity-v1"
    rec["seat_id"] = seat
    rec["project_uid"] = project_uid
    if title:
        rec["project_title"] = title
    rec["updated_wall_ms"] = int(now_ms)
    _atomic_write_json(path, rec)
    return path


def _update_seat_file(
    state_root: Path,
    seat: str,
    project_uid: str,
    title: str,
) -> Optional[Path]:
    """席位檔加 project_uid(供 pick_project(seat_record=…));找不到不擋。"""
    try:
        import bind_one as BO  # noqa: E402
    except ImportError:
        BO = None
    path = None
    rec: Optional[dict] = None
    if BO is not None:
        try:
            path_s, rec = BO.find_seat(state_root, seat)
            path = Path(path_s)
        except Exception:
            path, rec = None, None
    if path is None or not isinstance(rec, dict):
        # 寬鬆掃描(測試治具可能只有 seats/wN.json)
        hits = []
        for p in sorted(Path(x) for x in BO.seat_file_paths(state_root)) if BO is not None else []:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict) and raw.get("seat_id") == seat:
                hits.append((p, raw))
        if len(hits) == 1:
            path, rec = hits[0]
        else:
            return None
    assert rec is not None and path is not None
    rec = dict(rec)
    rec["project_uid"] = project_uid
    if title:
        rec["project_title"] = title
    _atomic_write_json(path, rec)
    _registry(state_root).bind_quietly(project_uid, seat, title=title, by="newproj", root=state_root)
    return path


def _registry(state_root: Path):
    """席綁專案的唯一正本(src/cockpit/project_seats.py,docs/DESIGN-project-sweep-1012.md §2)。"""
    import project_seats  # noqa: E402 —— _COCKPIT 已在 sys.path
    return project_seats


def create_project(
    *,
    seat: str,
    title: str = "",
    cwd: Optional[Path] = None,
    state_root: Optional[Path] = None,
    persona_uid: str = "",
    dry_run: bool = False,
    now: Optional[float] = None,
    before_write: Any = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
    bind: bool = True,
) -> dict[str, Any]:
    """before_write(project_uid, title):第一個位元組落檔之前呼叫(newproj 用它先進帳本);它丟例外 ⇒ 什麼都沒寫。

    bind=False(aib newproj 沒指定席位,the maintainer 10-01):只建卡與憲法候選,不碰任何席位檔與延續檔;seat 必須是空的。"""
    if bind and not SEAT_ID_RE.fullmatch(seat or ""):
        raise NewProjectError("seat 形狀不合: %r" % (seat,))
    if not bind and seat:
        raise NewProjectError("bind=False takes no seat: %r" % (seat,))
    root = state_root if state_root is not None else state_root_of()
    work = (cwd or Path.cwd()).expanduser().resolve()
    fps = PC.validate_repo_fingerprints(repo_fingerprints_for(work))
    ts = time.time() if now is None else float(now)
    now_ms = int(ts * 1000)

    persona = resolve_persona_uid(root, seat, persona_uid or None) if bind else (persona_uid or "").strip()
    card_title = (title or "").strip() or work.name
    reuse = _recent_reuse_uid(root, seat, fps, ts, title=card_title) if bind else None
    project_uid = reuse or new_project_uid()
    PC.validate_project_uid(project_uid)

    constitution = constitution_draft_from_persona(root, persona)
    assert constitution.get("approved") is False

    layers = PC.empty_four_layers()
    layers["constitution"] = constitution
    card = PC.make_project_card(
        project_uid,
        title=card_title,
        repo_fingerprints=fps,
        layers=layers,
        extra={
            "created_wall_ms": now_ms,
            "created_by_seat": seat,              # 沒指定席位建的卡 ⇒ 空字串
            "persona_uid_source": persona or "",
            "constitution_status": "candidate",  # 未核准;生效路徑另議
            **dict(extra_fields or {}),
        },
    )
    # 雙保險:卡內憲法不得標核准
    card["layers"]["constitution"]["approved"] = False

    c_path = card_path_of(root, project_uid)
    cand_path = candidate_path_of(root, project_uid)
    candidate_doc = {
        "schema": "aib-constitution-candidate/v1",
        "project_uid": project_uid,
        "approved": False,
        "source": "persona-draft",
        "persona_uid": persona or "",
        "constitution": constitution,
        "created_wall_ms": now_ms,
        "note": "候選初稿;本人核准前不得當作生效憲法(card.layers.constitution.approved 必須為 false)",
    }

    result = {
        "ok": True,
        "project_uid": project_uid,
        "card_path": str(c_path),
        "candidate_path": str(cand_path),
        "seat": seat,
        "approved": False,
        "reused": bool(reuse),
        "dry_run": bool(dry_run),
        "title": card_title,
    }

    if dry_run:
        # 仍驗證形狀,但不寫
        PC.validate_project_card(card)
        return result

    if before_write is not None:
        before_write(project_uid, card_title)
    _atomic_write_json(c_path, card)
    _atomic_write_json(cand_path, candidate_doc)
    # 絕不寫「生效憲法」旁路檔
    active_forbidden = project_dir(root, project_uid) / "constitution.json"
    if active_forbidden.exists():
        # 若殘留舊檔且 approved 假象,不覆寫成核准;本包不建立此檔
        pass

    if not bind:
        return result
    _update_continuity(root, seat, project_uid, card_title, now_ms)
    _update_seat_file(root, seat, project_uid, card_title)

    # 煙測:pick_project 要看得到
    seat_rec = {"project_uid": project_uid, "project_title": card_title}
    picked = pick_project(seat_record=seat_rec, environ={})
    if not picked or picked.get("project_uid") != project_uid:
        raise NewProjectError("pick_project 未見剛寫的 project_uid")

    return result


# ── aib newproj(09-29 a seat)────────────────────────────────────────────────────────
#   選單那條路(上面 create_project + main 的後半)一個位元組都沒改:stderr 仍是空的
#   (mouse-ctl 用 2>&1 收輸出再整段當 JSON 解,多印一行就會被判成失敗)。

def _say(msg: str, prefix: str = "[*]") -> None:
    print("%s %s" % (prefix, msg), file=sys.stderr)


def _refuse(msg: str, seat: str) -> int:
    _say(T("aib.newproj.nothing_written", {"msg": msg}), "[x]")
    print(json.dumps({"ok": False, "error": msg, "seat": seat, "approved": False},
                     ensure_ascii=False, sort_keys=True))
    return 2


def load_card(state_root: Path, project_uid: str) -> Optional[dict]:
    """專案卡正本;不在、讀不到、形狀不合 ⇒ None。"""
    try:
        uid = PC.validate_project_uid(project_uid)
    except PC.ProjectCardError:
        return None
    card = _load_json(card_path_of(state_root, uid))
    if not card or card.get("project_uid") != uid:
        return None
    try:
        return PC.validate_project_card(card)
    except PC.ProjectCardError:
        return None


def projects_titled(state_root: Path, title: str) -> list[str]:
    """標題逐字相同的既有專案 uid(排序)。"""
    want = (title or "").strip()
    out = []
    folder = state_root / "projects"
    if not want or not folder.is_dir():
        return out
    for child in sorted(folder.iterdir()):
        card = _load_json(child / "card.json")
        if card and str(card.get("title") or "").strip() == want and card.get("project_uid") == child.name:
            out.append(child.name)
    return out


def _resolve_seat(state_root: Path, seat_arg: str):
    """--seat 的三種寫法 ⇒ (seat_id, 席位紀錄, 艙 id, 錯誤)。規則只住在 create_and_bind_persona 一處。"""
    import create_and_bind_persona as CBP  # noqa: E402
    CBP.AIB_HOME = state_root
    seat_id, rec, instance, err = CBP.resolve_seat_arg(seat_arg)
    if err:
        return None, None, "", err
    if rec is None:
        hits = [r for r in CBP.iter_seat_records() if r[1].get("seat_id") == seat_id]
        if len(hits) != 1:
            return None, None, "", T("aib.newproj.seat_not_found", {"seat": seat_arg, "n": len(hits)})
        _, rec, instance = hits[0]
    return seat_id, rec, instance, None


def _ledger_note(state_root: Path, agent_uid: str, actor: dict, zh: str, en: str) -> None:
    """先入帳。用既有的 identity.bound(事件種類是封閉清單,不為專案加第 16 種)。"""
    import role_model as RM  # noqa: E402
    RM.RoleModel(state_root=str(state_root))._append_event("identity.bound", zh, en, agent_uid, actor)


def resolve_folder(contract: Any, wanted: str):
    """--folder 的寫法 ⇒ (route_key, folder, 錯誤)。收路由代號、完整資料夾路徑、或資料夾最後一段
    (可以不帶前面的編號:「系統記憶交接」= 「C80-系統記憶交接」)。對到不只一個 ⇒ 列出來、不挑。"""
    want = (wanted or "").strip().strip("/")
    routes = contract.get("routes") if isinstance(contract, dict) else None
    if not want or not isinstance(routes, list):
        return None, None, T("aib.newproj.no_folder_list")
    hits = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        key, folder = str(r.get("routeKey") or ""), str(r.get("folder") or "").strip("/")
        if not key or not folder:
            continue
        last = folder.rsplit("/", 1)[-1]
        bare = re.sub(r"^[A-Za-z0-9]{1,6}-", "", last)
        if want in (key, folder, last, bare):
            hits.append((key, folder))
    if not hits:
        return None, None, T("aib.newproj.folder_unknown", {"value": want})
    if len(hits) > 1:
        return None, None, T("aib.newproj.folder_ambiguous", {
            "value": want, "n": len(hits), "items": "、".join("%s=%s" % h for h in hits)})
    return hits[0][0], hits[0][1], None


def _folder_of_key(contract: Any, route_key: str) -> str:
    for r in (contract.get("routes") if isinstance(contract, dict) else None) or []:
        if isinstance(r, dict) and r.get("routeKey") == route_key:
            return str(r.get("folder") or "")
    return ""


def _drop_project_fields(path: Path) -> bool:
    rec = _load_json(path)
    if not rec or not (rec.get("project_uid") or rec.get("project_title")):
        return False
    rec = dict(rec)
    rec.pop("project_uid", None)
    rec.pop("project_title", None)
    _atomic_write_json(path, rec)
    return True


def project_memory_notes(state_root: Path, project_uid: str, limit: int = 5) -> list:
    """10-15 H1:這個專案在 aib memory 的最新幾則(交接優先)⇒ 接手包的指標行;讀不到 ⇒ []。
    索引用這次的狀態根(不靠環境猜),與卡同一個根。"""
    try:
        mem = str(REPO_ROOT / "src" / "memory")
        if mem not in sys.path:
            sys.path.insert(0, mem)
        import aib_memory as AM  # noqa: E402
        return AM.pointer_lines(project_uid, limit, index=Path(state_root) / "memory-index" / "notes.jsonl")
    except Exception:  # noqa: BLE001 —— 記憶讀不到不擋寄接手包
        return []


def deliver_handoff(state_root: Path, seat_id: str, seat_rec: Mapping[str, Any], instance: str,
                    card: Mapping[str, Any], persona_uid: str, dry_run: bool = False) -> dict:
    """接手包用 aib dm 寄給那一席 ⇒ dict。寄不出去不改變「已綁」,只如實回報。"""
    import create_and_bind_persona as CBP  # noqa: E402
    import handoff_pack as HP  # noqa: E402
    import open_ack as OA  # noqa: E402
    dm_address, seat_address = CBP._cockpit_modules()
    code = seat_address.code_of(dict(seat_rec))
    cabin = CBP._seat_cabin(dict(seat_rec), instance)
    if not code or not cabin:
        return {"ok": False, "reason": T("aib.newproj.no_address", {"code": code or "?", "value": cabin or "?"})}
    address = dm_address.format_address(code, cabin)
    if dry_run:
        _say(T("aib.newproj.dry_send", {"target": address}))
        return {"ok": None, "dry_run": True, "address": address}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    text = HP.build_handoff_for_seat(card, persona_uid=persona_uid, last_event_at=stamp,
                                     memory_notes=project_memory_notes(state_root, str(card.get("project_uid") or "")))
    if not text:
        return {"ok": False, "address": address, "reason": T("aib.newproj.pack_failed")}
    title = str(card.get("title") or card.get("project_uid"))
    reply = OA.handoff_ack_literal(title, stamp)
    body = T("aib.newproj.pack_body", {"title": title, "text": text.rstrip(), "value": reply})
    rc, out, err = CBP.dm_send_body(address, body, seat_rec)   # review r12 M2:走 dm 的家整行貼
    stage = ""
    for line in reversed(out.splitlines()):
        if line.startswith("RESULT "):
            stage = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv).get("stage", "")
            break
    # review r13:走 dm 的家貼出去的是 lint_safe 換過的字 ⇒ 要對方回的那一行也照同一個規則換,兩邊才對得上
    result = {"ok": rc == 0, "rc": rc, "address": address, "stage": stage,
              "expect_reply": CBP.dm_inline_text(reply, seat_rec)[0]}
    if rc in (3, 4):
        # review r14 M1:同 create_and_bind_persona.deliver_persona —— 3/4 是「已送、勿重送」,不是沒寄到
        result["ok"] = None
        result["no_retry"] = True
        result["reason"] = "aib dm rc=%d:已送出或已進對方框但沒能確認,不要重跑;等對方回「%s」" % (
            rc, result["expect_reply"])
    elif rc != 0:
        result["reason"] = (err.strip().splitlines() or ["?"])[-1][-200:]
    return result


def run_newproj(args) -> int:
    import bind_one as BO  # noqa: E402
    root = state_root_of(args.state_root) if args.state_root else state_root_of()
    seat_arg = (args.seat or "").strip()
    if args.new_seat:
        if seat_arg:
            return _refuse(T("aib.newproj.seat_xor_new"), seat_arg)
        if args.unbind:
            return _refuse(T("aib.newproj.unbind_needs_seat"), "(new seat)")
        import create_and_bind_persona as CBP  # noqa: E402
        CBP.AIB_HOME = root
        role = CBP.seat_role_of(args.title, "project")
        # ★ review r15 M1(人格):開新席**之前**先做不需要席位的檢查。以前先開席 ⇒ 後面被擋(標題撞名、uid 不存在、
        #   新席沒人格所以綁不上資料夾 —— 帶 --folder 或同標題舊卡記了資料夾時**每次**都中)都留下一個在跑的新席,
        #   訊息卻說「什麼都沒寫」;試跑也在檢查前就回綠燈。
        _title = (args.title or "").strip()
        _pre_uid = ""
        if args.project_uid:
            if load_card(root, args.project_uid.strip()) is None:
                return _refuse(T("aib.newproj.uid_unknown", {"uid": args.project_uid.strip()}), "(new seat)")
            _pre_uid = args.project_uid.strip()
        elif _title:
            _same = projects_titled(root, _title)
            if len(_same) > 1:
                return _refuse(T("aib.newproj.title_ambiguous", {"title": _title, "n": len(_same),
                                                                 "items": "、".join(_same)}), "(new seat)")
            _pre_uid = _same[0] if _same else ""
        else:
            return _refuse(T("aib.newproj.need_title"), "(new seat)")
        _known = load_card(root, _pre_uid) if _pre_uid else None
        _want = (args.folder or "").strip() or str((_known or {}).get("route_key") or "")
        if _want:
            # 新開的席一定還沒綁人格,bind_one 規定沒人格不能綁資料夾 ⇒ 這個組合真跑必被擋,開席前就說
            return _refuse(T("aib.newproj.folder_unbindable", {"key": _want, "err": T("aib.newproj.new_seat_no_persona")}),
                           "(new seat)")
        if not (root / "ledger").is_dir():
            return _refuse(T("aib.newproj.no_ledger", {"path": root / "ledger"}), "(new seat)")
        if not (args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip():
            return _refuse(T("aib.newproj.need_actor"), "(new seat)")
        try:
            # review r16 m2:試跑也驗帳本/署名(以前試跑在這幾道之前就回綠燈);review r16(人格):署名格式在開席前驗;以前開了席才由 actor_of 擋 ⇒ 留下一個在跑的新席
            BO.actor_of((args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip())
        except BO.BindError as exc:
            return _refuse(str(exc), "(new seat)")
        if args.dry_run:
            cabin = CBP.current_cabin()
            _say(T("aib.newproj.dry_new_seat", {"instance": cabin or T("aib.newproj.unknown"),
                                                "engine": args.engine, "role": role}))
            print(json.dumps({"ok": bool(cabin), "dry_run": True, "new_seat": {
                "engine": args.engine, "role": role, "cabin": cabin}}, ensure_ascii=False, sort_keys=True))
            return 0 if cabin else 2
        try:
            _sid, _rec, _inst = CBP.open_new_seat(role, engine=args.engine, timeout=CBP.new_seat_timeout())
        except CBP.NewSeatError as exc:
            if getattr(exc, "opened", False):
                # review r17 m3:窗已開(等不到席碼 / 冒出多席)⇒ 不能說「什麼都沒寫」;卡也還沒建
                _say(T("aib.newproj.new_seat_opened_unbound", {"msg": str(exc)}), "[x]")
                print(json.dumps({"ok": False, "error": str(exc), "seat": "(new seat)", "seat_opened": True,
                                  "approved": False}, ensure_ascii=False, sort_keys=True))
                return 2
            return _refuse(str(exc), "(new seat)")
        seat_arg = _sid
    if not seat_arg:
        return run_card_only(args, root)
    seat_id, seat_rec, instance, err = _resolve_seat(root, seat_arg)
    if err:
        return _refuse(err, seat_arg)
    if not SEAT_ID_RE.fullmatch(seat_id or ""):
        return _refuse(T("aib.newproj.bad_seat", {"seat": repr(seat_id)}), seat_arg)
    sig = (args.actor or os.environ.get("EZ2_OWNER_SIG") or "").strip()
    if not sig:
        return _refuse(T("aib.newproj.need_actor"), seat_arg)
    try:
        actor = BO.actor_of(sig)
        _role, session_uuid, agent_uid = BO.seat_binding_inputs(dict(seat_rec))
    except BO.BindError as exc:
        return _refuse(str(exc), seat_arg)
    if not (root / "ledger").is_dir():
        return _refuse(T("aib.newproj.no_ledger", {"path": root / "ledger"}), seat_arg)

    now_ms = int(time.time() * 1000)
    base = {"ok": True, "seat": seat_id, "code": agent_uid, "approved": False}   # 席碼欄叫 code(B15:舊欄名已退役)
    try:
        current_route = (BO.seat_status(root, seat_id).get("agent") or {}).get("brain_route_key") or ""
    except Exception:  # noqa: BLE001  身份層還沒有這一席(從沒綁過)⇒ 沒有資料夾
        current_route = ""
    if args.dry_run:
        base["dry_run"] = True

    # 契約來源只算一次:解綁重投影、預檢、bind_seat 共用(R2 中-2:unbind 也要帶,否則艙外
    # 身份層清了、bindings.json 重投影讀不到 REGISTRY 仍留舊 route,指令卻回 ok:true)。
    _spec = BO.default_contract_spec()

    # ── 解綁 ──
    if args.unbind:
        bound = str(seat_rec.get("project_uid") or "").strip()
        if not bound:
            return _refuse(T("aib.newproj.not_bound"), seat_arg)
        base.update({"unbound": True, "project_uid": bound})
        bound_card = load_card(root, bound) or {}
        if bound_card.get("route_key") and bound_card.get("route_key") == current_route:
            base.update({"route_key": current_route, "folder": str(bound_card.get("folder") or "")})
        if args.dry_run:
            _say(T("aib.newproj.dry_unbind", {"uid": bound, "seat": seat_id}))
        else:
            _ledger_note(root, agent_uid, actor,
                         "專案解綁:%s project=%s(專案卡保留)" % (agent_uid, bound),
                         "project unbound: %s project=%s (card retained)" % (agent_uid, bound))
            if base.get("route_key"):
                # 這一席綁的資料夾就是這個專案的資料夾 ⇒ 一起解(資料夾就是專案);先解它,它會整檔重寫延續檔
                plan = BO.unbind_seat(root, seat_id, actor, brain_only=True, contract_spec=_spec)
                reproj = (plan or {}).get("reproject") or {}
                if reproj.get("rc", 0) != 0:
                    # 身份層已解;重投影失敗 ⇒ 不回 ok:true(與 bind-one CLI rc=1 同精神)
                    detail = (reproj.get("log_tail") or "")[-200:]
                    # 10-07:改走四語字典(以前是字面,tools-i18n-ratchet 因此 1391→1392 紅;the maintainer 同意搬)
                    err = T("aib.newproj.unbind_reproject_failed", {"rc": reproj.get("rc"), "detail": detail})
                    base.update({"ok": False, "error": err, "reproject": {"rc": reproj.get("rc")}})
                    _say(err, "[x]")
                    print(json.dumps(base, ensure_ascii=False, sort_keys=True))
                    return 1
            seat_path, _ = BO.find_seat(root, seat_id)
            _drop_project_fields(Path(seat_path))
            _drop_project_fields(root / "identity" / "seat-continuity" / ("%s.json" % seat_id))
            _registry(root).unbind_quietly(seat_id, by="newproj --unbind", root=root)
            _say(T("aib.newproj.unbound", {"uid": bound, "seat": seat_id, "path": card_path_of(root, bound)}))
        print(json.dumps(base, ensure_ascii=False, sort_keys=True))
        return 0

    # ── 綁哪一張:明給 uid > 同標題的既有卡 > 新建 ──
    title = (args.title or "").strip()
    existing_uid = ""
    if args.project_uid:
        if load_card(root, args.project_uid.strip()) is None:
            return _refuse(T("aib.newproj.uid_unknown", {"uid": args.project_uid.strip()}), seat_arg)
        existing_uid = args.project_uid.strip()
    elif title:
        same = projects_titled(root, title)
        if len(same) > 1:
            return _refuse(T("aib.newproj.title_ambiguous", {"title": title, "n": len(same),
                                                             "items": "、".join(same)}), seat_arg)
        if same:
            existing_uid = same[0]
    else:
        return _refuse(T("aib.newproj.need_title"), seat_arg)

    persona = resolve_persona_uid(root, seat_id, args.persona_uid or None)

    # ── 專案對應的資料夾:明給 > 既有卡記的 > 這一席現在綁的 ──
    # 預檢與 bind_seat 必須同一份契約來源(與上方 _spec 相同)。
    contract = BO.load_contract(_spec)
    known = load_card(root, existing_uid) if existing_uid else None
    card_route = str((known or {}).get("route_key") or "")
    route_key, folder = "", ""
    if args.folder:
        route_key, folder, ferr = resolve_folder(contract, args.folder)
        if ferr:
            return _refuse(ferr, seat_arg)
        if card_route and card_route != route_key:
            return _refuse(T("aib.newproj.folder_mismatch", {"key": card_route, "path": (known or {}).get("folder") or "?",
                                                             "value": route_key}), seat_arg)
    elif card_route:
        route_key, folder = card_route, str((known or {}).get("folder") or "")
    elif current_route:
        route_key, folder = current_route, _folder_of_key(contract, current_route)
    if route_key:
        ok, code, detail = BO.check_against_contract(route_key, contract)
        if not ok:
            return _refuse(T("aib.newproj.folder_rejected", {"key": route_key, "code": code, "detail": detail}), seat_arg)
        if route_key != current_route:
            try:                                   # 預演:這一席綁得上這個資料夾嗎(沒綁人格就綁不上)
                BO.bind_seat(root, seat_id, None, actor, brain=route_key, dry_run=True,
                             contract_spec=_spec)
            except BO.BindError as exc:
                return _refuse(T("aib.newproj.folder_unbindable", {"key": route_key, "err": exc}), seat_arg)
        base.update({"route_key": route_key, "folder": folder})
    else:
        _say(T("aib.newproj.no_folder"))

    def bind_folder() -> None:
        """席位的資料夾綁成專案的資料夾。要在寫延續檔的專案欄位**之前**做:它會整檔重寫延續檔。"""
        if route_key and route_key != current_route and not args.dry_run:
            BO.bind_seat(root, seat_id, None, actor, brain=route_key, contract_spec=_spec)
            _say(T("aib.newproj.folder_bound", {"seat": seat_id, "path": folder, "key": route_key}))

    def note(project_uid: str, card_title: str) -> None:
        _ledger_note(root, agent_uid, actor,
                     "專案綁定:%s project=%s(%s)" % (agent_uid, project_uid, card_title),
                     "project bound: %s project=%s" % (agent_uid, project_uid))

    if existing_uid:
        # 讀不到卡在上面 --project-uid 那一格就擋掉了;這裡再讀一次讀不到(同時被刪)也不崩,當沒有標題
        card = load_card(root, existing_uid) or {}
        card_title = str(card.get("title") or existing_uid)
        _say(T("aib.newproj.reuse_card", {"uid": existing_uid, "title": card_title}))
        base.update({"project_uid": existing_uid, "title": card_title, "created": False,
                     "card_path": str(card_path_of(root, existing_uid)),
                     "candidate_path": str(candidate_path_of(root, existing_uid))})
        if not args.dry_run:
            note(existing_uid, card_title)
            if route_key and not card_route:
                # 卡本來沒有記資料夾 ⇒ 補記這兩欄;其餘欄位一個位元組都不動
                # 記憶整理第 3 包(10-13):讀改寫跟 aib memory tidy 用同一把卡鎖(<卡資料夾>/card.lock),兩邊同時改不互蓋
                wrote, busy = False, False
                try:
                    with _card_lock(card_path_of(root, existing_uid)):
                        raw = _load_json(card_path_of(root, existing_uid)) or {}
                        raw.update({"route_key": route_key, "folder": folder})
                        # 記憶整理第 1 包(10-13):改卡前先快照;拍不成就不補記(卡維持原樣,綁定照做)
                        wrote = _archive_snapshot(card_path_of(root, existing_uid), "project", existing_uid).get("status") != "failed"
                        if wrote:
                            _atomic_write_json(card_path_of(root, existing_uid), raw)
                except OSError:                            # 卡鎖 10 秒拿不到(別席正在整理)或鎖檔開不了:這次不補記,綁定照做
                    wrote, busy = False, True
                if wrote:
                    card = load_card(root, existing_uid)
                    _say(T("aib.newproj.card_folder_added", {"path": folder, "key": route_key}))
                else:
                    _say(T("aib.newproj.card_folder_skipped_busy" if busy else "aib.newproj.card_folder_skipped_backup",
                           {"path": folder}))
            bind_folder()
            _update_continuity(root, seat_id, existing_uid, card_title, now_ms)
            _update_seat_file(root, seat_id, existing_uid, card_title)
        # review r6 m6:卡上的草稿沒有紅線、也沒核准 ⇒ 照這一席的人格重出(核准過的卡一個位元組都不動)
        # ★ review r7 m2:到這裡席位已經綁好了;重出失敗(寫不進去、人格讀壞)不能讓整個指令崩掉、
        #   讓人以為沒綁 ⇒ 接住,照實記在 draft_refresh_error,其餘照走。
        try:
            refreshed = refresh_empty_draft(root, existing_uid, persona, dry_run=bool(args.dry_run))
        except Exception as exc:  # noqa: BLE001
            refreshed = False
            base["draft_refresh_error"] = "%s: %s" % (type(exc).__name__, exc)
        if refreshed:
            base["draft_refreshed"] = True
            _say(T("aib.newproj.draft_refresh_will" if args.dry_run else "aib.newproj.draft_refreshed",
                   {"uid": persona, "path": candidate_path_of(root, existing_uid)}))
            if not args.dry_run:
                card = load_card(root, existing_uid) or card
    else:
        def note_then_folder(project_uid: str, card_title: str) -> None:
            note(project_uid, card_title)
            bind_folder()

        try:
            out = create_project(seat=seat_id, title=title,
                                 cwd=Path(args.cwd) if args.cwd else Path.cwd(),
                                 state_root=root, persona_uid=args.persona_uid,
                                 dry_run=bool(args.dry_run), before_write=note_then_folder,
                                 extra_fields=({"route_key": route_key, "folder": folder} if route_key else None))
        except (NewProjectError, PC.ProjectCardError, OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc), "seat": seat_id, "approved": False},
                             ensure_ascii=False, sort_keys=True))
            return 1
        except Exception as exc:          # 帳本拒收(RoleModelError)⇒ before_write 丟出來,檔案一個都還沒寫
            return _refuse(T("aib.newproj.ledger_refused", {"err": exc}), seat_arg)
        base.update({"project_uid": out["project_uid"], "title": out["title"], "created": True,
                     "card_path": out["card_path"], "candidate_path": out["candidate_path"]})
        if out.get("reused"):
            base["reused"] = True
        card = None if args.dry_run else load_card(root, out["project_uid"])
        _say(T("aib.newproj.card_will" if args.dry_run else "aib.newproj.card_made",
               {"uid": out["project_uid"], "title": out["title"]}))
        _say(T("aib.newproj.draft_note", {"path": out["candidate_path"], "uid": out["project_uid"]}))

    _say(T("aib.newproj.seat_will" if args.dry_run else "aib.newproj.seat_bound",
           {"seat": seat_id, "code": agent_uid, "uid": base["project_uid"]}))
    if args.deliver:
        if card is None and args.dry_run:
            card = {"title": base["title"], "project_uid": base["project_uid"]}
        base["delivery"] = deliver_handoff(root, seat_id, seat_rec, instance, card, persona,
                                           dry_run=bool(args.dry_run))
        if base["delivery"].get("ok") is False:
            _say(T("aib.newproj.pack_not_sent", {"reason": base["delivery"].get("reason")}), "[x]")
            print(json.dumps(base, ensure_ascii=False, sort_keys=True))
            return 6
    print(json.dumps(base, ensure_ascii=False, sort_keys=True))
    return 0


def _union_keep_order(*lists: Any) -> list:
    """幾份清單照順序接起來、去掉重複(同內容只留第一次出現的);不是清單的當空的。"""
    out: list = []
    seen: set = set()
    for items in lists:
        for item in (items if isinstance(items, list) else []):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def refresh_empty_draft(root: Path, project_uid: str, persona_uid: str, dry_run: bool = False) -> bool:
    """review r6 m6:綁既有專案卡時,卡上的憲法草稿**沒有紅線且沒核准** ⇒ 照這一席綁的人格重出草稿 ⇒ True。

    典型:沒指定席位先建的卡(當時沒有人格可抽,紅線是空的),之後綁上有人格的席。
    卡或候選檔任一邊已核准、草稿已經有紅線、人格抽不出紅線 ⇒ 什麼都不動 ⇒ False。
    重寫的是候選檔 constitution-draft.json 與卡上的 layers.constitution(兩邊同一份,approved 一律 false);
    卡的其他欄位一個都不動。dry_run ⇒ 只回會不會重出。"""
    if not persona_uid:
        return False
    c_path, cand_path = card_path_of(root, project_uid), candidate_path_of(root, project_uid)
    raw = _load_json(c_path)
    if not isinstance(raw, dict):
        return False
    layers = raw.get("layers") if isinstance(raw.get("layers"), dict) else {}
    card_const = layers.get("constitution") if isinstance(layers.get("constitution"), dict) else {}
    cand = _load_json(cand_path) or {}
    cand_const = cand.get("constitution") if isinstance(cand.get("constitution"), dict) else {}
    if card_const.get("approved") is not False or cand.get("approved", False) is not False \
            or cand_const.get("approved", False) is not False:
        return False
    if card_const.get("red_lines") or cand_const.get("red_lines"):
        return False
    draft = constitution_draft_from_persona(root, persona_uid)
    if not draft["red_lines"]:
        return False
    if dry_run:
        return True
    # ★ review r7 m1:卡沒核准時,技能教人改的是候選檔(constitution-draft.json)⇒ 目標與 the maintainer 裁定以候選檔為主,
    #   卡上有而候選檔沒有的再補在後面(聯集、不重複、先後照原樣)。以前只拿卡上的,候選檔手改的會被蓋掉。
    draft["goals"] = _union_keep_order(cand_const.get("goals"), card_const.get("goals"))
    draft["human_rulings"] = _union_keep_order(cand_const.get("human_rulings"), card_const.get("human_rulings"))
    draft["approved"] = False
    raw["layers"] = dict(layers, constitution=draft)
    raw["persona_uid_source"] = persona_uid
    PC.validate_project_card(raw)
    now_ms = int(time.time() * 1000)
    cand_doc = dict(cand) if cand else {"schema": "aib-constitution-candidate/v1", "project_uid": project_uid,
                                        "source": "persona-draft", "created_wall_ms": now_ms}
    cand_doc.update({"approved": False, "persona_uid": persona_uid, "constitution": draft,
                     "refreshed_wall_ms": now_ms})
    _atomic_write_json(cand_path, cand_doc)
    _atomic_write_json(c_path, raw)
    return True


def _folder_refusal(BO: Any, route_key: str, contract: Any) -> str:
    """只建卡那條路的資料夾契約檢查:過 ⇒ "";不過 ⇒ 拒絕理由(與綁席那條同一句 aib.newproj.folder_rejected)。"""
    verdict = BO.check_against_contract(route_key, contract)
    if verdict[0]:
        return ""
    return T("aib.newproj.folder_rejected", {"key": route_key, "code": verdict[1], "detail": verdict[2]})


def run_card_only(args, root: Path) -> int:
    """aib newproj 沒指定席位(the maintainer 10-01):只建專案卡,不開席、不綁任何席、不進帳本(沒有綁定可記)。

    同標題的卡已經有一張 ⇒ 不另開、不改卡,照實說「本來就有了」。--unbind / --deliver / --project-uid 都是對某一席做事 ⇒ 要 --seat。"""
    no_seat = "(no seat)"
    for flag, on in (("--unbind", args.unbind), ("--deliver", args.deliver), ("--project-uid", bool(args.project_uid))):
        if on:
            return _refuse(T("aib.newproj.flag_needs_seat", {"flag": flag}), no_seat)
    title = (args.title or "").strip()
    if not title:
        # review r6 m4:只建卡這條路 --project-uid 要配 --seat(上面已擋),所以只提 --title
        return _refuse(T("aib.newproj.card_only_need_title"), no_seat)
    same = projects_titled(root, title)
    if len(same) > 1:
        return _refuse(T("aib.newproj.title_ambiguous", {"title": title, "n": len(same),
                                                         "items": "、".join(same)}), no_seat)
    base: dict[str, Any] = {"ok": True, "seat": None, "approved": False, "bound": False}
    if args.dry_run:
        base["dry_run"] = True
    if same:
        base.update({"project_uid": same[0], "title": title, "created": False,
                     "card_path": str(card_path_of(root, same[0])),
                     "candidate_path": str(candidate_path_of(root, same[0]))})
        base["reply"] = T("aib.newproj.card_only_exists", {"title": title, "uid": same[0]})
        print(base["reply"], file=sys.stderr)
        print(json.dumps(base, ensure_ascii=False, sort_keys=True))
        return 0
    route_key, folder = "", ""
    if args.folder:
        import bind_one as BO  # noqa: E402
        _spec = BO.default_contract_spec()
        contract = BO.load_contract(_spec)
        route_key, folder, ferr = resolve_folder(contract, args.folder)
        if ferr:
            return _refuse(ferr, no_seat)
        refusal = _folder_refusal(BO, route_key, contract)
        if refusal:
            return _refuse(refusal, no_seat)
        base.update({"route_key": route_key, "folder": folder})
    try:
        out = create_project(seat="", title=title, cwd=Path(args.cwd) if args.cwd else Path.cwd(),
                             state_root=root, persona_uid=args.persona_uid, dry_run=bool(args.dry_run),
                             extra_fields=({"route_key": route_key, "folder": folder} if route_key else None),
                             bind=False)
    except (NewProjectError, PC.ProjectCardError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "seat": None, "approved": False},
                         ensure_ascii=False, sort_keys=True))
        return 1
    base.update({"project_uid": out["project_uid"], "title": out["title"], "created": True,
                 "card_path": out["card_path"], "candidate_path": out["candidate_path"]})
    base["reply"] = T("aib.newproj.card_only_dry" if args.dry_run else "aib.newproj.card_only_made",
                      {"title": out["title"], "uid": out["project_uid"]})
    _say(T("aib.newproj.draft_note", {"path": out["candidate_path"], "uid": out["project_uid"]}))
    print(base["reply"], file=sys.stderr)
    print(json.dumps(base, ensure_ascii=False, sort_keys=True))
    return 0


# ── aib newproj --approve <專案 uid>(10-15 C11)──────────────────────────────────────────
#   newproj 一直叫人「核准目標與紅線」,卻沒有任何指令能核准。只給真人:終端機 + human_attest 三條件
#   (不在 hub 裡、tty 屬登入者、上游只有殼且沒有 AI session 標記)全到,再親手打一次專案 uid。AI 席跑不過。

def _human_attest() -> tuple:
    """(是不是真人, 登入名, 缺哪幾件)。量不到一律算不是人(失效方向只往低權)。"""
    try:
        import human_attest as HA   # src/cockpit(上面已掛進 sys.path)
        res = HA.attest()
    except Exception as exc:  # noqa: BLE001
        return False, None, ["attest_unavailable:%s" % exc]
    return bool(res.get("human")), res.get("human_id"), list(res.get("missing") or [])


def approve_constitution(root: Path, project_uid: str, *, human_check: Any = None, ask: Any = None,
                         stdin: Any = None, stdout: Any = None) -> int:
    """真人核准專案憲法初稿:候選檔 → 卡上 layers.constitution,approved=true,先入帳再寫。
    rc:0 核准(或本來就核准)/ 2 拒(找不到卡、不是真人、沒終端機)/ 3 真人打錯 uid 取消。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    card = load_card(root, project_uid)
    if card is None:
        _say(T("aib.newproj.approve_not_found", {"uid": project_uid}), "[x]")
        return 2
    uid = card["project_uid"]
    if (card["layers"].get("constitution") or {}).get("approved") is True:
        _say(T("aib.newproj.approve_already", {"uid": uid}))
        return 0
    try:
        tty = bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError):
        tty = False
    if not tty:
        _say(T("aib.newproj.approve_need_human", {"why": "no tty"}), "[x]")
        return 2
    human, human_id, missing = (human_check or _human_attest)()
    if not human or not human_id:
        _say(T("aib.newproj.approve_need_human", {"why": ",".join(missing) or "?"}), "[x]")
        return 2
    cand_path = candidate_path_of(root, uid)
    cand = _load_json(cand_path) or {}
    draft = cand.get("constitution") if isinstance(cand.get("constitution"), dict) else card["layers"]["constitution"]
    const = dict(PC.empty_constitution(), **{k: v for k, v in draft.items() if k in ("goals", "red_lines", "human_rulings")})

    def lines(items):
        return ["    - %s" % (i.get("text") if isinstance(i, dict) else i) for i in (items or [])] or ["    " + T("aib.newproj.approve_empty")]

    show = [T("aib.newproj.approve_head", {"title": card.get("title") or uid, "uid": uid}),
            "  " + T("aib.newproj.approve_goals")] + lines(const["goals"]) + \
           ["  " + T("aib.newproj.approve_red_lines")] + lines(const["red_lines"]) + \
           ["  " + T("aib.newproj.approve_rulings")] + lines(const["human_rulings"])
    stdout.write("\n".join(show) + "\n" + T("aib.newproj.approve_prompt", {"uid": uid}))
    stdout.flush()
    ans = (ask() if ask else stdin.readline()).strip()
    if ans != uid:
        _say(T("aib.newproj.approve_cancel"), "[x]")
        return 3
    now_ms = int(time.time() * 1000)
    actor = {"ai_id": "human:%s" % human_id, "sig": "human@0000/Human", "kind": "human"}
    import role_model as RM  # noqa: E402 —— 先入帳(入不了帳 ⇒ 卡不動)
    if not (root / "ledger").is_dir():      # 只建卡沒跑過 aib init 的新機器:先建狀態樹(明給狀態根,不靠環境猜)
        subprocess.run([sys.executable, str(REPO_ROOT / "src" / "bootstrap" / "aib_init.py"), "--state-root", str(root)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    try:
        RM.RoleModel(state_root=str(root))._append_event(
            "decision.resolved",
            "專案 %s 的目標與紅線由真人 %s 核准" % (uid, human_id),
            "project %s goals and red lines approved by human %s" % (uid, human_id),
            uid, actor)
    except RM.RoleModelError as exc:
        _say(T("aib.newproj.approve_ledger_failed", {"err": exc}), "[x]")
        return 2
    c_path = card_path_of(root, uid)
    with _card_lock(c_path):
        snap = _archive_snapshot(c_path, "project", uid)
        if snap.get("status") == "failed":
            _say(T("aib.newproj.approve_backup_failed", {"err": snap.get("error")}), "[x]")
            return 2
        raw = _load_json(c_path) or {}
        import datetime as _dt                                    # +08:00 形(跟既有卡同形);不靠 %z(有的平台給時區名)
        at = _dt.datetime.fromtimestamp(now_ms / 1000).astimezone().isoformat(timespec="seconds")
        rulings = [r for r in (const.get("human_rulings") or []) if isinstance(r, (dict, str))]
        rulings.append({"uid": "approve-%d" % now_ms, "text": T("aib.newproj.approve_ruling", {"who": human_id}), "at": at})
        const.update({"approved": True, "approved_by": "human:%s" % human_id, "approved_at": at,
                      "approved_wall_ms": now_ms, "human_rulings": rulings})
        raw["layers"] = dict(raw.get("layers") or {}, constitution=const)
        raw["constitution_status"] = "approved"
        PC.validate_project_card(raw)
        _atomic_write_json(c_path, raw)
        if cand:
            cand.update({"approved": True, "constitution": const, "approved_wall_ms": now_ms})
            _atomic_write_json(cand_path, cand)
    _say(T("aib.newproj.approve_done", {"uid": uid}))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog=os.environ.get("AIB_NEWPROJ_PROG") or None,
                                 description="新開專案:專案卡 + 憲法候選 + 綁席")
    ap.add_argument("--seat", default="", help=T("aib.newproj.help_seat"))
    ap.add_argument("--title", default="", help="專案標題(預設=cwd basename)")
    ap.add_argument("--cwd", default="", help="用來算倉指紋的工作目錄(預設 pwd)")
    ap.add_argument("--state-root", default="", help="狀態根(預設 EZ2_AIB_HOME / ~/.ez2-aib)")
    ap.add_argument("--persona-uid", default="", help="覆寫人格 uid(預設讀延續檔)")
    ap.add_argument("--dry-run", action="store_true", help="只印結果,不落檔")
    ap.add_argument("--actor", default="", help=T("aib.newproj.help_actor"))
    ap.add_argument("--project-uid", default="", help=T("aib.newproj.help_project_uid"))
    ap.add_argument("--deliver", action="store_true", help=T("aib.newproj.help_deliver"))
    ap.add_argument("--unbind", action="store_true", help=T("aib.newproj.help_unbind"))
    ap.add_argument("--folder", default="", help=T("aib.newproj.help_folder"))
    ap.add_argument("--new-seat", action="store_true", help=T("aib.newproj.help_new_seat"))
    ap.add_argument("--engine", default="claude", help=T("aib.newproj.help_engine"))
    ap.add_argument("--entry", default="", choices=["", "newproj"], help=argparse.SUPPRESS)
    ap.add_argument("--approve", default="", metavar="PROJECT_UID", help=T("aib.newproj.help_approve"))
    args = ap.parse_args(argv)
    if args.approve:                          # 10-15 C11:只給真人,其他旗標不看
        return approve_constitution(state_root_of(args.state_root or None), args.approve.strip())
    # 選單那條路一定帶 --seat;aib newproj 不帶 ⇒ 只建卡(the maintainer 10-01),不在這裡擋
    if not args.seat and not args.new_seat and args.entry != "newproj":
        ap.error(T("aib.newproj.need_seat"))
    if (args.entry == "newproj" or args.actor or args.project_uid or args.deliver or args.unbind
            or args.folder or args.new_seat):
        return run_newproj(args)
    try:
        out = create_project(
            seat=args.seat.strip(),
            title=args.title,
            cwd=Path(args.cwd) if args.cwd else Path.cwd(),
            state_root=state_root_of(args.state_root) if args.state_root else state_root_of(),
            persona_uid=args.persona_uid,
            dry_run=bool(args.dry_run),
        )
    except (NewProjectError, PC.ProjectCardError, OSError, ValueError) as err:
        err_out = {
            "ok": False,
            "error": str(err),
            "seat": args.seat,
            "approved": False,
        }
        print(json.dumps(err_out, ensure_ascii=False, sort_keys=True))
        return 1
    # 機器可讀;不帶 secrets
    public = {
        "ok": out["ok"],
        "project_uid": out["project_uid"],
        "card_path": out["card_path"],
        "candidate_path": out["candidate_path"],
        "seat": out["seat"],
        "approved": False,
    }
    if out.get("reused"):
        public["reused"] = True
    if out.get("dry_run"):
        public["dry_run"] = True
    print(json.dumps(public, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
