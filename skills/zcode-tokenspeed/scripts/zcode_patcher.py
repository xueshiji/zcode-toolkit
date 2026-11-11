#!/usr/bin/env python3
"""
ZCode 客户端补丁工具
=====================

对本地 ZCode 安装打补丁（自动探测安装位置、独立备份、幂等）：

TPS 统计栏（app.asar，--tps-footer）
  向渲染层 out/renderer/index.html 注入 zcode-tps.js（读 ServicePort 事件流得精确
  tok/s/首 token/out；脚本内不调用 port.start()，否则 3.12.2 会卡启动）：
  输入框下方常驻统计条（默认位置），左组=本轮即时指标（首 token/tok/s/out）、
  右组=会话累计（第 N 轮/输入/命中+命中率/累出），空会话显示空态绿点常驻；
  胶囊右键可切「输入框下方（默认）/ 输入框工具栏 / 会话顶部 sticky」，localStorage 记忆。
  数据取自页面内 MessagePort 会话事件流，无常驻服务。
  重打包级修改：整体重排 asar 目录、对改动文件重算 integrity。
  原件备份 app.asar.tps.bak（带指纹 meta），记录在 app.asar.tps-patch.json，可整体还原。

用法：
  python zcode_patcher.py --all --check         # 【推荐第一步】只读体检，不改任何文件
  python zcode_patcher.py --all                 # 打上 TPS 状态栏（先完全退出 ZCode）
  python zcode_patcher.py --all --revert        # 还原 TPS 状态栏
  python zcode_patcher.py --tps-footer          # 同上，显式指定（支持 --check/--revert）
  python zcode_patcher.py "D:\\ZCode"           # 只处理指定安装（安装根目录或 zcode.cjs 均可）

通用开关：
  --all       打开全部补丁（当前仅 TPS；配合 --check / --revert 用）
  --dry-run   只报告将要做的改动，不写盘
  --force     跳过备份指纹校验（备份与当前文件对不上时强制继续，慎用）
  --verbose   打印安装探测的每一步结果（定位不到安装时用）

注意：
  * ZCode 升级会覆盖 app.asar，升级后需重新执行补丁；升级后首次打补丁
    会自动归档旧版本的 .bak（备份带指纹），避免还原时把旧版本盖回新客户端。
  * 打完补丁完全退出并重启 ZCode 后生效；打补丁前请先完全退出 ZCode（脚本会预检）。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

try:                                   # 控制台编码/窗口安全网（见 _console.py 的说明）
    from _console import bad_mark, no_window_kwargs, ok_mark, safe_stdio
except ImportError:                    # 被别处 import 时脚本目录可能不在 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _console import bad_mark, no_window_kwargs, ok_mark, safe_stdio

# 全部补丁的命令行参数名：--all 会一次性把它们打开（新增补丁时只需加进这里）
ALL_PATCH_FLAGS = (
    "tps_footer",
)


def _expand_all(args) -> None:
    """把 --all 展开成逐个补丁参数（原地修改 args）。

    单独抽成函数是为了可测，并让「--all 到底覆盖了哪些补丁」有唯一事实来源
    （ALL_PATCH_FLAGS）：新加补丁时只改常量，不会再出现「--all 漏了新功能」。
    """
    for name in ALL_PATCH_FLAGS:
        setattr(args, name, True)

# --verbose：打印安装探测与备份校验的细节（定位不到安装、备份指纹异常时用）
VERBOSE = False

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asar_version(asar: Path) -> str | None:
    """从 asar 内 package.json 读客户端版本（读不到返回 None）。"""
    try:
        raw, header, data_start = _asar_header_raw(asar)
        ent = _asar_find_entry(header, "package.json")
        if not ent:
            return None
        m = re.search(rb'"version"\s*:\s*"([^"]+)"', _asar_entry_bytes(raw, data_start, ent))
        return m.group(1).decode("utf-8", "replace") if m else None
    except Exception:
        return None


# ---------------------------------------------------------------- 备份指纹
# 备份必须带指纹：ZCode 升级会覆盖 zcode.cjs / app.asar，而 .bak 会留在原地。
# 没有指纹时，「打补丁」会复用旧版本的备份、「还原」会把旧版本文件盖回新客户端
# （A 版内核 + B 版 app.asar 混搭 → 客户端起不来）。指纹失配时一律归档旧备份并重建。

def _backup_meta(backup: Path) -> Path:
    return backup.with_name(backup.name + ".meta.json")


def _load_backup_meta(backup: Path) -> dict | None:
    meta = _backup_meta(backup)
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_backup_meta(backup: Path, original: bytes, patched: bytes | None,
                       version: str | None, source: str) -> None:
    meta = {
        "tool": "zcode-patcher",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,                      # 被备份的文件路径
        "zcode_version": version,              # 备份时的客户端版本（尽力而为）
        "sha256": _sha256(original),           # 原始（未打补丁）内容哈希
        "size": len(original),
        "patched_sha256": _sha256(patched) if patched is not None else None,
    }
    try:
        _backup_meta(backup).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    except OSError:
        pass


def _archive_backup(backup: Path) -> Path | None:
    """把不再可信的旧备份改名留档（不删除），返回归档后的路径。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup.with_name(f"{backup.name}.stale-{stamp}")
    n = 1
    while target.exists():
        target = backup.with_name(f"{backup.name}.stale-{stamp}-{n}")
        n += 1
    try:
        backup.replace(target)
        if _backup_meta(backup).is_file():
            _backup_meta(backup).replace(_backup_meta(target))
        return target
    except OSError:
        return None


