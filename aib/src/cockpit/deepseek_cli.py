#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""DeepSeek API-key terminal seat (interactive REPL).

AIB opens this like other providers (tmux pane), but auth is DEEPSEEK_API_KEY
from the secrets vault / env — not subscription OAuth CLI (ED.4).

OpenAI-compatible Chat Completions at https://api.deepseek.com .
"""
from __future__ import annotations

import repl_readline as _repl_rl
_repl_rl.enable_repl_readline()

import argparse
import json
import os
import time
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
import aib_dm_tool as _aib_dm
import api_persona as _api_persona
import api_model_gauge as _api_model_gauge  # 10-01:右欄顯示這一席現在送出去的模型

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
USER_AGENT = "aib-deepseek/0.62 (+Ez2-AIB; OpenAI-compat)"
KEY_NAME = "DEEPSEEK_API_KEY"
DM_ADDRESS = _aib_dm.DM_ADDRESS
DM_TOOL = _aib_dm.DM_TOOL
LOCAL_TOOL = {
    "type": "function",
    "function": {
        "name": "aib_local",
        "description": "Read, search, edit, test (with the machine lease), and commit/push your own branch in your own AIB worktree.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["read", "read_chunk", "search", "test", "propose", "replace", "write", "git"]},
                "git_action": {"type": "string", "enum": ["status", "diff", "add", "commit", "push"],
                               "description": "For operation=git. add needs explicit relative paths; push only pushes your own ds/* branch."},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Explicit relative paths for git add."},
                "message": {"type": "string", "description": "Commit message for git commit."},
                "cached": {"type": "boolean", "description": "git diff --cached."},
                "path": {"type": "string", "description": "Relative project path."},
                "content": {"type": "string", "description": "Whole replacement content; required for write."},
                "old": {"type": "string", "description": "For replace: exact text that appears exactly once in the file."},
                "new": {"type": "string", "description": "For replace: the text to put in its place."},
                "query": {"type": "string", "description": "Text to search; required for search."},
                "start_line": {"type": "integer", "description": "1-based first line for read_chunk."},
                "line_count": {"type": "integer", "description": "Lines to return for read_chunk, at most 400."},
                "rationale": {"type": "string", "description": "Why this already-read file should change; required for propose."},
                "fresh": {"type": "boolean", "description": "For test: really run it even if the last green run can be reused."},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
}
TOOL_SYSTEM = {
    "role": "system",
    "content": (
        _aib_dm.GENE_HEAD + "You are an AIB seat. " + _aib_dm.dm_system_snippet() + " "
        "You can assist local development with aib_local: read and search project files, "
        "use read_chunk to continue any truncated read until the result says [已讀完], "
        "run only tests/test-*.py (the tool takes the machine lease; if it says 排隊中, wait and retry later), "
        "then change only a file you already read: prefer operation=replace (old text that appears exactly once -> "
        "new text) and use write (whole-file replacement) only when most of the file changes. When you work in your own "
        "worktree you may use operation=git (status/diff/add explicit paths/commit/push your own branch) — "
        "never main. You must explain a proposed change before writing it and report "
        "the actual result. You cannot access paths outside the project, secrets, "
        "network, or arbitrary shell commands. Do not claim you lack these capabilities. "
        "Never claim a file is fully read when a result says it was truncated. If tool "
        "calling is unavailable, emit exactly one line "
        "<aib_tool>{\"name\":\"aib_local\",\"arguments\":{...}}</aib_tool>."
    ),
}
TOOL_ROUNDS = max(4, min(200, int(os.environ.get("AIB_DEEPSEEK_TOOL_ROUNDS") or 40)))   # 一輪最多幾次工具呼叫(讀檔也算)
DM_FAIL_DROP_TOOLS = 2      # 一輪裡 aib_dm 累計失敗 2 次(成功不歸零)⇒ 之後不再給 aib_dm(09-28 P1 r3:與五支外殼同一套)
DM_HAMMER_STOP = 3          # 停用後仍硬打 dm 這麼多次 ⇒ 結束這一回合
_TOOL_SYSTEM_DM_SNIPPET = _aib_dm.dm_system_snippet() + " "
assert _TOOL_SYSTEM_DM_SNIPPET in TOOL_SYSTEM["content"]
# 停用後換上這份:拿掉 aib_dm 說明(TOOL_SYSTEM 本來就不含 <aib_tool> aib_dm 範本)。
TOOL_SYSTEM_DM_OFF = {
    "role": "system",
    "content": TOOL_SYSTEM["content"].replace(_TOOL_SYSTEM_DM_SNIPPET, "") + " " + _aib_dm.DM_DISABLED_NOTE,
}
TEXT_TOOL = re.compile(r"^\s*<aib_tool>(\{.*\})</aib_tool>\s*$", re.DOTALL)
PLAIN_READ_CHUNK = re.compile(
    r"read_chunk\s*\(\s*path\s*=\s*[`\"']?([^,`\"'\s]+)[`\"']?\s*,\s*"
    r"start_line\s*=\s*(\d+)\s*,\s*line_count\s*=\s*(\d+)\s*\)",
    re.IGNORECASE,
)
PLAIN_SEARCH = re.compile(
    r"(?:aib_local\s+)?search(?:\s+query)?\s*[:=]\s*[`\"']([^`\"']+)[`\"']",
    re.IGNORECASE,
)
UNVERIFIED_DM_CLAIM = _aib_dm.UNVERIFIED_DM_CLAIM


def _bootstrap_on_path():
    # deepseek_cli.py lives in src/cockpit/ → bootstrap is sibling under src/
    here = Path(__file__).resolve().parent
    boot = here.parent / "bootstrap"
    if str(boot) not in sys.path:
        sys.path.insert(0, str(boot))


def resolve_api_key():
    """Env first, then secrets/api-key-deepseek.env (decrypt if enc.v1.). Never print the value."""
    env_val = (os.environ.get(KEY_NAME) or "").strip()
    if env_val:
        return env_val
    _bootstrap_on_path()
    import aib_secrets as S  # noqa: E402
    rel = S.file_for_name(KEY_NAME) or "api-key-deepseek.env"
    try:
        rows = S.load_file(rel)
    except S.SecretError as err:
        raise SystemExit(
            "deepseek: 讀不到金鑰櫃 %s(%s)。把 %s 寫進 ~/.ez2-aib/secrets/%s "
            "或 export %s=…"
            % (rel, getattr(err, "reason", err), KEY_NAME, rel, KEY_NAME)
        ) from err
    raw = (rows.get(KEY_NAME) or "").strip()
    if not raw:
        raise SystemExit(
            "deepseek: 缺少 %s。寫進 ~/.ez2-aib/secrets/%s 一行 "
            "%s=… 或在開席前 export %s=…（這不是 OAuth CLI，不必登入網頁）"
            % (KEY_NAME, rel, KEY_NAME, KEY_NAME)
        )
    try:
        import secret_crypto as C  # noqa: E402
        if C.is_envelope(raw):
            d = S.secrets_dir()
            return C.decrypt_value(raw, KEY_NAME, d)
    except Exception as err:  # noqa: BLE001
        raise SystemExit("deepseek: 金鑰解密失敗") from err
    return raw


def session_dir(session_id: str) -> Path:
    root = (
        os.environ.get("EZ2_AIB_STATE_ROOT")
        or os.environ.get("EZ2_AIB_HOME")
        or "~/.ez2-aib"
    )
    path = Path(os.path.expanduser(root)) / "deepseek-sessions" / session_id
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _git_out(path: Path, *argv) -> str:
    r = subprocess.run(["git", "-C", str(path)] + list(argv), text=True, capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError("git %s 失敗:%s" % (" ".join(argv), (r.stderr or "").strip()[:200]))
    return r.stdout.strip()


def authorized_preset_worktree(preset: str, project_dir: str, session_id: str) -> Path:
    """AIB_API_WORKTREE 指的樹要是這一席自己的(09-28 P1 r3 Codex 高):同一專案的次要 worktree(不是主樹/正本)、
    分支 ds/<本席五碼>… 或 feat/ds-<本席五碼>…。任何一項不成立就拒,不退回另建(另建只會讓寫檔全拒,還看不出原因)。"""
    import deepseek_git as _dg
    import seat_address as _sa
    wt = Path(preset).resolve()
    if not wt.is_dir():
        raise RuntimeError("AIB_API_WORKTREE 不存在:%s" % wt)
    if Path(_git_out(wt, "rev-parse", "--show-toplevel")).resolve() != wt:
        raise RuntimeError("AIB_API_WORKTREE 不是 git worktree 的根目錄:%s" % wt)
    git_dir = Path(_git_out(wt, "rev-parse", "--absolute-git-dir")).resolve()
    common = (wt / _git_out(wt, "rev-parse", "--git-common-dir")).resolve()
    if git_dir == common:
        raise RuntimeError("AIB_API_WORKTREE 指到主樹(正本),不是這一席自己的 worktree:%s" % wt)
    proj = Path(project_dir).resolve()
    if (proj / _git_out(proj, "rev-parse", "--git-common-dir")).resolve() != common:
        raise RuntimeError("AIB_API_WORKTREE 跟 --cwd 不是同一個專案。")
    branch = _dg.current_branch(wt)
    tail = _sa.uuid_tail5(session_id)
    if not tail:
        raise RuntimeError("算不出本席五碼(session id %r),無法確認這棵樹是自己的。" % session_id)
    own = re.match(r"^(?:ds/%s-|feat/ds-%s(?:-|$))" % (tail, tail), branch or "")
    if not _dg.OWN_BRANCH_RE.match(branch or "") or not own:
        raise RuntimeError("AIB_API_WORKTREE 的分支 %r 不是這一席(@%s)自己的分支。" % (branch, tail))
    return wt


def development_worktree(project_dir: str, session_id: str) -> Path:
    """Create or reuse one non-main worktree for a DeepSeek development seat."""
    project = Path(project_dir).resolve()
    probe = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != project:
        raise RuntimeError("DeepSeek 開發席必須從 Git 專案根目錄啟動。")
    root = Path(os.path.expanduser(
        os.environ.get("AIB_DEEPSEEK_WORKTREE_ROOT", "~/.ez2-aib/worktrees")))
    dest = root / ("deepseek-" + session_id.replace("/", "-"))
    branch = "deepseek/" + session_id.replace("/", "-")
    if dest.exists():
        current = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "--show-toplevel"],
            text=True, capture_output=True, check=False,
        )
        if current.returncode != 0 or Path(current.stdout.strip()).resolve() != dest.resolve():
            raise RuntimeError("DeepSeek worktree 路徑已被其他內容佔用。")
    else:
        root.mkdir(parents=True, exist_ok=True)
        exists = subprocess.run(
            ["git", "-C", str(project), "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
            check=False,
        ).returncode == 0
        command = ["git", "-C", str(project), "worktree", "add"]
        if not exists:
            command += ["-b", branch]
        command += [str(dest), branch if exists else "HEAD"]
        made = subprocess.run(command, text=True, capture_output=True, check=False)
        if made.returncode != 0:
            raise RuntimeError("建立 DeepSeek worktree 失敗：" + made.stderr.strip()[:300])
    return dest.resolve()


def load_history(path: Path):
    hist_path = path / "history.jsonl"
    messages = []
    if not hist_path.is_file():
        return messages
    for line in hist_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("role") in ("user", "assistant", "system"):
            content = row.get("content")
            if isinstance(content, str):
                messages.append({"role": row["role"], "content": content})
    return messages


def append_history(path: Path, role: str, content: str):
    hist_path = path / "history.jsonl"
    with open(hist_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": role, "content": content, "ts": int(time.time())}, ensure_ascii=False) + "\n")   # ts:10-09 重放卡要印時間
    try:
        os.chmod(hist_path, 0o600)
    except OSError:
        pass


def chat_completion(*, base_url: str, api_key: str, model: str, messages, stream: bool,
                    tools=None):
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    apply_sampling(payload)   # 觀察 11:AIB_API_TEMPERATURE
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
    )
    return urllib.request.urlopen(req, timeout=120)


def stream_assistant(resp) -> str:
    parts = []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content")
            if delta:
                parts.append(delta)
                sys.stdout.write(delta)
                sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(parts)


def one_shot_assistant(resp) -> str:
    data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    sys.stdout.write(content + ("\n" if not content.endswith("\n") else ""))
    sys.stdout.flush()
    return content


def run_aib_dm(target: str, message: str) -> str:
    """Delegate to shared native aib dm --verify runner."""
    return _aib_dm.run_aib_dm(target, message)




class LocalWorkspace:
    """A deliberately small local-development authority for an API-key seat."""

    def __init__(self, root: str, worktree_envs=("AIB_API_WORKTREE", "AIB_DEEPSEEK_WORKTREE")):
        self.root = Path(root).resolve()
        self.read_paths = set()
        self.proposed_paths = set()
        self.events = []          # 快驗升級 2:改檔與測試結果的順序紀錄
        # §114.4b:各家 API 席共用;NVIDIA r1 低:非 DeepSeek 家只認 AIB_API_WORKTREE(繼承到 DeepSeek 的樹就錯寫)
        _wt = next((os.environ.get(k) for k in worktree_envs if os.environ.get(k)), "")
        self.write_root = Path(_wt).resolve() if _wt else None

    def _lease_tool(self):
        return self.root / "tools" / "governance" / "host_lease.py"

    def _lease_acquire(self, what):
        """跑測試前拿本機租約(同機只准一份驗證)。回 run_id(字串帶 'ds-' 前綴的 tuple)或拒絕原因字串或 None。
        沒有租約工具的倉(治具)⇒ 回 ('', False) 當作不用租約。"""
        tool = self._lease_tool()
        if not tool.is_file():
            # r1 M1(codex):缺租約工具不准 fail-open 照跑;只有治具明給旗標才放行
            if os.environ.get("AIB_DEEPSEEK_NO_LEASE_OK") == "1":
                return ("", False)
            return "這個倉沒有機器租約工具(tools/governance/host_lease.py),不跑"
        run_id = "ds-%s-%d" % (re.sub(r"[^A-Za-z0-9]", "", what)[:24], os.getpid())
        try:
            r = subprocess.run([sys.executable, str(tool), "acquire", "--owner", "deepseek@" + (os.environ.get("AIB_SEAT_CODE") or "?"),
                                "--what", "deepseek test " + what, "--run-id", run_id, "--ttl", "900", "--pid", str(os.getpid())],
                               capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "租約工具失敗:%s" % type(exc).__name__
        if r.returncode != 0:
            return ((r.stdout + r.stderr).strip().splitlines() or ["機器被佔用"])[-1][:160]
        return (run_id, True)

    def _lease_release(self, lease):
        if not isinstance(lease, tuple) or not lease[1]:
            return
        try:
            subprocess.run([sys.executable, str(self._lease_tool()), "release", "--run-id", lease[0]],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _path(self, raw):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("需要專案內的相對檔案路徑。")
        candidate = (self.root / raw).resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise ValueError("拒絕專案外路徑。")
        import deepseek_git as _dg
        if _dg.forbidden_part(candidate.relative_to(self.root).parts) or _dg.forbidden_part(Path(raw).parts):
            raise ValueError("拒絕讀寫 Git 或秘密檔(不分大小寫、全形、尾端點或空白都算;含 .env)。")
        try:
            if candidate.is_file() and os.stat(candidate).st_nlink > 1:
                raise ValueError("拒絕讀寫硬連結檔(可能指到秘密檔)。")
        except OSError:
            pass
        return candidate

    def execute(self, args):
        """快驗升級 2(09-28):照原樣執行,另外把「改了哪個檔」「跑了哪支測試、結果」依序記下來,
        回合結束時由 evidence_note 產出檢查紀錄(機器寫的,模型改不了)。"""
        out = self._execute(args)
        op = (args.get("operation") or "").strip() if isinstance(args, dict) else ""
        if op in ("write", "replace") and out.startswith("已修改"):
            self.events.append(("write", str(args.get("path") or "")))
        elif op == "test":
            m = re.match(r"測試 rc=(-?\d+)", out)
            if m:
                self.events.append(("test", str(args.get("path") or ""), int(m.group(1)), "沿用上次的綠燈" in out[:80]))
        return out

    def _execute(self, args):
        op = (args.get("operation") or "").strip()
        try:
            if op == "read":
                path = self._path(args.get("path"))
                text = path.read_text(encoding="utf-8")
                if len(text) > 24000:
                    text = text[:24000] + "\n[截斷：檔案超過 24000 字元]"
                self.read_paths.add(path)
                return "已讀取 %s：\n%s" % (path.relative_to(self.root), text)
            if op == "read_chunk":
                path = self._path(args.get("path"))
                start = args.get("start_line", 1)
                count = args.get("line_count", 300)
                if not isinstance(start, int) or not isinstance(count, int) or start < 1 or count < 1:
                    return "分段讀取未執行：start_line 與 line_count 必須是正整數。"
                if count > 400:
                    return "分段讀取未執行：line_count 不得超過 400。"
                lines = path.read_text(encoding="utf-8").splitlines()
                if start > len(lines) + 1:
                    return "分段讀取未執行：start_line 超過檔案尾端。"
                end = min(start + count - 1, len(lines))
                self.read_paths.add(path)
                body = "\n".join(lines[start - 1:end])
                if end < len(lines):
                    marker = "[尚未讀完：下一段 start_line=%d；總行數=%d]" % (end + 1, len(lines))
                else:
                    marker = "[已讀完：%s 1-%d/%d 行]" % (path.relative_to(self.root), end, len(lines))
                return "已讀取 %s:%d-%d/%d 行\n%s\n%s" % (
                    path.relative_to(self.root), start, end, len(lines), body, marker)
            if op == "search":
                query = args.get("query")
                if not isinstance(query, str) or not query:
                    return "搜尋未執行：需要 query。"
                hits = []
                for path in self.root.rglob("*"):
                    if not path.is_file() or ".git" in path.parts or "secrets" in path.parts:
                        continue
                    try:
                        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                            if query in line:
                                hits.append("%s:%d:%s" % (path.relative_to(self.root), number, line[:300]))
                                if len(hits) == 80:
                                    return "搜尋結果(截斷 80 筆)：\n" + "\n".join(hits)
                    except (OSError, UnicodeDecodeError):
                        continue
                return "搜尋結果：\n" + ("\n".join(hits) if hits else "沒有命中")
            if op == "git":
                import deepseek_git
                return deepseek_git.execute(self.root, str(self.write_root) if self.write_root else None, args)
            if op == "test":
                path = self._path(args.get("path"))
                if path.parent != self.root / "tests" or not path.name.startswith("test-") or path.suffix != ".py":
                    return "測試未執行：只允許專案內 tests/test-*.py。"
                if not path.is_file():
                    return "測試未執行：找不到指定測試檔。"
                import seat_test_reuse as _str
                # 09-29(設計稿 docs/DESIGN-aib-test-entry-shadow-0929.md 第四節):API 席(deepseek、nvidia、
                #   openrouter)都用這個 LocalWorkspace,跑測試一律走 aib test 的入口:先自檢影子續跑、
                #   能沿用就沿用(不拿租約、不排隊)、實跑一定留紀錄。實跑照舊拿機器租約(a seat 09-29 Q3)。
                verify_dir = str(Path(__file__).resolve().parents[2] / "tools" / "verify")
                if verify_dir not in sys.path:
                    sys.path.insert(0, verify_dir)
                import test_entry
                rel = str(path.relative_to(self.root))
                fresh = bool(args.get("fresh"))
                if not fresh and test_entry.would_reuse(self.root, rel):
                    res = test_entry.run_tests(self.root, [rel])
                    r = res["tests"][0]
                    if r.get("reused"):
                        # 上次輸出的尾巴:紀錄裡有才附,並標明是上次的(a seat 09-29 16:00;入口的紀錄由 runner 寫,有沒有 tail 看它)
                        tail = r.get("output") or ""
                        return ("測試 rc=0(沿用上次的綠燈:這支測試上次 %s 實跑 %s 秒,它讀過的 %d 個檔和執行環境都沒變,"
                                "所以沒有重跑、沒有排隊;要真的重跑請帶 fresh=true)\n%s%s"
                                % (r.get("prev_time") or "?", r.get("prev_duration_s", "?"), r.get("prev_inputs") or 0,
                                   res["line1"], ("\n(以下是上次實跑的輸出尾巴,不是這一次的)\n" + tail) if tail else
                                   "\n(這筆紀錄沒有存上次的輸出)"))
                    # 判完到跑之間沿用池變了,入口已經實跑(這一次沒拿租約);照實回結果
                    return "測試 rc=%d(%s):\n%s" % (r["rc"], res["line1"], _str.compact(r["rc"], r.get("output") or ""))
                lease = self._lease_acquire(path.name)
                if lease is None or isinstance(lease, str):
                    return "測試未執行：排隊中(%s)。機器有人在用,稍後再試;不要改用別的方法跑。" % (lease or "拿不到機器租約")
                try:
                    res = test_entry.run_tests(self.root, [rel], fresh=fresh, timeout=600)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as err:
                    return "測試未執行：%s" % err
                finally:
                    self._lease_release(lease)
                r = res["tests"][0]
                return "測試 rc=%d(%s):\n%s" % (r["rc"], res["line1"], _str.compact(r["rc"], r.get("output") or ""))
            if op == "propose":
                path = self._path(args.get("path"))
                rationale = args.get("rationale")
                if path not in self.read_paths:
                    return "提案未建立：必須先讀取同一檔案。"
                if not isinstance(rationale, str) or not rationale.strip():
                    return "提案未建立：必須說明修改原因。"
                self.proposed_paths.add(path)
                return "修改提案已記錄 %s：%s" % (path.relative_to(self.root), rationale.strip())
            if op == "write":
                path = self._path(args.get("path"))
                content = args.get("content")
                if self.write_root != self.root:
                    return "修改未執行：此席未在自己的 worktree 中(開席端要建 worktree 並設 AIB_API_WORKTREE)。"
                if path not in self.read_paths:
                    return "修改未執行：必須先讀取同一檔案。"
                if path not in self.proposed_paths:
                    return "修改未執行：必須先建立並說明修改提案。"
                if not isinstance(content, str) or len(content) > 1_000_000:
                    return "修改未執行：內容必須是 1 MB 以內的文字。"
                temp = path.with_name(path.name + ".aib-write-tmp")
                temp.write_text(content, encoding="utf-8")
                os.replace(temp, path)
                return "已修改 %s（尚未提交或推送）。" % path.relative_to(self.root)
            if op == "replace":
                # 觀察 2(09-28):改一行不必整支重寫——只送「這段 → 換成那段」,寫出來的 token 少很多。守門同 write。
                path = self._path(args.get("path"))
                old, new = args.get("old"), args.get("new")
                if self.write_root != self.root:
                    return "修改未執行：此席未在自己的 worktree 中(開席端要建 worktree 並設 AIB_API_WORKTREE)。"
                if path not in self.read_paths:
                    return "修改未執行：必須先讀取同一檔案。"
                if path not in self.proposed_paths:
                    return "修改未執行：必須先建立並說明修改提案。"
                if not isinstance(old, str) or not old or not isinstance(new, str) or len(new) > 1_000_000:
                    return "修改未執行：replace 需要非空的 old 與文字 new。"
                text = path.read_text(encoding="utf-8")
                hits = text.count(old)
                if hits != 1:
                    return "修改未執行：old 在檔案裡出現 %d 次,必須剛好 1 次(多給幾行前後文讓它唯一)。" % hits
                temp = path.with_name(path.name + ".aib-write-tmp")
                temp.write_text(text.replace(old, new, 1), encoding="utf-8")
                os.replace(temp, path)
                return "已修改 %s 的一段（尚未提交或推送）。" % path.relative_to(self.root)
            return "工具未執行：operation 只可為 read、read_chunk、search、test、propose、replace、write 或 git。"
        except (OSError, UnicodeDecodeError, ValueError) as err:
            return "本地工具未執行：%s" % err


# ── 09-28 觀察紀錄程式化(雲端 claudebot 接手 a seat):六支 API 席共用 ─────────────────────────
KEEP_RECENT_TOOL_RESULTS = int(os.environ.get("AIB_API_KEEP_TOOL_RESULTS") or 4)
OLD_TOOL_RESULT_CHARS = 600
_TOOL_RESULT_PREFIXES = ("aib_local result: ", "aib_dm result: ")


def _is_tool_result(msg) -> bool:
    if msg.get("role") == "tool":
        return True
    content = msg.get("content")
    return msg.get("role") == "user" and isinstance(content, str) and content.startswith(_TOOL_RESULT_PREFIXES)


# 省 token 設計稿 10h「歷史壓縮」是**量得到**的那一行:累計這個行程裡 compact_wire 少送的字元數(每回合都算一次,
#   因為被截掉的內容每一回合本來都要重送)。只加不減;儀表板與每日實測讀它,不影響送出的內容。
COMPACT_SAVED_CHARS = 0


EVIDENCE_CLAIM_RE = re.compile(r"修好|修正了|已修正|可以用了|能用了|測試通過|全綠|全部通過|\bfixed\b|\btests? pass|\ball (?:tests )?pass|works now", re.I)


def evidence_note(workspace, final):
    """快驗升級 2(the maintainer 09-28「說修好了一定要附檢查紀錄」,基因 3):這一回合有改檔,就在回覆最後附上系統產生的
    檢查紀錄——改了哪些檔、改完之後跑了哪些測試、紅還是綠、是實跑還是沿用上次綠燈。回覆說修好了、但改完沒跑測試
    或跑出紅的,加一行警告。沒有改檔的回合不加任何東西。AIB_SEAT_EVIDENCE_NOTE=0 關(每日實測用,讓成績只看模型)。"""
    if not final or os.environ.get("AIB_SEAT_EVIDENCE_NOTE", "1") == "0":
        return final
    events = list(getattr(workspace, "events", None) or [])
    writes = [i for i, e in enumerate(events) if e[0] == "write"]
    if not writes:
        return final
    after = [e for e in events[writes[-1] + 1:] if e[0] == "test"]
    files = []
    for i in writes:
        if events[i][1] not in files:
            files.append(events[i][1])
    lines = ["", "── 檢查紀錄(系統產生,不是模型寫的) ──", "改了:" + "、".join(files)]
    for e in after:
        lines.append("改完之後跑的測試:%s %s(%s)" % (e[1], "綠" if e[2] == 0 else "紅 rc=%d" % e[2],
                                                   "沿用上次綠燈" if e[3] else "實跑"))
    if not after:
        lines.append("改完之後沒有跑任何測試 ⇒ 還沒檢查")
    if EVIDENCE_CLAIM_RE.search(final) and not (after and all(e[2] == 0 for e in after)):
        lines.insert(2, "⚠ 回覆說已經修好,但%s,這個說法還沒經過檢查。"
                     % ("改完之後沒有跑測試" if not after else "改完之後的測試有紅的"))
    return final.rstrip("\n") + "\n" + "\n".join(lines)


def compact_wire(wire):
    """觀察 3、6:工具結果每回合累積重送,越後面越貴。只保留最近幾筆全文,較早的截成開頭一段並註明原長度。
    回傳新串列,不改呼叫端的 wire(完整歷史照留在本地)。"""
    idx = [i for i, m in enumerate(wire) if isinstance(m, dict) and _is_tool_result(m)]
    old = set(idx[:-KEEP_RECENT_TOOL_RESULTS]) if KEEP_RECENT_TOOL_RESULTS > 0 else set(idx)
    out = []
    saved = 0
    for i, m in enumerate(wire):
        content = m.get("content") if isinstance(m, dict) else None
        if i in old and isinstance(content, str) and len(content) > OLD_TOOL_RESULT_CHARS:
            m = dict(m, content=content[:OLD_TOOL_RESULT_CHARS]
                     + "\n[較早的工具結果已壓縮：原 %d 字；需要時請重新讀取]" % len(content))
            saved += len(content) - len(m["content"])
        out.append(m)
    global COMPACT_SAVED_CHARS
    COMPACT_SAVED_CHARS += max(0, saved)
    return out


def apply_sampling(payload: dict) -> dict:
    """觀察 11:AIB_API_TEMPERATURE 有設就固定隨機程度(實測、重現問題時用);沒設照平台預設。"""
    raw = (os.environ.get("AIB_API_TEMPERATURE") or "").strip()
    if raw:
        try:
            payload["temperature"] = max(0.0, min(2.0, float(raw)))
        except ValueError:
            pass
    return payload


def turn_error(err) -> str:
    """觀察 10:模型回合失敗時標出種類,呼叫端與實測不必從文字猜。保留原本「模型回合失敗」字樣。"""
    import socket
    code = getattr(err, "code", None)
    text = str(err)
    if code == 429:
        kind = "rate_limited"
    elif isinstance(code, int) and 500 <= code < 600:
        kind = "server_error"
    elif isinstance(code, int) and code in (401, 403):
        kind = "auth"
    elif isinstance(err, (TimeoutError, socket.timeout)) or "timed out" in text.lower():
        kind = "timeout"
    else:
        kind = "other"
    return "（模型回合失敗[%s]：%s）" % (kind, err)


def text_tool_call(content):
    """Decode the fallback protocol for models that ignore API tool declarations."""
    match = TEXT_TOOL.fullmatch(content or "")
    if not match:
        return None
    try:
        call = json.loads(match.group(1))
    except ValueError:
        return None
    return call if isinstance(call, dict) else None


def plain_read_tool_call(content):
    """Execute only explicit read/search text when the model ignores tool calls."""
    match = PLAIN_READ_CHUNK.search(content or "")
    if match:
        return {"name": "aib_local", "arguments": {
            "operation": "read_chunk", "path": match.group(1),
            "start_line": int(match.group(2)), "line_count": int(match.group(3)),
        }}
    match = PLAIN_SEARCH.search(content or "")
    if match:
        return {"name": "aib_local", "arguments": {
            "operation": "search", "query": match.group(1).strip(),
        }}
    return None


def enforce_dm_truth(content: str, dm_outcomes) -> str:
    """Delegate to shared DM truth filter."""
    return _aib_dm.enforce_dm_truth(content, dm_outcomes)




def assistant_turn(*, base_url: str, api_key: str, model: str, messages, cwd=None) -> str:
    """Complete one model turn, including at most two explicit DM tool calls."""
    wire = [_api_persona.with_persona(TOOL_SYSTEM)] + list(messages)   # 09-30:人格每一輪都在系統說明裡
    workspace = LocalWorkspace(cwd or os.getcwd())
    dm_outcomes = []
    dm_fails = 0
    for _attempt in range(TOOL_ROUNDS):
        if dm_fails >= DM_FAIL_DROP_TOOLS:
            wire[0] = _api_persona.with_persona(TOOL_SYSTEM_DM_OFF)     # 停用後不再說明 aib_dm
        tools = [_aib_dm.dm_tool(), LOCAL_TOOL] if dm_fails < DM_FAIL_DROP_TOOLS else [LOCAL_TOOL]
        with chat_completion(base_url=base_url, api_key=api_key, model=model,
                             messages=compact_wire(wire), stream=False, tools=tools) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if not choices:
            return ""
        reply = choices[0].get("message") or {}
        calls = reply.get("tool_calls") or []
        content = reply.get("content") or ""
        fallback_calls = []
        if not calls:
            explicit = text_tool_call(content)
            if explicit is not None and explicit.get("name") == "aib_local":
                fallback_calls = [explicit]
            else:
                one = plain_read_tool_call(content)
                if one is not None:
                    fallback_calls = [one]
            if not fallback_calls:
                final = evidence_note(workspace, enforce_dm_truth(content, dm_outcomes))
                if final:
                    sys.stdout.write(final + ("\n" if not final.endswith("\n") else ""))
                    sys.stdout.flush()
                return final
            calls = []
            for index, fallback_call in enumerate(fallback_calls):
                calls.append({
                    "id": "text-tool-%d-%d" % (_attempt, index),
                    "type": "function",
                    "function": {
                        "name": fallback_call.get("name", ""),
                        "arguments": json.dumps(fallback_call.get("arguments") or {}, ensure_ascii=False),
                    },
                })
        wire.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls:
            if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP:
                # 09-28 P1 r3c Codex 中 2:同一批裡達到硬打上限後,後面的呼叫(含 aib_local 寫檔/git)一律不執行;
                #   每個呼叫仍回一則 tool 結果(API 要求每個 tool_call_id 都有回應)。
                wire.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": "工具未執行：這一回合已停止(aib_dm 停用後仍一直要求送訊息),同一批剩下的工具呼叫都不執行。"})
                continue
            fn = call.get("function") or {}
            raw_args = fn.get("arguments") or {}
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args)
                except (TypeError, ValueError):
                    args = {}
            if fn.get("name") == "aib_dm" and dm_fails >= DM_FAIL_DROP_TOOLS:
                dm_fails += 1              # 已停用還硬打:不執行、不跑 aib dm
                outcome = "工具未執行：aib_dm 已停用,請直接用文字回答使用者。"
            elif fn.get("name") == "aib_dm":
                outcome = run_aib_dm(args.get("target", ""), args.get("message", ""))
                dm_outcomes.append(outcome)
                if outcome.startswith("DM 未送出"):
                    dm_fails += 1
                    if dm_fails >= DM_FAIL_DROP_TOOLS:
                        outcome += "(本回合已失敗 %d 次:接下來不再提供 aib_dm,請直接用文字回答使用者。)" % dm_fails
            elif fn.get("name") == "aib_local":
                outcome = workspace.execute(args)
            else:
                outcome = "工具未執行：不支援的工具名稱。"
            wire.append({"role": "tool", "tool_call_id": call.get("id", ""),
                         "content": outcome})
        if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP:
            break
    # 以前這裡寫「DM 工具連續呼叫超過上限」——實際是讀檔等所有工具的回合用完,字面錯(09-27 a seat 讀大檔就踩到)
    stop = ("aib_dm 已停用,模型仍一直要求送訊息;這一回合先停。請直接用文字再問一次。"
            if dm_fails >= DM_FAIL_DROP_TOOLS + DM_HAMMER_STOP
            else "工具回合用完(%d 次),這件還沒做完;請回我「繼續」,我接著做。" % TOOL_ROUNDS)
    final = evidence_note(workspace, enforce_dm_truth(stop, dm_outcomes))
    if final:
        sys.stdout.write(final + ("\n" if not final.endswith("\n") else ""))
        sys.stdout.flush()
    return final


def _model_created_ts(row) -> int:
    """Best-effort listing/update epoch from OpenAI-compat model row."""
    if not isinstance(row, dict):
        return 0
    for key in ("updated_at", "updated", "created_at", "created"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            s = raw.strip()
            if s.isdigit():
                return int(s)
            # ISO-ish
            try:
                from datetime import datetime
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return int(datetime.fromisoformat(s).timestamp())
            except Exception:
                continue
    return 0


def _fmt_model_day(ts: int) -> str:
    """YYYY-MM-DD in local tz; placeholder when provider sends a bogus epoch."""
    if not ts or ts < 1_500_000_000:  # before ~2017-07 → treat as unreliable
        return "—"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "—"

def list_models(*, base_url: str, api_key: str):
    """GET /v1/models — newest-first rows as (id, created_ts).

    Sort by created/updated desc; ties keep provider order (not A–Z).
    Accept base with or without /v1.
    """
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        url = root + "/models"
    else:
        url = root + "/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = []
    for idx, row in enumerate(payload.get("data") or []):
        mid = row.get("id") if isinstance(row, dict) else None
        if not (isinstance(mid, str) and mid.strip()):
            continue
        mid = mid.strip()
        ts = _model_created_ts(row)
        rows.append((ts, idx, mid))
    # newest first; same ts → original API order
    rows.sort(key=lambda r: (-r[0], r[1]))
    # de-dupe by id keeping first (newest / earliest API index)
    seen = set()
    out = []
    for ts, _idx, mid in rows:
        if mid in seen:
            continue
        seen.add(mid)
        out.append((mid, ts))
    return out

def _bc_seat(sess: Path) -> str:
    try:
        from seat_address import uuid_tail5
        return uuid_tail5(sess.name) or ""
    except Exception:  # noqa: BLE001
        return ""


def _bc_replay(sess: Path, kind: str) -> int:
    try:
        import bridge_cards as _bc
        return _bc.replay_history_file(sess, kind)
    except Exception:  # noqa: BLE001
        return 0


def run_repl(*, api_key: str, model: str, base_url: str, sess: Path, messages):
    from bridge_cards import ReplFrame
    _frame = ReplFrame("deepseek", seat=_bc_seat(sess))   # 10-09:標題列帶席碼,跟橋接席的卡一致
    sys.stdout.write(
        "deepseek · model=%s · session=%s\n"
        "（API key；/models [關鍵字] 列模型（新→舊＋日期）、/model <id> 切換、/model 看現用；"
        "Ctrl-D /exit /quit 結束）\n"
        "%s\n"
        % (model, sess.name, _aib_dm.dm_boot_banner() + _aib_dm.gene_banner())
    )
    sys.stdout.flush()
    _api_model_gauge.record(sess.name, model, "deepseek")  # 開 REPL:蓋掉上一個行程切過的模型
    _bc_replay(sess, "deepseek")                               # 10-09:開席重放 history.jsonl 最近幾則(橋接席已有,API 席補齊)
    while True:
        try:
            _frame.close()
            line = input("DeepSeek> ")
        except EOFError:
            sys.stdout.write("\n")
            break
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            break
        text = line.strip()
        if not text:
            continue
        if text in ("/exit", "/quit", "/q"):
            break
        if text in ("/help", "/?"):
            sys.stdout.write(
                "指令：/models [關鍵字]  ·  /model <id> 切換  ·  /model 現用  ·  "
                "/dm <艙位碼/席碼> <訊息>  ·  /exit 結束\n"
            )
            sys.stdout.flush()
            continue
        if text == "/model" or text.startswith("/model "):
            parts = text.split(None, 1)
            if len(parts) == 1:
                sys.stdout.write("現用模型：%s\n" % model)
                sys.stdout.flush()
                continue
            new_m = parts[1].strip()
            if not new_m:
                sys.stdout.write("用法：/model <id>\n")
                sys.stdout.flush()
                continue
            model = new_m
            _api_model_gauge.record(sess.name, model, "deepseek")  # /model 切換:右欄跟著變
            sys.stdout.write("已切換模型 → %s（之後回合都用這個）\n" % model)
            sys.stdout.flush()
            continue
        if text == "/models" or text.startswith("/models "):
            filt = text.split(None, 1)[1].strip().lower() if " " in text else ""
            try:
                ids = list_models(base_url=base_url, api_key=api_key)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", errors="replace")[:300]
                sys.stderr.write("deepseek: /models HTTP %s — %s\n" % (err.code, detail))
                continue
            except urllib.error.URLError as err:
                sys.stderr.write("deepseek: /models 連線失敗 — %s\n" % err.reason)
                continue
            if filt:
                rows = [(i, ts) for i, ts in ids if filt in i.lower()]
            else:
                rows = list(ids)
            if not rows:
                sys.stdout.write("（沒有符合的模型）\n")
            else:
                sys.stdout.write(
                    "可選模型 %d 個%s（新→舊；右側為上架／更新日期）：\n"
                    % (len(rows), ("（含 %r）" % filt) if filt else "")
                )
                for mid, ts in rows:
                    mark = " *" if mid == model else ""
                    sys.stdout.write(
                        "  %s  %s%s\n" % (mid, _fmt_model_day(ts), mark)
                    )
            sys.stdout.flush()
            continue

        slash = _aib_dm.parse_dm_slash(text)
        if slash:
            target, body = slash
            outcome = run_aib_dm(target, body)
            sys.stdout.write(outcome + "\n")
            sys.stdout.flush()
            continue
        _frame.open()   # 10-09 對話框:品牌標題列(非 TTY 不印)
        messages.append({"role": "user", "content": text})
        append_history(sess, "user", text)
        sys.stdout.write("deepseek> ")
        sys.stdout.flush()
        try:
            content = assistant_turn(
                base_url=base_url, api_key=api_key, model=model,
                messages=messages, cwd=os.getcwd())
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            detail = raw[:400]
            if "cloudflare" in raw.lower() or "error code: 1010" in raw or "<!doctype html>" in raw.lower():
                detail = (
                    "Cloudflare 擋了請求（常是 UA／IP）。本 CLI 已帶 User-Agent；"
                    "若仍 403，多半是額度或儀表板限制。原始片段：%s"
                    % raw[:160].replace("\n", " ")
                )
            elif raw.lstrip().startswith("{"):
                try:
                    ej = json.loads(raw)
                    em = (ej.get("error") or {}).get("message") or raw[:200]
                    detail = em
                except Exception:
                    pass
            sys.stderr.write("deepseek: API HTTP %s — %s\n" % (err.code, detail))
            messages.pop()
            continue
        except urllib.error.URLError as err:
            sys.stderr.write("deepseek: 連線失敗 — %s\n" % err.reason)
            messages.pop()
            continue
        except Exception as err:  # noqa: BLE001
            sys.stderr.write("deepseek: 回合失敗 — %s\n" % err)
            messages.pop()
            continue
        if content:
            messages.append({"role": "assistant", "content": content})
            append_history(sess, "assistant", content)


def build_parser():
    p = argparse.ArgumentParser(prog="deepseek", description="DeepSeek API-key AIB seat")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--session-id", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--role", default="")
    p.add_argument("--mode", choices=("safe", "dgrs"), default="safe",
                   help="dgrs 對 API-key 席無沙箱意義；誠實接受旗標但不改權限模型")
    p.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE))
    p.add_argument("--prompt", default="", help="若給定則單回合後結束（測試用）")
    p.add_argument("--no-stream", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    sid = args.session_id.strip() or "anon"
    _api_persona.set_session(sid)   # 09-30:這一席綁的人格(鍵是 sid 尾五碼)
    if args.cwd:
        try:
            # 09-28 00:1x:開席端已指定這一席自己的 worktree(AIB_API_WORKTREE)就直接用,不另建 deepseek-<session>
            #   (另建的那棵分支叫 deepseek/<session>,不在 ds/* 白名單、也和 AIB_API_WORKTREE 對不上 ⇒ 寫檔與 git 全被拒)。
            #   09-28 P1 r2 低:與 --cwd 不相等時也以 AIB_API_WORKTREE 為準(LocalWorkspace 先讀它,另建樹只會讓寫檔全拒)。
            preset = (os.environ.get("AIB_API_WORKTREE") or "").strip()
            if preset:
                workspace = authorized_preset_worktree(preset, args.cwd, sid)
            else:
                workspace = development_worktree(args.cwd, sid)
            os.environ["AIB_DEEPSEEK_WORKTREE"] = str(workspace)
            os.chdir(workspace)
        except (OSError, RuntimeError) as err:
            raise SystemExit("deepseek: 無法進入目錄 %s: %s" % (args.cwd, err)) from err
    api_key = resolve_api_key()
    sess = session_dir(sid)
    messages = load_history(sess)
    if args.prompt:
        messages.append({"role": "user", "content": args.prompt})
        append_history(sess, "user", args.prompt)
        try:
            content = assistant_turn(base_url=args.base_url, api_key=api_key,
                                     model=args.model, messages=messages, cwd=os.getcwd())
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")[:400]
            raise SystemExit("deepseek: API HTTP %s — %s" % (err.code, detail)) from err
        if content:
            sys.stdout.write(content + ("\n" if not content.endswith("\n") else ""))
            sys.stdout.flush()
        if content:
            append_history(sess, "assistant", content)
        return 0
    run_repl(
        api_key=api_key, model=args.model, base_url=args.base_url,
        sess=sess, messages=messages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
