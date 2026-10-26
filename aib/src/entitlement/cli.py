#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-entitlement CLI：資格查詢與發撤草稿殼。

for_task: §108.320
status → 機器可讀 JSON（永不含憑證原文）。
issue / revoke → 草稿殼 only，不接真金鑰（TODO the maintainer）。
"""
from __future__ import annotations

import json
import sys
from typing import List


def _print_json(obj: dict, exit_code: int = 0) -> int:
    json.dump(obj, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return exit_code


def cmd_status(argv: List[str]) -> int:
    from .tier import get_entitlement

    result = get_entitlement()
    # 雙重保險：若未來有人把敏感欄塞進 result，這裡剝掉
    safe = {
        "tier": result.get("tier"),
        "providers": result.get("providers"),
        "reason": result.get("reason"),
        "source_path": result.get("source_path"),
    }
    return _print_json(safe, 0)


def cmd_plan(argv: List[str]) -> int:
    """方案級別 free / plus / pro / ultra(外掛只問這裡);--json 跟不帶一樣輸出 JSON。"""
    from .plan import current

    r = current()
    safe = {k: r.get(k) for k in ("plan", "reason", "expires_at", "trial", "machine_bound", "source")}
    return _print_json(safe, 0)


def cmd_allowed(argv: List[str]) -> int:
    """aib license allowed <sku> [--free-at <plan>] [--json]:這件現在能不能用(方案內含 / 單買 / 單訂閱同一支判斷)。"""
    from .items import allowed

    args = [a for a in argv if a != "--json"]
    sku, free_at = "", None
    i = 0
    while i < len(args):
        if args[i] == "--free-at" and i + 1 < len(args):
            free_at = args[i + 1]
            i += 2
            continue
        if not sku:
            sku = args[i]
        i += 1
    if not sku:
        sys.stderr.write("usage: aib license allowed <sku> [--free-at <plan>] [--json]\n")
        return 2
    r = allowed(sku, free_at)
    safe = {k: r.get(k) for k in ("allowed", "source", "plan", "reason", "expires_at", "trial", "left_days", "was")}
    safe["sku"] = sku
    return _print_json(safe, 0)


def cmd_issue(argv: List[str]) -> int:
    # TODO(the maintainer §108.320): 發證接真金鑰／後台；草稿殼不寫檔、不產簽章。
    return _print_json(
        {
            "ok": False,
            "action": "issue",
            "reason": "draft_shell",
            "message": "issue is a draft shell; no real key wired (for_task §108.320)",
        },
        2,
    )


def cmd_revoke(argv: List[str]) -> int:
    # TODO(the maintainer §108.320): 撤銷接真金鑰／後台；草稿殼不寫檔。
    return _print_json(
        {
            "ok": False,
            "action": "revoke",
            "reason": "draft_shell",
            "message": "revoke is a draft shell; no real key wired (for_task §108.320)",
        },
        2,
    )


def _license_obj():
    from .store import read_license_obj
    obj = read_license_obj() or {}
    return obj if isinstance(obj, dict) else {}


def cmd_device_id(argv: List[str]) -> int:
    """這台在這份授權下的機器碼(給發證的人做離線綁定用;不含任何可辨識這台的原始資訊)。"""
    from . import device
    lid = argv[0] if argv else _license_obj().get("license_id")
    did = device.device_id(lid) if lid else None
    return _print_json({"ok": did is not None, "license_id": lid, "device_id": did}, 0 if did else 3)


def _post(url: str, payload: dict, timeout: float = 20.0) -> dict:
    import urllib.request
    if not (url.startswith("https://") or url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        raise ValueError("activation_url must be https")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "error": "http-%d" % e.code}


def cmd_activate(argv: List[str], action: str = "activate") -> int:
    """線上啟用 / 停用這台:把(簽過的)授權與這台的機器碼送到授權裡寫的 activation_url。只送這兩樣。"""
    from . import binding, device
    from .store import resolve_home
    obj = _license_obj()
    url, lid = obj.get("activation_url"), obj.get("license_id")
    if not url or not lid:
        return _print_json({"ok": False, "error": "license-has-no-activation"}, 3)
    did = device.device_id(lid)
    if did is None:
        return _print_json({"ok": False, "error": "no-machine-id"}, 3)
    try:
        res = _post(url.rstrip("/") + "/" + action, {"license": obj, "device_id": did})
    except Exception as exc:  # noqa: BLE001 - 連不上 ⇒ 照實說,不改本機檔
        return _print_json({"ok": False, "error": "unreachable:%s" % type(exc).__name__}, 4)
    if action == "deactivate":
        if res.get("ok"):
            try:
                binding.activation_path().unlink()
            except OSError:
                pass
        return _print_json({"ok": bool(res.get("ok")), "error": res.get("error")}, 0 if res.get("ok") else 3)
    token = res.get("token")
    ok, why = binding.token_ok(token, lid, did)
    if not ok:
        return _print_json({"ok": False, "error": res.get("error") or why, "max_devices": res.get("max_devices")}, 3)
    p = binding.activation_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(token, ensure_ascii=False) + "\n", encoding="utf-8")
    return _print_json({"ok": True, "expires_at": token.get("expires_at"), "path": str(resolve_home() / binding.ACTIVATION_FILE)}, 0)


BACKUPS_KEEP = 2


def cmd_install(argv: List[str]) -> int:
    """aib license install <檔>(10-20 @abb89,會員清單 #9):驗簽章與到期,放到 <home>/license.json(0600);
    有舊檔先備份 license.json.bak-<月日-時分>,只留最近 BACKUPS_KEEP 份。只印 tier / addons / 到期 / 路徑,不印簽章。"""
    from pathlib import Path
    from .store import read_license_obj
    if len(argv) != 1:
        return _print_json({"ok": False, "error": "usage: aib license install <file>"}, 2)
    obj = read_license_obj(path=Path(argv[0]).expanduser())
    if obj is None:
        return _print_json({"ok": False, "error": "not-a-license-file"}, 2)
    res, rc = install_obj(obj)
    return _print_json(res, rc)


def install_obj(obj):
    """驗簽章與到期,放到 <home>/license.json(0600、留備份)。回 (回條, rc);aib license install 與 aib plugins license 共用。"""
    import os
    import shutil
    import time
    from . import signing
    from .store import resolve_license_path
    from .tier import _parse_expires_at, _utc_now
    if not isinstance(obj, dict):
        return {"ok": False, "error": "not-a-license-file"}, 2
    ver = signing.verify_object(obj)
    if ver != signing.OK:
        return {"ok": False, "error": "signature-%s" % ver}, 3
    exp = _parse_expires_at(obj.get("expires_at"))
    if exp is not None and exp <= _utc_now():
        return {"ok": False, "error": "expired", "expires_at": obj.get("expires_at")}, 3
    dest = resolve_license_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if dest.exists():
        backup = dest.with_name("%s.bak-%s" % (dest.name, time.strftime("%m%d-%H%M%S")))
        shutil.copy2(dest, backup)
        os.chmod(backup, 0o600)
        old = sorted(dest.parent.glob(dest.name + ".bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in old[BACKUPS_KEEP:]:
            p.unlink()
    tmp = dest.with_name("%s.%d.tmp" % (dest.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, dest)
    os.chmod(dest, 0o600)
    addons = [a for a in (obj.get("addons") or []) if isinstance(a, str)]
    return {"ok": True, "tier": obj.get("tier"), "addons": addons, "expires_at": obj.get("expires_at"),
            "path": str(dest), "backup": str(backup) if backup else None}, 0


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stderr.write(
            "usage: aib-entitlement status|plan|device-id|activate|deactivate|install|issue|revoke\n"
            "  install <file>  verify a signed license.json and put it in place (0600; old one backed up)\n"
            "  device-id   this machine's id under the license (for offline binding)\n"
            "  activate    activate this machine online (license with activation_url)\n"
            "  deactivate  free this machine's slot (do it before switching computers)\n"
            "  status  print entitlement JSON (machine-readable)\n"
            "  plan [--json]  print plan JSON: free / go / plus / pro / ultra (signed license or trial)\n"
            "  allowed <sku> [--free-at <plan>] [--json]  is this sku usable now (plan tier, single purchase or rental)\n"
            "  issue   draft shell only (no real key)\n"
            "  revoke  draft shell only (no real key)\n"
        )
        return 0 if args and args[0] in ("-h", "--help", "help") else 2
    cmd = args[0]
    rest = args[1:]
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "plan":
        return cmd_plan(rest)
    if cmd == "allowed":
        return cmd_allowed(rest)
    if cmd == "issue":
        return cmd_issue(rest)
    if cmd == "revoke":
        return cmd_revoke(rest)
    if cmd == "install":
        return cmd_install(rest)
    if cmd == "device-id":
        return cmd_device_id(rest)
    if cmd == "activate":
        return cmd_activate(rest)
    if cmd == "deactivate":
        return cmd_activate(rest, "deactivate")
    sys.stderr.write(f"unknown subcommand: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