def _ensure_backup(backup: Path, current: Path, version: str | None, *,
                   patched: bytes | None = None, dry_run: bool = False) -> bytes | None:
    """确保 backup 是 current 的「同一版本」原始副本。
    返回当前文件内容（调用方复用），失败返回 None。
    指纹失配（例如客户端升级过）→ 归档旧备份并重建，而不是沿用。"""
    try:
        data = current.read_bytes()
    except OSError as e:
        print(f"    [!] 无法读取 {current}：{e}")
        return None
    if not backup.is_file():
        if dry_run:
            print(f"    [~] 将新建备份 {backup.name}")
            return data
        try:
            backup.write_bytes(data)
            _write_backup_meta(backup, data, patched, version, str(current))
        except OSError as e:
            print(f"    [!] 备份失败：{e}")
            return None
        print(f"    [+] 已建立备份 {backup.name}（{len(data):,} 字节，含指纹）")
        return data

    meta = _load_backup_meta(backup)
    cur_sha = _sha256(data)
    if meta is None:
        arch = None if dry_run else _archive_backup(backup)
        print(f"    [!] 旧备份 {backup.name} 无指纹（早期版本生成），无法确认与当前版本一致"
              + (f"，已归档为 {arch.name}" if arch else "（dry-run：将归档）"))
        if dry_run:
            return data
        try:
            backup.write_bytes(data)
            _write_backup_meta(backup, data, patched, version, str(current))
            print(f"    [+] 已按当前版本重建备份（{len(data):,} 字节）")
        except OSError as e:
            print(f"    [!] 备份失败：{e}")
            return None
        return data

    if cur_sha == meta.get("sha256"):
        return data                                  # 已是原始件：备份与当前一致
    if cur_sha == meta.get("patched_sha256"):
        return data                                  # 已是打过补丁的那份：备份有效
    arch = None if dry_run else _archive_backup(backup)
    print(f"    [!] 备份与当前文件不符（版本已变化，如客户端升级过）"
          + (f"，旧备份已归档为 {arch.name}" if arch else "（dry-run：将归档）"))
    if dry_run:
        return data
    try:
        backup.write_bytes(data)
        _write_backup_meta(backup, data, patched, version, str(current))
        print(f"    [+] 已按当前版本重建备份（{len(data):,} 字节）")
    except OSError as e:
        print(f"    [!] 备份失败：{e}")
        return None
    return data


# ---------------------------------------------------------------- 安装位置探测

def _norm(s: str) -> str:
    return s.lower().replace(" ", "").replace("-", "")


def _from_running_processes(found: list[Path]) -> None:
    """1) 正在运行的 ZCode 进程路径（最准：用户实际在用哪个）"""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object {$_.Path} | "
             "Select-Object -ExpandProperty Path -Unique"],
            capture_output=True, timeout=15, errors="replace",
            **no_window_kwargs(),
        ).stdout or ""
    except Exception:
        return
    # 注意：传了 errors="replace" 时 subprocess 会直接解码成 **str**（不是 bytes），
    # 所以这里不能再 `.decode()` —— 那会抛 AttributeError 并被上面吞掉，
    # 表现为「探测器命中 0 个」这种极难定位的静默失败。两种类型都接住。
    if isinstance(out, bytes):
        out = out.decode(errors="replace")
    for line in out.splitlines():
        line = line.strip()
        # ZCode.exe / ZCode Skin Manager 等都指向安装根目录。
        # 存 PureWindowsPath 而非 Path：单测把 os.name 伪造成 "nt"（zp.os 即全局 os
        # 模块单例），此时 Path() 在 macOS/Linux 上会尝试构造 WindowsPath 并抛
        # NotImplementedError；discover() 消费时统一 Path(root) 转回具体路径
        # （真实 Windows 运行时才转换，PureWindowsPath 属性与 WindowsPath 一致）。
        if line.lower().endswith(".exe") and "zcode" in _norm(PureWindowsPath(line).name):
            found.append(PureWindowsPath(line).parent)


def _from_registry(found: list[Path]) -> None:
    """2) 注册表卸载信息里的 InstallLocation / DisplayIcon"""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                base = winreg.OpenKey(hive, sub)
            except OSError:
                continue
            with base:
                for i in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, i)) as k:
                            try:
                                name = winreg.QueryValueEx(k, "DisplayName")[0]
                            except OSError:
                                continue
                            if "zcode" not in _norm(str(name)):
                                continue
                            for val in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    v = str(winreg.QueryValueEx(k, val)[0])
                                except OSError:
                                    continue
                                p = Path(v.strip('"').split(",")[0].strip())
                                found.append(p if p.is_dir() else p.parent)
                                break
                    except OSError:
                        continue


def _from_common_dirs(found: list[Path]) -> None:
    """3) 常规安装目录：Windows Program Files 系 / macOS /Applications / Linux /opt、/usr/share"""
    bases = [os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)"),
             os.environ.get("ProgramW6432"),
             "/Applications",
             os.path.expanduser("~/Applications"),
             "/opt",
             "/usr/share"]
    # 只在确实有 LOCALAPPDATA 时加 %LOCALAPPDATA%\Programs：
    # 否则 os.path.join("", "Programs") 会拼出相对路径，可能误命中当前工作目录下的同名文件夹
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        bases.insert(3, os.path.join(local_appdata, "Programs"))
    for base in bases:
        if not base or not Path(base).is_dir():
            continue
        try:
            for entry in os.scandir(base):
                if not entry.is_dir() or "zcode" not in _norm(entry.name):
                    continue
                if entry.name.endswith(".app"):
                    found.append(Path(entry.path) / "Contents")   # macOS .app 包，资源在 Contents 下
                else:
                    found.append(Path(entry.path))
        except OSError:
            continue


