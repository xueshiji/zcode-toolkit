#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZCode 紧急还原脚本（从干净备份整包恢复）
=========================================
用途：补丁导致客户端无法启动时，一条命令恢复，**无需重装 ZCode**。

备份目录里存的是未打补丁的干净文件：
  app.asar.<版本>.clean
  zcode.cjs.<版本>.clean

用法：
  python restore_clean.py                 # 列出可用备份
  python restore_clean.py --latest        # 用最新备份还原（需先完全退出 ZCode）
  python restore_clean.py --version 3.14.3
  python restore_clean.py --backup        # 把当前安装的文件另存为新备份

安全约束（重要）：
  * 还原前会比对「备份版本」与「当前安装版本」，不一致时**拒绝执行**——否则会把旧版
    app.asar / zcode.cjs 盖到新版客户端上，反而把安装弄坏（用 --force 可强行继续）。
  * 安装位置复用 zcode_patcher.discover()（跨平台探测），不再硬编码盘符。
  * 备份目录默认 ~/.zcode/patcher-backups（跨平台）；历史默认 D:/ZCode-clean-backup
    若存在仍会作为回退候选。

注意：还原会覆盖 resources 下的 app.asar / glm/zcode.cjs，执行前务必完全退出 ZCode。
"""

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

try:                                   # 控制台编码安全网（见 _console.py 的说明）
    from _console import ok_mark, safe_stdio, warn_mark
except ImportError:                    # 被别处 import 时脚本目录可能不在 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _console import ok_mark, safe_stdio, warn_mark

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import zcode_patcher as zp
except Exception:                       # 单文件分发时退化为内置探测
    zp = None

LEGACY_BACKUP_DIR = Path("D:/ZCode-clean-backup")
DEFAULT_BACKUP_DIR = Path.home() / ".zcode" / "patcher-backups"

CANDIDATE_ROOTS = [
    Path(r"C:/Program Files/ZCode"),
    Path(r"D:/ZCode"),
    Path.home() / "AppData/Local/Programs/ZCode",
    Path("/Applications/ZCode.app/Contents"),
    Path("/opt/ZCode"),
    Path("/usr/share/ZCode"),
]


def find_resources() -> Path | None:
    """定位 resources 目录（优先复用主脚本的跨平台探测）。"""
    if zp is not None:
        try:
            for cjs in zp.discover():
                res = cjs.parent.parent
                if (res / "app.asar").is_file():
                    return res
        except Exception:
            pass
    for root in CANDIDATE_ROOTS:
        res = root / "resources"
        if (res / "app.asar").is_file():
            return res
    return None


def installed_version(res: Path) -> str | None:
    """当前安装的客户端版本（读 asar 内 package.json）。"""
    if zp is None:
        return None
    try:
        return zp.asar_version(res / "app.asar")
    except Exception:
        return None


def list_backups(bdir: Path):
    asars = sorted(bdir.glob("app.asar.*.clean"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for a in asars:
        ver = a.name[len("app.asar."):-len(".clean")]
        cjs = bdir / f"zcode.cjs.{ver}.clean"
        out.append((ver, a, cjs if cjs.is_file() else None, a.stat().st_mtime))
    return out


def stale_local_backups(res: Path) -> list[Path]:
    """补丁在安装目录留下的整包备份（app.asar.*.bak）。可能来自旧版本，还原前要当心。"""
    found = []
    for p in sorted(res.glob("app.asar*.bak")):
        meta = p.with_name(p.name + ".meta.json")
        note = ""
        if meta.is_file():
            try:
                import json
                m = json.loads(meta.read_text(encoding="utf-8"))
                note = f"（指纹版本 {m.get('zcode_version') or '未知'}）"
            except Exception:
                note = "（指纹无法解析）"
        else:
            note = "（无指纹：无法确认属于哪个版本）"
        found.append((p, note))
    return found


def main() -> int:
    safe_stdio()          # 输出被重定向时 cp936 会编不出符号，先把这条路封死
    ap = argparse.ArgumentParser(description="ZCode 紧急还原（从干净备份整包恢复）")
    ap.add_argument("--backup-dir", default=None, help="备份目录（默认 ~/.zcode/patcher-backups）")
    ap.add_argument("--latest", action="store_true", help="用最新备份还原")
    ap.add_argument("--version", default=None, help="指定版本备份（如 3.14.3）")
    ap.add_argument("--backup", action="store_true", help="把当前安装另存为备份")
    ap.add_argument("--force", action="store_true", help="备份版本与当前安装不一致时也强行还原")
    args = ap.parse_args()

    if args.backup_dir:
        bdir = Path(args.backup_dir)
    else:
        bdir = DEFAULT_BACKUP_DIR if DEFAULT_BACKUP_DIR.is_dir() or not LEGACY_BACKUP_DIR.is_dir() \
            else LEGACY_BACKUP_DIR

    res = find_resources()
    if res is None:
        print("[!] 未找到 ZCode 安装（可用 --backup-dir 指定备份目录，或手动复制）")
        return 1
    tgt_asar, tgt_cjs = res / "app.asar", res / "glm" / "zcode.cjs"
    cur_ver = installed_version(res)
    print(f"[*] 安装目录: {res}")
    print(f"[*] 当前版本: {cur_ver or '未知'} | 备份目录: {bdir}")

    try:
        bdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[!] 备份目录不可用：{e}")
        return 1
    backups = list_backups(bdir)

    if args.backup:
        ver = cur_ver or ("manual-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(tgt_asar, bdir / f"app.asar.{ver}.clean")
        shutil.copy2(tgt_cjs, bdir / f"zcode.cjs.{ver}.clean")
        print(f"[+] 已备份当前安装（版本标记 {ver}）")
        print(f"    app.asar: {tgt_asar.stat().st_size:,} 字节")
        return 0

    if not backups:
        print(f"[!] {bdir} 下没有可用备份；先运行 --backup 保存干净副本")
        return 1
    if not (args.latest or args.version):
        print(f"\n可用备份（{bdir}）：")
        for ver, a, c, mt in backups:
            flag = "  ← 与当前版本一致" if cur_ver and ver == cur_ver else ""
            print(f"  {ver:24} app.asar {a.stat().st_size:>13,}B  "
                  f"zcode.cjs {'有' if c else '无'}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(mt))}{flag}")
        stale = stale_local_backups(res)
        if stale:
            print(f"\n[!] 安装目录里还有补丁留下的整包备份（{len(stale)} 个），它们是"
                  f"**打补丁那一刻**的副本，客户端升级后往往已属旧版本，不要拿来还原：")
            for p, note in stale:
                print(f"    {p.name}{note}")
        print("\n用 --latest 或 --version <版本> 执行还原")
        return 0

    if args.version:
        pick = next((b for b in backups if b[0] == args.version), None)
        if pick is None:
            print(f"[!] 没有版本 {args.version} 的备份")
            return 1
    else:
        pick = backups[0]
    ver, src_asar, src_cjs, _ = pick

    if cur_ver and ver != cur_ver and not args.force:
        print(f"[!] 备份版本 {ver} 与当前安装版本 {cur_ver} 不一致 —— 已拒绝还原")
        print("    用旧版 app.asar 覆盖新版客户端会把安装弄坏。")
        print("    确需继续：加 --force；想拿到当前版本的干净副本：重装该版本后跑 --backup")
        return 1
    if not cur_ver:
        print("[!] 无法读出当前版本（asar 结构异常），请自行确认备份版本是否匹配（--force 跳过本检查）")

    print(f"\n准备从备份 {ver} 还原：")
    print(f"  源: {src_asar}")
    print(f"  目标: {tgt_asar}")
    try:
        shutil.copy2(src_asar, tgt_asar)
        print(f"  ✓ app.asar 已还原（{src_asar.stat().st_size:,} 字节）")
        if src_cjs:
            shutil.copy2(src_cjs, tgt_cjs)
            print(f"  {ok_mark()} zcode.cjs 已还原（{src_cjs.stat().st_size:,} 字节）")
        else:
            print(f"  {warn_mark()} 该版本无 zcode.cjs 备份，内核未改回")
    except OSError as e:
        print(f"[!] 写入被拒绝（ZCode 未完全退出 或 需要管理员权限）：{e}")
        return 1

    # 清理本机补丁产物，避免还原后 sidecar 与实际不符
    cleaned = 0
    for pat in ("*.bak", "*-patch.json", "*.tps-tmp", "*.tmp"):
        for f in res.rglob(pat):
            try:
                f.unlink()
                cleaned += 1
            except OSError:
                pass
    if cleaned:
        print(f"  · 已清理 {cleaned} 个补丁产物（备份/sidecar/临时文件）")
    print(f"\n{ok_mark()} 还原完成，请启动 ZCode 验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
