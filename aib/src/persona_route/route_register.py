# SPDX-License-Identifier: Apache-2.0
"""E5.4 經 gateway 寫入 persona route —— 本版只做**登記層**。

大綱原文:經 gateway 寫入 persona route(先登記 routeKey)。

## 三條紅線(the maintainer 令,寫死在程式裡不是寫在註解裡)

1. **vault 的角色 .md 絕不直寫。** 角色檔是整檔 hash,動一 byte 全艦隊掉驗證。
   `refuse_direct_write()` 對任何指向 vault 角色檔的路徑硬拒,而且**先於**
   其他檢查跑 —— 它不是一個選項,是一道牆。
2. **gateway 回 rc=0 不算成功。** 判準沿用 ez2-ai 的 receipt.js(唯讀考察
   `git -C ~/ez2-ai show origin/dev:desktop-shell/src/chat-core/receipt.js`):
   `vault-write` 的成功條件是 `modifiedVault === true` 且 blockers 為空。
   `gateway_write_ok()` 只看這兩件,**完全不看 exit code** —— 一個沒改到任何
   東西卻 exit 0 的呼叫,是「什麼都沒發生」不是「成功」。
3. **天花板未解鎖前一律 staging/登記層。** 本模組沒有任何真寫入路徑:
   `register()` 只把 routeKey 的登記記錄寫進 staging,`plan_write()` 只回一份
   「將來會送什麼」的計畫。正式寫入候 E5.5 owner adapter,在那之前
   連程式碼裡都不存在能改 vault 的那一行。

## routeKey 的語意(來自 ez2-ai 路由中心合約)

「UID 是筆記身份,routeKey 是寫入語意,資料夾只是目前顯示位置。」
執行期解析鏈是 routeKey → route-key-index → folderId → folder-id-index →
currentPath。所以登記的是 **routeKey**,不是資料夾路徑 —— 硬寫中文資料夾路徑
正是這份合約要消滅的東西。
"""

import hashlib
import json
import subprocess
import os
import re
import secrets
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ledger"))
sys.path.insert(0, os.path.join(_HERE, "..", "collectors"))
sys.path.insert(0, os.path.join(_HERE, "..", "cockpit"))   # ED.1 (b):狀態根走 seats_dir_lib 單源
sys.path.insert(0, os.path.join(_HERE, "..", "memory"))    # E5.4 審修:vault 落點守衛借 brain_write_plan.refuse_vault_output

from ledger import Ledger  # noqa: E402
from collector_common import clip  # noqa: E402

# routeKey:小寫英數與連字號,dot 分層,至少兩層(對齊 ez2-ai 既有樣式
# 如 product.expert.draft)
ROUTE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
ROUTE_KEY_MAX = 120           # 合約裡 routeKey 取 slice(0, 120)

# 角色檔紅線:vault 內任何被視為角色定義的 .md
_ROLE_MD_RE = re.compile(r"(^|/)(角色|roles?)(/|[^/]*\.md$)", re.IGNORECASE)

STATUS_REGISTERED = "registered_pending_owner_adapter"   # 舊值:天花板未翻時的登記狀態
STATUS_REGISTERED_BOUND = "registered_owner_bound"       # 新值:owner adapter 已接且來源可用


def _ceiling_state_module():
    """src/fleet_identity/ceiling_state —— 天花板狀態的單一判讀入口。

    延後匯入(與 decision_inbox / pyramid 同慣例):匯不到就讓它炸,
    **天花板讀不到不得退成「當作已翻」**(那個方向會讓登記檔宣稱自己已授權)。
    """
    path = os.path.join(_HERE, "..", "fleet_identity")
    if path not in sys.path:
        sys.path.insert(0, path)
    return __import__("ceiling_state")


def current_status():
    """**當下**的登記狀態值,向 ceiling_state 現查導出。

    ★ PKG-CEILING-FLIP 丙件(判準 qa3/criteria-ceiling-flip-0827 更正六)。
    翻的是「從寫死變成可測」,不是「從否變成是」:owner source 未設定時
    回的就是 `STATUS_REGISTERED`,與舊值逐字相同。

    ★★ **C9 相容性決定(選的,不是預設的)**:**不收舊值。**
    兩道閘(`_load_all` 篩選、`plan_write` 前置)一律以 `current_status()` 比對,
    所以天花板翻轉之後,帶舊值的登記檔會被判**不是登記檔**而遭丟棄/拒絕規劃寫入。
    **代價逐字寫在這裡**:丙沒有 schema `enum` 那條路(值是**產品常數**,不是 schema),
    「收舊值」只能在產品碼裡做,**那會讓兩個值同時合法** —— 而登記檔的用途正是
    「這條路在**當下的天花板規制下**被登記過」,兩個值同時合法會讓那句話失去意義。
    ⇒ 選擇拒收。**這不是因為現在磁碟上零筆(那只是讓這個決定現在最便宜),
    是因為兩個都合法會讓登記狀態不再回答它要回答的問題。**

    ★ **C10 遷移面**:既有舊值登記檔的處置=**明寫作廢,不遷移**。
    理由同上:遷移等於把「舊規制下的登記」改寫成「新規制下的登記」,
    而那正是登記檔不該被改寫的那件事(FREEZE 同源)。作廢後重新登記。
    """
    fields = _ceiling_state_module().ceiling_fields()
    return STATUS_REGISTERED_BOUND if fields["formal_eligible"] else STATUS_REGISTERED


