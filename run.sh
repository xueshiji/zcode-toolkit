#!/bin/sh
# zcode-toolkit 一键入口（macOS / Linux / Git Bash）
#
# 作用：找到可用的 Python，把参数原样转交给 autopilot.py。
# 用法：
#   ./run.sh                  # 全自动跑通
#   ./run.sh --no-deploy      # 只构建 + 测试
#   ./run.sh --unattended     # 无人值守（CI）
#
# 为什么需要这层壳：三平台对「python 还是 python3」的约定不同，
# 且从文件管理器双击运行时工作目录未必是仓库根。这里两者都处理掉。

set -eu

# 与 run.cmd 对齐：管道/重定向下 Windows 默认按 ANSI 代码页输出，中文会丢成 ?。
export PYTHONIOENCODING=utf-8

# 定位仓库根 = 本脚本所在目录。
#
# ★ Windows / Git Bash（MSYS2）的坑：`pwd -P` 返回 POSIX 形式 `/f/foo/bar`，
#   把它拼给**原生** python.exe 时，MSYS 会再做一次路径转换 —— 「/f/...」被当成
#   相对路径，于是拼出 `F:\f\foo\bar` 这种不存在的路径（多了一层 f）。
#   所以只要 cygpath 可用，最后统一把仓库根转成 Windows 原生路径；
#   在真正的 Linux / macOS 上没有 cygpath，这一整段自然不生效。
SCRIPT_DIR=$(dirname -- "$0")
cd "$SCRIPT_DIR" 2>/dev/null || {
    echo "[x] 无法进入仓库目录：$SCRIPT_DIR" >&2
    exit 1
}
SCRIPT_DIR=$(pwd -P)

if command -v cygpath >/dev/null 2>&1; then
    WINPATH=$(cygpath -w "$SCRIPT_DIR" 2>/dev/null || true)
    [ -n "$WINPATH" ] && SCRIPT_DIR=$WINPATH
fi

# 依次尝试常见的 Python 命令名；能跑起来的第一个就用（版本检查交给 autopilot.py）
PY=""
for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys' >/dev/null 2>&1; then
            PY="$cand"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "[x] 找不到可用的 Python。" >&2
    echo "    macOS:  brew install python@3.12" >&2
    echo "    Ubuntu: sudo apt install python3.12" >&2
    echo "    Fedora: sudo dnf install python3.12" >&2
    exit 2
fi

exec "$PY" "$SCRIPT_DIR/autopilot.py" "$@"

