---
name: zcode-tokenspeed
description: "[仅手动调用，禁止自动触发] ZCode 客户端 TPS 性能状态栏补丁：向 app.asar 注入 zcode-tps.js，在输入框下方常驻展示本轮首 token/tok/s 与会话累计指标（真实 usage，不做文本估算）。全程幂等、可 --check、可 --revert 精确还原。只有当用户明确要求执行本 skill、或明确点名「zcode-tokenspeed」「TPS 状态栏」时才加载；用户只是泛泛提到性能、速度、状态栏等话题时，一律不要自动触发本 skill。"
---

# ZCode TPS 性能状态栏补丁

> 本 skill 由 **zcode-tokenspeed 插件**提供，脚本就在本 skill 目录下的 `scripts/`（下文 `<skill目录>` 均指本目录）。

**先跑这一条**（只读，不改任何文件）：

```bash
python "<skill目录>/scripts/zcode_patcher.py" --all --check
```

`--all` 当前等价于 `--tps-footer`；配合 `--check` 是体检、不带参数是注入、配合 `--revert` 是还原。

**用户说「插件装了但没生效」时，先跑安装自检**：

```bash
python "<skill目录>/scripts/doctor.py"          # 可读报告
python "<skill目录>/scripts/doctor.py" --json   # 结构化输出
python "<skill目录>/scripts/doctor.py" --where  # 只查插件装在哪、给出可复制的绝对路径
python "<skill目录>/scripts/doctor.py" --fix    # 一键自愈（会排期看护）
```

> **别站在插件缓存目录里敲相对路径**：GitHub 来源的市场缓存成
> `cache/<市场名>/<插件名>/<版本>/`，`skills/` 在**版本目录里面**。
> 找不到时用 `doctor.py --where`，或系统命令搜索
> （PowerShell `Get-ChildItem "$env:USERPROFILE\.zcode\cli\plugins" -Recurse -Filter doctor.py`；
> macOS/Linux `find ~/.zcode/cli/plugins -name doctor.py`）。

插件这条路要过「安装 → 启用 → 钩子触发 → 自动注入 → 打补丁」五关。最常见的卡点是
**插件没启用**。钩子是否执行过，看 `scripts/_sync.last`；文件不存在 = 钩子从未被调用。

> **装完即用，不需要打开配置页。** 插件清单里唯一开关 `tps_footer` 的默认值是 `true`；
> 没保存过配置时 `sync.py` 按默认值注入。ZCode 的插件清单没有「安装时钩子」，
> 最早能自动触发的时机是**下一次会话启动的 `SessionStart`**。

## 当前能力

只有一项补丁，全部围绕 TPS 状态栏：

| 能力 | 命令 | 改动范围 |
|---|---|---|
| 输入框下方常驻统计条：本轮首 token / tok/s / 思考 + 会话累计（第 N 轮 / 总用 / 输入 / 命中率 / 累出） | `python zcode_patcher.py --tps-footer [--check/--revert]` | `app.asar`（重打包级：新增 `out/renderer/zcode-tps.js` + `index.html` 挂载） |

通用开关：`--all`（当前仅 TPS）· `--dry-run`（只报告不写盘）· `--verbose`（打印探测细节）·
`--force`（跳过备份指纹校验，慎用）· `--prune`（清理安装目录产物；`--deep` 连当前备份与
sidecar 一起清，之后无法 `--revert`）。

## 版本与生效时机

- 补丁只影响**下一次启动**：ZCode 启动时只读一次 `app.asar`，运行期写入不会改变当前会话。
- 打补丁/还原前会预检 ZCode 进程：运行中直接拒绝（退出码 2）。只读 `--check` 与
  `--dry-run` 不受限。
- 备份带**版本指纹**（`app.asar.tps.bak` + `.meta.json`）：客户端升级后旧备份自动归档为
  `.stale-<时间>`；还原时版本不一致会拒绝执行，避免把旧文件盖回新客户端。
- 想确认客户端版本：看「关于」，或 `--check` 输出第一行。

### 插件开关

