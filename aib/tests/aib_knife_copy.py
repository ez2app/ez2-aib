# SPDX-License-Identifier: Apache-2.0
"""刀落在**複本**上,不改寫真樹(10-04 雲端實踩後的共用工具)。

為什麼:直接改寫 ROOT 下的產品檔再 finally 還原,行程被強制中斷(容器重開、kill -9)時 finally 不會跑,
真樹就一直壞著,之後每支用到它的測試都誤紅;並行跑時別支也可能剛好讀到被改壞的那一刻。

    with knife_copy(ROOT, "src/messaging/dm_paste.py", needle, replacement) as copy_root:
        subprocess.run([sys.executable, str(copy_root / "tests" / THIS_NAME), ...], cwd=str(copy_root))

複本內容:整棵樹(不含 .git、__pycache__);tests/ 只帶輔助檔(非 test-*)與呼叫端指定的測試檔。
★ 10-04 改成整棵抄:原本只抄 src/i18n/tools/deploy,帳本要的 schemas/ 不在複本 ⇒ 子行程因「找不到 schema」而紅,
  刀看起來有牙其實沒驗到(E 組實踩:an internal service-client、inbox-prescreen 補抄前六格全因缺 schema 紅)。
  少抄一個資料夾 = 假紅;寧可多抄。
離開時刪複本,並驗真樹那支檔的雜湊沒變(變了就 AssertionError)。錨不是恰好出現一次 ⇒ AssertionError,不落刀。
"""
import contextlib
import hashlib
import shutil
import tempfile
from pathlib import Path

_SKIP_TOP = (".git", "tests")


@contextlib.contextmanager
def knife_copy(root, rel, needle, replacement, tests=(), drop=()):
    """drop:複本裡要拿掉的路徑(相對樹根;例:aib_vendor_variants.VENDOR_PATHS ⇒ 刀落在「沒有 vendor 副本」的那一種)。"""
    root = Path(root)
    real = root / rel
    real_sha = hashlib.sha256(real.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="aib-knife-copy-") as td:
        dst = Path(td) / "tree"
        ign = shutil.ignore_patterns("__pycache__")
        dst.mkdir()
        for ent in root.iterdir():
            if ent.name in _SKIP_TOP:
                continue
            if ent.is_dir() and not ent.is_symlink():
                shutil.copytree(ent, dst / ent.name, ignore=ign, symlinks=True)
            else:
                shutil.copy2(ent, dst / ent.name, follow_symlinks=False)
        (dst / "tests").mkdir()
        for f in (root / "tests").glob("*.py"):
            if not f.name.startswith("test-") or f.name in tests:
                shutil.copy2(f, dst / "tests" / f.name)
        for d in drop:
            shutil.rmtree(dst / d, ignore_errors=True)
        layer = dst / rel
        text = layer.read_text(encoding="utf-8")
        if text.count(needle) != 1:
            raise AssertionError("錨不是恰好出現一次(%d 次),刀不准落:%s" % (text.count(needle), rel))
        layer.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
        yield dst
    if hashlib.sha256(real.read_bytes()).hexdigest() != real_sha:
        raise AssertionError("真樹的 %s 被動到了" % rel)
