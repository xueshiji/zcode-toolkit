# zcode-tokenspeed · ZCode TPS 性能状态栏

<p align="left">
  <a href="https://github.com/c80361619/zcode-toolkit/releases"><img src="https://img.shields.io/badge/version-0.7.1-blue.svg?style=flat-square" alt="Version"></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/dependencies-0%20(std%20only)-success.svg?style=flat-square" alt="Dependencies">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg?style=flat-square" alt="Platform">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg?style=flat-square" alt="License"></a>
</p>

给 ZCode 桌面客户端加一条**常驻的实时 TPS 与性能状态栏**：

- 📊 **本轮即时指标**：首 Token 延迟、tok/s 生成速度、本轮思考 Token。
- 🧮 **会话累计指标**：第 N 轮、总用量、累计输入/输出、缓存命中率、累计思考 Token。
- 🖱️ **位置随你选**：输入框下方（默认）/ 输入框工具栏 / 会话顶部 sticky，右键切换并记忆。
- 🛡️ **升级无感自愈**：客户端升级覆盖 `app.asar` 后，插件自动感知并重新排期注入。
- ♻️ **幂等、安全、可还原**：全程 Python 标准库，零第三方依赖；写入前自动备份并校验指纹。

> **纯 Python 标准库驱动**：零第三方依赖、免编译；改动仅作用于本地客户端的 `app.asar`。
>
> ⚠️ **声明**：非官方开源套件，与 ZCode 官方团队无关联。仓库名 `zcode-toolkit` ≠ 插件名 **`zcode-tokenspeed`**。

---

## ⚡ 三分钟极速上手（推荐，全程零命令）

1. 确认电脑已安装 **Python 3.10+** 与 **ZCode 桌面客户端**。
2. 打开 ZCode **设置 → 插件 → 右上角「创建」→「添加插件市场」**，来源填：
   `c80361619/zcode-toolkit`
3. 在「个人」分段找到 **ZCode TPS 状态栏**（`zcode-tokenspeed`），点**安装**。
4. **完全退出并重启 ZCode**（托盘图标右键 → 退出），在新会话里发一条消息。
   状态栏会在下次启动后出现（补丁只在 ZCode 退出时写入）。

> 插件清单默认开启，装完无需打开配置页。想关闭时在插件配置页把「TPS 状态栏」拨成关并保存，
> 下次完全退出 ZCode 时会自动还原。

---

## 🧩 功能一览

| 指标 | 含义 |
|:---|:---|
| 首 token | 请求开始到首个流式块；请求进行中逐请求更新，轮结束后显示本轮各请求的平均值 |
| tok/s | 每个模型请求完成后按真实 tokens 更新；轮结束显示按输出 tokens 加权的平均速度 |
| 第 N 轮 | 脚本当前观察到的轮次（不是历史会话序号） |
| 总用 / 输入 / 累出 | 会话累计总用量 / 输入 Token / 输出 Token（真实 usage，不做估算） |
| 命中 + 平均命中率 | 会话累计缓存命中 Token 与「缓存读 ÷ 总输入」的加权命中率 |
| 思考 | 最近一轮推理 Token（模型上报时显示） |

统计条默认挂在输入框下方；**右键胶囊**可切到「输入框工具栏」或「会话顶部 sticky」。
空会话显示绿点空态，常驻但不打扰。

---

## 🛠️ 故障排查与一键自愈

客户端升级会覆盖官方原版文件，插件检测到升级后会自动重新排期。若状态栏仍未出现：

### 方案 A：客户端内一键修复（推荐）
在任意对话窗口输入内置斜杠命令：
- `/zcode-patch-doctor`：诊断插件、钩子、看护与补丁状态；
- `/zcode-patch-apply`：重新应用 TPS 补丁。

### 方案 B：一条命令自愈
```powershell
# Windows (CMD / PowerShell)
python -c "import pathlib,subprocess,sys; p=next(pathlib.Path.home().glob('.zcode/**/doctor.py'),None); subprocess.run([sys.executable,str(p),'--fix']) if p else print('未找到已安装的 ZCode 插件，请先在客户端内安装')"
```
```bash
# macOS / Linux
python3 -c "import pathlib,subprocess,sys; p=next(pathlib.Path.home().glob('.zcode/**/doctor.py'),None); subprocess.run([sys.executable,str(p),'--fix']) if p else print('未找到已安装的 ZCode 插件，请先在客户端内安装')"
```