配置键：`~/.zcode/cli/config.json` 的 `plugins.options["zcode-tokenspeed@<市场名>"]`。
只有一个键：

| 开关 | 控制的功能 | 默认 | 生效时机 |
|---|---|---|---|
| `tps_footer` | TPS 状态栏 | 开 | ZCode 完全退出时由看护写入，**再启动**才生效 |

行为约定（`scripts/sync.py`，由 `SessionStart` hook 调用）：

- **零配置自动注入**：`resolve_wanted()` 合并规则是**已保存开关优先，没保存过的键退回
  `plugin.json` 的 `userConfig.*.default`**。显式关掉就是 `false`，会触发还原。
- **旧版开关键会被忽略**：升级到 0.7.0 后，配置或 `_watchdog.want` 里若还留着
  `usage_chart` / `enhance_prompt` / `model_puller`，同步与看护都会跳过它们，不会报错。
- **钩子只做「登记心跳 + 后台化」**（`sync.py --detach` → 子进程 `--worker`）。
  hook 是内联执行的，同步要跑多次 `--check`，做完再返回会拖住会话启动。
- **首次自动注入时往会话里发一条说明**，标记文件 `_autoinject.done` 保证只出现一次。
  输出只用顶层 `additionalContext`；stdout 必须是 JSON。
- **重打包级补丁写进退出后看护** `apply_after_exit.py`，等 ZCode 完全退出时自动应用。
  真正原因是 ZCode **只在启动时读一次** `app.asar`，不是文件被锁。
- 日志与心跳：`scripts/_sync.log`、`scripts/_sync.last`。

### 退出后自动注入

```bash
python "<skill目录>/scripts/zcode_patcher.py" --tps-footer
```

由 `apply_after_exit.py` 在插件链路中自动拉起：轮询等 ZCode 退出 → 应用/还原 → 写日志 →
重新拉起 ZCode。也可以自己挂计划任务调用它；不需要时删掉任务即可。

## 标准执行流程（AI 代执行与人工自助通用）

1. **定位安装**：脚本自动探测（运行进程 → 注册表 → 常见目录，跨 Windows/macOS/Linux）。
   探测不到就把安装根目录作为位置参数传入，并用 `--verbose` 看每一步。
2. **只读核实**：
   - `python zcode_patcher.py --all --check`：客户端版本、index.html 是否找到、注入状态、
     sidecar/备份是否存在，最后给出 1 行汇总。
   - 已注入但脚本内容与现行版本不一致时，输出会写
     **「已打（含旧版组件，重跑可自动更新）」**——此时必须重跑注入，不能当成已生效。
   - 脚本对「`</body>` 出现次数 ≠1」等情况一律拒绝盲改并报告原因——**报告即结论，不要绕过**。
3. **确认备份与还原命令**：
   - 首次注入自动生成 `app.asar.tps.bak`（整包备份，带 `.meta.json` 指纹）与
     `app.asar.tps-patch.json`（原始 index.html base64 + asar 尺寸指纹）。
   - 展示给用户的还原命令：
     ```bash
     python "<skill目录>/scripts/zcode_patcher.py" --tps-footer --revert
     ```
4. **执行注入**（ZCode 已完全退出，或交给看护）：
   ```bash
   python "<skill目录>/scripts/zcode_patcher.py" --tps-footer
   ```
   不确定会改什么时先加 `--dry-run`。
5. **重启验证**：完全退出并重启 ZCode，输入框下方应出现统计条；无轮次时是绿点空态。
6. **失败回退**：执行 `--tps-footer --revert` → 重启 ZCode → 重新 `--check`。
   客户端异常到打不开时，用 `python scripts/restore_clean.py --latest` 整包还原。

## TPS 统计胶囊

> 实现：`zcode-tps.js` 读 ServicePort 事件流；tok/s、首 token、会话累计都基于真实 usage，
> **不做文本估算**。

### 关键约束：绝不调用 `port.start()`