def discover() -> list[Path]:
    """返回所有探测到的 zcode.cjs（去重、保序）"""
    roots: list[Path] = []
    for probe in (_from_running_processes, _from_registry, _from_common_dirs):
        before = len(roots)
        try:
            probe(roots)
        except Exception as e:
            if VERBOSE:
                print(f"[·] 探测器 {probe.__name__} 异常：{type(e).__name__}: {e}")
        if VERBOSE:
            print(f"[·] 探测器 {probe.__name__} 命中 {len(roots) - before} 个候选目录")
    seen, result = set(), []
    for root in map(Path, roots):     # 探针可能存 PureWindowsPath（见 _from_running_processes）
        cjs = root / "resources" / "glm" / "zcode.cjs"
        try:
            key = cjs.resolve()
        except OSError:
            key = cjs
        if VERBOSE:
            print(f"[·] 检查 {cjs} → {'存在' if cjs.is_file() else '不存在'}")
        if cjs.is_file() and key not in seen:
            seen.add(key)
            result.append(cjs)
    return result


def resolve_target(arg: str | None) -> list[Path]:
    if not arg:
        return discover()
    p = Path(arg)
    # 显式路径兼容多种形态：安装根目录 / macOS 的 .app 包 / zcode.cjs 文件本身
    roots = [p, p / "Contents"] if p.name.lower().endswith(".app") else [p]
    for root in roots:
        cjs = root / "resources" / "glm" / "zcode.cjs"
        if cjs.is_file():
            return [cjs]
    raise SystemExit(f"[!] 指定路径下找不到 zcode.cjs：{arg}")


# ------------------------------------------------- TPS 统计栏注入（asar 重打包级）

TPS_INDEX_PATH = "out/renderer/index.html"
TPS_SCRIPT_PATH = "out/renderer/zcode-tps.js"
TPS_TAG = f'<script src="./{TPS_SCRIPT_PATH.split("/")[-1]}"></script>'


def _read_asar_header(asar: Path):
    """只读 asar 头（不解码数据区），返回 (header 树, 数据区起始偏移)。
    重打包时不该把整包读进内存，所以单独提供这个轻量版本。"""
    with open(asar, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            raise ValueError(f"asar 文件过小: {asar}")
        f0, f1, f2, f3 = struct.unpack("<4I", head)
        if f0 != 4:
            raise ValueError(f"asar 头格式不符（首 uint32={f0}，期望 4）: {asar}")
        if not 0 < f3 < (1 << 28):
            raise ValueError(f"asar 头长度异常（jsonLen={f3}）: {asar}")
        blob = f.read(f3)
    if len(blob) != f3:
        raise ValueError(f"asar 头被截断: {asar}")
    return json.loads(blob.decode("utf-8")), 8 + f1


def _asar_header_raw(asar: Path):
    """读整个 asar：返回 (原始全量 bytes, header 树, 数据区起始偏移)。"""
    header, data_start = _read_asar_header(asar)
    return asar.read_bytes(), header, data_start


def _asar_entry_bytes(raw: bytes, data_start: int, ent: dict) -> bytes:
    off = data_start + int(ent["offset"])
    return raw[off:off + ent["size"]]


def _asar_find_entry(header: dict, entry_path: str) -> dict | None:
    node = header
    for part in entry_path.split("/"):
        files = node.get("files") or {}
        if part not in files:
            return None
        node = files[part]
    return node


def _asar_integrity(data: bytes, block_size: int = 4194304) -> dict:
    blocks = [hashlib.sha256(data[i:i + block_size]).hexdigest()
              for i in range(0, len(data), block_size)]
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(),
            "blockSize": block_size, "blocks": blocks}


def _asar_walk_entries(node, path=""):
    """yield (全路径, 叶子条目 dict)。目录与 unpacked 条目不产出。"""
    for name, ent in (node.get("files") or {}).items():
        p = f"{path}/{name}" if path else name
        if "files" in ent:
            yield from _asar_walk_entries(ent, p)
        elif not ent.get("unpacked"):
            yield p, ent


def _replace_with_retry(tmp: Path, dst: Path, attempts: int = 5) -> None:
    """os.replace，遇到 Windows 瞬时占用时退避重试。

    WinError 5（拒绝访问）与 WinError 32（正被占用）在 Windows 上极常见：
    刚被别的进程读过的文件、杀软正在扫描的文件，句柄释放有几毫秒到几秒延迟。
    只做短退避重试，不做长等待 —— 真正的「ZCode 还在运行」由上层运行守卫拦截。
    """
    delay = 0.25
    for i in range(attempts):
        try:
            os.replace(tmp, dst)
            return
        except OSError as e:
            if getattr(e, "winerror", None) not in (5, 32) or i == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 4.0)


