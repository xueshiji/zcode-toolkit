# AGENTS.md

## 项目定位

这是 ZCode 桌面客户端的本地增强套件：以插件 `zcode-tokenspeed` 分发（仓库名是 `zcode-toolkit`，二者刻意不同名），通过改写客户端本地的 `resources/app.asar` 注入功能。当前只提供 1 项补丁：实时 TPS 与性能状态栏。

所有操作必须幂等、可 `--check`、可 `--revert`；任何写入都要有备份与版本指纹。

## 关键目录

- `skills/zcode-tokenspeed/scripts/`：全部核心代码。Python 补丁引擎 + 会被注入客户端的 JS。
- `skills/zcode-tokenspeed/SKILL.md`：面向使用者的 TPS 操作手册与排障记录。改补丁行为前先读相关章节。
- `commands/`：插件斜杠命令（`/zcode-patch-*`），内容是指示 AI 调用 skill 的流程说明。
- `hooks/hooks.json`：唯一的 `SessionStart` 钩子，调用 `sync.py --detach`。
- `tests/`：标准库 unittest + Node 冒烟测试。
- `.zcode-plugin/plugin.json` 与 `marketplace.json`：插件清单与市场清单，版本号需保持一致。
- `autopilot.py`：无人值守流水线（env/deps/build/test/deploy/verify）；`bootstrap.py`：交互式引导；`run.sh` / `run.cmd`：跨平台入口。

## 常用命令

```bash
# 只读体检全部补丁（不需要退出 ZCode）
python3 skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --check

# 打补丁 / 还原（必须先完全退出 ZCode，否则脚本以退出码 2 拒绝）
python3 skills/zcode-tokenspeed/scripts/zcode_patcher.py --all
python3 skills/zcode-tokenspeed/scripts/zcode_patcher.py --tps-footer --revert

# 链路诊断与一键自愈
python3 skills/zcode-tokenspeed/scripts/doctor.py
python3 skills/zcode-tokenspeed/scripts/doctor.py --fix

# 回归测试（Python 标准库；装了 node 才会跑 JS 相关用例）
python3 -m unittest discover -s tests -v

# 只构建 + 测试，不碰客户端
python3 autopilot.py --no-deploy

# 一键全流程（含部署排期）
./run.sh            # macOS / Linux
run.cmd             # Windows
```

CI 使用 `python autopilot.py --no-deploy --unattended --report ci-report.md`，矩阵覆盖 Python 3.10 / 3.12 / 3.13 与 Windows/macOS。

## 运行链路

1. `zcode_patcher.py` 自动探测安装位置（`discover()`），解析 asar、校验锚点、备份、写入并重算 integrity。锚点不唯一或表达式次数不符时**拒绝盲改**——报告即结论，不要绕过。
2. 补丁是重打包级（`--tps-footer`）：整体重排 asar、重算 integrity、加文件锁；`--all` 当前等价于它。
3. 插件链路：`SessionStart` → `sync.py --detach`（登记心跳后后台化）→ 子进程 `sync.py --worker` 读取 `plugins.options["zcode-tokenspeed@<市场名>"]`，缺失键回退 `plugin.json` 的 `userConfig.tps_footer.default`（默认开启）；旧版遗留开关键会被忽略。
4. 若客户端正在运行，改动交给 `apply_after_exit.py` 看护：等 ZCode 完全退出后应用/还原，再拉起客户端。对用户汇报时必须说「已排期，下次退出时生效」，不能说「已完成」。
5. 诊断入口：`doctor.py` 查安装/启用/钩子心跳/补丁状态；`restore_clean.py` 是客户端无法启动时的整包还原。

## 修改约束

- **仅 Python 标准库**，最低 3.10。不要新增 requirements、第三方依赖或 3.11+ 专属语法。
- **不要硬编码安装路径或用户目录**：复用 `zcode_patcher.discover()`、`HERE = Path(__file__).parent` 与数据根解析逻辑。
- **不要手工改 app.asar / zcode.cjs**，也不要绕过脚本的拒绝结论；不确定时先 `--dry-run`。
- 新增会 print 的 Python 脚本必须使用 `_console.safe_stdio()` 与 `ok_mark()` / `bad_mark()` 等辅助函数：中文 Windows 的 cp936 管道会因 `✓`/`✗` 抛 `UnicodeEncodeError` 并打断整段输出。新增符号前先确认 `'字'.encode('cp936')` 可行。
- `run.cmd` 必须保持纯 ASCII（cmd.exe 按 OEM 代码页解析）。
- 发布时同步更新 `.zcode-plugin/plugin.json`、`marketplace.json` 与 README 徽章的版本号。
- 提交信息沿用仓库风格：`type(scope): 中文摘要`；不要提交 `logs/`、`_sync.*`、`*.bak`、`*-patch.json` 等运行时产物。

## 已知坑

- **当前事实以 `plugin.json`、argparse 与 `sync.py` 的 `PATCHES` 为准**：只有 1 项补丁（TPS 状态栏）。旧文档里出现的 `--usage-chart`、`--model-width`、`--enhance-prompt`、`--model-puller` 都已移除，不要恢复。
- 补丁只影响下一次启动：ZCode 启动时只读一次 asar，运行期写入不会改变当前会话。
- 运行中写入会被 `zcode_running()` 全局拦截；`--check` 与 `--dry-run` 不受限，但也别在运行中尝试手工写文件。
- 备份带版本指纹：客户端升级后旧备份会归档为 `.stale-*`，还原时版本不一致会拒绝执行；不要用 `--force` 掩盖版本错配。
- 插件缓存可能在 `~/.zcode/cli/plugins/cache/<市场名>/<插件名>/<版本>/`，插件根在版本目录里；`doctor.py --where` 可直接定位，不要站在市场目录里猜相对路径。
- 本机没装 node 或无 ZCode 时，相关测试会自动跳过，这是预期行为，不是失败。
