# SPDX-License-Identifier: Apache-2.0
"""AIB dataguard:packaged 資料檔的可移植嚴格載入(艦長核准之 dd3c2 方案)。

問題:git 只管 executable 位,umask 007 的 checkout 會讓已審核的 JSON 帶
group-write,現行 fail-closed 閘(正確地)拒讀 → 乾淨環境假紅四連踩。

方案(docs/reports/AIB-DATA-PERMISSION-PORTABILITY-20260810.md 推薦案):
  1. 來源一律 O_NOFOLLOW|O_NONBLOCK 開一次,之後全部驗 descriptor。
  2. world-writable 無條件拒收;非 packaged(無 pin)檔維持原規則不放寬。
  3. group-writable 的 packaged 檔:位元組 SHA256 必須等於 release 對釘
     (pins.py),驗過才把「已驗位元組」原子寫進 owner-only 快照(0600),
     並落 owner-only 收據;之後只從快照 descriptor 載入。
  4. 快照與收據都在 instance 狀態目錄(0700),路徑不可被他人插手。

security 立場:此模組沒有放寬任何拒絕——它只是給「位元組與 release 完全
相同、只差 checkout 權限位」的檔案一條可驗證的活路;任何一 byte 不同就
回到原本的 fail-closed。
"""

import hashlib
import json
import os
import stat
import time

from . import pins

_MAX_DATA_BYTES = 1 << 20  # 1 MiB;packaged 資料檔遠小於此


class DataGuardError(Exception):
    """dataguard 自身的 fail-closed;呼叫端通常轉包成自己的 error_type。"""


def _open_nofollow(path, error_type, label):
    if not hasattr(os, "O_NOFOLLOW"):
        raise error_type("%s loader requires O_NOFOLLOW support" % label)
    flags = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))
    try:
        return os.open(os.fspath(path), flags)
    except OSError as err:
        raise error_type("%s is unreadable: %s (%s)" % (label, path, err))


def _fstat_regular_owned(descriptor, path, error_type, label):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise error_type("%s must be a regular non-symlink file: %s"
                         % (label, path))
    if info.st_uid != os.getuid():
        raise error_type("%s owner does not match the current user: %s"
                         % (label, path))
    if info.st_nlink != 1:
        raise error_type("%s must have exactly one filesystem link: %s"
                         % (label, path))
    return info


def _read_all(descriptor, path, error_type, label):
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_DATA_BYTES:
            raise error_type("%s exceeds %d bytes: %s"
                             % (label, _MAX_DATA_BYTES, path))
        chunks.append(chunk)
    return b"".join(chunks)


def snapshot_dir():
    """owner-only 快照基地:自管 ~/.aib-dataguard,0700 直掛 home。

    刻意不用 EZ2_COCKPIT_STATE_DIR:那條鏈(~/.ez2-aib/…)在 umask 007
    機器上天生 0770,會被本模組自己的祖先巡檢正確拒收——快照是內容定址
    (同 pin 同位元組),全域一份即可,不需要跟 instance 狀態同居。
    EZ2_AIB_DATAGUARD_DIR 供測試與特殊部署覆寫。"""
    base = os.environ.get("EZ2_AIB_DATAGUARD_DIR") or os.path.expanduser(
        "~/.aib-dataguard")
    return os.path.join(base, "snapshots")


def _ensure_private_dir(directory, error_type, label):
    """快照目錄 fail-closed:既存不安全目錄拒絕(不代為 chmod),
    並沿目錄鏈逐層驗 owner/symlink/寬鬆位——可寫父目錄=可換整個快照區。"""
    directory = os.path.abspath(directory)
    existed = os.path.isdir(directory)
    if not existed:
        # makedirs 的 mode 只作用於最後一層,中間層吃 umask(007 環境
        # 會生出 0770 祖先、被下方巡檢正確拒收)——逐層自建+chmod 0700。
        parts = []
        probe = directory
        while probe and not os.path.isdir(probe):
            parts.append(probe)
            probe = os.path.dirname(probe)
        for level in reversed(parts):
            try:
                os.mkdir(level, 0o700)
            except FileExistsError:
                continue
            os.chmod(level, 0o700)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise error_type("%s snapshot dir must be a real directory: %s"
                         % (label, directory))
    if info.st_uid != os.getuid():
        raise error_type("%s snapshot dir owner mismatch: %s"
                         % (label, directory))
    if stat.S_IMODE(info.st_mode) & 0o077:
        if existed:
            raise error_type("%s pre-existing snapshot dir is not "
                             "owner-only: %s" % (label, directory))
        os.chmod(directory, 0o700)
    # 祖先鏈只審「本使用者擁有」的層:user-owned symlink 或寬鬆位=可換
    # 整個快照區,拒;走到非本人擁有的目錄(root 系統域,如 /var/folders、
    # /private/tmp)即停——系統 symlink(/var->/private/var)root 所有,
    # 不在本閘威脅模型內,全根巡檢反而在 macOS 上必假紅。
    parent = os.path.dirname(directory)
    home = os.path.expanduser("~")
    while parent and parent != "/":
        if parent == home:
            break  # home=信任邊界(OS 基線),巡檢止於其下
        pinfo = os.lstat(parent)
        if pinfo.st_uid != os.getuid():
            break
        # 10-04(雲端以 root 執行時實踩):/tmp 這類「所有人可寫 + 黏著位元」的公用目錄,root 跑時剛好「是本人擁有」,
        #   以前因此被當成寬鬆的祖先拒絕(快照區在 /tmp 底下時一律紅)。黏著位元下別人刪不掉、換不掉本人建的項目,
        #   而快照目錄本身另有「真目錄 + 擁有者=本人」的檢查 ⇒ 與「非本人擁有的系統域」同樣當信任邊界、巡檢到此為止。
        #   沒有黏著位元的寬鬆目錄照舊拒(下面那條)。
        if (not stat.S_ISLNK(pinfo.st_mode) and stat.S_ISDIR(pinfo.st_mode)
                and pinfo.st_mode & stat.S_ISVTX and stat.S_IMODE(pinfo.st_mode) & 0o002):
            break
        if stat.S_ISLNK(pinfo.st_mode):
            raise error_type("%s snapshot ancestor is a symlink: %s"
                             % (label, parent))
        if stat.S_IMODE(pinfo.st_mode) & 0o022:
            raise error_type("%s snapshot ancestor is group/world "
                             "writable: %s" % (label, parent))
        parent = os.path.dirname(parent)