class _AsarWriteLock:
    """跨进程 + 跨线程互斥锁：保护对同一个 asar 的「读 header → 排布 → 写数据 → 原子替换」全程。

    ★ 为什么必须有（2026-09-23 事故）：看护（apply_after_exit）与手动 / 流水线可能
      同时对同一个 app.asar 调 `_repack_asar`。两个进程各自读到一份 header、各自算
      offset，交错落盘后就会产出「数据区按 A 的布局、header 按 B 的布局」的错位文件 ——
      表现为 offset 整体偏移固定字节数、integrity 全部失配，Electron 静默拒绝加载。
      回读校验（integrity 全域比对）能拦住落盘，但更根本的是**从一开始就不让两个写入者
      同时进入**，否则用户只会看到「打补丁失败」而不知原因。

    两层保护，缺一不可：
      ① 进程内 threading.Lock —— msvcrt/fcntl 的 advisory lock 是**按进程**持有的，
         同一进程内的两个线程都能拿到，必须自己再串一层（`--all` 若改为并行就会踩）。
      ② 跨进程文件锁 —— 锁文件放 asar 同目录（`<asar>.zp-lock`），
         Windows msvcrt.locking / POSIX fcntl.flock。拿不到就轮询等待，超时抛 TimeoutError
         由上层「被占用」分支处理。锁文件不删（避免 unlink 竞态），内容记持有者 pid + 时间戳。
    """

    # 同一 asar 路径 → 进程内锁（key 用规范化的绝对路径，避免相对/绝对各持一把）
    _thread_locks: dict[str, "threading.Lock"] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, asar: Path, timeout: float = 60.0, poll: float = 0.2):
        self.path = asar.with_name(asar.name + ".zp-lock")
        self.timeout = timeout
        self.poll = poll
        self._key = os.path.abspath(str(asar))
        self._fh = None
        self._tlock = None

    @classmethod
    def _thread_lock(cls, key: str) -> "threading.Lock":
        with cls._thread_locks_guard:
            lk = cls._thread_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                cls._thread_locks[key] = lk
            return lk

    def __enter__(self):
        # ① 先拿进程内锁（带超时，避免永久阻塞）
        self._tlock = self._thread_lock(self._key)
        if not self._tlock.acquire(timeout=self.timeout):
            raise TimeoutError(
                f"等待 asar 写入锁超时（{self.timeout:.0f}s）：{self.path.name}\n"
                f"    本进程内已有注入流程正在写入，请稍后重试")
        # ② 再拿跨进程文件锁（Windows msvcrt / POSIX fcntl —— 插件在 macOS/Linux 也会注入客户端）
        try:
            if os.name == "nt":
                import msvcrt  # Windows 专用
            else:
                import fcntl   # POSIX 跨进程文件锁
            deadline = time.time() + self.timeout
            self.path.parent.mkdir(parents=True, exist_ok=True)
            while True:
                try:
                    fh = open(self.path, "a+b")
                    try:
                        if os.name == "nt":
                            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        fh.close()
                        raise
                    self._fh = fh
                    try:
                        fh.seek(0)
                        fh.truncate()
                        fh.write(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}".encode())
                        fh.flush()
                    except OSError:
                        pass
                    return self
                except OSError:
                    if time.time() >= deadline:
                        raise TimeoutError(
                            f"等待 asar 写入锁超时（{self.timeout:.0f}s）：{self.path.name}\n"
                            f"    可能另一个注入流程（看护 / 流水线）正在写入，请稍后重试")
                    time.sleep(self.poll)
        except BaseException:
            self._release_thread()
            raise

    def _release_thread(self):
        if self._tlock is not None:
            try:
                self._tlock.release()
            except RuntimeError:
                pass
            self._tlock = None

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                self._fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        self._release_thread()
        return False


def _repack_asar(asar: Path, overwrite: dict[str, bytes], remove: set[str]) -> int:
    """通用 asar 重打包：树中删除 remove 条目，overwrite 覆盖/新增文件数据并重算 integrity，
    全部条目 offset 重排；写临时文件、回读校验后原子替换。返回新文件大小。

    内存策略：**不把整包读进内存**——未改动条目从源文件按块流式搬运（asar 常达数百 MB，
    原先 read_bytes() 会让峰值内存接近 2× 包体），只有被覆盖的条目在内存里。

    并发：全程持有 `_AsarWriteLock`，防止看护与手动/流水线流程交错写入造成布局错位。"""
    with _AsarWriteLock(asar):
        return _repack_asar_locked(asar, overwrite, remove)


