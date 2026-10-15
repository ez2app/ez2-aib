#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""已定義的席位角色 —— 單一來源:四語字典裡有 aib.role.<角色> 名稱的那些(顯示得出名字的才算定義過)。

the maintainer 10-15:「預設新開新從原有的角色上選,不另開新角色」。10-14 開出 boards / bot 這種沒定義的角色,
右欄只能畫 👤、艦長/主管判定認不得 ⇒ 開席時就擋,並列出可用的角色。工作分類靠人格與專案,不靠角色名。

規則:
- 同家族可帶數字尾(dev2 = dev 家族;與 pane-right-ai.sh role_family 同一條)。
- EZ2_AIB_CAPTAIN_ROLES 裡自訂的艦長角色也算定義過(艦長判定本來就讀它)。
- AIB_ALLOW_CUSTOM_ROLE=1:明講才用的逃生門(真的要自訂角色的人);預設不開。

CLI:
  roles.py check <role>   定義過 ⇒ rc 0;沒有 ⇒ rc 2,stderr 一段用你的語言列出可用角色
  roles.py list           一行一個已定義角色
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
I18N_DIR = HERE.parents[1] / "i18n"
KEY_RE = re.compile(r"^aib\.role\.([a-z0-9-]+)$")


def defined_roles(i18n_dir=None):
    """四語字典(以 zh-TW 為準,其他語系同鍵)裡 aib.role.* 的角色名,照字典順序。"""
    try:
        d = json.loads((Path(i18n_dir or I18N_DIR) / "zh-TW.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [m.group(1) for k in d for m in [KEY_RE.match(k)] if m]


def role_family(role):
    """尾端 ASCII 數字剝掉(dev2 → dev);全是數字的怪字面原樣回。"""
    i = len(role)
    while i > 0 and role[i - 1].isascii() and role[i - 1].isdigit():
        i -= 1
    return role[:i] or role


def is_defined(role, env=None, i18n_dir=None):
    env = os.environ if env is None else env
    if env.get("AIB_ALLOW_CUSTOM_ROLE") == "1":
        return True
    if not isinstance(role, str) or not role:
        return False
    known = set(defined_roles(i18n_dir)) | set((env.get("EZ2_AIB_CAPTAIN_ROLES") or "").split())
    return role in known or role_family(role) in known


def _t(key, params=None):
    try:
        sys.path.insert(0, str(HERE))
        import i18n_lib
        return i18n_lib.t(key, params)
    except Exception:  # noqa: BLE001
        return key


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["list"] and len(a) == 1:
        print("\n".join(defined_roles()))
        return 0
    if a[:1] == ["check"] and len(a) == 2:
        if is_defined(a[1]):
            return 0
        names = ", ".join("%s(%s)" % (r, _t("aib.role.%s" % r)) for r in defined_roles())
        print(_t("aib.err.role_undefined", {"role": a[1], "roles": names}), file=sys.stderr)
        return 2
    print(__doc__.split("CLI:")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