MessagePort 队列一旦启用，消息只派发给「启用瞬间已注册的监听器」。本脚本是普通 script，
早于 `type=module` 的应用主包执行；若先 `start()` 就会消费掉服务端的 `Initialize` 启动握手
→ 应用协议客户端永远收不到握手 → **ZCode 3.12.2 卡在启动界面**。
正确做法：只 `addEventListener`，把 `start()` 留给应用。

真实 Chrome / MessageChannel 实测（双方持有同一 port）：

| 做法 | 我们收到 | 应用收到 | 结果 |
|---|---|---|---|
| 我们先 `start()` | `["Initialize"]` | `[]` | ✗ 卡启动界面 |
| 只监听、不 start | `["Initialize"]` | `["Initialize"]` | ✓ 双方正常 |

> **不可用 Node 验证此语义**：Node 的 MessagePort 在 `addEventListener` 时隐式 start，
> 会让「不 start」也失败，从而误判为方案不可行。

**已知特性**：tok/s 在本轮第一个模型请求完成（`usage.delta` 到达）后才出现；请求进行期间
不显示估算速度，保留最近一次真实请求的值。首 token 逐请求更新，当前请求还没首包时先隐藏。

### 展示与位置

默认挂在**输入框卡片下方、水平居中**；右键胶囊可切：

- 输入框下方（默认）
- 输入框工具栏（行内居中）
- 会话顶部 sticky

位置写入 `localStorage`（键 `ztps-pos`），重开保持。数据层零改动，仅挂载点不同；
挂载点找不到（空态/设置页）时自清理。诊断：`window.__ztpsDiag.posMode` / `hiddenReason`。

```
生成中（首个请求已完成）: ● 首 token 8s · 32 tok/s · 思考 1.2k │ 第 8 轮 · 总用 57.5k · 输入 45.2k · 命中 38.1k · 平均命中 84.00% · 累出 12.3k
结束后:                  ● 平均首 token 9s · 平均 35 tok/s · 思考 1.2k │ 第 8 轮 · 总用 57.5k · 输入 45.2k · 命中 38.1k · 平均命中 84.00% · 累出 12.3k
空会话:                  ●            （绿点空态常驻）
```

- **会话累计口径**：轮数=可见轮次数；输入/命中/累出=各轮 `usage.delta` 累加（API 计费视角，
  每轮 inputTokens 含历史所以累计值偏大，属预期）；平均命中=累计命中 ÷ 累计输入，固定两位
  小数。不做估算：请求完成前不计入，`usage.delta` 到达后一次性计入。
- **渐进降级顺序**：溢出时先丢总用 → 首 token → tok/s；轮数/输入/命中/累出保留。

### 行为规则（验收标准）

- **绿点 ● 常驻**：有可展示轮次就在；流式生成中发亮，空闲静态。不显示假时钟。
- **同一 turnId 复用（编辑重发/重试）自动清零**：检测到新一轮开始即重置旧统计。
- **首 token**：从请求开始（`model.request.status` 的 started）到首个非子代理
  `stream.chunk`；请求进行中逐请求更新，当前请求还没首包时先隐藏；轮结束后显示本轮
  各请求首 token 的算术平均。第一个请求缺 started 时用轮开始时间兜底，后续请求缺
  started 时该请求不计入平均。
- **tok/s**：只取真实 usage；每个模型请求完成（`usage.delta`）后更新一次；轮结束后显示
  按 `outputTokens` 加权的平均速度：`Σ(out_i × tps_i) / Σ out_i`。其中 `tps_i` 是该请求
  的 `outputTokens ÷ decode 时长`，decode 时长优先取官方 `durationMs − TTFT`。
- **动态刷新**：首 token 在请求首包到达时逐请求更新；token 数与 tok/s 在 `usage.delta`
  到达时更新一次；不做文本估算。
- **静默期**：工具执行期间不刷新 tok/s，保留最近一次真实请求的值；停止/出错且没有 usage
  时不做估算，相关 token 暂不计入累计。
- **切换会话立即消失**：只认「DOM 可见轮次（`section[data-turn-id]`）+
  `data-session-id` 匹配当前会话」双重条件，不依赖会话切换事件。