def _repack_asar_locked(asar: Path, overwrite: dict[str, bytes], remove: set[str]) -> int:
    """`_repack_asar` 的加锁实现体（持锁调用，不要直接调用）。"""
    header, data_start = _read_asar_header(asar)

    # overwrite 中树里尚不存在的路径（新增文件）按层级插入占位条目
    for p in overwrite:
        parts = p.split("/")
        node = header
        for part in parts[:-1]:
            node = node.setdefault("files", {}).setdefault(part, {"files": {}})
        leaf = node.setdefault("files", {})
        leaf.setdefault(parts[-1], {"size": 0, "offset": "0"})

    def purge(node, prefix):
        files = node.get("files") or {}
        for name in list(files.keys()):
            ent = files[name]
            p = f"{prefix}/{name}" if prefix else name
            if "files" in ent:
                purge(ent, p)
                if not ent["files"]:
                    del files[name]          # 删空的目录一并移除
            elif p in remove:
                del files[name]

    purge(header, "")
    # relayout 会改写 ent.offset/size，源文件里的旧位置必须先快照（流式搬运时用）
    old_positions = {p: (int(ent["offset"]), int(ent["size"]))
                     for p, ent in _asar_walk_entries(header)}
    cursor = 0

    def relayout(node, prefix):
        nonlocal cursor
        for name, ent in (node.get("files") or {}).items():
            p = f"{prefix}/{name}" if prefix else name
            if "files" in ent:
                relayout(ent, p)
            elif ent.get("unpacked"):
                continue
            else:
                data = overwrite.get(p)
                size = len(data) if data is not None else old_positions[p][1]
                ent["size"] = size
                ent["offset"] = str(cursor)
                if data is not None:
                    ent["integrity"] = _asar_integrity(data)
                cursor += size

    relayout(header, "")
    json_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    header_blob = (struct.pack("<4I", 4, 8 + len(json_bytes) + pad, 4 + len(json_bytes) + pad, len(json_bytes))
                   + json_bytes + b"\x00" * pad)

    tmp = asar.with_name(f"{asar.name}.{os.getpid()}.tmp")
    chunk_size = 1 << 20
    try:
        with open(asar, "rb") as src, open(tmp, "wb") as out:
            out.write(header_blob)

            def emit(node, prefix):
                for name, ent in (node.get("files") or {}).items():
                    p = f"{prefix}/{name}" if prefix else name
                    if "files" in ent:
                        emit(ent, p)
                    elif ent.get("unpacked"):
                        continue
                    else:
                        data = overwrite.get(p)
                        if data is not None:
                            out.write(data)
                            continue
                        off, size = old_positions[p]
                        src.seek(data_start + off)
                        left = size
                        while left > 0:            # 未改动条目：分块搬运，不进内存
                            chunk = src.read(min(chunk_size, left))
                            if not chunk:
                                raise ValueError(f"源文件读取不足: {p}（还差 {left} 字节）")
                            out.write(chunk)
                            left -= len(chunk)

            emit(header, "")

        # 回读校验：只读临时文件的头，逐条比对被覆盖条目 + 校验总长度（不整包读回内存）
        v_header, v_start = _read_asar_header(tmp)
        v_files = dict(_asar_walk_entries(v_header))
        expect = v_start + sum(int(e["size"]) for _, e in _asar_walk_entries(v_header))
        if tmp.stat().st_size != expect:
            raise ValueError(f"重打包校验失败：文件长度 {tmp.stat().st_size} ≠ 期望 {expect}")
        with open(tmp, "rb") as f:
            for p, want in overwrite.items():
                ent = v_files.get(p)
                if ent is None or int(ent["size"]) != len(want):
                    raise ValueError(f"重打包校验失败（条目缺失或长度不符）: {p}")
                f.seek(v_start + int(ent["offset"]))
                if f.read(len(want)) != want:
                    raise ValueError(f"重打包校验失败（内容不符）: {p}")

            # ★ 全域 integrity 自洽校验（2026-09-23 事故后补）：
            #   上面只比对「本次被覆盖」的条目，一旦数据区排布与 header 声明错位
            #   （并发写入 / 中途被打断 / 布局基准取错），**未改动条目**会静默损坏——
            #   它们的内容被搬到偏移 K 字节处，integrity 却还记着旧内容的哈希。
            #   后果极隐蔽：asar 结构自洽（offset/size 连续无重叠）、注入条目语法正确，
            #   但 Electron 加载时按 integrity 校验会**拒绝加载** 4 千多个模块 →
            #   主进程依赖链断裂 → **启动后静默退出、无任何日志**（2026-09-23 实测）。
            #   因此这里必须逐条重算哈希：内容对的条目哈希必然对得上，
            #   只要有一条对不上就说明布局错位，宁可失败也不落盘。
            #
            #   性能：27k 条目 / 320MB 全量哈希约 1-2s，相对整次重打包（含 320MB 搬运）
            #   可忽略；分块读避免大条目整条进内存（单条最大约 8MB）。
            for p, ent in _asar_walk_entries(v_header):
                itg = ent.get("integrity")
                if not itg:
                    continue
                size = int(ent["size"])
                f.seek(v_start + int(ent["offset"]))
                left = size
                digest = hashlib.sha256()
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        raise ValueError(f"重打包校验失败（integrity 读不足）: {p}")
                    digest.update(chunk)
                    left -= len(chunk)
                if digest.hexdigest() != itg.get("hash"):
                    raise ValueError(
                        f"重打包校验失败（integrity 与实际内容不符，疑似布局错位）: {p}"
                        f"\n    声明 offset={ent['offset']} size={size}，"
                        f"该处内容哈希与 integrity 记录不一致 —— 拒绝落盘")
        _replace_with_retry(tmp, asar)
    except BaseException:
        tmp.unlink(missing_ok=True)   # 失败/被占用都不留临时文件残留
        raise
    _cleanup_stale_tmp(asar.parent)
    return asar.stat().st_size


def _cleanup_stale_tmp(folder: Path) -> None:
    """清理历史遗留的临时文件（早期版本固定用 app.asar.tps-tmp，异常退出会留在安装目录）。"""
    for pat in ("app.asar*.tps-tmp", "app.asar*.tmp"):
        for f in folder.glob(pat):
            try:
                f.unlink()
                print(f"    [·] 已清理残留临时文件 {f.name}")
            except OSError:
                pass


def _refresh_tps_sidecar(asar: Path) -> None:
    """asar 重打包后数据区整体位移：TPS sidecar 只记 asar_size，必须跟着更新，
    否则后续 `--check` 会把 sidecar 报成「无」，看着像记录丢了。"""
    try:
        cur_size = asar.stat().st_size
    except OSError:
        return
    side = asar.with_name(asar.name + ".tps-patch.json")
    if not side.is_file():
        return
    try:
        rec = json.loads(side.read_text(encoding="utf-8"))
    except Exception:
        return
    if isinstance(rec, dict) and rec.get("asar_size") != cur_size:
        rec["asar_size"] = cur_size
        try:
            side.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            print(f"[*] 已同步 {side.name} 的指纹到重打包后的 asar")
        except OSError:
            pass


def _mark_backup_patched(backup: Path, current: Path) -> None:
    """补丁写入后刷新备份指纹里的「已打形态」哈希（还原前校验要用）。"""
    meta = _load_backup_meta(backup)
    if not meta:
        return
    try:
        data = current.read_bytes()
        meta["patched_sha256"] = _sha256(data)
        meta["patched_size"] = len(data)
        _backup_meta(backup).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    except OSError:
        pass