常见原因：
- **只关了窗口没退出进程**：需要托盘右键 → 退出，看护才能写 `app.asar`。
- **插件更新了但补丁没更新**：`doctor.py` 会提示「旧版组件」，重新执行 `/zcode-patch-apply` 即可。
- **客户端升级后**：首次会话会自动感知并排期重注入，通常退出重启一次即可恢复。

---

## 🔄 卸载与还原

```bash
# 完全退出 ZCode 后，还原 TPS 补丁
python "<插件目录>/skills/zcode-tokenspeed/scripts/zcode_patcher.py" --tps-footer --revert
# 或一次还原全部（当前仅 TPS）
python "<插件目录>/skills/zcode-tokenspeed/scripts/zcode_patcher.py" --all --revert
```
然后在 ZCode **设置 → 插件 → 管理已安装** 中卸载插件。

> **从 0.6.x 升级到 0.7.0**：0.7.0 只保留 TPS 状态栏，**不会**自动清理旧版本的
> 用量图表 / 提示词增强 / 模型拉取注入。想要完全干净的客户端，请先用 0.6.x 执行
> `--all --revert`，再升级插件；客户端已异常到打不开时用
> `restore_clean.py --latest` 整包还原。

---

## 📚 开发者指南

<details>
<summary><b>本地离线安装、纯 CLI、引导脚本与全自动流水线</b></summary>

### 本地源码安装
```bash
git clone https://github.com/c80361619/zcode-toolkit.git
```
在 ZCode「设置 → 插件 → 创建 → 添加插件市场」中选择该仓库目录，校验通过后安装。

### 纯命令行模式（不装插件）
```bash
# 只读体检（自动探测安装位置）
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all --check

# 完全退出 ZCode 后注入
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --all

# 还原
python skills/zcode-tokenspeed/scripts/zcode_patcher.py --tps-footer --revert
```

### 引导脚本 / 全自动流水线
```bash
python bootstrap.py     # 环境校验 + 语法构建 + 回归测试
python autopilot.py --no-deploy   # 只构建与测试，不碰客户端
./run.sh                # macOS / Linux 全流程
run.cmd                 # Windows 全流程
```

### 测试
```bash
python -m unittest discover -s tests -v
node tests/tps_pct_smoke.js
```
</details>

<details>
<summary><b>自动注入机制与 ASAR 安全设计</b></summary>

### 1. 为什么补丁需要退出后写入？
`zcode_patcher.py` 有严格的运行预检：只要 ZCode 在运行就拒绝写盘，因为运行中的
`app.asar` 会被文件系统锁定。插件通过 `SessionStart` 钩子登记期望状态，由
`apply_after_exit.py` 看护等 ZCode 完全退出后原子写入，再自动拉起客户端。

### 2. ASAR 重打包安全性
- **全域 Integrity 校验**：重打包时对所有 entry 的 SHA-256 与 offset 排布做全局自洽检验；
- **跨进程互斥锁**：写入全程施加文件锁，避免多进程并发争抢；
- **备份带版本指纹**：客户端升级后旧备份自动归档为 `.stale-*`，绝不把旧版文件盖回新客户端；
- **外科手术式还原**：只摘除 TPS 自己的 `<script>` 与脚本条目，不依赖整包覆盖。
</details>

<details>
<summary><b>TPS 相关更新记录</b></summary>

#### 0.7.1
- tok/s 与 token 统计改为只取真实 usage，移除文本估算；
- 每个模型请求完成后更新一次；轮结束后首 token 显示本轮平均值，tok/s 显示按输出 tokens 加权的平均速度。

#### 0.7.0
- 仓库精简为单一 TPS 状态栏，移除用量图表、提示词增强、模型拉取与相关诊断工具；
- 保留升级自愈、看护、`stale` 检测与完整还原链路。

#### 0.6.14
- 即时指标聚焦「首 token 延迟」与「生成速率」，会话累计新增总 Token 用量。

#### 0.6.12
- 修复侧边栏辅助对话导致状态栏来回跳动：严格限定只在主界面对话框显示。

#### 0.6.8 – 0.6.9
- 平均命中率精确到两位小数。

#### 0.6.5
- 引入 `_AsarWriteLock` 进程锁/文件锁与全域 integrity 校验，防范布局错位。

#### 0.6.10 – 0.6.11
- 注入脚本内容比对与 `stale` 态；客户端升级指纹感知与自动重注入。
</details>

---

## 许可协议

本项目采用 [MIT License](LICENSE) 开源协议。