def _atomic_owner_only_write(directory, name, data, error_type, label):
    """O_EXCL 臨時檔寫入後 os.replace;目的地永遠 0600 且不經 symlink。"""
    tmp_path = os.path.join(directory, ".%s.%d.tmp" % (name, os.getpid()))
    final_path = os.path.join(directory, name)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(tmp_path, flags, 0o600)
    except OSError as err:
        raise error_type("%s snapshot temp create failed: %s (%s)"
                         % (label, tmp_path, err))
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    except OSError as err:
        os.close(descriptor)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise error_type("%s snapshot write failed: %s (%s)"
                         % (label, tmp_path, err))
    else:
        os.close(descriptor)
    try:
        # 目的地若被換成 symlink,os.replace 會直接蓋掉連結本身而非目標,
        # 但保險起見仍先 lstat 拒絕既存 symlink(check 在 replace 前,
        # race 窗口由「replace 為原子且我們之後只讀回 fd 驗 hash」封閉)。
        if os.path.islink(final_path):
            raise error_type("%s snapshot destination is a symlink: %s"
                             % (label, final_path))
        os.replace(tmp_path, final_path)
    except OSError as err:
        raise error_type("%s snapshot publish failed: %s (%s)"
                         % (label, final_path, err))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return final_path


def load_verified_bytes(path, *, pin_key, error_type=DataGuardError,
                        label="packaged data", snapshot_base=None,
                        force_pin=False):
    """回傳 (bytes, receipt_dict_or_None)。

    嚴格模式(無 group/world write)直接回 descriptor 位元組,receipt=None。
    group-write 且 pin 相符 → 快照+收據;其餘一律 raise error_type。
    """
    descriptor = _open_nofollow(path, error_type, label)
    try:
        info = _fstat_regular_owned(descriptor, path, error_type, label)
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o002:
            raise error_type("%s must not be world writable: %s"
                             % (label, path))
        data = _read_all(descriptor, path, error_type, label)
    finally:
        os.close(descriptor)

    if not mode & 0o020 and not force_pin:
        return data, None  # 現行嚴格路徑,原樣通過
    # force_pin:救援入口已見過 group-writable,即使重開後 mode 變乾淨
    # 也必須驗 pin——封掉「拒收與救援之間換檔+chmod」的一致性缺口
    # (實際利用需 owner 權限,此為深度防禦非特權修補)。

    # group-writable:只有 release 對釘的 packaged 檔有活路
    pin = pins.PINS.get(pin_key)
    if pin is None:
        raise error_type("%s must not be group/world writable: %s"
                         % (label, path))
    digest = hashlib.sha256(data).hexdigest()
    if digest != pin:
        raise error_type(
            "%s is group writable and does not match the release pin: %s"
            % (label, path))

    directory = snapshot_base or snapshot_dir()
    _ensure_private_dir(directory, error_type, label)
    safe_name = pin_key.replace("/", "__")
    snap_path = _atomic_owner_only_write(directory, safe_name, data,
                                         error_type, label)

    # 只信快照 descriptor:回讀並重驗 hash,杜絕發佈後被換
    verify_fd = _open_nofollow(snap_path, error_type, label)
    try:
        _fstat_regular_owned(verify_fd, snap_path, error_type, label)
        snap_info = os.fstat(verify_fd)
        if stat.S_IMODE(snap_info.st_mode) & 0o077:
            raise error_type("%s snapshot must stay owner-only: %s"
                             % (label, snap_path))
        snapped = _read_all(verify_fd, snap_path, error_type, label)
    finally:
        os.close(verify_fd)
    if hashlib.sha256(snapped).hexdigest() != pin:
        raise error_type("%s snapshot verification failed: %s"
                         % (label, snap_path))

    receipt = {
        "source_path": os.path.abspath(os.fspath(path)),
        "source_mode": "%04o" % mode,
        "sha256": digest,
        "snapshot_path": snap_path,
        "snapshot_mode": "0600",
        "pinned_at_version": pins.PINNED_AT_VERSION,
        "normalized_at_epoch": int(time.time()),
    }
    receipt_bytes = json.dumps(receipt, ensure_ascii=False,
                               sort_keys=True).encode("utf-8")
    _atomic_owner_only_write(directory, safe_name + ".receipt.json",
                             receipt_bytes, error_type, label)
    return snapped, receipt