def _process_script_inject(asar: Path, check_only: bool, revert: bool, src_path: Path | None,
                           *, label: str, script_entry: str, tag: str,
                           side_suffix: str, bak_suffix: str,
                           dry_run: bool = False, version: str | None = None) -> bool:
    """renderer 单脚本注入通用链路(TPS 统计栏):
    index.html </body> 前挂 <script> + 新增脚本条目,整体重排 asar、重算 integrity。
    ⚠️ zcode-tps.js 内**绝不调用 port.start()**(只 addEventListener,start 交给应用)——
    否则会消费掉服务端 Initialize 启动握手,ZCode 3.12.2 会卡在启动界面。
    详见 scripts/zcode-tps.js 头部说明与 SKILL.md。
    返回 True=成功/无需改动，False=失败。"""
    side = asar.with_name(asar.name + side_suffix)
    bak = asar.with_name(asar.name + bak_suffix)
    tag_b = tag.encode()

    try:
        asar_size = asar.stat().st_size
        raw, header, data_start = _asar_header_raw(asar)
    except (OSError, ValueError) as e:
        print(f"[!] {asar}\n    无法读取：{e}")
        return False
    paths = {p: ent for p, ent in _asar_walk_entries(header)}
    idx_ent = paths.get(TPS_INDEX_PATH)
    if idx_ent is None:
        print(f"[!] {asar}\n    未找到 {TPS_INDEX_PATH}，版本结构可能已变，跳过")
        return False
    idx_bytes = _asar_entry_bytes(raw, data_start, idx_ent)
    tagged = tag_b in idx_bytes
    installed = tagged and script_entry in paths

    saved = None
    if side.is_file():
        try:
            rec = json.loads(side.read_text(encoding="utf-8"))
            if rec.get("asar_size") == asar_size:
                saved = rec
        except Exception:
            saved = None

    # 注入源脚本：显式指定优先，否则取本脚本同目录下的同名文件。
    # 放在 check 之前解析 —— check 也要做内容比对（见下）。
    if src_path is None:
        src_path = Path(__file__).resolve().parent / script_entry.split("/")[-1]

    if check_only:
        state = "已打" if installed else ("不完整（index.html 有 tag 但缺脚本条目）" if tagged else "未打")
        # 已注入、但脚本内容与现行源不一致 → 必须显式报 stale。
        # 只按「结构在不在」判会输出裸「已打」，sync.check_state() 据此判 on → run_sync 走
        # `continue`（视为已一致）→ 脚本改了**永远不生效，而且一句报错都没有**。
        # 特征串必须与 sync.check_state() 逐字一致：它只认「含旧版组件」。
        if installed and src_path.is_file() and \
                _asar_entry_bytes(raw, data_start, paths[script_entry]) != src_path.read_bytes():
            state = "已打（含旧版组件，重跑可自动更新）"
        print(f"[*] {asar}\n    {label}注入: {state} | sidecar: {'有' if saved else '无'} | 备份: {'有' if bak.is_file() else '无'}")
        return True

    if revert:
        if not installed and not saved:
            print(f"[.] {asar}\n    未打 {label}注入，跳过")
            return True
        if dry_run:
            print(f"[~] {asar}\n    将移除 {label}注入（摘除 index.html 的 tag 与脚本条目）")
            return True
        # 外科手术式还原：只从当前 index.html 摘除 TPS 的 tag；sidecar 原件仅作兜底。
        stripped = idx_bytes.replace(tag_b + b"\n", b"").replace(tag_b, b"")
        if stripped == idx_bytes and saved and saved.get("index_original_b64"):
            stripped = base64.b64decode(saved["index_original_b64"])
        try:
            new_size = _repack_asar(asar, {TPS_INDEX_PATH: stripped}, {script_entry})
        except OSError as e:
            print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
            return False
        side.unlink(missing_ok=True)
        bak.unlink(missing_ok=True)
        _backup_meta(bak).unlink(missing_ok=True)
        _refresh_tps_sidecar(asar)
        print(f"[+] {asar}\n    已移除 {label}注入（新大小 {new_size:,} 字节，备份已清理）")
        return True

    if not src_path.is_file():
        print(f"[!] 找不到注入源脚本 {src_path}（可用 --tps-src 指定路径）")
        return False
    script_bytes = src_path.read_bytes()

    # 脚本热更新：已注入但内容与源不一致时替换脚本条目（无需先 revert）。
    # 只按"脚本条目是否存在"判断会让改动后的脚本永不生效（版本适配修复无法落地）。
    if installed:
        cur_script = _asar_entry_bytes(raw, data_start, paths[script_entry]) if script_entry in paths else None
        if cur_script == script_bytes:
            print(f"[=] {asar}\n    已打 {label}注入（脚本同源），跳过")
            return True
        if dry_run:
            print(f"[~] {asar}\n    将热更新 {label}脚本（{len(cur_script or b''):,} → {len(script_bytes):,} 字节）")
            return True
        try:
            new_size = _repack_asar(asar, {script_entry: script_bytes}, set())
        except OSError as e:
            print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
            return False
        _refresh_tps_sidecar(asar)
        print(f"[+] {asar}\n    {label}脚本已热更新（{src_path.name} {len(script_bytes):,} 字节，其余组件不变）")
        return True

    if idx_bytes.count(b"</body>") != 1:
        print(f"[!] {asar}\n    index.html 的 </body> 出现 {idx_bytes.count(b'</body>')} 次（期望 1），拒绝盲改")
        return False

    if dry_run:
        print(f"[~] {asar}\n    将注入 {script_entry}（{len(script_bytes):,} 字节）并在 index.html 挂载 tag")
        return True

    if _ensure_backup(bak, asar, version) is None:
        return False

    new_idx = idx_bytes.replace(b"</body>", tag_b + b"</body>", 1)
    try:
        new_size = _repack_asar(asar, {TPS_INDEX_PATH: new_idx, script_entry: script_bytes}, set())
    except OSError as e:
        print(f"[!] {asar}\n    文件被占用或无写入权限：{e}\n    请完全退出 ZCode 后重试")
        return False
    _mark_backup_patched(bak, asar)
    side.write_text(json.dumps({
        "asar_size": new_size,
        "index_path": TPS_INDEX_PATH,
        "script_entry": script_entry,
        "index_original_b64": base64.b64encode(idx_bytes).decode(),
    }, ensure_ascii=False), encoding="utf-8")
    _refresh_tps_sidecar(asar)
    print(f"[+] {asar}\n    {label}注入完成（{src_path.name} {len(script_bytes):,} 字节 -> {script_entry}，index.html 已挂载）\n"
          f"    原件备份: {bak.name} | 记录: {side.name}")
    return True