def CEILING_SITE(env=None, source=None, adapter=None):
    """判準 H2 的掛鉤:本處當下宣告的天花板狀態。有這個屬性代表本處已翻。"""
    return _ceiling_state_module().ceiling_fields(
        env=env, source=source, adapter=adapter)


class RouteError(ValueError):
    """登記或判定不合規 —— fail-closed。"""


class RedLineError(RouteError):
    """踩到紅線。與一般錯誤分開,因為它不該被當成「再試一次就好」。"""


def refuse_direct_write(target_path):
    """紅線一:任何指向 vault 角色檔的直寫,硬拒。

    先於其他檢查呼叫;回 None 表示這條路徑不是角色檔(仍不代表可以寫,
    本模組沒有任何真寫入路徑)。
    """
    p = str(target_path or "")
    if _ROLE_MD_RE.search(p):
        raise RedLineError(
            "紅線:角色檔絕不直寫(%s);合法路徑只有 writeback-gateway" % p)
    return None


def validate_route_key(route_key):
    key = str(route_key or "")
    if not key or len(key) > ROUTE_KEY_MAX:
        raise RouteError("routeKey 長度不合(1-%d):%r" % (ROUTE_KEY_MAX, route_key))
    if not ROUTE_KEY_RE.fullmatch(key):   # E5.4 codex 低-1:`$` 會放過尾端換行,fullmatch 不會
        raise RouteError(
            "routeKey 形狀不合(小寫英數與連字號,dot 分層至少兩層):%r" % route_key)
    return key


def gateway_write_ok(response):
    """紅線二:判斷 gateway 寫入是否**真的**成功。

    判準沿用 ez2-ai receipt.js 的 vault-write:modifiedVault === true 且
    blockers 為空。刻意不看 exit code / rc / ok 這些欄位 —— 它們只證明
    程式跑完了,不證明 vault 被改到。

    回 (成功?, 理由碼)。
    """
    if not isinstance(response, dict):
        return False, "no_response"
    if response.get("modifiedVault") is not True:
        return False, "vault_not_modified"
    blockers = response.get("blockers")
    # E5.4 codex 高-3:blockers 缺席=空(契約 acceptance 逐字要求 {"modifiedVault": true} 算成功);
    # 但「在場而不是陣列」(false / {} / "")不是空,是形狀壞掉——fail-closed。
    # rc / ok / exit0 仍刻意不看(紅線二,README §2):它們只證明程式跑完。
    if blockers is not None and not isinstance(blockers, list):
        return False, "blockers_malformed"
    if blockers:
        return False, "blockers_present"
    return True, "vault_modified"


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SPEC_PREFIX = "git:"


def _parse_git_spec(spec):
    """`git:<repo>#<rev>:<relpath>` → (repo, rev, relpath);不是這個形狀回 None。
    repo 可帶 ~;rev 是分支/tag/sha;relpath 是 repo 內相對路徑。"""
    if not isinstance(spec, str) or not spec.startswith(GIT_SPEC_PREFIX):
        return None
    body = spec[len(GIT_SPEC_PREFIX):]
    if "#" not in body:
        return None
    repo, _, rest = body.partition("#")
    if ":" not in rest:
        return None
    rev, _, rel = rest.partition(":")
    if not repo or not rev or not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    return os.path.expanduser(repo), rev, rel


def read_contract_bytes(spec=None):
    """契約原始位元組(唯讀)。來源兩形:
    - 檔案路徑(舊形,仍收)
    - `git:<repo>#<rev>:<relpath>`:`git -C <repo> show <rev>:<relpath>`,**不走工作樹檔案系統**
      (艦外 08-17 23:42 裁:全機 69 份同名檔四種版本,D lane 一律讀 git main 正本)。
    讀不到回 None。"""
    p = spec or default_contract_spec()   # 10-15:沒給就走唯一來源(以前這裡只讀 EZ2_ROUTE_REGISTRY,與 default_contract_spec 分岔)
    if not p:
        return None
    git = _parse_git_spec(p)
    if git is not None:
        repo, rev, rel = git
        try:
            proc = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, rel)],
                                  capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def contract_digest(spec=None):
    """來源內容 sha256(provenance 用);讀不到回 None。"""
    raw = read_contract_bytes(spec)
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


