"""vault 位置的唯一規則(從 route_register 搬出來,a9136 11-09)。

只用 os:右欄每回合都要問 vault(🧠 行查 labels.json),匯入整個 route_register 會連帶
載入 ledger / collector_common,冷啟動約 24 ms(Mac B 量)。route_register 從這裡匯入
同一組名字,全套件仍只有這一處規則。
"""

import os


def aib_default_vault(env=None):
    env = os.environ if env is None else env
    aib_home = env.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(aib_home, "vault")


VAULT_SETTING_NAME = "vault-dir"   # <aib 家>/vault-dir:一行絕對路徑,不靠環境變數的那一處設定
LEGACY_VAULT_REL = ("Desktop", "Obsidian", "Ez2")


def vault_setting_file(env=None):
    env = os.environ if env is None else env
    aib_home = env.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(aib_home, VAULT_SETTING_NAME)


def configured_vault(env=None):
    """有人明確指過的 vault;都沒有回 None(不猜)。

    順序:EZ2_VAULT_DIR > EZ2_VAULT_ROOT > <aib 家>/vault-dir(檔內路徑是資料夾才算)
    > ~/Desktop/Obsidian/Ez2(有才算)。
    ★ 11-06(dev@f9e99):舊預設的空殼改名後,沒繼承 EZ2_VAULT_DIR 的行程(launchd、別的 shell)
      會安靜落到 ~/.ez2-aib/vault;vault-dir 設定檔讓這些行程也找得到真庫。全套件只在這裡寫這條規則,
      bindme / create_and_bind_persona / brain_write_plan / cockpit-env.sh 都問這裡。"""
    env = os.environ if env is None else env
    for var in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
        if env.get(var):
            return os.path.expanduser(env[var])
    try:
        with open(vault_setting_file(env), encoding="utf-8") as fh:
            line = fh.readline(4096).strip()
    except OSError:
        line = ""
    if line:
        p = os.path.expanduser(line)
        if os.path.isabs(p) and os.path.isdir(p):
            return p
    legacy = os.path.join(os.path.expanduser("~"), *LEGACY_VAULT_REL)
    return legacy if os.path.isdir(legacy) else None


def default_vault(env=None):
    """vault 位置,全套件同一條規則:configured_vault() 有就用它,沒有才退 ~/.ez2-aib/vault。"""
    env = os.environ if env is None else env
    return configured_vault(env) or aib_default_vault(env)
