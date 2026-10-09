#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""建 <state_root>/secrets/ 整棵樹,並搬遷舊的兩支檔。

既有檔不覆寫內容。目錄 0700、檔 0600。不進 vault。
"""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "src" / "bootstrap"
if str(BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(BOOTSTRAP))
COCKPIT = BOOTSTRAP.parent / "cockpit"
if str(COCKPIT) not in sys.path:
    sys.path.insert(0, str(COCKPIT))
import i18n_lib  # noqa: E402  印給人看的字走 i18n,不留中文字面

import aib_secrets as S  # noqa: E402

LEGACY = (
    # (舊相對路徑, 新檔相對 secrets/, 新行名, 取值法)
    ("secrets/deepgram.env", "services.env", "DEEPGRAM_API_KEY", "env"),
    ("secrets/demo-token.txt", "ez2.env", "EZ2_DEMO_TOKEN", "raw"),
)


def _item(path, action, detail=""):
    return {"path": str(path), "action": action, "detail": detail}


def _chmod(path, mode):
    os.chmod(str(path), mode)
    got = stat.S_IMODE(os.lstat(str(path)).st_mode)
    if got != mode:
        raise OSError(i18n_lib.t("aib.secrets.init.chmod_still") + " %s: %s" % (format(got, "04o"), path))


def _mkdir(path, items):
    path = Path(path)
    if path.is_symlink():
        items.append(_item(path, "fail", "symlink"))
        return False
    if path.exists():
        if not path.is_dir():
            items.append(_item(path, "fail", "not a dir"))
            return False
        if stat.S_IMODE(os.lstat(str(path)).st_mode) != S.DIR_MODE:
            _chmod(path, S.DIR_MODE)
            items.append(_item(path, "fixed", "0700"))
        else:
            items.append(_item(path, "ok", "dir"))
        return True
    os.mkdir(str(path), S.DIR_MODE)
    _chmod(path, S.DIR_MODE)
    items.append(_item(path, "created", "0700"))
    return True


def _write_new(path, text, items):
    path = Path(path)
    if path.exists():
        items.append(_item(path, "ok", "exists, not overwritten"))
        return False
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, S.FILE_MODE)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fchmod(fd, S.FILE_MODE)
    finally:
        os.close(fd)
    items.append(_item(path, "created", "0600"))
    return True


def _set_line(path, name, value):
    """在既有或新檔寫入一行;已有非空值不覆寫。回 True=寫入了。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    rows = S.parse_env_bytes(text)
    if rows.get(name):
        return False
    lines = text.splitlines()
    replaced = False
    out = []
    for ln in lines:
        if ln.startswith(name + "=") and not replaced:
            out.append("%s=%s" % (name, value))
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append("%s=%s" % (name, value))
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, S.FILE_MODE)
    try:
        os.write(fd, body.encode("utf-8"))
        os.fchmod(fd, S.FILE_MODE)
    finally:
        os.close(fd)
    return True


def _wipe_legacy(path, dest_rel):
    note = "# migrated to %s %s\n" % (dest_rel, date.today().isoformat())
    fd = os.open(str(path), os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, note.encode("utf-8"))
        os.fchmod(fd, S.FILE_MODE)
    finally:
        os.close(fd)


def migrate_legacy(state_root, secrets, items):
    state_root = Path(state_root)
    log_path = secrets / "MIGRATED.jsonl"
    for old_rel, new_rel, name, how in LEGACY:
        src = state_root / old_rel
        if not src.is_file():
            continue
        raw = src.read_bytes()
        if how == "env":
            rows = S.parse_env_bytes(raw)
            value = rows.get(name) or rows.get("DEEPGRAM_API_KEY") or ""
            if not value and len(rows) == 1:
                value = next(iter(rows.values()))
        else:
            value = raw.decode("utf-8", "replace").strip()
        if not value:
            items.append(_item(src, "ok", "legacy empty, skip"))
            continue
        dest = secrets / new_rel
        wrote = _set_line(dest, name, value)
        _wipe_legacy(src, "secrets/" + new_rel)
        rec = {
            "from": old_rel,
            "to": "secrets/" + new_rel,
            "name": name,
            "wrote": wrote,
            "date": date.today().isoformat(),
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.chmod(str(log_path), S.FILE_MODE)
        items.append(_item(src, "migrated", name))


def ensure_layout(state_root, env=None):
    """建樹。回 items 清單(與 aib_init 同形的 action 字)。"""
    state_root = Path(os.path.expanduser(str(state_root)))
    env = dict(os.environ if env is None else env)
    env.setdefault("EZ2_AIB_STATE_ROOT", str(state_root))
    secrets = S.secrets_dir(env)
    S._refuse_vault(secrets)
    items = []
    if not _mkdir(secrets, items):
        return items
    for sub in ("social", "live", "games"):
        if not _mkdir(secrets / sub, items):
            return items
    _write_new(secrets / "README.md", S.README_TEXT, items)
    today = date.today().isoformat()
    index = {"backend": "file", "entries": []}
    for row in S.index_entries():
        row = dict(row)
        row["created"] = today
        index["entries"].append(row)
    index_path = secrets / "index.json"
    if not index_path.exists():
        _write_new(index_path,
                   json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                   items)
    else:
        items.append(_item(index_path, "ok", "exists, not overwritten"))
    seen = set()
    for rel, _p, _c, _cmt, _keys in S.LAYOUT:
        if rel in seen:
            continue
        seen.add(rel)
        parent = secrets / Path(rel).parent
        if parent != secrets:
            _mkdir(parent, items)
        _write_new(secrets / rel, S.template_text(rel), items)
    import secret_read_gate as G
    import secret_collect as COL
    import secret_crypto as C
    _write_new(secrets / G.POLICY_NAME,
               json.dumps(G.DEFAULT_POLICY, ensure_ascii=False, indent=2) + "\n",
               items)
    _write_new(secrets / COL.POLICY_NAME,
               json.dumps(COL.DEFAULT_POLICY, ensure_ascii=False, indent=2) + "\n",
               items)
    try:
        wrap = C.wrap_key_path(secrets)
        if wrap.exists():
            items.append(_item(wrap, "ok", "exists, not overwritten"))
        else:
            C.load_wrap_key(secrets, create=True)
            items.append(_item(wrap, "created", "0600"))
    except C.CryptoError as err:
        items.append(_item(secrets / C.KEY_NAME, "fail", err.reason))
    migrate_legacy(state_root, secrets, items)
    return items


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=i18n_lib.t("aib.secrets.init.description"))
    p.add_argument(
        "--state-root",
        default=os.environ.get(
            "EZ2_AIB_STATE_ROOT",
            os.environ.get("EZ2_AIB_HOME",
                           os.path.expanduser("~/.ez2-aib"))))
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    items = ensure_layout(args.state_root)
    fails = sum(1 for i in items if i["action"] == "fail")
    if args.json:
        print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    else:
        for i in items:
            print("%s %s %s" % (i["action"], i["path"], i["detail"]))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