- **历史会话累计缺失**：usage.delta 不回放，重新打开旧会话拿不到当时统计，属预期。
- **空会话常驻**：无轮次时显示仅绿点的空态胶囊。

### 数据链路（无常驻服务）

1. ZCode preload 把主进程的 MessagePort 经
   `window.postMessage("zcode:service-port", "*", [port])` 转交渲染页面；注入脚本接管端口
   （`window.__ztpsHook` 可对存量端口手动补挂，`window.__ztpsPort` 供调试旁路监听）。
2. 协议帧是二进制内嵌 JSON，两类：
   - **version:1 事件流**：`usage.delta`（inputTokens/outputTokens/cacheReadTokens/
     totalTokens/reasoningTokens，每次模型请求完成时发）、`model.request.status`
     （requestId/status/querySource/durationMs，用于逐请求首 token 与速度）、
     `stream.chunk`（assistantMessageId + chunkLength + channel，流式期间每 50-100ms 一批）。
     若客户端额外下发 `model.request.metrics`，脚本优先使用其中的
     `durationMs` / `timeToFirstContentMs`。
   - **conversation 行事件**：`turnHeader`、`userInput`、`reasoning`/`assistantText`。
3. **轮关联链**：轮的 key 是 productTurnId（`msg_xxx`，与 DOM `section[data-turn-id]` 一致）；
   `stream.chunk` 的 assistantMessageId 是 assistantResponseId，需经行事件的
   `assistantResponseId → turnId` 映射中转；`usage.delta` 经 `sourceCommandId` 关联。
   关联断了的表现：tok/s 与 token 一直不出现。
4. 渲染：扫描 `section[data-turn-id]` + sessionId 双条件取当前会话最新轮 → 算指标 → 更新胶囊。
5. 刷新：MessagePort 事件驱动 + 500ms 轻量巡检；`usage.delta` 到达时更新一次；渲染带内容
   签名，数据未变零 DOM 写。

### 注入原理（asar 重打包级）

状态栏要**新增文件**，必须整体重打包：

1. `out/renderer/index.html` 的 `</body>` 前插 `<script src="./zcode-tps.js"></script>`；
   `zcode-tps.js` 作为新条目写入 `out/renderer/`。index.html 无 CSP、无 nonce，
   普通脚本标签在 `type="module"` 的 React bundle 之前同步执行。
2. asar 布局：头 16 字节 = 4 个 uint32 LE `[4, headerSize, pickleLen, jsonLen]`，
   `pickleLen = 4 + jsonLen + pad4`，`headerSize = 8 + jsonLen + pad4`，
   数据区起点 = `16 + jsonLen + pad4`；条目 `offset` 相对数据区起点，全部文件带 integrity。
3. 重打包：读全量 → 树上删/插条目 → 全部 offset 重排 → 覆盖与新增条目重算 integrity →
   写临时文件 → **逐条回读校验** → 原子替换。
4. **实现关键坑**：offset 重排会改写条目，此后从旧文件切片必须用**重排前快照的旧位置**，
   否则「新 offset + 旧数据区起点」错位读取；新增条目的树插入必须在重打包函数内部做。
5. 备份与记录：首次注入前整包备份 `app.asar.tps.bak`；`app.asar.tps-patch.json` 记录原始
   index.html（base64）与 asar 尺寸指纹。

### 升级 / 回退 / 排障

| 现象 | 处理 |
|---|---|
| 升级后胶囊消失 | `app.asar` 被覆盖，重跑 `--tps-footer` |
| 重启后无胶囊 | `--tps-footer --check` 看 state；渲染进程 console 查 `window.__ztps` |
| console 出现 CSP 拦截 | 当前版本 index.html 无 CSP；未来若加了需同步放宽 `script-src` |
| 指标只有「●」绿点 | `usage.delta` 未关联到轮（看 `window.__ztpsTurns` 的 outputTokens/lastUsageAt/requests）；用 `window.__ztpsPort` 旁路比对字段 |
| tok/s 不出现 | 本轮还没有请求完成（`usage.delta` 未到）；或缺少请求耗时（`model.request.status`/`metrics` 未下发），无法算速度 |
| 想换脚本逻辑 | 改 `zcode-tps.js` 后重跑 `--tps-footer`（内容变了会热更新脚本条目，无需 revert） |

