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


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stderr.write(
            "usage: aib-entitlement status|device-id|activate|deactivate|issue|revoke\n"
            "  device-id   this machine's id under the license (for offline binding)\n"
            "  activate    activate this machine online (license with activation_url)\n"
            "  deactivate  free this machine's slot (do it before switching computers)\n"
            "  status  print entitlement JSON (machine-readable)\n"
            "  issue   draft shell only (no real key)\n"
            "  revoke  draft shell only (no real key)\n"
        )
        return 0 if args and args[0] in ("-h", "--help", "help") else 2
    cmd = args[0]
    rest = args[1:]
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "issue":
        return cmd_issue(rest)
    if cmd == "revoke":
        return cmd_revoke(rest)
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
