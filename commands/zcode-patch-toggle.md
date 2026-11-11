---
description: 查看/切换 zcode-tokenspeed 的 TPS 状态栏开关
---

加载本插件自带的 `zcode-tokenspeed` 技能（`${CLAUDE_PLUGIN_ROOT}/skills/zcode-tokenspeed/SKILL.md`；若该变量未展开，就按插件安装目录下的同名路径读取），然后按下面的流程帮用户开关 TPS 状态栏。

**开关的存储位置**：`~/.zcode/cli/config.json` 的 `plugins.options["zcode-tokenspeed@<市场名>"]`（`@` 后面按**实际安装的市场**填；本机是 `zcode-toolkit`）。

| 键 | 功能 | 生效时机 |
|---|---|---|
| `tps_footer` | TPS 状态栏 | ZCode 完全退出时由看护写入，再启动才生效 |

**流程**：

1. 读 `~/.zcode/cli/config.json`，取出 `tps_footer` 的当前值（缺失表示“用户未表态”，插件按清单默认开启处理）。
2. 同时跑 `python "<skill目录>/scripts/zcode_patcher.py" --tps-footer --check` 拿到**客户端实际注入状态**，把“配置值 / 实际状态”两列一起用中文表格展示给用户。
3. 问用户要改成开还是关。用户明确指定后再改。
4. 改配置：**整份读入 → 只增改 `tps_footer` → 写回**，保持 JSON 缩进与其余内容不变；`plugins.options` 或插件 id 那一层不存在时按需创建。**不要动 `enabledPlugins` 等其他字段。**
5. 告诉用户生效时机：**要等 ZCode 完全退出后由看护写入，再启动才生效**（脚本在客户端运行时会直接拒绝写入，而会话钩子必然在运行中触发）。
6. 若用户想立刻生效而不等下次会话，可以按技能里的「标准执行流程」直接执行命令（**前提是 ZCode 已完全退出**）。

**注意**：改 `config.json` 前建议先备份一份（`config.json.bak`）；若 ZCode 正在运行且用户在设置页保存过供应商配置，有被回写的可能，改完提示用户确认一次。旧版遗留的 `usage_chart` / `enhance_prompt` / `model_puller` 键会被同步逻辑忽略，可保留也可删除。