## ★★ 插件更新 ≠ 补丁更新：`stale` 态

插件市场「更新」只替换**插件目录**，**不会**重新注入 `app.asar`。此时 asar 里还是上一版
的注入片段，而 `--check` 会给出：

- `已打（含旧版组件，重跑可自动更新）` → 注入内容旧
- `已打`（脚本同源） → 真正最新

`check_state()` 必须把前者判成 `stale`，不能判成 `on`，否则 `run_sync()` 会视为已一致
永远跳过，**改动永远不生效，而且没有一句报错**。

**判断顺序是硬性要求**：`if "含旧版组件" in out: return "stale"` 必须排在
`if "已打" in out` 之前——前者本身含「已打」，顺序反了 `stale` 分支永远走不到。
已有回归测试分别从语义（喂字符串）和源码顺序（`inspect.getsource`）两个角度钉死。

`stale` 时：重打包级补丁转交看护并报「插件已更新，客户端退出时重注入为新版」；
若开关是关的直接还原（不必先更新再还原绕一圈）。

## ★★ 为什么有时要「重启两遍」才生效

**写入时机在退出，不在启动**：

```
启动① → 钩子跑 sync → 有待办 → 挂看护 W（W 在 while zcode_running(): sleep(3) 里等）
退出  → W 醒来 → 写 app.asar → 主动拉起 ZCode
启动② → 新 asar 生效 ✓          ← 真正起作用的是这第二个启动
```

所以用户说「重启了一遍没反应」时，先确认他是不是**关窗口**而不是**托盘右键退出**；
只关窗口时 ZCode 进程还在，看护会继续等。

## ★★ asar「布局错位」＝客户端打不开，且毫无日志

**症状**：注入后 ZCode 双击无反应（无窗口、无错误弹窗、无崩溃记录），
`--enable-logging=stderr` 也不打印，退出码 1。

**根因**：数据区排布与 header 声明错位。典型形态是 header 里某条目 size 已变大，
数据区仍按旧 size 递增，之后每个条目 offset 都偏移固定字节数，但 integrity 仍是旧内容哈希。
**结构完全自洽（条目数对、offset 连续、零越界），只有逐条重算哈希才能发现。**

**规矩**：

1. **回读校验必须覆盖全部条目**，不能只校验本次改动的条目。`_repack_asar` 落盘前
   逐条重算 sha256 与 integrity 比对，任一条不符即抛错拒绝落盘。
2. **同一 asar 的写入必须互斥。** `_AsarWriteLock`（进程内 `threading.Lock` +
   跨进程 `msvcrt.locking` 文件锁 `<asar>.zp-lock`）覆盖整个
   「读 header → 排布 → 写数据 → 原子替换」。
3. **怀疑 asar 不干净时先做全域 integrity 体检**，别先去查 Electron 自动更新、崩溃日志。
4. **事故恢复**：换回 `app.asar.tps.bak`（写入前备份），再跑一次全域体检确认为 0。

## 无人值守路径必须带超时

`subprocess.run` 不写 `timeout=` 时可能永久挂起，而用户只看到「卡住不动」：

- `bootstrap.py`：`pip install` / `git clone` 默认 1200s，超时退 124 并带出部分输出。
- `apply_after_exit.py`：看护是后台无人值守进程 → `tasklist` 30s、补丁 600s。
  **`tasklist` 超时必须按「仍在运行」处理**——否则会在 asar 还被占用时动手写，
  且已错过退出时机，补丁永远写不进去。

## 从 0.6.x 升级

0.7.0 只保留 TPS 状态栏，**不会**自动清理旧版本的用量图表 / 提示词增强 / 模型拉取注入。
想要完全干净的客户端：先用 0.6.x 执行 `--all --revert`，再升级插件；客户端已异常到打不开时
用 `restore_clean.py --latest` 整包还原。