DEFAULT_CONTRACT_REL = "samples/obsidian/obsidian-route-registry-contract.json"


def default_contract_spec(env=None, repo_has_main=None):
    """路由契約來源,逐字照 cockpit-env.sh(`EZ2_ROUTE_CONTRACT_SPEC` 那段):
    顯式 EZ2_ROUTE_CONTRACT_SPEC > ez2-ai repo(EZ2_EZ2AI_REPO,預設 ~/ez2-ai)有 main 就讀 git main 正本
    > 退回 EZ2_ROUTE_REGISTRY(舊形檔案路徑)。08-17 23:42 艦外裁「一律讀 git main 正本」。
    ★ a seat 審查 09-26 重大 2:第一版把中間兩個寫反,沒在艙裡跑的行程會讀到舊的工作樹檔。
    ★ 10-15:全套件「沒給 --contract」一律走這一支(含 read_contract_bytes / load_contract 的預設);
      順位第一的與 EZ2_ROUTE_REGISTRY 內容不同時交給 reconcile_contract_spec 判(見該函式)。"""
    env = os.environ if env is None else env
    if env.get("EZ2_ROUTE_CONTRACT_SPEC"):
        return reconcile_contract_spec(env["EZ2_ROUTE_CONTRACT_SPEC"], env)
    repo = env.get("EZ2_EZ2AI_REPO") or "~/ez2-ai"
    if repo_has_main is not None:
        has_main = repo_has_main(repo)
    else:                                   # 與 cockpit-env.sh 同一個判準:git rev-parse --verify -q main
        try:
            has_main = subprocess.run(["git", "-C", os.path.expanduser(repo), "rev-parse", "--verify", "-q", "main"],
                                      capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            has_main = False
    if has_main:
        return reconcile_contract_spec("git:%s#main:%s" % (repo, DEFAULT_CONTRACT_REL), env)
    return env.get("EZ2_ROUTE_REGISTRY") or fallback_contract(env)


def contract_route_keys(contract):
    """契約裡的 routeKey 集合;讀不到 / 形狀不對回 None(不當成空集合)。"""
    routes = contract.get("routes") if isinstance(contract, dict) else None
    if not isinstance(routes, list):
        return None
    return {r["routeKey"] for r in routes if isinstance(r, dict) and isinstance(r.get("routeKey"), str)}


_CONTRACT_WARNED = set()


def reconcile_contract_spec(primary, env=None, warn=True):
    """順位第一的來源(primary)與 EZ2_ROUTE_REGISTRY 各指一份、內容不同時,決定用哪份並**出聲**。

    10-15 艙內實量:REGISTRY 是七月舊檔(32 條),SPEC 指 git main(40 條,多 core.site_ops / core.meeting / storage.*);
    別席重綁時整艙重投影讀的是舊檔 ⇒ 大腦在 core.site_ops 的席被從 bindings 安靜丟掉,狀態變「缺人格」。
    規則(刻意的選擇,改這裡要同步 README / 測試):
      兩份 routeKey 集合相同          ⇒ 用 primary,不出聲
      REGISTRY 是 primary 的嚴格超集  ⇒ 改用 REGISTRY(不用缺路由的那份)
      primary 讀不到、REGISTRY 讀得到 ⇒ 改用 REGISTRY
      其他(primary 是超集 / 各缺各的)⇒ 照順位用 primary
    只要兩份不同就 stderr 一行(同一對來源每個行程只講一次),點名兩邊與條數差。"""
    env = os.environ if env is None else env
    reg = env.get("EZ2_ROUTE_REGISTRY")
    if not reg or not primary or reg == primary:
        return primary
    keys_p = contract_route_keys(load_contract(primary, with_vault_routes=False))
    keys_r = contract_route_keys(load_contract(reg, with_vault_routes=False))
    if keys_r is None or keys_p == keys_r:
        return primary
    if keys_p is None:
        pick, key, params = reg, "aib.contract.primary_unreadable", {"a": primary, "b": reg}
    else:
        pick = reg if keys_r > keys_p else primary
        key = "aib.contract.sources_differ"
        params = {"a": primary, "na": len(keys_p), "b": reg, "nb": len(keys_r),
                  "a_only": len(keys_p - keys_r), "b_only": len(keys_r - keys_p), "pick": pick}
    if warn and (primary, reg) not in _CONTRACT_WARNED:
        _CONTRACT_WARNED.add((primary, reg))
        sys.stderr.write("route_register: %s\n" % _ui(key, **params))
    return pick


_UI_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵(與 bind_one / binding_projection 同款);讀不到印鍵名,不讓程式停。"""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _UI_TR.get(loc)
        if tr is None:
            tr = _UI_TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001 —— 印不出字不能讓程式停
        return key


# 10-12 新用戶全面檢查(the maintainer「讓用戶在用時不會有這個問題」):新機器沒有 ez2-ai、也沒設契約 ⇒ aib newrole / 第二大腦
#   全卡死。套件附一份預設契約,vault 是 aib 自己的預設(~/.ez2-aib/vault)時用;自訂 vault 只在 bundled_ok_for_vault
#   判得出安全時用(空的 / 有自己的索引 / 已有 10-Roles),其他照舊不猜,不會把資料夾開進別人整理好的庫。
BUNDLED_CONTRACT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default-route-contract.json")


def aib_default_vault(env=None):
    env = os.environ if env is None else env
    aib_home = env.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(aib_home, "vault")


def default_vault(env=None):
    """vault 位置,全套件同一條規則:EZ2_VAULT_DIR > EZ2_VAULT_ROOT > ~/Desktop/Obsidian/Ez2(有才算)> ~/.ez2-aib/vault。"""
    env = os.environ if env is None else env
    for var in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"):
        if env.get(var):
            return os.path.expanduser(env[var])
    legacy = os.path.join(os.path.expanduser("~"), "Desktop", "Obsidian", "Ez2")
    return legacy if os.path.isdir(legacy) else aib_default_vault(env)


# 10-12 the maintainer「一併修正」:團隊成員的 Mac(有 Ez2 的知識庫、沒有 ez2-ai)⇒ 私有倉隨附一份 ez2-ai 正本的副本
#   (src/persona_route/vendor/obsidian-route-registry-contract.json,只在私有倉;公開版不帶 —— export_aib EXCLUDE)。
#   順序:顯式 > ez2-ai git main > EZ2_ROUTE_REGISTRY > 私有倉隨附正本 > 套件預設(只在 aib 預設 vault)。
VENDORED_CONTRACT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "obsidian-route-registry-contract.json")


def fallback_contract(env=None):
    if os.path.isfile(VENDORED_CONTRACT):
        return VENDORED_CONTRACT
    return bundled_contract_if_default_vault(env)


BUNDLED_LIBRARY = "10-Roles"     # 套件預設契約的角色庫資料夾(default-route-contract.json 的 product.expert.agent_library)


def bundled_ok_for_vault(vault):
    """自訂 vault 也能用套件預設契約的三種情況(10-12 a seat:Pro 的 vault 是 ~/Ez2/Vault,公開版 newrole 一律 rc=2):
    (一)還沒有或是空的(只有 . 開頭的檔,例如 .obsidian)= 新的,aib 來建;
    (二)uid 版面、自己有 route-key-index.json = 資料夾以 vault 自己的索引為準,契約只提供 routeKey 清單;
    (三)已經有套件的角色庫資料夾(10-Roles)= 以前就是 aib 建的。
    其他(有東西、又看不出版面)⇒ 不猜,免得在別人整理好的庫裡另開一套資料夾。"""
    try:
        names = [n for n in os.listdir(vault) if not n.startswith(".")]
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not names:
        return True
    if vault_is_uid_layout(vault) and os.path.isfile(os.path.join(vault, ".ez2", "index", "route-key-index.json")):
        return True
    return os.path.isdir(os.path.join(vault, BUNDLED_LIBRARY))


def bundled_contract_if_default_vault(env=None):
    env = os.environ if env is None else env
    if not os.path.isfile(BUNDLED_CONTRACT):
        return None
    vault = default_vault(env)
    if os.path.abspath(vault) == os.path.abspath(aib_default_vault(env)) or bundled_ok_for_vault(vault):
        return BUNDLED_CONTRACT
    return None


def resolve_route_folder(route_key, spec=None, contract=None, use_index=True, root=None):
    """routeKey → Vault 相對資料夾,每次從路由契約現查(the maintainer 08-27 第二鐵律:程式不得寫死 folder_path)。
    契約讀不到、沒有這條、或資料夾形狀不安全 ⇒ 回 None,由呼叫端決定怎麼辦,這裡不猜。
    資料夾改 uid(the maintainer 09-26 R9)之後,改的是契約裡的 folder,呼叫端一行都不用動。
    root = 呼叫端實際要寫的 vault(10-15:不傳就只看環境變數,一般終端機沒設 EZ2_VAULT_DIR 時索引查不到、
    退回契約字面,寫入端 mkdir 就把轉換前的舊名資料夾長回來)。uid 版面而索引沒有這條 ⇒ None,不退回字面。"""
    indexed = vault_index_folder(route_key, root) if use_index else None
    if indexed is not None:                 # uid 版面(the maintainer 09-26 R9 / 10-05 轉換):vault 自己的索引才是真路徑
        return indexed                      # use_index=False = 只要契約字面(給人看的顯示用,不是落點)
    if use_index and vault_is_uid_layout(root):
        return None                         # 已轉 uid 版面:契約字面那個夾已經不存在,退回去就會被建回來
    if contract is None:
        spec = spec or default_contract_spec()
        if not spec:                        # 來源解不出就停在這層,不讓 load_contract 再讀一次 os.environ(a seat 建議 8)
            return None
        contract = load_contract(spec)
    for route in (contract or {}).get("routes") or []:
        if isinstance(route, dict) and route.get("routeKey") == route_key:
            folder = route.get("folder")
            return folder if folder and not _unsafe_folder(folder) else None
    return None


def route_label(route_key, root=None, contract=None, spec=None):
    """routeKey → 給人看的資料夾名稱(不是路徑;路徑一律 resolve_route_folder / aib memory write)。
    the maintainer 10-15「用 uid 建立,顯示才用 title」:uid 版面 ⇒ <vault>/.ez2/display/labels.json 的
    folders[<folderId>].title(folderId 從 route-key-index 查);舊版面 ⇒ 契約字面的最後一段(跟以前畫面一樣)。
    都查不到 ⇒ routeKey 本身。"""
    if not route_key:
        return ""
    root = root or vault_root_guess() or default_vault()
    if vault_is_uid_layout(root):
        try:
            with open(os.path.join(root, ".ez2", "index", "route-key-index.json"), encoding="utf-8") as fh:
                items = json.load(fh).get("items") or []
            fid = next((it.get("folderId") for it in items
                        if isinstance(it, dict) and it.get("routeKey") == route_key), None)
            with open(os.path.join(root, ".ez2", "display", "labels.json"), encoding="utf-8") as fh:
                title = ((json.load(fh).get("folders") or {}).get(fid) or {}).get("title") if fid else None
            if isinstance(title, str) and title.strip():
                return title.strip()
        except (OSError, ValueError, AttributeError):
            pass
        return route_key
    literal = resolve_route_folder(route_key, spec=spec, contract=contract, use_index=False)
    return literal.rstrip("/").split("/")[-1] if literal else route_key


def vault_root_guess():
    """沿用 default_vault 的共用順位,讓沒有繼承環境變數的工具殼也讀同一份索引。

    明確的環境設定仍優先;不掃描其他知識庫。uid 版面與索引存在性由呼叫端檢查。
    """
    return default_vault()


def vault_is_uid_layout(root=None):
    """<vault>/.ez2/migration-state.json 的 mode == uid_layout(uid_layout_convert.py apply 之後才有)。"""
    import os
    root = root or vault_root_guess()
    if not root:
        return False
    try:
        with open(os.path.join(root, ".ez2", "migration-state.json"), encoding="utf-8") as fh:
            return json.load(fh).get("mode") == "uid_layout"
    except (OSError, ValueError):
        return False


def vault_index_folder(route_key, root=None):
    """routeKey → vault 內真路徵(<vault>/.ez2/index/route-key-index.json 的 folderPath)。
    只在 uid 版面下生效;索引沒有這條、或路徑不安全 ⇒ None(退回契約)。"""
    import os
    root = root or vault_root_guess()
    if not root or not vault_is_uid_layout(root):
        return None
    try:
        with open(os.path.join(root, ".ez2", "index", "route-key-index.json"), encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except (OSError, ValueError):
        return None
    for it in items:
        if isinstance(it, dict) and it.get("routeKey") == route_key:
            folder = it.get("folderPath")
            # 10-06 a seat:這一行刻意不與契約路(上面)同形 —— test-route-folder-resolver 的刀以那一行為錨,要求全檔恰好一處
            if not folder or _unsafe_folder(folder):
                return None
            return folder
    return None


def load_contract(path=None, with_vault_routes=True):
    """讀 ez2-ai 的路由中心契約(唯讀)。讀不到回 None,由呼叫端決定怎麼辦。
    來源形狀見 read_contract_bytes(檔案路徑或 git: 規格)。
    with_vault_routes:併入 Vault 自掛的路由(vault_routes;10-16 folder-add --route-key)。
    全套件讀契約都走這一支,所以 newproj / 綁定 / 投影 / aib memory write 看到的是同一份。"""
    raw = read_contract_bytes(path)
    if raw is None:
        return None
    try:
        contract = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if with_vault_routes:
        extra = vault_routes(contract)
        if extra:
            contract["routes"] = list(contract["routes"]) + extra
    return contract


def vault_routes(contract, root=None):
    """uid 版面 Vault 自掛的路由(route-key-index 裡帶 inheritsFrom 的條目)⇒ 契約形狀的條目清單。
    權限一律照抄 inheritsFrom 那條契約路由(dataLevel / aiReadable / aiWritable),不另給、不放寬;
    inheritsFrom 不在契約、代號跟契約撞、形狀不合、路徑不安全、同代號第二筆 ⇒ 略過(契約永遠優先)。
    沒設 EZ2_VAULT_DIR / 不是 uid 版面 / 索引讀不到 ⇒ []。"""
    routes = contract.get("routes") if isinstance(contract, dict) else None
    root = root or vault_root_guess()
    if not isinstance(routes, list) or not root or not vault_is_uid_layout(root):
        return []
    try:
        with open(os.path.join(root, ".ez2", "index", "route-key-index.json"), encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
        with open(os.path.join(root, ".ez2", "display", "labels.json"), encoding="utf-8") as fh:
            labels = json.load(fh).get("folders") or {}
    except (OSError, ValueError, AttributeError):
        return []
    base = {r.get("routeKey"): r for r in routes if isinstance(r, dict)}
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        key, parent, folder = str(it.get("routeKey") or ""), it.get("inheritsFrom"), it.get("folderPath")
        if (not parent or parent not in base or key in base or key in seen or not ROUTE_KEY_RE.fullmatch(key)
                or len(key) > ROUTE_KEY_MAX or _unsafe_folder(folder)):
            continue
        seen.add(key)
        p = base[parent]
        title = ((labels.get(it.get("folderId")) or {}) if isinstance(labels, dict) else {}).get("title")
        entry = {"routeKey": key, "module": p.get("module"), "folder": folder, "dataLevel": p.get("dataLevel"),
                 "aiReadable": p.get("aiReadable"), "aiWritable": p.get("aiWritable"),
                 "inheritsFrom": parent, "source": "vault"}
        if isinstance(title, str) and title.strip():
            entry["title"] = title.strip()
        out.append(entry)
    return out


# write_rules 第 7 條:routeKey 只能指向 vault 相對路徑,
# 不可使用絕對路徑、..、家目錄或外部磁碟路徑
def _unsafe_folder(folder):
    f = str(folder or "")
    if not f:
        return "empty_folder"
    if f.startswith("/") or f.startswith("~"):
        return "absolute_or_home_path"
    if "\\" in f or ":" in f.split("/")[0]:
        return "non_posix_path"           # E5.4 codex 中-6:反斜線 / 磁碟機代號一律不是 vault 相對路徑
    if ".." in f.split("/"):
        return "parent_traversal"
    return None


# dataLevel 封閉字彙:ez2-ai 契約繁中四級 + note-format 英文四級(brain_write_plan.DATA_LEVEL_TO_LEVEL /
# PRIVACY_ORDER 同源;契約 acceptance 第 6 條治具用 "internal")。表外值一律擋(E5.4 codex 高-4)。
KNOWN_DATA_LEVELS = ("一般", "內部", "敏感", "機密", "public", "internal", "restricted", "sensitive")
BLOCKED_DATA_LEVELS = ("敏感", "機密", "sensitive", "restricted")   # owner 決策,登記層不放行


def check_against_contract(route_key, contract):
    """對照契約檢查一個 routeKey。回 (ok?, code, detail)。

    契約不可用時回 (False, "contract_unavailable", ...) —— fail-closed:
    看不到規則就不准登記,而不是「沒人反對就通過」。

    敏感/機密資料層級一律擋:那是 owner 決策,不是登記層能放行的
    (與 E2.6 的『AI 不得核可身份升權』同一條精神)。
    """
    if not isinstance(contract, dict) or not contract.get("routes"):
        return False, "contract_unavailable", "讀不到路由契約,不予登記"
    raw_routes = contract["routes"]
    if not isinstance(raw_routes, list):
        return False, "contract_malformed", "routes 不是陣列,不予登記"
    # E5.4 codex 高-4:非物件條目 / 重複 routeKey 都是形狀壞掉,fail-closed——重複時「最後一筆蓋前面」
    # 會讓限制較嚴的那筆被繞過,所以不挑、整份拒。
    routes = {}
    for r in raw_routes:
        if not isinstance(r, dict) or not isinstance(r.get("routeKey"), str) or not r.get("routeKey"):
            return False, "contract_malformed", "routes 內有非物件或缺 routeKey 的條目,不予登記"
        if r["routeKey"] in routes:
            return False, "contract_malformed", "routeKey 重複:%s(不挑哪一筆,整份拒)" % r["routeKey"]
        routes[r["routeKey"]] = r
    route = routes.get(route_key)
    if route is None:
        return (False, "route_not_in_contract",
                "契約裡沒有這個 routeKey;要先在路由中心新增,不能由本層自行造路")
    if route.get("aiWritable") is not True:      # 嚴格布林:"false" / 1 / "yes" 都不是 True
        return False, "route_not_ai_writable", "契約標記此路不可由 AI 寫入(aiWritable 必須是布林 true)"
    unsafe = _unsafe_folder(route.get("folder"))
    if unsafe:
        return False, "unsafe_folder_path", "違反 write_rules 第 7 條:%s" % unsafe
    level = route.get("dataLevel")
    if level not in KNOWN_DATA_LEVELS:
        return (False, "unknown_data_level",
                "dataLevel=%r 不在繁中四級之內,看不懂的層級不放行" % (level,))
    if level in BLOCKED_DATA_LEVELS:
        return (False, "sensitive_data_level",
                "資料層級 %s 需 owner 決策,登記層不放行" % level)
    return True, "contract_ok", "契約允許 AI 寫入(%s)" % level


def _state_root():
    """ED.1 (b):狀態根單源 `seats_dir_lib.state_root()`,不另抄 env 鏈(延後匯入,見 bind_queue 同名函式)。"""
    from seats_dir_lib import state_root
    return state_root()[0]


def staging_dir(base=None):
    """單路徑 env 仍優先(相容);沒設才落在狀態根下(ED.1 (b))。"""
    return base or os.environ.get("EZ2_PERSONA_ROUTE_STAGING") or \
        os.path.join(_state_root(), "persona-route-staging")


def default_ledger_path():
    return os.environ.get("EZ2_AIB_LEDGER") or \
        os.path.join(_state_root(), "ledger", "aib-ledger.jsonl")


def _record_path(base, route_key):
    return os.path.join(staging_dir(base), "%s.json" % route_key)


def load_registration(route_key, base=None):
    try:
        with open(_record_path(base, route_key), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def list_registrations(base=None, errors=None):
    """登記清單。`errors`(list)給了就把讀不動/形狀壞的檔具名放進去(E5.4 codex 中-4:
    「無資料」與「無法判斷」不得混成同一個空清單);沒給維持舊行為(跳過)。"""
    d = staging_dir(base)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError) as err:
            if errors is not None:
                errors.append({"file": name, "reason": "unreadable_or_not_json", "detail": str(err)[:120]})
            continue
        if not isinstance(rec, dict) or rec.get("status") != current_status() or not rec.get("route_key"):
            if errors is not None:
                errors.append({"file": name, "reason": "not_a_registration_record"})
            continue
        out.append(rec)
    return out


_SIG_RE = re.compile(r"^[A-Za-z0-9:@._/-]{1,128}$")   # requested_by:可印、無空白/控制字元(E5.4 codex 中-7)


def _refuse_vault(path, what):
    """登記層的寫入點(帳本、staging)都不得落在 vault 之下——紅線一的對象不只 target_hint
    (E5.4 codex 高-1)。借 brain_write_plan.refuse_vault_output(realpath 解完比對,連 symlink 一起擋),
    不另抄 vault 根的解析;延後匯入是因為 brain_write_plan 在模組層匯入本檔。"""
    from brain_write_plan import refuse_vault_output, WritePlanError
    try:
        refuse_vault_output(path)
    except WritePlanError as err:
        raise RedLineError("%s 不得落在 vault 之下:%s" % (what, err))


def register(route_key, purpose, requested_by, ledger_path, base=None,
             target_hint=None, now_ms=None, contract=None,
             enforce_contract=True):
    """登記一個 persona 寫入用的 routeKey(只進 staging,不碰 vault)。

    target_hint 若指向角色檔,直接踩紅線拒絕 —— 登記階段就擋掉,
    不要等到有人真的去寫才發現。

    enforce_contract=True(預設)時,routeKey 必須在 ez2-ai 路由中心契約裡
    存在、標記 aiWritable、資料層級非敏感/機密、且資料夾路徑合乎 write_rules。
    契約讀不到就不登記(fail-closed)—— 看不到規則不等於沒有規則。
    """
    if target_hint:
        refuse_direct_write(target_hint)
    _refuse_vault(ledger_path, "--ledger")
    _refuse_vault(staging_dir(base), "--staging")
    key = validate_route_key(route_key)
    if not purpose or not str(purpose).strip():
        raise RouteError("必須說明用途(purpose)")
    if not isinstance(requested_by, str) or not _SIG_RE.fullmatch(requested_by):
        raise RouteError("requested_by 形狀不合(可印字元、無空白,≤128):%r" % (requested_by,))
    if enforce_contract:
        ok, code, detail = check_against_contract(
            key, contract if contract is not None else load_contract())
        if not ok:
            raise RouteError("契約檢查未過(%s):%s" % (code, detail))
        contract_check = code
    else:
        # E5.4 codex 高-5:--no-contract 是測試/治具用的旁路,不是正式路徑;留痕而不是默默放行
        contract_check = "skipped(--no-contract)"
        sys.stderr.write("route_register: ★ 契約檢查被關閉(--no-contract),本登記不代表契約允許 %s\n" % key)

    existing = load_registration(key, base)
    if existing:
        return existing, False

    led = Ledger(ledger_path, node_id="persona-route")
    event = led.append({
        "kind": "system.note",
        "actor": {"ai_id": requested_by, "sig": "route@00000/System",
                  "kind": "system", "engine": "system"},
        "body": {
            "summary": {
                "zh-TW": clip("登記 persona routeKey %s(%s);正式寫入候 E5.5 owner adapter"
                              % (key, purpose)),
                "en": clip("registered persona routeKey %s (%s); real writes pending E5.5"
                           % (key, purpose)),
            },
            "subject": {"kind": "other", "id": key},
        },
        "causation": {"causation_id": None, "correlation_id": "E5.4"},
    }, wall_ms=now_ms)

    record = {
        "schema_version": "0.1.0-draft",
        "route_key": key,
        "purpose": str(purpose),
        "requested_by": str(requested_by),
        "status": current_status(),
        "target_hint": target_hint,
        "registered_event_id": event["event_id"],
        "registered_at_ms": event["hlc"]["wall_ms"],
        "contract_check": contract_check,
        "_ceiling": "正式寫入未解鎖:本登記不授權任何 vault 寫入(候 E5.5 owner adapter)",
    }
    path = _record_path(base, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # E5.4 codex 中-2 / 高-1:暫存檔名帶 pid+亂數(不可預測、不撞),O_EXCL|O_NOFOLLOW 建檔——
    # 既有同名檔或 symlink 都會讓 open 失敗,而不是跟著 symlink 寫到別處。
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), secrets.token_hex(4))
    tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with open(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return record, True


def plan_write(route_key, payload_sha256, base=None):
    """紅線三:只回「將來會送什麼」,不送。

    回一份計畫 dict,裡面沒有任何可執行的東西;它的存在是為了讓人能在
    owner adapter 解鎖前就審視這條寫入長什麼樣。
    """
    key = validate_route_key(route_key)
    if not isinstance(payload_sha256, str) or not _SHA256_RE.fullmatch(payload_sha256):
        raise RouteError("payload_sha256 必須是 64 位小寫十六進位(E5.4 codex 中-5):%r" % (payload_sha256,))
    reg = load_registration(key, base)
    if reg is None:
        raise RouteError("routeKey 未登記,不得規劃寫入:%s" % key)
    if not isinstance(reg, dict) or reg.get("status") != current_status() or reg.get("route_key") != key:
        raise RouteError("登記檔形狀不合或 route_key 不符,不得規劃寫入:%s" % key)
    return {
        "route_key": key,
        "payload_sha256": payload_sha256,
        "would_call": "writeback-gateway (E5.5 owner adapter 解鎖後)",
        "executes_now": False,
        "success_criteria": "modifiedVault === true 且 blockers 為空(rc 不算)",
        "registration_status": reg["status"],
    }


def _main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="route_register.py")
    p.add_argument("action", choices=["register", "list", "plan", "check-response", "check-contract"])
    p.add_argument("--route-key"); p.add_argument("--purpose")
    p.add_argument("--requested-by", default="system:aib")
    p.add_argument("--target-hint"); p.add_argument("--payload-sha256", default="")
    p.add_argument("--response-json"); p.add_argument("--staging")
    p.add_argument("--ledger"); p.add_argument("--now-ms", type=int)
    p.add_argument("--contract"); p.add_argument("--no-contract", action="store_true")
    a = p.parse_args(argv)

    ledger = a.ledger or default_ledger_path()   # ED.1 (b)
    try:
        if a.action == "register":
            rec, created = register(a.route_key, a.purpose, a.requested_by,
                                    ledger, a.staging, a.target_hint, a.now_ms,
                                    contract=load_contract(a.contract),
                                    enforce_contract=not a.no_contract)
            rec["_created"] = created
            print(json.dumps(rec, ensure_ascii=False))
            return 0
        if a.action == "list":
            errors = []
            rows = list_registrations(a.staging, errors=errors)
            print(json.dumps(rows, ensure_ascii=False))
            for e in errors:
                sys.stderr.write("route_register: 無法判斷的登記檔 %s(%s)\n" % (e["file"], e["reason"]))
            return 1 if errors else 0
        if a.action == "plan":
            print(json.dumps(plan_write(a.route_key, a.payload_sha256, a.staging),
                             ensure_ascii=False))
            return 0
        if a.action == "check-contract":
            ok, code, detail = check_against_contract(
                a.route_key, load_contract(a.contract))
            print(json.dumps({"ok": ok, "code": code, "detail": detail},
                             ensure_ascii=False))
            return 0 if ok else 1
        try:
            resp = json.loads(a.response_json or "")
        except ValueError:
            resp = None
        ok, code = gateway_write_ok(resp)
        print(json.dumps({"success": ok, "code": code}, ensure_ascii=False))
        return 0 if ok else 1
    except RedLineError as err:
        print("[紅線] %s" % err, file=sys.stderr)
        return 3
    except RouteError as err:
        print(str(err), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