def process_tps_footer(asar: Path, check_only: bool, revert: bool, tps_src: Path | None, *,
                       dry_run: bool = False, version: str | None = None) -> bool:
    return _process_script_inject(asar, check_only, revert, tps_src,
                                  label="TPS 统计栏", script_entry=TPS_SCRIPT_PATH, tag=TPS_TAG,
                                  side_suffix=".tps-patch.json", bak_suffix=".tps.bak",
                                  dry_run=dry_run, version=version)


def _pad_display(text: str, width: int) -> str:
    """按终端显示宽度补齐（中日韩字符占两列），让汇总表对齐。"""
    w = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
    return text + " " * max(1, width - w)


def _auto_prune_stale(asar: Path) -> None:
    """打补丁时自动静默清理失效归档（.stale-*）与废弃功能残留，防止客户端升级后积累几百兆历史文件。"""
    try:
        folder = asar.parent
        for pat in (f"{asar.name}*.stale-*", f"{asar.name}*.tps-tmp", f"{asar.name}.*.tmp"):
            for p in folder.glob(pat):
                if p.is_file():
                    try:
                        p.unlink()
                    except OSError:
                        pass
    except Exception:
        pass


def prune_artifacts(asars: list[Path], deep: bool, dry_run: bool = False) -> int:
    """清理补丁产物，返回回收的字节数。

    默认只删「确定没用的」：`.stale-*` 归档（升级前的旧版本整包）、`*.tps-tmp` / `*.tmp` 残留。
    `--deep` 连当前 `.bak` 与 sidecar 一起删 —— **之后将无法还原**，需要重打才有记录。
    只删本工具自己命名的文件，不碰目录里其它任何东西。"""
    targets: list[Path] = []

    def collect(folder: Path, patterns) -> None:
        if not folder.is_dir():
            return
        for pat in patterns:
            targets.extend(p for p in folder.glob(pat) if p.is_file())

    for asar in asars:
        folder = asar.parent
        collect(folder, (f"{asar.name}*.stale-*", f"{asar.name}*.tps-tmp", f"{asar.name}.*.tmp"))
        if deep:
            collect(folder, (f"{asar.name}*.bak", f"{asar.name}*.bak.meta.json",
                             f"{asar.name}*-patch.json"))
        # 内核备份（历史整包还原用，若存在）
        collect(folder / "glm", ("zcode.cjs.bak", "zcode.cjs.bak.meta.json"))
        if deep:
            collect(folder / "glm", ("zcode.cjs.bak.stale-*",))

    targets = sorted(set(targets))
    if not targets:
        print("[.] 没有可清理的补丁产物")
        return 0

    total = sum(p.stat().st_size for p in targets)
    print(f"[*] {'将' if dry_run else ''}清理 {len(targets)} 个文件，共 {total / 1048576:.1f} MB：")
    for p in targets:
        print(f"      {p.name:<44} {p.stat().st_size / 1048576:>9.1f} MB")
    if deep:
        print("    [!] --deep 已包含当前备份与 sidecar：清理后将无法用 --revert 还原，需重打才有记录")
    if dry_run:
        print("    （--dry-run 未删除）")
        return 0

    freed = 0
    for p in targets:
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
        except OSError as e:
            print(f"    [!] 删除失败 {p.name}: {e}")
    print(f"[+] 已清理，回收 {freed / 1048576:.1f} MB")
    return freed


def _resolve_asars(target: str | None) -> list[Path]:
    asars = []
    for cjs in resolve_target(target):
        asar = cjs.parent.parent / "app.asar"   # resources/glm/zcode.cjs -> resources/app.asar
        if asar.is_file() and asar not in asars:
            asars.append(asar)
    if not asars:
        raise SystemExit("[!] 未找到 app.asar")
    return asars


def _pid_commandline(pid: str) -> "str | None":
    """POSIX：返回进程完整命令行；进程已消失/查询失败返回 None。"""
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", pid],
                           capture_output=True, timeout=5, **no_window_kwargs())
        if r.returncode != 0:
            return None
        return r.stdout.decode(errors="replace").strip()
    except Exception:
        return None


def _pgrep_hits_are_alive(pids: "list[str]", cmdline_of=None) -> bool:
    """对 pgrep -f ZCode 命中的 PID 逐个核对命令行，排除 crashpad 后判应用是否存活。

    ★ 为什么必须逐个核对（2026-09-24 macOS 实测）：ZCode 每次退出都会泄漏一个
    chrome_crashpad_handler（ppid=1 的崩溃报告进程，命令行仍含 ZCode.app 路径），
    pgrep -f ZCode 因此永远非零——退出后看护等不到「退出」，重打包级补丁永远写不
    进去。本机曾堆积 3.9.1 / 3.10.1 时代的多个泄漏进程，应用本体早已不在。应用真正
    存活时主进程 / ZCode Helper 必然在列且非 crashpad，不受此排除影响。
    cmdline_of 返回 None（进程在 pgrep 与 ps 之间消失）按已退出处理。
    """
    cmdline_of = cmdline_of or _pid_commandline
    for pid in pids:
        cmd = cmdline_of(pid)
        if cmd is None or "chrome_crashpad_handler" in cmd:
            continue          # 已消失的 / crashpad 僵尸：不代表应用存活
        return True
    return False


def zcode_running() -> bool:
    """ZCode 是否在运行（打补丁前预检：运行中会锁住 app.asar，配置也可能被回写覆盖）。"""
    try:
        if os.name == "nt":
            # 用 bytes 检索：tasklist 输出是 GBK，text=True 会在读线程抛 UnicodeDecodeError
            out = subprocess.run(["tasklist"], capture_output=True, timeout=15,
                                 **no_window_kwargs()).stdout or b""
            return b"ZCode.exe" in out
        r = subprocess.run(["pgrep", "-f", "ZCode"], capture_output=True, timeout=10)  # no-window-ok: 只在 POSIX 分支执行
        if r.returncode != 0:
            return False
        pids = r.stdout.decode(errors="replace").split()
        return _pgrep_hits_are_alive(pids)
    except Exception:
        return False


def main() -> int:
    safe_stdio()          # 输出被重定向时 cp936 会编不出 ✓/✗，先把这条路封死
    ap = argparse.ArgumentParser(
        description="ZCode 客户端补丁工具：TPS 统计栏（自动探测安装位置）")
    ap.add_argument("target", nargs="?", help="可选：安装根目录或 zcode.cjs 路径；缺省自动探测全部")
    mode_g = ap.add_mutually_exclusive_group()
    mode_g.add_argument("--check", action="store_true", help="只检查状态，不修改")
    mode_g.add_argument("--revert", action="store_true", help="从备份还原")
    ap.add_argument("--dry-run", action="store_true", help="只报告将要做的改动，不写盘")
    ap.add_argument("--force", action="store_true",
                    help="跳过备份指纹校验（备份与当前文件不是同一版本时强制继续，慎用）")
    ap.add_argument("--verbose", action="store_true", help="打印安装探测的每一步结果")
    ap.add_argument("--all", action="store_true",
                    help="打开全部补丁（当前仅 TPS）："
                         "`--all --check` 只读体检，`--all` 打上，`--all --revert` 还原")
    ap.add_argument("--tps-footer", action="store_true",
                    help="注入 TPS 统计栏：输入框下方常驻统计条（本轮指标 + 会话累计），asar 重打包级")
    ap.add_argument("--tps-src", default=None,
                    help="指定注入脚本路径（默认本目录 zcode-tps.js）")
    ap.add_argument("--prune", action="store_true",
                    help="清理安装目录里的补丁产物（旧版本归档 / 临时文件残留）")
    ap.add_argument("--deep", action="store_true",
                    help="配合 --prune：连当前 .bak 与 sidecar 一起清（之后无法 --revert，慎用）")
    args = ap.parse_args()

    if args.all:
        _expand_all(args)

    global VERBOSE
    VERBOSE = args.verbose

    # ---------- 清理产物（不涉及打补丁，不需要预检进程） ----------
    if args.prune:
        try:
            asars = _resolve_asars(args.target)
        except SystemExit as e:
            print(e)
            return 1
        print(f"=== 清理补丁产物，目标 {len(asars)} 处安装"
              f"{'（含当前备份，清理后无法 --revert）' if args.deep else '（仅归档与临时文件）'} ===")
        try:
            prune_artifacts(asars, args.deep, dry_run=args.dry_run)
        except Exception as e:
            print(f"[!] 清理失败：{type(e).__name__}: {e}")
            return 1
        return 0

    asar_flags = args.tps_footer
    if not asar_flags:
        ap.print_help()
        return 0

    mode = "检查" if args.check else ("还原" if args.revert else ("预演" if args.dry_run else "打补丁"))
    fails: list[str] = []
    results: list[tuple[str, str, bool]] = []      # (补丁名, 目标, 是否成功)

    def run_step(title: str, target: Path, fn) -> None:
        """统一执行 + 记录结果：任何异常都收敛成「失败」，不把裸堆栈甩给用户。"""
        try:
            ok = bool(fn(target))
        except Exception as e:
            print(f"[!] {target}\n    执行失败：{type(e).__name__}: {e}")
            ok = False
        results.append((title, str(target), ok))
        if not ok:
            fails.append(f"{title} @ {target}")

    # ---------- 预检：运行中一律不打补丁（asar 被锁） ----------
    if not args.check and not args.dry_run:
        if zcode_running():
            print("[!] 检测到 ZCode 正在运行 —— 打补丁/还原前请完全退出 ZCode")
            print("    运行中 app.asar 会被锁定")
            print("    只查看状态：加 --check；预演改动：加 --dry-run")
            return 2

    try:
        asars = _resolve_asars(args.target)
    except SystemExit as e:
        print(e)
        return 1
    # 预先自动清理失效的历史归档与已废弃功能残留文件，防止升级产生冗余大文件
    for a in asars:
        _auto_prune_stale(a)

    version = asar_version(asars[0])
    print(f"=== 客户端版本 {version or '未知'} | 目标 {len(asars)} 处 | 模式：{mode} ===")

    steps = [
        (args.tps_footer, "TPS 统计栏注入",
         lambda a: process_tps_footer(a, args.check, args.revert,
                                      Path(args.tps_src) if args.tps_src else None,
                                      dry_run=args.dry_run, version=version)),
    ]
    for enabled, title, fn in steps:
        if not enabled:
            continue
        print(f"=== {title}，目标 {len(asars)} 处，模式：{mode} ===")
        for a in asars:
            run_step(title, a, fn)

    if results:
        print(f"\n=== 执行汇总（模式：{mode}）===")
        for title, target, ok in results:
            p_ = Path(target)
            shown = target if p_.is_dir() else p_.name
            print(f"  {ok_mark() if ok else bad_mark()} {_pad_display(title, 22)}{shown}")
        ok_n = sum(1 for *_, ok in results if ok)
        print(f"  合计 {len(results)} 项：成功 {ok_n}，失败 {len(results) - ok_n}")

    if not args.check and not args.revert and not args.dry_run:
        print("=== 提示：完全退出并重启 ZCode 后生效；升级后需重新执行本脚本 ===")

    if fails:
        print(f"\n[!] 执行完成，但有 {len(fails)} 项未成功：")
        for f in fails:
            print("    -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
